-- v1.0 - brief 14 ROUTE_LAB: tables, discovery views and the APEX views granted to ASKORACLE. Run as ROUTE_LAB.
--        public copy, 12-Oct-2026: the four views only the lab's APEX pages read (V_MODEL_REGIONS,
--              V_MODEL_PARAMS, V_ROUTE_RESULTS, V_CALLS_SUMMARY) and the SELECT grants to the app's parsing
--              schema are left out; V_CATALOGUE stays (sql/06 reads it). Nothing else changed.
--        v1.0: first version (PLAN.md v1.0 section 10). Written in the plan stage; NOT run yet.
--
-- Usage : sqlplus -s -L ROUTE_LAB@<alias> @01_route_lab_objects.sql   (password on stdin, never on the command line)
-- Re-run: safe. Tables are created if absent (data kept); views are replaced; grants re-applied.
-- No OCID is written by this script. RL_CONFIG and RL_PROFILE receive the compartment and profile OCIDs from
-- poc/db_register.py through bind variables; neither table is granted to anyone, and no view exposes them.
whenever sqlerror exit failure rollback
set verify off feedback off serveroutput on size unlimited define off

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '01: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '01: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if user != 'ROUTE_LAB' then
    raise_application_error(-20140, '01: run as ROUTE_LAB');
  end if;
end;
/

-- ---------------------------------------------------------------------------------------------- tables
create table if not exists RL_CONFIG (
  name  varchar2(30)  primary key check (name in ('COMPARTMENT_ID', 'REGION', 'CREDENTIAL')),
  value varchar2(400) not null,
  constraint rl_config_phx check (name <> 'REGION' or value = 'us-phoenix-1')
);

create table if not exists RL_PROFILE (
  alias       varchar2(10)  primary key check (regexp_like(alias, '^P[1-5]$')),
  model_id    varchar2(200) not null,
  ocid        varchar2(255),            -- null when S4 did not create this profile (direct calls still run)
  ocid_sha16  varchar2(16),
  region      varchar2(30)  default 'us-phoenix-1' not null check (region = 'us-phoenix-1'),
  loaded_utc  timestamp     default sys_extract_utc(systimestamp) not null
);

create table if not exists RL_PRICE (
  model_id     varchar2(200) primary key,
  usd_in_1m    number not null check (usd_in_1m >= 0),
  usd_out_1m   number not null check (usd_out_1m >= 0)
);

-- one open allotment per step; the Python ledger moves calls and USD here (PLAN.md section 7)
create table if not exists RL_BUDGET (
  step            varchar2(30) primary key check (step in ('s6_plsql', 's7_select_ai')),
  allotment_no    number       not null,
  calls_allotted  number       not null check (calls_allotted > 0),
  usd_allotted    number       not null check (usd_allotted > 0),
  calls_used      number       default 0 not null,
  usd_used_est    number       default 0 not null,
  opened_utc      timestamp    default sys_extract_utc(systimestamp) not null,
  closed_utc      timestamp,
  constraint rl_budget_calls check (calls_used <= calls_allotted)
);

create table if not exists RL_CALL (
  call_id            number generated always as identity primary key,
  step               varchar2(30)  not null,
  caller             varchar2(12)  not null check (caller in ('PLSQL', 'SELECT_AI')),
  alias              varchar2(10)  not null,
  kind               varchar2(10)  not null check (kind in ('DIRECT', 'PROFILE')),
  model_id           varchar2(200) not null,
  prompt_no          number        not null,
  http_status        number,
  err_code           varchar2(100),
  err_text           varchar2(1000),
  latency_ms         number,
  model_id_shape     varchar2(120),
  model_version      varchar2(100),
  prompt_tokens      number,
  completion_tokens  number,
  reasoning_tokens   number,
  finish_reason      varchar2(40),
  answer_ok          varchar2(1)   check (answer_ok in ('Y', 'N')),
  answer_snip        varchar2(200),
  usd_est            number,
  called_utc         timestamp     default sys_extract_utc(systimestamp) not null
);

create table if not exists MD_SNAPSHOT (
  probe      varchar2(64) primary key,
  taken_utc  varchar2(30) not null,
  body       clob         not null check (body is json)
);

-- redacted results of the Python steps (S3 smoke, S4 creates, S5 summary, S9), for the APEX page
create table if not exists RL_RESULT (
  kind       varchar2(10)  not null check (kind in ('S3', 'S4', 'S5', 'S9')),
  seq        number        not null,
  alias      varchar2(40),
  payload    clob          not null check (payload is json),
  loaded_utc timestamp     default sys_extract_utc(systimestamp) not null,
  constraint rl_result_pk primary key (kind, seq)
);

-- ---------------------------------------------------------------------------------------------- discovery views
create or replace view MD_MODEL as
select jt.model_id, jt.vendor, jt.model_access, jt.param_count, s.taken_utc
  from md_snapshot s,
       json_table(s.body, '$.items[*]' columns (
         model_id     varchar2(200) path '$.modelId',
         vendor       varchar2(50)  path '$.vendor',
         model_access varchar2(20)  path '$.modelAccess',
         param_count  number        path '$.parameters.size()')) jt
 where s.probe = 'md_all';

create or replace view MD_CAPABILITY as
select jt.model_id, jt.capability
  from md_snapshot s,
       json_table(s.body, '$.items[*]' columns (
         model_id varchar2(200) path '$.modelId',
         nested path '$.capabilities[*]' columns (capability varchar2(60) path '$'))) jt
 where s.probe = 'md_all' and jt.capability is not null;

