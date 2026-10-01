-- v1.2 - RAG tuning lab (brief 09): remove what the lab created. Never an embedding model:
--        the models stay in ASKORACLE for good (PLAN.md 0.3).
--        v1.2: public copy: the lab-internal isolation snapshot after cleanup removed (comment only).
--        v1.1: Codex adversarial review: the two arguments are checked at SQL*Plus level before any
--              PL/SQL sees them (A1). Phase lab refuses before any drop when RAG_LAB owns a profile,
--              pipeline or credential not named RAG_, and removes the status tables its RAG_
--              pipelines leave behind. Phase admin refuses before any change when RAG_LAB owns an
--              object that is not a lab object, or holds a host ACE that is not one of the three
--              00 appended on the GenAI host (A6); it lists what it found and changes nothing.
--              It revokes SELECT ON MINING MODEL on ASKORACLE's models from RAG_LAB and READ on
--              RAG_MODEL_DIR from ASKORACLE (A5), and never drops a model. Codex re-review: the
--              host ACE removal is the first change, so if it fails nothing else has changed.
--        v1.0: first version, PLAN.md v1.2 section 7 (99) and review item 23.
--
-- Run as : two phases, because the cloud APIs act on the connected user's own objects.
--            phase lab    as RAG_LAB : refuse unless every profile, pipeline and credential is
--                                      named RAG_; DROP_VECTOR_INDEX (include_data) of each RAG_
--                                      index, check USER_CLOUD_PIPELINES is empty, drop the
--                                      status tables those pipelines left, the RAG_ profiles and
--                                      the credential RAG_LAB_OCI_CRED
--            phase admin  as SYSDBA  : stop unless RAG_LAB's cloud objects are gone, every object
--                                      RAG_LAB owns is a lab object and every RAG_LAB host ACE is
--                                      one 00 appended on the GenAI host; then remove those host
--                                      ACEs, revoke SELECT on ASKORACLE's models from RAG_LAB,
--                                      drop user RAG_LAB, revoke READ on RAG_MODEL_DIR from
--                                      ASKORACLE and drop the three RAG_ directories (only where
--                                      they point into /opt/oracle/kb/rag_lab), count
--                                      ASKORACLE's models before and after (they must be equal)
-- Lab objects (phase admin): names starting RAG_ (the lab's tables, the vector tables of RAG_
--          indexes, RAG_PRB_LOG, RAG_BUILD_LOG), plus what such a table brings with it: its
--          indexes, LOB segments and identity sequences, the VECTOR$<index>$... tables of a
--          vector index on it, and its recycle-bin copies. Anything else is listed and the phase
--          stops before any change; the operator decides.
-- Usage  : @99_cleanup.sql lab   <genai_host>
--          @99_cleanup.sql admin <genai_host>
--          Both arguments may hold only letters, digits and _ . : - ; anything else stops the
--          script before any PL/SQL sees it (README_RUN.md, quoting risk in hand runs).
--          run_all.sh STAGES=CLEANUP runs both, then 99b_cleanup_files.sh.
-- Re-run : safe; every step skips what is already gone.
--
-- The operator decided (PLAN.md section 10) that RAG_LAB is kept after the blog until they
-- say otherwise; this script exists for GO-4 and is run only on request.

set verify off echo off feedback off termout on
set serveroutput on size unlimited format wrapped
whenever sqlerror exit failure
whenever oserror exit failure

define cleanup_phase = '&1'
define genai_host    = '&2'

-- Argument guard (Codex A1): this query is the first statement to hold the arguments, and only
-- inside q'{...}' literals, which a value from the allowed set (written out, no ranges) cannot
-- end. Output is off while it runs; the block after it stops the script unless the verdict is
-- exactly YES. The next block checks the precise forms.
define rag_arg_re = '^[0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_.:-]{1,200}$'
define rag_args_ok = 'NO'
column rag_args_ok new_value rag_args_ok noprint
prompt checking the arguments (allowed: letters, digits and _ . : -)
set termout off
select case when regexp_like(q'{&cleanup_phase}', '&rag_arg_re', 'c')
             and regexp_like(q'{&genai_host}', '&rag_arg_re', 'c')
            then 'YES' else 'NO' end rag_args_ok
  from dual;
