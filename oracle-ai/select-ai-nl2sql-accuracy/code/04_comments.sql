-- v1.0 - NL2SQL accuracy lab, practice 1: COMMENTS - say what tables and columns mean.
--
-- Run as : NL2SQL_LAB, after 03_profile_baseline.sql
-- Re-run : safe. COMMENT ON simply overwrites; the attribute is set, not toggled.
--
-- The business rules that were only in people's heads go into the dictionary: what the
-- status and channel codes mean, that revenue counts completed orders only, how the
-- fiscal year runs, which customer column drives region. Decoy tables are labelled
-- "do not use" - a comment can steer the model away from a table as well as towards one.
-- The 22 unrelated tables are left uncommented, as they usually are in real schemas.

set feedback off serveroutput on
whenever sqlerror exit failure

comment on table ord_hdr is 'Sales order header: one row per customer order. Join ORD_LN on ORD_NO for amounts. Fiscal years run April to March and are named by the year they start: FY2025 = 1-Apr-2025 to 31-Mar-2026, and fiscal quarter 1 = April to June.';
comment on column ord_hdr.ord_no     is 'Order number, unique per order.';
comment on column ord_hdr.cust_no    is 'Customer who placed the order (the ordering customer). Use this column, not BILL_TO_NO, for anything about customers, customer region or customer segment.';
comment on column ord_hdr.bill_to_no is 'Account the invoice was sent to. Differs from CUST_NO for some business customers. Not used for customer, region or segment analysis.';
comment on column ord_hdr.ord_dt     is 'Date the order was placed. Every period question (year, month, quarter, fiscal year) uses this date.';
comment on column ord_hdr.ship_dt    is 'Date the order shipped. Null for cancelled and pending orders. Not used for reporting periods.';
comment on column ord_hdr.sts_cd     is 'Order status: C = completed, X = cancelled, R = returned, P = pending. Revenue, sales, units sold and margin count completed orders only (STS_CD = ''C''). Order counts include every status unless a status is named.';
comment on column ord_hdr.chnl_cd    is 'Sales channel: ST = store, WB = web, MP = marketplace. Online orders are WB and MP.';

comment on table ord_ln is 'Order lines: one row per product on an order. NET_AMT is the revenue of the line.';
comment on column ord_ln.ord_no   is 'Order number; joins ORD_HDR.ORD_NO.';
comment on column ord_ln.ln_no    is 'Line number within the order.';
comment on column ord_ln.prd_id   is 'Product sold; joins PRD_MST.PRD_ID.';
comment on column ord_ln.qty      is 'Units sold on the line.';
comment on column ord_ln.unit_prc is 'List price per unit when ordered, before discount, in INR.';
comment on column ord_ln.disc_pct is 'Discount on the line in percent (5 means 5%).';
comment on column ord_ln.net_amt  is 'Revenue of the line in INR after discount = QTY * UNIT_PRC * (1 - DISC_PCT/100). Revenue = SUM(NET_AMT) over completed orders only.';

comment on table cust_mst is 'Customer master: one row per customer.';
comment on column cust_mst.cust_no  is 'Customer number.';
comment on column cust_mst.cust_nm  is 'Customer name.';
comment on column cust_mst.seg_cd   is 'Customer segment: R = retail, W = wholesale, O = online-only.';
comment on column cust_mst.rgn_cd   is 'Customer region code; joins RGN_MST. N = North, S = South, E = East, W = West, C = Central.';
comment on column cust_mst.city_nm  is 'City of the customer.';
comment on column cust_mst.join_dt  is 'Date the customer joined.';
comment on column cust_mst.cr_lmt   is 'Credit limit in INR.';
comment on column cust_mst.actv_flg is 'Y = active customer, N = inactive.';

comment on table prd_mst is 'Product master: one row per product.';
comment on column prd_mst.prd_id    is 'Product id.';
comment on column prd_mst.prd_desc  is 'Product name.';
comment on column prd_mst.cat_cd    is 'Product category code; joins CAT_LKP for the category name. ELEC = Electronics, HOME = Home and Kitchen, APPL = Large Appliances, TOYS = Toys and Games, SPRT = Sports and Fitness.';
comment on column prd_mst.brnd_nm   is 'Brand.';
comment on column prd_mst.list_prc  is 'Current list price in INR.';
comment on column prd_mst.unit_cost is 'Cost per unit in INR. Gross margin = NET_AMT - QTY * UNIT_COST.';

comment on table cat_lkp is 'Product category names.';
comment on column cat_lkp.cat_cd   is 'Category code.';
comment on column cat_lkp.cat_desc is 'Category name.';

comment on table rgn_mst is 'Sales regions.';
comment on column rgn_mst.rgn_cd is 'Region code.';
comment on column rgn_mst.rgn_nm is 'Region name.';

comment on table sls_tgt is 'Revenue target per customer region and fiscal year.';
comment on column sls_tgt.rgn_cd  is 'Region code; joins RGN_MST and CUST_MST.RGN_CD.';
comment on column sls_tgt.fisc_yr is 'Fiscal year label such as FY2025 (1-Apr-2025 to 31-Mar-2026).';
comment on column sls_tgt.tgt_amt is 'Revenue target in INR. A region beats its target when its revenue for the fiscal year is greater than TGT_AMT.';

-- decoys: say what they are
comment on table ar_rcpt      is 'Cash receipts: payments collected from customers. This is NOT revenue; never use it for sales or revenue questions.';
comment on table ord_hdr_stg  is 'Staging copy of a March 2026 load that contains duplicate rows. Do not use for reporting.';
comment on table ord_ln_stg   is 'Staging copy of March 2026 order lines with duplicates. Do not use for reporting.';
comment on table cust_mst_bkp is 'Obsolete backup snapshot of CUST_MST. Do not use.';

begin
  dbms_cloud_ai.set_attribute(profile_name    => 'NL2SQL_LAB_AI',
                              attribute_name  => 'comments',
                              attribute_value => 'true');
end;
/

prompt
select count(*) as tables_commented from user_tab_comments where comments is not null;
select count(*) as columns_commented from user_col_comments where comments is not null;
select attribute_name, dbms_lob.substr(attribute_value, 10, 1) attribute_value
  from user_cloud_ai_profile_attributes
 where profile_name = 'NL2SQL_LAB_AI' and attribute_name = 'comments';
