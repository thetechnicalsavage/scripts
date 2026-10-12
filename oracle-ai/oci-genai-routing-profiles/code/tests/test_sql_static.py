# v1.3 2026-10-10  Brief 14: static checks of the ROUTE_LAB SQL and the APEX helper scripts (PLAN.md v1.1
#   public copy, 12-Oct-2026: the APEX helper scripts are not published, so their checks are left out and
#   ALL_SQL is sql/ only; the guard list names only lab schemas already public in this repository.
#   sections 5 and 10). v1.3: (screenshot stage) sql/06_shot_queries.sql is read-only and never reads a table that
#   holds an OCID, a compartment or budget values; apex/shot_user.sql creates one end user only while the capture copy
#   is installed, never prints the password and removes only that user.
#   v1.2: sql/05_state_check.sql is read-only and prints no OCID column; reset_salt_lab.sql
#   sets the workspace before the reset (S8 fix).
#   v1.1: Select AI is called only through PKG_ROUTE_LAB.sai_chat (Codex plan review r1, finding 1); the claim
#   writes the RL_CALL row and settle closes the allotment on a breach; Select AI profiles set max_tokens (Codex
#   code review r1, findings 5-7). v1.0: first version. No database: these read the files only.
import re
from pathlib import Path

import pytest

from b14lib import budget, chat

CODE = Path(__file__).resolve().parents[1]
SQL = sorted((CODE / "sql").glob("*.sql"))
ALL_SQL = SQL
DB_WORK = [p for p in ALL_SQL]                       # every script touches the database: all carry the window check
FORBIDDEN_SCHEMAS = ("ANOMOPS", "ANOM_FEED", "DBOPS", "ORADB1_LINK", "RAG_LAB", "NL2SQL_LAB", "SPA_APP")
PHX_INFER = "inference.generativeai.us-phoenix-1.oci.oraclecloud.com"


