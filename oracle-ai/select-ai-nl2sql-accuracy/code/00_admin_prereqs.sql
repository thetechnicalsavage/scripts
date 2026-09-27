-- v1.2 - NL2SQL accuracy lab: create the lab schema and give it what Select AI needs
--        on a non-Autonomous Oracle AI Database 26ai.
--        v1.2: CREATE SEQUENCE removed (unused); genai_host validated before use.
--        v1.1: display only - wider listing of what the schema holds.
--
-- Run as : SYSDBA (or a DBA account) connected to the PDB.
-- Usage  : @00_admin_prereqs.sql <lab_password> <genai_host>
--            lab_password : letters and digits only (no quote, no space)
--            genai_host   : the OCI Generative AI inference host of your region, e.g.
--                           inference.generativeai.us-phoenix-1.oci.oraclecloud.com
-- Re-run : safe. The user is created if absent, otherwise its password is reset;
--          grants and the host ACE are simply re-applied.
--
-- Nothing in this file is a credential. The password arrives as argument 1 and is
-- used only in CREATE USER / ALTER USER, which Oracle does not keep in the SQL text.

set verify off feedback off serveroutput on
whenever sqlerror exit failure

define lab_pwd    = &1
define genai_host = &2

-- Reject a host that is not a plain DNS name before it reaches the ACL API.
-- (The password is deliberately NOT checked here: any PL/SQL literal holding it would
-- stay in the shared pool. run_all.sh checks it in the shell instead.)
begin
  if not regexp_like('&genai_host', '^[a-z0-9.-]+$') then
    raise_application_error(-20001, 'genai_host must be a DNS name');
  end if;
end;
/

-- 1. The schema. Its own quota on USERS; nothing here needs a dedicated tablespace.
create user if not exists NL2SQL_LAB identified by "&lab_pwd"
  default tablespace USERS temporary tablespace TEMP;
alter user NL2SQL_LAB identified by "&lab_pwd" account unlock;
alter user NL2SQL_LAB quota 256M on USERS;

-- 2. The only object privileges the lab needs: its tables and views. No CREATE PROCEDURE
--    (profiles are created by anonymous blocks) and no CREATE JOB (the vector pipeline
--    creates its own job).
grant create session, create table, create view to NL2SQL_LAB;

-- 3. Select AI. On a non-Autonomous database these packages are owned by the
--    common user C##CLOUD$SERVICE, not SYS. DBMS_CLOUD is needed for the credential.
grant execute on C##CLOUD$SERVICE.DBMS_CLOUD    to NL2SQL_LAB;
grant execute on C##CLOUD$SERVICE.DBMS_CLOUD_AI to NL2SQL_LAB;

-- 4. Outbound HTTPS to the model endpoint - one host, not '*'.
begin
  dbms_network_acl_admin.append_host_ace(
    host => '&genai_host',
    ace  => xs$ace_type(privilege_list => xs$name_list('connect', 'resolve', 'http'),
                        principal_name => 'NL2SQL_LAB',
                        principal_type => xs_acl.ptype_db));
  dbms_output.put_line('host ACE present for NL2SQL_LAB on &genai_host');
end;
/

prompt
prompt What NL2SQL_LAB now holds:
set linesize 150 pagesize 50
column privilege format a22
column object    format a70
select privilege, null object from dba_sys_privs where grantee = 'NL2SQL_LAB'
union all
select privilege, owner || '.' || table_name from dba_tab_privs where grantee = 'NL2SQL_LAB'
union all
select privilege, host from dba_host_aces where principal = 'NL2SQL_LAB'
order by 2 nulls first, 1;
