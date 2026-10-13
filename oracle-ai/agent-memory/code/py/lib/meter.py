# v1.1 2026-10-10  Brief 12 PLAN 10.3: the allow-listed event listener (LLM calls and tokens, embedder events by
#                  concrete class), an independent call counter on litellm's entry points, and the POC token ledger
#                  that enforces D5's 5M-token budget.
#                  v1.1: (Codex review of step 0, findings 2 and 3) the budget is a hard pre-call ceiling: every
#                  litellm call (also the SDK's internal ones) is refused before dispatch unless ledger + this step's
#                  tokens + a reserve of RESERVE_PER_CALL stays below the budget; a failed call is debited
#                  FAILED_CALL_ESTIMATE tokens because its usage is unknown. v1.0: first version (day 1).
"""Count, never keep: no event object, prompt, response text or argument is stored, printed or logged.

Meter.on_event copies only these fields: the event class name, model, success, error_type, input/output/thinking/
total tokens, and for EmbedderStartedEvent / EmbedderCompletedEvent the concrete_class and input_count (B18, B19,
shot 29). repr() of an LLM event carries the OCI values (B19), so nothing here ever formats an event.

LitellmCounter wraps litellm.completion / acompletion / embedding / aembedding with a pass-through that counts calls
and reads only the integer usage fields of the response. It is a second, independent count: listener registration
is context-local, so an LLM call made on a thread without the context would be missed by the listener alone.
The budget uses the larger of the two token counts.

The ledger (state/token_ledger.json) holds cumulative integers per step. Before every litellm call the counter
checks ledger total + this step's tokens (the larger count, plus FAILED_CALL_ESTIMATE per failed call) +
RESERVE_PER_CALL against the budget and raises BudgetExceeded instead of dispatching. RESERVE_PER_CALL is the SDK's
own default per-request token limit (memory_extraction_token_limit 100,000), so no single call can cross the budget.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path

from oracleagentmemory.observability import EventListener


class BudgetExceeded(RuntimeError):
    pass


RESERVE_PER_CALL = 100_000      # the SDK's default extraction/linking token limit per request (API_NOTES, D7)
FAILED_CALL_ESTIMATE = 5_000    # debited per failed call (usage unknown); our prompts are far smaller


_LLM_DONE = "LlmGenerationCompletedEvent"
_LLM_START = "LlmGenerationStartedEvent"
_EMB = ("EmbedderStartedEvent", "EmbedderCompletedEvent")


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


class Meter(EventListener):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: Counter[str] = Counter()
        self.llm_calls = 0
        self.llm_failures = 0
        self.llm_error_types: Counter[str] = Counter()
        self.tokens = Counter()                 # input, output, thinking, total
        self.by_model: dict[str, Counter] = {}
        self.embedder_classes: Counter[str] = Counter()
        self.embedder_inputs = 0

    def on_event(self, event) -> None:          # SDK API name
        name = type(event).__name__
        with self._lock:
            self.events[name] += 1
            if name == _LLM_DONE:
                self.llm_calls += 1
                model = str(getattr(event, "model", "") or "")
                if getattr(event, "success", True) is False:
                    self.llm_failures += 1
                    self.llm_error_types[str(getattr(event, "error_type", "") or "unknown")] += 1
                c = Counter(input=_int(getattr(event, "input_tokens", 0)),
                            output=_int(getattr(event, "output_tokens", 0)),
                            thinking=_int(getattr(event, "thinking_tokens", 0)),
                            total=_int(getattr(event, "total_tokens", 0)))
                if c["total"] == 0:
                    c["total"] = c["input"] + c["output"] + c["thinking"]
                self.tokens.update(c)
                self.by_model.setdefault(model, Counter()).update(c + Counter(calls=1))
            elif name in _EMB:
                self.embedder_classes[f"{name}:{getattr(event, 'concrete_class', '')}"] += 1
                if name == "EmbedderStartedEvent":
                    self.embedder_inputs += _int(getattr(event, "input_count", 0))

    def summary(self) -> dict:
        with self._lock:
            return {
                "events": dict(sorted(self.events.items())),
                "llm_calls": self.llm_calls,
                "llm_failures": self.llm_failures,
                "llm_error_types": dict(self.llm_error_types),
                "tokens": dict(self.tokens),
                "by_model": {m: dict(c) for m, c in self.by_model.items()},
                "embedder_events_by_class": dict(sorted(self.embedder_classes.items())),
                "embedder_inputs": self.embedder_inputs,
            }


class LitellmCounter:
    """Pass-through wrappers on litellm's four entry points; counts and integer usage only. With a ledger, every
    call is checked against the budget before it is dispatched (hard ceiling)."""

    def __init__(self, meter: "Meter | None" = None, ledger: "Ledger | None" = None) -> None:
        self._lock = threading.Lock()
        self.calls: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.tokens = Counter()
        self.failed = 0
        self.blocked = 0
        self.meter = meter
        self.ledger = ledger
        self._orig: dict[str, object] = {}

    def step_tokens(self) -> int:
        listener = int(self.meter.summary()["tokens"].get("total", 0)) if self.meter is not None else 0
        with self._lock:
            own = int(self.tokens.get("total", 0))
            failed = self.failed
        return max(listener, own) + failed * FAILED_CALL_ESTIMATE

    def _precheck(self, name: str) -> None:
        if self.ledger is None:
            return
        if self.ledger.total + self.step_tokens() + RESERVE_PER_CALL >= self.ledger.budget:
            with self._lock:
                self.blocked += 1
            raise BudgetExceeded(f"{name} refused before dispatch: the D5 token budget would be reached")

    def _usage(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is None and isinstance(resp, dict):
            u = resp.get("usage")
        if u is None:
            return
        get = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
        with self._lock:
            self.tokens.update(Counter(input=_int(get("prompt_tokens")), output=_int(get("completion_tokens")),
                                       total=_int(get("total_tokens"))))

    def install(self) -> None:
        import litellm
        for name in ("completion", "acompletion", "embedding", "aembedding"):
            if name in self._orig:
                continue
            orig = getattr(litellm, name)
            self._orig[name] = orig
            if name.startswith("a"):
                async def wrapper(*a, _am_orig=orig, _am_name=name, **k):
                    self._precheck(_am_name)
                    with self._lock:
                        self.calls[_am_name] += 1
                    try:
                        r = await _am_orig(*a, **k)
                    except BaseException as exc:
                        with self._lock:
                            self.errors[f"{_am_name}:{type(exc).__name__}"] += 1
                            self.failed += 1
                        raise
                    self._usage(r)
                    return r
            else:
                def wrapper(*a, _am_orig=orig, _am_name=name, **k):
                    self._precheck(_am_name)
                    with self._lock:
                        self.calls[_am_name] += 1
                    try:
                        r = _am_orig(*a, **k)
                    except BaseException as exc:
                        with self._lock:
                            self.errors[f"{_am_name}:{type(exc).__name__}"] += 1
                            self.failed += 1
                        raise
                    self._usage(r)
                    return r
            setattr(litellm, name, wrapper)

    def summary(self) -> dict:
        with self._lock:
            return {"calls": dict(self.calls), "errors": dict(self.errors), "tokens": dict(self.tokens),
                    "failed_calls": self.failed, "blocked_by_budget": self.blocked,
                    "failed_call_estimate_tokens": self.failed * FAILED_CALL_ESTIMATE}


class Ledger:
    """POC-wide token ledger (integers only) under state/; one SDK process at a time (5.3), so no file locking."""

    def __init__(self, path: Path, budget: int) -> None:
        self.path = path
        self.budget = budget
        self.data = {"budget": budget, "total_tokens": 0, "llm_calls": 0, "steps": {}}
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    @property
    def total(self) -> int:
        return int(self.data["total_tokens"])

    def check(self, pending_tokens: int = 0) -> None:
        if self.total + pending_tokens >= self.budget:
            raise BudgetExceeded(f"token budget reached: {self.total + pending_tokens} of {self.budget}")

    def commit(self, step: str, tokens: int, calls: int) -> None:
        s = self.data["steps"].setdefault(step, {"tokens": 0, "llm_calls": 0, "runs": 0})
        s["tokens"] += tokens
        s["llm_calls"] += calls
        s["runs"] += 1
        self.data["total_tokens"] = self.total + tokens
        self.data["llm_calls"] = int(self.data["llm_calls"]) + calls
        self.data["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)


def step_tokens(meter: Meter, counter: LitellmCounter) -> tuple[int, int]:
    """(tokens, calls) for the ledger: the larger of the listener's and the litellm counter's token totals plus the
    failed-call estimate, and the larger call count."""
    m = meter.summary()
    c = counter.summary()
    tok = (max(int(m["tokens"].get("total", 0)), int(c["tokens"].get("total", 0)))
           + int(c["failed_call_estimate_tokens"]))
    calls = max(int(m["llm_calls"]), int(c["calls"].get("completion", 0)) + int(c["calls"].get("acompletion", 0)))
    return tok, calls


class BudgetGuard:
    """Raise BudgetExceeded unless ledger total + this step's tokens + one call's reserve stay below the budget."""

    def __init__(self, ledger: Ledger, meter: Meter, counter: LitellmCounter) -> None:
        self.ledger, self.meter, self.counter = ledger, meter, counter

    def check(self) -> None:
        tok, _ = step_tokens(self.meter, self.counter)
        self.ledger.check(tok + RESERVE_PER_CALL)
