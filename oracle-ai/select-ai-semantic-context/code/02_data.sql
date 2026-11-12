-- v1.2 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.1 2026-11-11 - no order falls within a day of the 90/180/365-day window edges, so "last N days" answers cannot
--                    differ by one day between SYSDATE and TRUNC(SYSDATE) forms.
-- v1.0 2026-11-11 - brief 15 lab data: deterministic (DBMS_RANDOM seed 1515), dates relative to the day it runs, so
--   "last 90 days" and "this fiscal year" questions have answers that cover every business rule in the glossary.
--   600 customers, about 9,000 orders over 30 months, about 1,400 tickets. Run as SAI_SEM_LAB after 01_schema.sql:
--     as SAI_SEM_LAB, any SQL client (README)
declare
  c_today constant date := trunc(sysdate);
  l_order_id  pls_integer := 0;
  l_ticket_id pls_integer := 0;
  l_segment   varchar2(12);
  l_status    varchar2(10);
  l_last_gap  pls_integer;   -- days since this customer's last order (most customers order until recently)
  l_n_orders  pls_integer;
  l_day       date;
  l_amt       number;
  l_ostatus   varchar2(10);
  l_promised  date;
  l_shipped   date;
  l_r         number;
begin
  dbms_random.seed(1515);
  delete from tickets; delete from orders; delete from customers; delete from regions;
  insert into regions values (1, 'North');
  insert into regions values (2, 'South');
  insert into regions values (3, 'East');
  insert into regions values (4, 'West');
  insert into regions values (5, 'Central');

  for c in 1 .. 600 loop
    l_r := dbms_random.value;
    l_segment := case when l_r < 0.55 then 'RETAIL' when l_r < 0.85 then 'SMB' else 'ENTERPRISE' end;
    l_r := dbms_random.value;
    l_status := case when l_r < 0.08 then 'CLOSED' when l_r < 0.12 then 'ON_HOLD' else 'OPEN' end;
    insert into customers values (c, 'Customer '||to_char(c, 'FM000'), trunc(dbms_random.value(1, 6)), l_segment,
                                  l_status, c_today - trunc(dbms_random.value(400, 1500)));

    -- about 22% of customers stopped ordering 91-500 days ago; closed accounts stopped too
    l_r := dbms_random.value;
    l_last_gap := case when l_status = 'CLOSED' or l_r < 0.22 then trunc(dbms_random.value(91, 500))
                       else trunc(dbms_random.value(0, 60)) end;
    l_n_orders := trunc(dbms_random.value(2, 28));
    for o in 1 .. l_n_orders loop
      l_day := c_today - l_last_gap - trunc(dbms_random.value(0, 900 - least(l_last_gap, 600)));
      if o = 1 then l_day := c_today - l_last_gap; end if;    -- the most recent order sits exactly at the gap
      if c_today - l_day in (89, 90, 91, 179, 180, 181, 364, 365, 366) then l_day := l_day - 3; end if;
      l_amt := round(case l_segment when 'RETAIL' then dbms_random.value(300, 4500)
                                    when 'SMB' then dbms_random.value(1500, 9000)
                                    else dbms_random.value(6000, 30000) end, 2);
      l_r := dbms_random.value;
      l_ostatus := case when l_day > c_today - 10 and l_r < 0.6 then 'BOOKED'
                        when l_r < 0.07 then 'CANCELLED' when l_r < 0.12 then 'RETURNED' else 'SHIPPED' end;
      l_promised := l_day + trunc(dbms_random.value(3, 10));
      l_r := dbms_random.value;
      l_shipped := case when l_ostatus in ('SHIPPED', 'RETURNED')
                        then l_promised + case when l_r < 0.70 then -trunc(dbms_random.value(0, 3))
                                               when l_r < 0.85 then trunc(dbms_random.value(1, 3))     -- 1-2 days: within grace
                                               else trunc(dbms_random.value(3, 12)) end                  -- late
                   end;
      if l_shipped > c_today then l_shipped := null; l_ostatus := 'BOOKED'; end if;
      l_order_id := l_order_id + 1;
      insert into orders values (l_order_id, c, l_day, l_ostatus, l_amt,
                                 case when dbms_random.value < 0.35 then round(l_amt * dbms_random.value(0.02, 0.20), 2) else 0 end,
                                 l_promised, l_shipped);
    end loop;

    for t in 1 .. trunc(dbms_random.value(0, 5)) loop
      l_ticket_id := l_ticket_id + 1;
      l_day := c_today - trunc(dbms_random.value(0, 700));
      l_r := dbms_random.value;
      l_status := case when l_day > c_today - 120 and l_r < 0.45 then 'OPEN'
                       when l_day > c_today - 120 and l_r < 0.60 then 'PENDING' else 'CLOSED' end;
      insert into tickets values (l_ticket_id, c, l_day,
                                  case when l_status = 'CLOSED' then l_day + trunc(dbms_random.value(1, 30)) end,
                                  trunc(dbms_random.value(1, 5)),
                                  case when dbms_random.value < 0.30 then 'Y' else 'N' end, l_status);
    end loop;
  end loop;
  commit;
  dbms_output.put_line('orders '||l_order_id||', tickets '||l_ticket_id);
end;
/
begin
  dbms_stats.gather_schema_stats(user);
end;
/
select (select count(*) from customers) customers, (select count(*) from orders) orders,
       (select count(*) from tickets) tickets, (select min(order_date) from orders) first_order,
       (select max(order_date) from orders) last_order from dual;
select status, count(*) from orders group by status order by 1;
