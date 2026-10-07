-- v1.1 2026-10-07 - v1.1 (Codex review): F04 also returns the driver's TPS by minute of hour (APP_MINUTE.APP_TPS).
-- v1.0 2026-10-07 - phase 6.3 of app 900 / brief 10: every number in results/false_alarms.md. Read-only, as ANOMOPS on
--        ORCLPDB1. F01-F11 read only observer tables (METRIC_MINUTE, APP_MINUTE, SCORE_EVAL(_DETAIL), SCORE_MINUTE,
--        ALERT_EVAL, INCIDENT_TRUTH, TRAIN_EXCLUDE, THRESHOLD_DEF, MODEL_REGISTRY, COLLECT_LOG, the scheduler run log).
--        A01-A04 read the target's in-memory ASH through ANOM_MON_LINK: the incident drill-down path of contract
--        section 10 (ANOM_MON holds SELECT on V$ACTIVE_SESSION_HISTORY, sql/86 argument 5 = Y; the estate has the
--        Diagnostics Pack), read after EVAL_TO, so no training exclusion window applies. Each A-query is one remote
--        SELECT in the existing link session; the script commits nothing and closes the link at the end. In-memory ASH
--        held only the last 9 hours or so when this ran (07-Oct about 02:00Z; the span is fa_a01), so the A-results
--        are kept as CSV in results/csv/fa_*.csv: a later run returns less. All times UTC.
-- Run: sqlplus -s ANOMOPS@ORCLPDB1 @false_alarms_queries.sql > fa.out   (blocks are headed "@@ <name>")
set markup csv on quote on
set pagesize 50000 linesize 32767 trimspool on feedback off heading on verify off numwidth 24
set serveroutput off define off
alter session set nls_date_format = 'YYYY-MM-DD HH24:MI:SS';
alter session set nls_timestamp_format = 'YYYY-MM-DD HH24:MI:SS.FF3';
alter session set nls_numeric_characters = '.,';

-- F01 MSET-SPRT's three test-day false-alarm episodes (results/test_results.md section 4) with every detail signal
--     stored for the opening minute and that interval's raw values. LOGONS is "Logons Per Sec" (x 60 = per minute).
prompt @@ fa_f01_openings
select e.episode_no, e.start_ts, e.end_ts, e.top_signals, d.rank, d.signal_code, d.weight, round(d.actual_value, 6) actual_value,
       round(m.logons * 60, 2) logons_per_min, m.sessions, round(m.sql_rt, 4) sql_rt, round(m.w_userio, 2) w_userio,
       round(m.aas, 3) aas, round(m.pio, 1) pio
  from ALERT_EVAL e
  join SCORE_EVAL_DETAIL d on d.run_tag = e.run_tag and d.model_name = e.model_name and d.ts = e.start_ts
  join METRIC_MINUTE m on m.begin_time = e.start_ts
 where e.run_tag = 'EVAL_TEST' and e.detector = 'MSET'
   and e.start_ts in (timestamp '2026-10-06 10:19:31', timestamp '2026-10-06 14:21:30', timestamp '2026-10-06 21:19:30')
 order by e.start_ts, d.rank;

-- F02 every interval from 5 minutes before to 12 minutes after each opening: signals and MSET-SPRT's flag and score
prompt @@ fa_f02_context
select o.opening, m.begin_time, round(m.logons * 60, 2) logons_per_min, m.sessions, round(m.sql_rt, 4) sql_rt,
       round(m.w_userio, 2) w_userio, round(m.aas, 3) aas, round(m.pio, 1) pio, s.flag mset_flag, round(s.score, 3) mset_score
  from (select timestamp '2026-10-06 10:19:31' opening from dual union all select timestamp '2026-10-06 14:21:30' from dual
        union all select timestamp '2026-10-06 21:19:30' from dual) o
  join METRIC_MINUTE m on m.begin_time between o.opening - 5/1440 and o.opening + 12/1440
  left join SCORE_EVAL s on s.run_tag = 'EVAL_TEST' and s.model_name = 'ANOM_MSET_FINAL_202610050007' and s.ts = m.begin_time
 order by o.opening, m.begin_time;