set termout on
column rag_args_ok clear
begin
  if '&rag_args_ok' = 'YES' then
    dbms_output.put_line('arguments: plain characters only');
  else
    raise_application_error(-20000, 'the arguments may hold only letters, digits and _ . : - ' ||
      '(README_RUN.md, quoting risk in hand runs); nothing was changed');
  end if;
end;
/

declare
  l_phase varchar2(16) := lower(trim('&cleanup_phase'));
  l_n     pls_integer;
begin
  if l_phase not in ('lab', 'admin') then
    raise_application_error(-20001, 'phase must be lab or admin');
  end if;
  if not regexp_like('&genai_host', '^inference\.generativeai\.[a-z0-9-]+\.oci\.oraclecloud\.com$') then
    raise_application_error(-20002, 'genai_host must look like inference.generativeai.<region>.oci.oraclecloud.com');
  end if;
  if l_phase = 'lab' and sys_context('userenv', 'session_user') <> 'RAG_LAB' then
    raise_application_error(-20003, 'phase lab runs as RAG_LAB');
  end if;
  if l_phase = 'admin' then
    if sys_context('userenv', 'con_name') = 'CDB$ROOT' then
      raise_application_error(-20004, 'phase admin runs in the PDB, not CDB$ROOT');
    end if;
    select count(*) into l_n from session_privs where privilege = 'DROP USER';
    if l_n = 0 then
      raise_application_error(-20005, 'phase admin needs a DBA session (DROP USER)');
    end if;
  end if;
end;
/

-- =====================================================================================
-- phase lab (RAG_LAB): its own indexes, pipelines, profiles and credential
-- =====================================================================================
declare
  l_phase varchar2(16) := lower(trim('&cleanup_phase'));
  l_n     pls_integer;
  l_left  varchar2(4000);
  l_st    varchar2(400);
  l_sts   sys.odcivarchar2list := sys.odcivarchar2list();
  c       sys_refcursor;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  procedure assert_lab(n varchar2) is
  begin
    if sys_context('userenv', 'session_user') <> 'RAG_LAB'
       or n is null or substr(n, 1, 4) <> 'RAG_'
       or dbms_assert.simple_sql_name(n) is null then
      raise_application_error(-20090, 'guard: refusing to drop ' || nvl(n, '<null>'));
    end if;
  end;

  -- a status table this phase may drop: a plain name, recorded from a RAG_ pipeline of RAG_LAB
  -- before the index drops, and still a table of RAG_LAB
  procedure assert_status_table(n varchar2) is
    k      pls_integer;
    l_seen boolean := false;
  begin
    for i in 1 .. l_sts.count loop                 -- a VARRAY: no MEMBER OF
      l_seen := l_seen or l_sts(i) = n;
    end loop;
    select count(*) into k from user_tables where table_name = n;
    if sys_context('userenv', 'session_user') <> 'RAG_LAB' or n is null
       or dbms_assert.simple_sql_name(n) is null or k <> 1 or not l_seen then
      raise_application_error(-20091, 'guard: refusing to drop table ' || nvl(n, '<null>'));
    end if;
  end;
