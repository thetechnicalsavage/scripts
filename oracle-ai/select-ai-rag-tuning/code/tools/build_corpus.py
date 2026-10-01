#!/usr/bin/env python3
# v1.2 - Corpus builder for brief 09 (Select AI RAG tuning, English + Arabic).
#        v1.2: public copy: the other app is named the existing HR app; India rows say source=hr-app-original.
#        v1.1: Arabic font is Amiri 1.003. Noto Naskh/Sans Arabic draw each dotted letter
#              as a dotless shape plus a separate dot glyph, so extractors lost 56-63% of
#              Arabic words; Amiri keeps 91-98% (local probe, 2026-09-29). Arabic is set
#              ragged (no justify, so no kashida) with kerning off. Gulf documents carry
#              only a page number in the footer, so repeated header text does not fill
#              small chunks. The 48 India PDFs are the existing HR app's originals, copied and hashed,
#              never re-rendered, so production chunking can be reproduced. Every Gulf
#              document also gets a Transitional-OOXML DOCX: Oracle Text documents that
#              PDFs with embedded fonts may not filter correctly, and DOCX is the fallback.
#        Renders corpus/src/gulf/*.txt to A4 PDFs through headless Chromium (correct Arabic
#        shaping and right-to-left layout), writes the DOCX copies through LibreOffice, and
#        records how much of each document's text survives extraction.
#
# Deterministic content: no randomness, no locale parsing. PDFs carry the renderer's
# creation date, so files are content-stable, not byte-identical.
# Needs: python 3.10+, pypdf, poppler-utils (pdftotext, pdffonts, pdfinfo), LibreOffice
#        (soffice), Chromium or chrome-headless-shell.
#
# usage: build_corpus.py [--only ID[,ID...]] [--chrome PATH] [--india-pdf DIR] [--no-docx]
"""
Source markup (UTF-8; one document per .txt):

    #META
    id:        GLF-001-EN          (suffix -AR marks an Arabic document)
    title:     Annual Leave and Public Holidays (Gulf)
    owner:     ...
    version:   ...
    effective: ...
    review:    ...
    applies:   ...
    #BODY
    ## 1. Heading
    A paragraph with **bold**.
    - bullet
    | header | header |
    | cell   | cell   |
"""
import argparse
import csv
import hashlib
import html
import logging
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
GULF_SRC = os.path.join(ROOT, "corpus", "src", "gulf")
INDIA_SRC = os.path.join(ROOT, "corpus", "src", "india")
FONT_DIR = os.path.join(ROOT, "corpus", "fonts")
OUT_PDF = os.path.join(ROOT, "corpus", "pdf")
OUT_DOCX = os.path.join(ROOT, "corpus", "docx")
OUT_HTML = os.path.join(ROOT, "corpus", "build", "html")
MANIFEST = os.path.join(ROOT, "corpus", "manifest.csv")
DEFAULT_INDIA_PDF = os.path.expanduser("~/hr-kb/pdf")       # the existing HR app's originals
DEFAULT_CHROME = os.path.expanduser(
    "~/.cache/ms-playwright/chromium_headless_shell-1208/"
    "chrome-headless-shell-linux64/chrome-headless-shell")

META_KEYS = ("id", "title", "owner", "version", "effective", "review", "applies")


def _cls(*ranges):
    """Regex character class from (first, last) code point pairs - no invisible literals."""
    return "[" + "".join("%s-%s" % (re.escape(chr(a)), re.escape(chr(b))) for a, b in ranges) + "]"


ARABIC = re.compile(_cls((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)))
LATIN = re.compile(r"[A-Za-z]")
TATWEEL = chr(0x0640)
# Removed before comparing: bidi controls, tatweel, and Arabic short-vowel marks (harakat,
# U+064B-U+065F and U+0670), which extractors drop or reorder and retrieval does not need.
IGNORED = re.compile(_cls((0x200E, 0x200F), (0x202A, 0x202E), (0x2066, 0x2069),
                          (0x0640, 0x0640), (0x064B, 0x065F), (0x0670, 0x0670)))
ARABIC_INDIC = {0x0660 + i: ord("0") + i for i in range(10)}
ARABIC_INDIC.update({0x06F0 + i: ord("0") + i for i in range(10)})
# A word is a run of Arabic LETTERS (U+0621-U+064A, so no Arabic comma or digits), a Latin
# word, or a number. Punctuation is not part of a word.
WORD = re.compile(_cls((0x0621, 0x063A), (0x0641, 0x064A)) + "+|[A-Za-z][A-Za-z'-]*[A-Za-z]|[0-9]+")
DOCX_TRANSITIONAL = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# Local sanity gates. The database's own extraction (PLAN.md probe P5) is the real gate.
MIN_INTEGRITY = {"en": 0.98, "ar": 0.90}
MIN_DOCX_INTEGRITY = 0.99

