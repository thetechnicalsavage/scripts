-- v1.0 - app 900 phase 4b: deletes test rows from the target's plan (SHOP.CHAOS_PLAN, plan ids 9000-9099 only, as
--        written by tools/anom_plan_test.sql through PKG_ANOM_GRADE.push_plan), in oradb1 / FREEPDB1 as SHOP. Their
--        runs stay in CHAOS_RUN (the record; training excludes every chaos window). No password here.
--          tools/anom_chaos_run.sh stage tools/anom_plan_clear.sql
--          tools/anom_chaos_run.sh run anom_plan_clear.sql <log> -- <first_id> <last_id>
--        Arguments are not secrets (the runner allows [A-Za-z0-9_.:=-] only); verify is off all the same.
--        v1.0: first version, 01-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 200
whenever sqlerror exit failure rollback

define a_first = "&1"
define a_last = "&2"

declare
  l_first number;
  l_last  number;
begin
  if sys_context('USERENV', 'SESSION_USER') != 'SHOP' then
    raise_application_error(-20900, 'anom_plan_clear: run as SHOP');
  end if;
  if not regexp_like('&a_first', '^[0-9]{4}$') or not regexp_like('&a_last', '^[0-9]{4}$') then
    raise_application_error(-20900, 'anom_plan_clear: two plan ids from 9000 to 9099');
  end if;
  l_first := to_number('&a_first');
  l_last  := to_number('&a_last');
  if l_first < 9000 or l_last > 9099 or l_first > l_last then
    raise_application_error(-20900, 'anom_plan_clear: test plan ids are 9000-9099');
  end if;
  for p in (select plan_id, scenario, intensity, minutes, status, run_id, note from CHAOS_PLAN
             where plan_id between l_first and l_last order by plan_id) loop
    dbms_output.put_line('  plan '||p.plan_id||' '||p.scenario||' '||p.intensity||' '||p.minutes||' min: '||p.status
                         ||' run '||nvl(to_char(p.run_id), '-')||' ('||p.note||')');
  end loop;
  delete from CHAOS_PLAN where plan_id between l_first and l_last;
  dbms_output.put_line('anom_plan_clear: '||sql%rowcount||' target test plan row(s) deleted at '
                       ||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
  commit;
end;
/
undefine a_first
undefine a_last
