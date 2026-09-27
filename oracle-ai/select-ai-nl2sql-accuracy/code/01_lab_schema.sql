-- v1.1 - NL2SQL accuracy lab: a retail order schema with legacy-style names, plus decoys.
--        v1.1: drops ORDERS_V on rebuild (named CUSTOMER_V by mistake).
--
-- Run as : NL2SQL_LAB
-- Re-run : drop-and-rebuild. Every run drops the tables this script owns (and only
--          those), recreates them and reloads identical data. All "random" values come
--          from ORA_HASH with fixed seeds, so every rebuild is byte-for-byte the same and
--          accuracy results can be reproduced.
--
-- The schema deliberately starts with NO comments, NO annotations and NO constraints.
-- Later steps add each of them so their effect on NL2SQL accuracy can be measured.
--
-- Business rules that are true in this schema but visible only to people who know it
-- (later steps teach them to Select AI):
--   * revenue   = SUM(ORD_LN.NET_AMT) for orders with ORD_HDR.STS_CD = 'C' (completed)
--   * STS_CD    : C completed, X cancelled, R returned, P pending
--   * CHNL_CD   : ST store, WB web, MP marketplace. "online" = WB + MP
--   * SEG_CD    : R retail, W wholesale, O online-only
--   * fiscal year runs April to March and is named by its starting year:
--                 FY2025 = 01-APR-2025 .. 31-MAR-2026
--   * a customer's region is CUST_MST.RGN_CD of the ORDERING customer (CUST_NO),
--     never of the billing account (BILL_TO_NO)
--   * gross margin = NET_AMT - QTY * PRD_MST.UNIT_COST
--
-- Decoys, on purpose: ORD_HDR_STG / ORD_LN_STG (a duplicated staging load of March 2026),
-- CUST_MST_BKP (an old snapshot), AR_RCPT (cash received, not revenue) and 22 unrelated
-- tables whose column names collide with the real ones (NET_AMT, QTY, STS_CD, CUST_NO...).

set serveroutput on feedback off
whenever sqlerror exit failure

-- 1. drop what this script owns ------------------------------------------------------
begin
  for t in (select column_value tn from table(sys.odcivarchar2list(
              'RGN_MST','CAT_LKP','CUST_MST','PRD_MST','ORD_HDR','ORD_LN','SLS_TGT','AR_RCPT',
              'ORD_HDR_STG','ORD_LN_STG','CUST_MST_BKP',
              'EMP_MST','DEPT_MST','PAYRL_HDR','PAYRL_LN','LV_REQ','GL_ACCT','GL_JRNL_HDR',
              'GL_JRNL_LN','AP_VNDR','AP_INV','PO_HDR','PO_LN','WH_MST','INV_BAL','STK_MOV',
              'ASSET_REG','TKT_HDR','TKT_LOG','JOB_RUN_LOG','AUD_LOG','USR_MST','CURR_RATE',
              'PROMO_MST'))) loop
    execute immediate 'drop table if exists ' || t.tn || ' cascade constraints purge';
  end loop;
  -- views created by later steps depend on these tables
  for v in (select view_name from user_views where view_name in ('SALES_V','ORDERS_V')) loop
    execute immediate 'drop view ' || v.view_name;
  end loop;
end;
/

-- 2. the real tables ------------------------------------------------------------------
create table rgn_mst (rgn_cd varchar2(2) not null, rgn_nm varchar2(30) not null);
create table cat_lkp (cat_cd varchar2(4) not null, cat_desc varchar2(40) not null);

create table cust_mst (
  cust_no   number(8)     not null,
  cust_nm   varchar2(80)  not null,
  seg_cd    varchar2(1)   not null,
  rgn_cd    varchar2(2)   not null,
  city_nm   varchar2(40),
  join_dt   date          not null,
  cr_lmt    number(12,2),
  actv_flg  varchar2(1)
);

