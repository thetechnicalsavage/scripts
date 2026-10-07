#!/usr/bin/env bash
# v1.9 - app 900 observer runner (builder O, phase 3): stages the observer scripts into the ora26ai container
#        and runs one of them in ORCLPDB1 as ANOMOPS, the password on SQL*Plus's standard input.
#        v1.9: public copy: host name and local-time references removed; the secrets file default is a placeholder. v1.8: phase 5d: also stages test/anom_test_phase5d.sql (the clean-up covers it through
#              /tmp/app900/anom_*.sql; tools/anom_gap_deploy.sql is staged by tools/anom_*.sql already); the usage
#              text printed without arguments runs to the Exit lines again (2,41p).
#        v1.7: phase 5c: also stages test/anom_test_phase5c.sql (the clean-up covers it through
#              /tmp/app900/anom_*.sql; tools/anom_train_exclude.sql is staged by tools/anom_*.sql already).
#        v1.6: phase 4b: also stages test/anom_test_phase4b.sql and test/anom_test_chaos_link.sql (the clean-up
#              already covers both through /tmp/app900/anom_*.sql).
#        v1.5: phase 4 integrator: also stages sql/98 (the live-model jobs) and test/anom_test_live.sql (the clean-up
#              already covers both: 98 by its own pattern, the test by /tmp/app900/anom_*.sql).
#        v1.4: phase 4 (builder M): stages and cleans sql/94-96 and test/anom_test_phase4.sql; --pwd also takes
#              CHAOS_CTL_PWD (91 v1.1 creates ANOM_CHAOS_LINK).
#        v1.3: secret_value applies the allowlist in awk before bash holds the value (bash drops NUL
#              bytes); scan patterns are also cut at NUL bytes (Codex confirmation, 01-Oct-2026).
#        v1.2: secrets block shared with anom_collector_cost.sh: lines cut as Python cuts them, literal "export ",
#              and a scan pattern that is a piece of every value the driver can read (Codex re-review, 01-Oct).
#        v1.1: the secrets file is read the way the load driver reads it (optional "export ", blanks, one pair of
#              matching quotes); the log scan also searches those values, so a quoted entry can no longer hide
#              a leaked password from it (review finding, 01-Oct-2026).
#        v1.0: first version, 01-Oct-2026.
#
# Run as : the demo user on the demo VM, from the staging copy (~/app900), with ora26ai running.
# Usage  : tools/anom_obs_run.sh stage                       # copy sql/9[0-24-6]*.sql, sql/98*.sql, the observer tests, tools/*.sql in
#          tools/anom_obs_run.sh run <file.sql> <log> [--pwd KEY ...] [-- arg ...]
#                                                            # run /tmp/app900/<file.sql> as ANOMOPS
#          tools/anom_obs_run.sh clean                       # remove the staged copies from the container
#          tools/anom_obs_run.sh scan <logfile>              # secret check of any log (shreds on a hit)
#          --pwd KEY (ANOM_MON_PWD, ANOM_FEED_PWD or CHAOS_CTL_PWD, repeatable) passes that password as the
#          script's next positional argument: 91 takes ANOM_MON_PWD then CHAOS_CTL_PWD,
#          test/anom_test_phase3.sql takes ANOM_FEED_PWD.
#          Plain arguments after "--" follow them (they must not be secrets).
#
# Secrets: read from the secrets file (ANOM_SECRETS; the default <secrets-file> is a placeholder) into unexported
#          shell variables and written to SQL*Plus's standard input with the printf builtin: never on a command
#          line, never in an environment, never printed. Every script that receives one has "set verify off".
#          After the run the log is searched for every value in the secrets file; a log that holds one is
#          shredded and the run fails (exit 3). The log is printed only after that check.
# Exit   : 0 ok; 1 usage or environment; 2 the SQL script failed (non-zero exit, ORA- under WHENEVER, or SP2-);
#          3 a secret was found in the log (log shredded).
set -euo pipefail
umask 077

STAGE="${ANOM_STAGE:-$HOME/app900}"
SECRETS="${ANOM_SECRETS:-<secrets-file>}"
CONTAINER="${ANOM_OBS_CONTAINER:-ora26ai}"
CDIR="/tmp/app900"
SERVICE="//localhost:1521/orclpdb1"
LOGDIR="$STAGE/logs"

