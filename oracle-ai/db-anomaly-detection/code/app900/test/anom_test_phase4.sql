-- v1.4 - app 900 phase 4 tests (builder M): detectors, training rows, scoring, alert episodes, grading, the incident
--        plan, in ora26ai / ORCLPDB1. Run as ANOMOPS after 94-96 (no password is substituted here):
--          tools/anom_obs_run.sh run anom_test_phase4.sql <log>
--        One PASS or FAIL line per check, then the totals; exits non-zero when anything failed.
--        Real data: trains one model of each in-database detector (variant UTP4) on the last <= 24 h of
--        FEATURE_MINUTE, scores it under run tag UT_P4_SCORE, and drops both at the end. Fabricated data (the alert
--        rules, grading and the plan) live only inside transactions that are rolled back. Every number of PLAN.md
--        section 7 is checked against hand-computed values; test/test_models_static.py recomputes the same values
--        from the c_* constants of section E with an independent Python implementation.
--        Never activates a model, never enables a job, never starts an incident.
--        v1.4: (phase 5c, 95 v1.2) P28: a flagged minute's details are numbered 1..k without a hole, k <= 40 (was 5),
--              and past the fifth only signals tied at the third-highest weight (MSET, SVM, EM, PCA); STATIC and
--              SEASONAL keep every breach (P28b unchanged). The tie rule itself is tested in anom_test_phase5c.sql.
--              (94 v1.3) P10 and P12's independent filters also leave out the TRAIN_EXCLUDE windows (both ends
--              included), as the package now does; P12 failed 402 / 429 on the first run after the deploy, the 27
--              rows of the exclusion windows. 02-Oct-2026.
--        v1.3: (phase 4b, 94 v1.2 / 96 v1.3) P04-P05: ANOM_INCIDENT_JOB is gone (the target starts the hidden
--              incidents); the truth job and the score job keep their UTC start; P69: run_due is now a one-off mirror
--              of the target's CHAOS_PLAN (one link session) and starts nothing. P31 counts ties at the top weight
--              (PLAN 7a's tie rule): on 01-Oct 22:48Z RT_TXN tied TXN at the top in 4 of 13 planted minutes and
--              Oracle's @rank put TXN first, which the rank-1 check counted as a miss. 01-Oct-2026.
--        v1.2: (phase 4 review, Codex findings 1, 2, 4) P55 = the plan with every short run capped at its scenario's
--              maximum (96 v1.2); P58 checks each run against that maximum (contract section 5); when the real
--              INCIDENT_PLAN exists, P56-P60 check the real plan instead of being skipped (only the literals P54-P55
--              are skipped); the independent training-row counts (P10, P12) use the UTC clock, as 94 v1.1 does.
--              The header now carries the file's version (it said v1.0 under the v1.1 changelog). 01-Oct-2026.
--        v1.1: (integrator, contract v1.2 / PLAN 7a) STATIC episodes need 3 consecutive breaches of the SAME signal:
--              the fabricated STATIC minutes carry breach lists (c_brk_stat), minutes 1200-1203 alternate two
--              signals and open nothing (the v1.0 rule would have opened a false alarm at 1202), STATIC is
--              attributed by the signal that fired (blocking_chain's episode fired on PIO: attribution 2/3, was 3/3),
--              P28b checks the stored breach lists, P40b/P44b the fired signals, P50b-P50d the run bookkeeping and
--              P52b a STATIC run carried across two live scoring runs. P13's fabricated run sits on the median
--              training row (the window's midpoint fell inside C's TEST-run exclusions: nothing left to remove). 01-Oct-2026.
--        v1.0: first version, 01-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 200 pagesize 100
whenever sqlerror exit failure rollback

variable n_pass number
variable n_fail number
variable t_start varchar2(40)
exec :n_pass := 0; :n_fail := 0; :t_start := to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD HH24:MI:SS.FF6');

begin
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, 'anom_test_phase4: run as ANOMOPS');
  end if;
end;
/

-- leftovers of an interrupted earlier run (only this test's own variant and tag)
begin
  for r in (select model_name from MODEL_REGISTRY where variant like 'UTP4%' and status != 'ACTIVE') loop
    PKG_ANOM_TRAIN.drop_model(r.model_name);
  end loop;
  delete from SCORE_EVAL_DETAIL where run_tag = 'UT_P4_SCORE';
  delete from SCORE_EVAL where run_tag = 'UT_P4_SCORE';
  delete from ALERT_EVAL where run_tag = 'UT_P4_SCORE';
  commit;
end;
/

prompt == A. objects, seeds, jobs, links
declare
  n   number;
  n2  number;
  n3  number;
  n4  number;
  l_t varchar2(4000);
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
begin
  select count(*) into n from user_tables where table_name in ('MODEL_REGISTRY', 'THRESHOLD_DEF', 'SEASONAL_BASE',
    'MODEL_SIGNAL_STAT', 'INCIDENT_TRUTH', 'EXPECTED_SIGNALS', 'ANOM_STATE', 'SCORE_MINUTE', 'SCORE_DETAIL', 'SCORE_EVAL',
    'SCORE_EVAL_DETAIL', 'ALERT', 'ALERT_STATE', 'ALERT_EVAL', 'INCIDENT_PLAN', 'GRADE_RESULT', 'GRADE_INCIDENT');
  ok('P01', n = 17, 'phase 4 tables present: '||n||' of 17');
  select count(*), count(case when status = 'VALID' then 1 end) into n, n2 from user_objects
   where object_name in ('PKG_ANOM_TRAIN', 'PKG_ANOM_SCORE', 'PKG_ANOM_GRADE') and object_type in ('PACKAGE', 'PACKAGE BODY');
  ok('P02', n = 6 and n2 = 6, 'PKG_ANOM_TRAIN/SCORE/GRADE spec and body valid: '||n2||' of '||n);
  select count(*), count(distinct scenario), count(case when direction = 'DOWN' then 1 end) into n, n2, n3
    from EXPECTED_SIGNALS;
  select listagg(scenario||'.'||signal_code, ',') within group (order by scenario, signal_code) into l_t
    from EXPECTED_SIGNALS where direction = 'DOWN';
  ok('P03', n = 46 and n2 = 12 and n3 = 3 and l_t = 'app_error_burst.COMMITS,app_error_burst.TXN,blocking_chain.COMMITS',
     'EXPECTED_SIGNALS: '||n||' rows (46), '||n2||' scenarios (12), DOWN '||l_t);
  -- the two jobs exist with an explicit UTC start; the truth job runs; the score job may run only with an ACTIVE
  -- model. v1.3: ANOM_INCIDENT_JOB is gone (96 v1.3: the target's dispatchers start the hidden incidents)
  select count(*) into n from user_scheduler_jobs
   where job_name in ('ANOM_TRUTH_JOB', 'ANOM_SCORE_JOB')
     and extract(timezone_region from start_date) = 'UTC';
  select count(*) into n2 from user_scheduler_jobs where job_name = 'ANOM_TRUTH_JOB' and enabled = 'TRUE';
  ok('P04', n = 2 and n2 = 1, 'jobs with a UTC start date: '||n||' of 2; ANOM_TRUTH_JOB enabled: '||n2);
  select count(*) into n from user_scheduler_jobs where job_name = 'ANOM_SCORE_JOB' and enabled = 'TRUE';
  select count(*) into n2 from MODEL_REGISTRY where status = 'ACTIVE';
  select count(*) into n3 from user_scheduler_jobs where job_name = 'ANOM_INCIDENT_JOB';
  ok('P05', (n = 0 or n2 > 0) and n3 = 0,
     'ANOM_SCORE_JOB enabled '||n||' with '||n2||' ACTIVE model(s); ANOM_INCIDENT_JOB present: '||n3);
  select count(*) into n from user_db_links where db_link like 'ANOM_CHAOS_LINK%' and username = 'CHAOS_CTL';
  ok('P06', n = 1, 'ANOM_CHAOS_LINK connects as CHAOS_CTL: '||n);
  select count(*) into n from MODEL_REGISTRY where status = 'ACTIVE' and detector = 'IFOREST';
  select count(*) into n2 from (select detector from MODEL_REGISTRY where status = 'ACTIVE' group by detector
                                having count(*) > 1);
  ok('P07', n = 0 and n2 = 0, 'no ACTIVE IFOREST ('||n||'); no detector with two ACTIVE models ('||n2||')');
end;
/

prompt == B. INCIDENT_TRUTH refresh (tolerates a target without CHAOS_RUN)
declare
  l_code number := 0;
  l_msg  varchar2(4000);
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
begin
  begin
    PKG_ANOM_TRAIN.refresh_truth_job;
  exception
    when others then l_code := sqlcode; l_msg := sqlerrm;
  end;
  ok('P08', l_code = 0, 'refresh_truth_job returns normally (due or not, CHAOS_RUN present or not)'
                        ||case when l_code != 0 then ': '||l_msg end);
  l_code := 0;
  begin
    PKG_ANOM_TRAIN.refresh_truth;
  exception
    when others then l_code := sqlcode; l_msg := sqlerrm;
  end;
  ok('P09', l_code in (0, -942, -2019, -41900, -1031),
     'refresh_truth: '||case when l_code = 0 then 'copied' else 'target not readable yet ('||l_msg||')' end);
end;
/

prompt == C. training rows: the chaos-window exclusion and null rows (rolled back)
variable w_from varchar2(20)
variable w_to varchar2(20)
begin
  -- the window: the last <= 24 h of the soak (it began at 11:35 UTC on 01-Oct)
  :w_to := to_char(trunc(cast(sys_extract_utc(systimestamp) as date), 'MI') - 2 / 1440, 'YYYY-MM-DD HH24:MI:SS');
  :w_from := to_char(greatest(to_date('2026-10-01 11:35:00', 'YYYY-MM-DD HH24:MI:SS'),
                              to_date(:w_to, 'YYYY-MM-DD HH24:MI:SS') - 1), 'YYYY-MM-DD HH24:MI:SS');
  dbms_output.put_line('   window '||:w_from||' to '||:w_to||' UTC');
end;
/
declare
  l_from  date := to_date(:w_from, 'YYYY-MM-DD HH24:MI:SS');
  l_to    date := to_date(:w_to, 'YYYY-MM-DD HH24:MI:SS');
  l_used  varchar2(4000);
  l_drop  varchar2(32767);
  l_q     varchar2(32767);
  l_nn    varchar2(8000);
  n       number;
  n2      number;
  n3      number;
  n4      number;
  l_bad   varchar2(4000);
  l_s     date;
  l_e     date;
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
  -- the training-row filter written out independently of the package
  function nonnull_pred (p_list varchar2) return varchar2 is
    r varchar2(8000);
  begin
    for i in 1 .. regexp_count(p_list, ',') + 1 loop
      r := r || ' and f.' || regexp_substr(p_list, '[^,]+', 1, i) || ' is not null';
    end loop;
    return r;
  end;
begin
  PKG_ANOM_TRAIN.window_signals(l_from, l_to, l_used, l_drop);
  -- every dropped signal is really constant (or empty) over the window minus the chaos windows
  l_bad := null;
  for d in (select j.sig, j.reason from json_table(l_drop, '$[*]' columns (sig varchar2(20) path '$.signal',
                                                                          reason varchar2(60) path '$.reason')) j) loop
    execute immediate 'select count(distinct f.' || d.sig || ') from FEATURE_MINUTE f where f.ts >= :a and f.ts < :b '
      || 'and not exists (select 1 from INCIDENT_TRUTH t where f.ts >= coalesce(t.start_ts, t.requested_ts) - 5/1440 '
      || 'and f.ts <= coalesce(t.end_ts, case when t.status = ''RUNNING'' or t.status is null then '
      || 'greatest(coalesce(t.planned_end_ts, cast(sys_extract_utc(systimestamp) as date)), cast(sys_extract_utc(systimestamp) as date)) '
      || 'else coalesce(t.planned_end_ts, cast(sys_extract_utc(systimestamp) as date)) end) + 30/1440)'
      || ' and not exists (select 1 from TRAIN_EXCLUDE x where f.ts between x.from_ts and x.to_ts)'
      into n using l_from, l_to;
    if n > 1 and d.reason not like 'constant once%' then
      l_bad := l_bad || d.sig || '(' || n || ') ';
    end if;
  end loop;
  ok('P10', l_used is not null and l_bad is null,
     'dropped signals are constant over the window: '||nvl(l_bad, 'all')||'; dropped '||l_drop);
  -- every used signal varies over the training rows
  l_q := PKG_ANOM_TRAIN.training_query(l_from, l_to, l_used);
  l_bad := null;
  for i in 1 .. regexp_count(l_used, ',') + 1 loop
    execute immediate 'select count(distinct ' || regexp_substr(l_used, '[^,]+', 1, i) || ') from (' || l_q || ')' into n;
    if n < 2 then
      l_bad := l_bad || regexp_substr(l_used, '[^,]+', 1, i) || ' ';
    end if;
  end loop;
  ok('P11', l_bad is null, 'every used signal varies over the training rows'||case when l_bad is not null then ': '||l_bad end);

  -- the package's rows = the complete rows of the window minus the chaos windows and (v1.4) the exclusion windows,
  -- counted independently
  l_nn := nonnull_pred(l_used);
  execute immediate 'select count(*) from (' || l_q || ')' into n;
  execute immediate 'select count(*) from FEATURE_MINUTE f where f.ts >= :a and f.ts < :b' || l_nn
    || ' and not exists (select 1 from INCIDENT_TRUTH t where f.ts >= coalesce(t.start_ts, t.requested_ts) - 5/1440'
    || ' and f.ts <= case when t.end_ts is not null then t.end_ts'
    || ' when t.status = ''RUNNING'' or t.status is null then greatest(nvl(t.planned_end_ts, '
    || 'cast(sys_extract_utc(systimestamp) as date)), cast(sys_extract_utc(systimestamp) as date))'
    || ' else nvl(t.planned_end_ts, cast(sys_extract_utc(systimestamp) as date)) end + 30/1440)'
    || ' and not exists (select 1 from TRAIN_EXCLUDE x where f.ts between x.from_ts and x.to_ts)'
    into n2 using l_from, l_to;
  ok('P12', n = n2 and n >= PKG_ANOM_TRAIN.c_min_rows, 'training rows '||n||' = independent count '||n2
                                                      ||' (chaos and exclusion windows left out)');
  dbms_output.put_line('   training rows in the window: '||n);

  -- a fabricated finished run at the median training row removes exactly its [start - 5, end + 30] minutes; the
  -- expected counts are taken first (the query text reads INCIDENT_TRUTH when it runs). v1.1: anchored on the median
  -- training row, not the window's midpoint, which can sit inside real chaos windows (no row left to remove)
  execute immediate 'select ts from (' || l_q || ') order by ts offset ' || floor(n / 2) || ' rows fetch first 1 row only'
    into l_s;
  l_s := trunc(l_s, 'MI');
  l_e := l_s + 10 / 1440;
  execute immediate 'select count(*) from (' || l_q || ') q where q.ts between :s and :e'
    into n3 using l_s - 5 / 1440, l_e + 30 / 1440;
  execute immediate 'select count(*) from (' || l_q || ') q where q.ts >= :s' into n4 using l_s - 5 / 1440;
  insert into INCIDENT_TRUTH (run_id, scenario, intensity, source, requested_ts, start_ts, planned_end_ts, end_ts,
                              status, restored, refreshed_ts)
  values (-900001, 'cpu_hog', 'LOW', 'TEST', l_s, l_s, l_e, l_e, 'DONE', 'Y', sys_extract_utc(systimestamp));
  execute immediate 'select count(*) from (' || PKG_ANOM_TRAIN.training_query(l_from, l_to, l_used) || ')' into n2;
  ok('P13', n3 > 0 and n2 = n - n3, 'a finished chaos run removes its [start - 5 min, end + 30 min]: '||n||' -> '||n2
                                    ||' (expected -'||n3||')');
  -- a RUNNING run (no end yet) removes everything from start - 5 min up to now + 30 min
  update INCIDENT_TRUTH set status = 'RUNNING', end_ts = null, planned_end_ts = l_s + 2 / 1440 where run_id = -900001;
  execute immediate 'select count(*) from (' || PKG_ANOM_TRAIN.training_query(l_from, l_to, l_used) || ')' into n2;
  ok('P14', n2 = n - n4, 'a RUNNING chaos run removes every minute from its start - 5 min: '||n||' -> '||n2
                         ||' (expected -'||n4||')');
  -- a run of any source counts (here UI)
  update INCIDENT_TRUTH set source = 'UI', status = 'DONE', end_ts = l_e, planned_end_ts = l_e where run_id = -900001;
  execute immediate 'select count(*) from (' || PKG_ANOM_TRAIN.training_query(l_from, l_to, l_used) || ')' into n2;
  ok('P15', n2 = n - n3, 'a run started from the UI is excluded too: '||n||' -> '||n2||' (expected -'||n3||')');
  rollback;
  -- scoring rows are not filtered by chaos windows
  execute immediate 'select count(*) from (' || PKG_ANOM_TRAIN.rows_query(l_from, l_to, l_used) || ')' into n2;
  ok('P16', n2 >= n, 'rows_query (scoring) keeps chaos minutes: '||n2||' >= '||n);
end;
/

prompt == D. one model per in-database detector on the real window (variant UTP4), scored under UT_P4_SCORE
declare
  l_from  date := to_date(:w_from, 'YYYY-MM-DD HH24:MI:SS');
  l_to    date := to_date(:w_to, 'YYYY-MM-DD HH24:MI:SS');
  l_used  varchar2(4000);
  l_drop  varchar2(32767);
  l_name  varchar2(128);
  l_n     number;
  n       number;
  n2      number;
  n3      number;
  n4      number;
  l_code  number;
  l_js    json;
  type t_names is table of varchar2(128) index by varchar2(10);
  l_m     t_names;
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
begin
  PKG_ANOM_TRAIN.window_signals(l_from, l_to, l_used, l_drop);
  execute immediate 'select count(*) from (' || PKG_ANOM_TRAIN.training_query(l_from, l_to, l_used) || ')' into l_n;
  for d in (select column_value det from table(sys.odcivarchar2list('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL'))) loop
    l_m(d.det) := PKG_ANOM_TRAIN.train(d.det, l_from, l_to, 'UTP4');
  end loop;
  -- P17: the registry rows
  select count(*) into n from MODEL_REGISTRY
   where variant = 'UTP4' and status = 'CANDIDATE' and n_rows = l_n and train_seconds > 0 and size_bytes > 0
     and train_from = l_from and train_to = l_to
     and json_value(signals_json, '$.rows_trained' returning number) = l_n
     and model_name like 'ANOM\_' || detector || '\_UTP4\_' || to_char(sys_extract_utc(systimestamp), 'YYYYMMDD') || '%'
         escape '\';
  ok('P17', n = 6, 'six CANDIDATE models with the training row count ('||l_n||'), time, size and contract name: '||n);
  -- P18: each lists exactly the window's used signals
  select count(*) into n from MODEL_REGISTRY r
   where r.variant = 'UTP4'
     and (select listagg(j.sig, ',') within group (order by j.o) from json_table(r.signals_json, '$.used[*]'
          columns (o for ordinality, sig varchar2(20) path '$')) j) = l_used;
  ok('P18', n = 6, 'every model uses the window''s signals ('||(regexp_count(l_used, ',') + 1)||'): '||n||' of 6');
  -- P19: the four mining models exist; STATIC and SEASONAL definitions are complete
  select count(*) into n from user_mining_models where model_name in (l_m('MSET'), l_m('SVM'), l_m('EM'), l_m('PCA'));
  select count(*), count(case when lo is not null then 1 end),
         count(case when lo is not null and signal_code not in ('TXN', 'CALLS', 'EXECS', 'COMMITS', 'APP_TPS') then 1 end)
    into n2, n3, l_code from THRESHOLD_DEF where model_name = l_m('STATIC');
  ok('P19', n = 4 and n2 = regexp_count(l_used, ',') + 1 and l_code = 0,
     'mining models '||n||' of 4; STATIC thresholds '||n2||' (one per signal), lo on '||n3||' volume signals only');
  select count(*), min(n), min(mad) into n, n2, n3 from SEASONAL_BASE where model_name = l_m('SEASONAL');
  ok('P20', n > 0 and n2 >= 30 and n3 > 0, 'SEASONAL baselines '||n||', fewest rows per hour '||n2||' (>= 30), MAD > 0');
  select count(*) into n from MODEL_REGISTRY
   where model_name = l_m('MSET') and json_value(settings_json, '$.ALGO_NAME') = 'ALGO_MSET_SPRT'
     and json_value(settings_json, '$.MSET_ALERT_COUNT') = '3' and json_value(settings_json, '$.MSET_ALERT_WINDOW') = '5'
     and json_value(settings_json, '$.MSET_PROJECTION_THRESHOLD') is null;
  ok('P21', n = 1, 'MSET settings as stored: ALGO_MSET_SPRT, alert 3 of 5, no random-projection setting');

  -- P22: knob overrides are applied; unknown keys and detectors are refused
  select json('{"SVMS_OUTLIER_RATE":0.05}') into l_js from dual;
  l_name := PKG_ANOM_TRAIN.train('SVM', l_from, l_to, 'UTP4B', l_js);
  select count(*) into n from MODEL_REGISTRY
   where model_name = l_name and json_value(settings_json, '$.SVMS_OUTLIER_RATE') = '.05';
  ok('P22', n = 1, 'SVMS_OUTLIER_RATE 0.05 reaches the model ('||l_name||')');
  PKG_ANOM_TRAIN.drop_model(l_name);
  l_code := 0;
  begin
    select json('{"MSET_ALERT_COUNT":2}') into l_js from dual;
    l_name := PKG_ANOM_TRAIN.train('SVM', l_from, l_to, 'UTP4C', l_js);
  exception when others then l_code := sqlcode;
  end;
  ok('P23', l_code = -20202, 'a key of another detector is refused (ORA'||l_code||')');
  l_code := 0;
  begin
    l_name := PKG_ANOM_TRAIN.train('IFOREST', l_from, l_to, 'UTP4C');
  exception when others then l_code := sqlcode;
  end;
  ok('P24', l_code = -20201, 'IFOREST is not trained in the database (ORA'||l_code||')');
  l_code := 0;
  begin
    l_name := PKG_ANOM_TRAIN.train('STATIC', l_from, l_from + 5 / 1440, 'UTP4C');
  exception when others then l_code := sqlcode;
  end;
  ok('P25', l_code = -20208, 'a window with fewer than '||PKG_ANOM_TRAIN.c_min_rows||' rows is refused (ORA'||l_code||')');

  -- score every model over the window (grading path); SCORE_MINUTE is never touched
  for d in (select column_value det from table(sys.odcivarchar2list('MSET', 'SVM', 'EM', 'PCA', 'STATIC', 'SEASONAL'))) loop
    PKG_ANOM_SCORE.score_range(l_m(d.det), l_from, l_to, 'UT_P4_SCORE');
  end loop;
  select count(*) into n from FEATURE_MINUTE where ts >= l_from and ts < l_to;
  select count(*) into n2 from (select model_name from SCORE_EVAL where run_tag = 'UT_P4_SCORE'
                                group by model_name having count(*) = n);
  select count(*) into n3 from SCORE_MINUTE where model_name like 'ANOM\_%\_UTP4\_%' escape '\';
  ok('P26', n2 = 6 and n3 = 0, 'score_range: one SCORE_EVAL row per minute ('||n||') for '||n2||' of 6 models; '
                               ||'SCORE_MINUTE rows of these models: '||n3);
  select count(*) into n from SCORE_EVAL e join MODEL_REGISTRY r on r.model_name = e.model_name
   where e.run_tag = 'UT_P4_SCORE' and r.detector in ('PCA', 'STATIC', 'SEASONAL') and e.flag is not null
     and ((e.flag = 1 and not e.score > 1) or (e.flag = 0 and e.score > 1));
  ok('P27', n = 0, 'PCA, STATIC, SEASONAL: flag = 1 exactly when score > 1 (violations '||n||')');
  -- details: only for flagged minutes, ranks 1..k without holes, k <= 40 (v1.4: 95 v1.2's tie-safe details)
  select count(*) into n from SCORE_EVAL_DETAIL d
   where d.run_tag = 'UT_P4_SCORE'
     and not exists (select 1 from SCORE_EVAL e where e.run_tag = d.run_tag and e.model_name = d.model_name
                        and e.ts = d.ts and e.flag = 1);
  select count(*) into n2 from (select model_name, ts, count(*) c, max(rank) mx from SCORE_EVAL_DETAIL
                                 where run_tag = 'UT_P4_SCORE' group by model_name, ts having count(*) != max(rank)
                                    or count(*) > 40);
  -- v1.4: past the fifth, MSET, SVM, EM and PCA keep only signals tied at the third-highest weight (rank() <= 3)
  select count(*) into n4
    from (select d.rank, rank() over (partition by d.model_name, d.ts order by d.weight desc nulls last) wr
            from SCORE_EVAL_DETAIL d join MODEL_REGISTRY r on r.model_name = d.model_name
           where d.run_tag = 'UT_P4_SCORE' and r.detector in ('MSET', 'SVM', 'EM', 'PCA'))
   where rank > 5 and wr > 3;
  select count(*) into n3 from SCORE_EVAL e join MODEL_REGISTRY r on r.model_name = e.model_name
   where e.run_tag = 'UT_P4_SCORE' and e.flag = 1 and r.detector in ('MSET', 'PCA', 'STATIC', 'SEASONAL')
     and not exists (select 1 from SCORE_EVAL_DETAIL d where d.run_tag = e.run_tag and d.model_name = e.model_name
                        and d.ts = e.ts);
  ok('P28', n = 0 and n2 = 0 and n3 = 0 and n4 = 0,
     'details only on flagged minutes ('||n||' strays), ranks 1..k <= 40 ('||n2||' bad), past the fifth only ties at '
     ||'the third weight ('||n4||' others), every flagged MSET/PCA/STATIC/SEASONAL minute named ('||n3||' unnamed)');
  -- v1.1: a flagged STATIC/SEASONAL minute stores its full breach list, strongest first (the detail's rank 1 leads,
  -- the top 5 details are its first entries); other detectors and unflagged minutes store none
  select count(*) into n from SCORE_EVAL e join MODEL_REGISTRY r on r.model_name = e.model_name
   where e.run_tag = 'UT_P4_SCORE'
     and ((r.detector in ('STATIC', 'SEASONAL') and e.flag = 1
           and (e.breached is null
                or e.breached not like (select listagg(d.signal_code, ',') within group (order by d.rank)
                                          from SCORE_EVAL_DETAIL d where d.run_tag = e.run_tag
                                           and d.model_name = e.model_name and d.ts = e.ts) || '%'))
          or ((r.detector not in ('STATIC', 'SEASONAL') or nvl(e.flag, 0) = 0) and e.breached is not null));
  select count(*) into n2 from SCORE_EVAL e join MODEL_REGISTRY r on r.model_name = e.model_name
   where e.run_tag = 'UT_P4_SCORE' and r.detector in ('STATIC', 'SEASONAL') and e.flag = 1;
  ok('P28b', n = 0, 'BREACHED: every flagged STATIC/SEASONAL minute ('||n2||') lists its breaches led by its stored '
                    ||'details; none elsewhere ('||n||' violations)');
  commit;
  for r in (select detector, n_rows, train_seconds, size_bytes, score_ms_per_min from MODEL_REGISTRY
             where variant = 'UTP4' order by detector) loop
    dbms_output.put_line('   '||rpad(r.detector, 9)||' rows '||r.n_rows||', train '||r.train_seconds||' s, size '
                         ||r.size_bytes||' B, scoring '||r.score_ms_per_min||' ms/minute (batch)');
  end loop;
end;
/

prompt == D2. scoring core: the MSET context, planted anomalies, PCA residual feasibility, STATIC and SEASONAL math
declare
  l_from  date := to_date(:w_from, 'YYYY-MM-DD HH24:MI:SS');
  l_to    date := to_date(:w_to, 'YYYY-MM-DD HH24:MI:SS');
  l_m     varchar2(128);
  l_sig   varchar2(4000);
  l_cols  varchar2(8000);
  l_q     varchar2(32767);
  l_cut   date;
  l_last  date;
  l_first date;
  l_k     number;
  l_thr   number;
  l_med   number;
  l_sd    number;
  l_err0  number;
  l_err1  number;
  l_flag  number;
  l_score number;
  l_r1    varchar2(30);
  l_w1    number;
  l_hi    number;
  l_lo    number;
  l_kz    number;
  l_hr    number;
  n       number;
  n2      number;
  n3      number;
  l_diff  number := 0;
  l_cnt   number := 0;
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
  function model (p_det varchar2) return varchar2 is
    r varchar2(128);
  begin
    select model_name into r from MODEL_REGISTRY where variant = 'UTP4' and detector = p_det;
    return r;
  end;
  -- "ts, A, B * 5 B, ..." : the model's columns with the given replacements (one expression per signal)
  function cols (p_sig varchar2, p_a varchar2, p_expr_a varchar2, p_b varchar2 default null, p_expr_b varchar2 default null)
    return varchar2 is
    r varchar2(8000) := 'ts';
    s varchar2(30);
  begin
    for i in 1 .. regexp_count(p_sig, ',') + 1 loop
      s := regexp_substr(p_sig, '[^,]+', 1, i);
      r := r || ', ' || case when s = p_a then p_expr_a || ' ' || s when s = p_b then p_expr_b || ' ' || s else s end;
    end loop;
    return r;
  end;
begin
  -- P29 MSET over (order by ts): minute by minute with 120 minutes of context = one pass over the range
  l_m := model('MSET');
  l_sig := PKG_ANOM_TRAIN.model_signals(l_m);
  for r in (select ts, flag, score from SCORE_EVAL where run_tag = 'UT_P4_SCORE' and model_name = l_m
               and flag is not null order by ts desc fetch first 20 rows only) loop
    PKG_ANOM_SCORE.score_query(l_m, PKG_ANOM_TRAIN.rows_query(r.ts - 120 / 1440, r.ts + 1 / 86400, l_sig), r.ts);
    select flag, score into l_flag, l_score from SCORE_TMP where ts = r.ts;
    l_cnt := l_cnt + 1;
    if l_flag != r.flag or abs(l_score - r.score) > 1e-9 then
      l_diff := l_diff + 1;
    end if;
  end loop;
  ok('P29', l_cnt = 20 and l_diff = 0, 'MSET minute-by-minute with 120 min of context equals the one-pass score: '
                                       ||l_diff||' of '||l_cnt||' differ');

  -- P30-P31 MSET finds a planted anomaly and names it: on the newest 40 training minutes (no chaos minute among
  -- them), the last 15 get RT_TXN x 5 and APP_P95_MS x 20
  execute immediate 'select max(ts), min(ts) from (select ts from (' || PKG_ANOM_TRAIN.training_query(l_from, l_to, l_sig)
                    || ') order by ts desc fetch first 40 rows only)' into l_last, l_first;
  execute immediate 'select min(ts) from (select ts from (' || PKG_ANOM_TRAIN.training_query(l_from, l_to, l_sig)
                    || ') order by ts desc fetch first 15 rows only)' into l_cut;
  l_cut := l_cut - 1 / 86400;
  l_q := 'select ' || cols(l_sig, 'RT_TXN', 'case when ts > ' || PKG_ANOM_TRAIN.date_lit(l_cut) || ' then RT_TXN * 5 else RT_TXN end',
                                  'APP_P95_MS', 'case when ts > ' || PKG_ANOM_TRAIN.date_lit(l_cut)
                                  || ' then APP_P95_MS * 20 else APP_P95_MS end')
      || ' from (' || PKG_ANOM_TRAIN.training_query(l_from, l_to, l_sig) || ')';
  PKG_ANOM_SCORE.score_query(l_m, l_q, l_first);
  select count(case when ts > l_cut and flag = 1 then 1 end), count(case when ts > l_cut then 1 end),
         count(case when ts <= l_cut and flag = 1 then 1 end)
    into n, n2, n3 from SCORE_TMP;
  ok('P30', n2 > 0 and n >= n2 - 3 and n3 <= 2, 'MSET flags '||n||' of '||n2||' planted minutes (all but its first '
                                                ||'alert-count minutes) and '||n3||' of the normal ones before');
  -- v1.3: "first" counts ties (PLAN 7a: MSET weights come in fifths and Oracle's @rank orders tied signals
  -- arbitrarily): a planted signal carries the minute's top weight
  select count(*), count(case when pw = mw then 1 end),
         listagg(distinct r1, ',') within group (order by r1) into n, n2, l_r1
    from (select ts, max(case when rnk = 1 then signal_code end) r1, max(weight) mw,
                 max(case when signal_code in ('RT_TXN', 'APP_P95_MS') then weight end) pw
            from SCORE_TMP_DETAIL where ts > l_cut group by ts);
  ok('P31', n > 0 and n2 * 10 >= n * 8, 'MSET gives a planted signal the top weight (ties included) in '||n2||' of '||n
                                        ||' flagged planted minutes (>= 80%; Oracle''s rank 1: '||l_r1||')');

  -- P32 SVM and EM flag the planted minutes too and their details parse
  for d in (select column_value det from table(sys.odcivarchar2list('SVM', 'EM'))) loop
    PKG_ANOM_SCORE.score_query(model(d.det), l_q, l_last - 40 / 1440);
    select count(case when ts > l_cut and flag = 1 then 1 end) into n from SCORE_TMP;
    select count(*) into n2 from SCORE_TMP_DETAIL;
    ok('P32'||case d.det when 'SVM' then 'a' else 'b' end, n > 0 and n2 > 0,
       d.det||' flags '||n||' planted minutes; detail rows parsed: '||n2);
  end loop;

  -- P33-P34 PCA residual (D4 feasibility, contract section 6): the reconstruction error computed in SQL from the
  -- DM$VE/DM$VV views is small on training rows and large when one signal breaks its correlations
  l_m := model('PCA');
  l_sig := PKG_ANOM_TRAIN.model_signals(l_m);
  select json_value(settings_json, '$.K' returning number), json_value(settings_json, '$.THRESHOLD' returning number),
         json_value(settings_json, '$.TRAIN_ERR_MEDIAN' returning number)
    into l_k, l_thr, l_med from MODEL_REGISTRY where model_name = l_m;
  select sd into l_sd from MODEL_SIGNAL_STAT where model_name = l_m and signal_code = 'DBTIME';
  ok('P33', l_k between 1 and regexp_count(l_sig, ',') and l_med < 0.25 * (regexp_count(l_sig, ',') + 1)
            and l_thr > l_med,
     'PCA: K '||l_k||' of '||(regexp_count(l_sig, ',') + 1)||' components, median training error '||round(l_med, 3)
     ||' (< 0.25 per signal), threshold '||round(l_thr, 3));
  -- the newest training minute (outside every chaos window), as it is and with DBTIME + 20 sd
  execute immediate 'select max(ts) from (' || PKG_ANOM_TRAIN.training_query(l_from, l_to, l_sig) || ')' into l_last;
  PKG_ANOM_SCORE.score_query(l_m, PKG_ANOM_TRAIN.rows_query(l_last, l_last + 1 / 86400, l_sig), l_last);
  select score * l_thr into l_err0 from SCORE_TMP where ts = l_last;
  PKG_ANOM_SCORE.score_query(l_m, 'select ' || cols(l_sig, 'DBTIME', '(DBTIME + ' || PKG_ANOM_TRAIN.num_lit(20 * l_sd) || ')')
                             || ' from (' || PKG_ANOM_TRAIN.rows_query(l_last, l_last + 1 / 86400, l_sig) || ')', l_last);
  select score * l_thr, flag into l_err1, l_flag from SCORE_TMP where ts = l_last;
  select signal_code into l_r1 from SCORE_TMP_DETAIL where ts = l_last and rnk = 1;
  ok('P34', l_err1 > 10 * l_med and l_err1 > l_thr and l_flag = 1 and l_r1 in ('DBTIME', 'AAS'),
     'PCA: DBTIME + 20 sd lifts the error '||round(l_err0, 3)||' -> '||round(l_err1, 3)||' (> 10 x median, > threshold), '
     ||'flagged, first residual '||l_r1||' => D4 kept');

  -- P35 STATIC: hand-computed multiples (AAS at 3 x hi, TXN at lo / 2, the rest inside)
  l_m := model('STATIC');
  l_sig := PKG_ANOM_TRAIN.model_signals(l_m);
  l_q := 'select ' || PKG_ANOM_TRAIN.date_lit(date '2000-01-01') || ' ts';
  for s in (select regexp_substr(l_sig, '[^,]+', 1, level) code from dual connect by level <= regexp_count(l_sig, ',') + 1) loop
    select lo, hi into l_lo, l_hi from THRESHOLD_DEF where model_name = l_m and signal_code = s.code;
    l_q := l_q || ', ' || PKG_ANOM_TRAIN.num_lit(case
             when s.code = 'AAS' then 3 * l_hi
             when s.code = 'TXN' then l_lo / 2
             when l_lo is not null then (l_lo + l_hi) / 2
             else l_hi / 2 end) || ' ' || s.code;
  end loop;
  PKG_ANOM_SCORE.score_query(l_m, l_q || ' from dual', date '2000-01-01');
  select flag, score into l_flag, l_score from SCORE_TMP;
  select listagg(signal_code || '=' || round(weight, 6), ',') within group (order by rnk) into l_r1 from SCORE_TMP_DETAIL;
  ok('P35', l_flag = 1 and round(l_score, 9) = 3 and l_r1 = 'AAS=3,TXN=2',
     'STATIC: AAS at 3 x hi and TXN at lo / 2 give flag 1, score 3, details '||l_r1||' (expected AAS=3,TXN=2)');

  -- P36 SEASONAL: one signal at med + 6 x 1.4826 x MAD of its hour, the rest at the median: |z| = 6 > K
  l_m := model('SEASONAL');
  l_sig := PKG_ANOM_TRAIN.model_signals(l_m);
  select json_value(settings_json, '$.K' returning number) into l_kz from MODEL_REGISTRY where model_name = l_m;
  select min(hour_utc) into l_hr from SEASONAL_BASE where model_name = l_m;
  l_q := 'select ' || PKG_ANOM_TRAIN.date_lit(date '2000-01-01' + l_hr / 24) || ' ts';
  for s in (select regexp_substr(l_sig, '[^,]+', 1, level) code from dual connect by level <= regexp_count(l_sig, ',') + 1) loop
    select max(med), max(mad) into l_med, l_sd from SEASONAL_BASE
     where model_name = l_m and signal_code = s.code and hour_utc = l_hr;
    l_q := l_q || ', ' || PKG_ANOM_TRAIN.num_lit(case when s.code = 'CPU' then l_med + 6 * 1.4826 * l_sd else l_med end)
         || ' ' || s.code;
  end loop;
  PKG_ANOM_SCORE.score_query(l_m, l_q || ' from dual', date '2000-01-01');
  select flag, score into l_flag, l_score from SCORE_TMP;
  select signal_code, weight into l_r1, l_w1 from SCORE_TMP_DETAIL where rnk = 1;
  ok('P36', l_flag = 1 and abs(l_score - 6 / l_kz) < 1e-6 and l_r1 = 'CPU' and abs(l_w1 - 6) < 1e-6,
     'SEASONAL: CPU at |z| = 6 in hour '||l_hr||' gives flag 1, score '||round(l_score, 6)||' (6 / K = '
     ||round(6 / l_kz, 6)||'), first signal '||l_r1||' with weight '||round(l_w1, 6));
  rollback;
end;
/

prompt == E. alert episodes and grading on fabricated minutes and incidents (rolled back)
declare
  -- the fabricated case; test/test_models_static.py recomputes every e_* value from these c_* constants
  c_t0          constant date := date '2000-01-01';
  c_minutes     constant pls_integer := 1440;
  c_flags_mset  constant varchar2(200) := '63,100,215,216,260,395,790,1000,1005,1020';
  c_flags_stat  constant varchar2(200) := '61,62,64,65,66,198,199,200,405,406,407,499,501,502,1100,1101,1102,'
                                       || '1200,1201,1202,1203';
  c_flags_ifor  constant varchar2(200) := '1300,1301,1302,1329,1330,1331';
  c_gap_stat    constant varchar2(20)  := '500';   -- STATIC has no row at this minute
  c_det_mset    constant varchar2(400) := '63:CPU|AAS|RT_TXN;100:W_OTHER;215:APP_P95_MS|RT_TXN|LIO;260:W_OTHER;'
                                       || '395:W_OTHER;790:LIO_TXN|CPU|W_USERIO;1000:REDO;1020:REDO';
  c_det_stat    constant varchar2(400) := '66:CPU|DBTIME;200:PIO|W_USERIO;407:W_APPL|PIO;1102:REDO';
  -- v1.1: STATIC's breach list per flagged minute, strongest first (the same-signal rule reads it)
  c_brk_stat    constant varchar2(800) := '61:CPU;62:CPU;64:CPU;65:CPU|DBTIME;66:CPU|DBTIME;198:PIO;199:PIO|W_USERIO;'
                                       || '200:PIO|W_USERIO;405:PIO;406:PIO|W_APPL;407:W_APPL|PIO;499:REDO;501:REDO;'
                                       || '502:REDO;1100:REDO;1101:REDO|PIO;1102:REDO;1200:CPU;1201:PIO;1202:CPU;'
                                       || '1203:PIO';
  c_incidents   constant varchar2(800) := '-900101:cpu_hog:HIGH:TEST:60:80;-900102:io_storm:LOW:TEST:200:230;'
                                       || '-900103:blocking_chain:LOW:TEST:400:410;-900104:slow_drift:HIGH:TEST:600:780;'
                                       || '-900105:commit_storm:LOW:TEST:1500:1520;'
                                       || '-900106:plan_regression:HIGH:UI:1295:1300';
  -- expected (hand-computed; see the comments in test_models_static.py)
  e_ep_mset     constant varchar2(200) := '63-63-73,100-100-110,215-216-226,260-260-270,395-395-405,790-790-800,'
                                       || '1000-1005-1015,1020-1020-1030';
  e_ep_stat     constant varchar2(200) := '66-66-76,200-200-210,407-407-417,1102-1102-1112';
  e_ep_ifor     constant varchar2(200) := '1302-1302-1312,1331-1331-1341';
  e_res_mset    constant varchar2(200) := 'inc=4,caught=3,med=15,p90=155,fa=3,fa24=3,attr=2/3';
  e_res_stat    constant varchar2(200) := 'inc=4,caught=3,med=6,p90=6.8,fa=1,fa24=1,attr=2/3,b=1,c=1,p=1,best=Y';
  e_res_ifor    constant varchar2(200) := 'inc=4,caught=0,med=,p90=,fa=1,fa24=1,attr=/,b=3,c=0,p=.25,best=N';
  e_half_mset   constant varchar2(200) := 'inc=4,fa=1,fa24=2';
  e_inc_mset    constant varchar2(400) := '-900104:Y:190:LIO_TXN,CPU,W_USERIO:Y;-900103:N:::;-900102:Y:15:APP_P95_MS,RT_TXN,LIO:N;'
                                       || '-900101:Y:3:CPU,AAS,RT_TXN:Y';
  e_inc_stat    constant varchar2(400) := '-900104:N:::;-900103:Y:7:PIO:N;-900102:Y:0:PIO:Y;-900101:Y:6:CPU:Y';
  e_fired_stat  constant varchar2(200) := '66:CPU,200:PIO,407:PIO,1102:REDO';

  l_t      varchar2(4000);
  n        number;
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
  function has (p_list varchar2, p_m pls_integer) return boolean is
  begin
    return instr(',' || p_list || ',', ',' || p_m || ',') > 0;
  end;
  -- the "A|B" of minute p_m in a "m:A|B;..." spec, as "A,B" (null when the minute is not listed)
  function spec_of (p_spec varchar2, p_m pls_integer) return varchar2 is
    l_e varchar2(400);
  begin
    for i in 1 .. nvl(regexp_count(p_spec, ';') + 1, 0) loop
      l_e := regexp_substr(p_spec, '[^;]+', 1, i);
      if l_e like p_m || ':%' then
        return replace(substr(l_e, instr(l_e, ':') + 1), '|', ',');
      end if;
    end loop;
    return null;
  end;
  procedure series (p_model varchar2, p_det varchar2, p_flags varchar2, p_gap varchar2, p_score number,
                    p_brk varchar2 default null) is
    l_f number;
    l_s number;
    l_b varchar2(400);
  begin
    for m in 0 .. c_minutes - 1 loop
      if not has(p_gap, m) then
        -- (a private function cannot appear in SQL: flag, score and breach list are worked out first)
        l_f := case when has(p_flags, m) then 1 else 0 end;
        l_s := case when l_f = 0 then 0 when p_model = 'UT_MSET' and m = 1005 then 0.95 else p_score end;
        l_b := case when l_f = 1 then spec_of(p_brk, m) end;
        insert into SCORE_EVAL (run_tag, ts, model_name, detector, flag, score, scored_ts, breached)
        values ('UT_P4_GRADE', c_t0 + m / 1440, p_model, p_det, l_f, l_s, sys_extract_utc(systimestamp), l_b);
      end if;
    end loop;
  end;
  procedure details (p_model varchar2, p_spec varchar2) is
    l_e varchar2(200);
    l_m pls_integer;
    l_s varchar2(200);
  begin
    for i in 1 .. regexp_count(p_spec, ';') + 1 loop
      l_e := regexp_substr(p_spec, '[^;]+', 1, i);
      continue when l_e is null;
      l_m := to_number(substr(l_e, 1, instr(l_e, ':') - 1));
      l_s := substr(l_e, instr(l_e, ':') + 1);
      for j in 1 .. regexp_count(l_s, '\|') + 1 loop
        insert into SCORE_EVAL_DETAIL (run_tag, ts, model_name, rank, signal_code, weight, actual_value)
        values ('UT_P4_GRADE', c_t0 + l_m / 1440, p_model, j, regexp_substr(l_s, '[^|]+', 1, j), 1 / j, 0);
      end loop;
    end loop;
  end;
  function episodes (p_model varchar2) return varchar2 is
    r varchar2(4000);
  begin
    select listagg(round((start_ts - c_t0) * 1440)||'-'||round((last_ts - c_t0) * 1440)||'-'
                   ||round((end_ts - c_t0) * 1440), ',') within group (order by episode_no)
      into r from ALERT_EVAL where run_tag = 'UT_P4_GRADE' and model_name = p_model;
    return r;
  end;
  function result (p_model varchar2, p_full boolean) return varchar2 is
    r varchar2(4000);
  begin
    select 'inc='||n_incidents||',caught='||n_caught||',med='||ttd_median||',p90='||ttd_p90||',fa='||n_false_alarms
           ||',fa24='||fa_per_24h||',attr='||n_attr_hit||'/'||n_attr_scored
           ||case when p_full and detector != 'MSET' then ',b='||mcnemar_b||',c='||mcnemar_c||',p='||mcnemar_p
                  ||',best='||best_rival end
      into r from GRADE_RESULT where run_tag = 'UT_P4_GRADE' and model_name = p_model;
    return r;
  end;
begin
  delete from SCORE_EVAL_DETAIL where run_tag = 'UT_P4_GRADE';
  delete from SCORE_EVAL where run_tag = 'UT_P4_GRADE';
  series('UT_MSET', 'MSET', c_flags_mset, null, 0.9);
  series('UT_STATIC', 'STATIC', c_flags_stat, c_gap_stat, 2, c_brk_stat);
  series('UT_IFOREST', 'IFOREST', c_flags_ifor, null, 0.7);
  details('UT_MSET', c_det_mset);
  details('UT_STATIC', c_det_stat);
  for i in 1 .. regexp_count(c_incidents, ';') + 1 loop
    l_t := regexp_substr(c_incidents, '[^;]+', 1, i);
    insert into INCIDENT_TRUTH (run_id, scenario, intensity, source, requested_ts, start_ts, planned_end_ts, end_ts,
                                status, restored, refreshed_ts)
    values (to_number(regexp_substr(l_t, '[^:]+', 1, 1)), regexp_substr(l_t, '[^:]+', 1, 2),
            regexp_substr(l_t, '[^:]+', 1, 3), regexp_substr(l_t, '[^:]+', 1, 4),
            c_t0 + to_number(regexp_substr(l_t, '[^:]+', 1, 5)) / 1440, c_t0 + to_number(regexp_substr(l_t, '[^:]+', 1, 5)) / 1440,
            c_t0 + to_number(regexp_substr(l_t, '[^:]+', 1, 6)) / 1440, c_t0 + to_number(regexp_substr(l_t, '[^:]+', 1, 6)) / 1440,
            'DONE', 'Y', sys_extract_utc(systimestamp));
  end loop;

  -- the episode rules: MSET opens on its own flag; IFOREST (like SVM, EM, PCA) at the 3rd consecutive flagged
  -- minute; STATIC at the 3rd consecutive breach of the same signal (v1.1); a missing minute breaks a run; every
  -- episode closes at its 10th unflagged minute
  PKG_ANOM_SCORE.build_eval_alerts('UT_P4_GRADE');
  l_t := episodes('UT_MSET');
  ok('P37', l_t = e_ep_mset, 'MSET episodes (start-last-end): '||l_t);
  l_t := episodes('UT_STATIC');
  ok('P38', l_t = e_ep_stat, 'STATIC episodes (3 consecutive breaches of one signal; the run broken by the missing '
                             ||'minute and the alternating 1200-1203 open none): '||l_t);
  l_t := episodes('UT_IFOREST');
  ok('P39', l_t = e_ep_ifor, 'IFOREST episodes: '||l_t);
  select count(*) into n from ALERT_EVAL
   where run_tag = 'UT_P4_GRADE' and model_name = 'UT_MSET' and start_ts = c_t0 + 1000 / 1440 and peak_score = 0.95
     and top_signals = 'REDO';
  ok('P40', n = 1, 'an episode keeps the peak score of its later minutes and its opening minute''s signals');
  select listagg(round((start_ts - c_t0) * 1440)||':'||fired, ',') within group (order by episode_no) into l_t
    from ALERT_EVAL where run_tag = 'UT_P4_GRADE' and model_name = 'UT_STATIC';
  select count(*) into n from ALERT_EVAL where run_tag = 'UT_P4_GRADE' and model_name != 'UT_STATIC' and fired is not null;
  ok('P40b', l_t = e_fired_stat and n = 0, 'STATIC episodes name the signal that fired: '||l_t||'; other detectors '
                                           ||'none ('||n||')');

  -- grading over the day: caught, time to detect, false alarms (30-min cool-down, any source), attribution, McNemar
  PKG_ANOM_GRADE.grade('UT_P4_GRADE', c_t0, c_t0 + 1, 'TEST', 'N', 'N');
  l_t := result('UT_MSET', false);
  ok('P41', l_t = e_res_mset, 'MSET: '||l_t);
  l_t := result('UT_STATIC', true);
  ok('P42', l_t = e_res_stat, 'STATIC: '||l_t);
  l_t := result('UT_IFOREST', true);
  ok('P43', l_t = e_res_ifor, 'IFOREST (no attribution): '||l_t);
  select listagg(run_id||':'||caught||':'||ttd_min||':'||top3||':'||attr_hit, ';') within group (order by run_id)
    into l_t from GRADE_INCIDENT where run_tag = 'UT_P4_GRADE' and model_name = 'UT_MSET';
  ok('P44', l_t = e_inc_mset, 'MSET per incident (run:caught:ttd:top3:hit): '||l_t);
  select listagg(run_id||':'||caught||':'||ttd_min||':'||top3||':'||attr_hit, ';') within group (order by run_id)
    into l_t from GRADE_INCIDENT where run_tag = 'UT_P4_GRADE' and model_name = 'UT_STATIC';
  ok('P44b', l_t = e_inc_stat, 'STATIC per incident, attributed by the signal that fired: '||l_t);
  select count(*), count(distinct run_id) into n, l_t from GRADE_INCIDENT where run_tag = 'UT_P4_GRADE';
  ok('P45', n = 12 and l_t = '4', 'GRADE_INCIDENT: 3 models x 4 TEST incidents in range = '||n
                                  ||' rows (the UI run and the run after the range are not graded)');
  select count(*) into n from GRADE_RESULT where run_tag = 'UT_P4_GRADE' and mcnemar_ref = 'UT_MSET';
  ok('P46', n = 2, 'McNemar reference = the MSET model for both rivals: '||n);
  select count(*) into n from GRADE_SCENARIO where run_tag = 'UT_P4_GRADE' and model_name = 'UT_STATIC'
     and ((scenario = 'slow_drift' and n_caught = 0) or (scenario = 'blocking_chain' and n_caught = 1));
  ok('P47', n = 2, 'GRADE_SCENARIO per scenario (STATIC: slow_drift 0 of 1, blocking_chain 1 of 1)');

  -- false alarms per 24 h scale with the graded hours: [T0, T0 + 12 h)
  PKG_ANOM_GRADE.grade('UT_P4_GRADE', c_t0, c_t0 + 0.5, 'TEST', 'N', 'N');
  select 'inc='||n_incidents||',fa='||n_false_alarms||',fa24='||fa_per_24h into l_t
    from GRADE_RESULT where run_tag = 'UT_P4_GRADE' and model_name = 'UT_MSET';
  ok('P48', l_t = e_half_mset, 'MSET over 12 h: '||l_t);
  rollback;
end;
/

prompt == F. exact McNemar p values (hand computed)
declare
  l_code number := 0;
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
  procedure p (p_id varchar2, b pls_integer, c pls_integer, e number) is
    v number := PKG_ANOM_GRADE.mcnemar_exact_p(b, c);
  begin
    ok(p_id, abs(v - e) < 1e-12, 'McNemar exact p(b='||b||', c='||c||') = '||v||' (expected '||e||')');
  end;
begin
  p('P49a', 0, 5, 0.0625);           -- 2 x C(5,0) / 2^5
  p('P49b', 1, 7, 0.0703125);        -- 2 x (1 + 8) / 2^8
  p('P49c', 2, 10, 0.03857421875);   -- 2 x (1 + 12 + 66) / 2^12
  p('P49d', 5, 1, 0.21875);          -- 2 x (1 + 6) / 2^6, symmetric in b and c
  p('P49e', 3, 0, 0.25);             -- 2 x 1 / 2^3
  p('P49f', 3, 3, 1);                -- capped at 1
  p('P49g', 0, 0, 1);                -- no discordant pair
  begin
    l_code := PKG_ANOM_GRADE.mcnemar_exact_p(-1, 2);
    l_code := 0;
  exception when others then l_code := sqlcode;
  end;
  ok('P49h', l_code = -20403, 'a negative count is refused (ORA'||l_code||')');
end;
/

prompt == F2. the same-signal rule's run lists (v1.1)
declare
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
  procedure eq (p_id varchar2, p_got varchar2, p_exp varchar2, p_what varchar2) is
  begin
    ok(p_id, (p_got = p_exp) or (p_got is null and p_exp is null),
       p_what||' = '||nvl(p_got, '(null)')||' (expected '||nvl(p_exp, '(null)')||')');
  end;
begin
  eq('P50b1', PKG_ANOM_SCORE.runs_next(null, 'CPU,PIO'), ',CPU:1,PIO:1,', 'runs_next(null, CPU,PIO)');
  eq('P50b2', PKG_ANOM_SCORE.runs_next(',CPU:1,PIO:1,', 'CPU'), ',CPU:2,', 'runs_next: PIO stops breaching, drops out');
  eq('P50b3', PKG_ANOM_SCORE.runs_next(',CPU:3,', 'CPU,AAS'), ',CPU:3,AAS:1,', 'runs_next caps a run at 3');
  eq('P50b4', PKG_ANOM_SCORE.runs_next(',W_APPL:2,', 'W_APPL,W_APPL'), ',W_APPL:3,', 'runs_next counts a repeat once');
  eq('P50b5', PKG_ANOM_SCORE.runs_next(',AAS:2,', null), null, 'runs_next with no breach');
  ok('P50c', PKG_ANOM_SCORE.runs_max(',CPU:3,AAS:1,') = 3 and PKG_ANOM_SCORE.runs_max(null) = 0
             and PKG_ANOM_SCORE.runs_max(',APP_P95_MS:2,') = 2, 'runs_max: 3, 0 for none, 2');
  eq('P50d1', PKG_ANOM_SCORE.runs_fired(',W_APPL:2,PIO:3,', 'W_APPL,PIO'), 'PIO', 'runs_fired keeps the 3-runs only');
  eq('P50d2', PKG_ANOM_SCORE.runs_fired(',REDO:3,CPU:3,', 'CPU,REDO'), 'CPU,REDO', 'runs_fired in breach order');
  eq('P50d3', PKG_ANOM_SCORE.runs_fired(',CPU:2,', 'CPU'), null, 'runs_fired before the 3rd breach');
end;
/

prompt == G. live episodes across scoring runs (rolled back)
declare
  c_t1  constant date := date '2000-02-01';
  n     number;
  l_t   varchar2(400);
  l_fired varchar2(400);
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
  procedure rows (p_model varchar2, p_det varchar2, p_a pls_integer, p_b pls_integer, p_flags varchar2) is
  begin
    for m in p_a .. p_b loop
      insert into SCORE_MINUTE (ts, model_name, detector, flag, score, scored_ts)
      values (c_t1 + m / 1440, p_model, p_det,
              case when instr(','||p_flags||',', ','||m||',') > 0 then 1 else 0 end,
              case when instr(','||p_flags||',', ','||m||',') > 0 then 0.8 else 0 end, sys_extract_utc(systimestamp));
    end loop;
  end;
  -- v1.1: STATIC minutes p_a..p_b; p_brk "m:A|B;..." lists the flagged minutes and their breaches
  procedure rows_brk (p_model varchar2, p_a pls_integer, p_b pls_integer, p_brk varchar2) is
    l_b varchar2(400);
  begin
    for m in p_a .. p_b loop
      l_b := null;
      for i in 1 .. regexp_count(p_brk, ';') + 1 loop
        if regexp_substr(p_brk, '[^;]+', 1, i) like m || ':%' then
          l_b := replace(regexp_substr(regexp_substr(p_brk, '[^;]+', 1, i), '[^:]+$'), '|', ',');
        end if;
      end loop;
      insert into SCORE_MINUTE (ts, model_name, detector, flag, score, scored_ts, breached)
      values (c_t1 + m / 1440, p_model, 'STATIC', case when l_b is not null then 1 else 0 end,
              case when l_b is not null then 2 else 0.5 end, sys_extract_utc(systimestamp), l_b);
    end loop;
  end;
  function live (p_model varchar2) return varchar2 is
    r varchar2(400);
  begin
    select listagg(round((start_ts - c_t1) * 1440)||'-'||round((last_ts - c_t1) * 1440)||'-'
                   ||nvl(to_char(round((end_ts - c_t1) * 1440)), 'open')||'-'||status, ',') within group (order by alert_id)
      into r from ALERT where model_name = p_model;
    return r;
  end;
begin
  -- SVM: flags 5-8 open at 7 (3rd consecutive); 30-31 are two only. Scored in two runs (0-12, 13-40).
  rows('UTP4_LIVE', 'SVM', 0, 12, '5,6,7,8,30,31');
  PKG_ANOM_SCORE.process_alerts('UTP4_LIVE');
  l_t := live('UTP4_LIVE');
  select count(*) into n from ALERT_STATE where model_name = 'UTP4_LIVE' and open_alert_id is not null
     and last_ts = c_t1 + 12 / 1440;
  ok('P50', l_t = '7-8-open-OPEN' and n = 1, 'after run 1 the episode is open: '||l_t||'; state carried: '||n);
  rows('UTP4_LIVE', 'SVM', 13, 40, '5,6,7,8,30,31');
  PKG_ANOM_SCORE.process_alerts('UTP4_LIVE');
  l_t := live('UTP4_LIVE');
  select count(*) into n from ALERT_STATE where model_name = 'UTP4_LIVE' and open_alert_id is null
     and last_ts = c_t1 + 40 / 1440;
  ok('P51', l_t = '7-8-18-CLOSED' and n = 1, 'after run 2 it closed at the 10th unflagged minute: '||l_t
                                             ||'; two flags alone opened nothing');
  -- MSET opens on its own flag
  rows('UTP4_LIVE_M', 'MSET', 0, 15, '3');
  PKG_ANOM_SCORE.process_alerts('UTP4_LIVE_M');
  l_t := live('UTP4_LIVE_M');
  ok('P52', l_t = '3-3-13-CLOSED', 'MSET live episode on a single flag: '||l_t);
  -- v1.1 STATIC: CPU breaches at 10 and 11 (run 1), again at 12 (run 2): the run is carried in SIG_RUNS and opens
  -- at 12 on CPU; PIO/CPU/PIO alternating at 30-32 open nothing
  rows_brk('UTP4_LIVE_S', 0, 11, '10:CPU;11:CPU|AAS');
  PKG_ANOM_SCORE.process_alerts('UTP4_LIVE_S');
  select count(*) into n from ALERT_STATE where model_name = 'UTP4_LIVE_S' and sig_runs = ',CPU:2,AAS:1,'
     and open_alert_id is null;
  l_t := live('UTP4_LIVE_S');
  rows_brk('UTP4_LIVE_S', 12, 40, '12:PIO|CPU;30:PIO;31:CPU;32:PIO');
  PKG_ANOM_SCORE.process_alerts('UTP4_LIVE_S');
  l_t := nvl(l_t, 'none') || ' / ' || live('UTP4_LIVE_S');
  select max(fired) into l_fired from ALERT where model_name = 'UTP4_LIVE_S';
  ok('P52b', n = 1 and l_t = 'none / 12-12-22-CLOSED' and l_fired = 'CPU',
     'STATIC live: run state carried between scoring runs ('||n||'), episodes '||l_t||', fired '||l_fired);
  rollback;
end;
/

prompt == H. the hidden incident plan (generated and rolled back; the real plan is checked when it exists)
declare
  c_from constant date := to_date('2026-10-04 12:35', 'YYYY-MM-DD HH24:MI');
  l_from date;
  n      number;
  n2     number;
  n3     number;
  l_t    varchar2(4000);
  l_code number := 0;
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
begin
  -- the generator (MINSTD, seed 20261004): first five uniforms, recomputed in test_models_static.py
  l_t := PKG_ANOM_GRADE.rng_sample(20261004, 5);
  ok('P53', l_t = '0.425551412360,0.792226010371,0.541746623601,0.651267830120,0.349427725351', 'rng: '||l_t);
  select count(*), max(plan_from) into n, l_from from INCIDENT_PLAN;
  if n > 0 then
    -- v1.2: the real plan exists; its literals differ (another start), but its invariants are checked below
    dbms_output.put_line('SKIP P54-P55: INCIDENT_PLAN holds the real plan ('||n||' rows from '
                         ||to_char(l_from, 'YYYY-MM-DD HH24:MI')||' UTC); P56-P60 check it');
  else
    l_from := c_from;
    PKG_ANOM_GRADE.plan_incidents(c_from, 'N');
    select count(*), count(case when intensity = 'LOW' then 1 end), count(case when day_no = 0 then 1 end)
      into n, n2, n3 from INCIDENT_PLAN;
    ok('P54', n = 28 and n2 = 12 and n3 = 14, 'plan rows '||n||' (28), LOW '||n2||' (12), day 0 '||n3||' (14)');
    select listagg(scenario||'/'||intensity||'/'||to_char(planned_start, 'YYYY-MM-DD HH24:MI')||'/'||minutes, ';')
             within group (order by plan_id)
      into l_t from INCIDENT_PLAN where plan_id in (1, 2, 27, 28);
    ok('P55', l_t = 'hard_parse_storm/HIGH/2026-10-04 13:12/26;logon_storm/HIGH/2026-10-04 14:35/18;'
                    ||'conn_leak/HIGH/2026-10-06 09:39/40;conn_leak/HIGH/2026-10-06 11:08/31',
       'first and last rows as the Python reference: '||l_t);
  end if;
  select count(*), count(distinct day_no), count(distinct intensity) into n, n2, n3
    from INCIDENT_PLAN where scenario = 'slow_drift' and minutes = 180;
  ok('P56', n = 2 and n2 = 2 and n3 = 2, 'slow_drift: once a day for 180 min, LOW once and HIGH once');
  select count(*) into n from (select scenario from INCIDENT_PLAN group by scenario having count(distinct intensity) = 2);
  select count(*) into n2 from (select day_no from INCIDENT_PLAN group by day_no having count(distinct scenario) = 12);
  ok('P57', n = 12 and n2 = 2, 'every scenario at both intensities ('||n||' of 12); every day has all 12 ('||n2||' of 2)');
  -- v1.2: 10-40 min and never more than the scenario's maximum of contract section 5 (PKG_CHAOS refuses more with
  -- -20103 and the row would be SKIPPED); the table is written out here, independently of 93 and 96
  select count(*) into n from INCIDENT_PLAN
   where scenario != 'slow_drift'
     and (minutes < 10 or minutes > least(40, case scenario
                                                when 'blocking_chain'   then 30
                                                when 'plan_regression'  then 60
                                                when 'batch_wrong_time' then 30
                                                when 'hard_parse_storm' then 30
                                                when 'commit_storm'     then 30
                                                when 'io_storm'         then 30
                                                when 'cpu_hog'          then 30
                                                when 'temp_spill'       then 30
                                                when 'conn_leak'        then 60
                                                when 'logon_storm'      then 30
                                                when 'app_error_burst'  then 60
                                                else -1 end));
  ok('P58', n = 0, 'short runs last 10-40 min within their scenario''s maximum (30 or 60): '||n||' outside');
  select count(*) into n from (
    select planned_start, lag(planned_start + minutes / 1440) over (partition by day_no order by planned_start) prev_end,
           lag(planned_start) over (partition by day_no order by planned_start) prev_start
      from INCIDENT_PLAN)
   where prev_end is not null and (planned_start < prev_end + 45 / 1440 or planned_start < prev_start + 80 / 1440);
  ok('P59', n = 0, 'each start is >= 45 min after the previous end and >= 80 min after the previous start: '||n||' violations');
  select count(*) into n from INCIDENT_PLAN
   where planned_start + minutes / 1440 > l_from + day_no + (1440 - 40) / 1440 or planned_start < l_from + day_no;
  ok('P60', n = 0, 'every run starts in its day and ends >= 40 min before the day ends: '||n||' violations');
  begin
    PKG_ANOM_GRADE.plan_incidents(c_from, 'N');
  exception when others then l_code := sqlcode;
  end;
  ok('P61', l_code = -20401, 'the plan is generated once (ORA'||l_code||')');
  rollback;
end;
/

prompt == I. guards (each refused before any change)
declare
  l_code number;
  n      number;
  n2     number;
  procedure ok (p_id varchar2, p_cond boolean, p_msg varchar2) is
  begin
    if p_cond then :n_pass := :n_pass + 1; dbms_output.put_line('PASS '||p_id||' '||p_msg);
    else :n_fail := :n_fail + 1; dbms_output.put_line('FAIL '||p_id||' '||p_msg); end if;
  end;
begin
  l_code := 0;
  begin PKG_ANOM_TRAIN.activate('UTP4_NO_SUCH_MODEL'); exception when others then l_code := sqlcode; end;
  ok('P62', l_code = -20204, 'activate an unknown model: ORA'||l_code);
  insert into MODEL_REGISTRY (model_name, detector, variant, train_from, train_to, n_rows, signals_json, settings_json,
                              status, created_ts)
  values ('UTP4_FAKE_IFOREST', 'IFOREST', 'UTP4', date '2000-01-01', date '2000-01-02', 1, '{"used":["AAS"]}', '{}',
          'CANDIDATE', sys_extract_utc(systimestamp));
  l_code := 0;
  begin PKG_ANOM_TRAIN.activate('UTP4_FAKE_IFOREST'); exception when others then l_code := sqlcode; end;
  ok('P63', l_code = -20205, 'IFOREST is never activated: ORA'||l_code);
  update MODEL_REGISTRY set status = 'ACTIVE' where model_name = 'UTP4_FAKE_IFOREST';
  l_code := 0;
  begin PKG_ANOM_TRAIN.drop_model('UTP4_FAKE_IFOREST'); exception when others then l_code := sqlcode; end;
  ok('P64', l_code = -20206, 'an ACTIVE model is never dropped: ORA'||l_code);
  -- (score_range rolls back on failure, so this check comes last for the fabricated row)
  l_code := 0;
  begin PKG_ANOM_SCORE.score_range('UTP4_FAKE_IFOREST', sysdate - 1, sysdate, 'UT_P4_X'); exception when others then l_code := sqlcode; end;
  ok('P65', l_code = -20301, 'IFOREST is not scored in the database: ORA'||l_code);
  rollback;
  l_code := 0;
  begin PKG_ANOM_SCORE.score_range('UTP4_NO_SUCH_MODEL', sysdate - 1, sysdate, 'bad tag'); exception when others then l_code := sqlcode; end;
  ok('P66', l_code = -20303, 'a run tag outside A-Z, 0-9, _ is refused: ORA'||l_code);
  l_code := 0;
  begin PKG_ANOM_GRADE.grade('UT_P4_SCORE', sysdate - 1, sysdate, 'TEST', 'Y', 'N'); exception when others then l_code := sqlcode; end;
  ok('P67', l_code = -20400, 'grade with a refresh but no commit is refused: ORA'||l_code);
  l_code := 0;
  begin PKG_ANOM_TRAIN.purge_retired(5); exception when others then l_code := sqlcode; end;
  ok('P68', l_code = -20200, 'retired models are kept at least 14 days: ORA'||l_code);
  -- v1.3: run_due is a one-off mirror of the target's CHAOS_PLAN (one link session) and starts nothing
  select count(*) into n from INCIDENT_PLAN where status in ('STARTED', 'SKIPPED', 'MISSED');
  l_code := 0;
  begin PKG_ANOM_GRADE.run_due; exception when others then l_code := sqlcode; end;
  select count(*) into n2 from INCIDENT_PLAN where status in ('STARTED', 'SKIPPED', 'MISSED');
  ok('P69', l_code = 0 and n2 >= n, 'run_due mirrors the target''s plan statuses and starts nothing (acted rows '
                                    ||n||' -> '||n2||')');
  rollback;
end;
/

prompt == J. clean-up: this run's models, scores and the ERROR rows its refusals logged
declare
  n number;
begin
  for r in (select model_name from MODEL_REGISTRY where variant like 'UTP4%' and status != 'ACTIVE') loop
    PKG_ANOM_TRAIN.drop_model(r.model_name);
  end loop;
  delete from SCORE_EVAL_DETAIL where run_tag in ('UT_P4_SCORE', 'UT_P4_GRADE');
  delete from SCORE_EVAL where run_tag in ('UT_P4_SCORE', 'UT_P4_GRADE');
  delete from ALERT_EVAL where run_tag in ('UT_P4_SCORE', 'UT_P4_GRADE');
  delete from GRADE_INCIDENT where run_tag in ('UT_P4_SCORE', 'UT_P4_GRADE');
  delete from GRADE_RESULT where run_tag in ('UT_P4_SCORE', 'UT_P4_GRADE');
  delete from COLLECT_LOG
   where log_ts >= to_timestamp(:t_start, 'YYYY-MM-DD HH24:MI:SS.FF6') and severity = 'ERROR'
     and (message like 'event=train_failed detector=SVM model=ANOM\_SVM\_UTP4C\_%' escape '\'
          or message like 'event=train_failed detector=IFOREST %'
          or message like 'event=train_failed detector=STATIC model=ANOM\_STATIC\_UTP4C\_%' escape '\'
          or message like 'event=score_range_failed model=UTP4\_%' escape '\');
  n := sql%rowcount;
  commit;
  select count(*) into n from MODEL_REGISTRY where variant like 'UTP4%';
  dbms_output.put_line('   left behind: '||n||' UTP4 model(s)');
end;
/

prompt == totals
begin
  dbms_output.put_line('RESULT '||:n_pass||' PASS, '||:n_fail||' FAIL');
  if :n_fail > 0 then
    raise_application_error(-20999, 'anom_test_phase4: '||:n_fail||' check(s) failed');
  end if;
end;
/
