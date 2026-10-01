-- v1.5 - RAG tuning lab (brief 09): create the RAG_LAB schema with the least privileges
--        Select AI RAG needs on a non-Autonomous Oracle AI Database 26ai (23.26.1).
--        v1.5: public copy: the other app's references renamed to the existing HR app (text only).
--        v1.4: no ACCEPT ... HIDE (empty under docker exec -i): the caller defines lab_pwd before @.
--        v1.3: the ACE guard also passes when ASKORACLE uses a wildcard ACL but an ACL for the exact host already
--              exists (brief 08's NL2SQL_LAB, 27-Sep): the append then creates no ACL, so precedence is unchanged.
--              It still stops when the append would create the first exact-host ACL (GO-1, 29-Sep).
--              The idempotence count of RAG_LAB's own ACEs counts GRANT entries only (Codex).
--        v1.2: Codex adversarial review (A1): the argument and the password are checked at SQL*Plus
--              level before any PL/SQL or DDL sees them, each by a query that holds the value only
--              in a q'{...}' literal (allowed: letters, digits, _ . : - for the host; 12 to 64
--              letters and digits for the password); the password check's cursor is purged from
--              the shared pool right after it runs.
--        v1.1: VM-safety review: DBMS_VECTOR_CHAIN's owner is read from DBA_OBJECTS (CTXSYS on
--              this estate, not SYS) and granted through DBMS_ASSERT; the host ACE is added only
--              when ASKORACLE already holds connect, resolve and http on that exact host (so no
--              new host ACL can change the existing HR app's ACL precedence); termout is off while the
--              password lines run, and only a fixed line is printed.
--        v1.0: first version, PLAN.md v1.2 section 2.2 (password by hidden prompt, not argument).
--
-- Run as : SYSDBA (or a DBA account), connected to the PDB, not CDB$ROOT.
-- Usage  : @00_admin_prereqs.sql <genai_host>
--            genai_host : inference.generativeai.<region>.oci.oraclecloud.com
--          The script then asks "RAG_LAB password:" with the input hidden. run_all.sh answers
--          that prompt on stdin. The password is never an argument, so it never reaches the
--          shell history or a process list. Hand runs: the host may hold only letters, digits
--          and _ . : - and the password only 12 to 64 letters and digits; anything else stops
--          the script before a statement uses it (README_RUN.md, quoting risk).
-- Re-run : safe. The user is created if absent, otherwise its password is reset and the
--          account unlocked. Grants are re-applied (a no-op when held). The host ACE is added
--          only when one of its three privileges is missing.
--
-- Granted: CREATE SESSION, CREATE TABLE (the index's vector table lives in RAG_LAB),
--          QUOTA 3G ON USERS, EXECUTE on C##CLOUD$SERVICE.DBMS_CLOUD / DBMS_CLOUD_AI /
--          DBMS_CLOUD_PIPELINE, SYS.DBMS_VECTOR and DBMS_VECTOR_CHAIN (owner looked up: CTXSYS
--          on this estate), one host ACE (connect, resolve, http) on the GenAI inference host.
-- Not granted, on purpose: DBA, CREATE JOB, CREATE PROCEDURE, a wallet ACE, a '*' host,
--          anything on ASKORACLE. The models are granted by their owner in 03_load_models.sql,
--          the directories by 00b_directories.sql.
-- Refuses: an argument or password outside its allowed characters (before anything runs); the
--          root container; a host that is not the regional GenAI inference host; a host on
--          which ASKORACLE (the existing HR app) does not already hold connect, resolve and http directly.
--          In that last case the existing HR app reaches the host through a wildcard or domain ACL, and a
--          new exact-host ACL for RAG_LAB would take precedence over it for that host.

set verify off echo off feedback off termout on
set serveroutput on size unlimited format wrapped
whenever sqlerror exit failure
whenever oserror exit failure

define genai_host = '&1'

-- Argument guard (Codex A1). SQL*Plus splices a value into the script text before the database
-- sees it, so this query is the first statement to hold it, and only inside a q'{...}' literal:
-- a value from the allowed set (written out below, no ranges, so no NLS_SORT can widen it) has
-- no brace and no quote and cannot end that literal. Output is off while it runs, so a
-- rejected value is never echoed; the block after it stops the script unless the verdict is
-- exactly YES. The PL/SQL below still checks the precise host format.
define rag_arg_re = '^[0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_.:-]{1,200}$'
define rag_args_ok = 'NO'
column rag_args_ok new_value rag_args_ok noprint
prompt checking the argument (allowed: letters, digits and _ . : -)
set termout off
select case when regexp_like(q'{&genai_host}', '&rag_arg_re', 'c')
            then 'YES' else 'NO' end rag_args_ok
  from dual;
set termout on
column rag_args_ok clear
begin
  if '&rag_args_ok' = 'YES' then
    dbms_output.put_line('argument: plain characters only');
  else
    raise_application_error(-20000, 'the argument may hold only letters, digits and _ . : - ' ||
      '(README_RUN.md, quoting risk in hand runs); nothing was changed');
  end if;
end;
/

-- Refuse the root container, anything but the regional GenAI inference host, and a host for which
-- the append would create the first exact-host ACL while the existing HR app relies on a wildcard/domain ACL,
-- before any change is made.
declare
  l_app pls_integer;
  l_acl pls_integer;
begin
  if sys_context('userenv', 'con_name') = 'CDB$ROOT' then
    raise_application_error(-20001, 'connect to the PDB, not CDB$ROOT');
  end if;
  if not regexp_like('&genai_host', '^inference\.generativeai\.[a-z0-9-]+\.oci\.oraclecloud\.com$') then
    raise_application_error(-20002,
      'genai_host must look like inference.generativeai.<region>.oci.oraclecloud.com');
  end if;
  select count(distinct upper(privilege)) into l_app
    from dba_host_aces
   where lower(host) = lower('&genai_host')
     and principal = 'ASKORACLE'
     and grant_type = 'GRANT'
     and upper(privilege) in ('CONNECT', 'RESOLVE', 'HTTP');
  if l_app < 3 then
    -- ASKORACLE reaches the host through a wildcard or domain ACL. Appending RAG_LAB's ACE is safe
    -- only if an ACL for this exact host (no ports) already exists: APPEND_HOST_ACE then adds to that
    -- ACL and creates none, so the precedence the existing HR app already lives with is unchanged. Without one,
    -- the append would CREATE an exact-host ACL, which is the case the operator must decide.
    select count(*) into l_acl
      from dba_host_acls
     where lower(host) = lower('&genai_host') and lower_port is null and upper_port is null;
    if l_acl = 0 then
      raise_application_error(-20003, 'ASKORACLE holds ' || l_app || ' of connect/resolve/http on this exact host ' ||
        'and no ACL exists for it yet. The existing HR app reaches it through a wildcard or domain ACL, and a new host ACL ' ||
        'for RAG_LAB could change its ACL precedence. Stopping: ask the operator.');
    end if;
    dbms_output.put_line('ASKORACLE reaches the GenAI host through a wildcard/domain ACL, and an ACL for this exact ' ||
      'host already exists (another lab''s ACEs): RAG_LAB''s ACE is appended to it, no ACL is created, so the existing HR app''s ' ||
      'ACL precedence is unchanged');
  else
    dbms_output.put_line('ASKORACLE already holds connect, resolve and http on the GenAI host (exact-host ACE)');
  end if;
end;
/

-- lab_pwd is defined by the caller before @: run_all.sh sends define lab_pwd = "..." on SQL*Plus's
-- stdin (never argv). ACCEPT ... HIDE is not used here: without a terminal (docker exec -i) it reads
-- nothing (checked 29-Sep). A hand run in a terminal types, before @00:
--   accept lab_pwd char prompt 'RAG_LAB password: ' hide

-- Password guard (Codex A1): checked like the argument before any statement uses it, 12 to 64
-- letters and digits (run_all.sh applies the same rule to the secrets file). Output is off, so
-- the value is never echoed. The query is marked so that the block after it can purge its
-- cursor from the shared pool, where the literal would otherwise stay until it ages out.
prompt checking the RAG_LAB password characters (output hidden on purpose)
set termout off
define rag_secret_ok = 'NO'
column rag_secret_ok new_value rag_secret_ok noprint
select /*rag_lab_secret_guard*/ case when regexp_like(q'{&lab_pwd}', '^[0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz]{12,64}$', 'c')
            then 'YES' else 'NO' end rag_secret_ok
  from dual;
set termout on
column rag_secret_ok clear

-- purge the check's cursor (it is the lab's own statement; nothing else is touched). A DBA account
-- without EXECUTE on DBMS_SHARED_POOL gets a note instead; the cursor then ages out on its own.
declare
  c      sys_refcursor;
  l_addr varchar2(64);
  l_hash number;
  l_n    pls_integer := 0;
begin
  open c for 'select rawtohex(address), hash_value from v$sqlarea where sql_text like :m'
    using 'select /*rag_lab_secret_guard*/%';
  loop
    fetch c into l_addr, l_hash;
    exit when c%notfound;
    execute immediate 'begin sys.dbms_shared_pool.purge(:n, ''C''); end;' using l_addr || ',' || l_hash;
    l_n := l_n + 1;
  end loop;
  close c;
  dbms_output.put_line('password check: ' || l_n || ' cursor(s) of it purged from the shared pool');
exception
  when others then
    if c%isopen then close c; end if;
    dbms_output.put_line('NOTE: the password check could not be purged from the shared pool (ORA' ||
      to_char(sqlcode, 'fm00000') || '); it ages out on its own');
end;
/

begin
  if '&rag_secret_ok' = 'YES' then
    dbms_output.put_line('RAG_LAB password: 12 to 64 letters and digits');
  else
    raise_application_error(-20005, 'the RAG_LAB password must be 12 to 64 letters and digits; nothing was changed');
  end if;
end;
/

-- 1. The schema. USERS only; the vector tables stay under 60 MB (PLAN.md 4.4).
--    termout off: an error on these two lines would otherwise print the statement with the
--    password substituted into it. Only the fixed lines below reach the log.
prompt creating RAG_LAB or resetting its password (statement output hidden on purpose)
set termout off
create user if not exists RAG_LAB identified by "&lab_pwd"
  default tablespace USERS temporary tablespace TEMP;
alter user RAG_LAB identified by "&lab_pwd" account unlock;
set termout on
undefine lab_pwd
prompt RAG_LAB user: OK
alter user RAG_LAB quota 3G on USERS;

-- 2. System privileges: sessions, and the tables CREATE_VECTOR_INDEX makes in the owner's schema.
grant create session, create table to RAG_LAB;

-- 3. Select AI and the vector packages. On a non-Autonomous database the cloud packages are
--    owned by the common user C##CLOUD$SERVICE, not SYS. DBMS_VECTOR_CHAIN belongs to CTXSYS
--    on this estate (a SYS-qualified grant fails with ORA-04042), so its owner is looked up.
grant execute on C##CLOUD$SERVICE.DBMS_CLOUD          to RAG_LAB;
grant execute on C##CLOUD$SERVICE.DBMS_CLOUD_AI       to RAG_LAB;
grant execute on C##CLOUD$SERVICE.DBMS_CLOUD_PIPELINE to RAG_LAB;
grant execute on SYS.DBMS_VECTOR                      to RAG_LAB;

declare
  l_owner varchar2(128);
  l_n     pls_integer;
begin
  select count(*) into l_n
    from dba_objects
   where object_name = 'DBMS_VECTOR_CHAIN' and object_type = 'PACKAGE' and owner in ('CTXSYS', 'SYS');
  if l_n <> 1 then
    raise_application_error(-20004, 'expected one DBMS_VECTOR_CHAIN package owned by CTXSYS or SYS, found ' || l_n);
  end if;
  select owner into l_owner
    from dba_objects
   where object_name = 'DBMS_VECTOR_CHAIN' and object_type = 'PACKAGE' and owner in ('CTXSYS', 'SYS');
  execute immediate 'grant execute on ' || dbms_assert.enquote_name(l_owner, false) || '.' ||
                    dbms_assert.enquote_name('DBMS_VECTOR_CHAIN', false) || ' to RAG_LAB';
  dbms_output.put_line('granted EXECUTE on ' || l_owner || '.DBMS_VECTOR_CHAIN');
end;
/

-- 4. Outbound HTTPS to the chat model: one host, not '*'. Embeddings stay in the database.
--    ASKORACLE's exact-host ACE was confirmed above, so this ACE joins that host's ACL.
declare
  l_have pls_integer;
begin
  select count(distinct upper(privilege)) into l_have
    from dba_host_aces
   where lower(host) = lower('&genai_host')
     and principal = 'RAG_LAB'
     and grant_type = 'GRANT'
     and upper(privilege) in ('CONNECT', 'RESOLVE', 'HTTP');
  if l_have < 3 then
    dbms_network_acl_admin.append_host_ace(
      host => '&genai_host',
      ace  => xs$ace_type(privilege_list => xs$name_list('connect', 'resolve', 'http'),
                          principal_name => 'RAG_LAB',
                          principal_type => xs_acl.ptype_db));
    dbms_output.put_line('host ACE added for RAG_LAB');
  else
    dbms_output.put_line('host ACE already present for RAG_LAB');
  end if;
end;
/

prompt
prompt What RAG_LAB now holds (region masked):
set linesize 160 pagesize 100 heading on
column privilege format a24
column object    format a80
select privilege, cast(null as varchar2(200)) object
  from dba_sys_privs where grantee = 'RAG_LAB'
union all
select privilege, owner || '.' || table_name
  from dba_tab_privs where grantee = 'RAG_LAB'
union all
select 'ACE ' || upper(privilege),
       regexp_replace(host, '^inference\.generativeai\.[^.]+\.', 'inference.generativeai.<region>.')
  from dba_host_aces where principal = 'RAG_LAB'
union all
select 'QUOTA', tablespace_name || ' ' ||
       case when max_bytes = -1 then 'UNLIMITED' else to_char(round(max_bytes / power(1024, 3), 2)) || ' GB' end
  from dba_ts_quotas where username = 'RAG_LAB'
order by 2 nulls first, 1;
