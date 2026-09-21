-- v1.0 | 00_registry.sql | CHANGES STATE
--
-- Keep the losers. This is the whole idea: a row per candidate tried, with its
-- metric and its training time, so "why this model" is a query rather than
-- somebody's recollection six months later.

create table AI_COMPONENT (
  component_id  number(4)     not null,
  display_name  varchar2(60)  not null,
  mining_func   varchar2(30)  not null,
  model_name    varchar2(30),            -- the winner, renamed into place
  constraint ai_component_pk primary key (component_id)
);

create table MODEL_CANDIDATE (
  candidate_id  number(8) generated always as identity,
  component_id  number(4)     not null,
  algorithm     varchar2(40)  not null,
  metric_name   varchar2(30)  not null,
  metric_value  number,
  train_sec     number(8,1),
  selected      varchar2(1)   default 'N' not null,
  trained_ts    timestamp(0)  default systimestamp not null,
  constraint model_candidate_pk primary key (candidate_id),
  constraint model_candidate_fk foreign key (component_id)
    references AI_COMPONENT (component_id),
  constraint model_candidate_sel_ck check (selected in ('Y','N'))
);

-- The screen behind "why this model".
create or replace view V_BAKEOFF_RESULTS as
select c.display_name, c.mining_func, mc.algorithm, mc.metric_name,
       mc.metric_value, mc.train_sec, mc.selected, mc.trained_ts
  from MODEL_CANDIDATE mc
  join AI_COMPONENT   c on c.component_id = mc.component_id;
