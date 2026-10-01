-- v1.1 - RAG tuning lab (brief 09): the index-side profile RAG_EMB_<KEY> for one embedding
--        model. It becomes the profile_name of every lab index built on that model, so it
--        embeds the documents (and, per [D1], the prompts).
--        v1.1: Codex adversarial review (A1): the three arguments are checked at SQL*Plus level
--              before any PL/SQL sees them, by one query that holds each only in a q'{...}'
--              literal; output is off meanwhile, so the compartment is never echoed.
--        v1.0: first version, PLAN.md v1.2 section 2.4.
--
-- Run as : RAG_LAB, after 02_create_credential.py and 03_load_models.sql <KEY>.
-- Usage  : @06_profiles.sql <KEY> <oci_compartment_ocid> <oci_region>
--            KEY    M0 M1 M2 M3 M4 M5 M6 M1Q
--            region e.g. the region part of the GenAI host name
--          Every argument may hold only letters, digits and _ . : - ; anything else stops the
--          script before any PL/SQL sees it (README_RUN.md, quoting risk in hand runs).
-- Re-run : safe. An existing RAG_EMB_<KEY> with exactly these attributes is kept. One whose
--          embedding_model differs is never changed (indexes built on it would silently get a
--          different query model): the script stops. One whose OCI settings differ stops too,
--          because 07_build_index.sql copies them into the RAG profiles.
-- The compartment OCID and the region are arguments, never stored in this file, and are not
-- printed back: the attribute listing is a whitelist.

set verify off echo off feedback off termout on
set serveroutput on size unlimited format wrapped
whenever sqlerror exit failure
whenever oserror exit failure

define model_key       = '&1'
define oci_compartment = '&2'
define oci_region      = '&3'

-- Argument guard (Codex A1): this query is the first statement to hold the arguments, and only
-- inside q'{...}' literals, which a value from the allowed set (written out, no ranges) cannot
-- end. Output is off while it runs, so the compartment OCID is never echoed; the block after it
-- stops the script unless the verdict is exactly YES. The PL/SQL below checks the precise forms.
define rag_arg_re = '^[0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_.:-]{1,200}$'
define rag_args_ok = 'NO'
column rag_args_ok new_value rag_args_ok noprint
prompt checking the arguments (allowed: letters, digits and _ . : -; values not shown)
set termout off
select case when regexp_like(q'{&model_key}', '&rag_arg_re', 'c')
             and regexp_like(q'{&oci_compartment}', '&rag_arg_re', 'c')
             and regexp_like(q'{&oci_region}', '&rag_arg_re', 'c')
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
  l_key      varchar2(16) := upper(trim('&model_key'));
  l_comp     varchar2(400) := '&oci_compartment';
  l_region   varchar2(64)  := '&oci_region';
  l_model    varchar2(128);
  l_profile  varchar2(128);
  l_emb      varchar2(400);
  l_attr     clob;
  l_n        pls_integer;
  l_diff     varchar2(4000);
  c_cred     constant varchar2(128) := 'RAG_LAB_OCI_CRED';
  c_llm      constant varchar2(128) := 'xai.grok-4.20-non-reasoning';

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function attr(p_profile varchar2, p_name varchar2) return varchar2 is
    l varchar2(4000);
  begin
    select dbms_lob.substr(attribute_value, 4000, 1) into l
      from user_cloud_ai_profile_attributes
     where profile_name = p_profile and attribute_name = p_name;
    return l;
  exception
    when no_data_found then return null;
  end;
