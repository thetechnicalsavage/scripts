-- v1.3 - RAG tuning lab (brief 09): load one ONNX embedding model into ASKORACLE, check it,
--        and let RAG_LAB score it. The models stay in ASKORACLE for good (PLAN.md 0.3): no
--        script of this lab removes a model.
--        v1.3: Codex adversarial review: the key and the five accepted defines are checked at
--              SQL*Plus level before any PL/SQL sees them (A1, one query holding each value only in
--              a q'{...}' literal); each define is bound once at the top, so a hand run is prompted
--              at most once per value. rag_model_sha256 (A2 support): run_all.sh passes the pin it
--              has just checked inside the container; 03 validates its form and prints it on the
--              MODEL line and before a load. The hash itself is checked by run_all.sh, not here.
--        v1.2: VM-safety review: capacity gate before every load. ASKORACLE's quota on its
--              default tablespace (unlimited or at least 2x the file), room there including
--              autoextend headroom (at least 2x the file) and, in ARCHIVELOG, recovery-area
--              headroom of at least 5 GB; otherwise it stops and loads nothing.
--        v1.1: Codex review: the skip path says that MODEL_SIZE does not prove identity (P2 does).
--        v1.0: first version, PLAN.md v1.2 section 3 (load part).
--
-- Run as : ASKORACLE (it already owns ALL_MINILM_L12_V2 and holds CREATE MINING MODEL).
-- Usage  : define rag_cap_ts_room_bytes  = <bytes | na>      (run_all.sh sets all four from a
--          define rag_cap_log_mode        = <ARCHIVELOG | NOARCHIVELOG | na>   SYSDBA read taken
--          define rag_cap_fra_room_bytes  = <bytes | na>      just before the load; prompted
--          define rag_cap_fra_limit_bytes = <bytes | na>      otherwise; 'na' = not supplied)
--          define rag_model_sha256        = <64 lowercase hex | na>   the pin run_all.sh checked
--          @03_load_models.sql <KEY>
--          Every value may hold only letters, digits and _ . : - ; anything else stops the script
--          before any PL/SQL sees it (README_RUN.md, quoting risk in hand runs).
--            KEY  M0  ALL_MINILM_L12_V2        384  (already loaded: checked and granted only)
--                 M1  MULTILINGUAL_E5_SMALL    384  M2 MULTILINGUAL_E5_BASE   768
--                 M3  MULTILINGUAL_E5_LARGE   1024  M4 BGE_M3                1024
--                 M5  ARCTIC_EMBED_L_V2       1024  M6 ARABIC_TRIPLET_V2      768
--                 M1Q MULTILINGUAL_E5_SMALL_Q  384  (exploratory)
--          The file RAG_MODEL_DIR:<lowercase model name>.onnx must be staged first:
--            01_stage_files.sh model <KEY>
-- Re-run : safe.
--            model absent, file staged   -> capacity gate, then loaded (DBMS_VECTOR.LOAD_ONNX_MODEL,
--                                           default metadata)
--            model present, same size    -> skipped
--            model present, other size   -> STOP; the existing model is left exactly as it is
--            model present, file absent  -> skipped (size not re-checked; the note says so)
--            model absent, file absent   -> STOP
--          Then: dimensions must equal the key's, VECTOR_NORM of an English and an Arabic
--          embedding is printed, and SELECT ON MINING MODEL is granted to RAG_LAB.
-- The DB-vs-local parity check on 20 strings (PLAN.md P2) is a probe, not part of this load.
-- It, not MODEL_SIZE, proves a model's identity: 01_stage_files.sh checks the file's pinned
-- sha256 before staging, and a model skipped here as "same size" still has to pass P2.
--
-- Capacity gate (before LOAD_ONNX_MODEL only; the skip paths load nothing). Each figure is read
-- here first; if ASKORACLE cannot see the view (no DBA or V$ access), the SYSDBA figure that
-- run_all.sh defined seconds earlier is used; if neither exists the script stops:
--   quota   USER_TS_QUOTAS on the default tablespace: UNLIMITED (or the UNLIMITED TABLESPACE
--           privilege), or free quota of at least 2 x the file          (always read here)
--   room    DBA_FREE_SPACE + autoextend headroom of DBA_DATA_FILES in that tablespace, at least
--           2 x the file                                                (or rag_cap_ts_room_bytes)
--   redo    V$DATABASE.LOG_MODE; in ARCHIVELOG, V$RECOVERY_FILE_DEST limit - used + reclaimable
--           of at least 5 GB and a sized recovery area   (or rag_cap_log_mode / rag_cap_fra_*)

