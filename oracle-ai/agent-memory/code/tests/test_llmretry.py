# v1.0 2026-10-12  Brief 12 tests for py/lib/llmretry.py (PLAN 10.5, 7.0): at most two retries after 5 s and 20 s,
#                  budget refusals and cancellations never retried, timeouts counted, the 5% void rule; sync and
#                  async paths, happy and failure paths. No network (the wrapped function is a stub; sleeps faked).
#                  v1.0: first version (phase 2).
import asyncio

import pytest

from lib import llmretry


class Budget(RuntimeError):
    pass


def flaky(fails: int, exc=ValueError):
    state = {"n": 0}

    def fn(*a, **k):
        state["n"] += 1
        if state["n"] <= fails:
            raise exc("boom")
        return {"ok": True, "args": a, "kw": k}
    return fn, state


def test_sync_succeeds_first_time():
    st = llmretry.RetryStats()
    slept = []
    fn, state = flaky(0)
    w = llmretry.wrap_sync(fn, st, sleep=slept.append)
    assert w(1, x=2) == {"ok": True, "args": (1,), "kw": {"x": 2}}
    s = st.snapshot()
    assert (s["calls"], s["attempts"], s["retried_calls"], s["failed_after_retries"]) == (1, 1, 0, 0)
    assert slept == []


def test_sync_recovers_on_second_retry_with_5_then_20_seconds():
    st = llmretry.RetryStats()
    slept = []
    fn, state = flaky(2)
    w = llmretry.wrap_sync(fn, st, sleep=slept.append)
    assert w()["ok"]
    s = st.snapshot()
    assert slept == [5.0, 20.0]
    assert (s["calls"], s["attempts"], s["retried_calls"], s["recovered_calls"], s["failed_after_retries"]) == \
        (1, 3, 1, 1, 0)
    assert s["attempt_errors"] == {"ValueError": 2}


def test_sync_fails_after_two_retries_and_reraises():
    st = llmretry.RetryStats()
    slept = []
    fn, state = flaky(5)
    w = llmretry.wrap_sync(fn, st, sleep=slept.append)
    with pytest.raises(ValueError):
        w()
    s = st.snapshot()
    assert state["n"] == 3 and slept == [5.0, 20.0]
    assert (s["failed_after_retries"], s["final_errors"]) == (1, {"ValueError": 1})


def test_sync_budget_refusal_is_not_retried():
    st = llmretry.RetryStats()
    slept = []
    fn, state = flaky(5, exc=Budget)
    w = llmretry.wrap_sync(fn, st, no_retry=(Budget,), sleep=slept.append)
    with pytest.raises(Budget):
        w()
    s = st.snapshot()
    assert state["n"] == 1 and slept == []
    assert (s["not_retried"], s["failed_after_retries"]) == (1, 0)


def test_async_recovers_and_uses_async_sleep():
    st = llmretry.RetryStats()
    slept = []

    async def asleep(s):
        slept.append(s)

    n = {"n": 0}

    async def fn():
        n["n"] += 1
        if n["n"] == 1:
            raise ConnectionError("x")
        return "r"
    w = llmretry.wrap_async(fn, st, asleep=asleep, timeout=1.0)
    assert asyncio.run(w()) == "r"
    s = st.snapshot()
    assert slept == [5.0] and (s["attempts"], s["recovered_calls"]) == (2, 1)


def test_async_timeout_counts_and_fails_after_retries():
    st = llmretry.RetryStats()

    async def asleep(s):
        return None

    async def hang():
        await asyncio.sleep(10)
    w = llmretry.wrap_async(hang, st, asleep=asleep, timeout=0.01)
    with pytest.raises(TimeoutError):
        asyncio.run(w())
    s = st.snapshot()
    assert (s["attempts"], s["timeouts"], s["failed_after_retries"]) == (3, 3, 1)


def test_async_cancellation_passes_through_without_retry():
    st = llmretry.RetryStats()

    async def asleep(s):
        raise AssertionError("must not sleep")

    async def cancelled():
        raise asyncio.CancelledError()
    w = llmretry.wrap_async(cancelled, st, asleep=asleep, timeout=None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(w())
    assert st.snapshot()["not_retried"] == 1


def test_void_rule_is_more_than_five_percent():
    assert llmretry.void_by_failures(0, 0) is False
    assert llmretry.void_by_failures(3, 60) is False          # exactly 5%: not more than 5%
    assert llmretry.void_by_failures(4, 60) is True
    with pytest.raises(ValueError):
        llmretry.failed_fraction(2, 1)
    with pytest.raises(ValueError):
        llmretry.failed_fraction(-1, 1)
