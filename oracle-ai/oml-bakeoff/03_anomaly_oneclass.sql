-- v1.0 | 03_anomaly_oneclass.sql | CHANGES STATE
--
-- ANOMALY DETECTION IS CLASSIFICATION WITH NO TARGET. That is the whole trick
-- and it is the line worth searching for.

declare
  v_set dbms_data_mining.setting_list;
begin
  begin dbms_data_mining.drop_model('BILL_ANOM_EM');
  exception when others then null; end;

  v_set('ALGO_NAME')         := 'ALGO_EXPECTATION_MAXIMIZATION';
  v_set('PREP_AUTO')         := 'ON';
  v_set('EMCS_OUTLIER_RATE') := '0.02';       -- algorithm-specific

  dbms_data_mining.create_model2(
    model_name          => 'BILL_ANOM_EM',
    mining_function     => 'CLASSIFICATION',   -- yes, CLASSIFICATION
    data_query          => 'select * from V_TRAIN_ANOMALY',
    set_list            => v_set,
    case_id_column_name => 'SUBSCRIBER_ID',
    target_column_name  => null);              -- and this is what makes it one-class
end;
/

-- SVM takes different settings for the same job.
declare
  v_set dbms_data_mining.setting_list;
begin
  begin dbms_data_mining.drop_model('BILL_ANOM_SVM');
  exception when others then null; end;

  v_set('ALGO_NAME')            := 'ALGO_SUPPORT_VECTOR_MACHINES';
  v_set('PREP_AUTO')            := 'ON';
  v_set('SVMS_OUTLIER_RATE')    := '0.02';
  v_set('SVMS_KERNEL_FUNCTION') := 'SVMS_GAUSSIAN';

  dbms_data_mining.create_model2(
    model_name          => 'BILL_ANOM_SVM',
    mining_function     => 'CLASSIFICATION',
    data_query          => 'select * from V_TRAIN_ANOMALY',
    set_list            => v_set,
    case_id_column_name => 'SUBSCRIBER_ID',
    target_column_name  => null);
end;
/

-- THE MEASUREMENT TRAP.
-- Scoring an unsupervised detector means asking "is what it flags actually
-- extreme?". The first attempt ranked on a bill-volatility measure that is NULL
-- for prepaid lines, because they have no invoices, and EVERY SCORE COLLAPSED
-- TO ZERO. Rank on a composite every subscriber has, or the detector tells you
-- nothing, quietly.
select model, avg(extremeness) as mean_extremeness, count(*) as flagged
  from (select 'EM' as model, s.subscriber_id,
               s.extremeness_composite as extremeness   -- every row has this
          from V_SCORE_ANOMALY s
         where prediction(BILL_ANOM_EM using *) = 1
        union all
        select 'SVM', s.subscriber_id, s.extremeness_composite
          from V_SCORE_ANOMALY s
         where prediction(BILL_ANOM_SVM using *) = 1)
 group by model;
