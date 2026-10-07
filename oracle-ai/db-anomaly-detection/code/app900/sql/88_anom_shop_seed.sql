-- v1.0 - SHOP in oradb1 / FREEPDB1 (the target) for application 900: deterministic seed data for the shop
--        (docs/contract.md section 1 row counts). Run as SHOP in FREEPDB1, after 87, password on stdin:
--          { printf 'connect SHOP/"%s"@//localhost:1521/FREEPDB1\n' "$P"; echo "@/tmp/app900/88_anom_shop_seed.sql"; } \
--            | docker exec -i oradb1 sqlplus -s -L /nolog
--        v1.0: first version, 01-Oct-2026.
--
--        What it makes (every value is a function of the row's key through ORA_HASH with a fixed salt, so the
--        same key always gets the same data, whatever the plan or the order of execution):
--          CATEGORIES 50 / PRODUCTS 20,000 (400 a category; product N is in category mod(N-1,50)+1; products
--          1-50 are the hot items, one per category, most popular) / INVENTORY 20,000 (hot 2,000,000 units,
--          others 200,000-299,999: the driver cannot run them out) / CUSTOMERS 200,000 / PROMO_RULES 300
--          (6 tiers a category; 1 in 7 expired) / ORDERS 1,000,000 history orders, ids 1-1,000,000, over the
--          60 UTC days before the anchor day, spread over the day by the driver's UTC load shape; 1-5 lines each
--          (mean 3); 90% PAID with a payment (payment_id = order_id), 6% NEW, 4% FAILED; payments before the
--          anchor day are settled and DAILY_SETTLEMENT holds those days / CLICK_ARCHIVE 1,600,000 rows,
--          about 1.25 GB, larger than the 512 MB buffer cache.
--        The anchor day is today (UTC) at the first run; a later run reads it back from order 1, so the dates
--        never move.
--
--        Re-runnable. Every step inserts only what is missing and never deletes or updates a row: small tables
--        by key (NOT EXISTS), history orders by day batch (a full day is skipped, an empty day is loaded, a
--        partial day is refused), CLICK_ARCHIVE by 100,000-row batch from its highest id. Rows the driver
--        creates (ids above 1,000,000) are never read or touched. A re-run that finds everything in place
--        changes nothing and skips the statistics.
--
--        Bounded for a 2-CPU Free instance: one serial session (at most one CPU), batches of 16,667 orders
--        and 100,000 clicks, each its own transaction. Large batches are direct-path inserts (APPEND), which
--        in this NOARCHIVELOG database write almost no redo for table data, so the 2 x 10 MB online logs
--        are not flooded. Statistics: FOR ALL COLUMNS SIZE 1 (no histograms) as table preferences, so the
--        nightly auto-stats job keeps plans stable during the soak.
set serveroutput on size unlimited format wrapped
set verify off
set define off
set feedback off
set sqlblanklines on
set linesize 200 tab off trimout on
whenever sqlerror exit failure rollback
whenever oserror exit failure

variable g_t0       number
variable g_inserted number
variable g_anchor   varchar2(10)

prompt 88: checking where this runs and that 87 is in place
declare
  n        number;
  l_anchor date;
