# v1.1 2026-10-10  Brief 14: the inference call counter and cap (PLAN.md v1.1 section 7).
#   v1.1: (Codex code review r1, finding 5) the hold is the per-call bound under the sent maxTokens (IN_BOUND input +
#         2 x MAX_TOKENS output tokens); settle() fails closed: a call billed above maxTokens marks its model unbounded
#         (no further call to it) and any total above the cap halts every later call.
#   v1.0: first version.
"""USD 2.00 and 200 calls in total, with per-step sub-caps, enforced before every inference attempt.

The ledger is one JSON file (mode 600) rewritten atomically under an exclusive lock. reserve() counts the call at once
(a crash or transport error still counts) and holds the call's bound; settle() replaces the hold by the cost of the
returned usage. allot_db()/settle_db() move a block of calls to the database side (ROUTE_LAB.RL_BUDGET) and back.

The bound: every request sends maxTokens = MAX_TOKENS, which the inference API defines as the most tokens generated
per output sequence, and which Oracle's Grok 4.20 page shows reasoning counts against (research/src/xai-grok-4-20.txt
lines 77-80). A call is held at IN_BOUND input + 2 x MAX_TOKENS output tokens (settle bills completion + reasoning
tokens, which double-counts when a service already includes reasoning in completion; the factor 2 keeps the hold above
what settle can charge under either convention). The bound is checked on every answer that reports usage: a model
billed above it is marked unbounded and never called again, so the only exposure past the hold is the first such call
per model (PLAN.md section 7 states it).
"""
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

CAP_CALLS = 200
CAP_USD = 2.00
STEP_CAPS = {                     # bucket: (calls, USD)   PLAN.md section 7
    "s3_smoke": (10, 0.20),
    "s5_routing": (70, 1.00),
    "s6_plsql": (16, 0.15),
    "s7_select_ai": (12, 0.15),
    "s9_responses": (10, 0.10),
    "reserve": (82, 0.40),
}
if sum(c for c, _ in STEP_CAPS.values()) != CAP_CALLS or abs(sum(u for _, u in STEP_CAPS.values()) - CAP_USD) > 1e-9:
    raise RuntimeError("STEP_CAPS must add up to the total caps")

# USD per 1M tokens (input, output), 10-Oct-2026 Oracle price list, under 200K context
# (research/src/price-list-genai-2026-10-10.txt). No entry -> the model is refused (no OpenAI model, by design).
PRICES = {
    "xai.grok-4.3": (1.25, 2.50),                   # B112080 / B112082
    "xai.grok-4.20-0309-reasoning": (1.25, 2.50),   # Grok 4.2 lines B111910 / B112076 (our mapping)
    "google.gemini-2.5-flash": (0.30, 2.50),        # B111851 / B111853
    "google.gemini-2.5-pro": (1.25, 10.00),         # B111847 / B111849
    "xai.grok-4.7": (2.00, 6.00),                   # B113303 / B113305
    "xai.grok-4.6": (2.00, 6.00),                   # B112086 / B112589 (P5 fallback)
}
MAX_TOKENS = 1024        # sent as maxTokens (chat), max_output_tokens (Responses) and max_tokens (Select AI)
IN_BOUND = 2000          # our prompts are about 20 tokens; Select AI adds its own wrapper; 2,000 covers both
RESERVE_IN_TOKENS = IN_BOUND
RESERVE_OUT_TOKENS = 2 * MAX_TOKENS
FREE_STATUSES = {400, 401, 403, 404, 409, 412, 429}   # refused before inference: no charge assumed


class BudgetExceeded(RuntimeError):
    pass


