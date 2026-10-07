-- v1.2 - ANOMOPS in ora26ai / ORCLPDB1 (the observer) for application 900, phase 4 (builder M): scoring, the
--        per-minute signal attribution and the alert episodes. Run as ANOMOPS after 94
--        (tools/anom_obs_run.sh run 95_anom_score.sql <log>). No password here. Contract v1.2 section 7, v1.8 13.
--        v1.2: (phase 5c, PLAN 7a "top 3 including ties") the stored details are tie-safe. MSET's weights come in
--              fifths and its third weight was shared past the fifth stored row in 319 of 325 flagged minutes of
--              model ANOM_MSET_INTERIM_202610011817, so a fixed top 5 could drop an expected signal that ties the
--              third. The core now asks PREDICTION_DETAILS for the top c_detail_topn (40) signals (MSET: the topN
--              form over (order by ts), which works on 23.26.1: up to 22 named per minute; the 1-argument form
--              returns 5), numbers them by weight (row_number, Oracle's @rank breaks ties) and keep_details keeps,
--              for MSET, SVM, EM and PCA, the first 5 AND every signal whose rank() by weight is <= 3; for STATIC
--              and SEASONAL every breached signal (grading ranks the signals that fired by these weights). At
--              most c_detail_max (40) rows a minute; a minute over it is cut to 40 and logged (WARN
--              event=detail_bound_hit). SCORE_DETAIL / SCORE_EVAL_DETAIL rank checks widened to 1..40 (rank =
--              position by weight, unique per minute, as before). ALERT / ALERT_EVAL.TOP_SIGNALS stay the first 5.
--              Specification: keep_details added at its end, nothing else changed. 02-Oct-2026.
--        v1.1: (integrator, contract v1.2 / PLAN 7a) STATIC and SEASONAL open an episode only when the SAME signal
--              breaches in 3 consecutive minutes (the OEM way): every scored minute keeps its full breach list
--              (SCORE_MINUTE / SCORE_EVAL.BREACHED, strongest first), ALERT_STATE.SIG_RUNS carries the per-signal
--              run lengths between scoring runs, ALERT / ALERT_EVAL.FIRED names the signal(s) whose run opened the
--              episode ("the metric that fired", graded by 96 v1.1). Columns are added when absent. 01-Oct-2026.
--        v1.0: first version, 01-Oct-2026.
--
--        SCORE_MINUTE      live: one row per FEATURE_MINUTE minute and ACTIVE model: flag (1 = anomalous; null =
--                          not scorable: a null in a used signal, or a SEASONAL hour without a baseline) and score.
--                          MSET, SVM, EM: score = PREDICTION_PROBABILITY of class 0 (the anomalous class);
--                          PCA: reconstruction error / threshold; STATIC: the largest multiple of a threshold
--                          (value / hi, or lo / value); SEASONAL: the largest |robust z| / K. flag = 1 exactly when
--                          the detector says anomalous (PREDICTION = 0, or score > 1).
--        SCORE_DETAIL      the named signals of every flagged minute, rank = position by weight: MSET, SVM, EM from
--                          PREDICTION_DETAILS (an XMLType on 23.26.1, parsed with XMLTABLE); PCA |residual| in z
--                          units; STATIC the breached signals by multiple of the threshold; SEASONAL the breached
--                          signals by |z|. v1.2: MSET, SVM, EM, PCA keep the first 5 and every signal tied at the
--                          third-highest weight (rank() <= 3); STATIC and SEASONAL keep every breach; at most 40.
--        SCORE_EVAL(_DETAIL) the same for grading, under a run tag, written by score_range (and tools/iforest.py);
--                          never touches SCORE_MINUTE.
--        ALERT             live alert episodes: MSET opens on its own flag (its alert count/window already
--                          debounce); SVM, EM and PCA open at the 3rd consecutive flagged minute; STATIC and SEASONAL
--                          open when the SAME signal has breached in 3 consecutive minutes (PLAN 7a: breaches of
--                          different signals do not chain); minutes more than 90 s apart are not consecutive;
--                          start_ts = the minute the episode opens (the page time); an open episode takes every
--                          later flagged minute (last_ts, peak_score) and closes at its 10th unflagged minute after
--                          the last flagged one (end_ts = that minute). top_signals = the opening minute's top 5
--                          (comma list); FIRED (STATIC, SEASONAL) = the signals whose 3-minute run opened it, in the
--                          minute's breach order. ALERT_STATE carries the episode state between runs (SIG_RUNS:
--                          ",SIGNAL:run," per breaching signal, capped at 3). ALERT_EVAL holds the same episodes
--                          built from SCORE_EVAL. A flagged STATIC/SEASONAL minute without a breach list (a row
--                          written before v1.1) counts as one unnamed signal '#', i.e. the v1.0 rule.
--
--        PKG_ANOM_SCORE.score_new (ANOM_SCORE_JOB): every ACTIVE model (IFOREST is never active) scores the
--        FEATURE_MINUTE minutes after its last scored one (first run: from the later of its train_to and 6 hours
--        ago; at most one day per run). MSET scores `over (order by ts)` over the new minutes plus the preceding 120
--        minutes of context and stores only the new ones. A minute with a null in a used signal that is younger than
--        5 minutes is left for the next run (its APP_MINUTE row may still arrive); older, it is stored with a null
--        flag. Each model is scored, its alerts advanced and committed on its own; a failure is logged and the
--        others go on. score_range(model, from, to, run_tag) does the same into SCORE_EVAL for grading.
--        ANOM_SCORE_JOB is created DISABLED (the integrator enables it once a model is ACTIVE); a re-run keeps the
--        job's enabled state.
--        Idempotent: tables are created when absent and checked, the package and the job are re-applied.
set serveroutput on size unlimited format wrapped
set verify off
set linesize 200
set feedback off
whenever sqlerror exit failure rollback