set verify off echo off feedback off termout on
set serveroutput on size unlimited format wrapped
whenever sqlerror exit failure
whenever oserror exit failure
alter session set nls_numeric_characters = '.,';

define model_key = '&1'
-- the accepted values, bound once: defined by run_all.sh, else prompted here, once each
define rag_cap_ts_room_bytes   = '&rag_cap_ts_room_bytes'
define rag_cap_log_mode        = '&rag_cap_log_mode'
define rag_cap_fra_room_bytes  = '&rag_cap_fra_room_bytes'
define rag_cap_fra_limit_bytes = '&rag_cap_fra_limit_bytes'
define rag_model_sha256        = '&rag_model_sha256'

-- Argument guard (Codex A1): this query is the first statement to hold the values, and only
-- inside q'{...}' literals, which a value from the allowed set (written out, no ranges) cannot
-- end. Output is off while it runs; the block after it stops the script unless the verdict is
-- exactly YES. The PL/SQL below still checks each value's precise form.
define rag_arg_re = '^[0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_.:-]{1,200}$'
define rag_args_ok = 'NO'
column rag_args_ok new_value rag_args_ok noprint
prompt checking the arguments (allowed: letters, digits and _ . : -)
set termout off
select case when regexp_like(q'{&model_key}', '&rag_arg_re', 'c')
             and regexp_like(q'{&rag_cap_ts_room_bytes}', '&rag_arg_re', 'c')
             and regexp_like(q'{&rag_cap_log_mode}', '&rag_arg_re', 'c')
             and regexp_like(q'{&rag_cap_fra_room_bytes}', '&rag_arg_re', 'c')
             and regexp_like(q'{&rag_cap_fra_limit_bytes}', '&rag_arg_re', 'c')
             and regexp_like(nvl(q'{&rag_model_sha256}', 'na'), '&rag_arg_re', 'c')
            then 'YES' else 'NO' end rag_args_ok
  from dual;
set termout on
column rag_args_ok clear
begin
  if '&rag_args_ok' = 'YES' then
    dbms_output.put_line('arguments: plain characters only');
  else
    raise_application_error(-20000, 'the key and the rag_cap_* and rag_model_sha256 values may hold only ' ||
      'letters, digits and _ . : - (README_RUN.md, quoting risk in hand runs); nothing was loaded');
  end if;
end;
/

