-- v1.5 - RAG tuning lab (brief 09): build one lab vector index over RAG_CORPUS_DIR, wait for
--        its initial load, stop its pipeline, record what it holds, and create its RAG profile
--        with the same embedding model (pairing guard).
--        v1.5: public copy: the other app is named the existing HR app (comment only).
--        v1.4: Codex adversarial review: the arguments and the timeout are checked at SQL*Plus
--              level before any PL/SQL sees them (A1, one query holding each value only in a
--              q'{...}' literal), and every input, the timeout included, is checked in step 1
--              before a rebuild drops anything. Create, wait and stop run in ONE block (step 2)
--              whose handler stops the index's own pipeline on any error, a cancel included,
--              before the error goes on (A4). The pipeline is resolved from the index's
--              pipeline_name attribute, then <index>$VECPIPELINE, then the one <index>$...
--              pipeline, and its name is written right after the create: a PIPELINE| line and a
--              committed row of RAG_LAB.RAG_BUILD_LOG. Step 3 re-checks STOPPED under the same
--              kind of handler. The rebuild path says plainly that the old index goes first (A3:
--              kept as designed, lab indexes are disposable). Codex re-review: when the create
--              itself raised, the handler looks for its pipeline for 30 s more, then prints
--              PIPELINE|...|event=error_unresolved and says a pipeline may still be running.
--        v1.3: VM-safety review: the load counts as complete only when the pipeline says so (its
--              per-file status table has rows and none PENDING/RUNNING, or else its last history
--              run has ended) AND every file is in the vector table AND the counts held still over
--              3-6 polls (more for the larger models); the same test on the 'existing' path. The
--              pipeline is then stopped with force => false; force only on the timeout or failure
--              paths. The pipeline name falls back to <index>$... before any stop. Codex review:
--              block 3 records the build only when the pipeline status is exactly STOPPED.
--        v1.2: Codex re-review: an existing RAG profile must hold exactly the lab's attributes
--              (seed allowed, for P11); any other attribute stops the script.
--        v1.1: Codex review: an existing RAG profile with other attributes now stops the script
--              (it was dropped and recreated).
--        v1.0: first version, PLAN.md v1.2 sections 2.4, 2.5, 4.3 and 7.
--
-- Run as : RAG_LAB, after 06_profiles.sql <KEY>.
-- Usage  : define rag_build_timeout_min = <minutes>      (run_all.sh sets it; prompted otherwise)
--          @07_build_index.sql <cfg> <KEY> <chunk> <overlap> <metric> <k> <thr>
--            cfg     config_id from experiments.csv, e.g. 1, 8, 18. A trailing 'r' (e.g. 1r)
--                    means an identical REBUILD: the existing index is dropped and built again,
--                    and the old and new counts are compared.
--            KEY     M0 M1 M2 M3 M4 M5 M6 M1Q
--            chunk   chunk_size in characters, 50-4000
--            overlap chunk_overlap in characters, 0 to chunk/2
--            metric  COS | DOT | EUC | MAN   (COSINE, DOT, EUCLIDEAN, MANHATTAN)
--            k       match_limit, 1-100
--            thr     similarity_threshold, 0-1
--          Every value may hold only letters, digits and _ . : - ; anything else stops the
--          script before any PL/SQL sees it (README_RUN.md, quoting risk in hand runs).
--          Names follow from the arguments:
--            index        RAG_<KEY>_C<chunk>_O<overlap>_<metric>      e.g. RAG_M0_C1024_O128_COS
--            vector table <index>$VECTAB (from the dictionary)
--            RAG profile  RAG_P_<index without RAG_>                   e.g. RAG_P_M0_C1024_O128_COS
--            index profile RAG_EMB_<KEY>
-- Steps  : 1/4 checks every input and the prerequisites; for a rebuild it then drops the old
--          index (first, before the new one exists). 2/4 is one block: create, wait for the load,
--          stop the pipeline; on any error its handler stops the index's own pipeline, then the
--          error goes on. 3/4 checks the pipeline is STOPPED (stopping it again if a refresh
--          restarted it) and records the counts. 4/4 creates the RAG profile (pairing guard).
-- Record : right after the create, 'PIPELINE|cfg=..|index=..|pipeline=..|event=created' is printed
--          and the same row is committed to RAG_LAB.RAG_BUILD_LOG (created here if absent), so the
--          pipeline's name can be read while the load runs, and after anything ends the session.
-- Re-run : safe (idempotent). An existing index with the same settings is not rebuilt: the
--          script applies the same completion test (waiting if it is still loading), makes sure
--          its pipeline is stopped, re-reads its counts and re-checks the RAG profile. An existing index, or RAG profile, with
--          other settings under the same name stops the script. match_limit and similarity_threshold of an
--          existing index are reported, never changed here (08_set_query_knobs.sql does that).
--
-- Load completion (block 2): "signal" is the pipeline's own word that its run is over: the
--         status table (USER_CLOUD_PIPELINES.STATUS_TABLE) has rows and none is PENDING or
--         RUNNING, or, when that table cannot be read, the latest USER_CLOUD_PIPELINE_HISTORY
--         run has an END_DATE. Complete = signal + every listed file in the vector table + chunk
--         and file counts unchanged over N consecutive 15-second polls (N = 3 for M0, M1, M1Q;
--         4 for M2, M6; 6 for M3, M4, M5). A FAILED file in the status table, or a finished run
--         that left files out, stops the script. If neither the status table nor the history
--         can be read, the script stops the pipeline and stops: completion cannot be proven.
-- Guards: every drop, stop, enable or profile replacement is limited to objects of the
--         connected user RAG_LAB whose names start with RAG_. Nothing here reads or names
--         the existing HR app's objects. The last line is machine-readable for run_all.sh:
--           RESULT|cfg=..|index=..|rag_profile=..|action=created|existing|rebuilt|files=n/n|...|pipeline=..

set verify off echo off feedback off termout on
set serveroutput on size unlimited format wrapped
set linesize 400 trimout on
whenever sqlerror exit failure
whenever oserror exit failure
alter session set nls_numeric_characters = '.,';

define cfg_id     = '&1'
define model_key  = '&2'
define chunk_size = '&3'
define chunk_ovl  = '&4'
define metric     = '&5'
define match_k    = '&6'
define sim_thr    = '&7'
-- the build timeout, bound once: defined by run_all.sh, else prompted here, once
define rag_build_timeout_min = '&rag_build_timeout_min'

