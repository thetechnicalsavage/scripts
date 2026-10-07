-- v1.0 - SHOP in oradb1 / FREEPDB1 (the target, the demo's "production" database) for application 900:
--        the shop's tables and indexes, package PKG_SHOP, the grants to SHOP_APP, the single DRIVER_CONTROL
--        row and the nightly SHOP_SETTLEMENT job (docs/contract.md section 1).
--        Run as SHOP in FREEPDB1, after 86, the password on stdin (never on a command line):
--          { printf 'connect SHOP/"%s"@//localhost:1521/FREEPDB1\n' "$P"; echo "@/tmp/app900/87_anom_shop_schema.sql"; } \
--            | docker exec -i oradb1 sqlplus -s -L /nolog
--        v1.0: first version, 01-Oct-2026.
--
--        Idempotent. Tables and indexes are created only when absent (CREATE ... IF NOT EXISTS, 23ai); the
--        package is replaced; grants are re-stated; the DRIVER_CONTROL row is inserted only when absent, so a
--        re-run keeps whatever PKG_CHAOS or an operator set; the job is created when absent and put back on its
--        01:00 UTC calendar when it drifted (its enabled state is left as found). Nothing is dropped, truncated
--        or deleted. The last block checks the result and fails the run when anything is missing or invalid.
--
--        Choices the contract leaves open:
--          - Timestamps are TIMESTAMP columns defaulting to SYS_EXTRACT_UTC(SYSTIMESTAMP): UTC whatever the
--            server's or the session's time zone (the container's OS clock is UTC as well).
--          - ORDERS.order_id and PAYMENTS.payment_id are identity columns starting at 1,000,001. The seed (88)
--            owns ids 1 to 1,000,000 for history, so its rows and the driver's can never collide.
--          - PROMO_RULES has no index and no key at all, on purpose: every place_order scans it in full, and
--            the slow_drift scenario (phase 4) grows it.
--          - CLICK_ARCHIVE has a primary key (it lets 88 resume its batches without scanning 1.2 GB); the
--            io_storm scenario reads it with full scans on non-key columns.
--          - DRIVER_CONTROL carries check constraints that bound what the driver can be told to do.
--          - The job's start date is a TIMESTAMP WITH TIME ZONE in region UTC. The scheduler evaluates the
--            calendar in the start date's zone, so its default zone (PST8PDT in this database) cannot move it.
set serveroutput on size unlimited format wrapped
set verify off
set define off
set feedback off
set sqlblanklines on
set linesize 200 tab off trimout on
whenever sqlerror exit failure rollback
whenever oserror exit failure

prompt 87: checking where this runs
begin
  if sys_context('USERENV','CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '87: run in the PDB (FREEPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV','SESSION_USER') != 'SHOP' then
    raise_application_error(-20900, '87: run as SHOP, not '||sys_context('USERENV','SESSION_USER'));
  end if;
end;
/

-- ------------------------------------------------------------------ tables and indexes
prompt 87: tables and indexes (created only when absent)

create table if not exists CATEGORIES (
  category_id   number         not null,
  name          varchar2(60)   not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint categories_pk primary key (category_id)
);

create table if not exists PRODUCTS (
  product_id    number         not null,
  category_id   number         not null,
  name          varchar2(80)   not null,
  price         number(10,2)   not null,
  popularity    number         not null,
  active        char(1)        default 'Y' not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint products_pk        primary key (product_id),
  constraint products_cat_fk    foreign key (category_id) references CATEGORIES (category_id),
  constraint products_active_ck check (active in ('Y','N')),
  constraint products_price_ck  check (price >= 0)
);
-- browse and search: one category, by popularity (also covers the foreign key)
create index if not exists PROD_CAT_IX on PRODUCTS (category_id, popularity);

create table if not exists INVENTORY (
  product_id    number         not null,
  qty_on_hand   number         not null,
  updated_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint inventory_pk      primary key (product_id),
  constraint inventory_prod_fk foreign key (product_id) references PRODUCTS (product_id),
  constraint inventory_qty_ck  check (qty_on_hand >= 0)
);

create table if not exists CUSTOMERS (
  customer_id   number         not null,
  name          varchar2(80)   not null,
  email         varchar2(120)  not null,
  country       varchar2(2)    not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint customers_pk primary key (customer_id)
);

create table if not exists ORDERS (
  order_id      number generated by default as identity (start with 1000001 cache 1000) not null,
  customer_id   number         not null,
  order_ts      timestamp      default sys_extract_utc(systimestamp) not null,
  status        varchar2(10)   default 'NEW' not null,
  total         number(12,2)   not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint orders_pk        primary key (order_id),
  constraint orders_status_ck check (status in ('NEW','PAID','SETTLED','FAILED'))
);
-- order_status: a customer's latest orders (scenario S2 makes this index invisible, then restores it)
create index if not exists ORD_CUST_IX on ORDERS (customer_id, order_ts);

create table if not exists ORDER_LINES (
  order_id      number         not null,
  line_no       number(3)      not null,
  product_id    number         not null,
  qty           number(5)      not null,
  price         number(10,2)   not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint order_lines_pk     primary key (order_id, line_no),
  constraint order_lines_qty_ck check (qty > 0)
);

create table if not exists PAYMENTS (
  payment_id    number generated by default as identity (start with 1000001 cache 1000) not null,
  order_id      number         not null,
  amount        number(12,2)   not null,
  method        varchar2(20)   not null,
  paid_ts       timestamp      default sys_extract_utc(systimestamp) not null,
  settled       char(1)        default 'N' not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint payments_pk         primary key (payment_id),
  constraint payments_settled_ck check (settled in ('Y','N'))
);

-- NO index, NO primary key, NO unique constraint on PROMO_RULES: on purpose (see the header)
create table if not exists PROMO_RULES (
  rule_id       number         not null,
  category_id   number         not null,
  min_total     number(12,2)   not null,
  discount_pct  number(5,2)    not null,
  valid_to      date           not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null
);

-- wide, insert-only history, read only by the io_storm scenario: no free space kept in its blocks
create table if not exists CLICK_ARCHIVE (
  click_id      number         not null,
  click_ts      timestamp      not null,
  customer_id   number         not null,
  product_id    number         not null,
  session_id    varchar2(32)   not null,
  event_type    varchar2(16)   not null,
  page_url      varchar2(200)  not null,
  referrer      varchar2(200),
  user_agent    varchar2(200)  not null,
  detail        varchar2(600)  not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint click_archive_pk primary key (click_id)
) pctfree 0;

-- the driver reads this every 10 s; PKG_CHAOS (phase 4) writes it
create table if not exists DRIVER_CONTROL (
  id                number     default 1 not null,
  enabled           char(1)    default 'Y' not null,
  load_pct          number     default 100 not null,
  conn_leak_target  number     default 0 not null,
  logon_storm       char(1)    default 'N' not null,
  error_pct         number     default 0 not null,
  updated_ts        timestamp  default sys_extract_utc(systimestamp) not null,
  created_ts        timestamp  default sys_extract_utc(systimestamp) not null,
  constraint driver_control_pk      primary key (id),
  constraint driver_control_one_ck  check (id = 1),
  constraint driver_control_en_ck   check (enabled in ('Y','N')),
  constraint driver_control_ls_ck   check (logon_storm in ('Y','N')),
  constraint driver_control_load_ck check (load_pct between 0 and 300),
  constraint driver_control_leak_ck check (conn_leak_target between 0 and 40),
  constraint driver_control_err_ck  check (error_pct between 0 and 100)
);

create table if not exists DAILY_SETTLEMENT (
  settle_date   date           not null,
  n_payments    number         not null,
  amount        number(14,2)   not null,
  created_ts    timestamp      default sys_extract_utc(systimestamp) not null,
  constraint daily_settlement_pk primary key (settle_date)
);

-- ------------------------------------------------------------------ PKG_SHOP
prompt 87: package PKG_SHOP
create or replace package PKG_SHOP authid definer as
  -- v1.0 - the demo shop's three business calls (docs/contract.md section 1). Definer rights: SHOP_APP
  --        holds EXECUTE on this package and no DML on the tables.
  --        Errors raised:
  --          -20001  unknown product, or not enough stock (the contract's code; error_pct relies on it)
  --          -20002  malformed request: customer id, items string, amount, method or day out of shape
  --          -20003  the order cannot be paid: it does not exist or is not NEW
  --        place_order and pay never commit (the caller does) and are all-or-nothing: on any error they roll
  --        back to their own savepoint, so a failed call leaves nothing behind whatever the caller does next.

  -- p_items: "pid:qty,pid:qty" (1 to 20 distinct products; a repeated product adds up). Looks up PROMO_RULES
  -- once (full scan, by the order's categories and gross total), inserts ORDERS and ORDER_LINES, and
  -- decrements INVENTORY row by row in product-id order (so concurrent orders cannot deadlock).
  procedure place_order (p_customer_id number, p_items varchar2, p_order_id out number);

  -- Inserts PAYMENTS (settled 'N') and sets ORDERS.status = 'PAID'.
  procedure pay (p_order_id number, p_amount number, p_method varchar2);

  -- The nightly batch: marks the day's payments settled in one update, merges DAILY_SETTLEMENT with the
  -- day's settled count and amount, runs one report aggregate over the day's ORDER_LINES. Commits.
  -- The day is a UTC calendar day (the database server's clock is UTC).
  procedure settle (p_day date default trunc(sysdate - 1));
end PKG_SHOP;
/

create or replace package body PKG_SHOP as
  -- v1.0 - see the specification.

  c_max_products constant pls_integer := 20;    -- distinct products per order
  c_max_qty      constant pls_integer := 999;   -- per item token

  type t_num_tab is table of number index by pls_integer;

  function utc_now return timestamp is
  begin
    return sys_extract_utc(systimestamp);
  end utc_now;

  procedure place_order (p_customer_id number, p_items varchar2, p_order_id out number) is
    -- product_id -> quantity; an index-by table iterates in key order, which fixes the lock order
    type t_qty_tab is table of pls_integer index by pls_integer;
    l_qty      t_qty_tab;
    l_items    varchar2(1100);
    l_tok      varchar2(1100);
    l_pos      pls_integer := 1;
    l_comma    pls_integer;
    l_colon    pls_integer;
    l_pid      number;
    l_q        number;
    l_price    number;
    l_cat      number;
    l_cats     varchar2(4000) := ',';   -- the order's categories as ",c1,c2,": one scan of PROMO_RULES
    l_gross    number := 0;
    l_disc     number;
    l_total    number;
    l_n        pls_integer := 0;
    l_line_no  t_num_tab;
    l_line_pid t_num_tab;
    l_line_qty t_num_tab;
    l_line_prc t_num_tab;
    l_now      timestamp := utc_now;
  begin
    savepoint shop_place_order;
    p_order_id := null;

    if p_customer_id is null or p_customer_id <= 0 or p_customer_id != trunc(p_customer_id) then
      raise_application_error(-20002, 'place_order: customer id must be a positive integer');
    end if;
    if p_items is null or length(p_items) > 1000 then
      raise_application_error(-20002, 'place_order: items are missing or longer than 1000 characters');
    end if;

    -- parse "pid:qty,pid:qty"; blanks are ignored, an empty or malformed token is refused
    l_items := replace(p_items, ' ') || ',';
    loop
      l_comma := instr(l_items, ',', l_pos);
      exit when l_comma = 0;
      l_tok := substr(l_items, l_pos, l_comma - l_pos);
      l_pos := l_comma + 1;
      if l_tok is null or not regexp_like(l_tok, '^[0-9]{1,9}:[0-9]{1,3}$') then
        raise_application_error(-20002, 'place_order: item "'||substr(l_tok, 1, 30)||'" is not pid:qty');
      end if;
      l_colon := instr(l_tok, ':');
      l_pid   := to_number(substr(l_tok, 1, l_colon - 1));
      l_q     := to_number(substr(l_tok, l_colon + 1));
      if l_q < 1 or l_q > c_max_qty then
        raise_application_error(-20002, 'place_order: quantity for product '||l_pid||' must be 1 to '||c_max_qty);
      end if;
      if l_qty.exists(l_pid) then
        l_qty(l_pid) := l_qty(l_pid) + l_q;
      else
        l_qty(l_pid) := l_q;
      end if;
    end loop;
    if l_qty.count = 0 or l_qty.count > c_max_products then
      raise_application_error(-20002, 'place_order: an order has 1 to '||c_max_products||' distinct products');
    end if;

    -- price, category and stock, one product at a time, in product-id order
    l_pid := l_qty.first;
    while l_pid is not null loop
      begin
        select price, category_id into l_price, l_cat from PRODUCTS where product_id = l_pid;
      exception
        when no_data_found then
          raise_application_error(-20001, 'place_order: unknown product '||l_pid);
      end;
      update INVENTORY
         set qty_on_hand = qty_on_hand - l_qty(l_pid),
             updated_ts  = l_now
       where product_id = l_pid
         and qty_on_hand >= l_qty(l_pid);
      if sql%rowcount = 0 then
        raise_application_error(-20001, 'place_order: not enough stock for product '||l_pid);
      end if;
      l_n := l_n + 1;
      l_line_no(l_n)  := l_n;
      l_line_pid(l_n) := l_pid;
      l_line_qty(l_n) := l_qty(l_pid);
      l_line_prc(l_n) := l_price;
      l_gross := l_gross + l_price * l_qty(l_pid);
      if instr(l_cats, ','||to_char(l_cat)||',') = 0 then
        l_cats := l_cats || to_char(l_cat) || ',';
      end if;
      l_pid := l_qty.next(l_pid);
    end loop;

    -- the best live promotion for any of the order's categories: one full scan of PROMO_RULES, by design
    select nvl(max(discount_pct), 0)
      into l_disc
      from PROMO_RULES
     where instr(l_cats, ','||to_char(category_id)||',') > 0
       and min_total <= l_gross
       and valid_to >= l_now;
    l_total := round(l_gross * (100 - l_disc) / 100, 2);

    insert into ORDERS (customer_id, order_ts, status, total)
    values (p_customer_id, l_now, 'NEW', l_total)
    returning order_id into p_order_id;

    forall i in 1 .. l_n
      insert into ORDER_LINES (order_id, line_no, product_id, qty, price)
      values (p_order_id, l_line_no(i), l_line_pid(i), l_line_qty(i), l_line_prc(i));
  exception
    when others then
      -- (an OUT parameter is not copied back when the call raises: the caller's variable keeps its value)
      rollback to savepoint shop_place_order;
      raise;
  end place_order;

  procedure pay (p_order_id number, p_amount number, p_method varchar2) is
    l_now timestamp := utc_now;   -- a private function cannot be called inside SQL
  begin
    savepoint shop_pay;
    if p_order_id is null or p_amount is null or p_amount <= 0
       or p_method is null or length(p_method) > 20 then
      raise_application_error(-20002, 'pay: an order id, a positive amount and a method (up to 20 characters) are required');
    end if;
    update ORDERS set status = 'PAID' where order_id = p_order_id and status = 'NEW';
    if sql%rowcount = 0 then
      raise_application_error(-20003, 'pay: order '||p_order_id||' does not exist or is not NEW');
    end if;
    insert into PAYMENTS (order_id, amount, method, paid_ts, settled)
    values (p_order_id, p_amount, p_method, l_now, 'N');
  exception
    when others then
      rollback to savepoint shop_pay;
      raise;
  end pay;

  procedure settle (p_day date default trunc(sysdate - 1)) is
    l_day      date;
    l_from     timestamp;
    l_to       timestamp;
    l_settled  pls_integer;
    l_orders   number;
    l_lines    number;
    l_revenue  number;
    l_module   varchar2(64);
    l_action   varchar2(64);
    l_t0       pls_integer := dbms_utility.get_time;
  begin
    -- first, so that the handler's rollback to it is valid whatever fails next (a test caught ORA-01086)
    savepoint shop_settle;
    dbms_application_info.read_module(l_module, l_action);
    if p_day is null then
      raise_application_error(-20002, 'settle: the day is required');
    end if;
    l_day  := trunc(p_day);
    l_from := cast(l_day as timestamp);
    l_to   := l_from + interval '1' day;
    dbms_application_info.set_module('SHOP_SETTLEMENT', 'settle '||to_char(l_day, 'YYYY-MM-DD'));

    -- 1. the day's payments, in one update
    update PAYMENTS
       set settled = 'Y'
     where paid_ts >= l_from and paid_ts < l_to
       and settled = 'N';
    l_settled := sql%rowcount;

    -- 2. the day's totals (re-running a day recomputes the same row)
    merge into DAILY_SETTLEMENT d
    using (select l_day settle_date, count(*) n_payments, nvl(sum(amount), 0) amount
             from PAYMENTS
            where paid_ts >= l_from and paid_ts < l_to
              and settled = 'Y') s
       on (d.settle_date = s.settle_date)
     when matched then update set d.n_payments = s.n_payments, d.amount = s.amount
     when not matched then insert (settle_date, n_payments, amount)
                           values (s.settle_date, s.n_payments, s.amount);

    -- 3. the report: the day's order lines
    select count(distinct o.order_id), count(l.line_no), nvl(sum(l.qty * l.price), 0)
      into l_orders, l_lines, l_revenue
      from ORDERS o
      join ORDER_LINES l on l.order_id = o.order_id
     where o.order_ts >= l_from and o.order_ts < l_to;

    commit;
    dbms_output.put_line('settle '||to_char(l_day, 'YYYY-MM-DD')||': payments_settled='||l_settled
      ||' orders='||l_orders||' lines='||l_lines||' revenue='||l_revenue
      ||' elapsed_s='||to_char((dbms_utility.get_time - l_t0) / 100, 'fm9999990.00'));
    dbms_application_info.set_module(l_module, l_action);
  exception
    when others then
      rollback to savepoint shop_settle;
      dbms_application_info.set_module(l_module, l_action);
      raise;
  end settle;
end PKG_SHOP;
/

-- CREATE OR REPLACE with compilation errors is a warning, not an SQL error: check and fail loudly
declare
  l_bad varchar2(4000);
begin
  for e in (select name, type, line, position, text
              from user_errors
             where name = 'PKG_SHOP'
             order by type, sequence) loop
    l_bad := substr(l_bad||chr(10)||'  '||e.type||' line '||e.line||': '||e.text, 1, 3900);
  end loop;
  if l_bad is not null then
    raise_application_error(-20900, '87: PKG_SHOP did not compile:'||l_bad);
  end if;
end;
/

-- ------------------------------------------------------------------ grants (contract: these and no more)
prompt 87: grants to SHOP_APP
grant select on CATEGORIES     to SHOP_APP;
grant select on PRODUCTS       to SHOP_APP;
grant select on INVENTORY      to SHOP_APP;
grant select on ORDERS         to SHOP_APP;
grant select on ORDER_LINES    to SHOP_APP;
grant select on DRIVER_CONTROL to SHOP_APP;
grant execute on PKG_SHOP      to SHOP_APP;

-- ------------------------------------------------------------------ DRIVER_CONTROL: the single row
prompt 87: DRIVER_CONTROL row (inserted only when absent)
declare
  n number;
begin
  insert into DRIVER_CONTROL (id)
  select 1 from dual where not exists (select 1 from DRIVER_CONTROL where id = 1);
  n := sql%rowcount;
  commit;
  dbms_output.put_line('  DRIVER_CONTROL: '||case n when 1 then 'row inserted with the defaults'
                                                      else 'row already present; values kept' end);
end;
/

-- ------------------------------------------------------------------ the nightly settlement job
prompt 87: job SHOP_SETTLEMENT (daily at 01:00 UTC)
declare
  c_job     constant varchar2(30)  := 'SHOP_SETTLEMENT';
  c_repeat  constant varchar2(100) := 'FREQ=DAILY;BYHOUR=1;BYMINUTE=0;BYSECOND=0';
  c_action  constant varchar2(100) := 'begin SHOP.PKG_SHOP.settle; end;';
  l_start   timestamp with time zone;
  l_cur_start  timestamp with time zone;
  l_cur_repeat varchar2(4000);
  l_cur_action varchar2(4000);
  l_enabled    varchar2(5);
  n            number;
begin
  -- the next 01:00 UTC strictly in the future, carrying the region UTC (not an offset, not the DB default)
  l_start := from_tz(cast(trunc(sys_extract_utc(systimestamp)) as timestamp) + interval '1' hour, 'UTC');
  if l_start <= systimestamp then
    l_start := l_start + interval '1' day;
  end if;

  select count(*) into n from user_scheduler_jobs where job_name = c_job;
  if n = 0 then
    dbms_scheduler.create_job(
      job_name        => c_job,
      job_type        => 'PLSQL_BLOCK',
      job_action      => c_action,
      start_date      => l_start,
      repeat_interval => c_repeat,
      auto_drop       => false,
      enabled         => true,
      comments        => 'app 900: nightly settlement of the previous UTC day, 01:00 UTC');
    dbms_output.put_line('  '||c_job||' created, first run '||to_char(l_start, 'YYYY-MM-DD HH24:MI:SS TZR'));
  else
    select start_date, repeat_interval, job_action, enabled
      into l_cur_start, l_cur_repeat, l_cur_action, l_enabled
      from user_scheduler_jobs where job_name = c_job;
    if nvl(l_cur_repeat, '-') != c_repeat or nvl(l_cur_action, '-') != c_action
       or nvl(to_char(l_cur_start, 'TZR'), '-') != 'UTC' then
      dbms_scheduler.set_attribute(c_job, 'job_action', c_action);
      dbms_scheduler.set_attribute(c_job, 'repeat_interval', c_repeat);
      dbms_scheduler.set_attribute(c_job, 'start_date', l_start);
      dbms_output.put_line('  '||c_job||' existed with a different calendar or action: put back on 01:00 UTC');
    else
      dbms_output.put_line('  '||c_job||' already present and on 01:00 UTC; enabled='||l_enabled);
    end if;
  end if;
end;
/

-- ------------------------------------------------------------------ verification: fail when anything is off
prompt 87: verification
declare
  n      number;
  l_cols varchar2(200);
  procedure need (p_ok boolean, p_what varchar2) is
  begin
    if not p_ok then
      raise_application_error(-20900, '87: verification failed: '||p_what);
    end if;
  end;
begin
  select count(*) into n from user_tables
   where table_name in ('CATEGORIES','PRODUCTS','INVENTORY','CUSTOMERS','ORDERS','ORDER_LINES','PAYMENTS',
                        'PROMO_RULES','CLICK_ARCHIVE','DRIVER_CONTROL','DAILY_SETTLEMENT');
  need(n = 11, 'expected 11 tables, found '||n);

  select count(*) into n from user_indexes where table_name = 'PROMO_RULES';
  need(n = 0, 'PROMO_RULES must have no index, found '||n);

  select listagg(column_name, ',') within group (order by column_position) into l_cols
    from user_ind_columns where index_name = 'ORD_CUST_IX' and table_name = 'ORDERS';
  need(l_cols = 'CUSTOMER_ID,ORDER_TS', 'ORD_CUST_IX columns are "'||l_cols||'"');
  select listagg(column_name, ',') within group (order by column_position) into l_cols
    from user_ind_columns where index_name = 'PROD_CAT_IX' and table_name = 'PRODUCTS';
  need(l_cols = 'CATEGORY_ID,POPULARITY', 'PROD_CAT_IX columns are "'||l_cols||'"');

  select count(*) into n from user_objects
   where object_name = 'PKG_SHOP' and object_type in ('PACKAGE','PACKAGE BODY') and status = 'VALID';
  need(n = 2, 'PKG_SHOP spec and body are not both VALID');

  select count(*) into n from DRIVER_CONTROL;
  need(n = 1, 'DRIVER_CONTROL has '||n||' rows');

  select count(*) into n from user_tab_privs_made
   where grantee = 'SHOP_APP'
     and ((privilege = 'SELECT' and table_name in ('CATEGORIES','PRODUCTS','INVENTORY','ORDERS','ORDER_LINES','DRIVER_CONTROL'))
       or (privilege = 'EXECUTE' and table_name = 'PKG_SHOP'));
  need(n = 7, 'SHOP_APP holds '||n||' of the 7 contract grants');

  select count(*) into n from user_scheduler_jobs where job_name = 'SHOP_SETTLEMENT';
  need(n = 1, 'job SHOP_SETTLEMENT is missing');

  for r in (select table_name, num_rows from user_tables
             where table_name in ('CATEGORIES','PRODUCTS','INVENTORY','CUSTOMERS','ORDERS','ORDER_LINES','PAYMENTS',
                                  'PROMO_RULES','CLICK_ARCHIVE','DRIVER_CONTROL','DAILY_SETTLEMENT')
             order by table_name) loop
    dbms_output.put_line('  '||rpad(r.table_name, 18)||' present');
  end loop;
  for j in (select job_name, enabled, state, repeat_interval,
                   to_char(next_run_date, 'YYYY-MM-DD HH24:MI:SS TZR') next_run
              from user_scheduler_jobs where job_name = 'SHOP_SETTLEMENT') loop
    dbms_output.put_line('  job '||j.job_name||': enabled='||j.enabled||' state='||j.state
                         ||' next_run='||j.next_run||' calendar='||j.repeat_interval);
  end loop;
  dbms_output.put_line('87: done - schema verified at '||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
/
