-- v1.0 - app 900 (phase 4 integrator): what the live detectors did in a time window, in ora26ai / ORCLPDB1 as
--        ANOMOPS: tools/anom_obs_run.sh run anom_live_report.sql <log> -- <from> <to>   (UTC, YYYY-MM-DDTHH24:MI).
--        Read-only apart from one INCIDENT_TRUTH refresh (the copy of the target's CHAOS_RUN). For every chaos run
--        that starts in the window: each live model's first alert episode that opens in [run start, run end + 10 min]
--        (the PLAN.md section 7 catch rule), its time to detect (alert minute - run start) and its page time
--        (opened_ts - run start), the signals it names (MSET: rank, weight, value at the opening minute; STATIC and
--        SEASONAL: the signal that fired), the per-minute flags, and the STATIC/SEASONAL breach lists. Then every
--        episode that opened outside all chaos runs and their 30-minute cool-down (the false alarms).
--        Arguments are not secrets; verify is off all the same.
--        v1.0: first version, 01-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 250 pagesize 500 trimspool on
whenever sqlerror exit failure rollback

define a_from = "&1"
define a_to = "&2"

variable v_from varchar2(20)
variable v_to varchar2(20)
begin
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, 'anom_live_report: run as ANOMOPS');
  end if;
  if not regexp_like('&a_from', '^\d{4}-\d\d-\d\dT\d\d:\d\d$') or not regexp_like('&a_to', '^\d{4}-\d\d-\d\dT\d\d:\d\d$') then
    raise_application_error(-20900, 'anom_live_report: from and to are YYYY-MM-DDTHH24:MI (UTC)');
  end if;
  :v_from := '&a_from';
  :v_to := '&a_to';
  begin
    PKG_ANOM_TRAIN.refresh_truth;
  exception
    when others then
      dbms_output.put_line('WARN INCIDENT_TRUTH not refreshed ('||sqlerrm||'); the copy may be up to an hour old');
  end;
  dbms_output.put_line('window '||:v_from||'Z to '||:v_to||'Z, report at '
                       ||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
/

prompt
prompt == live models (ACTIVE now, or scored in the window)
col model_name format a40
col win format a25
select r.model_name, r.detector, r.status, r.n_rows,
       to_char(r.train_from, 'MM-DD HH24:MI')||' - '||to_char(r.train_to, 'MM-DD HH24:MI') win
  from MODEL_REGISTRY r
 where r.status = 'ACTIVE'
    or r.model_name in (select model_name from SCORE_MINUTE
                         where ts >= to_date(:v_from, 'YYYY-MM-DD"T"HH24:MI') and ts < to_date(:v_to, 'YYYY-MM-DD"T"HH24:MI'))
 order by r.detector, r.created_ts;

prompt
prompt == chaos runs starting in the window (INCIDENT_TRUTH)
col scenario format a17
col src format a4
select run_id, scenario, intensity, substr(source, 1, 4) src, to_char(start_ts, 'HH24:MI:SS') started,
       to_char(end_ts, 'HH24:MI:SS') ended, round((end_ts - start_ts) * 1440, 1) minutes, status, restored
  from INCIDENT_TRUTH
 where start_ts >= to_date(:v_from, 'YYYY-MM-DD"T"HH24:MI') and start_ts < to_date(:v_to, 'YYYY-MM-DD"T"HH24:MI')
 order by run_id;

prompt
prompt == detection per run: the first episode of each live model that opens in [run start, run end + 10 min]
declare
  l_from date := to_date(:v_from, 'YYYY-MM-DD"T"HH24:MI');
  l_to   date := to_date(:v_to, 'YYYY-MM-DD"T"HH24:MI');
  l_any   boolean;
  l_open  varchar2(400);
  l_first date;
  l_nflag number;
  l_nmin  number;
  l_mts   date;
  l_peak  date;
  l_how   varchar2(60);
begin
  for i in (select run_id, scenario, intensity, start_ts s, nvl(end_ts, planned_end_ts) e
              from INCIDENT_TRUTH where start_ts >= l_from and start_ts < l_to order by run_id) loop
    dbms_output.put_line(chr(10)||'run '||i.run_id||' '||i.scenario||' '||i.intensity||'  '
                         ||to_char(i.s, 'HH24:MI:SS')||' - '||to_char(i.e, 'HH24:MI:SS')||' UTC');
    dbms_output.put_line('  '||rpad('detector', 9)||rpad('alert minute', 13)||rpad('ttd min', 9)||rpad('paged after', 12)
                         ||rpad('peak', 9)||'signals named (fired for STATIC/SEASONAL)');
    -- the models that scored the run's minutes (a model retired before the run is not listed)
    for m in (select distinct s.model_name, s.detector from SCORE_MINUTE s
               where s.ts > i.s - 1 / 1440 and s.ts < i.e order by s.detector) loop
      l_any := false;
      for a in (select start_ts, opened_ts, peak_score, top_signals, fired from ALERT
                 where model_name = m.model_name and start_ts >= i.s and start_ts <= i.e + 10 / 1440
                 order by start_ts fetch first 1 row only) loop
        l_any := true;
        dbms_output.put_line('  '||rpad(m.detector, 9)||rpad(to_char(a.start_ts, 'HH24:MI:SS'), 13)
                             ||rpad(to_char(round((a.start_ts - i.s) * 1440, 1), 'FM990.0'), 9)
                             ||rpad(to_char(round((cast(a.opened_ts as date) - i.s) * 1440, 1), 'FM990.0')||' min', 12)
                             ||rpad(to_char(round(a.peak_score, 3)), 9)
                             ||case when m.detector in ('STATIC', 'SEASONAL') then 'fired '||a.fired||'; top '
                                    else '' end||a.top_signals);
      end loop;
      if not l_any then
        -- an episode that opened before the run and was still open cannot catch it (section 7), but is shown
        select case when count(*) > 0 then ' (an episode opened before the run was still open: '
                    ||listagg(to_char(start_ts, 'HH24:MI'), ',') within group (order by start_ts)||')' end
          into l_open from ALERT
         where model_name = m.model_name and start_ts < i.s and nvl(end_ts, sysdate + 1) >= i.s;
        dbms_output.put_line('  '||rpad(m.detector, 9)||'no alert opened in the window'||l_open);
      end if;
      -- the minute-level view, whatever the episodes did: the first flagged minute whose interval overlaps the run
      -- (a minute ts covers [ts, ts + 60 s)) and how many of the run's minutes were flagged
      select min(case when flag = 1 then ts end), count(case when flag = 1 then 1 end), count(*)
        into l_first, l_nflag, l_nmin
        from SCORE_MINUTE where model_name = m.model_name and ts > i.s - 1 / 1440 and ts < i.e;
      dbms_output.put_line('  '||rpad(' ', 9)||'first flagged minute '
                           ||nvl(to_char(l_first, 'HH24:MI:SS')||' ('||to_char(round((l_first + 1 / 1440 - i.s) * 1440, 1),
                                 'FM990.0')||' min after the start, at the minute''s end)', 'none')
                           ||'; '||l_nflag||' of '||l_nmin||' run minutes flagged');
    end loop;
    -- MSET's attribution: at its alert's opening minute (or, when no episode opened, its first flagged minute of the
    -- run) and at its strongest flagged minute of the run (the highest P(anomalous))
    select min(a.start_ts) into l_mts from ALERT a
     where a.detector = 'MSET' and a.start_ts >= i.s and a.start_ts <= i.e + 10 / 1440;
    l_how := 'alert opening minute';
    if l_mts is null then
      select min(ts) into l_mts from SCORE_MINUTE
       where detector = 'MSET' and flag = 1 and ts > i.s - 1 / 1440 and ts < i.e;
      l_how := 'first flagged minute of the run (no new episode)';
    end if;
    select max(ts) keep (dense_rank first order by score desc, ts) into l_peak from SCORE_MINUTE
     where detector = 'MSET' and flag = 1 and ts > i.s - 1 / 1440 and ts < i.e;
    for t in (select l_mts ts, l_how how from dual where l_mts is not null
              union all
              select l_peak, 'strongest flagged minute of the run' from dual where l_peak is not null and l_peak != l_mts) loop
      dbms_output.put_line('  MSET signals at '||to_char(t.ts, 'HH24:MI')||' ('||t.how||'):');
      for d in (select x.rank, x.signal_code, x.weight, x.actual_value from SCORE_DETAIL x
                 where x.ts = t.ts and x.model_name in (select model_name from SCORE_MINUTE
                                                         where ts = t.ts and detector = 'MSET')
                 order by x.rank) loop
        dbms_output.put_line('    #'||d.rank||' '||rpad(d.signal_code, 12)||'weight '
                             ||rpad(to_char(round(d.weight, 3), 'FM0.000'), 7)||' value '||round(d.actual_value, 3));
      end loop;
    end loop;
  end loop;
end;
/

prompt
prompt == per-minute flags around each run (1 flagged, 0 normal, . not scorable, blank no row); MSET score = P(anomalous)
col ts format a8
col mset format a5
col run format a5
col svm format a4
col em format a4
col pca format a4
col stat format a5
col seas format a5
col mset_p format a7
col breached_static format a60
col breached_seasonal format a60
with runs as (
  select run_id, start_ts s, nvl(end_ts, planned_end_ts) e from INCIDENT_TRUTH
   where start_ts >= to_date(:v_from, 'YYYY-MM-DD"T"HH24:MI') and start_ts < to_date(:v_to, 'YYYY-MM-DD"T"HH24:MI')),
mins as (
  select distinct s.ts from SCORE_MINUTE s join runs r on s.ts >= r.s - 3 / 1440 and s.ts <= r.e + 12 / 1440),
f as (
  select m.ts, s.detector, s.flag, s.score, s.breached from mins m join SCORE_MINUTE s on s.ts = m.ts)
select to_char(m.ts, 'HH24:MI') ts,
       (select listagg(r.run_id, ',') within group (order by r.run_id) from runs r where m.ts >= r.s - 1 / 1440
           and m.ts <= r.e) run,
       max(case when f.detector = 'MSET' then nvl(to_char(f.flag), '.') end) mset,
       max(case when f.detector = 'MSET' then to_char(round(f.score, 3), 'FM0.000') end) mset_p,
       max(case when f.detector = 'SVM' then nvl(to_char(f.flag), '.') end) svm,
       max(case when f.detector = 'EM' then nvl(to_char(f.flag), '.') end) em,
       max(case when f.detector = 'PCA' then nvl(to_char(f.flag), '.') end) pca,
       max(case when f.detector = 'STATIC' then nvl(to_char(f.flag), '.') end) stat,
       max(case when f.detector = 'SEASONAL' then nvl(to_char(f.flag), '.') end) seas,
       max(case when f.detector = 'STATIC' then substr(f.breached, 1, 60) end) breached_static,
       max(case when f.detector = 'SEASONAL' then substr(f.breached, 1, 60) end) breached_seasonal
  from mins m left join f on f.ts = m.ts
 group by m.ts order by m.ts;

prompt
prompt == every alert episode that opened in the window (chaos = inside a run's [start, end + 30 min])
col model_name format a40
col top_signals format a50
col fired format a20
select a.detector, to_char(a.start_ts, 'MM-DD HH24:MI') opened, to_char(a.last_ts, 'HH24:MI') last,
       nvl(to_char(a.end_ts, 'HH24:MI'), a.status) closed,
       case when exists (select 1 from INCIDENT_TRUTH t
                          where a.start_ts >= t.start_ts and a.start_ts <= nvl(t.end_ts, t.planned_end_ts) + 30 / 1440)
            then 'chaos' else 'FALSE ALARM' end kind,
       round(a.peak_score, 3) peak, a.fired, a.top_signals
  from ALERT a
 where a.start_ts >= to_date(:v_from, 'YYYY-MM-DD"T"HH24:MI') and a.start_ts < to_date(:v_to, 'YYYY-MM-DD"T"HH24:MI')
 order by a.start_ts, a.detector;

prompt
prompt == false alarms per detector in the window (episodes outside every chaos run and its 30-min cool-down)
select r.detector, count(a.alert_id) false_alarms,
       round(count(a.alert_id) * 24 / ((to_date(:v_to, 'YYYY-MM-DD"T"HH24:MI') - to_date(:v_from, 'YYYY-MM-DD"T"HH24:MI')) * 24), 1) per_24h
  from (select distinct detector from MODEL_REGISTRY where status = 'ACTIVE') r
  left join ALERT a on a.detector = r.detector
        and a.start_ts >= to_date(:v_from, 'YYYY-MM-DD"T"HH24:MI') and a.start_ts < to_date(:v_to, 'YYYY-MM-DD"T"HH24:MI')
        and not exists (select 1 from INCIDENT_TRUTH t
                         where a.start_ts >= t.start_ts and a.start_ts <= nvl(t.end_ts, t.planned_end_ts) + 30 / 1440)
 group by r.detector order by r.detector;

prompt
prompt == scoring coverage in the window (minutes scored, flagged, not scorable)
select detector, count(*) minutes, count(case when flag = 1 then 1 end) flagged, count(case when flag is null then 1 end) not_scorable
  from SCORE_MINUTE
 where ts >= to_date(:v_from, 'YYYY-MM-DD"T"HH24:MI') and ts < to_date(:v_to, 'YYYY-MM-DD"T"HH24:MI')
 group by detector order by detector;
undefine a_from
undefine a_to