-- Argument guard (Codex A1): this query is the first statement to hold the values, and only
-- inside q'{...}' literals, which a value from the allowed set (written out, no ranges) cannot
-- end. Output is off while it runs; the block after it stops the script unless the verdict is
-- exactly YES. Step 1/4 then checks each value's precise form.
define rag_arg_re = '^[0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_.:-]{1,200}$'
define rag_args_ok = 'NO'
column rag_args_ok new_value rag_args_ok noprint
prompt checking the arguments (allowed: letters, digits and _ . : -)
set termout off
select case when regexp_like(q'{&cfg_id}', '&rag_arg_re', 'c')
             and regexp_like(q'{&model_key}', '&rag_arg_re', 'c')
             and regexp_like(q'{&chunk_size}', '&rag_arg_re', 'c')
             and regexp_like(q'{&chunk_ovl}', '&rag_arg_re', 'c')
             and regexp_like(q'{&metric}', '&rag_arg_re', 'c')
             and regexp_like(q'{&match_k}', '&rag_arg_re', 'c')
             and regexp_like(q'{&sim_thr}', '&rag_arg_re', 'c')
             and regexp_like(q'{&rag_build_timeout_min}', '&rag_arg_re', 'c')
            then 'YES' else 'NO' end rag_args_ok
  from dual;
set termout on
column rag_args_ok clear
begin
  if '&rag_args_ok' = 'YES' then
    dbms_output.put_line('arguments: plain characters only');
  else
    raise_application_error(-20000, 'the arguments and rag_build_timeout_min may hold only letters, digits ' ||
      'and _ . : - (README_RUN.md, quoting risk in hand runs); nothing was changed');
  end if;
end;
/

variable v_cfg        varchar2(16)
variable v_index      varchar2(128)
variable v_rag        varchar2(128)
variable v_embprof    varchar2(128)
variable v_model      varchar2(128)
variable v_emb        varchar2(400)
variable v_metric     varchar2(16)
variable v_vectab     varchar2(200)
variable v_action     varchar2(20)
variable v_expect     number
variable v_dims       number
variable v_k          number
variable v_thr        number
variable v_t0         number
variable v_old_chunks number
variable v_old_chars  number
variable v_chunks     number
variable v_chars      number
variable v_distinct   number
variable v_elapsed    number
variable v_pstatus    varchar2(64)
variable v_key        varchar2(16)
variable v_pipe       varchar2(400)
variable v_signal     varchar2(40)
variable v_stable_ch  number
variable v_stable_fi  number
variable v_chunk      number
variable v_ovl        number
variable v_timeout    number

prompt == 1/4 checks (for a rebuild, then the drop of the old index)

declare
  l_cfg      varchar2(16)  := trim('&cfg_id');
  l_key      varchar2(16)  := upper(trim('&model_key'));
  l_chunk_s  varchar2(16)  := trim('&chunk_size');
  l_ovl_s    varchar2(16)  := trim('&chunk_ovl');
  l_met_s    varchar2(16)  := upper(trim('&metric'));
  l_k_s      varchar2(16)  := trim('&match_k');
  l_thr_s    varchar2(16)  := trim('&sim_thr');
  l_tmo_s    varchar2(16)  := trim('&rag_build_timeout_min');
  l_chunk    pls_integer;
  l_ovl      pls_integer;
  l_k        pls_integer;
  l_thr      number;
  l_code     varchar2(3);
  l_metric   varchar2(16);
  l_model    varchar2(128);
  l_dims_exp pls_integer;
  l_index    varchar2(128);
  l_n        pls_integer;
  l_exists   boolean;
  l_bad      varchar2(4000);
  l_vectab   varchar2(200);
  l_dims     number;
  l_old_chunks number;
  l_old_chars  number;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function idx_attr(i varchar2, a varchar2) return varchar2 is
    l varchar2(4000);
  begin
    select dbms_lob.substr(attribute_value, 4000, 1) into l
      from user_cloud_vector_index_attributes where index_name = i and attribute_name = a;
    return l;
  exception
    when no_data_found then return null;
  end;

  function prof_attr(pr varchar2, a varchar2) return varchar2 is
    l varchar2(4000);
  begin
    select dbms_lob.substr(attribute_value, 4000, 1) into l
      from user_cloud_ai_profile_attributes where profile_name = pr and attribute_name = a;
    return l;
  exception
    when no_data_found then return null;
  end;

  -- the only objects this script may drop, stop or replace: RAG_LAB's own, named RAG_...
  procedure assert_lab(n varchar2) is
  begin
    if sys_context('userenv', 'session_user') <> 'RAG_LAB'
       or n is null or substr(n, 1, 4) <> 'RAG_'
       or dbms_assert.simple_sql_name(n) is null then
      raise_application_error(-20090, 'guard: refusing to touch ' || nvl(n, '<null>'));
    end if;
  end;

  function is_int(s varchar2, maxlen pls_integer) return boolean is
  begin
    return regexp_like(s, '^[0-9]{1,' || maxlen || '}$');
  end;
