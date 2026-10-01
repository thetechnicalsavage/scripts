#!/usr/bin/env python3
# v1.2 - brief 09 (Select AI RAG tuning, EN + AR): print the stored statistics as VERBATIM terminal
#        transcripts for the screenshots of PLAN.md section 6, the way 10_capture_shots.py saves its
#        file shots: a neutral '$ ' line, then the output, written only after the lost-character
#        check passes (fail closed).
#        v1.2: public copy: tools/capture.py and tools/leak_scan.py are lab-internal and not shipped;
#              their normalise, lost-character gate and atomic write are inlined below, the leak scan
#              and --patterns-file are removed. The printed numbers are unchanged.
#          90  results table, dev and test, per GO-3 config          (stats_answers_<UTC>.json)
#          91  per-question verdict grid, S1 vs S10, test split      (answer files; checked against H3
#                                                                     of stats_family_<UTC>.json)
#          92  significance table H1-H5, Holm, Wilson CIs            (stats_family_<UTC>.json)
#          45  S1 distance-metric check, determinism and runsql      (results/supp_v24/, eval_retrieval.py
#                                                                     summary line, printed as stored)
#        No printed line may start like a render_transcript.py marker ("!! ", "++ ", "## ", "<<rtl",
#        "rtl>>"), so make_shots.py highlights stay the only markers in a rendered excerpt.
#        v1.1 (Codex review 1): 91 and 92 check that every stored Holm p and reject follows from the stored
#              p values (stats.holm) over exactly the pre-registered H1-H5 rows; 91 refuses an H3 row whose
#              a/b are not S10/S1. v1.0: first version.
#        Every table is printed twice, "scorer" and "model-adjudicated". The second view is the
#        scorer's verdicts with results/model_adjudication.jsonl applied: a BLIND MODEL adjudication,
#        not a human audit. stats.py calls that view "adjudicated" and its record field
#        "human_verdict"; this tool refuses to print it unless every record of the audit file says
#        it is not a human audit, so the label can never be wrong.
#
# Run as : the local authoring workstation; no database, no network.
# Usage  : python3 print_stats.py [--results DIR] [--out-dir DIR] [--overwrite | --stdout]
#                                 [--answers FILE] [--family FILE] [--metric-file FILE]
#                                 {90,91,92,45}
#          defaults: --results <brief>/results, --out-dir <brief>/transcripts, the stats files with the
#          latest UTC stamp in their name, the one S1 file under results/supp_v24/ with a metric check.
# Re-run : safe. Reads only; a transcript is replaced atomically and never without --overwrite
#          (it is evidence). Numbers are printed as the JSON stores them: no rounding.
# Exit   : 0 printed or saved | 2 refused (missing or malformed input, a stats file of another kind or
#          split, an audit file that is not the model adjudication, a recomputation that disagrees
#          with the stored file, an existing transcript) | 3 lost-character finding (nothing saved)
#
# Where a number is not in a stored file (the dev rows of 90, the per-question rows of 91), it is
# computed with stats.py's own functions (load_answers, answers_table, answer_outcomes, paired) over
# the answer files the stored file names, and the same computation must first reproduce the stored
# test table (90) or the stored H3 row (91) exactly, in both views; otherwise the tool refuses.
from __future__ import annotations

import argparse
import collections
import glob
import json
import logging
import os
import re
import sys
import tempfile
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "eval"))
sys.path.insert(0, HERE)
import stats as st                       # noqa: E402

log = logging.getLogger("print_stats")
VERSION = "print_stats.py v1.2"
SCRIPT = "print_stats.py"
DEFAULT_RESULTS = os.path.join(BRIEF, "results")
DEFAULT_OUT_DIR = os.path.join(BRIEF, "transcripts")

STAMP = r"[0-9]{8}T[0-9]{6}Z"
ANSWERS_FILE = re.compile(rf"^stats_answers_({STAMP})\.json$")
FAMILY_FILE = re.compile(rf"^stats_family_({STAMP})\.json$")
METRIC_GLOB = os.path.join("supp_v24", "retrieval_RAG_M0_C1024_O128_COS_*.jsonl")
MODEL_AUDIT_MARK = "not a human audit"        # every record's "auditor" must say so (model_adjudication.jsonl)

# GO-3 answer configs (results/go3_configs.md): config id -> (stage, index). Config 12 is answered
# once, with S10's calibrated knobs, and is labelled S10 (PLAN de-duplication).
GO3 = {"1": ("S1", "RAG_M0_C1024_O128_COS"), "2": ("S2", "RAG_M0_C2000_O300_COS"),
       "8": ("S8", "RAG_M1_C1024_O128_COS"), "11": ("S8", "RAG_M4_C1024_O128_COS"),
       "12": ("S10", "RAG_M5_C1024_O128_COS"), "13": ("S8", "RAG_M6_C1024_O128_COS")}
