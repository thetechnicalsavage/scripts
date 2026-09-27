-- v1.1 - NL2SQL accuracy lab: the BASELINE profile - what a first attempt usually looks like.
--        v1.1: region is an argument (was hard-coded), so it matches the ACE host.
--
-- Run as : NL2SQL_LAB
-- Usage  : @03_profile_baseline.sql <oci_compartment_ocid> <oci_region>   e.g. us-phoenix-1
-- Re-run : drop-and-recreate of the profile NL2SQL_LAB_AI.
--
-- Baseline = the whole schema in the object list and nothing else: no comments,
-- annotations or constraints attributes, no enforce_object_list. temperature is 0 here
-- and in every later step, so the ONLY thing that changes between steps is metadata.
-- On-prem there is no PDB compartment to default to, so oci_compartment_id is required.

set verify off feedback off serveroutput on long 20000 linesize 200 pagesize 100
whenever sqlerror exit failure

define compartment = &1
define region      = &2

begin
  for p in (select profile_name from user_cloud_ai_profiles where profile_name = 'NL2SQL_LAB_AI') loop
    dbms_cloud_ai.drop_profile(profile_name => p.profile_name, force => true);
  end loop;

  dbms_cloud_ai.create_profile(
    profile_name => 'NL2SQL_LAB_AI',
    attributes   => '{
      "provider"           : "oci",
      "credential_name"    : "NL2SQL_LAB_CRED",
      "region"             : "&region",
      "oci_compartment_id" : "&compartment",
      "model"              : "xai.grok-4.20-non-reasoning",
      "object_list"        : [{"owner": "NL2SQL_LAB"}],
      "temperature"        : 0
    }',
    status       => 'enabled',
    description  => 'NL2SQL accuracy lab - baseline');
end;
/

prompt
prompt Profile attributes (compartment hidden):
column attribute_name  format a20
column attribute_value format a70
select attribute_name,
       case when attribute_name = 'oci_compartment_id' then '<hidden>'
            else dbms_lob.substr(attribute_value, 70, 1) end attribute_value
  from user_cloud_ai_profile_attributes
 where profile_name = 'NL2SQL_LAB_AI'
 order by attribute_name;

prompt
prompt Round trip to the model:
select dbms_cloud_ai.generate(
         prompt       => 'Reply with the single word OK',
         profile_name => 'NL2SQL_LAB_AI',
         action       => 'chat') as reply
  from dual;
