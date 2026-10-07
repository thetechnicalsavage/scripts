#!/usr/bin/env bash
# v1.3 - app 900 (integrator): read-only health check of the whole soak chain: load driver -> oradb1/FREEPDB1
#        (target) -> collector -> ora26ai/ORCLPDB1 (ANOMOPS.METRIC_MINUTE, APP_MINUTE, FEATURE_MINUTE).
#        v1.3: public copy: host name and local-time references removed. v1.2: a trailing gap fails: newest driver minute line, max(APP_MINUTE.ts_minute) and the newest live
#              METRIC_MINUTE row must be at most 2 minutes old. Collector job: RUN_LOOP (92 v1.2) enabled and
#              running or scheduled; it starts once an hour, so "started in the last 3 minutes" is gone.
#              The COLLECT_LOG verdict counts the collector's own ERRORs (the detectors log there too).
#        v1.1: the blocking check counts only sessions blocked by another user session (20 samples over 10 s,
#              fails on a wait of 5 s or more); a commit waiting on LGWR (log file sync) also sets
#              blocking_session and v1.0 counted it as blocking. Target queries run from CDB$ROOT.
#        v1.0: first version, 01-Oct-2026.
#
# Run as : the demo user on the demo VM, from the staging copy (~/app900/tools), with oradb1 and ora26ai running.
# Usage  : tools/anom_soak_check.sh [minutes]          window = the last N whole UTC minutes (1-1440, default 30)
# Prints : 1. driver   - the systemd unit's state and anom_soak_minutes.py's per-minute table and checks
#                        (error rate < 1%, TPS within 25% of the load shape, no missing minute, feed ok,
#                        no ERROR/CRITICAL line, no restart, newest minute ended at most 2 minutes ago);
#          2. observer - per metric interval: the DB signals that show load (AAS, CPU, TXN, commits, executes,
#                        sessions, logons, response time, commit waits) side by side with the FEATURE_MINUTE app
#                        columns; gap checks of METRIC_MINUTE and APP_MINUTE (inside the window and at its
#                        end), the FEATURE_MINUTE join, the collector job and COLLECT_LOG; AAS now and
#                        extrapolated to the peak rate;
#          3. target   - docker stats of oradb1; FREEPDB1 sessions by user/program/status, sessions with a
#                        blocker now (with the blocker's type), and row-lock chains sampled over 10 s.
#          Every check prints "CHECK PASS|FAIL <what>".
# Access : SYSDBA by OS authentication inside the containers (docker exec), queries only: no password is read,
#          nothing is written to either database, no parameter is changed.
# Exit   : 0 every check passed; 1 usage or environment; 3 at least one check failed.
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OBS="${ANOM_OBS_CONTAINER:-ora26ai}"
TGT="${ANOM_TGT_CONTAINER:-oradb1}"
DRIVER_LOG="${ANOM_DRIVER_LOG:-$HOME/anomaly/logs/driver.log}"
DRIVER_INI="${ANOM_DRIVER_INI:-$HOME/anomaly/driver.ini}"
UNIT="anomaly-driver"
MIN="${1:-30}"

