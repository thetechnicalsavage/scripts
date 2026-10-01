-- v1.1 - RAG tuning lab (brief 09): set match_limit and similarity_threshold on ONE lab index.
--        v1.1: Codex adversarial review (A1): the three arguments are checked at SQL*Plus level
--              before any PL/SQL sees them, by one query that holds each only in a q'{...}' literal.
--        v1.0: first version, PLAN.md v1.2 sections 4.1 and 7.
--
-- Run as : RAG_LAB
-- Usage  : @08_set_query_knobs.sql <index_name> <k> <thr>
--            index_name  a RAG_LAB vector index whose name starts with RAG_ (e.g. RAG_M0_C1024_O128_COS)
--            k           match_limit, 1-100
--            thr         similarity_threshold, 0-1, at most 4 decimals
--          Every argument may hold only letters, digits and _ . : - ; anything else stops the
--          script before any PL/SQL sees it (README_RUN.md, quoting risk in hand runs).
-- Re-run : safe. A value that is already set is not rewritten.
--
-- Only lab indexes: the name must start with RAG_ and be listed in RAG_LAB's own
-- USER_CLOUD_VECTOR_INDEXES, so no other schema's index can be reached from here.
-- UPDATE_VECTOR_INDEX changes the index for every profile that uses it; in this lab that is
-- one RAG profile per index. Its pipeline must stay stopped (PLAN.md 2.5); if an update turns
-- it back on, it is stopped again and the output says so.

set verify off echo off feedback off termout on
set serveroutput on size unlimited format wrapped
whenever sqlerror exit failure
whenever oserror exit failure
alter session set nls_numeric_characters = '.,';

define knob_index = '&1'
define knob_k     = '&2'
define knob_thr   = '&3'

-- Argument guard (Codex A1): this query is the first statement to hold the arguments, and only
-- inside q'{...}' literals, which a value from the allowed set (written out, no ranges) cannot
-- end. Output is off while it runs; the block after it stops the script unless the verdict is
-- exactly YES. The PL/SQL below checks the precise forms (a RAG_ index, k 1-100, thr 0-1).
define rag_arg_re = '^[0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_.:-]{1,200}$'
define rag_args_ok = 'NO'
column rag_args_ok new_value rag_args_ok noprint
prompt checking the arguments (allowed: letters, digits and _ . : -)
set termout off
select case when regexp_like(q'{&knob_index}', '&rag_arg_re', 'c')
             and regexp_like(q'{&knob_k}', '&rag_arg_re', 'c')
             and regexp_like(q'{&knob_thr}', '&rag_arg_re', 'c')
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
  l_index   varchar2(128) := upper(trim('&knob_index'));
  l_k_s     varchar2(16)  := trim('&knob_k');
  l_thr_s   varchar2(16)  := trim('&knob_thr');
  l_k       pls_integer;
  l_thr     number;
  l_n       pls_integer;
  l_k_now   varchar2(40);
  l_thr_now varchar2(40);
  l_pipe    varchar2(400);
  l_pstatus varchar2(64);

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function idx_attr(a varchar2) return varchar2 is
    l varchar2(4000);
  begin
    select dbms_lob.substr(attribute_value, 4000, 1) into l
      from user_cloud_vector_index_attributes where index_name = l_index and attribute_name = a;
    return l;
  exception
    when no_data_found then return null;
  end;

  function pipe_status return varchar2 is
    l varchar2(64);
  begin
    select status into l from user_cloud_pipelines where pipeline_name = l_pipe;
    return l;
  exception
    when no_data_found then return '<absent>';
  end;

  -- the only objects this script may update or stop: RAG_LAB's own, named RAG_...
  procedure assert_lab(n varchar2) is
  begin
    if sys_context('userenv', 'session_user') <> 'RAG_LAB'
       or n is null or substr(n, 1, 4) <> 'RAG_'
       or dbms_assert.simple_sql_name(n) is null then
      raise_application_error(-20090, 'guard: refusing to touch ' || nvl(n, '<null>'));
    end if;
  end;
begin
  -- guards: connected as RAG_LAB, a RAG_ name, and an index RAG_LAB itself owns
  if sys_context('userenv', 'session_user') <> 'RAG_LAB' then
    raise_application_error(-20001, 'run as RAG_LAB');
  end if;
  if not regexp_like(l_index, '^RAG_[A-Z0-9_]{1,100}$') then
    raise_application_error(-20002, 'index name must start with RAG_ (letters, digits, _)');
  end if;
  l_index := dbms_assert.simple_sql_name(l_index);
  select count(*) into l_n from user_cloud_vector_indexes where index_name = l_index;
  if l_n = 0 then
    raise_application_error(-20003, l_index || ' is not a RAG_LAB vector index');
  end if;
  if not regexp_like(l_k_s, '^[0-9]{1,3}$') or to_number(l_k_s) not between 1 and 100 then
    raise_application_error(-20004, 'match_limit must be an integer 1-100');
  end if;
  if not regexp_like(l_thr_s, '^[0-9](\.[0-9]{1,4})?$') or to_number(l_thr_s) > 1 then
    raise_application_error(-20005, 'similarity_threshold must be a number 0-1 with at most 4 decimals');
  end if;
  l_k   := to_number(l_k_s);
  l_thr := to_number(l_thr_s);

  l_k_now   := idx_attr('match_limit');
  l_thr_now := idx_attr('similarity_threshold');
  l_pipe    := idx_attr('pipeline_name');
  p(l_index || ' before: match_limit ' || nvl(l_k_now, '<unset>') ||
    ', similarity_threshold ' || nvl(l_thr_now, '<unset>'));

  if l_k_now is null or to_number(l_k_now) <> l_k then
    assert_lab(l_index);
    dbms_cloud_ai.update_vector_index(index_name      => l_index,
                                      attribute_name  => 'match_limit',
                                      attribute_value => to_char(l_k));
  end if;
  if l_thr_now is null or to_number(l_thr_now) <> l_thr then
    assert_lab(l_index);
    dbms_cloud_ai.update_vector_index(index_name      => l_index,
                                      attribute_name  => 'similarity_threshold',
                                      attribute_value => to_char(l_thr, 'fm0.0999'));
  end if;

  p(l_index || ' after : match_limit ' || idx_attr('match_limit') ||
    ', similarity_threshold ' || idx_attr('similarity_threshold'));

  -- the pipeline stays stopped; stop it again only if it is a RAG_ pipeline of RAG_LAB
  if l_pipe is not null then
    l_pstatus := pipe_status;
    if upper(l_pstatus) not in ('STOPPED', '<ABSENT>') then
      assert_lab(l_pipe);
      dbms_cloud_pipeline.stop_pipeline(pipeline_name => l_pipe, force => true);
      p('NOTE: pipeline ' || l_pipe || ' was ' || l_pstatus || ' after the update; stopped again');
    end if;
  end if;
  p('KNOBS|index=' || l_index || '|match_limit=' || idx_attr('match_limit') ||
    '|similarity_threshold=' || idx_attr('similarity_threshold'));
end;
/