VCODE = {"correct": "C", "wrong": "W", "false_refusal": "R", "contaminated": "X", "hedged": "H", None: "-"}
SCODE = {"ok": "ok", "no_match": "nm", "declined": "dc", "infra_error": "ie", None: "--"}
VIEWS = (("scorer", "scorer view"), ("adjudicated", "model-adjudicated view"))
TRANSCRIPTS = {"90": "90-results-table-dev-test.txt", "91": "91-verdict-grid-s1-vs-s10.txt",
               "92": "92-significance-h1-h5.txt", "45": "45-metric-check-s1.txt"}
WRAP = 100
# a line render_transcript.py would read as a presentation marker must never come from the data
MARKER_LINE = re.compile(r"^(?:!! |\+\+ |## |\s*<<rtl\s*$|\s*rtl>>\s*$)")


class Refused(Exception):
    """An input failed a guard: nothing is printed or written (exit 2)."""


# ---------------------------------------------------------------------------------------------
# transcript helpers, as in the lab's tools/capture.py (lab-internal, not shipped); its leak scan
# step is removed
# ---------------------------------------------------------------------------------------------
LOST_CHARS = re.compile(r"�|¿|\?{3,}")


class GateRefused(Exception):
    """The lost-character gate refused the transcript (exit 3)."""


def normalise(out: str) -> str:
    lines = [ln.rstrip() for ln in out.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def gate(text: str, *, name: str):
    """The lost-character check a transcript must pass before it is saved. Raises GateRefused."""
    problems = []
    for n, line in enumerate(text.split("\n"), 1):
        if LOST_CHARS.search(line):
            problems.append(f"{name}:{n}: lost characters ('?', U+00BF or U+FFFD) - check NLS_LANG")
    if problems:
        raise GateRefused(problems)


def write_atomic(path: str, text: str):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".capture_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Out:
    def __init__(self):
        self.lines = []

    def __call__(self, s=""):
        self.lines.append(s)

    def text(self):
        return "\n".join(self.lines)


def _st(fn, *args, **kw):
    """stats.py stops with SystemExit(message) on a bad input; here that is a refusal."""
    try:
        return fn(*args, **kw)
    except SystemExit as e:
        raise Refused(str(e)) from None


def norm(x):
    """JSON round trip: int keys become strings, tuples lists, as in a stored stats file."""
    return json.loads(json.dumps(x, sort_keys=True))


def num(v) -> str:
    """A stored number exactly as the JSON holds it (no rounding)."""
    return json.dumps(v)


def rate(r) -> str:
    """x/n rate [lo-hi] of a stats._r() dict, as stored."""
    lo, hi = r["ci95"]
    return f"{r['x']}/{r['n']} {num(r['rate'])} [{num(lo)}-{num(hi)}]"


def short_index(idx: str) -> str:
    """RAG_M0_C1024_O128_COS -> 'M0 1024/128' (as make_charts.py labels it); anything else as is."""
    m = re.fullmatch(r"RAG_([A-Z0-9]+)_C([0-9]+)_O([0-9]+)_[A-Z]+", idx or "")
    return f"{m.group(1)} {m.group(2)}/{m.group(3)}" if m else str(idx)


def natural(qid: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"([0-9]+)", qid)]


# ---------------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------------
def latest(results, pattern, what):
    try:
        names = sorted(n for n in os.listdir(results) if pattern.match(n))
    except OSError as e:
        raise Refused(f"cannot list the results folder: {e.strerror}") from None
    if not names:
        raise Refused(f"no {what} file in the results folder")
    return os.path.join(results, max(names, key=lambda n: pattern.match(n).group(1)))


def load_json(path, kind):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except OSError as e:
        raise Refused(f"{os.path.basename(path)}: {e.strerror}") from None
    except ValueError as e:
        raise Refused(f"{os.path.basename(path)}: not JSON ({e})") from None
    if not isinstance(d, dict) or d.get("kind") != kind:
        raise Refused(f"{os.path.basename(path)}: not a stats.py '{kind}' file")
    res = d.get("result")
    if not isinstance(res, dict) or not all(k in res for k in ("scorer", "adjudicated", "audit")):
        raise Refused(f"{os.path.basename(path)}: result needs 'scorer', 'adjudicated' and 'audit'")
    if not isinstance(d.get("inputs"), list) or not d.get("questions_digest"):
        raise Refused(f"{os.path.basename(path)}: inputs or questions_digest missing")
    return d


