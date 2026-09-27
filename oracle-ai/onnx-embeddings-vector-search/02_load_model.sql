-- v1.0 | 02_load_model.sql | CHANGES STATE
--
-- Load Oracle's prebuilt all_MiniLM_L12_v2 embedding model, the way Oracle's
-- "Import ONNX Models into Oracle AI Database End-to-End Example" does it:
-- https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/import-onnx-models-oracle-ai-database-end-end-example.html
--
-- WHERE THE FILE COMES FROM. Oracle's page "Import Pretrained Models in ONNX
-- Format" lists all_MiniLM_L12_v2, 384 dimensions, with a download link to
-- all_MiniLM_L12_v2_augmented.zip. Take the zip from that page:
-- https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/import-pretrained-models-onnx-format-vector-generation-database.html
-- It holds all_MiniLM_L12_v2.onnx (133,322,334 bytes) and
-- LICENSE_ATTRIBUTION.txt. Per that file, the licence is Apache License 2.0.
-- Unzip it into a folder the database can read.
--
-- Three parts, two users:
--   PART 1  as SYSDBA, in the PDB: the DM_DUMP directory and the grants
--   PART 2  as the model owner: the load
--   PART 3  as the model owner: the smoke test
--
-- Start it connected AS SYSDBA to the PDB:
--   @02_load_model.sql <model_owner> <folder_holding_the_onnx> <pdb_connect_identifier>
-- After part 1 the script connects as the model owner and SQL*Plus asks for
-- that password. Nothing is stored in this file.
--
-- To reload over an existing model, uncomment the DROP_ONNX_MODEL line in
-- part 2. Oracle's example has the same optional drop.

whenever sqlerror exit failure

define model_owner = '&1'
define model_dir   = '&2'
define pdb_connect = '&3'

-- =========================================================================
-- PART 1. As SYSDBA, in the PDB.
-- =========================================================================

-- Stop before changing anything if this is the root.
begin
  if sys_context('userenv','con_name') = 'CDB$ROOT' then
    raise_application_error(-20001,
      'Part 1 runs as SYSDBA in the PDB, not in CDB$ROOT.');
  end if;
end;
/

grant db_developer_role to &model_owner.;
grant create mining model to &model_owner.;

-- DM_DUMP points at the folder holding the unzipped all_MiniLM_L12_v2.onnx.
create or replace directory DM_DUMP as '&model_dir.';
grant read, write on directory DM_DUMP to &model_owner.;

-- =========================================================================
-- PART 2. As the model owner.
-- =========================================================================

connect &model_owner.@&pdb_connect.

-- Optional, only when reloading. Oracle's example drops the model first:
-- exec dbms_vector.drop_onnx_model(model_name => 'ALL_MINILM_L12_V2', force => true)

-- No metadata argument. The DBMS_VECTOR reference gives its default as
-- JSON('{"function" : "embedding", "embeddingOutput" : "embedding", "input": {"input":["DATA"]}}')
begin
  dbms_vector.load_onnx_model(
    directory  => 'DM_DUMP',
    file_name  => 'all_MiniLM_L12_v2.onnx',
    model_name => 'ALL_MINILM_L12_V2');
end;
/

-- =========================================================================
-- PART 3. Smoke test, still as the model owner.
-- =========================================================================

-- On the instance measured, MODEL_SIZE for this model was 133,322,334 bytes,
-- the size of the .onnx file in Oracle's zip.
select model_name, mining_function, algorithm
  from user_mining_models
 where model_name = 'ALL_MINILM_L12_V2';

select vector_dimension_count(
         vector_embedding(ALL_MINILM_L12_V2 using 'test' as data)) as dims
  from dual;
-- 384

-- Another schema that calls the model needs SELECT on it. See 01_grants.sql.

whenever sqlerror continue