begin
  if sys_context('USERENV','CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '88: run in the PDB (FREEPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV','SESSION_USER') != 'SHOP' then
    raise_application_error(-20900, '88: run as SHOP, not '||sys_context('USERENV','SESSION_USER'));
  end if;
  select count(*) into n from user_tables
   where table_name in ('CATEGORIES','PRODUCTS','INVENTORY','CUSTOMERS','ORDERS','ORDER_LINES','PAYMENTS',
                        'PROMO_RULES','CLICK_ARCHIVE','DRIVER_CONTROL','DAILY_SETTLEMENT');
  if n != 11 then
    raise_application_error(-20900, '88: run 87_anom_shop_schema.sql first ('||n||' of 11 tables present)');
  end if;
  :g_t0       := dbms_utility.get_time;
  :g_inserted := 0;
  -- the anchor: the history is the 60 days before it. On a re-run it is read back from order 1, which is
  -- on the first history day (anchor - 60), so the dates never move.
  begin
    select trunc(order_ts) + 60 into l_anchor from ORDERS where order_id = 1;
  exception
    when no_data_found then
      l_anchor := trunc(sys_extract_utc(systimestamp));
  end;
  :g_anchor := to_char(l_anchor, 'YYYY-MM-DD');
  dbms_application_info.set_module('88_anom_shop_seed', 'start');
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 start        anchor='||:g_anchor||' history='||to_char(l_anchor - 60, 'YYYY-MM-DD')
    ||'..'||to_char(l_anchor - 1, 'YYYY-MM-DD')||' (UTC days)');
end;
/

prompt 88: CATEGORIES
declare
  n  number;
  t0 pls_integer := dbms_utility.get_time;
begin
  insert into CATEGORIES (category_id, name)
  select v.id, v.name
    from (values (1,'Coffee Maker'), (2,'Kettle'), (3,'Toaster'), (4,'Blender'), (5,'Air Fryer'),
                 (6,'Rice Cooker'), (7,'Cookware Set'), (8,'Knife Set'), (9,'Dinner Plate'), (10,'Water Bottle'),
                 (11,'Desk Lamp'), (12,'Floor Lamp'), (13,'Office Chair'), (14,'Standing Desk'), (15,'Bookshelf'),
                 (16,'Bed Sheet'), (17,'Pillow'), (18,'Bath Towel'), (19,'Shower Head'), (20,'Laundry Basket'),
                 (21,'Vacuum Cleaner'), (22,'Air Purifier'), (23,'Ceiling Fan'), (24,'Smart Plug'), (25,'LED Bulb'),
                 (26,'Headphones'), (27,'Bluetooth Speaker'), (28,'Power Bank'), (29,'Phone Case'), (30,'USB Cable'),
                 (31,'Keyboard'), (32,'Mouse'), (33,'Monitor'), (34,'Webcam'), (35,'Router'),
                 (36,'Running Shoe'), (37,'Backpack'), (38,'Sunglasses'), (39,'Wrist Watch'), (40,'Wallet'),
                 (41,'Yoga Mat'), (42,'Dumbbell'), (43,'Bicycle Helmet'), (44,'Tent'), (45,'Camping Stove'),
                 (46,'Board Game'), (47,'Jigsaw Puzzle'), (48,'Notebook'), (49,'Pen Set'), (50,'Plant Pot')
         ) v (id, name)
   where not exists (select 1 from CATEGORIES c where c.category_id = v.id);
  n := sql%rowcount;
  commit;
  :g_inserted := :g_inserted + n;
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 categories   inserted='||n||' elapsed_s='||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
end;
/

prompt 88: PRODUCTS
declare
  n  number;
  t0 pls_integer := dbms_utility.get_time;
begin
  insert into PRODUCTS (product_id, category_id, name, price, popularity, active)
  select g.pid,
         c.category_id,
         a.adj||' '||c.name||' '||chr(65 + ora_hash(g.pid, 25, 45))||to_char(100 + ora_hash(g.pid, 899, 46)),
         case when g.pid <= 50 then 15 + ora_hash(g.pid, 45, 42)
              else trunc(3 + power(ora_hash(g.pid, 9999, 42) / 9999, 2) * 497) end + 0.99,
         case when g.pid <= 50 then 1000000 - g.pid else ora_hash(g.pid, 99999, 43) end,
         'Y'
    from (select level pid from dual connect by level <= 20000) g
    join CATEGORIES c on c.category_id = mod(g.pid - 1, 50) + 1
    join (values (0,'Classic'), (1,'Compact'), (2,'Deluxe'), (3,'Eco'), (4,'Essential'), (5,'Premium'),
                 (6,'Pro'), (7,'Smart'), (8,'Ultra'), (9,'Urban'), (10,'Vintage'), (11,'Modern'), (12,'Travel'),
                 (13,'Family'), (14,'Mini'), (15,'Max'), (16,'Lite'), (17,'Signature'), (18,'Studio'), (19,'Daily')
         ) a (k, adj) on a.k = ora_hash(g.pid, 19, 41)
   where not exists (select 1 from PRODUCTS p where p.product_id = g.pid);
  n := sql%rowcount;
  commit;
  :g_inserted := :g_inserted + n;
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 products     inserted='||n||' elapsed_s='||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
end;
/

prompt 88: INVENTORY
declare
  n  number;
  t0 pls_integer := dbms_utility.get_time;
begin
  -- never resets stock: only products without an inventory row get one
  insert into INVENTORY (product_id, qty_on_hand)
  select p.product_id,
         case when p.product_id <= 50 then 2000000 else 200000 + ora_hash(p.product_id, 99999, 44) end
    from PRODUCTS p
   where p.product_id <= 20000
     and not exists (select 1 from INVENTORY i where i.product_id = p.product_id);
  n := sql%rowcount;
  commit;
  :g_inserted := :g_inserted + n;
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 inventory    inserted='||n||' elapsed_s='||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
end;
/

prompt 88: CUSTOMERS
declare
  n        number;
  t0       pls_integer := dbms_utility.get_time;
  l_anchor timestamp := cast(to_date(:g_anchor, 'YYYY-MM-DD') as timestamp);
begin
  insert /*+ append */ into CUSTOMERS (customer_id, name, email, country, created_ts)
  select g.id,
         f.fname||' '||l.lname,
         'customer'||to_char(g.id, 'fm000000')||'@example.com',
         case when ora_hash(g.id, 99, 51) < 40 then 'QA'
              when ora_hash(g.id, 99, 51) < 55 then 'AE'
              when ora_hash(g.id, 99, 51) < 70 then 'SA'
              when ora_hash(g.id, 99, 51) < 80 then 'IN'
              when ora_hash(g.id, 99, 51) < 85 then 'KW'
              when ora_hash(g.id, 99, 51) < 90 then 'OM'
              when ora_hash(g.id, 99, 51) < 95 then 'BH'
              else 'GB' end,
         l_anchor - numtodsinterval(1 + ora_hash(g.id, 1094, 52), 'DAY')
                  + numtodsinterval(ora_hash(g.id, 86399, 53), 'SECOND')
    from (select level id from dual connect by level <= 200000) g
    join (values (0,'Ahmed'), (1,'Fatima'), (2,'Mohammed'), (3,'Aisha'), (4,'Omar'), (5,'Mariam'), (6,'Ali'),
                 (7,'Noora'), (8,'Khalid'), (9,'Sara'), (10,'Yousef'), (11,'Layla'), (12,'Hassan'), (13,'Huda'),
                 (14,'Ravi'), (15,'Priya'), (16,'Arjun'), (17,'Ananya'), (18,'James'), (19,'Emma'), (20,'Daniel'),
                 (21,'Olivia'), (22,'Jose'), (23,'Maria'), (24,'Chen'), (25,'Mei'), (26,'Ivan'), (27,'Elena'),
                 (28,'Kwame'), (29,'Amina')) f (k, fname) on f.k = ora_hash(g.id, 29, 54)
    join (values (0,'Hassan'), (1,'Khan'), (2,'Rahman'), (3,'Haddad'), (4,'Saleh'), (5,'Nasser'), (6,'Farouk'),
                 (7,'Mansour'), (8,'Karim'), (9,'Aziz'), (10,'Sharma'), (11,'Patel'), (12,'Iyer'), (13,'Nair'),
                 (14,'Reddy'), (15,'Smith'), (16,'Jones'), (17,'Brown'), (18,'Taylor'), (19,'Wilson'), (20,'Garcia'),
                 (21,'Lopez'), (22,'Silva'), (23,'Santos'), (24,'Wang'), (25,'Li'), (26,'Petrov'), (27,'Ivanova'),
                 (28,'Mensah'), (29,'Okafor')) l (k, lname) on l.k = ora_hash(g.id, 29, 55)
   where not exists (select 1 from CUSTOMERS c where c.customer_id = g.id);
  n := sql%rowcount;
  commit;
  :g_inserted := :g_inserted + n;
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 customers    inserted='||n||' elapsed_s='||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
end;
/

prompt 88: PROMO_RULES
declare
  n  number;
  t0 pls_integer := dbms_utility.get_time;
begin
  -- rule ids 1-300 only; rows the slow_drift scenario adds (phase 4) are not this script's business
  insert into PROMO_RULES (rule_id, category_id, min_total, discount_pct, valid_to)
  select g.rid,
         mod(g.rid - 1, 50) + 1,
         case ceil(g.rid / 50) when 1 then 25 when 2 then 50 when 3 then 100 when 4 then 200 when 5 then 350 else 500 end,
         case ceil(g.rid / 50) when 1 then 2  when 2 then 3  when 3 then 5   when 4 then 7   when 5 then 10  else 12  end,
         case when mod(g.rid, 7) = 0 then date '2026-06-30' else date '2030-12-31' end
    from (select level rid from dual connect by level <= 300) g
   where not exists (select 1 from PROMO_RULES r where r.rule_id = g.rid);
  n := sql%rowcount;
  commit;
  :g_inserted := :g_inserted + n;
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 promo_rules  inserted='||n||' elapsed_s='||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
end;
/

prompt 88: ORDERS, ORDER_LINES, PAYMENTS (60 day batches)
declare
  c_n      constant number := 1000000;   -- history orders, ids 1 .. c_n
  c_days   constant number := 60;
  l_anchor date := to_date(:g_anchor, 'YYYY-MM-DD');
  l_first  date;
  l_end    timestamp;                    -- the anchor day 00:00 UTC: payments before it are settled
  l_lo     number;
  l_hi     number;
  n        number;
  n_ins    number := 0;
  n_skip   number := 0;
  n_rows   number := 0;
  t0       pls_integer := dbms_utility.get_time;
  tb       pls_integer;
begin
  l_first := l_anchor - c_days;
  l_end   := cast(l_anchor as timestamp);
  for d in 0 .. c_days - 1 loop
    l_lo := ceil(d * c_n / c_days) + 1;
    l_hi := ceil((d + 1) * c_n / c_days);
    select count(*) into n from ORDERS where order_id between l_lo and l_hi;
    if n = l_hi - l_lo + 1 then
      n_skip := n_skip + 1;
    elsif n > 0 then
      raise_application_error(-20900, '88: history day '||to_char(l_first + d, 'YYYY-MM-DD')||' is partly there ('
        ||n||' of '||(l_hi - l_lo + 1)||' orders, ids '||l_lo||'-'||l_hi||'); nothing changed - investigate');
    else
      tb := dbms_utility.get_time;
      dbms_application_info.set_action('orders day '||(d + 1)||'/'||c_days);
      -- one statement, one transaction per day: the order, its lines and its payment are written together
      insert /*+ append */ all
        when line_no = 1 then
          into ORDERS (order_id, customer_id, order_ts, status, total)
          values (order_id, customer_id, order_ts, status, order_total)
        when 1 = 1 then
          into ORDER_LINES (order_id, line_no, product_id, qty, price)
          values (order_id, line_no, product_id, qty, price)
        when line_no = 1 and status = 'PAID' then
          into PAYMENTS (payment_id, order_id, amount, method, paid_ts, settled)
          values (order_id, order_id, order_total, method, paid_ts, settled)
      with
        -- the driver's UTC load shape as hourly weights (night 25, peaks 100, shoulder 60, ramps between)
        hw (h, w) as (
          select * from (values (0,25), (1,25), (2,25), (3,25), (4,44), (5,81), (6,100), (7,100), (8,100),
                                (9,60), (10,60), (11,60), (12,60), (13,60), (14,60), (15,100), (16,100), (17,100),
                                (18,93), (19,78), (20,63), (21,48), (22,33), (23,25)) v (h, w)),
        hc as (
          select h, (sum(w) over (order by h) - w) / sum(w) over () lo, sum(w) over (order by h) / sum(w) over () hi
            from hw),
        -- position of each order in its day, 0 <= u < 1, evenly spaced
        g as (
          select l_lo + level - 1 i, (l_lo + level - 2) * c_days / c_n - d u
            from dual connect by level <= l_hi - l_lo + 1),
        o as (
          select g.i order_id,
                 1 + ora_hash(g.i, 199999, 61) customer_id,
                 -- inverse of the load shape's distribution: the hour whose share holds u, then the place in it
                 l_end - numtodsinterval(c_days - d, 'DAY')
                   + numtodsinterval(round((hc.h + (g.u - hc.lo) / (hc.hi - hc.lo)) * 3600, 3), 'SECOND') order_ts,
                 case when ora_hash(g.i, 99, 63) < 90 then 'PAID'
                      when ora_hash(g.i, 99, 63) < 96 then 'NEW'
                      else 'FAILED' end status,
                 case ora_hash(g.i, 9, 64) when 0 then 'BANK' when 1 then 'COD' when 2 then 'WALLET'
                                           when 3 then 'WALLET' when 4 then 'WALLET' else 'CARD' end method,
                 1 + ora_hash(g.i, 4, 62) n_lines
            from g
            join hc on g.u >= hc.lo and g.u < hc.hi),
        l as (
          select o.order_id, o.customer_id, o.order_ts, o.status, o.method,
                 o.order_ts + numtodsinterval(30 + ora_hash(o.order_id, 1769, 65), 'SECOND') paid_ts,
                 k.line_no,
                 case when k.line_no = 1 and ora_hash(o.order_id, 99, 66) < 20
                      then 1 + ora_hash(o.order_id, 49, 67)                       -- a hot product in 20% of orders
                      else 51 + ora_hash(o.order_id * 8 + k.line_no, 19949, 68) end product_id,
                 case when ora_hash(o.order_id * 8 + k.line_no, 99, 69) < 70 then 1
                      when ora_hash(o.order_id * 8 + k.line_no, 99, 69) < 90 then 2
                      else 3 end qty
            from o
            join (select level line_no from dual connect by level <= 5) k on k.line_no <= o.n_lines)
      select l.order_id, l.customer_id, l.order_ts, l.status, l.method, l.paid_ts,
             case when l.paid_ts < l_end then 'Y' else 'N' end settled,
             l.line_no, l.product_id, l.qty, p.price,
             sum(l.qty * p.price) over (partition by l.order_id) order_total
        from l
        join PRODUCTS p on p.product_id = l.product_id;
      n := sql%rowcount;
      commit;
      n_ins  := n_ins + 1;
      n_rows := n_rows + n;
      dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
        ||' 88 orders       day='||to_char(l_first + d, 'YYYY-MM-DD')||' ids='||l_lo||'-'||l_hi
        ||' rows='||n||' elapsed_s='||to_char((dbms_utility.get_time - tb) / 100, 'fm99990.00'));
    end if;
  end loop;
  :g_inserted := :g_inserted + n_rows;
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 orders       days_loaded='||n_ins||' days_skipped='||n_skip||' rows='||n_rows
    ||' elapsed_s='||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