def input_paths(results, d, prefix):
    """The input files a stats file names (basenames under results/) whose name starts with prefix."""
    out = []
    for name in d["inputs"]:
        if not isinstance(name, str) or os.path.basename(name) != name:
            raise Refused(f"stats input {name!r} is not a plain file name")
        if name.startswith(prefix):
            p = os.path.join(results, name)
            if not os.path.isfile(p):
                raise Refused(f"{name}: named by the stats file but not in the results folder")
            out.append(p)
    if not out:
        raise Refused(f"the stats file names no {prefix}* input")
    return out


def check_model_audit(results, d):
    """The audit file of the adjudicated view must be the blind model adjudication. Returns its
    basename and record count."""
    name = (d["result"]["audit"] or {}).get("file")
    if not name:
        raise Refused("the stats file has no audit file: there is no model-adjudicated view to print")
    if os.path.basename(name) != name:
        raise Refused(f"audit file {name!r} is not a plain file name")
    path = os.path.join(results, name)
    if not os.path.isfile(path):
        raise Refused(f"{name}: named by the stats file but not in the results folder")
    n = 0
    for _, r in _st(lambda: list(st.read_jsonl([path]))):
        n += 1
        if MODEL_AUDIT_MARK not in str(r.get("auditor", "")):
            raise Refused(f"{name}: record {n} is not marked as a model adjudication "
                          f"(auditor must say '{MODEL_AUDIT_MARK}'); refusing to label it")
    if not n:
        raise Refused(f"{name}: no records")
    return name, path, n


class Answers:
    """The answer files a stats file names, loaded both ways (stats.load_answers), plus each
    run's GENERATE status and the GO-3 configs of the header."""

    def __init__(self, results, d):
        self.paths = input_paths(results, d, "rag_")
        hdrs = _st(st.headers, self.paths)
        digest = _st(st.check_headers, hdrs)
        if digest != d["questions_digest"]:
            raise Refused("the answer files carry another questions seal than the stats file")
        self.audit_name, audit_path, self.audit_records = check_model_audit(results, d)
        self.adj = _st(st.load_answers, self.paths, _st(st.load_audit, audit_path))
        self.scorer = st.scorer_view(self.adj)
        self.view = {"scorer": self.scorer, "adjudicated": self.adj}
        self.stamps = sorted({str(h.get("stamp")) for h in hdrs})
        self.runs = sorted({int(h.get("run")) for h in hdrs if h.get("run") is not None})
        self.configs = {}                             # index -> (config_id, stage, k, thr)
        for h in hdrs:
            for c in h.get("configs") or []:
                cid, idx = str(c.get("config_id")), c.get("index")
                p = c.get("pairing") or {}
                if cid not in GO3 or GO3[cid][1] != idx:
                    raise Refused(f"answer config {cid} ({idx}) is not a GO-3 config of results/go3_configs.md")
                row = (cid, GO3[cid][0], p.get("match_limit"), p.get("similarity_threshold"))
                if self.configs.setdefault(idx, row) != row:
                    raise Refused(f"{idx}: the run headers disagree on its config or knobs")
        self.status = {}                              # (index, id, run) -> status of the record kept
        for _, r in _st(lambda: list(st.read_jsonl(self.paths))):
            if r.get("type") != "answer":
                continue
            key = (r["index"], r["id"], r["run"])
            if r.get("status") in ("ok", "no_match", "declined") or key not in self.status:
                self.status[key] = r.get("status")    # the same rule as stats.load_answers

    def config(self, idx):
        if idx not in self.configs:
            raise Refused(f"{idx}: not in the answer files' header")
        return self.configs[idx]


def files_line(paths):
    return ", ".join(os.path.basename(p) for p in paths)


def views_line(out, a: Answers):
    out("   scorer view           : the eval_norm verdicts as scored")
    out(f"   model-adjudicated view: the same answers with the verdicts of {a.audit_name} applied")
    out(f"                           (a blind model adjudication of {a.audit_records} answers; not a human audit)")


