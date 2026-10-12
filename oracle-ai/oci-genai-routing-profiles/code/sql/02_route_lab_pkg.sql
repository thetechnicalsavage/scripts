-- v1.1 - brief 14 ROUTE_LAB: PKG_ROUTE_LAB, the PL/SQL caller (S6) and the Select AI caller (S7). Run as ROUTE_LAB.
--        v1.1: (Codex code review r1, findings 6-7) claim() writes the RL_CALL row in the same transaction as the
--              budget claim, and the call updates that row, so a later failure still leaves the evidence; settle()
--              closes the allotment when a call is billed above the per-call bound or the allotment's USD is passed;
--              the hold is 2,000 input + 2 x 1,024 output tokens, as budget.py v1.1.
--        v1.0: first version (PLAN.md v1.0 sections 6, 7, 10). Written in the plan stage; NOT run or compiled yet.
--
-- Usage : sqlplus -s -L ROUTE_LAB@<alias> @02_route_lab_pkg.sql   (password on stdin)
-- Re-run: safe (CREATE OR REPLACE only; no table, row or job is touched).
--
-- What it guarantees:
--   * the only URI it can call is the Phoenix inference chat action (a constant); nothing a caller passes reaches a URI;
--   * every call first claims one call from the step's open RL_BUDGET row (select ... for update) and refuses when the
--     allotment's calls are used up or its USD estimate would pass the allotment (hold = 2,000 in + 2 x 1,024 out
--     tokens at RL_PRICE, as the Python ledger reserves); the claim and the call's RL_CALL row commit together;
--   * the request body is built with JSON_OBJECT from RL_CONFIG and RL_PROFILE, so no OCID is in any script text, and
--     what is recorded (RL_CALL) holds no OCID: returned model ids are reduced to type and region key;
--   * SEND_REQUEST may raise on a non-2xx status: an error number in -20400..-20599 is read as HTTP status
--     -sqlcode - 20000, as app 800's PKG_INV_OCI does; any other error is status 0 (transport), recorded, not retried.
whenever sqlerror exit failure rollback
set verify off feedback off serveroutput on size unlimited define off

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '02: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '02: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if user != 'ROUTE_LAB' then
    raise_application_error(-20140, '02: run as ROUTE_LAB');
  end if;
end;
/

create or replace package PKG_ROUTE_LAB authid definer as
  c_chat_uri   constant varchar2(200) :=
    'https://inference.generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/actions/chat';
  c_cred       constant varchar2(30)  := 'RL_CRED';
  c_max_tokens constant pls_integer   := 1024;
  c_hold_in    constant pls_integer   := 2000;
  c_hold_out   constant pls_integer   := 2048;

  -- the same five prompts as poc/b14lib/chat.py PROMPTS (0-based)
  function prompt_word (p_no pls_integer) return varchar2;
  function prompt_text (p_no pls_integer) return varchar2;
  function redact (p_text varchar2) return varchar2;
  function ocid_shape (p_id varchar2) return varchar2;

  -- S6: one chat call; p_kind 'DIRECT' sends the model name, 'PROFILE' the routing profile OCID. Returns RL_CALL.call_id.
  function chat (p_step varchar2, p_alias varchar2, p_kind varchar2, p_prompt_no pls_integer) return number;

  -- S7: one Select AI chat through a profile created by 03_select_ai_profiles.sql. Returns RL_CALL.call_id.
  function sai_chat (p_step varchar2, p_sai_profile varchar2, p_prompt_no pls_integer) return number;
end PKG_ROUTE_LAB;
/

