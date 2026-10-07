-- v1.0 - SHOP_APP in oradb1 / FREEPDB1: the five driver transactions of docs/contract.md section 2, each run
--        once with SQL*Plus timing, for the blog transcript (brief 10 transcripts/01-shop-transactions.txt).
--        Also shows that SHOP_APP cannot change ORDERS directly (26ai reports ORA-41900, missing UPDATE
--        privilege). Everything is rolled back: the driver commits after place_order and pay, this script
--        does not, so it leaves no rows behind.
--        Run as SHOP_APP, the password on stdin (never on a command line):
--          { printf 'connect SHOP_APP/"%s"@//localhost:1521/FREEPDB1\n' "$P"; echo "@/tmp/app900/anom_target_app_tx.sql"; } \
--            | docker exec -i oradb1 sqlplus -s -L /nolog
--        v1.0: first version, 01-Oct-2026.
set verify off
set define off
set feedback on
set linesize 400 pagesize 60 tab off trimout on
set serveroutput on size unlimited format wrapped
whenever sqlerror exit failure rollback
whenever oserror exit failure
column name format a32
column pdb format a10
column utc format a20
column session_user format a12

prompt == app 900: the five transactions the load driver sends, as SHOP_APP, each run once
select sys_context('USERENV','SESSION_USER') session_user, sys_context('USERENV','CON_NAME') pdb,
       to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"') utc
  from dual;

variable cat    number
variable pat    varchar2(40)
variable cust   number
variable items  varchar2(200)
variable oid    number
variable amt    number
variable method varchar2(20)
begin
  :cat := 7; :pat := '%PRO%'; :cust := 4242; :items := '7:1,1234:2'; :method := 'CARD';
end;
/
prompt binds: cat=7  pat='%PRO%'  cust=4242  items='7:1,1234:2' (product 7 is a hot item)  method='CARD'

-- sqlplus -s does not echo commands, so each statement is shown with PROMPT before it runs
set timing on
prompt
prompt -- 1. browse (45%)
prompt SQL> select p.product_id, p.name, p.price, i.qty_on_hand from SHOP.PRODUCTS p join SHOP.INVENTORY i on i.product_id = p.product_id where p.category_id = :cat and p.active = 'Y' order by p.popularity desc fetch first 20 rows only;
select p.product_id, p.name, p.price, i.qty_on_hand from SHOP.PRODUCTS p join SHOP.INVENTORY i on i.product_id = p.product_id where p.category_id = :cat and p.active = 'Y' order by p.popularity desc fetch first 20 rows only;
prompt -- 2. search (10%)
prompt SQL> select product_id, name, price from SHOP.PRODUCTS where category_id = :cat and upper(name) like :pat fetch first 20 rows only;
select product_id, name, price from SHOP.PRODUCTS where category_id = :cat and upper(name) like :pat fetch first 20 rows only;
prompt -- 3. place_order (20%); the driver commits after it, this script does not
prompt SQL> begin SHOP.PKG_SHOP.place_order(:cust, :items, :oid); end;
begin SHOP.PKG_SHOP.place_order(:cust, :items, :oid); end;
/
set timing off
print oid
begin select total into :amt from SHOP.ORDERS where order_id = :oid; end;
/
print amt
set timing on
prompt -- 4. pay (15%); the driver commits after it, this script does not
prompt SQL> begin SHOP.PKG_SHOP.pay(:oid, :amt, :method); end;
begin SHOP.PKG_SHOP.pay(:oid, :amt, :method); end;
/
prompt -- 5. order_status (10%)
prompt SQL> select o.order_id, o.status, o.total, count(l.line_no) from SHOP.ORDERS o join SHOP.ORDER_LINES l on l.order_id = o.order_id where o.customer_id = :cust group by o.order_id, o.status, o.total, o.order_ts order by o.order_ts desc fetch first 5 rows only;
select o.order_id, o.status, o.total, count(l.line_no) from SHOP.ORDERS o join SHOP.ORDER_LINES l on l.order_id = o.order_id where o.customer_id = :cust group by o.order_id, o.status, o.total, o.order_ts order by o.order_ts desc fetch first 5 rows only;
set timing off
prompt -- least privilege: SHOP_APP reads ORDERS but can change it only through PKG_SHOP
prompt SQL> update SHOP.ORDERS set status = 'SETTLED' where order_id = :oid;
whenever sqlerror continue none
update SHOP.ORDERS set status = 'SETTLED' where order_id = :oid;
whenever sqlerror exit failure rollback
prompt SQL> rollback;
rollback;
select count(*) orders_left_behind from SHOP.ORDERS where order_id = :oid;
prompt == done: everything above was rolled back
