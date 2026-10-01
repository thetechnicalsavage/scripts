#!/usr/bin/env python3
# v1.3 - (v1.3, GO-3: "declined" answer records are scored like no_match) RAG tuning lab: statistics for the retrieval and answer layers (PLAN.md 5.4).
#        v1.2 (review 1, 29-Sep): human audit verdicts (results/audit_verdicts.jsonl, keyed by index
#              or config_id, id and run) override the scorer in load_answers, and every table,
#              grid and family is reported twice, "scorer" and "adjudicated", with an audit block
#              (applied, changed, agreement, confusion, flagged answers still unaudited); bucket D
#              (retrieval_only) is out of every hypothesis unit set, the tables (own D rows) and
#              the grid; H1 is restricted to facts containable in both twins, with the
#              unconditional variant reported next to it outside the Holm family.
#        v1.1: after Codex review: every input file must carry the same questions seal digest,
#              and partial runs (--only, or a split that excludes the one analysed) are refused;
#              a query record must belong to its own file's header.
#        v1.0: first version: majority of 3 runs, exact McNemar on discordant pairs, Holm over the
#              pre-declared family H1-H5 (alpha 0.05), Wilson 95% CIs, attribution grid.
#
# Run as : any user, no database. stdlib only (math.comb), so the numbers do not depend on scipy.
# Usage  : python3 stats.py answers --rag ../../results/rag_<UTC>_r1.jsonl ... [--split test]
#                                   [--audit ../../results/audit_verdicts.jsonl]   (default: that file if present)
#          python3 stats.py family  --rag ... --retrieval ../../results/retrieval_<INDEX>_<UTC>.jsonl ...
#                                   [--spec hypotheses.json] [--bind S10=RAG_... --bind S3_WINNER=RAG_...]
#          python3 stats.py grid    --rag ... --retrieval ... [--split test]
# Re-run : safe; reads JSONL, writes a new results/stats_<kind>_<UTC>.json.
#
# The H1-H5 definitions below are the harness's reading of PLAN.md section 1. They are a
# pre-registration: fix them (or pass --spec) BEFORE the test split is scored, and do not edit
# them after. A hypothesis whose inputs are missing gets p = 1 inside Holm (conservative).
# (v1.2 changed H1 to the containable-in-both-twins units before any split was scored.)
#
# Audit file (one JSON object per line, written by the human auditor):
#   {"index": "RAG_M0_C1024_O128_COS" | "config_id": "S1", "id": "T08-EN", "run": 2,
#    "human_verdict": "correct", "auditor": "...", "note": "...", "answer_sha256": "<optional>"}
# human_verdict is one of eval_norm.VERDICTS. answer_sha256, when given, must equal sha256 of the
# scored answer text, so a verdict cannot land on a re-run answer it was not given for.
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import logging
import math
import os
import sys
import time

log = logging.getLogger("stats")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from eval_norm import VERDICTS  # noqa: E402

DEFAULT_AUDIT = os.path.join(HERE, "..", "..", "results", "audit_verdicts.jsonl")
RETRIEVAL_ONLY_BUCKETS = ("D",)         # dialect paraphrases of T facts: exploratory, own rows only
Z95 = 1.959963984540054
ALPHA = 0.05
BUDGET_CHARS = 6000                     # PLAN.md 4.1 equal context budget: k = 9/6/4/3