create or replace package body PKG_ROUTE_LAB as

  function prompt_word (p_no pls_integer) return varchar2 is
  begin
    return case p_no when 0 then 'ALPHA' when 1 then 'BRAVO' when 2 then 'CHARLIE' when 3 then 'DELTA'
                     when 4 then 'ECHO' end;
  end prompt_word;

  function prompt_text (p_no pls_integer) return varchar2 is
  begin
    if prompt_word(p_no) is null then
      raise_application_error(-20144, 'PKG_ROUTE_LAB: prompt number must be 0-4');
    end if;
    return 'Reply with exactly one word: ' || prompt_word(p_no);
  end prompt_text;

  -- OCIDs to <type:regionkey>, then IPv4 addresses; the compartment is never echoed by the service in our tests,
  -- and any OCID-shaped text is reduced anyway.
  function redact (p_text varchar2) return varchar2 is
    l varchar2(4000) := substr(p_text, 1, 4000);
  begin
    l := regexp_replace(l, 'ocid1\.([a-z0-9_-]+)\.([a-z0-9_-]*)\.([a-z0-9_-]*)\.[A-Za-z0-9_.-]+', '<\1:\3>');
    l := regexp_replace(l, '([0-9]{1,3}\.){3}[0-9]{1,3}', '<ip>');
    return substr(l, 1, 1000);
  end redact;

  function ocid_shape (p_id varchar2) return varchar2 is
  begin
    if p_id is null then
      return null;
    elsif regexp_like(p_id, '^ocid1\.[a-z0-9_-]+\.[a-z0-9_-]*\.[a-z0-9_-]*\.[A-Za-z0-9_.-]+$') then
      return '<' || regexp_substr(p_id, '^ocid1\.([a-z0-9_-]+)\.', 1, 1, null, 1) || ':'
             || nvl(regexp_substr(p_id, '^ocid1\.[a-z0-9_-]+\.[a-z0-9_-]*\.([a-z0-9_-]*)\.', 1, 1, null, 1), '-')
             || '>';
    else
      return substr(p_id, 1, 100);
    end if;
  end ocid_shape;

  function to_blob_utf8 (p_clob clob) return blob is
    l_blob blob;
    l_dest integer := 1;
    l_src  integer := 1;
    l_ctx  integer := dbms_lob.default_lang_ctx;
    l_warn integer;
  begin
    dbms_lob.createtemporary(l_blob, true);
    dbms_lob.converttoblob(l_blob, p_clob, dbms_lob.lobmaxsize, l_dest, l_src,
                           nls_charset_id('AL32UTF8'), l_ctx, l_warn);
    return l_blob;
  end to_blob_utf8;

  function hold_usd (p_model varchar2) return number is
    l_in  number;
    l_out number;
  begin
    select usd_in_1m, usd_out_1m into l_in, l_out from rl_price where model_id = p_model;
    return (c_hold_in * l_in + c_hold_out * l_out) / 1e6;
  exception
    when no_data_found then
      raise_application_error(-20145, 'PKG_ROUTE_LAB: no price for ' || p_model || '; it is not called');
  end hold_usd;

  -- claim one call from the step's open allotment and write its RL_CALL row in the same transaction, committed at
  -- once: a later failure (transport, parse, settle) still counts the call and leaves its row
  function claim (p_step varchar2, p_model varchar2, p_caller varchar2, p_alias varchar2, p_kind varchar2,
                  p_prompt_no pls_integer) return number is
    l_calls_used number;
    l_calls_all  number;
    l_usd_used   number;
    l_usd_all    number;
    l_hold       number := hold_usd(p_model);
    l_id         number;
  begin
    select calls_used, calls_allotted, usd_used_est, usd_allotted
      into l_calls_used, l_calls_all, l_usd_used, l_usd_all
      from rl_budget where step = p_step and closed_utc is null for update;
    if l_calls_used >= l_calls_all then
      rollback;
      raise_application_error(-20142, 'budget: the ' || p_step || ' allotment has no call left');
    end if;
    if l_usd_used + l_hold > l_usd_all then
      rollback;
      raise_application_error(-20142, 'budget: the ' || p_step || ' allotment has not enough USD left');
    end if;
    update rl_budget set calls_used = calls_used + 1, usd_used_est = usd_used_est + l_hold where step = p_step;
    insert into rl_call (step, caller, alias, kind, model_id, prompt_no, usd_est, err_text)
    values (p_step, p_caller, p_alias, p_kind, p_model, p_prompt_no, l_hold, 'claimed; no answer recorded yet')
    returning call_id into l_id;
    commit;
    return l_id;
  exception
    when no_data_found then
      raise_application_error(-20143, 'budget: no open allotment for ' || p_step);
  end claim;

  -- replace the hold by the cost of the returned usage (output = completion + reasoning, never under-counted)
  -- fails closed: a call billed above the per-call bound, or an allotment whose USD estimate passed its allotment,
  -- closes the allotment (calls_allotted := calls_used), so the next claim is refused
  procedure settle (p_step varchar2, p_model varchar2, p_status number, p_in number, p_out number,
                    p_reason number, p_usd out number, p_breach out varchar2) is
    l_in   number;
    l_out  number;
    l_hold number := hold_usd(p_model);
    l_used number;
    l_all  number;
  begin
    p_breach := null;
    select usd_in_1m, usd_out_1m into l_in, l_out from rl_price where model_id = p_model;
    if p_status in (400, 401, 403, 404, 409, 412, 429) then
      p_usd := 0;
    elsif p_status = 200 and p_in is not null and p_out is not null then
      p_usd := (p_in * l_in + (p_out + nvl(p_reason, 0)) * l_out) / 1e6;
      if p_out + nvl(p_reason, 0) > c_hold_out or p_in > c_hold_in then
        p_breach := 'billed above the per-call bound; allotment closed';
      end if;
    else
      p_usd := l_hold;
    end if;
    update rl_budget set usd_used_est = usd_used_est - l_hold + p_usd where step = p_step
    returning usd_used_est, usd_allotted into l_used, l_all;
    if l_used > l_all then
      p_breach := 'allotment USD passed; allotment closed';
    end if;
    if p_breach is not null then
      update rl_budget set calls_allotted = calls_used where step = p_step;
    end if;
    commit;
  end settle;

  function chat (p_step varchar2, p_alias varchar2, p_kind varchar2, p_prompt_no pls_integer) return number is
    l_model   rl_profile.model_id%type;
    l_ocid    rl_profile.ocid%type;
    l_serving varchar2(255);
    l_comp    rl_config.value%type;
    l_body    clob;
    l_resp    dbms_cloud_types.resp;
    l_status  number;
    l_text    clob;
    l_err     varchar2(1000);
    l_code    varchar2(100);
    l_t0      timestamp with time zone;
    l_ms      number;
    l_obj     json_object_t;
    l_cr      json_object_t;
    l_ch      json_array_t;
    l_msg     json_object_t;
    l_cont    json_array_t;
    l_usage   json_object_t;
    l_answer  varchar2(4000);
    l_mid     varchar2(400);
    l_mver    varchar2(100);
    l_finish  varchar2(40);
    l_pt      number;
    l_ct      number;
    l_rt      number;
    l_usd     number;
    l_breach  varchar2(100);
    l_id      number;
  begin
    if p_kind not in ('DIRECT', 'PROFILE') then
      raise_application_error(-20144, 'PKG_ROUTE_LAB.chat: kind must be DIRECT or PROFILE');
    end if;
    select model_id, ocid into l_model, l_ocid from rl_profile where alias = p_alias;
    if p_kind = 'PROFILE' and l_ocid is null then
      raise_application_error(-20147, 'PKG_ROUTE_LAB.chat: S4 created no profile for ' || p_alias);
    end if;
    select value into l_comp from rl_config where name = 'COMPARTMENT_ID';
    l_serving := case p_kind when 'PROFILE' then l_ocid else l_model end;
    select json_object(
             'compartmentId' value l_comp,
             'servingMode'   value json_object('servingType' value 'ON_DEMAND', 'modelId' value l_serving),
             'chatRequest'   value json_object(
                'apiFormat'   value 'GENERIC',
                'messages'    value json_array(json_object(
                                 'role' value 'USER',
                                 'content' value json_array(json_object('type' value 'TEXT',
                                                                        'text' value prompt_text(p_prompt_no))))),
                'maxTokens'   value c_max_tokens,
                'temperature' value 0)
             returning clob)
      into l_body from dual;

    l_id := claim(p_step, l_model, 'PLSQL', p_alias, p_kind, p_prompt_no);
    l_t0 := systimestamp;
    begin
      l_resp := dbms_cloud.send_request(
                  credential_name => c_cred,
                  uri             => c_chat_uri,
                  method          => dbms_cloud.method_post,
                  headers         => '{"Content-Type":"application/json"}',
                  body            => to_blob_utf8(l_body));
      l_status := dbms_cloud.get_response_status_code(l_resp);
      l_text   := dbms_cloud.get_response_text(l_resp);
    exception
      when others then
        l_status := case when sqlcode between -20599 and -20400 then -sqlcode - 20000 else 0 end;
        l_err    := redact(sqlerrm);
    end;
    l_ms := round(extract(second from (systimestamp - l_t0)) * 1000
                  + extract(minute from (systimestamp - l_t0)) * 60000);

    if l_status = 200 and l_text is not null then
     begin
      l_obj := json_object_t.parse(l_text);
      l_mid := l_obj.get_string('modelId');
      l_mver := l_obj.get_string('modelVersion');
      l_cr := l_obj.get_object('chatResponse');
      if l_cr is not null then
        l_ch := l_cr.get_array('choices');
        if l_ch is not null and l_ch.get_size > 0 then
          l_msg := treat(l_ch.get(0) as json_object_t);
          l_finish := l_msg.get_string('finishReason');
          l_cont := l_msg.get_object('message').get_array('content');
          if l_cont is not null and l_cont.get_size > 0 then
            l_answer := substr(treat(l_cont.get(0) as json_object_t).get_string('text'), 1, 200);
          end if;
        end if;
        l_usage := l_cr.get_object('usage');
        if l_usage is not null then
          l_pt := l_usage.get_number('promptTokens');
          l_ct := l_usage.get_number('completionTokens');
          if l_usage.get_object('completionTokensDetails') is not null then
            l_rt := l_usage.get_object('completionTokensDetails').get_number('reasoningTokens');
          end if;
        end if;
      end if;
     exception
      when others then
        -- a 200 whose body we could not read is still recorded (usage unknown: the hold stands as its cost)
        l_err := 'response parse error: ' || redact(sqlerrm);
        l_pt := null;
        l_ct := null;
     end;
    elsif l_text is not null then
      begin
        l_obj  := json_object_t.parse(l_text);
        l_code := substr(l_obj.get_string('code'), 1, 100);
        l_err  := redact(l_obj.get_string('message'));
      exception
        when others then
          l_err := redact(dbms_lob.substr(l_text, 500, 1));
      end;
    end if;

    settle(p_step, l_model, l_status, l_pt, l_ct, l_rt, l_usd, l_breach);
    update rl_call
       set http_status = l_status, err_code = l_code,
           err_text = substr(l_err || case when l_breach is not null then ' [' || l_breach || ']' end, 1, 1000),
           latency_ms = l_ms, model_id_shape = ocid_shape(l_mid), model_version = substr(l_mver, 1, 100),
           prompt_tokens = l_pt, completion_tokens = l_ct, reasoning_tokens = l_rt,
           finish_reason = substr(l_finish, 1, 40),
           answer_ok = case when instr(upper(l_answer), prompt_word(p_prompt_no)) > 0 then 'Y' else 'N' end,
           answer_snip = redact(l_answer), usd_est = l_usd
     where call_id = l_id;
    commit;
    return l_id;
  end chat;

  function sai_chat (p_step varchar2, p_sai_profile varchar2, p_prompt_no pls_integer) return number is
    l_alias  varchar2(10);
    l_kind   varchar2(10);
    l_model  rl_profile.model_id%type;
    l_answer clob;
    l_status number;
    l_err    varchar2(1000);
    l_t0     timestamp with time zone;
    l_ms     number;
    l_usd    number;
    l_breach varchar2(100);
    l_id     number;
  begin
    -- RL_SAI_P1 / RL_SAI_P3 name a routing profile; RL_SAI_DIRECT names P1's model directly (the control)
    case p_sai_profile
      when 'RL_SAI_P1'     then l_alias := 'P1'; l_kind := 'PROFILE';
      when 'RL_SAI_P3'     then l_alias := 'P3'; l_kind := 'PROFILE';
      when 'RL_SAI_DIRECT' then l_alias := 'P1'; l_kind := 'DIRECT';
      else raise_application_error(-20144, 'PKG_ROUTE_LAB.sai_chat: unknown profile ' || p_sai_profile);
    end case;
    select model_id into l_model from rl_profile where alias = l_alias;
    l_id := claim(p_step, l_model, 'SELECT_AI', l_alias, l_kind, p_prompt_no);
    l_t0 := systimestamp;
    begin
      l_answer := dbms_cloud_ai.generate(prompt => prompt_text(p_prompt_no), profile_name => p_sai_profile,
                                         action => 'chat');
      l_status := 200;
    exception
      when others then
        l_status := 0;
        l_err := redact(sqlerrm);
    end;
    l_ms := round(extract(second from (systimestamp - l_t0)) * 1000
                  + extract(minute from (systimestamp - l_t0)) * 60000);
    -- Select AI shows no usage: the hold stands as the cost (settle with unknown usage)
    settle(p_step, l_model, case when l_status = 200 then 200 else 500 end, null, null, null, l_usd, l_breach);
    update rl_call
       set http_status = l_status,
           err_text = substr(l_err || case when l_breach is not null then ' [' || l_breach || ']' end, 1, 1000),
           latency_ms = l_ms,
           answer_ok = case when instr(upper(dbms_lob.substr(l_answer, 2000, 1)), prompt_word(p_prompt_no)) > 0
                            then 'Y' else 'N' end,
           answer_snip = redact(dbms_lob.substr(l_answer, 200, 1)), usd_est = l_usd
     where call_id = l_id;
    commit;
    return l_id;
  end sai_chat;

end PKG_ROUTE_LAB;
/

show errors package PKG_ROUTE_LAB
show errors package body PKG_ROUTE_LAB
declare
  l_bad number;
begin
  select count(*) into l_bad from user_objects where object_name = 'PKG_ROUTE_LAB' and status <> 'VALID';
  if l_bad > 0 then
    raise_application_error(-20146, '02: PKG_ROUTE_LAB did not compile VALID');
  end if;
  dbms_output.put_line('PKG_ROUTE_LAB: VALID');
end;
/
