#!/usr/bin/env python3
# v1.3 - Gold set v2 checker and finaliser for brief 09 (Select AI RAG tuning, English + Arabic).
#        v1.3: public copy: the lab host's name removed from the Run as line (comment only).
#        v1.2 (review 1, 29-Sep): numbers stated in a question include number words ("two days ago"
#              holds 2, via eval_norm.to_digits), for the sibling rule and question_contains();
#              context_ok key placed after forbidden_siblings; --write also writes the seal file
#              results/questions.sha256 (sha256sum -c format) and check mode verifies it.
#        v1.1 (Codex review): derived span_has_fact per span; table_refs occurrence selector;
#              --write keeps the file mode of questions.json (mkstemp would leave it 0600).
#        Checks every evidence span of code/eval/questions.json against (a) the gold document's
#        source text and (b) the text extracted from the built corpus file (pdftotext for PDFs,
#        word/document.xml for DOCX), using the normaliser of tools/build_corpus.py.
#
# Run as : any local user. No database, no network, no credentials.
# Usage  : python3 validate_gold.py                 # check only; exit 0 = every span found everywhere
#          python3 validate_gold.py --write         # recompute the derived fields and the seal, then check
#          [--questions PATH] [--report PATH.json] [--formats pdf,docx] [--seal-file PATH]
#          The seal file defaults to results/questions.sha256 for the default questions path only;
#          verify it with: cd <brief folder> && sha256sum -c results/questions.sha256
# Re-run : safe. Check mode writes nothing except the optional --report file. --write only rewrites
#          the derived fields (split, span_docs, overlap_ratio, max_shared_run, forbidden_siblings,
#          forbidden = authored + siblings, sealed_sha256_of_questions); authored text is never
#          changed, and a second --write produces a byte-identical file (and seal file).
# Needs  : python 3.10+, poppler-utils (pdftotext) for the extraction check; tools/build_corpus.py.
#
# How a span is matched
#   source   : verbatim substring of ONE segment of the gold document body (a paragraph, a list
#              item, a heading or a single table cell) after removing the "**" emphasis markers.
#              The META block and the closing disclaimer line are not segments.
#   extracted: match_key(span) is a substring of match_key(extracted text), where match_key() is
#              build_corpus.norm() (NFKC, bidi controls, tatweel and harakat removed, Arabic-Indic
#              digits to ASCII) followed by removing ALL whitespace. pdftotext moves the space next
#              to a digit or Latin run inside Arabic text, so whitespace cannot be compared. The
#              whitespace-collapsed ("strict") result is reported as information only.
#
# Derived fields (see PLAN.md 5.1)
#   split             sha256(fact_id) order inside each bucket; the first ceil(n/3) fact ids are dev.
#                     D questions (dialect paraphrases) take the split of the T fact they paraphrase.
#   span_docs         for each span, the gold document whose source contains it.
#   overlap_ratio     share of the question's distinct words that also occur in its spans.
#   max_shared_run    longest run of consecutive words shared by the question and any one span.
#   span_has_fact     per span: does the span itself contain a fact value? When false (a table
#                     row label, a heading), an evidence hit must also find a fact value in the
#                     same chunk, so a label retrieved without its value does not count.
#   forbidden_siblings numbers in the same table row and column as each table_refs cell, minus the
#                     facts, numbers already in the question (digits or number words), sibling_exempt
#                     values, times, grade codes and day-of-month dates. Both ASCII and Arabic-Indic
#                     forms are listed.
#   context_ok (authored) forbidden values the gold document states legitimately for the asker; the
#                     scorer counts them as contamination only when a fact is missing.
import argparse
import csv
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "code", "tools"))
import build_corpus as bc  # noqa: E402  (the normaliser and the source parser are reused, not copied)
sys.path.insert(0, HERE)
from eval_norm import to_digits  # noqa: E402  (the scorer's number-word rules, not a copy)

QUESTIONS = os.path.join(HERE, "questions.json")
SEAL_FILE = os.path.join(ROOT, "results", "questions.sha256")
SEAL_LABEL = "code/eval/questions.json"          # brief-relative path named in the seal file
CORPUS = os.path.join(ROOT, "corpus")
MANIFEST = os.path.join(CORPUS, "manifest.csv")
SRC_DIRS = (os.path.join(CORPUS, "src", "gulf"), os.path.join(CORPUS, "src", "india"))

log = logging.getLogger("validate_gold")