begin
  if l_phase <> 'lab' then
    return;
  end if;

  -- anything RAG_LAB owns that is not named RAG_ is unexpected: stop, touch nothing (Codex A6:
  -- all of it is checked here, before the first drop)
  for i in (select index_name from user_cloud_vector_indexes where substr(index_name, 1, 4) <> 'RAG_') loop
    l_left := l_left || ' ' || i.index_name;
  end loop;
  if l_left is not null then
    raise_application_error(-20010, 'RAG_LAB owns vector indexes not named RAG_:' || l_left || '; investigate first');
  end if;
  for r in (select 'profile ' || profile_name n from user_cloud_ai_profiles where substr(profile_name, 1, 4) <> 'RAG_'
            union all
            select 'pipeline ' || pipeline_name from user_cloud_pipelines where substr(pipeline_name, 1, 4) <> 'RAG_'
            union all
            select 'credential ' || credential_name from user_credentials where substr(credential_name, 1, 4) <> 'RAG_'
            order by 1) loop
    l_left := substr(l_left || ' ' || r.n || ';', 1, 3000);
  end loop;
  if l_left is not null then
    raise_application_error(-20012, 'RAG_LAB owns objects not named RAG_:' || l_left ||
      ' nothing was dropped; the operator decides about them first');
  end if;

  -- the status tables of the lab's pipelines, read before the index drops: one that outlives its
  -- pipeline is a lab object and is dropped below, so phase admin finds only lab objects
  begin
    open c for 'select status_table from user_cloud_pipelines where substr(pipeline_name, 1, 4) = ''RAG_'''
            || ' and status_table is not null order by 1';
    loop
      fetch c into l_st;
      exit when c%notfound;
      -- the view may qualify the name with the owner: only RAG_LAB's own, unquoted
      l_st := replace(regexp_replace(l_st, '^"?RAG_LAB"?\.', ''), '"');
      if instr(l_st, '.') = 0 then
        l_sts.extend;
        l_sts(l_sts.count) := l_st;
      else
        p('note: status table ' || l_st || ' is not RAG_LAB''s; left alone');
      end if;
    end loop;
    close c;
  exception
    when others then
      if c%isopen then close c; end if;
      p('note: pipeline status tables not readable (' || substr(sqlerrm, 1, 100) || '); none recorded, ' ||
        'so phase admin lists any that are left');
  end;

  for i in (select index_name from user_cloud_vector_indexes order by index_name) loop
    assert_lab(i.index_name);
    dbms_cloud_ai.drop_vector_index(index_name => i.index_name, include_data => true, force => true);
    p('dropped vector index ' || i.index_name);
  end loop;

  select count(*) into l_n from user_cloud_pipelines;
  if l_n > 0 then
    for r in (select pipeline_name, status from user_cloud_pipelines order by 1) loop
      p('pipeline still present: ' || r.pipeline_name || ' (' || r.status || ')');
    end loop;
    raise_application_error(-20011, l_n || ' pipeline(s) remain after the index drops; stopping before the profiles');
  end if;

  for i in 1 .. l_sts.count loop
    select count(*) into l_n from user_tables where table_name = l_sts(i);
    if l_n = 1 then
      assert_status_table(l_sts(i));
      execute immediate 'drop table ' || dbms_assert.enquote_name(l_sts(i), false) || ' purge';
      p('dropped status table ' || l_sts(i) || ' (its pipeline is gone)');
    end if;
  end loop;

  for r in (select profile_name from user_cloud_ai_profiles order by profile_name) loop
    assert_lab(r.profile_name);
    dbms_cloud_ai.drop_profile(profile_name => r.profile_name, force => true);
    p('dropped profile ' || r.profile_name);
  end loop;

  select count(*) into l_n from user_credentials where credential_name = 'RAG_LAB_OCI_CRED';
  if l_n > 0 then
    dbms_cloud.drop_credential(credential_name => 'RAG_LAB_OCI_CRED');
    p('dropped credential RAG_LAB_OCI_CRED');
  end if;

  select count(*) into l_n from user_cloud_vector_indexes;
  p('left: vector indexes ' || l_n);
  select count(*) into l_n from user_cloud_ai_profiles;
  p('left: profiles ' || l_n);
  select count(*) into l_n from user_credentials;
  p('left: credentials ' || l_n);
  select count(*) into l_n from user_tables where table_name like '%$VECTAB';
  p('left: vector tables ' || l_n);
end;
/