# ---------------------------------------------------------------------------------------------
# 90: results table, dev and test
# ---------------------------------------------------------------------------------------------
def shot_90(results, answers_path, family_path=None) -> str:
    d = load_json(answers_path, "answers")
    if d.get("split") != "test":
        raise Refused(f"{os.path.basename(answers_path)}: split {d.get('split')!r}; 90 prints a stored test table")
    a = Answers(results, d)
    tables = {}
    for view, _ in VIEWS:
        test = norm(st.answers_table(a.view[view], "test"))
        if test != norm(d["result"][view]):
            raise Refused(f"{view}: the test table computed from the answer files differs from "
                          f"{os.path.basename(answers_path)}")
        tables[view] = {"test": d["result"][view], "dev": norm(st.answers_table(a.view[view], "dev"))}
    if norm(dict(st.audit_summary(a.adj), file=a.audit_name)) != norm(d["result"]["audit"]):
        raise Refused(f"the audit block computed from the answer files differs from {os.path.basename(answers_path)}")

    out = Out()
    out("== 90  answer accuracy per GO-3 config, dev and test split (answerable: majority of 3 runs)")
    out(f"   test: {os.path.basename(answers_path)} (stats.py, split test), printed as stored")
    out("   dev : no stored stats file; computed here with stats.py's answers_table over the same answer")
    out("         files and adjudication file; the test rows computed the same way equal the stored file")
    out(f"   answers: {files_line(a.paths)}")
    out(f"   questions seal {d['questions_digest']}; runs {', '.join(map(str, a.runs))}")
    views_line(out, a)
    same = True
    for view, label in VIEWS:
        out()
        out(f"-- {label}")
        out(f"   {'stage':<5}  {'cfg':<3}  {'index':<12}  {'k':<2}  {'thr':<4}  {'split':<5}  "
            f"{'answerable correct [95% CI]':<33} {'runs 1/2/3':<10}  {'refused':<7}  {'stable':<6}  infra")
        rows = []
        for split in ("dev", "test"):
            for r in tables[view][split]:
                if r["scope"] != "core":
                    continue
                cid, stage, k, thr = a.config(r["index"])
                rows.append(((int(cid), split), stage, cid, r, split, k, thr))
        for _, stage, cid, r, split, k, thr in sorted(rows, key=lambda x: x[0]):
            per = r["answerable_per_run"]
            runs = "/".join(str(per[str(run)]["x"]) if str(run) in per else "-" for run in a.runs)
            u = r["unanswerable_majority_descriptive"]
            unans = f"{u['x']}/{u['n']}"
            stable = f"{r['stable_all_runs']}/{r['questions']}"
            out(f"   {stage:<5}  {cid:<3}  {short_index(r['index']):<12}  {num(k):<2}  {num(thr):<4}  {split:<5}  "
                f"{rate(r['answerable_majority']):<33} {runs:<10}  {unans:<7}  {stable:<6}  {r['infra_unscored']}")
    for split in ("dev", "test"):
        for s, m in zip(tables["scorer"][split], tables["adjudicated"][split]):
            if s["index"] != m["index"] or s["answerable_majority"] != m["answerable_majority"]:
                same = False
    out()
    out("   index: RAG_<model>_C<chunk_size>_O<overlap>_COS; stage and cfg: results/go3_configs.md;")
    out("          k and thr: match_limit and similarity_threshold of the index, from the answer files' header")
    out("   answerable correct: x/n rate [Wilson 95% CI], where the majority of the scored runs is correct")
    out("   refused: unanswerable questions whose majority verdict is correct (a refusal); descriptive only")
    out("   stable: same verdict in every run, of all questions; infra: runs lost to an infrastructure error")
    out(f"   answerable correct is the same in both views for every config and split: {'yes' if same else 'no'}")
    return out.text()


# ---------------------------------------------------------------------------------------------
# 91: per-question verdict grid, S1 vs S10
# ---------------------------------------------------------------------------------------------
def h3_spec():
    for h in st.DEFAULT_SPEC["hypotheses"]:
        if h["id"] == "H3":
            return h
    raise Refused("stats.DEFAULT_SPEC has no H3")


def family_row(d, view, hid):
    rows = [r for r in (d["result"][view] or {}).get("rows", []) if r.get("id") == hid]
    if len(rows) != 1:
        raise Refused(f"{view}: the family file has {len(rows)} rows {hid}")
    return rows[0]


PAIRED_KEYS = ("n", "a_only", "b_only", "p", "a_rate", "b_rate", "better", "label")


def check_holm(d, view, name):
    """Codex review 1: p_holm and reject_holm are printed, so they must follow from the stored p of
    every row of the family (stats.holm, m = the number of rows, missing input counted as p = 1)."""
    res = d["result"][view] or {}
    rows = res.get("rows") or []
    family = [h["id"] for h in st.DEFAULT_SPEC["hypotheses"]]
    if [r.get("id") for r in rows] != family:      # Codex re-review: m is the declared family, never fewer rows
        raise Refused(f"{view}: the family file's rows {[r.get('id') for r in rows]} are not the pre-registered "
                      f"{family}")
    adj = st.holm({r["id"]: (None if r.get("missing") else r.get("p")) for r in rows},
                  res.get("alpha", st.ALPHA), m=len(family))
    for r in rows:
        if (r.get("p_holm"), r.get("reject_holm")) != (adj[r["id"]]["p_holm"], adj[r["id"]]["reject"]):
            raise Refused(f"{view}: {r['id']} Holm p / reject in {name} do not follow from the stored p values")


