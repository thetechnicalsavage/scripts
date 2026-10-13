# v1.2 2026-10-12  Brief 12 tests for py/lib/c1run.py (step 4, PLAN 7.1): frozen-data validation, the per-query arm
#                  rotation, scoring with the frozen measures, the confirmatory family, P1/P2, the wording rule and the
#                  page 4 rows; happy and failure paths. No database.
#                  v1.2: check_loaded() (moved from the step script): a HYBRID store is checked through its hybrid
#                  index's vectors, not a chunk-table EMBEDDING column (the 10-Oct 19:17Z false stop); VECTOR and
#                  KEYWORD failure paths; (Codex 31 r1) identity coverage: a missing chunk hidden behind an
#                  orphan one, or no coverage read-back, stops every strategy.
#                  v1.1: crosscheck() happy and failure path (Codex 26 r1).
#                  v1.0: first version (day 1, phase 2).
import json
from collections import Counter
from pathlib import Path

import pytest

from lib import c1run

FROZEN = Path(__file__).resolve().parents[1] / "data" / "frozen"


@pytest.fixture(scope="module")
def corpus():
    return c1run.load_corpus(FROZEN / "c1_corpus.jsonl")


@pytest.fixture(scope="module")
def queries(corpus):
    return c1run.load_queries(FROZEN / "c1_queries.jsonl", [m["memory_id"] for m in corpus])


def records_for(queries, hit_rule):
    """Synthetic per-query records: hit_rule(strategy, query) -> ranked id list."""
    out = []
    for q in queries:
        for s in c1run.STRATEGIES:
            ranked = hit_rule(s, q)
            rec = {"qid": q["qid"], "qclass": q["qclass"], "strategy": s, "ranked_ids": ranked, "latency_ms": 10.0}
            rec.update(c1run.score(ranked, q["gold_memory_id"]))
            out.append(rec)
    return out


def test_frozen_data_loads_with_plan_facts(corpus, queries):
    assert len(corpus) == 400 and corpus[0]["memory_id"] == "biz_m0001" and corpus[-1]["memory_id"] == "biz_m0400"
    assert Counter(q["qclass"] for q in queries) == {"A": 40, "B": 40, "C": 40}
    assert [q["qid"] for q in queries] == sorted(q["qid"] for q in queries)


def test_truncated_corpus_is_refused(tmp_path):
    lines = (FROZEN / "c1_corpus.jsonl").read_text(encoding="utf-8").splitlines()[:399]
    p = tmp_path / "c.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(c1run.DataError):
        c1run.load_corpus(p)


def test_query_with_unknown_gold_is_refused(tmp_path, corpus):
    rows = [json.loads(x) for x in (FROZEN / "c1_queries.jsonl").read_text(encoding="utf-8").splitlines()]
    rows[0]["gold_memory_id"] = "biz_m9999"
    p = tmp_path / "q.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    with pytest.raises(c1run.DataError):
        c1run.load_queries(p, [m["memory_id"] for m in corpus])


def test_arm_order_rotates_evenly():
    firsts = Counter(c1run.arm_order(i)[0] for i in range(120))
    lasts = Counter(c1run.arm_order(i)[2] for i in range(120))
    assert firsts == {"HYBRID": 40, "VECTOR": 40, "KEYWORD": 40} == lasts
    assert all(sorted(c1run.arm_order(i)) == sorted(c1run.STRATEGIES) for i in range(6))
    with pytest.raises(ValueError):
        c1run.arm_order(-1)


def test_score_hits_ranks_and_empty_list():
    s = c1run.score(["x", "y", "g"], "g")
    assert (s["hit_at_1"], s["hit_at_5"], s["rr_at_10"], s["gold_rank"]) == (False, True, pytest.approx(1 / 3), 3)
    e = c1run.score([], "g")
    assert e["zero_results"] and not e["hit_at_1"] and e["rr_at_10"] == 0.0 and e["gold_rank"] is None
    with pytest.raises(ValueError):
        c1run.score([str(i) for i in range(11)], "g")


