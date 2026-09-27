-- v1.1 | 01_grants.sql | CHANGES STATE
-- v1.1 2026-09-27: SELECT, not EXECUTE, on a mining model; package grants marked optional; loading points to 02_load_model.sql.
-- What an application schema needs to call the vector operators.
-- Run as a user that can grant these.

define app_schema = '&1'

-- Optional. Pattern B uses only the SQL functions VECTOR_EMBEDDING and
-- VECTOR_DISTANCE, so a vector-column query needs no package grant. On the
-- instance measured, PUBLIC already held EXECUTE on both packages. These two
-- grants are harmless, and they only matter if you call the PL/SQL APIs in
-- DBMS_VECTOR or DBMS_VECTOR_CHAIN.
grant execute on dbms_vector       to &app_schema.;
grant execute on dbms_vector_chain to &app_schema.;

-- Only needed if the model lives in a different schema from the caller.
-- A mining model takes SELECT ("score or view the mining model"). There is no
-- EXECUTE privilege on a mining model. On the instance measured, the
-- application schema held only this grant on the model, and embedded all 33
-- rows of its table with it.
-- grant select on mining model MODEL_OWNER.ALL_MINILM_L12_V2 to &app_schema.;

-- Loading a model is a separate job, done by the model owner. Oracle's example
-- grants that user DB_DEVELOPER_ROLE and CREATE MINING MODEL, plus READ and
-- WRITE on the directory holding the .onnx file. See 02_load_model.sql.
