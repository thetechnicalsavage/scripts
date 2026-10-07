-- v1.2 - SHOP in oradb1 / FREEPDB1 (the target, the demo's "production" database) for application 900, phase 4:
--        the fault injector of docs/contract.md sections 5 and 12. Tables CHAOS_RUN, CHAOS_SCRATCH and CHAOS_PLAN,
--        package PKG_CHAOS (start_scenario, run, dispatch, stop_all, restore, set_load, reap, state_problems,
--        max_minutes), the dispatcher jobs CHAOS_DISPATCH_1 and CHAOS_DISPATCH_2, the reaper job CHAOS_REAPER and
--        the grants to CHAOS_CTL. Demo only: a production database would hold none of it.
--        Run as SHOP in FREEPDB1, after 87 and 88, the password on stdin (never on a command line), from ~/app900:
--          tools/anom_chaos_run.sh stage && tools/anom_chaos_run.sh run 93_anom_chaos.sql run93
--        v1.2: (phase 4b, PLAN.md 7a: the injector must be invisible) scenarios run in two permanent dispatcher
--              sessions instead of a new scheduler job per run (each such job added a session and a logon for
--              exactly the incident window). start_scenario only queues the run (status QUEUED); dispatcher 1 runs
--              it in its own session, dispatcher 2 runs cpu_hog HIGH's second half; stop_all and the reaper set a
--              STOP flag on the run (CHAOS_RUN.stop_req) that every scenario loop reads at least every 2 s, and stop
--              a dispatcher job only when a run does not end within 30 s of it (noted on the run). New table
--              CHAOS_PLAN: the hidden-incident plan, pushed once by the observer; check constraints keep every row
--              within its scenario's maximum minutes; a dispatcher starts a due PLANNED row itself (source
--              SCHEDULE), so no observer call happens when a hidden incident begins; a due row that finds another
--              run active, or that is more than 5 minutes late, is SKIPPED. CHAOS_CTL also gets INSERT and SELECT
--              on CHAOS_PLAN. 01-Oct-2026.
--        v1.1: comments only - 26ai reports blocking_chain's lock wait timeout as ORA-00054, not ORA-30006 (seen in
--              test/anom_test_chaos.sql blocking_chain_error); CHAOS_REAPER's run log keeps every run, because the
--              default job class logs runs whatever the job's own level (01-Oct-2026). No behaviour change.
--        v1.0: first version, 01-Oct-2026.
--
--        Idempotent. Tables and indexes are created only when absent (IF NOT EXISTS); an older CHAOS_RUN gets the
--        v1.2 columns and status list (the old status check is replaced by a wider one, nothing else changes); the
--        package is replaced; the grants to CHAOS_CTL are re-stated and anything else CHAOS_CTL holds on SHOP is
--        revoked; the three jobs are created when absent, put back on their calendar when they drifted, and
--        enabled. No table, index or row is dropped.
--        A re-run is refused while a chaos run is QUEUED or RUNNING. Before the package is replaced, CHAOS_REAPER and
--        the dispatchers are disabled and waited for (a dispatcher notices within 10 s; replacing a package that a
--        session is executing would wait on that session); all three are enabled again at the end. If the script
--        fails in between, they stay disabled: run it again. The last block checks the result (both dispatchers
--        must be running) and fails the run when anything is missing or invalid.
--
--        How a run works (every time UTC, SYS_EXTRACT_UTC(SYSTIMESTAMP)):
--          start_scenario  validates, inserts CHAOS_RUN with status QUEUED, commits, returns the run id. Two unique
--                          function-based indexes allow one QUEUED or RUNNING run at a time, so two concurrent starts
--                          cannot both succeed.
--          dispatch(1|2)   the body of job CHAOS_DISPATCH_<n> (FREQ=MINUTELY at second 0, UTC). It refuses any other
--                          caller. One run lasts until HH:59:50 of its UTC hour (a run that starts in an hour's last
--                          two minutes takes the next hour too); the scheduler starts the next run at once, because
--                          a minutely job's next run date passed long ago. A session is therefore always there,
--                          whether or not a scenario runs, and is replaced once an hour at a fixed time. Every 2 s it
--                          runs one statement (the active run through its unique index, and the next due plan row
--                          through CHAOS_PLAN's index), and every 5th poll it reads its job's enabled flag (it ends
--                          within 10 s once the job is disabled). Dispatcher 1 claims a QUEUED run (RUNNING, start_ts,
--                          planned end moved by the queue delay, exec_a/sid_a = its job and session) and runs it in
--                          its own session; dispatcher 2 claims cpu_hog HIGH's second half (exec_b/sid_b). Either
--                          one starts a due PLANNED row of CHAOS_PLAN (source SCHEDULE) or SKIPS it (another run is
--                          active, or it is more than 5 minutes late). A scenario that is still running at HH:59:50
--                          moves that dispatcher's hand-over to the next whole hour, so a hand-over never follows the
--                          end of an incident. When a dispatcher run starts, a RUNNING run still marked as executed by
--                          its job belongs to the session that ended (the scheduler never runs two instances of a
--                          job): it is marked FAILED and restored (half A), or released for re-claiming (half B).
--          run             the scenario in the dispatcher's session: does the work until planned_end_ts (every loop
--                          reads CHAOS_RUN at most every 2 s and ends when the run is no longer RUNNING or its STOP
--                          flag is set), then rolls its own transaction back, restores and marks DONE (STOPPED when
--                          the flag was set). On an exception: rollback, restore, FAILED with the message; the
--                          dispatcher resets its session (work area policy, DDL wait) and goes on polling.
--          stop_all        sets the STOP flag of the active run (a QUEUED run is STOPPED at once) and waits up to 30 s
--                          for its dispatcher to end it; a run that does not end is marked STOPPED and its dispatcher
--                          job(s) are stopped (force: the session ends and its transaction, row locks included, is
--                          rolled back; the scheduler starts the dispatcher again within a minute); the stop is noted
--                          on the run. Then it restores. It names only SHOP's own two dispatcher jobs.
--          restore         idempotent: both indexes VISIBLE; PROMO_RULES rows with rule_id >= 9,000,000 deleted and
--                          the table moved online when it grew (a delete leaves the high-water mark, and every
--                          place_order scans up to it); CHAOS_SCRATCH truncated; DRIVER_CONTROL's chaos columns back
--                          to 0 / 'N' / 0. enabled and load_pct belong to set_load and are never touched by it.
--          CHAOS_REAPER    every minute at second 15: a QUEUED run nobody picked up within 3 minutes is FAILED; a
--                          RUNNING run 2 minutes past its planned end is stopped as stop_all does and restored; a
--                          RUNNING run whose executing session is gone is FAILED and restored; a plan row more than
--                          5 minutes late is SKIPPED; when nothing is active, the restored state is verified and
--                          repaired (the repair is noted on the latest run).
--        The public entry points are autonomous transactions: the observer calls them through ANOM_CHAOS_LINK, and
--        a remote function that commits in the caller's distributed transaction fails with ORA-02064.
--
--        Errors: -20101 unknown scenario; -20102 intensity not LOW/HIGH; -20103 minutes not a whole number from 2
--        to the scenario's max; -20104 another run is QUEUED or RUNNING; -20105 source not UI/SCHEDULE/TEST;
--        -20106 run() or dispatch() called outside a dispatcher job; -20107 no such run; -20108 set_load arguments;
--        -20109 the dispatcher job CHAOS_DISPATCH_1 is missing or disabled (run 93); -20110 a scenario
--        precondition is missing; -20111 restore incomplete (the reaper retries); -20112 restore refused while a
--        run is QUEUED or RUNNING (stop_all stops and restores it).
set serveroutput on size unlimited format wrapped
set verify off
set define off
set feedback off
set sqlblanklines on
set linesize 200 tab off trimout on
whenever sqlerror exit failure rollback
whenever oserror exit failure

prompt 93: checking where this runs
declare
  n number;