FONTS = (("Doc Sans", "NotoSans-Regular.ttf", 400), ("Doc Sans", "NotoSans-Bold.ttf", 700),
         ("Doc Arabic", "Amiri-Regular.ttf", 400), ("Doc Arabic", "Amiri-Bold.ttf", 700))
ARABIC_FONT_NAME = "Amiri"

log = logging.getLogger("build_corpus")

LABELS = {
    "en": {"doc_id": "Document ID", "owner": "Policy owner", "version": "Version",
           "effective": "Effective date", "review": "Next review", "applies": "Applies to",
           "manual": "Meridian Systems Gulf FZ-LLC  |  Human Resources Policy Manual",
           "page": "Page "},
    "ar": {"doc_id": "رقم الوثيقة", "owner": "الجهة المالكة", "version": "الإصدار",
           "effective": "تاريخ السريان", "review": "المراجعة التالية", "applies": "نطاق التطبيق",
           "manual": "ميريديان سيستمز الخليج  |  دليل سياسات الموارد البشرية",
           "page": "صفحة "},
}


# --------------------------------------------------------------------------- parsing
def parse_source(path):
    """Return (meta, blocks). Raises ValueError on malformed input."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    if "#META" not in raw or "#BODY" not in raw:
        raise ValueError("missing #META or #BODY marker")
    head, body = raw.split("#BODY", 1)
    meta = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("bad meta line %r" % line)
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip()
    missing = [k for k in META_KEYS if k not in meta]
    if missing:
        raise ValueError("missing meta keys %s" % missing)

    blocks, buf_p, buf_l, buf_t = [], [], [], []

    def flush():
        if buf_p:
            blocks.append(("p", " ".join(buf_p)))
            buf_p.clear()
        if buf_l:
            blocks.append(("l", list(buf_l)))
            buf_l.clear()
        if buf_t:
            blocks.append(("t", list(buf_t)))
            buf_t.clear()

    for line in body.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("## "):
            flush()
            blocks.append(("h", s[3:].strip()))
        elif s.startswith("- "):
            if buf_p or buf_t:
                flush()
            buf_l.append(s[2:].strip())
        elif s.startswith("|"):
            if buf_p or buf_l:
                flush()
            buf_t.append([c.strip() for c in s.strip("|").split("|")])
        else:
            if buf_l or buf_t:
                flush()
            buf_p.append(s)
    flush()
    return meta, blocks


def doc_language(meta, blocks):
    """-AR suffix means Arabic. The body must agree, so a mislabelled file fails loudly."""
    lang = "ar" if meta["id"].upper().endswith("-AR") else "en"
    text = " ".join(str(b[1]) for b in blocks)
    n_ar, n_lat = len(ARABIC.findall(text)), len(LATIN.findall(text))
    if lang == "ar" and n_ar < 4 * n_lat:
        raise ValueError("id says Arabic but the body is mostly Latin (%d ar / %d latin)"
                         % (n_ar, n_lat))
    if lang == "en" and n_ar > 0:
        raise ValueError("English document contains %d Arabic characters" % n_ar)
    return lang


def plain_text(meta, blocks):
    """The words a reader of the document sees - used to score extraction."""
    parts = [meta["title"]]
    for kind, payload in blocks:
        if kind in ("h", "p"):
            parts.append(payload)
        elif kind == "l":
            parts.extend(payload)
        elif kind == "t":
            parts.extend(" ".join(r) for r in payload)
    return re.sub(r"\*\*", "", "\n".join(parts))


# --------------------------------------------------------------------------- HTML
def rich(text):
    out = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)


def css_string(text):
    """Quote text for a CSS content: property."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_html(meta, blocks, lang):
    lb = LABELS[lang]
    rtl = lang == "ar"
    start = "right" if rtl else "left"
    fonts = "\n".join('@font-face{font-family:"%s";src:url("file://%s/%s");font-weight:%d}'
                      % (fam, FONT_DIR, f, w) for fam, f, w in FONTS)
    family = '"Doc Arabic","Doc Sans"' if rtl else '"Doc Sans","Doc Arabic"'
    # Footer: page number only. Arabic: ragged paragraphs and no kerning, because
    # justification inserts kashida (U+0640) and both break words in extracted text.
    page_css = """
@page{size:A4;margin:22mm 18mm 20mm 20mm;
 @bottom-center{content:%(page)s counter(page);font:7.5pt %(fam)s;color:#666;vertical-align:top;padding-top:3mm}
}""" % {"fam": family, "page": css_string(lb["page"])}
    body_css = """
body{font-family:%(fam)s;font-size:%(fs)s;line-height:1.5;color:#111;margin:0;%(kern)s}
h1{font-size:16pt;color:#1F3864;margin:0 0 2pt 0}
.sub{font-size:9pt;color:#555;margin:0 0 8pt 0}
h2{font-size:11pt;color:#1F3864;margin:10pt 0 4pt 0;break-after:avoid}
p{margin:0 0 5pt 0;text-align:%(align)s}
ul{margin:0 0 6pt 0;padding-%(start)s:14pt}
li{margin:0 0 2pt 0}
table{border-collapse:collapse;width:100%%;margin:2pt 0 8pt 0;font-size:8.5pt;break-inside:auto}
th{background:#1F3864;color:#fff;text-align:%(start)s;font-weight:700}
th,td{border:0.4pt solid #B4C6E7;padding:3pt 4pt;vertical-align:top}
tr:nth-child(even) td{background:#EDF2FA}
thead{display:table-header-group}
table.meta{background:#EDF2FA;font-size:8pt;table-layout:fixed}
table.meta td{border:0.3pt solid #fff;width:34%%}
table.meta td.k{font-weight:700;width:16%%}
""" % {"fam": family, "start": start, "align": "start" if rtl else "justify",
       "fs": "11pt" if rtl else "9.5pt",
       "kern": "font-kerning:none;" if rtl else ""}

    rows = [(lb["doc_id"], meta["id"], lb["owner"], meta["owner"]),
            (lb["version"], meta["version"], lb["effective"], meta["effective"]),
            (lb["review"], meta["review"], lb["applies"], meta["applies"])]
    meta_html = "<table class=\"meta\">" + "".join(
        "<tr><td class=\"k\">%s</td><td>%s</td><td class=\"k\">%s</td><td>%s</td></tr>"
        % tuple(rich(c) for c in r) for r in rows) + "</table>"

    parts = ["<h1>%s</h1>" % rich(meta["title"]),
             "<div class=\"sub\">%s  |  %s</div>" % (rich(meta["id"]), rich(lb["manual"])),
             meta_html]
    for kind, payload in blocks:
        if kind == "h":
            parts.append("<h2>%s</h2>" % rich(payload))
        elif kind == "p":
            parts.append("<p>%s</p>" % rich(payload))
        elif kind == "l":
            parts.append("<ul>" + "".join("<li>%s</li>" % rich(i) for i in payload) + "</ul>")
        elif kind == "t":
            ncol = max(len(r) for r in payload)
            rws = [r + [""] * (ncol - len(r)) for r in payload]
            parts.append("<table><thead><tr>" + "".join("<th>%s</th>" % rich(c) for c in rws[0])
                         + "</tr></thead><tbody>" + "".join(
                             "<tr>" + "".join("<td>%s</td>" % rich(c) for c in r) + "</tr>"
                             for r in rws[1:]) + "</tbody></table>")
        else:
            raise ValueError("unknown block kind %r" % kind)
    return ("<!doctype html>\n<html lang=\"%s\" dir=\"%s\"><head><meta charset=\"utf-8\">"
            "<title>%s</title><style>%s\n%s\n%s</style></head><body>\n%s\n</body></html>\n"
            % (lang, "rtl" if rtl else "ltr", html.escape(meta["title"]), fonts, page_css,
               body_css, "\n".join(parts)))


