#!/usr/bin/env bash
# v1.2 - app 900 chaos test sequence (builder C, phase 4): runs test/anom_test_chaos.sql part by part as SHOP (and
#        test/anom_test_chaos_ctl.sql as CHAOS_CTL after "basic") through tools/anom_chaos_run.sh, with a gap after
#        every part that injects, so the incidents on the soak stay apart. Prints one summary line per part.
#        v1.2: public copy: host name and local-time references removed. v1.1: (phase 4b) the parts dispatch and plan (93 v1.2's dispatchers, STOP flag and CHAOS_PLAN) and the HIGH
#              smoke parts high_<scenario> (contract section 10); "high" runs the 11 of them and cpu_hog_high, in
#              the contract's order. 01-Oct-2026.
#        v1.0: first version, 01-Oct-2026.
#
# Run as : the demo user on the demo VM, from the staging copy (~/app900), after tools/anom_chaos_run.sh stage.
# Usage  : tools/anom_chaos_test.sh [part ...]       default: every part of ALL, in the order below
#          tools/anom_chaos_test.sh high               the HIGH smoke tests (HIGH below), 3-minute gaps
#          ANOM_CHAOS_GAP=<seconds> sets the gap after an injecting part (default 180, the brief's 3 minutes).
# Logs   : ~/app900/logs/chaos_<UTC stamp>_<part>.log (secret-scanned by the runner) and chaos_<stamp>.summary.
# Exit   : 0 every part passed; 1 usage or environment; 2 at least one part failed.
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="${ANOM_STAGE:-$HOME/app900}"
GAP="${ANOM_CHAOS_GAP:-180}"
ALL=(basic ctl dispatch plan blocking_chain plan_regression slow_drift batch_wrong_time hard_parse_storm commit_storm
     io_storm cpu_hog temp_spill conn_leak logon_storm app_error_burst cpu_hog_high stop_all blocking_chain_error
     reaper_orphan reaper_overstay reaper_repair final)
HIGH=(high_blocking_chain high_plan_regression high_slow_drift high_batch_wrong_time high_hard_parse_storm
      high_commit_storm high_io_storm cpu_hog_high high_temp_spill high_conn_leak high_logon_storm high_app_error_burst)

log() { printf '%s anom_chaos_test %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >&2; }
die() { log ERROR "$*"; exit 1; }

[[ "$GAP" =~ ^[0-9]{1,4}$ ]] || die "ANOM_CHAOS_GAP must be a whole number of seconds"
parts=("$@")
[[ ${#parts[@]} -gt 0 ]] || parts=("${ALL[@]}")
[[ "${parts[*]}" == "high" ]] && parts=("${HIGH[@]}")
for p in "${parts[@]}"; do
  printf '%s\n' "${ALL[@]}" "${HIGH[@]}" | grep -qxF -- "$p" || die "unknown part: $p"
done

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$STAGE/logs"
summary="$STAGE/logs/chaos_${stamp}.summary"
failed=0
n=${#parts[@]}
i=0
for p in "${parts[@]}"; do
  i=$((i + 1))
  name="chaos_${stamp}_${p}"
  log INFO "part $i/$n $p start"
  rc=0
  if [[ "$p" == ctl ]]; then
    "$HERE/anom_chaos_run.sh" run anom_test_chaos_ctl.sql "$name" --as CHAOS_CTL > /dev/null 2>&1 || rc=$?
  else
    "$HERE/anom_chaos_run.sh" run anom_test_chaos.sql "$name" -- "$p" > /dev/null 2>&1 || rc=$?
  fi
  counts="$(grep -oE '[0-9]+ passed, [0-9]+ failed' "$STAGE/logs/$name.log" 2>/dev/null | tail -n 1 || true)"
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $p rc=$rc ${counts:-no counts (log missing or shredded)}"
  printf '%s\n' "$line" | tee -a "$summary"
  if [[ $rc -ne 0 ]]; then failed=$((failed + 1)); fi
  case "$p" in
    basic|ctl|final) ;;
    *) if [[ $i -lt $n ]]; then log INFO "gap of $GAP s after $p"; sleep "$GAP"; fi ;;
  esac
done
log INFO "done: $n part(s), $failed failed; summary $summary"
[[ $failed -eq 0 ]] || exit 2