declare
  l_key       varchar2(16) := upper(trim('&model_key'));
  l_model     varchar2(128);
  l_dims_exp  pls_integer;
  l_file      varchar2(200);
  l_exists    pls_integer;
  l_size      number;
  l_file_len  number;
  l_bf        bfile;
  l_dims      pls_integer;
  l_norm_en   number;
  l_norm_ar   number;
  l_action    varchar2(20);
  l_t0        pls_integer;
  l_secs      number;
  l_priv      pls_integer;
  l_sql       varchar2(1000);
  c_gb        constant number := 1073741824;
  -- figures a SYSDBA session read just before this run (run_all.sh); 'na' when not supplied
  c_cap_room  constant varchar2(40) := trim('&rag_cap_ts_room_bytes');
  c_cap_log   constant varchar2(40) := upper(trim('&rag_cap_log_mode'));
  c_cap_fra   constant varchar2(40) := trim('&rag_cap_fra_room_bytes');
  c_cap_lim   constant varchar2(40) := trim('&rag_cap_fra_limit_bytes');
  -- the pinned sha256 of the staged file, as run_all.sh checked it inside the container ('na'
  -- when nothing is staged or in a hand run); printed with the load record, not re-checked here
  c_sha       constant varchar2(64) := nvl(lower(trim('&rag_model_sha256')), 'na');
  -- a fixed English and Arabic probe; the Arabic is written with UNISTR so the client
  -- character set cannot change it ("annual leave policy")
  c_probe_en  constant varchar2(100) := 'annual leave policy for employees in the Gulf region';
  c_probe_ar  constant varchar2(200) := unistr('\0633\064A\0627\0633\0629 \0627\0644\0625\062C\0627\0632\0629 \0627\0644\0633\0646\0648\064A\0629');

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function gb(b number) return varchar2 is
  begin
    return to_char(round(b / c_gb, 2), 'fm999999990.00') || ' GB';
  end;

  -- a supplied SYSDBA figure: digits only, else null
  function cap_num(s varchar2) return number is
  begin
    if regexp_like(s, '^[0-9]{1,20}$') then return to_number(s); end if;
    return null;
  end;

  -- Capacity gate before a load of p_bytes. Raises (and nothing is loaded) when a figure is
  -- short or cannot be read at all. Prints what it used and where each figure came from.
  procedure capacity_gate(p_bytes number) is
    l_ts     varchar2(128);
    l_max    number;
    l_used   number;
    l_unlim  pls_integer;
    l_room   number;
    l_src    varchar2(20);
    l_mode   varchar2(20);
    l_lim    number;
    l_fra    number;
  begin
    -- quota: ASKORACLE's own views, always readable
    select default_tablespace into l_ts from user_users;
    select count(*) into l_unlim from session_privs where privilege = 'UNLIMITED TABLESPACE';
    begin
      select max_bytes, bytes into l_max, l_used from user_ts_quotas where tablespace_name = l_ts;
    exception
      when no_data_found then l_max := 0; l_used := 0;
    end;
    if l_unlim = 0 and l_max <> -1 and l_max - l_used < 2 * p_bytes then
      raise_application_error(-20010, 'capacity: ASKORACLE has ' || gb(greatest(l_max - l_used, 0)) ||
        ' of quota left on ' || l_ts || ', needs 2 x ' || gb(p_bytes) || '. Nothing loaded; ask the operator.');
    end if;
    p('capacity: quota on ' || l_ts || ' ' ||
      case when l_unlim > 0 or l_max = -1 then 'UNLIMITED' else gb(l_max - l_used) || ' left' end);

    -- room in the tablespace, autoextend headroom included
    begin
      execute immediate
        q'[select (select nvl(sum(bytes), 0) from dba_free_space where tablespace_name = :1)
                + (select nvl(sum(case when autoextensible = 'YES' then greatest(maxbytes, bytes) else bytes end)
                              - sum(bytes), 0)
                     from dba_data_files where tablespace_name = :2)
             from dual]' into l_room using l_ts, l_ts;
      l_src := 'read here';
    exception
      when others then
        if sqlcode not in (-942, -1031, -1039) then raise; end if;
        l_room := cap_num(c_cap_room);
        l_src := 'SYSDBA figure';
    end;
    if l_room is null then
      raise_application_error(-20011, 'capacity: cannot tell the room in ' || l_ts ||
        ' (no DBA view here and no SYSDBA figure). Nothing loaded; run through run_all.sh.');
    end if;
    if l_room < 2 * p_bytes then
      raise_application_error(-20012, 'capacity: ' || l_ts || ' has ' || gb(l_room) ||
        ' of room including autoextend, needs 2 x ' || gb(p_bytes) || '. Nothing loaded; ask the operator.');
    end if;
    p('capacity: room in ' || l_ts || ' ' || gb(l_room) || ' (' || l_src || ')');

    -- redo: in ARCHIVELOG the load's redo lands in the recovery area
    begin
      execute immediate 'select log_mode from v$database' into l_mode;
      l_src := 'read here';
    exception
      when others then
        if sqlcode not in (-942, -1031, -1039) then raise; end if;
        l_mode := case when c_cap_log in ('ARCHIVELOG', 'NOARCHIVELOG') then c_cap_log end;
        l_src := 'SYSDBA figure';
    end;
    if l_mode is null then
      raise_application_error(-20013, 'capacity: cannot tell the log mode (no V$DATABASE here and no ' ||
        'SYSDBA figure). Nothing loaded; run through run_all.sh.');
    end if;
    if l_mode = 'ARCHIVELOG' then
      begin
        execute immediate 'select nvl(sum(space_limit), 0), nvl(sum(space_limit), 0) - nvl(sum(space_used), 0)'
                       || ' + nvl(sum(space_reclaimable), 0) from v$recovery_file_dest' into l_lim, l_fra;
        l_src := 'read here';
      exception
        when others then
          if sqlcode not in (-942, -1031, -1039) then raise; end if;
          l_lim := cap_num(c_cap_lim);
          l_fra := cap_num(c_cap_fra);
          l_src := 'SYSDBA figure';
      end;
      if l_lim is null or l_fra is null then
        raise_application_error(-20014, 'capacity: ARCHIVELOG and the recovery-area headroom cannot be read. ' ||
          'Nothing loaded; run through run_all.sh or ask the operator.');
      end if;
      if l_lim = 0 or l_fra < 5 * c_gb then
        raise_application_error(-20015, 'capacity: ARCHIVELOG with ' ||
          case when l_lim = 0 then 'no sized recovery area' else gb(l_fra) || ' of recovery-area headroom' end ||
          ' (needs 5 GB). Nothing loaded; ask the operator.');
      end if;
      p('capacity: ARCHIVELOG, recovery-area headroom ' || gb(l_fra) || ' (' || l_src || ')');
    else
      p('capacity: ' || l_mode || ' (' || l_src || '), no recovery-area check needed');
    end if;
  end;