begin
  if sys_context('USERENV','CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '93: run in the PDB (FREEPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV','SESSION_USER') != 'SHOP' then
    raise_application_error(-20900, '93: run as SHOP, not '||sys_context('USERENV','SESSION_USER'));
  end if;
  select count(*) into n from user_tables
   where table_name in ('PRODUCTS','INVENTORY','ORDERS','ORDER_LINES','PAYMENTS','PROMO_RULES','CLICK_ARCHIVE',
                        'DRIVER_CONTROL','DAILY_SETTLEMENT');
  if n != 9 then
    raise_application_error(-20900, '93: run 87 and 88 first ('||n||' of 9 tables present)');
  end if;
  select count(*) into n from user_indexes where index_name in ('PROD_CAT_IX','ORD_CUST_IX');
  if n != 2 then
    raise_application_error(-20900, '93: indexes PROD_CAT_IX and ORD_CUST_IX are required (87)');
  end if;
  -- never replace PKG_CHAOS under an active scenario
  select count(*) into n from user_tables where table_name = 'CHAOS_RUN';
  if n = 1 then
    execute immediate 'select count(*) from CHAOS_RUN where status in (''QUEUED'', ''RUNNING'')' into n;
    if n > 0 then
      raise_application_error(-20900, '93: a chaos run is QUEUED or RUNNING; call SHOP.PKG_CHAOS.stop_all first');
    end if;
  end if;
  -- v1.0/v1.1 created one job per run
  select count(*) into n from user_scheduler_running_jobs where regexp_like(job_name, '^CHAOS_[0-9]+(_[AB])?$');
  if n > 0 then
    raise_application_error(-20900, '93: a CHAOS_<n> job is running; call SHOP.PKG_CHAOS.stop_all first');
  end if;
end;
/

-- ------------------------------------------------------------------ pause the jobs that execute PKG_CHAOS
prompt 93: pausing CHAOS_REAPER and the dispatchers (enabled again below)
declare
  e_not_running exception;
  pragma exception_init(e_not_running, -27366);   -- the run ended between the check and the stop
  type t_names is table of varchar2(30);
  l_jobs t_names := t_names('CHAOS_REAPER', 'CHAOS_DISPATCH_1', 'CHAOS_DISPATCH_2');
  n      number;
  function running (p_job varchar2, p_max_sec pls_integer) return boolean is
  begin
    for i in 0 .. p_max_sec loop
      select count(*) into n from user_scheduler_running_jobs where job_name = p_job;
      exit when n = 0 or i = p_max_sec;
      dbms_session.sleep(1);
    end loop;
    return n > 0;
  end running;
begin
  for i in 1 .. l_jobs.count loop
    select count(*) into n from user_scheduler_jobs where job_name = l_jobs(i) and enabled = 'TRUE';
    if n > 0 then
      dbms_scheduler.disable(l_jobs(i), force => true);   -- force: also while a run is active (it goes on)
      dbms_output.put_line('  '||l_jobs(i)||' disabled');
    end if;
  end loop;
  for i in 1 .. l_jobs.count loop
    -- an idle dispatcher reads its flag every 10 s; the reaper's run takes seconds
    if running(l_jobs(i), 60) then
      begin
        dbms_scheduler.stop_job(job_name => l_jobs(i), force => true);
        dbms_output.put_line('  '||l_jobs(i)||' did not end within 60 s: stopped');
      exception
        when e_not_running then null;    -- it ended by itself: the goal is reached
      end;
      if running(l_jobs(i), 30) then
        raise_application_error(-20900, '93: '||l_jobs(i)||' is still running; not replacing PKG_CHAOS under it '
                                        ||'(the jobs stay disabled: run 93 again)');
      end if;
    end if;
  end loop;
  dbms_output.put_line('  no PKG_CHAOS job runs at '||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
/

-- ------------------------------------------------------------------ tables and the one-active indexes
prompt 93: tables (created only when absent; an older CHAOS_RUN gets the v1.2 columns)

create table if not exists CHAOS_RUN (
  run_id          number generated always as identity (start with 1) not null,
  scenario        varchar2(30)   not null,
  intensity       varchar2(4)    not null,
  source          varchar2(8)    not null,
  requested_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  start_ts        timestamp,
  planned_end_ts  timestamp      not null,
  end_ts          timestamp,
  status          varchar2(8)    default 'QUEUED' not null,
  restored        char(1)        default 'N' not null,
  job_name        varchar2(128),
  note            varchar2(400),
  created_ts      timestamp      default sys_extract_utc(systimestamp) not null,
  stop_req        char(1)        default 'N' not null,
  exec_a          varchar2(30),
  sid_a           number,
  exec_b          varchar2(30),
  sid_b           number,
  constraint chaos_run_pk          primary key (run_id),
  constraint chaos_run_scenario_ck check (scenario in ('blocking_chain','plan_regression','slow_drift',
                                    'batch_wrong_time','hard_parse_storm','commit_storm','io_storm','cpu_hog',
                                    'temp_spill','conn_leak','logon_storm','app_error_burst')),
  constraint chaos_run_int_ck      check (intensity in ('LOW','HIGH')),
  constraint chaos_run_source_ck   check (source in ('UI','SCHEDULE','TEST')),
  constraint chaos_run_state_ck    check (status in ('QUEUED','RUNNING','DONE','STOPPED','FAILED')),
  constraint chaos_run_restored_ck check (restored in ('Y','N')),
  constraint chaos_run_stop_ck     check (stop_req in ('Y','N')),
  constraint chaos_run_window_ck   check (planned_end_ts > requested_ts)
);

-- a v1.0/v1.1 CHAOS_RUN: the five v1.2 columns, the default status QUEUED, and the status list with QUEUED (the new
-- check is added first, then the narrower v1.0 one is dropped: every row satisfies both)
declare
  n number;
  procedure addcol (p_col varchar2, p_def varchar2) is
  begin
    select count(*) into n from user_tab_columns where table_name = 'CHAOS_RUN' and column_name = p_col;
    if n = 0 then
      execute immediate 'alter table CHAOS_RUN add ('||p_col||' '||p_def||')';
      dbms_output.put_line('  CHAOS_RUN.'||p_col||' added');
    end if;
  end addcol;
begin
  addcol('STOP_REQ', 'char(1) default ''N'' not null');
  addcol('EXEC_A',   'varchar2(30)');
  addcol('SID_A',    'number');
  addcol('EXEC_B',   'varchar2(30)');
  addcol('SID_B',    'number');
  execute immediate 'alter table CHAOS_RUN modify (status default ''QUEUED'')';
  select count(*) into n from user_constraints where table_name = 'CHAOS_RUN' and constraint_name = 'CHAOS_RUN_STOP_CK';
  if n = 0 then
    execute immediate 'alter table CHAOS_RUN add constraint chaos_run_stop_ck check (stop_req in (''Y'',''N''))';
    dbms_output.put_line('  CHAOS_RUN: check chaos_run_stop_ck added');
  end if;
  select count(*) into n from user_constraints where table_name = 'CHAOS_RUN' and constraint_name = 'CHAOS_RUN_STATE_CK';
  if n = 0 then
    execute immediate 'alter table CHAOS_RUN add constraint chaos_run_state_ck '
                    ||'check (status in (''QUEUED'',''RUNNING'',''DONE'',''STOPPED'',''FAILED''))';
    dbms_output.put_line('  CHAOS_RUN: check chaos_run_state_ck (with QUEUED) added');
  end if;
  select count(*) into n from user_constraints where table_name = 'CHAOS_RUN' and constraint_name = 'CHAOS_RUN_STATUS_CK';
  if n > 0 then
    execute immediate 'alter table CHAOS_RUN drop constraint chaos_run_status_ck';
    dbms_output.put_line('  CHAOS_RUN: the v1.0 status check (without QUEUED) replaced by chaos_run_state_ck');
  end if;
end;
/

-- one RUNNING run (v1.0) and one QUEUED-or-RUNNING run (v1.2) at a time, enforced by the database: of two concurrent
-- starts the second gets ORA-00001. The second index is also how a dispatcher finds the active run.
create unique index if not exists CHAOS_RUN_ONE_RUNNING_UX on CHAOS_RUN (case when status = 'RUNNING' then 0 end);
create unique index if not exists CHAOS_RUN_ONE_ACTIVE_UX
  on CHAOS_RUN (case when status in ('QUEUED','RUNNING') then 0 end);

-- commit_storm's target: no key, no index (a row per commit is the point); truncated by every restore
create table if not exists CHAOS_SCRATCH (
  run_id      number         not null,
  seq         number         not null,
  pad         varchar2(40)   not null,
  created_ts  timestamp      default sys_extract_utc(systimestamp) not null
);

-- the hidden-incident plan (contract section 12): pushed once by the observer (CHAOS_CTL inserts it through
-- ANOM_CHAOS_LINK), started here by the dispatchers. The minutes check is the target's own copy of contract section
-- 5's maxima (PKG_CHAOS.max_minutes holds the same table; test/test_chaos_static.py checks that they agree).
create table if not exists CHAOS_PLAN (
  plan_id           number         not null,
  planned_start_ts  timestamp      not null,
  scenario          varchar2(30)   not null,
  intensity         varchar2(4)    not null,
  minutes           number         not null,
  status            varchar2(8)    default 'PLANNED' not null,
  run_id            number,
  acted_ts          timestamp,
  note              varchar2(400),
  created_ts        timestamp      default sys_extract_utc(systimestamp) not null,
  constraint chaos_plan_pk          primary key (plan_id),
  constraint chaos_plan_id_ck       check (plan_id > 0 and plan_id = trunc(plan_id)),
  constraint chaos_plan_scenario_ck check (scenario in ('blocking_chain','plan_regression','slow_drift',
                                     'batch_wrong_time','hard_parse_storm','commit_storm','io_storm','cpu_hog',
                                     'temp_spill','conn_leak','logon_storm','app_error_burst')),
  constraint chaos_plan_int_ck      check (intensity in ('LOW','HIGH')),
  constraint chaos_plan_status_ck   check (status in ('PLANNED','STARTED','SKIPPED')),
  constraint chaos_plan_minutes_ck  check (minutes = trunc(minutes) and minutes >= 2
                                     and minutes <= case scenario
                                                       when 'blocking_chain'   then 30
                                                       when 'plan_regression'  then 60
                                                       when 'slow_drift'       then 240
                                                       when 'batch_wrong_time' then 30
                                                       when 'hard_parse_storm' then 30
                                                       when 'commit_storm'     then 30
                                                       when 'io_storm'         then 30
                                                       when 'cpu_hog'          then 30
                                                       when 'temp_spill'       then 30
                                                       when 'conn_leak'        then 60
                                                       when 'logon_storm'      then 30
                                                       when 'app_error_burst'  then 60
                                                     end)
);
-- the dispatchers' "next due row": only PLANNED rows are in this index (the others map to null)
create index if not exists CHAOS_PLAN_DUE_IX on CHAOS_PLAN (case when status = 'PLANNED' then planned_start_ts end);

-- ------------------------------------------------------------------ PKG_CHAOS
prompt 93: package PKG_CHAOS
create or replace package PKG_CHAOS authid definer as
  -- v1.2 - the demo's fault injector (docs/contract.md sections 5 and 12; the header of 93_anom_chaos.sql explains
  --        the design and lists the error codes -20101 .. -20112). Definer rights: CHAOS_CTL holds EXECUTE on this
  --        package, SELECT on CHAOS_RUN and DRIVER_CONTROL, INSERT and SELECT on CHAOS_PLAN, nothing else.
  --        Every time is UTC.

  -- Queues one scenario: p_name one of the 12 names (max_minutes), p_minutes a whole number from 2 to the
  -- scenario's max, p_intensity LOW or HIGH, p_source UI, SCHEDULE or TEST. Returns the run id; dispatcher 1 starts
  -- it within about 2 s. Refused while another run is QUEUED or RUNNING (-20104). Commits (autonomous transaction).
  function start_scenario (p_name varchar2, p_minutes number, p_intensity varchar2 default 'LOW',
                           p_source varchar2 default 'UI') return number;

  -- A claimed run's scenario, in the dispatcher's own session; refuses any other caller (-20106).
  procedure run (p_run_id number);

  -- Sets the STOP flag of the active run, waits for it to end (a run that does not end within 30 s is stopped with
  -- its dispatcher job), restores. -20111 if the restore is incomplete (the reaper retries every minute).
  procedure stop_all (p_note varchar2 default null);

  -- Restores the target after run p_run_id (idempotent) and records the result on the run. -20107 no such run,
  -- -20112 while a run is QUEUED or RUNNING, -20111 incomplete.
  procedure restore (p_run_id number);

  -- The Control Center's load switch: DRIVER_CONTROL.enabled ('Y'/'N') and load_pct (0 to 200).
  procedure set_load (p_enabled varchar2, p_load_pct number);

  -- The body of job CHAOS_REAPER (every minute).
  procedure reap;

  -- null when the target is in its restored state, else what is not (read-only).
  function state_problems return varchar2;

  -- the scenario's maximum minutes, null for a name that is not one of the 12 (read-only).
  function max_minutes (p_name varchar2) return number;

  -- v1.2: the body of job CHAOS_DISPATCH_<p_slot> (1 or 2); refuses any other caller (-20106).
  procedure dispatch (p_slot pls_integer);
end PKG_CHAOS;
/

create or replace package body PKG_CHAOS as
  -- v1.2 - see the specification and the header of 93_anom_chaos.sql.

  c_owner        constant varchar2(30) := 'SHOP';
  c_disp1        constant varchar2(30) := 'CHAOS_DISPATCH_1';
  c_disp2        constant varchar2(30) := 'CHAOS_DISPATCH_2';
  c_rule_base    constant number       := 9000000;  -- slow_drift's PROMO_RULES rows: rule_id >= this, only them
  c_promo_blocks constant pls_integer  := 16;       -- PROMO_RULES is one 8-block extent when compact (300 rules)
  c_ddl_wait     constant pls_integer  := 30;       -- seconds a DDL waits for the driver's open transactions
  c_wrap         constant number       := 4294967296;   -- DBMS_UTILITY.GET_TIME wraps at 2^32
  c_poll_cs      constant pls_integer  := 200;      -- a dispatcher polls, and a scenario reads its run, every 2 s
  c_flag_every   constant pls_integer  := 5;        -- ... and every 5th time also reads its job's enabled flag
  c_stop_wait    constant pls_integer  := 30;       -- seconds stop_all and the reaper wait on a run's STOP flag
  c_late_min     constant pls_integer  := 5;        -- a plan row later than this is SKIPPED, never started late
  c_queue_min    constant pls_integer  := 3;        -- a QUEUED run nobody claimed within this is FAILED (reaper)

  e_busy         exception;
  pragma exception_init(e_busy, -54);
  e_not_running  exception;
  pragma exception_init(e_not_running, -27366);

  -- session state: the dispatcher (set by dispatch) and the run it executes (set by run)
  g_job          varchar2(30);
  g_run_id       number;
  g_label        varchar2(60);     -- "<scenario> <intensity>" (+ " half B" for cpu_hog HIGH's second half)
  g_checked_at   number;           -- DBMS_UTILITY.GET_TIME of the last CHAOS_RUN read
  g_alive        boolean := false;
  g_reads        pls_integer := 0; -- CHAOS_RUN reads by alive(), for the job-flag cadence

  type t_names is table of varchar2(30);

  -- ---------------------------------------------------------------- small helpers
  function utc_now return timestamp is
  begin
    return sys_extract_utc(systimestamp);
  end utc_now;

  function secs (p_from timestamp, p_to timestamp) return number is
    d interval day(9) to second(6) := p_to - p_from;
  begin
    return extract(day from d) * 86400 + extract(hour from d) * 3600 + extract(minute from d) * 60
           + extract(second from d);
  end secs;

  -- centiseconds since p_t0 (a GET_TIME value), correct across the counter's wrap
  function elapsed_cs (p_t0 number) return number is
  begin
    return mod(dbms_utility.get_time - p_t0 + c_wrap, c_wrap);
  end elapsed_cs;

  function ts_text (p timestamp) return varchar2 is
  begin
    return to_char(p, 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
  end ts_text;

  procedure ddl_wait (p_seconds pls_integer) is
  begin
    execute immediate 'alter session set ddl_lock_timeout = '||to_char(p_seconds);
  end ddl_wait;

  -- this session's end: kill, cancel, shutdown (never handled as a scenario failure)
  function is_stop (p_code number) return boolean is
  begin
    return p_code in (-28, -31, -1013, -1089, -1092);
  end is_stop;

  function max_minutes (p_name varchar2) return number is
  begin
    return case p_name
             when 'blocking_chain'   then 30
             when 'plan_regression'  then 60
             when 'slow_drift'       then 240
             when 'batch_wrong_time' then 30
             when 'hard_parse_storm' then 30
             when 'commit_storm'     then 30
             when 'io_storm'         then 30
             when 'cpu_hog'          then 30
             when 'temp_spill'       then 30
             when 'conn_leak'        then 60
             when 'logon_storm'      then 30
             when 'app_error_burst'  then 60
           end;
  end max_minutes;

  -- the job flag of this session's dispatcher (read on the dispatcher's cadence, also while a scenario runs, so the
  -- session's statements are the same whether or not it executes a scenario)
  function job_enabled return boolean is
    l_on varchar2(5);
  begin
    select max(enabled) into l_on from user_scheduler_jobs where job_name = g_job;
    return l_on = 'TRUE';
  end job_enabled;

  -- ---------------------------------------------------------------- the run's clock (dispatcher session only)
  -- true while the run is RUNNING, its STOP flag unset and before its planned end; reads CHAOS_RUN at most every 2 s
  -- unless p_now
  function alive (p_now boolean default false) return boolean is
    l_status varchar2(8);
    l_stop   char(1);
    l_end    timestamp;
    l_flag   boolean;
  begin
    if not p_now and g_checked_at is not null and elapsed_cs(g_checked_at) < c_poll_cs then
      return g_alive;
    end if;
    select status, stop_req, planned_end_ts into l_status, l_stop, l_end from CHAOS_RUN where run_id = g_run_id;
    g_alive := l_status = 'RUNNING' and l_stop = 'N' and utc_now < l_end;
    g_checked_at := dbms_utility.get_time;
    g_reads := g_reads + 1;
    if mod(g_reads, c_flag_every) = 0 and g_job is not null then
      l_flag := job_enabled;   -- read for the cadence only: a run is never cut short by its dispatcher's flag
    end if;
    return g_alive;
  end alive;

  -- sleeps p_seconds in steps of at most a second; returns at once when the run ends
  procedure nap (p_seconds number) is
    l_until timestamp := utc_now + numtodsinterval(greatest(nvl(p_seconds, 0), 0), 'SECOND');
    l_left  number;
  begin
    loop
      exit when not alive;
      l_left := secs(utc_now, l_until);
      exit when l_left <= 0;
      dbms_session.sleep(least(l_left, 1));
    end loop;
  end nap;

  -- keeps whatever the scenario set up in place until the run ends
  procedure hold is
  begin
    while alive loop
      dbms_session.sleep(1);
    end loop;
  end hold;

  -- the run's progress on CHAOS_RUN.note, visible at once (autonomous: never commits the run's own work, so
  -- blocking_chain keeps its row locks). Once the STOP flag is set the note keeps the stop's text.
  procedure progress (p_text varchar2) is
    pragma autonomous_transaction;
  begin
    update CHAOS_RUN set note = substr(g_label||': '||p_text, 1, 400)
     where run_id = g_run_id and status = 'RUNNING' and stop_req = 'N';
    commit;
  end progress;

  -- DRIVER_CONTROL's chaos columns (the driver reads the row every 10 s); a null argument keeps the column
  procedure control (p_leak number default null, p_storm varchar2 default null, p_err number default null) is
    l_now timestamp := utc_now;      -- (a private function cannot be called inside SQL)
  begin
    update DRIVER_CONTROL
       set conn_leak_target = nvl(p_leak, conn_leak_target),
           logon_storm      = nvl(p_storm, logon_storm),
           error_pct        = nvl(p_err, error_pct),
           updated_ts       = l_now
     where id = 1;
    if sql%rowcount = 0 then
      raise_application_error(-20110, 'DRIVER_CONTROL row 1 is missing (run 87)');
    end if;
    commit;
  end control;

  -- p_index is one of the two names below (never input); retried when the DDL lock wait times out
  procedure set_visible (p_index varchar2, p_visible boolean) is
  begin
    if p_index not in ('PROD_CAT_IX', 'ORD_CUST_IX') then
      raise_application_error(-20110, 'set_visible: not a scenario index: '||p_index);
    end if;
    for i in 1 .. 3 loop
      begin
        execute immediate 'alter index '||c_owner||'.'||p_index
                          ||case when p_visible then ' visible' else ' invisible' end;
        return;
      exception
        when e_busy then
          if i = 3 then
            raise;
          end if;
          dbms_session.sleep(2);
      end;
    end loop;
  end set_visible;

  -- ---------------------------------------------------------------- the 12 scenarios (dispatcher session)
  -- S1: the run takes the row locks of hot products 1..n one row at a time, in product-id order (PKG_SHOP's own
  --     order, so it cannot deadlock with an order), and holds them; orders for those products queue behind it.
  --     LOW 5 products, 15 s of every 60; HIGH 25 products, continuously. A row it cannot get within 10 s fails
  --     the run (ORA-30006 in the manual; 26ai reports it as ORA-00054), and run() then rolls back: no lock
  --     outlives the run.
  procedure do_blocking_chain (p_high boolean) is
    l_n     pls_integer := case when p_high then 25 else 5 end;
    l_pid   number;
    l_cycle pls_integer := 0;
    l_t0    timestamp;
  begin
    loop
      exit when not alive(true);
      l_t0    := utc_now;
      l_cycle := l_cycle + 1;
      for p in 1 .. l_n loop
        select product_id into l_pid from INVENTORY where product_id = p for update wait 10;
      end loop;
      progress('cycle '||l_cycle||' holds the row locks of hot products 1-'||l_n||' since '||ts_text(l_t0));
      if p_high then
        hold;
      else
        nap(15 - secs(l_t0, utc_now));
      end if;
      rollback;                                      -- releases the rows
      exit when p_high;
      nap(60 - secs(l_t0, utc_now));
    end loop;
    rollback;
  end do_blocking_chain;

  -- S2: an index made invisible: LOW PROD_CAT_IX (browse and search), HIGH ORD_CUST_IX (order_status)
  procedure do_plan_regression (p_high boolean) is
    l_index varchar2(30) := case when p_high then 'ORD_CUST_IX' else 'PROD_CAT_IX' end;
  begin
    ddl_wait(c_ddl_wait);
    set_visible(l_index, false);
    ddl_wait(0);
    progress(l_index||' INVISIBLE since '||ts_text(utc_now)
             ||case when p_high then ' (order_status loses its index)' else ' (browse and search lose their index)' end);
    hold;
  end do_plan_regression;

  -- S3: PROMO_RULES grows every minute, linearly (LOW +40,000 rows over the run, HIGH +200,000); every
  --     place_order scans the whole table. The rows are expired rules (valid_to 2000-01-01): scanned, never
  --     matched, so no order total changes. rule_id >= 9,000,000, so the restore deletes exactly them.
  procedure do_slow_drift (p_high boolean) is
    l_total   number := case when p_high then 200000 else 40000 end;
    l_t0      timestamp;
    l_end     timestamp;
    l_minutes number;
    l_base    number;
    l_done    number := 0;
    l_want    number;
    l_k       pls_integer := 0;
  begin
    select start_ts, planned_end_ts into l_t0, l_end from CHAOS_RUN where run_id = g_run_id;
    l_minutes := greatest(1, round(secs(l_t0, l_end) / 60));
    -- continue above any row left behind (a restore removes them; so does the reaper)
    select nvl(max(rule_id), c_rule_base) into l_base from PROMO_RULES where rule_id >= c_rule_base;
    loop
      exit when not alive(true);
      l_k    := l_k + 1;
      l_want := least(l_total, round(l_total * l_k / l_minutes));
      if l_want > l_done then
        insert into PROMO_RULES (rule_id, category_id, min_total, discount_pct, valid_to)
        select l_base + l_done + level, mod(l_done + level - 1, 50) + 1, 25 * (1 + mod(l_done + level, 20)), 5,
               date '2000-01-01'
          from dual connect by level <= l_want - l_done;
        commit;
        l_done := l_want;
      end if;
      progress('minute '||l_k||' of '||l_minutes||': '||l_done||' expired rules added to PROMO_RULES (rule_id '
               ||(l_base + 1)||'-'||(l_base + l_done)||')');
      nap(60 * l_k - secs(l_t0, utc_now));
    end loop;
  end do_slow_drift;

  -- S4: the nightly settlement's work, now, for days that are settled already (LOW the latest settled day, HIGH
  --     the latest 7): the bulk update of the day's payments ('Y' stays 'Y': the scans, row locks, undo, redo and
  --     block changes are the real batch's), then PKG_SHOP.settle(day), which recomputes DAILY_SETTLEMENT with
  --     the same values and runs the day's report. Passes repeat until the end; LOW pauses at least 30 s (and at
  --     least as long as the pass) between passes, HIGH does not pause.
  procedure do_batch_wrong_time (p_high boolean) is
    type t_days is table of date;
    l_days  t_days;
    l_day   date;
    l_from  timestamp;
    l_to    timestamp;
    l_pass  pls_integer := 0;
    l_rows  number := 0;
    l_t0    timestamp;
    l_secs  number;
  begin
    select settle_date bulk collect into l_days
      from (select settle_date from DAILY_SETTLEMENT order by settle_date desc)
     where rownum <= case when p_high then 7 else 1 end;
    if l_days.count = 0 then
      raise_application_error(-20110, 'batch_wrong_time: DAILY_SETTLEMENT holds no settled day');
    end if;
    loop
      exit when not alive(true);
      l_pass := l_pass + 1;
      l_t0   := utc_now;
      for i in 1 .. l_days.count loop
        exit when not alive;
        l_day  := l_days(i);
        l_from := cast(l_day as timestamp);
        l_to   := l_from + interval '1' day;
        update PAYMENTS set settled = 'Y'
         where paid_ts >= l_from and paid_ts < l_to
           and settled = 'Y';
        l_rows := l_rows + sql%rowcount;
        commit;
        PKG_SHOP.settle(l_day);
      end loop;
      l_secs := secs(l_t0, utc_now);
      progress('pass '||l_pass||' over '||l_days.count||' settled day(s) '||to_char(l_days(l_days.count), 'YYYY-MM-DD')
               ||' to '||to_char(l_days(1), 'YYYY-MM-DD')||': '||l_rows||' payment rows re-updated so far, last pass '
               ||to_char(round(l_secs, 1), 'fm9999990.0')||' s');
      if not p_high then
        nap(greatest(l_secs, 30));
      end if;
    end loop;
  end do_batch_wrong_time;

  -- S5: dynamic SQL with literals in a loop. Every text is new (the literals and the run id differ), so every
  --     statement is a hard parse (cursor_sharing is EXACT). LOW 20 statements a second, HIGH unthrottled.
  procedure do_hard_parse_storm (p_high boolean) is
    l_n    number := 0;
    l_x    number;
    l_t    number;
    l_last number := dbms_utility.get_time;
  begin
    loop
      exit when not alive;
      l_t := dbms_utility.get_time;
      for i in 1 .. 20 loop
        l_n := l_n + 1;
        execute immediate 'select /* chaos hard_parse_storm */ count(*) from PRODUCTS where product_id = '
                          ||to_char(mod(l_n * 7919, 20000) + 1)||' and price < '||to_char(l_n)
                          ||' and category_id > -'||to_char(g_run_id)
          into l_x;
      end loop;
      if elapsed_cs(l_last) >= 1000 then
        progress(l_n||' literal statements so far');
        l_last := dbms_utility.get_time;
      end if;
      if not p_high then
        dbms_session.sleep(greatest(0, 1 - least(elapsed_cs(l_t), 100) / 100));
      end if;
    end loop;
    progress(l_n||' literal statements in all');
  end do_hard_parse_storm;

  -- S6: a row per transaction into CHAOS_SCRATCH, each with a synchronous commit (a plain COMMIT inside PL/SQL
  --     does not wait for the log writer; COMMIT WRITE IMMEDIATE WAIT does). LOW ~50 commits a second, HIGH
  --     unthrottled.
  procedure do_commit_storm (p_high boolean) is
    l_n    number := 0;
    l_t    number;
    l_last number := dbms_utility.get_time;
  begin
    loop
      exit when not alive;
      l_t := dbms_utility.get_time;
      for i in 1 .. 50 loop
        l_n := l_n + 1;
        insert into CHAOS_SCRATCH (run_id, seq, pad) values (g_run_id, l_n, 'commit_storm');
        commit write immediate wait;
      end loop;
      if elapsed_cs(l_last) >= 1000 then
        progress(l_n||' rows committed one by one so far');
        l_last := dbms_utility.get_time;
      end if;
      if not p_high then
        dbms_session.sleep(greatest(0, 1 - least(elapsed_cs(l_t), 100) / 100));
      end if;
    end loop;
    progress(l_n||' rows committed one by one in all');
  end do_commit_storm;

  -- S7: full scans of CLICK_ARCHIVE (about 1.3 GB, larger than the 512 MB buffer cache, so Oracle reads it with
  --     direct path reads: physical I/O each time) on a column no index holds. LOW one scan, then a pause as
  --     long as the scan; HIGH back to back.
  procedure do_io_storm (p_high boolean) is
    l_x     number;
    l_scans pls_integer := 0;
    l_t0    timestamp;
    l_secs  number;
  begin
    loop
      exit when not alive(true);
      l_t0 := utc_now;
      select /*+ full(c) no_parallel(c) */ count(*) into l_x from CLICK_ARCHIVE c where c.user_agent like 'chaos%';
      l_scans := l_scans + 1;
      l_secs  := secs(l_t0, utc_now);
      progress(l_scans||' full scans of CLICK_ARCHIVE so far, the last took '||to_char(round(l_secs, 1), 'fm9999990.0')||' s');
      if not p_high then
        nap(greatest(l_secs, 1));
      end if;
    end loop;
  end do_io_storm;

  -- S8: PL/SQL arithmetic, no SQL and no wait. LOW one session, one second busy and one idle (50% duty cycle);
  --     HIGH two sessions (dispatcher 1 and, for the second half, dispatcher 2), continuously.
  procedure do_cpu_hog (p_high boolean) is
    l_x    number := 1;
    l_t    number;
    l_busy pls_integer := 0;
    l_idle pls_integer := 0;
  begin
    loop
      exit when not alive;
      l_t := dbms_utility.get_time;
      while elapsed_cs(l_t) < 100 loop
        for i in 1 .. 20000 loop
          l_x := mod(l_x * 31 + i, 1000003);
        end loop;
      end loop;
      l_busy := l_busy + 1;
      if not p_high then
        dbms_session.sleep(1);
        l_idle := l_idle + 1;
      end if;
      if mod(l_busy, 10) = 0 then
        progress(l_busy||' s busy, '||l_idle||' s idle');
      end if;
    end loop;
  end do_cpu_hog;

  -- S9: this session only gets a manual work area of 64 KB, so the sort and the join of a large query over
  --     ORDERS and ORDER_LINES (100,000 history orders, about 300,000 lines, a different slice each time) spill
  --     to TEMP. LOW one query, then a pause at least as long (10 s at least); HIGH back to back. (The dispatcher
  --     puts the work area policy back to AUTO after every run.)
  procedure do_temp_spill (p_high boolean) is
    l_x    number;
    l_q    pls_integer := 0;
    l_lo   number;
    l_t0   timestamp;
    l_secs number;
  begin
    execute immediate 'alter session set workarea_size_policy = manual';
    execute immediate 'alter session set sort_area_size = 65536';
    execute immediate 'alter session set hash_area_size = 65536';
    loop
      exit when not alive(true);
      l_q  := l_q + 1;
      l_t0 := utc_now;
      l_lo := 1 + mod((l_q - 1) * 100000, 900000);
      select /* chaos temp_spill */ count(*) into l_x
        from (select o.customer_id, l.product_id,
                     row_number() over (partition by o.customer_id order by l.qty * l.price desc, l.product_id) rn
                from ORDERS o
                join ORDER_LINES l on l.order_id = o.order_id
               where o.order_id between l_lo and l_lo + 99999
                 and l.order_id between l_lo and l_lo + 99999)
       where rn = 1;
      l_secs := secs(l_t0, utc_now);
      progress(l_q||' spilling queries so far, the last took '||to_char(round(l_secs, 1), 'fm9999990.0')||' s');
      if not p_high then
        nap(greatest(l_secs, 10));
      end if;
    end loop;
  end do_temp_spill;

  -- S10: the driver opens and holds DRIVER_CONTROL.conn_leak_target extra sessions (LOW 10, HIGH 40)
  procedure do_conn_leak (p_high boolean) is
    l_n pls_integer := case when p_high then 40 else 10 end;
  begin
    control(p_leak => l_n);
    progress('DRIVER_CONTROL.conn_leak_target = '||l_n||' since '||ts_text(utc_now));
    hold;
  end do_conn_leak;

  -- S11: the driver connects and disconnects for every transaction while DRIVER_CONTROL.logon_storm = 'Y'
  --      (LOW 30 s of every 60, HIGH throughout)
  procedure do_logon_storm (p_high boolean) is
    l_t0    timestamp;
    l_cycle pls_integer := 0;
  begin
    if p_high then
      control(p_storm => 'Y');
      progress('DRIVER_CONTROL.logon_storm = Y since '||ts_text(utc_now));
      hold;
    else
      loop
        exit when not alive(true);
        l_t0    := utc_now;
        l_cycle := l_cycle + 1;
        control(p_storm => 'Y');
        progress('cycle '||l_cycle||': DRIVER_CONTROL.logon_storm = Y from '||ts_text(l_t0)||' for 30 s');
        nap(30 - secs(l_t0, utc_now));
        control(p_storm => 'N');
        nap(60 - secs(l_t0, utc_now));
      end loop;
    end if;
  end do_logon_storm;

  -- S12: a share of the driver's orders carries an unknown product and fails with -20001 (LOW 3%, HIGH 15%)
  procedure do_app_error_burst (p_high boolean) is
    l_pct number := case when p_high then 15 else 3 end;
  begin
    control(p_err => l_pct);
    progress('DRIVER_CONTROL.error_pct = '||l_pct||' since '||ts_text(utc_now));
    hold;
  end do_app_error_burst;

  -- ---------------------------------------------------------------- state, restore, bookkeeping
  function state_problems return varchar2 is
    l varchar2(4000);
    n number;
    procedure flag (p varchar2) is
    begin
      l := substr(l||case when l is not null then '; ' end||p, 1, 4000);
    end flag;
  begin
    select count(*) into n from user_indexes where index_name in ('PROD_CAT_IX','ORD_CUST_IX');
    if n != 2 then
      flag('PROD_CAT_IX or ORD_CUST_IX is missing');
    end if;
    for i in (select index_name, visibility from user_indexes
               where index_name in ('PROD_CAT_IX','ORD_CUST_IX') and visibility != 'VISIBLE') loop
      flag(i.index_name||' is '||i.visibility);
    end loop;
    select count(*) into n from PROMO_RULES where rule_id >= c_rule_base;
    if n > 0 then
      flag(n||' slow_drift rows in PROMO_RULES');
    end if;
    select nvl(sum(blocks), 0) into n from user_segments where segment_name = 'PROMO_RULES';
    if n > c_promo_blocks then
      flag('PROMO_RULES segment holds '||n||' blocks (8 when compact)');
    end if;
    select count(*) into n from CHAOS_SCRATCH where rownum = 1;
    if n > 0 then
      flag('CHAOS_SCRATCH is not empty');
    end if;
    select count(*) into n from DRIVER_CONTROL
     where id = 1 and conn_leak_target = 0 and logon_storm = 'N' and error_pct = 0;
    if n = 0 then
      flag('DRIVER_CONTROL chaos columns are not at their defaults');
    end if;
    return l;
  end state_problems;

  -- Puts back everything a scenario can change. Idempotent. Returns null when the target is clean afterwards,
  -- else what is still wrong. p_in_job: the run's own session calls it, and its open transaction (blocking_chain's
  -- row locks, any half-done work) is rolled back first.
  function restore_state (p_in_job boolean) return varchar2 is
    l_errors varchar2(4000);
    n        number;
    l_del    number := 0;
    l_now    timestamp := utc_now;
    procedure err (p varchar2) is
    begin
      l_errors := substr(l_errors||case when l_errors is not null then '; ' end||p, 1, 4000);
    end err;
  begin
    if p_in_job then
      rollback;
    end if;
    ddl_wait(c_ddl_wait);
    -- 1. both indexes visible (plan_regression)
    for i in (select index_name from user_indexes
               where index_name in ('PROD_CAT_IX','ORD_CUST_IX') and visibility != 'VISIBLE') loop
      begin
        set_visible(i.index_name, true);
      exception
        when others then
          err(i.index_name||': '||sqlerrm);
      end;
    end loop;
    -- 2. slow_drift's rows (exactly rule_id >= 9,000,000), in bounded batches; then the table compacted, because
    --    a delete leaves the high-water mark where it was and every place_order scans up to it
    begin
      loop
        delete from PROMO_RULES where rule_id >= c_rule_base and rownum <= 50000;
        n := sql%rowcount;
        commit;
        l_del := l_del + n;
        exit when n < 50000;
      end loop;
      select nvl(sum(blocks), 0) into n from user_segments where segment_name = 'PROMO_RULES';
      if l_del > 0 or n > c_promo_blocks then
        execute immediate 'alter table '||c_owner||'.PROMO_RULES move online';
      end if;
    exception
      when others then
        rollback;
        err('PROMO_RULES: '||sqlerrm);
    end;
    -- 3. CHAOS_SCRATCH emptied (commit_storm)
    begin
      select count(*) into n from CHAOS_SCRATCH where rownum = 1;
      if n > 0 then
        execute immediate 'truncate table '||c_owner||'.CHAOS_SCRATCH';
      end if;
    exception
      when others then
        err('CHAOS_SCRATCH: '||sqlerrm);
    end;
    -- 4. DRIVER_CONTROL's chaos columns to their defaults (enabled and load_pct are set_load's: not touched)
    begin
      update DRIVER_CONTROL
         set conn_leak_target = 0, logon_storm = 'N', error_pct = 0, updated_ts = l_now
       where id = 1 and (conn_leak_target != 0 or logon_storm != 'N' or error_pct != 0);
      commit;
    exception
      when others then
        rollback;
        err('DRIVER_CONTROL: '||sqlerrm);
    end;
    ddl_wait(0);
    return coalesce(l_errors, state_problems);
  exception
    when others then
      ddl_wait(0);
      raise;
  end restore_state;

  -- ends an active run (no-op for a run that ended already); p_text null keeps the note as it is; the caller commits
  procedure mark_end (p_run_id number, p_status varchar2, p_text varchar2) is
    l_now timestamp := utc_now;
  begin
    update CHAOS_RUN
       set status = p_status,
           end_ts = l_now,
           note   = case when p_text is null then note
                         else substr(p_text||case when note is not null then ' | '||note end, 1, 400) end
     where run_id = p_run_id and status in ('QUEUED', 'RUNNING');
  end mark_end;

  -- records a restore's outcome on the run; the caller commits
  procedure mark_restored (p_run_id number, p_problems varchar2) is
  begin
    update CHAOS_RUN
       set restored = case when p_problems is null then 'Y' else 'N' end,
           note     = case when p_problems is null then note
                           else substr('restore incomplete: '||p_problems||' | '||note, 1, 400) end
     where run_id = p_run_id;
  end mark_restored;

  -- is the session that claimed half A (or B) still running its dispatcher job? (USER_SCHEDULER_RUNNING_JOBS: only
  -- SHOP's own jobs can be named)
  function executor_alive (p_job varchar2, p_sid number) return boolean is
    n number;
  begin
    if p_job is null or p_sid is null then
      return false;
    end if;
    select count(*) into n from user_scheduler_running_jobs where job_name = p_job and session_id = p_sid;
    return n > 0;
  end executor_alive;

  -- stops one of SHOP's two dispatcher jobs (force: the session ends, its transaction rolls back; the scheduler
  -- starts the job again) and waits up to 30 s for it to end. Never names any other job.
  procedure stop_dispatcher (p_job varchar2) is
    n number;
  begin
    if p_job not in (c_disp1, c_disp2) then
      raise_application_error(-20110, 'stop_dispatcher: not a dispatcher job: '||substr(p_job, 1, 40));
    end if;
    begin
      dbms_scheduler.stop_job(job_name => c_owner||'.'||p_job, force => true);
    exception
      when e_not_running then
        null;   -- it ended on its own meanwhile: what this procedure wants
    end;
    for i in 1 .. 30 loop
      select count(*) into n from user_scheduler_running_jobs where job_name = p_job;
      exit when n = 0;
      dbms_session.sleep(1);
    end loop;
  end stop_dispatcher;

  -- Ends the active run p_id: a QUEUED run is STOPPED at once; a RUNNING one gets its STOP flag (and p_note, which
  -- stays the note's head) and its dispatcher is given c_stop_wait seconds to end and restore it. A run that is still
  -- RUNNING then is marked STOPPED; when its executor is still alive (a hung scenario) its dispatcher job(s) are
  -- stopped, which is noted on the run. The caller restores and records the restore. Commits.
  procedure end_active (p_id number, p_note varchar2) is
    r      CHAOS_RUN%rowtype;
    l_now  timestamp := utc_now;
    l_jobs varchar2(200);
  begin
    update CHAOS_RUN
       set status   = case when status = 'QUEUED' then 'STOPPED' else status end,
           end_ts   = case when status = 'QUEUED' then l_now else end_ts end,
           stop_req = 'Y',
           note     = substr(p_note||case when note is not null then ' | '||note end, 1, 400)
     where run_id = p_id and status in ('QUEUED', 'RUNNING');
    commit;
    for i in 1 .. c_stop_wait loop
      select * into r from CHAOS_RUN where run_id = p_id;
      exit when r.status != 'RUNNING' or not executor_alive(r.exec_a, r.sid_a);
      dbms_session.sleep(1);
    end loop;
    select * into r from CHAOS_RUN where run_id = p_id;     -- as it is now, after the last wait
    if r.status = 'RUNNING' then
      if executor_alive(r.exec_a, r.sid_a) then
        stop_dispatcher(r.exec_a);
        l_jobs := r.exec_a;
      end if;
      if executor_alive(r.exec_b, r.sid_b) then
        stop_dispatcher(r.exec_b);
        l_jobs := l_jobs||case when l_jobs is not null then ' and ' end||r.exec_b;
      end if;
      mark_end(p_id, 'STOPPED', case when l_jobs is not null then
                                  'dispatcher job '||l_jobs||' stopped '||ts_text(utc_now)||' (the run did not end within '
                                  ||c_stop_wait||' s of its STOP flag; the scheduler starts it again)' end);
      commit;
    end if;
  end end_active;

  -- validates and queues a run (status QUEUED, planned end = request + p_minutes); no commit
  function enqueue (p_name varchar2, p_minutes number, p_intensity varchar2, p_source varchar2) return number is
    l_name   varchar2(100) := lower(trim(substr(p_name, 1, 100)));
    l_int    varchar2(100) := upper(trim(substr(p_intensity, 1, 100)));
    l_src    varchar2(100) := upper(trim(substr(p_source, 1, 100)));
    l_max    number;
    l_busy   number;
    l_id     number;
    l_now    timestamp := utc_now;
    n        number;
  begin
    l_max := max_minutes(l_name);
    if l_max is null then
      raise_application_error(-20101, 'start_scenario: unknown scenario "'||substr(p_name, 1, 40)||'"; one of '
        ||'blocking_chain, plan_regression, slow_drift, batch_wrong_time, hard_parse_storm, commit_storm, io_storm, '
        ||'cpu_hog, temp_spill, conn_leak, logon_storm, app_error_burst');
    end if;
    if l_int is null or l_int not in ('LOW', 'HIGH') then
      raise_application_error(-20102, 'start_scenario: intensity must be LOW or HIGH');
    end if;
    if p_minutes is null or p_minutes != trunc(p_minutes) or p_minutes < 2 or p_minutes > l_max then
      raise_application_error(-20103, 'start_scenario: minutes for '||l_name||' must be a whole number from 2 to '||l_max);
    end if;
    if l_src is null or l_src not in ('UI', 'SCHEDULE', 'TEST') then
      raise_application_error(-20105, 'start_scenario: source must be UI, SCHEDULE or TEST');
    end if;
    select max(run_id) into l_busy from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
    if l_busy is not null then
      raise_application_error(-20104, 'start_scenario: run '||l_busy||' is QUEUED or RUNNING; one run at a time '
                                      ||'(stop_all ends it)');
    end if;
    select count(*) into n from user_scheduler_jobs where job_name = c_disp1 and enabled = 'TRUE';
    if n = 0 then
      raise_application_error(-20109, 'start_scenario: the dispatcher job '||c_disp1||' is missing or disabled; run 93');
    end if;
    begin
      insert into CHAOS_RUN (scenario, intensity, source, requested_ts, start_ts, planned_end_ts, status, restored,
                             stop_req)
      values (l_name, l_int, l_src, l_now, null, l_now + numtodsinterval(p_minutes, 'MINUTE'), 'QUEUED', 'N', 'N')
      returning run_id into l_id;
    exception
      when dup_val_on_index then
        raise_application_error(-20104, 'start_scenario: another run started at the same moment; one run at a time');
    end;
    return l_id;
  end enqueue;

  -- ---------------------------------------------------------------- the dispatcher's steps
  -- the end of a dispatcher run that starts at p_t: HH:59:50 of p_t's hour, or of the next hour when p_t is in the
  -- last two minutes of its hour
  function next_end (p_t timestamp) return timestamp is
  begin
    return cast(trunc(cast(p_t + interval '2' minute as date), 'HH') as timestamp) + interval '1' hour
           - interval '10' second;
  end next_end;

  -- claims half A (dispatcher 1: a QUEUED run) or half B (dispatcher 2: cpu_hog HIGH's second session); commits
  function claim (p_run_id number, p_half varchar2, p_sid number) return boolean is
    l_now timestamp := utc_now;
    n     number;
  begin
    if p_half = 'A' then
      -- the run's clock starts now: the planned end moves by the time it spent queued
      update CHAOS_RUN
         set status = 'RUNNING', start_ts = l_now, planned_end_ts = l_now + (planned_end_ts - requested_ts),
             exec_a = g_job, sid_a = p_sid, job_name = g_job
       where run_id = p_run_id and status = 'QUEUED' and stop_req = 'N';
    else
      update CHAOS_RUN
         set exec_b = g_job, sid_b = p_sid, job_name = substr(job_name||','||g_job, 1, 128)
       where run_id = p_run_id and status = 'RUNNING' and stop_req = 'N' and exec_b is null
         and scenario = 'cpu_hog' and intensity = 'HIGH';
    end if;
    n := sql%rowcount;
    commit;
    return n = 1;
  end claim;

  -- after a run (whatever happened): nothing pending, the session's settings as a dispatcher has them
  procedure reset_session (p_slot pls_integer) is
  begin
    rollback;
    execute immediate 'alter session set workarea_size_policy = auto';   -- temp_spill set it to MANUAL
    ddl_wait(0);
    g_run_id     := null;
    g_label      := null;
    g_checked_at := null;
    g_alive      := false;
    dbms_application_info.set_module('PKG_CHAOS', 'dispatcher '||p_slot||' idle');
  end reset_session;

  -- runs a claimed run; a scenario failure has been recorded on the run by run(), the dispatcher goes on
  procedure execute_run (p_run_id number, p_slot pls_integer) is
  begin
    begin
      run(p_run_id);
    exception
      when others then
        if is_stop(sqlcode) then
          raise;                 -- this session is being ended: nothing more can run in it
        end if;
        rollback;                -- run() restored, marked the run FAILED with the message and committed
    end;
    reset_session(p_slot);
  end execute_run;

  -- the earliest due PLANNED row: started as a QUEUED run with source SCHEDULE (dispatcher 1 claims it at its next
  -- poll), or SKIPPED (another run active, a refusal, or more than c_late_min minutes late). One row per call;
  -- commits; false when no row was free. SKIP LOCKED: the two dispatchers never handle the same row.
  function start_due_plan return boolean is
    l_now  timestamp := utc_now;
    l_id   number;
    l_err  varchar2(400);
    l_note varchar2(400);
  begin
    for p in (select plan_id, scenario, intensity, minutes, planned_start_ts
                from CHAOS_PLAN
               where (case when status = 'PLANNED' then planned_start_ts end) <= l_now
               order by (case when status = 'PLANNED' then planned_start_ts end), plan_id
                 for update skip locked) loop
      if p.planned_start_ts < l_now - numtodsinterval(c_late_min, 'MINUTE') then
        -- (a private function cannot be called inside SQL: the note is built first)
        l_note := 'missed: due '||ts_text(p.planned_start_ts)||', found '||ts_text(l_now)||' by '||g_job;
        update CHAOS_PLAN set status = 'SKIPPED', acted_ts = l_now, note = l_note where plan_id = p.plan_id;
      else
        savepoint before_start;
        begin
          l_id := enqueue(p.scenario, p.minutes, p.intensity, 'SCHEDULE');
          l_note := 'started by '||g_job;
          update CHAOS_PLAN set status = 'STARTED', run_id = l_id, acted_ts = l_now, note = l_note
           where plan_id = p.plan_id;
        exception
          when others then
            l_err := substr(sqlerrm, 1, 300);
            rollback to savepoint before_start;
            update CHAOS_PLAN set status = 'SKIPPED', acted_ts = l_now, note = l_err where plan_id = p.plan_id;
        end;
      end if;
      commit;
      return true;
    end loop;
    return false;
  end start_due_plan;

  -- a new dispatcher run: a RUNNING run still marked as executed by this job belongs to the session that ended
  procedure sweep (p_slot pls_integer) is
    l_problems varchar2(4000);
  begin
    if p_slot = 1 then
      for r in (select run_id from CHAOS_RUN where status = 'RUNNING' and exec_a = g_job) loop
        mark_end(r.run_id, 'FAILED', 'dispatcher '||g_job||' restarted '||ts_text(utc_now)
                                     ||': the session that ran this run has ended');
        commit;
        l_problems := restore_state(false);
        mark_restored(r.run_id, l_problems);
        commit;
      end loop;
    else
      update CHAOS_RUN set exec_b = null, sid_b = null where status = 'RUNNING' and exec_b = g_job;
      commit;
    end if;
  end sweep;

  -- ---------------------------------------------------------------- public entry points
  function start_scenario (p_name varchar2, p_minutes number, p_intensity varchar2 default 'LOW',
                           p_source varchar2 default 'UI') return number is
    pragma autonomous_transaction;
    l_id number;
  begin
    l_id := enqueue(p_name, p_minutes, p_intensity, p_source);
    commit;
    return l_id;
  exception
    when others then
      rollback;
      raise;
  end start_scenario;

  procedure dispatch (p_slot pls_integer) is
    l_sid     number := to_number(sys_context('USERENV', 'SID'));
    l_end     timestamp;
    l_t       number;
    l_polls   pls_integer := 0;
    l_acted   boolean;
    l_ran     boolean;
    r_id      number;
    r_status  varchar2(8);
    r_sc      varchar2(30);
    r_int     varchar2(4);
    r_execb   varchar2(30);
    r_stop    char(1);
    l_due     timestamp;
    n         number;
  begin
    if p_slot is null or p_slot not in (1, 2) then
      raise_application_error(-20106, 'dispatch: the slot is 1 or 2');
    end if;
    g_job := case p_slot when 1 then c_disp1 else c_disp2 end;
    select count(*) into n from user_scheduler_running_jobs where job_name = g_job and session_id = l_sid;
    if n = 0 then
      g_job := null;
      raise_application_error(-20106, 'dispatch: only the job '||case p_slot when 1 then c_disp1 else c_disp2 end
                                      ||' runs dispatcher '||p_slot);
    end if;
    dbms_output.disable;                             -- PKG_SHOP.settle prints; a job has no reader for it
    reset_session(p_slot);
    sweep(p_slot);
    l_end := next_end(utc_now);
    loop
      l_t     := dbms_utility.get_time;
      l_acted := false;
      l_ran   := false;
      l_polls := l_polls + 1;
      -- the poll: one statement. The active run through its unique index (at most one row; the aggregates make it
      -- one row always) and the earliest due plan row through CHAOS_PLAN_DUE_IX.
      select a.run_id, a.status, a.scenario, a.intensity, a.exec_b, a.stop_req,
             (select min(case when p.status = 'PLANNED' then p.planned_start_ts end) from CHAOS_PLAN p)
        into r_id, r_status, r_sc, r_int, r_execb, r_stop, l_due
        from (select max(r.run_id) run_id, max(r.status) status, max(r.scenario) scenario,
                     max(r.intensity) intensity, max(r.exec_b) exec_b, max(r.stop_req) stop_req
                from CHAOS_RUN r
               where (case when r.status in ('QUEUED', 'RUNNING') then 0 end) = 0) a;
      if p_slot = 1 and r_status = 'QUEUED' and r_stop = 'N' then
        if claim(r_id, 'A', l_sid) then
          execute_run(r_id, p_slot);
          l_ran := true;
        end if;
        l_acted := true;
      elsif p_slot = 2 and r_status = 'RUNNING' and r_sc = 'cpu_hog' and r_int = 'HIGH' and r_execb is null
            and r_stop = 'N' then
        if claim(r_id, 'B', l_sid) then
          execute_run(r_id, p_slot);
          l_ran := true;
        end if;
        l_acted := true;
      elsif l_due is not null and l_due <= utc_now then
        l_acted := start_due_plan;
      end if;
      if l_ran and utc_now >= l_end - interval '30' second then
        -- a scenario ran up to or past the hour's hand-over: the hand-over moves to the next whole hour, so it
        -- never comes right after the end of an incident
        l_end := next_end(utc_now);
      end if;
      if not l_acted then
        exit when utc_now >= l_end;
        if mod(l_polls, c_flag_every) = 0 then
          exit when not job_enabled;                 -- 93 (re-run) disables the job to end this loop
        end if;
        dbms_session.sleep(greatest(0, c_poll_cs - least(elapsed_cs(l_t), c_poll_cs)) / 100);
      end if;
    end loop;
    commit;
    dbms_application_info.set_module(null, null);
  end dispatch;

  procedure run (p_run_id number) is
    l_sid      number := to_number(sys_context('USERENV', 'SID'));
    l_job      varchar2(128);
    r          CHAOS_RUN%rowtype;
    l_main     boolean;
    l_high     boolean;
    l_stop     char(1);
    l_problems varchar2(4000);
    l_err      varchar2(4000);
    l_code     number;
  begin
    if p_run_id is null or p_run_id <= 0 or p_run_id != trunc(p_run_id) then
      raise_application_error(-20106, 'run: a run id is a positive whole number');
    end if;
    -- only a dispatcher job's session runs a scenario, and only a run it has claimed
    begin
      select job_name into l_job from user_scheduler_running_jobs
       where session_id = l_sid and job_name in (c_disp1, c_disp2);
    exception
      when no_data_found or too_many_rows then
        raise_application_error(-20106, 'run: only a dispatcher job ('||c_disp1||', '||c_disp2||') runs a scenario; '
                                        ||'use start_scenario');
    end;
    begin
      select * into r from CHAOS_RUN where run_id = p_run_id;
    exception
      when no_data_found then
        raise_application_error(-20107, 'run: no run '||p_run_id);
    end;
    if r.exec_a = l_job and r.sid_a = l_sid then
      l_main := true;
    elsif r.exec_b = l_job and r.sid_b = l_sid then
      l_main := false;
    else
      raise_application_error(-20106, 'run: run '||p_run_id||' is not claimed by this dispatcher session');
    end if;
    if r.status != 'RUNNING' or r.stop_req = 'Y' then
      return;                                        -- stopped before it began: nothing to do
    end if;
    g_job        := l_job;
    g_run_id     := p_run_id;
    g_checked_at := null;
    g_label      := r.scenario||' '||r.intensity||case when not l_main then ' half B' end;
    l_high       := r.intensity = 'HIGH';
    dbms_application_info.set_module('PKG_CHAOS', substr(g_label, 1, 64));
    begin
      case r.scenario
        when 'blocking_chain'   then do_blocking_chain(l_high);
        when 'plan_regression'  then do_plan_regression(l_high);
        when 'slow_drift'       then do_slow_drift(l_high);
        when 'batch_wrong_time' then do_batch_wrong_time(l_high);
        when 'hard_parse_storm' then do_hard_parse_storm(l_high);
        when 'commit_storm'     then do_commit_storm(l_high);
        when 'io_storm'         then do_io_storm(l_high);
        when 'cpu_hog'          then do_cpu_hog(l_high);
        when 'temp_spill'       then do_temp_spill(l_high);
        when 'conn_leak'        then do_conn_leak(l_high);
        when 'logon_storm'      then do_logon_storm(l_high);
        when 'app_error_burst'  then do_app_error_burst(l_high);
      end case;
      if l_main then
        l_problems := restore_state(true);
        select stop_req into l_stop from CHAOS_RUN where run_id = p_run_id;
        if l_stop = 'Y' then
          mark_end(p_run_id, 'STOPPED', null);         -- the note keeps who stopped it
        else
          mark_end(p_run_id, 'DONE', 'done '||ts_text(utc_now));
        end if;
        mark_restored(p_run_id, l_problems);
        commit;
      else
        rollback;
      end if;
    exception
      when others then
        l_code := sqlcode;
        l_err  := substr(sqlerrm, 1, 300);
        rollback;                                    -- the run's own transaction first: no lock outlives a failure
        if is_stop(l_code) then
          raise;                                     -- this session is being ended (the reaper restores)
        end if;
        if l_main then
          begin
            l_problems := restore_state(true);
          exception
            when others then
              l_problems := substr('the restore raised '||sqlerrm, 1, 300);
          end;
        end if;
        mark_end(p_run_id, 'FAILED', 'failed '||ts_text(utc_now)||': '||l_err);
        if l_main then
          mark_restored(p_run_id, l_problems);
        end if;
        commit;
        raise;
    end;
  end run;

  procedure stop_all (p_note varchar2 default null) is
    pragma autonomous_transaction;
    l_id       number;
    l_problems varchar2(4000);
    l_stamp    varchar2(30) := ts_text(utc_now);
  begin
    -- 1. the active run (at most one): its STOP flag, then its dispatcher ends and restores it
    select max(run_id) into l_id from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
    if l_id is not null then
      end_active(l_id, 'stopped by stop_all '||l_stamp||case when p_note is not null then ': '||substr(p_note, 1, 200) end);
    end if;
    -- 2. the restore, also when nothing was active: "stop all" leaves the target clean
    l_problems := restore_state(false);
    if l_id is not null then
      mark_restored(l_id, l_problems);
    end if;
    commit;
    if l_problems is not null then
      raise_application_error(-20111, 'stop_all: run stopped, restore incomplete (the reaper retries every minute): '
                                      ||substr(l_problems, 1, 300));
    end if;
  end stop_all;

  procedure restore (p_run_id number) is
    pragma autonomous_transaction;
    n          number;
    l_busy     number;
    l_problems varchar2(4000);
  begin
    select count(*) into n from CHAOS_RUN where run_id = p_run_id;
    if n = 0 then
      raise_application_error(-20107, 'restore: no run '||nvl(to_char(p_run_id), 'null'));
    end if;
    select max(run_id) into l_busy from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
    if l_busy is not null then
      raise_application_error(-20112, 'restore: run '||l_busy||' is QUEUED or RUNNING; stop_all stops and restores it');
    end if;
    l_problems := restore_state(false);
    mark_restored(p_run_id, l_problems);
    commit;
    if l_problems is not null then
      raise_application_error(-20111, 'restore: incomplete (the reaper retries every minute): '||substr(l_problems, 1, 300));
    end if;
  end restore;

  procedure set_load (p_enabled varchar2, p_load_pct number) is
    pragma autonomous_transaction;
    l_en  varchar2(100) := upper(trim(substr(p_enabled, 1, 100)));
    l_now timestamp := utc_now;
  begin
    if l_en is null or l_en not in ('Y', 'N') or p_load_pct is null or p_load_pct < 0 or p_load_pct > 200 then
      raise_application_error(-20108, 'set_load: enabled must be Y or N and load_pct from 0 to 200');
    end if;
    update DRIVER_CONTROL set enabled = l_en, load_pct = p_load_pct, updated_ts = l_now where id = 1;
    if sql%rowcount = 0 then
      rollback;
      raise_application_error(-20110, 'set_load: DRIVER_CONTROL row 1 is missing (run 87)');
    end if;
    commit;
  end set_load;

  procedure reap is
    pragma autonomous_transaction;
    n          number;
    r          CHAOS_RUN%rowtype;
    l_id       number;
    l_problems varchar2(4000);
    l_after    varchar2(4000);
    l_last     number;
    l_now      timestamp := utc_now;
    l_stamp    varchar2(30) := ts_text(utc_now);
  begin
    -- 1. the active run (at most one)
    select max(run_id) into l_id from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
    if l_id is not null then
      select * into r from CHAOS_RUN where run_id = l_id;
      if r.status = 'QUEUED' then
        if r.requested_ts < l_now - numtodsinterval(c_queue_min, 'MINUTE') then
          -- nothing was applied: nothing to restore
          mark_end(l_id, 'FAILED', 'reaper '||l_stamp||': QUEUED for '||c_queue_min
                                   ||' min, no dispatcher claimed it (is '||c_disp1||' running?)');
          update CHAOS_RUN set restored = 'Y' where run_id = l_id;
          commit;
        end if;
      elsif r.planned_end_ts + interval '2' minute < l_now then
        end_active(l_id, 'reaper '||l_stamp||': RUNNING 2 min past its planned end; stop requested');
        l_problems := restore_state(false);
        mark_restored(l_id, l_problems);
        commit;
      elsif not executor_alive(r.exec_a, r.sid_a) then
        mark_end(l_id, 'FAILED', 'reaper '||l_stamp||': its executor session is gone (dispatcher '
                                 ||nvl(r.exec_a, 'none')||' session '||nvl(to_char(r.sid_a), 'none')||')');
        commit;
        l_problems := restore_state(false);
        mark_restored(l_id, l_problems);
        commit;
      elsif r.exec_b is not null and not executor_alive(r.exec_b, r.sid_b) then
        -- cpu_hog HIGH's second session ended: dispatcher 2 claims the second half again
        update CHAOS_RUN set exec_b = null, sid_b = null where run_id = l_id and status = 'RUNNING';
        commit;
      end if;
    end if;
    -- 2. a plan row the dispatchers never started (both were down): SKIPPED, never started late
    update CHAOS_PLAN
       set status = 'SKIPPED', acted_ts = l_now,
           note = 'missed: due '||to_char(planned_start_ts, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')||', found '||l_stamp
                  ||' by the reaper (no dispatcher ran)'
     where (case when status = 'PLANNED' then planned_start_ts end) < l_now - numtodsinterval(c_late_min, 'MINUTE');
    commit;
    -- 3. nothing active: the target must be in its restored state
    select count(*) into n from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
    if n = 0 then
      l_problems := state_problems;
      if l_problems is not null then
        -- checked again just before repairing: a run that started meanwhile owns the state now
        select count(*) into n from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
        if n = 0 then
          l_after := restore_state(false);
          select max(run_id) into l_last from CHAOS_RUN;
          update CHAOS_RUN
             set note = substr('reaper '||l_stamp||': repaired '||l_problems
                               ||case when l_after is not null then '; still wrong: '||l_after end
                               ||case when note is not null then ' | '||note end, 1, 400)
           where run_id = l_last;
          commit;
        end if;
      end if;
      -- 4. runs that ended with restored = 'N' are marked 'Y' once the target is verified clean
      if state_problems is null then
        update CHAOS_RUN set restored = 'Y'
         where restored = 'N' and status not in ('QUEUED', 'RUNNING')
           and not exists (select 1 from CHAOS_RUN r where r.status in ('QUEUED', 'RUNNING'));
      end if;
    end if;
    commit;
  end reap;
end PKG_CHAOS;
/

-- CREATE OR REPLACE with compilation errors is a warning, not an SQL error: check and fail loudly
declare
  l_bad varchar2(4000);
begin
  for e in (select name, type, line, position, text
              from user_errors
             where name = 'PKG_CHAOS'
             order by type, sequence) loop
    l_bad := substr(l_bad||chr(10)||'  '||e.type||' line '||e.line||': '||e.text, 1, 3900);
  end loop;
  if l_bad is not null then
    raise_application_error(-20900, '93: PKG_CHAOS did not compile:'||l_bad);
  end if;
end;
/

-- ------------------------------------------------------------------ grants (contract: these five and no more)
prompt 93: grants to CHAOS_CTL
grant execute on PKG_CHAOS      to CHAOS_CTL;
grant select  on CHAOS_RUN      to CHAOS_CTL;
grant select  on DRIVER_CONTROL to CHAOS_CTL;
grant insert  on CHAOS_PLAN     to CHAOS_CTL;
grant select  on CHAOS_PLAN     to CHAOS_CTL;
begin
  for g in (select privilege, table_name from user_tab_privs_made
             where grantee = 'CHAOS_CTL' and type != 'USER'
               and not ((privilege = 'EXECUTE' and table_name = 'PKG_CHAOS')
                     or (privilege = 'SELECT' and table_name in ('CHAOS_RUN', 'DRIVER_CONTROL', 'CHAOS_PLAN'))
                     or (privilege = 'INSERT' and table_name = 'CHAOS_PLAN'))) loop
    execute immediate 'revoke '||g.privilege||' on '||dbms_assert.enquote_name(g.table_name, false)||' from CHAOS_CTL';
    dbms_output.put_line('  revoked '||g.privilege||' on '||g.table_name||' from CHAOS_CTL (not in the contract)');
  end loop;
end;
/

-- ------------------------------------------------------------------ the jobs (explicit UTC start dates)
prompt 93: jobs CHAOS_REAPER (every minute at second 15) and CHAOS_DISPATCH_1/2 (minutely at second 0), UTC
declare
  procedure ensure (p_job varchar2, p_repeat varchar2, p_action varchar2, p_comment varchar2) is
    l_start      timestamp with time zone;
    l_cur_start  timestamp with time zone;
    l_cur_repeat varchar2(4000);
    l_cur_action varchar2(4000);
    n            number;
  begin
    -- the next whole UTC minute, carrying the region UTC (the scheduler's default zone here is PST8PDT)
    l_start := from_tz(cast(trunc(sys_extract_utc(systimestamp), 'MI') as timestamp) + interval '1' minute, 'UTC');
    select count(*) into n from user_scheduler_jobs where job_name = p_job;
    if n = 0 then
      dbms_scheduler.create_job(
        job_name        => p_job,
        job_type        => 'PLSQL_BLOCK',
        job_action      => p_action,
        start_date      => l_start,
        repeat_interval => p_repeat,
        auto_drop       => false,
        enabled         => false,
        comments        => p_comment);
      dbms_output.put_line('  '||p_job||' created, first run '||to_char(l_start, 'YYYY-MM-DD HH24:MI:SS TZR'));
    else
      select start_date, repeat_interval, job_action
        into l_cur_start, l_cur_repeat, l_cur_action
        from user_scheduler_jobs where job_name = p_job;
      if nvl(l_cur_repeat, '-') != p_repeat or nvl(l_cur_action, '-') != p_action
         or nvl(to_char(l_cur_start, 'TZR'), '-') != 'UTC' then
        dbms_scheduler.set_attribute(p_job, 'job_action', p_action);
        dbms_scheduler.set_attribute(p_job, 'repeat_interval', p_repeat);
        dbms_scheduler.set_attribute(p_job, 'start_date', l_start);
        dbms_output.put_line('  '||p_job||' existed with a different calendar or action: put back');
      end if;
      dbms_scheduler.set_attribute(p_job, 'comments', p_comment);
    end if;
    -- (the default job class logs every run anyway, the more detailed level wins; the scheduler purges its log
    --  after 30 days: about 1,440 rows a day for the reaper, 24 a day per dispatcher)
    dbms_scheduler.set_attribute(p_job, 'logging_level', dbms_scheduler.logging_failed_runs);
    dbms_scheduler.enable(p_job);
    dbms_output.put_line('  '||p_job||' enabled');
  end ensure;
begin
  ensure('CHAOS_REAPER', 'FREQ=MINUTELY;INTERVAL=1;BYSECOND=15', 'begin SHOP.PKG_CHAOS.reap; end;',
         'app 900: chaos safety net - ends overstayed or orphaned runs, skips missed plan rows, repairs the target');
  ensure('CHAOS_DISPATCH_1', 'FREQ=MINUTELY;INTERVAL=1;BYSECOND=0', 'begin SHOP.PKG_CHAOS.dispatch(1); end;',
         'app 900: chaos dispatcher 1 - runs queued scenarios and starts due plan rows; one session, about an hour');
  ensure('CHAOS_DISPATCH_2', 'FREQ=MINUTELY;INTERVAL=1;BYSECOND=0', 'begin SHOP.PKG_CHAOS.dispatch(2); end;',
         'app 900: chaos dispatcher 2 - cpu_hog HIGH''s second session; starts due plan rows; one session, about an hour');
end;
/

-- ------------------------------------------------------------------ verification: fail when anything is off
prompt 93: verification (waits up to 75 s for both dispatchers to run)
declare
  n   number;
  l_p varchar2(4000);
  procedure need (p_ok boolean, p_what varchar2) is
  begin
    if not p_ok then
      raise_application_error(-20900, '93: verification failed: '||p_what);
    end if;
  end;
begin
  select count(*) into n from user_tables where table_name in ('CHAOS_RUN', 'CHAOS_SCRATCH', 'CHAOS_PLAN');
  need(n = 3, 'CHAOS_RUN, CHAOS_SCRATCH and CHAOS_PLAN expected, found '||n);
  select count(*) into n from user_tab_columns
   where table_name = 'CHAOS_RUN'
     and column_name in ('RUN_ID','SCENARIO','INTENSITY','SOURCE','REQUESTED_TS','START_TS','PLANNED_END_TS','END_TS',
                         'STATUS','RESTORED','JOB_NAME','NOTE','STOP_REQ','EXEC_A','SID_A','EXEC_B','SID_B');
  need(n = 17, 'CHAOS_RUN has '||n||' of the 17 contract columns');
  select count(*) into n from user_tab_columns
   where table_name = 'CHAOS_PLAN'
     and column_name in ('PLAN_ID','PLANNED_START_TS','SCENARIO','INTENSITY','MINUTES','STATUS','RUN_ID','ACTED_TS','NOTE');
  need(n = 9, 'CHAOS_PLAN has '||n||' of the 9 contract columns');
  select count(*) into n from user_constraints
   where table_name = 'CHAOS_RUN' and constraint_name = 'CHAOS_RUN_STATE_CK' and status = 'ENABLED';
  need(n = 1, 'the status check with QUEUED (chaos_run_state_ck) is missing');
  select count(*) into n from user_constraints
   where table_name = 'CHAOS_PLAN' and constraint_name = 'CHAOS_PLAN_MINUTES_CK' and status = 'ENABLED'
     and validated = 'VALIDATED';
  need(n = 1, 'the plan''s maximum-minutes check (chaos_plan_minutes_ck) is missing');
  select count(*) into n from user_indexes
   where index_name in ('CHAOS_RUN_ONE_RUNNING_UX', 'CHAOS_RUN_ONE_ACTIVE_UX') and table_name = 'CHAOS_RUN'
     and uniqueness = 'UNIQUE';
  need(n = 2, 'the unique indexes that allow one active run are missing ('||n||' of 2)');
  select count(*) into n from user_objects
   where object_name = 'PKG_CHAOS' and object_type in ('PACKAGE','PACKAGE BODY') and status = 'VALID';
  need(n = 2, 'PKG_CHAOS spec and body are not both VALID');
  select count(*) into n from user_tab_privs_made
   where grantee = 'CHAOS_CTL' and type != 'USER';
  need(n = 5, 'CHAOS_CTL holds '||n||' privileges on SHOP, the contract says 5');
  select count(*) into n from user_tab_privs_made
   where grantee = 'CHAOS_CTL'
     and ((privilege = 'EXECUTE' and table_name = 'PKG_CHAOS')
       or (privilege = 'SELECT' and table_name in ('CHAOS_RUN', 'DRIVER_CONTROL', 'CHAOS_PLAN'))
       or (privilege = 'INSERT' and table_name = 'CHAOS_PLAN'));
  need(n = 5, 'CHAOS_CTL holds '||n||' of the 5 contract grants');
  select count(*) into n from user_scheduler_jobs
   where job_name = 'CHAOS_REAPER' and enabled = 'TRUE' and to_char(start_date, 'TZR') = 'UTC'
     and repeat_interval = 'FREQ=MINUTELY;INTERVAL=1;BYSECOND=15';
  need(n = 1, 'job CHAOS_REAPER is missing, disabled or off its UTC calendar');
  select count(*) into n from user_scheduler_jobs
   where job_name in ('CHAOS_DISPATCH_1', 'CHAOS_DISPATCH_2') and enabled = 'TRUE'
     and to_char(start_date, 'TZR') = 'UTC' and repeat_interval = 'FREQ=MINUTELY;INTERVAL=1;BYSECOND=0';
  need(n = 2, 'the dispatcher jobs are missing, disabled or off their UTC calendar ('||n||' of 2)');
  for i in 1 .. 75 loop
    select count(*) into n from user_scheduler_running_jobs where job_name in ('CHAOS_DISPATCH_1', 'CHAOS_DISPATCH_2');
    exit when n = 2;
    dbms_session.sleep(1);
  end loop;
  need(n = 2, 'the dispatchers did not both start within 75 s ('||n||' running)');
  select count(*) into n from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
  need(n = 0, n||' run(s) active after the install');
  l_p := PKG_CHAOS.state_problems;
  dbms_output.put_line('  target state: '||nvl(l_p, 'clean (restored state)'));
  for j in (select job_name, enabled, state, to_char(next_run_date, 'YYYY-MM-DD HH24:MI:SS TZR') next_run
              from user_scheduler_jobs
             where job_name in ('CHAOS_REAPER', 'CHAOS_DISPATCH_1', 'CHAOS_DISPATCH_2') order by job_name) loop
    dbms_output.put_line('  job '||j.job_name||': enabled='||j.enabled||' state='||j.state||' next_run='||j.next_run);
  end loop;
  select count(*) into n from CHAOS_RUN;
  dbms_output.put_line('  CHAOS_RUN rows: '||n);
  select count(*) into n from CHAOS_PLAN;
  dbms_output.put_line('  CHAOS_PLAN rows: '||n);
  dbms_output.put_line('93: done - verified at '||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
/
