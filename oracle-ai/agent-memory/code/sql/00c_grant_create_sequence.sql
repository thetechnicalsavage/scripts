-- 00c_grant_create_sequence.sql v0.3 2026-10-10 - Brief 12 day 1 (PLAN v0.3.3, 11.1, D1 as amended by the plan owner
--   on 10-Oct): CREATE SEQUENCE for AGENTMEM, and nothing else. Oracle requires it to create a table with an identity
--   column even in one's own schema; the SDK's managed tables have identity columns (pkb:3481 and four more), and
--   step 1 failed with ORA-01031 at DEMO_ACTOR_PROFILE (results/s1/s1_failure_diagnosis.json). PLAN 11.1 v0.3 assumed
--   CREATE TABLE covers them; that was wrong.
--   Run as SYSDBA (OS authentication) inside the database container through tools/sysdba_run.sh v1.3, which first checks
--   that AGENTMEM exists, was created by this POC and logs in with the stored password. No password is used here.
--   Guards: the container must be ORCLPDB1; AGENTMEM must exist and hold exactly D1's six privileges (or the six plus
--   CREATE SEQUENCE, on a re-run), none with ADMIN OPTION, and no role; after the grant it must hold exactly the seven.
--   Objects: every object AGENTMEM owns must be listed in AGENTMEM.POC_MANIFEST (the manifest's own table and index
--   excepted, the same rule as 99_rollback.sql's guard) and AGENTMEM_RT must own none, or nothing is granted; the counts
--   are printed (G|objects|...) so the amendment can prove that the object delta since PRE is the POC's own stores.
--   Re-run: safe (granting a privilege already held changes nothing; every check only reads).
--   No comment may follow a semicolon on its line, and no ampersand appears anywhere (SQL*Plus).
--   v0.3: (Codex adversarial review r2, finding 1) the pre-grant guard requires the exact D1 set: a missing D1
--   privilege now stops the script before the grant (it used to stop only after it, at -20034).
--   v0.2: (Codex adversarial review r1, finding 1) the object guard and the G|objects lines; the amendment tool
--   compares them with the 02 read-back.
--   v0.1: first version.
whenever sqlerror exit failure
whenever oserror exit failure
set verify off echo off feedback on heading off pagesize 0 linesize 400 trimspool on termout on

alter session set container = ORCLPDB1;
-- SERVEROUTPUT after the container switch: the PDB session needs its own output buffer (00 printed nothing).
set serveroutput on
begin
  if sys_context('userenv', 'con_name') <> 'ORCLPDB1' then
    raise_application_error(-20001, 'expected ORCLPDB1, found ' || sys_context('userenv', 'con_name'));
  end if;
end;
/

-- 1. Preconditions (read-only).
declare
  l_n pls_integer;
begin
  select count(*) into l_n from dba_users where username = 'AGENTMEM';
  if l_n <> 1 then
    raise_application_error(-20031, 'AGENTMEM does not exist: nothing granted');
  end if;
  select count(*) into l_n from dba_sys_privs
   where grantee = 'AGENTMEM'
     and (admin_option <> 'NO'
          or privilege not in ('CREATE SESSION', 'CREATE TABLE', 'CREATE PROCEDURE', 'CREATE TRIGGER',
                               'CREATE PROPERTY GRAPH', 'CREATE JOB', 'CREATE SEQUENCE'));
  if l_n > 0 then
    raise_application_error(-20032, 'AGENTMEM holds ' || l_n ||
      ' system privilege(s) outside D1 or with ADMIN OPTION: nothing granted; ask the operator');
  end if;
  -- exactly the six D1 privileges, or the six plus CREATE SEQUENCE (a re-run): every one of the six must be present
  select count(*) into l_n from dba_sys_privs
   where grantee = 'AGENTMEM' and admin_option = 'NO'
     and privilege in ('CREATE SESSION', 'CREATE TABLE', 'CREATE PROCEDURE', 'CREATE TRIGGER',
                       'CREATE PROPERTY GRAPH', 'CREATE JOB');
  if l_n <> 6 then
    raise_application_error(-20036, 'AGENTMEM holds ' || l_n ||
      ' of the six D1 privileges, expected all six: nothing granted; ask the operator');
  end if;
  select count(*) into l_n from dba_role_privs where grantee = 'AGENTMEM';
  if l_n > 0 then
    raise_application_error(-20033, 'AGENTMEM holds ' || l_n || ' role(s): nothing granted; ask the operator');
  end if;
  dbms_output.put_line('preconditions ok: AGENTMEM exists, exactly the D1 privileges, no role');
end;
/

-- 1b. Objects (read-only): every AGENTMEM object is listed in POC_MANIFEST, AGENTMEM_RT owns nothing. The manifest is
--     referenced through dynamic SQL, so the block compiles when it does not exist (then AGENTMEM must own nothing).
declare
  l_all      pls_integer;
  l_unlisted pls_integer;
  l_rt       pls_integer;
  l_man      pls_integer;
begin
  select count(*) into l_all from dba_objects where owner = 'AGENTMEM';
  select count(*) into l_rt from dba_objects where owner = 'AGENTMEM_RT';
  select count(*) into l_man from dba_tables where owner = 'AGENTMEM' and table_name = 'POC_MANIFEST';
  if l_man = 0 then
    l_unlisted := l_all;
  else
    execute immediate q'[
      select count(*) from dba_objects o
       where o.owner = 'AGENTMEM'
         and not (o.object_name = 'POC_MANIFEST' and o.object_type = 'TABLE')
         and not (o.object_name = 'POC_MANIFEST_PK' and o.object_type = 'INDEX')
         and not exists (select 1 from agentmem.poc_manifest m
                          where m.object_name = o.object_name and m.object_type = o.object_type)]' into l_unlisted;
  end if;
  if l_unlisted > 0 or l_rt > 0 then
    raise_application_error(-20035, 'AGENTMEM owns ' || l_unlisted || ' object(s) not in POC_MANIFEST, AGENTMEM_RT owns ' ||
      l_rt || ': nothing granted; ask the operator');
  end if;
  dbms_output.put_line('objects ok: AGENTMEM ' || l_all || ', all listed in POC_MANIFEST; AGENTMEM_RT none');
