# v1.1 2026-10-10  Brief 14 tests: the call counter and cap (b14lib/budget.py).
#   v1.1: per-call bound hold, model marked unbounded, halt after an overrun (Codex code review r1, finding 5).
#   v1.0: first version.
import json
import stat

import pytest

from b14lib import budget
from b14lib.budget import BudgetExceeded, Ledger

GROK = "xai.grok-4.3"
PRO = "google.gemini-2.5-pro"


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "state" / "budget.json")


def test_caps_add_up_and_prices_have_no_openai():
    assert sum(c for c, _ in budget.STEP_CAPS.values()) == 200
    assert round(sum(u for _, u in budget.STEP_CAPS.values()), 6) == 2.0
    assert not any(m.startswith("openai.") for m in budget.PRICES)


def test_every_attempt_is_counted_and_the_step_call_cap_holds(ledger):
    for _ in range(10):
        t = ledger.reserve("s3_smoke", GROK, kind="direct")
        ledger.settle(t, http_status=200, prompt_tokens=20, completion_tokens=5)
    with pytest.raises(BudgetExceeded, match="call cap 10"):
        ledger.reserve("s3_smoke", GROK, kind="direct")
    assert ledger.summary()["buckets"]["s3_smoke"]["calls"] == 10


def test_usd_hold_refuses_before_the_cap_is_passed(ledger):
    hold = budget.cost_usd(PRO, budget.IN_BOUND, 2 * budget.MAX_TOKENS)     # 0.02298
    n = int(0.20 // hold)                                                  # 8 open holds fit in s3_smoke's 0.20
    for _ in range(n):
        ledger.reserve("s3_smoke", PRO, kind="direct")
    with pytest.raises(BudgetExceeded, match="USD cap 0.20"):
        ledger.reserve("s3_smoke", PRO, kind="direct")


def test_settle_rules(ledger):
    t = ledger.reserve("s5_routing", GROK, kind="profile")
    usd = ledger.settle(t, http_status=200, prompt_tokens=100, completion_tokens=50, reasoning_tokens=200)
    assert usd == pytest.approx(budget.cost_usd(GROK, 100, 250))      # reasoning added to output
    for status in (400, 401, 403, 404, 429):
        t = ledger.reserve("s5_routing", GROK, kind="profile")
        assert ledger.settle(t, http_status=status) == 0.0
    t = ledger.reserve("s5_routing", GROK, kind="profile")
    hold = budget.cost_usd(GROK, budget.IN_BOUND, 2 * budget.MAX_TOKENS)
    assert ledger.settle(t, http_status=500) == pytest.approx(hold)
    t = ledger.reserve("s5_routing", GROK, kind="profile")
    assert ledger.settle(t, http_status=None) == pytest.approx(hold)
    t = ledger.reserve("s5_routing", GROK, kind="profile")
    assert ledger.settle(t, http_status=200) == pytest.approx(hold)                # 200 without usage
    with pytest.raises(BudgetExceeded):
        ledger.settle(t, http_status=200)                                # settled twice


def test_unpriced_model_and_unknown_bucket_are_refused(ledger):
    with pytest.raises(BudgetExceeded, match="no price"):
        ledger.reserve("s3_smoke", "openai.gpt-5", kind="direct")
    with pytest.raises(BudgetExceeded, match="unknown budget bucket"):
        ledger.reserve("s99", GROK, kind="direct")


def test_reserve_bucket_needs_a_reason(ledger):
    with pytest.raises(BudgetExceeded, match="reason"):
        ledger.reserve("s5_routing", GROK, kind="direct", use_reserve="  ")
    t = ledger.reserve("s5_routing", GROK, kind="direct", use_reserve="429 retry")
    st = json.loads(ledger.path.read_text())
    assert st["calls"][-1]["bucket"] == "reserve" and st["calls"][-1]["reason"] == "429 retry" and t == 1


def test_a_call_billed_above_the_bound_stops_that_model(ledger):
    t = ledger.reserve("s5_routing", PRO, kind="direct")
    ledger.settle(t, http_status=200, prompt_tokens=10, completion_tokens=900, reasoning_tokens=1500)  # 2,400 > 2,048
    with pytest.raises(BudgetExceeded, match="not called again"):
        ledger.reserve("s5_routing", PRO, kind="direct")
    ledger.reserve("s5_routing", GROK, kind="direct")                    # other models go on
    assert ledger.summary()["unbounded_models"] == [PRO]


def test_within_the_bound_is_fine(ledger):
    t = ledger.reserve("s5_routing", PRO, kind="direct")
    ledger.settle(t, http_status=200, prompt_tokens=30, completion_tokens=1024, reasoning_tokens=1000)
    ledger.reserve("s5_routing", PRO, kind="direct")


def test_an_overrun_halts_everything(ledger):
    t = ledger.reserve("s5_routing", PRO, kind="direct")
    usd = ledger.settle(t, http_status=200, prompt_tokens=10, completion_tokens=300_000)   # USD 3: a broken service
    assert usd > budget.CAP_USD and ledger.summary()["halted"]
    for step, model in (("s5_routing", GROK), ("s9_responses", "google.gemini-2.5-flash")):
        with pytest.raises(BudgetExceeded, match="halted"):
            ledger.reserve(step, model, kind="direct")
    with pytest.raises(BudgetExceeded, match="halted"):
        ledger.allot_db("s6_plsql", 1, 0.01)


def test_a_database_overrun_halts_everything(ledger):
    n = ledger.allot_db("s7_select_ai", 3, 0.05)
    ledger.settle_db(n, 3, 0.07)
    with pytest.raises(BudgetExceeded, match="halted"):
        ledger.reserve("s5_routing", GROK, kind="direct")


def test_total_cap_across_buckets(ledger, monkeypatch):
    monkeypatch.setattr(budget, "CAP_CALLS", 3)
    for step in ("s3_smoke", "s5_routing", "s6_plsql"):
        ledger.reserve(step, GROK, kind="direct") if step != "s6_plsql" else ledger.allot_db(step, 1, 0.01)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("s9_responses", GROK, kind="direct")


def test_db_allotment_round_trip(ledger):
    n = ledger.allot_db("s6_plsql", 16, 0.15)
    assert ledger.summary()["buckets"]["s6_plsql"]["calls"] == 16          # the whole block counts while open
    with pytest.raises(BudgetExceeded):
        ledger.allot_db("s6_plsql", 1, 0.01)                              # the bucket is full
    with pytest.raises(BudgetExceeded, match="more calls than allotted"):
        ledger.settle_db(n, 17, 0.1)
    ledger.settle_db(n, 12, 0.05)
    assert ledger.summary()["buckets"]["s6_plsql"]["calls"] == 12
    with pytest.raises(BudgetExceeded):
        ledger.settle_db(n, 12, 0.05)
    with pytest.raises(BudgetExceeded):
        ledger.allot_db("s6_plsql", 0, 0.1)


def test_file_is_600_and_a_refusal_leaves_it_unchanged(ledger):
    ledger.reserve("s3_smoke", GROK, kind="direct")
    assert stat.S_IMODE(ledger.path.stat().st_mode) == 0o600
    before = ledger.path.read_text()
    with pytest.raises(BudgetExceeded):
        ledger.reserve("s3_smoke", "openai.gpt-5", kind="direct")
    assert ledger.path.read_text() == before


def test_a_ledger_with_other_caps_is_refused(ledger):
    ledger.reserve("s3_smoke", GROK, kind="direct")
    st = json.loads(ledger.path.read_text())
    st["cap_usd"] = 50
    ledger.path.write_text(json.dumps(st))
    with pytest.raises(BudgetExceeded, match="caps differ"):
        ledger.reserve("s3_smoke", GROK, kind="direct")
