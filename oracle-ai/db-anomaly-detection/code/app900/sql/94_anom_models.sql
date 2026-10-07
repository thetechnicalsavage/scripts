-- v1.3 - ANOMOPS in ora26ai / ORCLPDB1 (the observer) for application 900, phase 4 (builder M): the detector
--        registry and definitions, the copy of the target's chaos runs, the attribution key and the trainer.
--        Run as ANOMOPS after 90-92 and 91 v1.1 (tools/anom_obs_run.sh run 94_anom_models.sql <log>).
--        No password here. Contract v1.1 sections 6 and 9, v1.6 section 12, v1.8 section 13; PLAN.md 5-7 and 7a.
--        v1.3: (phase 5c) training exclusion windows: TRAIN_EXCLUDE(from_ts, to_ts, reason, created_by, created_ts)
--              holds windows of the observer's own contacts with the target during training (a grant re-run, an
--              ASH read, a maintenance deploy). Training removes every FEATURE_MINUTE row with from_ts <= ts <= to_ts
--              exactly where it removes the chaos windows: training_query (so train(), train_all, 98's interim
--              and FINAL training and tools/iforest.py), window_signals' first pass, and train()'s row counts
--              (signals_json gains rows_in_exclusion_windows; rows_with_null no longer counts them). Seeded with
--              2026-10-02 01:15-01:40Z (phase 5b: the 86 grant re-run at 01:20:44Z and three ASH reads).
--              PKG_ANOM_TRAIN.add_exclusion (autonomous, validated, -20214) is how a caller adds one (97's
--              load_ash, tools/anom_train_exclude.sql). Specification: add_exclusion added at its end, nothing
--              else changed (its dependants stay valid). 02-Oct-2026.
--        v1.2: (phase 4b, PLAN.md 7a: every observer-to-target call at a constant cadence) ANOM_TRUTH_JOB runs
--              PKG_ANOM_TRAIN.truth_loop (FREQ=MINUTELY;BYSECOND=20, UTC): one run per UTC hour keeps one
--              ANOM_CHAOS_LINK session and refreshes INCIDENT_TRUTH at minutes 0, 5, .. 55 (second 20) ALWAYS,
--              whatever the chaos state (v1.1 refreshed every 5 minutes only while a copied run was RUNNING, else
--              hourly: one target logon per refresh, so LOGONS rose during incidents). The run holds its session
--              until HH:59:50 and the next run starts at once: one link logon an hour (at HH:00:20), the session
--              absent for about 30 s an hour, both at fixed times. Each refresh also mirrors the target's
--              CHAOS_PLAN statuses into INCIDENT_PLAN (96's PKG_ANOM_GRADE.mirror_plan, over the same session).
--              batch_truth_begin / batch_truth_end: a batch of train() calls refreshes INCIDENT_TRUTH once (98's
--              interim and final training, train_all), not once per detector. The chaos-window filter treats a
--              QUEUED run (93 v1.2) like a RUNNING one. The install stops a running truth loop before it replaces
--              the package (as 92 does for the collector). Spec: three procedures added at its end, nothing else
--              changed (its dependants stay valid). 01-Oct-2026.
--        v1.1: (phase 4 review, Codex finding 3) the chaos-window end of a RUNNING (or open) run is measured on the
--              UTC clock, cast(sys_extract_utc(systimestamp) as date), not SYSDATE (the server's zone). Both
--              containers run on UTC today, so the rows a model trains on do not change. Package body only; the
--              specification is unchanged (its dependants stay valid). 01-Oct-2026.
--        v1.0: first version, 01-Oct-2026.
--
--        MODEL_REGISTRY    one row per trained detector model: detector, variant, training window, rows, the
--                          signals used and dropped (signals_json), the effective settings (settings_json), status
--                          CANDIDATE / ACTIVE / RETIRED (at most one ACTIVE per detector, a unique index), and the
--                          measured train_seconds, size_bytes and score_ms_per_min (set by 95's score_range).
--        THRESHOLD_DEF     STATIC: per signal hi = the PCT percentile of training (default 99.5) and, for TXN,
--                          CALLS, EXECS, COMMITS and APP_TPS, lo = the (100 - PCT) percentile.
--        SEASONAL_BASE     SEASONAL: per signal and UTC hour, the median and the MAD of training. A MAD of 0 (a
--                          signal that sits still in that hour) is replaced by the signal's MAD over all hours, or,
--                          if that is 0 too, by its mean absolute deviation / 1.4826, so z is always defined.
--        MODEL_SIGNAL_STAT PCA: per signal the training mean and standard deviation (the z-scaling the SVD saw).
--        INCIDENT_TRUTH    copy of the target's SHOP.CHAOS_RUN through ANOM_CHAOS_LINK (times as UTC DATEs). Rows the
--                          target no longer has are kept with gone_ts set. No detector and no training query reads
--                          it except to EXCLUDE its windows from training; grading (96) reads it.
--        EXPECTED_SIGNALS  the attribution key: PLAN.md section 6, one row per scenario and expected signal.
--        ANOM_STATE        small bookkeeping (last INCIDENT_TRUTH refresh and attempt).
--        TRAIN_EXCLUDE     (v1.3) training exclusion windows [from_ts, to_ts] (UTC DATEs, both ends included), with
--                          the reason, who made it and when. Never read by a detector or by scoring; only removed
--                          from the training rows.
--
--        PKG_ANOM_TRAIN.train(detector, from, to, variant, settings json, activate) builds one model named
--        ANOM_<DET>_<VARIANT>_<yyyymmddhh24mi> (UTC) on the training rows: FEATURE_MINUTE in [from, to), minus
--        every minute within [start - 5 min, end + 30 min] of any chaos run (any source, running runs to now),
--        minus every minute within a TRAIN_EXCLUDE window (v1.3),
--        minus rows with a null in a used signal; signals without values or constant in the window are dropped
--        and listed in signals_json. MSET-SPRT (classification, case id TS, no target, no random projections),
--        one-class SVM (Gaussian, PREP_AUTO), EM anomaly (PREP_AUTO), PCA residual (SVD on z-scored rows; the
--        reconstruction error is computed in SQL from the DM$VE/DM$VV detail views; K = components for VAR_PCT of
--        the variance; threshold = the RESID_PCT percentile of training errors), STATIC and SEASONAL. Isolation
--        Forest is trained outside the database by tools/iforest.py and is only registered here.
--        Training first refreshes INCIDENT_TRUTH and refuses to train on stale chaos windows (link failure);
--        a target without CHAOS_RUN yet (93 not installed) has no runs, so training goes on.
--
--        ANOM_TRUTH_JOB (v1.2, enabled): PKG_ANOM_TRAIN.truth_loop, FREQ=MINUTELY;BYSECOND=20 (UTC). A run covers
--        the rest of its UTC hour: it refreshes INCIDENT_TRUTH (and mirrors CHAOS_PLAN into INCIDENT_PLAN) at
--        HH:00:20, HH:05:20, .. HH:55:20 over one ANOM_CHAOS_LINK session that it keeps open, holds the session
--        until HH:59:50, commits and closes the link. The scheduler starts the next run at once (a minutely job's
--        next run date passed long ago), which opens the link at its first refresh. So the target sees one CHAOS_CTL
--        session that is replaced once an hour at a fixed time, and three remote queries every 5 minutes, whatever
--        happens there. A failed refresh is logged, the link closed, and the next one comes 5 minutes later; a target
--        without CHAOS_RUN is logged at most once an hour. A run ends within 10 s of the job being disabled.
--        refresh_truth_job (v1.0's job action: refresh when due) is kept for manual use only.
--        Idempotent: tables are created when absent and checked, a running truth loop is stopped (job disabled,
--        waited for) before the package is replaced, the package and the job are re-applied, the expected-signal key
--        is merged and rows outside it removed. Never drops an ACTIVE model.
set serveroutput on size unlimited format wrapped
set verify off
set linesize 200
set feedback off
whenever sqlerror exit failure rollback

begin
  if sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '94: run in the PDB (ORCLPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, '94: run as ANOMOPS');
  end if;
end;
/

-- ------------------------------------------------------------------ v1.2: stop a running truth loop first
-- CREATE OR REPLACE of a package that a session is executing waits for that session, so the loop must end before
-- PKG_ANOM_TRAIN is replaced. Disabling the job makes truth_loop return within about 10 s (it reads the flag between
-- sleeps); a v1.1 run (one refresh) ends by itself within seconds. The job is enabled again below.
declare
  n             number;
  e_not_running exception;
  pragma exception_init(e_not_running, -27366);   -- the run ended between the check and the stop
  function running (p_max_sec pls_integer) return boolean is
  begin
    for i in 0 .. p_max_sec loop
      select count(*) into n from user_scheduler_running_jobs where job_name = 'ANOM_TRUTH_JOB';
      exit when n = 0 or i = p_max_sec;
      dbms_session.sleep(1);
    end loop;
    return n > 0;
  end;
begin
  select count(*) into n from user_scheduler_jobs where job_name = 'ANOM_TRUTH_JOB';
  if n > 0 then
    dbms_scheduler.disable('ANOM_TRUTH_JOB', force => true);   -- force: also while a run is active
    if running(90) then
      begin
        dbms_scheduler.stop_job('ANOM_TRUTH_JOB');
      exception
        when e_not_running then null;    -- it ended by itself: the goal is reached
      end;
      if running(30) then
        raise_application_error(-20900, '94: ANOM_TRUTH_JOB is still running; not replacing PKG_ANOM_TRAIN under it '
                                        ||'(the job stays disabled: run 94 again)');
      end if;
    end if;
    dbms_output.put_line('  ANOM_TRUTH_JOB disabled and idle at '
                         ||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')||' (enabled again below)');
  end if;
end;
/

-- ------------------------------------------------------------------ tables (created only when absent)
declare
  procedure mk (p_table varchar2, p_ddl varchar2) is
    n number;
  begin
    select count(*) into n from user_tables where table_name = p_table;
    if n = 0 then
      execute immediate p_ddl;
      dbms_output.put_line('  table '||p_table||' created');
    else
      dbms_output.put_line('  table '||p_table||' already present');
    end if;
  end;
begin
  mk('ANOM_STATE', q'[
    create table ANOM_STATE (
      name       varchar2(30)    not null,
      value_ts   timestamp,
      value_txt  varchar2(4000),
      constraint anom_state_pk primary key (name)
    )]');

  mk('MODEL_REGISTRY', q'[
    create table MODEL_REGISTRY (
      model_name        varchar2(128)  not null,
      detector          varchar2(10)   not null,
      variant           varchar2(30)   not null,
      train_from        date,
      train_to          date,
      n_rows            number,
      signals_json      clob,
      settings_json     clob,
      status            varchar2(10)   default 'CANDIDATE' not null,
      created_ts        timestamp      default sys_extract_utc(systimestamp) not null,
      status_ts         timestamp,
      train_seconds     number,
      size_bytes        number,
      score_ms_per_min  number,
      note              varchar2(4000),
      constraint model_registry_pk     primary key (model_name),
      constraint model_registry_det_ck check (detector in ('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL', 'IFOREST')),
      constraint model_registry_st_ck  check (status in ('CANDIDATE', 'ACTIVE', 'RETIRED')),
      constraint model_registry_sig_js check (signals_json is json),
      constraint model_registry_set_js check (settings_json is json),
      constraint model_registry_win_ck check (train_to > train_from)
    )]');

  mk('THRESHOLD_DEF', q'[
    create table THRESHOLD_DEF (
      model_name   varchar2(128)  not null,
      signal_code  varchar2(20)   not null,
      lo           number,
      hi           number         not null,
      constraint threshold_def_pk  primary key (model_name, signal_code),
      constraint threshold_def_mfk foreign key (model_name) references MODEL_REGISTRY (model_name) on delete cascade,
      constraint threshold_def_sfk foreign key (signal_code) references SIGNAL_DEF (signal_code)
    )]');

  mk('SEASONAL_BASE', q'[
    create table SEASONAL_BASE (
      model_name   varchar2(128)  not null,
      signal_code  varchar2(20)   not null,
      hour_utc     number(2)      not null,
      med          number         not null,
      mad          number         not null,
      n            number         not null,
      constraint seasonal_base_pk  primary key (model_name, signal_code, hour_utc),
      constraint seasonal_base_hck check (hour_utc between 0 and 23),
      constraint seasonal_base_mck check (mad > 0),
      constraint seasonal_base_mfk foreign key (model_name) references MODEL_REGISTRY (model_name) on delete cascade,
      constraint seasonal_base_sfk foreign key (signal_code) references SIGNAL_DEF (signal_code)
    )]');

  mk('MODEL_SIGNAL_STAT', q'[
    create table MODEL_SIGNAL_STAT (
      model_name   varchar2(128)  not null,
      signal_code  varchar2(20)   not null,
      mu           number         not null,
      sd           number         not null,
      n            number         not null,
      constraint model_signal_stat_pk  primary key (model_name, signal_code),
      constraint model_signal_stat_mfk foreign key (model_name) references MODEL_REGISTRY (model_name) on delete cascade,
      constraint model_signal_stat_sfk foreign key (signal_code) references SIGNAL_DEF (signal_code)
    )]');

  mk('INCIDENT_TRUTH', q'[
    create table INCIDENT_TRUTH (
      run_id          number         not null,
      scenario        varchar2(30),
      intensity       varchar2(4),
      source          varchar2(10),
      requested_ts    date,
      start_ts        date,
      planned_end_ts  date,
      end_ts          date,
      status          varchar2(10),
      restored        char(1),
      job_name        varchar2(128),
      note            varchar2(400),
      refreshed_ts    timestamp      not null,
      gone_ts         timestamp,
      constraint incident_truth_pk primary key (run_id)
    )]');

  mk('EXPECTED_SIGNALS', q'[
    create table EXPECTED_SIGNALS (
      scenario     varchar2(30)  not null,
      signal_code  varchar2(20)  not null,
      direction    varchar2(4)   default 'UP' not null,
      constraint expected_signals_pk  primary key (scenario, signal_code),
      constraint expected_signals_dck check (direction in ('UP', 'DOWN')),
      constraint expected_signals_sfk foreign key (signal_code) references SIGNAL_DEF (signal_code)
    )]');

  -- v1.3: training exclusion windows (both ends included, UTC)
  mk('TRAIN_EXCLUDE', q'[
    create table TRAIN_EXCLUDE (
      exclude_id   number generated always as identity,
      from_ts      date            not null,
      to_ts        date            not null,
      reason       varchar2(400)   not null,
      created_by   varchar2(255)   not null,
      created_ts   timestamp       default sys_extract_utc(systimestamp) not null,
      constraint train_exclude_pk     primary key (exclude_id),
      constraint train_exclude_win_ck check (to_ts > from_ts)
    )]');
end;
/

declare
  n number;
begin
  -- at most one ACTIVE model per detector
  select count(*) into n from user_indexes where index_name = 'MODEL_REGISTRY_ACTIVE_UX';
  if n = 0 then
    execute immediate q'[create unique index MODEL_REGISTRY_ACTIVE_UX on MODEL_REGISTRY
                         (case when status = 'ACTIVE' then detector end)]';
    dbms_output.put_line('  index MODEL_REGISTRY_ACTIVE_UX created');
  end if;
end;
/

-- an existing table must have every column this version expects
declare
  procedure need (p_table varchar2, p_cols varchar2) is
    l_missing varchar2(4000);
  begin
    select listagg(c.col, ',') within group (order by c.col) into l_missing
      from (select regexp_substr(p_cols, '[^,]+', 1, level) col
              from dual connect by level <= regexp_count(p_cols, ',') + 1) c
     where not exists (select 1 from user_tab_columns t
                        where t.table_name = p_table and t.column_name = c.col);
    if l_missing is not null then
      raise_application_error(-20900, '94: '||p_table||' lacks column(s) '||l_missing);
    end if;
  end;
begin
  need('ANOM_STATE',        'NAME,VALUE_TS,VALUE_TXT');
  need('MODEL_REGISTRY',    'MODEL_NAME,DETECTOR,VARIANT,TRAIN_FROM,TRAIN_TO,N_ROWS,SIGNALS_JSON,SETTINGS_JSON,STATUS,'
                         || 'CREATED_TS,STATUS_TS,TRAIN_SECONDS,SIZE_BYTES,SCORE_MS_PER_MIN,NOTE');
  need('THRESHOLD_DEF',     'MODEL_NAME,SIGNAL_CODE,LO,HI');
  need('SEASONAL_BASE',     'MODEL_NAME,SIGNAL_CODE,HOUR_UTC,MED,MAD,N');
  need('MODEL_SIGNAL_STAT', 'MODEL_NAME,SIGNAL_CODE,MU,SD,N');
  need('INCIDENT_TRUTH',    'RUN_ID,SCENARIO,INTENSITY,SOURCE,REQUESTED_TS,START_TS,PLANNED_END_TS,END_TS,STATUS,'
                         || 'RESTORED,JOB_NAME,NOTE,REFRESHED_TS,GONE_TS');
  need('EXPECTED_SIGNALS',  'SCENARIO,SIGNAL_CODE,DIRECTION');
  need('TRAIN_EXCLUDE',     'EXCLUDE_ID,FROM_TS,TO_TS,REASON,CREATED_BY,CREATED_TS');
  dbms_output.put_line('  table shapes checked');
end;
/

-- v1.3: the first exclusion window, from phase 5b (re-running the install finds it and adds nothing)
merge into TRAIN_EXCLUDE d
using (select to_date('2026-10-02 01:15:00', 'YYYY-MM-DD HH24:MI:SS') f,
              to_date('2026-10-02 01:40:00', 'YYYY-MM-DD HH24:MI:SS') t,
              'phase 5b: 86 grant re-run (01:20:44Z) and three ASH reads through ANOM_MON_LINK' r
         from dual) s
on (d.from_ts = s.f and d.to_ts = s.t and d.reason = s.r)
when not matched then insert (from_ts, to_ts, reason, created_by) values (s.f, s.t, s.r, 'install (94 v1.3)');
commit;
select '  TRAIN_EXCLUDE rows: '||count(*) as excl from TRAIN_EXCLUDE;

-- ------------------------------------------------------------------ the attribution key (PLAN.md section 6)
-- S1-S12 in the plan's order; "(down)" in the plan is direction DOWN. Attribution (section 7) asks only whether
-- a top-3 signal is in the scenario's set; the direction is kept for the incident page.
merge into EXPECTED_SIGNALS d
using (
  select 'blocking_chain' sc, 'W_APPL' sig, 'UP' dir from dual union all
  select 'blocking_chain',    'ENQ_WAITS',   'UP'   from dual union all
  select 'blocking_chain',    'RT_TXN',      'UP'   from dual union all
  select 'blocking_chain',    'APP_P95_MS',  'UP'   from dual union all
  select 'blocking_chain',    'COMMITS',     'DOWN' from dual union all
  select 'plan_regression',   'LIO_TXN',     'UP'   from dual union all
  select 'plan_regression',   'LIO',         'UP'   from dual union all
  select 'plan_regression',   'CPU_TXN',     'UP'   from dual union all
  select 'plan_regression',   'RT_TXN',      'UP'   from dual union all
  select 'plan_regression',   'APP_P95_MS',  'UP'   from dual union all
  select 'slow_drift',        'LIO_TXN',     'UP'   from dual union all
  select 'slow_drift',        'CPU_TXN',     'UP'   from dual union all
  select 'slow_drift',        'RT_TXN',      'UP'   from dual union all
  select 'slow_drift',        'APP_P95_MS',  'UP'   from dual union all
  select 'batch_wrong_time',  'REDO',        'UP'   from dual union all
  select 'batch_wrong_time',  'BLKCHG',      'UP'   from dual union all
  select 'batch_wrong_time',  'W_COMMIT',    'UP'   from dual union all
  select 'batch_wrong_time',  'PWRITES',     'UP'   from dual union all
  select 'batch_wrong_time',  'LIO',         'UP'   from dual union all
  select 'hard_parse_storm',  'HARDPARSE',   'UP'   from dual union all
  select 'hard_parse_storm',  'PARSE',       'UP'   from dual union all
  select 'hard_parse_storm',  'W_CONCUR',    'UP'   from dual union all
  select 'hard_parse_storm',  'CPU',         'UP'   from dual union all
  select 'commit_storm',      'COMMITS',     'UP'   from dual union all
  select 'commit_storm',      'REDO',        'UP'   from dual union all
  select 'commit_storm',      'W_COMMIT',    'UP'   from dual union all
  select 'commit_storm',      'W_CONFIG',    'UP'   from dual union all
  select 'io_storm',          'PIO',         'UP'   from dual union all
  select 'io_storm',          'PIO_BYTES',   'UP'   from dual union all
  select 'io_storm',          'W_USERIO',    'UP'   from dual union all
  select 'io_storm',          'LONGSCANS',   'UP'   from dual union all
  select 'cpu_hog',           'CPU',         'UP'   from dual union all
  select 'cpu_hog',           'CPU_TXN',     'UP'   from dual union all
  select 'cpu_hog',           'AAS',         'UP'   from dual union all
  select 'cpu_hog',           'DBTIME',      'UP'   from dual union all
  select 'temp_spill',        'TEMP',        'UP'   from dual union all
  select 'temp_spill',        'W_USERIO',    'UP'   from dual union all
  select 'temp_spill',        'PWRITES',     'UP'   from dual union all
  select 'conn_leak',         'SESSIONS',    'UP'   from dual union all
  select 'conn_leak',         'LOGONS',      'UP'   from dual union all
  select 'logon_storm',       'LOGONS',      'UP'   from dual union all
  select 'logon_storm',       'CPU',         'UP'   from dual union all
  select 'logon_storm',       'W_OTHER',     'UP'   from dual union all
  select 'app_error_burst',   'APP_ERR_PCT', 'UP'   from dual union all
  select 'app_error_burst',   'TXN',         'DOWN' from dual union all
  select 'app_error_burst',   'COMMITS',     'DOWN' from dual
) s
on (d.scenario = s.sc and d.signal_code = s.sig)
when matched then update set d.direction = s.dir where d.direction != s.dir
when not matched then insert (scenario, signal_code, direction) values (s.sc, s.sig, s.dir);

declare
  n  number;
  n2 number;
begin
  -- the key is pre-registered: a row outside the seed (an older version's) is removed, the count must be 46
  delete from EXPECTED_SIGNALS e
   where (e.scenario, e.signal_code) not in (
     ('blocking_chain','W_APPL'), ('blocking_chain','ENQ_WAITS'), ('blocking_chain','RT_TXN'),
     ('blocking_chain','APP_P95_MS'), ('blocking_chain','COMMITS'),
     ('plan_regression','LIO_TXN'), ('plan_regression','LIO'), ('plan_regression','CPU_TXN'),
     ('plan_regression','RT_TXN'), ('plan_regression','APP_P95_MS'),
     ('slow_drift','LIO_TXN'), ('slow_drift','CPU_TXN'), ('slow_drift','RT_TXN'), ('slow_drift','APP_P95_MS'),
     ('batch_wrong_time','REDO'), ('batch_wrong_time','BLKCHG'), ('batch_wrong_time','W_COMMIT'),
     ('batch_wrong_time','PWRITES'), ('batch_wrong_time','LIO'),
     ('hard_parse_storm','HARDPARSE'), ('hard_parse_storm','PARSE'), ('hard_parse_storm','W_CONCUR'),
     ('hard_parse_storm','CPU'),
     ('commit_storm','COMMITS'), ('commit_storm','REDO'), ('commit_storm','W_COMMIT'), ('commit_storm','W_CONFIG'),
     ('io_storm','PIO'), ('io_storm','PIO_BYTES'), ('io_storm','W_USERIO'), ('io_storm','LONGSCANS'),
     ('cpu_hog','CPU'), ('cpu_hog','CPU_TXN'), ('cpu_hog','AAS'), ('cpu_hog','DBTIME'),
     ('temp_spill','TEMP'), ('temp_spill','W_USERIO'), ('temp_spill','PWRITES'),
     ('conn_leak','SESSIONS'), ('conn_leak','LOGONS'),
     ('logon_storm','LOGONS'), ('logon_storm','CPU'), ('logon_storm','W_OTHER'),
     ('app_error_burst','APP_ERR_PCT'), ('app_error_burst','TXN'), ('app_error_burst','COMMITS'));
  if sql%rowcount > 0 then
    dbms_output.put_line('  EXPECTED_SIGNALS: '||sql%rowcount||' row(s) outside the key removed');
  end if;
  select count(*), count(distinct scenario) into n, n2 from EXPECTED_SIGNALS;
  dbms_output.put_line('  EXPECTED_SIGNALS rows: '||n||' over '||n2||' scenarios');
  if n != 46 or n2 != 12 then
    raise_application_error(-20900, '94: EXPECTED_SIGNALS holds '||n||' rows / '||n2||' scenarios; 46 / 12 expected');
  end if;
end;
/
commit;

comment on table MODEL_REGISTRY is 'App 900: trained detector models (one ACTIVE per detector), windows, signals, settings, cost';
comment on table THRESHOLD_DEF is 'App 900: STATIC detector thresholds per model and signal (hi = PCT percentile; lo for volume signals)';
comment on table SEASONAL_BASE is 'App 900: SEASONAL detector median and MAD per model, signal and UTC hour';
comment on table MODEL_SIGNAL_STAT is 'App 900: PCA detector z-scaling (training mean and sd) per model and signal';
comment on table INCIDENT_TRUTH is 'App 900: copy of the target''s CHAOS_RUN (UTC). Read only to exclude training windows and to grade';
comment on table EXPECTED_SIGNALS is 'App 900: attribution key, PLAN.md section 6 (scenario -> expected signals)';
comment on table ANOM_STATE is 'App 900: job bookkeeping (INCIDENT_TRUTH refresh times)';
comment on table TRAIN_EXCLUDE is 'App 900: training exclusion windows [from_ts, to_ts] (UTC): the observer''s own target contacts during training';

-- ------------------------------------------------------------------ the trainer
create or replace package PKG_ANOM_TRAIN authid definer as
  -- v1.0 - app 900 detector training and the chaos-run copy (see 94_anom_models.sql). All times UTC.

  c_min_rows constant pls_integer := 30;        -- fewer training rows than this: refused (-20208)

  -- trains one model (MSET, SVM, EM, PCA, STATIC, SEASONAL) on the training rows of [p_from, p_to); returns its
  -- name. p_settings overrides the detector's knobs (a JSON object of numbers; see the body for the keys).
  -- The model is a CANDIDATE unless p_activate = 'Y'. Commits.
  function train (p_detector in varchar2, p_from in date, p_to in date, p_variant in varchar2 default 'DEFAULT',
                  p_settings in json default null, p_activate in varchar2 default 'N') return varchar2;

  -- every in-database detector with variant DEFAULT; a failure is logged, the others go on; raises at the end
  -- (-20212) when any failed
  procedure train_all (p_from in date, p_to in date, p_activate in varchar2 default 'N');

  -- ACTIVE: the detector's previous ACTIVE model becomes RETIRED. IFOREST cannot be activated (not live).
  procedure activate (p_model in varchar2);
  procedure retire (p_model in varchar2);
  -- drops a CANDIDATE or RETIRED model (mining model + definitions); refuses an ACTIVE one (-20206)
  procedure drop_model (p_model in varchar2);
  -- drops RETIRED models retired more than p_days ago (only this schema's models exist here)
  procedure purge_retired (p_days in number default 14);

  -- copies SHOP.CHAOS_RUN@ANOM_CHAOS_LINK into INCIDENT_TRUTH (MERGE on run_id; rows the target lost get gone_ts).
  -- Raises on any failure (after logging it). Commits and closes the link.
  procedure refresh_truth;
  -- ANOM_TRUTH_JOB's action: refresh_truth when due (a RUNNING copy, or an hour since the last attempt). A target
  -- without CHAOS_RUN (or without the grant) is logged at most once an hour and skipped; other failures raise.
  procedure refresh_truth_job;

  -- the SELECT of the training rows: ts + p_signals (comma list of SIGNAL_DEF codes) from FEATURE_MINUTE in
  -- [p_from, p_to), minus the chaos windows, minus the TRAIN_EXCLUDE windows (v1.3), minus rows with a null in any
  -- listed signal. Used by train() and by tools/iforest.py, so both detectors families learn from the same rows.
  function training_query (p_from in date, p_to in date, p_signals in varchar2) return varchar2;
  -- the same rows without the chaos-window exclusion (scoring reads every complete minute)
  function rows_query (p_from in date, p_to in date, p_signals in varchar2) return varchar2;
  -- the window's signal selection: p_used = comma list in display order; p_dropped = JSON array of
  -- {"signal","reason"} for signals without values or constant over the window's rows
  procedure window_signals (p_from in date, p_to in date, p_used out varchar2, p_dropped out varchar2);
  -- the used signals of a registered model (comma list)
  function model_signals (p_model in varchar2) return varchar2;
  -- PCA: per (ts, signal) the value, its z and its residual after projecting on the first p_k components
  function pca_resid_sql (p_model in varchar2, p_k in pls_integer, p_rows_sql in varchar2) return varchar2;
  -- NLS-proof literals for generated SQL
  function num_lit (p in number) return varchar2;
  function date_lit (p in date) return varchar2;
  -- "AAS as 'AAS', ..." for UNPIVOT, from a validated comma list
  function unpivot_list (p_signals in varchar2) return varchar2;

  -- v1.2 (added at the end of the specification: the units above and their dependants are unchanged)
  -- ANOM_TRUTH_JOB's action (see the header): refreshes INCIDENT_TRUTH every 5 minutes at second p_second over one
  -- ANOM_CHAOS_LINK session kept open until the end of the UTC hour. Test aids: p_until (UTC) ends the loop instead
  -- (the job flag is not read and no end-of-hour hold), p_second replaces the job's BYSECOND. -20213 bad argument.
  procedure truth_loop (p_until in date default null, p_second in pls_integer default null);
  -- one INCIDENT_TRUTH refresh for a batch of train() calls in this session: train() skips its own refresh until
  -- batch_truth_end (or 6 h). A target without CHAOS_RUN is logged and the batch goes on; any other failure raises
  -- -20210 (training would use stale chaos windows).
  procedure batch_truth_begin;
  procedure batch_truth_end;

  -- v1.3 (added at the end of the specification: the units above and their dependants are unchanged)
  -- a training exclusion window [p_from, p_to] (UTC, both ends included) with its reason and who made it; returns its
  -- id. Its own autonomous transaction (it commits only this row, whatever the caller has pending), so a caller can
  -- add it BEFORE it contacts the target. Refused (-20214, nothing written): a missing end, p_from >= p_to, a window
  -- over 24 hours, a blank reason or creator.
  function add_exclusion (p_from in date, p_to in date, p_reason in varchar2, p_by in varchar2) return number;
end PKG_ANOM_TRAIN;
/

create or replace package body PKG_ANOM_TRAIN as
  -- v1.3 (TRAIN_EXCLUDE windows removed with the chaos windows; add_exclusion); v1.2 (the truth loop, the batch
  -- refresh, QUEUED runs in the chaos windows); v1.1 (the chaos-window end on the UTC clock)
  c_ts_fmt     constant varchar2(30) := 'YYYY-MM-DD"T"HH24:MI:SS"Z"';
  c_lower_sigs constant varchar2(200) := ',TXN,CALLS,EXECS,COMMITS,APP_TPS,';   -- STATIC: these also get a lo
  -- chaos window of a copied run: [start - 5 min, end + 30 min]; a run still QUEUED or RUNNING (or without an end)
  -- lasts until now at least
  c_win_start  constant varchar2(200) := 'coalesce(t.start_ts, t.requested_ts) - 5 / 1440';
  c_win_end    constant varchar2(400) := 'coalesce(t.end_ts, case when t.status in (''RUNNING'', ''QUEUED'') or t.status is null '
                                      || 'then greatest(coalesce(t.planned_end_ts, cast(sys_extract_utc(systimestamp) as date)), '
                                      || 'cast(sys_extract_utc(systimestamp) as date)) '
                                      || 'else coalesce(t.planned_end_ts, cast(sys_extract_utc(systimestamp) as date)) end) + 30 / 1440';
  c_truth_hourly constant number := 55 / 1440;  -- refresh_truth_job: at least this often (a few minutes under the hour)
  -- v1.2 truth loop
  c_truth_job    constant varchar2(30) := 'ANOM_TRUTH_JOB';
  c_truth_every  constant pls_integer  := 5;    -- minutes between refreshes, on the hour's 5-minute marks
  c_chunk_sec    constant number       := 10;   -- longest single sleep; the job flag is read after each one
  c_late_sec     constant number       := 5;    -- a due time at most this far in the past is still served
  c_batch_hours  constant number       := 6;    -- a batch refresh stands in for train()'s own for at most this long
  g_batch_ts     timestamp;                     -- set by batch_truth_begin (this session only)
  c_excl_max_hours constant number     := 24;   -- v1.3: add_exclusion refuses a longer window

  type t_set is table of varchar2(4000) index by varchar2(30);    -- ODM settings (strings, as Oracle takes them)
  type t_par is table of number index by varchar2(30);            -- this package's knobs and derived values
  type t_list is table of varchar2(20) index by pls_integer;

  -- a target without SHOP.CHAOS_RUN (93 not installed) or without CHAOS_CTL's grant on it
  function absent_code (p_code number) return boolean is
  begin
    return p_code in (-942, -2019, -41900, -1031);
  end;

  function ts (p date) return varchar2 is
  begin
    return to_char(p, c_ts_fmt);
  end;

  function now_utc return date is
  begin
    return cast(sys_extract_utc(systimestamp) as date);
  end;

  -- COLLECT_LOG writer (autonomous: a rollback of the caller keeps the row)
  procedure log_event (p_severity varchar2, p_message varchar2) is
    pragma autonomous_transaction;
  begin
    insert into COLLECT_LOG (log_ts, severity, message)
    values (sys_extract_utc(systimestamp), p_severity, substr(p_message, 1, 4000));
    commit;
  end;

  procedure set_state (p_name varchar2, p_ts timestamp, p_txt varchar2 default null) is
  begin
    merge into ANOM_STATE s using (select p_name n from dual) x on (s.name = x.n)
     when matched then update set s.value_ts = p_ts, s.value_txt = p_txt
     when not matched then insert (name, value_ts, value_txt) values (p_name, p_ts, p_txt);
  end;

  function get_state (p_name varchar2) return timestamp is
    l timestamp;
  begin
    select value_ts into l from ANOM_STATE where name = p_name;
    return l;
  exception
    when no_data_found then return null;
  end;

  function num_lit (p in number) return varchar2 is
  begin
    if p is null then
      return 'null';
    end if;
    return to_char(p, 'TM9', 'NLS_NUMERIC_CHARACTERS=''.,''');
  end;

  function date_lit (p in date) return varchar2 is
  begin
    return 'to_date(''' || to_char(p, 'YYYY-MM-DD HH24:MI:SS') || ''', ''YYYY-MM-DD HH24:MI:SS'')';
  end;

  -- a comma list of signal codes, each one checked against SIGNAL_DEF (no duplicates): the only way a name
  -- reaches generated SQL
  function split_signals (p_signals varchar2) return t_list is
    l   t_list;
    l_s varchar2(100);
    n   number;
  begin
    if p_signals is null then
      raise_application_error(-20207, 'no signal listed');
    end if;
    for i in 1 .. regexp_count(p_signals, ',') + 1 loop
      l_s := upper(trim(regexp_substr(p_signals, '[^,]+', 1, i)));
      if l_s is null then
        raise_application_error(-20207, 'empty signal name in the list');
      end if;
      select count(*) into n from SIGNAL_DEF where signal_code = l_s;
      if n = 0 then
        raise_application_error(-20207, 'unknown signal '||substr(l_s, 1, 30));
      end if;
      for j in 1 .. l.count loop
        if l(j) = l_s then
          raise_application_error(-20207, 'signal listed twice: '||l_s);
        end if;
      end loop;
      l(l.count + 1) := l_s;
    end loop;
    return l;
  end;

  function unpivot_list (p_signals in varchar2) return varchar2 is
    l   t_list := split_signals(p_signals);
    r   varchar2(4000);
  begin
    for i in 1 .. l.count loop
      r := r || case when i > 1 then ', ' end || l(i) || ' as ''' || l(i) || '''';
    end loop;
    return r;
  end;

  procedure check_window (p_from date, p_to date) is
  begin
    if p_from is null or p_to is null or p_from >= p_to then
      raise_application_error(-20200, 'the window must have p_from < p_to');
    end if;
  end;

  -- the complete rows of [p_from, p_to): ts + the signals, none of them null
  function rows_query (p_from in date, p_to in date, p_signals in varchar2) return varchar2 is
    l    t_list := split_signals(p_signals);
    l_c  varchar2(4000);
    l_nn varchar2(8000);
  begin
    check_window(p_from, p_to);
    for i in 1 .. l.count loop
      l_c  := l_c || ', f.' || l(i);
      l_nn := l_nn || ' and f.' || l(i) || ' is not null';
    end loop;
    return 'select f.ts' || l_c || ' from FEATURE_MINUTE f where f.ts >= ' || date_lit(p_from)
        || ' and f.ts < ' || date_lit(p_to) || l_nn;
  end;

  function chaos_filter return varchar2 is
  begin
    return ' and not exists (select 1 from INCIDENT_TRUTH t where f.ts >= ' || c_win_start
        || ' and f.ts <= ' || c_win_end || ')';
  end;

  -- v1.3: a TRAIN_EXCLUDE window removes a minute exactly as a chaos window does (both ends included)
  function exclude_filter return varchar2 is
  begin
    return ' and not exists (select 1 from TRAIN_EXCLUDE x where f.ts >= x.from_ts and f.ts <= x.to_ts)';
  end;

  -- every row training leaves out on purpose: the chaos windows and the exclusion windows
  function train_filter return varchar2 is
  begin
    return chaos_filter || exclude_filter;
  end;

  function training_query (p_from in date, p_to in date, p_signals in varchar2) return varchar2 is
  begin
    return rows_query(p_from, p_to, p_signals) || train_filter;
  end;

  procedure window_signals (p_from in date, p_to in date, p_used out varchar2, p_dropped out varchar2) is
    type t_num is table of number index by pls_integer;
    l_cand   t_list;
    l_codes  t_list;
    l_min    t_num;
    l_max    t_num;
    l_cnt    t_num;
    l_used   varchar2(4000);
    l_drop   json_array_t := json_array_t();
    l_o      json_object_t;
    l_cols   varchar2(4000);
    l_found  boolean;
    l_again  boolean := true;
    l_pass   pls_integer := 0;
    l_q      varchar2(32767);
    l_keep   varchar2(4000);
  begin
    check_window(p_from, p_to);
    for s in (select signal_code from SIGNAL_DEF where in_model = 'Y' order by display_seq) loop
      l_cand(l_cand.count + 1) := s.signal_code;
      l_cols := l_cols || case when l_cols is not null then ',' end || s.signal_code;
    end loop;
    if l_cand.count = 0 then
      raise_application_error(-20207, 'no signal has in_model = Y');
    end if;
    -- pass 1: min, max and count of each signal's values over the window minus the chaos windows and (v1.3) the
    -- exclusion windows (nulls allowed)
    l_q := 'select signal_code, min(val), max(val), count(val) from (select f.ts';
    for i in 1 .. l_cand.count loop
      l_q := l_q || ', f.' || l_cand(i);
    end loop;
    l_q := l_q || ' from FEATURE_MINUTE f where f.ts >= ' || date_lit(p_from) || ' and f.ts < ' || date_lit(p_to)
        || train_filter || ') unpivot (val for signal_code in (' || unpivot_list(l_cols) || ')) group by signal_code';
    execute immediate l_q bulk collect into l_codes, l_min, l_max, l_cnt;
    for i in 1 .. l_cand.count loop
      l_found := false;
      for j in 1 .. l_codes.count loop
        if l_codes(j) = l_cand(i) then
          l_found := true;
          if l_min(j) = l_max(j) then
            l_o := json_object_t();
            l_o.put('signal', l_cand(i));
            l_o.put('reason', 'constant');
            l_o.put('value', l_min(j));
            l_drop.append(l_o);
          else
            l_used := l_used || case when l_used is not null then ',' end || l_cand(i);
          end if;
        end if;
      end loop;
      if not l_found then
        l_o := json_object_t();
        l_o.put('signal', l_cand(i));
        l_o.put('reason', 'no values');
        l_drop.append(l_o);
      end if;
    end loop;
    -- pass 2..: once rows with a null in a used signal are removed, a used signal may turn constant; repeat
    while l_again and l_used is not null and l_pass < 5 loop
      l_pass := l_pass + 1;
      l_again := false;
      l_q := 'select signal_code, min(val), max(val), count(val) from (' || training_query(p_from, p_to, l_used)
          || ') unpivot (val for signal_code in (' || unpivot_list(l_used) || ')) group by signal_code';
      execute immediate l_q bulk collect into l_codes, l_min, l_max, l_cnt;
      l_keep := null;
      for s in (select regexp_substr(l_used, '[^,]+', 1, level) code from dual
                 connect by level <= regexp_count(l_used, ',') + 1) loop
        l_found := false;
        for j in 1 .. l_codes.count loop
          if l_codes(j) = s.code and l_min(j) != l_max(j) then
            l_found := true;
          end if;
        end loop;
        if l_found then
          l_keep := l_keep || case when l_keep is not null then ',' end || s.code;
        elsif l_codes.count > 0 then
          l_o := json_object_t();
          l_o.put('signal', s.code);
          l_o.put('reason', 'constant once rows with a null are removed');
          l_drop.append(l_o);
          l_again := true;
        end if;
      end loop;
      if l_codes.count = 0 then
        exit;   -- no complete row at all: train() reports the row count
      end if;
      l_used := l_keep;
    end loop;
    p_used := l_used;
    p_dropped := l_drop.to_string;
  end;

  function model_signals (p_model in varchar2) return varchar2 is
    l_used varchar2(4000);
  begin
    select listagg(j.sig, ',') within group (order by j.ord)
      into l_used
      from MODEL_REGISTRY r,
           json_table(r.signals_json, '$.used[*]' columns (ord for ordinality, sig varchar2(20) path '$')) j
     where r.model_name = p_model;
    if l_used is null then
      raise_application_error(-20204, 'model '||substr(p_model, 1, 128)||' is not registered or lists no signal');
    end if;
    return l_used;
  end;

  -- per (ts, signal): value, z (training mean/sd) and residual after the projection on components 1..p_k
  function pca_resid_sql (p_model in varchar2, p_k in pls_integer, p_rows_sql in varchar2) return varchar2 is
    l_sig varchar2(4000);
    n     number;
  begin
    if not regexp_like(p_model, '^[A-Z0-9_]{1,100}$') then
      raise_application_error(-20204, 'model name not acceptable');
    end if;
    select count(*) into n from user_mining_models where model_name = p_model;
    if n = 0 or p_k is null or p_k < 1 then
      raise_application_error(-20204, 'PCA model '||p_model||' missing or K invalid');
    end if;
    select listagg(signal_code, ',') within group (order by signal_code) into l_sig
      from MODEL_SIGNAL_STAT where model_name = p_model;
    return 'with x as (select ts, signal_code, val from (' || p_rows_sql || ') unpivot (val for signal_code in ('
        || unpivot_list(l_sig) || '))), '
        || 'z as (select x.ts, x.signal_code, x.val, (x.val - st.mu) / st.sd z from x join MODEL_SIGNAL_STAT st '
        || 'on st.model_name = ''' || p_model || ''' and st.signal_code = x.signal_code), '
        || 'v as (select feature_id, attribute_name signal_code, value from DM$VV' || p_model
        || ' where feature_id <= ' || p_k || '), '
        || 'p as (select z.ts, v.feature_id, sum(z.z * v.value) proj from z join v on v.signal_code = z.signal_code '
        || 'group by z.ts, v.feature_id), '
        || 'rc as (select p.ts, v.signal_code, sum(p.proj * v.value) zhat from p join v on v.feature_id = p.feature_id '
        || 'group by p.ts, v.signal_code) '
        || 'select z.ts, z.signal_code, z.val, z.z, z.z - nvl(rc.zhat, 0) resid from z left join rc '
        || 'on rc.ts = z.ts and rc.signal_code = z.signal_code';
  end;

  -- ---------------------------------------------------------------- settings
  -- p_settings keys a detector accepts; ODM keys go to the mining model, the others are this package's knobs
  function allowed (p_det varchar2, p_key varchar2) return varchar2 is
  begin
    return case
      when p_det = 'MSET' and p_key in ('MSET_ALERT_COUNT', 'MSET_ALERT_WINDOW', 'MSET_ALPHA_PROB', 'MSET_BETA_PROB',
           'MSET_STD_TOLERANCE', 'MSET_MEMORY_VECTORS', 'MSET_ADB_HEIGHT', 'MSET_HELDASIDE') then 'ODM'
      when p_det = 'SVM' and p_key in ('SVMS_OUTLIER_RATE', 'SVMS_STD_DEV', 'SVMS_TOLERANCE') then 'ODM'
      when p_det = 'EM' and p_key in ('EMCS_OUTLIER_RATE', 'EMCS_NUM_COMPONENTS') then 'ODM'
      when p_det = 'PCA' and p_key in ('VAR_PCT', 'RESID_PCT') then 'PAR'
      when p_det = 'STATIC' and p_key = 'PCT' then 'PAR'
      when p_det = 'SEASONAL' and p_key in ('K', 'MIN_ROWS_HOUR') then 'PAR'
    end;
  end;

  procedure defaults (p_det varchar2, p_odm in out nocopy t_set, p_par in out nocopy t_par) is
  begin
    case p_det
      when 'MSET' then
        p_odm('ALGO_NAME') := 'ALGO_MSET_SPRT';
        p_odm('PREP_AUTO') := 'ON';
        p_odm('MSET_ALERT_COUNT') := '3';        -- the spike's values: 3 anomalous minutes in a window of 5
        p_odm('MSET_ALERT_WINDOW') := '5';
      when 'SVM' then
        p_odm('ALGO_NAME') := 'ALGO_SUPPORT_VECTOR_MACHINES';
        p_odm('SVMS_KERNEL_FUNCTION') := 'SVMS_GAUSSIAN';
        p_odm('SVMS_OUTLIER_RATE') := '.01';
        p_odm('PREP_AUTO') := 'ON';
      when 'EM' then
        p_odm('ALGO_NAME') := 'ALGO_EXPECTATION_MAXIMIZATION';
        p_odm('EMCS_OUTLIER_RATE') := '.01';
        p_odm('PREP_AUTO') := 'ON';
      when 'PCA' then
        p_odm('ALGO_NAME') := 'ALGO_SINGULAR_VALUE_DECOMP';
        p_odm('PREP_AUTO') := 'OFF';             -- the rows are z-scored here (PREP_AUTO would only centre them)
        p_par('VAR_PCT') := 90;
        p_par('RESID_PCT') := 99.5;
      when 'STATIC' then
        p_par('PCT') := 99.5;
      when 'SEASONAL' then
        p_par('K') := 4;
        p_par('MIN_ROWS_HOUR') := 30;
    end case;
  end;

  procedure apply_settings (p_det varchar2, p_settings json, p_odm in out nocopy t_set, p_par in out nocopy t_par) is
    l_obj  json_object_t;
    l_keys json_key_list;
    l_k    varchar2(100);
    l_el   json_element_t;
    l_v    number;
    l_cls  varchar2(3);
  begin
    if p_settings is null then
      return;
    end if;
    l_obj := json_object_t(p_settings);
    l_keys := l_obj.get_keys;
    for i in 1 .. l_keys.count loop
      l_k := upper(l_keys(i));
      l_cls := allowed(p_det, l_k);
      if l_cls is null then
        raise_application_error(-20202, 'setting '||substr(l_k, 1, 40)||' is not accepted for '||p_det);
      end if;
      l_el := l_obj.get(l_keys(i));
      if not l_el.is_number then
        raise_application_error(-20202, 'setting '||l_k||' must be a JSON number');
      end if;
      l_v := l_obj.get_number(l_keys(i));
      if l_v is null or l_v <= 0
         or (l_k = 'VAR_PCT' and l_v not between 50 and 99.99)
         or (l_k = 'RESID_PCT' and l_v not between 50 and 99.999)
         or (l_k = 'PCT' and l_v not between 90 and 99.999)
         or (l_k = 'K' and l_v not between 1 and 20)
         or (l_k = 'MIN_ROWS_HOUR' and l_v not between 5 and 60) then
        raise_application_error(-20202, 'setting '||l_k||' out of range');
      end if;
      if l_cls = 'ODM' then
        p_odm(l_k) := num_lit(l_v);
      else
        p_par(l_k) := l_v;
      end if;
    end loop;
  end;

  function par_num (p_par t_par, p_key varchar2) return number is
  begin
    return p_par(p_key);
  end;

  -- ---------------------------------------------------------------- builders
  procedure build_mining (p_name varchar2, p_function varchar2, p_query varchar2, p_odm t_set) is
    v dbms_data_mining.setting_list;
    k varchar2(30);
  begin
    k := p_odm.first;
    while k is not null loop
      v(k) := p_odm(k);
      k := p_odm.next(k);
    end loop;
    dbms_data_mining.create_model2(
      model_name          => p_name,
      mining_function     => p_function,
      data_query          => p_query,
      set_list            => v,
      case_id_column_name => 'TS',
      target_column_name  => null);
  end;

  procedure build_pca (p_name varchar2, p_q varchar2, p_odm in out nocopy t_set, p_par in out nocopy t_par) is
    l_zq   varchar2(32767);
    l_k    number;
    l_thr  number;
    l_med  number;
    n      number;
  begin
    execute immediate 'insert into MODEL_SIGNAL_STAT (model_name, signal_code, mu, sd, n) '
                   || 'select :m, signal_code, avg(val), stddev_samp(val), count(*) from (' || p_q || ') '
                   || 'unpivot (val for signal_code in (' || unpivot_list(model_signals(p_name)) || ')) '
                   || 'group by signal_code' using p_name;
    select count(*), count(case when sd is null or sd = 0 then 1 end) into n, l_k
      from MODEL_SIGNAL_STAT where model_name = p_name;
    if l_k > 0 then
      raise_application_error(-20209, 'PCA: a signal has no spread over the training rows');
    end if;
    p_odm('FEAT_NUM_FEATURES') := to_char(n);
    l_zq := 'select q.ts';
    for s in (select signal_code, mu, sd from MODEL_SIGNAL_STAT where model_name = p_name order by signal_code) loop
      l_zq := l_zq || ', (q.' || s.signal_code || ' - ' || num_lit(s.mu) || ') / ' || num_lit(s.sd) || ' '
           || s.signal_code;
    end loop;
    l_zq := l_zq || ' from (' || p_q || ') q';
    build_mining(p_name, 'FEATURE_EXTRACTION', l_zq, p_odm);
    -- K: the fewest leading components whose squared singular values reach VAR_PCT of the total
    execute immediate 'select min(feature_id) from (select feature_id, sum(value * value) over (order by feature_id) '
                   || '/ sum(value * value) over () cum from DM$VE' || p_name || ') where cum >= :p'
      into l_k using par_num(p_par, 'VAR_PCT') / 100;
    -- the threshold: the RESID_PCT percentile of the training rows' reconstruction errors
    execute immediate 'select percentile_cont(:p) within group (order by err), median(err) from ('
                   || 'select ts, sum(resid * resid) err from (' || pca_resid_sql(p_name, l_k, p_q) || ') group by ts)'
      into l_thr, l_med using par_num(p_par, 'RESID_PCT') / 100;
    if l_thr is null or l_thr <= 0 then
      raise_application_error(-20209, 'PCA: no positive residual threshold');
    end if;
    p_par('K') := l_k;
    p_par('THRESHOLD') := l_thr;
    p_par('TRAIN_ERR_MEDIAN') := l_med;
  end;

  procedure build_static (p_name varchar2, p_q varchar2, p_par t_par) is
    l_pct number := par_num(p_par, 'PCT');
  begin
    execute immediate 'insert into THRESHOLD_DEF (model_name, signal_code, lo, hi) '
                   || 'select :m, signal_code, case when instr(:lows, '','' || signal_code || '','') > 0 then '
                   || 'percentile_cont(:plo) within group (order by val) end, '
                   || 'percentile_cont(:phi) within group (order by val) from (' || p_q || ') '
                   || 'unpivot (val for signal_code in (' || unpivot_list(model_signals(p_name)) || ')) '
                   || 'group by signal_code'
      using p_name, c_lower_sigs, (100 - l_pct) / 100, l_pct / 100;
  end;

  procedure build_seasonal (p_name varchar2, p_q varchar2, p_par t_par) is
  begin
    execute immediate 'insert into SEASONAL_BASE (model_name, signal_code, hour_utc, med, mad, n) '
      || 'with x as (select ts, signal_code, val from (' || p_q || ') unpivot (val for signal_code in ('
      || unpivot_list(model_signals(p_name)) || '))), '
      || 'h as (select signal_code, to_number(to_char(ts, ''HH24'')) hr, val from x), '
      || 'mh as (select signal_code, hr, median(val) med, count(*) n from h group by signal_code, hr), '
      || 'dh as (select h.signal_code, h.hr, median(abs(h.val - mh.med)) mad_h from h join mh '
      ||        'on mh.signal_code = h.signal_code and mh.hr = h.hr group by h.signal_code, h.hr), '
      || 'ma as (select signal_code, median(val) med_all from x group by signal_code), '
      || 'da as (select x.signal_code, median(abs(x.val - ma.med_all)) mad_all, avg(abs(x.val - ma.med_all)) mean_abs '
      ||        'from x join ma on ma.signal_code = x.signal_code group by x.signal_code) '
      || 'select :m, mh.signal_code, mh.hr, mh.med, '
      || 'case when dh.mad_h > 0 then dh.mad_h when da.mad_all > 0 then da.mad_all else da.mean_abs / 1.4826 end, mh.n '
      || 'from mh join dh on dh.signal_code = mh.signal_code and dh.hr = mh.hr '
      || 'join da on da.signal_code = mh.signal_code '
      || 'where mh.n >= :minrows and (dh.mad_h > 0 or da.mad_all > 0 or da.mean_abs > 0)'
      using p_name, par_num(p_par, 'MIN_ROWS_HOUR');
    if sql%rowcount = 0 then
      raise_application_error(-20208, 'SEASONAL: no UTC hour has '||p_par('MIN_ROWS_HOUR')||' training rows');
    end if;
  end;

  -- the effective settings as JSON: the mining model's stored settings (as Oracle keeps them), then the knobs
  function settings_doc (p_name varchar2, p_mining boolean, p_par t_par) return clob is
    l_o  json_object_t := json_object_t();
    k    varchar2(30);
  begin
    if p_mining then
      for s in (select setting_name, setting_value from user_mining_model_settings
                 where model_name = p_name order by setting_name) loop
        l_o.put(s.setting_name, s.setting_value);
      end loop;
    end if;
    k := p_par.first;
    while k is not null loop
      l_o.put(k, p_par(k));
      k := p_par.next(k);
    end loop;
    return l_o.to_clob;
  end;

  procedure drop_mining_if_any (p_name varchar2) is
    n number;
  begin
    select count(*) into n from user_mining_models where model_name = p_name;
    if n > 0 then
      dbms_data_mining.drop_model(p_name, true);
    end if;
  end;

  -- the chaos windows made current before training (train() and batch_truth_begin): a link failure refuses
  -- (-20210); a target without CHAOS_RUN has no runs, which is logged once an hour
  procedure ensure_truth (p_who varchar2) is
  begin
    refresh_truth;
  exception
    when others then
      if absent_code(sqlcode) then
        -- no chaos run can exist on the target yet: nothing to exclude beyond the copy (logged once an hour)
        if nvl(get_state('TRUTH_SKIP_LOGGED'), timestamp '2000-01-01 00:00:00')
           < sys_extract_utc(systimestamp) - interval '60' minute then
          log_event('WARN', 'event=train_truth_absent model='||p_who||' error="'||sqlerrm||'"');
          set_state('TRUTH_SKIP_LOGGED', sys_extract_utc(systimestamp));
          commit;
        end if;
      else
        raise_application_error(-20210, 'INCIDENT_TRUTH could not be refreshed; training refused: '||sqlerrm);
      end if;
  end ensure_truth;

  -- ---------------------------------------------------------------- train
  function train (p_detector in varchar2, p_from in date, p_to in date, p_variant in varchar2 default 'DEFAULT',
                  p_settings in json default null, p_activate in varchar2 default 'N') return varchar2 is
    l_det     varchar2(10)  := upper(trim(p_detector));
    l_var     varchar2(100) := upper(trim(p_variant));
    l_name    varchar2(128);
    l_used    varchar2(4000);
    l_dropped varchar2(32767);
    l_q       varchar2(32767);
    l_n       number;
    l_all     number;
    l_chaos   number;
    l_excl    number;     -- v1.3: rows in an exclusion window and in no chaos window
    l_t0      timestamp;
    l_secs    number;
    l_size    number;
    l_odm     t_set;
    l_par     t_par;
    l_sig     json_object_t;
    l_arr     json_array_t := json_array_t();
    l_mining  boolean;
    l_err     varchar2(4000);
    l_bt      varchar2(4000);
    l_created boolean := false;
    l_doc     clob;
    l_note    varchar2(4000);
    n         number;
  begin
    if l_det is null or l_det not in ('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL') then
      raise_application_error(-20201, 'detector must be MSET, SVM, EM, PCA, STATIC or SEASONAL'
                              ||' (IFOREST is trained by tools/iforest.py)');
    end if;
    check_window(p_from, p_to);
    if p_to > now_utc + 1 / 1440 then
      raise_application_error(-20200, 'the window may not end in the future');
    end if;
    if l_var is null or not regexp_like(l_var, '^[A-Z0-9_]{1,30}$') then
      raise_application_error(-20200, 'variant must be 1-30 of A-Z, 0-9, _');
    end if;
    if nvl(p_activate, '?') not in ('Y', 'N') then
      raise_application_error(-20200, 'p_activate must be Y or N');
    end if;
    l_mining := l_det in ('MSET', 'SVM', 'EM', 'PCA');
    l_name := 'ANOM_' || l_det || '_' || l_var || '_' || to_char(sys_extract_utc(systimestamp), 'YYYYMMDDHH24MI');
    select count(*) into n from (select model_name from MODEL_REGISTRY where model_name = l_name
                                 union all select model_name from user_mining_models where model_name = l_name);
    if n > 0 then
      raise_application_error(-20203, 'model '||l_name||' exists: use another variant or wait a minute');
    end if;
    defaults(l_det, l_odm, l_par);
    apply_settings(l_det, p_settings, l_odm, l_par);

    -- the chaos windows must be current: a link failure refuses training; a target without CHAOS_RUN has no runs.
    -- v1.2: inside a batch (batch_truth_begin) the batch's one refresh stands in for this one.
    if g_batch_ts is null or g_batch_ts < sys_extract_utc(systimestamp) - numtodsinterval(c_batch_hours, 'HOUR') then
      ensure_truth(l_name);
    end if;

    window_signals(p_from, p_to, l_used, l_dropped);
    if l_used is null then
      raise_application_error(-20207, 'no usable signal in the window');
    end if;
    l_q := training_query(p_from, p_to, l_used);
    execute immediate 'select count(*) from (' || l_q || ')' into l_n;
    if l_n < c_min_rows then
      raise_application_error(-20208, 'only '||l_n||' training rows in the window ('||c_min_rows||' needed)');
    end if;
    execute immediate 'select count(*) from FEATURE_MINUTE f where f.ts >= :a and f.ts < :b' into l_all
      using p_from, p_to;
    execute immediate 'select count(*) from FEATURE_MINUTE f where f.ts >= :a and f.ts < :b and exists ('
                   || 'select 1 from INCIDENT_TRUTH t where f.ts >= ' || c_win_start || ' and f.ts <= '
                   || c_win_end || ')' into l_chaos using p_from, p_to;
    -- v1.3: the exclusion windows' rows that no chaos window already took (so the four counts add up)
    execute immediate 'select count(*) from FEATURE_MINUTE f where f.ts >= :a and f.ts < :b' || chaos_filter
                   || ' and exists (select 1 from TRAIN_EXCLUDE x where f.ts >= x.from_ts and f.ts <= x.to_ts)'
      into l_excl using p_from, p_to;

    l_sig := json_object_t();
    for s in (select regexp_substr(l_used, '[^,]+', 1, level) code from dual
               connect by level <= regexp_count(l_used, ',') + 1) loop
      l_arr.append(s.code);
    end loop;
    l_sig.put('used', l_arr);
    l_sig.put('dropped', json_array_t.parse(l_dropped));
    l_sig.put('rows_in_window', l_all);
    l_sig.put('rows_in_chaos_windows', l_chaos);
    l_sig.put('rows_in_exclusion_windows', l_excl);
    l_sig.put('rows_with_null', l_all - l_chaos - l_excl - l_n);
    l_sig.put('rows_trained', l_n);

    l_doc := l_sig.to_clob;   -- a PL/SQL JSON object's method cannot be called inside SQL (ORA-40573)
    insert into MODEL_REGISTRY (model_name, detector, variant, train_from, train_to, n_rows, signals_json,
                                settings_json, status, created_ts, status_ts, note)
    values (l_name, l_det, l_var, p_from, p_to, l_n, l_doc, '{}', 'CANDIDATE',
            sys_extract_utc(systimestamp), sys_extract_utc(systimestamp), null);
    l_created := true;

    l_t0 := systimestamp;
    case l_det
      when 'MSET'     then build_mining(l_name, 'CLASSIFICATION', l_q, l_odm);
      when 'SVM'      then build_mining(l_name, 'CLASSIFICATION', l_q, l_odm);
      when 'EM'       then build_mining(l_name, 'CLASSIFICATION', l_q, l_odm);
      when 'PCA'      then build_pca(l_name, l_q, l_odm, l_par);
      when 'STATIC'   then build_static(l_name, l_q, l_par);
      when 'SEASONAL' then build_seasonal(l_name, l_q, l_par);
    end case;
    l_secs := extract(day from (systimestamp - l_t0)) * 86400 + extract(hour from (systimestamp - l_t0)) * 3600
            + extract(minute from (systimestamp - l_t0)) * 60 + extract(second from (systimestamp - l_t0));

    -- size: the mining model's size as Oracle reports it, plus the bytes of this package's definition rows
    select nvl(max(model_size), 0) into l_size from user_mining_models where model_name = l_name;
    select l_size + nvl(sum(vsize(signal_code) + nvl(vsize(lo), 0) + vsize(hi)), 0) into l_size
      from THRESHOLD_DEF where model_name = l_name;
    select l_size + nvl(sum(vsize(signal_code) + vsize(hour_utc) + vsize(med) + vsize(mad)), 0) into l_size
      from SEASONAL_BASE where model_name = l_name;
    select l_size + nvl(sum(vsize(signal_code) + vsize(mu) + vsize(sd)), 0) into l_size
      from MODEL_SIGNAL_STAT where model_name = l_name;

    -- private functions cannot appear in SQL: the values are computed first
    l_doc  := settings_doc(l_name, l_mining, l_par);
    l_note := 'trained on '||l_n||' rows ('||ts(p_from)||' to '||ts(p_to)||')';
    update MODEL_REGISTRY
       set settings_json = l_doc,
           train_seconds = round(l_secs, 3),
           size_bytes    = l_size,
           note          = l_note
     where model_name = l_name;
    commit;
    log_event('INFO', 'event=model_trained model='||l_name||' rows='||l_n||' seconds='||round(l_secs, 3)
                      ||' size_bytes='||l_size);
    if p_activate = 'Y' then
      activate(l_name);
    end if;
    return l_name;
  exception
    when others then
      l_err := sqlerrm;
      l_bt  := dbms_utility.format_error_backtrace;
      rollback;
      if l_created then
        -- remove what this call made (the mining model, the registry row and its definitions)
        begin
          drop_mining_if_any(l_name);
          delete from MODEL_REGISTRY where model_name = l_name;
          commit;
        exception
          when others then
            log_event('ERROR', 'event=train_cleanup_failed model='||l_name||' error="'||sqlerrm||'"');
        end;
      end if;
      log_event('ERROR', 'event=train_failed detector='||l_det||' model='||l_name||' error="'||l_err
                         ||'" backtrace="'||replace(rtrim(l_bt, chr(10)), chr(10), ' | ')||'"');
      raise;
  end train;

  procedure train_all (p_from in date, p_to in date, p_activate in varchar2 default 'N') is
    type t_dets is table of varchar2(10);
    l_dets   t_dets := t_dets('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL');
    l_name   varchar2(128);
    l_failed varchar2(4000);
  begin
    batch_truth_begin;     -- v1.2: one INCIDENT_TRUTH refresh for the batch (raises -20210 when it fails)
    for i in 1 .. l_dets.count loop
      begin
        l_name := train(l_dets(i), p_from, p_to, 'DEFAULT', null, p_activate);
        dbms_output.put_line('  trained '||l_name);
      exception
        when others then
          -- train() has logged it; carry on with the next detector
          l_failed := l_failed || case when l_failed is not null then '; ' end || l_dets(i) || ': ' || sqlerrm;
      end;
    end loop;
    batch_truth_end;
    if l_failed is not null then
      raise_application_error(-20212, 'train_all: '||substr(l_failed, 1, 3000));
    end if;
  end;

  -- ---------------------------------------------------------------- lifecycle
  procedure activate (p_model in varchar2) is
    r MODEL_REGISTRY%rowtype;
    n number;
  begin
    begin
      select * into r from MODEL_REGISTRY where model_name = p_model for update;
    exception
      when no_data_found then
        raise_application_error(-20204, 'model '||substr(p_model, 1, 128)||' is not registered');
    end;
    if r.detector = 'IFOREST' then
      raise_application_error(-20205, 'IFOREST is an offline reference and is never active');
    end if;
    if r.status = 'ACTIVE' then
      rollback;
      return;
    end if;
    if r.detector in ('MSET', 'SVM', 'EM', 'PCA') then
      select count(*) into n from user_mining_models where model_name = p_model;
      if n = 0 then
        raise_application_error(-20211, 'model '||p_model||' has no mining model');
      end if;
    end if;
    update MODEL_REGISTRY set status = 'RETIRED', status_ts = sys_extract_utc(systimestamp)
     where detector = r.detector and status = 'ACTIVE';
    update MODEL_REGISTRY set status = 'ACTIVE', status_ts = sys_extract_utc(systimestamp)
     where model_name = p_model;
    commit;
    log_event('INFO', 'event=model_activated model='||p_model||' detector='||r.detector);
  end;

  procedure retire (p_model in varchar2) is
    n number;
  begin
    select count(*) into n from MODEL_REGISTRY where model_name = p_model;
    if n = 0 then
      raise_application_error(-20204, 'model '||substr(p_model, 1, 128)||' is not registered');
    end if;
    update MODEL_REGISTRY set status = 'RETIRED', status_ts = sys_extract_utc(systimestamp)
     where model_name = p_model and status != 'RETIRED';
    commit;
  end;

  procedure drop_model (p_model in varchar2) is
    l_status varchar2(10);
  begin
    begin
      select status into l_status from MODEL_REGISTRY where model_name = p_model for update;
    exception
      when no_data_found then
        raise_application_error(-20204, 'model '||substr(p_model, 1, 128)||' is not registered');
    end;
    if l_status = 'ACTIVE' then
      raise_application_error(-20206, 'model '||p_model||' is ACTIVE and is never dropped');
    end if;
    drop_mining_if_any(p_model);
    delete from MODEL_REGISTRY where model_name = p_model;   -- definitions go with it (on delete cascade)
    commit;
  end;

  procedure purge_retired (p_days in number default 14) is
  begin
    if p_days is null or p_days < 14 then
      raise_application_error(-20200, 'purge_retired keeps retired models for at least 14 days');
    end if;
    for r in (select model_name from MODEL_REGISTRY
               where status = 'RETIRED' and status_ts < sys_extract_utc(systimestamp) - numtodsinterval(p_days, 'DAY')) loop
      drop_model(r.model_name);
      log_event('INFO', 'event=model_purged model='||r.model_name);
    end loop;
  end;

  -- ---------------------------------------------------------------- INCIDENT_TRUTH
  procedure close_chaos_link is
    e_not_open exception;
    pragma exception_init(e_not_open, -2081);
  begin
    dbms_session.close_database_link('ANOM_CHAOS_LINK');
  exception
    when e_not_open then
      null;   -- ORA-02081: the link is not open in this session, which is the state we want
  end;

  -- v1.2: 96's INCIDENT_PLAN mirror of the target's CHAOS_PLAN, over this session's link (called by name, so 94 does
  -- not depend on 96; skipped while 96 v1.3 is not installed). Never raises: a failure is logged at most once an hour
  -- and the truth refresh stands.
  procedure mirror_plan_hook is
    n      number;
    l_last timestamp;
  begin
    select count(*) into n from user_procedures where object_name = 'PKG_ANOM_GRADE' and procedure_name = 'MIRROR_PLAN';
    if n = 0 then
      return;
    end if;
    execute immediate 'begin PKG_ANOM_GRADE.mirror_plan; end;';
    commit;
  exception
    when others then
      rollback;
      l_last := get_state('PLAN_MIRROR_LOGGED');
      if l_last is null or l_last < sys_extract_utc(systimestamp) - interval '60' minute then
        log_event('WARN', 'event=plan_mirror_failed error="'||sqlerrm||'" (logged at most once an hour)');
        set_state('PLAN_MIRROR_LOGGED', sys_extract_utc(systimestamp));
        commit;
      end if;
  end mirror_plan_hook;

  -- the copy (and the plan mirror); p_close: close the link afterwards (refresh_truth) or keep it (truth_loop).
  -- A failure always closes the link, is logged (except a target without CHAOS_RUN) and re-raised.
  procedure refresh_core (p_close boolean) is
    l_rows number;
    l_gone number;
    l_err  varchar2(4000);
    l_code number;
  begin
    set_state('TRUTH_ATTEMPT', sys_extract_utc(systimestamp));
    commit;
    execute immediate q'[
      merge into INCIDENT_TRUTH t
      using (select run_id, substr(scenario, 1, 30) scenario, substr(intensity, 1, 4) intensity,
                    substr(source, 1, 10) source, cast(requested_ts as date) requested_ts,
                    cast(start_ts as date) start_ts, cast(planned_end_ts as date) planned_end_ts,
                    cast(end_ts as date) end_ts, substr(status, 1, 10) status, substr(restored, 1, 1) restored,
                    substr(job_name, 1, 128) job_name, substr(note, 1, 400) note
               from SHOP.CHAOS_RUN@ANOM_CHAOS_LINK) s
         on (t.run_id = s.run_id)
       when matched then update set
            t.scenario = s.scenario, t.intensity = s.intensity, t.source = s.source,
            t.requested_ts = s.requested_ts, t.start_ts = s.start_ts, t.planned_end_ts = s.planned_end_ts,
            t.end_ts = s.end_ts, t.status = s.status, t.restored = s.restored, t.job_name = s.job_name,
            t.note = s.note, t.refreshed_ts = sys_extract_utc(systimestamp), t.gone_ts = null
       when not matched then insert (run_id, scenario, intensity, source, requested_ts, start_ts, planned_end_ts,
            end_ts, status, restored, job_name, note, refreshed_ts)
            values (s.run_id, s.scenario, s.intensity, s.source, s.requested_ts, s.start_ts, s.planned_end_ts,
            s.end_ts, s.status, s.restored, s.job_name, s.note, sys_extract_utc(systimestamp))]';
    l_rows := sql%rowcount;
    -- a run the target no longer has stays (its window is still excluded from training) but is marked
    execute immediate q'[
      update INCIDENT_TRUTH t set t.gone_ts = sys_extract_utc(systimestamp)
       where t.run_id > 0 and t.gone_ts is null
         and not exists (select 1 from SHOP.CHAOS_RUN@ANOM_CHAOS_LINK r where r.run_id = t.run_id)]';
    l_gone := sql%rowcount;
    set_state('TRUTH_REFRESH', sys_extract_utc(systimestamp), 'rows='||l_rows||' gone='||l_gone);
    commit;   -- also ends the distributed transaction
    mirror_plan_hook;
    if p_close then
      close_chaos_link;
    end if;
    if l_gone > 0 then
      log_event('WARN', 'event=truth_rows_gone rows='||l_gone||' (kept with gone_ts)');
    end if;
  exception
    when others then
      l_code := sqlcode;
      l_err := sqlerrm;
      rollback;
      close_chaos_link;
      if not absent_code(l_code) then
        log_event('ERROR', 'event=truth_refresh_failed error="'||l_err||'"');
      end if;
      raise;
  end refresh_core;

  procedure refresh_truth is
  begin
    refresh_core(true);
  end refresh_truth;

  procedure refresh_truth_job is
    l_last    timestamp := get_state('TRUTH_ATTEMPT');
    l_running number;
    l_logged  timestamp;
    l_code    number;
    l_err     varchar2(4000);
  begin
    select count(*) into l_running from INCIDENT_TRUTH where status = 'RUNNING' and gone_ts is null;
    if l_running = 0 and l_last is not null
       and l_last > sys_extract_utc(systimestamp) - numtodsinterval(c_truth_hourly * 1440, 'MINUTE') then
      return;   -- not due: nothing is running and the last look is less than an hour old
    end if;
    refresh_truth;
  exception
    when others then
      l_code := sqlcode;
      l_err := sqlerrm;
      if absent_code(l_code) then
        -- the target has no CHAOS_RUN yet (93 not installed) or CHAOS_CTL lacks the grant: log hourly, skip
        l_logged := get_state('TRUTH_SKIP_LOGGED');
        if l_logged is null or l_logged < sys_extract_utc(systimestamp) - interval '60' minute then
          log_event('WARN', 'event=truth_skipped reason="'||l_err||'" (SHOP.CHAOS_RUN not readable over '
                            ||'ANOM_CHAOS_LINK yet; logged at most once an hour)');
          set_state('TRUTH_SKIP_LOGGED', sys_extract_utc(systimestamp));
          commit;
        end if;
      else
        raise;
      end if;
  end refresh_truth_job;

  -- ---------------------------------------------------------------- v1.2: the truth loop and the batch refresh
  function utc_now return timestamp is
  begin
    return sys_extract_utc(systimestamp);
  end;

  function seconds_of (p_iv interval day to second) return number is
  begin
    return extract(day from p_iv) * 86400 + extract(hour from p_iv) * 3600
         + extract(minute from p_iv) * 60 + extract(second from p_iv);
  end;

  -- ANOM_TRUTH_JOB's phase second, from its repeat_interval (FREQ=MINUTELY;BYSECOND=<s>, set at install)
  function truth_job_second return pls_integer is
    l_rep user_scheduler_jobs.repeat_interval%type;
    l_sec number;
  begin
    begin
      select repeat_interval into l_rep from user_scheduler_jobs where job_name = c_truth_job;
    exception
      when no_data_found then
        raise_application_error(-20213, 'truth_loop: job '||c_truth_job||' not found; pass p_second');
    end;
    l_sec := to_number(regexp_substr(l_rep, 'BYSECOND=([0-9]{1,2})(;|$)', 1, 1, 'i', 1));
    if l_sec is null or l_sec not between 0 and 59 then
      raise_application_error(-20213, 'truth_loop: no BYSECOND 0-59 in '||c_truth_job||'.repeat_interval ('||l_rep||')');
    end if;
    return l_sec;
  end;

  -- the job's flag: false once the job is disabled (or dropped), which ends a job-mode loop
  function truth_job_enabled return boolean is
    l_on user_scheduler_jobs.enabled%type;
  begin
    select enabled into l_on from user_scheduler_jobs where job_name = c_truth_job;
    return l_on = 'TRUE';
  exception
    when no_data_found then
      return false;
  end;

  -- a stop of this session, not a refresh failure: these end the loop (stop_job, kill, shutdown)
  function is_stop (p_code number) return boolean is
  begin
    return p_code in (-1013, -28, -31, -1089, -1092);
  end;

  -- sleeps until p_until in chunks of at most c_chunk_sec; in job mode returns false once the job is disabled
  function sleep_until (p_until timestamp, p_job boolean) return boolean is
    l_wait number;
  begin
    loop
      l_wait := seconds_of(p_until - utc_now);
      exit when l_wait <= 0;
      dbms_session.sleep(least(l_wait, c_chunk_sec));
      if p_job and not truth_job_enabled then
        return false;
      end if;
    end loop;
    return true;
  end;

  procedure truth_loop (p_until in date default null, p_second in pls_integer default null) is
    l_job     constant boolean     := p_until is null;
    l_mode    constant varchar2(4) := case when p_until is null then 'job' else 'test' end;
    l_sec     pls_integer;
    l_due     timestamp;
    l_until   timestamp;
    l_iter    pls_integer := 0;
    l_ok      pls_integer := 0;
    l_failed  pls_integer := 0;
    l_stopped boolean     := false;
    l_code    number;
    l_err     varchar2(4000);
    l_bt      varchar2(4000);
    l_logged  timestamp;
  begin
    if p_second is not null and p_second not between 0 and 59 then
      raise_application_error(-20213, 'truth_loop: p_second must be 0-59');
    end if;
    if p_until is not null and p_until <= cast(utc_now as date) then
      raise_application_error(-20213, 'truth_loop: p_until must be in the future (UTC)');
    end if;
    l_sec := nvl(p_second, truth_job_second);
    -- the first due time: the next 5-minute mark of the hour at second l_sec (one at most c_late_sec ago counts)
    l_due := cast(trunc(cast(utc_now as date), 'HH') as timestamp) + numtodsinterval(l_sec, 'SECOND');
    while l_due < utc_now - numtodsinterval(c_late_sec, 'SECOND') loop
      l_due := l_due + numtodsinterval(c_truth_every, 'MINUTE');
    end loop;
    -- a job run covers the UTC hour of its first due time and ends at HH:59:50; the job's next start takes the next
    l_until := case when l_job then cast(trunc(cast(l_due as date), 'HH') as timestamp) + interval '1' hour
                                    - interval '10' second
                    else cast(p_until as timestamp) end;

    while l_due < l_until loop
      if not sleep_until(l_due, l_job) then
        l_stopped := true;
        exit;
      end if;
      l_iter := l_iter + 1;
      begin
        refresh_core(false);             -- the link stays open for the next refresh
        l_ok := l_ok + 1;
      exception
        when others then
          l_code := sqlcode;
          l_err  := sqlerrm;
          if is_stop(l_code) then
            raise;
          end if;
          -- refresh_core rolled back, closed the link and logged the ERROR; the next refresh opens a fresh session
          rollback;
          l_failed := l_failed + 1;
          if absent_code(l_code) then
            l_logged := get_state('TRUTH_SKIP_LOGGED');
            if l_logged is null or l_logged < sys_extract_utc(systimestamp) - interval '60' minute then
              log_event('WARN', 'event=truth_skipped reason="'||l_err||'" (SHOP.CHAOS_RUN not readable over '
                                ||'ANOM_CHAOS_LINK yet; logged at most once an hour)');
              set_state('TRUTH_SKIP_LOGGED', sys_extract_utc(systimestamp));
              commit;
            end if;
          else
            log_event('WARN', 'event=truth_loop_refresh_failed mode='||l_mode||' due='||ts(cast(l_due as date))
                              ||' error="'||l_err||'" action=next_refresh_in_'||c_truth_every||'_min');
          end if;
      end;
      -- the next mark; marks that passed during a slow refresh are skipped (the next refresh copies everything)
      l_due := l_due + numtodsinterval(c_truth_every, 'MINUTE');
      while l_due < utc_now - numtodsinterval(c_late_sec, 'SECOND') loop
        l_due := l_due + numtodsinterval(c_truth_every, 'MINUTE');
      end loop;
    end loop;
    -- a job run keeps its session until HH:59:50, so the session is replaced at a fixed time once an hour
    if l_job and not l_stopped then
      l_stopped := not sleep_until(l_until, true);
    end if;
    commit;
    close_chaos_link;
    if not l_job or l_failed > 0 then
      log_event(case when l_job then 'WARN' else 'INFO' end,
                'event=truth_loop_end mode='||l_mode||' second='||l_sec||' refreshes='||l_iter||' ok='||l_ok
                ||' failed='||l_failed||' stopped='||case when l_stopped then 'Y' else 'N' end);
    end if;
  exception
    when others then
      l_code := sqlcode;
      l_err  := sqlerrm;
      l_bt   := dbms_utility.format_error_backtrace;
      rollback;
      if is_stop(l_code) then
        -- a stop is not a refresh failure: WARN, then let the session end (the scheduler records the stop)
        log_event('WARN', 'event=truth_loop_stopped mode='||l_mode||' refreshes='||l_iter||' error="'||l_err||'"');
        raise;
      end if;
      log_event('ERROR', 'event=truth_loop_failed mode='||l_mode||' error="'||l_err||'" backtrace="'
                         ||replace(rtrim(l_bt, chr(10)), chr(10), ' | ')||'"');
      raise;
  end truth_loop;

  procedure batch_truth_begin is
  begin
    g_batch_ts := null;
    ensure_truth('batch');
    g_batch_ts := sys_extract_utc(systimestamp);
  end batch_truth_begin;

  procedure batch_truth_end is
  begin
    g_batch_ts := null;
  end batch_truth_end;

  -- ---------------------------------------------------------------- v1.3: training exclusion windows
  function add_exclusion (p_from in date, p_to in date, p_reason in varchar2, p_by in varchar2) return number is
    pragma autonomous_transaction;
    l_id     number;
    l_reason varchar2(400) := substr(trim(p_reason), 1, 400);
    l_by     varchar2(255) := substr(trim(p_by), 1, 255);
  begin
    if p_from is null or p_to is null or p_from >= p_to then
      raise_application_error(-20214, 'add_exclusion: the window must have p_from < p_to (UTC)');
    end if;
    if (p_to - p_from) * 24 > c_excl_max_hours then
      raise_application_error(-20214, 'add_exclusion: a window may last at most '||c_excl_max_hours||' hours');
    end if;
    if l_reason is null or l_by is null then
      raise_application_error(-20214, 'add_exclusion: the reason and the creator are required');
    end if;
    insert into TRAIN_EXCLUDE (from_ts, to_ts, reason, created_by, created_ts)
    values (p_from, p_to, l_reason, l_by, sys_extract_utc(systimestamp))
    returning exclude_id into l_id;
    commit;
    log_event('INFO', 'event=train_exclusion_added id='||l_id||' from='||ts(p_from)||' to='||ts(p_to)
                      ||' by="'||l_by||'" reason="'||l_reason||'"');
    return l_id;
  exception
    when others then
      rollback;
      raise;
  end add_exclusion;
end PKG_ANOM_TRAIN;
/
show errors package body PKG_ANOM_TRAIN

declare
  n number;
begin
  select count(*) into n from user_objects where object_name = 'PKG_ANOM_TRAIN' and status != 'VALID';
  if n > 0 then
    raise_application_error(-20900, '94: PKG_ANOM_TRAIN did not compile');
  end if;
end;
/

-- ------------------------------------------------------------------ ANOM_TRUTH_JOB (v1.2: the truth loop, enabled)
declare
  l_rep constant varchar2(100) := 'FREQ=MINUTELY;BYSECOND=20';
  l_on  timestamp with time zone;
  n     number;
begin
  select count(*) into n from user_scheduler_jobs where job_name = 'ANOM_TRUTH_JOB';
  if n = 0 then
    dbms_scheduler.create_job(
      job_name        => 'ANOM_TRUTH_JOB',
      job_type        => 'STORED_PROCEDURE',
      job_action      => 'PKG_ANOM_TRAIN.TRUTH_LOOP',
      start_date      => systimestamp at time zone 'UTC',
      repeat_interval => l_rep,
      enabled         => false,
      auto_drop       => false,
      comments        => 'App 900: INCIDENT_TRUTH every 5 min over one ANOM_CHAOS_LINK session an hour (truth_loop)');
    dbms_output.put_line('  ANOM_TRUTH_JOB created ('||l_rep||', UTC)');
  else
    dbms_scheduler.set_attribute('ANOM_TRUTH_JOB', 'job_action', 'PKG_ANOM_TRAIN.TRUTH_LOOP');
    dbms_scheduler.set_attribute('ANOM_TRUTH_JOB', 'repeat_interval', l_rep);
    dbms_scheduler.set_attribute('ANOM_TRUTH_JOB', 'comments',
                                 'App 900: INCIDENT_TRUTH every 5 min over one ANOM_CHAOS_LINK session an hour (truth_loop)');
    dbms_output.put_line('  ANOM_TRUTH_JOB present; set to PKG_ANOM_TRAIN.TRUTH_LOOP, '||l_rep||' (UTC)');
  end if;
  dbms_scheduler.set_attribute('ANOM_TRUTH_JOB', 'logging_level', dbms_scheduler.logging_failed_runs);
  l_on := systimestamp;
  dbms_scheduler.enable('ANOM_TRUTH_JOB');
  -- the loop starts at the next second 20: wait for it (at most 75 s) so a job that cannot run fails here
  for i in 1 .. 75 loop
    select count(*) into n from user_scheduler_jobs
     where job_name = 'ANOM_TRUTH_JOB' and (state = 'RUNNING' or last_start_date >= l_on);
    exit when n > 0;
    dbms_session.sleep(1);
  end loop;
  if n = 0 then
    raise_application_error(-20900, '94: ANOM_TRUTH_JOB was enabled but did not start within 75 s');
  end if;
end;
/

-- one refresh now (best effort: a target without CHAOS_RUN is reported, not fatal)
begin
  PKG_ANOM_TRAIN.refresh_truth;
  dbms_output.put_line('  INCIDENT_TRUTH refreshed');
exception
  when others then
    if sqlcode in (-942, -2019, -41900, -1031) then
      dbms_output.put_line('  INCIDENT_TRUTH not refreshed: SHOP.CHAOS_RUN not readable yet ('||sqlerrm||')');
    else
      raise;
    end if;
end;
/

select '  INCIDENT_TRUTH rows: '||count(*) as truth from INCIDENT_TRUTH;
select '  '||job_name||': '||enabled||', '||state||', next '||to_char(next_run_date, 'YYYY-MM-DD HH24:MI:SS TZR') as job
  from user_scheduler_jobs where job_name = 'ANOM_TRUTH_JOB';
