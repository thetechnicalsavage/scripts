-- v1.0 - NL2SQL accuracy lab, practice 5 done right: the views REPLACE the base tables.
--
-- Run as : NL2SQL_LAB, after 08_views.sql
-- Re-run : safe; the attribute is set to the same value.
--
-- Measured here: offering SALES_V/ORDERS_V NEXT TO the base tables they are built on
-- dropped accuracy from 86% to 59%. The model mixed the two - it put view columns such
-- as FISCAL_YEAR and CALENDAR_YEAR onto base-table aliases (ORA-00904, caught by
-- Select AI's own validation). So the object list now offers the views and only the
-- tables the views do not already cover: customers (join date, segment), regions and
-- targets.

set feedback off serveroutput on
whenever sqlerror exit failure

begin
  dbms_cloud_ai.set_attribute(
    profile_name    => 'NL2SQL_LAB_AI',
    attribute_name  => 'object_list',
    attribute_value => '[{"owner":"NL2SQL_LAB","name":"SALES_V"},
                         {"owner":"NL2SQL_LAB","name":"ORDERS_V"},
                         {"owner":"NL2SQL_LAB","name":"CUST_MST"},
                         {"owner":"NL2SQL_LAB","name":"RGN_MST"},
                         {"owner":"NL2SQL_LAB","name":"SLS_TGT"}]');
end;
/

prompt
select dbms_lob.substr(attribute_value, 400, 1) as object_list
  from user_cloud_ai_profile_attributes
 where profile_name = 'NL2SQL_LAB_AI' and attribute_name = 'object_list';