create table prd_mst (
  prd_id    number(6)     not null,
  prd_desc  varchar2(80)  not null,
  cat_cd    varchar2(4)   not null,
  brnd_nm   varchar2(30),
  list_prc  number(10,2)  not null,
  unit_cost number(10,2)  not null
);

create table ord_hdr (
  ord_no     number(10)   not null,
  cust_no    number(8)    not null,
  bill_to_no number(8)    not null,
  ord_dt     date         not null,
  ship_dt    date,
  sts_cd     varchar2(1)  not null,
  chnl_cd    varchar2(2)  not null
);

create table ord_ln (
  ord_no    number(10)    not null,
  ln_no     number(3)     not null,
  prd_id    number(6)     not null,
  qty       number(6)     not null,
  unit_prc  number(10,2)  not null,
  disc_pct  number(5,2)   not null,
  net_amt   number(12,2)  not null
);

create table sls_tgt (rgn_cd varchar2(2) not null, fisc_yr varchar2(6) not null, tgt_amt number(14,2) not null);

create table ar_rcpt (
  rcpt_no  number(10)    not null,
  cust_no  number(8)     not null,
  ord_no   number(10),
  rcpt_dt  date          not null,
  rcpt_amt number(12,2)  not null,
  pay_mode varchar2(4)
);

-- 3. reference data --------------------------------------------------------------------
insert into rgn_mst values ('N','North');
insert into rgn_mst values ('S','South');
insert into rgn_mst values ('E','East');
insert into rgn_mst values ('W','West');
insert into rgn_mst values ('C','Central');

insert into cat_lkp values ('ELEC','Electronics');
insert into cat_lkp values ('HOME','Home and Kitchen');
insert into cat_lkp values ('APPL','Large Appliances');
insert into cat_lkp values ('TOYS','Toys and Games');
insert into cat_lkp values ('SPRT','Sports and Fitness');

-- 4. customers: 600, unique names, 12 cities spread over the 5 regions -----------------
insert into cust_mst
with pfx as (select column_value nm, rownum - 1 i from table(sys.odcivarchar2list(
               'Sunrise','Blue Lotus','Green Valley','Silver Oak','Golden Leaf','Riverbend',
               'Maple Leaf','Harbor View','Crescent','Summit','Lakeview','Evergreen'))),
     sfx as (select column_value nm, rownum - 1 i from table(sys.odcivarchar2list(
               'Traders','Stores','Mart','Retail','Enterprises','Supplies','Outlet','Emporium'))),
     cty as (select column_value nm, rownum - 1 i from table(sys.odcivarchar2list(
               'Delhi','Jaipur','Lucknow','Chennai','Kochi','Bengaluru',
               'Kolkata','Bhubaneswar','Pune','Ahmedabad','Bhopal','Nagpur'))),
     n   as (select level - 1 i from dual connect by level <= 600)
select 10001 + n.i,
       p.nm || ' ' || s.nm || ' ' || c.nm,
       case when mod(n.i, 10) < 6 then 'R' when mod(n.i, 10) < 8 then 'W' else 'O' end,
       case when c.i < 3 then 'N' when c.i < 6 then 'S' when c.i < 8 then 'E'
            when c.i < 10 then 'W' else 'C' end,
       c.nm,
       date '2022-01-01' + mod(n.i * 37, 1550),
       case when mod(n.i, 10) between 6 and 7 then 500000 else 50000 + mod(n.i, 10) * 5000 end,
       case when mod(n.i, 25) = 0 then 'N' else 'Y' end
  from n
  join cty c on c.i = mod(n.i, 12)
  join pfx p on p.i = mod(trunc(n.i / 12), 12)
  join sfx s on s.i = mod(trunc(n.i / 144), 8);

