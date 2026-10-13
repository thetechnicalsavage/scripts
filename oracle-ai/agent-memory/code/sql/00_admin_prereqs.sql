-- 00_admin_prereqs.sql v0.4 (DRAFT, NOT RUN) - Brief 12 POC: owner schema AGENTMEM and the model grant.
--   v0.4: (Codex adversarial review, 10-Oct) the package owner is counted, not selected INTO: exactly one VALID package
--   owned by SYS or CTXSYS, or the script stops (no TOO_MANY_ROWS, no guess).
--   v0.3: CREATE SEQUENCE dropped (identity columns in one's own schema need only CREATE TABLE; the 26ai CREATE TABLE
--   reference asks for CREATE ANY SEQUENCE only when the table is in another schema). v0.2: CREATE VIEW dropped (the
--   SDK creates no view, API_NOTES B29); package owners looked up, not assumed. v0.1: first version. Run as SYSDBA (OS authentication) on the database host. The caller defines agentmem_pwd on
--   stdin before @. Re-run: safe (IF NOT EXISTS; grants are idempotent; the conditional grants check first).
whenever sqlerror exit failure
whenever oserror exit failure
set verify off echo off feedback on termout on serveroutput on

alter session set container = ORCLPDB1;
begin
  if sys_context('userenv', 'con_name') <> 'ORCLPDB1' then
    raise_application_error(-20001, 'expected ORCLPDB1, found ' || sys_context('userenv', 'con_name'));
  end if;
end;
/

-- 1. Owner schema. The SDK installs DBMS_AGENT_MEMORY_STORE(_INT) and the AGENT_MEMORY_STORES registry here.
set termout off
create user if not exists AGENTMEM identified by "&agentmem_pwd"
  default tablespace USERS temporary tablespace TEMP;
set termout on
undefine agentmem_pwd
alter user AGENTMEM quota 1G on USERS;
-- CREATE TABLE also covers the SDK's identity columns (dbms_agent_memory_store.pkb:3481 and four more) in AGENTMEM's
-- own schema. CREATE JOB: the purge jobs (B13). CREATE TRIGGER and CREATE PROPERTY GRAPH: the link-delete trigger and
-- the memory graph (B4, B5). CREATE PROCEDURE: the SDK's two packages (B29).
grant create session, create table, create procedure, create trigger,
      create property graph, create job to AGENTMEM;

-- 2. In-database embedding model: a grant only. The model stays in ASKORACLE; nothing here moves or drops it.
grant select on mining model ASKORACLE.ALL_MINILM_L12_V2 to AGENTMEM;

-- 3. Vector and hybrid packages: EXECUTE granted to AGENTMEM only if neither PUBLIC nor AGENTMEM holds it already.
declare
  procedure grant_if_needed(p_pkg varchar2) is
    l_owner dba_objects.owner%type;
    l_n     pls_integer;
  begin
    select count(*), max(owner) into l_n, l_owner from dba_objects
     where object_name = p_pkg and object_type = 'PACKAGE' and owner in ('SYS', 'CTXSYS') and status = 'VALID';
    if l_n <> 1 then
      raise_application_error(-20002, p_pkg || ': expected exactly one VALID package owned by SYS or CTXSYS, found ' ||
                                      l_n || ': ask the operator');
    end if;
    select count(*) into l_n from dba_tab_privs
     where owner = l_owner and table_name = p_pkg and privilege = 'EXECUTE' and grantee in ('PUBLIC', 'AGENTMEM');
    if l_n = 0 then
      execute immediate 'grant execute on ' || dbms_assert.enquote_name(l_owner, false) || '.' ||
                        dbms_assert.enquote_name(p_pkg, false) || ' to AGENTMEM';
      dbms_output.put_line('granted EXECUTE on ' || l_owner || '.' || p_pkg);
    else
      dbms_output.put_line('EXECUTE on ' || l_owner || '.' || p_pkg || ' already available');
    end if;
  end;
begin
  grant_if_needed('DBMS_VECTOR_CHAIN');
  grant_if_needed('DBMS_HYBRID_VECTOR');
end;
/

select privilege from dba_sys_privs where grantee = 'AGENTMEM'
union all
select privilege || ' ON ' || owner || '.' || table_name from dba_tab_privs where grantee = 'AGENTMEM'
order by 1;