begin
  if sys_context('userenv', 'session_user') <> 'RAG_LAB' then
    raise_application_error(-20001, 'run as RAG_LAB');
  end if;

  -- 1. arguments: every one, the timeout included, before anything is dropped or created
  if not regexp_like(l_cfg, '^[0-9]{1,3}r?$') then
    raise_application_error(-20002, 'cfg must be a config_id like 1, 12 or 1r');
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
    else raise_application_error(-20003, 'unknown model key ' || substr(l_key, 1, 8));
  end case;
  if not is_int(l_chunk_s, 4) or to_number(l_chunk_s) not between 50 and 4000 then
    raise_application_error(-20004, 'chunk_size must be an integer 50-4000');
  end if;
  l_chunk := to_number(l_chunk_s);
  if not is_int(l_ovl_s, 4) or to_number(l_ovl_s) > floor(l_chunk / 2) then
    raise_application_error(-20005, 'chunk_overlap must be an integer 0 to chunk_size/2');
  end if;
  l_ovl := to_number(l_ovl_s);
  case l_met_s
    when 'COS' then l_code := 'COS'; l_metric := 'cosine';
    when 'COSINE' then l_code := 'COS'; l_metric := 'cosine';
    when 'DOT' then l_code := 'DOT'; l_metric := 'dot';
    when 'EUC' then l_code := 'EUC'; l_metric := 'euclidean';
    when 'EUCLIDEAN' then l_code := 'EUC'; l_metric := 'euclidean';
    when 'MAN' then l_code := 'MAN'; l_metric := 'manhattan';
    when 'MANHATTAN' then l_code := 'MAN'; l_metric := 'manhattan';
    else raise_application_error(-20006, 'metric must be COS, DOT, EUC or MAN');
  end case;
  if not is_int(l_k_s, 3) or to_number(l_k_s) not between 1 and 100 then
    raise_application_error(-20007, 'match_limit must be an integer 1-100');
  end if;
  l_k := to_number(l_k_s);
  if not regexp_like(l_thr_s, '^[0-9](\.[0-9]{1,4})?$') or to_number(l_thr_s) > 1 then
    raise_application_error(-20008, 'similarity_threshold must be a number 0-1 with at most 4 decimals');
  end if;
  l_thr := to_number(l_thr_s);
  if not is_int(l_tmo_s, 4) or to_number(l_tmo_s) not between 1 and 1440 then
    raise_application_error(-20020, 'rag_build_timeout_min must be minutes, 1-1440');
  end if;

  -- 2. names (all derived; each one checked before it reaches dynamic SQL or an API)
  l_index := 'RAG_' || l_key || '_C' || l_chunk || '_O' || l_ovl || '_' || l_code;
  assert_lab(l_index);
  :v_cfg     := l_cfg;
  :v_key     := l_key;
  :v_index   := l_index;
  :v_rag     := 'RAG_P_' || substr(l_index, 5);
  :v_embprof := 'RAG_EMB_' || l_key;
  :v_model   := dbms_assert.simple_sql_name(l_model);
  :v_emb     := 'database:ASKORACLE.' || l_model;
  :v_metric  := l_metric;
  :v_k       := l_k;
  :v_thr     := l_thr;
  :v_chunk   := l_chunk;
  :v_ovl     := l_ovl;
  :v_timeout := to_number(l_tmo_s);
  assert_lab(:v_rag);
  assert_lab(:v_embprof);

  -- 3. prerequisites: index profile paired with the expected model, model visible with the
  --    expected dimensions, credential present, corpus visible with ASCII file names only
  select count(*) into l_n from user_cloud_ai_profiles where profile_name = :v_embprof;
  if l_n = 0 then
    raise_application_error(-20010, :v_embprof || ' does not exist (run 06_profiles.sql ' || l_key || ')');
  end if;
  if nvl(upper(replace(prof_attr(:v_embprof, 'embedding_model'), ' ')), '-') <> upper(:v_emb) then
    raise_application_error(-20011, :v_embprof || ' embeds with ' ||
      nvl(prof_attr(:v_embprof, 'embedding_model'), '<none>') || ', expected ' || :v_emb);
  end if;
  select count(*) into l_n from all_mining_models where owner = 'ASKORACLE' and model_name = l_model;
  if l_n = 0 then
    raise_application_error(-20012, 'ASKORACLE.' || l_model || ' not visible (run 03_load_models.sql ' || l_key || ')');
  end if;
  execute immediate 'select vector_dimension_count(vector_embedding(ASKORACLE.' || :v_model ||
                    ' using :t as data)) from dual' into l_dims using 'dimension probe';
  :v_dims := l_dims;
  if l_dims <> l_dims_exp then
    raise_application_error(-20013, 'ASKORACLE.' || l_model || ' returns ' || :v_dims ||
      ' dimensions, expected ' || l_dims_exp);
  end if;
  select count(*) into l_n from user_credentials where credential_name = 'RAG_LAB_OCI_CRED';
  if l_n = 0 then
    raise_application_error(-20014, 'credential RAG_LAB_OCI_CRED missing (run 02_create_credential.py)');
  end if;
  select count(*) into l_n from table(dbms_cloud.list_files('RAG_CORPUS_DIR'));
  if l_n = 0 then
    raise_application_error(-20015, 'RAG_CORPUS_DIR is empty (run 01_stage_files.sh corpus)');
  end if;
  :v_expect := l_n;
  for f in (select object_name from table(dbms_cloud.list_files('RAG_CORPUS_DIR'))
             where not regexp_like(object_name, '^[A-Za-z0-9][A-Za-z0-9._-]*\.(pdf|docx)$')
             order by 1) loop
    l_bad := substr(l_bad || ' ' || f.object_name, 1, 3000);
  end loop;
  if l_bad is not null then
    raise_application_error(-20016, 'RAG_CORPUS_DIR holds files that are not ASCII-named pdf/docx:' || l_bad);
  end if;
  p('config ' || l_cfg || ': ' || l_index || ' on ASKORACLE.' || l_model || ' (' || :v_dims ||
    ' dims), ' || l_metric || ', ' || :v_expect || ' files in RAG_CORPUS_DIR');

  -- the build record (Codex A4): step 2/4 writes the pipeline's name here as soon as the index exists
  execute immediate 'create table if not exists RAG_BUILD_LOG (' ||
                    '  utc           timestamp default sys_extract_utc(systimestamp) not null,' ||
                    '  cfg           varchar2(16)  not null,' ||
                    '  index_name    varchar2(128) not null,' ||
                    '  pipeline_name varchar2(400),' ||
                    '  event         varchar2(32)  not null)';

  -- 4. existing index: same settings or stop; rebuild only when asked (cfg ending in r)
  select count(*) into l_n from user_cloud_vector_indexes where index_name = l_index;
  l_exists := l_n > 0;
  if l_exists then
    if upper(replace(nvl(idx_attr(l_index, 'location'), '-'), ' ')) <> 'RAG_CORPUS_DIR:*' then
      l_bad := l_bad || ' location';
    end if;
    if nvl(idx_attr(l_index, 'profile_name'), '-') <> :v_embprof then l_bad := l_bad || ' profile_name'; end if;
    if nvl(trim(idx_attr(l_index, 'chunk_size')), '-') <> to_char(l_chunk) then l_bad := l_bad || ' chunk_size'; end if;
    if nvl(trim(idx_attr(l_index, 'chunk_overlap')), '-') <> to_char(l_ovl) then l_bad := l_bad || ' chunk_overlap'; end if;
    if nvl(lower(trim(idx_attr(l_index, 'vector_distance_metric'))), '-') <> l_metric then
      l_bad := l_bad || ' vector_distance_metric';
    end if;
    if l_bad is not null then
      raise_application_error(-20017, l_index || ' exists with other settings (' || trim(l_bad) ||
        '); stopping rather than guessing which one is right');
    end if;
    if l_cfg like '%r' then
      l_vectab := nvl(idx_attr(l_index, 'vector_table_name'), l_index || '$VECTAB');
      select count(*) into l_n from user_tables where table_name = l_vectab;
      if l_n > 0 then
        execute immediate 'select count(*), nvl(sum(length(content)), 0) from ' ||
          dbms_assert.enquote_name(dbms_assert.simple_sql_name(l_vectab), false)
          into l_old_chunks, l_old_chars;
        :v_old_chunks := l_old_chunks;
        :v_old_chars  := l_old_chars;
        p('before rebuild: ' || l_old_chunks || ' chunks, ' || l_old_chars || ' characters');
      end if;
      p('REBUILD: the old lab index ' || l_index || ' is dropped FIRST (its vector table goes with it); ' ||
        'step 2/4 then creates the new one. Until step 2/4 succeeds no index of this name exists; ' ||
        're-run config ' || l_cfg || ' to build it.');
      assert_lab(l_index);
      dbms_cloud_ai.drop_vector_index(index_name => l_index, include_data => true, force => true);
      p(l_index || ' dropped for the identical rebuild (the old index is gone; step 2/4 creates the new one)');
      :v_action := 'rebuilt';
    else
      :v_action := 'existing';
      p(l_index || ' already exists with these settings; not rebuilt');
    end if;
  else
    :v_action := 'created';
    if l_cfg like '%r' then
      p('NOTE: config ' || l_cfg || ' asks for a rebuild but ' || l_index || ' does not exist; building it');
    end if;
  end if;
