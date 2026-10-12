-- v1.2 - brief 14 ROUTE_LAB: the read-only discovery queries behind screenshots 04-09 (screenshots/SHOTS_PLAN.md v1.0).
--        Run as ROUTE_LAB. v1.2: (second run, 09:44Z: ORA-00934) Q04 names its columns through the table alias, so
--        ORDER BY GROUPING() no longer meets the select alias of the same name; Q09a counts with an outer join instead
--        of a subquery inside SUM. v1.1: (first run, 09:43Z: ORA-00923) the column alias ACCESS is an Oracle
--        reserved word: renamed MODEL_ACCESS in Q04 and Q05b. Results unchanged in meaning. v1.0: first version (screenshot stage, 10-Oct). Every query reads the S1 snapshot views
--        (MD_*, LM_MODEL, V_CATALOGUE) or MD_SNAPSHOT itself; none reads RL_PROFILE, RL_CONFIG, RL_BUDGET or RL_CALL,
--        so no OCID, compartment or budget value can appear in the output. It writes, creates, grants and calls
--        nothing. Each query sits between "-- [Qnn]" and "-- [/Qnn]" lines: code/tools/make_shots.py shows exactly
--        that text above the output that follows the matching "=== Qnn" prompt.
--
-- Usage : tools/b14_sql.sh lab sql/06_shot_queries.sql <logname>   (password on stdin; read-only)
whenever sqlerror exit failure rollback
set verify off feedback off define off serveroutput on size unlimited linesize 220 pagesize 200 tab off trimout on

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '06: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '06: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if user != 'ROUTE_LAB' then
    raise_application_error(-20140, '06: run as ROUTE_LAB');
  end if;
end;
/

column vendor format a8
column model_access format a12
column models format 999999
column model_id format a40
column od_regions format 99999
column deprecated format a10
column phx_od_retires format a14
column ret_30d format a7
column priced format a6
column in_discovery format a12
column in_listmodels format a13
column lm_state format a8
column probe format a30
column filter_sent format a76
column items format 99999
column api_capability format a32
column name format a34
column on_demand_regions format a100
column chat_models format 99999
column on_demand_in_phoenix format 99999
column snapshot_utc format a22
column probes format 99999

prompt === Q00 snapshot
-- [Q00]
select taken_utc as snapshot_utc, (select count(*) from md_snapshot) as probes from md_snapshot where probe = 'md_all';
-- [/Q00]

prompt === Q04 models in discovery by vendor and access
-- [Q04]
select case when grouping(m.vendor) = 1 then 'all' else m.vendor end as vendor,
       case when grouping(m.model_access) = 1 then '-' else m.model_access end as model_access,
       count(*) as models
  from md_model m
 group by grouping sets ((m.vendor, m.model_access), ())
 order by grouping(m.vendor), m.vendor, m.model_access;
-- [/Q04]

prompt === Q05a Phoenix on-demand chat models by vendor
-- [Q05a]
select m.vendor, count(*) as models
  from md_model m
  join md_availability a on a.model_id = m.model_id
 where a.region = 'us-phoenix-1' and a.on_demand = 1
   and exists (select 1 from md_capability k where k.model_id = m.model_id and k.capability = 'CHAT')
 group by m.vendor
 order by m.vendor;
-- [/Q05a]

prompt === Q05b the xAI and Google ones, with what the S2 rules read
-- [Q05b]
select m.model_id, m.model_access, c.od_region_count as od_regions,
       nvl(to_char(a.time_deprecated, 'YYYY-MM-DD'), '-') as deprecated,
       nvl(to_char(a.time_od_retired, 'YYYY-MM-DD'), '-') as phx_od_retires,
       c.retires_within_30d as ret_30d,
       case when p.model_id is null then 'no' else 'yes' end as priced
  from md_model m
  join md_availability a on a.model_id = m.model_id and a.region = 'us-phoenix-1' and a.on_demand = 1
  join v_catalogue c on c.model_id = m.model_id
  left join rl_price p on p.model_id = m.model_id
 where m.vendor in ('xai', 'google')
   and exists (select 1 from md_capability k where k.model_id = m.model_id and k.capability = 'CHAT')
 order by m.vendor desc, m.model_id;
-- [/Q05b]

prompt === Q06a the Grok 4.20 ids: discovery against ListModels
-- [Q06a]
select model_id, in_discovery, in_listmodels, nvl(listmodels_lifecycle, '-') as lm_state
  from v_catalogue
 where model_id like 'xai.grok-4.20%'
 order by model_id;
-- [/Q06a]

prompt === Q06b discovery asked for those ids directly (modelId filter)
-- [Q06b]
select s.probe, json_value(s.body, '$.request') as filter_sent, json_value(s.body, '$.item_count') as items
  from md_snapshot s
 where s.probe in ('md_grok420_nonreasoning', 'md_grok420_0309_nonreasoning')
 order by s.probe;
-- [/Q06b]

prompt === Q06c every model in ListModels but not in discovery
-- [Q06c]
select count(*) as models from v_catalogue where in_listmodels = 'Y' and in_discovery = 'N';
-- [/Q06c]

prompt === Q07a apiCapability values (models per value; one model can carry several)
-- [Q07a]
select nvl(api_capability, '(none)') as api_capability, count(distinct model_id) as models
  from md_api_capability
 group by api_capability
 order by count(distinct model_id) desc, api_capability;
-- [/Q07a]

prompt === Q07b parameter names for "maximum output tokens"
-- [Q07b]
select name, count(distinct model_id) as models
  from md_parameter
 where regexp_like(name, 'max.*tokens', 'i') and name not like '%PerDocument'
 group by name
 order by count(distinct model_id) desc, name;
-- [/Q07b]

prompt === Q08 returned by isOnDemandRetired=false, yet retiring within 30 days of the snapshot
-- [Q08]
select jt.model_id, substr(jt.t_odr, 1, 10) as phx_od_retires
  from md_snapshot s,
       json_table(s.body, '$.items[*]' columns (
         model_id varchar2(200) path '$.modelId',
         nested path '$.availability[*]' columns (
           region varchar2(40) path '$.region',
           t_odr  varchar2(40) path '$.timeOnDemandRetired'))) jt
 where s.probe = 'md_region_od_not_retired'
   and jt.region = 'us-phoenix-1'
   and jt.t_odr is not null
   and to_timestamp(substr(jt.t_odr, 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS')
       <= to_timestamp(substr(s.taken_utc, 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS') + interval '30' day
 order by jt.model_id;
-- [/Q08]

prompt === Q09a Cohere and Meta chat models: how many are on demand in Phoenix
-- [Q09a]
select m.vendor, count(*) as chat_models, count(p.model_id) as on_demand_in_phoenix
  from md_model m
  left join (select distinct a.model_id from md_availability a
              where a.region = 'us-phoenix-1' and a.on_demand = 1) p on p.model_id = m.model_id
 where m.vendor in ('cohere', 'meta')
   and exists (select 1 from md_capability k where k.model_id = m.model_id and k.capability = 'CHAT')
 group by m.vendor
 order by m.vendor;
-- [/Q09a]

prompt === Q09b where the two we used to call are on demand
-- [Q09b]
select a.model_id, listagg(a.region, ', ') within group (order by a.region) as on_demand_regions
  from md_availability a
 where a.model_id in ('cohere.command-a-03-2025', 'meta.llama-3.3-70b-instruct') and a.on_demand = 1
 group by a.model_id
 order by a.model_id;
-- [/Q09b]

prompt === end (nothing changed)