def cost_usd(model: str, in_tokens: int, out_tokens: int) -> float:
    p_in, p_out = PRICES[model]
    return (in_tokens * p_in + out_tokens * p_out) / 1_000_000


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)

    # ------------------------------------------------------------------ storage
    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = self._load()
            yield state
            self._save(state)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "cap_calls": CAP_CALLS, "cap_usd": CAP_USD, "next": 1, "calls": [],
                    "db": [], "max_out": {}, "unbounded": {}, "halted": None, "created_utc": _now()}
        st = json.loads(self.path.read_text(encoding="utf-8"))
        if st.get("cap_calls") != CAP_CALLS or st.get("cap_usd") != CAP_USD:
            raise BudgetExceeded("the ledger's caps differ from the code's caps; refusing to continue")
        return st

    def _save(self, st: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".budget.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(st, fh, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ------------------------------------------------------------------ arithmetic
    @staticmethod
    def _committed(st: dict, bucket: str | None = None):
        """(calls, usd) counted so far: settled costs + open holds + database allotments not yet settled."""
        calls, usd = 0, 0.0
        for c in st["calls"]:
            if bucket is None or c["bucket"] == bucket:
                calls += 1
                usd += c["usd"] if c["settled"] else c["hold_usd"]
        for d in st["db"]:
            if bucket is None or d["bucket"] == bucket:
                calls += d["used_calls"] if d["settled"] else d["calls"]
                usd += d["used_usd"] if d["settled"] else d["usd"]
        return calls, usd

    @staticmethod
    def _hold(st: dict, model: str) -> float:
        return cost_usd(model, RESERVE_IN_TOKENS, RESERVE_OUT_TOKENS)

    @staticmethod
    def _refuse_if_stopped(st: dict, model: str | None = None) -> None:
        if st.get("halted"):
            raise BudgetExceeded(f"budget halted: {st['halted']}")
        if model is not None and st.get("unbounded", {}).get(model):
            raise BudgetExceeded(f"{model} billed more than maxTokens once; it is not called again")

    def _check(self, st: dict, bucket: str, add_calls: int, add_usd: float) -> None:
        if bucket not in STEP_CAPS:
            raise BudgetExceeded(f"unknown budget bucket {bucket!r}")
        cap_c, cap_u = STEP_CAPS[bucket]
        b_calls, b_usd = self._committed(st, bucket)
        t_calls, t_usd = self._committed(st)
        if b_calls + add_calls > cap_c:
            raise BudgetExceeded(f"{bucket}: call cap {cap_c} reached ({b_calls} counted)")
        if t_calls + add_calls > CAP_CALLS:
            raise BudgetExceeded(f"total call cap {CAP_CALLS} reached ({t_calls} counted)")
        if b_usd + add_usd > cap_u + 1e-12:
            raise BudgetExceeded(f"{bucket}: USD cap {cap_u:.2f} would be exceeded ({b_usd:.4f} committed)")
        if t_usd + add_usd > CAP_USD + 1e-12:
            raise BudgetExceeded(f"total USD cap {CAP_USD:.2f} would be exceeded ({t_usd:.4f} committed)")

    @staticmethod
    def _bucket(step: str, use_reserve: str | None) -> str:
        if use_reserve is not None:
            if not use_reserve.strip():
                raise BudgetExceeded("the reserve bucket needs a reason")
            return "reserve"
        return step

    # ------------------------------------------------------------------ Python-side calls
    def reserve(self, step: str, model: str, *, kind: str, alias: str = "", use_reserve: str | None = None) -> int:
        """Count one attempt and hold its worst-case cost. Returns the ticket number. Raises BudgetExceeded."""
        if model not in PRICES:
            raise BudgetExceeded(f"model {model!r} has no price entry; it is not called")
        bucket = self._bucket(step, use_reserve)
        with self._locked() as st:
            self._refuse_if_stopped(st, model)
            hold = self._hold(st, model)
            self._check(st, bucket, 1, hold)
            n = st["next"]
            st["next"] = n + 1
            st["calls"].append({"n": n, "step": step, "bucket": bucket, "model": model, "kind": kind, "alias": alias,
                                "reason": use_reserve, "hold_usd": round(hold, 6), "usd": 0.0, "settled": False,
                                "reserved_utc": _now()})
            return n

    def settle(self, ticket: int, *, http_status: int | None, prompt_tokens: int | None = None,
               completion_tokens: int | None = None, reasoning_tokens: int | None = None) -> float:
        """Replace the hold by the actual cost. Output billed as completion + reasoning (over-counts, never under)."""
        with self._locked() as st:
            c = next((x for x in st["calls"] if x["n"] == ticket), None)
            if c is None or c["settled"]:
                raise BudgetExceeded(f"ticket {ticket} unknown or already settled")
            usage = prompt_tokens is not None and completion_tokens is not None
            if http_status in FREE_STATUSES:
                usd = 0.0
            elif http_status is not None and 200 <= http_status < 300 and usage:
                out = completion_tokens + (reasoning_tokens or 0)
                usd = cost_usd(c["model"], prompt_tokens, out)
                st["max_out"][c["model"]] = max(st["max_out"].get(c["model"], 0), out)
                if out > RESERVE_OUT_TOKENS or prompt_tokens > IN_BOUND:
                    st.setdefault("unbounded", {})[c["model"]] = f"call {ticket} billed above the per-call bound"
            else:
                usd = c["hold_usd"]      # unknown usage, 5xx or transport error: the bound stands as the cost
            c.update({"usd": round(usd, 6), "settled": True, "http_status": http_status,
                      "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "reasoning_tokens": reasoning_tokens, "settled_utc": _now()})
            total_calls, total_usd = self._committed(st)
            if total_usd > CAP_USD + 1e-12 or total_calls > CAP_CALLS:
                st["halted"] = f"total {total_calls} calls / USD {total_usd:.4f} passed the cap after call {ticket}"
            return usd

    # ------------------------------------------------------------------ database-side block
    def allot_db(self, step: str, calls: int, usd: float, *, use_reserve: str | None = None) -> int:
        if calls <= 0 or usd <= 0:
            raise BudgetExceeded("a database allotment needs positive calls and USD")
        bucket = self._bucket(step, use_reserve)
        with self._locked() as st:
            self._refuse_if_stopped(st)
            self._check(st, bucket, calls, usd)
            n = st["next"]
            st["next"] = n + 1
            st["db"].append({"n": n, "step": step, "bucket": bucket, "calls": calls, "usd": round(usd, 6),
                             "reason": use_reserve, "used_calls": 0, "used_usd": 0.0, "settled": False,
                             "allotted_utc": _now()})
            return n

    def settle_db(self, allotment: int, used_calls: int, used_usd: float) -> None:
        with self._locked() as st:
            d = next((x for x in st["db"] if x["n"] == allotment), None)
            if d is None or d["settled"]:
                raise BudgetExceeded(f"allotment {allotment} unknown or already settled")
            if used_calls > d["calls"]:
                raise BudgetExceeded("the database used more calls than allotted: stop and investigate")
            d.update({"used_calls": used_calls, "used_usd": round(max(used_usd, 0.0), 6), "settled": True,
                      "settled_utc": _now()})
            total_calls, total_usd = self._committed(st)
            if used_usd > d["usd"] + 1e-12 or total_usd > CAP_USD + 1e-12:
                st["halted"] = f"database allotment {allotment} used USD {used_usd:.4f} of {d['usd']:.4f}"

    # ------------------------------------------------------------------ reporting
    def summary(self) -> dict:
        st = self._load()
        out = {"cap_calls": CAP_CALLS, "cap_usd": CAP_USD, "buckets": {}}
        for b, (cc, cu) in STEP_CAPS.items():
            calls, usd = self._committed(st, b)
            out["buckets"][b] = {"calls": calls, "cap_calls": cc, "usd": round(usd, 4), "cap_usd": cu}
        calls, usd = self._committed(st)
        out["total"] = {"calls": calls, "usd": round(usd, 4)}
        out["open_holds"] = sum(1 for c in st["calls"] if not c["settled"])
        out["open_db_allotments"] = sum(1 for d in st["db"] if not d["settled"])
        out["halted"] = st.get("halted")
        out["unbounded_models"] = sorted(st.get("unbounded", {}))
        return out
