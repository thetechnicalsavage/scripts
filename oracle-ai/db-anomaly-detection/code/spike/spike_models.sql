-- v1.0 - spike (phase 0) as ANOMOPS on ORCLPDB1: can MSET-SPRT, one-class SVM and EM anomaly be built and
--        scored on time-ordered numeric signals on 23.26.1, and what does PREDICTION_DETAILS return?
--        Synthetic data only. Re-runnable: every object it makes starts with SPK_ and is dropped first.
--        Run: sqlplus -s -L ANOMOPS/...@//localhost:1521/ORCLPDB1 @spike_models.sql < /dev/null
--
--        Train: 3,000 minutes of five related signals driven by one "load" curve with noise.
--        Test : 300 minutes; from minute 150 signal S3 drifts away from its relationship with load by a
--               growing amount (0 to +30% at minute 300), while every signal stays inside its training range.
set serveroutput on size unlimited
set feedback off
set pagesize 200 linesize 220 long 4000 longchunksize 4000
whenever sqlerror exit failure rollback

begin
  for m in (select model_name from user_mining_models where model_name like 'SPK\_%' escape '\') loop
    dbms_data_mining.drop_model(m.model_name);
  end loop;
  for t in (select table_name from user_tables where table_name in ('SPK_TRAIN','SPK_TEST')) loop
    execute immediate 'drop table '||t.table_name||' purge';
  end loop;
end;
/

-- deterministic noise: no DBMS_RANDOM, a hash of the row number instead
create table SPK_TRAIN as
with g as (
  select level n,
         timestamp '2026-09-01 00:00:00' + numtodsinterval(level, 'MINUTE') ts,
         50 + 40 * sin(2 * acos(-1) * level / 1440) load_
    from dual connect by level <= 3000),
z as (
  select n, ts, load_,
         (mod(ora_hash(n, 1000, 1), 1000) / 1000 - 0.5) e1,
         (mod(ora_hash(n, 1000, 2), 1000) / 1000 - 0.5) e2,
         (mod(ora_hash(n, 1000, 3), 1000) / 1000 - 0.5) e3,
         (mod(ora_hash(n, 1000, 4), 1000) / 1000 - 0.5) e4,
         (mod(ora_hash(n, 1000, 5), 1000) / 1000 - 0.5) e5
    from g)
select ts,
       round(load_ + 2 * e1, 3)                    s1,   -- the load itself
       round(2.0 * load_ + 4 * e2, 3)              s2,   -- proportional to load
       round(0.5 * load_ + 10 + 1.5 * e3, 3)       s3,   -- proportional, with an offset
       round(20 + 3 * e4, 3)                       s4,   -- independent of load
       round(load_ * load_ / 100 + 2 * e5, 3)      s5    -- non-linear in load
  from z;

create table SPK_TEST as
with g as (
  select level n,
         timestamp '2026-09-03 02:00:00' + numtodsinterval(level, 'MINUTE') ts,
         50 + 40 * sin(2 * acos(-1) * (3000 + level) / 1440) load_
    from dual connect by level <= 300),
z as (
  select n, ts, load_,
         (mod(ora_hash(n, 1000, 11), 1000) / 1000 - 0.5) e1,
         (mod(ora_hash(n, 1000, 12), 1000) / 1000 - 0.5) e2,
         (mod(ora_hash(n, 1000, 13), 1000) / 1000 - 0.5) e3,
         (mod(ora_hash(n, 1000, 14), 1000) / 1000 - 0.5) e4,
         (mod(ora_hash(n, 1000, 15), 1000) / 1000 - 0.5) e5
    from g)
select ts, n minute_no,
       round(load_ + 2 * e1, 3) s1,
       round(2.0 * load_ + 4 * e2, 3) s2,
       round((0.5 * load_ + 10) * (1 + case when n > 150 then 0.30 * (n - 150) / 150 else 0 end) + 1.5 * e3, 3) s3,
       round(20 + 3 * e4, 3) s4,
       round(load_ * load_ / 100 + 2 * e5, 3) s5
  from z;

prompt == ranges: train vs test (every test value inside the training range?)
select 'S3' sig, (select round(min(s3),1)||'..'||round(max(s3),1) from SPK_TRAIN) train_range,
       (select round(min(s3),1)||'..'||round(max(s3),1) from SPK_TEST) test_range from dual;

-- ------------------------------------------------------------------ MSET-SPRT
declare
  v dbms_data_mining.setting_list;
begin
  v('ALGO_NAME')         := 'ALGO_MSET_SPRT';
  v('MSET_ALERT_COUNT')  := '3';
  v('MSET_ALERT_WINDOW') := '5';
  dbms_data_mining.create_model2(
    model_name          => 'SPK_MSET',
    mining_function     => 'CLASSIFICATION',
    data_query          => 'select * from SPK_TRAIN',
    set_list            => v,
    case_id_column_name => 'TS',
    target_column_name  => null);
  dbms_output.put_line('SPK_MSET built');
end;
/
prompt == MSET-SPRT model settings as stored
select setting_name, setting_value from user_mining_model_settings where model_name = 'SPK_MSET' order by 1;

prompt == MSET-SPRT scoring: first minute flagged, and the flagged count before/after the drift starts
with s as (
  select minute_no,
         prediction(SPK_MSET using *) over (order by ts) pred,
         prediction_probability(SPK_MSET, 0 using *) over (order by ts) p_anom
    from SPK_TEST)
select sum(case when minute_no <= 150 and pred = 0 then 1 else 0 end) flagged_before_drift,
       sum(case when minute_no >  150 and pred = 0 then 1 else 0 end) flagged_after_drift,
       min(case when minute_no >  150 and pred = 0 then minute_no end) first_flag_minute
  from s;

prompt == MSET-SPRT PREDICTION_DETAILS for three flagged minutes
select minute_no, pred, det from (
  select minute_no,
         prediction(SPK_MSET using *) over (order by ts) pred,
         prediction_details(SPK_MSET using *) over (order by ts) det
    from SPK_TEST)
 where pred = 0 and minute_no in (select minute_no from SPK_TEST where minute_no > 150)
   and rownum <= 3;

-- ------------------------------------------------------------------ one-class SVM and EM anomaly
declare
  v dbms_data_mining.setting_list;
begin
  v('ALGO_NAME')            := 'ALGO_SUPPORT_VECTOR_MACHINES';
  v('SVMS_KERNEL_FUNCTION') := 'SVMS_GAUSSIAN';
  v('SVMS_OUTLIER_RATE')    := '0.01';
  v('PREP_AUTO')            := 'ON';
  dbms_data_mining.create_model2('SPK_SVM', 'CLASSIFICATION', 'select * from SPK_TRAIN', v, 'TS', null);
  dbms_output.put_line('SPK_SVM built');
  v.delete;
  v('ALGO_NAME')            := 'ALGO_EXPECTATION_MAXIMIZATION';
  v('EMCS_OUTLIER_RATE')    := '0.01';
  v('PREP_AUTO')            := 'ON';
  dbms_data_mining.create_model2('SPK_EM', 'CLASSIFICATION', 'select * from SPK_TRAIN', v, 'TS', null);
  dbms_output.put_line('SPK_EM built');
end;
/
prompt == SVM and EM on the same test minutes
select sum(case when minute_no <= 150 and prediction(SPK_SVM using *) = 0 then 1 else 0 end) svm_before,
       sum(case when minute_no >  150 and prediction(SPK_SVM using *) = 0 then 1 else 0 end) svm_after,
       min(case when minute_no >  150 and prediction(SPK_SVM using *) = 0 then minute_no end) svm_first,
       sum(case when minute_no <= 150 and prediction(SPK_EM using *) = 0 then 1 else 0 end) em_before,
       sum(case when minute_no >  150 and prediction(SPK_EM using *) = 0 then 1 else 0 end) em_after,
       min(case when minute_no >  150 and prediction(SPK_EM using *) = 0 then minute_no end) em_first
  from SPK_TEST;
prompt == SVM PREDICTION_DETAILS for one flagged minute
select minute_no, prediction_details(SPK_SVM using *) det
  from SPK_TEST where minute_no > 150 and prediction(SPK_SVM using *) = 0 and rownum = 1;
exit
