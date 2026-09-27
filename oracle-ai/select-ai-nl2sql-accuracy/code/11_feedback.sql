-- v1.3 - NL2SQL accuracy lab, practice 6: FEEDBACK - correct what is still wrong.
--        v1.1: run each SELECT AI SHOWSQL first - FEEDBACK by sql_text raised ORA-20000
--              "No matching SQL statement found" for a statement never executed.
--        v1.2: no positive feedback - it stored Select AI's "Sorry, unfortunately a valid
--              SELECT statement could not be generated" reply as the approved answer.
--              Confirm SQL only after a human has checked what SHOWSQL returned.
--        v1.3: embedding model passed as arguments instead of hard-coded.
--
-- Run as : NL2SQL_LAB, after 10_view_vocabulary.sql and 00b_grant_embedding_model.sql
-- Usage  : @11_feedback.sql <EMBED_OWNER> <EMBED_MODEL>   e.g. ASKORACLE ALL_MINILM_L12_V2
-- Re-run : safe. Each entry is deleted first (operation => 'delete'), then added again.
--
-- On-prem, feedback needs an embedding model: it stores each entry in a vector index
-- (NL2SQL_LAB_AI_FEEDBACK_VECINDEX, created on first use) and later retrieves the entries
-- most similar to a new prompt as hints. With provider "oci" and no embedding_model the
-- first call failed (ORA-20404 on OCI's embedText endpoint); the in-database ONNX model
-- below works.
--
-- Feedback here covers ONLY the questions that still failed at least once in the
-- previous step. Every corrected SQL was checked against the gold answer first. The
-- paraphrased questions get no feedback, so they measure whether it generalises.

set feedback off serveroutput on verify off
whenever sqlerror exit failure
define embed_owner = &1
define embed_model = &2

begin
  dbms_cloud_ai.set_attribute(profile_name    => 'NL2SQL_LAB_AI',
                              attribute_name  => 'embedding_model',
                              attribute_value => 'database:&embed_owner..&embed_model');
end;
/

-- FEEDBACK by sql_text looks the statement up among SELECT AI statements this database
-- has already run, so each one is run first. This also shows the SQL being corrected.
exec dbms_cloud_ai.set_profile('NL2SQL_LAB_AI')
set long 20000 linesize 200 pagesize 100
column response format a180 word_wrapped
prompt
prompt SQL> select ai showsql What was the average order value of online orders in calendar year 2025?
select ai showsql What was the average order value of online orders in calendar year 2025?;
prompt
prompt SQL> select ai showsql What percentage of orders were returned in calendar year 2025 for each sales channel? Round to one decimal place.
select ai showsql What percentage of orders were returned in calendar year 2025 for each sales channel? Round to one decimal place.;
prompt
prompt SQL> select ai showsql How many customers who joined in 2025 have never placed an order?
select ai showsql How many customers who joined in 2025 have never placed an order?;
prompt
prompt SQL> select ai showsql How many orders were cancelled in fiscal year 2024?
select ai showsql How many orders were cancelled in fiscal year 2024?;
prompt
prompt SQL> select ai showsql Which 5 products generated the most revenue in calendar year 2025?
select ai showsql Which 5 products generated the most revenue in calendar year 2025?;

declare
  procedure fb(p_question varchar2, p_type varchar2, p_sql clob := null, p_hint varchar2 := null) is
    l_text varchar2(4000) := 'select ai showsql ' || p_question;
  begin
    begin
      dbms_cloud_ai.feedback(profile_name => 'NL2SQL_LAB_AI', sql_text => l_text, operation => 'delete');
    exception when others then null;   -- nothing to delete on the first run
    end;
    if p_type = 'negative' then
      dbms_cloud_ai.feedback(profile_name     => 'NL2SQL_LAB_AI',
                             sql_text         => l_text,
                             feedback_type    => 'negative',
                             response         => p_sql,
                             feedback_content => p_hint);
    else
      dbms_cloud_ai.feedback(profile_name  => 'NL2SQL_LAB_AI',
                             sql_text      => l_text,
                             feedback_type => 'positive');
    end if;
    dbms_output.put_line(rpad(p_type, 9) || p_question);
  end;
begin
  fb('What was the average order value of online orders in calendar year 2025?', 'negative',
     q'[SELECT AVG(order_value) AS avg_order_value
          FROM (SELECT order_no, SUM(revenue) AS order_value
                  FROM NL2SQL_LAB.SALES_V
                 WHERE is_online = 'Y' AND calendar_year = 2025
                 GROUP BY order_no)]',
     'Average order value: first SUM(REVENUE) per ORDER_NO, then AVG of those order totals. Never AVG of individual lines.');

  fb('What percentage of orders were returned in calendar year 2025 for each sales channel? Round to one decimal place.', 'negative',
     q'[SELECT sales_channel,
               ROUND(100 * SUM(CASE WHEN order_status = 'Returned' THEN 1 ELSE 0 END) / COUNT(*), 1) AS returned_pct
          FROM NL2SQL_LAB.ORDERS_V
         WHERE calendar_year = 2025
         GROUP BY sales_channel]',
     'Return rate = returned orders / all orders of any status, from ORDERS_V.');

  fb('How many customers who joined in 2025 have never placed an order?', 'negative',
     q'[SELECT COUNT(*) AS customers
          FROM NL2SQL_LAB.CUST_MST c
         WHERE c.join_dt >= DATE '2025-01-01' AND c.join_dt < DATE '2026-01-01'
           AND NOT EXISTS (SELECT 1 FROM NL2SQL_LAB.ORDERS_V o WHERE o.customer_no = c.cust_no)]',
     'Compare DATE columns with DATE literals directly; never wrap a date in UPPER().');

  fb('How many orders were cancelled in fiscal year 2024?', 'negative',
     q'[SELECT COUNT(*) AS cancelled_orders
          FROM NL2SQL_LAB.ORDERS_V
         WHERE order_status = 'Cancelled' AND fiscal_year = 'FY2024']',
     'Use the FISCAL_YEAR label (FY2024), never a computed date range.');

  fb('Which 5 products generated the most revenue in calendar year 2025?', 'negative',
     q'[SELECT product_name, SUM(revenue) AS revenue
          FROM NL2SQL_LAB.SALES_V
         WHERE calendar_year = 2025
         GROUP BY product_name
         ORDER BY revenue DESC
         FETCH FIRST 5 ROWS ONLY]',
     'Top products by revenue come from SALES_V grouped by PRODUCT_NAME.');
end;
/

prompt
select count(*) as feedback_entries from nl2sql_lab_ai_feedback_vecindex$vectab;
