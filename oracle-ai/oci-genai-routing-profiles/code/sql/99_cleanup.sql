-- v1.1 - brief 14 ROUTE_LAB: remove the schema, its ACE and its grants. Run as SYSDBA, after 98 (as ROUTE_LAB).
--        v1.1: (S8, 10-Oct) SERVEROUTPUT switched on again after ALTER SESSION SET CONTAINER (the switch drops it, so S8's check printed no FREE/TAKEN verdict).
--        v1.0: first version (PLAN.md v1.0 section 12 step 2). Written in the plan stage; NOT run yet.
--
-- Usage : on the demo VM, piped into SQL*Plus in the database container (brief 12's approved D13 runner):
--           connect / as sysdba
--           @99_cleanup.sql
-- Re-run: safe; each step skips what is already gone.
-- Refuses to drop a ROUTE_LAB that does not carry brief 14's marker table (it would not be ours).
-- Dropping the user removes its tables, views, package, credential and the SELECT grants it gave to ASKORACLE.
-- The host ACE is not owned by the user, so it is removed first.
whenever sqlerror exit failure rollback
set verify off feedback off serveroutput on size unlimited define off
alter session set container = ORCLPDB1;
set serveroutput on size unlimited

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '99: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '99: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if sys_context('USERENV', 'CON_NAME') != 'ORCLPDB1' then
    raise_application_error(-20140, '99: run in ORCLPDB1');
  end if;
end;
/

declare
  l_user number;
  l_mark number;
begin
  select count(*) into l_user from dba_users where username = 'ROUTE_LAB';
  select count(*) into l_mark from dba_tables where owner = 'ROUTE_LAB' and table_name = 'RL_MARK';
  if l_user = 1 and l_mark = 0 then
    raise_application_error(-20141, '99: ROUTE_LAB has no brief 14 marker; not dropping it');
  end if;
end;
/

begin
  for a in (select distinct host from dba_host_aces where principal = 'ROUTE_LAB') loop
    if a.host != 'inference.generativeai.us-phoenix-1.oci.oraclecloud.com' then
      -- 00 only ever adds the Phoenix inference host: anything else was not ours to remove
      raise_application_error(-20149, '99: ROUTE_LAB holds an ACE for an unexpected host; stopping');
    end if;
    dbms_network_acl_admin.remove_host_ace(
      host             => a.host,
      ace              => xs$ace_type(privilege_list => xs$name_list('connect', 'resolve', 'http'),
                                      principal_name => 'ROUTE_LAB',
                                      principal_type => xs_acl.ptype_db),
      remove_empty_acl => true);
    dbms_output.put_line('host ACE removed for the Phoenix inference host');
  end loop;
end;
/

begin
  for s in (select sid, serial# from v$session where username = 'ROUTE_LAB') loop
    raise_application_error(-20148, '99: a ROUTE_LAB session is still open; close it first (nothing was killed)');
  end loop;
end;
/

drop user if exists ROUTE_LAB cascade;

prompt Verification (every count must be 0):
select (select count(*) from dba_users where username = 'ROUTE_LAB')                     as users_left,
       (select count(*) from dba_host_aces where principal = 'ROUTE_LAB')                 as aces_left,
       (select count(*) from dba_tab_privs where grantor = 'ROUTE_LAB' or owner = 'ROUTE_LAB'
                                             or grantee = 'ROUTE_LAB')                    as grants_left,
       (select count(*) from dba_credentials where owner = 'ROUTE_LAB')                   as credentials_left
  from dual;