end;
/

prompt == 2/4 create, wait for the initial load (pipeline signal, every file present, counts stable), stop the pipeline

-- One block from the create to the stop (Codex A4): its handler stops the index's own pipeline
-- on any error, a cancel (ORA-01013) included, before the error goes on. There is no gap
-- between blocks in which a lost client could leave the pipeline running; a client that dies
-- mid-call does not end the call by itself (it runs on to its stop or timeout), and run_all.sh's
-- pipeline check at the end of every stage stays the backstop.
declare
  c_poll      constant pls_integer := 15;
  l_deadline  timestamp with time zone;
  l_vectab    varchar2(260);
  l_need      pls_integer;
  l_distinct  number := 0;
  l_chunks    number := 0;
  l_prev_ch   number := -1;
  l_prev_fi   number := -1;
  l_stable    pls_integer := 0;
  l_polls     pls_integer := 0;
  l_pipe      varchar2(400);
  l_st        varchar2(400);
  l_st_ok     boolean := true;
  l_hist_ok   boolean := true;
  l_st_n      number;
  l_st_act    number;
  l_st_fail   number;
  l_signal    varchar2(40);
  l_obj       varchar2(1000);
  l_attr      clob;
  l_status    varchar2(64);
  l_recorded  boolean := false;
  l_create_tried boolean := false;
  c           sys_refcursor;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  -- the only objects this block may enable or stop: RAG_LAB's own, named RAG_...
  procedure assert_lab(n varchar2) is
  begin
    if sys_context('userenv', 'session_user') <> 'RAG_LAB'
       or n is null or substr(n, 1, 4) <> 'RAG_'
       or dbms_assert.simple_sql_name(n) is null then
      raise_application_error(-20090, 'guard: refusing to touch ' || nvl(n, '<null>'));
    end if;
  end;

  function idx_attr(a varchar2) return varchar2 is
    l varchar2(4000);
  begin
    select dbms_lob.substr(attribute_value, 4000, 1) into l
      from user_cloud_vector_index_attributes where index_name = :v_index and attribute_name = a;
    return l;
  exception
    when no_data_found then return null;
  end;

  procedure count_now is
  begin
    execute immediate 'select count(distinct json_value(attributes, ''$.object_name'')), count(*) from ' || l_vectab
      into l_distinct, l_chunks;
  exception
    when others then
      if sqlcode = -942 then l_distinct := 0; l_chunks := 0; else raise; end if;
  end;

  -- the index's own pipeline (Codex A4): its pipeline_name attribute, else <index>$VECPIPELINE,
  -- else the one RAG_LAB pipeline named <index>$...; null when none of them gives one
  function resolve_pipe return varchar2 is
    l varchar2(400);
    n pls_integer;
  begin
    begin
      select dbms_lob.substr(attribute_value, 400, 1) into l
        from user_cloud_vector_index_attributes
       where index_name = :v_index and attribute_name = 'pipeline_name';
    exception
      when no_data_found then l := null;
    end;
    if l is null then
      select count(*) into n from user_cloud_pipelines where pipeline_name = :v_index || '$VECPIPELINE';
      if n = 1 then
        l := :v_index || '$VECPIPELINE';
      end if;
    end if;
    if l is null then
      select count(*) into n from user_cloud_pipelines
       where substr(pipeline_name, 1, length(:v_index) + 1) = :v_index || '$';
      if n = 1 then
        select pipeline_name into l from user_cloud_pipelines
         where substr(pipeline_name, 1, length(:v_index) + 1) = :v_index || '$';
      end if;
    end if;
    return l;
  end;

  function pipe_status(n varchar2) return varchar2 is
    l varchar2(64);
  begin
    select status into l from user_cloud_pipelines where pipeline_name = n;
    return l;
  exception
    when no_data_found then return '<absent>';
  end;

  -- the build record (Codex A4): the pipeline's name as a PIPELINE| line (in the step log when
  -- this block returns, error or not) and as a RAG_BUILD_LOG row committed at once (autonomous),
  -- readable while the load runs and after anything that ends the session
  procedure record_pipe(p_event varchar2) is
    pragma autonomous_transaction;
  begin
    p('PIPELINE|cfg=' || :v_cfg || '|index=' || :v_index || '|pipeline=' ||
      nvl(l_pipe, '<unresolved>') || '|event=' || p_event);
    execute immediate 'insert into RAG_BUILD_LOG (cfg, index_name, pipeline_name, event) values (:1, :2, :3, :4)'
      using :v_cfg, :v_index, l_pipe, p_event;
    commit;
    l_recorded := l_pipe is not null;
  exception
    when others then
      rollback;
      raise;
  end;

  -- stop the lab pipeline: force only when the load is abandoned (timeout, failure)
  procedure stop_it(p_force boolean) is
  begin
    if l_pipe is null then
      l_pipe := resolve_pipe;
    end if;
    if l_pipe is null then
      p('!! the pipeline of ' || :v_index || ' could not be identified; run_all.sh checks that no lab pipeline runs');
      return;
    end if;
    if substr(l_pipe, 1, 4) = 'RAG_'
       and sys_context('userenv', 'session_user') = 'RAG_LAB'
       and upper(pipe_status(l_pipe)) not in ('<ABSENT>', 'STOPPED') then
      begin
        dbms_cloud_pipeline.stop_pipeline(pipeline_name => l_pipe, force => p_force);
        p('pipeline ' || l_pipe || ' stopped' || case when p_force then ' (force)' end);
      exception
        when others then
          if p_force then raise; end if;
          p('NOTE: stop_pipeline(force => false) failed (' || substr(sqlerrm, 1, 120) || '); stopping with force');
          dbms_cloud_pipeline.stop_pipeline(pipeline_name => l_pipe, force => true);
      end;
    end if;
  end;

  -- the pipeline's own completion signal: 'done', 'active', 'failed' or 'none' (not readable yet)
  procedure read_signal is
    l_end timestamp with time zone;
    l_hs  varchar2(128);
  begin
    l_signal := 'none';
    if l_pipe is null then
      l_pipe := resolve_pipe;
    end if;
    if l_pipe is null then
      return;
    end if;
    if l_st_ok then
      begin
        if l_st is null then
          -- dynamic: the column set of USER_CLOUD_PIPELINES is not verified on 23.26.1 (P6)
          execute immediate 'select status_table from user_cloud_pipelines where pipeline_name = :1'
            into l_st using l_pipe;
        end if;
        if l_st is not null then
          execute immediate 'select count(*), count(case when upper(status) in (''PENDING'', ''RUNNING'') then 1 end),'
                         || ' count(case when upper(status) like ''FAIL%'' then 1 end) from '
                         || dbms_assert.qualified_sql_name(l_st)
            into l_st_n, l_st_act, l_st_fail;
          if l_st_fail > 0 and l_st_act = 0 then
            l_signal := 'failed';
          elsif l_st_n > 0 and l_st_act = 0 then
            l_signal := 'done';
          else
            l_signal := 'active';
          end if;
          :v_signal := 'status_table';
          return;
        end if;
      exception
        when others then
          l_st_ok := false;
          p('note: pipeline status table not readable (' || substr(sqlerrm, 1, 100) || '); using the run history');
      end;
    end if;
    if l_hist_ok then
      begin
        execute immediate 'select end_date, status from (select end_date, status from user_cloud_pipeline_history '
                       || 'where pipeline_name = :n order by start_date desc) where rownum = 1'
          into l_end, l_hs using l_pipe;
        -- only a run that ended and says it succeeded counts; a stopped or unknown one does not
        if l_end is null then
          l_signal := 'active';
        elsif upper(l_hs) like '%FAIL%' then
          l_signal := 'failed';
        elsif regexp_like(upper(l_hs), 'SUCCE|COMPLET|FINISH') then
          l_signal := 'done';
        else
          l_signal := 'active';
        end if;
        :v_signal := 'history';
      exception
        when no_data_found then
          l_signal := 'active';                        -- no run recorded yet
        when others then
          l_hist_ok := false;
          p('note: pipeline history not readable (' || substr(sqlerrm, 1, 100) || ')');
      end;
    end if;
  end;

  -- Codex A4: on any error from the create on, the index's own pipeline is stopped before the
  -- error goes on. This never raises, so SQL*Plus reports the original error; a stop that fails
  -- is printed for the operator. A create that raised may still have started a pipeline whose
  -- metadata shows a little later: then it looks again for 30 seconds before giving up, and says
  -- plainly that a pipeline may still be running.
  procedure stop_on_error is
  begin
    if l_pipe is null then
      l_pipe := resolve_pipe;
    end if;
    if l_pipe is null and l_create_tried then
      for i in 1 .. 6 loop
        dbms_session.sleep(5);
        l_pipe := resolve_pipe;
        exit when l_pipe is not null;
      end loop;
    end if;
    if l_pipe is null then
      if l_create_tried then
        p('PIPELINE|cfg=' || :v_cfg || '|index=' || :v_index || '|pipeline=<unresolved>|event=error_unresolved');
        p('!! error path: no pipeline of ' || :v_index || ' found after 30 s, but the create was called: one ' ||
          'may still be running. run_all.sh''s pipeline check lists it; stop it as RAG_LAB before anything else.');
      else
        p('error path: no pipeline of ' || :v_index || ' found (attribute, $VECPIPELINE name, prefix); nothing to stop');
      end if;
      return;
    end if;
    p('error path: stopping pipeline ' || l_pipe || ' of ' || :v_index);
    stop_it(true);
  exception
    when others then
      p('!! error path: could not stop pipeline ' || nvl(l_pipe, '<unknown>') || ' of ' || :v_index || ': ' ||
        substr(sqlerrm, 1, 200) || '. Stop it as RAG_LAB before anything else (PLAN.md 2.5).');
  end;

  function missing_files return varchar2 is
    l varchar2(4000);
  begin
    open c for 'select object_name from table(dbms_cloud.list_files(''RAG_CORPUS_DIR'')) minus ' ||
               'select json_value(attributes, ''$.object_name'') from ' || l_vectab || ' order by 1';
    loop
      fetch c into l_obj;
      exit when c%notfound;
      l := substr(l || ' ' || l_obj, 1, 3000);
    end loop;
    close c;
    return nvl(l, ' <none listed>');
  end;

  function failed_files return varchar2 is
    l varchar2(4000);
  begin
    if l_st is null or not l_st_ok then
      return ' (see USER_CLOUD_PIPELINE_HISTORY)';
    end if;
    open c for 'select name from ' || dbms_assert.qualified_sql_name(l_st) ||
               ' where upper(status) like ''FAIL%'' order by 1';
    loop
      fetch c into l_obj;
      exit when c%notfound;
      l := substr(l || ' ' || l_obj, 1, 3000);
    end loop;
    close c;
    return l;
  exception
    when others then
      return ' (status table not readable: ' || substr(sqlerrm, 1, 80) || ')';
  end;