end;
/

prompt 88: DAILY_SETTLEMENT for the history days
declare
  n  number;
  t0 pls_integer := dbms_utility.get_time;
  l_end timestamp := cast(to_date(:g_anchor, 'YYYY-MM-DD') as timestamp);
begin
  -- history payments only (ids up to 1,000,000), days before the anchor; a day the nightly job wrote is kept
  merge into DAILY_SETTLEMENT d
  using (select trunc(paid_ts) settle_date, count(*) n_payments, sum(amount) amount
           from PAYMENTS
          where payment_id <= 1000000 and settled = 'Y' and paid_ts < l_end
          group by trunc(paid_ts)) s
     on (d.settle_date = s.settle_date)
   when not matched then insert (settle_date, n_payments, amount)
                         values (s.settle_date, s.n_payments, s.amount);
  n := sql%rowcount;
  commit;
  :g_inserted := :g_inserted + n;
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 settlement   inserted='||n||' elapsed_s='||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
end;
/

prompt 88: CLICK_ARCHIVE (16 batches of 100,000 rows)
declare
  c_rows   constant number := 1600000;
  c_batch  constant number := 100000;
  l_end    timestamp := cast(to_date(:g_anchor, 'YYYY-MM-DD') as timestamp);
  l_max    number;
  l_cnt    number;
  l_lo     number;
  n        number;
  n_rows   number := 0;
  t0       pls_integer := dbms_utility.get_time;
  tb       pls_integer;
