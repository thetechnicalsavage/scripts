-- 82a_runtime_grants_docs.sql v0.2 2026-10-12 - Brief 12 step 8 (PLAN v0.3.8 11.3, D3): list A, the docs' read/write
--   runtime grant list (troubleshooting, "read/write runtime user"), from AGENTMEM to AGENTMEM_RT on store OPS. Run as
--   AGENTMEM by run_all.sh stage S8 (tools/sysdba_run.sh agentmem) after py/80_operations.py setup has created OPS.
--   A RECREATE of OPS drops its tables and their grants, so a re-run of S8 starts again from the registry and package
--   grants only. Re-run: safe (grants are idempotent).
--   v0.2: (phase 2, written for the run) run guards (user AGENTMEM, container ORCLPDB1, the database windows, OPS's
--   tables and AGENTMEM_RT present, else nothing is granted) and an exact read-back: AGENTMEM_RT holds exactly list A
--   from AGENTMEM (22 privilege rows), none grantable. The grant statements are 11.3's v0.1 text unchanged.
--   v0.1: PLAN 11.3 draft.
--   SQL*Plus: no comment follows a semicolon on its line, and no ampersand is used anywhere.
whenever sqlerror exit failure
set serveroutput on feedback on verify off define off

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
  l_n   pls_integer;
begin
  if user <> 'AGENTMEM' then
    raise_application_error(-20180, '82a: run as AGENTMEM, not ' || user || '; nothing granted');
  end if;
  if sys_context('userenv', 'con_name') <> 'ORCLPDB1' then
    raise_application_error(-20180, '82a: expected ORCLPDB1, found ' || sys_context('userenv', 'con_name'));
  end if;
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20180, '82a: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20180, '82a: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  select count(*) into l_n from user_tables
   where table_name in ('AGENT_MEMORY_STORES', 'OPS_THREAD', 'OPS_ACTOR_PROFILE', 'OPS_MESSAGE', 'OPS_MEMORY',
                        'OPS_RECORD_CHUNKS');
  if l_n <> 6 then
    raise_application_error(-20181, '82a: ' || l_n || ' of the 6 tables of list A exist: run after step 8 setup');
  end if;
  select count(*) into l_n from all_users where username = 'AGENTMEM_RT';
  if l_n <> 1 then
    raise_application_error(-20181, '82a: user AGENTMEM_RT does not exist (11.2); nothing granted');
  end if;
end;
/

-- List A: the docs' read/write runtime list (troubleshooting, "read/write runtime user"), applied first.
grant select on AGENT_MEMORY_STORES to AGENTMEM_RT;
grant execute on DBMS_AGENT_MEMORY_STORE to AGENTMEM_RT;
grant select, insert, update, delete on OPS_THREAD to AGENTMEM_RT;
grant select, insert, update, delete on OPS_ACTOR_PROFILE to AGENTMEM_RT;
grant select, insert, update, delete on OPS_MESSAGE to AGENTMEM_RT;
grant select, insert, update, delete on OPS_MEMORY to AGENTMEM_RT;
grant select, insert, update, delete on OPS_RECORD_CHUNKS to AGENTMEM_RT;

-- Read-back: AGENTMEM_RT holds exactly list A from AGENTMEM, nothing more, nothing grantable.
declare
  l_n pls_integer;
begin
  select count(*) into l_n from user_tab_privs_made p
   where p.grantee = 'AGENTMEM_RT' and p.grantable = 'NO'
     and ((p.privilege = 'SELECT' and p.table_name = 'AGENT_MEMORY_STORES')
          or (p.privilege = 'EXECUTE' and p.table_name = 'DBMS_AGENT_MEMORY_STORE')
          or (p.privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
              and p.table_name in ('OPS_THREAD', 'OPS_ACTOR_PROFILE', 'OPS_MESSAGE', 'OPS_MEMORY',
                                   'OPS_RECORD_CHUNKS')));
  if l_n <> 22 then
    raise_application_error(-20182, '82a: ' || l_n || ' of the 22 privileges of list A read back');
  end if;
  select count(*) into l_n from user_tab_privs_made where grantee = 'AGENTMEM_RT';
  if l_n <> 22 then
    raise_application_error(-20182, '82a: AGENTMEM_RT holds ' || l_n || ' AGENTMEM privilege row(s); list A has 22');
  end if;
  for r in (select privilege, table_name, grantable from user_tab_privs_made
             where grantee = 'AGENTMEM_RT' order by table_name, privilege) loop
    dbms_output.put_line('GR|AGENTMEM_RT|' || r.privilege || '|' || r.table_name || '|grantable=' || r.grantable);
  end loop;
  dbms_output.put_line('GR|82a|ok');
end;
/