def shot_91(results, answers_path, family_path) -> str:
    fam = load_json(family_path, "family")
    ans = load_json(answers_path, "answers")
    if fam["questions_digest"] != ans["questions_digest"]:
        raise Refused("the family and answers stats files carry different questions seals")
    a = Answers(results, fam)
    h = h3_spec()
    rows = {}
    for view, _ in VIEWS:
        row = family_row(fam, view, "H3")
        if row.get("missing"):
            raise Refused(f"{view}: H3 is missing in the family file")
        sides = {}
        for side in ("a", "b"):
            sides[side] = st.answer_outcomes(a.view[view], {"index": row[side]["index"]}, h)
            if not sides[side]:
                raise Refused(f"{view}: no answers for {row[side]['index']}")
        got = st.paired(sides["a"], sides["b"])
        if norm({k: got[k] for k in PAIRED_KEYS}) != norm({k: row.get(k) for k in PAIRED_KEYS}):
            raise Refused(f"{view}: H3 computed from the answer files differs from {os.path.basename(family_path)}")
        rows[view] = (row, sides)
    for view, _ in VIEWS:
        check_holm(fam, view, os.path.basename(family_path))
    row = rows["scorer"][0]
    s10, s1 = row["a"]["index"], row["b"]["index"]
    c10, c1 = a.config(s10), a.config(s1)
    if (c10[1], c1[1]) != ("S10", "S1") or any((r["a"]["index"], r["b"]["index"]) != (s10, s1)
                                               for r, _ in rows.values()):
        raise Refused(f"H3 must compare S10 (a) with S1 (b) in both views; the family file binds a = {s10} "
                      f"({c10[1]}), b = {s1} ({c1[1]})")               # Codex review 1: no mislabelled sides
    qids = sorted(rows["scorer"][1]["a"], key=natural)

    def runs(view, idx, q):
        e = a.view[view][(idx, q)]
        return [e["runs"].get(r) for r in a.runs]

    def stat(idx, q):
        return [SCODE.get(a.status.get((idx, q, r)), "??") for r in a.runs]

    def yn(v):
        return "y" if v else ("n" if v is not None else "-")

    def codes(view, idx, q):
        return " ".join(VCODE.get(v, "?") for v in runs(view, idx, q))

    out = Out()
    out(f"== 91  per-question verdicts, S1 baseline vs S10 tuned, test split, answerable ({len(qids)} questions)")
    out(f"   answers: {files_line(a.paths)}")
    out(f"   checked against H3 of {os.path.basename(family_path)} (n, a only, b only, p, rates): equal in both views")
    out(f"   S1  = {s1} (config {c1[0]}, k {num(c1[2])}, thr {num(c1[3])})")
    out(f"   S10 = {s10} (config {c10[0]}, k {num(c10[2])}, thr {num(c10[3])})")
    out("   verdict per run (eval_norm): C correct  W wrong  R false refusal  X contaminated  H hedged  - unscored")
    out("   GENERATE status per run    : ok  nm no_match (no chunk above the threshold)  dc declined")
    out("   maj: the majority of the runs is correct (y/n)")
    views_line(out, a)
    for view, label in VIEWS:
        sides = rows[view][1]
        out()
        out(f"-- {label}")
        if view == "scorer":
            out("   id        bucket  S1 runs  S1 status  maj   S10 runs  S10 status  maj   outcome")
            for q in qids:
                e = a.view[view][(s1, q)]
                o1, o10 = sides["b"][q], sides["a"][q]
                what = ("both" if o1 and o10 else "S1 only" if o1 else "S10 only" if o10 else "neither")
                out(f"   {q:<9} {e['q'].get('bucket', ''):<7} {codes(view, s1, q):<8} {' '.join(stat(s1, q)):<10} "
                    f"{yn(o1):<5} {codes(view, s10, q):<9} {' '.join(stat(s10, q)):<11} {yn(o10):<5} {what}")
        else:
            sc = rows["scorer"][1]
            changed = [q for q in qids if runs("scorer", s1, q) != runs(view, s1, q)
                       or runs("scorer", s10, q) != runs(view, s10, q)]
            out(f"   questions with a run verdict changed by the model adjudication: {len(changed)} of {len(qids)}")
            out("   id        S1 runs: scorer -> model   S10 runs: scorer -> model   maj S1   maj S10")
            for q in changed:
                a1 = f"{codes('scorer', s1, q)} -> {codes(view, s1, q)}"
                a10 = f"{codes('scorer', s10, q)} -> {codes(view, s10, q)}"
                m1 = f"{yn(sc['b'][q])} -> {yn(sides['b'][q])}"
                out(f"   {q:<9} {a1:<26} {a10:<27} {m1:<8} {yn(sc['a'][q])} -> {yn(sides['a'][q])}")
        both = sum(1 for q in qids if sides["a"][q] and sides["b"][q])
        neither = sum(1 for q in qids if sides["a"][q] is False and sides["b"][q] is False)
        r = rows[view][0]
        out(f"   both correct {both}   S1 only {r['b_only']}   S10 only {r['a_only']}   neither {neither}   (n {r['n']})")
        out(f"   S1 {rate(r['b_rate'])}   S10 {rate(r['a_rate'])}")
        out(f"   exact McNemar on the {r['a_only'] + r['b_only']} discordant questions: p {num(r['p'])}, "
            f"Holm p {num(r['p_holm'])} ({r['label']})")
    nm_all = [q for q in qids if all(s == "nm" for s in stat(s10, q))]
    total = collections.Counter(s for q in qids for s in stat(s10, q))
    out()
    out(f"   S10 status over {sum(total.values())} answers: " +
        ", ".join(f"{k} {v}" for k, v in sorted(total.items())))
    out(f"   S10 no_match in every run: {len(nm_all)} questions; S1 correct (scorer majority) on "
        f"{sum(1 for q in nm_all if rows['scorer'][1]['b'][q])} of them")
    return out.text()


