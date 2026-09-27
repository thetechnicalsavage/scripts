-- v1.1 - NL2SQL accuracy lab, practice 4: a CURATED object list, ENFORCED.
--        v1.1: display only - wider attribute listing.
--
-- Run as : NL2SQL_LAB, after 06_constraints.sql
-- Re-run : safe; the attributes are set to the same values.
--
-- Up to now the profile offered all 34 tables. Here it offers the 7 that answer sales
-- questions, and enforce_object_list makes Select AI reject SQL that touches anything else.

set feedback off serveroutput on
whenever sqlerror exit failure

begin
  dbms_cloud_ai.set_attribute(
    profile_name    => 'NL2SQL_LAB_AI',
    attribute_name  => 'object_list',
    attribute_value => '[{"owner":"NL2SQL_LAB","name":"ORD_HDR"},
                         {"owner":"NL2SQL_LAB","name":"ORD_LN"},
                         {"owner":"NL2SQL_LAB","name":"CUST_MST"},
                         {"owner":"NL2SQL_LAB","name":"PRD_MST"},
                         {"owner":"NL2SQL_LAB","name":"CAT_LKP"},
                         {"owner":"NL2SQL_LAB","name":"RGN_MST"},
                         {"owner":"NL2SQL_LAB","name":"SLS_TGT"}]');
  dbms_cloud_ai.set_attribute(
    profile_name    => 'NL2SQL_LAB_AI',
    attribute_name  => 'enforce_object_list',
    attribute_value => 'true');
end;
/

prompt
set linesize 200 pagesize 100
column attribute_name  format a20
column attribute_value format a150 word_wrapped
select attribute_name,
       case when attribute_name = 'oci_compartment_id' then '<hidden>'
            else dbms_lob.substr(attribute_value, 400, 1) end attribute_value
  from user_cloud_ai_profile_attributes
 where profile_name = 'NL2SQL_LAB_AI'
 order by attribute_name;
