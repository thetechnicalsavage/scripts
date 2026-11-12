-- v1.2 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.1 2026-11-11 - Codex r2: re-runnable (questions table dropped only if present; the results table is kept, created
--                    only if missing); Q11's "this fiscal year" is computed from SYSDATE instead of fixed dates.
-- v1.0 2026-11-11 - brief 15: the 12 evaluation questions with their gold SQL (written from the glossary by hand), and
--   the results table run_eval.py fills. Every question uses one glossary term whose plain-English reading differs from
--   the business definition. Run as SAI_SEM_LAB, re-runnable: any SQL client (README)
declare
  e_no_table exception;
  pragma exception_init(e_no_table, -942);
begin
  execute immediate 'drop table sem_eval_questions purge';
exception
  when e_no_table then null;
end;
/
create table sem_eval_questions (
  qid      varchar2(4) primary key,
  term     varchar2(40) not null,
  question varchar2(200) not null,
  gold_sql clob not null
);
declare
  l_n pls_integer;
begin
  select count(*) into l_n from user_tables where table_name = 'SEM_EVAL_RESULTS';
  if l_n = 0 then
    execute immediate q'~create table sem_eval_results (
  run_id        varchar2(30)  not null,
  arm           varchar2(12)  not null,
  qid           varchar2(4)   not null,
  rep           number(2)     not null,
  outcome       varchar2(12)  not null,   -- correct | wrong | error | no_sql
  detail        varchar2(400),
  generated_sql clob,
  elapsed_ms    number,
  created_at    timestamp default systimestamp,
  constraint sem_eval_results_pk primary key (run_id, arm, qid, rep)
)~';
  end if;
end;
/

insert into sem_eval_questions values ('Q01', 'high-value customer', 'How many high-value customers do we have?',
q'[select count(*) from (select cust_id from orders where status = 'SHIPPED'
 group by cust_id having sum(gross_amt - disc_amt) > 50000)]');

insert into sem_eval_questions values ('Q02', 'at-risk customer', 'How many at-risk customers are there in each region?',
q'[select r.region_name, count(*)
 from customers c join regions r on r.region_id = c.region_id
 where c.cust_id in (select cust_id from orders where status = 'SHIPPED'
                     group by cust_id having sum(gross_amt - disc_amt) > 50000)
   and c.cust_id not in (select cust_id from orders where status <> 'CANCELLED' and order_date >= trunc(sysdate) - 90)
   and c.cust_id in (select cust_id from tickets where escl_flag = 'Y' and status <> 'CLOSED')
 group by r.region_name]');

insert into sem_eval_questions values ('Q03', 'net revenue, fiscal year', 'What was our net revenue in FY2026?',
q'[select sum(gross_amt - disc_amt) from orders
 where status = 'SHIPPED' and order_date >= date '2025-04-01' and order_date < date '2026-04-01']');

insert into sem_eval_questions values ('Q04', 'late shipment', 'How many late shipments were there in FY2026?',
q'[select count(*) from orders
 where status = 'SHIPPED' and shipped_date > promised_date + 2
   and order_date >= date '2025-04-01' and order_date < date '2026-04-01']');

insert into sem_eval_questions values ('Q05', 'critical, open ticket', 'How many critical tickets are open right now?',
q'[select count(*) from tickets where sev = 1 and status in ('OPEN', 'PENDING')]');

insert into sem_eval_questions values ('Q06', 'active customer', 'How many active customers are there in the West region?',
q'[select count(*) from customers c join regions r on r.region_id = c.region_id
 where r.region_name = 'West' and c.acct_status = 'OPEN'
   and exists (select 1 from orders o where o.cust_id = c.cust_id and o.status = 'SHIPPED'
                                        and o.order_date >= trunc(sysdate) - 365)]');

insert into sem_eval_questions values ('Q07', 'average order value', 'What is the average order value for enterprise customers?',
q'[select sum(o.gross_amt - o.disc_amt) / count(*) from orders o join customers c on c.cust_id = o.cust_id
 where c.segment = 'ENTERPRISE' and o.status = 'SHIPPED']');

insert into sem_eval_questions values ('Q08', 'churned customer', 'How many churned customers do we have?',
q'[select count(*) from customers c
 where c.acct_status = 'CLOSED'
    or not exists (select 1 from orders o where o.cust_id = c.cust_id and o.status = 'SHIPPED'
                                            and o.order_date >= trunc(sysdate) - 180)]');

insert into sem_eval_questions values ('Q09', 'repeat customer', 'How many repeat customers are there in each segment?',
q'[select c.segment, count(*) from customers c
 where (select count(*) from orders o where o.cust_id = c.cust_id and o.status = 'SHIPPED') >= 3
 group by c.segment]');

insert into sem_eval_questions values ('Q10', 'open escalation', 'Which region has the most open escalations?',
q'[select region_name from (
 select r.region_name, count(*) n from tickets t join customers c on c.cust_id = t.cust_id
   join regions r on r.region_id = c.region_id
  where t.escl_flag = 'Y' and t.status <> 'CLOSED'
  group by r.region_name order by n desc fetch first 1 row only)]');

insert into sem_eval_questions values ('Q11', 'net revenue, this fiscal year', 'What is net revenue by region for this fiscal year?',
q'[select r.region_name, sum(o.gross_amt - o.disc_amt)
 from orders o join customers c on c.cust_id = o.cust_id join regions r on r.region_id = c.region_id
 where o.status = 'SHIPPED'
   and o.order_date >= add_months(trunc(add_months(sysdate, -3), 'YYYY'), 3)
   and o.order_date <  add_months(trunc(add_months(sysdate, -3), 'YYYY'), 15)
 group by r.region_name]');

insert into sem_eval_questions values ('Q12', 'late shipment, fiscal year', 'What percentage of FY2026 shipments were late?',
q'[select round(100 * sum(case when shipped_date > promised_date + 2 then 1 else 0 end) / count(*), 1) from orders
 where status = 'SHIPPED' and order_date >= date '2025-04-01' and order_date < date '2026-04-01']');
commit;

select qid, term, question from sem_eval_questions order by qid;
