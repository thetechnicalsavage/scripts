# v1.2 2026-10-12  Brief 12: tests of py/lib/opsrun.py (steps 8 and 10 helpers). v1.2: the counting rule as PLAN 6
#                  row 10 writes it (any failure against the minimally fixed S2 counts). v1.1: messages verbatim.
#                  v1.0: first version (phase 2).
import pytest

from lib import opsrun


def test_grant_lists_match_plan_11_3():
    a, b = opsrun.list_a(), opsrun.list_b()
    assert len(a) == 22 and len(b) == 13 and not (a & b)
    assert ("SELECT", "AGENT_MEMORY_STORES") in a and ("EXECUTE", "DBMS_AGENT_MEMORY_STORE") in a
    assert ("DELETE", "OPS_RECORD_CHUNKS") in a and ("SELECT", "OPS_MEMORY_LINK") not in a
    assert ("SELECT", "OPS_MEMORY_GRAPH") in b and ("INSERT", "OPS_THREAD_SUMMARY") in b
    assert len(a | b) == 35            # 82b's read-back count


def test_privilege_diff_exact_and_failures():
    a = opsrun.list_a()
    assert opsrun.privilege_diff([(p.lower(), o.lower()) for p, o in a], a) == {"missing": [], "extra": []}
    found = list(a - {("SELECT", "OPS_MEMORY")}) + [("SELECT", "OPS_DOCUMENT")]
    d = opsrun.privilege_diff(found, a)
    assert d["missing"] == [("SELECT", "OPS_MEMORY")] and d["extra"] == [("SELECT", "OPS_DOCUMENT")]


class _Ora(Exception):
    pass


def _chained():
    try:
        try:
            raise _Ora("ORA-00942: table or view \"AGENTMEM\".\"OPS_MEMORY_LINK\" does not exist\nHelp: x")
        except _Ora as inner:
            raise RuntimeError("Unable to link records.") from inner
    except RuntimeError as outer:
        return outer


def test_error_chain_follows_cause_and_context_and_extracts_codes():
    chain = opsrun.error_chain(_chained())
    assert [e["type"] for e in chain] == ["RuntimeError", "_Ora"]
    assert chain[1]["message"].endswith("does not exist\nHelp: x") and "truncated" not in chain[1]
    assert opsrun.oracle_codes(chain) == ["ORA-00942"]
    try:
        try:
            raise ValueError("DRG-50857: oracle error in drvxtab")
        except ValueError:
            raise KeyError("x")              # implicit context, no cause
    except KeyError as exc:
        ctx = opsrun.error_chain(exc)
    assert [e["type"] for e in ctx] == ["KeyError", "ValueError"] and opsrun.oracle_codes(ctx) == ["DRG-50857"]


def test_error_chain_redacts_truncates_and_cuts_loops():
    exc = ValueError("secret-XYZ " + "a" * 5000)
    exc.__context__ = exc                    # a loop must not hang
    chain = opsrun.error_chain(exc, redact=lambda t: t.replace("secret-XYZ", "[REDACTED]"))
    assert len(chain) == 1 and chain[0]["message"].startswith("[REDACTED]")
    assert len(chain[0]["message"]) == len("[REDACTED] ") + 5000 and "truncated" not in chain[0]
    big = ValueError("b" * (opsrun.MAX_MSG + 10))
    cut = opsrun.error_chain(big)[0]
    assert len(cut["message"]) == opsrun.MAX_MSG and cut["truncated"] is True


def test_run_op_ok_error_and_fatal():
    n = {"c": 0}

    def calls():
        return n["c"]

    def fn_ok():
        n["c"] += 2
        return [1, 2, 3]

    rec = opsrun.run_op("ok", fn_ok, summarize=len, calls=calls)
    assert rec["ok"] and rec["result"] == 3 and rec["llm_calls"] == 2

    def fn_bad():
        raise _chained()

    rec = opsrun.run_op("bad", fn_bad, calls=calls)
    assert rec["ok"] is False and rec["oracle_codes"] == ["ORA-00942"] and rec["llm_calls"] == 0

    class Budget(RuntimeError):
        pass

    def fn_fatal():
        raise Budget("stop")

    with pytest.raises(Budget):
        opsrun.run_op("fatal", fn_fatal, fatal=(Budget,))


def _s7(ok, msg=None, form="minimally fixed", types=("memory", "memory")):
    rec = {"ok": ok, "s2_form": form, "stored_types": list(types)}
    if msg:
        rec["error_chain"] = [{"type": "ValueError", "message": msg}]
    return rec


def test_snippet_count_rule():
    c = opsrun.snippet_count(_s7(False, opsrun.B17_MESSAGE))
    assert c["n_fail"] == 6 and c["failing"][-1] == "S7" and c["s7_counted"] and c["s7_error_is_b17"]
    assert opsrun.snippet_count(_s7(True))["n_fail"] == 5
    # PLAN 6 row 10: any failure against the minimally fixed S2's memories counts; the message is only reported
    other = opsrun.snippet_count(_s7(False, "'No record found with the given ID and type.'"))
    assert other["n_fail"] == 6 and other["s7_counted"] and not other["s7_error_is_b17"]
    assert "No record found" in other["reason"]
    wrong_form = opsrun.snippet_count(_s7(False, opsrun.B17_MESSAGE, types=("preference", "preference")))
    assert wrong_form["n_fail"] == 5 and not wrong_form["s7_counted"]
    wrong_s2 = opsrun.snippet_count(_s7(False, opsrun.B17_MESSAGE, form="corrected"))
    assert wrong_s2["n_fail"] == 5
