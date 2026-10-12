-- v1.0 - brief 14 ROUTE_LAB: read-only state check (POC restart after the 04:40Z session stop, and before clean-up).
--        Run as ROUTE_LAB. v1.0: first version. Prints what ROUTE_LAB holds: budget rows (open or closed), calls by
--        step, kind, status and answer, profile aliases with a yes/no for the OCID (never the OCID), Select AI profile
--        names, the credential name and state, object counts by type and status, snapshot and result rows, the
--        grants it made and its host ACEs. It writes, creates, grants and calls nothing.
--
-- Usage : sqlplus -s -L ROUTE_LAB@<alias> @05_state_check.sql   (password on stdin; tools/b14_sql.sh lab)
whenever sqlerror exit failure rollback
set verify off feedback off serveroutput on size unlimited define off linesize 200 pagesize 100

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '05: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '05: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if user != 'ROUTE_LAB' then
    raise_application_error(-20140, '05: run as ROUTE_LAB');
  end if;
end;
/

column step format a14
column caller format a9
column kind format a8
column alias format a6
column model_id format a32
column has_ocid format a8
column region format a14
column object_type format a14
column status format a8
column profile_name format a16
column credential_name format a16
column enabled format a8
column what format a24
column grantee format a10
column table_name format a18
column privilege format a10
column host format a60
column kind_or_probe format a26

prompt budget allotments (closed = no further database call is possible):
select step, allotment_no, calls_allotted, calls_used, usd_allotted, round(usd_used_est, 4) as usd_used_est,
       case when closed_utc is null then 'OPEN' else 'CLOSED' end as state
  from rl_budget order by step;

prompt calls by step, caller, kind, HTTP status and answer:
select step, caller, kind, alias, model_id, http_status, answer_ok, count(*) as n
  from rl_call group by step, caller, kind, alias, model_id, http_status, answer_ok
 order by step, caller, alias, kind, http_status;

prompt profile aliases (OCID shown only as present or absent):
select alias, model_id, case when ocid is null then 'no' else 'yes' end as has_ocid, region from rl_profile
 order by alias;

prompt Select AI profiles and the credential:
select profile_name from user_cloud_ai_profiles order by profile_name;
select credential_name, enabled from user_credentials order by credential_name;

prompt objects by type and status:
select object_type, status, count(*) as n from user_objects group by object_type, status order by object_type, status;

prompt snapshot and result rows:
select 'MD_SNAPSHOT' as what, count(*) as n from md_snapshot
union all select 'RL_RESULT ' || kind, count(*) from rl_result group by kind
union all select 'RL_CONFIG', count(*) from rl_config
union all select 'RL_PRICE', count(*) from rl_price;

prompt grants made by ROUTE_LAB:
select grantee, table_name, privilege from user_tab_privs_made order by grantee, table_name, privilege;

prompt host ACEs of ROUTE_LAB:
select host, privilege from user_host_aces order by host, privilege;

prompt state check done (nothing changed)
