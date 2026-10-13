# v1.0 2026-10-12  Brief 12 tests for py/lib/c2run.py (step 5, PLAN 7.2): frozen-data validation, the run schedule,
#                  the resolved-config check, scoring an export with the frozen matcher (a perfect, an empty and a
#                  harmful run), the void rules, the confirmatory test and the costs; happy and failure paths. No
#                  database.
#                  v1.0: first version (phase 2).
import json
from pathlib import Path

import pytest

from lib import c2run

FROZEN = Path(__file__).resolve().parents[1] / "data" / "frozen"


@pytest.fixture(scope="module")
def data():
    return c2run.load(FROZEN)


@pytest.fixture(scope="module")
def slots_rows():
    return [json.loads(x) for x in (FROZEN / "c2_slots.jsonl").read_text(encoding="utf-8").splitlines()]


def export_perfect(data, slots_rows, drop_links=False, extra_links=()):
    """A synthetic export: one memory per slot holding the slot's own statement, the gold links between them."""
    rows = []
    mid = {}
    for s in slots_rows:
        mid[s["slot_id"]] = f"m_{s['slot_id']}"
        rows.append({"table": "MEMORY", "record_id": mid[s["slot_id"]], "user_id": s["user_id"],
                     "thread_id": s["thread_id"], "content": s["statement"]})
    links = [] if drop_links else [(g.source_slot, g.target_slot, g.relation_type, v["user_id"])
                                    for v in data["by_thread"].values() for g in v["gold"]]
    for i, (a, b, t, u) in enumerate(list(links) + list(extra_links)):
        rows.append({"table": "MEMORY_LINK", "relation_id": f"r{i}", "source_memory_id": mid[a],
                     "source_memory_user_id": u, "target_memory_id": mid[b], "target_memory_user_id": u,
                     "relation_type": t})
    return rows


def test_load_frozen_set(data):
    assert len(data["threads"]) == 12
    assert sum(len(v["gold"]) for v in data["by_thread"].values()) == 36
    assert sum(len(v["distractors"]) for v in data["by_thread"].values()) == 9
    assert [len(c2run.exchanges(t["messages"])) for t in data["threads"]] == [5] * 12