def test_confirmatory_vector_beats_others_on_b(queries):
    def rule(s, q):
        if q["qclass"] == "B":
            return [q["gold_memory_id"]] if s == "VECTOR" else []
        return [q["gold_memory_id"]]
    tests = c1run.confirmatory(records_for(queries, rule), queries)
    assert len(tests) == 9
    hv = next(t for t in tests if t["query_class"] == "B" and (t["x"], t["y"]) == ("HYBRID", "VECTOR"))
    assert (hv["b"], hv["c"], hv["verdict"]) == (0, 40, "VECTOR better")
    a = [t for t in tests if t["query_class"] == "A"]
    assert all(t["verdict"] == "no difference detected" and "b = c" in t["post_wording"] for t in a)
    w = c1run.wording(tests)
    assert w["headline_vector_beats_hybrid"] and w["vector_beats_hybrid_classes"] == ["B"]
    p = c1run.predictions(records_for(queries, rule), queries)
    assert p["P1"]["keyword_zero_result_B"] == 40 and p["P1"]["confirmed"]
    assert p["P2"]["keyword_zero_result_C"] == 0 and not p["P2"]["confirmed"]


def test_small_lopsided_split_is_not_detectable(queries):
    a_ids = [q["qid"] for q in queries if q["qclass"] == "A"][:8]

    def rule(s, q):
        if s == "HYBRID" and q["qid"] in a_ids:
            return []
        return [q["gold_memory_id"]]
    tests = c1run.confirmatory(records_for(queries, rule), queries)
    hv = next(t for t in tests if t["query_class"] == "A" and (t["x"], t["y"]) == ("HYBRID", "VECTOR"))
    assert (hv["b"], hv["c"]) == (0, 8) and hv["verdict"] == "no difference detected"
    assert "not detectable with this design" in hv["post_wording"]


def test_missing_record_is_refused(queries):
    recs = records_for(queries, lambda s, q: [q["gold_memory_id"]])
    with pytest.raises(ValueError):
        c1run.confirmatory(recs[:-1], queries)
    with pytest.raises(ValueError):
        c1run.summary(recs + recs[:1], queries)


def test_summary_and_rows_follow_page4_contract(queries):
    recs = records_for(queries, lambda s, q: [q["gold_memory_id"]] if s != "KEYWORD" else [])
    summ = c1run.summary(recs, queries)
    assert len(summ) == 12
    kb = next(r for r in summ if r["strategy"] == "KEYWORD" and r["slice"] == "B")
    assert (kb["hit_at_1"], kb["zero_results"], kb["mrr_at_10"]) == (0, 40, 0.0)
    rows = c1run.result_rows(summ, c1run.confirmatory(recs, queries))
    keys = {(r["arm"], r["slice"]) for r in rows if r["metric"] == "hit_at_1"}
    assert {(s, c) for s in ("VECTOR", "HYBRID", "KEYWORD") for c in ("A", "B", "C")} <= keys
    assert all(len(r["arm"]) <= 32 and len(r["slice"]) <= 32 and len(r["metric"]) <= 64 for r in rows)


def test_near_collision_counts_sibling_at_rank_one(queries):
    def rule(s, q):
        if q["near_collision"] and s == "KEYWORD":
            return [q["sibling_memory_id"]]
        return [q["gold_memory_id"]]
    nc = c1run.near_collision(records_for(queries, rule), queries)
    k = {(r["strategy"], r["query_class"]): r for r in nc}
    assert k[("KEYWORD", "A")]["rank1_sibling"] == 15 and k[("KEYWORD", "C")]["rank1_gold"] == 0
    assert k[("VECTOR", "C")]["rank1_gold"] == 15


def test_crosscheck_ties_summary_to_frozen_counts(queries):
    recs = records_for(queries, lambda s, q: [q["gold_memory_id"]] if (s, q["qclass"]) != ("KEYWORD", "B") else [])
    tests = c1run.confirmatory(recs, queries)
    summ = c1run.summary(recs, queries)
    c1run.crosscheck(summ, tests)
    bad = [dict(r) for r in summ]
    next(r for r in bad if (r["strategy"], r["slice"]) == ("VECTOR", "A"))["hit_at_1"] -= 1
    with pytest.raises(ValueError):
        c1run.crosscheck(bad, tests)


def _rb(strategy, *, with_embedding=None, hybrid_vectors=None, pending=0, text=True):
    rb = {"memories": {"rows": 400, "distinct_ids": 400, "valid": 400, "other_users": 0, "with_ttl": 0},
          "chunks": {"rows": 400, "distinct_sources": 400, "with_embedding": with_embedding},
          "coverage": {"memories_without_chunk": 0, "chunks_without_memory": 0},
          "chunk_indexes": [{"index_name": "X_I", "index_type": "DOMAIN", "ityp_name": "CONTEXT_V2",
                             "status": "VALID", "domidx_opstatus": "VALID"}]}
    if strategy in ("HYBRID", "KEYWORD"):
        rb["text_indexes"] = [{"idx_name": "X_I", "sync_type": "ON COMMIT", "status": "INDEXED"}] if text else []
        rb["text_pending_rows"] = pending
    if hybrid_vectors is not None:
        rb["hybrid_vectors"] = hybrid_vectors
    return rb


