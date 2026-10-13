# v1.0 2026-10-12  Brief 12 PLAN 10.5 and 7.0 (steps 5 and 7): call-level retries for the SDK's own LLM calls. A
#                  failed litellm completion is retried at most twice, after 5 s and 20 s; a call that still fails
#                  is a "failed call after the retries" for 7.0's void rule. Counts only, never a prompt or response.
#                  v1.0: first version (phase 2, before step 5's first run).
"""Why at the litellm entry point and not around the SDK method.

Steps 5 and 7 never call the LLM themselves: the SDK does, inside thread.add_messages (INLINE extraction and link
resolution) and inside client.search (the pruner). Repeating those SDK methods would repeat work that is not a call
(add_messages stores the raw messages first, so a second add_messages would duplicate them, and a repeated search
would repeat every pruner call of the query). The one place where "a failed call" can be retried as such is the
litellm entry point the SDK's Llm uses (litellm.acompletion, looked up at call time; litellm.completion for the sync
path). install() wraps both, ON TOP of meter.LitellmCounter's wrappers, so every attempt still passes the budget
pre-check and is counted there; the SDK's Llm, its concurrency semaphore (max_concurrent_requests=2) and its events
see one logical call.

Rules: an attempt that raises an Exception (a provider error, a timeout of ATTEMPT_TIMEOUT_S on the async path) is
retried after RETRY_DELAYS[0], then RETRY_DELAYS[1]; meter.BudgetExceeded is never retried (the budget refusal is
final); BaseException that is not an Exception (cancellation, KeyboardInterrupt) passes through unchanged. After the
last attempt the exception is re-raised to the SDK unchanged. Stats are integers and exception class names only.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter

RETRY_DELAYS = (5.0, 20.0)          # PLAN 10.5: at most two retries, after 5 s and 20 s
ATTEMPT_TIMEOUT_S = 180.0           # async path only: a hung request counts as a failed attempt (then retried)


class RetryStats:
    """Thread-safe integer counters for logical calls and attempts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0                       # logical calls (what the SDK asked for)
        self.attempts = 0
        self.retried_calls = 0               # logical calls that needed at least one retry
        self.recovered_calls = 0             # ... and then succeeded
        self.failed_after_retries = 0        # logical calls that still failed (7.0's failed calls)
        self.not_retried = 0                 # budget refusals passed through without a retry
        self.timeouts = 0
        self.attempt_errors: Counter[str] = Counter()
        self.final_errors: Counter[str] = Counter()

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "attempts": self.attempts, "retried_calls": self.retried_calls,
                    "recovered_calls": self.recovered_calls, "failed_after_retries": self.failed_after_retries,
                    "not_retried": self.not_retried, "timeouts": self.timeouts,
                    "attempt_errors": dict(self.attempt_errors), "final_errors": dict(self.final_errors)}

    # internal ------------------------------------------------------------------------------------------------
    def _start(self) -> None:
        with self._lock:
            self.calls += 1

    def _attempt(self) -> None:
        with self._lock:
            self.attempts += 1

    def _error(self, exc: BaseException, *, timeout: bool = False) -> None:
        with self._lock:
            self.attempt_errors[type(exc).__name__] += 1
            if timeout:
                self.timeouts += 1

    def _end(self, *, retried: bool, ok: bool, exc: BaseException | None = None, passed: bool = False) -> None:
        with self._lock:
            if retried:
                self.retried_calls += 1
            if ok and retried:
                self.recovered_calls += 1
            if not ok and passed:
                self.not_retried += 1
            elif not ok:
                self.failed_after_retries += 1
                self.final_errors[type(exc).__name__ if exc is not None else "unknown"] += 1


def _no_retry(exc: BaseException, no_retry: tuple) -> bool:
    return isinstance(exc, no_retry) or not isinstance(exc, Exception)


def wrap_sync(fn, stats: RetryStats, *, delays=RETRY_DELAYS, no_retry: tuple = (), sleep=time.sleep):
    def wrapper(*a, **k):
        stats._start()
        retried = False
        for i in range(len(delays) + 1):
            stats._attempt()
            try:
                r = fn(*a, **k)
            except BaseException as exc:
                stats._error(exc)
                if _no_retry(exc, no_retry):
                    stats._end(retried=retried, ok=False, exc=exc, passed=True)
                    raise
                if i == len(delays):
                    stats._end(retried=retried, ok=False, exc=exc)
                    raise
                retried = True
                sleep(delays[i])
                continue
            stats._end(retried=retried, ok=True)
            return r
        raise RuntimeError("unreachable")          # the loop always returns or raises
    wrapper.__wrapped_by_llmretry__ = True
    return wrapper


def wrap_async(fn, stats: RetryStats, *, delays=RETRY_DELAYS, no_retry: tuple = (),
               timeout: float | None = ATTEMPT_TIMEOUT_S, asleep=asyncio.sleep):
    async def wrapper(*a, **k):
        stats._start()
        retried = False
        for i in range(len(delays) + 1):
            stats._attempt()
            try:
                if timeout is None:
                    r = await fn(*a, **k)
                else:
                    r = await asyncio.wait_for(fn(*a, **k), timeout)
            except BaseException as exc:
                is_timeout = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                stats._error(exc, timeout=is_timeout)
                if _no_retry(exc, no_retry):
                    stats._end(retried=retried, ok=False, exc=exc, passed=True)
                    raise
                if i == len(delays):
                    stats._end(retried=retried, ok=False, exc=exc)
                    raise
                retried = True
                await asleep(delays[i])
                continue
            stats._end(retried=retried, ok=True)
            return r
        raise RuntimeError("unreachable")
    wrapper.__wrapped_by_llmretry__ = True
    return wrapper


def install(stats: RetryStats, *, no_retry: tuple = ()) -> None:
    """Wrap litellm.completion and litellm.acompletion (after meter.LitellmCounter.install(), so every attempt is
    counted and budget-checked there). Idempotent: an entry point already wrapped here is left alone."""
    import litellm
    if not getattr(litellm.completion, "__wrapped_by_llmretry__", False):
        litellm.completion = wrap_sync(litellm.completion, stats, no_retry=no_retry)
    if not getattr(litellm.acompletion, "__wrapped_by_llmretry__", False):
        litellm.acompletion = wrap_async(litellm.acompletion, stats, no_retry=no_retry)


def failed_fraction(failed: int, calls: int) -> float:
    """Share of logical calls that failed after the retries (0.0 when no call was made)."""
    if failed < 0 or calls < 0:
        raise ValueError("counts must be non-negative")
    if failed > calls:
        raise ValueError("more failed calls than calls")
    return failed / calls if calls else 0.0


VOID_FAILED_SHARE = 0.05            # PLAN 7.0: a run is void if MORE than 5% of its LLM calls fail after the retries


def void_by_failures(failed: int, calls: int) -> bool:
    return failed_fraction(failed, calls) > VOID_FAILED_SHARE