begin
  l_deadline := systimestamp + numtodsinterval(:v_timeout, 'MINUTE');
  -- consecutive stable polls required: an embedding batch of the 560M models can take well
  -- over one poll, so a count that held still once proves nothing for them
  l_need := case :v_key when 'M3' then 6 when 'M4' then 6 when 'M5' then 6
                        when 'M2' then 4 when 'M6' then 4 else 3 end;

  if :v_action in ('created', 'rebuilt') then
    select json_object(
             'vector_db_provider'     value 'oracle',
             'location'               value 'RAG_CORPUS_DIR:*',
             'profile_name'           value :v_embprof,
             'vector_distance_metric' value :v_metric,
             'vector_dimension'       value :v_dims,
             'chunk_size'             value :v_chunk,
             'chunk_overlap'          value :v_ovl,
             'match_limit'            value :v_k,
             'similarity_threshold'   value :v_thr
             returning clob)
      into l_attr
      from dual;
    assert_lab(:v_index);
    :v_t0 := dbms_utility.get_time;
    l_create_tried := true;
    dbms_cloud_ai.create_vector_index(index_name => :v_index, attributes => l_attr);
    -- Codex A4: the pipeline's name is recorded before anything else can fail
    l_pipe := resolve_pipe;
    record_pipe('created');
    select status into l_status from user_cloud_vector_indexes where index_name = :v_index;
    if upper(l_status) <> 'ENABLED' then
      assert_lab(:v_index);
      dbms_cloud_ai.enable_vector_index(index_name => :v_index);
      p(:v_index || ' was ' || l_status || ' after create; enabled');
    end if;
    p(:v_index || ' created; initial load running');
  else
    l_pipe := resolve_pipe;
    record_pipe('existing');
  end if;
  :v_pipe   := l_pipe;
  :v_vectab := dbms_assert.simple_sql_name(nvl(idx_attr('vector_table_name'), :v_index || '$VECTAB'));
  l_vectab  := dbms_assert.enquote_name(:v_vectab, false);

  loop
    l_polls := l_polls + 1;
    count_now;
    read_signal;
    if l_pipe is not null and not l_recorded then
      record_pipe('resolved');                     -- the name was not known right after the create
    end if;
    if l_chunks = l_prev_ch and l_distinct = l_prev_fi then l_stable := l_stable + 1; else l_stable := 0; end if;
    l_prev_ch := l_chunks;
    l_prev_fi := l_distinct;
    dbms_application_info.set_action(substr(:v_index || ' ' || l_distinct || '/' || :v_expect, 1, 64));
    if mod(l_polls, 4) = 1 then
      p(to_char(sys_extract_utc(systimestamp), 'yyyy-mm-dd"T"hh24:mi:ss"Z"') || '  ' ||
        l_distinct || '/' || :v_expect || ' files, ' || l_chunks || ' chunks, pipeline ' ||
        nvl(l_pipe, '<unknown>') || ' ' || l_signal || ', stable ' || l_stable || '/' || l_need);
    end if;

    -- complete: the pipeline says its run is over, every file is in, and the counts held still
    exit when l_signal = 'done' and l_distinct = :v_expect and l_stable >= l_need;

    if not l_st_ok and not l_hist_ok then
      stop_it(true);
      raise_application_error(-20024, 'cannot prove that the load of ' || :v_index || ' finished: neither the ' ||
        'pipeline status table nor its history is readable. Pipeline stopped; see PROBES.md P6.');
    end if;
    if l_signal = 'failed' and l_stable >= l_need then
      stop_it(false);
      raise_application_error(-20022, 'the pipeline of ' || :v_index || ' reports failed files and its run is over at ' ||
        l_distinct || '/' || :v_expect || ' files. Failed:' || failed_files);
    end if;
    if l_signal = 'done' and l_distinct <> :v_expect and l_stable >= l_need then
      stop_it(false);
      raise_application_error(-20025, 'the pipeline run of ' || :v_index || ' is over but ' || l_distinct || ' of ' ||
        :v_expect || ' files reached the vector table. Missing:' || missing_files ||
        '. Rebuild with config id ' || :v_cfg || 'r, or investigate.');
    end if;
    if :v_action = 'existing' and upper(pipe_status(l_pipe)) = 'STOPPED' and l_signal <> 'done'
       and l_stable >= l_need then
      raise_application_error(-20021, :v_index || ' holds ' || l_distinct || ' of ' || :v_expect ||
        ' files, its pipeline is stopped and did not finish (' || l_signal || '). Rebuild it with config id ' ||
        :v_cfg || 'r, or investigate.');
    end if;
    if systimestamp > l_deadline then
      stop_it(true);
      raise_application_error(-20023, 'timeout after ' || :v_timeout || ' min: ' || l_distinct || ' of ' ||
        :v_expect || ' files loaded (pipeline ' || l_signal || '). Missing:' || missing_files);
    end if;
    dbms_session.sleep(c_poll);
  end loop;
  :v_stable_ch := l_chunks;
  :v_stable_fi := l_distinct;
  p('loaded: ' || l_distinct || ' of ' || :v_expect || ' files, ' || l_chunks || ' chunks (completion: ' ||
    :v_signal || ', counts stable over ' || l_stable || ' polls of ' || c_poll || ' s)');

  -- the load is complete: the pipeline is stopped here, in the block that created the index
  -- (Codex A4), with force => false as no run should be in progress; step 3/4 checks STOPPED
  stop_it(false);
  :v_pipe    := l_pipe;
  :v_pstatus := pipe_status(l_pipe);