-- 5. products: 120 across 5 categories --------------------------------------------------
insert into prd_mst
with n    as (select level - 1 i from dual connect by level <= 120),
     cat  as (select column_value cd, rownum - 1 i from table(sys.odcivarchar2list(
                'ELEC','HOME','APPL','TOYS','SPRT'))),
     brnd as (select column_value nm, rownum - 1 i from table(sys.odcivarchar2list(
                'Novatek','Zenware','Aurora','Everpeak'))),
     noun as (select column_value nm, rownum - 1 i from table(sys.odcivarchar2list(
                'Bluetooth Speaker','Smartwatch','Wireless Earbuds','Power Bank','Tablet','LED Monitor',
                'Pressure Cooker','Mixer Grinder','Non-stick Pan Set','Water Purifier','Electric Kettle','Dinner Set',
                'Refrigerator','Washing Machine','Microwave Oven','Air Conditioner','Dishwasher','Kitchen Chimney',
                'Building Blocks','Remote Control Car','Board Game','Puzzle Set','Doll House','Science Kit',
                'Yoga Mat','Treadmill','Cricket Bat','Dumbbell Set','Bicycle','Badminton Racket')))
select 501 + n.i,
       b.nm || ' ' || w.nm || ' M' || (501 + n.i),
       c.cd,
       b.nm,
       round(decode(c.cd, 'ELEC', 2000, 'HOME', 1200, 'APPL', 18000, 'TOYS', 800, 1500)
             * (1 + mod(n.i * 7, 9) / 4), 2),
       round(decode(c.cd, 'ELEC', 2000, 'HOME', 1200, 'APPL', 18000, 'TOYS', 800, 1500)
             * (1 + mod(n.i * 7, 9) / 4) * (0.52 + mod(n.i, 9) * 0.03), 2)
  from n
  join cat  c on c.i = mod(n.i, 5)
  join brnd b on b.i = trunc(trunc(n.i / 5) / 6)
  join noun w on w.i = c.i * 6 + mod(trunc(n.i / 5), 6);

-- 6. orders: 24,000 between 01-APR-2024 and 31-MAR-2026 (fiscal years 2024 and 2025) ----
--    Customers 10561..10600 never order. About 6% of orders bill a different account.
insert into ord_hdr
with n as (select level ord_no from dual connect by level <= 24000),
     d as (select ord_no,
                  date '2024-04-01' + ora_hash(ord_no, 729, 3) ord_dt,
                  10001 + ora_hash(ord_no, 559, 4)             cust_no,
                  ora_hash(ord_no, 19, 7)                      sts_h,
                  ora_hash(ord_no, 9, 8)                       chnl_h
             from n),
     s as (select d.*,
                  case when sts_h = 0 then 'X'
                       when sts_h = 1 then 'R'
                       when sts_h = 2 and ord_dt >= date '2026-03-15' then 'P'
                       else 'C' end sts_cd
             from d)
select ord_no,
       cust_no,
       case when ora_hash(ord_no, 16, 5) = 0 then 10001 + ora_hash(ord_no, 599, 6) else cust_no end,
       ord_dt,
       case when sts_cd in ('C', 'R') then ord_dt + 1 + ora_hash(ord_no, 5, 9) end,  -- shipped only
       sts_cd,
       case when chnl_h < 5 then 'ST' when chnl_h < 8 then 'WB' else 'MP' end
  from s;

-- 7. order lines: 1-4 per order, price from the product, some discounts ----------------
insert into ord_ln
with l as (select h.ord_no, x.ln_no
             from ord_hdr h,
                  lateral (select level ln_no from dual
                            connect by level <= 1 + ora_hash(h.ord_no, 3, 10)) x),
     p as (select l.ord_no, l.ln_no, 501 + ora_hash(l.ord_no * 10 + l.ln_no, 119, 11) prd_id
             from l)
select p.ord_no,
       p.ln_no,
       p.prd_id,
       q.qty,
       m.list_prc,
       q.disc,
       round(q.qty * m.list_prc * (1 - q.disc / 100), 2)
  from p
  join prd_mst m on m.prd_id = p.prd_id,
  lateral (select case when m.cat_cd = 'APPL' then 1
                       else 1 + ora_hash(p.ord_no * 10 + p.ln_no, 4, 12) end qty,
                  case ora_hash(p.ord_no * 10 + p.ln_no, 9, 13)
                       when 0 then 10 when 1 then 5 when 2 then 5 else 0 end disc
             from dual) q;