log() { printf '%s anom_obs_run %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >&2; }
die() { log ERROR "$*"; exit 1; }

# ---- the secrets file, read the way the load driver reads it (anomaly_driver.load_secrets). Identical block in
#      anom_obs_run.sh and anom_collector_cost.sh; test/test_observer_static.py checks both against the driver.
# The file cut into lines where Python's str.splitlines() cuts it (\n \r \v \f \x1c-\x1e U+0085 U+2028
# U+2029), byte-wise. Values only ever travel through pipes.
secrets_lines() {
  LC_ALL=C sed -e 's/\xc2\x85/\n/g' -e 's/\xe2\x80\xa8/\n/g' -e 's/\xe2\x80\xa9/\n/g' "$SECRETS" \
    | LC_ALL=C tr '\r\v\f\034\035\036' '\n'   # GNU tr repeats the last \n for the whole set
}

# KEY<TAB>VALUE per line: blank and '#' lines skipped, a literal "export " prefix, key and value trimmed, one
# pair of matching quotes removed. Only ASCII blanks are trimmed: where the driver also strips other
# whitespace, that character stays in the value here and secret_value refuses it (exact or refused, never a
# different password).
parse_secrets() {
  secrets_lines | LC_ALL=C awk '{
    line = $0
    sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line)
    if (line == "" || substr(line, 1, 1) == "#") next
    if (substr(line, 1, 7) == "export ") { line = substr(line, 8); sub(/^[[:space:]]+/, "", line) }
    eq = index(line, "=")
    if (eq < 2) next
    k = substr(line, 1, eq - 1); v = substr(line, eq + 1)
    sub(/[[:space:]]+$/, "", k); sub(/^[[:space:]]+/, "", v)
    q = substr(v, 1, 1)
    if (length(v) >= 2 && (q == "\"" || q == "\047") && substr(v, length(v), 1) == q) v = substr(v, 2, length(v) - 2)
    printf "%s\t%s\n", k, v
  }'
}

# one key's value (the last line for the key wins, as in the driver). The allowlist (the value goes inside a
# double-quoted SQL*Plus connect string or argument) is applied by awk to the raw bytes, before bash holds the
# value: bash would silently drop a NUL byte and could return a different password instead of refusing it.
secret_value() {
  local v rc=0
  v="$(parse_secrets | LC_ALL=C awk -F '\t' -v k="$1" '
         $1 == k { v = substr($0, length(k) + 2); n++ }
         END { if (!n || v == "") exit 2; if (v ~ /[^A-Za-z0-9_#%+=.:,~!?*@^\/-]/) exit 3; printf "%s", v }')" || rc=$?
  case "$rc" in
    0) ;;
    3) die "key $1 holds a character that cannot be quoted safely" ;;
    *) die "key $1 missing from the secrets file" ;;
  esac
  case "$v" in
    *[!A-Za-z0-9_#%+=.:,~!?*@^/-]*) die "key $1 holds a character that cannot be quoted safely" ;;
  esac
  printf '%s' "$v"
}

# every pattern the log scan looks for, one per line, for grep -f - (never argv). Never an empty one (it would
# match every line). Three readings of each line that holds "=", comment lines included:
#   1. the raw text after the first "=" (the v1.0 reading);
#   2. the value as parse_secrets reads it;
#   3. the text after the first "=" with every leading and trailing blank, \034-\037, quote and non-ASCII byte
#      removed, cut at NUL bytes (grep -F never matches a pattern that holds one). The driver's strip() and
#      unquoting only ever remove such characters from the ends, so each piece is part of whatever value the
#      driver reads from the line, and a log that holds the driver's value holds every piece.
secret_values() {
  LC_ALL=C awk 'index($0, "=") > 1 { v = substr($0, index($0, "=") + 1); if (length(v) > 0) print v }' "$SECRETS"
  parse_secrets | LC_ALL=C awk -F '\t' '{ v = substr($0, index($0, "\t") + 1); if (length(v) > 0) print v }'
  secrets_lines | LC_ALL=C awk 'index($0, "=") > 0 {
    v = substr($0, index($0, "=") + 1)
    sub(/^[[:space:]\034-\037"\047\200-\377]+/, "", v); sub(/[[:space:]\034-\037"\047\200-\377]+$/, "", v)
    n = split(v, piece, /\000/)
    for (i = 1; i <= n; i++) if (length(piece[i]) > 0) print piece[i]
  }'
}
# ---- end of the secrets block