-- =====================================================================================
-- phase admin (SYSDBA): metadata check, ACEs, user, directories; models counted, not touched
-- =====================================================================================
declare
  l_phase   varchar2(16) := lower(trim('&cleanup_phase'));
  c_root    constant varchar2(64) := '/opt/oracle/kb/rag_lab';
  l_n       pls_integer;
  l_users   pls_integer;
  l_models0 pls_integer;
  l_models1 pls_integer;
  l_block   varchar2(4000);
  l_path    varchar2(4000);
  l_unknown varchar2(4000);

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function masked(h varchar2) return varchar2 is
  begin
    return case when regexp_like(h, '^inference\.generativeai\.[^.]+\.oci\.oraclecloud\.com$', 'i')
                then regexp_replace(h, '^inference\.generativeai\.[^.]+\.', 'inference.generativeai.<region>.', 1, 1, 'i')
                else '<other host>' end;
  end;
begin
  if l_phase <> 'admin' then
    return;
  end if;

  select count(*) into l_models0 from dba_mining_models where owner = 'ASKORACLE';
  p('ASKORACLE mining models before cleanup: ' || l_models0 || ' (none is dropped)');

  select count(*) into l_users from dba_users where username = 'RAG_LAB';
  if l_users > 0 then
    -- 1. phase lab must have run: no vector tables and no scheduler jobs left in RAG_LAB
    select count(*) into l_n from dba_tables where owner = 'RAG_LAB' and table_name like '%$VECTAB';
    if l_n > 0 then l_block := l_block || ' ' || l_n || ' vector table(s);'; end if;
    select count(*) into l_n from dba_scheduler_jobs where owner = 'RAG_LAB';
    if l_n > 0 then l_block := l_block || ' ' || l_n || ' scheduler job(s);'; end if;

    -- 2. C##CLOUD$SERVICE metadata that still names RAG_LAB (read-only scan of every table or
    --    view there with an owner-like column; history and log tables are reported only)
    for t in (select c.owner, c.table_name, c.column_name
                from dba_tab_columns c
               where c.owner = 'C##CLOUD$SERVICE'
                 and c.column_name in ('OWNER', 'USERNAME', 'USER_NAME', 'SCHEMA_NAME', 'OBJECT_OWNER')
                 and c.data_type in ('VARCHAR2', 'CHAR', 'NVARCHAR2')
               order by c.table_name, c.column_name) loop
      begin
        execute immediate 'select count(*) from ' ||
          dbms_assert.enquote_name(t.owner, false) || '.' || dbms_assert.enquote_name(t.table_name, false) ||
          ' where ' || dbms_assert.enquote_name(t.column_name, false) || ' = :u'
          into l_n using 'RAG_LAB';
        if l_n > 0 then
          if regexp_like(t.table_name, '(HIST|HISTORY|LOG|LOGS|AUDIT)$') then
            p('info: ' || t.table_name || '.' || t.column_name || ' keeps ' || l_n || ' row(s) for RAG_LAB (history)');
          else
            l_block := substr(l_block || ' ' || t.table_name || '(' || l_n || ');', 1, 3000);
          end if;
        end if;
      exception
        when others then
          p('info: could not read ' || t.table_name || ': ' || substr(sqlerrm, 1, 80));
      end;
    end loop;
    if l_block is not null then
      raise_application_error(-20020, 'RAG_LAB still has cloud objects:' || l_block ||
        ' Run phase lab first (as RAG_LAB), then this phase again.');
    end if;

    -- every object RAG_LAB owns must be a lab object (Codex A6), before DROP USER ... CASCADE
    -- could take anything else with it: a RAG_ name, or something a RAG_ table brings with it
    for o in (with lab_tab as (
                select t.table_name
                  from dba_tables t
                 where t.owner = 'RAG_LAB' and t.table_name like 'RAG\_%' escape '\'
                union
                -- the internal tables of a vector index on a RAG_ table
                select t.table_name
                  from dba_tables t
                  join dba_indexes i on i.table_owner = 'RAG_LAB' and i.table_name like 'RAG\_%' escape '\'
                 where t.owner = 'RAG_LAB'
                   and substr(t.table_name, 1, length(i.index_name) + 8) = 'VECTOR$' || i.index_name || '$'),
              known as (
                select table_name name from lab_tab
                union select index_name from dba_indexes
                       where table_owner = 'RAG_LAB' and table_name in (select table_name from lab_tab)
                union select segment_name from dba_lobs
                       where owner = 'RAG_LAB' and table_name in (select table_name from lab_tab)
                union select index_name from dba_lobs
                       where owner = 'RAG_LAB' and table_name in (select table_name from lab_tab)
                union select sequence_name from dba_tab_identity_cols
                       where owner = 'RAG_LAB' and table_name in (select table_name from lab_tab)
                union select r.object_name from dba_recyclebin r
                       where r.owner = 'RAG_LAB'
                         and (r.original_name like 'RAG\_%' escape '\'
                              or r.base_object in (select b.base_object from dba_recyclebin b
                                                    where b.owner = 'RAG_LAB' and b.type = 'TABLE'
                                                      and b.original_name like 'RAG\_%' escape '\')))
              select o.object_type, o.object_name
                from dba_objects o
               where o.owner = 'RAG_LAB'
                 and o.object_name not like 'RAG\_%' escape '\'
                 and o.object_name not in (select name from known where name is not null)
               order by o.object_type, o.object_name) loop
      l_unknown := substr(l_unknown || ' ' || o.object_type || ' ' || o.object_name || ';', 1, 3000);
    end loop;
    if l_unknown is not null then
      raise_application_error(-20023, 'RAG_LAB owns objects that are not lab objects:' || l_unknown ||
        ' Nothing was changed. The operator decides about them (move or drop them), then this phase again.');
    end if;
  end if;

  -- every RAG_LAB host ACE must be one 00 appended: connect, resolve or http on the GenAI host,
  -- no ports, a grant (Codex A6); any other is listed and nothing is changed
  for a in (select host, lower_port, upper_port, upper(privilege) privilege, grant_type
              from dba_host_aces where principal = 'RAG_LAB'
             order by host, lower_port nulls first, upper_port nulls first, privilege) loop
    if lower(a.host) <> lower('&genai_host') or a.lower_port is not null or a.upper_port is not null
       or a.privilege not in ('CONNECT', 'RESOLVE', 'HTTP') or nvl(upper(a.grant_type), '-') <> 'GRANT' then
      l_unknown := substr(l_unknown || ' ' || nvl(upper(a.grant_type), '?') || ' ' || a.privilege || ' on ' ||
        masked(a.host) || ' ports ' || nvl(to_char(a.lower_port), '-') || '-' || nvl(to_char(a.upper_port), '-') ||
        ';', 1, 3000);
    end if;
  end loop;
  if l_unknown is not null then
    raise_application_error(-20024, 'RAG_LAB holds host ACEs that 00_admin_prereqs.sql did not append:' || l_unknown ||
      ' Nothing was changed; see DBA_HOST_ACES and decide with the operator.');
  end if;

  -- 3. RAG_LAB's host ACEs, all of them on the GenAI host (checked just above). The first change:
  --    if this call fails, nothing else has been changed yet
  for a in (select host, lower_port, upper_port, lower(privilege) privilege
              from dba_host_aces where principal = 'RAG_LAB'
                                   and lower(host) = lower('&genai_host')) loop
    dbms_network_acl_admin.remove_host_ace(
      host             => a.host,
      lower_port       => a.lower_port,
      upper_port       => a.upper_port,
      ace              => xs$ace_type(privilege_list => xs$name_list(a.privilege),
                                      principal_name => 'RAG_LAB',
                                      principal_type => xs_acl.ptype_db),
      remove_empty_acl => true);
    p('removed host ACE ' || a.privilege || ' on ' ||
      regexp_replace(a.host, '^inference\.generativeai\.[^.]+\.', 'inference.generativeai.<region>.'));
  end loop;

  -- 4. SELECT on ASKORACLE's mining models, given to RAG_LAB by 03 (Codex A5); the models stay
  if l_users > 0 then
    for g in (select table_name from dba_tab_privs
               where owner = 'ASKORACLE' and grantee = 'RAG_LAB' and privilege = 'SELECT'
                 and table_name in (select model_name from dba_mining_models where owner = 'ASKORACLE')
               order by table_name) loop
      execute immediate 'revoke select on mining model ' || dbms_assert.enquote_name('ASKORACLE', false) || '.' ||
                        dbms_assert.enquote_name(dbms_assert.simple_sql_name(g.table_name), false) || ' from RAG_LAB';
      p('revoked SELECT ON MINING MODEL ASKORACLE.' || g.table_name || ' from RAG_LAB (the model stays)');
    end loop;
  end if;

  -- 5. the user (its grants on SYS and C##CLOUD$SERVICE objects go with it)
  if l_users > 0 then
    execute immediate 'drop user ' || dbms_assert.simple_sql_name('RAG_LAB') || ' cascade';
    p('dropped user RAG_LAB');
  end if;

  -- 6. the three lab directories, only when they point into the lab tree; ASKORACLE's READ on
  --    RAG_MODEL_DIR (00b) is revoked first (Codex A5)
  for d in (select directory_name, directory_path from dba_directories
             where directory_name in ('RAG_CORPUS_DIR', 'RAG_PROBE_DIR', 'RAG_MODEL_DIR')
             order by directory_name) loop
    l_path := rtrim(d.directory_path, '/');
    if substr(l_path, 1, length(c_root) + 1) <> c_root || '/' then
      raise_application_error(-20021, d.directory_name || ' points at ' || d.directory_path ||
        ', outside ' || c_root || '; not dropped, investigate');
    end if;
    if d.directory_name = 'RAG_MODEL_DIR' then
      select count(*) into l_n from dba_tab_privs
       where owner = 'SYS' and table_name = 'RAG_MODEL_DIR' and grantee = 'ASKORACLE' and privilege = 'READ';
      if l_n > 0 then
        execute immediate 'revoke read on directory ' || dbms_assert.simple_sql_name(d.directory_name) || ' from ASKORACLE';
        p('revoked READ on directory RAG_MODEL_DIR from ASKORACLE');
      end if;
    end if;
    execute immediate 'drop directory ' || dbms_assert.simple_sql_name(d.directory_name);
    p('dropped directory ' || d.directory_name);
  end loop;

  select count(*) into l_models1 from dba_mining_models where owner = 'ASKORACLE';
  p('ASKORACLE mining models after cleanup: ' || l_models1);
  if l_models1 <> l_models0 then
    raise_application_error(-20022, 'the number of ASKORACLE models changed during cleanup; investigate');
  end if;

  select count(*) into l_n from dba_users where username = 'RAG_LAB';
  p('left: RAG_LAB users ' || l_n);
  select count(*) into l_n from dba_host_aces where principal = 'RAG_LAB';
  p('left: RAG_LAB host ACEs ' || l_n);
  select count(*) into l_n from dba_tab_privs
   where owner = 'ASKORACLE' and grantee = 'RAG_LAB'
     and table_name in (select model_name from dba_mining_models where owner = 'ASKORACLE');
  p('left: RAG_LAB privileges on ASKORACLE models ' || l_n);
  select count(*) into l_n from dba_tab_privs
   where owner = 'SYS' and table_name = 'RAG_MODEL_DIR' and grantee = 'ASKORACLE';
  p('left: ASKORACLE privileges on RAG_MODEL_DIR ' || l_n);
  select count(*) into l_n from dba_directories
   where directory_name in ('RAG_CORPUS_DIR', 'RAG_PROBE_DIR', 'RAG_MODEL_DIR');
  p('left: lab directories ' || l_n);
end;
/