def test_load_refuses_a_changed_set(tmp_path):
    for name in ("c2_threads.jsonl", "c2_slots.jsonl", "c2_links.jsonl", "c2_distractors.jsonl"):
        (tmp_path / name).write_text((FROZEN / name).read_text(encoding="utf-8"), encoding="utf-8")
    lines = (tmp_path / "c2_links.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "c2_links.jsonl").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(c2run.DataError):
        c2run.load(tmp_path)


def test_schedule_is_plan_order():
    assert c2run.schedule() == [(1, "POST_EXTRACTION"), (1, "DURING_EXTRACTION"), (1, "DISABLED"),
                                (2, "DURING_EXTRACTION"), (2, "DISABLED"), (2, "POST_EXTRACTION"),
                                (3, "DISABLED"), (3, "POST_EXTRACTION"), (3, "DURING_EXTRACTION")]


def test_resolved_config_check():
    good = {"extraction_mode": "INLINE", "memory_extraction_frequency": 2, "enable_context_summary": False,
            "memory_link_extraction_mode": "POST_EXTRACTION", "extract_memories": True,
            "memory_extraction_window": -1, "memory_extraction_token_limit": 100000,
            "memory_link_extraction_token_limit": 100000}
    assert c2run.check_resolved_config(good, "POST_EXTRACTION") == []
    bad = dict(good, extraction_mode="BACKGROUND", memory_extraction_window=4)
    probs = c2run.check_resolved_config(bad, "POST_EXTRACTION")
    assert len(probs) == 2
    assert c2run.check_resolved_config(good, "DISABLED")          # wrong mode for the store


def test_perfect_run_scores_gold_with_no_harm(data, slots_rows):
    rows = export_perfect(data, slots_rows)
    pt = c2run.score_run(rows, data)
    tot = c2run.run_totals(pt)
    assert (tot["tp"], tot["fp"], tot["fn"], tot["harmful_invalidations"], tot["distractor_links"]) == (36, 0, 0, 0, 0)
    assert tot["f1"] == 1.0 and tot["slot_coverage"] == 1.0 and tot["pair_recall"] == 1.0
    assert c2run.cross_user_links(rows) == 0


def test_empty_run_misses_everything(data, slots_rows):
    tot = c2run.run_totals(c2run.score_run(export_perfect(data, slots_rows, drop_links=True), data))
    assert (tot["tp"], tot["fp"], tot["fn"], tot["f1"]) == (0, 0, 36, 0.0)


def test_harmful_and_distractor_links_are_counted(data, slots_rows):
    t1 = data["by_thread"]["c2_t01"]
    current = [s.slot_id for s in t1["slots"] if s.end_status == "current"]
    a, b = t1["distractors"][0]
    extra = [(current[0], current[1], "supersedes", t1["user_id"]), (a, b, "supports", t1["user_id"])]
    tot = c2run.run_totals(c2run.score_run(export_perfect(data, slots_rows, extra_links=extra), data))
    assert tot["tp"] == 36 and tot["fp"] == 2
    assert tot["harmful_invalidations"] >= 1 and tot["distractor_links"] >= 1


def test_void_rules():
    links = [{"table": "MEMORY_LINK"}]
    assert c2run.void_reasons("POST_EXTRACTION", links, 0, 60, 0, True) == []
    assert c2run.void_reasons("DISABLED", links, 0, 60, 0, True)                  # a link in DISABLED
    assert c2run.void_reasons("POST_EXTRACTION", [], 4, 60, 4, True)              # 6.7% failed
    assert c2run.void_reasons("POST_EXTRACTION", [], 3, 60, 3, True) == []        # exactly 5%: counted
    assert c2run.void_reasons("POST_EXTRACTION", [], 0, 60, 0, False)             # no read-back


def _runs(data, slots_rows, harmful_mode=None):
    out = {}
    perfect = c2run.score_run(export_perfect(data, slots_rows), data)
    empty = c2run.score_run(export_perfect(data, slots_rows, drop_links=True), data)
    for rn, mode in c2run.schedule():
        pt = perfect if mode == "POST_EXTRACTION" else empty
        tot = c2run.run_totals(pt)
        if mode == harmful_mode:
            tot = dict(tot, harmful_invalidations=1)
        out[(rn, mode)] = {"per_thread": pt, "totals": tot,
                           "cost": {"llm_calls": 100 if mode != "DISABLED" else 60, "input_tokens": 1000,
                                    "output_tokens": 100, "total_tokens": 1100, "wall_seconds": 10.0,
                                    "exchange_seconds_mean": 0.2}}
    return out


def test_confirmatory_post_better_when_gate_holds(data, slots_rows):
    conf = c2run.confirmatory(_runs(data, slots_rows), data)
    assert conf["micro_f1_post"] == 1.0 and conf["micro_f1_during"] == 0.0
    assert conf["p"] == 2 / 4096
    assert conf["verdict"].startswith("POST_EXTRACTION links more accurately")


def test_confirmatory_gate_blocks_better(data, slots_rows):
    conf = c2run.confirmatory(_runs(data, slots_rows, harmful_mode="POST_EXTRACTION"), data)
    assert conf["verdict"].startswith("neither better")


def test_confirmatory_needs_three_runs(data, slots_rows):
    runs = _runs(data, slots_rows)
    del runs[(3, "POST_EXTRACTION")]
    with pytest.raises(ValueError):
        c2run.confirmatory(runs, data)


def test_costs_are_differences_of_means(data, slots_rows):
    c = c2run.costs(_runs(data, slots_rows))
    assert c["linking_cost_vs_disabled"]["POST_EXTRACTION"]["llm_calls"] == 40.0
    assert c["per_mode"]["DISABLED"]["llm_calls"]["runs"] == [60.0, 60.0, 60.0]
