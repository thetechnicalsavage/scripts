-- v1.0 | 00_check.sql | READ-ONLY
-- Is the ONNX embedding model there, what does it produce, and what vector
-- indexes exist? Run as the schema that will call the vector operators.

set pagesize 200 linesize 150 feedback off

prompt === the model ===
select model_name, mining_function, algorithm,
       round(model_size/1024/1024, 1) as mb
  from user_mining_models
 where algorithm = 'ONNX'
 order by model_name;

prompt
prompt === dimensions, and the fastest smoke test that it loaded correctly ===
prompt     Substitute your own model name if it is not the one below.
select vector_dimension_count(
         vector_embedding(ALL_MINILM_L12_V2 using 'test' as data)) as dims
  from dual;

prompt
prompt === pattern A: managed indexes ===
select index_name, status from user_cloud_vector_indexes order by index_name;

prompt
prompt === pattern B: your own VECTOR columns ===
select table_name, column_name, data_type
  from user_tab_columns
 where data_type = 'VECTOR'
 order by table_name, column_id;

set feedback on
