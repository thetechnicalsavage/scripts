-- v1.2 - NL2SQL accuracy lab, practice 7: AUTOMATED object list selection.
--        v1.2: region is an argument (was hard-coded).
--
-- Run as : NL2SQL_LAB, after 00b_grant_embedding_model.sql (and the metadata steps 04-10)
--        v1.1: embedding model passed as arguments instead of hard-coded.
-- Usage  : @12_automated_object_list.sql <oci_compartment_ocid> <oci_region> <EMBED_OWNER> <EMBED_MODEL>
-- Re-run : drop-and-recreate of the separate profile NL2SQL_LAB_AUTO.
--
-- A second profile, so it can be compared with the curated one: the WHOLE schema
-- (34 tables + 2 views) with object_list_mode "automated", and every metadata option on.
-- Select AI embeds each object's description into an object-list vector index when the
-- profile is created, then sends the model only the objects most similar to the prompt.
--
-- On-prem with provider "oci", creating this profile WITHOUT embedding_model failed at once:
--   ORA-20404: Object not found - https://inference.generativeai.<region>.oci.oraclecloud.com/20231130/actions/embedText
-- The in-database ONNX model works.

set verify off feedback off serveroutput on long 20000 linesize 200 pagesize 100
whenever sqlerror exit failure

define compartment = &1
define region      = &2
define embed_owner = &3
define embed_model = &4

begin
  for p in (select profile_name from user_cloud_ai_profiles where profile_name = 'NL2SQL_LAB_AUTO') loop
    dbms_cloud_ai.drop_profile(profile_name => p.profile_name, force => true);
  end loop;

  dbms_cloud_ai.create_profile(
    profile_name => 'NL2SQL_LAB_AUTO',
    attributes   => '{
      "provider"           : "oci",
      "credential_name"    : "NL2SQL_LAB_CRED",
      "region"             : "&region",
      "oci_compartment_id" : "&compartment",
      "model"              : "xai.grok-4.20-non-reasoning",
      "embedding_model"    : "database:&embed_owner..&embed_model",
      "object_list"        : [{"owner": "NL2SQL_LAB"}],
      "object_list_mode"   : "automated",
      "comments"           : true,
      "annotations"        : true,
      "constraints"        : true,
      "temperature"        : 0
    }',
    status       => 'enabled',
    description  => 'NL2SQL accuracy lab - automated object selection over the whole schema');
end;
/

prompt
prompt Vector indexes Select AI created for the lab:
select index_name, status from user_cloud_vector_indexes order by index_name;
