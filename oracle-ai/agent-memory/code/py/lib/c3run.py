# v1.1 2026-10-12  Brief 12 step 7 (PLAN 7.3, frozen): pure helpers for the C3 bounded-context comparison. They
#                  validate the frozen 40 set queries, fix the arm/run schedule, score one returned list with the
#                  frozen measures of py/lib/stats.py, check RV_VEC's read-back, and assemble the bootstrap decisions
#                  and the descriptive tables. No database, no network.
#                  v1.1: (Codex 34 r1, finding 3) check_store() also needs 400 distinct chunk sources, no TTL and
#                  identity coverage 0/0 (lib.c1db.coverage), so a missing chunk cannot hide behind an orphan one.
#                  v1.0: first version (phase 2, before step 7's first run).
"""C3 assembly. Nothing here defines a measure: estimated tokens (the SDK's own count), gold recall and precision,
the determinism check, the percentiles and the paired bootstrap with its decisions are lib.stats (PLAN 7.0 freeze).
This module only applies them to the per-query records step 7 writes.

Per-query record (one per query, arm and run), as py/70_bounded_context.py writes it:
    {"qid", "arm", "run", "returned_ids" (the SDK's order), "estimated_tokens", "recall", "precision",
     "pruner_calls", "pruner_input_tokens", "pruner_output_tokens", "pruner_total_tokens", "latency_ms", ...}
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Mapping, Sequence

from lib import stats

ARMS = stats.C3_ARMS                                   # TOPK10, TOPK5, PRUNE_FAST, PRUNE_EXT, PRUNE_EXH
PRUNING_ARMS = stats.C3_PRUNING_ARMS
TOPK_ARMS = ("TOPK10", "TOPK5")
RUNS = {"TOPK10": 2, "TOPK5": 2, "PRUNE_FAST": 3, "PRUNE_EXT": 3, "PRUNE_EXH": 3}       # PLAN 7.3 "Runs"
MAX_RESULTS = {"TOPK10": 10, "TOPK5": 5, "PRUNE_FAST": 10, "PRUNE_EXT": 10, "PRUNE_EXH": 10}
EXPECTED_MAX_CALLS = {"PRUNE_FAST": 6, "PRUNE_EXT": 6, "PRUNE_EXH": 10}     # PLAN 7.3, for a 10-candidate pool
STORE_ID = "RV_VEC"
USER_ID = "BIZ_0001"
N_QUERIES = stats.C3_N_QUERIES                         # 40
MEMORY_ID_RE = re.compile(r"^biz_m(\d{4})$")


class DataError(ValueError):
    """The frozen data is not what PLAN 7.3 describes: the step stops before any database work."""


def load_queries(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                raise DataError(f"empty line {n}")
            rows.append(json.loads(line))
    if len(rows) != N_QUERIES:
        raise DataError(f"{len(rows)} queries, PLAN 7.3 needs {N_QUERIES}")
    qids = [r["qid"] for r in rows]
    if qids != [f"C3-{i:02d}" for i in range(1, N_QUERIES + 1)]:
        raise DataError("qids are not C3-01..C3-40 in order")
    for r in rows:
        g = r["gold_memory_ids"]
        if not (2 <= len(g) <= 5) or len(set(g)) != len(g) or r["gold_size"] != len(g):
            raise DataError(f"{r['qid']}: gold set must hold 2-5 distinct memories")
        for mid in g:
            mm = MEMORY_ID_RE.match(mid)
            if not mm or not (1 <= int(mm.group(1)) <= 400):
                raise DataError(f"{r['qid']}: {mid} is not a corpus memory id")
        if not isinstance(r["text"], str) or not r["text"].strip():
            raise DataError(f"{r['qid']}: empty text")
    return rows


ROUNDS = ((1, ("TOPK10", "TOPK5", "PRUNE_FAST", "PRUNE_EXT", "PRUNE_EXH")),
          (2, ("PRUNE_EXT", "PRUNE_EXH", "PRUNE_FAST", "TOPK5", "TOPK10")),
          (3, ("PRUNE_EXH", "PRUNE_FAST", "PRUNE_EXT")))


def schedule() -> list[tuple[int, str]]:
    """(round, arm) in execution order. Arm order rotated per run (PLAN 7.0): the three pruning arms rotate so each
    takes each position once over its three runs (FAST, EXT, EXH; EXT, EXH, FAST; EXH, FAST, EXT); the two top-K
    arms run first in round 1 and last, reversed, in round 2, and have no third run (PLAN 7.3)."""
    return [(rnd, a) for rnd, arms in ROUNDS for a in arms]


def score(returned_ids: Sequence[str], formatted: Sequence[str], gold: Sequence[str]) -> dict:
    rec, prec = stats.recall_precision(returned_ids, gold)
    return {"estimated_tokens": stats.estimated_tokens(formatted), "recall": rec, "precision": prec,
            "n_returned": len(returned_ids)}


def check_store(rb: Mapping) -> list[str]:
    """RV_VEC as step 4 left it: 400 valid memories of BIZ_0001 and nothing else, one chunk with an embedding each."""
    bad = []
    mem = rb.get("memories", {})
    if (mem.get("rows"), mem.get("distinct_ids"), mem.get("valid"), mem.get("other_users")) != (400, 400, 400, 0):
        bad.append(f"RV_VEC memories {mem}, need 400 distinct valid of {USER_ID} and no other user")
    if mem.get("with_ttl") != 0:
        bad.append(f"RV_VEC memories with a TTL: {mem.get('with_ttl')}, need 0")
    ch = rb.get("chunks", {})
    if (ch.get("rows"), ch.get("distinct_sources"), ch.get("with_embedding")) != (400, 400, 400):
        bad.append(f"RV_VEC chunks {ch}, need 400 from 400 distinct memories, each with an embedding")
    cov = rb.get("coverage") or {}
    if (cov.get("memories_without_chunk"), cov.get("chunks_without_memory")) != (0, 0):
        bad.append(f"RV_VEC identity coverage {cov or 'missing'}, need 0 and 0")
    return bad


def per_query_values(records: Sequence[Mapping], queries: Sequence[Mapping]) -> tuple[dict, dict, dict]:
    """tokens[arm][q], recall[arm][q] and precision[arm][q] in qid order: top-K arms from run 1 (PLAN 7.3: the first
    of the two identical runs is used), pruning arms averaged over their 3 runs."""
    qids = [q["qid"] for q in queries]
    by = {}
    for r in records:
        by.setdefault((r["arm"], r["qid"]), {})[r["run"]] = r
    tokens, recall, prec = {}, {}, {}
    for arm in ARMS:
        tokens[arm], recall[arm], prec[arm] = [], [], []
        for q in qids:
            runs = by.get((arm, q), {})
            use = [1] if arm in TOPK_ARMS else [1, 2, 3]
            if any(n not in runs for n in use):
                raise ValueError(f"{arm} {q}: missing run(s) {[n for n in use if n not in runs]}")
            tokens[arm].append(statistics.fmean(float(runs[n]["estimated_tokens"]) for n in use))
            recall[arm].append(statistics.fmean(float(runs[n]["recall"]) for n in use))
            prec[arm].append(statistics.fmean(float(runs[n]["precision"]) for n in use))
    return tokens, recall, prec


def determinism(records: Sequence[Mapping]) -> dict:
    """stats.topk_deterministic for each top-K arm: query ids whose two runs differ (empty = identical)."""
    out = {}
    for arm in TOPK_ARMS:
        r1 = {r["qid"]: r["returned_ids"] for r in records if r["arm"] == arm and r["run"] == 1}
        r2 = {r["qid"]: r["returned_ids"] for r in records if r["arm"] == arm and r["run"] == 2}
        out[arm] = stats.topk_deterministic(r1, r2)
    return out


def decisions(tokens: Mapping, recall: Mapping) -> list[dict]:
    rows = []
    for d in stats.c3_bootstrap(tokens, recall):
        rows.append({"arm": d.arm, "r10_upper": d.r10_upper, "d10_lower": d.d10_lower, "r5_upper": d.r5_upper,
                     "d5_lower": d.d5_lower, "decision_a_smaller_without_material_loss": d.decision_a,
                     "decision_b_better_than_top5": d.decision_b})
    return rows


def wording(dec: Sequence[Mapping]) -> dict:
    """PLAN 7.3: a benefit is claimed only by a decision that holds; otherwise 'pruning did not show a benefit'."""
    shown = {d["arm"]: [k for k in ("decision_a_smaller_without_material_loss", "decision_b_better_than_top5")
                        if d[k]] for d in dec}
    any_benefit = any(shown.values())
    return {"benefits_shown": shown,
            "sentence": ("pruning showed a benefit on this set for: " +
                         "; ".join(f"{a} ({', '.join(v)})" for a, v in shown.items() if v))
            if any_benefit else "pruning did not show a benefit on this set",
            "rule": "PLAN 7.3: (a) upper bound of R10 < 0.80 and lower bound of D10 > -0.10; (b) lower bound of "
                    "D5 > 0 and upper bound of R5 <= 1.00; bounds one-sided at 0.05/6; answer quality not measured; "
                    "the pruner's own tokens are shown beside the tokens it saves"}


def describe(records: Sequence[Mapping], queries: Sequence[Mapping], tokens: Mapping, recall: Mapping,
             prec: Mapping) -> list[dict]:
    """Exploratory/descriptive per arm: returned tokens, recall, precision, results returned, pruner calls and
    tokens (per query and in total, per run), latency median and p90, calls above the plan's expectation."""
    out = []
    n = len(queries)
    for arm in ARMS:
        mine = [r for r in records if r["arm"] == arm]
        runs = sorted({r["run"] for r in mine})
        per_run = []
        for rn in runs:
            rr = [r for r in mine if r["run"] == rn]
            per_run.append({"run": rn, "tokens_sum": sum(r["estimated_tokens"] for r in rr),
                            "recall_mean": statistics.fmean(r["recall"] for r in rr),
                            "precision_mean": statistics.fmean(r["precision"] for r in rr),
                            "returned_mean": statistics.fmean(r["n_returned"] for r in rr),
                            "pruner_calls": sum(r["pruner_calls"] for r in rr),
                            "pruner_total_tokens": sum(r["pruner_total_tokens"] for r in rr),
                            "pruner_input_tokens": sum(r["pruner_input_tokens"] for r in rr),
                            "pruner_output_tokens": sum(r["pruner_output_tokens"] for r in rr),
                            "max_calls_one_query": max(r["pruner_calls"] for r in rr),
                            "queries_above_expected_calls": (sum(1 for r in rr if r["pruner_calls"]
                                                                 > EXPECTED_MAX_CALLS[arm])
                                                             if arm in EXPECTED_MAX_CALLS else 0),
                            "overlap_warnings": sum(r.get("overlap_warnings", 0) for r in rr)})
        lat = [r["latency_ms"] for r in mine]
        out.append({"arm": arm, "runs": runs, "queries": n,
                    "tokens_sum_used": sum(tokens[arm]), "recall_mean_used": statistics.fmean(recall[arm]),
                    "precision_mean_used": statistics.fmean(prec[arm]), "per_run": per_run,
                    "pruner_calls_per_query_mean": statistics.fmean(r["pruner_calls"] for r in mine),
                    "pruner_total_tokens_per_run_mean": statistics.fmean(p["pruner_total_tokens"] for p in per_run),
                    "latency_ms_median": stats.percentile_linear(lat, 50),
                    "latency_ms_p90": stats.percentile_linear(lat, 90)})
    base10 = next(d for d in out if d["arm"] == "TOPK10")["tokens_sum_used"]
    base5 = next(d for d in out if d["arm"] == "TOPK5")["tokens_sum_used"]
    for d in out:
        d["returned_tokens_saved_vs_topk10"] = base10 - d["tokens_sum_used"]
        d["returned_tokens_saved_vs_topk5"] = base5 - d["tokens_sum_used"]
    return out


def void_reasons(failed_calls: int, calls: int, aborted: bool) -> list[str]:
    from lib import llmretry
    out = []
    if aborted:
        out.append("a search raised (technical abort)")
    if llmretry.void_by_failures(failed_calls, calls):
        out.append(f"{failed_calls} of {calls} LLM calls failed after the retries (> 5%)")
    return out
