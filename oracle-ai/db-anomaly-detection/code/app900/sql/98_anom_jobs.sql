-- v1.1 - ANOMOPS in ora26ai / ORCLPDB1 (the observer) for application 900, phase 4 (integrator): the soak clock, the
--        pre-registered tuning grid and the live-model jobs. Run as ANOMOPS after 94-96
--        (tools/anom_obs_run.sh run 98_anom_jobs.sql <log>). No password here. Contract v1.2 sections 6-9, v1.6
--        section 12; PLAN.md 7, 7a.
--        v1.1: (phase 4b, PLAN.md 7a) the training clock restarts after the injector fix: SOAK_START is the first
--              full minute after this deployment (the last of phase 4b), every other clock value follows from it as
--              before; ANOM_RETRAIN_INTERIM keeps its 6-hourly calendar from RETRAIN_FIRST and gets the new end
--              (TRAIN_TO), ANOM_TRAIN_FINAL the new FINAL_AT. train_interim and train_final_job refresh INCIDENT_TRUTH
--              once per batch (94 v1.2), not once per detector. train_final_job generates the hidden-incident plan
--              and pushes it to the target in one transaction (96 v1.3) BEFORE it trains the FINAL models, so the
--              plan reaches the target 20 minutes before the evaluation even if training is slow; it enables nothing
--              (ANOM_INCIDENT_JOB is gone: the target's dispatchers start the incidents). INTERIM models trained under
--              the old clock stay ACTIVE and keep scoring until the next retraining. 01-Oct-2026.
--        v1.0: first version, 01-Oct-2026.
--
--        ANOM_STATE rows CLOCK_*   the soak clock (UTC), written here and read by PKG_ANOM_LIVE.clock:
--                          SOAK_START (v1.1: phase 4b's restart, see c_soak_start; v1.0 had builder TUNE's 13:11), TRAIN_FROM =
--                          SOAK_START + 1 h (warm-up), TRAIN_TO = TRAIN_FROM + 72 h, FINAL_AT = TRAIN_TO + 10 min,
--                          EVAL_FROM = TRAIN_TO + 30 min, EVAL_TO = EVAL_FROM + 48 h, RETRAIN_FIRST = the first
--                          interim retraining (every 6 h after it, the last one before TRAIN_TO).
--        TUNING_GRID       PLAN.md 7a: the five pre-registered values of every detector's sensitivity knob, step 1
--                          (least sensitive) to 5 (most), as the settings JSON PKG_ANOM_TRAIN.train / tools/iforest.py
--                          take. Step 4 is each detector's default (the values INTERIM and FINAL are trained with).
--                          Seeded by MERGE; rows outside the grid are removed; must be 7 x 5 = 35 rows.
--        PKG_ANOM_LIVE     train_interim: variant INTERIM of the six in-database detectors on every clean row from
--                          SOAK_START to now (PKG_ANOM_TRAIN's training rows: chaos windows and null rows excluded),
--                          each activated (the previous INTERIM is retired); enables ANOM_SCORE_JOB on request.
--                          retrain_interim_job (job ANOM_RETRAIN_INTERIM): train_interim while before TRAIN_TO.
--                          train_final_job (job ANOM_TRAIN_FINAL): disables ANOM_RETRAIN_INTERIM; has
--                          PKG_ANOM_GRADE.plan_incidents generate the 48-hour hidden-incident plan from EVAL_FROM and push
--                          it to the target (v1.1; the target's dispatchers start it); trains variant FINAL of the six
--                          detectors on [TRAIN_FROM, TRAIN_TO) and activates it (retiring INTERIM). Models stay frozen
--                          afterwards: nothing here retrains after TRAIN_TO.
--        Jobs              ANOM_RETRAIN_INTERIM: FREQ=HOURLY;INTERVAL=6;BYMINUTE=17;BYSECOND=0 from RETRAIN_FIRST (an
--                          explicit UTC start), end_date TRAIN_TO (the scheduler stops it for good there, and the
--                          procedure refuses after it too). ANOM_TRAIN_FINAL: one-off at FINAL_AT (UTC), kept after it
--                          runs (auto_drop false) so its run record stays.
--        Idempotent: tables created when absent and checked, the grid and the clock merged, the package re-applied;
--        a job is created when absent and re-pointed otherwise; a re-run never re-enables the retraining after
--        TRAIN_TO and never re-arms ANOM_TRAIN_FINAL once it has run.
set serveroutput on size unlimited format wrapped
set verify off
set linesize 200
set feedback off
whenever sqlerror exit failure rollback

begin
  if sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '98: run in the PDB (ORCLPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, '98: run as ANOMOPS');
  end if;
end;
/

-- 94-96 must be installed (this script calls their packages and enables their jobs)
declare
  n number;
begin
  select count(*) into n from user_objects
   where object_name in ('PKG_ANOM_TRAIN', 'PKG_ANOM_SCORE', 'PKG_ANOM_GRADE') and object_type = 'PACKAGE BODY'
     and status = 'VALID';
  if n != 3 then
    raise_application_error(-20900, '98: PKG_ANOM_TRAIN, PKG_ANOM_SCORE and PKG_ANOM_GRADE must be valid (run 94-96)');
  end if;
  select count(*) into n from user_scheduler_jobs where job_name = 'ANOM_SCORE_JOB';
  if n != 1 then
    raise_application_error(-20900, '98: ANOM_SCORE_JOB must exist (run 95)');
  end if;
  -- v1.1: the plan push (96 v1.3) and the batch refresh (94 v1.2)
  select count(*) into n from user_procedures
   where (object_name = 'PKG_ANOM_GRADE' and procedure_name = 'PUSH_PLAN')
      or (object_name = 'PKG_ANOM_TRAIN' and procedure_name in ('BATCH_TRUTH_BEGIN', 'BATCH_TRUTH_END'));
  if n != 3 then
    raise_application_error(-20900, '98: PKG_ANOM_GRADE.push_plan and PKG_ANOM_TRAIN.batch_truth_* are required (94 v1.2, 96 v1.3)');
  end if;
end;
/

-- ------------------------------------------------------------------ the tuning grid (PLAN.md 7a)
declare
  n number;
begin
  select count(*) into n from user_tables where table_name = 'TUNING_GRID';
  if n = 0 then
    execute immediate q'[
      create table TUNING_GRID (
        detector       varchar2(10)   not null,
        step           number(1)      not null,
        settings_json  varchar2(400)  not null,
        is_default     char(1)        not null,
        constraint tuning_grid_pk     primary key (detector, step),
        constraint tuning_grid_det_ck check (detector in ('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL', 'IFOREST')),
        constraint tuning_grid_stp_ck check (step between 1 and 5),
        constraint tuning_grid_js     check (settings_json is json),
        constraint tuning_grid_def_ck check (is_default in ('Y', 'N'))
      )]';
    dbms_output.put_line('  table TUNING_GRID created');
  else
    dbms_output.put_line('  table TUNING_GRID already present');
  end if;
  select count(*) into n from user_tab_columns
   where table_name = 'TUNING_GRID' and column_name in ('DETECTOR', 'STEP', 'SETTINGS_JSON', 'IS_DEFAULT');
  if n != 4 then
    raise_application_error(-20900, '98: TUNING_GRID lacks a column');
  end if;
end;
/

-- step 1 = least sensitive .. 5 = most sensitive; step 4 = the detector's default in 94 (and iforest.py's 0.01)
merge into TUNING_GRID d
using (
  select 'MSET' det, 1 stp, '{"MSET_ALERT_COUNT":6,"MSET_ALERT_WINDOW":10}' js, 'N' dflt from dual union all
  select 'MSET',     2,     '{"MSET_ALERT_COUNT":5,"MSET_ALERT_WINDOW":8}',      'N'      from dual union all
  select 'MSET',     3,     '{"MSET_ALERT_COUNT":4,"MSET_ALERT_WINDOW":6}',      'N'      from dual union all
  select 'MSET',     4,     '{"MSET_ALERT_COUNT":3,"MSET_ALERT_WINDOW":5}',      'Y'      from dual union all
  select 'MSET',     5,     '{"MSET_ALERT_COUNT":2,"MSET_ALERT_WINDOW":5}',      'N'      from dual union all
  select 'SVM',      1,     '{"SVMS_OUTLIER_RATE":0.001}',                       'N'      from dual union all
  select 'SVM',      2,     '{"SVMS_OUTLIER_RATE":0.0025}',                      'N'      from dual union all
  select 'SVM',      3,     '{"SVMS_OUTLIER_RATE":0.005}',                       'N'      from dual union all
  select 'SVM',      4,     '{"SVMS_OUTLIER_RATE":0.01}',                        'Y'      from dual union all
  select 'SVM',      5,     '{"SVMS_OUTLIER_RATE":0.02}',                        'N'      from dual union all
  select 'EM',       1,     '{"EMCS_OUTLIER_RATE":0.001}',                       'N'      from dual union all
  select 'EM',       2,     '{"EMCS_OUTLIER_RATE":0.0025}',                      'N'      from dual union all
  select 'EM',       3,     '{"EMCS_OUTLIER_RATE":0.005}',                       'N'      from dual union all
  select 'EM',       4,     '{"EMCS_OUTLIER_RATE":0.01}',                        'Y'      from dual union all
  select 'EM',       5,     '{"EMCS_OUTLIER_RATE":0.02}',                        'N'      from dual union all
  select 'PCA',      1,     '{"RESID_PCT":99.95}',                               'N'      from dual union all
  select 'PCA',      2,     '{"RESID_PCT":99.9}',                                'N'      from dual union all
  select 'PCA',      3,     '{"RESID_PCT":99.8}',                                'N'      from dual union all
  select 'PCA',      4,     '{"RESID_PCT":99.5}',                                'Y'      from dual union all
  select 'PCA',      5,     '{"RESID_PCT":99}',                                  'N'      from dual union all
  select 'IFOREST',  1,     '{"contamination":0.001}',                           'N'      from dual union all
  select 'IFOREST',  2,     '{"contamination":0.0025}',                          'N'      from dual union all
  select 'IFOREST',  3,     '{"contamination":0.005}',                           'N'      from dual union all
  select 'IFOREST',  4,     '{"contamination":0.01}',                            'Y'      from dual union all
  select 'IFOREST',  5,     '{"contamination":0.02}',                            'N'      from dual union all
  select 'STATIC',   1,     '{"PCT":99.95}',                                     'N'      from dual union all
  select 'STATIC',   2,     '{"PCT":99.9}',                                      'N'      from dual union all
  select 'STATIC',   3,     '{"PCT":99.8}',                                      'N'      from dual union all
  select 'STATIC',   4,     '{"PCT":99.5}',                                      'Y'      from dual union all
  select 'STATIC',   5,     '{"PCT":99}',                                        'N'      from dual union all
  select 'SEASONAL', 1,     '{"K":8}',                                           'N'      from dual union all
  select 'SEASONAL', 2,     '{"K":6}',                                           'N'      from dual union all
  select 'SEASONAL', 3,     '{"K":5}',                                           'N'      from dual union all
  select 'SEASONAL', 4,     '{"K":4}',                                           'Y'      from dual union all
  select 'SEASONAL', 5,     '{"K":3}',                                           'N'      from dual
) s
on (d.detector = s.det and d.step = s.stp)
when matched then update set d.settings_json = s.js, d.is_default = s.dflt
                       where d.settings_json != s.js or d.is_default != s.dflt
when not matched then insert (detector, step, settings_json, is_default) values (s.det, s.stp, s.js, s.dflt);

declare
  n  number;
  n2 number;
  n3 number;
begin
  delete from TUNING_GRID where step not between 1 and 5
     or detector not in ('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL', 'IFOREST');
  select count(*), count(distinct detector), count(case when is_default = 'Y' then 1 end) into n, n2, n3 from TUNING_GRID;
  dbms_output.put_line('  TUNING_GRID rows: '||n||' over '||n2||' detectors, '||n3||' defaults');
  if n != 35 or n2 != 7 or n3 != 7 then
    raise_application_error(-20900, '98: TUNING_GRID holds '||n||' rows / '||n2||' detectors / '||n3||' defaults; '
                                    ||'35 / 7 / 7 expected');
  end if;
end;
/
commit;
comment on table TUNING_GRID is 'App 900: PLAN.md 7a pre-registered sensitivity values per detector, step 1 (least) to 5 (most sensitive)';

-- ------------------------------------------------------------------ the soak clock (UTC)
declare
  -- v1.1: phase 4b restarted the training clock after the injector fix (docs/contract.md section 12): the first full
  -- minute after this script's deployment. (v1.0: 2026-10-01 13:11, builder TUNE's restart for peak_tps 70.)
  c_soak_start    constant date := to_date('2026-10-01 22:57:00', 'YYYY-MM-DD HH24:MI:SS');
  c_retrain_first constant date := to_date('2026-10-01 18:17:00', 'YYYY-MM-DD HH24:MI:SS');
  l_train_from    date := c_soak_start + 1 / 24;            -- the 1-hour warm-up
  l_train_to      date := l_train_from + 3;                 -- 72 hours of training
  procedure put (p_name varchar2, p_val date) is
    l_old timestamp;
  begin
    begin
      select value_ts into l_old from ANOM_STATE where name = p_name;
    exception
      when no_data_found then l_old := null;
    end;
    merge into ANOM_STATE s using (select p_name n from dual) x on (s.name = x.n)
     when matched then update set s.value_ts = cast(p_val as timestamp),
                                  s.value_txt = to_char(p_val, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
     when not matched then insert (name, value_ts, value_txt)
                           values (p_name, cast(p_val as timestamp), to_char(p_val, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
    dbms_output.put_line('  '||rpad(p_name, 20)||to_char(p_val, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                         ||case when l_old is null then ' (new)'
                                when l_old != cast(p_val as timestamp) then ' (changed from '
                                     ||to_char(l_old, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')||')' end);
  end;
begin
  put('CLOCK_SOAK_START',    c_soak_start);
  put('CLOCK_TRAIN_FROM',    l_train_from);
  put('CLOCK_TRAIN_TO',      l_train_to);
  put('CLOCK_FINAL_AT',      l_train_to + 10 / 1440);
  put('CLOCK_EVAL_FROM',     l_train_to + 30 / 1440);
  put('CLOCK_EVAL_TO',       l_train_to + 30 / 1440 + 2);
  put('CLOCK_RETRAIN_FIRST', c_retrain_first);
  commit;
end;
/

-- ------------------------------------------------------------------ the live-model package
create or replace package PKG_ANOM_LIVE authid definer as
  -- v1.0 - app 900 live models: interim training, the hour-72 switch to the frozen FINAL models and the hidden
  --        incidents (see 98_anom_jobs.sql). All times UTC. (v1.1: body only; this specification is unchanged.)

  c_retrain_job constant varchar2(30) := 'ANOM_RETRAIN_INTERIM';
  c_final_job   constant varchar2(30) := 'ANOM_TRAIN_FINAL';

  -- a clock value: SOAK_START, TRAIN_FROM, TRAIN_TO, FINAL_AT, EVAL_FROM, EVAL_TO, RETRAIN_FIRST (-20505 if unknown)
  function clock (p_name in varchar2) return date;

  -- the pre-registered settings JSON of a detector's tuning step (PLAN.md 7a; 1 = least .. 5 = most sensitive)
  function grid_settings (p_detector in varchar2, p_step in pls_integer) return varchar2;

  -- the interim training window at p_now: [SOAK_START, the whole minute of p_now), at most TRAIN_TO; both null at
  -- or after TRAIN_TO (the interim models end there)
  procedure interim_window (p_now in date, p_from out date, p_to out date);

  -- trains variant INTERIM of MSET, SVM, EM, PCA, STATIC and SEASONAL on the interim window and activates each
  -- (the detector's previous ACTIVE model is retired). p_enable_scoring = 'Y' enables ANOM_SCORE_JOB once a model is
  -- ACTIVE. Refuses at or after TRAIN_TO (-20501). A detector that fails is logged and the others go on; raises
  -- -20502 at the end when any failed. Commits.
  procedure train_interim (p_enable_scoring in varchar2 default 'Y');

  -- ANOM_RETRAIN_INTERIM's action: train_interim('N') before TRAIN_TO; after it, logs and does nothing
  procedure retrain_interim_job;

  -- ANOM_TRAIN_FINAL's action (refuses before TRAIN_TO, -20503): (b) disables ANOM_RETRAIN_INTERIM; (c) generates
  -- the hidden-incident plan from EVAL_FROM and pushes it to the target (v1.1: before the training; an existing plan
  -- is pushed again where the target lacks rows); (a) trains and activates variant FINAL of the six detectors on
  -- [TRAIN_FROM, TRAIN_TO) (skips a detector that already has an ACTIVE FINAL model of that window). A failed
  -- detector keeps its INTERIM model ACTIVE and is logged; every step runs anyway; raises -20504 at the end when
  -- anything failed. Commits.
  procedure train_final_job;
end PKG_ANOM_LIVE;
/

create or replace package body PKG_ANOM_LIVE as
  -- v1.1 (one INCIDENT_TRUTH refresh per training batch; the plan pushed to the target, nothing enabled)
  type t_dets is table of varchar2(10);
  c_dets constant t_dets := t_dets('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL');
  c_iso  constant varchar2(30) := 'YYYY-MM-DD"T"HH24:MI"Z"';

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

  function clock (p_name in varchar2) return date is
    l timestamp;
  begin
    select value_ts into l from ANOM_STATE where name = 'CLOCK_' || upper(p_name);
    return cast(l as date);
  exception
    when no_data_found then
      raise_application_error(-20505, 'clock value '||substr(p_name, 1, 30)||' is not set (run 98)');
  end;

  function grid_settings (p_detector in varchar2, p_step in pls_integer) return varchar2 is
    l varchar2(400);
  begin
    select settings_json into l from TUNING_GRID where detector = upper(p_detector) and step = p_step;
    return l;
  exception
    when no_data_found then
      raise_application_error(-20505, 'no tuning step '||p_step||' for detector '||substr(p_detector, 1, 10));
  end;

  procedure interim_window (p_now in date, p_from out date, p_to out date) is
    l_to date := clock('TRAIN_TO');
  begin
    if p_now is null or p_now >= l_to then
      p_from := null;
      p_to := null;
      return;
    end if;
    p_from := clock('SOAK_START');
    p_to := least(trunc(p_now, 'MI'), l_to);
  end;

  -- enables a job of this schema (a no-op when it is enabled) and logs it
  procedure enable_job (p_job varchar2) is
  begin
    dbms_scheduler.enable(p_job);
    log_event('INFO', 'event=job_enabled job='||p_job);
  end;

  procedure train_interim (p_enable_scoring in varchar2 default 'Y') is
    l_from   date;
    l_to     date;
    l_name   varchar2(128);
    l_ok     varchar2(4000);
    l_failed varchar2(4000);
    l_truth  boolean := false;
    n        number;
  begin
    if nvl(p_enable_scoring, '?') not in ('Y', 'N') then
      raise_application_error(-20500, 'p_enable_scoring must be Y or N');
    end if;
    interim_window(now_utc, l_from, l_to);
    if l_to is null then
      raise_application_error(-20501, 'the interim models end at TRAIN_TO ('||to_char(clock('TRAIN_TO'), c_iso)
                                      ||'); the FINAL models take over (ANOM_TRAIN_FINAL)');
    end if;
    -- v1.1: one INCIDENT_TRUTH refresh for the six models (one target logon), not one per detector
    begin
      PKG_ANOM_TRAIN.batch_truth_begin;
      l_truth := true;
    exception
      when others then
        l_failed := 'INCIDENT_TRUTH refresh: '||sqlerrm||' (no model trained: the chaos windows could be stale)';
    end;
    if l_truth then
      for i in 1 .. c_dets.count loop
        begin
          l_name := PKG_ANOM_TRAIN.train(c_dets(i), l_from, l_to, 'INTERIM', null, 'Y');
          l_ok := l_ok || case when l_ok is not null then ',' end || l_name;
        exception
          when others then
            -- train() has logged the failure with its backtrace; carry on with the next detector
            l_failed := l_failed || case when l_failed is not null then '; ' end || c_dets(i) || ': ' || sqlerrm;
        end;
      end loop;
      PKG_ANOM_TRAIN.batch_truth_end;
    end if;
    log_event(case when l_failed is null then 'INFO' else 'ERROR' end,
              'event=interim_trained window='||to_char(l_from, c_iso)||'/'||to_char(l_to, c_iso)
              ||' models='||nvl(l_ok, '-')||case when l_failed is not null then ' failed="'||l_failed||'"' end);
    if p_enable_scoring = 'Y' then
      select count(*) into n from MODEL_REGISTRY where status = 'ACTIVE';
      if n > 0 then
        enable_job('ANOM_SCORE_JOB');
      end if;
    end if;
    if l_failed is not null then
      raise_application_error(-20502, 'train_interim: '||substr(l_failed, 1, 3000));
    end if;
  end train_interim;

  procedure retrain_interim_job is
  begin
    if now_utc >= clock('TRAIN_TO') then
      log_event('INFO', 'event=interim_retrain_skipped reason="past TRAIN_TO; the FINAL models are frozen"');
      return;
    end if;
    train_interim('N');
  end retrain_interim_job;

  procedure train_final_job is
    l_from   date := clock('TRAIN_FROM');
    l_to     date := clock('TRAIN_TO');
    l_eval   date := clock('EVAL_FROM');
    l_name   varchar2(128);
    l_ok     varchar2(4000);
    l_failed varchar2(4000);
    l_truth  boolean := false;
    n        number;
    n2       number;
  begin
    if now_utc < l_to then
      raise_application_error(-20503, 'train_final_job runs at or after TRAIN_TO ('||to_char(l_to, c_iso)||')');
    end if;

    -- (b) the interim retraining stops for good (force: a run in progress finishes, none starts again)
    begin
      dbms_scheduler.disable(c_retrain_job, force => true);
      log_event('INFO', 'event=job_disabled job='||c_retrain_job);
    exception
      when others then
        l_failed := l_failed || case when l_failed is not null then '; ' end || 'disable '||c_retrain_job||': '||sqlerrm;
        log_event('ERROR', 'event=job_disable_failed job='||c_retrain_job||' error="'||sqlerrm||'"');
    end;

    -- (c) the hidden incidents of the 48-hour evaluation window, generated and pushed to the target in one
    --     transaction (96 v1.3); first, so the plan is there 20 minutes before EVAL_FROM even if training is slow
    select count(*), count(case when plan_from = l_eval then 1 end) into n, n2 from INCIDENT_PLAN;
    begin
      if n = 0 then
        if now_utc > l_eval then
          log_event('WARN', 'event=incident_plan_late eval_from='||to_char(l_eval, c_iso)
                            ||' (the target SKIPS rows more than 5 min late)');
        end if;
        PKG_ANOM_GRADE.plan_incidents(l_eval, 'Y');
      elsif n2 = n then
        PKG_ANOM_GRADE.push_plan(l_eval);   -- a re-run: the plan exists; the target gets any row it lacks
      else
        l_failed := l_failed || case when l_failed is not null then '; ' end
                    || 'INCIDENT_PLAN holds '||(n - n2)||' row(s) of another start; nothing generated or pushed';
        log_event('ERROR', 'event=incident_plan_conflict rows='||n||' eval_from='||to_char(l_eval, c_iso));
      end if;
    exception
      when others then
        -- plan_incidents / push_plan logged it; the training still runs
        l_failed := l_failed || case when l_failed is not null then '; ' end || 'plan: ' || sqlerrm;
    end;

    -- (a) the frozen FINAL models; activating one retires the detector's INTERIM model. One INCIDENT_TRUTH
    --     refresh for the batch (v1.1).
    begin
      PKG_ANOM_TRAIN.batch_truth_begin;
      l_truth := true;
    exception
      when others then
        l_failed := l_failed || case when l_failed is not null then '; ' end
                    || 'INCIDENT_TRUTH refresh: '||sqlerrm||' (no FINAL model trained)';
    end;
    for i in 1 .. c_dets.count loop
      exit when not l_truth;
      select max(model_name) into l_name from MODEL_REGISTRY
       where detector = c_dets(i) and variant = 'FINAL' and status = 'ACTIVE' and train_from = l_from and train_to = l_to;
      if l_name is not null then
        l_ok := l_ok || case when l_ok is not null then ',' end || l_name || '(kept)';
      else
        begin
          l_name := PKG_ANOM_TRAIN.train(c_dets(i), l_from, l_to, 'FINAL', null, 'Y');
          l_ok := l_ok || case when l_ok is not null then ',' end || l_name;
        exception
          when others then
            -- train() has logged it; this detector keeps its INTERIM model until FINAL is trained by hand on the
            -- same window (the window's rows do not change after TRAIN_TO)
            l_failed := l_failed || case when l_failed is not null then '; ' end || c_dets(i) || ': ' || sqlerrm;
        end;
      end if;
    end loop;
    if l_truth then
      PKG_ANOM_TRAIN.batch_truth_end;
    end if;
    log_event(case when l_failed is null then 'INFO' else 'ERROR' end,
              'event=final_trained window='||to_char(l_from, c_iso)||'/'||to_char(l_to, c_iso)||' models='
              ||nvl(l_ok, '-')||case when l_failed is not null then ' failed="'||l_failed||'"' end);
    if l_failed is not null then
      raise_application_error(-20504, 'train_final_job: '||substr(l_failed, 1, 3000));
    end if;
  end train_final_job;
end PKG_ANOM_LIVE;
/
show errors package body PKG_ANOM_LIVE

declare
  n number;
begin
  select count(*) into n from user_objects where object_name = 'PKG_ANOM_LIVE' and status != 'VALID';
  if n > 0 then
    raise_application_error(-20900, '98: PKG_ANOM_LIVE did not compile');
  end if;
end;
/

-- ------------------------------------------------------------------ the jobs (explicit UTC dates)
declare
  l_first  timestamp with time zone := from_tz(cast(PKG_ANOM_LIVE.clock('RETRAIN_FIRST') as timestamp), 'UTC');
  l_end    timestamp with time zone := from_tz(cast(PKG_ANOM_LIVE.clock('TRAIN_TO') as timestamp), 'UTC');
  l_final  timestamp with time zone := from_tz(cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp), 'UTC');
  l_rep    constant varchar2(100) := 'FREQ=HOURLY;INTERVAL=6;BYMINUTE=17;BYSECOND=0';
  l_now    timestamp with time zone := systimestamp at time zone 'UTC';
  n        number;
  l_runs   number;
begin
  -- ANOM_RETRAIN_INTERIM: every 6 h from RETRAIN_FIRST, ends for good at TRAIN_TO
  select count(*) into n from user_scheduler_jobs where job_name = 'ANOM_RETRAIN_INTERIM';
  if n = 0 then
    dbms_scheduler.create_job(
      job_name        => 'ANOM_RETRAIN_INTERIM',
      job_type        => 'STORED_PROCEDURE',
      job_action      => 'PKG_ANOM_LIVE.RETRAIN_INTERIM_JOB',
      start_date      => l_first,
      repeat_interval => l_rep,
      end_date        => l_end,
      enabled         => false,
      auto_drop       => false,
      comments        => 'App 900: retrains the INTERIM models on every clean row so far (until TRAIN_TO)');
    dbms_output.put_line('  ANOM_RETRAIN_INTERIM created ('||l_rep||' from '
                         ||to_char(l_first, 'YYYY-MM-DD HH24:MI TZR')||' to '||to_char(l_end, 'YYYY-MM-DD HH24:MI TZR')||')');
  elsif l_now < l_end then
    dbms_scheduler.set_attribute('ANOM_RETRAIN_INTERIM', 'job_action', 'PKG_ANOM_LIVE.RETRAIN_INTERIM_JOB');
    dbms_scheduler.set_attribute('ANOM_RETRAIN_INTERIM', 'repeat_interval', l_rep);
    dbms_scheduler.set_attribute('ANOM_RETRAIN_INTERIM', 'end_date', l_end);
    dbms_output.put_line('  ANOM_RETRAIN_INTERIM present; re-pointed');
  end if;
  dbms_scheduler.set_attribute('ANOM_RETRAIN_INTERIM', 'logging_level', dbms_scheduler.logging_runs);
  if l_now < l_end then
    dbms_scheduler.enable('ANOM_RETRAIN_INTERIM');
  else
    dbms_output.put_line('  ANOM_RETRAIN_INTERIM: past TRAIN_TO, not enabled');
  end if;

  -- ANOM_TRAIN_FINAL: one-off at FINAL_AT; never re-armed once it has run
  select count(*), max(run_count) into n, l_runs from user_scheduler_jobs where job_name = 'ANOM_TRAIN_FINAL';
  if n = 0 then
    dbms_scheduler.create_job(
      job_name        => 'ANOM_TRAIN_FINAL',
      job_type        => 'STORED_PROCEDURE',
      job_action      => 'PKG_ANOM_LIVE.TRAIN_FINAL_JOB',
      start_date      => l_final,
      repeat_interval => null,
      enabled         => false,
      auto_drop       => false,
      comments        => 'App 900: hour 72 - interim retraining off, the hidden incident plan from EVAL_FROM generated and '
                         ||'pushed to the target, FINAL models on [TRAIN_FROM, TRAIN_TO)');
    dbms_output.put_line('  ANOM_TRAIN_FINAL created (once at '||to_char(l_final, 'YYYY-MM-DD HH24:MI TZR')||')');
    l_runs := 0;
  elsif nvl(l_runs, 0) = 0 then
    dbms_scheduler.set_attribute('ANOM_TRAIN_FINAL', 'job_action', 'PKG_ANOM_LIVE.TRAIN_FINAL_JOB');
    dbms_scheduler.set_attribute('ANOM_TRAIN_FINAL', 'start_date', l_final);
    dbms_scheduler.set_attribute('ANOM_TRAIN_FINAL', 'comments',
                                 'App 900: hour 72 - interim retraining off, the hidden incident plan from EVAL_FROM '
                                 ||'generated and pushed to the target, FINAL models on [TRAIN_FROM, TRAIN_TO)');
    dbms_output.put_line('  ANOM_TRAIN_FINAL present, not run yet; re-pointed');
  else
    dbms_output.put_line('  ANOM_TRAIN_FINAL has run ('||l_runs||'); left as it is');
  end if;
  if nvl(l_runs, 0) = 0 then
    dbms_scheduler.set_attribute('ANOM_TRAIN_FINAL', 'logging_level', dbms_scheduler.logging_runs);
    dbms_scheduler.enable('ANOM_TRAIN_FINAL');
  end if;
end;
/

col job_name format a22
col sched format a120
select job_name, enabled, state,
       nvl(repeat_interval, 'once')||' start '||to_char(start_date, 'YYYY-MM-DD HH24:MI:SS TZR')
       ||' next '||to_char(next_run_date, 'YYYY-MM-DD HH24:MI:SS TZR')
       ||case when end_date is not null then ' end '||to_char(end_date, 'YYYY-MM-DD HH24:MI:SS TZR') end as sched
  from user_scheduler_jobs where job_name in ('ANOM_RETRAIN_INTERIM', 'ANOM_TRAIN_FINAL') order by job_name;
