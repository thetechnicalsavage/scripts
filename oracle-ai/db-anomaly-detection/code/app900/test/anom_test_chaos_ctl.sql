-- v1.2 - CHAOS_CTL in oradb1 / FREEPDB1: what the observer's link user can and cannot do (contract sections 5 and 12:
--        EXECUTE on SHOP.PKG_CHAOS, SELECT on SHOP.CHAOS_RUN and SHOP.DRIVER_CONTROL, INSERT and SELECT on
--        SHOP.CHAOS_PLAN, nothing else). Run as CHAOS_CTL, the password on stdin:
--          tools/anom_chaos_run.sh run anom_test_chaos_ctl.sql <log> --as CHAOS_CTL
--        v1.2: (phase 4b, 93 v1.2) the five grants; CHAOS_CTL pushes a plan row (a 2099 row, rolled back) and the
--              target refuses one over its scenario's maximum (ORA-02290); it cannot change or delete plan rows; it
--              cannot call dispatch() (-20106), 01-Oct-2026.
--        v1.1: a check's detail is printed on one line (an error stack's second line could start with ORA-),
--              01-Oct-2026.
--        v1.0: first version, 01-Oct-2026.
--        Injects nothing: the only start_scenario call is refused (-20101), the one accepted plan row is rolled back,
--        and every other write is refused by the database. 26ai reports a missing object privilege as ORA-41900;
--        ORA-01031 and ORA-00942 are accepted too.
--        Every check prints PASS or FAIL; the exit code is the number of failures.
set serveroutput on size unlimited format wrapped
set verify off
set define off
set feedback off
set linesize 200 tab off trimout on
whenever sqlerror exit failure rollback
whenever oserror exit failure

variable n_pass number
variable n_fail number

declare
  g_pass pls_integer := 0;
  g_fail pls_integer := 0;
  n      number;
  l_id   number;
  l_txt  varchar2(4000);

  function one_line (p varchar2) return varchar2 is
  begin
    return translate(p, chr(10)||chr(13), '  ');
  end one_line;

  procedure ok (p_what varchar2, p_cond boolean, p_detail varchar2 default null) is
  begin
    if p_cond then
      g_pass := g_pass + 1;
      dbms_output.put_line('PASS  '||p_what||case when p_detail is not null then '  ['||substr(one_line(p_detail), 1, 200)||']' end);
    else
      g_fail := g_fail + 1;
      dbms_output.put_line('FAIL  '||p_what||case when p_detail is not null then '  ['||substr(one_line(p_detail), 1, 200)||']' end);
    end if;
  end ok;

  procedure denied (p_what varchar2, p_sql varchar2, p_extra number default 0) is
  begin
    execute immediate p_sql;
    rollback;
    ok(p_what, false, 'the statement was allowed');
  exception
    when others then
      rollback;
      ok(p_what, sqlcode in (-942, -1031, -41900, p_extra), sqlerrm);
  end denied;
