#!/usr/bin/env bash
# v1.1 - app 900: runs sql/86_anom_target_users.sql on the target (oradb1 / FREEPDB1) as SYSDBA, with operating-system
#        authentication inside the container. 86 takes the four account passwords as arguments 1-4: they are read
#        from the secrets file and written to SQL*Plus's standard input (never on a command line, never printed;
#        86 sets verify off before it substitutes them). Argument 5 (the ASH grant, Y or N) comes from this
#        runner's command line. 86 is idempotent: the users exist and keep their passwords, every grant is applied
#        again. With Y the same session then prints the UTC time of the grant, ANOM_MON's object privileges and
#        the span of ASH samples the target holds in memory (no second logon).
#        v1.0: first version (phase 5b, builder U: the ASH drill-down switched on), 02-Oct-2026. v1.1: public copy: host name and local-time references removed; the secrets file default is a placeholder.
#
# Run as : the demo user on the demo VM, from the staging copy (~/app900), with oradb1 running.
# Usage  : tools/anom_target_users_run.sh run <log> Y|N      # stage sql/86 into oradb1, run it, remove it again
#          tools/anom_target_users_run.sh scan <logfile>      # secret check of any log (shreds on a hit)
#
# Secrets: read from the secrets file (ANOM_SECRETS; the default <secrets-file> is a placeholder) with the secrets
#          block of tools/anom_obs_run.sh (byte for byte, checked by test/test_apex900_static.py): keys SHOP_PWD,
#          SHOP_APP_PWD, ANOM_MON_PWD, CHAOS_CTL_PWD, into unexported variables, written with the printf builtin.
#          After the run the log is searched for every value in the secrets file; a log that holds one is
#          shredded and the run fails (exit 3). The log is printed only after that check.
# Exit   : 0 ok; 1 usage or environment; 2 the SQL failed (non-zero exit, an ORA- or SP2- line);
#          3 a secret was found in the log (log shredded).
set -euo pipefail
umask 077

STAGE="${ANOM_STAGE:-$HOME/app900}"
SECRETS="${ANOM_SECRETS:-<secrets-file>}"
CONTAINER="${ANOM_TGT_CONTAINER:-oradb1}"
CDIR="/tmp/app900"
FILE="86_anom_target_users.sql"
LOGDIR="$STAGE/logs"

log() { printf '%s anom_target_users_run %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >&2; }
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

run() {
  local name="$1" ash="$2" rc=0 out p_shop p_app p_mon p_ctl
  [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]] || die "log name not acceptable: $name"
  [[ "$ash" == "Y" || "$ash" == "N" ]] || die "argument 5 (the ASH grant) must be Y or N"
  [[ -f "$STAGE/sql/$FILE" ]] || die "missing in the staging copy: sql/$FILE"
  mkdir -p "$LOGDIR"
  out="$LOGDIR/$name.log"
  # every password is read before anything touches the target: a missing or unquotable key stops here
  p_shop="$(secret_value SHOP_PWD)"
  p_app="$(secret_value SHOP_APP_PWD)"
  p_mon="$(secret_value ANOM_MON_PWD)"
  p_ctl="$(secret_value CHAOS_CTL_PWD)"
  docker exec -u root "$CONTAINER" mkdir -p "$CDIR" || die "cannot create $CDIR in $CONTAINER"
  docker cp "$STAGE/sql/$FILE" "$CONTAINER:$CDIR/" >/dev/null || die "docker cp failed for $FILE"
  # docker cp leaves the file root-owned and private; the oracle user must read it
  docker exec -u root "$CONTAINER" chmod -R a+rX "$CDIR" || die "chmod failed in $CONTAINER"
  log INFO "run $FILE as SYSDBA in $CONTAINER/FREEPDB1 (argument 5 = $ash), log $out"
  set +e
  {
    printf 'connect / as sysdba\n'
    printf 'alter session set container = FREEPDB1;\n'
    printf 'whenever sqlerror exit failure rollback\n'
    printf '@%s/%s "%s" "%s" "%s" "%s" %s\n' "$CDIR" "$FILE" "$p_shop" "$p_app" "$p_mon" "$p_ctl" "$ash"
    cat <<'SQL'
set feedback off
set linesize 250
col done_utc format a40
select 'done at ' || to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD HH24:MI:SS') || 'Z' as done_utc from dual;
SQL
    if [[ "$ash" == "Y" ]]; then
      cat <<'SQL'
col grants format a240
col ash format a240
select '  ANOM_MON: ' || listagg(privilege || ' on ' || table_name, ', ') within group (order by table_name) as grants
  from dba_tab_privs where grantee = 'ANOM_MON';
select '  ASH in memory (FREEPDB1): ' || count(*) || ' samples, '
       || to_char(min(sample_time_utc), 'YYYY-MM-DD HH24:MI:SS') || ' to '
       || to_char(max(sample_time_utc), 'YYYY-MM-DD HH24:MI:SS') || ' UTC; sample_time - sample_time_utc = '
       || nvl(to_char(max(cast(sample_time as date) - cast(sample_time_utc as date)) * 24), '-') || ' h' as ash
  from v$active_session_history;
SQL
    fi
    printf 'exit\n'
  } | docker exec -i "$CONTAINER" bash -lc "sqlplus -s -L /nolog" > "$out" 2>&1
  rc=$?
  set -e
  p_shop=""; p_app=""; p_mon=""; p_ctl=""
  docker exec -u root "$CONTAINER" rm -f "$CDIR/$FILE" || log WARN "could not remove $CDIR/$FILE from $CONTAINER"
  scan_log "$out" || exit 3
  cat "$out"
  if [[ $rc -ne 0 ]]; then log ERROR "$FILE exited with status $rc"; exit 2; fi
  if grep -qE '^(SP2-|ORA-)' "$out"; then log ERROR "$FILE printed an SP2-/ORA- error"; exit 2; fi
  log INFO "$FILE ok"
}

[[ -r "$SECRETS" ]] || die "secrets file not readable: $SECRETS"
case "${1:-}" in
  run)  [[ $# -eq 3 ]] || die "usage: run <log> Y|N"; run "$2" "$3" ;;
  scan) [[ $# -eq 2 ]] || die "usage: scan <logfile>"; scan_log "$2" ;;
  *)    sed -n '2,24p' "$0" >&2; exit 1 ;;
esac