begin
  if sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '95: run in the PDB (ORCLPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, '95: run as ANOMOPS');
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
  mk('SCORE_MINUTE', q'[
    create table SCORE_MINUTE (
      ts          date           not null,
      model_name  varchar2(128)  not null,
      detector    varchar2(10)   not null,
      flag        number(1),
      score       number,
      scored_ts   timestamp      not null,
      breached    varchar2(1000),
      constraint score_minute_pk   primary key (ts, model_name),
      constraint score_minute_f_ck check (flag in (0, 1))
    )]');

  mk('SCORE_DETAIL', q'[
    create table SCORE_DETAIL (
      ts            date           not null,
      model_name    varchar2(128)  not null,
      rank          number(2)      not null,
      signal_code   varchar2(20)   not null,
      weight        number,
      actual_value  number,
      constraint score_detail_pk   primary key (ts, model_name, rank),
      constraint score_detail_r_ck check (rank between 1 and 40)
    )]');

  mk('SCORE_EVAL', q'[
    create table SCORE_EVAL (
      run_tag     varchar2(40)   not null,
      ts          date           not null,
      model_name  varchar2(128)  not null,
      detector    varchar2(10)   not null,
      flag        number(1),
      score       number,
      scored_ts   timestamp      not null,
      breached    varchar2(1000),
      constraint score_eval_pk   primary key (run_tag, model_name, ts),
      constraint score_eval_f_ck check (flag in (0, 1))
    )]');

  mk('SCORE_EVAL_DETAIL', q'[
    create table SCORE_EVAL_DETAIL (
      run_tag       varchar2(40)   not null,
      ts            date           not null,
      model_name    varchar2(128)  not null,
      rank          number(2)      not null,
      signal_code   varchar2(20)   not null,
      weight        number,
      actual_value  number,
      constraint score_eval_detail_pk   primary key (run_tag, model_name, ts, rank),
      constraint score_eval_detail_r_ck check (rank between 1 and 40)
    )]');

  mk('ALERT', q'[
    create table ALERT (
      alert_id     number generated always as identity,
      model_name   varchar2(128)  not null,
      detector     varchar2(10)   not null,
      start_ts     date           not null,
      last_ts      date           not null,
      end_ts       date,
      status       varchar2(6)    not null,
      top_signals  varchar2(400),
      peak_score   number,
      opened_ts    timestamp      not null,
      fired        varchar2(400),
      constraint alert_pk    primary key (alert_id),
      constraint alert_st_ck check (status in ('OPEN', 'CLOSED')),
      constraint alert_end_ck check ((status = 'OPEN' and end_ts is null) or (status = 'CLOSED' and end_ts is not null))
    )]');

  mk('ALERT_STATE', q'[
    create table ALERT_STATE (
      model_name     varchar2(128)  not null,
      last_ts        date,
      prev_ts        date,
      consec         number         not null,
      unflag         number         not null,
      open_alert_id  number,
      sig_runs       varchar2(4000),
      constraint alert_state_pk primary key (model_name)
    )]');

  mk('ALERT_EVAL', q'[
    create table ALERT_EVAL (
      run_tag      varchar2(40)   not null,
      model_name   varchar2(128)  not null,
      episode_no   number         not null,
      detector     varchar2(10)   not null,
      start_ts     date           not null,
      last_ts      date           not null,
      end_ts       date,
      status       varchar2(6)    not null,
      top_signals  varchar2(400),
      peak_score   number,
      fired        varchar2(400),
      constraint alert_eval_pk    primary key (run_tag, model_name, episode_no),
      constraint alert_eval_st_ck check (status in ('OPEN', 'CLOSED'))
    )]');

  -- session-private work tables of the scoring core
  mk('SCORE_TMP', q'[
    create global temporary table SCORE_TMP (
      ts     date,
      flag   number(1),
      score  number
    ) on commit preserve rows]');
  mk('SCORE_TMP_DETAIL', q'[
    create global temporary table SCORE_TMP_DETAIL (
      ts            date,
      rnk           number,
      signal_code   varchar2(30),
      weight        number,
      actual_value  number
    ) on commit preserve rows]');
  mk('SCORE_TMP_RESID', q'[
    create global temporary table SCORE_TMP_RESID (
      ts           date,
      signal_code  varchar2(30),
      val          number,
      resid        number
    ) on commit preserve rows]');
end;
/

-- v1.1 columns on tables a v1.0 install created (nullable: existing rows keep null, i.e. the v1.0 meaning)
declare
  procedure addcol (p_table varchar2, p_col varchar2, p_type varchar2) is
    n number;
  begin
    select count(*) into n from user_tab_columns where table_name = p_table and column_name = p_col;
    if n = 0 then
      execute immediate 'alter table ' || p_table || ' add (' || p_col || ' ' || p_type || ')';
      dbms_output.put_line('  column '||p_table||'.'||p_col||' added');
    end if;
  end;
begin
  addcol('SCORE_MINUTE', 'BREACHED', 'varchar2(1000)');
  addcol('SCORE_EVAL',   'BREACHED', 'varchar2(1000)');
  addcol('ALERT_STATE',  'SIG_RUNS', 'varchar2(4000)');
  addcol('ALERT',        'FIRED',    'varchar2(400)');
  addcol('ALERT_EVAL',   'FIRED',    'varchar2(400)');
end;
/

-- v1.2: the detail tables of a v1.0-v1.1 install allow rank 1..5 only; widened to 1..40 (tie-safe details). The
-- live scorer writes SCORE_DETAIL every minute, so each DDL waits up to 30 s for its row locks instead of failing.
alter session set ddl_lock_timeout = 30;
declare
  procedure widen (p_table varchar2, p_name varchar2) is
    l_cond varchar2(4000);
  begin
    select max(search_condition_vc) into l_cond from user_constraints
     where table_name = p_table and constraint_name = p_name and constraint_type = 'C';
    if l_cond is null or lower(regexp_replace(l_cond, '\s+', ' ')) != 'rank between 1 and 40' then
      if l_cond is not null then
        execute immediate 'alter table ' || p_table || ' drop constraint ' || p_name;
      end if;
      execute immediate 'alter table ' || p_table || ' add constraint ' || p_name || ' check (rank between 1 and 40)';
      dbms_output.put_line('  ' || p_table || '.' || p_name || ': rank between 1 and 40 (was: '
                           || nvl(l_cond, 'none') || ')');
    end if;
  end;
begin
  widen('SCORE_DETAIL', 'SCORE_DETAIL_R_CK');
  widen('SCORE_EVAL_DETAIL', 'SCORE_EVAL_DETAIL_R_CK');
end;
/
alter session set ddl_lock_timeout = 0;

declare
  n number;
  procedure ix (p_name varchar2, p_ddl varchar2) is
  begin
    select count(*) into n from user_indexes where index_name = p_name;
    if n = 0 then
      execute immediate p_ddl;
      dbms_output.put_line('  index '||p_name||' created');
    end if;
  end;
begin
  ix('SCORE_MINUTE_MODEL_IX', 'create index SCORE_MINUTE_MODEL_IX on SCORE_MINUTE (model_name, ts)');
  ix('ALERT_MODEL_IX', 'create index ALERT_MODEL_IX on ALERT (model_name, status, start_ts)');
