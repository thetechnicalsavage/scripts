-- v1.6 - ANOMOPS in ora26ai / ORCLPDB1 (the observer) for application 900, phase 4 (builder M): the hidden
--        incident schedule, its push to the target and the grading of PLAN.md section 7. Run as ANOMOPS after 94 and
--        95 (tools/anom_obs_run.sh run 96_anom_grade.sql <log>; during training through tools/anom_gap_deploy.sql,
--        so the truth loop's session never holds the replaced package). No password here. Contract v1.2 sections 8
--        and 9, v1.6 section 12, v1.8 section 13, v1.10 section 13.10.
--        v1.6: public copy: host name and local-time references removed. v1.5: (phase 5d, PLAN.md v1.7 7a "Attribution ties (amended 02-Oct 02:45Z)", before any evaluation
--              data) the primary attribution score of a caught incident is ATTR_P, the chance that at least one
--              expected signal is in the top 3 when tied weights are broken uniformly at random: walk the distinct
--              weights from the top; an expected signal in a group fully above the cut gives 1; else, for the group
--              that straddles the cut (m signals, s slots left, e expected), 1 - C(m - e, s) / C(m, s) (the new
--              tie_chance, exact: two integer products and one division); else 0. The same list as v1.4 for every
--              detector: MSET, SVM, EM and PCA by the opening minute's stored details, STATIC and SEASONAL by the
--              weights of the signals that FIRED (no stored weight: the first 3 fired, in order, no tie). Also per
--              incident ATTR_LENIENT (v1.4's "top 3 including ties", 0/1, = ATTR_HIT as a number) and TIE_SIZE (the
--              number of signals sharing the weight at position 3, or at the last position when fewer are listed;
--              1 = no tie at the cut). GRADE_RESULT adds ATTR_P_MEAN (mean ATTR_P over the caught incidents, the
--              primary figure) and TIE_SIZE_MEDIAN; ATTR_HIT_RATE stays the lenient rate. GRADE_SCENARIO adds
--              ATTR_P_MEAN. TOP3 and ATTR_HIT keep their v1.4 values. One ranking for all of it (attribute(); v1.4's
--              top_k and fired_top_k are gone). Specification: tie_chance appended at its end. 02-Oct-2026.
--        v1.4: (phase 5c, PLAN 7a "top 3 including ties, for every detector alike") grade() ranks by weight:
--              MSET, SVM, EM and PCA take every signal of the opening minute whose rank() by weight (desc) is <= 3,
--              i.e. every signal whose weight is at least the third-highest weight. v1.0-v1.3 took the stored
--              rank <= 3, a position in which Oracle's @rank orders tied weights arbitrarily (MSET weights come in
--              fifths: ties are the rule, not the exception), so a tied expected signal could count as a miss.
--              STATIC and SEASONAL, attributed by the metric that fired, rank the FIRED signals by their weight in
--              the opening minute the same way (95 v1.2 stores every breach); a fired signal without a stored weight
--              (details written before 95 v1.2) falls back to v1.1's first 3 of the list. The hit is read from that
--              list for every detector. GRADE_INCIDENT.TOP3 widened to varchar2(1000) (a tie can name many signals).
--              Specification unchanged. 02-Oct-2026.
--        v1.3: (phase 4b, PLAN.md 7a: no observer call when a hidden incident begins) plan_incidents(p_from, 'Y')
--              pushes the generated rows to SHOP.CHAOS_PLAN through ANOM_CHAOS_LINK in the same transaction (one
--              distributed commit: the plan exists on both sides or on neither) and the target's dispatchers start
--              them (93 v1.2). push_plan(p_plan_from) pushes the PLANNED rows of a plan the target lacks (a test
--              plan, or a retry); mirror_plan copies the target's statuses back into INCIDENT_PLAN (94 v1.2's truth
--              loop calls it every 5 minutes over its own link session). ANOM_INCIDENT_JOB is removed: it started
--              each incident through the link, which put a target logon in every incident's first minute. run_due
--              (kept in the specification) is now a one-off mirror and never starts anything. 01-Oct-2026.
--        v1.2: (phase 4 review, Codex finding 1) a short run's minutes are drawn from 10 to the smaller of 40 and the
--              scenario's maximum in contract section 5 (PKG_CHAOS refuses more with -20103, and run_due would have
--              marked 4 of the 28 seeded runs SKIPPED: temp_spill never ran); the generated plan is validated
--              against those maximums before it is committed (-20402). One draw per duration as before, so the
--              seeded sequence stays aligned. The specification is unchanged. 01-Oct-2026.
--        v1.1: (integrator, contract v1.2 / PLAN 7a) STATIC and SEASONAL are attributed "by the metric that fired":
--              the first 3 signals of the episode's FIRED list (95 v1.1: the signals whose 3 consecutive breaches
--              opened it), not the opening minute's top 3 by weight. Other detectors unchanged. 01-Oct-2026.
--        v1.0: first version, 01-Oct-2026.
--
--        INCIDENT_PLAN   the 48-hour evaluation schedule, generated once by plan_incidents(p_from) with seed
--                        20261004 (MINSTD generator: s = s * 48271 mod 2147483647, u = s / 2147483647, so the
--                        plan can be recomputed anywhere). Day 0 = dev (the first 24 h), day 1 = test. Per day:
--                        the 11 short scenarios once each and slow_drift once (180 min) in a shuffled order; each
--                        scenario's intensity is drawn for day 0 (LOW when u < 0.5) and is the opposite on day 1,
--                        so every scenario runs at both intensities and exactly half of these 24 are LOW. Short runs
--                        last 10-40 min, never more than the scenario's maximum (contract section 5: 30 or 60). The first start is 20-40 min into the day; each next start is the later of
--                        80-100 min after the previous start and 45 min after the previous end. While the day has
--                        room, extra short runs (scenario and intensity drawn) follow. Every run ends at least 40 min
--                        before the day ends (its 30-min cool-down and the 10-min catch margin stay in its day).
--        CHAOS_PLAN push (v1.3) plan_incidents(p_from, 'Y') inserts the rows into SHOP.CHAOS_PLAN@ANOM_CHAOS_LINK
--                        before its commit; the target refuses a row over its scenario's maximum (a check
--                        constraint), and then nothing is kept on either side (-20405). On the target a dispatcher
--                        starts each row when it is due (source SCHEDULE) or SKIPS it (another run active, or more
--                        than 5 minutes late); mirror_plan brings STARTED / SKIPPED / MISSED (a late row), the run id
--                        and the note back. Test rows use plan ids 9000-9999 (tools/anom_plan_test.sql).
--        GRADE_INCIDENT  per run tag, model and incident: caught (an ALERT_EVAL episode starts in [incident start,
--                        incident end + 10 min]), the first such episode's start, time to detect (minutes from the
--                        incident start), its first minute's top 3 signals including ties (v1.4: rank() by weight
--                        <= 3; STATIC, SEASONAL: of the signals that fired) and whether one is in the scenario's
--                        EXPECTED_SIGNALS (ATTR_HIT Y/N, = ATTR_LENIENT 1/0), ATTR_P (v1.5, the primary score: the
--                        chance of an expected signal in the top 3 with ties broken at random) and TIE_SIZE (v1.5);
--                        attribution is not scored for IFOREST (all four null).
--        GRADE_RESULT    per run tag and model: incidents, caught, median and p90 time to detect (PERCENTILE_CONT
--                        0.5 / 0.9 over the caught ones), false alarms (episodes starting in [p_from, p_to) outside
--                        every chaos run's [start, end + 30 min], any source) and false alarms per 24 h, attribution
--                        (v1.5: ATTR_P_MEAN = mean ATTR_P over the caught incidents, the primary figure; the lenient
--                        hits / scored and ATTR_HIT_RATE; TIE_SIZE_MEDIAN), and the exact McNemar test of the MSET
--                        model against each rival over the
--                        paired incidents (b = MSET caught and rival missed, c = the reverse, two-sided
--                        p = min(1, 2 * sum_{i <= min(b,c)} C(b+c, i) / 2^(b+c))); best_rival = Y on the rival with the
--                        most caught incidents (ties: fewer false alarms, then the lower median time to detect,
--                        then the name). The MSET reference is chosen by the same order among MSET models.
--        GRADE_SCENARIO  view: caught / total per run tag, model and scenario.
--        Idempotent: tables are created when absent and checked (v1.5: an existing GRADE_INCIDENT / GRADE_RESULT
--        gains its new columns and checks), the package and view are re-applied, a v1.2 ANOM_INCIDENT_JOB is dropped
--        (v1.3). INCIDENT_PLAN is never regenerated once it has rows.
set serveroutput on size unlimited format wrapped
set verify off
set linesize 200
set feedback off
whenever sqlerror exit failure rollback
alter session set ddl_lock_timeout = 30;

begin
  if sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '96: run in the PDB (ORCLPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, '96: run as ANOMOPS');
  end if;
end;
/

declare
  procedure mk (p_table varchar2, p_ddl varchar2) is
    n number;
  begin
    select count(*) into n from user_tables where table_name = p_table;
    if n = 0 then
      execute immediate p_ddl;
      dbms_output.put_line('  table '||p_table||' created');
    else
      dbms_output.put_line('  table '||p_table||' already present');
    end if;
  end;
begin
  mk('INCIDENT_PLAN', q'[
    create table INCIDENT_PLAN (
      plan_id        number         not null,
      plan_from      date           not null,
      day_no         number(1)      not null,
      seq_in_day     number         not null,
      scenario       varchar2(30)   not null,
      intensity      varchar2(4)    not null,
      planned_start  date           not null,
      minutes        number         not null,
      status         varchar2(8)    default 'PLANNED' not null,
      run_id         number,
      acted_ts       timestamp,
      message        varchar2(4000),
      seed           number         not null,
      created_ts     timestamp      not null,
      constraint incident_plan_pk    primary key (plan_id),
      constraint incident_plan_st_ck check (status in ('PLANNED', 'STARTED', 'SKIPPED', 'MISSED')),
      constraint incident_plan_in_ck check (intensity in ('LOW', 'HIGH')),
      constraint incident_plan_mi_ck check (minutes between 2 and 240)
    )]');

  mk('GRADE_INCIDENT', q'[
    create table GRADE_INCIDENT (
      run_tag      varchar2(40)   not null,
      model_name   varchar2(128)  not null,
      detector     varchar2(10)   not null,
      run_id       number         not null,
      scenario     varchar2(30),
      intensity    varchar2(4),
      inc_start    date           not null,
      inc_end      date           not null,
      caught       char(1)        not null,
      alert_start  date,
      ttd_min      number,
      top3         varchar2(1000),
      attr_hit     char(1),
      attr_lenient number(1),
      attr_p       number,
      tie_size     number(3),
      constraint grade_incident_pk primary key (run_tag, model_name, run_id),
      constraint grade_incident_c_ck check (caught in ('Y', 'N')),
      constraint grade_incident_a_ck check (attr_hit in ('Y', 'N')),
      constraint grade_incident_l_ck check (attr_lenient in (0, 1)),
      constraint grade_incident_p_ck check (attr_p between 0 and 1)
    )]');

  mk('GRADE_RESULT', q'[
    create table GRADE_RESULT (
      run_tag         varchar2(40)   not null,
      model_name      varchar2(128)  not null,
      detector        varchar2(10)   not null,
      grade_from      date           not null,
      grade_to        date           not null,
      hours           number         not null,
      n_incidents     number         not null,
      n_caught        number         not null,
      ttd_median      number,
      ttd_p90         number,
      n_false_alarms  number         not null,
      fa_per_24h      number         not null,
      n_attr_scored   number,
      n_attr_hit      number,
      attr_hit_rate   number,
      mcnemar_ref     varchar2(128),
      mcnemar_b       number,
      mcnemar_c       number,
      mcnemar_p       number,
      best_rival      char(1),
      graded_ts       timestamp      not null,
      attr_p_mean     number,
      tie_size_median number,
      constraint grade_result_pk primary key (run_tag, model_name),
      constraint grade_result_b_ck check (best_rival in ('Y', 'N'))
    )]');
end;
/

declare
  procedure need (p_table varchar2, p_cols varchar2) is
    l_missing varchar2(4000);
  begin
    select listagg(c.col, ',') within group (order by c.col) into l_missing
      from (select regexp_substr(p_cols, '[^,]+', 1, level) col
              from dual connect by level <= regexp_count(p_cols, ',') + 1) c
     where not exists (select 1 from user_tab_columns t
                        where t.table_name = p_table and t.column_name = c.col);
    if l_missing is not null then
      raise_application_error(-20900, '96: '||p_table||' lacks column(s) '||l_missing);
    end if;
  end;
  -- v1.5: a column a table created by v1.0-v1.4 lacks (nullable: existing rows keep null)
  procedure addcol (p_table varchar2, p_col varchar2, p_type varchar2) is
    n number;
  begin
    select count(*) into n from user_tab_columns where table_name = p_table and column_name = p_col;
    if n = 0 then
      execute immediate 'alter table ' || p_table || ' add (' || lower(p_col) || ' ' || p_type || ')';
      dbms_output.put_line('  '||p_table||'.'||p_col||' added ('||p_type||')');
    end if;
  end;
  -- v1.5: a check constraint a table created by v1.0-v1.4 lacks
  procedure addck (p_table varchar2, p_name varchar2, p_cond varchar2) is
    n number;
  begin
    select count(*) into n from user_constraints where table_name = p_table and constraint_name = p_name;
    if n = 0 then
      execute immediate 'alter table ' || p_table || ' add constraint ' || lower(p_name) || ' check (' || p_cond || ')';
      dbms_output.put_line('  '||p_table||' constraint '||p_name||' added');
    end if;
  end;
begin
  addcol('GRADE_INCIDENT', 'ATTR_LENIENT', 'number(1)');
  addcol('GRADE_INCIDENT', 'ATTR_P', 'number');
  addcol('GRADE_INCIDENT', 'TIE_SIZE', 'number(3)');
  addck('GRADE_INCIDENT', 'GRADE_INCIDENT_L_CK', 'attr_lenient in (0, 1)');
  addck('GRADE_INCIDENT', 'GRADE_INCIDENT_P_CK', 'attr_p between 0 and 1');
  addcol('GRADE_RESULT', 'ATTR_P_MEAN', 'number');
  addcol('GRADE_RESULT', 'TIE_SIZE_MEDIAN', 'number');
  need('INCIDENT_PLAN',  'PLAN_ID,PLAN_FROM,DAY_NO,SEQ_IN_DAY,SCENARIO,INTENSITY,PLANNED_START,MINUTES,STATUS,RUN_ID,'
                      || 'ACTED_TS,MESSAGE,SEED,CREATED_TS');
  need('GRADE_INCIDENT', 'RUN_TAG,MODEL_NAME,DETECTOR,RUN_ID,SCENARIO,INTENSITY,INC_START,INC_END,CAUGHT,ALERT_START,'
                      || 'TTD_MIN,TOP3,ATTR_HIT,ATTR_LENIENT,ATTR_P,TIE_SIZE');
  -- v1.4: TOP3 holds every signal tied at the third weight (a v1.0-v1.3 table has varchar2(200))
  for c in (select data_length from user_tab_columns
             where table_name = 'GRADE_INCIDENT' and column_name = 'TOP3' and data_length < 1000) loop
    execute immediate 'alter table GRADE_INCIDENT modify (top3 varchar2(1000))';
    dbms_output.put_line('  GRADE_INCIDENT.TOP3 widened from varchar2('||c.data_length||') to varchar2(1000)');
  end loop;
  need('GRADE_RESULT',   'RUN_TAG,MODEL_NAME,DETECTOR,GRADE_FROM,GRADE_TO,HOURS,N_INCIDENTS,N_CAUGHT,TTD_MEDIAN,TTD_P90,'
                      || 'N_FALSE_ALARMS,FA_PER_24H,N_ATTR_SCORED,N_ATTR_HIT,ATTR_HIT_RATE,MCNEMAR_REF,MCNEMAR_B,'
                      || 'MCNEMAR_C,MCNEMAR_P,BEST_RIVAL,GRADED_TS,ATTR_P_MEAN,TIE_SIZE_MEDIAN');
  dbms_output.put_line('  table shapes checked');
end;
/

-- v1.5: attr_p_mean = the mean ATTR_P of the caught incidents (avg skips the null of a miss)
create or replace view GRADE_SCENARIO as
select run_tag, model_name, detector, scenario,
       count(*) n_incidents,
       count(case when caught = 'Y' then 1 end) n_caught,
       median(ttd_min) ttd_median,
       count(case when attr_hit = 'Y' then 1 end) n_attr_hit,
       round(avg(attr_p), 6) attr_p_mean
  from GRADE_INCIDENT
 group by run_tag, model_name, detector, scenario;

comment on table INCIDENT_PLAN is 'App 900: the hidden 48-h incident schedule (seed 20261004), pushed to and started by the target';
comment on table GRADE_INCIDENT is 'App 900: per run tag, model and incident: caught, time to detect, attribution';
comment on table GRADE_RESULT is 'App 900: PLAN.md section 7 scoreboard per run tag and model, with exact McNemar vs MSET';
comment on table GRADE_SCENARIO is 'App 900: caught / total per run tag, model and scenario';
comment on column GRADE_INCIDENT.ATTR_HIT is 'Y when an expected signal is in the top 3 including ties (PLAN 7a v1.4 rule; = ATTR_LENIENT)';
comment on column GRADE_INCIDENT.ATTR_LENIENT is '1 when an expected signal is in the top 3 including ties (the lenient rule), else 0';
comment on column GRADE_INCIDENT.ATTR_P is 'PLAN 7a (v1.7) primary attribution: chance of an expected signal in the top 3, ties broken at random';
comment on column GRADE_INCIDENT.TIE_SIZE is 'signals sharing the weight at position 3 (at the last position when fewer are listed); 1 = no tie';
comment on column GRADE_RESULT.ATTR_P_MEAN is 'mean ATTR_P over the caught incidents (PLAN 7a v1.7: the primary attribution figure)';
comment on column GRADE_RESULT.ATTR_HIT_RATE is 'lenient attribution rate: N_ATTR_HIT / N_ATTR_SCORED (top 3 including ties)';
comment on column GRADE_RESULT.TIE_SIZE_MEDIAN is 'median TIE_SIZE over the caught incidents';

create or replace package PKG_ANOM_GRADE authid definer as
  -- v1.2 - app 900 hidden incident schedule and grading (see 96_anom_grade.sql). All times UTC.
  --        v1.2 (96 v1.5): tie_chance appended at the end; grade() also writes the amended attribution of PLAN 7a
  --        (v1.7). Every unit above it is unchanged.

  c_seed      constant number      := 20261004;
  c_late_min  constant pls_integer := 5;        -- run_due: a row more overdue than this is MISSED

  -- generates INCIDENT_PLAN for the 48 h from p_from (a whole minute, UTC); refuses (-20401) when the plan exists.
  -- p_commit = 'Y' also pushes the rows to the target's CHAOS_PLAN and commits both (v1.3; -20405 when the push
  -- fails: nothing is kept). p_commit = 'N' leaves the rows uncommitted and pushes nothing (tests). Prints and logs
  -- the number of rows.
  procedure plan_incidents (p_from in date, p_commit in varchar2 default 'Y');

  -- v1.3: the INCIDENT_PLAN mirror once, in its own link session (commits, closes the link). Starts nothing.
  procedure run_due;

  -- PLAN.md section 7 for the models scored under p_run_tag in SCORE_EVAL, over [p_from, p_to): rebuilds
  -- ALERT_EVAL, then GRADE_INCIDENT and GRADE_RESULT for the tag. Incidents = INCIDENT_TRUTH runs (not gone)
  -- whose source is in p_sources (comma list) and whose start is in the range; false-alarm windows = every run.
  -- p_refresh = 'Y' refreshes INCIDENT_TRUTH first (needs p_commit = 'Y'). p_commit = 'N': no commit (tests).
  procedure grade (p_run_tag in varchar2, p_from in date, p_to in date, p_sources in varchar2 default 'SCHEDULE',
                   p_refresh in varchar2 default 'Y', p_commit in varchar2 default 'Y');

  -- exact two-sided McNemar p for discordant counts b and c
  function mcnemar_exact_p (p_b in pls_integer, p_c in pls_integer) return number;

  -- the generator, exposed for tests: seeds it and returns the next p_n uniforms as a comma list
  function rng_sample (p_seed in number, p_n in pls_integer) return varchar2;

  -- v1.3 (added at the end of the specification: the units above and their dependants are unchanged)
  -- pushes the PLANNED rows of plan p_plan_from that the target's CHAOS_PLAN lacks; commits, closes the link, logs.
  -- -20400 null argument, -20406 no such plan, -20405 the push failed (nothing pushed).
  procedure push_plan (p_plan_from in date);
  -- copies the target's CHAOS_PLAN status, run id, time and note onto the INCIDENT_PLAN rows of the same plan id (a
  -- SKIPPED row whose note says "missed" becomes MISSED). No commit, the link stays open: the caller commits and
  -- closes it (94's truth loop, run_due).
  procedure mirror_plan;

  -- v1.5 (added at the end of the specification: the units above and their dependants are unchanged)
  -- PLAN 7a, amended 02-Oct-2026 02:45Z (PLAN.md v1.7): the chance that at least one of p_e expected signals
  -- among p_m signals tied at one weight takes one of the p_s top-3 slots left, when the tie is broken uniformly at
  -- random: 1 - C(p_m - p_e, p_s) / C(p_m, p_s). No factorial: the ratio of the two binomials is the product of
  -- (p_m - p_e - i) / (p_m - i) for i < p_s (equally, of (p_m - p_s - i) / (p_m - i) for i < p_e; the shorter one
  -- is used), formed as two exact integer products and one division while they stay under 36 digits (always for
  -- grading: s <= 3, m <= 40), else as a running product of fractions (NUMBER's 38 digits, never an overflow).
  -- 0 when p_e = 0 or p_s = 0. -20407 for a null, a negative, p_s > p_m or p_e > p_m.
  function tie_chance (p_m in pls_integer, p_s in pls_integer, p_e in pls_integer) return number;
end PKG_ANOM_GRADE;
/

create or replace package body PKG_ANOM_GRADE as
  -- v1.5 (attribution amended: ATTR_P with ties broken at random, ATTR_LENIENT, TIE_SIZE; tie_chance);
  -- v1.4 (attribution: top 3 including ties, by weight, for every detector); v1.3 (the plan pushed to the target
  -- and mirrored back; nothing starts an incident from here)
  c_top_k constant pls_integer := 3;   -- v1.4: PLAN 7a: every signal whose rank() by weight is <= 3
  c_mod   constant number := 2147483647;
  c_mul   constant number := 48271;
  g_state number;

  type t_names is table of varchar2(30);
  -- contract section 5 order; slow_drift is handled apart
  c_short constant t_names := t_names('blocking_chain', 'plan_regression', 'batch_wrong_time', 'hard_parse_storm',
                                      'commit_storm', 'io_storm', 'cpu_hog', 'temp_spill', 'conn_leak',
                                      'logon_storm', 'app_error_burst');

  procedure log_event (p_severity varchar2, p_message varchar2) is
    pragma autonomous_transaction;
  begin
    insert into COLLECT_LOG (log_ts, severity, message)
    values (sys_extract_utc(systimestamp), p_severity, substr(p_message, 1, 4000));
    commit;
  end;

  function now_utc return date is
  begin
    return cast(sys_extract_utc(systimestamp) as date);
  end;

  -- contract section 5's maximum minutes of a scenario: the same table as SHOP.PKG_CHAOS.max_minutes on the target
  -- (test/test_models_static.py checks that the two agree); null for a name outside the 12
  function max_min (p_name varchar2) return pls_integer is
  begin
    return case p_name
             when 'blocking_chain'   then 30
             when 'plan_regression'  then 60
             when 'slow_drift'       then 240
             when 'batch_wrong_time' then 30
             when 'hard_parse_storm' then 30
             when 'commit_storm'     then 30
             when 'io_storm'         then 30
             when 'cpu_hog'          then 30
             when 'temp_spill'       then 30
             when 'conn_leak'        then 60
             when 'logon_storm'      then 30
             when 'app_error_burst'  then 60
           end;
  end;

  -- ---------------------------------------------------------------- the generator (MINSTD)
  procedure seed (p number) is
  begin
    g_state := mod(p, c_mod);
    if g_state <= 0 then
      g_state := 1;
    end if;
  end;

  function rnd return number is
  begin
    g_state := mod(g_state * c_mul, c_mod);
    return g_state / c_mod;
  end;

  function rint (a pls_integer, b pls_integer) return pls_integer is
  begin
    return a + floor(rnd * (b - a + 1));
  end;

  function rng_sample (p_seed in number, p_n in pls_integer) return varchar2 is
    r varchar2(4000);
  begin
    seed(p_seed);
    for i in 1 .. p_n loop
      r := r || case when i > 1 then ',' end || to_char(round(rnd, 12), 'FM0.000000000000');
    end loop;
    return r;
  end;

  -- ---------------------------------------------------------------- the plan
  procedure close_chaos_link is
    e_not_open exception;
    pragma exception_init(e_not_open, -2081);
  begin
    dbms_session.close_database_link('ANOM_CHAOS_LINK');
  exception
    when e_not_open then
      null;   -- ORA-02081: not open in this session, the state we want
  end;

  -- v1.3: inserts the PLANNED rows of plan p_from that SHOP.CHAOS_PLAN lacks, through ANOM_CHAOS_LINK, row by row
  -- (at most a few dozen); returns how many. No commit: the caller's commit makes them durable on both sides.
  function push_rows (p_from date) return pls_integer is
    n      number;
    l_rows pls_integer := 0;
  begin
    for r in (select plan_id, planned_start, scenario, intensity, minutes from INCIDENT_PLAN
               where plan_from = p_from and status = 'PLANNED' order by plan_id) loop
      execute immediate 'select count(*) from SHOP.CHAOS_PLAN@ANOM_CHAOS_LINK where plan_id = :1' into n using r.plan_id;
      if n = 0 then
        execute immediate 'insert into SHOP.CHAOS_PLAN@ANOM_CHAOS_LINK (plan_id, planned_start_ts, scenario, intensity, '
                       || 'minutes) values (:1, :2, :3, :4, :5)'
          using r.plan_id, cast(r.planned_start as timestamp), r.scenario, r.intensity, r.minutes;
        l_rows := l_rows + 1;
      end if;
    end loop;
    return l_rows;
  end;

  procedure plan_incidents (p_from in date, p_commit in varchar2 default 'Y') is
    type t_item is record (scenario varchar2(30), intensity varchar2(4), drift boolean);
    type t_items is table of t_item index by pls_integer;
    l_int0   t_names := t_names();
    l_drift0 varchar2(4);
    l_items  t_items;
    l_tmp    t_item;
    l_id     pls_integer := 0;
    l_seq    pls_integer;
    l_day    date;
    l_limit  date;
    l_t      date;
    l_dur    pls_integer;
    l_pos    pls_integer;
    l_j      pls_integer;
    l_sc     varchar2(30);
    l_in     varchar2(4);
    l_err    varchar2(4000);
    n        number;

    function flip (p varchar2) return varchar2 is
    begin
      return case p when 'LOW' then 'HIGH' else 'LOW' end;
    end;

    procedure put (p_day pls_integer, p_sc varchar2, p_in varchar2, p_start date, p_min pls_integer) is
    begin
      l_id := l_id + 1;
      l_seq := l_seq + 1;
      insert into INCIDENT_PLAN (plan_id, plan_from, day_no, seq_in_day, scenario, intensity, planned_start, minutes,
                                 status, seed, created_ts)
      values (l_id, p_from, p_day, l_seq, p_sc, p_in, p_start, p_min, 'PLANNED', c_seed, sys_extract_utc(systimestamp));
    end;
  begin
    if p_from is null or p_from != trunc(p_from, 'MI') then
      raise_application_error(-20400, 'p_from must be a whole minute (UTC)');
    end if;
    if nvl(p_commit, '?') not in ('Y', 'N') then
      raise_application_error(-20400, 'p_commit must be Y or N');
    end if;
    select count(*) into n from INCIDENT_PLAN;
    if n > 0 then
      raise_application_error(-20401, 'INCIDENT_PLAN already holds '||n||' rows; it is generated once');
    end if;
    savepoint plan_start;
    seed(c_seed);
    -- day-0 intensity of each short scenario, then of slow_drift; day 1 takes the opposite
    l_int0.extend(c_short.count);
    for i in 1 .. c_short.count loop
      l_int0(i) := case when rnd < 0.5 then 'LOW' else 'HIGH' end;
    end loop;
    l_drift0 := case when rnd < 0.5 then 'LOW' else 'HIGH' end;

    for d in 0 .. 1 loop
      l_day := p_from + d;
      l_limit := l_day + (1440 - 40) / 1440;
      l_seq := 0;
      -- the 11 short scenarios, shuffled (Fisher-Yates), then slow_drift inserted at a drawn position 1..12
      l_items.delete;
      for i in 1 .. c_short.count loop
        l_items(i).scenario := c_short(i);
        l_items(i).intensity := case when d = 0 then l_int0(i) else flip(l_int0(i)) end;
        l_items(i).drift := false;
      end loop;
      for i in reverse 2 .. c_short.count loop
        l_j := rint(1, i);
        l_tmp := l_items(i);
        l_items(i) := l_items(l_j);
        l_items(l_j) := l_tmp;
      end loop;
      l_pos := rint(1, c_short.count + 1);
      for i in reverse l_pos .. c_short.count loop
        l_items(i + 1) := l_items(i);
      end loop;
      l_items(l_pos).scenario := 'slow_drift';
      l_items(l_pos).intensity := case when d = 0 then l_drift0 else flip(l_drift0) end;
      l_items(l_pos).drift := true;

      l_t := l_day + rint(20, 40) / 1440;
      for i in 1 .. l_items.count loop
        -- 10-40 min, capped by the scenario's maximum (one draw either way: the sequence stays aligned)
        l_dur := case when l_items(i).drift then 180 else rint(10, least(40, max_min(l_items(i).scenario))) end;
        if l_t + l_dur / 1440 > l_limit then
          raise_application_error(-20402, 'plan: day '||d||' has no room for '||l_items(i).scenario);
        end if;
        put(d, l_items(i).scenario, l_items(i).intensity, l_t, l_dur);
        l_t := greatest(l_t + rint(80, 100) / 1440, l_t + (l_dur + 45) / 1440);
      end loop;
      -- extra short runs while the day has room
      loop
        l_sc  := c_short(rint(1, c_short.count));
        l_in  := case when rnd < 0.5 then 'LOW' else 'HIGH' end;
        l_dur := rint(10, least(40, max_min(l_sc)));
        exit when l_t + l_dur / 1440 > l_limit;
        put(d, l_sc, l_in, l_t, l_dur);
        l_t := greatest(l_t + rint(80, 100) / 1440, l_t + (l_dur + 45) / 1440);
      end loop;
    end loop;
    -- every row must be startable: PKG_CHAOS refuses minutes outside 2 .. the scenario's maximum (-20103)
    select count(*) into n from INCIDENT_PLAN
     where plan_from = p_from
       and (minutes < 2 or minutes > case scenario
                                        when 'blocking_chain'   then 30
                                        when 'plan_regression'  then 60
                                        when 'slow_drift'       then 240
                                        when 'batch_wrong_time' then 30
                                        when 'hard_parse_storm' then 30
                                        when 'commit_storm'     then 30
                                        when 'io_storm'         then 30
                                        when 'cpu_hog'          then 30
                                        when 'temp_spill'       then 30
                                        when 'conn_leak'        then 60
                                        when 'logon_storm'      then 30
                                        when 'app_error_burst'  then 60
                                        else -1 end);
    if n > 0 then
      rollback to savepoint plan_start;
      raise_application_error(-20402, 'plan: '||n||' row(s) outside their scenario''s 2 .. maximum minutes; '
                                      ||'nothing generated');
    end if;
    dbms_output.put_line('  INCIDENT_PLAN: '||l_id||' rows from '||to_char(p_from, 'YYYY-MM-DD HH24:MI')||' UTC, seed '
                         ||c_seed||case when p_commit = 'N' then ' (not committed, not pushed)' end);
    if p_commit = 'Y' then
      -- v1.3: the same rows to the target, in this transaction; one commit keeps both sides or neither
      begin
        n := push_rows(p_from);
        commit;
      exception
        when others then
          l_err := sqlerrm;
          rollback;
          close_chaos_link;
          log_event('ERROR', 'event=incident_plan_push_failed from='||to_char(p_from, 'YYYY-MM-DD"T"HH24:MI"Z"')
                             ||' error="'||l_err||'" (nothing generated, nothing pushed)');
          raise_application_error(-20405, 'plan: the push to the target failed, nothing generated: '||substr(l_err, 1, 300));
      end;
      close_chaos_link;
      log_event('INFO', 'event=incident_plan_generated rows='||l_id||' pushed='||n||' from='
                        ||to_char(p_from, 'YYYY-MM-DD"T"HH24:MI"Z"')||' seed='||c_seed);
      dbms_output.put_line('  pushed to SHOP.CHAOS_PLAN: '||n||' rows');
    end if;
  end plan_incidents;

  procedure push_plan (p_plan_from in date) is
    n      number;
    l_rows pls_integer;
    l_err  varchar2(4000);
  begin
    if p_plan_from is null then
      raise_application_error(-20400, 'push_plan: p_plan_from is required');
    end if;
    select count(*) into n from INCIDENT_PLAN where plan_from = p_plan_from;
    if n = 0 then
      raise_application_error(-20406, 'push_plan: INCIDENT_PLAN has no plan from '||to_char(p_plan_from, 'YYYY-MM-DD HH24:MI'));
    end if;
    commit;   -- nothing local is pending when the target is written
    begin
      l_rows := push_rows(p_plan_from);
      commit;
    exception
      when others then
        l_err := sqlerrm;
        rollback;
        close_chaos_link;
        log_event('ERROR', 'event=plan_push_failed from='||to_char(p_plan_from, 'YYYY-MM-DD"T"HH24:MI"Z"')
                           ||' error="'||l_err||'"');
        raise_application_error(-20405, 'push_plan: the push to the target failed, nothing pushed: '||substr(l_err, 1, 300));
    end;
    close_chaos_link;
    log_event('INFO', 'event=plan_pushed from='||to_char(p_plan_from, 'YYYY-MM-DD"T"HH24:MI"Z"')||' rows='||l_rows
                      ||' of '||n);
    dbms_output.put_line('  pushed to SHOP.CHAOS_PLAN: '||l_rows||' of '||n||' rows');
  end push_plan;

  procedure mirror_plan is
  begin
    execute immediate q'[
      merge into INCIDENT_PLAN i
      using (select plan_id,
                    case when status = 'SKIPPED' and note like 'missed:%' then 'MISSED' else substr(status, 1, 8) end status,
                    run_id, cast(acted_ts as timestamp) acted_ts, substr(note, 1, 4000) note
               from SHOP.CHAOS_PLAN@ANOM_CHAOS_LINK) c
         on (i.plan_id = c.plan_id)
       when matched then update set i.status = c.status, i.run_id = c.run_id, i.acted_ts = c.acted_ts,
                                    i.message = c.note
            where decode(i.status, c.status, 0, 1) = 1 or decode(i.run_id, c.run_id, 0, 1) = 1
               or decode(i.acted_ts, c.acted_ts, 0, 1) = 1]';
  end mirror_plan;

  -- ---------------------------------------------------------------- the mirror, once (v1.3: never starts anything)
  procedure run_due is
    l_err varchar2(4000);
  begin
    commit;   -- nothing local is pending when the target is read
    mirror_plan;
    commit;   -- also ends the distributed transaction
    close_chaos_link;
  exception
    when others then
      l_err := sqlerrm;
      rollback;
      close_chaos_link;
      log_event('ERROR', 'event=plan_mirror_failed error="'||l_err||'"');
      raise;
  end run_due;

  -- ---------------------------------------------------------------- grading
  function mcnemar_exact_p (p_b in pls_integer, p_c in pls_integer) return number is
    n    pls_integer;
    l_t  number;
    l_s  number := 0;
  begin
    if p_b is null or p_c is null or p_b < 0 or p_c < 0 then
      raise_application_error(-20403, 'McNemar counts must be non-negative');
    end if;
    n := p_b + p_c;
    if n = 0 then
      return 1;
    end if;
    l_t := power(0.5, n);                  -- C(n, 0) / 2^n
    for i in 0 .. least(p_b, p_c) loop
      l_s := l_s + l_t;
      l_t := l_t * (n - i) / (i + 1);      -- C(n, i+1) / 2^n
    end loop;
    return least(1, 2 * l_s);
  end;

  -- v1.5: see the specification
  function tie_chance (p_m in pls_integer, p_s in pls_integer, p_e in pls_integer) return number is
    l_k   pls_integer;
    l_a   pls_integer;
    l_num number := 1;
    l_den number := 1;
    l_r   number := 1;
  begin
    if p_m is null or p_s is null or p_e is null or p_m < 0 or p_s < 0 or p_e < 0 or p_s > p_m or p_e > p_m then
      raise_application_error(-20407, 'tie_chance: need 0 <= s <= m and 0 <= e <= m (m = '||nvl(to_char(p_m), 'null')
                                      ||', s = '||nvl(to_char(p_s), 'null')||', e = '||nvl(to_char(p_e), 'null')||')');
    end if;
    if p_e = 0 or p_s = 0 then
      return 0;
    end if;
    -- C(m - e, s) / C(m, s) = C(m - s, e) / C(m, e): take the product with fewer factors. A factor reaches 0 (and
    -- the chance 1) exactly when m - e < s: fewer unexpected signals than slots.
    l_k := least(p_s, p_e);
    l_a := case when l_k = p_s then p_m - p_e else p_m - p_s end;
    if l_k * log(10, p_m) < 36 then
      for i in 0 .. l_k - 1 loop
        l_num := l_num * (l_a - i);        -- whole numbers below 10^36: exact in NUMBER
        l_den := l_den * (p_m - i);
      end loop;
      return (l_den - l_num) / l_den;
    end if;
    for i in 0 .. l_k - 1 loop
      l_r := l_r * (l_a - i) / (p_m - i);  -- each factor in [0, 1]: no overflow, 38 significant digits
    end loop;
    return 1 - l_r;
  end tie_chance;

  -- v1.5: PLAN 7a (amended, PLAN.md v1.7) for one caught incident, from one ranked list: MSET, SVM, EM, PCA the
  -- opening minute's stored details in position order; STATIC, SEASONAL the signals that FIRED (PLAN 7: "by the
  -- metric that fired") with their weight in the opening minute, in FIRED's order. A signal's rank is 1 + the
  -- number of listed signals with a greater weight (a missing weight ranks below every number; v1.4's rank()).
  -- o_top3: every signal with rank <= 3, in list order (v1.4's TOP3); o_lenient: 1 when one of them is expected
  -- (v1.4's hit); o_p: 1 when an expected signal is in a weight group that lies wholly above the cut, else
  -- tie_chance(m, s, e) of the group that straddles it, else 0; o_tie: the size of the group holding position 3
  -- (or the last position when fewer than 3 are listed), null for an empty list. A fired signal without a stored
  -- weight (details written before 95 v1.2 kept 5; a pre-v1.1 '#'): v1.1's rule, the first 3 fired, i.e. the
  -- list's own order with no tie.
  procedure attribute (p_run_tag varchar2, p_model varchar2, p_det varchar2, p_ts date, p_fired varchar2,
                       p_scenario varchar2, o_top3 out varchar2, o_lenient out pls_integer, o_p out number,
                       o_tie out pls_integer) is
    type t_sig is record (sig varchar2(30), w number, ex pls_integer);
    type t_sigs is table of t_sig;
    l       t_sigs := t_sigs();
    l_list  varchar2(4000);
    l_k     pls_integer;
    l_r     pls_integer;     -- rank of l(i): 1 + signals above it
    l_m     pls_integer;     -- signals sharing l(i)'s weight
    l_e     pls_integer;     -- expected signals among them
    l_strad number;
    l_full  boolean := false;
    l_nulls pls_integer := 0;
    function above (a number, b number) return boolean is
    begin
      return a is not null and (b is null or a > b);
    end;
    function same (a number, b number) return boolean is
    begin
      return (a is null and b is null) or a = b;
    end;
  begin
    o_top3 := null;
    o_lenient := 0;
    o_p := 0;
    o_tie := null;
    if p_det in ('STATIC', 'SEASONAL') then
      if p_fired is null then
        return;
      end if;
      select f.sig, d.weight, case when x.signal_code is not null then 1 else 0 end
        bulk collect into l
        from (select regexp_substr(p_fired, '[^,]+', 1, level) sig, level pos from dual
               connect by level <= regexp_count(p_fired, ',') + 1) f
        left join SCORE_EVAL_DETAIL d on d.run_tag = p_run_tag and d.model_name = p_model and d.ts = p_ts
                                     and d.signal_code = f.sig
        left join EXPECTED_SIGNALS x on x.scenario = p_scenario and x.signal_code = f.sig
       order by f.pos;
      for i in 1 .. l.count loop
        if l(i).w is null then
          l_nulls := l_nulls + 1;
        end if;
      end loop;
      if l_nulls > 0 then
        for i in 1 .. l.count loop
          l(i).w := l.count - i + 1;       -- the fired list's own order, no tie (v1.1's first 3)
        end loop;
      end if;
    else
      select d.signal_code, d.weight, case when x.signal_code is not null then 1 else 0 end
        bulk collect into l
        from SCORE_EVAL_DETAIL d
        left join EXPECTED_SIGNALS x on x.scenario = p_scenario and x.signal_code = d.signal_code
       where d.run_tag = p_run_tag and d.model_name = p_model and d.ts = p_ts
       order by d.rank;
    end if;
    if l.count = 0 then
      return;
    end if;
    l_k := least(c_top_k, l.count);
    for i in 1 .. l.count loop
      l_r := 1;
      l_m := 0;
      l_e := 0;
      for j in 1 .. l.count loop
        if above(l(j).w, l(i).w) then
          l_r := l_r + 1;
        elsif same(l(j).w, l(i).w) then
          l_m := l_m + 1;
          l_e := l_e + l(j).ex;
        end if;
      end loop;
      if l_r <= c_top_k then
        l_list := l_list || ',' || l(i).sig;
        if l(i).ex = 1 then
          o_lenient := 1;
        end if;
        if l_r - 1 + l_m <= c_top_k then
          if l_e > 0 then
            l_full := true;                -- the whole group is in the top 3
          end if;
        else
          l_strad := tie_chance(l_m, c_top_k - (l_r - 1), l_e);   -- the group that straddles the cut
        end if;
      end if;
      if l_r <= l_k and l_k <= l_r - 1 + l_m then
        o_tie := l_m;                      -- the group holding the last top-3 position
      end if;
    end loop;
    o_top3 := substr(ltrim(l_list, ','), 1, 1000);
    o_p := case when l_full then 1 else nvl(l_strad, 0) end;
  end attribute;

  procedure grade (p_run_tag in varchar2, p_from in date, p_to in date, p_sources in varchar2 default 'SCHEDULE',
                   p_refresh in varchar2 default 'Y', p_commit in varchar2 default 'Y') is
    l_src   varchar2(200) := ',' || upper(replace(p_sources, ' ')) || ',';
    l_hours number;
    l_ep    ALERT_EVAL%rowtype;
    l_top3  varchar2(1000);
    l_hit   char(1);
    l_len   pls_integer;
    l_p     number;
    l_tie   pls_integer;
    l_ref   varchar2(128);
    l_best  varchar2(128);
    l_b     number;
    l_c     number;
    n       number;
  begin
    if p_run_tag is null or not regexp_like(p_run_tag, '^[A-Z0-9_]{1,40}$') then
      raise_application_error(-20400, 'run tag must be 1-40 of A-Z, 0-9, _');
    end if;
    if p_from is null or p_to is null or p_from >= p_to then
      raise_application_error(-20400, 'the range must have p_from < p_to');
    end if;
    if nvl(p_refresh, '?') not in ('Y', 'N') or nvl(p_commit, '?') not in ('Y', 'N') then
      raise_application_error(-20400, 'p_refresh and p_commit must be Y or N');
    end if;
    if p_refresh = 'Y' and p_commit = 'N' then
      raise_application_error(-20400, 'p_refresh = Y commits; use p_refresh = N with p_commit = N');
    end if;
    select count(*) into n from SCORE_EVAL where run_tag = p_run_tag;
    if n = 0 then
      raise_application_error(-20404, 'no SCORE_EVAL rows under tag '||p_run_tag);
    end if;
    if p_refresh = 'Y' then
      begin
        PKG_ANOM_TRAIN.refresh_truth;
      exception
        when others then
          if sqlcode not in (-942, -2019, -41900, -1031) then
            raise;
          end if;   -- no CHAOS_RUN on the target: no incident exists beyond the copy
      end;
    end if;
    l_hours := (p_to - p_from) * 24;

    PKG_ANOM_SCORE.build_eval_alerts(p_run_tag);
    delete from GRADE_INCIDENT where run_tag = p_run_tag;
    delete from GRADE_RESULT where run_tag = p_run_tag;

    -- per model and incident: the first episode that starts in [start, end + 10 min]
    for m in (select model_name, max(detector) detector from SCORE_EVAL where run_tag = p_run_tag
               group by model_name order by model_name) loop
      for i in (select run_id, scenario, intensity, coalesce(start_ts, requested_ts) s,
                       coalesce(end_ts, planned_end_ts, start_ts, requested_ts) e
                  from INCIDENT_TRUTH
                 where gone_ts is null and instr(l_src, ',' || source || ',') > 0
                   and coalesce(start_ts, requested_ts) >= p_from and coalesce(start_ts, requested_ts) < p_to
                 order by run_id) loop
        begin
          select * into l_ep from ALERT_EVAL
           where run_tag = p_run_tag and model_name = m.model_name
             and start_ts >= i.s and start_ts <= i.e + 10 / 1440
           order by start_ts fetch first 1 row only;
          -- v1.5: one ranked list per incident (PLAN 7a, every detector alike): R1/R2 by the metric that fired
          -- (PLAN 7), the others by the opening minute's details. From it: v1.4's TOP3 and hit (the lenient rule),
          -- and the amended rule's ATTR_P with the tie size at the cut (PLAN.md v1.7)
          attribute(p_run_tag, m.model_name, m.detector, l_ep.start_ts, l_ep.fired, i.scenario,
                    l_top3, l_len, l_p, l_tie);
          if m.detector = 'IFOREST' then
            l_hit := null;   -- D5 has no attribution (PLAN.md section 7)
            l_len := null;
            l_p   := null;
            l_tie := null;
          else
            l_hit := case when l_len = 1 then 'Y' else 'N' end;
          end if;
          insert into GRADE_INCIDENT (run_tag, model_name, detector, run_id, scenario, intensity, inc_start, inc_end,
                                      caught, alert_start, ttd_min, top3, attr_hit, attr_lenient, attr_p, tie_size)
          values (p_run_tag, m.model_name, m.detector, i.run_id, i.scenario, i.intensity, i.s, i.e,
                  'Y', l_ep.start_ts, round((l_ep.start_ts - i.s) * 1440, 4), l_top3, l_hit, l_len, l_p, l_tie);
        exception
          when no_data_found then
            insert into GRADE_INCIDENT (run_tag, model_name, detector, run_id, scenario, intensity, inc_start, inc_end,
                                        caught, alert_start, ttd_min, top3, attr_hit, attr_lenient, attr_p, tie_size)
            values (p_run_tag, m.model_name, m.detector, i.run_id, i.scenario, i.intensity, i.s, i.e,
                    'N', null, null, null, null, null, null, null);
        end;
      end loop;

      -- the model's row: incidents, catches, time to detect, false alarms, attribution (v1.5: the mean ATTR_P is
      -- the primary figure; the lenient hits and rate and the median tie size stand beside it)
      insert into GRADE_RESULT (run_tag, model_name, detector, grade_from, grade_to, hours, n_incidents, n_caught,
                                ttd_median, ttd_p90, n_false_alarms, fa_per_24h, n_attr_scored, n_attr_hit,
                                attr_hit_rate, graded_ts, attr_p_mean, tie_size_median)
      select p_run_tag, m.model_name, m.detector, p_from, p_to, l_hours,
             (select count(*) from GRADE_INCIDENT g where g.run_tag = p_run_tag and g.model_name = m.model_name),
             (select count(*) from GRADE_INCIDENT g where g.run_tag = p_run_tag and g.model_name = m.model_name
                 and g.caught = 'Y'),
             (select percentile_cont(0.5) within group (order by g.ttd_min) from GRADE_INCIDENT g
               where g.run_tag = p_run_tag and g.model_name = m.model_name and g.caught = 'Y'),
             (select percentile_cont(0.9) within group (order by g.ttd_min) from GRADE_INCIDENT g
               where g.run_tag = p_run_tag and g.model_name = m.model_name and g.caught = 'Y'),
             fa.n, round(fa.n * 24 / l_hours, 6),
             case when m.detector != 'IFOREST' then
               (select count(*) from GRADE_INCIDENT g where g.run_tag = p_run_tag and g.model_name = m.model_name
                   and g.caught = 'Y') end,
             case when m.detector != 'IFOREST' then
               (select count(*) from GRADE_INCIDENT g where g.run_tag = p_run_tag and g.model_name = m.model_name
                   and g.attr_hit = 'Y') end,
             null, sys_extract_utc(systimestamp),
             case when m.detector != 'IFOREST' then
               (select round(avg(g.attr_p), 6) from GRADE_INCIDENT g where g.run_tag = p_run_tag
                   and g.model_name = m.model_name and g.caught = 'Y') end,
             case when m.detector != 'IFOREST' then
               (select median(g.tie_size) from GRADE_INCIDENT g where g.run_tag = p_run_tag
                   and g.model_name = m.model_name and g.caught = 'Y') end
        from (select count(*) n from ALERT_EVAL a
               where a.run_tag = p_run_tag and a.model_name = m.model_name
                 and a.start_ts >= p_from and a.start_ts < p_to
                 and not exists (select 1 from INCIDENT_TRUTH t
                                  where t.gone_ts is null
                                    and a.start_ts >= coalesce(t.start_ts, t.requested_ts)
                                    and a.start_ts <= coalesce(t.end_ts, t.planned_end_ts, t.start_ts, t.requested_ts)
                                                      + 30 / 1440)) fa;
      update GRADE_RESULT set attr_hit_rate = case when n_attr_scored > 0 then round(n_attr_hit / n_attr_scored, 6) end
       where run_tag = p_run_tag and model_name = m.model_name;
    end loop;

    -- McNemar: the MSET reference against every rival over the paired incidents; flag the best rival
    select max(model_name) keep (dense_rank first order by n_caught desc, n_false_alarms, ttd_median nulls last,
                                 model_name)
      into l_ref from GRADE_RESULT where run_tag = p_run_tag and detector = 'MSET';
    select max(model_name) keep (dense_rank first order by n_caught desc, n_false_alarms, ttd_median nulls last,
                                 model_name)
      into l_best from GRADE_RESULT where run_tag = p_run_tag and detector != 'MSET';
    if l_ref is not null then
      for r in (select model_name from GRADE_RESULT where run_tag = p_run_tag and detector != 'MSET') loop
        select count(case when a.caught = 'Y' and b.caught = 'N' then 1 end),
               count(case when a.caught = 'N' and b.caught = 'Y' then 1 end)
          into l_b, l_c
          from GRADE_INCIDENT a join GRADE_INCIDENT b on b.run_tag = a.run_tag and b.run_id = a.run_id
         where a.run_tag = p_run_tag and a.model_name = l_ref and b.model_name = r.model_name;
        update GRADE_RESULT
           set mcnemar_ref = l_ref, mcnemar_b = l_b, mcnemar_c = l_c, mcnemar_p = mcnemar_exact_p(l_b, l_c),
               best_rival = case when model_name = l_best then 'Y' else 'N' end
         where run_tag = p_run_tag and model_name = r.model_name;
      end loop;
    end if;
    if p_commit = 'Y' then
      commit;
    end if;
  end grade;
end PKG_ANOM_GRADE;
/
show errors package body PKG_ANOM_GRADE

declare
  n number;
begin
  select count(*) into n from user_objects where object_name = 'PKG_ANOM_GRADE' and status != 'VALID';
  if n > 0 then
    raise_application_error(-20900, '96: PKG_ANOM_GRADE did not compile');
  end if;
end;
/

-- ------------------------------------------------------------------ v1.3: ANOM_INCIDENT_JOB removed
-- It started each hidden incident through ANOM_CHAOS_LINK (a target logon in the incident's first minute). The target
-- starts them now (93 v1.2 dispatchers); the statuses come back with 94's truth loop. Dropped only when it is idle.
declare
  n number;
begin
  select count(*) into n from user_scheduler_jobs where job_name = 'ANOM_INCIDENT_JOB';
  if n > 0 then
    select count(*) into n from user_scheduler_running_jobs where job_name = 'ANOM_INCIDENT_JOB';
    if n > 0 then
      raise_application_error(-20900, '96: ANOM_INCIDENT_JOB is running; run 96 again in a minute');
    end if;
    dbms_scheduler.drop_job('ANOM_INCIDENT_JOB');
    dbms_output.put_line('  ANOM_INCIDENT_JOB dropped (v1.3: the target starts the hidden incidents)');
  else
    dbms_output.put_line('  ANOM_INCIDENT_JOB absent (v1.3)');
  end if;
end;
/

select '  INCIDENT_PLAN rows: '||count(*) as plan from INCIDENT_PLAN;