exception
  when others then
    stop_on_error;
    raise;
end;
/

prompt == 3/4 check the pipeline is STOPPED and record the index

declare
  l_pipe   varchar2(400);
  l_n      pls_integer;
  l_vectab varchar2(260) := dbms_assert.enquote_name(:v_vectab, false);
  l_minlen number;
  l_maxlen number;
  l_chunks number;
  l_chars  number;
  l_distinct number;
  l_pstatus varchar2(64);

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;
begin
  l_pipe := :v_pipe;
  if l_pipe is null then
    begin
      select dbms_lob.substr(attribute_value, 400, 1) into l_pipe
        from user_cloud_vector_index_attributes
       where index_name = :v_index and attribute_name = 'pipeline_name';
    exception
      when no_data_found then l_pipe := null;
    end;
  end if;
  if l_pipe is null then
    -- the attribute is expected (P9); next the name the create gives it (Codex A4)
    select count(*) into l_n from user_cloud_pipelines where pipeline_name = :v_index || '$VECPIPELINE';
    if l_n = 1 then
      l_pipe := :v_index || '$VECPIPELINE';
    end if;
  end if;
  if l_pipe is null then
    -- last, the one pipeline named after the index
    select count(*) into l_n from user_cloud_pipelines
     where substr(pipeline_name, 1, length(:v_index) + 1) = :v_index || '$';
    if l_n <> 1 then
      raise_application_error(-20030, 'cannot tell which pipeline belongs to ' || :v_index || ' (' || l_n || ' candidates)');
    end if;
    select pipeline_name into l_pipe from user_cloud_pipelines
     where substr(pipeline_name, 1, length(:v_index) + 1) = :v_index || '$';
  end if;
  -- guard: a pipeline of RAG_LAB (USER_ view) whose name starts with RAG_
  select count(*) into l_n from user_cloud_pipelines where pipeline_name = l_pipe;
  if l_n = 0 or substr(l_pipe, 1, 4) <> 'RAG_' or sys_context('userenv', 'session_user') <> 'RAG_LAB' then
    raise_application_error(-20031, 'guard: pipeline ' || l_pipe || ' is not a RAG_LAB lab pipeline');
  end if;
  select status into l_pstatus from user_cloud_pipelines where pipeline_name = l_pipe;
  if upper(l_pstatus) <> 'STOPPED' then
    -- step 2/4 stopped it after the load; a scheduled refresh may have started it since. No run
    -- of the initial load is in progress any more, so a plain stop first
    begin
      dbms_cloud_pipeline.stop_pipeline(pipeline_name => l_pipe, force => false);
    exception
      when others then
        -- a scheduled refresh may have started meanwhile; it must not keep running unattended.
        -- The counts are compared with block 2's below, so a changed index cannot pass.
        p('NOTE: stop_pipeline(force => false) failed (' || substr(sqlerrm, 1, 120) || '); stopping with force');
        dbms_cloud_pipeline.stop_pipeline(pipeline_name => l_pipe, force => true);
    end;
    select status into l_pstatus from user_cloud_pipelines where pipeline_name = l_pipe;
    p('pipeline ' || l_pipe || ' was running again; stopped (status ' || l_pstatus || ')');
  else
    p('pipeline ' || l_pipe || ' is STOPPED');
  end if;
  :v_pstatus := l_pstatus;
  -- only STOPPED counts (a STOPPING or STOP_REQUESTED pipeline could still load): no RESULT line
  if upper(nvl(l_pstatus, '-')) <> 'STOPPED' then
    raise_application_error(-20032, 'pipeline ' || l_pipe || ' is ' || nvl(l_pstatus, '<no status>') ||
      ', not STOPPED, after the stop; investigate before recording the build');
  end if;

  execute immediate 'select count(*), nvl(sum(length(content)), 0), ' ||
                    'count(distinct json_value(attributes, ''$.object_name'')), ' ||
                    'min(length(content)), max(length(content)) from ' || l_vectab
    into l_chunks, l_chars, l_distinct, l_minlen, l_maxlen;
  :v_chunks   := l_chunks;
  :v_chars    := l_chars;
  :v_distinct := l_distinct;
  if l_chunks <> :v_stable_ch or l_distinct <> :v_stable_fi then
    raise_application_error(-20033, :v_index || ' changed while its pipeline was being stopped (' ||
      :v_stable_fi || ' files / ' || :v_stable_ch || ' chunks at completion, now ' || l_distinct || ' / ' ||
      l_chunks || '). Rebuild with config id ' || :v_cfg || 'r.');
  end if;
  if :v_t0 is not null then
    :v_elapsed := round((dbms_utility.get_time - :v_t0) / 100);
  end if;
  p('recorded: ' || :v_chunks || ' chunks, ' || :v_chars || ' characters, ' || :v_distinct || ' files, ' ||
    'chunk length ' || l_minlen || '-' || l_maxlen ||
    case when :v_elapsed is not null then ', build ' || :v_elapsed || ' s' end);
  if :v_old_chunks is not null then
    p('rebuild check: chunks ' || :v_old_chunks || ' -> ' || :v_chunks || ', characters ' ||
      :v_old_chars || ' -> ' || :v_chars || ' : ' ||
      case when :v_old_chunks = :v_chunks and :v_old_chars = :v_chars then 'MATCH' else 'DIFF' end);
  end if;
