-- v1.5 - Brief 09 (Select AI RAG tuning): the SQL*Plus probes of PLAN.md section 8.1.
--        v1.5: public copy: the other app is named the existing HR app; tools/capture.py (lab-internal) not shipped.
--        v1.4: GO-1: c_need = 6 * 1073741824 overflowed (two integer literals multiply as PLS_INTEGER),
--              which failed every probe at block elaboration; now 6 * c_gb (NUMBER).
--        v1.3: Codex adversarial review (A1, verifier gap): the probe name is checked at SQL*Plus
--              level before any PL/SQL sees it (P0 runs as SYSDBA): one guard query holds it only in
--              a q'{...}' literal and allows letters, digits and _ only; the block after it stops the
--              script unless the verdict is exactly YES.
--        v1.2: build()'s normal stop after the load falls back to <index>$VECPIPELINE when the index has
--              no pipeline_name attribute (verifier gap), as stop_after_error already did.
--        v1.1: P0 ends with a "P0 STATUS: PASS|FAIL ... <utc>" line (04_probes.py p0-status turns it
--              into results/probes/p0.status, which gates MODELS) and gains hard gates on ASKORACLE's
--              quota and on the room in its default tablespace and SYSAUX (6 GB each: twice the ~2.8 GB
--              of lab models, M6 in FP32). P6/P10/P14: any error while the build is waited for stops
--              the index's pipeline first, then is raised again.
--        v1.0: first version. P0, P1, P1A, P3, P4B, P6, P9, P10, P12, P13, P14 (builds p4), P15.
--              The probes that need Python or call the LLM (P4, P5 scoring, P7, P8, P11, P14
--              scoring, P16, P17) are in 04_probes.py; PROBES.md is the runbook.
--
-- Run as : depends on the probe; always connected to the PDB, never CDB$ROOT.
--            P0, P1   SYSDBA or a DBA account   read-only dictionary and V$ queries
--            P1A      ASKORACLE                 read-only: the existing HR app's own indexes and profiles
--            others   RAG_LAB
-- Usage  : @04_probes.sql <PROBE>           e.g.  @04_probes.sql P6
--          PROBE may hold only letters, digits and _ ; anything else stops the script before any
--          PL/SQL sees it (README_RUN.md, quoting risk in hand runs).
--          (the lab ran it through tools/capture.py, a lab-internal transcript wrapper not shipped here)
--          SQL*Plus must run with NLS_LANG=AMERICAN_AMERICA.AL32UTF8: checked, because Arabic
--          would otherwise reach the database as '?'.
-- Order  : P0, P1, P1A, P3, [py p4], [05 + py p5], P6, P9, P4B, [py p7, p8], P10, [py p11],
--          P14, [py p14], P12, [py p16], [stage the P13 files] P13, [probe-rm] P15, [py p17].
--          The probe indexes are built here, not by 07_build_index.sql: 07 builds over
--          RAG_CORPUS_DIR only. P6/P10/P14 follow its conventions (see that block).
-- Re-run : safe. The read-only probes change nothing. P6, P10 and P14 drop and rebuild only
--          their own RAG_PRB_* index and RAG_P_PRB_* profile (names checked before any drop).
--          P13 and P15 run the p1 pipeline once and leave it stopped. Every probe appends its
--          key figures to RAG_LAB.RAG_PRB_LOG (created here, idempotently).
-- Output : transcript-ready. No host name, file-system path, OCID, region or credential is
--          selected; profile and index attributes are printed from a whitelist; error text is
--          redacted (OCID, region, request id, IP, e-mail, /home path) before it is printed.
--
-- Why one PL/SQL block per probe, with dynamic SQL: every block is compiled whichever user
-- runs the file, so a static reference to a V$ view, a DBA_ view, a Select AI package or the
-- lab's own tables would fail to compile for the other users. Only views every user has
-- (USER_*, ALL_*) and public packages (DBMS_OUTPUT, DBMS_ASSERT, DBMS_LOB, DBMS_SESSION) are
-- referenced statically.

set verify off echo off feedback off termout on heading off pagesize 0
set linesize 240 trimout on trimspool on long 100000 longchunksize 32767
set serveroutput on size unlimited format wrapped
set sqlblanklines on
whenever sqlerror exit failure
whenever oserror exit failure

define probe = '&1'

-- Argument guard (Codex A1, as in 03, 06, 07, 08 and 99): this query is the first statement
-- to hold the probe name, and only inside a q'{...}' literal, which a value from the allowed set
-- (written out, no ranges, so no NLS_SORT can widen it) cannot end. Output is off while it runs
-- (in a script run with @; SQL*Plus shows it when the file comes on stdin, as the lab's wrapper sent it,
-- and the column is noprint either way). The block after it stops the script unless the verdict
-- is exactly YES; the prelude then checks the name against the list of probes.
define rag_arg_re = '^[0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_]{1,64}$'
define rag_args_ok = 'NO'
column rag_args_ok new_value rag_args_ok noprint
set termout off
select case when regexp_like(q'{&probe}', '&rag_arg_re', 'c')
            then 'YES' else 'NO' end rag_args_ok
  from dual;
set termout on
column rag_args_ok clear
begin
  if '&rag_args_ok' = 'YES' then
    null;
  else
    raise_application_error(-20000, 'the probe name may hold only letters, digits and _ ' ||
      '(README_RUN.md, quoting risk in hand runs); nothing was run');
  end if;
end;
/

-- ---------------------------------------------------------------------------------------------
-- prelude: argument, container, client character set, role, the lab's probe log
-- ---------------------------------------------------------------------------------------------
declare
  l_probe varchar2(64) := '&probe';
  l_user  varchar2(128) := sys_context('userenv', 'session_user');
  l_n     number;
begin
  if not regexp_like(l_probe, '^(P0|P1|P1A|P3|P4B|P6|P9|P10|P12|P13|P14|P15)$') then
    raise_application_error(-20100,
      'usage: @04_probes.sql <P0|P1|P1A|P3|P4B|P6|P9|P10|P12|P13|P14|P15> (upper case)');
  end if;
  if sys_context('userenv', 'con_name') = 'CDB$ROOT' then
    raise_application_error(-20101, 'connect to the PDB, not CDB$ROOT');
  end if;
  -- this literal arrives intact only when the SQL*Plus client speaks UTF-8 (and the database
  -- can hold Arabic: P0 is exempt, because P0 is the probe that reports the database charset)
  if l_probe <> 'P0' and unistr('\0627\0644\0639\0631\0628\064A\0629') <> 'العربية' then
    raise_application_error(-20102,
      'the SQL*Plus client is not UTF-8: set NLS_LANG=AMERICAN_AMERICA.AL32UTF8 (Arabic would be lost)');
  end if;
  if l_probe in ('P0', 'P1') then
    begin
      execute immediate 'select count(*) from dba_users where rownum = 1' into l_n;
    exception
      when others then
        raise_application_error(-20103, 'run ' || l_probe || ' as SYSDBA or a DBA account');
    end;
  elsif l_probe = 'P1A' then
    if l_user <> 'ASKORACLE' then
      raise_application_error(-20104, 'run P1A as ASKORACLE (read-only)');
    end if;
  else
    if l_user <> 'RAG_LAB' then
      raise_application_error(-20105, 'run ' || l_probe || ' as RAG_LAB');
    end if;
    select count(*) into l_n from user_tables where table_name = 'RAG_PRB_LOG';
    if l_n = 0 then
      execute immediate 'create table RAG_PRB_LOG (' ||
                        '  utc   timestamp default sys_extract_utc(systimestamp) not null,' ||
                        '  probe varchar2(8)    not null,' ||
                        '  item  varchar2(128)  not null,' ||
                        '  val   varchar2(4000))';
    end if;
  end if;
end;
/

-- =============================================================================================
-- P0 (SYSDBA/DBA, read-only): capacity and safety before any model is loaded or index built.
-- Hard gates (PLAN.md 8.1): database character set not AL32UTF8 -> stop the Arabic part;
-- ARCHIVELOG with less than 5 GB of recovery-area headroom -> stop and ask the operator.
-- Hard gates added in v1.1, because a P0 PASS now authorises MODELS (permanent loads into
-- ASKORACLE): ASKORACLE's quota on its default tablespace, and the room in that tablespace and in
-- SYSAUX, each at least 6 GB (twice the ~2.8 GB of the seven lab models with M6 in FP32).
-- Warning: PGA headroom under 2 GB.
-- The last line is "P0 STATUS: PASS <utc>" or "P0 STATUS: FAIL <gates> <utc>" (the UTC time of the
-- database); 04_probes.py p0-status turns the transcript into results/probes/p0.status.
-- No host name and no file-system path is selected. The host disk is 04_probes.py p0-df.
-- =============================================================================================
declare
  c_gb   constant number := 1073741824;
  c_need constant number := 6 * c_gb;             -- not 6 * 1073741824: two integer literals multiply as PLS_INTEGER (ORA-01426)
  l_utc  varchar2(20) := to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
  l_fail varchar2(400);
  l_dts  varchar2(128);
  l_qmax number;
  l_qused number;
  l_unl  number;
  l_room number;
  l_cs   varchar2(128);
  l_log  varchar2(32);
  l_lim  number;
  l_tgt  number;
  l_all  number;
  l_max  number;
  l_fl   number;
  l_fu   number;
  l_fr   number;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function gb(b number) return varchar2 is
  begin
    return case when b is null then 'n/a' else to_char(round(b / c_gb, 1), 'fm999990.0') || ' GB' end;
  end;

  function qv(q varchar2) return varchar2 is
    v varchar2(4000);
  begin
    execute immediate q into v;
    return v;
  exception
    when no_data_found then return null;
  end;

  function qn(q varchar2) return number is
    v number;
  begin
    execute immediate q into v;
    return v;
  exception
    when no_data_found then return null;
  end;

  procedure show(q varchar2) is
    rc sys_refcursor;
    l  varchar2(4000);
  begin
    open rc for q;
    loop
      fetch rc into l;
      exit when rc%notfound;
      p(l);
    end loop;
    close rc;
  end;

  -- free space plus what autoextend can still add (the host disk is p0-df's question)
  function room(ts varchar2) return number is
    n number;
  begin
    execute immediate
      q'[select (select nvl(sum(bytes), 0) from dba_free_space where tablespace_name = :1)
              + (select nvl(sum(case when autoextensible = 'YES' then greatest(maxbytes, bytes) else bytes end)
                            - sum(bytes), 0)
                   from dba_data_files where tablespace_name = :2)
           from dual]' into n using ts, ts;
    return n;
  end;

  procedure room_gate(ts varchar2, tag varchar2) is
  begin
    l_room := room(ts);
    if l_room < c_need then
      p('GATE room ' || rpad(ts, greatest(12, length(ts))) || ': FAIL - ' || gb(l_room) || ' < ' || gb(c_need)
        || ' (twice the ~2.8 GB of lab models)');
      l_fail := l_fail || ' ' || tag;
    else
      p('GATE room ' || rpad(ts, greatest(12, length(ts))) || ': PASS - ' || gb(l_room));
    end if;
  end;
