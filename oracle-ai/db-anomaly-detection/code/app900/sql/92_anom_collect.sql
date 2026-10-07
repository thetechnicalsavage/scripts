-- v1.3 - ANOMOPS in ora26ai / ORCLPDB1 (the observer) for application 900: the metric collector and its job.
--        Run as ANOMOPS after 90 and 91 (tools/anom_obs_run.sh run 92_anom_collect.sql <log>). No password here.
--        v1.3: (phase 4 review, Codex finding 5) the manual backfill(p_minutes) measures its window on the UTC clock,
--              cast(sys_extract_utc(systimestamp) as date), not SYSDATE (the server's zone). collect() and run_loop
--              are unchanged; they never used it. 01-Oct-2026.
--        v1.2: persistent-session collector (phase 4 tune). Measured on v1.1, every one-minute job run logged the
--              link on to the target again: ~116 ms of the target's DB CPU per run, ~103 ms of it the logon, and
--              ~0.017 logons/s added to the LOGONS signal. ANOM_COLLECT_JOB now runs PKG_ANOM_COLLECT.run_loop:
--              one run per UTC hour that keeps one ANOM_MON_LINK session and calls collect() once per target
--              interval at the job's phase second. collect() is unchanged. Install first stops a running loop
--              (replacing a package that a session is executing would wait on it).
--        v1.1: the collector sets METRIC_MINUTE.minute_key (see 90 v1.1): minute_key_of() rounds the begin time
--              to the nearest whole minute against the target's stable metric phase, so second-level jitter
--              can no longer give two intervals one key. Install fills keys left null by a v1.0 collector
--              between 90 and 92 and then makes the column NOT NULL. History rows are inserted in time order.
--        v1.0: first version, 01-Oct-2026.
--
--        PKG_ANOM_COLLECT.collect, once a minute from run_loop (ANOM_COLLECT_JOB):
--          1. reads the newest complete group-18 interval of V$CON_SYSMETRIC@ANOM_MON_LINK (25 metrics, pivoted
--             on the target in one remote query) and the current V$CON_WAITCLASSMETRIC joined to
--             V$SYSTEM_WAIT_CLASS (a second remote query);
--          2. pairs them: the target publishes the wait-class interval a few seconds after the sysmetric one
--             (7 s on oradb1), so when the two begin times are more than 30 s apart the lagging view is read
--             again after 2 s, at most 6 times. Unpaired after that: the row is written with null waits and a
--             WARN is logged;
--          3. MERGEs one METRIC_MINUTE row keyed on the interval's begin time (re-runs are harmless; a re-run
--             never replaces a wait value with null; a back-filled 'H' row is upgraded to 'L'), with its
--             minute_key from minute_key_of() (kept when the row already has one);
--          4. when the row just written is more than 90 s after the previous one (or is the first of the last
--             hour), back-fills the missing intervals of the last 60 minutes from V$CON_SYSMETRIC_HISTORY
--             (source 'H', waits null: the target keeps no wait-class history) and logs what it added;
--          5. commits, which also ends the distributed transaction the remote reads opened. It closes no link.
--        A failure rolls back, is written to COLLECT_LOG (autonomous) and re-raised, so the scheduler records
--        the failed run too. Success is not logged.
--        Cost on the target in steady state: two remote queries a minute and one link logon an hour (measured:
--        tools/anom_collector_cost.sh --persistent).
--
--        PKG_ANOM_COLLECT.run_loop (v1.2), the job's action: waits for the job's phase second of each minute and
--        calls collect(), in one session, so ANOM_MON_LINK stays open for the whole run. A run ends after the
--        collection due at HH:59:<sec> of its UTC hour (about a minute before the next run is due), commits and
--        closes the link; the job's next start (HH+1:00:<sec>) begins the next hour. A failed collect() (it has
--        logged an ERROR) is rolled back, the link is closed, a WARN is logged, the loop backs off 10 s and goes
--        on with the next interval; due times that passed meanwhile are skipped and collect() back-fills them from
--        the target's history. A session stop (ORA-01013, ORA-00028, shutdown) ends the run. The loop also ends
--        within 10 s of the job being disabled, which is how this script stops it before replacing the package.
--        Why the run ends a minute, not two, before the next start: run k's last collection and run k+1's first
--        are 60 s apart, so any hand-over margin above 60 s skips an interval every hour, and a back-filled row
--        has no wait-class values (training drops rows with a null signal).
--
--        ANOM_COLLECT_JOB: FREQ=MINUTELY at a fixed second, chosen at install as 20 s after the end second of the
--        target's current group-18 interval (both views are then published for the same minute), UTC. A run lasts
--        until the end of its hour and the scheduler never starts a second instance of a running job, so the job
--        starts once an hour; if a run ends early (a stop, an instance restart), the next minute starts it again.
--        Measured on 23.26.1 (01-Oct, a throwaway minutely job that ran 100 s): the next run date is computed when
--        a run starts, so a run that outlasts it is followed at once by the next run. The run that starts at
--        about HH:59:50 therefore finds HH:59:<sec> already served (METRIC_MINUTE.collected_ts), waits for
--        HH+1:00:<sec> and covers hour HH+1: one link logon an hour, no interval collected twice or skipped.
--        Only failed runs go to the scheduler log. Idempotent: a running loop is stopped (job disabled, wait), the
--        package is replaced, the job is created when absent or re-pointed and re-enabled when present.
set serveroutput on size unlimited format wrapped
set verify off
set linesize 200
set feedback off
whenever sqlerror exit failure rollback