exception
  when others then
    -- Codex A4: no error in this step may leave the index's pipeline running; the error goes on
    begin
      if l_pipe is not null and substr(l_pipe, 1, 4) = 'RAG_'
         and sys_context('userenv', 'session_user') = 'RAG_LAB' then
        select count(*) into l_n from user_cloud_pipelines
         where pipeline_name = l_pipe and upper(nvl(status, '-')) <> 'STOPPED';
        if l_n > 0 then
          dbms_cloud_pipeline.stop_pipeline(pipeline_name => l_pipe, force => true);
          p('error path: pipeline ' || l_pipe || ' stopped (force)');
        end if;
      end if;
    exception
      when others then
        p('!! error path: could not stop pipeline ' || nvl(l_pipe, '<unknown>') || ': ' || substr(sqlerrm, 1, 200) ||
          '. Stop it as RAG_LAB before anything else.');
    end;
    raise;
end;
/

prompt == 4/4 RAG profile and pairing guard

declare
  l_attr      clob;
  l_n         pls_integer;
  l_same      boolean := true;
  l_provider  varchar2(64);
  l_cred      varchar2(128);
  l_region    varchar2(64);
  l_comp      varchar2(400);
  l_llm       varchar2(200);
  l_emb_idx   varchar2(400);
  l_emb_rag   varchar2(400);
  l_idx_prof  varchar2(400);
  l_k_now     varchar2(40);
  l_thr_now   varchar2(40);
  l_extra     varchar2(4000);

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function prof_attr(pr varchar2, a varchar2) return varchar2 is
    l varchar2(4000);
  begin
    select dbms_lob.substr(attribute_value, 4000, 1) into l
      from user_cloud_ai_profile_attributes where profile_name = pr and attribute_name = a;
    return l;
  exception
    when no_data_found then return null;
  end;

  function idx_attr(a varchar2) return varchar2 is
    l varchar2(4000);
  begin
    select dbms_lob.substr(attribute_value, 4000, 1) into l
      from user_cloud_vector_index_attributes where index_name = :v_index and attribute_name = a;
    return l;
  exception
    when no_data_found then return null;
  end;

  function norm(s varchar2) return varchar2 is
  begin
    return lower(replace(trim(s), ' '));
  end;

  procedure want(a varchar2, v varchar2) is
  begin
    if nvl(norm(prof_attr(:v_rag, a)), '-') <> nvl(norm(v), '-') then
      l_same := false;
    end if;
  end;