begin
  if '&probe' <> 'P0' then
    return;
  end if;
  p('== P0  capacity and safety (read-only)');
  p('database              : ' || qv(q'[select replace(banner_full, chr(10), ' / ') from v$version where rownum = 1]'));
  p('container             : ' || sys_context('userenv', 'con_name'));
  l_cs := qv(q'[select value from nls_database_parameters where parameter = 'NLS_CHARACTERSET']');
  p('NLS_CHARACTERSET      : ' || l_cs);
  p('NLS_NCHAR_CHARACTERSET: ' || qv(q'[select value from nls_database_parameters where parameter = 'NLS_NCHAR_CHARACTERSET']'));
  p('max_string_size       : ' || qv(q'[select value from v$parameter where name = 'max_string_size']')
    || '   (VECTOR_EMBEDDING converts its input to VARCHAR2; P4 caps its sample to fit)');
  p('cpu_count             : ' || qv(q'[select value from v$parameter where name = 'cpu_count']'));
  l_lim := qn(q'[select to_number(value) from v$parameter where name = 'pga_aggregate_limit']');
  l_tgt := qn(q'[select to_number(value) from v$parameter where name = 'pga_aggregate_target']');
  l_all := qn(q'[select value from v$pgastat where name = 'total PGA allocated']');
  l_max := qn(q'[select value from v$pgastat where name = 'maximum PGA allocated']');
  p('pga_aggregate_limit   : ' || case when l_lim = 0 then '0 (no limit)' else gb(l_lim) end);
  p('pga_aggregate_target  : ' || gb(l_tgt));
  p('PGA allocated now     : ' || gb(l_all) || '   (peak since startup ' || gb(l_max) || ')');
  p('sga_target            : ' || gb(qn(q'[select to_number(value) from v$parameter where name = 'sga_target']')));
  l_log := qv(q'[select log_mode from v$database]');
  execute immediate
    q'[select nvl(sum(space_limit), 0), nvl(sum(space_used), 0), nvl(sum(space_reclaimable), 0)
         from v$recovery_file_dest]' into l_fl, l_fu, l_fr;
  p('log_mode              : ' || l_log);
  p('recovery area         : limit ' || gb(l_fl) || ', used ' || gb(l_fu) || ', reclaimable ' || gb(l_fr));

  p('');
  p('tablespace   allocated   max(autoextend)   free now   room left');
  show(q'[select rpad(d.tablespace_name, 11)
              || lpad(to_char(round(d.alloc / 1073741824, 1), 'fm999990.0') || ' GB', 12)
              || lpad(to_char(round(d.maxb / 1073741824, 1), 'fm999990.0') || ' GB', 18)
              || lpad(to_char(round(nvl(f.free, 0) / 1073741824, 1), 'fm999990.0') || ' GB', 11)
              || lpad(to_char(round((nvl(f.free, 0) + d.maxb - d.alloc) / 1073741824, 1), 'fm999990.0') || ' GB', 12)
            from (select tablespace_name, sum(bytes) alloc,
                         sum(case when autoextensible = 'YES' then greatest(maxbytes, bytes) else bytes end) maxb
                    from dba_data_files
                   where tablespace_name in ('USERS', 'SYSAUX')
                   group by tablespace_name) d
            left join (select tablespace_name, sum(bytes) free from dba_free_space group by tablespace_name) f
              on f.tablespace_name = d.tablespace_name
           order by d.tablespace_name]');
  p('');
  show(q'[select 'ASKORACLE default tablespace ' || u.default_tablespace || ', quota there: '
              || nvl((select case when q.max_bytes = -1 then 'UNLIMITED'
                                  else to_char(round(q.max_bytes / 1048576)) || ' MB' end
                        from dba_ts_quotas q
                       where q.username = u.username and q.tablespace_name = u.default_tablespace), 'none')
              || (select case when count(*) > 0 then ' (holds UNLIMITED TABLESPACE)' end
                    from dba_sys_privs s
                   where s.grantee = u.username and s.privilege = 'UNLIMITED TABLESPACE')
            from dba_users u
           where u.username = 'ASKORACLE']');
  p('ONNX models in ASKORACLE (MODEL_SIZE as DBA_MINING_MODELS reports it):');
  show(q'[select '  ' || rpad(model_name, 28) || lpad(to_char(model_size), 10) || '  ' || mining_function
            from dba_mining_models
           where owner = 'ASKORACLE' and algorithm = 'ONNX'
           order by model_name]');
  show(q'[select 'RAG_LAB user          : ' || case count(*) when 0 then 'not created yet' else 'exists' end
            from dba_users where username = 'RAG_LAB']');

  p('');
  if l_cs = 'AL32UTF8' then
    p('GATE character set    : PASS (AL32UTF8)');
  else
    p('GATE character set    : FAIL (' || l_cs || ') - stop the Arabic part of the lab');
    l_fail := l_fail || ' character-set';
  end if;
  if l_log = 'ARCHIVELOG' then
    if l_fl = 0 then
      p('GATE archive / FRA    : FAIL - ARCHIVELOG without a sized recovery area; the archive destination cannot be checked from here');
      l_fail := l_fail || ' archive-destination';
    elsif l_fl - l_fu + l_fr < 5 * c_gb then
      p('GATE archive / FRA    : FAIL - headroom ' || gb(l_fl - l_fu + l_fr) || ' < 5 GB (model loads and builds write about their size in redo)');
      l_fail := l_fail || ' recovery-area';
    else
      p('GATE archive / FRA    : PASS - headroom ' || gb(l_fl - l_fu + l_fr));
    end if;
  else
    p('GATE archive / FRA    : n/a (' || l_log || ')');
  end if;
  if l_lim > 0 and l_lim - l_all < 2 * c_gb then
    p('WARN PGA              : headroom ' || gb(l_lim - l_all) || ' < 2 GB; a 560M INT8 model needs about 0.6 GB per scoring session');
  else
    p('PGA headroom          : ' || case when l_lim = 0 then 'no limit set' else gb(l_lim - l_all) end);
  end if;
  -- the lab's models load into ASKORACLE and stay there (PLAN.md 0.3): quota and room must hold them
  l_dts := qv(q'[select default_tablespace from dba_users where username = 'ASKORACLE']');
  if l_dts is null then
    p('GATE ASKORACLE        : FAIL - user ASKORACLE not found');
    l_fail := l_fail || ' askoracle-missing';
  else
    execute immediate
      q'[select nvl(max(max_bytes), 0), nvl(max(bytes), 0) from dba_ts_quotas
          where username = 'ASKORACLE' and tablespace_name = :1]' into l_qmax, l_qused using l_dts;
    execute immediate
      q'[select count(*) from dba_sys_privs where grantee = 'ASKORACLE' and privilege = 'UNLIMITED TABLESPACE']'
      into l_unl;
    if l_unl > 0 or l_qmax = -1 then
      p('GATE ASKORACLE quota  : PASS - unlimited on ' || l_dts);
    elsif l_qmax - l_qused >= c_need then
      p('GATE ASKORACLE quota  : PASS - ' || gb(l_qmax - l_qused) || ' left on ' || l_dts);
    else
      p('GATE ASKORACLE quota  : FAIL - ' || gb(greatest(l_qmax - l_qused, 0)) || ' left on ' || l_dts || ' < ' || gb(c_need));
      l_fail := l_fail || ' askoracle-quota';
    end if;
    room_gate(l_dts, 'askoracle-room');
  end if;
  if l_dts is null or l_dts <> 'SYSAUX' then
    room_gate('SYSAUX', 'sysaux-room');
  end if;
  p('host disk             : 04_probes.py p0-df on the database host (it prints no path)');
  p('');
  if l_fail is null then
    p('P0 STATUS: PASS ' || l_utc);
  else
    p('P0 STATUS: FAIL' || l_fail || ' ' || l_utc);
    raise_application_error(-20110, 'P0 hard gate failed:' || l_fail || '. Stop and ask the operator.');
  end if;
end;
/