FOOTER_PREFIXES = ("Fictional document created", "وثيقة خيالية")
WS = re.compile(r"\s+")
TO_ASCII_SEP = str.maketrans({"٬": ",", "٫": "."})
TO_INDIC = str.maketrans({**{str(i): chr(0x0660 + i) for i in range(10)}, ",": "٬", ".": "٫"})
NUM = re.compile(r"\d+(?:[.,]\d+)*")
TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
GRADE = re.compile(r"\bG\d+\b")
MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
          "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
          "يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر")
DATE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:%s)\b|(?:%s)\s+\d{1,4}\b" % (MONTHS, MONTHS))

DERIVED_KEYS = ("split", "span_docs", "span_has_fact", "overlap_ratio", "max_shared_run",
                "forbidden_siblings")
KEY_ORDER = ("id", "fact_id", "q_lang", "doc_lang", "bucket", "split", "question", "gold_docs",
             "twin_of", "paraphrase_of", "evidence_spans", "span_docs", "span_has_fact", "facts",
             "forbidden", "forbidden_siblings", "context_ok", "answerable", "tags", "table_refs",
             "sibling_exempt",
             "statutory", "counterparts", "absence_check", "overlap_ratio", "max_shared_run", "notes")


# --------------------------------------------------------------------------- text helpers
def match_key(text):
    """Normalised form used to find a span in extracted text: build_corpus.norm, no whitespace."""
    return WS.sub("", bc.norm(text))


def strict_key(text):
    """Informational: build_corpus.norm with whitespace collapsed to single spaces."""
    return WS.sub(" ", bc.norm(text)).strip()


def western(text):
    """build_corpus.norm plus the Arabic thousands and decimal separators mapped to ASCII."""
    return bc.norm(text).translate(TO_ASCII_SEP)


def num_key(value):
    """Canonical numeric key: ASCII digits, no thousands separators ("1,00,000" -> "100000")."""
    v = western(str(value)).strip()
    return v.replace(",", "")


def numbers_in(text):
    """Numeric keys of every number in text (after digit normalisation)."""
    return {n.replace(",", "") for n in NUM.findall(western(text))}


def contains_value(text, value):
    """True if value occurs in text on a boundary (numbers are not matched inside longer numbers)."""
    t = western(text).casefold()
    v = western(value).casefold().strip()
    if not v:
        return False
    if NUM.fullmatch(v):
        return v.replace(",", "") in numbers_in(t)
    return re.search(r"(?<!\w)%s(?!\w)" % re.escape(v), t) is not None


def question_numbers(question):
    """Numeric keys a question states, as digits or as number words ("two days ago" -> 2)."""
    return numbers_in(question) | numbers_in(to_digits(question))


def question_contains(question, value):
    """contains_value() that also sees number words in the question and in the value."""
    return contains_value(question, value) or contains_value(to_digits(question), to_digits(value))


def to_indic(value):
    """ASCII digits and separators to Arabic-Indic ("2,500" -> "٢٬٥٠٠")."""
    return str(value).translate(TO_INDIC)


def words(text):
    """Word tokens as build_corpus counts them (Arabic letter runs, Latin words, numbers)."""
    return [w.casefold() for w in bc.WORD.findall(bc.norm(text))]


