-- v1.0 | 02_clustering.sql | CHANGES STATE
--
-- CLUSTERING HAS NO ACCURACY, so the criterion is how confidently each point is
-- assigned: average CLUSTER_PROBABILITY of the winning cluster. A model whose
-- points sit firmly in one cluster has found real structure; one whose points
-- split near evenly has not. Cluster balance is the tiebreak, because one giant
-- cluster and seven singletons is useless on a screen.
--
-- Score on CLUSTERS ACTUALLY POPULATED, not the number you asked for: EM prunes
-- its own components and tends to collapse, so k-Means is given several k values
-- and a fair chance to win on the real criterion.

declare
  type t_cand is record (algo varchar2(40), k number);
  type t_list is table of t_cand;
  l_list t_list := t_list(
    t_cand('ALGO_KMEANS', 5), t_cand('ALGO_KMEANS', 6),
    t_cand('ALGO_KMEANS', 7), t_cand('ALGO_KMEANS', 8),
    t_cand('ALGO_EXPECTATION_MAXIMIZATION', 6),
    t_cand('ALGO_EXPECTATION_MAXIMIZATION', 8),
    t_cand('ALGO_O_CLUSTER', 6));
  v_set dbms_data_mining.setting_list;
  l_tmp varchar2(30);
  l_conf number;
  l_pop  number;
begin
  for i in 1 .. l_list.count loop
    l_tmp := 'SEG_C'||i;
    begin
      begin dbms_data_mining.drop_model(l_tmp);
      exception when others then null; end;

      v_set.delete;
      v_set('ALGO_NAME') := l_list(i).algo;
      v_set('PREP_AUTO') := 'ON';
      -- Setting name and value must match the algorithm, or ORA-40205.
      v_set('CLUS_NUM_CLUSTERS') := to_char(l_list(i).k);

      dbms_data_mining.create_model2(
        model_name          => l_tmp,
        mining_function     => 'CLUSTERING',
        data_query          => 'select * from V_TRAIN_SEGMENT',
        set_list            => v_set,
        case_id_column_name => 'SUBSCRIBER_ID');

      select avg(cp), count(distinct cid)
        into l_conf, l_pop
        from (select cluster_id(SEG_C1 using *)          as cid,
                     cluster_probability(SEG_C1 using *) as cp
                from V_TRAIN_SEGMENT);

      insert into MODEL_CANDIDATE
        (component_id, algorithm, metric_name, metric_value)
      values (3, l_list(i).algo||' k='||l_list(i).k,
              'MEAN_CLUSTER_PROB (pop='||l_pop||')', l_conf);
    exception when others then
      insert into MODEL_CANDIDATE
        (component_id, algorithm, metric_name, metric_value)
      values (3, l_list(i).algo||' k='||l_list(i).k, 'FAILED', null);
    end;
  end loop;
  commit;
end;
/