-- =============================================================================================
-- P1 (SYSDBA/DBA, read-only): which dictionary views describe cloud AI profiles, vector indexes
-- and pipelines (99 needs their names), and - through them, when they exist - a re-check
-- that every ASKORACLE index pairs one embedding model on both sides (PLAN.md 0.4).
-- P1A (ASKORACLE, read-only): the same pairing re-check through ASKORACLE's USER_ views.
-- Nothing is written in either; the existing HR app's objects are only read.
-- =============================================================================================
declare
  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function red(s varchar2) return varchar2 is
    r varchar2(32767) := s;
  begin
    r := regexp_replace(r, 'ocid1\.[A-Za-z0-9._-]+', '<ocid>');
    r := regexp_replace(r, 'opc[-_]request[-_]id[^ ,;]*', '<request-id>', 1, 0, 'i');
    r := regexp_replace(r, '(https?|file)://[^ "'']*', '<url>');
    r := regexp_replace(r, '[a-z]+-[a-z]+-[0-9]+', '<region>');
    r := regexp_replace(r, '(^|[^0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9.]|$)', '\1<ip>\3');
    r := regexp_replace(r, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '<email>');
    r := regexp_replace(r, '/home/[A-Za-z0-9._-]+', '/home/<user>');
    return r;
  end;

  function show(q varchar2) return pls_integer is
    rc sys_refcursor;
    l  varchar2(4000);
    n  pls_integer := 0;
  begin
    open rc for q;
    loop
      fetch rc into l;
      exit when rc%notfound;
      p(l);
      n := n + 1;
    end loop;
    close rc;
    return n;
  end;

  -- index -> its profile_name -> that profile's embedding_model, next to every RAG profile that
  -- names the index and that profile's embedding_model
  function pairing_sql(v varchar2, owner_filter varchar2) return varchar2 is
  begin
    return replace(replace(
      q'[with ix as (select index_name, upper(dbms_lob.substr(attribute_value, 128, 1)) prof
                       from #V#_CLOUD_VECTOR_INDEX_ATTRIBUTES
                      where attribute_name = 'profile_name' #F#),
              pa as (select profile_name, attribute_name, dbms_lob.substr(attribute_value, 400, 1) val
                       from #V#_CLOUD_AI_PROFILE_ATTRIBUTES
                      where attribute_name in ('embedding_model', 'vector_index_name') #F#)
         select '  ' || rpad(ix.index_name, 20) || rpad(ix.prof, 16) || rpad(nvl(e1.val, '-'), 42)
                || rpad(nvl(r.profile_name, '-'), 16) || rpad(nvl(e2.val, '-'), 42)
                || case when r.profile_name is null then 'NO RAG PROFILE'
                        when e1.val is null or e2.val is null then 'UNSET'
                        when upper(replace(e1.val, ' ')) = upper(replace(e2.val, ' ')) then 'PAIRED'
                        else 'MISMATCH' end
           from ix
           left join pa e1 on e1.profile_name = ix.prof and e1.attribute_name = 'embedding_model'
           left join pa r  on r.attribute_name = 'vector_index_name' and upper(r.val) = ix.index_name
           left join pa e2 on e2.profile_name = r.profile_name and e2.attribute_name = 'embedding_model'
          order by ix.index_name, r.profile_name]', '#V#', v), '#F#', owner_filter);
  end;

  procedure header is
  begin
    p('  ' || rpad('INDEX', 20) || rpad('INDEX PROFILE', 16) || rpad('ITS EMBEDDING_MODEL (documents)', 42)
      || rpad('RAG PROFILE', 16) || rpad('ITS EMBEDDING_MODEL (queries)', 42) || 'VERDICT');
  end;
begin
  if '&probe' not in ('P1', 'P1A') then
    return;
  end if;
  if '&probe' = 'P1' then
    p('== P1  dictionary views for cloud AI profiles, vector indexes and pipelines (read-only)');
    if show(q'[select '  ' || rpad(owner, 20) || view_name from dba_views
                where view_name like '%CLOUD\_AI\_PROFILE%' escape '\'
                   or view_name like '%CLOUD\_VECTOR\_INDEX%' escape '\'
                   or view_name like '%CLOUD\_PIPELINE%' escape '\'
                order by view_name, owner]') = 0 then
      p('  (no such view)');
    end if;
    p('public synonyms for the DBA_/CDB_ variants:');
    if show(q'[select '  ' || rpad(synonym_name, 40) || '-> ' || table_owner || '.' || table_name
                 from dba_synonyms
                where owner = 'PUBLIC'
                  and (synonym_name like 'DBA\_CLOUD\_%' escape '\' or synonym_name like 'CDB\_CLOUD\_%' escape '\')
                order by synonym_name]') = 0 then
      p('  (none)');
    end if;
    p('');
    p('ASKORACLE pairing through the DBA views (document side vs query side):');
    header;
    begin
      if show(pairing_sql('DBA', q'[and owner = 'ASKORACLE']')) = 0 then
        p('  (no vector index owned by ASKORACLE)');
      end if;
    exception
      when others then
        p('  not readable through DBA_CLOUD_* views here: ' || red(sqlerrm));
        p('  -> run P1A as ASKORACLE for the same check (read-only)');
    end;
  else
    p('== P1A  ASKORACLE vector indexes: document-side vs query-side embedding model (read-only)');
    header;
    if show(pairing_sql('USER', null)) = 0 then
      p('  (no vector index in this schema)');
    end if;
    p('');
    p('ONNX models in this schema (MODEL_SIZE as USER_MINING_MODELS reports it):');
    if show(q'[select '  ' || rpad(model_name, 28) || lpad(to_char(model_size), 10)
                 from user_mining_models where algorithm = 'ONNX' order by model_name]') = 0 then
      p('  (none)');
    end if;
  end if;
end;
/

-- =============================================================================================
-- P3 (RAG_LAB): VECTOR_NORM of each model's embedding of one English and one Arabic sentence.
-- Unit norm means COSINE, DOT and EUCLIDEAN rank identically and only SCORE changes (story 5);
-- otherwise S7 becomes a real sweep. Also the norms of the vectors the probe indexes stored.
-- =============================================================================================
declare
  type t_list is table of varchar2(128);
  l_keys   t_list := t_list('M0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M1Q');
  l_models t_list := t_list('ALL_MINILM_L12_V2', 'MULTILINGUAL_E5_SMALL', 'MULTILINGUAL_E5_BASE',
                            'MULTILINGUAL_E5_LARGE', 'BGE_M3', 'ARCTIC_EMBED_L_V2', 'ARABIC_TRIPLET_V2',
                            'MULTILINGUAL_E5_SMALL_Q');
  c_en constant varchar2(400) :=
    'Employees may carry forward a limited number of unused annual leave days into the next leave year.';
  c_ar constant varchar2(800) :=
    'يجوز للموظف ترحيل عدد محدود من أيام الإجازة السنوية غير المستخدمة إلى سنة الإجازات التالية.';
  l_n   number;
  l_dim number;
  l_ne  number;
  l_na  number;
  l_min number;
  l_max number;
  l_bad pls_integer := 0;
  l_tabs pls_integer := 0;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function red(s varchar2) return varchar2 is
    r varchar2(32767) := s;
  begin
    r := regexp_replace(r, 'ocid1\.[A-Za-z0-9._-]+', '<ocid>');
    r := regexp_replace(r, 'opc[-_]request[-_]id[^ ,;]*', '<request-id>', 1, 0, 'i');
    r := regexp_replace(r, '(https?|file)://[^ "'']*', '<url>');
    r := regexp_replace(r, '[a-z]+-[a-z]+-[0-9]+', '<region>');
    r := regexp_replace(r, '(^|[^0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9.]|$)', '\1<ip>\3');
    r := regexp_replace(r, '/home/[A-Za-z0-9._-]+', '/home/<user>');
    return r;
  end;

  procedure logv(i varchar2, v varchar2) is
  begin
    execute immediate 'insert into RAG_PRB_LOG (probe, item, val) values (:1, :2, :3)'
      using '&probe', substr(i, 1, 128), substr(v, 1, 4000);
    commit;
  end;
begin
  if '&probe' <> 'P3' then
    return;
  end if;
  p('== P3  VECTOR_NORM per model (1.000000 = unit length; COSINE, DOT and EUCLIDEAN then rank alike)');
  p(rpad('key', 5) || rpad('model', 26) || lpad('dims', 6) || lpad('norm EN', 12) || lpad('norm AR', 12)
    || lpad('max |1-norm|', 14));
  for i in 1 .. l_keys.count loop
    select count(*) into l_n from all_mining_models where owner = 'ASKORACLE' and model_name = l_models(i);
    if l_n = 0 then
      p(rpad(l_keys(i), 5) || rpad(l_models(i), 26) || '  not visible to ' || user
        || ' (not loaded, or no SELECT ON MINING MODEL)');
      continue;
    end if;
    begin
      execute immediate 'select vector_dimension_count(v), vector_norm(v) from (select vector_embedding(ASKORACLE.'
                        || dbms_assert.simple_sql_name(l_models(i)) || ' using :1 as data) v from dual)'
        into l_dim, l_ne using c_en;
      execute immediate 'select vector_norm(vector_embedding(ASKORACLE.'
                        || dbms_assert.simple_sql_name(l_models(i)) || ' using :1 as data)) from dual'
        into l_na using c_ar;
      p(rpad(l_keys(i), 5) || rpad(l_models(i), 26) || lpad(l_dim, 6)
        || lpad(to_char(l_ne, 'fm0.000000'), 12) || lpad(to_char(l_na, 'fm0.000000'), 12)
        || lpad(to_char(greatest(abs(1 - l_ne), abs(1 - l_na)), '9.99EEEE'), 14));
      logv(l_keys(i) || ' norm', to_char(l_ne, 'fm0.000000') || ' ' || to_char(l_na, 'fm0.000000') || ' dims ' || l_dim);
      if greatest(abs(1 - l_ne), abs(1 - l_na)) > 1e-3 then
        l_bad := l_bad + 1;
      end if;
    exception
      when others then
        p(rpad(l_keys(i), 5) || rpad(l_models(i), 26) || '  error: ' || red(sqlerrm));
        logv(l_keys(i) || ' norm', 'ERROR ' || red(sqlerrm));
    end;
  end loop;
  p('');
  p('stored vectors of the probe indexes:');
  l_n := 0;
  for t in (select table_name from user_tables
             where table_name like 'RAG\_PRB\_%$VECTAB' escape '\' order by table_name) loop
    execute immediate 'select count(*), min(vector_norm(embedding)), max(vector_norm(embedding)) from "'
                      || dbms_assert.simple_sql_name(t.table_name) || '"'
      into l_n, l_min, l_max;
    p('  ' || rpad(t.table_name, 22) || lpad(l_n, 6) || ' rows   norm min ' || to_char(l_min, 'fm0.000000')
      || '   max ' || to_char(l_max, 'fm0.000000'));
    l_tabs := l_tabs + 1;
  end loop;
  if l_tabs = 0 then
    p('  (no probe index yet; P3 can be re-run after P6)');
  end if;
  p('');
  p(case when l_bad = 0 then 'VERDICT: every visible model returns unit-length vectors'
         else 'VERDICT: ' || l_bad || ' model(s) are not unit length: S7 must sweep the metrics for real' end);
end;
/

-- =============================================================================================
-- P4B (RAG_LAB): is the stored $VECTAB embedding exactly VECTOR_EMBEDDING(model USING content)?
-- Ten chunks of p1 (five from Arabic files, five from English files), chunks of at most 1,300
-- characters so the CLOB converts to VARCHAR2. The model is read from the dictionary (index ->
-- profile_name -> embedding_model), never assumed. Every harness number depends on this.
-- =============================================================================================
declare
  c_idx  constant varchar2(30) := 'RAG_PRB_P1';
  c_eps  constant number := 1e-6;
  l_prof varchar2(128);
  l_emb  varchar2(400);
  l_own  varchar2(128);
  l_mod  varchar2(128);
  l_sql  varchar2(4000);
  rc     sys_refcursor;
  l_obj  varchar2(1024);
  l_len  number;
  l_d    number;
  l_n    pls_integer := 0;
  l_same pls_integer := 0;
  l_dmax number := 0;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function qv2(q varchar2, b1 varchar2, b2 varchar2) return varchar2 is
    v varchar2(4000);
  begin
    execute immediate q into v using b1, b2;
    return v;
  exception
    when no_data_found then return null;
  end;

  procedure logv(i varchar2, v varchar2) is
  begin
    execute immediate 'insert into RAG_PRB_LOG (probe, item, val) values (:1, :2, :3)'
      using '&probe', substr(i, 1, 128), substr(v, 1, 4000);
    commit;
  end;
begin
  if '&probe' <> 'P4B' then
    return;
  end if;
  l_prof := qv2(q'[select dbms_lob.substr(attribute_value, 128, 1) from user_cloud_vector_index_attributes
                    where index_name = :1 and attribute_name = :2]', c_idx, 'profile_name');
  if l_prof is null then
    raise_application_error(-20130, c_idx || ' does not exist: run P6 first');
  end if;
  l_emb := qv2(q'[select dbms_lob.substr(attribute_value, 400, 1) from user_cloud_ai_profile_attributes
                   where profile_name = upper(:1) and attribute_name = :2]', l_prof, 'embedding_model');
  l_own := upper(regexp_substr(l_emb, '^database:([A-Za-z][A-Za-z0-9_$#]*)\.([A-Za-z][A-Za-z0-9_$#]*)$', 1, 1, 'i', 1));
  l_mod := upper(regexp_substr(l_emb, '^database:([A-Za-z][A-Za-z0-9_$#]*)\.([A-Za-z][A-Za-z0-9_$#]*)$', 1, 1, 'i', 2));
  if l_own is null or l_own <> 'ASKORACLE' or l_mod is null then
    raise_application_error(-20131, 'the index profile ' || l_prof || ' does not name an ASKORACLE model: ' || nvl(l_emb, '<none>'));
  end if;
  l_mod := dbms_assert.simple_sql_name(l_mod);
  p('== P4B  stored embedding vs VECTOR_EMBEDDING(ASKORACLE.' || l_mod || ' USING content) on ' || c_idx);
  p('   model from the dictionary: ' || c_idx || ' -> ' || upper(l_prof) || ' -> ' || l_emb);
  p('   ' || rpad('object_name', 36) || lpad('chars', 7) || lpad('cosine distance', 18) || '   verdict');
  l_sql := 'select obj, len, vector_distance(embedding, vector_embedding(ASKORACLE.' || l_mod
        || ' using content as data), COSINE)'
        || '  from (select obj, length(content) len, content, embedding,'
        || '               row_number() over (partition by case when obj like ''PRB-AR-%'' then 1 else 2 end'
        || '                                  order by obj, ora_hash(dbms_lob.substr(content, 200, 1))) rn'
        || '          from (select json_value(v.attributes, ''$.object_name'' returning varchar2(1024)) obj,'
        || '                       v.content, v.embedding'
        || '                  from "' || dbms_assert.simple_sql_name(c_idx || '$VECTAB') || '" v'
        || '                 where dbms_lob.getlength(v.content) <= 1300))'
        || ' where rn <= 5 order by obj, rn';
  open rc for l_sql;
  loop
    fetch rc into l_obj, l_len, l_d;
    exit when rc%notfound;
    l_n := l_n + 1;
    l_dmax := greatest(l_dmax, l_d);
    if l_d <= c_eps then
      l_same := l_same + 1;
    end if;
    p('   ' || rpad(substr(l_obj, 1, 35), 36) || lpad(l_len, 7) || lpad(to_char(l_d, '9.99EEEE'), 18)
      || '   ' || case when l_d <= c_eps then 'identical' else 'DIFFERENT' end);
  end loop;
  close rc;
  p('');
  p('   ' || l_same || ' of ' || l_n || ' identical (cosine distance <= 1e-6); largest distance '
    || to_char(l_dmax, '9.99EEEE'));
  logv('P4B identical', l_same || '/' || l_n || ' max ' || to_char(l_dmax, '9.99EEEE'));
  if l_n = 0 then
    raise_application_error(-20132, 'no chunk of 1,300 characters or less in ' || c_idx || '$VECTAB');
  elsif l_same < l_n then
    p('VERDICT: the stored vector is NOT the embedding of CONTENT for every chunk. Find what is embedded');
    p('         before any harness number is used (PLAN.md 8.1 P4b).');
    raise_application_error(-20133, 'P4b: stored and recomputed embeddings differ');
  else
    p('VERDICT: the stored vector is VECTOR_EMBEDDING(model USING content): the harness can recompute it');
  end if;
end;
/

-- =============================================================================================
-- P6 / P10 / P14 (RAG_LAB): build the probe indexes the way 07_build_index.sql builds lab
-- indexes: index profile RAG_EMB_M0, drop-and-create (lab names only), wait until the initial
-- load has finished, stop the pipeline, record counts and times, create RAG_P_<name> with the
-- same embedding_model and check the pairing from USER_CLOUD_AI_PROFILE_ATTRIBUTES.
--   P6   p1  RAG_PRB_P1  1024/128  COSINE     with READ only on RAG_PROBE_DIR (the P6 question)
--   P10  p2  RAG_PRB_P2   640/0    COSINE     overlap 0
--        p3  RAG_PRB_P3   640/160  COSINE     overlap 25 %
--        x1  RAG_PRB_X1   640/640  COSINE     overlap = chunk size: expected to be rejected
--   P14  p4  RAG_PRB_P4  1024/128  EUCLIDEAN  for SCORE units (04_probes.py p14 measures)
-- P12 timing comes from the figures logged here.
-- =============================================================================================
declare
  c_timeout constant pls_integer := 1200;          -- seconds per index (5-7 small files)
  c_poll    constant pls_integer := 10;
  c_emb     constant varchar2(30) := 'RAG_EMB_M0';
  c_dir     constant varchar2(30) := 'RAG_PROBE_DIR';
  c_model   constant varchar2(128) := 'DATABASE:ASKORACLE.ALL_MINILM_L12_V2';
  type t_cfg is record (idx varchar2(30), chunk pls_integer, ovl pls_integer, metric varchar2(12), expect_ok boolean);
  type t_cfgs is table of t_cfg index by pls_integer;
  l_cfgs     t_cfgs;
  l_want     varchar2(400);
  l_prov     varchar2(64);
  l_cred     varchar2(128);
  l_reg      varchar2(64);
  l_comp     varchar2(400);
  l_llm      varchar2(128);
  l_expected number;
  l_roles    varchar2(4000);

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function red(s varchar2) return varchar2 is
    r varchar2(32767) := s;
  begin
    r := regexp_replace(r, 'ocid1\.[A-Za-z0-9._-]+', '<ocid>');
    r := regexp_replace(r, 'opc[-_]request[-_]id[^ ,;]*', '<request-id>', 1, 0, 'i');
    r := regexp_replace(r, '(https?|file)://[^ "'']*', '<url>');
    r := regexp_replace(r, '[a-z]+-[a-z]+-[0-9]+', '<region>');
    r := regexp_replace(r, '(^|[^0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9.]|$)', '\1<ip>\3');
    r := regexp_replace(r, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '<email>');
    r := regexp_replace(r, '/home/[A-Za-z0-9._-]+', '/home/<user>');
    return r;
  end;

  function qv1(q varchar2, b1 varchar2) return varchar2 is
    v varchar2(4000);
  begin
    execute immediate q into v using b1;
    return v;
  exception
    when no_data_found then return null;
  end;

  function qv2(q varchar2, b1 varchar2, b2 varchar2) return varchar2 is
    v varchar2(4000);
  begin
    execute immediate q into v using b1, b2;
    return v;
  exception
    when no_data_found then return null;
  end;

  function qn1(q varchar2, b1 varchar2) return number is
    v number;
  begin
    execute immediate q into v using b1;
    return v;
  end;

  procedure logv(i varchar2, v varchar2) is
  begin
    execute immediate 'insert into RAG_PRB_LOG (probe, item, val) values (:1, :2, :3)'
      using '&probe', substr(i, 1, 128), substr(v, 1, 4000);
    commit;
  end;

  function secs(a timestamp with time zone, b timestamp with time zone) return number is
    d interval day(3) to second(3) := b - a;
  begin
    return extract(day from d) * 86400 + extract(hour from d) * 3600 + extract(minute from d) * 60
         + extract(second from d);
  end;

  function ix_attr(i varchar2, a varchar2) return varchar2 is
  begin
    return qv2(q'[select dbms_lob.substr(attribute_value, 4000, 1) from user_cloud_vector_index_attributes
                   where index_name = :1 and attribute_name = :2]', i, a);
  end;

  function pr_attr(pr varchar2, a varchar2) return varchar2 is
  begin
    return qv2(q'[select dbms_lob.substr(attribute_value, 4000, 1) from user_cloud_ai_profile_attributes
                   where profile_name = upper(:1) and attribute_name = :2]', pr, a);
  end;

  -- -1 while the vector table does not exist yet
  function vt_num(vt varchar2, expr varchar2) return number is
    n number;
  begin
    execute immediate 'select ' || expr || ' from "' || dbms_assert.simple_sql_name(vt) || '"' into n;
    return n;
  exception
    when others then
      if sqlcode = -942 then return -1; end if;
      raise;
  end;

  procedure add_cfg(i varchar2, c pls_integer, o pls_integer, m varchar2, ok boolean) is
    n pls_integer := l_cfgs.count + 1;
  begin
    l_cfgs(n).idx := i;
    l_cfgs(n).chunk := c;
    l_cfgs(n).ovl := o;
    l_cfgs(n).metric := m;
    l_cfgs(n).expect_ok := ok;
  end;

  procedure show_whitelisted(pr varchar2) is
    rc sys_refcursor;
    k  varchar2(128);
    v  varchar2(4000);
    n  pls_integer := 0;
  begin
    open rc for q'[select attribute_name, dbms_lob.substr(attribute_value, 200, 1)
                     from user_cloud_ai_profile_attributes where profile_name = :1 order by attribute_name]' using pr;
    loop
      fetch rc into k, v;
      exit when rc%notfound;
      if k in ('provider', 'model', 'embedding_model', 'vector_index_name', 'conversation', 'temperature',
               'max_tokens', 'seed') then
        p('     ' || rpad(k, 20) || v);
      else
        n := n + 1;
      end if;
    end loop;
    close rc;
    p('     (' || n || ' further attributes not shown: credential, compartment, region)');
  end;

  procedure drop_leftovers(i varchar2, rag varchar2) is
    l_vt varchar2(160) := i || '$VECTAB';
  begin
    -- only names this block owns: RAG_PRB_<x> and RAG_P_PRB_<x>
    if not regexp_like(i, '^RAG_PRB_[A-Z0-9]{1,8}$') or not regexp_like(rag, '^RAG_P_PRB_[A-Z0-9]{1,8}$') then
      raise_application_error(-20124, 'refusing to drop ' || i || ' / ' || rag || ': not a probe name');
    end if;
    if qn1('select count(*) from user_cloud_vector_indexes where index_name = :1', i) > 0 then
      execute immediate 'begin dbms_cloud_ai.drop_vector_index(index_name => :1, include_data => true, force => true); end;'
        using i;
      p('   dropped the previous ' || i);
    end if;
    if qn1('select count(*) from user_cloud_pipelines where pipeline_name = :1', i || '$VECPIPELINE') > 0 then
      execute immediate 'begin dbms_cloud_pipeline.drop_pipeline(pipeline_name => :1, force => true); end;'
        using i || '$VECPIPELINE';
      p('   dropped a leftover pipeline ' || i || '$VECPIPELINE');
    end if;
    if qn1('select count(*) from user_tables where table_name = :1', l_vt) > 0 then
      execute immediate 'drop table "' || dbms_assert.simple_sql_name(l_vt) || '" purge';
      p('   dropped a leftover table ' || l_vt);
    end if;
    if qn1('select count(*) from user_cloud_ai_profiles where profile_name = :1', rag) > 0 then
      execute immediate 'begin dbms_cloud_ai.drop_profile(profile_name => :1, force => true); end;' using rag;
      p('   dropped the previous profile ' || rag);
    end if;
  end;

  -- after an error during a build: stop the index's pipeline (default refresh_rate would re-scan the
  -- directory on its own), then let the caller raise the error again. The pipeline_name attribute
  -- may not have been read yet; then the name drop_leftovers knows is used when it exists.
  procedure stop_after_error(i varchar2, pipe varchar2) is
    l_p varchar2(128) := pipe;
    l_s varchar2(64);
  begin
    if l_p is null and qn1('select count(*) from user_cloud_pipelines where pipeline_name = :1', i || '$VECPIPELINE') > 0 then
      l_p := i || '$VECPIPELINE';
    end if;
    if l_p is null then
      p('!! no pipeline found for ' || i || ': nothing to stop');
      return;
    end if;
    execute immediate 'begin dbms_cloud_pipeline.stop_pipeline(pipeline_name => :1, force => true); end;' using l_p;
    l_s := qv1('select status from user_cloud_pipelines where pipeline_name = :1', l_p);
    p('!! pipeline ' || l_p || ' stopped before the error is raised again; status ' || nvl(l_s, '<no row>'));
  exception
    when others then
      p('!! could not stop pipeline ' || nvl(l_p, i || '$VECPIPELINE') || ': ' || red(sqlerrm)
        || ' - stop it by hand (DBMS_CLOUD_PIPELINE.STOP_PIPELINE) before anything else');
  end;

  procedure build(c t_cfg) is
    l_rag     varchar2(128) := 'RAG_P_' || substr(c.idx, 5);
    l_vt      varchar2(160) := c.idx || '$VECTAB';
    l_attr    clob;
    l_t0      timestamp with time zone;
    l_t1      timestamp with time zone;
    l_pipe    varchar2(128);
    l_st      varchar2(128);
    l_rows    number := -1;
    l_prev    number := -2;
    l_objs    number;
    l_tot     number := -1;
    l_act     number := 0;
    l_failed  number := 0;
    l_skipped number := 0;
    l_hfail   number := 0;
    l_stable  pls_integer := 0;
    l_el      number;
    l_ready   number;
    l_create  number;
    l_timeout boolean := false;
    l_chars   number;
    l_maxc    number;
    l_avgc    number;
    l_load    number;
    l_e1      varchar2(400);
    l_e2      varchar2(400);
    l_ip      varchar2(128);
    rc        sys_refcursor;
    l_name    varchar2(1024);
    l_status  varchar2(64);
    l_rl      number;
    l_ec      number;
    l_em      varchar2(4000);
    l_werr    varchar2(4000);
  begin
    if not regexp_like(c.idx, '^RAG_PRB_[A-Z0-9]{1,8}$') then
      raise_application_error(-20125, 'not a probe index name: ' || c.idx);
    end if;
    l_rag := dbms_assert.simple_sql_name(l_rag);
    p('');
    p('-- ' || c.idx || '   chunk_size ' || c.chunk || ', chunk_overlap ' || c.ovl || ', ' || c.metric
      || ', profile_name ' || c_emb || ', location ' || c_dir || ':*');
    drop_leftovers(c.idx, l_rag);

    select json_object('vector_db_provider'     value 'oracle',
                       'location'               value c_dir || ':*',
                       'profile_name'           value c_emb,
                       'vector_distance_metric' value c.metric,
                       'chunk_size'             value c.chunk,
                       'chunk_overlap'          value c.ovl,
                       'match_limit'            value 5,
                       'similarity_threshold'   value 0
                       returning clob)
      into l_attr from dual;

    l_t0 := systimestamp;
    begin
      execute immediate 'begin dbms_cloud_ai.create_vector_index(index_name => :1, attributes => :2); end;'
        using c.idx, l_attr;
    exception
      when others then
        if c.expect_ok then
          p('!! CREATE_VECTOR_INDEX failed after ' || to_char(secs(l_t0, systimestamp), 'fm99990.0') || ' s:');
          p('!! ' || red(sqlerrm));
          logv(c.idx || ' create', 'FAILED ' || red(sqlerrm));
          raise_application_error(-20120, c.idx || ' was not created (error above). For P6 this is the privilege '
            || 'finding: record it, grant the smallest privilege the error names, re-run P6.');
        else
          p('   rejected, as expected: ' || red(sqlerrm));
          logv(c.idx || ' create', 'REJECTED ' || red(sqlerrm));
          return;
        end if;
    end;
    l_t1 := systimestamp;
    l_create := secs(l_t0, l_t1);
    l_rows := vt_num(l_vt, 'count(*)');
    p('   CREATE_VECTOR_INDEX returned after ' || to_char(l_create, 'fm99990.0') || ' s; rows in ' || l_vt
      || ' at that moment: ' || case when l_rows < 0 then 'table not created yet' else to_char(l_rows) end);
    logv(c.idx || ' create_s', to_char(l_create, 'fm99990.0'));
    logv(c.idx || ' rows_at_return', l_rows);

    -- from here until the wait is over, any error stops the pipeline first, then is raised again: the
    -- status table and the history are read with dynamic SQL in a shape 23.26.1 has not confirmed
    begin
      l_pipe := ix_attr(c.idx, 'pipeline_name');
      if l_pipe is not null then
        l_pipe := dbms_assert.simple_sql_name(l_pipe);
        l_st := qv1('select status_table from user_cloud_pipelines where pipeline_name = :1', l_pipe);
        if l_st is not null then
          l_st := dbms_assert.simple_sql_name(l_st);
        end if;
      end if;
      p('   pipeline_name: ' || nvl(l_pipe, '<not in the index attributes>')
        || ', per-file status table: ' || case when l_st is null then 'none' else 'yes' end);
      logv(c.idx || ' pipeline_name', l_pipe);

      if not c.expect_ok then
        p('!! ACCEPTED although chunk_overlap >= chunk_size; it is dropped again');
        logv(c.idx || ' create', 'ACCEPTED');
        execute immediate 'begin dbms_cloud_ai.drop_vector_index(index_name => :1, include_data => true, force => true); end;'
          using c.idx;
        return;
      end if;

      -- wait for the initial load. Done when the per-file status table has rows and none is
      -- PENDING/RUNNING and the row count held still for one poll; without usable status rows,
      -- when every listed file is in the vector table, or the count held still for 60 s (a file
      -- that yields no chunk would otherwise hold the wait until the timeout).
      loop
        dbms_session.sleep(c_poll);
        l_el := secs(l_t0, systimestamp);
        l_rows := vt_num(l_vt, 'count(*)');
        l_objs := vt_num(l_vt, 'count(distinct json_value(attributes, ''$.object_name'' returning varchar2(1024)))');
        if l_st is not null then
          execute immediate 'select count(*), count(case when status in (''PENDING'', ''RUNNING'') then 1 end),'
                         || ' count(case when status = ''FAILED'' then 1 end),'
                         || ' count(case when status = ''SKIPPED'' then 1 end) from ' || l_st
            into l_tot, l_act, l_failed, l_skipped;
        end if;
        if l_pipe is not null then
          -- only runs of this build: a dropped index of the same name may have left history
          execute immediate q'[select count(*) from user_cloud_pipeline_history
                                where pipeline_name = :1 and status like 'FAIL%' and start_date >= :2]'
            into l_hfail using l_pipe, l_t0;
        end if;
        l_stable := case when l_rows > 0 and l_rows = l_prev then l_stable + 1 else 0 end;
        if l_rows <> l_prev or l_el > c_timeout then
          p('   t+' || lpad(round(l_el), 5) || ' s   rows ' || lpad(l_rows, 5) || '   files ' || lpad(l_objs, 3)
            || case when l_tot >= 0 then '   status table: ' || l_tot || ' files, ' || l_act || ' pending/running, '
                                        || l_failed || ' failed, ' || l_skipped || ' skipped' end);
        end if;
        exit when l_hfail > 0 and (l_tot <= 0 or l_act = 0);
        exit when l_tot > 0 and l_act = 0 and l_rows = l_prev and l_rows >= 0;
        exit when l_tot <= 0 and l_expected is not null and l_objs >= l_expected and l_rows = l_prev;
        exit when l_tot <= 0 and l_stable * c_poll >= 60;
        if l_el > c_timeout then
          l_timeout := true;
          exit;
        end if;
        l_prev := l_rows;
      end loop;
    exception
      when others then
        l_werr := red(sqlerrm);
        p('!! ' || c.idx || ': error while its load was awaited: ' || l_werr);
        stop_after_error(c.idx, l_pipe);
        raise;
    end;
    l_ready := secs(l_t0, systimestamp);

    -- stop the pipeline after the initial load (default refresh_rate would re-scan daily). The index
    -- attribute may be missing on 23.26.1; then the name drop_leftovers and stop_after_error know is used.
    if l_pipe is null and qn1('select count(*) from user_cloud_pipelines where pipeline_name = :1',
                              c.idx || '$VECPIPELINE') > 0 then
      l_pipe := c.idx || '$VECPIPELINE';
      p('   pipeline_name not in the index attributes; stopping ' || l_pipe);
    end if;
    if l_pipe is null then
      p('!! no pipeline name known for ' || c.idx || '; run_all check_pipelines reports any lab pipeline left running');
    end if;
    if l_pipe is not null then
      begin
        execute immediate 'begin dbms_cloud_pipeline.stop_pipeline(pipeline_name => :1, force => true); end;' using l_pipe;
      exception
        when others then
          p('   stop_pipeline: ' || red(sqlerrm));
      end;
      l_status := qv1('select status from user_cloud_pipelines where pipeline_name = :1', l_pipe);
      p('   pipeline ' || l_pipe || ' status after stop: ' || nvl(l_status, '<no row>'));
      logv(c.idx || ' pipeline_status', l_status);
      if upper(l_status) in ('STARTED', 'RUNNING') then
        raise_application_error(-20126, 'pipeline ' || l_pipe || ' is still ' || l_status || '; stop it before going on');
      end if;
    end if;

    if l_hfail > 0 then
      p('!! the pipeline history holds a failed run:');
      open rc for q'[select status, error_number, error_message from user_cloud_pipeline_history
                      where pipeline_name = :1 and status like 'FAIL%' and start_date >= :2
                      order by start_date]' using l_pipe, l_t0;
      loop
        fetch rc into l_status, l_ec, l_em;
        exit when rc%notfound;
        p('!!   ' || l_status || ' ORA-' || l_ec || ' ' || red(substr(l_em, 1, 300)));
      end loop;
      close rc;
    end if;

    execute immediate 'select count(*), count(distinct json_value(attributes, ''$.object_name'' returning varchar2(1024))),'
                   || ' nvl(sum(length(content)), 0), nvl(max(length(content)), 0), nvl(round(avg(length(content))), 0)'
                   || ' from "' || dbms_assert.simple_sql_name(l_vt) || '"'
      into l_rows, l_objs, l_chars, l_maxc, l_avgc;
    p('   loaded: ' || l_rows || ' chunks from ' || l_objs || ' files, ' || l_chars || ' characters'
      || ' (longest chunk ' || l_maxc || ', mean ' || l_avgc || ')');
    if l_st is not null then
      begin
        execute immediate 'select round((cast(max(end_time) as date) - cast(min(start_time) as date)) * 86400) from ' || l_st
          into l_load;
      exception
        when others then l_load := null;
      end;
    end if;
    p('   time: create call ' || to_char(l_create, 'fm99990.0') || ' s, ready after ' || to_char(l_ready, 'fm99990.0')
      || ' s (polled every ' || c_poll || ' s)' || case when l_load is not null then ', status-table load span ' || l_load || ' s' end
      || case when l_rows > 0 then ', ' || to_char(l_ready / l_rows, 'fm9990.000') || ' s per chunk' end);
    logv(c.idx || ' ready_s', to_char(l_ready, 'fm99990.0'));
    logv(c.idx || ' load_span_s', l_load);
    logv(c.idx || ' chunks', l_rows);
    logv(c.idx || ' files', l_objs);
    logv(c.idx || ' chars', l_chars);
    logv(c.idx || ' max_chunk_chars', l_maxc);
    if l_rows > 0 then
      logv(c.idx || ' s_per_chunk', to_char(l_ready / l_rows, 'fm9990.000'));
    end if;

    p('   per file (object_name, chunks, characters):');
    open rc for 'select json_value(attributes, ''$.object_name'' returning varchar2(1024)), count(*), sum(length(content))'
             || ' from "' || dbms_assert.simple_sql_name(l_vt) || '" group by json_value(attributes, ''$.object_name'''
             || ' returning varchar2(1024)) order by 1';
    loop
      fetch rc into l_name, l_rl, l_chars;
      exit when rc%notfound;
      p('     ' || rpad(nvl(l_name, '<null>'), 36) || lpad(l_rl, 6) || lpad(l_chars, 9));
    end loop;
    close rc;
    if l_st is not null then
      p('   pipeline status table (file, status, rows loaded, error):');
      begin
        open rc for 'select name, status, rows_loaded, error_code, error_message from ' || l_st || ' order by name';
        loop
          fetch rc into l_name, l_status, l_rl, l_ec, l_em;
          exit when rc%notfound;
          p('     ' || rpad(nvl(l_name, '<null>'), 36) || rpad(nvl(l_status, '-'), 11) || lpad(nvl(to_char(l_rl), '-'), 6)
            || case when l_ec is not null then '  ORA-' || l_ec || ' ' || red(substr(l_em, 1, 160)) end);
        end loop;
        close rc;
      exception
        when others then
          p('     status table not readable in the documented shape: ' || red(sqlerrm));
      end;
    end if;
    if l_timeout then
      raise_application_error(-20121, c.idx || ': the initial load did not finish within ' || c_timeout || ' s');
    end if;
    if l_hfail > 0 then
      -- the load job itself failed: the index may be partial for reasons other than extraction
      raise_application_error(-20119, c.idx || ': the pipeline job failed (history above); not usable, re-run after '
        || 'fixing the cause');
    end if;
    if l_rows = 0 then
      raise_application_error(-20127, c.idx || ' holds no chunk after its load (see the status above)');
    end if;
    -- a file that failed or gave no chunk is a P5 finding (e.g. an embedded-font PDF), not a
    -- build failure: the index stays, and the gap is printed where nobody can miss it
    if l_failed > 0 or l_skipped > 0 or (l_expected is not null and l_objs < l_expected) then
      p('!! WARN: ' || l_objs || ' of ' || nvl(to_char(l_expected), '?') || ' listed files have chunks; status table: '
        || greatest(l_failed, 0) || ' FAILED, ' || greatest(l_skipped, 0) || ' SKIPPED. Files without chunks:');
      begin
        open rc for 'select f.object_name from table(dbms_cloud.list_files(:1)) f where not exists'
                 || ' (select 1 from "' || dbms_assert.simple_sql_name(l_vt) || '" v'
                 || '   where json_value(v.attributes, ''$.object_name'' returning varchar2(1024)) = f.object_name)'
                 || ' order by 1' using c_dir;
        loop
          fetch rc into l_name;
          exit when rc%notfound;
          p('!!   ' || l_name);
        end loop;
        close rc;
      exception
        when others then
          p('!!   (not listable: ' || red(sqlerrm) || ')');
      end;
      logv(c.idx || ' files_without_chunks', to_char(nvl(l_expected, l_objs) - l_objs));
    end if;

    -- the RAG profile: the index profile's OCI settings (never printed) and embedding_model
    select json_object('provider'           value l_prov,
                       'credential_name'    value l_cred,
                       'region'             value l_reg,
                       'oci_compartment_id' value l_comp,
                       'model'              value l_llm,
                       'embedding_model'    value l_want,
                       'vector_index_name'  value c.idx,
                       'conversation'       value 'false' format json,
                       'temperature'        value 0,
                       'max_tokens'         value 1024
                       returning clob)
      into l_attr from dual;
    execute immediate 'begin dbms_cloud_ai.create_profile(profile_name => :1, attributes => :2, status => ''enabled'','
                   || ' description => :3); end;'
      using l_rag, l_attr, 'RAG lab probe profile for ' || c.idx;

    -- PAIRING GUARD (PLAN.md 0.4): both sides embed with the same model, read back from the dictionary
    l_ip := ix_attr(c.idx, 'profile_name');
    l_e1 := pr_attr(l_ip, 'embedding_model');
    l_e2 := pr_attr(l_rag, 'embedding_model');
    if upper(l_ip) <> c_emb or l_e1 is null or l_e2 is null
       or upper(replace(l_e1, ' ')) <> upper(replace(l_e2, ' ')) then
      raise_application_error(-20122, 'PAIRING GUARD: ' || c.idx || ' profile ' || nvl(l_ip, '<none>') || ' embeds with '
        || nvl(l_e1, '<none>') || ', ' || l_rag || ' with ' || nvl(l_e2, '<none>'));
    end if;
    p('   ' || l_rag || ' created. Pairing guard: ' || upper(l_ip) || ' and ' || l_rag || ' both use ' || l_e2);
    logv(c.idx || ' pairing', l_e2);
    show_whitelisted(l_rag);
  end;