begin
  -- OCI settings come from the index profile, so the compartment is never retyped here
  l_provider := prof_attr(:v_embprof, 'provider');
  l_cred     := prof_attr(:v_embprof, 'credential_name');
  l_region   := prof_attr(:v_embprof, 'region');
  l_comp     := prof_attr(:v_embprof, 'oci_compartment_id');
  l_llm      := prof_attr(:v_embprof, 'model');
  if l_provider is null or l_cred is null or l_region is null or l_comp is null or l_llm is null then
    raise_application_error(-20040, :v_embprof || ' lacks provider, credential, region, compartment or model; ' ||
      'refusing to create a half-configured RAG profile');
  end if;

  select count(*) into l_n from user_cloud_ai_profiles where profile_name = :v_rag;
  if l_n > 0 then
    want('provider', l_provider);
    want('credential_name', l_cred);
    want('region', l_region);
    want('oci_compartment_id', l_comp);
    want('model', l_llm);
    want('embedding_model', :v_emb);
    want('vector_index_name', :v_index);
    want('conversation', 'false');
    want('temperature', '0');
    want('max_tokens', '1024');
    -- exactly the lab's attributes: on this instance the view lists only what was set
    for a in (select attribute_name from user_cloud_ai_profile_attributes
               where profile_name = :v_rag
                 and attribute_name not in ('provider', 'credential_name', 'region', 'oci_compartment_id',
                                            'model', 'embedding_model', 'vector_index_name', 'conversation',
                                            'temperature', 'max_tokens', 'seed')
               order by attribute_name) loop
      l_extra := substr(l_extra || ' ' || a.attribute_name, 1, 3000);
    end loop;
    if not l_same or l_extra is not null then
      raise_application_error(-20042, :v_rag || ' exists with other attributes' ||
        case when l_extra is not null then ' (extra:' || l_extra || ')' end ||
        '. Stopping rather than replacing it; compare it with its index profile and decide.');
    end if;
    p(:v_rag || ' already exists with these attributes; kept');
  end if;
  if l_n = 0 then
    select json_object(
             'provider'           value l_provider,
             'credential_name'    value l_cred,
             'region'             value l_region,
             'oci_compartment_id' value l_comp,
             'model'              value l_llm,
             'embedding_model'    value :v_emb,
             'vector_index_name'  value :v_index,
             'conversation'       value 'false' format json,
             'temperature'        value 0,
             'max_tokens'         value 1024
             returning clob)
      into l_attr
      from dual;
    dbms_cloud_ai.create_profile(
      profile_name => :v_rag,
      attributes   => l_attr,
      status       => 'enabled',
      description  => 'RAG lab profile for ' || :v_index || ' (config ' || :v_cfg || ')');
    p(:v_rag || ' created');
  end if;

  -- PAIRING GUARD (PLAN.md 0.4): the index profile and the RAG profile embed with one model
  l_idx_prof := idx_attr('profile_name');
  l_emb_idx  := prof_attr(:v_embprof, 'embedding_model');
  l_emb_rag  := prof_attr(:v_rag, 'embedding_model');
  p('pairing: index ' || :v_index || ' -> profile ' || l_idx_prof || ' -> ' || l_emb_idx);
  p('pairing: RAG profile ' || :v_rag || ' -> ' || l_emb_rag);
  if nvl(l_idx_prof, '-') <> :v_embprof
     or nvl(upper(replace(l_emb_idx, ' ')), '-') <> upper(:v_emb)
     or nvl(upper(replace(l_emb_rag, ' ')), '-') <> upper(:v_emb) then
    raise_application_error(-20041, 'PAIRING GUARD: the document side and the question side do not ' ||
      'use the same embedding model. Stopping.');
  end if;
  p('pairing: OK, both sides ' || :v_emb);

  l_k_now   := idx_attr('match_limit');
  l_thr_now := idx_attr('similarity_threshold');
  if :v_action = 'existing'
     and (to_number(l_k_now) <> :v_k or to_number(l_thr_now) <> :v_thr) then
    p('NOTE: the index has match_limit ' || l_k_now || ', similarity_threshold ' || l_thr_now ||
      '; requested ' || :v_k || ' / ' || :v_thr || '. Use 08_set_query_knobs.sql to change them.');
  end if;

  p('RESULT|cfg=' || :v_cfg || '|index=' || :v_index || '|rag_profile=' || :v_rag ||
    '|action=' || :v_action || '|files=' || :v_distinct || '/' || :v_expect ||
    '|chunks=' || :v_chunks || '|content_chars=' || :v_chars ||
    '|elapsed_s=' || nvl(to_char(:v_elapsed), 'na') || '|dims=' || :v_dims ||
    '|metric=' || :v_metric || '|match_limit=' || l_k_now || '|similarity_threshold=' || l_thr_now ||
    '|embedding_model=' || :v_emb || '|pipeline_status=' || :v_pstatus || '|pipeline=' || :v_pipe);
end;
/

prompt
prompt Lab index (whitelisted attributes):
set linesize 160 pagesize 100 heading on
column attribute_name  format a24
column attribute_value format a40
select attribute_name, dbms_lob.substr(attribute_value, 40, 1) attribute_value
  from user_cloud_vector_index_attributes
 where index_name = :v_index
   and attribute_name in ('location', 'profile_name', 'chunk_size', 'chunk_overlap', 'vector_distance_metric',
                          'vector_dimension', 'match_limit', 'similarity_threshold', 'refresh_rate')
 order by attribute_name;