begin
  if sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '92: run in the PDB (ORCLPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, '92: run as ANOMOPS');
  end if;
end;
/

-- ------------------------------------------------------------------ v1.2: stop a running collector loop first
-- CREATE OR REPLACE of a package that a session is executing waits for that session, so the loop must end
-- before the package is replaced. Disabling the job makes run_loop return within about 10 s (it checks the
-- flag between sleeps); a v1.1 run (one collect()) ends by itself within seconds. The job is re-enabled below.
declare
  n            number;
  l_was_on     varchar2(5);
  e_not_running exception;
  pragma exception_init(e_not_running, -27366);   -- the run ended between the check and the stop
  function running (p_max_sec pls_integer) return boolean is
  begin
    for i in 0 .. p_max_sec loop
      select count(*) into n from user_scheduler_running_jobs where job_name = 'ANOM_COLLECT_JOB';
      exit when n = 0 or i = p_max_sec;
      dbms_session.sleep(1);
    end loop;
    return n > 0;
  end;
begin
  select count(*), max(enabled) into n, l_was_on from user_scheduler_jobs where job_name = 'ANOM_COLLECT_JOB';
  if n > 0 then
    dbms_scheduler.disable('ANOM_COLLECT_JOB', force => true);   -- force: also while a run is active
    if running(90) then
      -- the loop did not notice the flag (e.g. a remote call hangs): ask the scheduler to stop the run
      begin
        dbms_scheduler.stop_job('ANOM_COLLECT_JOB');
      exception
        when e_not_running then null;    -- it ended by itself: the goal is reached
      end;
      if running(30) then
        -- leave the collector as it was found rather than disabled, then fail the install
        if l_was_on = 'TRUE' then
          dbms_scheduler.enable('ANOM_COLLECT_JOB');
        end if;
        raise_application_error(-20900, '92: ANOM_COLLECT_JOB is still running; not replacing the package under it');
      end if;
    end if;
    dbms_output.put_line('  ANOM_COLLECT_JOB disabled and idle at '
                         ||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')||' (re-enabled below)');
  end if;
end;
/

create or replace package PKG_ANOM_COLLECT authid definer as
  -- v1.2 - app 900 metric collector (see 92_anom_collect.sql). All times are the target's UTC clock.

  -- one collection: live interval + wait classes, MERGE, back-fill of a gap that just formed, commit
  procedure collect;

  -- the minute an interval beginning at p_begin stands for: its nearest whole minute, measured against the
  -- target's metric phase = the circular mean of the begin second of the 5 intervals before it in
  -- METRIC_MINUTE and of p_begin itself. Jitter of a few seconds around that phase never changes the minute.
  -- Reads METRIC_MINUTE, so it cannot be called from DML on that table (ORA-04091).
  function minute_key_of (p_begin in date) return date;

  -- manual back-fill: fill every interval of the last p_minutes (1-60) the target's history still holds
  -- and METRIC_MINUTE lacks; returns through p_rows how many rows it added. Commits.
  procedure backfill (p_minutes in pls_integer default 60, p_rows out pls_integer);

  -- test aid: drives the failure path (rollback, COLLECT_LOG ERROR row, re-raise of ORA-20990) without
  -- reading the target. Used by test/anom_test_phase3.sql.
  procedure check_failure_path;

  -- v1.2, the job's action: collect() once per target interval at the job's phase second, in this one session
  -- (ANOM_MON_LINK stays open), until the end of the current UTC hour; then commit and close the link. A failed
  -- collect() does not end the loop: rollback, close the link, WARN, 10 s back-off, next interval.
  -- With no arguments (the job): the second is the job's BYSECOND, the loop ends after the HH:59 collection or
  -- within 10 s of the job being disabled. Test aids: p_until (UTC) ends the loop instead (and the job flag is
  -- not read), p_second replaces BYSECOND, the first p_fail_first iterations call check_failure_path instead of
  -- collect(). A test-mode run logs one INFO 'loop_end' row; a job run logs 'loop_end' only after a failure.
  procedure run_loop (p_until      in date        default null,
                      p_second     in pls_integer default null,
                      p_fail_first in pls_integer default 0);
end PKG_ANOM_COLLECT;
/

create or replace package body PKG_ANOM_COLLECT as
  -- v1.3
  c_tol_sec    constant number      := 30;   -- sysmetric and wait-class intervals belong together within this
  c_retries    constant pls_integer := 6;    -- re-reads of the lagging view
  c_sleep_sec  constant number      := 2;    -- between re-reads
  c_gap_sec    constant number      := 90;   -- begin times further apart than this = an interval is missing
  c_hist_min   constant pls_integer := 60;   -- the target keeps one hour of V$CON_SYSMETRIC_HISTORY
  c_ts_fmt     constant varchar2(30) := 'YYYY-MM-DD"T"HH24:MI:SS"Z"';
  -- run_loop (v1.2)
  c_job         constant varchar2(30) := 'ANOM_COLLECT_JOB';
  c_link        constant varchar2(30) := 'ANOM_MON_LINK';
  c_backoff_sec constant number      := 10;  -- after a failed collect(): never spin
  c_chunk_sec   constant number      := 10;  -- longest single sleep; the job flag is read after each one
  c_late_sec    constant number      := 5;   -- a due time at most this far in the past is still collected

  e_link_not_open exception;
  pragma exception_init(e_link_not_open, -2081);

  type t_sys is record (
    begin_time date, end_time date, intsize_csec number, n_metrics number,
    aas number, dbtime number, cpu number, cpu_txn number, wait_ratio number, lio_txn number, lio number,
    pio number, pio_bytes number, pwrites number, redo number, blkchg number, commits number, txn number,
    calls number, execs number, hardparse number, parse number, logons number, sessions number,
    rt_txn number, sql_rt number, enq_waits number, temp number, longscans number);
  type t_sys_tab is table of t_sys;

  type t_wait is record (
    begin_time date, n_intervals number, n_classes number,
    w_userio number, w_commit number, w_concur number, w_appl number, w_config number, w_other number);

  e_no_interval exception;

  function ts (p date) return varchar2 is
  begin
    return to_char(p, c_ts_fmt);
  end;

  -- COLLECT_LOG writer: its own transaction, so a failure's rollback does not take the log row with it
  procedure log_event (p_severity varchar2, p_message varchar2) is
    pragma autonomous_transaction;
  begin
    insert into COLLECT_LOG (log_ts, severity, message)
    values (sys_extract_utc(systimestamp), p_severity, substr(p_message, 1, 4000));
    commit;
  end;

  -- the failure path shared by every public entry point: roll back (this also ends the distributed
  -- transaction), record the error, and leave the re-raise to the caller's handler
  procedure record_failure (p_where varchar2, p_error varchar2, p_backtrace varchar2) is
  begin
    rollback;
    log_event('ERROR', 'event=collect_failed where='||p_where||' error="'||p_error||'" backtrace="'
                       ||replace(rtrim(p_backtrace, chr(10)), chr(10), ' | ')||'"');
  exception
    when others then
      -- the log itself could not be written: fail loudly with both errors rather than hide either
      raise_application_error(-20991, 'collect failed in '||p_where||' ('||p_error
                              ||') and COLLECT_LOG could not record it', true);
  end;

  -- newest complete group-18 interval, pivoted on the target (one remote round trip)
  function read_sys return t_sys is
    r t_sys;
  begin
    select begin_time, end_time, intsize_csec, n_metrics,
           aas, dbtime, cpu, cpu_txn, wait_ratio, lio_txn, lio, pio, pio_bytes, pwrites, redo, blkchg,
           commits, txn, calls, execs, hardparse, parse, logons, sessions, rt_txn, sql_rt, enq_waits,
           temp, longscans
      into r
      from (select begin_time, end_time, intsize_csec, count(distinct metric_name) n_metrics,
                   max(case metric_name when 'Average Active Sessions'           then value end) aas,
                   max(case metric_name when 'Database Time Per Sec'             then value end) dbtime,
                   max(case metric_name when 'CPU Usage Per Sec'                 then value end) cpu,
                   max(case metric_name when 'CPU Usage Per Txn'                 then value end) cpu_txn,
                   max(case metric_name when 'Database Wait Time Ratio'          then value end) wait_ratio,
                   max(case metric_name when 'Logical Reads Per Txn'             then value end) lio_txn,
                   max(case metric_name when 'Logical Reads Per Sec'             then value end) lio,
                   max(case metric_name when 'Physical Reads Per Sec'            then value end) pio,
                   max(case metric_name when 'Physical Read Total Bytes Per Sec' then value end) pio_bytes,
                   max(case metric_name when 'Physical Writes Per Sec'           then value end) pwrites,
                   max(case metric_name when 'Redo Generated Per Sec'            then value end) redo,
                   max(case metric_name when 'DB Block Changes Per Sec'          then value end) blkchg,
                   max(case metric_name when 'User Commits Per Sec'              then value end) commits,
                   max(case metric_name when 'User Transaction Per Sec'          then value end) txn,
                   max(case metric_name when 'User Calls Per Sec'                then value end) calls,
                   max(case metric_name when 'Executions Per Sec'                then value end) execs,
                   max(case metric_name when 'Hard Parse Count Per Sec'          then value end) hardparse,
                   max(case metric_name when 'Total Parse Count Per Sec'         then value end) parse,
                   max(case metric_name when 'Logons Per Sec'                    then value end) logons,
                   max(case metric_name when 'Session Count'                     then value end) sessions,
                   max(case metric_name when 'Response Time Per Txn'             then value end) rt_txn,
                   max(case metric_name when 'SQL Service Response Time'         then value end) sql_rt,
                   max(case metric_name when 'Enqueue Waits Per Sec'             then value end) enq_waits,
                   max(case metric_name when 'Temp Space Used'                   then value end) temp,
                   max(case metric_name when 'Long Table Scans Per Sec'          then value end) longscans
              from v$con_sysmetric@ANOM_MON_LINK
             where group_id = 18
               and metric_name in ('Average Active Sessions', 'Database Time Per Sec', 'CPU Usage Per Sec',
                   'CPU Usage Per Txn', 'Database Wait Time Ratio', 'Logical Reads Per Txn', 'Logical Reads Per Sec',
                   'Physical Reads Per Sec', 'Physical Read Total Bytes Per Sec', 'Physical Writes Per Sec',
                   'Redo Generated Per Sec', 'DB Block Changes Per Sec', 'User Commits Per Sec',
                   'User Transaction Per Sec', 'User Calls Per Sec', 'Executions Per Sec',
                   'Hard Parse Count Per Sec', 'Total Parse Count Per Sec', 'Logons Per Sec', 'Session Count',
                   'Response Time Per Txn', 'SQL Service Response Time', 'Enqueue Waits Per Sec',
                   'Temp Space Used', 'Long Table Scans Per Sec')
             group by begin_time, end_time, intsize_csec
            having count(distinct metric_name) = 25   -- a group caught mid-update is skipped
             order by begin_time desc
             fetch first 1 row only);
    return r;
  exception
    when no_data_found then
      raise e_no_interval;
  end;

  -- current wait-class interval: centiseconds waited per second, per class. A class absent from
  -- V$SYSTEM_WAIT_CLASS has never waited since the target started, so it counts as 0.
  function read_wait return t_wait is
    r t_wait;
  begin
    select max(m.begin_time), count(distinct m.begin_time), count(*),
           nvl(sum(case n.wait_class when 'User I/O'      then m.time_waited / (m.intsize_csec / 100) end), 0),
           nvl(sum(case n.wait_class when 'Commit'        then m.time_waited / (m.intsize_csec / 100) end), 0),
           nvl(sum(case n.wait_class when 'Concurrency'   then m.time_waited / (m.intsize_csec / 100) end), 0),
           nvl(sum(case n.wait_class when 'Application'   then m.time_waited / (m.intsize_csec / 100) end), 0),
           nvl(sum(case n.wait_class when 'Configuration' then m.time_waited / (m.intsize_csec / 100) end), 0),
           nvl(sum(case n.wait_class when 'Other'         then m.time_waited / (m.intsize_csec / 100) end), 0)
      into r
      from v$con_waitclassmetric@ANOM_MON_LINK m
      left join v$system_wait_class@ANOM_MON_LINK n on n.wait_class# = m.wait_class#
     where m.intsize_csec > 0;
    return r;
  end;

  function minute_key_of (p_begin in date) return date is
    c_rad  constant number := acos(-1) / 30;    -- one second of the minute, in radians
    l_sin  number;
    l_cos  number;
    l_ph   number;
  begin
    select nvl(sum(sin(a)), 0), nvl(sum(cos(a)), 0) into l_sin, l_cos
      from (select to_number(to_char(begin_time, 'SS')) * c_rad a
              from METRIC_MINUTE
             where begin_time < p_begin
             order by begin_time desc
             fetch first 5 rows only);
    l_sin := l_sin + sin(to_number(to_char(p_begin, 'SS')) * c_rad);
    l_cos := l_cos + cos(to_number(to_char(p_begin, 'SS')) * c_rad);
    l_ph  := mod(atan2(l_sin, l_cos) / c_rad + 60, 60);           -- the phase, 0 to <60 s
    -- shift the phase to mid-minute, then truncate: the result is the nearest minute of a begin time at the
    -- phase, and stays the same for any begin within +-29 s of it
    return trunc(p_begin + round(mod(30 - l_ph + 60, 60)) / 86400, 'MI');
  end;

  -- 'paired' = one wait-class interval, begun within c_tol_sec of the sysmetric interval
  function paired (p_s t_sys, p_w t_wait) return boolean is
  begin
    -- never NULL: an empty wait read (no rows, null begin time) is "not paired", so it is re-read
    if p_w.n_intervals = 1 and p_w.n_classes > 0 and p_w.begin_time is not null and p_s.begin_time is not null
       and abs(p_w.begin_time - p_s.begin_time) * 86400 <= c_tol_sec then
      return true;
    end if;
    return false;
  end;

  -- insert every complete history interval at or after p_from that METRIC_MINUTE lacks; returns the count
  function fill_from_history (p_from date) return pls_integer is
    l_h     t_sys_tab;
    l_key   date;
    l_added pls_integer := 0;
  begin
    select begin_time, end_time, intsize_csec, count(distinct metric_name),
           max(case metric_name when 'Average Active Sessions'           then value end),
           max(case metric_name when 'Database Time Per Sec'             then value end),
           max(case metric_name when 'CPU Usage Per Sec'                 then value end),
           max(case metric_name when 'CPU Usage Per Txn'                 then value end),
           max(case metric_name when 'Database Wait Time Ratio'          then value end),
           max(case metric_name when 'Logical Reads Per Txn'             then value end),
           max(case metric_name when 'Logical Reads Per Sec'             then value end),
           max(case metric_name when 'Physical Reads Per Sec'            then value end),
           max(case metric_name when 'Physical Read Total Bytes Per Sec' then value end),
           max(case metric_name when 'Physical Writes Per Sec'           then value end),
           max(case metric_name when 'Redo Generated Per Sec'            then value end),
           max(case metric_name when 'DB Block Changes Per Sec'          then value end),
           max(case metric_name when 'User Commits Per Sec'              then value end),
           max(case metric_name when 'User Transaction Per Sec'          then value end),
           max(case metric_name when 'User Calls Per Sec'                then value end),
           max(case metric_name when 'Executions Per Sec'                then value end),
           max(case metric_name when 'Hard Parse Count Per Sec'          then value end),
           max(case metric_name when 'Total Parse Count Per Sec'         then value end),
           max(case metric_name when 'Logons Per Sec'                    then value end),
           max(case metric_name when 'Session Count'                     then value end),
           max(case metric_name when 'Response Time Per Txn'             then value end),
           max(case metric_name when 'SQL Service Response Time'         then value end),
           max(case metric_name when 'Enqueue Waits Per Sec'             then value end),
           max(case metric_name when 'Temp Space Used'                   then value end),
           max(case metric_name when 'Long Table Scans Per Sec'          then value end)
      bulk collect into l_h
      from v$con_sysmetric_history@ANOM_MON_LINK
     where group_id = 18
       and begin_time >= p_from
       and metric_name in ('Average Active Sessions', 'Database Time Per Sec', 'CPU Usage Per Sec',
           'CPU Usage Per Txn', 'Database Wait Time Ratio', 'Logical Reads Per Txn', 'Logical Reads Per Sec',
           'Physical Reads Per Sec', 'Physical Read Total Bytes Per Sec', 'Physical Writes Per Sec',
           'Redo Generated Per Sec', 'DB Block Changes Per Sec', 'User Commits Per Sec',
           'User Transaction Per Sec', 'User Calls Per Sec', 'Executions Per Sec',
           'Hard Parse Count Per Sec', 'Total Parse Count Per Sec', 'Logons Per Sec', 'Session Count',
           'Response Time Per Txn', 'SQL Service Response Time', 'Enqueue Waits Per Sec',
           'Temp Space Used', 'Long Table Scans Per Sec')
     group by begin_time, end_time, intsize_csec
    having count(distinct metric_name) = 25
     order by begin_time;   -- oldest first: each key is computed from the rows before it

    for i in 1 .. l_h.count loop
      l_key := minute_key_of(l_h(i).begin_time);
      insert into METRIC_MINUTE (
        begin_time, end_time, minute_key, collected_ts, intsize_csec,
        aas, dbtime, cpu, cpu_txn, wait_ratio, lio_txn, lio, pio, pio_bytes, pwrites, redo, blkchg,
        commits, txn, calls, execs, hardparse, parse, logons, sessions, rt_txn, sql_rt, enq_waits,
        temp, longscans, source)
      select l_h(i).begin_time, l_h(i).end_time, l_key, sys_extract_utc(systimestamp), l_h(i).intsize_csec,
             l_h(i).aas, l_h(i).dbtime, l_h(i).cpu, l_h(i).cpu_txn, l_h(i).wait_ratio, l_h(i).lio_txn,
             l_h(i).lio, l_h(i).pio, l_h(i).pio_bytes, l_h(i).pwrites, l_h(i).redo, l_h(i).blkchg,
             l_h(i).commits, l_h(i).txn, l_h(i).calls, l_h(i).execs, l_h(i).hardparse, l_h(i).parse,
             l_h(i).logons, l_h(i).sessions, l_h(i).rt_txn, l_h(i).sql_rt, l_h(i).enq_waits,
             l_h(i).temp, l_h(i).longscans, 'H'
        from dual
       where not exists (select 1 from METRIC_MINUTE m where m.begin_time = l_h(i).begin_time);
      l_added := l_added + sql%rowcount;
    end loop;
    return l_added;
  end;

  procedure collect is
    l_s      t_sys;
    l_w      t_wait;
    l_nowait t_wait;               -- all fields null: "no wait values for this row"
    l_tries  pls_integer := 0;
    l_paired boolean;
    l_prev   date;
    l_from   date;
    l_key    date;
    l_added  pls_integer;
    l_err    varchar2(4000);
    l_bt     varchar2(4000);
  begin
    -- 1. read both views; an interval caught mid-update is re-read like a lagging one
    begin
      l_s := read_sys;
    exception
      when e_no_interval then
        dbms_session.sleep(c_sleep_sec);
        l_s := read_sys;   -- a second miss propagates as a failure
    end;
    l_w := read_wait;

    -- 2. pair the two intervals: re-read whichever view lags
    l_paired := paired(l_s, l_w);
    while not l_paired and l_tries < c_retries loop
      dbms_session.sleep(c_sleep_sec);
      l_tries := l_tries + 1;
      if l_w.n_intervals != 1 or l_w.begin_time < l_s.begin_time then
        l_w := read_wait;
      else
        l_s := read_sys;
      end if;
      l_paired := paired(l_s, l_w);
    end loop;
    if not l_paired then
      log_event('WARN', 'event=wait_unpaired begin='||ts(l_s.begin_time)||' wait_begin='||ts(l_w.begin_time)
                        ||' wait_intervals='||l_w.n_intervals||' tries='||l_tries||' (row kept, waits null)');
      l_w := l_nowait;
    end if;

    -- 3. one row per interval; a re-run updates it in place, keeps its minute_key, never blanks a wait value
    l_key := minute_key_of(l_s.begin_time);
    merge into METRIC_MINUTE m
    using (select l_s.begin_time begin_time from dual) s
       on (m.begin_time = s.begin_time)
     when matched then update set
          m.end_time = l_s.end_time, m.intsize_csec = l_s.intsize_csec, m.minute_key = nvl(m.minute_key, l_key),
          m.collected_ts = sys_extract_utc(systimestamp),
          m.aas = l_s.aas, m.dbtime = l_s.dbtime, m.cpu = l_s.cpu, m.cpu_txn = l_s.cpu_txn,
          m.wait_ratio = l_s.wait_ratio, m.lio_txn = l_s.lio_txn, m.lio = l_s.lio, m.pio = l_s.pio,
          m.pio_bytes = l_s.pio_bytes, m.pwrites = l_s.pwrites, m.redo = l_s.redo, m.blkchg = l_s.blkchg,
          m.commits = l_s.commits, m.txn = l_s.txn, m.calls = l_s.calls, m.execs = l_s.execs,
          m.hardparse = l_s.hardparse, m.parse = l_s.parse, m.logons = l_s.logons,
          m.sessions = l_s.sessions, m.rt_txn = l_s.rt_txn, m.sql_rt = l_s.sql_rt,
          m.enq_waits = l_s.enq_waits, m.temp = l_s.temp, m.longscans = l_s.longscans,
          m.w_userio = nvl(l_w.w_userio, m.w_userio), m.w_commit = nvl(l_w.w_commit, m.w_commit),
          m.w_concur = nvl(l_w.w_concur, m.w_concur), m.w_appl = nvl(l_w.w_appl, m.w_appl),
          m.w_config = nvl(l_w.w_config, m.w_config), m.w_other = nvl(l_w.w_other, m.w_other),
          m.source = 'L'
     when not matched then insert (
          begin_time, end_time, minute_key, collected_ts, intsize_csec,
          aas, dbtime, cpu, cpu_txn, wait_ratio, lio_txn, lio, pio, pio_bytes, pwrites, redo, blkchg,
          commits, txn, calls, execs, hardparse, parse, logons, sessions, rt_txn, sql_rt, enq_waits,
          temp, longscans, w_userio, w_commit, w_concur, w_appl, w_config, w_other, source)
     values (
          l_s.begin_time, l_s.end_time, l_key, sys_extract_utc(systimestamp), l_s.intsize_csec,
          l_s.aas, l_s.dbtime, l_s.cpu, l_s.cpu_txn, l_s.wait_ratio, l_s.lio_txn, l_s.lio, l_s.pio,
          l_s.pio_bytes, l_s.pwrites, l_s.redo, l_s.blkchg, l_s.commits, l_s.txn, l_s.calls, l_s.execs,
          l_s.hardparse, l_s.parse, l_s.logons, l_s.sessions, l_s.rt_txn, l_s.sql_rt, l_s.enq_waits,
          l_s.temp, l_s.longscans, l_w.w_userio, l_w.w_commit, l_w.w_concur, l_w.w_appl, l_w.w_config,
          l_w.w_other, 'L');

    -- 4. a gap that has just formed (or an empty last hour): one read of the target's history
    select max(begin_time) into l_prev from METRIC_MINUTE where begin_time < l_s.begin_time;
    if l_prev is null or (l_s.begin_time - l_prev) * 86400 > c_gap_sec then
      l_from := greatest(nvl(l_prev, date '1970-01-01'), l_s.begin_time - c_hist_min / 1440);
      l_added := fill_from_history(l_from);
      if l_added > 0 then
        log_event('INFO', 'event=backfill rows='||l_added||' from='||ts(l_from)||' to='||ts(l_s.begin_time)
                          ||' source=V$CON_SYSMETRIC_HISTORY');
      elsif l_prev is not null then
        log_event('WARN', 'event=gap_unfilled after='||ts(l_prev)||' before='||ts(l_s.begin_time)
                          ||' seconds='||round((l_s.begin_time - l_prev) * 86400)
                          ||' (the target''s history holds no interval for it)');
      end if;
    end if;

    -- 5. one commit: the row, the back-fill, and the end of the distributed transaction
    commit;
  exception
    when others then
      l_err := sqlerrm;
      l_bt  := dbms_utility.format_error_backtrace;
      record_failure('collect', l_err, l_bt);
      raise;
  end collect;

  procedure backfill (p_minutes in pls_integer default 60, p_rows out pls_integer) is
    l_err varchar2(4000);
    l_bt  varchar2(4000);
  begin
    if p_minutes is null or p_minutes not between 1 and c_hist_min then
      raise_application_error(-20901, 'backfill: p_minutes must be 1-'||c_hist_min);
    end if;
    p_rows := fill_from_history(cast(sys_extract_utc(systimestamp) as date) - p_minutes / 1440);
    if p_rows > 0 then
      log_event('INFO', 'event=backfill rows='||p_rows||' minutes='||p_minutes||' source=V$CON_SYSMETRIC_HISTORY manual=Y');
    end if;
    commit;
  exception
    when others then
      l_err := sqlerrm;
      l_bt  := dbms_utility.format_error_backtrace;
      record_failure('backfill', l_err, l_bt);
      raise;
  end backfill;

  procedure check_failure_path is
    l_err varchar2(4000);
    l_bt  varchar2(4000);
  begin
    raise_application_error(-20990, 'check_failure_path: deliberate failure, the target is not read');
  exception
    when others then
      l_err := sqlerrm;
      l_bt  := dbms_utility.format_error_backtrace;
      record_failure('check_failure_path', l_err, l_bt);
      raise;
  end check_failure_path;

  -- ------------------------------------------------------------------ run_loop (v1.2)
  function utc_now return timestamp is
  begin
    return sys_extract_utc(systimestamp);
  end;

  function seconds_of (p_iv interval day to second) return number is
  begin
    return extract(day from p_iv) * 86400 + extract(hour from p_iv) * 3600
         + extract(minute from p_iv) * 60 + extract(second from p_iv);
  end;

  function tsx (p timestamp) return varchar2 is
  begin
    return to_char(p, c_ts_fmt);
  end;

  -- the job's phase second, from its repeat_interval (FREQ=MINUTELY;BYSECOND=<s>, set below at install)
  function job_second return pls_integer is
    l_rep user_scheduler_jobs.repeat_interval%type;
    l_sec number;
  begin
    begin
      select repeat_interval into l_rep from user_scheduler_jobs where job_name = c_job;
    exception
      when no_data_found then
        raise_application_error(-20902, 'run_loop: job '||c_job||' not found; pass p_second');
    end;
    l_sec := to_number(regexp_substr(l_rep, 'BYSECOND=([0-9]{1,2})(;|$)', 1, 1, 'i', 1));
    if l_sec is null or l_sec not between 0 and 59 then
      raise_application_error(-20902, 'run_loop: no BYSECOND 0-59 in '||c_job||'.repeat_interval ('||l_rep||')');
    end if;
    return l_sec;
  end;

  -- the job's flag: false once the job is disabled (or dropped), which ends a job-mode loop
  function job_enabled return boolean is
    l_on user_scheduler_jobs.enabled%type;
  begin
    select enabled into l_on from user_scheduler_jobs where job_name = c_job;
    return l_on = 'TRUE';
  exception
    when no_data_found then
      return false;
  end;

  -- close this session's link to the target (it must have no open transaction); not open: nothing to do
  procedure close_link is
  begin
    dbms_session.close_database_link(c_link);
  exception
    when e_link_not_open then
      null;
  end;

  -- a stop of this session, not a collector failure: these end the loop (stop_job, kill, shutdown)
  function is_stop (p_code number) return boolean is
  begin
    return p_code in (-1013, -28, -31, -1089, -1092);
  end;

  procedure run_loop (p_until      in date        default null,
                      p_second     in pls_integer default null,
                      p_fail_first in pls_integer default 0) is
    l_job     constant boolean     := p_until is null;
    l_mode    constant varchar2(4) := case when p_until is null then 'job' else 'test' end;
    l_sec     pls_integer;
    l_due     timestamp;
    l_until   timestamp;
    l_last    timestamp;
    l_wait    number;
    l_iter    pls_integer := 0;
    l_ok      pls_integer := 0;
    l_failed  pls_integer := 0;
    l_skipped pls_integer := 0;
    l_stopped boolean     := false;
    l_code    number;
    l_err     varchar2(4000);
    l_bt      varchar2(4000);
  begin
    if p_second is not null and p_second not between 0 and 59 then
      raise_application_error(-20903, 'run_loop: p_second must be 0-59');
    end if;
    if p_fail_first is null or p_fail_first < 0 then
      raise_application_error(-20903, 'run_loop: p_fail_first must be 0 or more');
    end if;
    if p_until is not null and p_until <= cast(utc_now as date) then
      raise_application_error(-20903, 'run_loop: p_until must be in the future (UTC)');
    end if;
    l_sec := nvl(p_second, job_second);

    -- the first due time: this minute's phase second, or the next minute's when it passed more than c_late_sec
    -- ago or a collection has already served it (a run started right after the previous run's last collect)
    l_due := cast(trunc(cast(utc_now as date), 'MI') as timestamp) + numtodsinterval(l_sec, 'SECOND');
    select max(collected_ts) into l_last from METRIC_MINUTE;
    if l_due < utc_now - numtodsinterval(c_late_sec, 'SECOND') or l_last >= l_due then
      l_due := l_due + interval '1' minute;
    end if;
    -- a job run covers the rest of the UTC hour of its first due time; the job's next start takes the next hour
    l_until := case when l_job then cast(trunc(cast(l_due as date), 'HH') + 1 / 24 as timestamp)
                    else cast(p_until as timestamp) end;

    while l_due < l_until loop
      -- sleep until the due time, at most c_chunk_sec at a time; a job run ends once the job is disabled
      loop
        l_wait := seconds_of(l_due - utc_now);
        exit when l_wait <= 0;
        dbms_session.sleep(least(l_wait, c_chunk_sec));
        if l_job and not job_enabled then
          l_stopped := true;
          exit;
        end if;
      end loop;
      exit when l_stopped;

      l_iter := l_iter + 1;
      begin
        if l_iter <= p_fail_first then
          check_failure_path;            -- test aid: the failure path, without reading the target
        else
          collect;
        end if;
        l_ok := l_ok + 1;
      exception
        when others then
          l_code := sqlcode;
          l_err  := sqlerrm;
          if is_stop(l_code) then
            raise;
          end if;
          -- collect() has rolled back and logged the ERROR; end anything left open and drop the link session,
          -- so the next interval starts on a fresh one
          rollback;
          begin
            close_link;
          exception
            when others then
              log_event('WARN', 'event=loop_close_link_failed mode='||l_mode||' error="'||sqlerrm||'"');
          end;
          l_failed := l_failed + 1;
          log_event('WARN', 'event=loop_collect_failed mode='||l_mode||' due='||tsx(l_due)||' error="'||l_err
                            ||'" action=rollback,close_link,backoff_'||c_backoff_sec||'s,next_interval');
          dbms_session.sleep(c_backoff_sec);
      end;

      -- the next interval. Due times that passed during a slow collect() or the back-off are skipped; the next
      -- collect() back-fills their intervals from the target's history.
      l_due := l_due + interval '1' minute;
      while l_due < utc_now - numtodsinterval(c_late_sec, 'SECOND') loop
        l_due := l_due + interval '1' minute;
        l_skipped := l_skipped + 1;
      end loop;
    end loop;

    -- end of the run: nothing stays open on the target
    commit;
    begin
      close_link;
    exception
      when others then
        log_event('WARN', 'event=loop_close_link_failed mode='||l_mode||' at=end error="'||sqlerrm||'"');
    end;
    if not l_job or l_failed > 0 or l_skipped > 0 then
      log_event(case when l_job then 'WARN' else 'INFO' end,
                'event=loop_end mode='||l_mode||' second='||l_sec||' iterations='||l_iter||' ok='||l_ok
                ||' failed='||l_failed||' skipped='||l_skipped||' stopped='||case when l_stopped then 'Y' else 'N' end);
    end if;
  exception
    when others then
      l_code := sqlcode;
      l_err  := sqlerrm;
      l_bt   := dbms_utility.format_error_backtrace;
      if is_stop(l_code) then
        -- a stop is not a collector failure: WARN, then let the session end (the scheduler records the stop)
        rollback;
        log_event('WARN', 'event=loop_stopped mode='||l_mode||' iterations='||l_iter||' error="'||l_err||'"');
        raise;
      end if;
      record_failure('run_loop', l_err, l_bt);
      raise;
  end run_loop;
end PKG_ANOM_COLLECT;
/
show errors package body PKG_ANOM_COLLECT

declare
  n number;
begin
  select count(*) into n from user_objects
   where object_name = 'PKG_ANOM_COLLECT' and status != 'VALID';
  if n > 0 then
    raise_application_error(-20900, '92: PKG_ANOM_COLLECT did not compile');
  end if;
end;
/

-- ------------------------------------------------------------------ minute_key: fill, then NOT NULL (v1.1)
-- rows a v1.0 collector wrote between 90 v1.1 and this script have no key yet; oldest first, one by one
-- (minute_key_of reads the table, so it cannot sit inside the UPDATE)
alter session set ddl_lock_timeout = 30;
declare
  n        number := 0;
  l_null   varchar2(1);
  l_key    date;
begin
  for r in (select begin_time from METRIC_MINUTE where minute_key is null order by begin_time) loop
    l_key := PKG_ANOM_COLLECT.minute_key_of(r.begin_time);   -- computed first: the UPDATE must not call it
    update METRIC_MINUTE set minute_key = l_key where begin_time = r.begin_time;
    n := n + 1;
  end loop;
  commit;
  if n > 0 then
    dbms_output.put_line('  minute_key filled on '||n||' row(s)');
  end if;
  select nullable into l_null from user_tab_columns where table_name = 'METRIC_MINUTE' and column_name = 'MINUTE_KEY';
  if l_null = 'Y' then
    execute immediate 'alter table METRIC_MINUTE modify (minute_key not null)';
    dbms_output.put_line('  METRIC_MINUTE.minute_key is now NOT NULL');
  end if;
end;
/

-- one collection now, in this session: proves the package end to end (and back-fills the last hour on a first
-- install) before the job is enabled again
begin
  PKG_ANOM_COLLECT.collect;
end;
/

-- ------------------------------------------------------------------ the job, phased on the target's interval
declare
  l_sec    number;
  l_rep    varchar2(100);
  n        number;
  l_on     timestamp with time zone;
begin
  -- 20 s after the second the target's current group-18 interval ended: by then the wait-class interval
  -- of the same minute (published ~5 s later) is out too, and the next interval is ~40 s away
  select mod(to_number(to_char(max(end_time), 'SS')) + 20, 60) into l_sec
    from v$con_sysmetric@ANOM_MON_LINK where group_id = 18;
  commit;
  l_rep := 'FREQ=MINUTELY;BYSECOND='||l_sec;

  select count(*) into n from user_scheduler_jobs where job_name = 'ANOM_COLLECT_JOB';
  if n = 0 then
    dbms_scheduler.create_job(
      job_name        => 'ANOM_COLLECT_JOB',
      job_type        => 'STORED_PROCEDURE',
      job_action      => 'PKG_ANOM_COLLECT.RUN_LOOP',
      start_date      => systimestamp at time zone 'UTC',
      repeat_interval => l_rep,
      enabled         => false,
      auto_drop       => false,
      comments        => 'App 900: one METRIC_MINUTE row a minute from the target, one ANOM_MON_LINK session an hour');
    dbms_output.put_line('  ANOM_COLLECT_JOB created ('||l_rep||', UTC)');
  else
    dbms_scheduler.set_attribute('ANOM_COLLECT_JOB', 'job_action', 'PKG_ANOM_COLLECT.RUN_LOOP');
    dbms_scheduler.set_attribute('ANOM_COLLECT_JOB', 'repeat_interval', l_rep);
    dbms_scheduler.set_attribute('ANOM_COLLECT_JOB', 'comments',
                                 'App 900: one METRIC_MINUTE row a minute from the target, one ANOM_MON_LINK session an hour');
    dbms_output.put_line('  ANOM_COLLECT_JOB present; set to PKG_ANOM_COLLECT.RUN_LOOP, '||l_rep||' (UTC)');
  end if;
  dbms_scheduler.set_attribute('ANOM_COLLECT_JOB', 'logging_level', dbms_scheduler.logging_failed_runs);
  l_on := systimestamp;
  dbms_scheduler.enable('ANOM_COLLECT_JOB');
  dbms_output.put_line('  ANOM_COLLECT_JOB enabled at '
                       ||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));

  -- the loop starts at the next phase second: wait for it (at most 75 s) so a job that cannot run fails here
  for i in 1 .. 75 loop
    select count(*) into n from user_scheduler_jobs
     where job_name = 'ANOM_COLLECT_JOB' and (state = 'RUNNING' or last_start_date >= l_on);
    exit when n > 0;
    dbms_session.sleep(1);
  end loop;
  if n = 0 then
    raise_application_error(-20900, '92: ANOM_COLLECT_JOB was enabled but did not start within 75 s');
  end if;
end;
/

select '  METRIC_MINUTE: '||count(*)||' rows ('||count(case when source = 'L' then 1 end)||' live, '
       ||count(case when source = 'H' then 1 end)||' back-filled), newest '
       ||to_char(max(begin_time), 'YYYY-MM-DD HH24:MI:SS')||' UTC' as metric_minute
  from METRIC_MINUTE;

select '  '||job_name||': '||job_action||', '||repeat_interval||', '||enabled||', '||state||', last start '
       ||to_char(sys_extract_utc(last_start_date), 'YYYY-MM-DD HH24:MI:SS')||' UTC' as job
  from user_scheduler_jobs where job_name = 'ANOM_COLLECT_JOB';
