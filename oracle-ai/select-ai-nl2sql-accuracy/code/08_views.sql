-- v1.0 - NL2SQL accuracy lab, practice 5: SEMANTIC VIEWS - encode the joins and metrics once.
--
-- Run as : NL2SQL_LAB, after 07_object_list.sql
-- Re-run : safe (CREATE OR REPLACE; comments overwrite; attribute set to the same value).
--
-- Two views carry the business rules as columns, so the model no longer has to derive
-- them: SALES_V holds ONLY completed order lines, with revenue, margin, fiscal year and
-- quarter, region and category names already joined in; ORDERS_V holds every order
-- (any status) for count and rate questions. The base tables stay in the object list.

set feedback off serveroutput on
whenever sqlerror exit failure

create or replace view sales_v as
select h.ord_no                                                         as order_no,
       h.ord_dt                                                         as order_date,
       extract(year from h.ord_dt)                                      as calendar_year,
       'FY' || to_char(add_months(trunc(h.ord_dt, 'MM'), -3), 'YYYY')   as fiscal_year,
       to_number(to_char(add_months(h.ord_dt, -3), 'Q'))                as fiscal_quarter,
       h.cust_no                                                        as customer_no,
       c.cust_nm                                                        as customer_name,
       decode(c.seg_cd, 'R', 'Retail', 'W', 'Wholesale', 'O', 'Online-only') as customer_segment,
       r.rgn_nm                                                         as customer_region,
       decode(h.chnl_cd, 'ST', 'Store', 'WB', 'Web', 'MP', 'Marketplace') as sales_channel,
       case when h.chnl_cd in ('WB', 'MP') then 'Y' else 'N' end        as is_online,
       l.prd_id                                                         as product_id,
       p.prd_desc                                                       as product_name,
       k.cat_desc                                                       as product_category,
       l.qty                                                            as units_sold,
       l.disc_pct                                                       as discount_pct,
       l.net_amt                                                        as revenue,
       l.net_amt - l.qty * p.unit_cost                                  as gross_margin
  from ord_hdr h
  join ord_ln   l on l.ord_no  = h.ord_no
  join cust_mst c on c.cust_no = h.cust_no
  join rgn_mst  r on r.rgn_cd  = c.rgn_cd
  join prd_mst  p on p.prd_id  = l.prd_id
  join cat_lkp  k on k.cat_cd  = p.cat_cd
 where h.sts_cd = 'C';

create or replace view orders_v as
select h.ord_no                                                         as order_no,
       h.ord_dt                                                         as order_date,
       extract(year from h.ord_dt)                                      as calendar_year,
       'FY' || to_char(add_months(trunc(h.ord_dt, 'MM'), -3), 'YYYY')   as fiscal_year,
       decode(h.sts_cd, 'C', 'Completed', 'X', 'Cancelled', 'R', 'Returned', 'P', 'Pending') as order_status,
       decode(h.chnl_cd, 'ST', 'Store', 'WB', 'Web', 'MP', 'Marketplace') as sales_channel,
       h.cust_no                                                        as customer_no,
       r.rgn_nm                                                         as customer_region,
       decode(c.seg_cd, 'R', 'Retail', 'W', 'Wholesale', 'O', 'Online-only') as customer_segment
  from ord_hdr h
  join cust_mst c on c.cust_no = h.cust_no
  join rgn_mst  r on r.rgn_cd  = c.rgn_cd;

comment on table sales_v is 'Completed sales only: one row per order line of a COMPLETED order. Use for every revenue, sales, units, discount and margin question. Cancelled, returned and pending orders are already excluded.';
comment on column sales_v.fiscal_year      is 'Fiscal year label, e.g. FY2025 = 1-Apr-2025 to 31-Mar-2026.';
comment on column sales_v.fiscal_quarter   is 'Fiscal quarter 1-4; quarter 1 = April to June.';
comment on column sales_v.calendar_year    is 'Calendar year of the order date.';
comment on column sales_v.customer_region  is 'Region of the ordering customer: North, South, East, West or Central.';
comment on column sales_v.sales_channel    is 'Store, Web or Marketplace.';
comment on column sales_v.is_online        is 'Y for Web and Marketplace orders.';
comment on column sales_v.revenue          is 'Revenue of the line in INR after discount.';
comment on column sales_v.gross_margin     is 'Gross margin in INR = revenue - units * unit cost.';
comment on column sales_v.units_sold       is 'Units sold on the line.';

comment on table orders_v is 'Every order with its status, one row per order. Use for order counts, cancellation and return rates. Has no amounts; use SALES_V for revenue.';
comment on column orders_v.order_status    is 'Completed, Cancelled, Returned or Pending.';
comment on column orders_v.fiscal_year     is 'Fiscal year label, e.g. FY2025 = 1-Apr-2025 to 31-Mar-2026.';
comment on column orders_v.customer_region is 'Region of the ordering customer.';

begin
  dbms_cloud_ai.set_attribute(
    profile_name    => 'NL2SQL_LAB_AI',
    attribute_name  => 'object_list',
    attribute_value => '[{"owner":"NL2SQL_LAB","name":"SALES_V"},
                         {"owner":"NL2SQL_LAB","name":"ORDERS_V"},
                         {"owner":"NL2SQL_LAB","name":"ORD_HDR"},
                         {"owner":"NL2SQL_LAB","name":"ORD_LN"},
                         {"owner":"NL2SQL_LAB","name":"CUST_MST"},
                         {"owner":"NL2SQL_LAB","name":"PRD_MST"},
                         {"owner":"NL2SQL_LAB","name":"CAT_LKP"},
                         {"owner":"NL2SQL_LAB","name":"RGN_MST"},
                         {"owner":"NL2SQL_LAB","name":"SLS_TGT"}]');
end;
/

prompt
select 'SALES_V' view_name, count(*) rows_, sum(revenue) revenue from sales_v
union all
select 'ORDERS_V', count(*), null from orders_v;