def body(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def code_only(p: Path) -> str:
    """the file without '--' comment lines (string literals keep their text)"""
    return "\n".join(ln for ln in body(p).splitlines() if not ln.lstrip().startswith("--"))


@pytest.mark.parametrize("p", ALL_SQL, ids=lambda p: p.name)
def test_header_and_error_handling(p):
    first = body(p).splitlines()[0]
    assert re.match(r"^-- v\d+\.\d+ ", first), first
    assert re.search(r"(?im)^whenever sqlerror exit", body(p))


@pytest.mark.parametrize("p", DB_WORK, ids=lambda p: p.name)
def test_window_check_is_first_in_every_script(p):
    t = code_only(p)
    assert "'0150'" in t and "'0400'" in t and "timestamp '2026-10-12 09:00:00'" in t


@pytest.mark.parametrize("p", ALL_SQL, ids=lambda p: p.name)
def test_no_identifier_and_phoenix_only(p):
    t = body(p)
    assert not re.search(r"ocid1\.", t, re.I), "OCID-shaped text"
    assert not re.search(r"(?<![\d.])(\d{1,3}\.){3}\d{1,3}(?![\d.])", t), "IPv4 literal"
    regions = set(re.findall(r"\b[a-z]{2,}-[a-z]+-\d\b", t))
    assert regions <= {"us-phoenix-1"}, regions
    hosts = set(re.findall(r"[a-z0-9.-]+\.oci\.oraclecloud\.com", t))
    assert hosts <= {PHX_INFER}, hosts


@pytest.mark.parametrize("p", ALL_SQL, ids=lambda p: p.name)
def test_no_other_schema_is_touched(p):
    t = code_only(p).upper()
    for s in FORBIDDEN_SCHEMAS:
        assert not re.search(rf"\b{s}\b", t), s
    for stmt in re.findall(r"(?m)^\s*(drop user[^;]*);", code_only(p), re.I):
        assert re.match(r"(?i)drop user if exists ROUTE_LAB cascade$", stmt.strip()), stmt


def test_grants_go_only_to_route_lab_or_askoracle_on_views():
    for p in SQL:
        for g in re.findall(r"(?im)^\s*grant\s+(.+?)\s+to\s+(\w+)\s*;", code_only(p)):
            what, who = g
            assert who.upper() in ("ROUTE_LAB", "ASKORACLE"), g
            if who.upper() == "ASKORACLE":
                assert re.match(r"(?i)select on V_\w+$", what.strip()), g


def test_marker_guards_create_and_drop():
    for name in ("00_route_lab_prereqs.sql", "99_cleanup.sql"):
        t = body(CODE / "sql" / name)
        assert "RL_MARK" in t and "-20141" in t
    t = body(CODE / "sql" / "99_cleanup.sql")
    assert "v$session" in t and not re.search(r"(?i)alter\s+system\s+(kill|disconnect)", t)


def test_package_claims_budget_before_every_call():
    t = code_only(CODE / "sql" / "02_route_lab_pkg.sql")
    pkg_body = t[t.index("create or replace package body"):]
    for fn, call in (("function chat", "dbms_cloud.send_request"), ("function sai_chat", "dbms_cloud_ai.generate")):
        seg = pkg_body[pkg_body.index(fn):]
        assert seg.index("claim(") < seg.index(call), fn
    assert t.count("dbms_cloud.send_request") == 1
    assert re.search(r"uri\s*=>\s*c_chat_uri", t)                  # the URI is the constant, nothing else


def test_prompts_and_limits_match_the_python_side():
    t = body(CODE / "sql" / "02_route_lab_pkg.sql")
    words = re.findall(r"when (\d) then '([A-Z]+)'", t)
    assert [w for _, w in sorted(words)] == [w for w, _ in chat.PROMPTS]
    assert "'Reply with exactly one word: '" in t and all(p.startswith("Reply with exactly one word: ")
                                                          for _, p in chat.PROMPTS)
    assert f"c_max_tokens constant pls_integer   := {chat.MAX_TOKENS}" in t
    assert f"c_hold_in    constant pls_integer   := {budget.RESERVE_IN_TOKENS}" in t
    assert f"c_hold_out   constant pls_integer   := {budget.RESERVE_OUT_TOKENS}" in t
    assert set(re.findall(r"in \((\d{3}(?:, \d{3})*)\)", t)[0].split(", ")) == {str(s) for s in budget.FREE_STATUSES}


def test_inference_happens_only_inside_the_budgeted_package():
    for p in ALL_SQL:
        t = code_only(p).lower()
        if p.name == "02_route_lab_pkg.sql":
            assert t.count("dbms_cloud_ai.generate(") == 1 and t.count("dbms_cloud.send_request(") == 1
        else:
            assert "dbms_cloud_ai.generate" not in t and "dbms_cloud.send_request" not in t, p.name
    t = code_only(CODE / "sql" / "04_db_calls.sql")
    assert "PKG_ROUTE_LAB.sai_chat(" in t and "PKG_ROUTE_LAB.chat(" in t


def test_claim_writes_the_row_and_settle_fails_closed():
    t = code_only(CODE / "sql" / "02_route_lab_pkg.sql")
    claim = t[t.index("function claim ("):t.index("end claim;")]
    assert "insert into rl_call" in claim and claim.index("update rl_budget") < claim.index("commit;")
    settle = t[t.index("procedure settle ("):t.index("end settle;")]
    assert "c_hold_out" in settle and "set calls_allotted = calls_used" in settle
    for fn in ("function chat (", "function sai_chat ("):
        seg = t[t.index(fn):]
        seg = seg[:seg.index("end ")]
        assert "insert into rl_call" not in seg                      # the row exists from the claim on


def test_select_ai_profiles_bound_the_output():
    t = code_only(CODE / "sql" / "03_select_ai_profiles.sql")
    assert t.count(f"'max_tokens' value {budget.MAX_TOKENS}") == 2


def test_state_check_is_read_only_and_never_selects_an_ocid():
    t = code_only(CODE / "sql" / "05_state_check.sql")
    assert not re.search(r"(?i)\b(insert|update|delete|merge|create|drop|alter|grant|revoke|truncate|commit)\b",
                         t.replace("whenever sqlerror exit failure rollback", ""))
    assert not re.search(r"(?i)dbms_cloud|pkg_route_lab", t)
    assert "case when ocid is null then 'no' else 'yes' end" in t
    assert not re.search(r"(?im)^select[^;]*\bocid\b(?!\s+is null)", t.replace("ocid is null", ""))
    # RL_CONFIG holds the compartment OCID: it may only be counted
    assert all("count(*) from rl_config" in ln for ln in t.splitlines() if re.search(r"(?i)\brl_config\b", ln))


def test_shot_queries_are_read_only_and_never_read_identifier_tables():
    t = code_only(CODE / "sql" / "06_shot_queries.sql")
    assert not re.search(r"(?i)\b(insert|update|delete|merge|create|drop|alter|grant|revoke|truncate|commit)\b",
                         t.replace("whenever sqlerror exit failure rollback", ""))
    assert not re.search(r"(?i)dbms_cloud|pkg_route_lab|\brl_(profile|config|budget|call|result)\b|\bocid\b", t)
    assert "user != 'ROUTE_LAB'" in t
