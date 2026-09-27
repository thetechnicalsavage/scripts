-- v1.2 - NL2SQL accuracy lab, practice 2: ANNOTATIONS - how people actually talk.
--        v1.2: identifiers checked with DBMS_ASSERT before dynamic DDL.
--        v1.1: display only - one line per annotation.
--
-- Run as : NL2SQL_LAB, after 04_comments.sql
-- Re-run : safe. Each annotation is dropped IF EXISTS and added again.
--
-- Comments explain meaning; annotations carry vocabulary: business terms, synonyms and
-- usage hints as name/value pairs that travel with the schema. The same annotation
-- names as Oracle's post are used (Business_Term, Synonyms, NL2SQL_Hint,
-- Business_Description). Annotations are a 23ai/26ai feature.

set feedback off serveroutput on
whenever sqlerror exit failure

declare
  -- declared inside the block, so the lab user needs no CREATE PROCEDURE privilege
  procedure lab_annotate(p_table varchar2, p_column varchar2, p_name varchar2, p_value varchar2) is
    l_target varchar2(200);
  begin
    -- identifiers are asserted before they reach dynamic DDL; values are quoted and escaped
    l_target := dbms_assert.simple_sql_name(p_table)
                || case when p_column is not null then ' modify ' || dbms_assert.simple_sql_name(p_column) end;
    if dbms_assert.simple_sql_name(p_name) is null then null; end if;
    execute immediate 'alter table ' || l_target || ' annotations (drop if exists ' || p_name || ')';
    execute immediate 'alter table ' || l_target || ' annotations (add ' || p_name || ' '''
                      || replace(p_value, '''', '''''') || ''')';
  end;
begin
  -- the fact tables
  lab_annotate('ord_hdr', null, 'Business_Description', 'Customer orders. Revenue, units and margin come from completed orders only.');
  lab_annotate('ord_hdr', null, 'NL2SQL_Hint',          'Revenue = SUM(ORD_LN.NET_AMT) where ORD_HDR.STS_CD = ''C''. Filter periods on ORD_DT. FY2025 = ORD_DT from 2025-04-01 to 2026-03-31. Region and segment come from CUST_MST joined on CUST_NO.');
  lab_annotate('ord_hdr', 'sts_cd',  'Business_Term', 'Order status');
  lab_annotate('ord_hdr', 'sts_cd',  'Synonyms',      'completed, booked, fulfilled = C; cancelled = X; returned, refunded = R; pending, open = P');
  lab_annotate('ord_hdr', 'chnl_cd', 'Business_Term', 'Sales channel');
  lab_annotate('ord_hdr', 'chnl_cd', 'Synonyms',      'store, in-store, offline = ST; web, website, e-commerce = WB; marketplace, third-party marketplace = MP; online = WB or MP');
  lab_annotate('ord_hdr', 'ord_dt',  'Business_Term', 'Order date');

  lab_annotate('ord_ln', 'net_amt',  'Business_Term', 'Revenue');
  lab_annotate('ord_ln', 'net_amt',  'Synonyms',      'sales, sales value, booked revenue, turnover, earnings, income from sales');
  lab_annotate('ord_ln', 'qty',      'Business_Term', 'Units sold');
  lab_annotate('ord_ln', 'qty',      'Synonyms',      'units, quantity, items sold, volume');
  lab_annotate('ord_ln', 'disc_pct', 'Business_Term', 'Discount percentage');

  -- the dimensions
  lab_annotate('cust_mst', 'seg_cd',   'Business_Term', 'Customer segment');
  lab_annotate('cust_mst', 'seg_cd',   'Synonyms',      'retail = R; wholesale, B2B, trade = W; online-only, digital = O');
  lab_annotate('cust_mst', 'rgn_cd',   'Business_Term', 'Customer region');
  lab_annotate('cust_mst', 'join_dt',  'Synonyms',      'signed up, onboarded, became a customer');
  lab_annotate('prd_mst',  'cat_cd',   'Business_Term', 'Product category');
  lab_annotate('prd_mst',  'cat_cd',   'Synonyms',      'product line, category, merchandise category');
  lab_annotate('prd_mst',  'unit_cost','Synonyms',      'cost, cost of goods; gross margin and gross profit = NET_AMT - QTY * UNIT_COST');
  lab_annotate('cat_lkp',  'cat_desc', 'Synonyms',      'product line name, category name');
  lab_annotate('sls_tgt',  'tgt_amt',  'Synonyms',      'target, sales target, quota, budget');
end;
/


begin
  dbms_cloud_ai.set_attribute(profile_name    => 'NL2SQL_LAB_AI',
                              attribute_name  => 'annotations',
                              attribute_value => 'true');
end;
/

prompt
set linesize 200 pagesize 100
column object_name      format a10
column column_name      format a10
column annotation_name  format a20
column annotation_value format a110 word_wrapped
select object_name, column_name, annotation_name, annotation_value
  from user_annotations_usage
 order by object_name, column_name nulls first, annotation_name;