# shred a log that holds any secret value; returns 3 if it did
scan_log() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  if secret_values | grep -qF -f - "$f"; then
    shred -u "$f" || rm -f "$f"
    log ERROR "a secret value was found in $(basename "$f"); the log was shredded"
    return 3
  fi
  return 0
}

stage() {
  local f n=0
  docker exec -u root "$CONTAINER" mkdir -p "$CDIR" || die "cannot create $CDIR in $CONTAINER"
  for f in "$STAGE"/sql/9[0-2]_*.sql "$STAGE"/sql/9[4-6]_*.sql "$STAGE"/test/anom_test_phase[34].sql \
           "$STAGE"/tools/anom_*.sql "$STAGE"/sql/98_*.sql "$STAGE"/test/anom_test_live.sql \
           "$STAGE"/test/anom_test_phase4b.sql "$STAGE"/test/anom_test_chaos_link.sql \
           "$STAGE"/test/anom_test_phase5c.sql "$STAGE"/test/anom_test_phase5d.sql; do
    [[ -f "$f" ]] || continue
    docker cp "$f" "$CONTAINER:$CDIR/" >/dev/null || die "docker cp failed for $(basename "$f")"
    n=$((n + 1))
  done
  # docker cp leaves the files root-owned and private; the oracle user must read them
  docker exec -u root "$CONTAINER" chmod -R a+rX "$CDIR" || die "chmod failed in $CONTAINER"
  log INFO "staged $n file(s) into $CONTAINER:$CDIR"
}

clean() {
  docker exec -u root "$CONTAINER" bash -c 'rm -f /tmp/app900/9[0-24-6]_*.sql /tmp/app900/anom_test_phase[34].sql /tmp/app900/anom_*.sql /tmp/app900/98_*.sql' \
    || die "clean-up in $CONTAINER failed"
  log INFO "removed the observer scripts from $CONTAINER:$CDIR"
}

run() {
  local file="$1" name="$2"; shift 2
  local keys=() extra=() pw=() rc=0 ops k
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --pwd)
        [[ $# -ge 2 ]] || die "--pwd needs a key"
        case "$2" in ANOM_MON_PWD|ANOM_FEED_PWD|CHAOS_CTL_PWD) keys+=("$2") ;; *) die "--pwd key not allowed: $2" ;; esac
        shift 2 ;;
      --) shift; extra=("$@"); break ;;
      *) die "unknown option $1" ;;
    esac
  done
  [[ "$file" =~ ^[A-Za-z0-9_.-]+\.sql$ ]] || die "script name not acceptable: $file"
  [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]] || die "log name not acceptable: $name"
  local a; for a in "${extra[@]}"; do [[ "$a" =~ ^[A-Za-z0-9_.:=-]*$ ]] || die "argument not acceptable: $a"; done
  mkdir -p "$LOGDIR"
  local out="$LOGDIR/$name.log"
  ops="$(secret_value ANOMOPS_PWD)"
  for k in "${keys[@]}"; do pw+=("$(secret_value "$k")"); done
  log INFO "run $file as ANOMOPS in $CONTAINER, log $out"
  set +e
  {
    printf 'whenever sqlerror exit failure rollback\n'
    printf 'connect ANOMOPS/"%s"@%s\n' "$ops" "$SERVICE"
    printf '@%s/%s' "$CDIR" "$file"
    for k in "${pw[@]}"; do printf ' "%s"' "$k"; done
    for a in "${extra[@]}"; do printf ' "%s"' "$a"; done
    printf '\n'
  } | docker exec -i "$CONTAINER" bash -lc "sqlplus -s -L /nolog" > "$out" 2>&1
  rc=$?
  set -e
  ops=""; pw=()
  scan_log "$out" || exit 3
  cat "$out"
  if [[ $rc -ne 0 ]]; then log ERROR "$file exited with status $rc"; exit 2; fi
  if grep -qE '^(SP2-|ORA-)' "$out"; then log ERROR "$file printed an SP2-/ORA- error"; exit 2; fi
  log INFO "$file ok"
}

[[ -r "$SECRETS" ]] || die "secrets file not readable: $SECRETS"
case "${1:-}" in
  stage) stage ;;
  clean) clean ;;
  run)   [[ $# -ge 3 ]] || die "usage: run <file.sql> <log> [--pwd KEY ...] [-- arg ...]"; shift; run "$@" ;;
  scan)  [[ $# -eq 2 ]] || die "usage: scan <logfile>"; scan_log "$2" ;;
  *)     sed -n '2,41p' "$0" >&2; exit 1 ;;
esac
