-- 00d_grant_create_view.sql v0.1 2026-10-10 - Brief 12 phase 2 (PLAN v0.3.5, 11.1, D1 as amended a second time by the
--   plan owner on 10-Oct): CREATE VIEW for AGENTMEM, and nothing else. Step 4's creation of the HYBRID store RV_HYB
--   failed inside Oracle Text: ORA-29855 (ODCIINDEXCREATE for RV_HYB_RECORD_CHUNKS_CONTENT_HVX_I) / DRG-50857 (oracle
--   error in drvxtab.create_index_tables) / ORA-01031 (results/s4/diag_1844Z/diag_s4_repeat.json). A hybrid vector
--   index creates a <index>$VECTORS view in the index owner's schema; Oracle's Agent Memory run-locally page grants
--   CREATE VIEW to the owner; 00 v0.2 had dropped it ("the SDK creates no view"). Oracle does not name the privilege,
--   so this grant is a tested hypothesis: step 4 re-runs and its result says whether it was the one.
--   Run as SYSDBA (OS authentication) inside the database container through tools/sysdba_run.sh v1.9, which first checks
--   that AGENTMEM exists, was created by this POC and logs in with the stored password. No password is used here.
--   Guards: the container must be ORCLPDB1; AGENTMEM must exist and hold exactly the seven privileges of D1 as amended
--   by 00c (or the seven plus CREATE VIEW, on a re-run), none with ADMIN OPTION, and no role; after the grant it must
--   hold exactly the eight.
--   Objects: every object AGENTMEM owns must be listed in AGENTMEM.POC_MANIFEST (the manifest's own table and index
--   excepted, the same rule as 99_rollback.sql's guard) and AGENTMEM_RT must own none, or nothing is granted; the counts
--   are printed (G|objects|...) so the amendment can prove that the object delta is the POC's own stores.
--   Re-run: safe (granting a privilege already held changes nothing; every check only reads).
--   No comment may follow a semicolon on its line, and no ampersand appears anywhere (SQL*Plus).
--   v0.1: first version (from 00c v0.3, the same guards with the seven-privilege base).
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
    raise_application_error(-20041, 'AGENTMEM does not exist: nothing granted');
  end if;
  select count(*) into l_n from dba_sys_privs
   where grantee = 'AGENTMEM'
     and (admin_option <> 'NO'
          or privilege not in ('CREATE SESSION', 'CREATE TABLE', 'CREATE PROCEDURE', 'CREATE TRIGGER',
                               'CREATE PROPERTY GRAPH', 'CREATE JOB', 'CREATE SEQUENCE', 'CREATE VIEW'));
  if l_n > 0 then
    raise_application_error(-20042, 'AGENTMEM holds ' || l_n ||
      ' system privilege(s) outside D1 as amended or with ADMIN OPTION: nothing granted; ask the operator');
  end if;
  -- exactly the seven privileges of D1 as amended by 00c, or the seven plus CREATE VIEW (a re-run): all seven present
  select count(*) into l_n from dba_sys_privs
   where grantee = 'AGENTMEM' and admin_option = 'NO'
     and privilege in ('CREATE SESSION', 'CREATE TABLE', 'CREATE PROCEDURE', 'CREATE TRIGGER',
                       'CREATE PROPERTY GRAPH', 'CREATE JOB', 'CREATE SEQUENCE');
  if l_n <> 7 then
    raise_application_error(-20046, 'AGENTMEM holds ' || l_n ||
      ' of the seven privileges of D1 as amended by 00c, expected all seven: nothing granted; ask the operator');
  end if;
  select count(*) into l_n from dba_role_privs where grantee = 'AGENTMEM';
  if l_n > 0 then
    raise_application_error(-20043, 'AGENTMEM holds ' || l_n || ' role(s): nothing granted; ask the operator');
  end if;
  dbms_output.put_line('preconditions ok: AGENTMEM exists, exactly the D1 privileges as amended by 00c, no role');
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
    raise_application_error(-20045, 'AGENTMEM owns ' || l_unlisted || ' object(s) not in POC_MANIFEST, AGENTMEM_RT owns ' ||
      l_rt || ': nothing granted; ask the operator');
  end if;
  dbms_output.put_line('objects ok: AGENTMEM ' || l_all || ', all listed in POC_MANIFEST; AGENTMEM_RT none');
end;
/
select 'G|objects|AGENTMEM|' || count(*) from dba_objects where owner = 'AGENTMEM';
select 'G|objects|AGENTMEM_RT|' || count(*) from dba_objects where owner = 'AGENTMEM_RT';

-- 2. The one grant (D1 as amended a second time, PLAN v0.3.5). No ADMIN OPTION.
grant create view to AGENTMEM;

-- 3. Read back and assert: exactly the eight privileges, none with ADMIN OPTION, no role.
select 'G|sys_priv|' || privilege || '|' || admin_option from dba_sys_privs where grantee = 'AGENTMEM'
 order by privilege;
declare
  l_all  pls_integer;
  l_d1   pls_integer;
  l_view pls_integer;
  l_role pls_integer;
begin
  select count(*) into l_all from dba_sys_privs where grantee = 'AGENTMEM';
  select count(*) into l_d1 from dba_sys_privs
   where grantee = 'AGENTMEM' and admin_option = 'NO'
     and privilege in ('CREATE SESSION', 'CREATE TABLE', 'CREATE PROCEDURE', 'CREATE TRIGGER',
                       'CREATE PROPERTY GRAPH', 'CREATE JOB', 'CREATE SEQUENCE', 'CREATE VIEW');
  select count(*) into l_view from dba_sys_privs
   where grantee = 'AGENTMEM' and privilege = 'CREATE VIEW' and admin_option = 'NO';
  select count(*) into l_role from dba_role_privs where grantee = 'AGENTMEM';
  if l_all <> 8 or l_d1 <> 8 or l_view <> 1 or l_role <> 0 then
    raise_application_error(-20044, 'AGENTMEM privileges after the grant are not exactly D1 as amended (all ' ||
      l_all || ', D1 ' || l_d1 || ', view ' || l_view || ', roles ' || l_role || '): ask the operator');
  end if;
  dbms_output.put_line('verified: AGENTMEM holds exactly the eight D1 privileges, no ADMIN OPTION, no role');
end;
/
select 'G|end|ok' from dual;