# --------------------------------------------------------------------------- render
def run(cmd, timeout):
    """Run an external tool; raise with its stderr on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("%s timed out after %ss" % (os.path.basename(cmd[0]), timeout)) from exc
    if r.returncode != 0:
        raise RuntimeError("%s exited %d: %s" % (os.path.basename(cmd[0]), r.returncode,
                                                  (r.stderr or r.stdout).strip()[-300:]))
    return r.stdout


def print_pdf(chrome, html_path, pdf_path):
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    # virtual-time-budget lets the @font-face files load before printing
    run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
         "--virtual-time-budget=8000", "--print-to-pdf=" + pdf_path, "file://" + html_path],
        timeout=180)
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 1000:
        raise RuntimeError("Chromium produced no PDF for %s" % os.path.basename(html_path))


def to_docx(html_path, out_dir, profile_dir):
    """LibreOffice HTML import -> Word 2007 XML. A private profile keeps it off the user's."""
    run(["soffice", "--headless", "--norestore", "-env:UserInstallation=file://" + profile_dir,
         "--convert-to", "docx:MS Word 2007 XML", "--outdir", out_dir, html_path], timeout=240)
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(html_path))[0] + ".docx")
    if not os.path.exists(out):
        raise RuntimeError("LibreOffice produced no DOCX for %s" % os.path.basename(html_path))
    return out


