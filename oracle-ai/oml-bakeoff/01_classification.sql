-- v1.0 | 01_classification.sql | CHANGES STATE
--
-- SELECTION METRIC IS BALANCED ACCURACY, NOT RAW ACCURACY.
-- With a minority target, a model that predicts "no" for everyone scores well
-- on accuracy and is worthless. Balanced accuracy is the mean of the two class
-- recalls and exposes that immediately.

declare
  type t_algos is table of varchar2(40);
  l_algos t_algos := t_algos('ALGO_RANDOM_FOREST',
                             'ALGO_DECISION_TREE',
                             'ALGO_GENERALIZED_LINEAR_MODEL',
                             'ALGO_NAIVE_BAYES',
                             'ALGO_SUPPORT_VECTOR_MACHINES');
  v_set  dbms_data_mining.setting_list;
  l_tmp  varchar2(30);
  l_t0   timestamp;
  l_bal  number;
begin
  for i in 1 .. l_algos.count loop
    l_tmp := 'CHURN_C'||i;
    begin
      begin dbms_data_mining.drop_model(l_tmp);
      exception when others then null; end;

      v_set.delete;
      v_set('ALGO_NAME')        := l_algos(i);
      v_set('PREP_AUTO')        := 'ON';
      v_set('ODMS_RANDOM_SEED') := '23';

      -- Not every algorithm takes this one, so the bake-off has to know which
      -- settings apply to which candidate. Naive Bayes is deliberately out.
      if l_algos(i) in ('ALGO_RANDOM_FOREST','ALGO_DECISION_TREE',
                        'ALGO_SUPPORT_VECTOR_MACHINES',
                        'ALGO_GENERALIZED_LINEAR_MODEL') then
        v_set('CLAS_WEIGHTS_BALANCED') := 'ON';
      end if;

      l_t0 := systimestamp;
      dbms_data_mining.create_model2(
        model_name          => l_tmp,
        mining_function     => 'CLASSIFICATION',
        data_query          => 'select * from V_TRAIN_CHURN',
        set_list            => v_set,
        case_id_column_name => 'SUBSCRIBER_ID',   -- ORA-40104 if composite
        target_column_name  => 'CHURNED');

      -- Balanced accuracy over held-out rows: the mean of the two recalls.
      select (sum(case when actual='Y' and pred='Y' then 1 else 0 end)
                / nullif(sum(case when actual='Y' then 1 else 0 end),0)
            + sum(case when actual='N' and pred='N' then 1 else 0 end)
                / nullif(sum(case when actual='N' then 1 else 0 end),0)) / 2
        into l_bal
        from (select churned as actual,
                     prediction(CHURN_C1 using *) as pred
                from V_HOLDOUT_CHURN);

      insert into MODEL_CANDIDATE
        (component_id, algorithm, metric_name, metric_value, train_sec)
      values
        (1, l_algos(i), 'BALANCED_ACCURACY', l_bal,
         extract(second from (systimestamp - l_t0)));

    exception when others then
      -- Log the failure as a candidate too. A candidate that will not build is
      -- a result; silently skipping it makes the table a lie.
      insert into MODEL_CANDIDATE
        (component_id, algorithm, metric_name, metric_value, train_sec)
      values (1, l_algos(i), 'FAILED', null, null);
    end;
  end loop;
  commit;
end;
/

select * from V_BAKEOFF_RESULTS
 where mining_func = 'CLASSIFICATION'
 order by metric_value desc nulls last;
