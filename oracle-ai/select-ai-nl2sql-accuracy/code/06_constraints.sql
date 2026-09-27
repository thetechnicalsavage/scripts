-- v1.1 - NL2SQL accuracy lab, practice 3: CONSTRAINTS - declare the join paths.
--        v1.1: identifiers checked with DBMS_ASSERT before dynamic DDL.
--
-- Run as : NL2SQL_LAB, after 05_annotations.sql
-- Re-run : safe. A constraint is added only if a constraint of that name is absent.
--
-- Primary and foreign keys tell the model which columns join. ORD_HDR has TWO foreign
-- keys to CUST_MST (CUST_NO and BILL_TO_NO) on purpose: constraints say both joins are
-- valid; only the comments say which one answers a region question.

set feedback off serveroutput on
whenever sqlerror exit failure

declare
  procedure add_c(p_table varchar2, p_name varchar2, p_def varchar2) is
    l_n pls_integer;
  begin
    select count(*) into l_n from user_constraints where constraint_name = upper(p_name);
    if l_n = 0 then
      execute immediate 'alter table ' || dbms_assert.simple_sql_name(p_table) || ' add constraint '
                        || dbms_assert.simple_sql_name(p_name) || ' ' || p_def;   -- p_def: constant text below
      dbms_output.put_line('added   ' || p_name);
    else
      dbms_output.put_line('present ' || p_name);
    end if;
  end;
begin
  add_c('rgn_mst',  'rgn_mst_pk',   'primary key (rgn_cd)');
  add_c('cat_lkp',  'cat_lkp_pk',   'primary key (cat_cd)');
  add_c('cust_mst', 'cust_mst_pk',  'primary key (cust_no)');
  add_c('prd_mst',  'prd_mst_pk',   'primary key (prd_id)');
  add_c('ord_hdr',  'ord_hdr_pk',   'primary key (ord_no)');
  add_c('ord_ln',   'ord_ln_pk',    'primary key (ord_no, ln_no)');
  add_c('sls_tgt',  'sls_tgt_pk',   'primary key (rgn_cd, fisc_yr)');

  add_c('cust_mst', 'cust_rgn_fk',     'foreign key (rgn_cd) references rgn_mst (rgn_cd)');
  add_c('prd_mst',  'prd_cat_fk',      'foreign key (cat_cd) references cat_lkp (cat_cd)');
  add_c('ord_hdr',  'ord_cust_fk',     'foreign key (cust_no) references cust_mst (cust_no)');
  add_c('ord_hdr',  'ord_billto_fk',   'foreign key (bill_to_no) references cust_mst (cust_no)');
  add_c('ord_ln',   'ordln_ord_fk',    'foreign key (ord_no) references ord_hdr (ord_no)');
  add_c('ord_ln',   'ordln_prd_fk',    'foreign key (prd_id) references prd_mst (prd_id)');
  add_c('sls_tgt',  'tgt_rgn_fk',      'foreign key (rgn_cd) references rgn_mst (rgn_cd)');
  add_c('ar_rcpt',  'rcpt_cust_fk',    'foreign key (cust_no) references cust_mst (cust_no)');
end;
/

begin
  dbms_cloud_ai.set_attribute(profile_name    => 'NL2SQL_LAB_AI',
                              attribute_name  => 'constraints',
                              attribute_value => 'true');
end;
/

prompt
column constraint_name format a16
column table_name      format a10
column refers_to       format a12
select c.constraint_name, c.table_name, c.constraint_type,
       (select r.table_name from user_constraints r where r.constraint_name = c.r_constraint_name) refers_to
  from user_constraints c
 where c.constraint_type in ('P', 'R')
 order by c.constraint_type, c.table_name, c.constraint_name;