begin
  -- batches are atomic and loaded in id order, so the highest id says how far a previous run got
  select nvl(max(click_id), 0), count(*) into l_max, l_cnt from CLICK_ARCHIVE where click_id <= c_rows;
  if l_cnt != l_max or mod(l_max, c_batch) != 0 then
    raise_application_error(-20900, '88: CLICK_ARCHIVE holds '||l_cnt||' rows up to id '||l_max
      ||', not whole batches of '||c_batch||'; nothing changed - investigate');
  end if;
  for b in l_max / c_batch .. c_rows / c_batch - 1 loop
    tb   := dbms_utility.get_time;
    l_lo := b * c_batch + 1;
    dbms_application_info.set_action('click_archive batch '||(b + 1)||'/'||(c_rows / c_batch));
    insert /*+ append */ into CLICK_ARCHIVE
      (click_id, click_ts, customer_id, product_id, session_id, event_type, page_url, referrer, user_agent, detail)
    select g.i,
           -- 400 days of clicks up to the anchor day, one every 21.6 s
           l_end - numtodsinterval(400, 'DAY') + numtodsinterval(g.i * 21.6, 'SECOND'),
           1 + ora_hash(g.i, 199999, 21),
           1 + ora_hash(g.i, 19999, 22),
           to_char(ora_hash(trunc(g.i / 12), 4294967295, 31), 'fm0XXXXXXX')
             ||to_char(ora_hash(trunc(g.i / 12), 4294967295, 32), 'fm0XXXXXXX')
             ||to_char(ora_hash(trunc(g.i / 12), 4294967295, 33), 'fm0XXXXXXX')
             ||to_char(ora_hash(trunc(g.i / 12), 4294967295, 34), 'fm0XXXXXXX'),
           case ora_hash(g.i, 9, 25) when 0 then 'search' when 1 then 'add_to_cart' when 2 then 'checkout'
                                     when 3 then 'click' when 4 then 'click' else 'view' end,
           '/catalog/category/'||(1 + mod(ora_hash(g.i, 19999, 22), 50))||'/product/'||(1 + ora_hash(g.i, 19999, 22))
             ||'?utm_source=newsletter;s='||to_char(ora_hash(g.i, 4294967295, 26), 'fm0XXXXXXX'),
           case ora_hash(g.i, 3, 27) when 0 then null
                when 1 then 'https://www.example.com/search?q=item+'||ora_hash(g.i, 999, 28)
                else 'https://shop.example.com/catalog/category/'||(1 + ora_hash(g.i, 49, 29)) end,
           case ora_hash(g.i, 3, 30)
             when 0 then 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
             when 1 then 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148'
             when 2 then 'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36'
             else 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15' end,
           rpad('{"ref":"'||to_char(ora_hash(g.i, 4294967295, 23), 'fm0XXXXXXX')||'","pad":"', 420,
                to_char(ora_hash(g.i, 4294967295, 24), 'fm0XXXXXXX'))||'"}'
      from (select l_lo + level - 1 i from dual connect by level <= c_batch) g;
    n := sql%rowcount;
    commit;
    n_rows := n_rows + n;
    dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
      ||' 88 click_arch   batch='||(b + 1)||'/'||(c_rows / c_batch)||' rows='||n
      ||' elapsed_s='||to_char((dbms_utility.get_time - tb) / 100, 'fm99990.00'));
  end loop;
  :g_inserted := :g_inserted + n_rows;
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 click_arch   rows_inserted='||n_rows||' (already there: '||l_max||') elapsed_s='
    ||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
