-- 82b_runtime_grants_extra.sql v0.2 2026-10-12 - Brief 12 step 8 (PLAN v0.3.8 11.3, D3): list B, added after list
--   A's test: the three OPS tables the docs' runtime list omits (API_NOTES B30) and SELECT on the property graph in
--   the form the docs' get-started page gives. Run as AGENTMEM by run_all.sh stage S8 (tools/sysdba_run.sh agentmem)
--   after py/80_operations.py rt A. Re-run: safe (grants are idempotent).
--   v0.2: (phase 2, written for the run) run guards as 82a (and the graph present) and an exact read-back: AGENTMEM_RT
--   holds exactly list A plus list B from AGENTMEM (35 privilege rows), none grantable. The grant statements are
--   11.3's v0.1 text unchanged. v0.1: PLAN 11.3 draft.
--   SQL*Plus: no comment follows a semicolon on its line, and no ampersand is used anywhere.
whenever sqlerror exit failure
set serveroutput on feedback on verify off define off

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
  l_n   pls_integer;
begin
  if user <> 'AGENTMEM' then
    raise_application_error(-20183, '82b: run as AGENTMEM, not ' || user || '; nothing granted');
  end if;
  if sys_context('userenv', 'con_name') <> 'ORCLPDB1' then
    raise_application_error(-20183, '82b: expected ORCLPDB1, found ' || sys_context('userenv', 'con_name'));
  end if;
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20183, '82b: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20183, '82b: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  select count(*) into l_n from user_tables
   where table_name in ('OPS_DOCUMENT', 'OPS_MEMORY_LINK', 'OPS_THREAD_SUMMARY');
  if l_n <> 3 then
    raise_application_error(-20184, '82b: ' || l_n || ' of the 3 tables of list B exist: run after step 8 setup');
  end if;
  select count(*) into l_n from user_objects where object_name = 'OPS_MEMORY_GRAPH' and object_type = 'PROPERTY GRAPH';
  if l_n <> 1 then
    raise_application_error(-20184, '82b: the property graph OPS_MEMORY_GRAPH does not exist; nothing granted');
  end if;
  select count(*) into l_n from user_tab_privs_made where grantee = 'AGENTMEM_RT';
  if l_n <> 22 then
    raise_application_error(-20184, '82b: AGENTMEM_RT holds ' || l_n || ' privilege row(s) before list B; '
                                    || 'list A (22) must have been granted and tested first');
  end if;
end;
/

-- List B: the three tables the docs' list omits (B30), and the graph in the form the docs' get-started page gives.
grant select, insert, update, delete on OPS_DOCUMENT to AGENTMEM_RT;
grant select, insert, update, delete on OPS_MEMORY_LINK to AGENTMEM_RT;
grant select, insert, update, delete on OPS_THREAD_SUMMARY to AGENTMEM_RT;
grant select on property graph OPS_MEMORY_GRAPH to AGENTMEM_RT;

-- Read-back: AGENTMEM_RT holds exactly list A plus list B from AGENTMEM, nothing more, nothing grantable.
declare
  l_n pls_integer;
begin
  select count(*) into l_n from user_tab_privs_made p
   where p.grantee = 'AGENTMEM_RT' and p.grantable = 'NO'
     and ((p.privilege = 'SELECT' and p.table_name = 'AGENT_MEMORY_STORES')
          or (p.privilege = 'EXECUTE' and p.table_name = 'DBMS_AGENT_MEMORY_STORE')
          or (p.privilege = 'SELECT' and p.table_name = 'OPS_MEMORY_GRAPH')
          or (p.privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
              and p.table_name in ('OPS_THREAD', 'OPS_ACTOR_PROFILE', 'OPS_MESSAGE', 'OPS_MEMORY',
                                   'OPS_RECORD_CHUNKS', 'OPS_DOCUMENT', 'OPS_MEMORY_LINK', 'OPS_THREAD_SUMMARY')));
  if l_n <> 35 then
    raise_application_error(-20185, '82b: ' || l_n || ' of the 35 privileges of lists A and B read back');
  end if;
  select count(*) into l_n from user_tab_privs_made where grantee = 'AGENTMEM_RT';
  if l_n <> 35 then
    raise_application_error(-20185, '82b: AGENTMEM_RT holds ' || l_n || ' AGENTMEM privilege row(s); A and B have 35');
  end if;
  for r in (select privilege, table_name, grantable from user_tab_privs_made
             where grantee = 'AGENTMEM_RT' order by table_name, privilege) loop
    dbms_output.put_line('GR|AGENTMEM_RT|' || r.privilege || '|' || r.table_name || '|grantable=' || r.grantable);
  end loop;
  dbms_output.put_line('GR|82b|ok');
end;
/