begin
  if '&probe' not in ('P6', 'P10', 'P14') then
    return;
  end if;
  if '&probe' = 'P6' then
    p('== P6  build p1 with READ only on ' || c_dir || ' (what does a file-backed index need?); P9 and P12 use it');
    add_cfg('RAG_PRB_P1', 1024, 128, 'COSINE', true);
  elsif '&probe' = 'P10' then
    p('== P10  which chunk_overlap values CREATE_VECTOR_INDEX accepts (p2 overlap 0, p3 overlap 25 %, x1 overlap = chunk)');
    add_cfg('RAG_PRB_P2', 640, 0, 'COSINE', true);
    add_cfg('RAG_PRB_P3', 640, 160, 'COSINE', true);
    add_cfg('RAG_PRB_X1', 640, 640, 'COSINE', false);
  else
    p('== P14  build p4 with EUCLIDEAN for the SCORE-units probe (04_probes.py p14 measures it)');
    add_cfg('RAG_PRB_P4', 1024, 128, 'EUCLIDEAN', true);
  end if;

  -- the index profile must exist and embed with M0 (06_profiles.sql M0 creates it)
  l_want := pr_attr(c_emb, 'embedding_model');
  if l_want is null then
    raise_application_error(-20123, c_emb || ' is missing or has no embedding_model: run 06_profiles.sql M0 first');
  end if;
  if upper(replace(l_want, ' ')) <> c_model then
    raise_application_error(-20128, c_emb || ' embeds with ' || l_want || ', not ' || c_model);
  end if;
  execute immediate
    q'[select max(case when attribute_name = 'provider'           then dbms_lob.substr(attribute_value, 64, 1) end),
              max(case when attribute_name = 'credential_name'    then dbms_lob.substr(attribute_value, 128, 1) end),
              max(case when attribute_name = 'region'             then dbms_lob.substr(attribute_value, 64, 1) end),
              max(case when attribute_name = 'oci_compartment_id' then dbms_lob.substr(attribute_value, 400, 1) end),
              max(case when attribute_name = 'model'              then dbms_lob.substr(attribute_value, 128, 1) end)
         from user_cloud_ai_profile_attributes where profile_name = :1]'
    into l_prov, l_cred, l_reg, l_comp, l_llm using c_emb;
  if l_prov is null or l_cred is null or l_reg is null or l_comp is null or l_llm is null then
    raise_application_error(-20129, c_emb || ' lacks an OCI setting (values not shown); re-run 06_profiles.sql M0');
  end if;

  p('   privileges RAG_LAB holds on ' || c_dir || ':');
  declare
    rc sys_refcursor;
    l  varchar2(200);
  begin
    open rc for q'[select privilege from user_tab_privs where table_name = :1 order by privilege]' using c_dir;
    loop
      fetch rc into l;
      exit when rc%notfound;
      p('     ' || l);
    end loop;
    close rc;
  end;
  execute immediate q'[select listagg(role, ', ') within group (order by role) from session_roles]' into l_roles;
  p('   roles enabled in this session: ' || nvl(l_roles, 'none'));
  begin
    execute immediate 'select count(*) from table(dbms_cloud.list_files(:1))' into l_expected using c_dir;
    p('   files DBMS_CLOUD.LIST_FILES sees in ' || c_dir || ': ' || l_expected);
  exception
    when others then
      l_expected := null;
      p('   DBMS_CLOUD.LIST_FILES(' || c_dir || ') failed: ' || red(sqlerrm));
  end;
  logv('&probe files_listed', l_expected);

  for i in 1 .. l_cfgs.count loop
    build(l_cfgs(i));
  end loop;