# ---------------------------------------------------------------------------------------------
# 92: significance table H1-H5
# ---------------------------------------------------------------------------------------------
def retrieval_headers(results, fam):
    """{index: header} of the retrieval files the family file names (first line of each)."""
    out = {}
    for p in input_paths(results, fam, "retrieval_"):
        try:
            with open(p, encoding="utf-8") as f:
                h = json.loads(f.readline())
        except (OSError, ValueError) as e:
            raise Refused(f"{os.path.basename(p)}: header unreadable ({e})") from None
        if h.get("type") != "header" or not h.get("index"):
            raise Refused(f"{os.path.basename(p)}: the first line is not a retrieval header")
        if h["index"] in out:
            raise Refused(f"two retrieval files for {h['index']} in the family file's inputs")
        out[h["index"]] = h
    return out


def side_text(side, hdrs, layer):
    """One side of a hypothesis: its index, direction or run label, and k / threshold, the stored
    symbol followed by the value the retrieval header resolves it to."""
    parts = [side.get("index") or "<unbound>"]
    for key in ("direction", "run_label"):
        if side.get(key):
            parts.append(side[key])
    if layer == "retrieval":
        h = hdrs.get(side.get("index"))
        if h is None:
            raise Refused(f"{side.get('index')}: no retrieval file among the family file's inputs")
        k = side.get("k", 5)
        if isinstance(k, str):
            k = f"{k} (= {_st(st._k_for, side, h)})"
        parts.append(f"k {k}")
        t = side.get("threshold")
        if t == "similarity_threshold":
            parts.append(f"threshold {t} (= {num((h.get('pairing') or {}).get('similarity_threshold'))})")
        elif t is not None:
            parts.append(f"threshold {num(t)}")
    else:
        parts.append("answers, majority of 3")
    return "  ".join(parts)


