-- v1.0 - brief 14 ROUTE_LAB: the database's own calls, part A (S6, PL/SQL SEND_REQUEST) or part B (S7, Select AI).
--        Run as ROUTE_LAB. v1.0: first version (PLAN.md v1.0 sections 6 and 7). Written in the plan stage; NOT run.
--
-- Usage : sqlplus -s -L ROUTE_LAB@<alias> @04_db_calls.sql A     (or B)   (password on stdin)
-- Needs : an open RL_BUDGET row for the part's step, written by `poc/db_register.py allot` (s6_plsql: 16 calls,
--         s7_select_ai: 12 calls); PKG_ROUTE_LAB refuses every call beyond it. After the run:
--         `poc/db_register.py settle` books what was used back into the Python ledger.
-- Calls : A = P1 and P3, three prompts each, direct and through the profile, alternating order (12 calls);
--         B = RL_SAI_P1, RL_SAI_P3, RL_SAI_DIRECT, three prompts each (9 calls). One call at a time, 1 s apart.
-- Re-run: each run makes new calls and new RL_CALL rows, within the allotment only.
whenever sqlerror exit failure rollback
set verify off feedback off serveroutput on size unlimited
define part = &1

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '04: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '04: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if user != 'ROUTE_LAB' then
    raise_application_error(-20140, '04: run as ROUTE_LAB');
  end if;
end;
/

declare
  c_part constant varchar2(1) := upper(substr('&part', 1, 1));
  l_id   number;
  e_budget exception;
  pragma exception_init(e_budget, -20142);

  procedure one_chat (p_alias varchar2, p_kind varchar2, p_n pls_integer) is
  begin
    l_id := PKG_ROUTE_LAB.chat('s6_plsql', p_alias, p_kind, p_n);
    dbms_output.put_line('call ' || l_id || ': ' || p_alias || ' ' || p_kind || ' prompt ' || p_n);
    dbms_session.sleep(1);
  exception
    when others then
      if sqlcode = -20147 then
        dbms_output.put_line(p_alias || ' ' || p_kind || ': skipped, ' || PKG_ROUTE_LAB.redact(sqlerrm));
      else
        raise;
      end if;
  end one_chat;
begin
  if c_part not in ('A', 'B') then
    raise_application_error(-20144, '04: argument 1 must be A or B');
  end if;
  if c_part = 'A' then
    for a in (select alias from rl_profile where alias in ('P1', 'P3') order by alias) loop
      for n in 0 .. 2 loop
        if mod(n, 2) = 0 then
          one_chat(a.alias, 'DIRECT', n);
          one_chat(a.alias, 'PROFILE', n);
        else
          one_chat(a.alias, 'PROFILE', n);
          one_chat(a.alias, 'DIRECT', n);
        end if;
      end loop;
    end loop;
  else
    for p in (select profile_name from user_cloud_ai_profiles
               where profile_name in ('RL_SAI_P1', 'RL_SAI_P3', 'RL_SAI_DIRECT') order by profile_name) loop
      for n in 0 .. 2 loop
        l_id := PKG_ROUTE_LAB.sai_chat('s7_select_ai', p.profile_name, n);
        dbms_output.put_line('call ' || l_id || ': ' || p.profile_name || ' prompt ' || n);
        dbms_session.sleep(1);
      end loop;
    end loop;
  end if;
exception
  when e_budget then
    dbms_output.put_line('STOPPED by the budget: ' || sqlerrm);
end;
/

prompt Calls of this part (no OCID is stored in RL_CALL):
set linesize 200 pagesize 100
column alias          format a5
column kind           format a8
column model_id       format a30
column err_code       format a24
column err_text       format a50
column model_id_shape format a28
column answer_snip    format a20
select call_id, alias, kind, model_id, prompt_no, http_status, answer_ok, latency_ms, model_id_shape,
       completion_tokens, reasoning_tokens, err_code, err_text, answer_snip
  from rl_call
 where step = case upper(substr('&part', 1, 1)) when 'A' then 's6_plsql' else 's7_select_ai' end
 order by call_id;
select step, calls_allotted, calls_used, round(usd_allotted, 4) usd_allotted, round(usd_used_est, 4) usd_used_est
  from rl_budget order by step;