begin
  if sys_context('userenv', 'session_user') <> 'ASKORACLE' then
    raise_application_error(-20001, 'run as ASKORACLE: the models live there permanently');
  end if;
  if not regexp_like(c_sha, '^([0123456789abcdef]{64}|na)$') then
    raise_application_error(-20016, 'rag_model_sha256 must be 64 hexadecimal characters or na');
  end if;

  case l_key
    when 'M0'  then l_model := 'ALL_MINILM_L12_V2';       l_dims_exp := 384;
    when 'M1'  then l_model := 'MULTILINGUAL_E5_SMALL';   l_dims_exp := 384;
    when 'M2'  then l_model := 'MULTILINGUAL_E5_BASE';    l_dims_exp := 768;
    when 'M3'  then l_model := 'MULTILINGUAL_E5_LARGE';   l_dims_exp := 1024;
    when 'M4'  then l_model := 'BGE_M3';                  l_dims_exp := 1024;
    when 'M5'  then l_model := 'ARCTIC_EMBED_L_V2';       l_dims_exp := 1024;
    when 'M6'  then l_model := 'ARABIC_TRIPLET_V2';       l_dims_exp := 768;
    when 'M1Q' then l_model := 'MULTILINGUAL_E5_SMALL_Q'; l_dims_exp := 384;
    else raise_application_error(-20002, 'unknown model key ' || substr(l_key, 1, 8) ||
                                         ' (M0 M1 M2 M3 M4 M5 M6 M1Q)');
  end case;
  l_model := dbms_assert.simple_sql_name(l_model);
  l_file  := lower(l_model) || '.onnx';

  select count(*) into l_exists from user_mining_models where model_name = l_model;
  if l_exists > 0 then
    select model_size into l_size from user_mining_models where model_name = l_model;
  end if;

  if l_key = 'M0' then
    -- the baseline is Oracle's prebuilt model, loaded long before this lab; never reloaded
    if l_exists = 0 then
      raise_application_error(-20003, 'ASKORACLE.' || l_model || ' is missing; this lab expects it loaded');
    end if;
    l_action := 'present';
  else
    begin
      l_bf := bfilename('RAG_MODEL_DIR', l_file);
      if dbms_lob.fileexists(l_bf) = 1 then
        l_file_len := dbms_lob.getlength(l_bf);
      end if;
    exception
      when others then
        raise_application_error(-20004, 'cannot read RAG_MODEL_DIR (run 00b_directories.sql): ' || sqlerrm);
    end;

    if l_exists > 0 and l_file_len is not null and l_size <> l_file_len then
      raise_application_error(-20005, 'ASKORACLE.' || l_model || ' exists with MODEL_SIZE ' || l_size ||
        ' but the staged file has ' || l_file_len || ' bytes. Stopping: the loaded model is not ' ||
        'replaced by this script. Investigate which one is right.');
    elsif l_exists > 0 and l_file_len is not null then
      l_action := 'skipped';
      p('ASKORACLE.' || l_model || ' already loaded with the same size (' || l_size || ' bytes); ' ||
        'identity is proven by the P2 parity gate, not by size');
    elsif l_exists > 0 then
      l_action := 'skipped';
      p('ASKORACLE.' || l_model || ' already loaded (' || l_size || ' bytes); file not staged, size not re-checked');
    elsif l_file_len is null then
      raise_application_error(-20006, 'RAG_MODEL_DIR:' || l_file || ' not found. Stage it first: ' ||
        '01_stage_files.sh model ' || l_key);
    else
      select count(*) into l_priv from session_privs where privilege = 'CREATE MINING MODEL';
      if l_priv = 0 then
        raise_application_error(-20007, 'ASKORACLE lacks CREATE MINING MODEL; ask the operator (not granted by this lab)');
      end if;
      capacity_gate(l_file_len);
      p('loading ' || l_file || ' (' || l_file_len || ' bytes, expected sha256 ' || c_sha ||
        ', checked by run_all.sh) as ASKORACLE.' || l_model || ' ...');
      l_t0 := dbms_utility.get_time;
      -- No metadata argument: the documented default is
      --   {"function":"embedding","embeddingOutput":"embedding","input":{"input":["DATA"]}}
      -- which is what the converter emits (input 'input', output 'embedding').
      dbms_vector.load_onnx_model(
        directory  => 'RAG_MODEL_DIR',
        file_name  => l_file,
        model_name => l_model);
      l_secs := round((dbms_utility.get_time - l_t0) / 100, 1);
      select model_size into l_size from user_mining_models where model_name = l_model;
      l_action := 'loaded';
      p('loaded in ' || l_secs || ' s, MODEL_SIZE ' || l_size || ' bytes');
      if l_size <> l_file_len then
        p('NOTE: MODEL_SIZE differs from the file size; a re-run with the file staged will stop at the size check');
      end if;
    end if;
  end if;

  -- identity checks: dimensions and norms of one English and one Arabic embedding
  l_sql := 'select vector_dimension_count(vector_embedding(' || l_model || ' using :a as data)), ' ||
           'vector_norm(vector_embedding(' || l_model || ' using :b as data)), ' ||
           'vector_norm(vector_embedding(' || l_model || ' using :c as data)) from dual';
  execute immediate l_sql into l_dims, l_norm_en, l_norm_ar using c_probe_en, c_probe_en, c_probe_ar;
  p('dimensions ' || l_dims || ' (expected ' || l_dims_exp || '), VECTOR_NORM en ' ||
    to_char(l_norm_en, 'fm0.000000') || ', ar ' || to_char(l_norm_ar, 'fm0.000000'));
  if l_dims <> l_dims_exp then
    raise_application_error(-20008, 'ASKORACLE.' || l_model || ' returns ' || l_dims ||
      ' dimensions, expected ' || l_dims_exp || '. The model is left in place; do not use it.');
  end if;
  if abs(l_norm_en - 1) > 0.001 or abs(l_norm_ar - 1) > 0.001 then
    p('NOTE: embeddings are not unit-norm; COSINE, DOT and EUCLIDEAN will not rank alike (PLAN.md story 5)');
  end if;

  -- RAG_LAB scores the model; ASKORACLE keeps ownership
  select count(*) into l_exists from all_users where username = 'RAG_LAB';
  if l_exists = 0 then
    raise_application_error(-20009, 'user RAG_LAB does not exist (run 00_admin_prereqs.sql first)');
  end if;
  execute immediate 'grant select on mining model ' || l_model || ' to RAG_LAB';
  p('granted SELECT ON MINING MODEL ASKORACLE.' || l_model || ' to RAG_LAB');

  p('MODEL|key=' || l_key || '|name=' || l_model || '|size_bytes=' || l_size || '|dims=' || l_dims ||
    '|norm_en=' || to_char(l_norm_en, 'fm0.000000') || '|norm_ar=' || to_char(l_norm_ar, 'fm0.000000') ||
    '|action=' || l_action || '|sha256_expected=' || c_sha);
end;
/

prompt
prompt Embedding models in ASKORACLE:
set linesize 160 pagesize 100 heading on
column model_name format a26
column mb         format 9990.9
select model_name, mining_function, algorithm, round(model_size / 1048576, 1) mb
  from user_mining_models
 where algorithm = 'ONNX'
 order by model_name;