# ---------------------------------------------------------------------------------------------
# pure statistics (unit-tested against known tables)
# ---------------------------------------------------------------------------------------------
def wilson(x: int, n: int, z: float = Z95):
    """Wilson score interval for x successes out of n. n = 0 gives the uninformative (0, 1)."""
    if n <= 0:
        return 0.0, 1.0
    if not 0 <= x <= n:
        raise ValueError("need 0 <= x <= n")
    p = x / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar: binomial test of min(b, c) on n = b + c discordant pairs, p = 0.5."""
    if b < 0 or c < 0:
        raise ValueError("counts must be >= 0")
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def holm(pvals: dict, alpha: float = ALPHA, m: int | None = None) -> dict:
    """Holm step-down. pvals: {id: p or None}; None counts as p = 1 (missing input)."""
    m = m or len(pvals)
    items = sorted(((1.0 if p is None else p), hid) for hid, p in pvals.items())
    out, running, stop = {}, 0.0, False
    for i, (p, hid) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        reject = (not stop) and running <= alpha
        if not reject:
            stop = True
        out[hid] = {"p": p, "p_holm": round(running, 6), "reject": reject, "missing": pvals[hid] is None}
    return out


def majority(verdicts):
    """Per-question majority of the scored runs (infra errors are never scored). Needs at least
    2 scored runs; with 2, both must be correct. Returns (bool or None, n_scored, n_correct)."""
    v = [x for x in verdicts if x is not None]
    n, k = len(v), sum(1 for x in v if x == "correct")
    if n < 2:
        return None, n, k
    return k * 2 > n, n, k


def paired(a: dict, b: dict) -> dict:
    """a, b: {unit: bool}. McNemar on the units present (not None) in both."""
    keys = sorted(k for k in a if k in b and a[k] is not None and b[k] is not None)
    bb = sum(1 for k in keys if a[k] and not b[k])        # a right, b wrong
    cc = sum(1 for k in keys if b[k] and not a[k])        # b right, a wrong
    n = len(keys)
    xa, xb = sum(1 for k in keys if a[k]), sum(1 for k in keys if b[k])
    p = mcnemar_exact(bb, cc)
    return {"n": n, "a_only": bb, "b_only": cc, "p": round(p, 6),
            "a_rate": _r(xa, n), "b_rate": _r(xb, n),
            "better": "a" if bb > cc else ("b" if cc > bb else "tie"),
            "label": "significant" if p < ALPHA else "within noise"}


def _r(x, n):
    lo, hi = wilson(x, n)
    return {"x": x, "n": n, "rate": round(x / n, 4) if n else None, "ci95": [round(lo, 4), round(hi, 4)]}


def attribution_grid(pairs):
    """pairs: iterable of (retrieved: bool, correct: bool). PLAN.md 5.3 attribution grid."""
    g = {"both": 0, "generation_failure": 0, "retrieval_miss": 0, "correct_without_retrieval": 0}
    for r, c in pairs:
        if r is None or c is None:
            continue
        g["both" if r and c else "generation_failure" if r else
          "correct_without_retrieval" if c else "retrieval_miss"] += 1
    return g


def budget_k(chunk_size: int, budget: int = BUDGET_CHARS) -> int:
    return max(1, int(math.floor(budget / chunk_size + 0.5)))


# ---------------------------------------------------------------------------------------------
# pre-declared confirmatory family (PLAN.md 1 and 5.4). <NAME> placeholders need --bind.
# ---------------------------------------------------------------------------------------------
S1 = "RAG_M0_C1024_O128_COS"
S2 = "RAG_M0_C2000_O300_COS"
DEFAULT_SPEC = {
    "alpha": ALPHA,
    "hypotheses": [
        {"id": "H1", "layer": "retrieval", "split": "test", "bucket": "T", "pair_on": "fact_id",
         "condition": "containable_both", "report_unconditional": True,
         "claim": "M0 finds the evidence AR->AR less often than EN->EN on the same facts (masked), "
                  "on facts whose evidence fits one chunk in both twins",
         "a": {"index": S1, "direction": "EN->EN", "k": 5}, "b": {"index": S1, "direction": "AR->AR", "k": 5}},
        {"id": "H2", "layer": "retrieval", "split": "test", "bucket": "T", "pair_on": "id",
         "claim": "the best multilingual model (chosen on dev) beats M0 on masked AR->AR evidence hit@5",
         "a": {"index": "<BEST_MULTILINGUAL>", "direction": "AR->AR", "k": 5},
         "b": {"index": S1, "direction": "AR->AR", "k": 5}},
        {"id": "H3", "layer": "answer", "split": "test", "answerable": True, "pair_on": "id",
         "claim": "S10 tuned beats S1 baseline on answer accuracy (majority of 3)",
         "a": {"index": "<S10>"}, "b": {"index": S1}},
        {"id": "H4", "layer": "retrieval", "split": "test", "answerable": True, "pair_on": "id",
         "claim": "S2 production retrieval settings vs S1 defaults, evidence hit at each match_limit",
         "a": {"index": S2, "run_label": "unmasked", "k": "match_limit", "threshold": "similarity_threshold"},
         "b": {"index": S1, "run_label": "unmasked", "k": "match_limit", "threshold": "similarity_threshold"}},
        {"id": "H5", "layer": "retrieval", "split": "test", "answerable": True, "pair_on": "id",
         "claim": "the chunk_size winner vs 1024, evidence hit at an equal ~6,000-character budget",
         "a": {"index": "<S3_WINNER>", "run_label": "unmasked", "k": "budget"},
         "b": {"index": S1, "run_label": "unmasked", "k": "budget"}},
    ],
}


def bind_spec(spec, binds):
    s = json.loads(json.dumps(spec))
    for h in s["hypotheses"]:
        for side in ("a", "b"):
            idx = h[side].get("index", "")
            if idx.startswith("<") and idx.endswith(">"):
                h[side]["index"] = binds.get(idx[1:-1])
    return s


# ---------------------------------------------------------------------------------------------
# loading results
# ---------------------------------------------------------------------------------------------
def read_jsonl(paths):
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if line.strip():
                    try:
                        yield p, json.loads(line)
                    except ValueError as e:
                        raise SystemExit(f"{p}:{n}: bad JSON ({e})") from e


def headers(paths):
    return [r for _, r in read_jsonl(paths) if r.get("type") == "header"]


def check_headers(hdrs, split=None):
    """Refuse to pair results from different question sets or from partial runs."""
    digests = {((h.get("seal") or {}).get("digest")) for h in hdrs}
    if len(digests) > 1:
        raise SystemExit(f"inputs come from different questions.json versions: {sorted(map(str, digests))}")
    for h in hdrs:
        if h.get("only"):
            raise SystemExit(f"{h.get('index') or h.get('stamp')}: a --only run cannot be analysed")
        if split and h.get("split", "all") not in ("all", split):
            raise SystemExit(f"{h.get('index') or h.get('stamp')}: run with split={h.get('split')}, "
                             f"analysis needs {split}")
    if None in digests:
        raise SystemExit("an input file has no questions seal in its header")
    return digests.pop() if digests else None


def load_retrieval(paths):
    """{index: {"header": h, "records": {(id, run_label): rec}}}; one file per index."""
    out, owner = {}, {}
    for p, r in read_jsonl(paths):
        if r.get("type") == "header":
            if r["index"] in out:
                raise SystemExit(f"two retrieval files for {r['index']}: pass one")
            out[r["index"]] = {"header": r, "records": {}}
            owner[p] = r["index"]
        elif r.get("type") == "query" and r.get("pass", 1) == 1:
            idx = owner.get(p)
            if idx is None or r.get("index", idx) != idx:
                raise SystemExit(f"{p}: query record without its own header index")
            out[idx]["records"][(r["id"], r["run_label"])] = r
    return out


class Answers(dict):
    """{(index, id): entry} as returned by load_answers, plus the audit bookkeeping."""
    audit_records = 0
    audit_unmatched = ()


def _sha(text) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def load_audit(path):
    """{(index or config_id, id, run): record} from the auditor's JSONL. Refuses a verdict that is
    not a scorer verdict, a record without index/config_id, id and run, and two different verdicts
    for one key."""
    out = {}
    for p, r in read_jsonl([path]):
        owner = r.get("index") or r.get("config_id")
        if not owner or not r.get("id") or r.get("run") in (None, ""):
            raise SystemExit(f"{p}: an audit record needs index or config_id, id and run: {r}")
        if r.get("human_verdict") not in VERDICTS:
            raise SystemExit(f"{p}: human_verdict {r.get('human_verdict')!r} is not one of {VERDICTS}")
        try:
            run = int(r["run"])
        except (TypeError, ValueError) as e:
            raise SystemExit(f"{p}: run {r['run']!r} is not a number") from e
        key = (owner, r["id"], run)
        if key in out and out[key]["human_verdict"] != r["human_verdict"]:
            raise SystemExit(f"{p}: two different human verdicts for {key}")
        out[key] = r
    return out


def load_answers(paths, audit=None):
    """{(index, id): {"q": rec, "runs": {run: verdict or None}, "scorer_runs": {...}, ...}}. For a
    (config, id, run) key the last scored record wins; infra_error records only fill a run that
    has no scored record. v1.2: "runs" holds the adjudicated verdict (the human verdict where the
    audit file has one, else the scorer's); "scorer_runs" keeps the scorer's."""
    out = Answers()
    for _, r in read_jsonl(paths):
        if r.get("type") != "answer":
            continue
        e = out.setdefault((r["index"], r["id"]), {"q": r, "runs": {}, "scorer_runs": {}, "flagged": {},
                                                   "human": {}, "answer_sha": {},
                                                   "config_id": r.get("config_id")})
        scored = r.get("status") in ("ok", "no_match", "declined")      # v1.3: declined = a scored refusal
        if scored or r["run"] not in e["scorer_runs"]:
            e["scorer_runs"][r["run"]] = r.get("verdict") if scored else None
            e["flagged"][r["run"]] = bool(r.get("audit")) if scored else False
            e["answer_sha"][r["run"]] = _sha(r.get("answer")) if scored else None
    pending = dict(audit or {})
    for (idx, qid), e in out.items():
        for run, verdict in e["scorer_runs"].items():
            found = [pending.pop(k) for k in {(idx, qid, run), (e["config_id"], qid, run)} if k in pending]
            if len({h["human_verdict"] for h in found}) > 1:
                raise SystemExit(f"audit: index and config_id records disagree for {idx} {qid} run {run}")
            if not found:
                e["runs"][run] = verdict
                continue
            h = found[0]
            if verdict is None:
                raise SystemExit(f"audit: {idx} {qid} run {run} has no scored answer (infra_error)")
            if h.get("answer_sha256") and h["answer_sha256"] != e["answer_sha"][run]:
                raise SystemExit(f"audit: {idx} {qid} run {run} was audited on a different answer text")
            e["runs"][run] = e["human"][run] = h["human_verdict"]
    out.audit_records = len(audit or {})
    out.audit_unmatched = tuple(sorted(pending, key=str))
    if pending:
        log.warning("event=audit_unmatched count=%d first=%s", len(pending), out.audit_unmatched[0])
    return out


def scorer_view(ans):
    """The same answers with the scorer's verdicts only (no human overrides)."""
    v = Answers({k: dict(e, runs=dict(e["scorer_runs"]), human={}) for k, e in ans.items()})
    v.audit_records, v.audit_unmatched = ans.audit_records, ans.audit_unmatched
    return v


def audit_summary(ans) -> dict:
    """Scorer-vs-human agreement (PLAN.md 5.3) and what is still waiting for an auditor."""
    pairs = [(e["scorer_runs"][run], hv) for e in ans.values() for run, hv in sorted(e["human"].items())]
    changed = sum(1 for s, h in pairs if s != h)
    flagged = [(e, run) for e in ans.values() for run, f in e["flagged"].items() if f]
    return {"records": ans.audit_records, "applied": len(pairs), "changed": changed,
            "agreement": _r(len(pairs) - changed, len(pairs)),
            "confusion": dict(sorted(collections.Counter(f"{s}->{h}" for s, h in pairs).items())),
            "flagged": len(flagged), "flagged_unaudited": sum(1 for e, run in flagged if run not in e["human"]),
            "unmatched": len(ans.audit_unmatched)}


def _k_for(side, header):
    k = side.get("k", 5)
    p = header.get("pairing") or {}
    if k == "match_limit":
        return int(p.get("match_limit") or 5)
    if k == "budget":
        import eval_retrieval as er
        m = er.INDEX_NAME.match(header["index"])
        if not m:
            raise SystemExit(f"budget k needs a RAG_<M>_C<chunk>_... index name: {header['index']}")
        return budget_k(int(m.group(2)))
    return int(k)


def _thr_for(side, header):
    t = side.get("threshold")
    if t == "similarity_threshold":
        t = (header.get("pairing") or {}).get("similarity_threshold")
    return float(t) if t not in (None, "") and float(t) > 0 else None


def _excluded(bucket, h) -> bool:
    """Bucket D is never a confirmatory unit, unless a hypothesis names it explicitly."""
    return bucket in RETRIEVAL_ONLY_BUCKETS and h.get("bucket") not in RETRIEVAL_ONLY_BUCKETS


def retrieval_records(ret, side, h):
    """(header, {unit: record}) for one side of a retrieval hypothesis, or (None, None)."""
    data = ret.get(side.get("index"))
    if not data:
        return None, None
    out = {}
    for rec in data["records"].values():
        if _excluded(rec["bucket"], h):
            continue
        if h.get("split") and rec["split"] != h["split"]:
            continue
        if h.get("bucket") and rec["bucket"] != h["bucket"]:
            continue
        if h.get("answerable") is not None and rec["answerable"] != h["answerable"]:
            continue
        if side.get("direction") and rec["direction"] != side["direction"]:
            continue
        if side.get("run_label") and rec["run_label"] != side["run_label"]:
            continue
        unit = rec[h.get("pair_on", "id")]
        if unit in out:
            raise SystemExit(f"{h['id']}: two records for unit {unit} in {side['index']}")
        out[unit] = rec
    return data["header"], out


def retrieval_outcomes(ret, side, h, units=None):
    import eval_retrieval as er
    header, recs = retrieval_records(ret, side, h)
    if recs is None:
        return None
    k, thr = _k_for(side, header), _thr_for(side, header)
    return {u: er.hit_at(rec, k, "evidence", thr) for u, rec in recs.items() if units is None or u in units}


def answer_outcomes(ans, side, h):
    out = {}
    for (idx, qid), e in ans.items():
        q = e["q"]
        if idx != side.get("index") or _excluded(q.get("bucket"), h):
            continue
        if h.get("split") and q["split"] != h["split"]:
            continue
        if h.get("answerable") is not None and q["answerable"] != h["answerable"]:
            continue
        out[q[h.get("pair_on", "id")]] = majority(e["runs"].values())[0]
    return out or None


def containable_both(ret, h):
    """Units whose evidence is containable in the run of both sides (H1: EN->EN and AR->AR), so a
    chunk boundary that splits one twin's span cannot pass for a retrieval difference."""
    _, ra = retrieval_records(ret, h["a"], h)
    _, rb = retrieval_records(ret, h["b"], h)
    if ra is None or rb is None:
        return set()
    return {u for u in ra if u in rb and ra[u].get("containable") and rb[u].get("containable")}


def family(spec, ret, ans):
    rows, pv = [], {}
    for h in spec["hypotheses"]:
        get = retrieval_outcomes if h["layer"] == "retrieval" else answer_outcomes
        src = ret if h["layer"] == "retrieval" else ans
        a = get(src, h["a"], h) if h["a"].get("index") else None
        b = get(src, h["b"], h) if h["b"].get("index") else None
        extra = {}
        if h.get("condition") == "containable_both" and a and b:
            if h.get("report_unconditional"):
                u = paired(a, b)
                extra["unconditional"] = dict(u, label="exploratory: " + u["label"])
            keep = containable_both(ret, h)
            a = {k: v for k, v in a.items() if k in keep}
            b = {k: v for k, v in b.items() if k in keep}
            extra["condition"] = "containable_both"
        if not a or not b:
            rows.append({"id": h["id"], "claim": h.get("claim"), "missing": True, **extra})
            pv[h["id"]] = None
            continue
        res = paired(a, b)
        rows.append({"id": h["id"], "claim": h.get("claim"), "a": h["a"], "b": h["b"], **res, **extra})
        pv[h["id"]] = res["p"]
    adj = holm(pv, spec.get("alpha", ALPHA), m=len(spec["hypotheses"]))
    for r in rows:
        r.update({"p_holm": adj[r["id"]]["p_holm"], "reject_holm": adj[r["id"]]["reject"]})
    return {"alpha": spec.get("alpha", ALPHA), "complete": all(not r.get("missing") for r in rows),
            "rows": rows}


def answers_table(ans, split=None):
    """One row per index for the core questions (scope "core") and, only when D answers exist (an
    --include-retrieval-only run), a separate "D_exploratory" row that never mixes with core."""
    by_idx = {}
    for (idx, _), e in ans.items():
        if split and e["q"]["split"] != split:
            continue
        scope = "D_exploratory" if e["q"].get("bucket") in RETRIEVAL_ONLY_BUCKETS else "core"
        by_idx.setdefault((idx, scope), []).append(e)
    out = []
    for (idx, scope), es in sorted(by_idx.items()):
        runs = sorted({r for e in es for r in e["runs"]})
        a = [e for e in es if e["q"]["answerable"]]
        u = [e for e in es if not e["q"]["answerable"]]
        maj = [majority(e["runs"].values())[0] for e in a]
        umaj = [majority(e["runs"].values())[0] for e in u]
        infra = sum(1 for e in es for v in e["runs"].values() if v is None)
        total = sum(len(e["runs"]) for e in es)
        stable = sum(1 for e in es if len(set(e["runs"].values())) == 1 and None not in e["runs"].values())
        out.append({
            "index": idx, "scope": scope, "runs": runs,
            "answerable_majority": _r(sum(1 for m in maj if m), sum(1 for m in maj if m is not None)),
            "answerable_per_run": {r: _r(sum(1 for e in a if e["runs"].get(r) == "correct"),
                                         sum(1 for e in a if e["runs"].get(r) is not None)) for r in runs},
            "unanswerable_majority_descriptive": _r(sum(1 for m in umaj if m),
                                                    sum(1 for m in umaj if m is not None)),
            "stable_all_runs": stable, "questions": len(es),
            "infra_unscored": infra, "infra_rate": round(infra / total, 4) if total else None,
            "publishable": (infra / total <= 0.02) if total else False})
    return out


def grid(ret, ans, split=None):
    import eval_retrieval as er
    out = {}
    for idx, data in ret.items():
        h = data["header"]
        k = _k_for({"k": "match_limit"}, h)
        thr = _thr_for({"threshold": "similarity_threshold"}, h)
        pairs = []
        for (rid, label), rec in data["records"].items():
            if label != "unmasked" or not rec["answerable"] or (split and rec["split"] != split):
                continue
            if rec["bucket"] in RETRIEVAL_ONLY_BUCKETS:
                continue
            e = ans.get((idx, rid))
            if e:
                pairs.append((er.hit_at(rec, k, "evidence", thr), majority(e["runs"].values())[0]))
        if pairs:
            out[idx] = {"k": k, "threshold": thr, **attribution_grid(pairs)}
    return out


def main(argv=None) -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="RAG lab statistics")
    ap.add_argument("kind", choices=("answers", "family", "grid"))
    ap.add_argument("--rag", nargs="*", default=[])
    ap.add_argument("--retrieval", nargs="*", default=[])
    ap.add_argument("--spec", default="")
    ap.add_argument("--bind", action="append", default=[], help="NAME=INDEX for <NAME> placeholders")
    ap.add_argument("--split", choices=("dev", "test"), default=None)
    ap.add_argument("--audit", default=None,
                    help=f"human verdicts JSONL (default {os.path.normpath(DEFAULT_AUDIT)} when it exists)")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "results"))
    a = ap.parse_args(argv)

    spec = DEFAULT_SPEC
    if a.spec:
        with open(a.spec, encoding="utf-8") as f:
            spec = json.load(f)
    splits = {h.get("split") for h in spec["hypotheses"]} if a.kind == "family" else {a.split}
    digest = check_headers(headers(a.rag + a.retrieval), splits.pop() if len(splits) == 1 else None)
    audit_path = a.audit or (DEFAULT_AUDIT if os.path.exists(DEFAULT_AUDIT) else None)
    if audit_path and not os.path.exists(audit_path):
        raise SystemExit(f"--audit {audit_path}: no such file")
    audit = load_audit(audit_path) if audit_path else {}
    log.info("event=audit file=%s records=%d", os.path.basename(audit_path) if audit_path else None, len(audit))
    ans = load_answers(a.rag, audit) if a.rag else Answers()
    scorer = scorer_view(ans)
    ret = load_retrieval(a.retrieval) if a.retrieval else {}
    # v1.2: every result twice, scorer-only and adjudicated (human verdicts applied), side by side
    if a.kind == "answers":
        res = {"scorer": answers_table(scorer, a.split), "adjudicated": answers_table(ans, a.split)}
    elif a.kind == "grid":
        res = {"scorer": grid(ret, scorer, a.split), "adjudicated": grid(ret, ans, a.split)}
    else:
        binds = dict(x.split("=", 1) for x in a.bind)
        bound = bind_spec(spec, binds)
        res = {"scorer": family(bound, ret, scorer), "adjudicated": family(bound, ret, ans)}
        if not res["adjudicated"]["complete"]:
            log.warning("event=family_incomplete missing=%s",
                        [r["id"] for r in res["adjudicated"]["rows"] if r.get("missing")])
    res["audit"] = dict(audit_summary(ans), file=os.path.basename(audit_path) if audit_path else None)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"stats_{a.kind}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"kind": a.kind, "utc": stamp, "split": a.split, "questions_digest": digest, "inputs":
                   [os.path.basename(p) for p in a.rag + a.retrieval], "result": res},
                  f, ensure_ascii=False, indent=1, sort_keys=True)
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    log.info("event=written path=%s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
