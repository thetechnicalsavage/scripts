-- v1.0 | 04_timeseries_scope.sql | CHANGES STATE
--
-- THE PARTITION CEILING IS REAL AND IT HURTS EARLY.
-- ODMS_MAX_PARTITIONS accepts 32,767 and rejects 65,535 with ORA-40206. The
-- practical cost climbs long before the ceiling: partitioned ESM across tens of
-- thousands of series spends its build in checkpoint waits, not arithmetic.
--
-- So scope the model, and make the split VISIBLE rather than hiding it: every
-- forecast row records which source produced it.

declare
  v_set dbms_data_mining.setting_list;
begin
  begin dbms_data_mining.drop_model('USAGE_FCST');
  exception when others then null; end;

  v_set('ALGO_NAME')            := 'ALGO_EXPONENTIAL_SMOOTHING';
  v_set('EXSM_INTERVAL')        := 'EXSM_INTERVAL_DAY';
  v_set('EXSM_PREDICTION_STEP') := '30';
  v_set('ODMS_MAX_PARTITIONS')  := '32767';   -- 65535 raises ORA-40206

  dbms_data_mining.create_model2(
    model_name          => 'USAGE_FCST',
    mining_function     => 'TIME_SERIES',
    data_query          => 'select * from V_TRAIN_USAGE_SCOPED',  -- 32,000 series
    set_list            => v_set,
    case_id_column_name => 'USAGE_DAY',
    target_column_name  => 'MB_USED');
end;
/

-- A partitioned time-series model keys its output on an INTERNAL PARTITION
-- NAME, not on the partition column, so reading a forecast back means joining
-- the partition dictionary. Doing that per row does not scale; materialise the
-- map once and hash join against it.
create materialized view PARTITION_MAP as
select partition_name, partition_value as subscriber_id
  from user_mining_model_partitions
 where model_name = 'USAGE_FCST';

-- And materialise the forecast itself: reading it live from the model view does
-- not survive 32,000 partitions.
create table FORECAST_CACHE as
select m.subscriber_id, f.case_id as usage_day, f.prediction as mb_forecast,
       'MODEL' as forecast_source
  from table(dbms_data_mining.get_model_details_exsm('USAGE_FCST')) f
  join PARTITION_MAP m on m.partition_name = f.partition_name;

-- Everyone outside the scope gets the same level-and-trend smoothing in SQL,
-- tagged so the split is visible rather than hidden.
insert into FORECAST_CACHE (subscriber_id, usage_day, mb_forecast, forecast_source)
select subscriber_id, forecast_day, smoothed_mb, 'SQL_SMOOTHING'
  from V_USAGE_SMOOTHED_FALLBACK;
commit;

select forecast_source, count(*) from FORECAST_CACHE group by forecast_source;