-- F03 profile by minute of hour (the minute of the interval's begin time; intervals begin at second 29-31) for the
--     FINAL training window, the dev day and the test day; every minute within [start - 5 min, end + 30 min] of any
--     run (INCIDENT_TRUTH, any source) is left out, as training does
prompt @@ fa_f03_minute_profile
with chaos as (select start_ts - 5/1440 f, nvl(end_ts, sysdate) + 30/1440 t from INCIDENT_TRUTH),
m as (
  select to_number(to_char(m.begin_time, 'MI')) mi,
         case when m.begin_time >= PKG_ANOM_LIVE.clock('TRAIN_FROM') and m.begin_time < PKG_ANOM_LIVE.clock('TRAIN_TO') then 'TRAIN'
              when m.begin_time >= PKG_ANOM_LIVE.clock('EVAL_FROM') and m.begin_time < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 then 'DEV'
              when m.begin_time >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 and m.begin_time < PKG_ANOM_LIVE.clock('EVAL_TO') then 'TEST' end per,
         m.logons * 60 lpm, m.aas, m.sql_rt, m.sessions, m.pio, m.w_userio
    from METRIC_MINUTE m
   where not exists (select 1 from chaos c where m.begin_time between c.f and c.t))
select per, mi, count(*) n, round(avg(lpm), 2) logons_per_min, round(avg(case when lpm >= 1.5 then 1 else 0 end), 2) share_ge_2,
       round(avg(sessions), 2) sessions, round(avg(aas), 3) aas, round(avg(sql_rt), 4) sql_rt, round(avg(pio), 1) pio,
       round(avg(w_userio), 2) w_userio
  from m where per is not null group by per, mi order by decode(per, 'TRAIN', 1, 'DEV', 2, 3), mi;

-- F04 the application's p95 latency and transaction rate by minute of the hour (APP_MINUTE.TS_MINUTE = the minute's start), same exclusions
prompt @@ fa_f04_app_p95
with chaos as (select start_ts - 5/1440 f, nvl(end_ts, sysdate) + 30/1440 t from INCIDENT_TRUTH),
a as (select to_number(to_char(a.ts_minute, 'MI')) mi,
             case when a.ts_minute >= PKG_ANOM_LIVE.clock('TRAIN_FROM') and a.ts_minute < PKG_ANOM_LIVE.clock('TRAIN_TO') then 'TRAIN'
                  when a.ts_minute >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 and a.ts_minute < PKG_ANOM_LIVE.clock('EVAL_TO') then 'TEST' end per,
             a.app_p95_ms, a.app_tps
        from APP_MINUTE a where not exists (select 1 from chaos c where a.ts_minute between c.f and c.t))
select per, mi, count(*) n, round(avg(app_p95_ms), 1) p95_avg_ms, round(max(app_p95_ms)) p95_max_ms, round(avg(app_tps), 1) app_tps_avg
  from a where per is not null group by per, mi order by decode(per, 'TRAIN', 1, 2), mi;

-- F05 per UTC day: physical reads, and User I/O wait, SQL response time and AAS in minutes :16-:25 against :40-:55
prompt @@ fa_f05_daily
with chaos as (select start_ts - 5/1440 f, nvl(end_ts, sysdate) + 30/1440 t from INCIDENT_TRUTH),
m as (select trunc(m.begin_time) d, to_number(to_char(m.begin_time, 'MI')) mi, m.pio, m.w_userio, m.sql_rt, m.aas, m.logons
        from METRIC_MINUTE m
       where m.begin_time >= timestamp '2026-10-02 00:00:00' and m.begin_time < PKG_ANOM_LIVE.clock('EVAL_TO')
         and not exists (select 1 from chaos c where m.begin_time between c.f and c.t))
select d, count(*) n, round(avg(pio), 1) pio,
       round(avg(case when mi between 16 and 25 then w_userio end), 2) w_userio_16_25, round(avg(case when mi between 40 and 55 then w_userio end), 2) w_userio_40_55,
       round(avg(case when mi between 16 and 25 then sql_rt end), 4) sql_rt_16_25, round(avg(case when mi between 40 and 55 then sql_rt end), 4) sql_rt_40_55,
       round(avg(case when mi between 16 and 25 then aas end), 3) aas_16_25, round(avg(case when mi between 40 and 55 then aas end), 3) aas_40_55,
       round(avg(case when mi = 19 then logons * 60 end), 2) logons_per_min_19, round(avg(case when mi = 21 then logons * 60 end), 2) logons_per_min_21
  from m group by d order by d;