def shot_92(results, answers_path, family_path) -> str:
    fam = load_json(family_path, "family")
    hdrs = retrieval_headers(results, fam)
    for view, _ in VIEWS:
        check_holm(fam, view, os.path.basename(family_path))
    name, _, nrec = check_model_audit(results, fam)
    spec = {h["id"]: h for h in st.DEFAULT_SPEC["hypotheses"]}
    out = Out()
    out("== 92  confirmatory family H1-H5, test split: exact McNemar on discordant pairs, Holm step-down,")
    out("       Wilson 95% CIs (PLAN.md 5.4)")
    out(f"   stored: {os.path.basename(family_path)} (stats.py), printed as stored")
    out(f"   questions seal {fam['questions_digest']}")
    out(f"   answers: {', '.join(n for n in fam['inputs'] if n.startswith('rag_'))}")
    out(f"   retrieval: {len(hdrs)} files, one per index")
    out("   scorer view           : the eval_norm verdicts as scored")
    out(f"   model-adjudicated view: the same answers with the verdicts of {name} applied")
    out(f"                           (a blind model adjudication of {nrec} answers; not a human audit)")
    layers = collections.defaultdict(list)
    for hid, h in spec.items():
        layers[h.get("layer")].append(hid)
    out(f"   retrieval-layer tests (no answer verdicts): {', '.join(layers['retrieval'])}; "
        f"answer-layer: {', '.join(layers['answer'])}")
    views = {}
    for view, label in VIEWS:
        res = fam["result"][view]
        rows = res.get("rows") or []
        views[view] = rows
        out()
        out(f"-- {label} (alpha {num(res.get('alpha'))}, complete {'yes' if res.get('complete') else 'no'})")
        out(f"   {'id':<3} {'a: x/n rate [95% CI]':<30} {'b: x/n rate [95% CI]':<30} {'n':<3} {'a/b only':<9} "
            f"{'p':<9} {'Holm p':<9} Holm")
        for r in rows:
            if r.get("missing"):
                out(f"   {r['id']:<3} missing input: p counted as 1 in Holm; Holm p {num(r.get('p_holm'))}")
                continue
            only = f"{r['a_only']}/{r['b_only']}"
            out(f"   {r['id']:<3} {rate(r['a_rate']):<30} {rate(r['b_rate']):<30} {r['n']:<3} {only:<9} "
                f"{num(r['p']):<9} {num(r['p_holm']):<9} {'reject' if r.get('reject_holm') else 'keep'}")
    out()
    out("   a/b only: discordant pairs (a right and b wrong / b right and a wrong); p: exact McNemar, two-sided;")
    out("   Holm: H0 rejected (reject) or kept (keep) at the alpha above after the Holm step-down over H1-H5")
    out()
    out("   the hypotheses (pre-registered in stats.py DEFAULT_SPEC; a and b as bound in the stored file):")
    for r in views["scorer"]:
        layer = (spec.get(r["id"]) or {}).get("layer", "?")
        claim = textwrap.wrap(str(r.get("claim") or ""), WRAP)
        out(f"   {r['id']}  " + (claim[0] if claim else ""))
        for line in claim[1:]:
            out(f"       {line}")
        if r.get("missing"):
            continue
        out(f"       a: {side_text(r['a'], hdrs, layer)}")
        out(f"       b: {side_text(r['b'], hdrs, layer)}")
        if r.get("condition") == "containable_both":
            out("       condition: containable_both (units whose evidence span fits one chunk on both sides)")
        elif r.get("condition"):
            out(f"       condition: {r['condition']}")
        u = r.get("unconditional")
        if u:
            out(f"       without the condition ({u.get('label')}, outside Holm):")
            out(f"          {rate(u['a_rate'])} vs {rate(u['b_rate'])}, n {u['n']}, p {num(u['p'])}")
    diff = []
    for s, m in zip(views["scorer"], views["adjudicated"]):
        for k in sorted(set(s) | set(m)):
            if s.get(k) != m.get(k):
                diff.append(f"{s.get('id')} {k}")
    if len(views["scorer"]) != len(views["adjudicated"]):
        diff.append("row count")
    out()
    out("   fields that differ between the two views: " + (", ".join(diff) if diff else "none"))
    au = fam["result"]["audit"]
    out(f"   model adjudication ({au.get('file')}; not a human audit): {au.get('applied')} answers adjudicated, "
        f"verdict changed on {au.get('changed')}")
    ag = au.get("agreement") or {}
    if ag.get("n"):
        out(f"      agreement with the scorer {rate(ag)}")
    out(f"      answers the scorer flagged for audit: {au.get('flagged')}, of which not adjudicated: "
        f"{au.get('flagged_unaudited')}")
    changes = [f"{k} {v}" for k, v in sorted((au.get("confusion") or {}).items())
               if k.split("->")[0] != k.split("->")[-1]]
    groups = [", ".join(changes[i:i + 3]) for i in range(0, len(changes), 3)] or ["none"]
    for i, g in enumerate(groups):                            # three per line: an item never splits
        out(("      changes (scorer -> model): " if i == 0 else "         ") + g
            + ("," if i < len(groups) - 1 else ""))
    return out.text()


# ---------------------------------------------------------------------------------------------
# 45: distance-metric check on the S1 index (supplementary S7 run, results/supp_v24/)
# ---------------------------------------------------------------------------------------------
def metric_file(results, explicit=None):
    if explicit:
        return explicit
    found = sorted(glob.glob(os.path.join(results, METRIC_GLOB)))
    if len(found) != 1:
        raise Refused(f"{len(found)} files match results/{METRIC_GLOB}: pass --metric-file")
    return found[0]