create or replace view MD_API_CAPABILITY as
select jt.model_id, jt.api_capability
  from md_snapshot s,
       json_table(s.body, '$.items[*]' columns (
         model_id varchar2(200) path '$.modelId',
         nested path '$.apiCapability[*]' columns (api_capability varchar2(60) path '$'))) jt
 where s.probe = 'md_all';

create or replace view MD_AVAILABILITY as
select jt.model_id, jt.realm, jt.region,
       jt.on_demand, jt.dedicated,
       to_timestamp(substr(jt.t_dep, 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS') as time_deprecated,
       to_timestamp(substr(jt.t_odr, 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS') as time_od_retired,
       to_timestamp(substr(jt.t_der, 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS') as time_ded_retired,
       jt.replacements
  from md_snapshot s,
       json_table(s.body, '$.items[*]' columns (
         model_id varchar2(200) path '$.modelId',
         nested path '$.availability[*]' columns (
           realm        varchar2(20)  path '$.realm',
           region       varchar2(40)  path '$.region',
           on_demand    number exists path '$.servingModes[*]?(@ == "ON_DEMAND")',
           dedicated    number exists path '$.servingModes[*]?(@ == "DEDICATED")',
           t_dep        varchar2(40)  path '$.timeDeprecated',
           t_odr        varchar2(40)  path '$.timeOnDemandRetired',
           t_der        varchar2(40)  path '$.timeDedicatedRetired',
           replacements varchar2(1000) format json path '$.supportedReplacements'))) jt
 where s.probe = 'md_all' and jt.region is not null;

create or replace view MD_PARAMETER as
select jt.model_id, jt.name, jt.type, jt.minimum, jt.maximum, jt.default_value
  from md_snapshot s,
       json_table(s.body, '$.items[*]' columns (
         model_id varchar2(200) path '$.modelId',
         nested path '$.parameters[*]' columns (
           name          varchar2(100) path '$.name',
           type          varchar2(40)  path '$.type',
           minimum       varchar2(40)  path '$.minimum',
           maximum       varchar2(40)  path '$.maximum',
           default_value varchar2(100) path '$.defaultValue'))) jt
 where s.probe = 'md_all' and jt.name is not null;

-- ListModels (capability=CHAT, which the service ignores; study finding 5); ids were redacted before loading
create or replace view LM_MODEL as
select jt.display_name, jt.model_type, jt.lifecycle,
       to_timestamp(substr(jt.t_dep, 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS') as time_deprecated,
       to_timestamp(substr(jt.t_odr, 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS') as time_od_retired
  from md_snapshot s,
       json_table(s.body, '$.items[*]' columns (
         display_name varchar2(200) path '$.displayName',
         model_type   varchar2(20)  path '$.type',
         lifecycle    varchar2(20)  path '$.lifecycleState',
         t_dep        varchar2(40)  path '$.timeDeprecated',
         t_odr        varchar2(40)  path '$.timeOnDemandRetired')) jt
 where s.probe = 'lm_chat';

-- ---------------------------------------------------------------------------------------------- catalogue view
create or replace view V_CATALOGUE as
with snap as (
  select to_timestamp(substr(max(taken_utc), 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS') as t0
    from md_snapshot where probe = 'md_all'
), caps as (
  select model_id, listagg(capability, ', ') within group (order by capability) as capabilities
    from md_capability group by model_id
), phx as (
  select model_id, max(on_demand) as on_demand, max(dedicated) as dedicated, min(time_od_retired) as od_retired
    from md_availability where region = 'us-phoenix-1' group by model_id
), od as (
  select model_id, count(distinct case when on_demand = 1 then region end) as od_regions
    from md_availability group by model_id
), lm as (
  select display_name as model_id, max(lifecycle) as lifecycle
    from lm_model where model_type = 'BASE' group by display_name
), ids as (
  select model_id from md_model union select model_id from lm
)
select i.model_id,
       nvl(m.vendor, substr(i.model_id, 1, instr(i.model_id, '.') - 1)) as vendor,
       m.model_access,
       c.capabilities,
       case when p.on_demand = 1 then 'Y' else 'N' end as phx_on_demand,
       case when p.dedicated = 1 then 'Y' else 'N' end as phx_dedicated,
       nvl(o.od_regions, 0) as od_region_count,
       cast(p.od_retired as date) as phx_od_retires,
       case when p.od_retired is not null and p.od_retired > s.t0 and p.od_retired <= s.t0 + interval '30' day
            then 'Y' else 'N' end as retires_within_30d,
       case when m.model_id is not null then 'Y' else 'N' end as in_discovery,
       case when l.model_id is not null then 'Y' else 'N' end as in_listmodels,
       l.lifecycle as listmodels_lifecycle,
       m.param_count,
       cast(s.t0 as date) as snapshot_utc
  from ids i
  cross join snap s
  left join md_model m on m.model_id = i.model_id
  left join caps c     on c.model_id = i.model_id
  left join phx p      on p.model_id = i.model_id
  left join od o       on o.model_id = i.model_id
  left join lm l       on l.model_id = i.model_id;

prompt ROUTE_LAB objects:
select object_type, object_name, status from user_objects
 where object_type in ('TABLE', 'VIEW') order by object_type, object_name;