-- F06 the 15-minute logon cluster's drift: excess logons a minute (logons per minute - 1, the reaper) by UTC day and
--     minute of hour mod 15; the fixed hourly minutes :59, :00, :01, :19, :21, :22 and the run windows are left out
prompt @@ fa_f06_autotask_drift
with chaos as (select start_ts - 5/1440 f, nvl(end_ts, sysdate) + 30/1440 t from INCIDENT_TRUTH),
m as (select trunc(m.begin_time) d, mod(to_number(to_char(m.begin_time, 'MI')), 15) r, m.logons * 60 - 1 ex
        from METRIC_MINUTE m
       where m.begin_time >= timestamp '2026-10-02 00:00:00' and m.begin_time < PKG_ANOM_LIVE.clock('EVAL_TO')
         and to_number(to_char(m.begin_time, 'MI')) not in (59, 0, 1, 19, 21, 22)
         and not exists (select 1 from chaos c where m.begin_time between c.f and c.t))
select d, r minute_mod_15, count(*) n, round(avg(ex), 2) excess_logons_per_min from m group by d, r order by d, r;

-- F07 MSET-SPRT (FINAL, live SCORE_MINUTE = the graded scores) in minutes :14-:29 of every dev- and test-day hour:
--     max score, flagged minutes, and the runs whose window or 30-minute cool-down touches those minutes
prompt @@ fa_f07_mset_hourly
with chaos as (select run_id, scenario, intensity, start_ts f, end_ts + 30/1440 t from INCIDENT_TRUTH where source = 'SCHEDULE'),
s as (select trunc(s.ts, 'HH') hh, s.ts, s.score, s.flag from SCORE_MINUTE s
       where s.model_name = 'ANOM_MSET_FINAL_202610050007' and s.ts >= PKG_ANOM_LIVE.clock('EVAL_FROM') and s.ts < PKG_ANOM_LIVE.clock('EVAL_TO')
         and to_number(to_char(s.ts, 'MI')) between 14 and 29)
select case when hh < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 then 'DEV' else 'TEST' end day, hh,
       round(max(score), 2) max_score, count(case when flag = 1 then 1 end) flagged_minutes,
       (select listagg(c.run_id || ' ' || c.scenario || ' ' || c.intensity, '; ') within group (order by c.run_id) from chaos c
         where c.f <= hh + 30/1440 and c.t >= hh + 14/1440) runs_or_cooldown
  from s group by hh order by hh;

-- F08 the observer's own jobs in the test day (start minute:second, failures) and COLLECT_LOG
prompt @@ fa_f08_jobs
select job_name, count(*) runs, count(case when status != 'SUCCEEDED' then 1 end) not_succeeded,
       min(to_char(sys_extract_utc(actual_start_date), 'MI:SS')) first_mmss, max(to_char(sys_extract_utc(actual_start_date), 'MI:SS')) last_mmss,
       count(case when job_name != 'ANOM_SCORE_JOB' and to_number(to_char(sys_extract_utc(actual_start_date), 'MI')) between 10 and 30 then 1 end) started_10_30,
       (select count(*) from COLLECT_LOG l where l.log_ts >= timestamp '2026-10-06 00:27:00' and l.log_ts < timestamp '2026-10-07 00:27:00') collect_log_rows
  from user_scheduler_job_run_details
 where actual_start_date >= timestamp '2026-10-06 00:27:00 +00:00' and actual_start_date < timestamp '2026-10-07 00:27:00 +00:00'
 group by job_name order by job_name;

-- F09 run 122 (conn_leak LOW, 10:33:02-10:50:03): MSET-SPRT's flags and first detail signals, 10:25-11:01
prompt @@ fa_f09_run122
select s.ts, s.flag, round(s.score, 3) score,
       (select listagg(d.signal_code || ' ' || d.weight, ', ') within group (order by d.rank) from SCORE_EVAL_DETAIL d
         where d.run_tag = s.run_tag and d.model_name = s.model_name and d.ts = s.ts and d.rank <= 4) first_details
  from SCORE_EVAL s where s.run_tag = 'EVAL_TEST' and s.model_name = 'ANOM_MSET_FINAL_202610050007'
   and s.ts between timestamp '2026-10-06 10:25:00' and timestamp '2026-10-06 11:01:00' order by s.ts;

-- F10 the static rival's (FINAL, the chosen p99.5) upper thresholds for the signals named above
prompt @@ fa_f10_static_thresholds
select signal_code, round(hi, 6) hi, round(case when signal_code = 'LOGONS' then hi * 60 end, 2) hi_per_min
  from THRESHOLD_DEF where model_name = 'ANOM_STATIC_FINAL_202610050007'
   and signal_code in ('LOGONS', 'SQL_RT', 'W_USERIO', 'AAS', 'APP_P95_MS') order by signal_code;