def shot_45(results, path) -> str:
    recs = _st(lambda: [r for _, r in st.read_jsonl([path])])
    heads = [r for r in recs if r.get("type") == "header"]
    sums = [r for r in recs if r.get("type") == "summary"]
    checks = [r for r in recs if r.get("type") == "metric_check"]
    if len(heads) != 1 or len(sums) != 1:
        raise Refused(f"{os.path.basename(path)}: needs one header and one summary line "
                      f"({len(heads)} and {len(sums)})")
    h, s = heads[0], sums[0]
    mc = s.get("metric_check")
    if not isinstance(mc, dict) or not all(m in mc for m in ("DOT", "EUCLIDEAN", "MANHATTAN")):
        raise Refused(f"{os.path.basename(path)}: the summary holds no metric_check")
    if any(mc[m].get("n") != len(checks) for m in mc):
        raise Refused(f"{os.path.basename(path)}: metric_check n differs from the {len(checks)} metric_check lines")
    p = h.get("pairing") or {}
    stats = (h.get("index_stats") or {}).get(h.get("index")) or {}
    by_split = collections.Counter(c.get("split") for c in checks)
    rel = os.path.relpath(path, results) if os.path.abspath(path).startswith(os.path.abspath(results) + os.sep) \
        else os.path.basename(path)
    out = Out()
    out("== 45  distance metric on the S1 index: do DOT, EUCLIDEAN and MANHATTAN rank the chunks as COSINE does?")
    out(f"   file: {rel} ({h.get('tool')})")
    out(f"   index {h.get('index')}: {stats.get('chunks')} chunks, {stats.get('objects')} files; model {p.get('model')}; "
        f"index metric {p.get('metric')}")
    out(f"   metric_check lines: {len(checks)} (question x run pairs: " +
        ", ".join(f"{k} {v}" for k, v in sorted(by_split.items())) + f"); each searched the top {h.get('top')} "
        "once per metric")
    out("   identical: the same top-20 order as COSINE (items tied in distance may swap)")
    out()
    out("   stored summary, metric_check, against the COSINE ranking:")
    out("   metric      identical ranking   mean top-10 overlap")
    for m in ("DOT", "EUCLIDEAN", "MANHATTAN"):
        out(f"   {m:<11} {str(mc[m]['identical']) + '/' + str(mc[m]['n']):<19} {num(mc[m]['mean_top10_overlap'])}")
    det = s.get("in_session_determinism")
    if isinstance(det, dict):
        out()
        out("   stored summary, in_session_determinism (a second pass in the same session, top 10):")
        out(f"   compared {det.get('compared')}, different {len(det.get('different') or [])}, "
            f"largest distance difference {num(det.get('max_abs_distance_diff'))}")
    rs = s.get("runsql")
    if isinstance(rs, dict):
        out()
        out("   stored summary, runsql (Select AI's own retrieved rows compared with the harness ranking):")
        out(f"   parsed {rs.get('parsed')}, mean chunk agreement {num(rs.get('mean_chunk_agreement'))}, "
            f"below 0.95: {'yes' if rs.get('below_0.95') else 'no'}")
    return out.text()


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(prog=SCRIPT, description="Print the stored statistics as verbatim transcripts.")
    ap.add_argument("shot", choices=sorted(TRANSCRIPTS))
    ap.add_argument("--results", default=DEFAULT_RESULTS)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--answers", default=None, help="stats_answers_<UTC>.json (default: the latest)")
    ap.add_argument("--family", default=None, help="stats_family_<UTC>.json (default: the latest)")
    ap.add_argument("--metric-file", default=None, help="45: the S1 retrieval file with a metric check")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--overwrite", action="store_true", help="replace an existing transcript")
    g.add_argument("--stdout", action="store_true", help="print the gated transcript; save nothing")
    return ap


def main(argv=None) -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    a = build_parser().parse_args(argv)
    out_path = os.path.join(a.out_dir, TRANSCRIPTS[a.shot])
    if not a.stdout and os.path.exists(out_path) and not a.overwrite:
        log.error("event=exists file=%s (pass --overwrite to replace it: it is evidence)", TRANSCRIPTS[a.shot])
        return 2
    try:
        if a.shot == "45":
            path = metric_file(a.results, a.metric_file)
            log.info("event=input metric_file=%s", os.path.basename(path))
            body = shot_45(a.results, path)
        else:
            ans = a.answers or latest(a.results, ANSWERS_FILE, "stats_answers_<UTC>.json")
            fam = a.family or latest(a.results, FAMILY_FILE, "stats_family_<UTC>.json")
            log.info("event=input answers=%s family=%s", os.path.basename(ans), os.path.basename(fam))
            body = {"90": shot_90, "91": shot_91, "92": shot_92}[a.shot](a.results, ans, fam)
        text = f"$ python3 {SCRIPT} {a.shot}\n" + normalise(body)
        marked = [n for n, ln in enumerate(text.split("\n"), 1) if MARKER_LINE.match(ln)]
        if marked:
            raise Refused(f"line(s) {marked[:5]} start like a render_transcript.py marker")
        gate(text, name=f"transcripts/{TRANSCRIPTS[a.shot]}")   # (the lab's leak scan here is lab-internal: removed)
    except Refused as e:
        log.error("event=refused shot=%s reason=%s", a.shot, e)
        return 2
    except GateRefused as e:
        for p in e.args[0]:
            log.error("event=gate %s", p)
        log.error("event=not_saved reason=gate (fail closed)")
        return 3
    if a.stdout:
        sys.stdout.write(text + "\n")
        return 0
    try:
        write_atomic(out_path, text)
    except OSError as e:
        log.error("event=write_failed file=%s error=%s", TRANSCRIPTS[a.shot], e.strerror)
        return 2
    log.info("event=saved file=%s lines=%d", TRANSCRIPTS[a.shot], text.count("\n") + 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