-- 8. sales targets per region and fiscal year, set around the actuals ------------------
insert into sls_tgt
with rev as (select c.rgn_cd,
                    'FY' || to_char(add_months(trunc(h.ord_dt, 'MM'), -3), 'YYYY') fisc_yr,
                    sum(l.net_amt) amt
               from ord_hdr h
               join ord_ln l   on l.ord_no = h.ord_no
               join cust_mst c on c.cust_no = h.cust_no
              where h.sts_cd = 'C'
              group by c.rgn_cd, 'FY' || to_char(add_months(trunc(h.ord_dt, 'MM'), -3), 'YYYY'))
select rgn_cd, fisc_yr,
       round(amt * case fisc_yr || rgn_cd
                     when 'FY2025N' then 0.93 when 'FY2025S' then 1.06 when 'FY2025E' then 0.97
                     when 'FY2025W' then 1.04 when 'FY2025C' then 0.99
                     when 'FY2024N' then 1.05 when 'FY2024S' then 0.95 when 'FY2024E' then 1.02
                     when 'FY2024W' then 0.97 else 1.03 end, -3)
  from rev;

-- 9. receipts: cash collected for ~90% of shipped orders (NOT revenue) -----------------
insert into ar_rcpt
select 900000 + h.ord_no, h.cust_no, h.ord_no,
       h.ship_dt + 10 + ora_hash(h.ord_no, 20, 14),
       t.amt,
       decode(ora_hash(h.ord_no, 3, 16), 0, 'UPI', 1, 'CARD', 2, 'NEFT', 'CASH')
  from ord_hdr h
  join (select ord_no, sum(net_amt) amt from ord_ln group by ord_no) t on t.ord_no = h.ord_no
 where h.sts_cd in ('C', 'R') and ora_hash(h.ord_no, 9, 15) < 9;

-- 10. decoys ---------------------------------------------------------------------------
-- A staging load of March 2026 that was loaded twice.
create table ord_hdr_stg as
  select * from ord_hdr where ord_dt >= date '2026-03-01'
  union all
  select * from ord_hdr where ord_dt >= date '2026-03-01';
create table ord_ln_stg as
  select l.* from ord_ln l join ord_hdr h on h.ord_no = l.ord_no where h.ord_dt >= date '2026-03-01'
  union all
  select l.* from ord_ln l join ord_hdr h on h.ord_no = l.ord_no where h.ord_dt >= date '2026-03-01';
-- An old customer snapshot.
create table cust_mst_bkp as select * from cust_mst where cust_no <= 10450;