end;
/

prompt 88: optimizer statistics
declare
  n  number;
  t0 pls_integer := dbms_utility.get_time;
begin
  select count(*) into n from user_tables
   where last_analyzed is null
     and table_name in ('CATEGORIES','PRODUCTS','INVENTORY','CUSTOMERS','ORDERS','ORDER_LINES','PAYMENTS',
                        'PROMO_RULES','CLICK_ARCHIVE','DRIVER_CONTROL','DAILY_SETTLEMENT');
  if :g_inserted = 0 and n = 0 then
    dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
      ||' 88 stats        skipped: nothing inserted and every table has statistics');
  else
    dbms_application_info.set_action('gather_schema_stats');
    -- no histograms, here and in the nightly auto-stats job: plans must not flip during the soak
    for t in (select table_name from user_tables
               where table_name in ('CATEGORIES','PRODUCTS','INVENTORY','CUSTOMERS','ORDERS','ORDER_LINES',
                                    'PAYMENTS','PROMO_RULES','CLICK_ARCHIVE','DRIVER_CONTROL','DAILY_SETTLEMENT')) loop
      dbms_stats.set_table_prefs(user, t.table_name, 'METHOD_OPT', 'FOR ALL COLUMNS SIZE 1');
    end loop;
    dbms_stats.gather_schema_stats(ownname => user, degree => 1, no_invalidate => false);
    dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
      ||' 88 stats        gathered elapsed_s='||to_char((dbms_utility.get_time - t0) / 100, 'fm99990.00'));
  end if;
end;
/

prompt 88: summary
declare
  n  number;
  mb number;
begin
  for t in (select table_name from user_tables
             where table_name in ('CATEGORIES','PRODUCTS','INVENTORY','CUSTOMERS','ORDERS','ORDER_LINES','PAYMENTS',
                                  'PROMO_RULES','CLICK_ARCHIVE','DRIVER_CONTROL','DAILY_SETTLEMENT')
             order by table_name) loop
    execute immediate 'select count(*) from "'||t.table_name||'"' into n;
    select nvl(round(sum(bytes) / 1048576), 0) into mb from user_segments where segment_name = t.table_name;
    dbms_output.put_line('  '||rpad(t.table_name, 18)||lpad(to_char(n, 'fm999,999,990'), 12)||' rows '
                         ||lpad(to_char(mb, 'fm99,990'), 7)||' MB');
  end loop;
  select round(sum(bytes) / 1048576) into mb from user_segments;
  dbms_output.put_line('  all SHOP segments: '||mb||' MB');
  dbms_application_info.set_module(null, null);
  dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ||' 88 done         inserted='||:g_inserted||' total_elapsed_s='
    ||to_char((dbms_utility.get_time - :g_t0) / 100, 'fm999990.00'));
end;
/