end;
/

-- =============================================================================================
-- P9 (RAG_LAB, read-only): what the build of p1 left behind - index attributes (whitelisted
-- values), the pipeline and its history, the per-file status, the $VECTAB columns, and whether
-- $VECTAB carries a vector index (approximate search) or none (exact search).
-- =============================================================================================
declare
  c_idx constant varchar2(30) := 'RAG_PRB_P1';
  l_pipe varchar2(128);
  l_st   varchar2(128);
  rc     sys_refcursor;
  k      varchar2(1024);
  v      varchar2(4000);
  a1     varchar2(200);
  a2     varchar2(200);
  a3     varchar2(200);
  n      number;
  n2     number;
  l_cnt  pls_integer;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function red(s varchar2) return varchar2 is
    r varchar2(32767) := s;
  begin
    r := regexp_replace(r, 'ocid1\.[A-Za-z0-9._-]+', '<ocid>');
    r := regexp_replace(r, 'opc[-_]request[-_]id[^ ,;]*', '<request-id>', 1, 0, 'i');
    r := regexp_replace(r, '(https?|file)://[^ "'']*', '<url>');
    r := regexp_replace(r, '[a-z]+-[a-z]+-[0-9]+', '<region>');
    r := regexp_replace(r, '(^|[^0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9.]|$)', '\1<ip>\3');
    r := regexp_replace(r, '/home/[A-Za-z0-9._-]+', '/home/<user>');
    return r;
  end;

  function qv1(q varchar2, b1 varchar2) return varchar2 is
    x varchar2(4000);
  begin
    execute immediate q into x using b1;
    return x;
  exception
    when no_data_found then return null;
  end;