-- 22 unrelated tables. Their contents do not matter; their column names do.
create table emp_mst     (emp_no number(6), emp_nm varchar2(60), dept_cd varchar2(4), desig varchar2(30), hire_dt date, sal_amt number(10,2), rgn_cd varchar2(2));
create table dept_mst    (dept_cd varchar2(4), dept_nm varchar2(40), cost_ctr varchar2(8));
create table payrl_hdr   (pay_run_id number(8), pay_prd varchar2(7), run_dt date, tot_amt number(14,2));
create table payrl_ln    (pay_run_id number(8), emp_no number(6), gross_amt number(10,2), ded_amt number(10,2), net_amt number(10,2));
create table lv_req      (req_id number(8), emp_no number(6), frm_dt date, to_dt date, lv_typ varchar2(4), sts_cd varchar2(1));
create table gl_acct     (acct_cd varchar2(8), acct_nm varchar2(60), acct_typ varchar2(4));
create table gl_jrnl_hdr (jrnl_id number(10), jrnl_dt date, src_cd varchar2(4), desc_txt varchar2(100));
create table gl_jrnl_ln  (jrnl_id number(10), ln_no number(4), acct_cd varchar2(8), dr_amt number(14,2), cr_amt number(14,2));
create table ap_vndr     (vndr_no number(6), vndr_nm varchar2(60), rgn_cd varchar2(2), pay_terms varchar2(8));
create table ap_inv      (inv_no number(10), vndr_no number(6), inv_dt date, inv_amt number(12,2), sts_cd varchar2(1));
create table po_hdr      (po_no number(10), vndr_no number(6), po_dt date, sts_cd varchar2(1), tot_amt number(14,2));
create table po_ln       (po_no number(10), ln_no number(3), prd_id number(6), qty number(6), unit_prc number(10,2));
create table wh_mst      (wh_cd varchar2(4), wh_nm varchar2(40), rgn_cd varchar2(2));
create table inv_bal     (wh_cd varchar2(4), prd_id number(6), on_hand_qty number(8), as_of_dt date);
create table stk_mov     (mov_id number(10), wh_cd varchar2(4), prd_id number(6), mov_dt date, mov_typ varchar2(3), qty number(8));
create table asset_reg   (asset_id number(8), asset_desc varchar2(80), acq_dt date, acq_cost number(12,2));
create table tkt_hdr     (tkt_no number(10), cust_no number(8), open_dt date, cls_dt date, sts_cd varchar2(1), prio_cd varchar2(1));
create table tkt_log     (tkt_no number(10), log_ts timestamp, note_txt varchar2(200));
create table job_run_log (run_id number(10), job_nm varchar2(40), start_ts timestamp, end_ts timestamp, sts_cd varchar2(1));
create table aud_log     (aud_id number(12), usr_id varchar2(20), act_cd varchar2(8), act_ts timestamp, obj_nm varchar2(60));
create table usr_mst     (usr_id varchar2(20), usr_nm varchar2(60), role_cd varchar2(8), actv_flg varchar2(1));
create table curr_rate   (curr_cd varchar2(3), rate_dt date, rate_to_inr number(12,4));
create table promo_mst   (promo_cd varchar2(10), promo_desc varchar2(80), disc_pct number(5,2), frm_dt date, to_dt date);

