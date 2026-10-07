#!/usr/bin/env bash
# v1.1 - app 900 (phase 4 integrator): the first end-to-end live detection test. Starts three chaos scenarios on the
#        target through ANOM_CHAOS_LINK with source 'UI' (tools/anom_live_start.sql, the Control Center's call), each
#        followed by its own minutes plus a quiet gap, then waits for the scoring job and writes the detection report
#        (tools/anom_live_report.sql) for the whole test.
#        Default sequence: cpu_hog LOW 6 min, plan_regression HIGH 6 min, blocking_chain LOW 6 min, 10 quiet minutes
#        after each run's end.
#        v1.0: first version, 01-Oct-2026. v1.1: public copy: host name and local-time references removed.
#
# Run as : the demo user on the demo VM, from the staging copy (~/app900), after tools/anom_obs_run.sh stage, with the
#          INTERIM models ACTIVE and ANOM_SCORE_JOB enabled (tools/anom_live_interim.sql).
# Usage  : tools/anom_live_test.sh [scenario:minutes:intensity ...]
#          ANOM_LIVE_QUIET=<seconds> quiet time after each run's end (default 600); ANOM_LIVE_TAIL=<seconds> wait after
#          the last quiet gap before the report (default 120: the scoring job's lag).
# Logs   : ~/app900/logs/live_<UTC stamp>_*.log (each secret-scanned by the runner) and live_<stamp>.summary.
# Exit   : 0 done; 1 usage or environment; 2 a start or the report failed (the runner's log says why).
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="${ANOM_STAGE:-$HOME/app900}"
QUIET="${ANOM_LIVE_QUIET:-600}"
TAIL="${ANOM_LIVE_TAIL:-120}"
SCENARIOS=" blocking_chain plan_regression slow_drift batch_wrong_time hard_parse_storm commit_storm io_storm cpu_hog \
temp_spill conn_leak logon_storm app_error_burst "

log() { printf '%s anom_live_test %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" | tee -a "$summary" >&2; }
die() { log ERROR "$*"; exit 1; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$STAGE/logs"
summary="$STAGE/logs/live_${stamp}.summary"

[[ "$QUIET" =~ ^[0-9]{1,4}$ ]] || die "ANOM_LIVE_QUIET must be a whole number of seconds"
[[ "$TAIL" =~ ^[0-9]{1,4}$ ]] || die "ANOM_LIVE_TAIL must be a whole number of seconds"
seq=("$@")
[[ ${#seq[@]} -gt 0 ]] || seq=(cpu_hog:6:LOW plan_regression:6:HIGH blocking_chain:6:LOW)
for item in "${seq[@]}"; do
  IFS=: read -r sc mins inten <<<"$item"
  [[ "$SCENARIOS" == *" $sc "* ]] || die "unknown scenario in $item"
  [[ "$mins" =~ ^[0-9]{1,2}$ ]] || die "minutes must be a whole number in $item"
  [[ "$inten" == LOW || "$inten" == HIGH ]] || die "intensity must be LOW or HIGH in $item"
done

first=""
for item in "${seq[@]}"; do
  IFS=: read -r sc mins inten <<<"$item"
  name="live_${stamp}_start_${sc}"
  log INFO "starting $sc $inten for $mins min"
  rc=0
  "$HERE/anom_obs_run.sh" run anom_live_start.sql "$name" -- "$sc" "$mins" "$inten" > /dev/null 2>&1 || rc=$?
  line="$(grep -E '^RUN_ID=' "$STAGE/logs/$name.log" 2>/dev/null || true)"
  [[ $rc -eq 0 && -n "$line" ]] || { log ERROR "start of $sc failed (rc=$rc); see $STAGE/logs/$name.log"; exit 2; }
  log INFO "$line $(grep -E '^RUN_START=' "$STAGE/logs/$name.log" || true)"
  [[ -n "$first" ]] || first="$(date -u -d '-15 minutes' +%Y-%m-%dT%H:%M)"
  # the run ends itself after its minutes (CHAOS_REAPER is the backstop); then the quiet gap
  sleep $(( mins * 60 + 15 + QUIET ))
done
sleep "$TAIL"

to="$(date -u -d '+1 minute' +%Y-%m-%dT%H:%M)"
name="live_${stamp}_report"
rc=0
"$HERE/anom_obs_run.sh" run anom_live_report.sql "$name" -- "$first" "$to" > /dev/null 2>&1 || rc=$?
[[ $rc -eq 0 ]] || { log ERROR "the report failed (rc=$rc); see $STAGE/logs/$name.log"; exit 2; }
log INFO "done: report $STAGE/logs/$name.log (window $first to $to UTC)"
