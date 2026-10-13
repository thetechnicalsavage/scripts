-- 01_preflight_readonly.sql v1.0 2026-10-10 - Brief 12 step 0 and every stage boundary (PLAN 5.3, step 0, D13):
--   read-only state of ORCLPDB1 as SYSDBA through tools/sysdba_run.sh. Only SELECTs and the container switch; no
--   DML, no DDL, no grant. Every row starts with PF| and a section name so the runner can parse it.
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

select 'PF|time_utc|' || to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"') from dual;
select 'PF|container|' || sys_context('userenv', 'con_name') from dual;
select 'PF|banner_full|' || replace(banner_full, chr(10), ' / ') from v$version where rownum = 1;
select 'PF|parameter|' || name || '|' || value from v$parameter
 where name in ('compatible', 'vector_memory_size') order by name;

-- vector memory pool (5.3: before and after each stage)
select 'PF|vector_pool|' || pool || '|alloc_bytes=' || alloc_bytes || '|used_bytes=' || used_bytes ||
       '|populate_status=' || populate_status
  from v$vector_memory_pool order by pool;

-- packages and Oracle Text the SDK relies on
select 'PF|package|' || owner || '.' || object_name || '|' || object_type || '|' || status
  from dba_objects
 where object_name in ('DBMS_VECTOR_CHAIN', 'DBMS_HYBRID_VECTOR', 'CTX_DDL', 'DBMS_VECTOR')
   and owner in ('SYS', 'CTXSYS') and object_type in ('PACKAGE', 'PACKAGE BODY')
 order by owner, object_name, object_type;
select 'PF|registry|' || comp_id || '|' || version || '|' || status from dba_registry
 where comp_id in ('CONTEXT', 'CATALOG', 'CATPROC') order by comp_id;

-- the embedding model: present, owner unchanged, ASKORACLE's model count (never changed by this POC)
select 'PF|model|' || owner || '.' || model_name || '|' || mining_function || '|' || algorithm
  from dba_mining_models where owner = 'ASKORACLE' and model_name = 'ALL_MINILM_L12_V2';
select 'PF|askoracle_model_count|' || count(*) from dba_mining_models where owner = 'ASKORACLE';

-- the POC users
select 'PF|user|' || u.n || '|' ||
       nvl((select account_status || '|created=' || to_char(created, 'YYYY-MM-DD HH24:MI:SS') ||
                   '|default_ts=' || default_tablespace
              from dba_users where username = u.n), 'ABSENT')
  from (select 'AGENTMEM' n from dual union all select 'AGENTMEM_RT' from dual) u;
select 'PF|sys_priv|' || grantee || '|' || privilege from dba_sys_privs
 where grantee in ('AGENTMEM', 'AGENTMEM_RT') order by grantee, privilege;
select 'PF|tab_priv|' || grantee || '|' || privilege || '|' || owner || '.' || table_name || '|' || type
  from dba_tab_privs where grantee in ('AGENTMEM', 'AGENTMEM_RT') order by grantee, owner, table_name, privilege;
select 'PF|role_priv|' || grantee || '|' || granted_role from dba_role_privs
 where grantee in ('AGENTMEM', 'AGENTMEM_RT') order by grantee, granted_role;
select 'PF|quota|' || username || '|' || tablespace_name || '|bytes=' || bytes || '|max_bytes=' || max_bytes
  from dba_ts_quotas where username in ('AGENTMEM', 'AGENTMEM_RT') order by username, tablespace_name;

-- AGENTMEM's footprint (5.3): objects by type, segments, recycle bin, graphs, Text/vectorizer preferences, sessions
select 'PF|objects|' || object_type || '|' || count(*) from dba_objects where owner = 'AGENTMEM'
 group by object_type order by object_type;
select 'PF|objects_total|' || count(*) from dba_objects where owner = 'AGENTMEM';
select 'PF|segments|' || segment_type || '|' || count(*) || '|bytes=' || sum(bytes) from dba_segments
 where owner = 'AGENTMEM' group by segment_type order by segment_type;
select 'PF|segments_total_bytes|' || nvl(sum(bytes), 0) from dba_segments where owner = 'AGENTMEM';
select 'PF|recyclebin|' || count(*) from dba_recyclebin where owner = 'AGENTMEM';
select 'PF|property_graphs|' || count(*) from dba_property_graphs where owner = 'AGENTMEM';
select 'PF|ctx_preferences|' || count(*) from ctxsys.ctx_preferences where pre_owner = 'AGENTMEM';
select 'PF|sessions|' || username || '|' || count(*) from v$session
 where username in ('AGENTMEM', 'AGENTMEM_RT') group by username order by username;

-- AGENTMEM's scheduler jobs and their runs (5.3, B13; R3: each purge job runs once at every store creation)
select 'PF|job|' || job_name || '|enabled=' || enabled || '|state=' || state || '|run_count=' || run_count ||
       '|last_start=' || nvl(to_char(sys_extract_utc(last_start_date), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'), '-')
  from dba_scheduler_jobs where owner = 'AGENTMEM' order by job_name;
select 'PF|job_run|' || job_name || '|' || status || '|error=' || error# ||
       '|start=' || to_char(sys_extract_utc(actual_start_date), 'YYYY-MM-DD"T"HH24:MI:SS"Z"') ||
       '|duration=' || run_duration
  from dba_scheduler_job_run_details where owner = 'AGENTMEM' order by log_date;
select 'PF|end|ok' from dual;