begin
  if '&probe' <> 'P9' then
    return;
  end if;
  if qv1('select index_name from user_cloud_vector_indexes where index_name = :1', c_idx) is null then
    raise_application_error(-20140, c_idx || ' does not exist: run P6 first');
  end if;
  p('== P9  build procedure: what CREATE_VECTOR_INDEX left behind for ' || c_idx);
  p('   index status: ' || qv1('select status from user_cloud_vector_indexes where index_name = :1', c_idx));
  p('   rows right after CREATE_VECTOR_INDEX returned (logged by P6): '
    || nvl(qv1(q'[select max(val) keep (dense_rank last order by utc) from RAG_PRB_LOG
                   where item = :1]', c_idx || ' rows_at_return'), 'not logged'));
  p('');
  p('   index attributes (values shown for the whitelisted names only):');
  l_cnt := 0;
  open rc for q'[select attribute_name, dbms_lob.substr(attribute_value, 200, 1)
                   from user_cloud_vector_index_attributes where index_name = :1 order by attribute_name]' using c_idx;
  loop
    fetch rc into k, v;
    exit when rc%notfound;
    l_cnt := l_cnt + 1;
    if lower(k) in ('location', 'profile_name', 'vector_db_provider', 'vector_distance_metric', 'chunk_size',
                    'chunk_overlap', 'match_limit', 'similarity_threshold', 'refresh_rate', 'vector_dimension',
                    'vector_table_name', 'pipeline_name', 'enable_sources') then
      p('     ' || rpad(k, 26) || v);
    else
      p('     ' || rpad(k, 26) || '<value not shown>');
    end if;
  end loop;
  close rc;
  p('     (' || l_cnt || ' attributes; any named like "wait" answers whether a wait-for-completion switch exists)');

  l_pipe := qv1(q'[select dbms_lob.substr(attribute_value, 128, 1) from user_cloud_vector_index_attributes
                    where index_name = :1 and attribute_name = 'pipeline_name']', c_idx);
  p('');
  if l_pipe is null then
    p('   no pipeline_name attribute');
  else
    l_pipe := dbms_assert.simple_sql_name(l_pipe);
    open rc for q'[select pipeline_type, status, to_char(last_execution, 'YYYY-MM-DD HH24:MI:SS'), status_table
                     from user_cloud_pipelines where pipeline_name = :1]' using l_pipe;
    fetch rc into a1, a2, a3, l_st;
    close rc;
    p('   pipeline ' || l_pipe || ': type ' || nvl(a1, '-') || ', status ' || nvl(a2, '-') || ', last execution '
      || nvl(a3, '-') || ', status table ' || case when l_st is null then 'none' else 'present' end);
    p('   pipeline history (status, start, end, error):');
    open rc for q'[select status, to_char(start_date, 'HH24:MI:SS'), to_char(end_date, 'HH24:MI:SS'),
                          nvl2(error_number, 'ORA-' || error_number || ' ' || substr(error_message, 1, 200), '')
                     from user_cloud_pipeline_history where pipeline_name = :1 order by start_date]' using l_pipe;
    loop
      fetch rc into a1, a2, a3, v;
      exit when rc%notfound;
      p('     ' || rpad(nvl(a1, '-'), 12) || rpad(nvl(a2, '-'), 10) || rpad(nvl(a3, '-'), 10) || red(v));
    end loop;
    close rc;
    p('   pipeline attributes (values for location, format, priority, interval and table_name only):');
    open rc for q'[select attribute_name, dbms_lob.substr(attribute_value, 160, 1)
                     from user_cloud_pipeline_attributes where pipeline_name = :1 order by attribute_name]' using l_pipe;
    loop
      fetch rc into k, v;
      exit when rc%notfound;
      p('     ' || rpad(k, 24) || case when lower(k) in ('location', 'format', 'priority', 'interval', 'table_name')
                                        then red(v) else '<value not shown>' end);
    end loop;
    close rc;
  end if;

  p('');
  p('   columns of ' || c_idx || '$VECTAB:');
  open rc for q'[select column_name, data_type from user_tab_columns where table_name = :1 order by column_id]'
    using c_idx || '$VECTAB';
  loop
    fetch rc into k, v;
    exit when rc%notfound;
    p('     ' || rpad(k, 16) || v);
  end loop;
  close rc;
  p('   indexes on ' || c_idx || '$VECTAB:');
  l_cnt := 0;
  open rc for q'[select i.index_name, i.index_type, nvl(listagg(c.column_name, ',') within group (order by c.column_position), '-')
                   from user_indexes i left join user_ind_columns c on c.index_name = i.index_name
                  where i.table_name = :1 group by i.index_name, i.index_type order by 1]' using c_idx || '$VECTAB';
  loop
    fetch rc into k, a1, v;
    exit when rc%notfound;
    l_cnt := l_cnt + 1;
    p('     ' || rpad(k, 34) || rpad(a1, 22) || v);
  end loop;
  close rc;
  execute immediate 'select count(*), count(distinct json_value(attributes, ''$.object_name'' returning varchar2(1024))) from "'
                    || dbms_assert.simple_sql_name(c_idx || '$VECTAB') || '"' into n, n2;
  p('     (' || l_cnt || ' index(es); ' || n || ' chunks from ' || n2 || ' files)');
  p('');
  p('VERDICT: ' || case when l_cnt = 0 then 'no index on $VECTAB: every retrieval is an exact scan'
                        else 'an index exists on $VECTAB (see its type above): retrieval may be approximate' end);
