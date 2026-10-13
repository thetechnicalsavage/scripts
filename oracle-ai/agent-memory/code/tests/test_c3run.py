# v1.1 2026-10-12  Brief 12 tests for py/lib/c3run.py (step 7, PLAN 7.3): frozen-query validation, the arm/run
#                  schedule, scoring with the frozen measures, the RV_VEC read-back check, per-query values (top-K run
#                  1, pruning means over 3 runs), determinism, decisions and wording, the void rule; happy and
#                  failure paths. No database.
#                  v1.1: check_store() identity coverage, distinct sources and TTL (Codex 34 r1, finding 3).
#                  v1.0: first version (phase 2).
from collections import Counter
from pathlib import Path

import pytest

from lib import c3run

FROZEN = Path(__file__).resolve().parents[1] / "data" / "frozen"


@pytest.fixture(scope="module")
def queries():
    return c3run.load_queries(FROZEN / "c3_queries.jsonl")


def test_load_queries(queries):
    assert len(queries) == 40
    assert Counter(len(q["gold_memory_ids"]) for q in queries) == Counter({2: 10, 3: 10, 4: 10, 5: 10})


def test_load_refuses_a_bad_set(tmp_path):
    lines = (FROZEN / "c3_queries.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "q.jsonl").write_text("\n".join(lines[:39]) + "\n", encoding="utf-8")
    with pytest.raises(c3run.DataError):
        c3run.load_queries(tmp_path / "q.jsonl")


def test_schedule_rotates_and_counts_runs():
    s = c3run.schedule()
    assert Counter(a for _, a in s) == Counter(c3run.RUNS)
    assert [a for r, a in s if r == 1] == list(c3run.ARMS)
    assert [a for r, a in s if r == 2] == ["PRUNE_EXT", "PRUNE_EXH", "PRUNE_FAST", "TOPK5", "TOPK10"]
    assert [a for r, a in s if r == 3] == ["PRUNE_EXH", "PRUNE_FAST", "PRUNE_EXT"]
    # each pruning arm takes each position once among the pruning arms over its three runs
    for arm in c3run.PRUNING_ARMS:
        pos = [[a for rr, a in s if rr == r and a in c3run.PRUNING_ARMS].index(arm) for r in (1, 2, 3)]
        assert sorted(pos) == [0, 1, 2]


def test_score_uses_sdk_token_rule():
    sc = c3run.score(["a", "b", "x"], ["1234567", "1", ""], ["a", "b", "c"])
    assert sc["estimated_tokens"] == 2 + 1 + 1          # round(7/3.5)=2, max(1, 0)=1, max(1, 0)=1
    assert sc["recall"] == pytest.approx(2 / 3) and sc["precision"] == pytest.approx(2 / 3)
    assert c3run.score([], [], ["a"])["precision"] == 0.0


def test_check_store():
    good = {"memories": {"rows": 400, "distinct_ids": 400, "valid": 400, "other_users": 0, "with_ttl": 0},
            "chunks": {"rows": 400, "distinct_sources": 400, "with_embedding": 400},
            "coverage": {"memories_without_chunk": 0, "chunks_without_memory": 0}}
    assert c3run.check_store(good) == []
    assert c3run.check_store({**good, "chunks": {"rows": 400, "distinct_sources": 400, "with_embedding": 399}})
    assert c3run.check_store({**good, "memories": {**good["memories"], "other_users": 1}})
    assert c3run.check_store({**good, "memories": {**good["memories"], "with_ttl": 2}})
    # a missing chunk hidden behind an orphan one: counts look right, identity does not
    assert c3run.check_store({**good, "coverage": {"memories_without_chunk": 1, "chunks_without_memory": 1}})
    assert c3run.check_store({k: v for k, v in good.items() if k != "coverage"})


def synthetic(queries, rule, runs_per_arm=None):
    """records for every arm and run; rule(arm, run, q) -> (returned_ids, tokens, pruner_calls)."""
    out = []
    for arm in c3run.ARMS:
        for rn in range(1, (runs_per_arm or c3run.RUNS)[arm] + 1):
            for q in queries:
                ids, tok, calls = rule(arm, rn, q)
                rec, prec = (sum(1 for i in ids if i in q["gold_memory_ids"]) / len(q["gold_memory_ids"]),
                             (sum(1 for i in ids if i in q["gold_memory_ids"]) / len(ids)) if ids else 0.0)
                out.append({"qid": q["qid"], "arm": arm, "run": rn, "returned_ids": ids, "estimated_tokens": tok,
                            "recall": rec, "precision": prec, "n_returned": len(ids), "pruner_calls": calls,
                            "pruner_total_tokens": 100 * calls, "pruner_input_tokens": 90 * calls,
                            "pruner_output_tokens": 10 * calls, "latency_ms": 5.0, "overlap_warnings": 0})
    return out


def _rule(arm, rn, q):
    g = q["gold_memory_ids"]
    if arm == "TOPK10":
        return g + [f"n{i}" for i in range(10 - len(g))], 300, 0
    if arm == "TOPK5":
        return (g + [f"n{i}" for i in range(5)])[:5], 150, 0
    return list(g), 20 * len(g), 6                      # pruning keeps exactly the gold


def test_per_query_values_and_decisions(queries):
    recs = synthetic(queries, _rule)
    tokens, recall, prec = c3run.per_query_values(recs, queries)
    assert len(tokens["PRUNE_EXH"]) == 40 and all(r == 1.0 for r in recall["PRUNE_FAST"])
    dec = c3run.decisions(tokens, recall)
    assert all(d["decision_a_smaller_without_material_loss"] for d in dec)
    w = c3run.wording(dec)
    assert w["sentence"].startswith("pruning showed a benefit")
    assert c3run.determinism(recs) == {"TOPK10": [], "TOPK5": []}
    desc = c3run.describe(recs, queries, tokens, recall, prec)
    assert next(d for d in desc if d["arm"] == "PRUNE_EXH")["per_run"][0]["queries_above_expected_calls"] == 0


def test_no_benefit_wording_and_missing_run(queries):
    recs = synthetic(queries, lambda a, rn, q: (["x"], 300, 6 if a.startswith("PRUNE") else 0))
    tokens, recall, prec = c3run.per_query_values(recs, queries)
    w = c3run.wording(c3run.decisions(tokens, recall))
    assert w["sentence"] == "pruning did not show a benefit on this set"
    short = [r for r in recs if not (r["arm"] == "PRUNE_EXT" and r["run"] == 3)]
    with pytest.raises(ValueError):
        c3run.per_query_values(short, queries)


def test_determinism_detects_a_difference(queries):
    recs = synthetic(queries, _rule)
    for r in recs:
        if r["arm"] == "TOPK5" and r["run"] == 2 and r["qid"] == "C3-07":
            r["returned_ids"] = list(reversed(r["returned_ids"]))
    assert c3run.determinism(recs)["TOPK5"] == ["C3-07"]


def test_void_reasons():
    assert c3run.void_reasons(0, 240, False) == []
    assert c3run.void_reasons(0, 0, True)
    assert c3run.void_reasons(13, 240, False)            # 5.4%