log() { printf '%s anom_soak_check %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >&2; }
die() { log ERROR "$*"; exit 1; }

# the window is interpolated into SQL below, so it must be a plain integer in range
if ! [[ "$MIN" =~ ^[0-9]{1,4}$ ]]; then die "minutes must be an integer 1-1440"; fi
MIN=$((10#$MIN))                                   # base 10: a leading zero is not octal
if (( MIN < 1 || MIN > 1440 )); then die "minutes must be an integer 1-1440"; fi
command -v docker >/dev/null 2>&1 || die "docker not found"
command -v python3 >/dev/null 2>&1 || die "python3 not found"
# peak_tps from the installed driver.ini (used to extrapolate AAS to the peak); digits and one dot only
PEAK="$(awk -F '=' '$1 ~ /^[[:space:]]*peak_tps[[:space:]]*$/ { gsub(/[[:space:]]/, "", $2); print $2; exit }' \
        "$DRIVER_INI" 2>/dev/null || true)"
[[ "$PEAK" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "cannot read peak_tps from $DRIVER_INI"

fails=0
section() { printf '\n==== %s ====\n' "$1"; }

# ---------------------------------------------------------------------------------------------------- driver
section "1. driver (window: last $MIN UTC minutes, now $(date -u +%Y-%m-%dT%H:%M:%SZ))"
systemctl --user show "$UNIT" -p ActiveState -p SubState -p UnitFileState -p MainPID -p NRestarts \
  | sed 's/^/  /' || die "systemctl --user show failed"
if [[ "$(systemctl --user is-active "$UNIT" || true)" == "active" ]]; then
  echo "CHECK PASS  driver unit active"
else
  echo "CHECK FAIL  driver unit active"; fails=$((fails + 1))
fi
set +e
python3 -B "$HERE/anom_soak_minutes.py" --log "$DRIVER_LOG" --minutes "$MIN" --peak-tps "$PEAK"
rc=$?
set -e
case $rc in 0) ;; 3) fails=$((fails + 1)) ;; *) die "anom_soak_minutes.py failed (exit $rc)" ;; esac

# ----------------------------------------------------------------------------------------------- observer
section "2. observer: $OBS / ORCLPDB1, ANOMOPS (read only)"
obs_sql() {
  cat <<EOF
connect / as sysdba
alter session set container=ORCLPDB1;
whenever sqlerror exit failure
set linesize 260 pagesize 200 feedback off heading on tab off trimout on verify off
alter session set nls_date_format = 'YYYY-MM-DD HH24:MI:SS';
alter session set nls_timestamp_format = 'YYYY-MM-DD HH24:MI:SS';
column minute_key format a16 heading 'MINUTE_UTC'
column db_begin format a8
column src format a3
-- now, UTC, whatever the container clock's zone
variable since varchar2(19)
begin
  :since := to_char(trunc(cast(sys_extract_utc(systimestamp) as date), 'MI') - ${MIN}/1440, 'YYYY-MM-DD HH24:MI:SS');
end;
/
prompt -- per metric interval: DB signals (per second unless stated) and the FEATURE_MINUTE app columns
prompt -- aas = average active sessions, cpu = CPUs busy, rt_ms = response time per txn (ms), w_commit and
prompt -- w_appl = commit / application (row lock) wait centiseconds per second, enq_s = enqueue waits per
prompt -- second, sess = session count at the end of the interval
select to_char(m.minute_key, 'YYYY-MM-DD HH24:MI') minute_key, to_char(m.begin_time, 'HH24:MI:SS') db_begin,
       m.source src, to_char(m.aas, '0.000') aas, to_char(m.cpu / 100, '0.000') cpu,
       to_char(m.txn, '990.00') txn, to_char(m.commits, '990.00') commits, to_char(m.execs, '9990.0') execs,
       to_char(m.sessions, '990') sess, to_char(m.logons, '90.00') logons, to_char(m.rt_txn * 10, '990.00') rt_ms,
       to_char(m.w_commit, '90.000') w_commit, to_char(m.w_appl, '90.000') w_appl,
       to_char(m.enq_waits, '90.00') enq_s, to_char(f.app_tps, '990.00') app_tps,
       to_char(f.app_p50_ms, '9990.0') app_p50, to_char(f.app_p95_ms, '9990.0') app_p95,
       to_char(f.app_err_pct, '990.00') app_err
  from ANOMOPS.METRIC_MINUTE m
  join ANOMOPS.FEATURE_MINUTE f on f.ts = m.begin_time
 where m.begin_time >= to_date(:since, 'YYYY-MM-DD HH24:MI:SS')
 order by m.begin_time;

prompt
prompt -- gap and join checks over the window
with w as (
  select begin_time, minute_key,
         (begin_time - lag(begin_time) over (order by begin_time)) * 86400 step_s,
         minute_key - lag(minute_key) over (order by begin_time) key_step
    from ANOMOPS.METRIC_MINUTE
   where begin_time >= to_date(:since, 'YYYY-MM-DD HH24:MI:SS'))
select count(*) intervals, min(begin_time) first_begin, max(begin_time) last_begin,
       max(step_s) max_step_s, count(distinct minute_key) keys,
       case when count(*) > 0 and max(nvl(step_s, 60)) <= 90 and count(distinct minute_key) = count(*)
                 and min(nvl(key_step, 1/1440)) = 1/1440 and max(nvl(key_step, 1/1440)) = 1/1440
            then 'CHECK PASS  METRIC_MINUTE gap-free, one key per interval, +1 minute per interval'
            else 'CHECK FAIL  METRIC_MINUTE gap-free, one key per interval, +1 minute per interval' end verdict
  from w;

-- APP_MINUTE: every minute from its first row in the window to its newest row is present
with a as (
  select ts_minute from ANOMOPS.APP_MINUTE where ts_minute >= to_date(:since, 'YYYY-MM-DD HH24:MI:SS')),
r as (select min(ts_minute) lo, max(ts_minute) hi from a)
select (select count(*) from a) app_rows, r.lo first_minute, r.hi last_minute,
       round((r.hi - r.lo) * 1440) + 1 - (select count(*) from a) app_missing,
       case when r.lo is not null and round((r.hi - r.lo) * 1440) + 1 = (select count(*) from a)
            then 'CHECK PASS  APP_MINUTE one row per minute, no gap'
            else 'CHECK FAIL  APP_MINUTE one row per minute, no gap' end verdict
  from r;

-- a trailing gap (the driver or its feed stopped) is not between two rows: the newest minute must have ended
-- at most 120 s ago (the driver writes it about 0.3 s after the minute ends)
select to_char(max(ts_minute), 'YYYY-MM-DD HH24:MI') app_newest_minute,
       round((cast(sys_extract_utc(systimestamp) as date) - (max(ts_minute) + 1 / 1440)) * 86400) app_newest_age_s,
       case when (cast(sys_extract_utc(systimestamp) as date) - (max(ts_minute) + 1 / 1440)) * 86400 <= 120
            then 'CHECK PASS  APP_MINUTE newest minute ended at most 2 minutes ago'
            else 'CHECK FAIL  APP_MINUTE newest minute ended at most 2 minutes ago' end verdict
  from ANOMOPS.APP_MINUTE;

-- the same for the collector: a live row (source L) collected at most 120 s ago (it collects once a minute)
select to_char(max(collected_ts), 'YYYY-MM-DD HH24:MI:SS') metric_newest_live_collect,
       case when max(collected_ts) >= sys_extract_utc(systimestamp) - interval '120' second
            then 'CHECK PASS  METRIC_MINUTE newest live row collected at most 2 minutes ago'
            else 'CHECK FAIL  METRIC_MINUTE newest live row collected at most 2 minutes ago' end verdict
  from ANOMOPS.METRIC_MINUTE where source = 'L';

-- FEATURE_MINUTE: every interval whose app minute is closed and after the driver's first row has app columns
select count(*) closed_intervals, sum(case when f.app_tps is null then 1 else 0 end) app_null,
       case when count(*) > 0 and sum(case when f.app_tps is null then 1 else 0 end) = 0
            then 'CHECK PASS  FEATURE_MINUTE app columns filled for every closed minute since the driver started'
            else 'CHECK FAIL  FEATURE_MINUTE app columns filled for every closed minute since the driver started' end verdict
  from ANOMOPS.METRIC_MINUTE m
  join ANOMOPS.FEATURE_MINUTE f on f.ts = m.begin_time
 where m.begin_time >= to_date(:since, 'YYYY-MM-DD HH24:MI:SS')
   and m.minute_key >= (select min(ts_minute) from ANOMOPS.APP_MINUTE
                         where ts_minute >= to_date(:since, 'YYYY-MM-DD HH24:MI:SS'))
   and m.minute_key <= (select max(ts_minute) from ANOMOPS.APP_MINUTE);

prompt
prompt -- load on the target while the driver runs (intervals joined to an app minute), extrapolated to the peak
prompt -- rate ${PEAK} TPS x 1.05 (the largest jitter): aas_at_peak = mean AAS / mean app TPS x ${PEAK} x 1.05
select count(*) intervals, to_char(avg(m.aas), '0.000') aas_mean, to_char(max(m.aas), '0.000') aas_max,
       to_char(avg(m.cpu) / 100, '0.000') cpu_mean, to_char(avg(f.app_tps), '990.00') tps_mean,
       to_char(avg(m.aas) / nullif(avg(f.app_tps), 0) * ${PEAK} * 1.05, '0.000') aas_at_peak,
       case when count(*) > 0 and max(m.aas) < 0.8
                 and avg(m.aas) / nullif(avg(f.app_tps), 0) * ${PEAK} * 1.05 < 0.8
            then 'CHECK PASS  AAS < 0.8 now and extrapolated to peak'
            else 'CHECK FAIL  AAS < 0.8 now and extrapolated to peak' end verdict
  from ANOMOPS.METRIC_MINUTE m
  join ANOMOPS.FEATURE_MINUTE f on f.ts = m.begin_time
 where m.begin_time >= to_date(:since, 'YYYY-MM-DD HH24:MI:SS')
   and f.app_tps > 0;

prompt
prompt -- collector job and its log. 92 v1.2: one run an hour (PKG_ANOM_COLLECT.RUN_LOOP) collects every minute
prompt -- in one session; between two runs (about HH:59:50) the job is SCHEDULED for a moment
column job_name format a18
column job_action format a26
column state format a10
select job_name, job_action, state, enabled, run_count, failure_count,
       to_char(sys_extract_utc(last_start_date), 'HH24:MI:SS') last_start_utc,
       case when enabled = 'TRUE' and state in ('SCHEDULED', 'RUNNING')
                 and job_action = 'PKG_ANOM_COLLECT.RUN_LOOP'
            then 'CHECK PASS  collector job enabled, RUN_LOOP, running or scheduled'
            else 'CHECK FAIL  collector job enabled, RUN_LOOP, running or scheduled' end verdict
  from dba_scheduler_jobs where owner = 'ANOMOPS' and job_name = 'ANOM_COLLECT_JOB';
select count(*) failed_runs,
       case when count(*) = 0 then 'CHECK PASS  no failed collector run in the window'
            else 'CHECK FAIL  no failed collector run in the window' end verdict
  from dba_scheduler_job_run_details
 where owner = 'ANOMOPS' and job_name = 'ANOM_COLLECT_JOB' and status != 'SUCCEEDED'
   and sys_extract_utc(log_date) >= to_timestamp(:since, 'YYYY-MM-DD HH24:MI:SS');
column message format a120
select to_char(log_ts, 'HH24:MI:SS') log_ts_utc, severity, substr(message, 1, 120) message
  from ANOMOPS.COLLECT_LOG
 where log_ts >= to_timestamp(:since, 'YYYY-MM-DD HH24:MI:SS') and severity in ('WARN', 'ERROR')
 order by log_ts;
-- COLLECT_LOG is shared with the phase 4 detectors: every WARN/ERROR is listed above, the verdict counts the
-- collector's own failures (event=collect_failed: collect, backfill, run_loop)
select count(*) collect_errors,
       case when count(*) = 0 then 'CHECK PASS  no collector ERROR in COLLECT_LOG in the window'
            else 'CHECK FAIL  no collector ERROR in COLLECT_LOG in the window' end verdict
  from ANOMOPS.COLLECT_LOG
 where log_ts >= to_timestamp(:since, 'YYYY-MM-DD HH24:MI:SS') and severity = 'ERROR'
   and message like 'event=collect_failed %';
exit
EOF
}
out="$(obs_sql | docker exec -i "$OBS" bash -lc "sqlplus -s -L /nolog" 2>&1)" || { echo "$out"; die "observer queries failed"; }
echo "$out"
if grep -q 'CHECK FAIL' <<<"$out"; then fails=$((fails + 1)); fi

# ------------------------------------------------------------------------------------------------- target
section "3. target: $TGT / FREEPDB1 (read only)"
docker stats --no-stream --format '  {{.Name}}: cpu {{.CPUPerc}} (100% = 1 core), mem {{.MemUsage}}' "$TGT" \
  || die "docker stats failed"
tgt_sql() {
  # from CDB$ROOT, filtered on FREEPDB1's con_id: no container switch, and background blockers (LGWR) are visible
  cat <<'EOF'
whenever sqlerror exit failure
set linesize 260 pagesize 200 feedback off heading on tab off trimout on verify off serveroutput on
column username format a12
column program format a28
column module format a16
column event format a32
column blocker format a26
variable con number
begin select con_id into :con from v$pdbs where name = 'FREEPDB1'; end;
/
prompt -- user sessions in FREEPDB1
select username, substr(program, 1, 28) program, status, count(*) sessions
  from v$session where type = 'USER' and con_id = :con
 group by username, substr(program, 1, 28), status order by 1, 2, 3;
prompt -- FREEPDB1 sessions that have a blocker now; a commit waiting on LGWR (log file sync) is listed but is
prompt -- not one session blocking another
select s.sid, s.username, substr(s.module, 1, 16) module, substr(s.event, 1, 32) event, s.seconds_in_wait,
       s.blocking_session, b.type || ' ' || substr(b.program, instr(b.program, '('), 18) blocker
  from v$session s left join v$session b on b.sid = s.blocking_session
 where s.type = 'USER' and s.con_id = :con and s.blocking_session is not null
 order by s.seconds_in_wait desc fetch first 10 rows only;
prompt -- 20 samples, 0.5 s apart: FREEPDB1 user sessions blocked by another user session (row locks)
declare
  n_blocked pls_integer;
  n_hits    pls_integer := 0;
  max_wait  number := 0;
  w         number;
begin
  for i in 1 .. 20 loop
    select count(*), nvl(max(s.seconds_in_wait), 0) into n_blocked, w
      from v$session s join v$session b on b.sid = s.blocking_session
     where s.type = 'USER' and s.con_id = :con and b.type = 'USER';
    n_hits := n_hits + n_blocked;
    max_wait := greatest(max_wait, w);
    dbms_session.sleep(0.5);
  end loop;
  dbms_output.put_line('user-on-user blocked observations ' || n_hits || ' in 20 samples, longest wait '
                       || max_wait || ' s');
  -- a sub-second wait on a hot inventory row is ordinary OLTP; a lock held for 5 s or more is not
  dbms_output.put_line(case when max_wait < 5
                            then 'CHECK PASS  no row-lock chain of 5 s or more in FREEPDB1 (10 s sample)'
                            else 'CHECK FAIL  no row-lock chain of 5 s or more in FREEPDB1 (10 s sample)' end);
end;
/
exit
EOF
}
out="$(tgt_sql | docker exec -i "$TGT" bash -lc "sqlplus -s -L / as sysdba" 2>&1)" || { echo "$out"; die "target queries failed"; }
echo "$out"
if grep -q 'CHECK FAIL' <<<"$out"; then fails=$((fails + 1)); fi

section "result"
if (( fails == 0 )); then echo "ALL CHECKS PASSED"; exit 0; fi
echo "FAILED SECTIONS: $fails"
exit 3
