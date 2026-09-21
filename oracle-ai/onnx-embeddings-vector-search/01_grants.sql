-- v1.0 | 01_grants.sql | CHANGES STATE
-- What an application schema needs to call the vector operators.
-- Run as a user that can grant these.

define app_schema = '&1'

grant execute on dbms_vector       to &app_schema.;
grant execute on dbms_vector_chain to &app_schema.;

-- Only needed if the model lives in a different schema from the caller.
-- grant execute on MODEL_OWNER.ALL_MINILM_L12_V2 to &app_schema.;

-- Loading a model additionally needs CREATE MINING MODEL. The load procedure
-- itself is not in this repo: see the README.