end;
/

-- =============================================================================================
-- P12 (RAG_LAB, read-only): build times of the probe indexes, from what P6/P10/P14 logged.
-- They replace the estimates in PLAN.md 4.3 together with the S1 build time 07 records.
-- =============================================================================================
declare
  rc sys_refcursor;
  l  varchar2(4000);
  n  pls_integer := 0;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;
begin
  if '&probe' <> 'P12' then
    return;
  end if;
  p('== P12  probe index build times (latest value logged for each index)');
  p('   ' || rpad('index', 14) || lpad('chunks', 8) || lpad('files', 7) || lpad('chars', 9) || lpad('create s', 10)
    || lpad('ready s', 9) || lpad('load span s', 13) || lpad('s/chunk', 9));
  open rc for q'[select '   ' || rpad(idx, 14)
                        || lpad(nvl(max(case when k = 'chunks'      then val end), '-'), 8)
                        || lpad(nvl(max(case when k = 'files'       then val end), '-'), 7)
                        || lpad(nvl(max(case when k = 'chars'       then val end), '-'), 9)
                        || lpad(nvl(max(case when k = 'create_s'    then val end), '-'), 10)
                        || lpad(nvl(max(case when k = 'ready_s'     then val end), '-'), 9)
                        || lpad(nvl(max(case when k = 'load_span_s' then val end), '-'), 13)
                        || lpad(nvl(max(case when k = 's_per_chunk' then val end), '-'), 9)
                   from (select regexp_substr(item, '^[^ ]+') idx, regexp_substr(item, '[^ ]+$') k, val,
                                row_number() over (partition by item order by utc desc) rn
                           from RAG_PRB_LOG where probe in ('P6', 'P10', 'P14'))
                  where rn = 1 and idx like 'RAG\_PRB\_P%' escape '\'
                  group by idx order by idx]';
  loop
    fetch rc into l;
    exit when rc%notfound;
    p(l);
    n := n + 1;
  end loop;
  close rc;
  if n = 0 then
    p('   (nothing logged yet: run P6, P10 and P14 first)');
  end if;
  p('   ready s includes up to one poll interval (10 s); load span is the pipeline''s own start-to-end time');
end;
/