end;
/

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
      raise_application_error(-20900, '95: '||p_table||' lacks column(s) '||l_missing);
    end if;
  end;
begin
  need('SCORE_MINUTE',      'TS,MODEL_NAME,DETECTOR,FLAG,SCORE,SCORED_TS,BREACHED');
  need('SCORE_DETAIL',      'TS,MODEL_NAME,RANK,SIGNAL_CODE,WEIGHT,ACTUAL_VALUE');
  need('SCORE_EVAL',        'RUN_TAG,TS,MODEL_NAME,DETECTOR,FLAG,SCORE,SCORED_TS,BREACHED');
  need('SCORE_EVAL_DETAIL', 'RUN_TAG,TS,MODEL_NAME,RANK,SIGNAL_CODE,WEIGHT,ACTUAL_VALUE');
  need('ALERT',             'ALERT_ID,MODEL_NAME,DETECTOR,START_TS,LAST_TS,END_TS,STATUS,TOP_SIGNALS,PEAK_SCORE,OPENED_TS,FIRED');
  need('ALERT_STATE',       'MODEL_NAME,LAST_TS,PREV_TS,CONSEC,UNFLAG,OPEN_ALERT_ID,SIG_RUNS');
  need('ALERT_EVAL',        'RUN_TAG,MODEL_NAME,EPISODE_NO,DETECTOR,START_TS,LAST_TS,END_TS,STATUS,TOP_SIGNALS,PEAK_SCORE,FIRED');
  need('SCORE_TMP',         'TS,FLAG,SCORE');
  need('SCORE_TMP_DETAIL',  'TS,RNK,SIGNAL_CODE,WEIGHT,ACTUAL_VALUE');
  need('SCORE_TMP_RESID',   'TS,SIGNAL_CODE,VAL,RESID');
  dbms_output.put_line('  table shapes checked');
end;
/

comment on table SCORE_MINUTE is 'App 900: live per-minute flag and score of every ACTIVE model (1 = anomalous; null = not scorable)';
comment on table SCORE_DETAIL is 'App 900: the named signals of each flagged live minute (first 5 plus ties at the third weight; STATIC/SEASONAL every breach), with weights and values';
comment on table SCORE_EVAL is 'App 900: per-minute flags and scores for grading, by run tag (never live)';
comment on table SCORE_EVAL_DETAIL is 'App 900: the named signals of each flagged grading minute (as SCORE_DETAIL)';
comment on table ALERT is 'App 900: live alert episodes (MSET on its flag; SVM/EM/PCA after 3 consecutive flagged minutes; STATIC/SEASONAL after 3 consecutive breaches of the same signal; close after 10 unflagged)';
comment on column SCORE_MINUTE.BREACHED is 'STATIC/SEASONAL: every signal past its threshold this minute, strongest first (comma list)';
comment on column SCORE_EVAL.BREACHED is 'STATIC/SEASONAL: every signal past its threshold this minute, strongest first (comma list)';
comment on column ALERT.FIRED is 'STATIC/SEASONAL: the signal(s) whose 3 consecutive breaches opened the episode';
comment on column ALERT_EVAL.FIRED is 'STATIC/SEASONAL: the signal(s) whose 3 consecutive breaches opened the episode';
comment on table ALERT_EVAL is 'App 900: alert episodes rebuilt from SCORE_EVAL for grading';
comment on table ALERT_STATE is 'App 900: live episode state per model between scoring runs';