def longest_shared_run(a, b):
    """Length of the longest run of consecutive tokens common to token lists a and b."""
    best, prev = 0, [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def overlap_stats(question, spans):
    q = words(question)
    s_all = set()
    run = 0
    for sp in spans:
        st = words(sp)
        s_all.update(st)
        run = max(run, longest_shared_run(q, st))
    qs = set(q)
    ratio = round(len(qs & s_all) / len(qs), 3) if qs else 0.0
    return ratio, run


# --------------------------------------------------------------------------- corpus
class Doc:
    """One gold document: source segments, META, tables, and the built corpus files."""

    def __init__(self, row, src_path):
        self.id = row["id"]
        self.lang = row["lang"]
        self.region = row["region"]
        self.pdf = os.path.join(CORPUS, "pdf", row["pdf"]) if row.get("pdf") else None
        self.docx = os.path.join(CORPUS, "docx", row["docx"]) if row.get("docx") else None
        self.src_path = src_path
        self.meta, self.blocks = bc.parse_source(src_path)
        self.segments = self._segments()
        self.body_key = match_key("\n".join(self.segments))

    def _segments(self):
        with open(self.src_path, encoding="utf-8") as fh:
            body = fh.read().split("#BODY", 1)[1]
        segs = []
        for line in body.splitlines():
            s = line.strip()
            if not s or s.startswith(FOOTER_PREFIXES):
                continue
            if s.startswith("|"):
                segs.extend(c.strip().replace("**", "") for c in s.strip("|").split("|"))
            else:
                segs.append(re.sub(r"^(?:#{2,3} |- )", "", s).replace("**", ""))
        return segs

    def tables(self):
        return [[[c.replace("**", "").strip() for c in row] for row in payload]
                for kind, payload in self.blocks if kind == "t"]

    def meta_numbers(self):
        vals = " ".join(self.meta.get(k, "") for k in ("version", "effective", "review"))
        return {n.lstrip("0") or "0" for n in numbers_in(vals)}

    def all_text(self):
        with open(self.src_path, encoding="utf-8") as fh:
            return fh.read()


def load_docs():
    """id -> Doc for every manifest row. Raises on a missing source or corpus file."""
    sources = {}
    for d in SRC_DIRS:
        for f in sorted(os.listdir(d)):
            if f.endswith(".txt"):
                sources[f] = os.path.join(d, f)
    docs = {}
    with open(MANIFEST, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            matches = [p for f, p in sources.items() if f.startswith(row["id"] + "-")]
            if len(matches) != 1:
                raise RuntimeError("manifest id %s matches %d source files" % (row["id"], len(matches)))
            doc = Doc(row, matches[0])
            for path in (doc.pdf, doc.docx):
                if path and not os.path.exists(path):
                    raise RuntimeError("corpus file missing: %s" % path)
            docs[row["id"]] = doc
    return docs


def span_segment_docs(span, gold_ids, docs):
    """Gold documents in which span is a verbatim substring of one body segment."""
    return [g for g in gold_ids if g in docs and any(span in seg for seg in docs[g].segments)]


def span_count(span, doc):
    """How often the normalised span occurs in the normalised body of doc."""
    k, hay = match_key(span), doc.body_key
    return hay.count(k) if k else 0


# --------------------------------------------------------------------------- tables
def find_cell(ref, docs):
    """(rows, r, c) of the table cell named by {"doc","header","row"[,"occurrence"]}.

    header is the exact text of the column's header cell and row the exact text of the row's
    first cell. Two tables in one document can share both (GLF-004 housing and transport); then
    "occurrence" (0-based, document order) picks one. Raises ValueError unless exactly one cell."""
    doc = docs[ref["doc"]]
    hits = []
    for rows in doc.tables():
        if ref["header"] not in rows[0]:
            continue
        c = rows[0].index(ref["header"])
        for r in range(1, len(rows)):
            if rows[r] and rows[r][0] == ref["row"]:
                hits.append((rows, r, c))
    if "occurrence" in ref:
        hits = hits[ref["occurrence"]:ref["occurrence"] + 1] if ref["occurrence"] < len(hits) else []
    if len(hits) != 1:
        raise ValueError("table_ref %r matches %d cells" % (ref, len(hits)))
    return hits[0]


def cell_values(cell):
    """Numbers in a table cell as (key, ascii display), ignoring times, grades and dates."""
    t = western(cell)
    t = TIME.sub(" ", t)
    t = GRADE.sub(" ", t)
    t = DATE.sub(" ", t)
    out = []
    for n in NUM.findall(t):
        key = n.replace(",", "")
        if key.strip("0.") == "":
            continue                       # "Day 0" and similar
        out.append((key, n))
    return out


def table_siblings(q, docs):
    """Sibling numbers (row and column of every table_refs cell) that must be forbidden."""
    fact_keys = {num_key(a) for grp in q.get("facts", []) for a in grp}
    exempt = {num_key(v) for v in q.get("sibling_exempt", [])}
    in_question = question_numbers(q["question"])
    seen, out = set(), []
    for ref in q.get("table_refs", []):
        rows, r, c = find_cell(ref, docs)
        cells = [rows[r][j] for j in range(1, len(rows[r])) if j != c]          # row siblings
        cells += [rows[i][c] for i in range(1, len(rows)) if i != r and c < len(rows[i])]  # column
        for cell in cells:
            for key, disp in cell_values(cell):
                if key in fact_keys or key in exempt or key in in_question or key in seen:
                    continue
                seen.add(key)
                out.append(disp)
    return out


def with_indic(values):
    """Each value followed by its Arabic-Indic form, without duplicates, order kept."""
    out = []
    for v in values:
        for form in (v, to_indic(v)):
            if form not in out:
                out.append(form)
    return out


# --------------------------------------------------------------------------- derived fields
def compute_splits(questions):
    """(bucket, fact_id) -> dev|test. D questions inherit the split of their T fact."""
    facts = {}
    for q in questions:
        if q["bucket"] != "D":
            facts.setdefault(q["bucket"], set()).add(q["fact_id"])
    split = {}
    for bucket, fids in facts.items():
        order = sorted(fids, key=lambda f: hashlib.sha256(f.encode("utf-8")).hexdigest())
        k = math.ceil(len(order) / 3)
        for i, f in enumerate(order):
            split[(bucket, f)] = "dev" if i < k else "test"
    for q in questions:
        if q["bucket"] == "D":
            split[("D", q["fact_id"])] = split[("T", q["fact_id"])]
    return split


def derive(q, docs, split):
    """Return the derived fields for one question (does not modify q)."""
    spans = q.get("evidence_spans", [])
    span_docs = []
    for sp in spans:
        hit = span_segment_docs(sp, q.get("gold_docs", []), docs)
        span_docs.append(hit[0] if len(hit) == 1 else None)
    ratio, run = overlap_stats(q["question"], spans)
    sib = with_indic(table_siblings(q, docs))
    return {"split": split[(q["bucket"], q["fact_id"])], "span_docs": span_docs,
            "span_has_fact": [span_has_fact(sp, q.get("facts", [])) for sp in spans],
            "overlap_ratio": ratio, "max_shared_run": run, "forbidden_siblings": sib}


def span_has_fact(span, facts):
    """True if the span itself contains an alternative of at least one fact group."""
    return any(contains_value(span, alt) for grp in facts for alt in grp)


def seal(questions):
    canon = json.dumps(questions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def seal_file_text(data, file_sha256):
    """results/questions.sha256: comment header plus one sha256sum -c line for the questions file."""
    return ("# v1.0 questions.json seal (PLAN.md 5.1), written by code/eval/validate_gold.py --write.\n"
            "# Check from the brief folder: sha256sum -c results/questions.sha256\n"
            "# questions version %s\n"
            "# questions digest %s\n"
            "%s  %s\n" % (data.get("version"), data.get("sealed_sha256_of_questions"), file_sha256, SEAL_LABEL))


def ordered(q):
    out = {k: q[k] for k in KEY_ORDER if k in q}
    out.update({k: v for k, v in q.items() if k not in out})
    return out


def finalise(data, docs):
    """Recompute derived fields, merge table siblings into forbidden, and seal. Returns new data."""
    qs = data["questions"]
    split = compute_splits(qs)
    new = []
    for q in qs:
        q = dict(q)
        d = derive(q, docs, split)
        q.update(d)
        forb = list(q.get("forbidden", []))
        for v in d["forbidden_siblings"]:
            if v not in forb:
                forb.append(v)
        q["forbidden"] = forb
        new.append(ordered(q))
    out = {"version": data["version"], "sealed_sha256_of_questions": seal(new),
           "notes": data.get("notes", []), "questions": new}
    return out


def dump(data):
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def write_seal_file(path, data, file_sha256):
    """Write the seal file atomically, mode 0644 (mkstemp would leave 0600)."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".seal.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(seal_file_text(data, file_sha256))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# --------------------------------------------------------------------------- extraction check
class Extractor:
    """Caches the normalised extracted text of each built corpus file."""

    def __init__(self):
        self.cache = {}

    def text(self, path):
        if path not in self.cache:
            if path.endswith(".pdf"):
                r = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True,
                                   text=True, timeout=120)
                if r.returncode != 0:
                    raise RuntimeError("pdftotext failed on %s: %s" % (os.path.basename(path),
                                                                     r.stderr.strip()[-200:]))
                raw = r.stdout
            else:
                raw = bc.docx_text(path)
            self.cache[path] = (match_key(raw), strict_key(raw))
        return self.cache[path]


def check(data, docs, formats):
    """List of result dicts, one per (question, span, gold document, format)."""
    ex = Extractor()
    results = []
    for q in data["questions"]:
        spans = q.get("evidence_spans", [])
        if q.get("answerable") and not spans:
            results.append({"id": q["id"], "span": None, "check": "spans", "ok": False,
                            "detail": "answerable question without evidence spans"})
        for sp in spans:
            hit = span_segment_docs(sp, q.get("gold_docs", []), docs)
            results.append({"id": q["id"], "span": sp, "check": "source", "doc": ",".join(hit),
                            "ok": len(hit) == 1,
                            "detail": "" if len(hit) == 1 else "found verbatim in %d gold documents" % len(hit)})
            for g in hit:
                n = span_count(sp, docs[g])
                results.append({"id": q["id"], "span": sp, "check": "unique", "doc": g, "ok": n == 1,
                                "detail": "" if n == 1 else "occurs %d times in the document" % n})
                for fmt in formats:
                    path = docs[g].pdf if fmt == "pdf" else docs[g].docx
                    if not path:
                        continue
                    loose, strict = ex.text(path)
                    ok = match_key(sp) in loose
                    results.append({"id": q["id"], "span": sp, "check": fmt, "doc": g, "ok": ok,
                                    "strict": strict_key(sp) in strict,
                                    "detail": "" if ok else "not in %s" % os.path.basename(path)})
    return results


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Check and finalise the brief 09 gold set (questions.json).")
    ap.add_argument("--questions", default=QUESTIONS)
    ap.add_argument("--write", action="store_true", help="recompute derived fields and the seal")
    ap.add_argument("--report", help="write every check result to this JSON file")
    ap.add_argument("--formats", default="pdf,docx", help="comma list of pdf,docx")
    ap.add_argument("--seal-file", default=None,
                    help="seal file to write (--write) or verify; default results/questions.sha256 "
                         "when --questions is the default")
    args = ap.parse_args(argv)
    seal_path = args.seal_file or (SEAL_FILE if os.path.abspath(args.questions) == os.path.abspath(QUESTIONS)
                                   else None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")

    formats = [f for f in args.formats.split(",") if f]
    if any(f not in ("pdf", "docx") for f in formats):
        log.error("--formats accepts pdf and docx only")
        return 2
    if "pdf" in formats and not shutil.which("pdftotext"):
        log.error("pdftotext (poppler-utils) is not on PATH")
        return 2
    try:
        with open(args.questions, encoding="utf-8") as fh:
            data = json.load(fh)
        docs = load_docs()
    except (OSError, ValueError, RuntimeError) as exc:
        log.error("cannot load inputs: %s", exc)
        return 2

    if args.write:
        try:
            new = finalise(data, docs)
        except (KeyError, ValueError) as exc:
            log.error("cannot derive fields: %s", exc)
            return 2
        text = dump(new)
        # mkstemp creates the temp file 0600; keep the questions file's own mode across os.replace
        mode = os.stat(args.questions).st_mode & 0o7777
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(args.questions)),
                                   prefix=".questions.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, mode)
            os.replace(tmp, args.questions)
        except OSError as exc:
            log.error("cannot write %s: %s", args.questions, exc)
            if os.path.exists(tmp):
                os.remove(tmp)
            return 2
        data = new
        log.info("derived fields written; seal %s", new["sealed_sha256_of_questions"])
        if seal_path:
            try:
                write_seal_file(seal_path, data, hashlib.sha256(text.encode("utf-8")).hexdigest())
            except OSError as exc:
                log.error("cannot write %s: %s", seal_path, exc)
                return 2
            log.info("seal file written: %s", seal_path)
    elif seal(data["questions"]) != data.get("sealed_sha256_of_questions"):
        log.error("seal mismatch: questions changed since the last --write")

    try:
        results = check(data, docs, formats)
    except (RuntimeError, OSError, KeyError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        log.error("extraction failed: %s", exc)
        return 2
    bad = [r for r in results if not r["ok"]]
    for r in bad:
        log.error("%-9s %-6s %-12s %s | %s", r["id"], r["check"], r.get("doc", ""),
                  r["detail"], r["span"])
    loose_only = [r for r in results if r["check"] in ("pdf", "docx") and r["ok"] and not r["strict"]]
    by_check = {}
    for r in results:
        by_check.setdefault(r["check"], [0, 0])[0 if r["ok"] else 1] += 1
    for c, (ok, ko) in sorted(by_check.items()):
        log.info("check %-7s ok %4d  failed %d", c, ok, ko)
    log.info("%d extraction matches needed whitespace removal (bidi spacing); informational",
             len(loose_only))
    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8") as fh:
                json.dump(results, fh, ensure_ascii=False, indent=1)
        except OSError as exc:
            log.error("cannot write report: %s", exc)
            return 2
    seal_ok = seal(data["questions"]) == data.get("sealed_sha256_of_questions")
    file_ok = True
    if seal_path:
        with open(args.questions, "rb") as fh:
            want = seal_file_text(data, hashlib.sha256(fh.read()).hexdigest())
        try:
            with open(seal_path, encoding="utf-8") as fh:
                file_ok = fh.read() == want
        except OSError as exc:
            log.error("cannot read seal file %s: %s", seal_path, exc)
            file_ok = False
        if not file_ok:
            log.error("seal file %s does not match %s: run --write", seal_path, args.questions)
    if bad or not seal_ok or not file_ok:
        log.error("%d span check(s) failed%s%s", len(bad), "" if seal_ok else "; seal mismatch",
                  "" if file_ok else "; seal file mismatch")
        return 1
    log.info("all %d span checks passed; seal %s", len(results), data["sealed_sha256_of_questions"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