-- F11 how often the :16 interval (HH:16:30-HH:17:30) shows the User I/O jump: hours whose minutes :14-:18 touch no
--     run window or cool-down; jump = W_USERIO(:16) more than twice W_USERIO(:15) and 0.5 cs/s above it. The interim
--     retraining (ANOM_RETRAIN_INTERIM, BYMINUTE=17 at 00, 06, 12, 18 UTC until TRAIN_TO) is separated out.
prompt @@ fa_f11_jump_hours
with chaos as (select start_ts - 5/1440 f, nvl(end_ts, sysdate) + 30/1440 t from INCIDENT_TRUTH),
h as (select trunc(m.begin_time, 'HH') hh,
             max(case when to_char(m.begin_time, 'MI') = '15' then m.w_userio end) w15, max(case when to_char(m.begin_time, 'MI') = '16' then m.w_userio end) w16,
             max(case when to_char(m.begin_time, 'MI') = '15' then m.pio end) p15, max(case when to_char(m.begin_time, 'MI') = '16' then m.pio end) p16
        from METRIC_MINUTE m
       where m.begin_time >= PKG_ANOM_LIVE.clock('TRAIN_FROM') and m.begin_time < PKG_ANOM_LIVE.clock('EVAL_TO')
         and not exists (select 1 from chaos c where trunc(m.begin_time, 'HH') + 14/1440 <= c.t and trunc(m.begin_time, 'HH') + 19/1440 >= c.f)
       group by trunc(m.begin_time, 'HH'))
select case when hh < PKG_ANOM_LIVE.clock('TRAIN_TO') then 'TRAIN' when hh < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 then 'DEV' else 'TEST' end per,
       count(*) hours, count(case when w16 > 2 * w15 and w16 - w15 > 0.5 then 1 end) hours_with_jump,
       count(case when to_char(hh, 'HH24') in ('00', '06', '12', '18') and hh < PKG_ANOM_LIVE.clock('TRAIN_TO') then 1 end) retrain_hours,
       count(case when not (to_char(hh, 'HH24') in ('00', '06', '12', '18') and hh < PKG_ANOM_LIVE.clock('TRAIN_TO'))
                   and w16 > 2 * w15 and w16 - w15 > 0.5 then 1 end) jump_hours_without_retrain,
       round(avg(p16 / nullif(p15, 0)), 2) pio_ratio_16_to_15, round(avg(w16 / nullif(w15, 0)), 1) w_userio_ratio_16_to_15
  from h where w15 is not null and w16 is not null
 group by case when hh < PKG_ANOM_LIVE.clock('TRAIN_TO') then 'TRAIN' when hh < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 then 'DEV' else 'TEST' end
 order by decode(per, 'TRAIN', 1, 'DEV', 2, 3);

-- A01 what in-memory ASH holds on the target
prompt @@ fa_a01_ash_span
select min(sample_time) ash_from, max(sample_time) ash_to, count(*) samples from v$active_session_history@ANOM_MON_LINK;

-- A02 every ASH sample of a session that is not the load driver, 06-Oct 17:00Z to EVAL_TO; a scenario's own samples
--     (module PKG_CHAOS, action "<scenario> LOW|HIGH") are counted, not listed. Program host names are cut to the
--     process name ("(J000)").
prompt @@ fa_a02_ash_nondriver
select to_char(a.sample_time, 'YYYY-MM-DD HH24:MI:SS') sample_time, nvl(u.username, '#' || a.user_id) username,
       substr(a.session_type, 1, 4) session_type, regexp_replace(a.program, '^oracle@[^ ]+ ', '') program, a.module, a.action,
       a.session_id, a.session_serial#, a.in_connection_mgmt, nvl(a.event, 'ON CPU') event
  from v$active_session_history@ANOM_MON_LINK a left join all_users@ANOM_MON_LINK u on u.user_id = a.user_id
 where a.sample_time >= timestamp '2026-10-06 17:00:00' and a.sample_time < timestamp '2026-10-07 00:27:00'
   and not (nvl(u.username, '-') = 'SHOP_APP' and a.program = 'anomaly-driver')
   and not (a.module = 'PKG_CHAOS' and regexp_like(a.action, ' (LOW|HIGH)$'))
 order by a.sample_time, a.session_id;
