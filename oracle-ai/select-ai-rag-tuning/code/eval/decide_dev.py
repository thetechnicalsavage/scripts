#!/usr/bin/env python3
# v1.3 - RAG tuning lab (brief 09): the pre-registered dev-split decisions R1-R7 of DEV_RULES.md
#        (30-Sep-2026, amendments v1.1 A1-A11) from the retrieval JSONL of the built indexes, the
#        stats.py bindings they give (S3_WINNER, BEST_MULTILINGUAL, S10) and the experiments.csv rows
#        they decide (6, 7, 14-17, 19).
#        v1.3 (independent re-check): R5 ranks the models present without waiting for R3 unless the best
#              model is one of M1..M5; a query record missing a key the rules read is refused (exit 2).
#        v1.2 (DEV_RULES v1.1 A1-A11 and check findings 4-8): complete-run guard, A2 ties, row 7 cut at w=640, R5 over the models present with R3's tie-breaks and the R2 rule carried over, cut rows final, R7 = calibrate_threshold in SCORE units, corpus_format from the inputs, lock at run_all.sh's RESULTS_DIR.
#        v1.1 (Codex review 1): R2 lists config 7 literally even when round(0.2 w) = 128 (same index
#              as the winner's own), and row 7 is then an explicit operator decision (JSON, report,
#              WARN) instead of a note; R5 ranks M0..M6 by the R3 primary metric only and reports
#              'undecided' on a tie at the top (R5 names no tie-break).
#        v1.0: first version.
#
# Run as : any user, no database. stdlib only; imports the pure parts of stats.py and
#          eval_retrieval.py (read_jsonl, load_retrieval, check_headers, retrieval_records, _k_for,
#          budget_k, hit_at, calibrate_threshold, INDEX_NAME), so the hit logic is the harness's own.
# Usage  : python3 decide_dev.py --retrieval $RESULTS_DIR/retrieval_<INDEX>_<UTC>.jsonl ... \
#                                [--experiments ../experiments.csv] [--out $RESULTS_DIR] \
#                                [--write-experiments] [--stamp YYYYMMDDTHHMMSSZ]
#          RESULTS_DIR is derived as run_all.sh derives it: ${RESULTS_DIR:-<code>/../results}.
#          One complete run per built index (no --only; split all or dev; exactly one summary
#          record; one pass-1 unmasked record per question), all on one questions seal.
#          Round 1, after builds 1-13: R1 and R3 decide; --write-experiments fills rows 6, 7 (or cuts
#          row 7 when w = 640), 14-17. Round 2, after 6/7 and 14-17 are evaluated (or cut): R2, R4
#          and R5 decide; row 19 is planned or skipped; R6 and R7 decide when S10 is an index that
#          exists. Round 3, only when build 19 was needed: R6 and R7 on it. A rule whose inputs are
#          missing reports 'pending' and names them; it never guesses.
# Re-run : safe and idempotent. Writes $RESULTS_DIR/dev_decisions_<stamp>.json (stamp = --stamp, else
#          the latest header utc of the inputs); the same content again is a no-op, other content
#          under an existing name is refused. experiments.csv changes only with --write-experiments
#          (which needs --out to be RESULTS_DIR), only in the decided rows, atomically (temp file +
#          rename, under RESULTS_DIR/.lab.lock); every other line stays byte-identical. A row whose
#          status is 'cut' is final: never rewritten, reported as cut_skipped with a WARN (A7). A row
#          past planned/skipped (built, evaluated, building, blocked, failed) is never rewritten
#          either: equal decided values are left alone, different ones stop the run.
# Exit   : 0 ok | 1 no usable input (no file, unreadable file, no dev records)
#          2 guard refusal (an incomplete run, mixed seals, a --only run, a split without dev, two
#            files for one index, malformed JSONL or experiments.csv rows, inputs that disagree on
#            corpus_format, a locked row that disagrees, --write-experiments with --out outside
#            RESULTS_DIR, output clash)
from __future__ import annotations

import argparse
import csv
import datetime as dt
import decimal
import fcntl
import functools
import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from fractions import Fraction

log = logging.getLogger("decide_dev")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stats as st                                     # noqa: E402
import eval_retrieval as er                            # noqa: E402

VERSION = "decide_dev.py v1.3"
RULES_VERSION = "DEV_RULES.md v1.1 (pre-registered 30-Sep-2026; amendments A1-A11)"
CODE_DIR = os.path.dirname(HERE)                        # run_all.sh's HERE
DEFAULT_EXPERIMENTS = os.path.join(HERE, "..", "experiments.csv")

SPLIT = "dev"
UNMASKED = "unmasked"
MARGIN = Fraction(3, 100)                  # "3 pp or less keeps the default" (exact, no float drift)
TIE_EPS = Fraction(1, 10 ** 9)             # R3 "difference below 1e-9"
DEFAULT_CHUNK, DEFAULT_OVERLAP = 1024, 128
R1_CONFIGS = {640: "3", 1024: "1", 1536: "4", 2000: "5"}
R3_CANDIDATES = ("M1", "M2", "M3", "M4", "M5")
R3_REPORTED = ("M0", "M1", "M2", "M3", "M4", "M5", "M6")      # R5's "ALL models" too (M1Q excluded)
R3_K = 5
CONFIG_1024 = {"M0": "1", "M1": "8", "M2": "9", "M3": "10", "M4": "11", "M5": "12", "M6": "13"}
# R3 tie-break 2 and R5 (A4), millions of parameters; then key order
PARAMS_M = {"M0": 33, "M1": 118, "M2": 278, "M6": 135, "M3": 560, "M4": 568, "M5": 568}
KEY_ORDER = tuple(er.MODEL_KEYS)                                     # M0 .. M6, M1Q
MASKED_DIRECTIONS = ("EN->EN", "AR->AR", "EN->AR", "AR->EN")
R4_SIZES = (1024, 1536, 2000)
R4_ROWS = {1: {1536: "14", 2000: "15"}, 2: {1536: "16", 2000: "17"}}
R4_ROW_IDS = frozenset(c for rows in R4_ROWS.values() for c in rows.values())
R6_KS = (3, 5, 6, 8, 10, 12)
R6_TOP = 12
R7_THRESHOLDS = tuple(decimal.Decimal(f"0.{i:02d}") for i in range(100))   # 0.00 .. 0.99
SCORE_QUANT = decimal.Decimal("0.01")
CORPUS_FORMATS = ("docx", "pdf")           # run_all.sh CORPUS_FORMAT (A10 takes it from the inputs)
STAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")

# experiments.csv: the contract run_all.sh (csvx) checks
HEADER = ["config_id", "stage", "model_key", "model_name", "chunk_size", "chunk_overlap", "metric",
          "match_limit", "similarity_threshold", "corpus_format", "index_name", "rag_profile",
          "answer_layer", "status", "notes"]
WRITABLE_STATUS = ("pending_decision", "planned", "skipped")   # anything else is never rewritten
FINAL_STATUS = "cut"                                           # A7: final, skipped with a WARN
DECIDED_ROWS = ("6", "7", "14", "15", "16", "17", "19")


class Refusal(Exception):
    """A guard refused the inputs or the write (exit 2)."""


class NoInput(Exception):
    """Nothing usable to decide from (exit 1)."""


# ---------------------------------------------------------------------------------------------
# names and small helpers (pure)
# ---------------------------------------------------------------------------------------------
def lab_results_dir() -> str:
    """RESULTS_DIR exactly as run_all.sh derives it: ${RESULTS_DIR:-$HERE/../results}, HERE being the
    code directory (an empty variable counts as unset, as with :-). A relative value is taken
    against the current directory, as run_all.sh does."""
    return os.environ.get("RESULTS_DIR") or os.path.join(CODE_DIR, "..", "results")


def index_name(key: str, chunk: int, overlap: int) -> str:
    """RAG_<KEY>_C<chunk>_O<overlap>_COS, the naming rule of 07_build_index.sql and run_all.sh."""
    name = f"RAG_{key}_C{chunk}_O{overlap}_COS"
    if not er.INDEX_NAME.match(name):
        raise ValueError(f"not a lab index name: {name}")
    return name


def profile_name(index: str) -> str:
    return "RAG_P_" + index[len("RAG_"):]


