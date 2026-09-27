-- v1.2 - NL2SQL accuracy lab: what the vector-based features need on-prem - FEEDBACK and
--        automated object selection.
--        v1.2: owner and model names validated with DBMS_ASSERT before use.
--        v1.1: + EXECUTE on DBMS_CLOUD_PIPELINE (automated mode raised ORA-20000
--              "Missing EXECUTE privilege on DBMS_CLOUD_PIPELINE package" without it).
--
-- Run as : SYSDBA (or the model owner), connected to the PDB.
-- Usage  : @00b_grant_embedding_model.sql <MODEL_OWNER> <MODEL_NAME>
--          e.g. @00b_grant_embedding_model.sql ASKORACLE ALL_MINILM_L12_V2
-- Re-run : safe; re-granting an existing privilege is a no-op.
--
-- Why (tested on 26ai 23.26.1 with provider "oci"): DBMS_CLOUD_AI.FEEDBACK embeds the prompt.
-- With no embedding_model set it called OCI's embedText endpoint and failed with
--   ORA-20404: Object not found - https://inference.generativeai.<region>.oci.oraclecloud.com/20231130/actions/embedText
-- Pointing the profile at an in-database model ("embedding_model":"database:OWNER.MODEL")
-- first failed with ORA-20047 "Embedding model does not exist" until this grant was made.

set verify off feedback off serveroutput on
whenever sqlerror exit failure
define model_owner = &1
define model_name  = &2

-- validate both names before they are spliced into DDL
begin
  if dbms_assert.simple_sql_name('&model_owner') is null or dbms_assert.simple_sql_name('&model_name') is null then
    null;
  end if;
end;
/

-- 1. the in-database embedding model (owned by another schema here)
grant select on mining model &model_owner..&model_name to NL2SQL_LAB;

-- 2. automated object selection builds its object-list index through a pipeline
grant execute on C##CLOUD$SERVICE.DBMS_CLOUD_PIPELINE to NL2SQL_LAB;

select owner, model_name, mining_function, algorithm
  from dba_mining_models
 where owner = upper('&model_owner') and model_name = upper('&model_name');
