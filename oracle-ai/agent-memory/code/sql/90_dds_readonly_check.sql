-- 90_dds_readonly_check.sql v0.2 2026-10-12 - Brief 12 step 9 (PLAN v0.3.8 6 row 9, 9): Deep Data Security,
--   read-only. As SYSDBA through tools/sysdba_run.sh (run_all.sh stage S9). It reports whether 23.26.1 has what the
--   SDK's core/deepsec/ code reads and writes: the views SYS.DBA_DATA_ROLES, SYS.DBA_DATA_GRANTS and
--   SYS.DBA_DATA_ROLE_GRANTS (core/deepsec/_queries.pyc:17-67), the ORA_END_USER_CONTEXT expression
--   (_queries.pyc:12), and the system privileges named like DATA ROLE / DATA GRANT / END USER. Only SELECTs, the
--   container switch and one SELECT run through EXECUTE IMMEDIATE inside an exception handler (so an unknown
--   expression is reported, not raised); no DML, no DDL, no grant. Every row starts with DDS| so it can be parsed.
--   v0.2: (first run 18:20Z) `set serveroutput on` moved after the container switch: enabled in CDB$ROOT, the
--   PDB's DBMS_OUTPUT buffer was never read, so the ORA_END_USER_CONTEXT probe line was not printed. Nothing else.
--   v0.1: first version (phase 2, step 9).
--   SQL*Plus: no comment follows a semicolon on its line, and no ampersand is used anywhere.
whenever sqlerror exit failure
set verify off echo off feedback off heading off pagesize 0 linesize 400 trimspool on termout on
alter session set container = ORCLPDB1;
set serveroutput on size unlimited format wrapped
begin
  if sys_context('userenv', 'con_name') <> 'ORCLPDB1' then
    raise_application_error(-20001, 'expected ORCLPDB1, found ' || sys_context('userenv', 'con_name'));
  end if;
end;
/

select 'DDS|time_utc|' || to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"') from dual;
select 'DDS|banner_full|' || replace(banner_full, chr(10), ' / ') from v$version where rownum = 1;
select 'DDS|version_full|' || version_full from v$instance;
select 'DDS|compatible|' || value from v$parameter where name = 'compatible';

-- the three dictionary views the SDK queries, and the end-user context name, in any schema and as any object type
select 'DDS|object|' || n.column_value || '|' ||
       nvl((select listagg(o.owner || '.' || o.object_type || '.' || o.status, ',')
                     within group (order by o.owner, o.object_type)
              from dba_objects o where o.object_name = n.column_value), 'absent')
  from table(sys.odcivarchar2list('DBA_DATA_ROLES', 'DBA_DATA_GRANTS', 'DBA_DATA_ROLE_GRANTS',
                                  'ORA_END_USER_CONTEXT')) n
 order by n.column_value;

-- related dictionary names on this release (context for the reader: what does exist)
select 'DDS|related_object|' || owner || '.' || object_name || '|' || object_type
  from dba_objects
 where owner = 'SYS' and object_type in ('VIEW', 'TABLE')
   and (object_name like 'DBA\_DATA\_%' escape '\' or object_name like '%END\_USER%' escape '\')
 order by object_name;

-- system privileges a Deep Data Security release adds (data roles, data grants, end users)
select 'DDS|system_privilege|' || name from system_privilege_map
 where name like '%DATA ROLE%' or name like '%DATA GRANT%' or name like '%END USER%'
 order by name;
select 'DDS|system_privilege_count|' || count(*) from system_privilege_map
 where name like '%DATA ROLE%' or name like '%DATA GRANT%' or name like '%END USER%';

-- the SDK's own end-user query (core/deepsec/_queries.pyc:12), run read-only; any error is printed, not raised
declare
  l_v varchar2(4000);
begin
  execute immediate 'select ORA_END_USER_CONTEXT.username from dual' into l_v;
  dbms_output.put_line('DDS|ora_end_user_context_query|ok|' || nvl(l_v, '(null)'));
exception
  when others then
    dbms_output.put_line('DDS|ora_end_user_context_query|error|' || replace(sqlerrm, chr(10), ' / '));
end;
/

select 'DDS|end|ok' from dual;
