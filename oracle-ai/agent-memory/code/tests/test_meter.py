# v1.1 2026-10-10  Brief 12 PLAN 10.3 / D5: the allow-listed listener keeps integers and names only; the litellm
#                  counter counts calls and usage; the ledger stops at the budget. v1.1: pre-dispatch budget refusal and
#                  failed-call debit tests. v1.0: first version (day 1).
import gc
import os
import sys
import weakref
from pathlib import Path

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
from lib import meter  # noqa: E402

SECRET = "ocid1.user.oc1..fakefakefake"


class LlmGenerationCompletedEvent:            # stand-in with the SDK class name; carries a secret in its repr
    def __init__(self, success=True, total=30):
        self.model = "oci/xai.grok-4.20-non-reasoning"
        self.success = success
        self.error_type = None if success else "OCIError"
        self.input_tokens, self.output_tokens, self.thinking_tokens, self.total_tokens = 20, 10, 0, total
        self.llm_config = {"oci_user": SECRET}

    def __repr__(self):
        return f"Event({self.llm_config})"


class EmbedderStartedEvent:
    concrete_class = "OracleDBEmbedder"
    input_count = 3


def test_listener_keeps_only_allow_listed_values():
    m = meter.Meter()
    ev = LlmGenerationCompletedEvent()
    ref = weakref.ref(ev)
    m.on_event(ev)
    m.on_event(LlmGenerationCompletedEvent(success=False, total=0))
    m.on_event(EmbedderStartedEvent())
    del ev
    gc.collect()
    assert ref() is None                       # no event object is retained
    s = m.summary()
    assert s["llm_calls"] == 2 and s["llm_failures"] == 1
    assert s["tokens"]["total"] == 60           # 30 + (20 + 10) when total is 0
    assert s["embedder_events_by_class"] == {"EmbedderStartedEvent:OracleDBEmbedder": 1}
    assert SECRET not in repr(s)


def test_litellm_counter_counts_calls_and_usage(monkeypatch):
    import litellm

    class Resp:
        usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}

    monkeypatch.setattr(litellm, "completion", lambda *a, **k: Resp())
    monkeypatch.setattr(litellm, "embedding", lambda *a, **k: Resp())
    c = meter.LitellmCounter()
    c.install()
    litellm.completion(model="x", messages=[])
    litellm.completion(model="x", messages=[])
    s = c.summary()
    assert s["calls"] == {"completion": 2} and s["tokens"]["total"] == 14


def test_litellm_counter_counts_errors(monkeypatch):
    import litellm

    def boom(*a, **k):
        raise TimeoutError("x")

    monkeypatch.setattr(litellm, "completion", boom)
    c = meter.LitellmCounter()
    c.install()
    with pytest.raises(TimeoutError):
        litellm.completion(model="x", messages=[])
    assert c.summary()["errors"] == {"completion:TimeoutError": 1}


def test_ledger_commits_and_stops_at_budget(tmp_path):
    led = meter.Ledger(tmp_path / "ledger.json", budget=100)
    led.commit("s0", 60, 2)
    led2 = meter.Ledger(tmp_path / "ledger.json", budget=100)
    assert led2.total == 60
    led2.check(30)
    with pytest.raises(meter.BudgetExceeded):
        led2.check(40)


def test_counter_refuses_before_dispatch_near_budget(monkeypatch, tmp_path):
    import litellm
    sent = []
    monkeypatch.setattr(litellm, "completion", lambda *a, **k: sent.append(1))
    led = meter.Ledger(tmp_path / "ledger.json", budget=meter.RESERVE_PER_CALL + 1000)
    led.commit("s0", 1000, 1)                  # 1000 + 0 + reserve >= budget: the next call is refused
    c = meter.LitellmCounter(meter=meter.Meter(), ledger=led)
    c.install()
    with pytest.raises(meter.BudgetExceeded):
        litellm.completion(model="x", messages=[])
    assert sent == [] and c.summary()["blocked_by_budget"] == 1


def test_failed_call_is_debited(monkeypatch):
    import litellm

    def boom(*a, **k):
        raise ConnectionError("x")

    monkeypatch.setattr(litellm, "completion", boom)
    m = meter.Meter()
    c = meter.LitellmCounter(meter=m)
    c.install()
    with pytest.raises(ConnectionError):
        litellm.completion(model="x", messages=[])
    tok, calls = meter.step_tokens(m, c)
    assert tok == meter.FAILED_CALL_ESTIMATE and calls == 1