insert into emp_mst     select level, 'Employee ' || level, 'D' || mod(level, 6), 'Associate', date '2020-01-01' + level * 11, 30000 + level * 250, decode(mod(level, 5), 0, 'N', 1, 'S', 2, 'E', 3, 'W', 'C') from dual connect by level <= 80;
insert into dept_mst    select 'D' || (level - 1), 'Department ' || level, 'CC' || level from dual connect by level <= 6;
insert into payrl_hdr   select level, to_char(add_months(date '2024-04-01', level - 1), 'YYYY-MM'), add_months(date '2024-04-28', level - 1), 2400000 + level * 1000 from dual connect by level <= 24;
insert into payrl_ln    select 1 + mod(level, 24), 1 + mod(level, 80), 40000, 4000, 36000 from dual connect by level <= 400;
insert into lv_req      select level, 1 + mod(level, 80), date '2025-01-01' + level, date '2025-01-03' + level, 'CL', decode(mod(level, 3), 0, 'C', 1, 'P', 'X') from dual connect by level <= 60;
insert into gl_acct     select 'A' || level, 'Ledger account ' || level, decode(mod(level, 4), 0, 'REV', 1, 'EXP', 2, 'AST', 'LIA') from dual connect by level <= 40;
insert into gl_jrnl_hdr select level, date '2025-04-01' + level, 'SLS', 'Journal ' || level from dual connect by level <= 60;
insert into gl_jrnl_ln  select 1 + mod(level, 60), level, 'A' || (1 + mod(level, 40)), 1000 * mod(level, 7), 1000 * mod(level, 5) from dual connect by level <= 240;
insert into ap_vndr     select level, 'Vendor ' || level, decode(mod(level, 5), 0, 'N', 1, 'S', 2, 'E', 3, 'W', 'C'), 'NET30' from dual connect by level <= 30;
insert into ap_inv      select level, 1 + mod(level, 30), date '2025-04-01' + level, 25000 + level * 90, decode(mod(level, 3), 0, 'C', 1, 'P', 'X') from dual connect by level <= 150;
insert into po_hdr      select level, 1 + mod(level, 30), date '2025-04-01' + level, decode(mod(level, 3), 0, 'C', 1, 'P', 'X'), 80000 + level * 100 from dual connect by level <= 120;
insert into po_ln       select 1 + mod(level, 120), level, 501 + mod(level, 120), 10 + mod(level, 40), 500 + level from dual connect by level <= 480;
insert into wh_mst      select 'W' || level, 'Warehouse ' || level, decode(mod(level, 5), 0, 'N', 1, 'S', 2, 'E', 3, 'W', 'C') from dual connect by level <= 8;
insert into inv_bal     select 'W' || (1 + mod(level, 8)), 501 + mod(level, 120), 20 + mod(level * 7, 300), date '2026-03-31' from dual connect by level <= 480;
insert into stk_mov     select level, 'W' || (1 + mod(level, 8)), 501 + mod(level, 120), date '2025-04-01' + mod(level, 365), decode(mod(level, 2), 0, 'IN', 'OUT'), 1 + mod(level, 25) from dual connect by level <= 900;
insert into asset_reg   select level, 'Asset ' || level, date '2019-01-01' + level * 20, 150000 + level * 1000 from dual connect by level <= 40;
insert into tkt_hdr     select level, 10001 + mod(level * 7, 600), date '2025-04-01' + mod(level, 360), date '2025-04-03' + mod(level, 360), decode(mod(level, 3), 0, 'C', 1, 'P', 'X'), decode(mod(level, 3), 0, 'H', 1, 'M', 'L') from dual connect by level <= 300;
insert into tkt_log     select 1 + mod(level, 300), timestamp '2025-04-01 10:00:00' + numtodsinterval(level, 'HOUR'), 'Customer contacted' from dual connect by level <= 600;
insert into job_run_log select level, 'NIGHTLY_LOAD', timestamp '2025-04-01 01:00:00' + numtodsinterval(level, 'DAY'), timestamp '2025-04-01 01:20:00' + numtodsinterval(level, 'DAY'), decode(mod(level, 10), 0, 'X', 'C') from dual connect by level <= 365;
insert into aud_log     select level, 'USR' || mod(level, 12), decode(mod(level, 3), 0, 'LOGIN', 1, 'UPDATE', 'EXPORT'), timestamp '2025-04-01 09:00:00' + numtodsinterval(level * 13, 'MINUTE'), 'ORD_HDR' from dual connect by level <= 500;
insert into usr_mst     select 'USR' || (level - 1), 'App user ' || level, decode(mod(level, 3), 0, 'ADMIN', 'CLERK'), 'Y' from dual connect by level <= 12;
insert into curr_rate   select decode(mod(level, 3), 0, 'USD', 1, 'EUR', 'AED'), date '2025-04-01' + trunc((level - 1) / 3), decode(mod(level, 3), 0, 83.2, 1, 90.1, 22.6) from dual connect by level <= 300;
insert into promo_mst   select 'PROMO' || level, 'Seasonal promotion ' || level, 5 * (1 + mod(level, 4)), date '2024-04-01' + level * 30, date '2024-04-15' + level * 30 from dual connect by level <= 20;

commit;

-- 11. what was built -------------------------------------------------------------------
begin
  for r in (select table_name, num_rows from (
              select table_name, to_number(extractvalue(xmltype(dbms_xmlgen.getxml(
                       'select count(*) c from ' || table_name)), '/ROWSET/ROW/C')) num_rows
                from user_tables where table_name in ('RGN_MST','CAT_LKP','CUST_MST','PRD_MST',
                      'ORD_HDR','ORD_LN','SLS_TGT','AR_RCPT','ORD_HDR_STG','ORD_LN_STG','CUST_MST_BKP'))
            order by table_name) loop
    dbms_output.put_line(rpad(r.table_name, 14) || lpad(r.num_rows, 8));
  end loop;
end;
/
select count(*) as tables_in_schema from user_tables;
