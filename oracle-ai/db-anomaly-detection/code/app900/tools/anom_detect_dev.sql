-- v1.0 - app 900 (builder M, phase 4): the detectors' development run and cost measurement on the soak so far, in
--        ora26ai / ORCLPDB1 as ANOMOPS (tools/anom_obs_run.sh run anom_detect_dev.sql <log>). No password here.
--        Trains one CANDIDATE model of every in-database detector (variant DEV) on FEATURE_MINUTE from the soak start
--        (11:35 UTC on 01-Oct) to the last whole minute, scores each over the same range into SCORE_EVAL under run
--        tag DEV_P4 (never SCORE_MINUTE), and prints per detector: training rows, training seconds, model size,
--        batch scoring cost per minute and the live path's cost for one new minute (MSET with its 120 minutes of
--        context; the mean of 5 calls). Isolation Forest is added by tools/iforest.py --run-tag DEV_P4.
--        Re-runnable: the previous DEV candidates and the DEV_P4 rows are removed first. Activates nothing.
--        v1.0: first version, 01-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 200 pagesize 100
whenever sqlerror exit failure rollback

begin
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, 'anom_detect_dev: run as ANOMOPS');
  end if;
  for r in (select model_name from MODEL_REGISTRY where variant = 'DEV' and status = 'CANDIDATE') loop
    PKG_ANOM_TRAIN.drop_model(r.model_name);
  end loop;
  delete from SCORE_EVAL_DETAIL where run_tag = 'DEV_P4' and model_name not like 'IFOREST%';
  delete from SCORE_EVAL where run_tag = 'DEV_P4' and model_name not like 'IFOREST%';
  delete from ALERT_EVAL where run_tag = 'DEV_P4';
  commit;
end;
/

declare
  l_from  date := to_date('2026-10-01 11:35:00', 'YYYY-MM-DD HH24:MI:SS');
  l_to    date := trunc(cast(sys_extract_utc(systimestamp) as date), 'MI') - 2 / 1440;
  l_name  varchar2(128);
  l_last  date;
  l_t0    timestamp;
  l_ms    number;
  l_n     number;
  l_f     number;
begin
  dbms_output.put_line('window '||to_char(l_from, 'YYYY-MM-DD HH24:MI')||' to '||to_char(l_to, 'YYYY-MM-DD HH24:MI')||' UTC');
  select max(ts) into l_last from FEATURE_MINUTE where ts < l_to;
  dbms_output.put_line(rpad('detector', 10)||rpad('rows', 7)||rpad('train s', 10)||rpad('size B', 10)
                       ||rpad('batch ms/min', 14)||rpad('live ms/min', 13)||rpad('flagged', 9)||'minutes');
  for d in (select column_value det from table(sys.odcivarchar2list('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL'))) loop
    l_name := PKG_ANOM_TRAIN.train(d.det, l_from, l_to, 'DEV');
    PKG_ANOM_SCORE.score_range(l_name, l_from, l_to, 'DEV_P4');
    -- the live path for one new minute (what ANOM_SCORE_JOB does each minute), mean of 5 calls
    l_t0 := systimestamp;
    for i in 1 .. 5 loop
      PKG_ANOM_SCORE.score_query(l_name,
        PKG_ANOM_TRAIN.rows_query(case when d.det = 'MSET' then l_last - PKG_ANOM_SCORE.c_context_min / 1440 else l_last end,
                                  l_last + 1 / 86400, PKG_ANOM_TRAIN.model_signals(l_name)), l_last);
    end loop;
    l_ms := (extract(minute from (systimestamp - l_t0)) * 60 + extract(second from (systimestamp - l_t0))) * 1000 / 5;
    rollback;
    select count(*), sum(flag) into l_n, l_f from SCORE_EVAL where run_tag = 'DEV_P4' and model_name = l_name;
    update MODEL_REGISTRY set note = note||'; live path '||round(l_ms, 1)||' ms per new minute' where model_name = l_name;
    commit;
    for r in (select n_rows, train_seconds, size_bytes, score_ms_per_min from MODEL_REGISTRY where model_name = l_name) loop
      dbms_output.put_line(rpad(d.det, 10)||rpad(r.n_rows, 7)||rpad(r.train_seconds, 10)||rpad(r.size_bytes, 10)
                           ||rpad(r.score_ms_per_min, 14)||rpad(round(l_ms, 1), 13)||rpad(nvl(l_f, 0), 9)||l_n);
    end loop;
  end loop;
end;
/

select model_name, detector, status, n_rows, train_seconds, size_bytes, score_ms_per_min
  from MODEL_REGISTRY where variant = 'DEV' order by detector;
select json_query(signals_json, '$.dropped' returning varchar2(2000)) dropped
  from MODEL_REGISTRY where variant = 'DEV' and detector = 'MSET';
