-- v1.3 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.2 2026-11-11 - the orders rule names ORDER_DATE as the date for fiscal-year and "last N days" figures (same text as
--                    the glossary file in 03).
-- v1.1 2026-11-11 - the annotation check query uses USER_ANNOTATIONS_USAGE's OBJECT_NAME column.
-- v1.0 2026-11-11 - brief 15 lab schema: a small fictional retailer (regions, customers, orders, support tickets).
--   Column names are implementation-style on purpose (GROSS_AMT, DISC_AMT, ESCL_FLAG, SEV). Comments are technical
--   only: they say what a column holds, never what a business term means. The business glossary reaches the model in
--   two other ways, compared in the evaluation: a semantic file source (arm B) and table annotations (arm C, read only
--   when a profile sets "annotations": true). Run as SAI_SEM_LAB, re-runnable (drops the four tables first):
--     as SAI_SEM_LAB, any SQL client (README)

drop table tickets purge;
drop table orders purge;
drop table customers purge;
drop table regions purge;

create table regions (
  region_id   number(2)    primary key,
  region_name varchar2(30) not null unique
);

create table customers (
  cust_id     number(6)    primary key,
  cust_name   varchar2(60) not null,
  region_id   number(2)    not null references regions,
  segment     varchar2(12) not null check (segment in ('RETAIL', 'SMB', 'ENTERPRISE')),
  acct_status varchar2(10) not null check (acct_status in ('OPEN', 'CLOSED', 'ON_HOLD')),
  signup_date date         not null
);

create table orders (
  order_id      number(8)    primary key,
  cust_id       number(6)    not null references customers,
  order_date    date         not null,
  status        varchar2(10) not null check (status in ('BOOKED', 'SHIPPED', 'CANCELLED', 'RETURNED')),
  gross_amt     number(12,2) not null,
  disc_amt      number(12,2) default 0 not null,
  promised_date date         not null,
  shipped_date  date
);

create table tickets (
  ticket_id   number(8)    primary key,
  cust_id     number(6)    not null references customers,
  opened_date date         not null,
  closed_date date,
  sev         number(1)    not null check (sev between 1 and 4),
  escl_flag   char(1)      not null check (escl_flag in ('Y', 'N')),
  status      varchar2(10) not null check (status in ('OPEN', 'PENDING', 'CLOSED'))
);

create index orders_cust_ix  on orders (cust_id, order_date);
create index tickets_cust_ix on tickets (cust_id);

comment on table regions   is 'Sales regions.';
comment on table customers is 'Customer accounts.';
comment on table orders    is 'Customer orders, one row per order.';
comment on table tickets   is 'Customer support tickets.';
comment on column customers.segment       is 'Customer segment: RETAIL, SMB or ENTERPRISE.';
comment on column customers.acct_status   is 'Account status: OPEN, CLOSED or ON_HOLD.';
comment on column orders.status           is 'Order status: BOOKED, SHIPPED, CANCELLED or RETURNED.';
comment on column orders.gross_amt        is 'Order amount before discount.';
comment on column orders.disc_amt         is 'Discount amount.';
comment on column orders.promised_date    is 'Delivery date promised to the customer.';
comment on column orders.shipped_date     is 'Date the order shipped; empty until it ships.';
comment on column tickets.sev             is 'Ticket severity, 1 to 4.';
comment on column tickets.escl_flag       is 'Escalation flag, Y or N.';
comment on column tickets.status          is 'Ticket status: OPEN, PENDING or CLOSED.';

-- Arm C: the same glossary as table annotations (26ai). Select AI sends them only when "annotations" is true.
alter table orders annotations (add business_rules
  'Net revenue: GROSS_AMT minus DISC_AMT, summed over orders with STATUS = SHIPPED only; BOOKED, CANCELLED and RETURNED orders add nothing. Average order value: net revenue divided by the number of SHIPPED orders. Late shipment: a SHIPPED order whose SHIPPED_DATE is more than 2 days after PROMISED_DATE. Fiscal year: 1 April to 31 March, named by the year it ends (FY2026 = 1 April 2025 to 31 March 2026); this fiscal year is the one that contains today. Fiscal-year figures and "last N days" windows use ORDER_DATE.');
alter table customers annotations (add business_rules
  'High-value customer: lifetime net revenue above 50000. At-risk customer: a high-value customer with no order other than CANCELLED in the last 90 days and at least one open escalation. Active customer: ACCT_STATUS = OPEN and at least one SHIPPED order in the last 365 days. Churned customer: ACCT_STATUS = CLOSED, or no SHIPPED order in the last 180 days. Repeat customer: 3 or more SHIPPED orders.');
alter table tickets annotations (add business_rules
  'Open ticket: STATUS is OPEN or PENDING. Open escalation: a ticket with ESCL_FLAG = Y that is not CLOSED. Critical ticket: SEV = 1 (1 is the most severe, 4 the least).');

select object_name, column_name, annotation_name, substr(annotation_value, 1, 60) value_start
  from user_annotations_usage order by 1, 3;
