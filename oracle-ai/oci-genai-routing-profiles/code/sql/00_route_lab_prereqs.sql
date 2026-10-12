-- v1.2 - brief 14 ROUTE_LAB: the schema, its grants and the Phoenix inference-host ACE. Run as SYSDBA.
--        v1.2: (S8, 10-Oct) SERVEROUTPUT switched on again after ALTER SESSION SET CONTAINER (the switch drops it, so S8's check printed no FREE/TAKEN verdict).
--        v1.1: (S6, 10-Oct) CREATE SEQUENCE added: 01's RL_CALL.call_id is an identity column, and Oracle creates
--              its sequence under the owner's privileges (01 stopped with ORA-01031 at RL_CALL without it; brief 12
--              hit the same with AGENTMEM); each ACE privilege is appended only when it is missing (re-run safe,
--              Codex review 13). Run through tools/b14_sql.sh v1.1.
--        v1.0: first version (PLAN.md v1.0 section 10). Written in the plan stage; NOT run yet.
--
-- Usage : on the demo VM, piped into SQL*Plus in the database container (brief 12's approved D13 runner):
--           connect / as sysdba
--           @00_route_lab_prereqs.sql <route_lab_password>
--         The password (24 letters and digits) is generated on the VM into ~/oracle26ai/secrets/route_lab.env (600)
--         and reaches SQL*Plus on stdin only; the runner checks its alphabet in the shell (a PL/SQL literal holding
--         it would stay in the shared pool, so it is not checked here).
-- Re-run: safe. The user is created if absent; an existing ROUTE_LAB without this brief's marker table is refused.
--         Grants and the ACE are re-applied (both are idempotent).
-- Footprint outside ROUTE_LAB (PLAN.md rule 5): EXECUTE on three C##CLOUD$SERVICE packages, one host ACE. Nothing
-- else in the database is touched.
whenever sqlerror exit failure rollback
set verify off feedback off serveroutput on size unlimited
alter session set container = ORCLPDB1;
set serveroutput on size unlimited

-- Rule 6: no database work 01:50-04:00 UTC, nor Monday 12-Oct-2026 09:00-16:00 UTC.
declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '00: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '00: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if sys_context('USERENV', 'CON_NAME') != 'ORCLPDB1' then
    raise_application_error(-20140, '00: run in ORCLPDB1');
  end if;
end;
/

-- Ours or nobody's: an existing ROUTE_LAB must carry the marker this script creates.
declare
  l_user number;
  l_mark number;
begin
  select count(*) into l_user from dba_users where username = 'ROUTE_LAB';
  select count(*) into l_mark from dba_tables where owner = 'ROUTE_LAB' and table_name = 'RL_MARK';
  if l_user = 1 and l_mark = 0 then
    raise_application_error(-20141, '00: a ROUTE_LAB user exists without the brief 14 marker; stopping');
  end if;
end;
/

define rl_pwd = &1
create user if not exists ROUTE_LAB identified by "&rl_pwd"
  default tablespace USERS temporary tablespace TEMP;
alter user ROUTE_LAB identified by "&rl_pwd" account unlock;
undefine rl_pwd
alter user ROUTE_LAB quota 100M on USERS;

create table if not exists ROUTE_LAB.RL_MARK (
  brief       varchar2(10) default 'b14' not null check (brief = 'b14'),
  created_utc timestamp    default sys_extract_utc(systimestamp) not null
);
insert into ROUTE_LAB.RL_MARK (brief) select 'b14' from dual where not exists (select 1 from ROUTE_LAB.RL_MARK);
commit;

grant create session, create table, create view, create procedure, create sequence to ROUTE_LAB;

-- Select AI and DBMS_CLOUD on a non-Autonomous 26ai are owned by C##CLOUD$SERVICE (as brief 08's lab found).
begin
  for p in (select object_name from dba_objects
             where owner = 'C##CLOUD$SERVICE' and object_type = 'PACKAGE'
               and object_name in ('DBMS_CLOUD', 'DBMS_CLOUD_AI', 'DBMS_CLOUD_TYPES')) loop
    execute immediate 'grant execute on C##CLOUD$SERVICE.' || p.object_name || ' to ROUTE_LAB';
    dbms_output.put_line('granted EXECUTE on ' || p.object_name);
  end loop;
end;
/

-- Outbound HTTPS to the Phoenix inference host only (the PLAN's Phoenix lock; no management-host ACE).
-- Appended only when one of the three privileges is missing, so a re-run adds nothing (Codex review 13, finding 4).
declare
  type t_list is table of varchar2(10);
  l_privs t_list := t_list('connect', 'resolve', 'http');
  l_n     number;
begin
  for i in 1 .. l_privs.count loop           -- each privilege on its own: only a missing one is appended
    select count(*) into l_n from dba_host_aces
     where host = 'inference.generativeai.us-phoenix-1.oci.oraclecloud.com' and principal = 'ROUTE_LAB'
       and upper(privilege) = upper(l_privs(i));
    if l_n = 0 then
      dbms_network_acl_admin.append_host_ace(
        host => 'inference.generativeai.us-phoenix-1.oci.oraclecloud.com',
        ace  => xs$ace_type(privilege_list => xs$name_list(l_privs(i)),
                            principal_name => 'ROUTE_LAB',
                            principal_type => xs_acl.ptype_db));
    end if;
  end loop;
  dbms_output.put_line('host ACE present for ROUTE_LAB on the Phoenix inference host');
end;
/

prompt What ROUTE_LAB holds outside its own schema:
set linesize 160 pagesize 50
column privilege format a24
column object    format a70
select privilege, null as object from dba_sys_privs where grantee = 'ROUTE_LAB'
union all
select privilege, owner || '.' || table_name from dba_tab_privs where grantee = 'ROUTE_LAB'
union all
select 'ACE', host from dba_host_aces where principal = 'ROUTE_LAB'
order by 2 nulls first, 1;