begin
  if sys_context('USERENV', 'SESSION_USER') != 'CHAOS_CTL' then
    raise_application_error(-20900, 'anom_test_chaos_ctl.sql: run as CHAOS_CTL, not '||sys_context('USERENV', 'SESSION_USER'));
  end if;
  dbms_output.put_line('anom_test_chaos_ctl v1.2 '||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));

  -- what it can do
  select count(*) into n from SHOP.CHAOS_RUN;
  ok('CHAOS_CTL reads SHOP.CHAOS_RUN', true, n||' runs');
  select count(*) into n from SHOP.DRIVER_CONTROL;
  ok('CHAOS_CTL reads SHOP.DRIVER_CONTROL', n = 1, n||' row');
  l_txt := SHOP.PKG_CHAOS.state_problems;
  ok('CHAOS_CTL calls SHOP.PKG_CHAOS (state_problems)', true, nvl(l_txt, 'target clean'));
  ok('CHAOS_CTL calls SHOP.PKG_CHAOS.max_minutes', SHOP.PKG_CHAOS.max_minutes('slow_drift') = 240);
  begin
    l_id := SHOP.PKG_CHAOS.start_scenario('rm_rf', 5);
    ok('PKG_CHAOS validates the name for CHAOS_CTL too: -20101', false, 'started run '||l_id);
    SHOP.PKG_CHAOS.stop_all('anom_test_chaos_ctl: this start should have been refused');
  exception
    when others then ok('PKG_CHAOS validates the name for CHAOS_CTL too: -20101', sqlcode = -20101, sqlerrm);
  end;
  begin
    SHOP.PKG_CHAOS.run(1);
    ok('CHAOS_CTL cannot call run() (only a dispatcher job can): -20106', false, 'no error');
  exception
    when others then ok('CHAOS_CTL cannot call run() (only a dispatcher job can): -20106', sqlcode = -20106, sqlerrm);
  end;
  begin
    SHOP.PKG_CHAOS.dispatch(1);
    ok('CHAOS_CTL cannot run a dispatcher loop (only the job can): -20106', false, 'no error');
  exception
    when others then ok('CHAOS_CTL cannot run a dispatcher loop (only the job can): -20106', sqlcode = -20106, sqlerrm);
  end;

  -- the plan push (v1.2): CHAOS_CTL inserts and reads CHAOS_PLAN; the target checks every row
  select count(*) into n from SHOP.CHAOS_PLAN;
  ok('CHAOS_CTL reads SHOP.CHAOS_PLAN', true, n||' plan rows');
  begin
    insert into SHOP.CHAOS_PLAN (plan_id, planned_start_ts, scenario, intensity, minutes)
    values (9198, timestamp '2099-01-01 00:00:00', 'cpu_hog', 'LOW', 30);
    ok('CHAOS_CTL inserts a plan row (a 2099 row, rolled back at once)', sql%rowcount = 1);
  exception
    when others then ok('CHAOS_CTL inserts a plan row (a 2099 row, rolled back at once)', false, sqlerrm);
  end;
  rollback;
  begin
    insert into SHOP.CHAOS_PLAN (plan_id, planned_start_ts, scenario, intensity, minutes)
    values (9198, timestamp '2099-01-01 00:00:00', 'cpu_hog', 'LOW', 31);
    rollback;
    ok('the target refuses a CHAOS_CTL plan row over the scenario''s maximum (cpu_hog 31 min): ORA-02290', false,
       'accepted');
  exception
    when others then
      rollback;
      ok('the target refuses a CHAOS_CTL plan row over the scenario''s maximum (cpu_hog 31 min): ORA-02290',
         sqlcode = -2290, sqlerrm);
  end;

  -- what it cannot do
  denied('CHAOS_CTL cannot update SHOP.DRIVER_CONTROL directly', 'update SHOP.DRIVER_CONTROL set error_pct = 0 where id = 1');
  denied('CHAOS_CTL cannot insert into SHOP.CHAOS_RUN',
         q'[insert into SHOP.CHAOS_RUN (scenario, intensity, source, planned_end_ts) values ('cpu_hog', 'LOW', 'UI', sysdate + 1)]');
  denied('CHAOS_CTL cannot read SHOP.ORDERS', 'select count(*) from SHOP.ORDERS where rownum = 1');
  denied('CHAOS_CTL cannot read SHOP.CHAOS_SCRATCH', 'select count(*) from SHOP.CHAOS_SCRATCH');
  denied('CHAOS_CTL cannot change a plan row', q'[update SHOP.CHAOS_PLAN set status = 'SKIPPED' where plan_id = -1]');
  denied('CHAOS_CTL cannot delete plan rows', 'delete from SHOP.CHAOS_PLAN where plan_id = -1');
  -- PLS-00201 (wrapped in ORA-06550) is how a missing EXECUTE shows in an anonymous block
  denied('CHAOS_CTL cannot execute SHOP.PKG_SHOP', q'[begin SHOP.PKG_SHOP.settle(date '2000-01-01'); end;]', -6550);

  select count(*) into n from user_tab_privs where grantee = 'CHAOS_CTL' and owner = 'SHOP' and type != 'USER';
  select count(*) into l_id from user_tab_privs
   where grantee = 'CHAOS_CTL' and owner = 'SHOP'
     and ((privilege = 'EXECUTE' and table_name = 'PKG_CHAOS')
       or (privilege = 'SELECT' and table_name in ('CHAOS_RUN', 'DRIVER_CONTROL', 'CHAOS_PLAN'))
       or (privilege = 'INSERT' and table_name = 'CHAOS_PLAN'));
  ok('CHAOS_CTL holds exactly the 5 contract privileges on SHOP', n = 5 and l_id = 5, n||' held, '||l_id||' of the 5');
  select listagg(privilege, ',') within group (order by privilege) into l_txt from session_privs;
  ok('CHAOS_CTL''s only system privilege is CREATE SESSION', l_txt = 'CREATE SESSION', l_txt);

  rollback;
  :n_pass := g_pass;
  :n_fail := g_fail;
exception
  when others then
    rollback;
    dbms_output.put_line('FAIL  unexpected error: '||sqlerrm);
    :n_pass := g_pass;
    :n_fail := g_fail + 1;
end;
/

begin
  dbms_output.put_line('anom_test_chaos_ctl: '||:n_pass||' passed, '||:n_fail||' failed');
end;
/
exit :n_fail rollback
