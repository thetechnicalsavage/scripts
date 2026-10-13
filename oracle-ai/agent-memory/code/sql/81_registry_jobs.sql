-- 81_registry_jobs.sql v0.1 2026-10-12 - Brief 12 step 8 (PLAN v0.3.8 6 row 8 (c) (d), shot 26): read-only report as
--   AGENTMEM, run by run_all.sh stage S8 (tools/sysdba_run.sh agentmem) twice: after py/80_operations.py setup (the
--   expired memory still stored, the purge jobs disabled) and at the end of the stage (after OPS's own purge job ran
--   once). It prints the registry rows, OPS's two purge jobs and their runs, the OPS memories that carry an expiry, and
--   the AGENTMEM privileges AGENTMEM_RT holds. Only SELECTs; no DML, no DDL, no grant. Every row starts with RJ|.
--   v0.1: first version (phase 2, step 8).
--   SQL*Plus: no comment follows a semicolon on its line, and no ampersand is used anywhere.
whenever sqlerror exit failure
set verify off echo off feedback off heading off pagesize 0 linesize 400 trimspool on termout on define off

begin
  if user <> 'AGENTMEM' then
    raise_application_error(-20186, '81: run as AGENTMEM, not ' || user);
  end if;
  if sys_context('userenv', 'con_name') <> 'ORCLPDB1' then
    raise_application_error(-20186, '81: expected ORCLPDB1, found ' || sys_context('userenv', 'con_name'));
  end if;
end;
/

select 'RJ|time_utc|' || to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"') from dual;

-- (c) the registry: one row per store
select 'RJ|registry|' || memory_store_name || '|' || lifecycle_state || '|schema_version=' || schema_version
  from AGENT_MEMORY_STORES order by memory_store_name;
select 'RJ|registry_ops_config|' ||
       replace(dbms_lob.substr(store_config, 3000, 1), chr(10), ' ')
  from AGENT_MEMORY_STORES where memory_store_name = 'OPS';

-- (d) OPS's two purge jobs and every run they made
select 'RJ|job|' || job_name || '|enabled=' || enabled || '|state=' || state || '|run_count=' || run_count ||
       '|failure_count=' || failure_count || '|repeat=' || repeat_interval
  from user_scheduler_jobs where job_name in ('OPS_PURGE_EXPIRED_RECORDS_J', 'OPS_PURGE_ORPHANED_CHUNKS_J')
 order by job_name;
select 'RJ|job_run|' || job_name || '|' || status || '|error=' || error# || '|start_utc=' ||
       to_char(sys_extract_utc(actual_start_date), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
  from user_scheduler_job_run_details
 where job_name in ('OPS_PURGE_EXPIRED_RECORDS_J', 'OPS_PURGE_ORPHANED_CHUNKS_J')
 order by log_date, job_name;

-- (d) OPS memories that carry an expiry, and their chunks
select 'RJ|expiring_memory|' || m.record_id || '|' || m.status || '|expires_at_utc=' ||
       to_char(sys_extract_utc(m.expires_at), 'YYYY-MM-DD"T"HH24:MI:SS"Z"') || '|expired=' ||
       case when m.expires_at <= systimestamp then 'yes' else 'no' end || '|chunks=' ||
       (select count(*) from OPS_RECORD_CHUNKS c where c.source_id = m.record_id)
  from OPS_MEMORY m where m.expires_at is not null order by m.record_id;
select 'RJ|expiring_memory_count|' || count(*) from OPS_MEMORY where expires_at is not null;
select 'RJ|ops_memory_count|' || count(*) from OPS_MEMORY;

-- (b) what AGENTMEM_RT holds from AGENTMEM at this point
select 'RJ|rt_privilege|' || privilege || '|' || table_name || '|grantable=' || grantable
  from user_tab_privs_made where grantee = 'AGENTMEM_RT' order by table_name, privilege;
select 'RJ|rt_privilege_count|' || count(*) from user_tab_privs_made where grantee = 'AGENTMEM_RT';

select 'RJ|end|ok' from dual;