HV_OK = {"view": "RV_HYB_RECORD_CHUNKS_CONTENT_HVX_I$VECTORS", "chunks": 400, "chunks_without_vector": 0,
         "vector_rows": 400, "distinct_docs": 400, "rows_with_embedding": 400}


def test_hybrid_is_checked_through_the_index_vectors_not_a_chunk_column():
    # the 10-Oct 19:17Z read-back had with_embedding None (no EMBEDDING column on a HYBRID store's chunk table)
    assert c1run.check_loaded("RV_HYB", "HYBRID", _rb("HYBRID", hybrid_vectors=HV_OK), []) == []


def test_hybrid_without_the_vectors_read_back_stops():
    bad = c1run.check_loaded("RV_HYB", "HYBRID", _rb("HYBRID"), [])
    assert len(bad) == 1 and "could not be checked" in bad[0]
    bad = c1run.check_loaded("RV_HYB", "HYBRID", _rb("HYBRID", hybrid_vectors={"error": "view X not found"}), [])
    assert len(bad) == 1 and "view X not found" in bad[0]


def test_hybrid_with_a_chunk_missing_its_vector_stops():
    hv = dict(HV_OK, chunks_without_vector=3)
    bad = c1run.check_loaded("RV_HYB", "HYBRID", _rb("HYBRID", hybrid_vectors=hv), [])
    assert len(bad) == 1 and "3 of 400" in bad[0]
    hv = dict(HV_OK, chunks=399)
    assert len(c1run.check_loaded("RV_HYB", "HYBRID", _rb("HYBRID", hybrid_vectors=hv), [])) == 1


def test_hybrid_text_queue_must_be_empty():
    bad = c1run.check_loaded("RV_HYB", "HYBRID", _rb("HYBRID", hybrid_vectors=HV_OK, pending=2), [])
    assert len(bad) == 1 and "pending" in bad[0]


def test_vector_still_needs_every_chunk_embedding():
    assert c1run.check_loaded("RV_VEC", "VECTOR", _rb("VECTOR", with_embedding=400), []) == []
    bad = c1run.check_loaded("RV_VEC", "VECTOR", _rb("VECTOR", with_embedding=399), [])
    assert len(bad) == 1 and "399 of 400" in bad[0]
    assert len(c1run.check_loaded("RV_VEC", "VECTOR", _rb("VECTOR", with_embedding=None), [])) == 1


def test_keyword_needs_a_text_index_and_an_empty_queue_and_our_ids():
    assert c1run.check_loaded("RV_KEY", "KEYWORD", _rb("KEYWORD"), []) == []
    assert len(c1run.check_loaded("RV_KEY", "KEYWORD", _rb("KEYWORD", text=False), [])) == 1
    assert len(c1run.check_loaded("RV_KEY", "KEYWORD", _rb("KEYWORD", pending=1), [])) == 1
    bad = c1run.check_loaded("RV_KEY", "KEYWORD", _rb("KEYWORD"), [["biz_m0001", "x"]])
    assert len(bad) == 1 and "ids other than ours" in bad[0]
    rb = _rb("KEYWORD")
    rb["memories"]["valid"] = 399
    assert len(c1run.check_loaded("RV_KEY", "KEYWORD", rb, [])) == 1


def test_counts_alone_do_not_pass_without_identity_coverage():
    for strategy, store, kw in (("VECTOR", "RV_VEC", {"with_embedding": 400}),
                                ("HYBRID", "RV_HYB", {"hybrid_vectors": HV_OK}), ("KEYWORD", "RV_KEY", {})):
        rb = _rb(strategy, **kw)
        rb["coverage"] = {"memories_without_chunk": 1, "chunks_without_memory": 1}   # 400 and 400 still
        bad = c1run.check_loaded(store, strategy, rb, [])
        assert len(bad) == 1 and "one to one" in bad[0]
        del rb["coverage"]
        assert len(c1run.check_loaded(store, strategy, rb, [])) == 1