def overlap_20pct(w: int) -> int:
    """round(0.2 * w), exactly. w / 5 has a fractional part in {0, .2, .4, .6, .8}: never a .5 tie."""
    return round(Fraction(w, 5))


def pp(f: Fraction) -> str:
    """A rate difference in percentage points for the reason strings: 4.2, 3, 3.1, 0.25."""
    return f"{float(f * 100):.2f}".rstrip("0").rstrip(".")


def score(similarity) -> decimal.Decimal | None:
    """Select AI SCORE units (P14): 1 - cosine distance rounded to 2 dp. Rounded half away from zero
    on the float's shortest decimal form (Oracle ROUND, A9), so 0.695 -> 0.70 and 0.694 -> 0.69."""
    if similarity is None:
        return None
    return decimal.Decimal(repr(float(similarity))).quantize(SCORE_QUANT, rounding=decimal.ROUND_HALF_UP)


def cfg_order(cfg: str):
    """Sort key for config ids such as 7, 14, 1r."""
    m = re.match(r"^([0-9]+)(.*)$", cfg)
    return (int(m.group(1)), m.group(2)) if m else (10 ** 9, cfg)


def public(obj):
    """Drop the internal '_' keys (exact fractions) before JSON."""
    if isinstance(obj, dict):
        return {k: public(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [public(v) for v in obj]
    return obj


def rule_text(fn) -> str:
    """The quoted rule: the first paragraph of the rule function's docstring."""
    return " ".join((fn.__doc__ or "").strip().split("\n\n")[0].split())


def amendment_text(fn) -> str:
    """The quoted amendments: the docstring paragraph that starts with 'Amendments v1.1'."""
    for para in (fn.__doc__ or "").strip().split("\n\n"):
        if para.strip().startswith("Amendments v1.1"):
            return " ".join(para.split())
    return ""


def corpus_format_of(ret) -> str:
    """A10: the corpus_format of a filled row is the inputs' header experiments_row.corpus_format;
    inputs that disagree, or one without a staged format (docx or pdf), are refused."""
    fmts = {idx: (data["header"].get("experiments_row") or {}).get("corpus_format") for idx, data in ret.items()}
    bad = {i: f for i, f in fmts.items() if f not in CORPUS_FORMATS}
    if bad:
        raise Refusal("header experiments_row.corpus_format is not a staged format (" + ", ".join(CORPUS_FORMATS)
                      + ") in " + ", ".join(f"{i} ({f!r})" for i, f in sorted(bad.items())))
    vals = sorted(set(fmts.values()))
    if len(vals) != 1:
        raise Refusal("the inputs disagree on corpus_format: "
                      + ", ".join(f"{i} {f}" for i, f in sorted(fmts.items())))
    return vals[0]


# ---------------------------------------------------------------------------------------------
# record selection and cells (pure; the hit logic is eval_retrieval.hit_at)
# ---------------------------------------------------------------------------------------------
def dev_records(ret, index, rule, *, answerable=True, run_label=UNMASKED, bucket=None, direction=None):
    """(header, [records]) of one index on the dev split through stats.retrieval_records, which also
    drops bucket D (retrieval_only) unless bucket D is asked for by name."""
    side = {"index": index}
    if run_label:
        side["run_label"] = run_label
    if direction:
        side["direction"] = direction
    h = {"id": rule, "split": SPLIT, "pair_on": "id"}
    if answerable is not None:
        h["answerable"] = answerable
    if bucket:
        h["bucket"] = bucket
    header, recs = st.retrieval_records(ret, side, h)
    if recs is None:
        return None, []
    return header, [recs[u] for u in sorted(recs)]


def cell(recs, k, **extra) -> dict:
    """n records, x of them with an evidence chunk at rank <= k (no threshold), exact rate in _frac."""
    n = len(recs)
    x = sum(1 for r in recs if er.hit_at(r, k, "evidence", None))
    return dict(extra, k=k, n=n, x=x, rate=round(x / n, 4) if n else None,
                _frac=Fraction(x, n) if n else None)


def budget_cell(ret, index, rule) -> dict:
    """R1/R2/R4 metric: containable-conditional evidence hit at budget k = stats.budget_k(chunk_size),
    dev, answerable, unmasked; a record counts only when its own 'containable' is true in this run."""
    header, recs = dev_records(ret, index, rule)
    k = st._k_for({"k": "budget"}, header)
    return cell([r for r in recs if r.get("containable") is True], k, index=index)


def keep_default_or_best(cells, field, default, tie_key, tie_text):
    """Highest rate wins; best - default <= 3 pp keeps the default (exact fractions, so a gap of
    exactly 3 pp keeps it); a tie at the top that includes the default keeps it. A tie at the top
    between non-default candidates that clear the margin goes to min(tie_key) (A2: nearer the
    default, then the smaller). Returns (status, winner cell, reason)."""
    best = max(c["_frac"] for c in cells)
    top = sorted((c for c in cells if c["_frac"] == best), key=lambda c: c[field])
    dflt = next(c for c in cells if c[field] == default)
    gap = best - dflt["_frac"]
    names = " and ".join(str(c[field]) for c in top)
    if gap == 0:
        if len(top) > 1:
            return "decided", dflt, f"{names} tie at the top ({float(best):.4f}); ties keep {default}"
        return "decided", dflt, f"{default} has the highest rate ({float(best):.4f}); {default} kept"
    if gap <= MARGIN:
        verb = "beats" if len(top) == 1 else "tie and beat"
        return "decided", dflt, f"{names} {verb} {default} by {pp(gap)} pp <= 3 pp: {default} kept"
    if len(top) == 1:
        return "decided", top[0], f"{names} beats {default} by {pp(gap)} pp > 3 pp"
    win = min(top, key=tie_key)
    return "decided", win, (f"{names} tie at {float(best):.4f}; the tie goes to {win[field]} ({tie_text}); "
                            f"{win[field]} beats {default} by {pp(gap)} pp > 3 pp")


def sweep(rule, ret, cands, field, default, tie_key, tie_text) -> dict:
    """One 'highest containable-conditional rate, 3 pp keeps the default' decision over cands
    ([{config, <field>, index}]). Pending when a candidate index is not among the inputs."""
    out = {"rule": rule, "candidates": cands}
    missing = list(dict.fromkeys(c["index"] for c in cands if c["index"] not in ret))
    if missing:
        return dict(out, status="pending", missing=missing, reason="waiting for " + ", ".join(missing))
    cells = [dict(c, **budget_cell(ret, c["index"], rule)) for c in cands]
    empty = [c["index"] for c in cells if not c["n"]]
    if empty:
        return dict(out, candidates=cells, status="no_data", missing=empty,
                    reason="no containable dev answerable unmasked record in " + ", ".join(empty))
    status, win, reason = keep_default_or_best(cells, field, default, tie_key, tie_text)
    return dict(out, candidates=cells, status=status, reason=reason, winner=win[field],
                winner_index=win["index"], winner_config=win["config"])


def tie_nearer(field, default):
    """The R1 tie rule, which A2 extends to R2 and R4: nearer the default, then the smaller."""
    return lambda c: (abs(c[field] - default), c[field])


# ---------------------------------------------------------------------------------------------
# the rules (pure: ret = stats.load_retrieval(...), plus the results of earlier rules)
# ---------------------------------------------------------------------------------------------
def r1_chunk_size(ret) -> dict:
    """R1 S3 chunk_size (M0, overlap 128): candidates configs 3 (640), 1 (1024), 4 (1536), 5 (2000).
    Metric: containable-conditional evidence hit at budget k, dev, answerable, unmasked.
    Winner: the highest rate; ties go to the size nearer 1024 (then the smaller); 3 pp or less keeps 1024.

    Amendments v1.1 A11: When S3_WINNER is S1 itself, H5 compares S1 with itself and is reported as
    such.

    Budget k = stats.budget_k(chunk_size): 640 -> 9, 1024 -> 6, 1536 -> 4, 2000 -> 3."""
    cands = [{"config": cfg, "chunk_size": size, "index": index_name("M0", size, DEFAULT_OVERLAP)}
             for size, cfg in sorted(R1_CONFIGS.items())]
    res = sweep("R1", ret, cands, "chunk_size", DEFAULT_CHUNK, tie_nearer("chunk_size", DEFAULT_CHUNK),
                "nearer 1024, then the smaller")
    notes = []
    if res.get("winner_index") == st.S1:
        notes.append(f"S3_WINNER is S1 ({st.S1}): H5 compares S1 with itself (A11)")
    return dict(res, rule_text=rule_text(r1_chunk_size), amendments=amendment_text(r1_chunk_size), notes=notes)


def r2_overlap(ret, r1) -> dict:
    """R2 S4 overlap at the R1 winner w: candidates overlap 0 (config 6), round(0.2 * w) (config 7) and 128
    (the R1 winner's own index). Same metric and k = budget_k(w). 3 pp or less keeps 128. Ties keep 128.
    Config 6/7 rows: chunk_size w, match_limit budget_k(w), threshold 0, index names
    RAG_M0_C<w>_O<ovl>_COS, profiles RAG_P_M0_C<w>_O<ovl>_COS.

    Amendments v1.1 A2: a tie at the top that includes the default keeps the default ("ties keep 128").
    A tie between two non-default candidates that both clear the 3 pp margin goes to the one nearer
    the default, then the smaller (the R1 tie rule): R2 at w=1024 -> 205 before 0. A3: R2 at w = 640:
    round(0.2 x 640) = 128 is the winner's own index, so R2 compares 0 and 128 only and row 7 is set
    to status 'cut' with the note "20% of 640 = 128, the S3 winner's own overlap".

    overlap_rule records the choice as a rule for R5 (A6): "0", "20%" or "128"."""
    base = {"rule": "R2", "rule_text": rule_text(r2_overlap), "amendments": amendment_text(r2_overlap)}
    if r1.get("status") != "decided":
        return dict(base, status="pending", missing=["R1"], reason="needs the R1 winner")
    w = r1["winner"]
    o7 = overlap_20pct(w)
    own = {"config": R1_CONFIGS[w], "chunk_overlap": DEFAULT_OVERLAP, "index": index_name("M0", w, DEFAULT_OVERLAP)}
    c6 = {"config": "6", "chunk_overlap": 0, "index": index_name("M0", w, 0)}
    notes = []
    if o7 == DEFAULT_OVERLAP:                       # A3 (w = 640)
        cands = [c6, own]
        notes.append(f"20% of {w} = 128, the S3 winner's own overlap: row 7 is cut and R2 compares 0 and 128 "
                     f"only (A3)")
    else:
        cands = [c6, {"config": "7", "chunk_overlap": o7, "index": index_name("M0", w, o7)}, own]
    res = sweep("R2", ret, cands, "chunk_overlap", DEFAULT_OVERLAP, tie_nearer("chunk_overlap", DEFAULT_OVERLAP),
                "nearer 128, then the smaller (A2)")
    out = dict(base, **res, chunk_size=w, row7_cut=o7 == DEFAULT_OVERLAP, notes=notes)
    if res["status"] == "decided":
        out["overlap_rule"] = {"6": "0", "7": "20%"}.get(res["winner_config"], "128")
    return out


def r3_scores(ret, key) -> dict:
    """R3 figures of one model's 1024/128 index: primary cell and the four masked T directions."""
    idx = index_name(key, DEFAULT_CHUNK, DEFAULT_OVERLAP)
    _, recs = dev_records(ret, idx, "R3")
    prim = cell(recs, R3_K)
    dirs = {}
    for d in MASKED_DIRECTIONS:
        _, drecs = dev_records(ret, idx, "R3", answerable=None, run_label=None, bucket="T", direction=d)
        dirs[d] = cell([r for r in drecs if r["run_label"] != UNMASKED], R3_K)
    tie1 = (sum(c["_frac"] for c in dirs.values()) / len(dirs)) if all(c["n"] for c in dirs.values()) else None
    return {"key": key, "index": idx, "config": CONFIG_1024[key], "params_m": PARAMS_M.get(key),
            "primary": prim, "masked_t": dirs, "tie_break_1": None if tie1 is None else round(float(tie1), 6),
            "_primary": prim["_frac"], "_tie1": tie1}


def r3_compare(a, b) -> int:
    """<0 when a ranks above b. Primary, then tie-break 1, each a tie when the difference is below
    1e-9, then the smaller model (A4 parameter counts: M0 33M, M1 118M, M2 278M, M6 135M, M3 560M,
    M4 568M, M5 568M), then key order. Callers make sure both tie-break 1 figures exist whenever the
    primaries tie (rank_models)."""
    for fa, fb in ((a["_primary"], b["_primary"]), (a["_tie1"], b["_tie1"])):
        d = fa - fb
        if abs(d) >= TIE_EPS:
            return -1 if d > 0 else 1
    pa, pb = PARAMS_M.get(a["key"]), PARAMS_M.get(b["key"])
    if pa is not None and pb is not None and pa != pb:
        return -1 if pa < pb else 1
    return KEY_ORDER.index(a["key"]) - KEY_ORDER.index(b["key"])


def r3_decided_by(a, b) -> str:
    if abs(a["_primary"] - b["_primary"]) >= TIE_EPS:
        return "primary"
    if abs(a["_tie1"] - b["_tie1"]) >= TIE_EPS:
        return "tie_break_1"
    return "tie_break_2"


def rank_models(models) -> tuple:
    """(ranking, unresolved) of {key: r3_scores} (every one with a primary figure) in the R3 order.
    Models whose primaries tie (below 1e-9) form a group; tie-break 1 is needed only inside a group
    of two or more. A group in which a model has no tie-break 1 figure (a masked T direction without
    records) cannot be ordered: it is listed in key order and returned in 'unresolved' with its
    ranks, so a rule can tell whether the ranks it depends on are affected."""
    keys = sorted(models, key=lambda k: (-models[k]["_primary"], KEY_ORDER.index(k)))
    groups = []
    for k in keys:
        if groups and models[groups[-1][-1]]["_primary"] - models[k]["_primary"] < TIE_EPS:
            groups[-1].append(k)
        else:
            groups.append([k])
    ranking, unresolved = [], []
    for g in groups:
        lacking = [k for k in g if models[k]["_tie1"] is None]
        if len(g) > 1 and lacking:
            unresolved.append({"models": list(g), "ranks": list(range(len(ranking) + 1, len(ranking) + len(g) + 1)),
                               "missing_tie_break_1": lacking})
            ranking.extend(g)
        else:
            ranking.extend(sorted(g, key=functools.cmp_to_key(lambda a, b: r3_compare(models[a], models[b]))))
    return ranking, unresolved


def unresolved_text(u) -> str:
    return (f"{' and '.join(u['models'])} tie on the primary metric (ranks {u['ranks'][0]}-{u['ranks'][-1]}); "
            f"tie-break 1 needs masked T records in all four directions, missing for {', '.join(u['missing_tie_break_1'])}")


def r3_model_ranking(ret) -> dict:
    """R3 model ranking (S8, fixed chunk 1024/128): multilingual candidates M1..M5 (configs 8..12).
    Primary: evidence hit@5, dev, answerable, unmasked, unconditional (all dev answerable non-D questions).
    Tie-break 1 (difference below 1e-9): mean over the four masked directions EN->EN, AR->AR, EN->AR,
    AR->EN of evidence hit@5 on dev bucket T. Tie-break 2: the smaller model (parameters: M1 118M,
    M2 278M, M3 560M, M4 568M, M5 568M; then key order).
    Rank 1 = BEST_MULTILINGUAL (binds stats.py H2 to its 1024/128 index); ranks 1 and 2 get builds 14-17.
    M0 and M6 are ranked too (reported), but only M1..M5 are candidates.

    Amendments v1.1 A4: Parameter counts for tie-breaks: M0 33M, M1 118M, M2 278M, M6 135M, M3 560M,
    M4 568M, M5 568M (then key order).

    Tie-break 1 figures are required only where a primary tie needs them (check finding 4): the
    candidate ranking is 'no_data' only when two candidates tie on the primary metric and one of them
    has a masked T direction without records."""
    base = {"rule": "R3", "rule_text": rule_text(r3_model_ranking), "amendments": amendment_text(r3_model_ranking)}
    models = {k: r3_scores(ret, k) for k in R3_REPORTED if index_name(k, DEFAULT_CHUNK, DEFAULT_OVERLAP) in ret}
    no_primary = [k for k in R3_REPORTED if k in models and models[k]["_primary"] is None]
    reported, unres_reported = rank_models({k: m for k, m in models.items() if k not in no_primary})
    out = dict(base, models=models, ranking_reported=reported, ranking_reported_unresolved=unres_reported,
               no_primary_data=no_primary)
    missing = [index_name(k, DEFAULT_CHUNK, DEFAULT_OVERLAP) for k in R3_CANDIDATES if k not in models]
    if missing:
        return dict(out, status="pending", missing=missing, reason="waiting for " + ", ".join(missing))
    bad = [k for k in R3_CANDIDATES if k in no_primary]
    if bad:
        return dict(out, status="no_data", missing=bad,
                    reason="no dev answerable unmasked record for " + ", ".join(bad))
    ranking, unresolved = rank_models({k: models[k] for k in R3_CANDIDATES})
    if unresolved:
        lacking = [k for u in unresolved for k in u["missing_tie_break_1"]]
        return dict(out, status="no_data", missing=lacking, unresolved=unresolved,
                    reason="; ".join(unresolved_text(u) for u in unresolved))
    r1k, r2k = ranking[0], ranking[1]
    a, b = models[r1k], models[r2k]
    how = r3_decided_by(a, b)
    reason = (f"rank 1 {r1k} (ev hit@5 {a['primary']['rate']}, masked T mean {a['tie_break_1']}) over rank 2 "
              f"{r2k} (ev hit@5 {b['primary']['rate']}, masked T mean {b['tie_break_1']}), decided by {how}")
    return dict(out, status="decided", ranking=ranking, rank1=r1k, rank2=r2k, top2=[r1k, r2k],
                decided_by=how, reason=reason,
                best_multilingual=index_name(r1k, DEFAULT_CHUNK, DEFAULT_OVERLAP))


def r4_chunk_per_model(ret, r3, cut_rows=frozenset()) -> dict:
    """R4 best chunk_size of each top-2 model: its 1024/128 index vs its 1536/128 (k 4) and 2000/128 (k 3)
    builds. Same metric as R1. 3 pp or less keeps 1024.
    Rows 14/15 = rank-1 model at 1536/2000; rows 16/17 = rank-2 model at 1536/2000
    (index RAG_<key>_C<size>_O128_COS, profile RAG_P_<key>_C<size>_O128_COS, match_limit = budget k).

    Amendments v1.1 A2: a tie between two non-default candidates that both clear the 3 pp margin goes
    to the one nearer the default, then the smaller: R4 -> 1536 before 2000. A7: If builds 14-17 are
    cut, R4 keeps 1024 for that model.

    The 1024 index is read at its budget k (6), as in R1. A cut build is not a candidate (not waited
    for); with both of a model's builds cut, 1024 is kept."""
    base = {"rule": "R4", "rule_text": rule_text(r4_chunk_per_model), "amendments": amendment_text(r4_chunk_per_model)}
    if r3.get("status") != "decided":
        return dict(base, status="pending", missing=["R3"], reason="needs the R3 top 2")
    models = {}
    for rank, key in enumerate(r3["top2"], 1):
        rows = R4_ROWS[rank]
        cut = [size for size in (1536, 2000) if rows[size] in cut_rows]
        cands = [{"config": CONFIG_1024[key] if size == DEFAULT_CHUNK else rows[size], "chunk_size": size,
                  "index": index_name(key, size, DEFAULT_OVERLAP)} for size in R4_SIZES if size not in cut]
        cut_text = (" and ".join(f"build {rows[s]} ({s})" for s in cut) + (" is" if len(cut) == 1 else " are")
                    + " cut (A7)") if cut else ""
        if len(cands) == 1:
            own = cands[0]
            models[key] = {"rule": "R4", "candidates": cands, "status": "decided", "winner": DEFAULT_CHUNK,
                           "winner_index": own["index"], "winner_config": own["config"], "cut": cut,
                           "reason": f"{cut_text}: 1024 kept", "rank": rank}
            continue
        sub = sweep("R4", ret, cands, "chunk_size", DEFAULT_CHUNK, tie_nearer("chunk_size", DEFAULT_CHUNK),
                    "nearer 1024, then the smaller (A2)")
        if cut:
            sub["reason"] = f"{cut_text}, not a candidate; {sub['reason']}"
        models[key] = dict(sub, cut=cut, rank=rank)
    statuses = {m["status"] for m in models.values()}
    status = "decided" if statuses == {"decided"} else "pending" if "pending" in statuses else "no_data"
    reason = "; ".join(f"{k}: {m['reason']}" for k, m in models.items())
    return dict(base, status=status, models=models, reason=reason)


def r5_s10(ret, r1, r2, r3, r4) -> dict:
    """R5 S10 model and chunking: the best of ALL models (M0..M6 and M1Q excluded as exploratory) by the R3
    primary metric at 1024/128, then that model's best chunk_size (R4 if it is a top-2 model, else 1024),
    and overlap: the R2 winner applies to M0 only; for another model overlap stays 128 unless R2 chose a
    different overlap, in which case build 19 = best model x best chunk x R2 overlap (PLAN 4.3 row 19).
    If build 19 is not needed, S10 reuses the existing index.

    Amendments v1.1 A5: R5 ranks every model whose 1024/128 index is among the inputs (M0..M6; M1Q
    excluded) by the R3 primary metric, with R3's tie-breaks (A4 counts). A model whose index is
    missing is reported and left out, not waited for. A6: for M0 the R1 winner w and the R2 overlap
    (an existing build: S3/S4 winner). For another model, its R4 best chunk c (1024 if it is not
    top-2, or if its builds 14-17 are cut), and the R2 choice carried over as a rule, not a character
    count: 128 stays 128, 0 stays 0, "20%" becomes round(0.2 x c). Build 19 is needed only when that
    index does not already exist. If S10 resolves to S1's own index (M0 wins, R1 and R2 keep the
    defaults), the tool reports it as an operator decision and binds nothing for H3. A8: Row 19
    build-time match_limit = budget_k(chunk) and similarity_threshold 0 (as rows 3-7 and 14-17); the
    S10 knobs from R6/R7 are applied afterwards with 08_set_query_knobs.sql.

    For another model, the index at overlap 128 is an existing build (8-13 at 1024, 14-17 at the R4
    winner); at overlap 0 or 20% it is not, so build 19 is needed exactly then. R5 needs R3 decided
    (the top 2) only when the best model is one of M1..M5 (re-check finding 1): the chunk of M0 or M6
    does not depend on it. Only rank 1 has to be resolved: an unbreakable tie lower down does not stop R5."""
    base = {"rule": "R5", "rule_text": rule_text(r5_s10), "amendments": amendment_text(r5_s10)}
    present = r3.get("models") or {}            # r3_model_ranking fills 'models' even when it is pending
    if not present:
        return dict(base, status="pending", missing=["R3"], reason="no model at 1024/128 among the inputs")
    left_out = [index_name(k, DEFAULT_CHUNK, DEFAULT_OVERLAP) for k in R3_REPORTED if k not in present]
    notes = [f"{i} is not among the inputs: left out of R5, not waited for (A5)" for i in left_out]
    base.update(left_out=left_out, notes=notes)
    no_primary = [k for k in R3_REPORTED if k in present and present[k]["_primary"] is None]
    if no_primary:
        return dict(base, status="no_data", missing=no_primary,
                    reason="no dev answerable unmasked record at 1024/128 for " + ", ".join(no_primary))
    ranking, unresolved = rank_models(present)
    top = [u for u in unresolved if 1 in u["ranks"]]
    if top:
        return dict(base, status="no_data", ranking=ranking, unresolved=unresolved,
                    missing=top[0]["missing_tie_break_1"], reason="rank 1: " + unresolved_text(top[0]))
    best = ranking[0]
    how = r3_decided_by(present[best], present[ranking[1]]) if len(ranking) > 1 else "only model"
    out = dict(base, ranking=ranking, unresolved=unresolved, best_model=best, decided_by=how)
    if r2.get("status") != "decided":           # pending or no_data: R5 inherits it
        st2 = r2.get("status") if r2.get("status") in ("pending", "no_data") else "pending"
        return dict(out, status=st2, missing=["R2"],
                    reason=f"best model {best}; needs the R2 overlap (R2 is {r2.get('status')})")
    if best == "M0":                            # A6: the S3/S4 winner, an existing build
        chunk, overlap, s10 = r2["chunk_size"], r2["winner"], r2["winner_index"]
        chunk_from = "M0: the R1 winner"
        overlap_from = f"M0: the R2 winner, config {r2['winner_config']}"
        build19 = False
    else:
        if best in R3_CANDIDATES and r3.get("status") != "decided":
            # only a multilingual candidate's chunk depends on top-2 membership and R4 (recheck finding 1)
            st3 = r3.get("status") if r3.get("status") in ("pending", "no_data") else "pending"
            return dict(out, status=st3, missing=["R3"],
                        reason=f"best model {best} is an R3 candidate; its chunk needs R3 decided (R3 is {r3.get('status')})")
        if best in R3_CANDIDATES and best in r3["top2"]:
            sub = r4["models"][best]
            if sub["status"] != "decided":
                return dict(out, status=sub["status"], missing=sub.get("missing", ["R4"]),
                            reason=f"best model {best} is a top-2 model; its R4 is {sub['status']}: {sub['reason']}")
            chunk = sub["winner"]
            chunk_from = "R4" + (", builds cut" if sub.get("cut") and len(sub["candidates"]) == 1 else "")
        else:
            chunk, chunk_from = DEFAULT_CHUNK, "not a top-2 model: 1024"
        rule = r2["overlap_rule"]
        overlap = {"128": DEFAULT_OVERLAP, "0": 0, "20%": overlap_20pct(chunk)}[rule]
        overlap_from = f"the R2 rule '{rule}' carried over" + (f": round(0.2 x {chunk})" if rule == "20%" else "")
        s10 = index_name(best, chunk, overlap)
        build19 = overlap != DEFAULT_OVERLAP
    s10_is_s1 = s10 == st.S1
    reason = (f"best of {', '.join(sorted(present, key=KEY_ORDER.index))} by R3 at 1024/128: {best} (decided by "
              f"{how}); chunk {chunk} ({chunk_from}); overlap {overlap} ({overlap_from}); "
              + (f"build 19 needed: {s10}" if build19 else f"S10 reuses the existing index {s10}"))
    if s10_is_s1:
        notes = notes + [f"S10 resolves to S1 ({st.S1}): operator decision; nothing is bound for H3 and no S10 "
                         f"knobs are given (A6)"]
    return dict(out, status="decided", chunk_size=chunk, chunk_overlap=overlap, chunk_from=chunk_from,
                overlap_from=overlap_from, build19=build19, s10=s10, s10_is_s1=s10_is_s1, reason=reason, notes=notes)


def r6_match_limit(ret, r5) -> dict:
    """R6 match_limit for S10 (S5 rule): the smallest k in {3,5,6,8,10,12} with dev unmasked evidence
    hit@k >= hit@12 - 0.03 (unconditional, answerable)."""
    base = {"rule": "R6", "rule_text": rule_text(r6_match_limit)}
    if r5.get("status") != "decided":
        return dict(base, status="pending", missing=["R5"], reason="needs S10 (R5)")
    idx = r5["s10"]
    if idx not in ret:
        return dict(base, status="pending", index=idx, missing=[idx],
                    reason=f"waiting for {idx}" + (" (build 19, then its retrieval run)" if r5["build19"] else ""))
    _, recs = dev_records(ret, idx, "R6")
    if not recs:
        return dict(base, status="no_data", index=idx, missing=[idx], reason=f"no dev answerable unmasked record in {idx}")
    cells = [cell(recs, k) for k in R6_KS]
    top = next(c for c in cells if c["k"] == R6_TOP)
    i = next(j for j, c in enumerate(cells) if c["_frac"] >= top["_frac"] - MARGIN)   # k = 12 always qualifies
    win = cells[i]
    reason = f"hit@{win['k']} {win['rate']} >= hit@12 {top['rate']} - 3 pp" + (
        f" (hit@{cells[i - 1]['k']} {cells[i - 1]['rate']} is below)" if i else "")
    return dict(base, status="decided", index=idx, candidates=cells, winner=win["k"], reason=reason)


def r7_selection(records):
    """eval_retrieval.calibrate_threshold's record selection (v1.3), unchanged: pass-1 unmasked dev
    query records outside bucket D. An answerable question gives its best evidence chunk's
    similarity, else its best gold-document chunk's (a doc-level fallback, counted), else nothing
    (left out, counted); an unanswerable one gives its top-1 hit's similarity, or is left out when it
    has no hits (counted). Returns (pos, neg, n_fallback, n_answerable_without_score, n_without_hits)."""
    rs = [r for r in records if r.get("type") == "query" and r.get("pass", 1) == 1
          and r["run_label"] == UNMASKED and r["split"] == SPLIT
          and r["bucket"] not in er.RETRIEVAL_ONLY_BUCKETS]
    pos, fallback, no_score = [], 0, 0
    for r in rs:
        if not r["answerable"]:
            continue
        if r.get("best_evidence"):
            pos.append(r["best_evidence"]["similarity"])
        elif r["best_gold"]:
            pos.append(r["best_gold"]["similarity"])
            fallback += 1
        else:
            no_score += 1
    neg = [r["hits"][0]["similarity"] for r in rs if not r["answerable"] and r["hits"]]
    no_hits = sum(1 for r in rs if not r["answerable"] and not r["hits"])
    return pos, neg, fallback, no_score, no_hits


def r7_threshold(ret, r5) -> dict:
    """R7 similarity_threshold for S10 (S6 rule, per model, SCORE units per P14): P14 measured that for a
    COSINE index Select AI's SCORE is 1 - cosine distance rounded to 2 dp (max error 4.9e-3 = rounding).
    Candidate thresholds t in {0.00, 0.01, ..., 0.99}. Maximise balanced accuracy =
    mean( share of dev answerable questions with round(best_evidence similarity, 2) >= t,
    share of dev unanswerable questions with round(top-1 hit similarity, 2) < t ). Ties go to the lower
    value. Unmasked records only.

    Amendments v1.1 A9: R7 uses eval_retrieval.calibrate_threshold's definition in SCORE units: an
    answerable question's score is its best evidence chunk, or, where no chunk holds the evidence, its
    best gold-document chunk (counted as a fallback); unanswerable questions without hits are left
    out. Scores are round(similarity, 2) half away from zero (Oracle ROUND), candidates t on the 0.01
    grid from 0.00.

    The selection is r7_selection, cross-checked against calibrate_threshold itself on every run (its
    n_answerable, n_unanswerable and n_doc_level_fallback must agree, or the run is refused); only
    the units (SCORE) and the candidate grid differ from the harness's raw-similarity calibration,
    which is reported alongside."""
    base = {"rule": "R7", "rule_text": rule_text(r7_threshold), "amendments": amendment_text(r7_threshold)}
    if r5.get("status") != "decided":
        return dict(base, status="pending", missing=["R5"], reason="needs S10 (R5)")
    idx = r5["s10"]
    if idx not in ret:
        return dict(base, status="pending", index=idx, missing=[idx],
                    reason=f"waiting for {idx}" + (" (build 19, then its retrieval run)" if r5["build19"] else ""))
    header, records = ret[idx]["header"], list(ret[idx]["records"].values())
    metric = (header.get("pairing") or {}).get("metric")
    if metric not in (None, "COSINE"):
        return dict(base, status="no_data", index=idx, reason=f"SCORE units are known for COSINE only (P14), not {metric}")
    pos_raw, neg_raw, fallback, no_score, no_hits = r7_selection(records)
    harness = er.calibrate_threshold(records)
    ours_none = not pos_raw or not neg_raw or any(x is None for x in pos_raw + neg_raw)
    if (harness is None) != ours_none or (harness is not None and (
            harness["n_answerable"], harness["n_unanswerable"], harness["n_doc_level_fallback"])
            != (len(pos_raw), len(neg_raw), fallback)):
        raise Refusal(f"R7 on {idx}: the record selection ({len(pos_raw)}, {len(neg_raw)}, fallback {fallback}) "
                      f"disagrees with eval_retrieval.calibrate_threshold ({harness}); eval_retrieval.py changed?")
    counts = dict(n_answerable=len(pos_raw), n_unanswerable=len(neg_raw), n_doc_level_fallback=fallback,
                  n_answerable_without_score=no_score, n_unanswerable_without_hits=no_hits)
    if not pos_raw or not neg_raw:
        return dict(base, status="no_data", index=idx, **counts,
                    reason=f"needs dev answerable and unanswerable unmasked records with a score "
                           f"({len(pos_raw)}, {len(neg_raw)})")
    if any(x is None for x in pos_raw + neg_raw):
        return dict(base, status="no_data", index=idx, **counts, reason="a similarity is missing (not a COSINE index?)")
    pos, neg = [score(x) for x in pos_raw], [score(x) for x in neg_raw]
    curve, best = [], None
    for t in R7_THRESHOLDS:
        a = sum(1 for p in pos if p >= t)
        u = sum(1 for q in neg if q < t)
        ba = (Fraction(a, len(pos)) + Fraction(u, len(neg))) / 2
        curve.append({"t": str(t), "answerable_pass": a, "unanswerable_pass": u, "balanced_accuracy": round(float(ba), 6)})
        if best is None or ba > best[0]:                   # strictly greater: ties keep the lower t
            best = (ba, t, a, u)
    ba, t, a, u = best
    reason = (f"t = {t} maximises balanced accuracy {float(ba):.4f} (answerable {a}/{len(pos)} >= t, "
              f"unanswerable {u}/{len(neg)} < t; {fallback} doc-level fallback)")
    return dict(base, status="decided", index=idx, winner=str(t), balanced_accuracy=round(float(ba), 6), **counts,
                harness_raw_threshold=harness["threshold"], curve=curve, reason=reason)


# ---------------------------------------------------------------------------------------------
# rows and bindings (pure)
# ---------------------------------------------------------------------------------------------
def planned_row(key, chunk, overlap, k, corpus_format, thr="0") -> dict:
    """The decided columns of a row run_all.sh valid_row/build_one accepts (status planned); build-time
    k = budget_k(chunk) and threshold 0 (A8); corpus_format from the inputs (A10)."""
    idx = index_name(key, chunk, overlap)
    return {"model_key": key, "model_name": er.MODEL_KEYS[key], "chunk_size": str(chunk),
            "chunk_overlap": str(overlap), "metric": "COS", "match_limit": str(k),
            "similarity_threshold": thr, "corpus_format": corpus_format, "index_name": idx,
            "rag_profile": profile_name(idx), "status": "planned"}


def experiment_rows(d, corpus_format) -> dict:
    """{config_id: {column: value}} for the rows the decisions fill: 6 and 7 (7 cut at w = 640, A3)
    after R1, 14-17 after R3, 19 after R5 (planned, or status skipped with a note)."""
    rows = {}
    r1, r3, r5 = d["R1"], d["R3"], d["R5"]
    if r1["status"] == "decided":
        w = r1["winner"]
        rows["6"] = planned_row("M0", w, 0, st.budget_k(w), corpus_format)
        if overlap_20pct(w) != DEFAULT_OVERLAP:
            rows["7"] = planned_row("M0", w, overlap_20pct(w), st.budget_k(w), corpus_format)
        else:
            rows["7"] = {"status": FINAL_STATUS,
                         "notes": f"build 7 cut (decide_dev R2, A3): 20% of {w} = 128, the S3 winner's own overlap"}
    if r3["status"] == "decided":
        for rank, key in enumerate(r3["top2"], 1):
            for size, cfg in R4_ROWS[rank].items():
                rows[cfg] = planned_row(key, size, DEFAULT_OVERLAP, st.budget_k(size), corpus_format)
    if r5["status"] == "decided":
        if r5["build19"]:
            rows["19"] = planned_row(r5["best_model"], r5["chunk_size"], r5["chunk_overlap"],
                                     st.budget_k(r5["chunk_size"]), corpus_format)
        elif r5["s10_is_s1"]:
            rows["19"] = {"status": "skipped",
                          "notes": f"build 19 (conditional): not needed (decide_dev R5): S10 resolves to S1 "
                                   f"{st.S1} itself; operator decision (A6)"}
        else:
            rows["19"] = {"status": "skipped",
                          "notes": f"build 19 (conditional): not needed (decide_dev R5): S10 reuses {r5['s10']}; "
                                   f"its k and threshold come from R6/R7 via 08_set_query_knobs.sql"}
    return rows


def operator_decisions(d) -> list:
    """What the rules resolve but leave to the operator (A6: S10 = S1 binds nothing for H3)."""
    r5 = d["R5"]
    if r5["status"] == "decided" and r5["s10_is_s1"]:
        return [{"subject": "S10 (H3)", "rule": "R5",
                 "reason": f"S10 resolves to S1 {st.S1} (M0 best, R1 and R2 keep the defaults): H3 would compare "
                           f"S1 with itself; nothing is bound for H3 and no S10 knobs are given (A6)"}]
    return []


def decide(ret, cut_rows=frozenset()) -> dict:
    """Every rule, in dependency order, plus the bindings and the experiments rows they give.
    cut_rows: config ids whose experiments.csv status is 'cut' (A7). Only 14-17 feed a rule (R4);
    those are recorded as cut_rows, so the tool's own cut of row 7 (A3) keeps a re-run idempotent."""
    cut_read = frozenset(cut_rows) & R4_ROW_IDS
    fmt = corpus_format_of(ret)
    try:
        r1 = r1_chunk_size(ret)
        r2 = r2_overlap(ret, r1)
        r3 = r3_model_ranking(ret)
        r4 = r4_chunk_per_model(ret, r3, cut_read)
        r5 = r5_s10(ret, r1, r2, r3, r4)
        r6 = r6_match_limit(ret, r5)
        r7 = r7_threshold(ret, r5)
    except SystemExit as e:              # stats.py guards (a record twice for one unit, a bad name)
        raise Refusal(str(e.code)) from None
    d = {"R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r5, "R6": r6, "R7": r7}
    bindings = {}
    if r1["status"] == "decided":
        bindings["S3_WINNER"] = r1["winner_index"]
    if r3["status"] == "decided":
        bindings["BEST_MULTILINGUAL"] = r3["best_multilingual"]
    s10_ok = r5["status"] == "decided" and not r5["s10_is_s1"]
    if s10_ok:
        bindings["S10"] = r5["s10"]
    knobs = None
    if s10_ok and r6["status"] == "decided" and r7["status"] == "decided":
        knobs = {"index": r5["s10"], "match_limit": r6["winner"], "similarity_threshold": r7["winner"],
                 "command": f"@08_set_query_knobs.sql {r5['s10']} {r6['winner']} {r7['winner']}"}
    return {"decisions": d, "bindings": bindings, "s10_knobs": knobs, "corpus_format": fmt,
            "cut_rows": sorted(cut_read, key=cfg_order),
            "bind_args": [f"--bind {k}={v}" for k, v in sorted(bindings.items())],
            "experiments_rows": experiment_rows(d, fmt), "operator_decisions": operator_decisions(d),
            "pending": [k for k, v in d.items() if v["status"] != "decided"]}


# ---------------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------------
def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


QUERY_KEYS = ("id", "run_label", "split", "bucket", "answerable", "direction", "containable", "hits",
              "best_evidence", "best_gold")
HIT_KEYS = ("rank", "evidence", "gold", "similarity")


def check_query(name, n, r):
    """A query record carries every key the rules read (eval_retrieval.py always writes them); a
    hand-edited or foreign record is a guard refusal naming the file and line, not a traceback."""
    missing = [k for k in QUERY_KEYS if k not in r]
    if missing:
        raise Refusal(f"{name}:{n}: query record without {', '.join(missing)}")
    if not isinstance(r["hits"], list):
        raise Refusal(f"{name}:{n}: 'hits' is not a list")
    for h in r["hits"]:
        bad = list(HIT_KEYS) if not isinstance(h, dict) else [k for k in HIT_KEYS if k not in h]
        if bad:
            raise Refusal(f"{name}:{n}: a hit without {', '.join(bad)}")


def scan_run(path):
    """(headers, n summary records, n pass-1 unmasked query records) of one retrieval JSONL; the shape
    of every query record is checked (check_query)."""
    hs, n_sum, n_unm = [], 0, 0
    name = os.path.basename(path)
    for n, (_, r) in enumerate(st.read_jsonl([path]), 1):
        t = r.get("type")
        if t == "header":
            hs.append(r)
        elif t == "summary":
            n_sum += 1
        elif t == "query":
            check_query(name, n, r)
            if r.get("pass", 1) == 1 and r.get("run_label") == UNMASKED:
                n_unm += 1
    return hs, n_sum, n_unm


def check_complete(name, h, n_sum, n_unm):
    """A1: exactly one summary record, and one pass-1 unmasked query record per question of the header
    (plan_runs gives every question exactly one unmasked run). A paused or crashed run fails both."""
    if n_sum != 1:
        raise Refusal(f"{name}: {n_sum} summary records, need exactly one (an unfinished run; give the complete "
                      f"one) (A1)")
    nq = h.get("n_questions")
    if not isinstance(nq, int) or isinstance(nq, bool):
        raise Refusal(f"{name}: header n_questions {nq!r} is not a count; completeness cannot be checked (A1)")
    if n_unm != nq:
        raise Refusal(f"{name}: {n_unm} pass-1 unmasked query records for header n_questions {nq} (an incomplete "
                      f"run) (A1)")


def load_inputs(paths):
    """(ret, inputs, headers, digest). Refuses an incomplete run (A1), (stats.check_headers semantics)
    mixed seals, a --only run, a split that excludes dev, a file without exactly one header, a non-lab
    index name, (stats.load_retrieval) two files for one index, and inputs that disagree on
    corpus_format (A10)."""
    if not paths:
        raise NoInput("no --retrieval file given")
    for p in paths:
        if not os.path.isfile(p):
            raise NoInput(f"{p}: no such file")
    try:
        inputs, hdrs = [], []
        for p in paths:
            name = os.path.basename(p)
            hs, n_sum, n_unm = scan_run(p)
            if len(hs) != 1:
                raise Refusal(f"{name}: {len(hs)} header records, need exactly one")
            h = hs[0]
            if not er.INDEX_NAME.match(str(h.get("index") or "")):
                raise Refusal(f"{name}: header index {h.get('index')!r} is not a RAG_<M>_C<n>_O<n>_<metric> name")
            check_complete(name, h, n_sum, n_unm)
            hdrs.append(h)
            inputs.append({"path": name, "sha256": sha256_file(p), "index": h["index"],
                           "config_id": h.get("config_id"), "seal_digest": (h.get("seal") or {}).get("digest"),
                           "split": h.get("split"), "stamp": h.get("stamp"), "utc": h.get("utc"),
                           "n_questions": h.get("n_questions"),
                           "corpus_format": (h.get("experiments_row") or {}).get("corpus_format")})
        digest = st.check_headers(hdrs, SPLIT)
        ret = st.load_retrieval(paths)
    except SystemExit as e:
        raise Refusal(str(e.code)) from None
    except UnicodeDecodeError as e:
        raise Refusal(f"an input is not UTF-8 JSONL ({e})") from None
    except OSError as e:
        raise NoInput(f"cannot read an input: {e}") from None
    corpus_format_of(ret)                                   # A10: refuse early, before any rule runs
    n_dev = sum(1 for data in ret.values() for r in data["records"].values() if r.get("split") == SPLIT)
    if not n_dev:
        raise NoInput("no dev query record in any input")
    for i in inputs:
        log.info("event=input path=%s index=%s sha256=%s seal=%s n_questions=%s corpus_format=%s", i["path"],
                 i["index"], i["sha256"][:16], (i["seal_digest"] or "")[:16], i["n_questions"], i["corpus_format"])
    return ret, sorted(inputs, key=lambda i: i["index"]), hdrs, digest


def stamp_of(hdrs, given=None) -> str:
    """--stamp, else the latest header utc (ISO with an offset; the header stamp as a fallback)."""
    if given:
        if not STAMP_RE.match(given):
            raise Refusal(f"--stamp {given!r} is not YYYYMMDDTHHMMSSZ")
        return given
    times = []
    for h in hdrs:
        if h.get("utc"):
            try:
                t = dt.datetime.fromisoformat(h["utc"])
            except ValueError:
                raise Refusal(f"{h.get('index')}: header utc {h['utc']!r} is not ISO 8601") from None
            if t.tzinfo is None:
                raise Refusal(f"{h.get('index')}: header utc {h['utc']!r} has no UTC offset")
            times.append(t.astimezone(dt.timezone.utc))
        elif STAMP_RE.match(str(h.get("stamp") or "")):
            times.append(dt.datetime.strptime(h["stamp"], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc))
        else:
            raise Refusal(f"{h.get('index')}: header has neither utc nor stamp; pass --stamp")
    return max(times).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------------------------
# experiments.csv (line-preserving: only the decided rows are re-serialised)
# ---------------------------------------------------------------------------------------------
def parse_experiments(text):
    """(lines, {config_id: (line index, row)}). One record per physical line, the run_all.sh header,
    15 fields, unique config_id; anything else is refused."""
    lines = text.splitlines(keepends=True)
    if not lines:
        raise Refusal("experiments.csv is empty")
    try:
        head = next(csv.reader([lines[0]], strict=True))
    except (csv.Error, StopIteration) as e:
        raise Refusal(f"experiments.csv: unreadable header ({e})") from None
    if head != HEADER:
        raise Refusal("experiments.csv: header differs from the run_all.sh contract")
    rows = {}
    for i, line in enumerate(lines[1:], 1):
        if not line.strip():
            continue
        try:
            recs = list(csv.reader([line], strict=True))
        except csv.Error as e:
            raise Refusal(f"experiments.csv line {i + 1}: {e}") from None
        if len(recs) != 1 or len(recs[0]) != len(HEADER):
            raise Refusal(f"experiments.csv line {i + 1}: {len(recs[0]) if recs else 0} fields, need {len(HEADER)} "
                          "on one line")
        row = dict(zip(HEADER, recs[0]))
        if row["config_id"] in rows:
            raise Refusal(f"experiments.csv: duplicate config_id {row['config_id']}")
        rows[row["config_id"]] = (i, row)
    return lines, rows


def cut_rows_of(text) -> frozenset:
    """Config ids whose status is 'cut' (A7)."""
    _, rows = parse_experiments(text)
    return frozenset(c for c, (_, r) in rows.items() if r["status"] == FINAL_STATUS)


def format_row(row, terminator) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow([row[h] for h in HEADER])   # QUOTE_MINIMAL, like csvx
    return buf.getvalue() + terminator


def plan_experiments(text, proposals):
    """(new text, actions). A 'cut' row is final: never rewritten, action cut_skipped (A7). A writable
    row (pending_decision, planned, skipped) takes the decided values; a row past that is left alone
    when its decided values already agree and refused when they do not. A written index_name may not
    belong to another row (its 'r' rebuild twin aside)."""
    lines, rows = parse_experiments(text)
    new = list(lines)
    actions = []
    for cfg in sorted(proposals, key=cfg_order):
        if cfg not in rows:
            raise Refusal(f"experiments.csv has no row {cfg}")
        i, cur = rows[cfg]
        want = dict(cur, **proposals[cfg])
        changed = [h for h in HEADER if want[h] != cur[h]]
        if cur["status"] == FINAL_STATUS:
            actions.append({"config_id": cfg, "action": "cut_skipped", "status": cur["status"],
                            "would_change": changed})
            continue
        if not changed:
            actions.append({"config_id": cfg, "action": "unchanged", "status": cur["status"]})
            continue
        if cur["status"] not in WRITABLE_STATUS:
            differ = [h for h in changed if h != "status"]
            if not differ:
                actions.append({"config_id": cfg, "action": "kept", "status": cur["status"]})
                continue
            raise Refusal(f"config {cfg} is {cur['status']}: {', '.join(f'{h} {cur[h]!r} != {want[h]!r}' for h in differ)}"
                          "; a row past planned is never rewritten (operator decision)")
        idx = want["index_name"]
        clash = [c for c, (_, r) in rows.items()
                 if c not in (cfg, cfg + "r") and cfg != c + "r" and r["index_name"] == idx and idx != "TBD"]
        if clash:
            raise Refusal(f"config {cfg}: index {idx} already belongs to config {', '.join(clash)}")
        body = lines[i].rstrip("\r\n")
        new[i] = format_row(want, lines[i][len(body):] or "\n")
        if cur["status"] != "pending_decision":
            log.warning("event=row_redecided config_id=%s status=%s columns=%s", cfg, cur["status"], ",".join(changed))
        actions.append({"config_id": cfg, "action": "written", "columns": changed})
    return "".join(new), actions


class LabLock:
    """RESULTS_DIR/.lab.lock, the flock run_all.sh holds for its whole run: never write experiments.csv
    under a running BUILD (its csvx rewrites the same file)."""

    def __init__(self, results_dir):
        self.path = os.path.join(results_dir, ".lab.lock")
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise Refusal(f"{self.path} is held (run_all.sh or another lab script is running); try again after it") from None
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
        return False


def replace_atomic(path, data: bytes, expect: bytes | None, mode: int = 0o644):
    """Temp file in the same directory, fsync, then rename. expect: the bytes the decision was based
    on (None: the file must not exist); another writer in between stops the replace."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        now = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                now = f.read()
        if now != expect:
            raise Refusal(f"{os.path.basename(path)} changed while deciding; nothing written, run again")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------------------------------------
# report and main
# ---------------------------------------------------------------------------------------------
def report(doc) -> str:
    out = [f"{VERSION}  stamp {doc['stamp']}  seal {str(doc['questions_digest'])[:16]}  inputs {len(doc['inputs'])}"
           f"  corpus_format {doc['corpus_format']}  cut builds 14-17 {','.join(doc['cut_rows']) or 'none'}"]
    for rid, r in doc["decisions"].items():
        out.append(f"{rid} {r['status']}: {r.get('reason', '')}")
        if rid in ("R1", "R2"):
            for c in r.get("candidates", []):
                if "n" in c:
                    out.append(f"   config {c['config']:>3} {c['index']:<26} k {c['k']:>2}  {c['x']:>3}/{c['n']:<3} {c['rate']}")
        elif rid == "R3":
            for k in r.get("ranking_reported", []):
                m = r["models"][k]
                tag = "candidate" if k in R3_CANDIDATES else "reported"
                out.append(f"   {k:<3} {m['index']:<26} hit@5 {m['primary']['x']:>3}/{m['primary']['n']:<3} "
                           f"{m['primary']['rate']}  masked T mean {m['tie_break_1']}  {m['params_m']}M  ({tag})")
            for u in r.get("ranking_reported_unresolved", []):
                out.append(f"   unresolved: {unresolved_text(u)}")
        elif rid == "R4":
            for k, m in r.get("models", {}).items():
                for c in m.get("candidates", []):
                    if "n" in c:
                        out.append(f"   {k} config {c['config']:>3} {c['index']:<26} k {c['k']} {c['x']:>3}/{c['n']:<3} {c['rate']}")
        elif rid == "R5" and r.get("ranking"):
            out.append("   ranking " + " > ".join(r["ranking"]))
        elif rid == "R6":
            for c in r.get("candidates", []):
                out.append(f"   hit@{c['k']:<2} {c['x']:>3}/{c['n']:<3} {c['rate']}")
        for n in r.get("notes", []):
            out.append(f"   note: {n}")
    out.append("bindings: " + (" ".join(doc["bind_args"]) or "none yet"))
    if doc["s10_knobs"]:
        out.append("S10 knobs: " + doc["s10_knobs"]["command"])
    for op in doc["operator_decisions"]:
        out.append(f"operator decision, {op['subject']} ({op['rule']}): {op['reason']}")
    for cfg, row in sorted(doc["experiments_rows"].items(), key=lambda kv: cfg_order(kv[0])):
        out.append(f"experiments row {cfg}: " + ", ".join(f"{k}={v}" for k, v in row.items()))
    return "\n".join(out) + "\n"


def log_actions(actions, event):
    for act in actions:
        if act["action"] == "cut_skipped":
            log.warning("event=%s config_id=%s action=cut_skipped reason=status cut is final (A7) would_change=%s",
                        event, act["config_id"], ",".join(act["would_change"]) or "-")
        else:
            log.info("event=%s config_id=%s action=%s", event, act["config_id"], act["action"])


def run(a) -> int:
    lab = lab_results_dir()
    if a.write_experiments and os.path.realpath(a.out) != os.path.realpath(lab):
        raise Refusal(f"--write-experiments takes the lab lock in RESULTS_DIR ({lab}), and --out is {a.out}; "
                      "set RESULTS_DIR as for run_all.sh, or drop --out")
    ret, inputs, hdrs, digest = load_inputs(a.retrieval)
    stamp = stamp_of(hdrs, a.stamp)

    # experiments.csv: read first (cut rows feed R4, A7), plan before writing, so a refusal leaves
    # nothing written
    before = None
    if os.path.exists(a.experiments):
        with open(a.experiments, "rb") as f:
            before = f.read()
        try:
            cut_rows = cut_rows_of(before.decode("utf-8"))
        except UnicodeDecodeError as e:
            raise Refusal(f"{a.experiments}: not UTF-8 ({e})") from None
    elif a.write_experiments:
        raise NoInput(f"{a.experiments}: no such file")
    else:
        cut_rows = frozenset()
        log.warning("event=no_experiments path=%s reason=cut rows unknown; decided as if none is cut", a.experiments)
    res = decide(ret, cut_rows)
    csv_plan = None
    if before is not None:
        try:
            text, actions = plan_experiments(before.decode("utf-8"), res["experiments_rows"])
            csv_plan = (before, text.encode("utf-8"), actions)
        except Refusal as e:
            if a.write_experiments:
                raise
            log.warning("event=experiments_dry_run_refused reason=%s", e)
    for rid, r in res["decisions"].items():
        log.info("event=rule rule=%s status=%s winner=%s missing=%s", rid, r["status"], r.get("winner"),
                 ",".join(r.get("missing", [])) or "-")
    for op in res["operator_decisions"]:
        log.warning("event=operator_decision subject=%s rule=%s reason=%s", op["subject"], op["rule"], op["reason"])
    doc = public({"tool": VERSION, "rules": RULES_VERSION, "stamp": stamp, "split": SPLIT,
                  "questions_digest": digest, "inputs": inputs, **res})
    data = (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")

    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"dev_decisions_{stamp}.json")
    old = None
    if os.path.exists(path):
        with open(path, "rb") as f:
            old = f.read()
        if old != data:
            raise Refusal(f"{os.path.basename(path)} exists with other content; pass --stamp for a new file")

    if a.write_experiments:
        with LabLock(lab):
            before, after, actions = csv_plan
            if after != before:
                replace_atomic(a.experiments, after, before, os.stat(a.experiments).st_mode & 0o777)
            log_actions(actions, "experiments_row")
            log.info("event=experiments file=%s changed=%s", os.path.basename(a.experiments), after != before)
    elif csv_plan:
        log_actions(csv_plan[2], "experiments_dry_run")

    if old is None:
        replace_atomic(path, data, None)
        log.info("event=written path=%s", path)
    else:
        log.info("event=unchanged path=%s", path)
    sys.stdout.write(report(doc))
    return 0


def main(argv=None) -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="RAG lab dev-split decisions (DEV_RULES.md R1-R7, amendments A1-A11)")
    ap.add_argument("--retrieval", nargs="*", default=[], help="retrieval_<INDEX>_<UTC>.jsonl, one per index")
    ap.add_argument("--experiments", default=DEFAULT_EXPERIMENTS)
    ap.add_argument("--out", default=None, help="default: RESULTS_DIR as run_all.sh derives it")
    ap.add_argument("--write-experiments", action="store_true",
                    help="fill rows 6, 7, 14-17 and 19 of experiments.csv (atomic, idempotent; --out must be RESULTS_DIR)")
    ap.add_argument("--stamp", default=None, help="YYYYMMDDTHHMMSSZ (default: the latest input header utc)")
    a = ap.parse_args(argv)
    if a.out is None:
        a.out = lab_results_dir()
    try:
        return run(a)
    except NoInput as e:
        log.error("event=no_input reason=%s", e)
        return 1
    except Refusal as e:
        log.error("event=refused reason=%s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
