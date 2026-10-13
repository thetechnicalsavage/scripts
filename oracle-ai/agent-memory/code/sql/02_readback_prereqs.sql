-- 02_readback_prereqs.sql v1.0 2026-10-10 - Brief 12 day 1, right after 00 and 00b (PLAN 11, 11.4): read back, as
--   SYSDBA through tools/sysdba_run.sh, what the prerequisite scripts created, for the creation manifest the
--   rollback needs (results/pre/creation_manifest.json, written by tools/write_creation_manifest.py from this log).
--   Read-only: SELECTs and the container switch only. Every row starts with RB| and a kind.
--   v1.0: first version (day 1).
whenever sqlerror exit failure
set verify off echo off feedback off heading off pagesize 0 linesize 400 trimspool on termout on
alter session set container = ORCLPDB1;
begin
  if sys_context('userenv', 'con_name') <> 'ORCLPDB1' then
    raise_application_error(-20001, 'expected ORCLPDB1, found ' || sys_context('userenv', 'con_name'));
  end if;
end;
/
select 'RB|time_utc|' || to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"') from dual;
select 'RB|user|' || username || '|' || account_status || '|' ||
       to_char(created, 'YYYY-MM-DD HH24:MI:SS') || ' (database clock)' ||
       '|' || default_tablespace || '|' || temporary_tablespace || '|' || authentication_type
  from dba_users where username in ('AGENTMEM', 'AGENTMEM_RT') order by username;
select 'RB|sys_priv|' || grantee || '|' || privilege || '|' || admin_option from dba_sys_privs
 where grantee in ('AGENTMEM', 'AGENTMEM_RT') order by grantee, privilege;
select 'RB|tab_priv|' || grantee || '|' || privilege || '|' || owner || '|' || table_name || '|' || type || '|' ||
       grantor || '|' || grantable
  from dba_tab_privs where grantee in ('AGENTMEM', 'AGENTMEM_RT') order by grantee, owner, table_name, privilege;
select 'RB|role_priv|' || grantee || '|' || granted_role from dba_role_privs
 where grantee in ('AGENTMEM', 'AGENTMEM_RT') order by grantee, granted_role;
select 'RB|quota|' || username || '|' || tablespace_name || '|' || max_bytes from dba_ts_quotas
 where username in ('AGENTMEM', 'AGENTMEM_RT') order by username, tablespace_name;
-- EXECUTE on the two vector packages: who holds it (PUBLIC or AGENTMEM), so the manifest says whether 00 granted it
select 'RB|pkg_exec|' || p.owner || '.' || p.table_name || '|' || p.grantee || '|' || p.grantor
  from dba_tab_privs p
 where p.table_name in ('DBMS_VECTOR_CHAIN', 'DBMS_HYBRID_VECTOR') and p.privilege = 'EXECUTE'
   and p.grantee in ('PUBLIC', 'AGENTMEM', 'AGENTMEM_RT') and p.owner in ('SYS', 'CTXSYS')
 order by p.table_name, p.grantee;
select 'RB|objects|' || owner || '|' || count(*) from dba_objects where owner in ('AGENTMEM', 'AGENTMEM_RT')
 group by owner order by owner;
select 'RB|askoracle_model_count|' || count(*) from dba_mining_models where owner = 'ASKORACLE';
select 'RB|end|ok' from dual;