prompt @@ fa_a02b_ash_scenario_samples
select a.action, count(*) samples, min(to_char(a.sample_time, 'HH24:MI:SS')) first_s, max(to_char(a.sample_time, 'HH24:MI:SS')) last_s
  from v$active_session_history@ANOM_MON_LINK a
 where a.sample_time >= timestamp '2026-10-06 17:00:00' and a.sample_time < timestamp '2026-10-07 00:27:00'
   and a.module = 'PKG_CHAOS' and regexp_like(a.action, ' (LOW|HIGH)$')
 group by a.action order by 3;

-- A03 the load driver's samples (SHOP_APP, program anomaly-driver) per interval minute of hour (interval = 60 s from
--     second 30), 06-Oct 17:00Z to EVAL_TO, minutes inside any run window or its 30-minute cool-down left out
prompt @@ fa_a03_ash_driver_by_minute
with chaos as (select start_ts - 5/1440 f, nvl(end_ts, sysdate) + 30/1440 t from INCIDENT_TRUTH),
a as (select trunc(cast(a.sample_time as date) - 30/86400, 'MI') ivl, nvl(a.event, 'ON CPU') ev, a.wait_class wc, a.action
        from v$active_session_history@ANOM_MON_LINK a join all_users@ANOM_MON_LINK u on u.user_id = a.user_id
       where u.username = 'SHOP_APP' and a.program = 'anomaly-driver'
         and a.sample_time >= timestamp '2026-10-06 17:00:00' and a.sample_time < timestamp '2026-10-07 00:27:00')
select to_char(ivl, 'MI') interval_minute, count(distinct trunc(ivl, 'HH')) hours, count(*) samples,
       round(count(*) / count(distinct trunc(ivl, 'HH')), 1) samples_per_hour,
       count(case when ev = 'ON CPU' then 1 end) on_cpu, count(case when wc = 'User I/O' then 1 end) user_io,
       count(case when ev = 'log file sync' then 1 end) log_file_sync, count(case when ev = 'resmgr:cpu quantum' then 1 end) resmgr_cpu,
       count(case when action = 'order_status' then 1 end) order_status, count(case when action = 'place_order' then 1 end) place_order
  from a where not exists (select 1 from chaos c where a.ivl + 30/86400 between c.f and c.t)
 group by to_char(ivl, 'MI') order by 1;

-- A04 when the driver's User I/O starts: samples per 15-second slot in HH:15:00-HH:19:59 (all ASH hours pooled), and
--     the first User I/O sample after HH:16:00 in each hour
prompt @@ fa_a04_ash_userio_onset
select to_char(a.sample_time, 'MI') minute, lpad(15 * trunc(to_number(to_char(a.sample_time, 'SS')) / 15), 2, '0') second_slot,
       count(*) user_io_samples, count(distinct trunc(cast(a.sample_time as date), 'HH')) hours
  from v$active_session_history@ANOM_MON_LINK a join all_users@ANOM_MON_LINK u on u.user_id = a.user_id
 where u.username = 'SHOP_APP' and a.program = 'anomaly-driver' and a.wait_class = 'User I/O'
   and a.sample_time >= timestamp '2026-10-06 17:00:00' and a.sample_time < timestamp '2026-10-07 01:00:00'
   and to_number(to_char(a.sample_time, 'MI')) between 15 and 19
 group by to_char(a.sample_time, 'MI'), lpad(15 * trunc(to_number(to_char(a.sample_time, 'SS')) / 15), 2, '0')
 order by 1, 2;
prompt @@ fa_a04b_first_userio_per_hour
select to_char(trunc(cast(a.sample_time as date), 'HH'), 'YYYY-MM-DD HH24') hour_utc, to_char(min(a.sample_time), 'HH24:MI:SS') first_user_io,
       count(*) user_io_samples_16_to_30
  from v$active_session_history@ANOM_MON_LINK a join all_users@ANOM_MON_LINK u on u.user_id = a.user_id
 where u.username = 'SHOP_APP' and a.program = 'anomaly-driver' and a.wait_class = 'User I/O'
   and a.sample_time >= timestamp '2026-10-06 17:00:00' and a.sample_time < timestamp '2026-10-07 01:00:00'
   and to_number(to_char(a.sample_time, 'MI')) between 16 and 30
 group by trunc(cast(a.sample_time as date), 'HH') order by 1;

rollback;
exec dbms_session.close_database_link('ANOM_MON_LINK')
prompt @@ end