-- ------------------------------------------------------------------ the scorer
create or replace package PKG_ANOM_SCORE authid definer as
  -- v1.1 - app 900 scoring, attribution and alert episodes (see 95_anom_score.sql). All times UTC.
  --        v1.1: STATIC/SEASONAL episodes need 3 consecutive breaches of the same signal (contract v1.2).

  c_context_min  constant pls_integer := 120;   -- MSET: minutes of context scored before the first new minute
  c_wait_min     constant pls_integer := 5;     -- an incomplete minute younger than this waits for the next run
  c_consec       constant pls_integer := 3;     -- non-MSET detectors: consecutive flagged minutes to open
                                                -- (STATIC, SEASONAL: consecutive breaches of the same signal)
  c_unnamed      constant varchar2(1) := '#';   -- the breach list of a flagged minute that has none (pre-v1.1 row)
  c_close_after  constant pls_integer := 10;    -- unflagged minutes that close an episode
  c_gap_sec      constant number      := 90;    -- minutes further apart than this are not consecutive

  -- ANOM_SCORE_JOB's action: every ACTIVE model scores its new minutes and advances its alerts. Commits per model.
  procedure score_new;

  -- grading: scores [p_from, p_to) with p_model (any status; not IFOREST) into SCORE_EVAL / SCORE_EVAL_DETAIL under
  -- p_run_tag (A-Z, 0-9, _; replaces that tag's rows of the model in the range), records score_ms_per_min. Commits.
  procedure score_range (p_model in varchar2, p_from in date, p_to in date, p_run_tag in varchar2);

  -- the scoring core (also a test hook): scores the rows of p_rows_sql (ts + the model's signals, no nulls) and keeps
  -- those with ts >= p_keep_from in SCORE_TMP / SCORE_TMP_DETAIL (both cleared first; the details are what
  -- keep_details keeps: v1.2 the first 5 plus every signal tied at the third-highest weight for MSET, SVM, EM and
  -- PCA, and every breached signal for STATIC and SEASONAL). For MSET p_rows_sql should include the context before
  -- p_keep_from. No commit.
  procedure score_query (p_model in varchar2, p_rows_sql in varchar2, p_keep_from in date);

  -- live episodes: advances ALERT / ALERT_STATE over p_model's SCORE_MINUTE rows after its state. No commit.
  procedure process_alerts (p_model in varchar2);

  -- grading episodes: rebuilds ALERT_EVAL for p_run_tag (all models of the tag, or p_model) from SCORE_EVAL. No commit.
  procedure build_eval_alerts (p_run_tag in varchar2, p_model in varchar2 default null);

  -- the same-signal rule's bookkeeping (pure functions, also test hooks). A run list is ",SIG:n,SIG:n," (null = none).
  -- runs_next: the run lengths after one more flagged minute whose breach list is p_breached ("A,B", strongest
  -- first): a breaching signal's run grows by one (capped at c_consec), every other signal drops out.
  function runs_next (p_runs in varchar2, p_breached in varchar2) return varchar2;
  -- the longest run in the list (0 for null)
  function runs_max (p_runs in varchar2) return pls_integer;
  -- the signals of p_breached (in its order) whose run in p_runs has reached c_consec, comma list (null = none)
  function runs_fired (p_runs in varchar2, p_breached in varchar2) return varchar2;

  -- v1.2 (added at the end of the specification) PLAN 7a's tie rule for the stored details (also a test hook):
  -- prunes SCORE_TMP_DETAIL (rnk = 1..n per minute, by weight) to what SCORE_DETAIL / SCORE_EVAL_DETAIL keep. MSET,
  -- SVM, EM, PCA: positions 1..5 and every row whose rank() by weight (desc, nulls last) is <= 3; STATIC, SEASONAL:
  -- every row (each one is a breach). Then at most 40 rows a minute: a minute over it keeps positions 1..40 and one
  -- WARN (event=detail_bound_hit, naming p_model) is logged for the call. No commit.
  procedure keep_details (p_model in varchar2, p_det in varchar2);
end PKG_ANOM_SCORE;
/

create or replace package body PKG_ANOM_SCORE as
  -- v1.2 (tie-safe details: topN 40, keep_details); v1.1
  c_detail_topn constant pls_integer := 40;   -- v1.2: PREDICTION_DETAILS' topN (more than the 35 signals)
  c_detail_max  constant pls_integer := 40;   -- v1.2: stored detail rows per minute at most (SCORE_*DETAIL check)
  c_top_shown   constant pls_integer := 5;    -- v1.2: positions always kept (and TOP_SIGNALS' length)
  c_tie_rank    constant pls_integer := 3;    -- v1.2: PLAN 7a: every signal whose rank() by weight is <= 3
  type t_state is record (
    consec   pls_integer := 0,
    unflag   pls_integer := 0,
    prev_ts  date,
    open     boolean := false,
    start_ts date,
    last_ts  date,
    peak     number,
    alert_id number,
    episode  pls_integer := 0,
    runs     varchar2(4000),          -- STATIC/SEASONAL: per-signal run lengths (",SIG:n,")
    fired    varchar2(400));          -- STATIC/SEASONAL: the signals whose run opened the current episode

  c_xml_cols constant varchar2(400) :=
    'columns name varchar2(30) path ''@name'', actual number path ''@actualValue'', '
    || 'weight number path ''@weight'', rnk number path ''@rank''';

  procedure log_event (p_severity varchar2, p_message varchar2) is
    pragma autonomous_transaction;
  begin
    insert into COLLECT_LOG (log_ts, severity, message)
    values (sys_extract_utc(systimestamp), p_severity, substr(p_message, 1, 4000));
    commit;
  end;

  function now_utc return date is
  begin
    return cast(sys_extract_utc(systimestamp) as date);
  end;

  function secs_since (p_t0 timestamp) return number is
    l interval day(3) to second(6) := systimestamp - p_t0;
  begin
    return extract(day from l) * 86400 + extract(hour from l) * 3600 + extract(minute from l) * 60
         + extract(second from l);
  end;

  -- a registered model's detector and settings; the name is also checked as a plain identifier, because it is
  -- written into generated SQL
  procedure model_info (p_model varchar2, p_det out varchar2, p_settings out clob) is
  begin
    if p_model is null or not regexp_like(p_model, '^[A-Z0-9_.]{1,100}$') then
      raise_application_error(-20304, 'model name not acceptable');
    end if;
    select detector, settings_json into p_det, p_settings from MODEL_REGISTRY where model_name = p_model;
  exception
    when no_data_found then
      raise_application_error(-20304, 'model '||p_model||' is not registered');
  end;

  -- "(f.AAS is null or f.DBTIME is null ...)" for a model's signals (each one checked against SIGNAL_DEF)
  function incomplete_pred (p_signals varchar2) return varchar2 is
    l_s varchar2(30);
    r   varchar2(8000);
    n   number;
  begin
    for i in 1 .. regexp_count(p_signals, ',') + 1 loop
      l_s := regexp_substr(p_signals, '[^,]+', 1, i);
      select count(*) into n from SIGNAL_DEF where signal_code = l_s;
      if n = 0 then
        raise_application_error(-20304, 'unknown signal in the model: '||substr(l_s, 1, 30));
      end if;
      r := r || case when i > 1 then ' or ' end || 'f.' || l_s || ' is null';
    end loop;
    return '(' || r || ')';
  end;

  -- ---------------------------------------------------------------- the scoring core
  procedure score_query (p_model in varchar2, p_rows_sql in varchar2, p_keep_from in date) is
    l_det  varchar2(10);
    l_set  clob;
    l_k    pls_integer;
    l_thr  number;
    l_kz   number;
    l_unp  varchar2(4000);
  begin
    model_info(p_model, l_det, l_set);
    delete from SCORE_TMP;
    delete from SCORE_TMP_DETAIL;
    delete from SCORE_TMP_RESID;
    case l_det
      when 'MSET' then
        -- one ordered pass over context + new rows; only the new rows are kept. PREDICTION 0 = anomalous.
        execute immediate
          'insert into SCORE_TMP (ts, flag, score) select ts, case when pred = 0 then 1 else 0 end, p0 from ('
          || 'select ts, prediction(' || p_model || ' using *) over (order by ts) pred, '
          || 'prediction_probability(' || p_model || ', 0 using *) over (order by ts) p0 from (' || p_rows_sql
          || ')) where ts >= :k' using p_keep_from;
        -- the class-0 details of the flagged minutes. v1.2: the topN form (class 0, top c_detail_topn) over
        -- (order by ts): measured on 23.26.1 on 02-Oct, it names up to 22 signals a minute, the 1-argument form only
        -- 5 (the default topN), which cut ties at the third weight. Numbered by weight; Oracle's @rank breaks ties.
        execute immediate
          'insert into SCORE_TMP_DETAIL (ts, rnk, signal_code, weight, actual_value) '
          || 'select s.ts, row_number() over (partition by s.ts order by x.weight desc nulls last, x.rnk), x.name, '
          || 'x.weight, x.actual from (select ts, pred, det from ('
          || 'select ts, prediction(' || p_model || ' using *) over (order by ts) pred, '
          || 'prediction_details(' || p_model || ', 0, ' || c_detail_topn || ' using *) over (order by ts) det from ('
          || p_rows_sql || '))) s, '
          || 'xmltable(''/Details/Attribute'' passing s.det ' || c_xml_cols || ') x '
          || 'where s.ts >= :k and s.pred = 0' using p_keep_from;
      when 'SVM' then
        execute immediate
          'insert into SCORE_TMP (ts, flag, score) select ts, case when prediction(' || p_model
          || ' using *) = 0 then 1 else 0 end, prediction_probability(' || p_model || ', 0 using *) from ('
          || p_rows_sql || ') where ts >= :k' using p_keep_from;
        execute immediate
          'insert into SCORE_TMP_DETAIL (ts, rnk, signal_code, weight, actual_value) '
          || 'select s.ts, row_number() over (partition by s.ts order by x.weight desc nulls last, x.rnk), x.name, '
          || 'x.weight, x.actual from (select ts, prediction_details(' || p_model
          || ', 0, ' || c_detail_topn || ' using *) det from (' || p_rows_sql || ') where ts >= :k and prediction('
          || p_model || ' using *) = 0) s, xmltable(''/Details/Attribute'' passing s.det ' || c_xml_cols || ') x'
          using p_keep_from;
      when 'EM' then
        execute immediate
          'insert into SCORE_TMP (ts, flag, score) select ts, case when prediction(' || p_model
          || ' using *) = 0 then 1 else 0 end, prediction_probability(' || p_model || ', 0 using *) from ('
          || p_rows_sql || ') where ts >= :k' using p_keep_from;
        execute immediate
          'insert into SCORE_TMP_DETAIL (ts, rnk, signal_code, weight, actual_value) '
          || 'select s.ts, row_number() over (partition by s.ts order by x.weight desc nulls last, x.rnk), x.name, '
          || 'x.weight, x.actual from (select ts, prediction_details(' || p_model
          || ', 0, ' || c_detail_topn || ' using *) det from (' || p_rows_sql || ') where ts >= :k and prediction('
          || p_model || ' using *) = 0) s, xmltable(''/Details/Attribute'' passing s.det ' || c_xml_cols || ') x'
          using p_keep_from;
      when 'PCA' then
        l_k   := json_value(l_set, '$.K' returning number);
        l_thr := json_value(l_set, '$.THRESHOLD' returning number);
        if l_k is null or l_thr is null or l_thr <= 0 then
          raise_application_error(-20305, 'PCA model '||p_model||' lacks K or THRESHOLD');
        end if;
        execute immediate 'insert into SCORE_TMP_RESID (ts, signal_code, val, resid) select ts, signal_code, val, resid '
          || 'from (' || PKG_ANOM_TRAIN.pca_resid_sql(p_model, l_k, p_rows_sql) || ') where ts >= :k'
          using p_keep_from;
        insert into SCORE_TMP (ts, flag, score)
        select ts, case when sum(resid * resid) / l_thr > 1 then 1 else 0 end, sum(resid * resid) / l_thr
          from SCORE_TMP_RESID group by ts;
        -- v1.2: every signal of a flagged minute, numbered by |residual|; keep_details prunes them below
        insert into SCORE_TMP_DETAIL (ts, rnk, signal_code, weight, actual_value)
        select ts, rnk, signal_code, w, val from (
          select r.ts, r.signal_code, abs(r.resid) w, r.val,
                 row_number() over (partition by r.ts order by abs(r.resid) desc nulls last, r.signal_code) rnk
            from SCORE_TMP_RESID r join SCORE_TMP s on s.ts = r.ts and s.flag = 1);
      when 'STATIC' then
        -- per signal the multiple of its threshold: value / hi above, lo / value below (100 when the threshold
        -- is 0 and the value is past it); > 1 exactly when the threshold is breached
        l_unp := PKG_ANOM_TRAIN.unpivot_list(PKG_ANOM_TRAIN.model_signals(p_model));
        execute immediate 'insert into SCORE_TMP_RESID (ts, signal_code, val, resid) '
          || 'select x.ts, x.signal_code, x.val, least(100, greatest('
          || 'case when t.hi > 0 then x.val / t.hi when x.val > t.hi then 100 else 0 end, '
          || 'case when t.lo is null then 0 when x.val > 0 then t.lo / x.val when x.val < t.lo then 100 else 0 end)) '
          || 'from (select ts, signal_code, val from (' || p_rows_sql || ') unpivot (val for signal_code in ('
          || l_unp || ')) where ts >= :k) x join THRESHOLD_DEF t on t.model_name = :m and t.signal_code = x.signal_code'
          using p_keep_from, p_model;
        insert into SCORE_TMP (ts, flag, score)
        select ts, case when max(resid) > 1 then 1 else 0 end, max(resid) from SCORE_TMP_RESID group by ts;
        -- every breached signal, strongest first (v1.2: the stored details keep them all too, up to 40)
        insert into SCORE_TMP_DETAIL (ts, rnk, signal_code, weight, actual_value)
        select ts, rnk, signal_code, w, val from (
          select r.ts, r.signal_code, r.resid w, r.val,
                 row_number() over (partition by r.ts order by r.resid desc, d.display_seq) rnk
            from SCORE_TMP_RESID r join SIGNAL_DEF d on d.signal_code = r.signal_code
           where r.resid > 1);
      when 'SEASONAL' then
        -- robust z against the same UTC hour; a minute whose hour has no baseline gets no row here (null later)
        l_kz  := json_value(l_set, '$.K' returning number);
        l_unp := PKG_ANOM_TRAIN.unpivot_list(PKG_ANOM_TRAIN.model_signals(p_model));
        execute immediate 'insert into SCORE_TMP_RESID (ts, signal_code, val, resid) '
          || 'select x.ts, x.signal_code, x.val, abs(x.val - b.med) / (1.4826 * b.mad) '
          || 'from (select ts, signal_code, val from (' || p_rows_sql || ') unpivot (val for signal_code in ('
          || l_unp || ')) where ts >= :k) x join SEASONAL_BASE b on b.model_name = :m '
          || 'and b.signal_code = x.signal_code and b.hour_utc = to_number(to_char(x.ts, ''HH24''))'
          using p_keep_from, p_model;
        insert into SCORE_TMP (ts, flag, score)
        select ts, case when max(resid) > l_kz then 1 else 0 end, max(resid) / l_kz from SCORE_TMP_RESID group by ts;
        insert into SCORE_TMP_DETAIL (ts, rnk, signal_code, weight, actual_value)
        select ts, rnk, signal_code, w, val from (
          select r.ts, r.signal_code, r.resid w, r.val,
                 row_number() over (partition by r.ts order by r.resid desc, d.display_seq) rnk
            from SCORE_TMP_RESID r join SIGNAL_DEF d on d.signal_code = r.signal_code
           where r.resid > l_kz);
      else
        raise_application_error(-20301, 'detector '||l_det||' is not scored in the database (tools/iforest.py)');
    end case;
    keep_details(p_model, l_det);    -- v1.2: PLAN 7a's tie rule decides what is stored
  end score_query;

  -- v1.2: see the specification. rnk is 1..n per minute in weight order, so both kept sets are prefixes of it and
  -- the rows left stay numbered 1..k without a hole.
  procedure keep_details (p_model in varchar2, p_det in varchar2) is
    l_over pls_integer;
    l_most pls_integer;
    l_ts   date;
  begin
    if p_det not in ('STATIC', 'SEASONAL') then
      delete from SCORE_TMP_DETAIL d
       where (d.ts, d.rnk) in (select ts, rnk
                                 from (select ts, rnk,
                                              rank() over (partition by ts order by weight desc nulls last) wrank
                                         from SCORE_TMP_DETAIL)
                                where rnk > c_top_shown and wrank > c_tie_rank);
    end if;
    select count(*), max(n), min(ts) into l_over, l_most, l_ts
      from (select ts, count(*) n from SCORE_TMP_DETAIL group by ts having count(*) > c_detail_max);
    if l_over > 0 then
      delete from SCORE_TMP_DETAIL where rnk > c_detail_max;
      log_event('WARN', 'event=detail_bound_hit model='||substr(p_model, 1, 128)||' detector='||p_det
                        ||' minutes='||l_over||' first='||to_char(l_ts, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                        ||' most_rows='||l_most||' kept='||c_detail_max);
    end if;
  end keep_details;

  -- STATIC/SEASONAL: a minute's full breach list from the work table, strongest first (null for other detectors)
  function breached_of (p_det varchar2, p_ts date) return varchar2 is
    r varchar2(1000);
  begin
    if p_det not in ('STATIC', 'SEASONAL') then
      return null;
    end if;
    select substr(listagg(signal_code, ',') within group (order by rnk), 1, 1000) into r
      from SCORE_TMP_DETAIL where ts = p_ts;
    return r;
  end;

  -- the minutes of [p_from, p_to) the core gave no score (a null in a used signal, a SEASONAL hour without a
  -- baseline) are kept with a null flag, so every minute of the range has a row
  procedure add_unscored (p_from date, p_to date) is
  begin
    insert into SCORE_TMP (ts, flag, score)
    select f.ts, null, null from FEATURE_MINUTE f
     where f.ts >= p_from and f.ts < p_to and not exists (select 1 from SCORE_TMP s where s.ts = f.ts);
  end;

  -- ---------------------------------------------------------------- the same-signal rule (v1.1)
  -- the run length of p_sig in a run list (0 when absent); signal codes hold no comma or colon
  function run_of (p_runs varchar2, p_sig varchar2) return pls_integer is
    l_key   varchar2(40) := ',' || p_sig || ':';
    l_pos   pls_integer := instr(p_runs, l_key);
    l_start pls_integer;
  begin
    if p_runs is null or l_pos = 0 then
      return 0;
    end if;
    l_start := l_pos + length(l_key);
    return to_number(substr(p_runs, l_start, instr(p_runs, ',', l_start) - l_start));
  end;

  function runs_next (p_runs in varchar2, p_breached in varchar2) return varchar2 is
    r   varchar2(4000);
    l_s varchar2(40);
  begin
    for i in 1 .. nvl(regexp_count(p_breached, ',') + 1, 0) loop
      l_s := trim(regexp_substr(p_breached, '[^,]+', 1, i));
      continue when l_s is null or instr(r, ',' || l_s || ':') > 0;
      r := nvl(r, ',') || l_s || ':' || least(run_of(p_runs, l_s) + 1, c_consec) || ',';
    end loop;
    return r;
  end;

  function runs_max (p_runs in varchar2) return pls_integer is
    l_max  pls_integer := 0;
    l_item varchar2(60);
  begin
    for i in 1 .. nvl(regexp_count(p_runs, '[^,]+'), 0) loop
      l_item := regexp_substr(p_runs, '[^,]+', 1, i);
      l_max := greatest(l_max, to_number(substr(l_item, instr(l_item, ':', -1) + 1)));
    end loop;
    return l_max;
  end;

  function runs_fired (p_runs in varchar2, p_breached in varchar2) return varchar2 is
    r   varchar2(4000);
    l_s varchar2(40);
  begin
    for i in 1 .. nvl(regexp_count(p_breached, ',') + 1, 0) loop
      l_s := trim(regexp_substr(p_breached, '[^,]+', 1, i));
      continue when l_s is null or instr(',' || r || ',', ',' || l_s || ',') > 0;
      if run_of(p_runs, l_s) >= c_consec then
        r := r || case when r is not null then ',' end || l_s;
      end if;
    end loop;
    return substr(r, 1, 400);
  end;

  -- ---------------------------------------------------------------- episodes (one rule set, live and grading)
  -- p_action: null, OPEN, EXTEND or CLOSE. p_same (STATIC, SEASONAL): the run that opens an episode must be one
  -- signal's (p_breached = the minute's breach list); otherwise any flagged minutes chain.
  procedure advance (st in out nocopy t_state, p_ts date, p_flag number, p_score number, p_mset boolean,
                     p_same boolean, p_breached varchar2, p_action out varchar2) is
  begin
    p_action := null;
    if st.prev_ts is not null and (p_ts - st.prev_ts) * 86400 > c_gap_sec then
      st.consec := 0;                 -- a gap in the minutes breaks a run of flags
      st.runs := null;
    end if;
    st.prev_ts := p_ts;
    if nvl(p_flag, 0) = 1 then
      if p_same then
        st.runs := runs_next(st.runs, nvl(p_breached, c_unnamed));
        st.consec := runs_max(st.runs);
      else
        st.consec := st.consec + 1;
      end if;
      if st.open then
        st.last_ts := p_ts;
        st.peak := greatest(nvl(st.peak, p_score), nvl(p_score, st.peak));
        st.unflag := 0;
        p_action := 'EXTEND';
      elsif st.consec >= case when p_mset then 1 else c_consec end then
        st.open := true;
        st.start_ts := p_ts;
        st.last_ts := p_ts;
        st.peak := p_score;
        st.unflag := 0;
        st.episode := st.episode + 1;
        st.fired := case when p_same then runs_fired(st.runs, nvl(p_breached, c_unnamed)) end;
        p_action := 'OPEN';
      end if;
    else
      st.consec := 0;
      st.runs := null;
      if st.open then
        st.unflag := st.unflag + 1;
        if st.unflag >= c_close_after then
          st.open := false;
          p_action := 'CLOSE';
        end if;
      end if;
    end if;
  end advance;

  procedure process_alerts (p_model in varchar2) is
    st       t_state;
    l_last   date;
    l_det    varchar2(10);
    l_action varchar2(6);
    l_top    varchar2(400);
    l_open   number;
  begin
    begin
      select last_ts, prev_ts, consec, unflag, open_alert_id, sig_runs
        into l_last, st.prev_ts, st.consec, st.unflag, l_open, st.runs
        from ALERT_STATE where model_name = p_model for update;
    exception
      when no_data_found then
        insert into ALERT_STATE (model_name, last_ts, prev_ts, consec, unflag, open_alert_id, sig_runs)
        values (p_model, null, null, 0, 0, null, null);
    end;
    if l_open is not null then
      select start_ts, last_ts, peak_score into st.start_ts, st.last_ts, st.peak from ALERT where alert_id = l_open;
      st.open := true;
      st.alert_id := l_open;
    end if;
    for r in (select ts, detector, flag, score, breached from SCORE_MINUTE
               where model_name = p_model and (l_last is null or ts > l_last) order by ts) loop
      l_det := r.detector;
      advance(st, r.ts, r.flag, r.score, r.detector = 'MSET', r.detector in ('STATIC', 'SEASONAL'), r.breached,
              l_action);
      if l_action = 'OPEN' then
        -- v1.2: TOP_SIGNALS stays the first 5 (SCORE_DETAIL may now hold more: ties, every breach)
        select substr(listagg(signal_code, ',') within group (order by rank), 1, 400) into l_top
          from SCORE_DETAIL where model_name = p_model and ts = r.ts and rank <= c_top_shown;
        insert into ALERT (model_name, detector, start_ts, last_ts, end_ts, status, top_signals, peak_score, opened_ts,
                           fired)
        values (p_model, r.detector, r.ts, r.ts, null, 'OPEN', l_top, r.score, sys_extract_utc(systimestamp), st.fired)
        returning alert_id into st.alert_id;
      elsif l_action = 'EXTEND' then
        update ALERT set last_ts = st.last_ts, peak_score = st.peak where alert_id = st.alert_id;
      elsif l_action = 'CLOSE' then
        update ALERT set end_ts = r.ts, status = 'CLOSED' where alert_id = st.alert_id;
        st.alert_id := null;
      end if;
      l_last := r.ts;
    end loop;
    update ALERT_STATE
       set last_ts = l_last, prev_ts = st.prev_ts, consec = st.consec, unflag = st.unflag,
           open_alert_id = case when st.open then st.alert_id end, sig_runs = st.runs
     where model_name = p_model;
  end process_alerts;

  procedure build_eval_alerts (p_run_tag in varchar2, p_model in varchar2 default null) is
    st       t_state;
    l_action varchar2(6);
    l_top    varchar2(400);
  begin
    delete from ALERT_EVAL where run_tag = p_run_tag and (p_model is null or model_name = p_model);
    for m in (select distinct model_name from SCORE_EVAL
               where run_tag = p_run_tag and (p_model is null or model_name = p_model) order by model_name) loop
      st := null;
      st.consec := 0;
      st.unflag := 0;
      st.open := false;
      st.episode := 0;
      for r in (select ts, detector, flag, score, breached from SCORE_EVAL
                 where run_tag = p_run_tag and model_name = m.model_name order by ts) loop
        advance(st, r.ts, r.flag, r.score, r.detector = 'MSET', r.detector in ('STATIC', 'SEASONAL'), r.breached,
                l_action);
        if l_action = 'OPEN' then
          select substr(listagg(signal_code, ',') within group (order by rank), 1, 400) into l_top
            from SCORE_EVAL_DETAIL where run_tag = p_run_tag and model_name = m.model_name and ts = r.ts
             and rank <= c_top_shown;
          insert into ALERT_EVAL (run_tag, model_name, episode_no, detector, start_ts, last_ts, end_ts, status,
                                  top_signals, peak_score, fired)
          values (p_run_tag, m.model_name, st.episode, r.detector, r.ts, r.ts, null, 'OPEN', l_top, r.score, st.fired);
        elsif l_action = 'EXTEND' then
          update ALERT_EVAL set last_ts = st.last_ts, peak_score = st.peak
           where run_tag = p_run_tag and model_name = m.model_name and episode_no = st.episode;
        elsif l_action = 'CLOSE' then
          update ALERT_EVAL set end_ts = r.ts, status = 'CLOSED'
           where run_tag = p_run_tag and model_name = m.model_name and episode_no = st.episode;
        end if;
      end loop;
    end loop;
  end build_eval_alerts;

  -- ---------------------------------------------------------------- live and grading entry points
  procedure score_model_live (p_model varchar2, p_det varchar2, p_train_to date) is
    l_sig    varchar2(4000) := PKG_ANOM_TRAIN.model_signals(p_model);
    l_last   date;
    l_from   date;
    l_newest date;
    l_stop   date;
    l_to     date;
    l_ctx    date;
    l_now    date := now_utc;
    l_brk    varchar2(1000);
  begin
    select max(ts) into l_last from SCORE_MINUTE where model_name = p_model;
    l_from := case when l_last is not null then l_last + 1 / 86400
                   else greatest(nvl(p_train_to, l_now - 6 / 24), l_now - 6 / 24) end;
    select max(ts) into l_newest from FEATURE_MINUTE;
    if l_newest is null or l_newest < l_from then
      return;
    end if;
    -- an incomplete minute that is still young stops the run before it
    execute immediate 'select min(f.ts) from FEATURE_MINUTE f where f.ts >= :a and f.ts > :young and '
                      || incomplete_pred(l_sig) into l_stop using l_from, l_now - c_wait_min / 1440;
    l_to := least(nvl(l_stop, l_newest + 1 / 86400), l_from + 1);   -- at most one day per run
    if l_to <= l_from then
      return;
    end if;
    l_ctx := case when p_det = 'MSET' then l_from - c_context_min / 1440 else l_from end;
    score_query(p_model, PKG_ANOM_TRAIN.rows_query(l_ctx, l_to, l_sig), l_from);
    add_unscored(l_from, l_to);
    for r in (select ts, flag, score from SCORE_TMP order by ts) loop
      -- (a private function cannot appear in SQL: the breach list is worked out first)
      l_brk := case when r.flag = 1 then breached_of(p_det, r.ts) end;
      insert into SCORE_MINUTE (ts, model_name, detector, flag, score, scored_ts, breached)
      values (r.ts, p_model, p_det, r.flag, r.score, sys_extract_utc(systimestamp), l_brk);
    end loop;
    insert into SCORE_DETAIL (ts, model_name, rank, signal_code, weight, actual_value)
    select ts, p_model, rnk, signal_code, weight, actual_value from SCORE_TMP_DETAIL where rnk <= c_detail_max;
  end score_model_live;

  procedure score_new is
    l_failed pls_integer := 0;
    l_err    varchar2(4000);
    l_bt     varchar2(4000);
  begin
    for m in (select model_name, detector, train_to from MODEL_REGISTRY
               where status = 'ACTIVE' and detector != 'IFOREST' order by detector) loop
      begin
        score_model_live(m.model_name, m.detector, m.train_to);
        process_alerts(m.model_name);
        commit;
      exception
        when others then
          l_err := sqlerrm;
          l_bt  := dbms_utility.format_error_backtrace;
          rollback;
          l_failed := l_failed + 1;
          log_event('ERROR', 'event=score_failed model='||m.model_name||' error="'||l_err||'" backtrace="'
                             ||replace(rtrim(l_bt, chr(10)), chr(10), ' | ')||'"');
      end;
    end loop;
    -- a model that is no longer ACTIVE leaves no episode open
    update ALERT set status = 'CLOSED', end_ts = last_ts
     where status = 'OPEN' and model_name not in (select model_name from MODEL_REGISTRY where status = 'ACTIVE');
    update ALERT_STATE set open_alert_id = null
     where open_alert_id is not null
       and model_name not in (select model_name from MODEL_REGISTRY where status = 'ACTIVE');
    commit;
    if l_failed > 0 then
      raise_application_error(-20302, 'score_new: '||l_failed||' model(s) failed (see COLLECT_LOG)');
    end if;
  end score_new;

  procedure score_range (p_model in varchar2, p_from in date, p_to in date, p_run_tag in varchar2) is
    l_det  varchar2(10);
    l_set  clob;
    l_sig  varchar2(4000);
    l_ctx  date;
    l_t0   timestamp;
    l_secs number;
    l_n    number;
    l_err  varchar2(4000);
    l_brk  varchar2(1000);
  begin
    if p_run_tag is null or not regexp_like(p_run_tag, '^[A-Z0-9_]{1,40}$') then
      raise_application_error(-20303, 'run tag must be 1-40 of A-Z, 0-9, _');
    end if;
    if p_from is null or p_to is null or p_from >= p_to then
      raise_application_error(-20303, 'the range must have p_from < p_to');
    end if;
    model_info(p_model, l_det, l_set);
    if l_det = 'IFOREST' then
      raise_application_error(-20301, 'IFOREST is scored by tools/iforest.py');
    end if;
    l_sig := PKG_ANOM_TRAIN.model_signals(p_model);
    l_ctx := case when l_det = 'MSET' then p_from - c_context_min / 1440 else p_from end;
    l_t0 := systimestamp;
    score_query(p_model, PKG_ANOM_TRAIN.rows_query(l_ctx, p_to, l_sig), p_from);
    l_secs := secs_since(l_t0);
    add_unscored(p_from, p_to);
    delete from SCORE_EVAL_DETAIL where run_tag = p_run_tag and model_name = p_model and ts >= p_from and ts < p_to;
    delete from SCORE_EVAL where run_tag = p_run_tag and model_name = p_model and ts >= p_from and ts < p_to;
    for r in (select ts, flag, score from SCORE_TMP order by ts) loop
      l_brk := case when r.flag = 1 then breached_of(l_det, r.ts) end;
      insert into SCORE_EVAL (run_tag, ts, model_name, detector, flag, score, scored_ts, breached)
      values (p_run_tag, r.ts, p_model, l_det, r.flag, r.score, sys_extract_utc(systimestamp), l_brk);
    end loop;
    insert into SCORE_EVAL_DETAIL (run_tag, ts, model_name, rank, signal_code, weight, actual_value)
    select p_run_tag, ts, p_model, rnk, signal_code, weight, actual_value from SCORE_TMP_DETAIL where rnk <= c_detail_max;
    select count(flag) into l_n from SCORE_TMP;
    if l_n > 0 then
      update MODEL_REGISTRY set score_ms_per_min = round(l_secs * 1000 / l_n, 3) where model_name = p_model;
    end if;
    commit;
  exception
    when others then
      l_err := sqlerrm;
      rollback;
      log_event('ERROR', 'event=score_range_failed model='||p_model||' tag='||p_run_tag||' error="'||l_err||'"');
      raise;
  end score_range;
end PKG_ANOM_SCORE;
/
show errors package body PKG_ANOM_SCORE

declare
  n number;
begin
  select count(*) into n from user_objects where object_name = 'PKG_ANOM_SCORE' and status != 'VALID';
  if n > 0 then
    raise_application_error(-20900, '95: PKG_ANOM_SCORE did not compile');
  end if;
end;
/

-- ------------------------------------------------------------------ ANOM_SCORE_JOB (created DISABLED)
-- phased 20 s after the collector's second, so the minute it scores has just been written
declare
  n     number;
  l_sec number;
  l_rep varchar2(100);
begin
  select nvl(max(to_number(regexp_substr(repeat_interval, 'BYSECOND=(\d+)', 1, 1, null, 1))), 25)
    into l_sec from user_scheduler_jobs where job_name = 'ANOM_COLLECT_JOB';
  l_rep := 'FREQ=MINUTELY;BYSECOND='||mod(l_sec + 20, 60);
  select count(*) into n from user_scheduler_jobs where job_name = 'ANOM_SCORE_JOB';
  if n = 0 then
    dbms_scheduler.create_job(
      job_name        => 'ANOM_SCORE_JOB',
      job_type        => 'STORED_PROCEDURE',
      job_action      => 'PKG_ANOM_SCORE.SCORE_NEW',
      start_date      => systimestamp at time zone 'UTC',
      repeat_interval => l_rep,
      enabled         => false,
      auto_drop       => false,
      comments        => 'App 900: every ACTIVE model scores its new minutes and advances its alerts');
    dbms_output.put_line('  ANOM_SCORE_JOB created DISABLED ('||l_rep||', UTC)');
  else
    dbms_scheduler.set_attribute('ANOM_SCORE_JOB', 'job_action', 'PKG_ANOM_SCORE.SCORE_NEW');
    dbms_scheduler.set_attribute('ANOM_SCORE_JOB', 'repeat_interval', l_rep);
    dbms_output.put_line('  ANOM_SCORE_JOB present; set to '||l_rep||' (enabled state kept)');
  end if;
  dbms_scheduler.set_attribute('ANOM_SCORE_JOB', 'logging_level', dbms_scheduler.logging_failed_runs);
end;
/

select '  '||job_name||': enabled '||enabled||', '||state||', '||repeat_interval as job
  from user_scheduler_jobs where job_name = 'ANOM_SCORE_JOB';
