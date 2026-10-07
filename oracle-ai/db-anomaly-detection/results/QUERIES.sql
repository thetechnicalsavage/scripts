-- v1.1 2026-10-07 - v1.1 (phase 6.3, verifier's finding): the write-time TTDs (Q02 TTD_WRITE_MIN, Q03 TTD_WRITE_*,
--        Q11 LIVE_TTD_WRITE_MIN) are computed from SCORE_MINUTE.SCORED_TS / ALERT.OPENED_TS (TIMESTAMP(6)) at full
--        precision through an INTERVAL; v1.0 cast them to DATE, which cut the fractional seconds (0.0-1.0 s per row).
--        Nothing else changed. v1.0 2026-10-07 - phase 6.2.
-- v1.0 2026-10-07 - phase 6.2 of app 900 / brief 10: every number in results/test_results.md, results/csv/ and
--        results/charts/ comes from one of these queries. Read-only, as ANOMOPS on ORCLPDB1 (the observer); nothing here
--        reads the target. Run with SQL*Plus (`sqlplus -s ANOMOPS@ORCLPDB1 @QUERIES.sql > queries.out`), then
--        `python3 make_results.py queries.out` splits the output on its "@@ <name>" lines into csv/<name>.csv and
--        builds the Markdown and the charts from those files only. All times UTC. Official run tags (contract 13.1):
--        EVAL_DEV = [EVAL_FROM, EVAL_FROM + 1 day) and EVAL_TEST = [EVAL_FROM + 1 day, EVAL_TO), graded by
--        PKG_ANOM_GRADE.grade (sql/96 v1.5) on 2026-10-07 01:03:50-01:03:51Z. Detector order: PLAN.md section 7
--        (D1 MSET, D2 SVM, D3 EM, D4 PCA, D5 IFOREST, R1 STATIC, R2 SEASONAL).
set markup csv on quote on
set pagesize 50000 linesize 32767 trimspool on feedback off heading on verify off numwidth 24 sqlblanklines on
set serveroutput off define off
alter session set nls_date_format = 'YYYY-MM-DD HH24:MI:SS';
alter session set nls_timestamp_format = 'YYYY-MM-DD HH24:MI:SS.FF3';
alter session set nls_numeric_characters = '.,';

-- Q00 the clock and what was scored under each official tag (minutes, flagged, unscored = a null in a used signal),
--     when it was scored (SCORE_EVAL.SCORED_TS) and when it was graded (GRADE_RESULT.GRADED_TS)
prompt @@ q00_scored
select r.run_tag, r.detector, r.model_name, m.variant, m.status, m.train_from, m.train_to, m.n_rows,
       json_value(m.settings_json, '$.contamination') iforest_contamination,
       json_value(m.settings_json, '$.random_state') iforest_random_state,
       (select count(*) from SCORE_EVAL s where s.run_tag = r.run_tag and s.model_name = r.model_name) minutes,
       (select count(case when s.flag = 1 then 1 end) from SCORE_EVAL s where s.run_tag = r.run_tag and s.model_name = r.model_name) flagged,
       (select count(case when s.flag is null then 1 end) from SCORE_EVAL s where s.run_tag = r.run_tag and s.model_name = r.model_name) unscored,
       (select min(s.ts) from SCORE_EVAL s where s.run_tag = r.run_tag and s.model_name = r.model_name) first_ts,
       (select max(s.ts) from SCORE_EVAL s where s.run_tag = r.run_tag and s.model_name = r.model_name) last_ts,
       (select min(s.scored_ts) from SCORE_EVAL s where s.run_tag = r.run_tag and s.model_name = r.model_name) first_scored_ts,
       (select max(s.scored_ts) from SCORE_EVAL s where s.run_tag = r.run_tag and s.model_name = r.model_name) last_scored_ts,
       r.grade_from, r.grade_to, r.graded_ts,
       PKG_ANOM_LIVE.clock('EVAL_FROM') eval_from, PKG_ANOM_LIVE.clock('EVAL_TO') eval_to,
       PKG_ANOM_UI.official_day(r.run_tag, r.grade_from, r.grade_to) official_day
  from GRADE_RESULT r left join MODEL_REGISTRY m on m.model_name = r.model_name
 where r.run_tag in ('EVAL_DEV', 'EVAL_TEST')
 order by r.run_tag, decode(r.detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7);

-- Q01 GRADE_RESULT of the official tags, as grade() wrote it (McNemar vs MSET and best_rival included)
prompt @@ q01_grade_result
select run_tag, detector, model_name, grade_from, grade_to, hours, n_incidents, n_caught,
       round(ttd_median, 4) ttd_median, round(ttd_p90, 4) ttd_p90, n_false_alarms, round(fa_per_24h, 6) fa_per_24h,
       n_attr_scored, n_attr_hit, round(attr_hit_rate, 6) attr_hit_rate, round(attr_p_mean, 6) attr_p_mean,
       tie_size_median, mcnemar_ref, mcnemar_b, mcnemar_c, round(mcnemar_p, 6) mcnemar_p, best_rival, graded_ts
  from GRADE_RESULT where run_tag in ('EVAL_DEV', 'EVAL_TEST')
 order by run_tag, decode(detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7);