begin
  if sys_context('userenv', 'session_user') <> 'RAG_LAB' then
    raise_application_error(-20001, 'run as RAG_LAB');
  end if;
  case l_key
    when 'M0'  then l_model := 'ALL_MINILM_L12_V2';
    when 'M1'  then l_model := 'MULTILINGUAL_E5_SMALL';
    when 'M2'  then l_model := 'MULTILINGUAL_E5_BASE';
    when 'M3'  then l_model := 'MULTILINGUAL_E5_LARGE';
    when 'M4'  then l_model := 'BGE_M3';
    when 'M5'  then l_model := 'ARCTIC_EMBED_L_V2';
    when 'M6'  then l_model := 'ARABIC_TRIPLET_V2';
    when 'M1Q' then l_model := 'MULTILINGUAL_E5_SMALL_Q';
    else raise_application_error(-20002, 'unknown model key ' || substr(l_key, 1, 8));
  end case;
  if not regexp_like(l_comp, '^ocid1\.(compartment|tenancy)\.[a-z0-9-]+\.[a-z0-9-]*\.[a-z0-9]+$') then
    raise_application_error(-20003, 'argument 2 is not a compartment or tenancy OCID (value not shown)');
  end if;
  if not regexp_like(l_region, '^[a-z]+-[a-z]+-[0-9]+$') then
    raise_application_error(-20004, 'argument 3 is not an OCI region name');
  end if;

  l_profile := dbms_assert.simple_sql_name('RAG_EMB_' || l_key);
  l_emb     := 'database:ASKORACLE.' || dbms_assert.simple_sql_name(l_model);

  -- the model must be visible (03 grants it) and the credential must exist (02 creates it)
  select count(*) into l_n from all_mining_models where owner = 'ASKORACLE' and model_name = l_model;
  if l_n = 0 then
    raise_application_error(-20005, 'ASKORACLE.' || l_model || ' is not visible to RAG_LAB (run 03_load_models.sql ' || l_key || ')');
  end if;
  select count(*) into l_n from user_credentials where credential_name = c_cred;
  if l_n = 0 then
    raise_application_error(-20006, 'credential ' || c_cred || ' missing (run 02_create_credential.py)');
  end if;

  select count(*) into l_n from user_cloud_ai_profiles where profile_name = l_profile;
  if l_n > 0 then
    if nvl(upper(replace(attr(l_profile, 'embedding_model'), ' ')), '-') <> upper(l_emb) then
      raise_application_error(-20007, l_profile || ' exists with embedding_model ' ||
        nvl(attr(l_profile, 'embedding_model'), '<none>') || ', not ' || l_emb ||
        '. Refusing to change it: indexes built on it would query with another model.');
    end if;
    if nvl(attr(l_profile, 'oci_compartment_id'), '-') <> l_comp then l_diff := l_diff || ' oci_compartment_id'; end if;
    if nvl(attr(l_profile, 'region'), '-')             <> l_region then l_diff := l_diff || ' region'; end if;
    if nvl(attr(l_profile, 'credential_name'), '-')    <> c_cred then l_diff := l_diff || ' credential_name'; end if;
    if nvl(lower(attr(l_profile, 'provider')), '-')    <> 'oci' then l_diff := l_diff || ' provider'; end if;
    if nvl(attr(l_profile, 'model'), '-')              <> c_llm then l_diff := l_diff || ' model'; end if;
    if l_diff is not null then
      raise_application_error(-20008, l_profile || ' exists but differs in:' || l_diff ||
        '. Stopping; decide with the operator before changing a profile that indexes use.');
    end if;
    p(l_profile || ' already exists with these attributes; kept');
  else
    -- JSON_OBJECT escapes every value; string concatenation would not
    select json_object(
             'provider'           value 'oci',
             'credential_name'    value c_cred,
             'region'             value l_region,
             'oci_compartment_id' value l_comp,
             'model'              value c_llm,
             'embedding_model'    value l_emb
             returning clob)
      into l_attr
      from dual;
    dbms_cloud_ai.create_profile(
      profile_name => l_profile,
      attributes   => l_attr,
      status       => 'enabled',
      description  => 'RAG lab index profile, model key ' || l_key);
    p(l_profile || ' created');
  end if;
end;
/

prompt
prompt Index-side profile (whitelisted attributes):
set linesize 160 pagesize 100 heading on
column profile_name    format a14
column attribute_name  format a18
column attribute_value format a60
select profile_name, attribute_name, dbms_lob.substr(attribute_value, 60, 1) attribute_value
  from user_cloud_ai_profile_attributes
 where profile_name = upper('RAG_EMB_' || trim('&model_key'))
   and attribute_name in ('provider', 'model', 'embedding_model')
 order by attribute_name;