-- =============================================================================================
-- P13 / P15 (RAG_LAB): the p1 pipeline run once by hand after a change to the probe directory
-- made through 01_stage_files.sh:
--   before P13: 04_probes.py stage-dir --stage p13 --dir D; 01_stage_files.sh probe D --allow-non-ascii
--               (an ASCII-named and an Arabic-named copy of the same reportlab PDF)
--   before P15: 01_stage_files.sh probe-rm PRB-P13-EN-COPY.pdf
-- RUN_PIPELINE_ONCE needs the pipeline stopped (ORA-20044 otherwise, brief 06); it is left stopped.
--   P13: are files with multibyte names skipped ([D3])? The ASCII copy is the positive control.
--   P15: do a deleted file's chunks stay in the index ("the pipeline only adds", brief 06)?
-- =============================================================================================
declare
  c_idx   constant varchar2(30) := 'RAG_PRB_P1';
  c_dir   constant varchar2(30) := 'RAG_PROBE_DIR';
  c_ascii constant varchar2(64) := 'PRB-P13-EN-COPY.pdf';
  l_arab  varchar2(256) := 'PRB-P13-AR-' || unistr('\0646\0633\062E\0629') || '.pdf';
  l_pipe  varchar2(128);
  l_st    varchar2(128);
  l_stat  varchar2(64);
  l_t0    timestamp with time zone;
  l_iv    interval day(3) to second(3);
  l_s     number;
  l_a0    number;
  l_a1    number;
  l_r0    number;
  l_r1    number;
  l_tot0  number;
  l_tot1  number;
  l_la    number := 0;
  l_lr    number := 0;
  l_err   varchar2(4000);
  rc      sys_refcursor;
  k       varchar2(1024);
  n       number;
  l_ec    number;
  l_em    varchar2(4000);

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function red(s varchar2) return varchar2 is
    r varchar2(32767) := s;
  begin
    r := regexp_replace(r, 'ocid1\.[A-Za-z0-9._-]+', '<ocid>');
    r := regexp_replace(r, 'opc[-_]request[-_]id[^ ,;]*', '<request-id>', 1, 0, 'i');
    r := regexp_replace(r, '(https?|file)://[^ "'']*', '<url>');
    r := regexp_replace(r, '[a-z]+-[a-z]+-[0-9]+', '<region>');
    r := regexp_replace(r, '(^|[^0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9.]|$)', '\1<ip>\3');
    r := regexp_replace(r, '/home/[A-Za-z0-9._-]+', '/home/<user>');
    return r;
  end;

  function qv1(q varchar2, b1 varchar2) return varchar2 is
    x varchar2(4000);
  begin
    execute immediate q into x using b1;
    return x;
  exception
    when no_data_found then return null;
  end;

  function chunks(obj varchar2) return number is
    x number;
  begin
    execute immediate 'select count(*) from "' || dbms_assert.simple_sql_name(c_idx || '$VECTAB')
                   || '" where json_value(attributes, ''$.object_name'' returning varchar2(1024)) = :1' into x using obj;
    return x;
  end;

  function total return number is
    x number;
  begin
    execute immediate 'select count(*) from "' || dbms_assert.simple_sql_name(c_idx || '$VECTAB') || '"' into x;
    return x;
  end;

  procedure logv(i varchar2, v varchar2) is
  begin
    execute immediate 'insert into RAG_PRB_LOG (probe, item, val) values (:1, :2, :3)'
      using '&probe', substr(i, 1, 128), substr(v, 1, 4000);
    commit;
  end;

  procedure list_dir is
  begin
    p('   files DBMS_CLOUD.LIST_FILES sees in ' || c_dir || ' (name, bytes):');
    l_la := 0;
    l_lr := 0;
    open rc for 'select object_name, bytes from table(dbms_cloud.list_files(:1)) order by object_name' using c_dir;
    loop
      fetch rc into k, n;
      exit when rc%notfound;
      p('     ' || rpad(k, 40) || lpad(n, 9));
      if k = c_ascii then
        l_la := 1;
      elsif k = l_arab then
        l_lr := 1;
      end if;
    end loop;
    close rc;
  end;

  procedure status_rows is
  begin
    if l_st is null then
      return;
    end if;
    p('   pipeline status table rows for the P13 files (file, status, rows loaded, error):');
    open rc for 'select name, status, rows_loaded, error_code, error_message from ' || l_st
             || q'[ where name like '%PRB-P13%' order by name]';
    loop
      fetch rc into k, l_stat, n, l_ec, l_em;
      exit when rc%notfound;
      p('     ' || rpad(nvl(k, '<null>'), 40) || rpad(nvl(l_stat, '-'), 11) || lpad(nvl(to_char(n), '-'), 6)
        || case when l_ec is not null then '  ORA-' || l_ec || ' ' || red(substr(l_em, 1, 160)) end);
    end loop;
    close rc;
  exception
    when others then
      p('     status table not readable in the documented shape: ' || red(sqlerrm));
  end;

  procedure run_once is
  begin
    l_stat := qv1('select status from user_cloud_pipelines where pipeline_name = :1', l_pipe);
    if upper(l_stat) in ('STARTED', 'RUNNING') then
      execute immediate 'begin dbms_cloud_pipeline.stop_pipeline(pipeline_name => :1, force => true); end;' using l_pipe;
      p('   pipeline was ' || l_stat || '; stopped first (RUN_PIPELINE_ONCE needs it stopped)');
    end if;
    l_t0 := systimestamp;
    begin
      execute immediate 'begin dbms_cloud_pipeline.run_pipeline_once(pipeline_name => :1); end;' using l_pipe;
      l_err := null;
    exception
      when others then
        l_err := red(sqlerrm);
    end;
    l_iv := systimestamp - l_t0;
    l_s := extract(day from l_iv) * 86400 + extract(hour from l_iv) * 3600 + extract(minute from l_iv) * 60
         + extract(second from l_iv);
    p('   RUN_PIPELINE_ONCE(' || l_pipe || '): ' || case when l_err is null then 'done' else 'ERROR ' || l_err end
      || ' after ' || to_char(l_s, 'fm99990.0') || ' s');
    logv(c_idx || ' run_once', nvl(l_err, 'ok') || ' ' || to_char(l_s, 'fm99990.0') || ' s');
    l_stat := qv1('select status from user_cloud_pipelines where pipeline_name = :1', l_pipe);
    if upper(l_stat) in ('STARTED', 'RUNNING') then
      execute immediate 'begin dbms_cloud_pipeline.stop_pipeline(pipeline_name => :1, force => true); end;' using l_pipe;
      l_stat := qv1('select status from user_cloud_pipelines where pipeline_name = :1', l_pipe);
    end if;
    p('   pipeline status afterwards: ' || nvl(l_stat, '<no row>'));
    if upper(l_stat) in ('STARTED', 'RUNNING') then
      raise_application_error(-20154, 'pipeline ' || l_pipe || ' is still ' || l_stat || ' after the run');
    end if;
    if l_err is not null then
      status_rows;
      p('');
      p('VERDICT: ERROR - RUN_PIPELINE_ONCE failed, so no verdict (the error above is the finding)');
      raise_application_error(-20155, 'RUN_PIPELINE_ONCE failed: ' || substr(l_err, 1, 300));
    end if;
  end;
begin
  if '&probe' not in ('P13', 'P15') then
    return;
  end if;
  l_pipe := qv1(q'[select dbms_lob.substr(attribute_value, 128, 1) from user_cloud_vector_index_attributes
                    where index_name = :1 and attribute_name = 'pipeline_name']', c_idx);
  if l_pipe is null then
    raise_application_error(-20150, c_idx || ' or its pipeline_name does not exist: run P6 first');
  end if;
  l_pipe := dbms_assert.simple_sql_name(l_pipe);
  l_st := qv1('select status_table from user_cloud_pipelines where pipeline_name = :1', l_pipe);
  if l_st is not null then
    l_st := dbms_assert.simple_sql_name(l_st);
  end if;

  if '&probe' = 'P13' then
    p('== P13  a file whose name has Arabic letters, next to an ASCII-named copy of the same bytes');
    list_dir;
    if l_la = 0 or l_lr = 0 then
      p('   (' || case when l_la = 0 then c_ascii || ' not listed' end
        || case when l_la = 0 and l_lr = 0 then '; ' end
        || case when l_lr = 0 then 'the Arabic-named copy not listed under its exact name' end || ')');
    end if;
    if l_la = 0 then
      raise_application_error(-20151, 'stage the P13 files first (04_probes.py stage-dir --stage p13, then '
        || '01_stage_files.sh probe <dir> --allow-non-ascii): ' || c_ascii || ' is not in ' || c_dir);
    end if;
    l_a0 := chunks(c_ascii);
    l_r0 := chunks(l_arab);
    l_tot0 := total;
    p('   chunks before: ASCII copy ' || l_a0 || ', Arabic-named copy ' || l_r0 || ', all ' || l_tot0);
    run_once;
    l_a1 := chunks(c_ascii);
    l_r1 := chunks(l_arab);
    l_tot1 := total;
    execute immediate 'select count(*) from "' || dbms_assert.simple_sql_name(c_idx || '$VECTAB')
                   || q'[" where json_value(attributes, '$.object_name' returning varchar2(1024)) like 'PRB-P13-AR-%']'
      into n;
    p('   chunks after : ASCII copy ' || l_a1 || ', Arabic-named copy ' || l_r1
      || ' (any name starting PRB-P13-AR-: ' || n || '), all ' || l_tot1);
    status_rows;
    logv('P13 chunks', 'ascii ' || l_a1 || ' arabic ' || l_r1 || ' arabic_prefix ' || n);
    p('');
    if l_a1 = 0 then
      p('VERDICT: INCONCLUSIVE - the ASCII-named copy was not loaded either, so the run did not pick up new files');
    elsif n = 0 then
      p('VERDICT: SKIPPED - the Arabic-named file was not vectorised; its ASCII-named twin gave ' || l_a1
        || ' chunks ([D3] holds on 23.26.1)');
    else
      p('VERDICT: LOADED - the Arabic-named file gave ' || n || ' chunks on this build, unlike [D3]');
    end if;
  else
    p('== P15  a file deleted from the directory: do its chunks leave the index?');
    list_dir;
    if l_la = 1 then
      raise_application_error(-20152, c_ascii || ' is still in ' || c_dir || ': run 01_stage_files.sh probe-rm '
        || c_ascii || ' first');
    end if;
    l_a0 := chunks(c_ascii);
    l_tot0 := total;
    if l_a0 = 0 then
      raise_application_error(-20153, c_ascii || ' has no chunk in ' || c_idx || ': P13 must have loaded it first');
    end if;
    p('   ' || c_ascii || ' is gone from the directory; its chunks in ' || c_idx || ' before the run: ' || l_a0
      || ' (all ' || l_tot0 || ')');
    run_once;
    l_a1 := chunks(c_ascii);
    l_tot1 := total;
    p('   its chunks after the run: ' || l_a1 || ' (all ' || l_tot1 || ')');
    status_rows;
    logv('P15 chunks', 'before ' || l_a0 || ' after ' || l_a1);
    p('');
    p(case when l_a1 = l_a0 then 'VERDICT: PERSIST - the deleted file''s ' || l_a1
                                 || ' chunks are still retrievable (the pipeline only adds)'
           when l_a1 = 0 then 'VERDICT: PURGED - the run removed the deleted file''s chunks'
           else 'VERDICT: CHANGED - ' || l_a0 || ' -> ' || l_a1 || ' chunks; inspect before claiming either way' end);
  end if;
end;
/
