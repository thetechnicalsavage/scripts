-- v1.0 - app 900 (phase 4 integrator): trains and activates the INTERIM models of the six in-database detectors on
--        every clean row since the soak start (PKG_ANOM_LIVE.train_interim) and enables ANOM_SCORE_JOB, in ora26ai /
--        ORCLPDB1 as ANOMOPS: tools/anom_obs_run.sh run anom_live_interim.sql <log>. No password, no argument.
--        Prints the ACTIVE models (rows, window, dropped signals, cost) and the live jobs.
--        v1.0: first version, 01-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 220 pagesize 100
whenever sqlerror exit failure rollback

begin
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, 'anom_live_interim: run as ANOMOPS');
  end if;
  dbms_output.put_line('start '||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
  PKG_ANOM_LIVE.train_interim('Y');
  dbms_output.put_line('end   '||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
/

col model_name format a40
col win format a23
col dropped format a70
select model_name, n_rows, to_char(train_from, 'MM-DD HH24:MI')||' - '||to_char(train_to, 'MM-DD HH24:MI') win,
       train_seconds secs, size_bytes,
       json_value(signals_json, '$.rows_in_chaos_windows') chaos_rows,
       json_value(signals_json, '$.rows_with_null') null_rows,
       json_query(signals_json, '$.dropped[*].signal' returning varchar2(400) with array wrapper) dropped
  from MODEL_REGISTRY where status = 'ACTIVE' order by detector;
col job_name format a22
col sched format a110
select job_name, enabled, state, nvl(repeat_interval, 'once')||' next '||to_char(next_run_date, 'YYYY-MM-DD HH24:MI:SS TZR') sched
  from user_scheduler_jobs
 where job_name in ('ANOM_SCORE_JOB', 'ANOM_RETRAIN_INTERIM', 'ANOM_TRAIN_FINAL', 'ANOM_INCIDENT_JOB', 'ANOM_TRUTH_JOB')
 order by job_name;