def norm(s):
    return IGNORED.sub("", unicodedata.normalize("NFKC", s)).translate(ARABIC_INDIC)


def word_integrity(source_text, extracted):
    """Share of the source's words that appear intact in the extracted text."""
    src = WORD.findall(norm(source_text))
    got = set(WORD.findall(norm(extracted)))
    return (sum(1 for w in src if w in got) / len(src)) if src else 1.0


def pdf_facts(pdf):
    fonts = run(["pdffonts", pdf], timeout=60).splitlines()[2:]
    embedded = [ln.split()[0] for ln in fonts if ln.strip()]
    pages = int(re.search(r"Pages:\s+(\d+)", run(["pdfinfo", pdf], timeout=60)).group(1))
    return embedded, pages


def pypdf_text(pdf):
    import pypdf
    return "".join((p.extract_text() or "") for p in pypdf.PdfReader(pdf).pages)


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    if DOCX_TRANSITIONAL not in xml[:4000]:
        raise RuntimeError("%s is not Transitional OOXML" % os.path.basename(path))
    xml = re.sub(r"</w:p>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def clear_outputs():
    """Remove previous build outputs so the corpus folder holds exactly this build."""
    for d, ext in ((OUT_PDF, ".pdf"), (OUT_DOCX, ".docx"), (OUT_HTML, ".html")):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(ext):
                    os.remove(os.path.join(d, f))


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated Gulf document ids to (re)build; no manifest")
    ap.add_argument("--chrome", default=os.environ.get("CHROME_BIN", DEFAULT_CHROME))
    ap.add_argument("--india-pdf", default=DEFAULT_INDIA_PDF,
                    help="folder holding the existing HR app's original India PDFs")
    ap.add_argument("--no-docx", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")

    if not os.path.exists(args.chrome):
        log.error("Chromium not found at %s (set --chrome or CHROME_BIN)", args.chrome)
        return 2
    for tool in ("pdftotext", "pdffonts", "pdfinfo") + (() if args.no_docx else ("soffice",)):
        if not shutil.which(tool):
            log.error("required tool not on PATH: %s", tool)
            return 2
    for _, f, _ in FONTS:
        if not os.path.exists(os.path.join(FONT_DIR, f)):
            log.error("font missing: %s", os.path.join(FONT_DIR, f))
            return 2
    only = set(args.only.split(",")) if args.only else None

    # 1. validate every Gulf source before writing anything
    docs, failed, seen = [], [], {}
    for fname in sorted(f for f in os.listdir(GULF_SRC) if f.endswith(".txt")):
        path = os.path.join(GULF_SRC, fname)
        try:
            meta, blocks = parse_source(path)
            lang = doc_language(meta, blocks)
            if meta["id"] in seen:
                raise ValueError("duplicate id (also in %s)" % seen[meta["id"]])
            if not fname.startswith(meta["id"] + "-"):
                raise ValueError("file name must start with the document id %s" % meta["id"])
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.txt", fname):
                raise ValueError("file name must be ASCII letters, digits, . _ -")
            seen[meta["id"]] = fname
            docs.append({"path": path, "fname": fname, "meta": meta, "blocks": blocks,
                         "lang": lang})
        except Exception as exc:                   # noqa: BLE001 - reported per file
            failed.append((fname, str(exc)))
    india = sorted(f for f in os.listdir(args.india_pdf) if f.endswith(".pdf")) \
        if os.path.isdir(args.india_pdf) else []
    india_src = sorted(f[:-4] for f in os.listdir(INDIA_SRC) if f.endswith(".txt"))
    if [f[:-4] for f in india] != india_src:
        failed.append(("india", "the %d PDFs in %s do not match the %d sources in %s"
                       % (len(india), args.india_pdf, len(india_src), INDIA_SRC)))
    if failed:
        for f, e in failed:
            log.error("invalid source %s: %s", f, e)
        log.error("%d problem(s); nothing was written", len(failed))
        return 1
    if only:
        docs = [x for x in docs if x["meta"]["id"] in only]
    log.info("%d Gulf documents to build (%d Arabic); %d India originals to copy",
             len(docs), sum(x["lang"] == "ar" for x in docs), 0 if only else len(india))

    for d in (OUT_PDF, OUT_DOCX, OUT_HTML):
        os.makedirs(d, exist_ok=True)
    if not only:
        clear_outputs()
    profile = os.path.join(ROOT, "corpus", "build", "lo-profile")
    rows, errors = [], []

    # 2. India: the existing HR app's originals, byte for byte
    for f in ([] if only else india):
        src, dst = os.path.join(args.india_pdf, f), os.path.join(OUT_PDF, f)
        shutil.copyfile(src, dst)
        if sha256(src) != sha256(dst):
            errors.append((f, "copy does not match the original"))
            continue
        embedded, pages = pdf_facts(dst)
        rows.append({"id": f.split("-")[0] + "-" + f.split("-")[1], "lang": "en",
                     "region": "india", "source": "hr-app-original", "title": "",
                     "pdf": f, "docx": "", "pages": pages, "pdf_fonts": " ".join(embedded),
                     "pdftotext_word_integrity": "", "pypdf_word_integrity": "",
                     "tatweel_added": "", "docx_word_integrity": "",
                     "pdf_sha256": sha256(dst)})

    # 3. Gulf: render, check, DOCX twin
    for x in docs:
        base = x["fname"][:-4]
        meta, lang = x["meta"], x["lang"]
        try:
            h = os.path.join(OUT_HTML, base + ".html")
            with open(h, "w", encoding="utf-8") as fh:
                fh.write(to_html(meta, x["blocks"], lang))
            pdf = os.path.join(OUT_PDF, base + ".pdf")
            print_pdf(args.chrome, h, pdf)
            embedded, pages = pdf_facts(pdf)
            if lang == "ar" and not any(ARABIC_FONT_NAME in e for e in embedded):
                raise RuntimeError("Arabic font %s is not embedded (%s)" % (ARABIC_FONT_NAME, embedded))
            text = plain_text(meta, x["blocks"])
            extracted = run(["pdftotext", pdf, "-"], timeout=60)
            integ = word_integrity(text, extracted)
            integ_pypdf = word_integrity(text, pypdf_text(pdf))
            # Kashida the author typed (e.g. the Hijri marker, or a prefix joined to a digit)
            # is legitimate; only kashida added by justification or rendering fails the gate.
            tatweel = extracted.count(TATWEEL) - text.count(TATWEEL)
            if integ < MIN_INTEGRITY[lang]:
                raise RuntimeError("only %.1f%% of words survive pdftotext (gate %.0f%%)"
                                   % (100 * integ, 100 * MIN_INTEGRITY[lang]))
            if tatweel > 0:
                raise RuntimeError("%d kashida characters added in the extracted text" % tatweel)
            docx_integ, docx_name = "", ""
            if not args.no_docx:
                dx = to_docx(h, OUT_DOCX, profile)
                di = word_integrity(text, docx_text(dx))
                if di < MIN_DOCX_INTEGRITY:
                    raise RuntimeError("DOCX keeps only %.1f%% of words" % (100 * di))
                docx_integ, docx_name = "%.3f" % di, os.path.basename(dx)
            rows.append({"id": meta["id"], "lang": lang, "region": "gulf", "source": "rendered",
                         "title": meta["title"], "pdf": os.path.basename(pdf), "docx": docx_name,
                         "pages": pages, "pdf_fonts": " ".join(embedded),
                         "pdftotext_word_integrity": "%.3f" % integ,
                         "pypdf_word_integrity": "%.3f" % integ_pypdf,
                         "tatweel_added": tatweel, "docx_word_integrity": docx_integ,
                         "pdf_sha256": sha256(pdf)})
            log.info("OK   %-12s %s %d p  words intact: pdftotext %.0f%%  pypdf %.0f%%%s",
                     meta["id"], lang, pages, 100 * integ, 100 * integ_pypdf,
                     ("  docx %s" % docx_integ) if docx_integ else "")
        except Exception as exc:                       # noqa: BLE001 - reported per file
            errors.append((x["fname"], str(exc)))
            log.error("FAIL %s: %s", x["fname"], exc)

    if not only:
        with open(MANIFEST, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: (r["region"], r["id"])))
        log.info("manifest: %s (%d rows)", MANIFEST, len(rows))
    if errors:
        for f, e in errors:
            log.error("%s: %s", f, e)
        log.error("%d document(s) failed", len(errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