end;
/
select 'G|objects|AGENTMEM|' || count(*) from dba_objects where owner = 'AGENTMEM';
select 'G|objects|AGENTMEM_RT|' || count(*) from dba_objects where owner = 'AGENTMEM_RT';

-- 2. The one grant (D1 as amended, PLAN v0.3.3). No ADMIN OPTION.
grant create sequence to AGENTMEM;

-- 3. Read back and assert: exactly the seven D1 privileges, none with ADMIN OPTION, no role.
select 'G|sys_priv|' || privilege || '|' || admin_option from dba_sys_privs where grantee = 'AGENTMEM'
 order by privilege;
declare
  l_all  pls_integer;
  l_d1   pls_integer;
  l_seq  pls_integer;
  l_role pls_integer;
begin
  select count(*) into l_all from dba_sys_privs where grantee = 'AGENTMEM';
  select count(*) into l_d1 from dba_sys_privs
   where grantee = 'AGENTMEM' and admin_option = 'NO'
     and privilege in ('CREATE SESSION', 'CREATE TABLE', 'CREATE PROCEDURE', 'CREATE TRIGGER',
                       'CREATE PROPERTY GRAPH', 'CREATE JOB', 'CREATE SEQUENCE');
  select count(*) into l_seq from dba_sys_privs
   where grantee = 'AGENTMEM' and privilege = 'CREATE SEQUENCE' and admin_option = 'NO';
  select count(*) into l_role from dba_role_privs where grantee = 'AGENTMEM';
  if l_all <> 7 or l_d1 <> 7 or l_seq <> 1 or l_role <> 0 then
    raise_application_error(-20034, 'AGENTMEM privileges after the grant are not exactly D1 as amended (all ' ||
      l_all || ', D1 ' || l_d1 || ', sequence ' || l_seq || ', roles ' || l_role || '): ask the operator');
  end if;
  dbms_output.put_line('verified: AGENTMEM holds exactly the seven D1 privileges, no ADMIN OPTION, no role');
end;
/
select 'G|end|ok' from dual;
