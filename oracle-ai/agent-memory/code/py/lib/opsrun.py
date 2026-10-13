# v1.2 2026-10-12  Brief 12 steps 8 and 10 (PLAN v0.3.8 6 rows 8 and 10, 11.3): pure helpers, tested offline.
#                  v1.2: (after step 10's first run, 18:22Z) snippet_count applies PLAN 6 row 10's counting rule as
#                  written: S7 counts when it fails against the minimally fixed S2's memories (stored as 'memory').
#                  v1.0/v1.1 also required B17's message, a condition the plan does not have; the first run failed with
#                  KeyError 'No record found with the given ID and type.' and was therefore not counted. Whether the
#                  error is B17's is still reported (s7_error_is_b17), and so is the control (stored types).
#                  v1.1: (Codex 35 r1, finding 3) error messages are kept verbatim (line breaks included, only
#                  redacted); a message longer than MAX_MSG is cut and marked truncated. v1.0: first version (phase 2).
"""Helpers shared by py/80_operations.py and py/100_snippets_live.py. No database, no SDK import, no LLM.

error_chain(exc)        the exception and every __cause__ / __context__ under it, as (type, verbatim message): the SDK
                        often re-raises a generic RuntimeError and keeps the Oracle error in __context__ (API_NOTES B11)
run_op(name, fn, ...)   one operation's outcome: ok, seconds, a short result, or the error chain; never raises except
                        for the exceptions the caller lists as fatal (the D5 budget stop)
list_a() / list_b()     PLAN 11.3's runtime grant lists, as (privilege, object) pairs
privilege_diff(found, expected)   missing and extra pairs
snippet_count(s7_live)  PLAN 6 row 10's counting rule: N of 10 = the 5 offline failures + S7 if it failed live
                        against memories written by the minimally fixed S2 (whichever error; whether it is B17's
                        incompatible-type message is reported apart)
"""
from __future__ import annotations

import time
from typing import Callable, Iterable

MAX_MSG = 8000
B17_MESSAGE = "A relation endpoint has an incompatible record type."
OFFLINE_FAILURES = ("S1", "S2", "S3a", "S3b", "S4")        # research/check_blog_snippets.out (09-Oct)
OFFLINE_WORKS = ("S5", "S6", "S7", "S8", "S9")

OPS_TABLES_A = ("OPS_THREAD", "OPS_ACTOR_PROFILE", "OPS_MESSAGE", "OPS_MEMORY", "OPS_RECORD_CHUNKS")
OPS_TABLES_B = ("OPS_DOCUMENT", "OPS_MEMORY_LINK", "OPS_THREAD_SUMMARY")
DML = ("SELECT", "INSERT", "UPDATE", "DELETE")


def list_a() -> set[tuple[str, str]]:
    """PLAN 11.3 list A (82a): the docs' read/write runtime list."""
    out = {("SELECT", "AGENT_MEMORY_STORES"), ("EXECUTE", "DBMS_AGENT_MEMORY_STORE")}
    out |= {(p, t) for t in OPS_TABLES_A for p in DML}
    return out


def list_b() -> set[tuple[str, str]]:
    """PLAN 11.3 list B (82b): the three tables the docs omit and SELECT on the property graph."""
    out = {(p, t) for t in OPS_TABLES_B for p in DML}
    out.add(("SELECT", "OPS_MEMORY_GRAPH"))
    return out


def privilege_diff(found: Iterable[tuple[str, str]], expected: set[tuple[str, str]]) -> dict:
    f = {(str(p).upper(), str(o).upper()) for p, o in found}
    return {"missing": sorted(expected - f), "extra": sorted(f - expected)}


def _msg(exc: BaseException, redact: Callable[[str], str] | None) -> tuple[str, bool]:
    """str(exc) verbatim (line breaks kept), redacted; cut at MAX_MSG characters, and then marked truncated."""
    text = str(exc)
    if redact is not None:
        text = redact(text)
    return text[:MAX_MSG], len(text) > MAX_MSG


def error_chain(exc: BaseException, redact: Callable[[str], str] | None = None, limit: int = 6) -> list[dict]:
    """The exception, then its __cause__ (or, if none, its __context__), and so on, at most `limit` deep; each as its
    type name and its message verbatim (redacted; "truncated": true if it was longer than MAX_MSG). Loops are cut."""
    out, seen = [], set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen and len(out) < limit:
        seen.add(id(cur))
        text, cut = _msg(cur, redact)
        rec = {"type": type(cur).__name__, "message": text}
        if cut:
            rec["truncated"] = True
        out.append(rec)
        cur = cur.__cause__ if cur.__cause__ is not None else cur.__context__
    return out


def oracle_codes(chain: list[dict]) -> list[str]:
    """The ORA-/DRG-/PLS- codes named anywhere in an error chain, in order, without repeats."""
    import re
    codes: list[str] = []
    for e in chain:
        for c in re.findall(r"\b(?:ORA|DRG|PLS)-\d{5}\b", e.get("message", "")):
            if c not in codes:
                codes.append(c)
    return codes


def run_op(name: str, fn: Callable[[], object], *, summarize: Callable[[object], object] | None = None,
           calls: Callable[[], int] | None = None, fatal: tuple[type[BaseException], ...] = (),
           redact: Callable[[str], str] | None = None, clock=time.perf_counter) -> dict:
    """Run one operation and record it. An exception of a `fatal` type propagates; any other is recorded with its
    chain and the Oracle codes in it. `calls` returns the LLM call counter, read before and after."""
    c0 = calls() if calls else None
    t = clock()
    rec: dict = {"op": name}
    try:
        res = fn()
        rec["ok"] = True
        rec["result"] = summarize(res) if summarize else None
    except fatal:
        raise
    except Exception as exc:          # recorded verbatim: a refused operation is a finding, not a crash
        rec["ok"] = False
        rec["error_chain"] = error_chain(exc, redact)
        rec["oracle_codes"] = oracle_codes(rec["error_chain"])
    rec["seconds"] = round(clock() - t, 3)
    if calls:
        rec["llm_calls"] = calls() - c0
    return rec


def snippet_count(s7_live: dict) -> dict:
    """PLAN 6 row 10's counting rule (v0.2), as written: "S7 counts as a sixth failure only if it fails against
    memories written by that minimally fixed S2" (only text= fixed, so stored as 'memory'). The error itself is
    reported, and whether it is the incompatible-record-type message the offline check predicted (B17), but the rule
    does not depend on which error it is.
    s7_live: {"ok": bool, "error_chain": [...], "s2_form": "minimally fixed", "stored_types": [...]}."""
    chain = s7_live.get("error_chain") or []
    msg_match = any(B17_MESSAGE in e.get("message", "") for e in chain)
    form_ok = s7_live.get("s2_form") == "minimally fixed" and set(s7_live.get("stored_types") or []) == {"memory"}
    s7_fails = (s7_live.get("ok") is False) and form_ok
    failures = list(OFFLINE_FAILURES) + (["S7"] if s7_fails else [])
    first = chain[0] if chain else {}
    reason = (f"S7 failed live against memories stored as 'memory' by the minimally fixed S2 "
              f"({first.get('type')}: {first.get('message')}; B17's predicted message: {msg_match})" if s7_fails else
              "S7 not counted: " + ("it succeeded" if s7_live.get("ok") else
                                    "the S2 form or the stored types were not the minimally fixed ones"))
    return {"n_fail": len(failures), "of": 10, "failing": failures, "s7_counted": s7_fails,
            "s7_error_is_b17": msg_match, "rule": "PLAN v0.3.8 6 row 10 (counting rule v0.2)", "reason": reason}