-- Q02 GRADE_INCIDENT of the official tags (with the opening episode's FIRED list and top signals), plus the time
--     to detect to the alert row's write time. PLAN 7a: TTD is
--     to the begin time of the interval whose scoring opens the episode (TTD_MIN, grade()'s); the row itself is written
--     when the live scorer (ANOM_SCORE_JOB) has scored that interval. That moment is SCORE_MINUTE.SCORED_TS of the
--     opening interval for the detector's FINAL model, which scored every evaluation minute live, in the same job run
--     a chosen variant would have been scored in. IFOREST (D5) has no live path (PLAN 7: not part of the live app): null.
prompt @@ q02_incidents
select g.run_tag, g.detector, g.model_name, g.run_id, g.scenario, g.intensity, g.inc_start, g.inc_end,
       round((g.inc_end - g.inc_start) * 1440, 2) inc_minutes, g.caught, g.alert_start, round(g.ttd_min, 4) ttd_min,
       -- v1.1: full precision (the INTERVAL keeps SCORED_TS's fractional seconds; a cast to DATE cut them)
       case when g.caught = 'Y' and g.detector != 'IFOREST' then round(extract(day from x.iv) * 1440 + extract(hour from x.iv) * 60
            + extract(minute from x.iv) + extract(second from x.iv) / 60, 4) end ttd_write_min,
       case when g.caught = 'Y' and g.detector != 'IFOREST' then s.scored_ts end alert_write_ts,
       g.top3, g.attr_hit, g.attr_lenient, round(g.attr_p, 6) attr_p, g.tie_size,
       (select listagg(x.signal_code, ',') within group (order by x.signal_code) from EXPECTED_SIGNALS x
         where x.scenario = g.scenario) expected_signals,
       (select max(e.fired) from ALERT_EVAL e where e.run_tag = g.run_tag and e.model_name = g.model_name
           and e.start_ts = g.alert_start) fired,
       (select max(e.top_signals) from ALERT_EVAL e where e.run_tag = g.run_tag and e.model_name = g.model_name
           and e.start_ts = g.alert_start) episode_top_signals
  from GRADE_INCIDENT g
  left join MODEL_REGISTRY f on f.detector = g.detector and f.variant = 'FINAL' and f.status = 'ACTIVE'
  left join SCORE_MINUTE s on s.model_name = f.model_name and s.ts = g.alert_start
  outer apply (select s.scored_ts - cast(g.inc_start as timestamp) iv from dual) x
 where g.run_tag in ('EVAL_DEV', 'EVAL_TEST')
 order by g.run_tag, g.run_id, decode(g.detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7);

-- Q03 the scoreboard per tag, detector and intensity slice (LOW, HIGH, ALL): caught/total, TTD median and p90
--     (PERCENTILE_CONT, as grade() computes them) to the opening interval and to the write time, ATTR_P mean (primary),
--     lenient hits / scored, median tie size; false alarms (ALL only: an episode has no intensity) from GRADE_RESULT;
--     n_caught_no_details = caught incidents whose opening minute has no stored detail signal (TOP3 null: ATTR_P 0).
prompt @@ q03_scoreboard
with w as (
  select g.run_tag, g.detector, g.model_name, g.intensity, g.caught, g.ttd_min, g.attr_p, g.attr_lenient, g.tie_size, g.top3,
         -- v1.1: full precision, as Q02
         case when g.caught = 'Y' and g.detector != 'IFOREST' then extract(day from x.iv) * 1440 + extract(hour from x.iv) * 60
              + extract(minute from x.iv) + extract(second from x.iv) / 60 end ttd_write
    from GRADE_INCIDENT g
    left join MODEL_REGISTRY f on f.detector = g.detector and f.variant = 'FINAL' and f.status = 'ACTIVE'
    left join SCORE_MINUTE s on s.model_name = f.model_name and s.ts = g.alert_start
    outer apply (select s.scored_ts - cast(g.inc_start as timestamp) iv from dual) x
   where g.run_tag in ('EVAL_DEV', 'EVAL_TEST')),
a as (
  select run_tag, detector, model_name, nvl(intensity, 'ALL') slice, grouping(intensity) is_all,
         count(*) n_incidents, count(case when caught = 'Y' then 1 end) n_caught,
         round(percentile_cont(0.5) within group (order by case when caught = 'Y' then ttd_min end), 4) ttd_median,
         round(percentile_cont(0.9) within group (order by case when caught = 'Y' then ttd_min end), 4) ttd_p90,
         round(percentile_cont(0.5) within group (order by ttd_write), 4) ttd_write_median,
         round(percentile_cont(0.9) within group (order by ttd_write), 4) ttd_write_p90,
         round(avg(case when caught = 'Y' then attr_p end), 6) attr_p_mean,
         count(case when caught = 'Y' and attr_lenient = 1 then 1 end) n_lenient_hit,
         count(case when caught = 'Y' and attr_lenient is not null then 1 end) n_lenient_scored,
         median(case when caught = 'Y' then tie_size end) tie_size_median,
         count(case when caught = 'Y' and top3 is null then 1 end) n_caught_no_details
    from w group by run_tag, detector, model_name, grouping sets ((intensity), ()))
select a.run_tag, a.detector, a.model_name, a.slice, a.n_incidents, a.n_caught, a.ttd_median, a.ttd_p90,
       a.ttd_write_median, a.ttd_write_p90,
       case when a.detector != 'IFOREST' then a.attr_p_mean end attr_p_mean,
       case when a.detector != 'IFOREST' then a.n_lenient_hit end n_lenient_hit,
       case when a.detector != 'IFOREST' then a.n_lenient_scored end n_lenient_scored,
       case when a.detector != 'IFOREST' and a.n_lenient_scored > 0 then round(a.n_lenient_hit / a.n_lenient_scored, 6) end lenient_rate,
       case when a.detector != 'IFOREST' then a.tie_size_median end tie_size_median,
       case when a.is_all = 1 then r.n_false_alarms end n_false_alarms,
       case when a.is_all = 1 then round(r.fa_per_24h, 6) end fa_per_24h,
       case when a.is_all = 1 then r.hours end hours,
       case when a.detector != 'IFOREST' then a.n_caught_no_details end n_caught_no_details
  from a join GRADE_RESULT r on r.run_tag = a.run_tag and r.model_name = a.model_name
 order by a.run_tag, decode(a.detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7),
          decode(a.slice, 'LOW', 1, 'HIGH', 2, 'ALL', 3);

-- Q04 check: Q03's ALL slice recomputed from GRADE_INCIDENT equals grade()'s GRADE_RESULT (0 differing rows expected)
prompt @@ q04_check_scoreboard
with a as (
  select run_tag, model_name, count(*) n_inc, count(case when caught = 'Y' then 1 end) n_caught,
         percentile_cont(0.5) within group (order by case when caught = 'Y' then ttd_min end) ttd_med,
         percentile_cont(0.9) within group (order by case when caught = 'Y' then ttd_min end) ttd_p90,
         round(avg(case when caught = 'Y' then attr_p end), 6) attr_p, count(case when attr_hit = 'Y' then 1 end) hits,
         median(case when caught = 'Y' then tie_size end) tie
    from GRADE_INCIDENT where run_tag in ('EVAL_DEV', 'EVAL_TEST') group by run_tag, model_name)
select count(*) models_checked,
       count(case when decode(a.n_inc, r.n_incidents, 0, 1) + decode(a.n_caught, r.n_caught, 0, 1)
                     + decode(round(a.ttd_med, 9), round(r.ttd_median, 9), 0, 1) + decode(round(a.ttd_p90, 9), round(r.ttd_p90, 9), 0, 1)
                     + case when r.detector = 'IFOREST' then 0
                            else decode(a.attr_p, r.attr_p_mean, 0, 1) + decode(a.hits, r.n_attr_hit, 0, 1)
                                 + decode(a.tie, r.tie_size_median, 0, 1) end > 0 then 1 end) differing_rows
  from a join GRADE_RESULT r on r.run_tag = a.run_tag and r.model_name = a.model_name;

-- Q05 exact McNemar of MSET-SPRT against every rival (grade()'s values) beside a recomputation from GRADE_INCIDENT:
--     b = MSET caught and the rival missed, c = the reverse, two-sided p = min(1, 2 * sum_{i <= min(b,c)} C(b+c,i) / 2^(b+c)).
--     Best rival per PLAN 7a: most incidents caught, then fewest false alarms per 24 h, then lower median TTD.
prompt @@ q05_mcnemar
with p as (
  select a.run_tag, b.model_name rival,
         count(case when a.caught = 'Y' and b.caught = 'N' then 1 end) b_re,
         count(case when a.caught = 'N' and b.caught = 'Y' then 1 end) c_re,
         count(case when a.caught = 'Y' and b.caught = 'Y' then 1 end) both_caught,
         count(case when a.caught = 'N' and b.caught = 'N' then 1 end) both_missed
    from GRADE_INCIDENT a
    join GRADE_INCIDENT b on b.run_tag = a.run_tag and b.run_id = a.run_id and b.detector != 'MSET'
   where a.run_tag in ('EVAL_DEV', 'EVAL_TEST') and a.detector = 'MSET'
   group by a.run_tag, b.model_name)
select r.run_tag, r.detector rival_detector, r.model_name rival_model, r.mcnemar_ref,
       m.n_caught mset_caught, r.n_caught rival_caught, r.n_incidents, p.both_caught, p.both_missed,
       r.mcnemar_b, r.mcnemar_c, round(r.mcnemar_p, 6) mcnemar_p,
       p.b_re, p.c_re, round(PKG_ANOM_GRADE.mcnemar_exact_p(p.b_re, p.c_re), 6) p_re,
       r.n_false_alarms rival_fa, round(r.fa_per_24h, 6) rival_fa_per_24h, round(r.ttd_median, 4) rival_ttd_median,
       m.n_false_alarms mset_fa, round(m.ttd_median, 4) mset_ttd_median,
       rank() over (partition by r.run_tag order by r.n_caught desc, r.fa_per_24h, r.ttd_median nulls last) plan7a_rank,
       r.best_rival
  from GRADE_RESULT r
  join GRADE_RESULT m on m.run_tag = r.run_tag and m.detector = 'MSET'
  join p on p.run_tag = r.run_tag and p.rival = r.model_name
 where r.run_tag in ('EVAL_DEV', 'EVAL_TEST') and r.detector != 'MSET'
 order by r.run_tag, plan7a_rank, decode(r.detector, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7);

-- Q06 per-scenario matrix: rows scenario x intensity, one row per detector: caught / incidents and the minutes to
--     detect of each incident in run id order ('-' = missed)
prompt @@ q06_scenario_matrix
select run_tag, scenario, intensity, detector, count(*) n_incidents, count(case when caught = 'Y' then 1 end) n_caught,
       listagg(case when caught = 'Y' then to_char(round(ttd_min, 1), 'FM9990.0') else '-' end, ' ') within group (order by run_id) ttd_list,
       listagg(run_id, ' ') within group (order by run_id) run_ids,
       round(avg(case when caught = 'Y' then attr_p end), 6) attr_p_mean
  from GRADE_INCIDENT where run_tag in ('EVAL_DEV', 'EVAL_TEST')
 group by run_tag, scenario, intensity, detector
 order by run_tag, scenario, decode(intensity, 'LOW', 1, 2),
          decode(detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7);

-- Q07 every false-alarm episode of the official tags (grade()'s predicate: an ALERT_EVAL episode starting in the
--     graded range outside every run's [start, end + 30 min], any source)
prompt @@ q07_false_alarms
select a.run_tag, a.detector, a.model_name, a.episode_no, a.start_ts, a.last_ts, a.end_ts, a.status,
       round((nvl(a.end_ts, a.last_ts) - a.start_ts) * 1440) minutes, a.fired, a.top_signals,
       (select max(t.run_id || ' ' || t.scenario || ' ' || t.intensity || ' ended ' || to_char(t.end_ts, 'HH24:MI'))
               keep (dense_rank last order by t.end_ts)
          from INCIDENT_TRUTH t where t.gone_ts is null and t.end_ts <= a.start_ts) previous_run
  from ALERT_EVAL a join GRADE_RESULT r on r.run_tag = a.run_tag and r.model_name = a.model_name
 where a.run_tag in ('EVAL_DEV', 'EVAL_TEST')
   and a.start_ts >= r.grade_from and a.start_ts < r.grade_to
   and not exists (select 1 from INCIDENT_TRUTH t
                    where t.gone_ts is null
                      and a.start_ts >= coalesce(t.start_ts, t.requested_ts)
                      and a.start_ts <= coalesce(t.end_ts, t.planned_end_ts, t.start_ts, t.requested_ts) + 30 / 1440)
 order by a.run_tag, decode(a.detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7), a.start_ts;

-- Q08 false alarms and episodes per day and detector (the chart "false alarms per day")
prompt @@ q08_fa_per_day
select r.run_tag, r.detector, r.model_name, r.n_false_alarms, round(r.fa_per_24h, 6) fa_per_24h,
       (select count(*) from ALERT_EVAL a where a.run_tag = r.run_tag and a.model_name = r.model_name) episodes,
       (select count(*) from ALERT_EVAL a where a.run_tag = r.run_tag and a.model_name = r.model_name and a.status = 'OPEN') open_at_end
  from GRADE_RESULT r where r.run_tag in ('EVAL_DEV', 'EVAL_TEST')
 order by r.run_tag, decode(r.detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7);

-- Q09 the slow-drift incident of the test day, minute by minute from 60 min before its start to 30 min after its
--     end: the four expected signals (EXPECTED_SIGNALS: APP_P95_MS, CPU_TXN, LIO_TXN, RT_TXN) as multiples of their
--     upper static threshold (THRESHOLD_DEF of the chosen STATIC model; 1 = the threshold), the MSET and STATIC flags
--     under EVAL_TEST and STATIC's breach list
prompt @@ q09_drift_minutes
with i as (select run_id, inc_start, inc_end from GRADE_INCIDENT
            where run_tag = 'EVAL_TEST' and detector = 'MSET' and scenario = 'slow_drift'),
st as (select model_name from GRADE_RESULT where run_tag = 'EVAL_TEST' and detector = 'STATIC'),
ms as (select model_name from GRADE_RESULT where run_tag = 'EVAL_TEST' and detector = 'MSET'),
th as (select t.signal_code, t.hi from THRESHOLD_DEF t join st on st.model_name = t.model_name)
select i.run_id, f.ts, round((f.ts - i.inc_start) * 1440, 4) min_from_start,
       f.lio_txn, f.cpu_txn, f.rt_txn, f.app_p95_ms,
       round(f.lio_txn / (select hi from th where signal_code = 'LIO_TXN'), 6) lio_txn_x,
       round(f.cpu_txn / (select hi from th where signal_code = 'CPU_TXN'), 6) cpu_txn_x,
       round(f.rt_txn / (select hi from th where signal_code = 'RT_TXN'), 6) rt_txn_x,
       round(f.app_p95_ms / (select hi from th where signal_code = 'APP_P95_MS'), 6) app_p95_ms_x,
       (select s.flag from SCORE_EVAL s join ms on ms.model_name = s.model_name where s.run_tag = 'EVAL_TEST' and s.ts = f.ts) mset_flag,
       (select s.flag from SCORE_EVAL s join st on st.model_name = s.model_name where s.run_tag = 'EVAL_TEST' and s.ts = f.ts) static_flag,
       (select s.breached from SCORE_EVAL s join st on st.model_name = s.model_name where s.run_tag = 'EVAL_TEST' and s.ts = f.ts) static_breached
  from i join FEATURE_MINUTE f on f.ts >= i.inc_start - 60 / 1440 and f.ts < i.inc_end + 30 / 1440
 order by f.ts;

-- Q10 the slow-drift incident's events on one time axis: incident start/end, every MSET and STATIC episode that
--     overlaps the window, the first STATIC breach (first minute STATIC flags any signal) and the first breach of an
--     expected signal inside [start, end + 10 min], MSET's first flagged minute there, and each detector's GRADE_INCIDENT
prompt @@ q10_drift_events
with i as (select run_id, scenario, intensity, inc_start, inc_end from GRADE_INCIDENT
            where run_tag = 'EVAL_TEST' and detector = 'MSET' and scenario = 'slow_drift'),
st as (select model_name from GRADE_RESULT where run_tag = 'EVAL_TEST' and detector = 'STATIC'),
ms as (select model_name from GRADE_RESULT where run_tag = 'EVAL_TEST' and detector = 'MSET')
select 'incident' event, i.run_id || ' ' || i.scenario || ' ' || i.intensity detail, i.inc_start ts, i.inc_end ts_end, cast(null as number) minutes_from_start from i
union all
select 'episode ' || a.detector, 'episode ' || a.episode_no || ' fired=' || a.fired || ' top=' || a.top_signals,
       a.start_ts, nvl(a.end_ts, a.last_ts), round((a.start_ts - i.inc_start) * 1440, 4)
  from ALERT_EVAL a, i
 where a.run_tag = 'EVAL_TEST' and a.detector in ('MSET', 'STATIC')
   and a.start_ts < i.inc_end + 30 / 1440 and nvl(a.end_ts, a.last_ts) >= i.inc_start - 60 / 1440
union all
select 'first static breach', min(s.breached) keep (dense_rank first order by s.ts), min(s.ts), cast(null as date), round((min(s.ts) - max(i.inc_start)) * 1440, 4)
  from SCORE_EVAL s join st on st.model_name = s.model_name, i
 where s.run_tag = 'EVAL_TEST' and s.flag = 1 and s.ts >= i.inc_start and s.ts <= i.inc_end + 10 / 1440
union all
select 'first static breach of an expected signal', min(s.breached) keep (dense_rank first order by s.ts), min(s.ts), cast(null as date),
       round((min(s.ts) - max(i.inc_start)) * 1440, 4)
  from SCORE_EVAL s join st on st.model_name = s.model_name, i
 where s.run_tag = 'EVAL_TEST' and s.flag = 1 and s.ts >= i.inc_start and s.ts <= i.inc_end + 10 / 1440
   and regexp_like(',' || s.breached || ',', ',(APP_P95_MS|CPU_TXN|LIO_TXN|RT_TXN),')
union all
select 'first mset flag', cast(null as varchar2(1)), min(s.ts), cast(null as date), round((min(s.ts) - max(i.inc_start)) * 1440, 4)
  from SCORE_EVAL s join ms on ms.model_name = s.model_name, i
 where s.run_tag = 'EVAL_TEST' and s.flag = 1 and s.ts >= i.inc_start and s.ts <= i.inc_end + 10 / 1440
union all
select 'graded ' || g.detector, 'caught=' || g.caught || ' top3=' || g.top3 || ' attr_p=' || round(g.attr_p, 4), g.alert_start, cast(null as date), round(g.ttd_min, 4)
  from GRADE_INCIDENT g join i on i.run_id = g.run_id where g.run_tag = 'EVAL_TEST'
 order by 3 nulls last, 1;

-- Q11 live cross-check for the two chosen models that ran live (MSET and STATIC = their FINAL models): did the live
--     ALERT table open an episode at the same interval as the graded one, and when was that row written
prompt @@ q11_live_alerts
select g.run_tag, g.detector, g.run_id, g.scenario, g.intensity, g.caught, g.alert_start,
       (select count(*) from ALERT l where l.model_name = g.model_name and l.start_ts = g.alert_start) live_same_start,
       (select min(l.opened_ts) from ALERT l where l.model_name = g.model_name and l.start_ts = g.alert_start) live_opened_ts,
       -- v1.1: full precision, as Q02
       (select round(extract(day from v.iv) * 1440 + extract(hour from v.iv) * 60 + extract(minute from v.iv)
               + extract(second from v.iv) / 60, 4)
          from (select min(l.opened_ts) - cast(g.inc_start as timestamp) iv from ALERT l
                 where l.model_name = g.model_name and l.start_ts = g.alert_start) v) live_ttd_write_min,
       (select min(l.start_ts) from ALERT l where l.model_name = g.model_name
           and l.start_ts >= g.inc_start and l.start_ts <= g.inc_end + 10 / 1440) live_first_start_in_window
  from GRADE_INCIDENT g
 where g.run_tag = 'EVAL_TEST' and g.detector in ('MSET', 'STATIC')
   and g.model_name in (select model_name from MODEL_REGISTRY where variant = 'FINAL')
 order by g.detector, g.run_id;

-- Q12 EVAL_DEV reproduces the dev-tuning record of each chosen candidate (results/dev_tuning.md): same incidents
--     caught, false alarms, TTD and attribution (0 differing rows expected)
prompt @@ q12_dev_matches_devtune
with c as (
  select 'MSET' detector, 'DEVTUNE_MSET_3_5' tag from dual union all select 'SVM', 'DEVTUNE_SVM_0_02' from dual union all
  select 'EM', 'DEVTUNE_EM_0_02' from dual union all select 'PCA', 'DEVTUNE_PCA_99' from dual union all
  select 'IFOREST', 'DEVTUNE_IFOREST_0_02' from dual union all select 'STATIC', 'DEVTUNE_STATIC_99_5' from dual union all
  select 'SEASONAL', 'DEVTUNE_SEASONAL_3' from dual)
select c.detector, c.tag devtune_tag, e.model_name, e.n_caught, d.n_caught devtune_caught, e.n_false_alarms, d.n_false_alarms devtune_fa,
       round(e.ttd_median, 4) ttd_median, round(d.ttd_median, 4) devtune_ttd_median,
       round(e.attr_p_mean, 6) attr_p_mean, round(d.attr_p_mean, 6) devtune_attr_p_mean,
       case when e.model_name = d.model_name and e.n_caught = d.n_caught and e.n_false_alarms = d.n_false_alarms
                 and decode(e.ttd_median, d.ttd_median, 1, 0) = 1 and decode(e.ttd_p90, d.ttd_p90, 1, 0) = 1
                 and decode(e.attr_p_mean, d.attr_p_mean, 1, 0) = 1 and decode(e.n_attr_hit, d.n_attr_hit, 1, 0) = 1
                 and (select count(*) from GRADE_INCIDENT x join GRADE_INCIDENT y on y.run_tag = c.tag and y.run_id = x.run_id
                       where x.run_tag = 'EVAL_DEV' and x.model_name = e.model_name
                         and (x.caught != y.caught or decode(x.alert_start, y.alert_start, 1, 0) = 0)) = 0
            then 'SAME' else 'DIFFERENT' end verdict
  from c join GRADE_RESULT e on e.run_tag = 'EVAL_DEV' and e.detector = c.detector
         join GRADE_RESULT d on d.run_tag = c.tag
 order by decode(c.detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7);

-- Q13 phase 6.0's test-day half (deferred by results/dev_tuning.md section 3 to the opening of 6.2): plan rows,
--     their runs, other runs, completeness, null-signal minutes, retraining
prompt @@ q13_preconditions_test_day
select 'plan rows of the test day' item, to_char(count(*)) value from INCIDENT_PLAN
 where planned_start >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 and planned_start < PKG_ANOM_LIVE.clock('EVAL_TO')
union all
select 'plan rows of the test day by status', listagg(status || ' ' || n, ', ') within group (order by status)
  from (select status, count(*) n from INCIDENT_PLAN
         where planned_start >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 and planned_start < PKG_ANOM_LIVE.clock('EVAL_TO') group by status)
union all
select 'runs starting in the test day by source / status', listagg(source || ' ' || status || ' ' || n, ', ') within group (order by source)
  from (select source, status, count(*) n from INCIDENT_TRUTH
         where coalesce(start_ts, requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
           and coalesce(start_ts, requested_ts) < PKG_ANOM_LIVE.clock('EVAL_TO') group by source, status)
union all
select 'STARTED plan rows without a SCHEDULE run', to_char(count(*)) from INCIDENT_PLAN p
 where p.planned_start >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 and p.planned_start < PKG_ANOM_LIVE.clock('EVAL_TO')
   and p.status = 'STARTED' and not exists (select 1 from INCIDENT_TRUTH t where t.run_id = p.run_id and t.source = 'SCHEDULE')
union all
select 'SCHEDULE runs without a STARTED plan row', to_char(count(*)) from INCIDENT_TRUTH t
 where coalesce(t.start_ts, t.requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
   and coalesce(t.start_ts, t.requested_ts) < PKG_ANOM_LIVE.clock('EVAL_TO') and t.source = 'SCHEDULE'
   and not exists (select 1 from INCIDENT_PLAN p where p.run_id = t.run_id and p.status = 'STARTED')
union all
select 'runs not DONE/restored or gone (evaluation)', to_char(count(*)) from INCIDENT_TRUTH t
 where coalesce(t.start_ts, t.requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM') and coalesce(t.start_ts, t.requested_ts) < PKG_ANOM_LIVE.clock('EVAL_TO')
   and (t.status != 'DONE' or nvl(t.restored, 'N') != 'Y' or t.gone_ts is not null)
union all
select 'other runs (UI/TEST or earlier) reaching into the test day', to_char(count(*)) from INCIDENT_TRUTH t
 where coalesce(t.end_ts, t.planned_end_ts, t.start_ts, t.requested_ts) + 30/1440 >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
   and coalesce(t.start_ts, t.requested_ts) < PKG_ANOM_LIVE.clock('EVAL_TO')
   and not (t.source = 'SCHEDULE' and coalesce(t.start_ts, t.requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1)
union all
select 'Control Center actions during the evaluation', to_char(count(*)) from ANOM_UI_AUDIT
 where cast(audit_ts as date) >= PKG_ANOM_LIVE.clock('EVAL_FROM') and cast(audit_ts as date) < PKG_ANOM_LIVE.clock('EVAL_TO')
union all
select 'METRIC_MINUTE rows / back-filled / gaps over 90 s (test day)',
       count(*) || ' / ' || count(case when source = 'H' then 1 end) || ' / ' || count(case when (begin_time - prev) * 86400 > 90 then 1 end)
       || ' (largest step ' || max(round((begin_time - prev) * 86400)) || ' s)'
  from (select begin_time, source, lag(begin_time) over (order by begin_time) prev from METRIC_MINUTE
         where begin_time >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 - 10/1440 and begin_time < PKG_ANOM_LIVE.clock('EVAL_TO'))
 where begin_time >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
union all
select 'APP_MINUTE rows / minutes with no transaction (test day)', count(*) || ' / ' || count(case when n_ok = 0 and n_err = 0 then 1 end)
  from APP_MINUTE where ts_minute >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 and ts_minute < PKG_ANOM_LIVE.clock('EVAL_TO')
union all
select 'FEATURE_MINUTE rows (test day)', to_char(count(*)) from FEATURE_MINUTE
 where ts >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 and ts < PKG_ANOM_LIVE.clock('EVAL_TO')
union all
select 'unscored (null-signal) minutes per model, EVAL_TEST',
       listagg(detector || ' ' || n, ', ') within group (order by detector)
  from (select detector, count(case when flag is null then 1 end) n from SCORE_EVAL where run_tag = 'EVAL_TEST' group by detector)
union all
select 'unscored minutes inside run ' || t.run_id || ' (' || t.scenario || ' ' || t.intensity || ')', to_char(count(distinct s.ts))
  from SCORE_EVAL s join INCIDENT_TRUTH t on s.ts >= t.start_ts - 1/1440 and s.ts <= t.end_ts
 where s.run_tag = 'EVAL_TEST' and s.flag is null group by t.run_id, t.scenario, t.intensity
union all
select 'models created after FINAL_AT that are not dev-tuning CANDIDATEs', to_char(count(*)) from MODEL_REGISTRY
 where created_ts >= cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp) + interval '1' minute
   and not (status = 'CANDIDATE' and (variant like 'TUNE\_%' escape '\' or detector = 'IFOREST'))
union all
select 'models created during the evaluation', to_char(count(*)) from MODEL_REGISTRY
 where created_ts >= cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp) + interval '1' minute
   and created_ts < cast(PKG_ANOM_LIVE.clock('EVAL_TO') as timestamp)
union all
select 'ACTIVE models (all FINAL, activated at FINAL_AT)', listagg(model_name || ' ' || to_char(status_ts, 'MM-DD HH24:MI:SS'), ', ') within group (order by detector)
  from MODEL_REGISTRY where status = 'ACTIVE'
union all
select 'COLLECT_LOG rows in the test day (any severity)', to_char(count(*)) from COLLECT_LOG
 where log_ts >= cast(PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 as timestamp) and log_ts < cast(PKG_ANOM_LIVE.clock('EVAL_TO') as timestamp)
union all
select 'INCIDENT_TRUTH last refresh / last evaluation run end', r.v || ' / ' || x.e
  from (select max(to_char(value_ts, 'YYYY-MM-DD HH24:MI:SS')) v from ANOM_STATE where name = 'TRUTH_REFRESH') r,
       (select to_char(max(end_ts), 'YYYY-MM-DD HH24:MI:SS') e from INCIDENT_TRUTH
         where coalesce(start_ts, requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM')
           and coalesce(start_ts, requested_ts) < PKG_ANOM_LIVE.clock('EVAL_TO')) x;

-- Q14 the test day's incidents (the graded set: INCIDENT_TRUTH SCHEDULE runs starting in the test day)
prompt @@ q14_test_incidents
select t.run_id, t.scenario, t.intensity, t.source, t.status, t.restored, t.start_ts, t.end_ts,
       round((t.end_ts - t.start_ts) * 1440, 2) minutes, p.plan_id, p.minutes plan_minutes, p.planned_start
  from INCIDENT_TRUTH t left join INCIDENT_PLAN p on p.run_id = t.run_id
 where t.gone_ts is null and t.source = 'SCHEDULE'
   and coalesce(t.start_ts, t.requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
   and coalesce(t.start_ts, t.requested_ts) < PKG_ANOM_LIVE.clock('EVAL_TO')
 order by t.run_id;
-- Q15 every alert episode of the official tags, classified: the episode that catches run N (GRADE_INCIDENT.ALERT_START),
--     a later episode inside run N's window [start, end + 30 min] (neither a catch nor a false alarm), or a false alarm
prompt @@ q15_episodes
select a.run_tag, a.detector, a.episode_no, a.start_ts, nvl(a.end_ts, a.last_ts) end_or_last_ts, a.status,
       round((nvl(a.end_ts, a.last_ts) - a.start_ts) * 1440) minutes, a.fired, a.top_signals,
       nvl((select case when count(*) > 0 then 'catch of ' || listagg(g.run_id || ' ' || g.scenario || ' ' || g.intensity, '; ')
                                                    within group (order by g.run_id) end
              from GRADE_INCIDENT g where g.run_tag = a.run_tag and g.model_name = a.model_name and g.alert_start = a.start_ts),
           nvl((select case when count(*) > 0 then 'inside window of ' || max(t.run_id || ' ' || t.scenario || ' ' || t.intensity) end
                  from INCIDENT_TRUTH t
                 where t.gone_ts is null and a.start_ts >= coalesce(t.start_ts, t.requested_ts)
                   and a.start_ts <= coalesce(t.end_ts, t.planned_end_ts, t.start_ts, t.requested_ts) + 30 / 1440),
               'false alarm')) classification
  from ALERT_EVAL a
 where a.run_tag in ('EVAL_DEV', 'EVAL_TEST')
 order by a.run_tag, decode(a.detector, 'MSET', 1, 'SVM', 2, 'EM', 3, 'PCA', 4, 'IFOREST', 5, 'STATIC', 6, 'SEASONAL', 7), a.start_ts;
-- Q16 the slow-drift incident of the test day: each expected signal's peak as a multiple of its static upper threshold
--     inside the incident [start, end], the minute of the peak, and the minutes at or above the threshold (x >= 1)
prompt @@ q16_drift_peaks
with i as (select run_id, inc_start, inc_end from GRADE_INCIDENT
            where run_tag = 'EVAL_TEST' and detector = 'MSET' and scenario = 'slow_drift'),
th as (select t.signal_code, t.hi from THRESHOLD_DEF t
        where t.model_name = (select model_name from GRADE_RESULT where run_tag = 'EVAL_TEST' and detector = 'STATIC')),
v as (select u.ts, u.signal_code, u.val
        from (select f.ts, f.lio_txn, f.cpu_txn, f.rt_txn, f.app_p95_ms
                from i join FEATURE_MINUTE f on f.ts >= i.inc_start and f.ts <= i.inc_end)
        unpivot (val for signal_code in (lio_txn as 'LIO_TXN', cpu_txn as 'CPU_TXN', rt_txn as 'RT_TXN', app_p95_ms as 'APP_P95_MS')) u)
select v.signal_code, th.hi static_hi, count(*) minutes, round(min(v.val / th.hi), 6) min_x,
       round(max(v.val / th.hi), 6) peak_x, max(v.ts) keep (dense_rank last order by v.val) peak_ts,
       count(case when v.val >= th.hi then 1 end) minutes_at_or_above
  from v join th on th.signal_code = v.signal_code
 group by v.signal_code, th.hi order by v.signal_code;
prompt @@ end
