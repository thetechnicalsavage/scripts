#!/usr/bin/env bash
# v1.1 - app 900 chaos runner (builder C, phase 4): stages the PKG_CHAOS script and its tests into the oradb1
#        container and runs one of them in FREEPDB1 as SHOP (default) or CHAOS_CTL, the password on SQL*Plus's
#        standard input. Same rules and the same secrets block as tools/anom_obs_run.sh (the observer runner).
#        v1.0: first version, 01-Oct-2026. v1.1: public copy: host name and local-time references removed; the secrets file default is a placeholder.
#
# Run as : the demo user on the demo VM, from the staging copy (~/app900), with oradb1 running.
# Usage  : tools/anom_chaos_run.sh stage [file ...]           # copy sql/93_*.sql and test/anom_test_chaos*.sql in
#                                                            # (or only the named files, relative to the staging copy)
#          tools/anom_chaos_run.sh run <file.sql> <log> [--as SHOP|CHAOS_CTL] [-- arg ...]
#                                                            # run /tmp/app900/<file.sql> in FREEPDB1
#          tools/anom_chaos_run.sh clean [file ...]          # remove the staged copies (only this runner's files)
#          tools/anom_chaos_run.sh scan <logfile>            # secret check of any log (shreds on a hit)
#          Plain arguments after "--" are passed to the script (they must not be secrets: [A-Za-z0-9_.:=-]).
#
# Secrets: read from the secrets file (ANOM_SECRETS; the default <secrets-file> is a placeholder) into unexported
#          shell variables and written to SQL*Plus's standard input with the printf builtin: never on a command
#          line, never in an environment, never printed. After the run the log is searched for every value in the
#          secrets file; a log that holds one is shredded and the run fails (exit 3). The log is printed only after
#          that check.
# Exit   : 0 ok; 1 usage or environment; 2 the SQL script failed (non-zero exit, ORA- under WHENEVER, or SP2-);
#          3 a secret was found in the log (log shredded).
set -euo pipefail
umask 077

STAGE="${ANOM_STAGE:-$HOME/app900}"
SECRETS="${ANOM_SECRETS:-<secrets-file>}"
CONTAINER="${ANOM_TGT_CONTAINER:-oradb1}"
CDIR="/tmp/app900"
SERVICE="//localhost:1521/FREEPDB1"
LOGDIR="$STAGE/logs"

log() { printf '%s anom_chaos_run %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >&2; }
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

# the files this runner owns in the container: 93_*.sql and anom_test_chaos*.sql, or exactly the ones named
own_name() { [[ "$1" =~ ^[A-Za-z0-9_.-]+\.sql$ ]]; }

stage() {
  local f n=0 files=()
  if [[ $# -gt 0 ]]; then
    for f in "$@"; do
      [[ "$f" =~ ^[A-Za-z0-9_./-]+\.sql$ && "$f" != *..* ]] || die "file name not acceptable: $f"
      files+=("$STAGE/$f")
    done
  else
    files=("$STAGE"/sql/93_*.sql "$STAGE"/test/anom_test_chaos*.sql)
  fi
  docker exec -u root "$CONTAINER" mkdir -p "$CDIR" || die "cannot create $CDIR in $CONTAINER"
  for f in "${files[@]}"; do
    [[ -f "$f" ]] || continue
    docker cp "$f" "$CONTAINER:$CDIR/" >/dev/null || die "docker cp failed for $(basename "$f")"
    n=$((n + 1))
  done
  # docker cp leaves the files root-owned; the oracle user must read them
  docker exec -u root "$CONTAINER" chmod -R a+rX "$CDIR" || die "chmod failed in $CONTAINER"
  log INFO "staged $n file(s) into $CONTAINER:$CDIR"
}

clean() {
  local f
  if [[ $# -gt 0 ]]; then
    for f in "$@"; do
      f="$(basename "$f")"
      own_name "$f" || die "file name not acceptable: $f"
      docker exec -u root "$CONTAINER" rm -f "$CDIR/$f" || die "clean-up of $f in $CONTAINER failed"
    done
  else
    docker exec -u root "$CONTAINER" bash -c 'rm -f /tmp/app900/93_*.sql /tmp/app900/anom_test_chaos*.sql' \
      || die "clean-up in $CONTAINER failed"
  fi
  log INFO "removed the chaos scripts from $CONTAINER:$CDIR"
}

run() {
  local file="$1" name="$2"; shift 2
  local user=SHOP key=SHOP_PWD extra=() rc=0 pw a
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --as)
        [[ $# -ge 2 ]] || die "--as needs a user"
        case "$2" in
          SHOP)      user=SHOP;      key=SHOP_PWD ;;
          CHAOS_CTL) user=CHAOS_CTL; key=CHAOS_CTL_PWD ;;
          *) die "--as user not allowed: $2" ;;
        esac
        shift 2 ;;
      --) shift; extra=("$@"); break ;;
      *) die "unknown option $1" ;;
    esac
  done
  own_name "$file" || die "script name not acceptable: $file"
  [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]] || die "log name not acceptable: $name"
  for a in "${extra[@]}"; do [[ "$a" =~ ^[A-Za-z0-9_.:=-]*$ ]] || die "argument not acceptable: $a"; done
  mkdir -p "$LOGDIR"
  local out="$LOGDIR/$name.log"
  pw="$(secret_value "$key")"
  log INFO "run $file as $user in $CONTAINER/FREEPDB1, log $out"
  set +e
  {
    printf 'whenever sqlerror exit failure rollback\n'
    printf 'connect %s/"%s"@%s\n' "$user" "$pw" "$SERVICE"
    printf '@%s/%s' "$CDIR" "$file"
    for a in "${extra[@]}"; do printf ' "%s"' "$a"; done
    printf '\n'
  } | docker exec -i "$CONTAINER" bash -lc "sqlplus -s -L /nolog" > "$out" 2>&1
  rc=$?
  set -e
  pw=""
  scan_log "$out" || exit 3
  cat "$out"
  if [[ $rc -ne 0 ]]; then log ERROR "$file exited with status $rc"; exit 2; fi
  if grep -qE '^(SP2-|ORA-)' "$out"; then log ERROR "$file printed an SP2-/ORA- error"; exit 2; fi
  log INFO "$file ok"
}

[[ -r "$SECRETS" ]] || die "secrets file not readable: $SECRETS"
case "${1:-}" in
  stage) shift; stage "$@" ;;
  clean) shift; clean "$@" ;;
  run)   [[ $# -ge 3 ]] || die "usage: run <file.sql> <log> [--as SHOP|CHAOS_CTL] [-- arg ...]"; shift; run "$@" ;;
  scan)  [[ $# -eq 2 ]] || die "usage: scan <logfile>"; scan_log "$2" ;;
  *)     sed -n '2,24p' "$0" >&2; exit 1 ;;
esac
