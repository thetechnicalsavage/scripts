# v1.2 2026-10-12  Brief 12 step 4 (PLAN 7.1, frozen): pure helpers for the C1 bake-off. They validate the frozen
#                  corpus and queries, fix the arm order per query, score one result list with the frozen measures of
#                  py/lib/stats.py and assemble the confirmatory family, the exploratory summary, P1/P2 and the
#                  wording rule. No database, no network; every number comes from a stats.py function.
#                  v1.2: (phase 2, after S4's second attempt stopped on 10-Oct 19:17Z; Codex 31 r1 finding 1:
#                  identity coverage, rb["coverage"] from lib.c1db.coverage, must be 0 and 0) check_loaded() moved here
#                  from py/40_retrieval_bakeoff.py (pure, so it is tested): a HYBRID store's vectors are checked in
#                  the hybrid index's <index>$VECTORS view (lib.c1db.hybrid_vectors), no longer in a chunk-table
#                  EMBEDDING column that a HYBRID store does not have; VECTOR and KEYWORD checks unchanged.
#                  v1.1: (Codex 26 r1) crosscheck(): the summary's per-class Hit@1 counts must equal the hits that
#                  stats.c1_family counted itself, so the exploratory table can never disagree with the frozen tests.
#                  v1.0: first version (day 1, phase 2).
"""C1 assembly. Nothing here defines a measure: Hit@k, the reciprocal rank, the percentiles, McNemar and Holm are the
frozen functions of lib.stats (PLAN 7.0). This module only applies them to the per-query records step 4 writes and
says, in the plan's words, what the numbers allow the post to say.

Per-query record (one per query and strategy), as py/40_retrieval_bakeoff.py writes it:
    {"qid", "qclass", "strategy", "ranked_ids" (the SDK's returned order, at most 10), "latency_ms", ...}
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from lib import stats

STRATEGIES = stats.C1_STRATEGIES                     # ("HYBRID", "VECTOR", "KEYWORD")
CLASSES = stats.C1_CLASSES                           # ("A", "B", "C")
STORE_OF = {"HYBRID": "RV_HYB", "VECTOR": "RV_VEC", "KEYWORD": "RV_KEY"}
USER_ID = "BIZ_0001"
N_MEMORIES = 400
N_PER_CLASS = stats.C1_PER_CLASS                     # 40
MEMORY_TYPES = ("fact", "guideline", "memory", "preference")
MAX_RESULTS = 10                                     # PLAN 7.1 call: max_results=10
P1_MIN_ZERO_B = 36                                   # PLAN 7.1 prediction P1 (KEYWORD, class B)
P2_MIN_ZERO_C = 30                                   # PLAN 7.1 prediction P2 (KEYWORD, class C)
NEAR_COLLISION_A = 15                                # PLAN 7.1: 15 class A and 15 class C near-collision golds
NEAR_COLLISION_C = 15
MEMORY_ID_RE = re.compile(r"^biz_m\d{4}$")


class DataError(ValueError):
    """The frozen data is not what PLAN 7.1 describes: the step stops before any database work."""


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                raise DataError(f"{Path(path).name}: empty line {n}")
            rows.append(json.loads(line))
    return rows


def load_corpus(path: Path) -> list[dict]:
    """The 400 memories of BIZ_0001 in memory-id order (= event-time order, PLAN 7.1 precisions)."""
    rows = _read_jsonl(path)
    if len(rows) != N_MEMORIES:
        raise DataError(f"corpus has {len(rows)} memories, PLAN 7.1 needs {N_MEMORIES}")
    ids = [r["memory_id"] for r in rows]
    if ids != [f"biz_m{i:04d}" for i in range(1, N_MEMORIES + 1)]:
        raise DataError("corpus memory ids are not biz_m0001..biz_m0400 in order")
    for r in rows:
        if r["user_id"] != USER_ID:
            raise DataError(f"{r['memory_id']}: user {r['user_id']} is not {USER_ID}")
        if r["memory_type"] not in MEMORY_TYPES:
            raise DataError(f"{r['memory_id']}: memory_type {r['memory_type']} is not a memory type")
        if not isinstance(r["content"], str) or not r["content"].strip():
            raise DataError(f"{r['memory_id']}: empty content")
        if not isinstance(r["timestamp"], str) or not r["timestamp"].endswith("+00:00"):
            raise DataError(f"{r['memory_id']}: timestamp is not ISO 8601 UTC")
    if [r["timestamp"] for r in rows] != sorted(r["timestamp"] for r in rows):
        raise DataError("corpus is not in event-time order")
    return rows


def load_queries(path: Path, corpus_ids: Iterable[str]) -> list[dict]:
    """The 120 labelled queries, sorted by qid; checks the design facts PLAN 7.1 states."""
    rows = sorted(_read_jsonl(path), key=lambda q: q["qid"])
    ids = set(corpus_ids)
    per = {c: [q for q in rows if q["qclass"] == c] for c in CLASSES}
    if {q["qclass"] for q in rows} != set(CLASSES) or any(len(v) != N_PER_CLASS for v in per.values()):
        raise DataError(f"queries per class {({c: len(v) for c, v in per.items()})}, PLAN 7.1 needs 40 each")
    if len({q["qid"] for q in rows}) != len(rows):
        raise DataError("duplicate qid")
    golds = [q["gold_memory_id"] for q in rows]
    if len(set(golds)) != len(golds):
        raise DataError("the 120 gold memories are not distinct")
    missing = [g for g in golds if g not in ids]
    if missing:
        raise DataError(f"gold memories not in the corpus: {missing[:5]}")
    for q in rows:
        if not isinstance(q["text"], str) or not q["text"].strip():
            raise DataError(f"{q['qid']}: empty query text")
    nc = {c: sum(1 for q in per[c] if q["near_collision"]) for c in CLASSES}
    if nc["A"] != NEAR_COLLISION_A or nc["C"] != NEAR_COLLISION_C:
        raise DataError(f"near-collision golds A={nc['A']} C={nc['C']}, PLAN 7.1 says 15 and 15")
    return rows



def check_loaded(store_id: str, strategy: str, rb: Mapping, mismatched_ids: Sequence) -> list[str]:
    """Preconditions for searching (step 4 read-back): every frozen memory is stored once, valid and searchable.
    rb is py/40_retrieval_bakeoff.py's read-back of one store. Every store: each valid memory has a chunk and each
    memory chunk belongs to a valid memory (rb["coverage"]). VECTOR: every chunk row has its EMBEDDING. HYBRID: the
    store's chunk table has no EMBEDDING column; every memory chunk must have a row with an embedding in the hybrid
    index's <index>$VECTORS view (rb["hybrid_vectors"], lib.c1db) and the Text queue must be empty. KEYWORD: an Oracle
    Text index exists and its queue is empty. Returns the failures (empty list = searchable)."""
    bad = []
    m = rb["memories"]
    if mismatched_ids:
        bad.append(f"{store_id}: add_memory returned ids other than ours: {list(mismatched_ids)[:5]}")
    if (m["rows"], m["distinct_ids"], m["valid"], m["other_users"]) != (N_MEMORIES, N_MEMORIES, N_MEMORIES, 0):
        bad.append(f"{store_id}: memories {m}, expected {N_MEMORIES} valid of {USER_ID} and nothing else")
    if rb["chunks"]["distinct_sources"] != N_MEMORIES:
        bad.append(f"{store_id}: chunks cover {rb['chunks']['distinct_sources']} memories, expected {N_MEMORIES}")
    cov = rb.get("coverage")
    if not cov or cov.get("memories_without_chunk") != 0 or cov.get("chunks_without_memory") != 0:
        bad.append(f"{store_id}: memories and chunks do not match one to one: {cov}")
    if strategy == "VECTOR" and rb["chunks"]["with_embedding"] != rb["chunks"]["rows"]:
        bad.append(f"{store_id}: {rb['chunks']['with_embedding']} of {rb['chunks']['rows']} chunks have an embedding")
    if strategy == "HYBRID":
        hv = rb.get("hybrid_vectors")
        if not hv or hv.get("error"):
            bad.append(f"{store_id}: the hybrid index's vectors could not be checked: "
                       f"{hv.get('error') if hv else 'no read-back of the <index>$VECTORS view'}")
        elif hv.get("chunks") != rb["chunks"]["rows"] or hv.get("chunks_without_vector") != 0:
            bad.append(f"{store_id}: {hv.get('chunks_without_vector')} of {hv.get('chunks')} chunks have no "
                       f"embedding in {hv.get('view')} (chunk rows {rb['chunks']['rows']})")
    broken = [i["index_name"] for i in rb["chunk_indexes"]
              if i["status"] in ("UNUSABLE", "INVALID") or i["domidx_opstatus"] == "FAILED"]
    if broken:
        bad.append(f"{store_id}: chunk index(es) not usable: {broken}")
    if strategy == "KEYWORD" and not rb.get("text_indexes"):
        bad.append(f"{store_id}: no Oracle Text index found")     # HYBRID: recorded only (its view rows may differ)
    if strategy in ("HYBRID", "KEYWORD") and rb.get("text_pending_rows") != 0:
        bad.append(f"{store_id}: {rb.get('text_pending_rows')} rows still pending in the Text index")
    return bad

def arm_order(index: int) -> tuple[str, str, str]:
    """PLAN 7.0 interleaving: the strategy order rotates by one position from one query to the next (query i, in qid
    order, starts with STRATEGIES[i % 3]), so no strategy always runs first or last."""
    if index < 0:
        raise ValueError("index must be >= 0")
    k = index % len(STRATEGIES)
    return tuple(STRATEGIES[k:] + STRATEGIES[:k])     # type: ignore[return-value]


def score(ranked_ids: Sequence[str], gold_id: str) -> dict:
    """One result list against its gold with the frozen measures (an empty list is a miss)."""
    ranked = list(ranked_ids)
    if len(ranked) > MAX_RESULTS:
        raise ValueError(f"{len(ranked)} results for max_results={MAX_RESULTS}")
    return {
        "n_results": len(ranked),
        "zero_results": len(ranked) == 0,
        "hit_at_1": stats.hit_at_k(ranked, gold_id, 1),
        "hit_at_5": stats.hit_at_k(ranked, gold_id, 5),
        "rr_at_10": stats.reciprocal_rank_at_k(ranked, gold_id, 10),
        "gold_rank": ranked.index(gold_id) + 1 if gold_id in ranked else None,
    }


def _check_records(records: Sequence[Mapping], queries: Sequence[Mapping]) -> dict:
    qmap = {q["qid"]: q for q in queries}
    seen: dict[tuple[str, str], Mapping] = {}
    for r in records:
        key = (r["strategy"], r["qid"])
        if r["strategy"] not in STRATEGIES or r["qid"] not in qmap:
            raise ValueError(f"unknown record {key}")
        if key in seen:
            raise ValueError(f"duplicate record {key}")
        seen[key] = r
    want = {(s, q) for s in STRATEGIES for q in qmap}
    if set(seen) != want:
        raise ValueError(f"records cover {len(seen)} of {len(want)} (strategy, query) pairs")
    return seen


def confirmatory(records: Sequence[Mapping], queries: Sequence[Mapping]) -> list[dict]:
    """The nine confirmatory tests of PLAN 7.1 through stats.c1_family (exact McNemar, Holm over nine), each with the
    sentence the post must use."""
    seen = _check_records(records, queries)
    hit1 = {s: {q["qid"]: bool(seen[(s, q["qid"])]["hit_at_1"]) for q in queries} for s in STRATEGIES}
    qclass = {q["qid"]: q["qclass"] for q in queries}
    out = []
    for t in stats.c1_family(hit1, qclass):
        if t.verdict != "no difference detected":
            post = f"{t.verdict.split()[0]} better on class {t.query_class} (Holm-adjusted p {t.p_holm:.4f})"
        elif t.b == t.c:
            post = (f"no difference we could detect with 40 queries (b={t.b}, c={t.c}, adjusted p "
                    f"{t.p_holm:.4f}); b = c")
        else:
            post = (f"no difference we could detect with 40 queries (b={t.b}, c={t.c}, adjusted p "
                    f"{t.p_holm:.4f}); a split this size is not detectable with this design, not a trend")
        out.append({"query_class": t.query_class, "x": t.x, "y": t.y, "hits_x": t.hits_x, "hits_y": t.hits_y,
                    "b": t.b, "c": t.c, "n": t.n, "p": t.p, "p_holm": t.p_holm, "verdict": t.verdict,
                    "post_wording": post})
    return out


def summary(records: Sequence[Mapping], queries: Sequence[Mapping]) -> list[dict]:
    """Exploratory and confirmatory per-strategy measures, per class and over all 120 (slice 'ALL')."""
    seen = _check_records(records, queries)
    rows = []
    for s in STRATEGIES:
        for sl in CLASSES + ("ALL",):
            qs = [q for q in queries if sl == "ALL" or q["qclass"] == sl]
            recs = [seen[(s, q["qid"])] for q in qs]
            lat = [float(r["latency_ms"]) for r in recs]
            n = len(recs)
            h1 = sum(bool(r["hit_at_1"]) for r in recs)
            h5 = sum(bool(r["hit_at_5"]) for r in recs)
            rows.append({
                "strategy": s, "slice": sl, "n": n,
                "hit_at_1": h1, "hit_at_1_rate": h1 / n,
                "hit_at_5": h5, "hit_at_5_rate": h5 / n,
                "mrr_at_10": sum(float(r["rr_at_10"]) for r in recs) / n,
                "zero_results": sum(bool(r["zero_results"]) for r in recs),
                "latency_ms_median": stats.percentile_linear(lat, 50),
                "latency_ms_p90": stats.percentile_linear(lat, 90),
                "kind": {"hit_at_1": "confirmatory (per class only)", "others": "exploratory"},
            })
    return rows


def crosscheck(summary_rows: Sequence[Mapping], tests: Sequence[Mapping]) -> None:
    """Every per-class Hit@1 count of summary() must equal the count stats.c1_family made for the same strategy and
    class (hits_x / hits_y of the confirmatory tests); raises ValueError on any difference."""
    frozen: dict[tuple[str, str], int] = {}
    for t in tests:
        for s, h in ((t["x"], t["hits_x"]), (t["y"], t["hits_y"])):
            if frozen.setdefault((s, t["query_class"]), h) != h:
                raise ValueError(f"c1_family counted two Hit@1 values for {s} on {t['query_class']}")
    ours = {(r["strategy"], r["slice"]): r["hit_at_1"] for r in summary_rows if r["slice"] in CLASSES}
    if ours != frozen:
        raise ValueError(f"summary Hit@1 differs from c1_family's counts: {sorted(set(ours.items()) ^ set(frozen.items()))}")


def predictions(records: Sequence[Mapping], queries: Sequence[Mapping]) -> dict:
    """P1 and P2 of PLAN 7.1 (predictions from the code, reported confirmed or refuted; not tests)."""
    seen = _check_records(records, queries)
    zb = sum(1 for q in queries if q["qclass"] == "B" and seen[("KEYWORD", q["qid"])]["zero_results"])
    zc = sum(1 for q in queries if q["qclass"] == "C" and seen[("KEYWORD", q["qid"])]["zero_results"])
    return {
        "P1": {"statement": "KEYWORD returns no result for at least 36 of the 40 class B queries",
               "keyword_zero_result_B": zb, "threshold": P1_MIN_ZERO_B, "confirmed": zb >= P1_MIN_ZERO_B},
        "P2": {"statement": "KEYWORD returns no result for at least 30 of the 40 class C queries",
               "keyword_zero_result_C": zc, "threshold": P2_MIN_ZERO_C, "confirmed": zc >= P2_MIN_ZERO_C},
    }


def near_collision(records: Sequence[Mapping], queries: Sequence[Mapping]) -> list[dict]:
    """Exploratory: on the 15 + 15 near-collision queries, how often rank 1 was the gold or its sibling."""
    seen = _check_records(records, queries)
    out = []
    for s in STRATEGIES:
        for c in ("A", "C"):
            qs = [q for q in queries if q["qclass"] == c and q["near_collision"]]
            first = [(seen[(s, q["qid"])]["ranked_ids"] or [None])[0] for q in qs]
            out.append({"strategy": s, "query_class": c, "n": len(qs),
                        "rank1_gold": sum(1 for q, f in zip(qs, first) if f == q["gold_memory_id"]),
                        "rank1_sibling": sum(1 for q, f in zip(qs, first) if f == q["sibling_memory_id"]),
                        "no_result": sum(1 for f in first if f is None)})
    return out


def wording(tests: Sequence[Mapping]) -> dict:
    """PLAN 7.1's wording rule applied to the confirmatory verdicts."""
    vec_over_hyb = [t["query_class"] for t in tests
                    if {t["x"], t["y"]} == {"HYBRID", "VECTOR"} and t["verdict"] == "VECTOR better"]
    better = [f"{t['verdict'].split()[0]} over {t['y'] if t['verdict'].split()[0] == t['x'] else t['x']} "
              f"on class {t['query_class']}" for t in tests if t["verdict"] != "no difference detected"]
    return {
        "confirmed_better_statements": better,
        "headline_vector_beats_hybrid": bool(vec_over_hyb),
        "vector_beats_hybrid_classes": vec_over_hyb,
        "rules": ["'Exact identifiers' claims come from class A only; class C is 'questions that contain an identifier'.",
                  "If VECTOR beats HYBRID anywhere, that is the headline of this section.",
                  "The comparison is of the three strategies as Agent Memory 26.8.0 implements them, not a verdict "
                  "on Oracle Text or hybrid indexes in general.",
                  "Only these nine tests can support the word 'better'; Hit@5, MRR@10, zero results and latency "
                  "are exploratory."],
    }


def result_rows(summary_rows: Sequence[Mapping], tests: Sequence[Mapping]) -> list[dict]:
    """Flat (comparison, arm, slice, metric, value, n) rows for py/lib/results.py and page 4 (PLAN 8.1 contract:
    C1 'hit_at_1', arm VECTOR/HYBRID/KEYWORD, slice A/B/C); the test rows carry b, c, p and the Holm p."""
    rows = []
    for r in summary_rows:
        for metric in ("hit_at_1", "hit_at_5", "mrr_at_10", "zero_results", "latency_ms_median", "latency_ms_p90"):
            rows.append({"comparison": "C1", "arm": r["strategy"], "slice": r["slice"], "metric": metric,
                         "value": r[metric], "n": r["n"]})
    for t in tests:
        arm = f"{t['x']}_vs_{t['y']}"
        for metric in ("b", "c", "p", "p_holm"):
            rows.append({"comparison": "C1", "arm": arm, "slice": t["query_class"], "metric": metric,
                         "value": t[metric], "n": t["n"]})
    keys = [(r["comparison"], r["arm"], r["slice"], r["metric"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate result row key")
    return rows
