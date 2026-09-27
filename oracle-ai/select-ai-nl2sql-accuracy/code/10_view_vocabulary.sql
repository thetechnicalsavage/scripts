-- v1.1 - NL2SQL accuracy lab: carry the business vocabulary onto the semantic views.
--        v1.1: identifiers checked with DBMS_ASSERT before dynamic DDL.
--
-- Run as : NL2SQL_LAB, after 09_views_replace_tables.sql
-- Re-run : safe (comments overwrite; annotations are dropped IF EXISTS and re-added).
--
-- Measured here: once the views replaced the base tables, "Rank the product lines ..."
-- failed on every run - the synonym "product line" was annotated on PRD_MST.CAT_CD, a
-- table no longer in the object list. Vocabulary does not follow data through a view;
-- it has to be put on the view. Nothing new is taught here: these are the same words
-- 05_annotations.sql put on the base tables.

set feedback off serveroutput on
whenever sqlerror exit failure

comment on column sales_v.product_category is 'Product category, also called product line or merchandise category: Electronics, Home and Kitchen, Large Appliances, Toys and Games, Sports and Fitness.';
comment on column sales_v.product_name     is 'Product name. For product lines use PRODUCT_CATEGORY, not this column.';
comment on column sales_v.revenue          is 'Revenue of the line in INR after discount; also called sales, sales value, booked revenue, turnover or earnings.';
comment on column sales_v.units_sold       is 'Units sold on the line; also called units, quantity, items sold or volume.';
comment on column sales_v.customer_segment is 'Retail, Wholesale (also called B2B or trade) or Online-only.';
comment on column sales_v.sales_channel    is 'Store (in-store, offline), Web (website, e-commerce) or Marketplace. Online = Web or Marketplace.';
comment on column sales_v.order_no         is 'Order number; several lines share one order.';
comment on column orders_v.order_status    is 'Completed (booked, fulfilled), Cancelled, Returned (refunded) or Pending (open).';
comment on column orders_v.sales_channel   is 'Store, Web or Marketplace. Online = Web or Marketplace.';

-- View-level annotations (23ai/26ai).
declare
  procedure annotate_view(p_view varchar2, p_name varchar2, p_value varchar2) is
  begin
    execute immediate 'alter view ' || dbms_assert.simple_sql_name(p_view) || ' annotations (drop if exists '
                      || dbms_assert.simple_sql_name(p_name) || ')';
    execute immediate 'alter view ' || p_view || ' annotations (add ' || p_name || ' '''
                      || replace(p_value, '''', '''''') || ''')';
  end;
begin
  annotate_view('sales_v',  'Business_Description', 'Completed sales at order-line grain. Product line = PRODUCT_CATEGORY. Booked revenue = REVENUE.');
  annotate_view('orders_v', 'Business_Description', 'One row per order, every status. Use for counts and rates, not amounts.');
end;
/

prompt
select table_name, column_name, substr(comments, 1, 80) comments
  from user_col_comments
 where table_name in ('SALES_V', 'ORDERS_V') and comments is not null
 order by table_name, column_name;
select object_name, annotation_name, annotation_value
  from user_annotations_usage
 where object_name in ('SALES_V', 'ORDERS_V');
