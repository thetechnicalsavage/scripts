#!/usr/bin/env bash
# v1.7 - app 900 (builder O): what the observer's collection costs the TARGET database.
#        v1.7: public copy: host name and local-time references removed; the secrets file default is a placeholder. v1.6: --persistent rows print the session as sid,serial#: v1.5 printed the "|"-joined key, which shifted
#              every later column (seen in the first run, 01-Oct); the row builder is minute_rows(), tested.
#        v1.5: phase 4 tune (92 v1.2, persistent-session collector): new mode --persistent measures the loop's
#              one link session minute by minute and the LOGONS it adds (SYSDBA only, no password). Target
#              snapshots now run from CDB$ROOT filtered on FREEPDB1 (no container switch). Phases A and B tell
#              their own link session from the loop's by a snapshot taken before they log on.
#        v1.4: secret_value applies the allowlist in awk before bash holds the value (bash drops NUL
#              bytes); scan patterns are also cut at NUL bytes (Codex confirmation, 01-Oct-2026).
#        v1.3: secrets block shared with anom_obs_run.sh: lines cut as Python cuts them, literal "export ", and a
#              scan pattern that is a piece of every value the driver can read (Codex re-review, 01-Oct-2026).
#        v1.2: the secrets file is read the way the load driver reads it (optional "export ", blanks, one pair of
#              matching quotes), for the password and for the secret scan (review finding, 01-Oct-2026).
#        v1.1: adds phase B, five job-like runs (a fresh session, one collect(), as the scheduler job does every
#              minute), because phase A showed that opening the link session costs more than the reads.
#        v1.0: first version, 01-Oct-2026 (phase A only).
#
#        Per-run mode (default; what the v1.1 collector, one fresh session a minute, cost):
#        Phase A: one ANOMOPS session in ora26ai/ORCLPDB1 opens ANOM_MON_LINK, waits for the quiet part of the
#                 minute (both target views published, the collector job not due), then runs
#                 PKG_ANOM_COLLECT.collect 10 times in a row. SYSDBA in oradb1 reads that link session's
#                 V$SESSTAT / V$SESS_TIME_MODEL before and after: the cost of the reads alone.
#        Phase B: five times, in the quiet part of a minute: a fresh ANOMOPS session runs collect() once and,
#                 before it ends, SYSDBA reads its link session's totals since logon: the cost of one v1.1 job
#                 run on the target (logon + link set-up + the reads).
#        Read-only on the target; on the observer collect() merges the rows the job would have merged anyway.
#        Persistent mode (--persistent, 92 v1.2): nothing logs on as ANOMOPS. SYSDBA in oradb1 reads the
#                 ANOM_MON sessions' V$SESSTAT / V$SESS_TIME_MODEL and FREEPDB1's 'logons cumulative' once a
#                 minute, 30 s after each collection, for N minutes; SYSDBA in ORCLPDB1 reads the collected
#                 LOGONS signal (METRIC_MINUTE) for the intervals inside that window and, with --ref-end, the
#                 30 intervals before that UTC minute as the reference. Read only on both databases.
#
# Run as : the demo user on the demo VM, from ~/app900, after 90-92 are installed (the job decides the window).
# Usage  : tools/anom_collector_cost.sh [transcript-file]
#              per-run mode (default transcript ~/app900/logs/02-collector-cost-over-link.txt)
#          tools/anom_collector_cost.sh --persistent [--minutes N] [--ref-end YYYY-MM-DDTHH:MMZ] [transcript-file]
#              N = 3-60 minutes (default 10); default transcript ~/app900/logs/02b-collector-cost-persistent.txt
# Secrets: per-run mode only reads one: ANOMOPS's password goes from the secrets file to SQL*Plus's standard
#          input (a FIFO) by the printf builtin; never on a command line, never printed. In both modes every raw
#          log is checked for every secret value (when the secrets file is readable) and shredded on a hit; the
#          transcript is written from the checked logs and is checked again.
# Exit   : 0 ok; 1 usage, environment or a step failed; 3 a secret was found (logs shredded).
set -euo pipefail
umask 077

STAGE="${ANOM_STAGE:-$HOME/app900}"
SECRETS="${ANOM_SECRETS:-<secrets-file>}"
OBS="${ANOM_OBS_CONTAINER:-ora26ai}"
TGT="${ANOM_TGT_CONTAINER:-oradb1}"
CALLS=10
RUNS=5
OBS_PID=""
OBS_OUT=""
JOB_SEC=""

log() { printf '%s anom_collector_cost %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >&2; }
die() { log ERROR "$*"; exit 1; }

# ---- arguments (v1.5); both values that reach SQL are validated before anything runs
MODE="per-run"
MINUTES=10
REF_END=""
OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --persistent) MODE="persistent"; shift ;;
    --minutes)    [[ $# -ge 2 ]] || die "--minutes needs a value"; MINUTES="$2"; MODE_ARG=1; shift 2 ;;
    --ref-end)    [[ $# -ge 2 ]] || die "--ref-end needs a value"; REF_END="$2"; MODE_ARG=1; shift 2 ;;
    -*)           die "unknown option $1" ;;
    *)            [[ -z "$OUT" ]] || die "one transcript file only"; OUT="$1"; shift ;;
  esac
done
[[ "$MINUTES" =~ ^[0-9]{1,2}$ ]] || die "--minutes must be 3-60"
MINUTES=$((10#$MINUTES))
(( MINUTES >= 3 && MINUTES <= 60 )) || die "--minutes must be 3-60"
[[ -z "$REF_END" || "$REF_END" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z$ ]] \
  || die "--ref-end must be YYYY-MM-DDTHH:MMZ (UTC)"
if [[ "$MODE" == "per-run" ]]; then
  [[ -z "${MODE_ARG:-}" ]] || die "--minutes and --ref-end belong to --persistent"
  OUT="${OUT:-$STAGE/logs/02-collector-cost-over-link.txt}"
else
  OUT="${OUT:-$STAGE/logs/02b-collector-cost-persistent.txt}"
fi
WORK="$(mktemp -d "${TMPDIR:-/tmp}/anom_cost.XXXXXX")"

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
has_secret() { secret_values | grep -qF -f - "$1"; }
# shred a file that holds a secret and stop. Without a readable secrets file (persistent mode needs none) there
# is nothing to compare against, and nothing in this run read a secret.
guard() {
  [[ -r "$SECRETS" ]] || return 0
  if [[ -f "$1" ]] && has_secret "$1"; then shred -u "$1"; log ERROR "secret in $(basename "$1"); shredded"; exit 3; fi
}

cleanup() {
  exec 3>&- 2>/dev/null || true
  # only the SQL*Plus pipeline this script started, by its PID
  if [[ -n "$OBS_PID" ]] && kill -0 "$OBS_PID" 2>/dev/null; then kill "$OBS_PID" 2>/dev/null || true; fi
  local f
  for f in "$WORK"/*; do
    [[ -f "$f" ]] || continue
    shred -u "$f" 2>/dev/null || rm -f "$f"
  done
  rm -rf "$WORK"
}
trap cleanup EXIT

# start one ANOMOPS SQL*Plus session fed through a FIFO on fd 3; its output goes to $WORK/<name>.out
start_obs() {
  local name="$1" pwd_
  OBS_OUT="$WORK/$name.out"
  mkfifo "$WORK/$name.in"
  docker exec -i "$OBS" bash -lc "sqlplus -s -L /nolog" < "$WORK/$name.in" > "$OBS_OUT" 2>&1 &
  OBS_PID=$!
  exec 3> "$WORK/$name.in"
  pwd_="$(secret_value ANOMOPS_PWD)"
  printf 'whenever sqlerror exit failure rollback\n' >&3
  printf 'connect ANOMOPS/"%s"@//localhost:1521/orclpdb1\n' "$pwd_" >&3
  pwd_=""
  printf 'set serveroutput on size unlimited format wrapped\nset feedback off verify off linesize 200\n' >&3
}

stop_obs() {
  printf 'exit\n' >&3
  exec 3>&-
  wait "$OBS_PID" || { guard "$OBS_OUT"; cat "$OBS_OUT" >&2; die "observer SQL*Plus session ended with an error"; }
  OBS_PID=""
  guard "$OBS_OUT"
}

# wait (max $2 s) until the observer session prints marker $1 or fails
wait_for() {
  local marker="$1" limit="$2" i
  for ((i = 0; i < limit * 2; i++)); do
    if grep -q "^$marker\$" "$OBS_OUT" 2>/dev/null; then return 0; fi
    if grep -qE '^(ORA-|SP2-)' "$OBS_OUT" 2>/dev/null; then break; fi
    if ! kill -0 "$OBS_PID" 2>/dev/null; then break; fi
    sleep 0.5
  done
  guard "$OBS_OUT"
  cat "$OBS_OUT" >&2
  return 1
}

# true while the UTC second of the minute is in [job - 12, job - 4]: both target views of the minute are
# published (they come out about 20 and 15 s before the job's second) and the job is not about to run
in_window() {
  local s; s=$((10#$(date -u +%S)))
  local d=$(( (s - JOB_SEC + 12 + 60) % 60 ))
  (( d <= 8 ))
}

# SYSDBA snapshot of every ANOM_MON session in the target PDB: SNAP|sid|serial#|logon|stat|value, plus
# AT|<UTC>, LOGONS|<FREEPDB1 'logons cumulative'>, TARGET|<version>, CPUS|<cpu_count>. Runs in CDB$ROOT and
# filters on FREEPDB1's con_id, so the snapshot never switches into (or logs on to) the PDB it measures.
snapshot() {
  docker exec -i "$TGT" bash -lc "sqlplus -s -L / as sysdba" > "$WORK/$1.out" 2>&1 <<'EOF'
whenever sqlerror exit failure
set heading off feedback off pagesize 0 linesize 300 trimspool on
variable con number
begin select con_id into :con from v$pdbs where name = 'FREEPDB1'; end;
/
select 'AT|'||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"') from dual;
select 'LOGONS|'||value from v$con_sysstat where con_id = :con and name = 'logons cumulative';
select 'SNAP|'||s.sid||'|'||s.serial#||'|'||to_char(s.logon_time, 'HH24:MI:SS')||'|'||n.name||'|'||st.value
  from v$session s
  join v$sesstat st on st.sid = s.sid
  join v$statname n on n.statistic# = st.statistic#
 where s.username = 'ANOM_MON' and s.con_id = :con
   and n.name in ('CPU used by this session', 'session logical reads', 'execute count', 'parse count (total)',
                  'SQL*Net roundtrips to/from client', 'bytes sent via SQL*Net to client',
                  'bytes received via SQL*Net from client', 'physical reads')
union all
select 'SNAP|'||s.sid||'|'||s.serial#||'|'||to_char(s.logon_time, 'HH24:MI:SS')||'|'||t.stat_name||'|'||t.value
  from v$session s
  join v$sess_time_model t on t.sid = s.sid
 where s.username = 'ANOM_MON' and s.con_id = :con
   and t.stat_name in ('DB CPU', 'DB time');
select 'TARGET|'||version_full from v$instance;
select 'CPUS|'||value from v$parameter where name = 'cpu_count';
exit
EOF
  guard "$WORK/$1.out"
  if grep -qE '^(ORA-|SP2-)' "$WORK/$1.out"; then cat "$WORK/$1.out" >&2; die "target snapshot $1 failed"; fi
  return 0
}

# SYSDBA in the observer PDB, read only: SQL on stdin, output in $WORK/<name>.out
obs_sysdba() {
  { printf 'connect / as sysdba\nalter session set container=ORCLPDB1;\n'; cat; } \
    | docker exec -i "$OBS" bash -lc "sqlplus -s -L /nolog" > "$WORK/$1.out" 2>&1 \
    || { guard "$WORK/$1.out"; cat "$WORK/$1.out" >&2; die "observer query $1 failed"; }
  guard "$WORK/$1.out"
  if grep -qE '^(ORA-|SP2-)' "$WORK/$1.out"; then cat "$WORK/$1.out" >&2; die "observer query $1 failed"; fi
  return 0
}

# ====================================================================================== per-run mode (v1.1 job)
per_run() {
  [[ -r "$SECRETS" ]] || die "secrets file not readable: $SECRETS"

  # ---------------------------------------------------------------- phase A: the reads alone
  log INFO "phase A: $CALLS collect() calls in one session"
  snapshot a0     # v1.5: the ANOM_MON sessions before phase A logs on (a 92 v1.2 loop keeps one open)
  start_obs phaseA
  cat >&3 <<'EOF'
declare
  n        number;
  l_rep    varchar2(200);
  l_job    number;
  l_start  number;
  l_sec    number;
begin
  -- open the link session on the target (logon + one trivial query); it stays open until this session ends
  select count(*) into n from dual@ANOM_MON_LINK;
  commit;
  -- the job runs at BYSECOND = (end second of the target's interval + 20); start 12 s before it
  select repeat_interval into l_rep from user_scheduler_jobs where job_name = 'ANOM_COLLECT_JOB';
  l_job   := to_number(regexp_substr(l_rep, 'BYSECOND=([0-9]+)', 1, 1, null, 1));
  l_start := mod(l_job + 48, 60);
  for i in 1 .. 140 loop
    l_sec := to_number(to_char(sysdate, 'SS'));
    exit when mod(l_sec - l_start + 60, 60) <= 2;
    dbms_session.sleep(0.5);
  end loop;
  dbms_output.put_line('WINDOW|'||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                       ||'|'||l_job);
end;
/
prompt MARK1
EOF
  wait_for MARK1 90 || die "observer session did not reach the window"
  JOB_SEC="$(awk -F'|' '$1 == "WINDOW" { print $3 }' "$OBS_OUT")"
  [[ "$JOB_SEC" =~ ^[0-9]+$ ]] || die "could not read the job's second"

  snapshot a1
  cat >&3 <<EOF
declare
  t0   timestamp;
  ms   number;
  tot  number := 0;
  mx   number := 0;
  mn   number := 1e9;
  c0   number;
  c1   number;
begin
  select count(*) into c0 from METRIC_MINUTE;
  for i in 1 .. $CALLS loop
    t0 := systimestamp;
    PKG_ANOM_COLLECT.collect;
    ms := (extract(minute from (systimestamp - t0)) * 60 + extract(second from (systimestamp - t0))) * 1000;
    tot := tot + ms; mx := greatest(mx, ms); mn := least(mn, ms);
    dbms_output.put_line('CALL|'||i||'|'||to_char(ms, 'FM9990.0'));
  end loop;
  select count(*) into c1 from METRIC_MINUTE;
  dbms_output.put_line('ELAPSED|mean='||to_char(tot / $CALLS, 'FM9990.0')||'|min='||to_char(mn, 'FM9990.0')
                       ||'|max='||to_char(mx, 'FM9990.0')||'|rows_added='||(c1 - c0));
end;
/
prompt MARK2
EOF
  wait_for MARK2 180 || die "the $CALLS collect() calls did not finish"
  snapshot a2
  stop_obs

  # the link session of phase A = the ANOM_MON session present in both snapshots and absent before phase A
  # logged on (a v1.1 job session logs on and off around its run; a v1.2 loop session was already in a0)
  awk -F'|' '
    FNR == 1 { file++ }
    file == 1 && $1 == "SNAP" { base[$2 "|" $3] = 1; next }
    $1 == "SNAP" { key = $2 "|" $3; if (file == 2) { a[key, $5] = $6; logon[key] = $4; s1[key] = 1 } else { b[key, $5] = $6; s2[key] = 1 } }
    $1 == "TARGET" { ver = $2 }
    $1 == "CPUS" { cpus = $2 }
    END {
      n = 0; for (k in s1) if ((k in s2) && !(k in base)) { n++; mine = k }
      if (n != 1) { printf "ERROR: %d new ANOM_MON sessions present in both snapshots (expected 1)\n", n; exit 2 }
      printf "TARGETVER|%s|%s\n", ver, cpus
      printf "SESSION|%s|%s\n", mine, logon[mine]
      split("CPU used by this session,DB CPU,DB time,session logical reads,physical reads,execute count,parse count (total),SQL*Net roundtrips to/from client,bytes sent via SQL*Net to client,bytes received via SQL*Net from client", st, ",")
      for (i = 1; i <= 10; i++) { s = st[i]; printf "STAT|%s|%s|%s|%s\n", s, a[mine, s], b[mine, s], b[mine, s] - a[mine, s] }
    }' "$WORK/a0.out" "$WORK/a1.out" "$WORK/a2.out" > "$WORK/deltaA.out" \
    || { cat "$WORK/deltaA.out" >&2; die "could not isolate the phase A link session"; }
  cp "$OBS_OUT" "$WORK/phaseA.keep"

  # ---------------------------------------------------------------- phase B: job-like runs
  log INFO "phase B: $RUNS job-like runs (fresh session, one collect())"
  : > "$WORK/runsB.out"
  local done_runs=0 attempts=0 w
  while (( done_runs < RUNS )); do
    (( attempts++ < RUNS * 3 )) || die "phase B: could not complete $RUNS clean runs"
    # wait (max 70 s) for the quiet part of the minute
    for ((w = 0; w < 140; w++)); do in_window && break; sleep 0.5; done
    in_window || continue
    snapshot "b${attempts}_0"     # v1.5: sessions present before this run logs on are not this run's
    start_obs "phaseB$attempts"
    printf 'exec PKG_ANOM_COLLECT.collect\nprompt MARKB\n' >&3
    wait_for MARKB 30 || die "phase B run $attempts: collect() did not finish"
    snapshot "b$attempts"
    stop_obs
    # exactly one new ANOM_MON session = this run's link session; otherwise the run is discarded and repeated
    if awk -F'|' -v run="$((done_runs + 1))" '
         FNR == 1 { file++ }
         file == 1 && $1 == "SNAP" { base[$2 "|" $3] = 1; next }
         $1 == "SNAP" { key = $2 "|" $3; if (key in base) next; s[key] = 1; v[$5] = $6 }
         END { n = 0; for (k in s) n++; if (n != 1) exit 1
               printf "RUN|%d|%s|%s|%s|%s|%s|%s|%s|%s\n", run, v["CPU used by this session"], v["DB CPU"], v["DB time"],
                      v["session logical reads"], v["execute count"], v["parse count (total)"],
                      v["SQL*Net roundtrips to/from client"], v["bytes sent via SQL*Net to client"] + v["bytes received via SQL*Net from client"] }' \
         "$WORK/b${attempts}_0.out" "$WORK/b$attempts.out" >> "$WORK/runsB.out"; then
      done_runs=$((done_runs + 1))
    else
      log WARN "phase B run $attempts saw another new ANOM_MON session; discarded"
    fi
  done

  # ---------------------------------------------------------------- transcript
  mkdir -p "$(dirname "$OUT")"
  {
    echo "# v1.1 - transcript: the observer collector's cost on the TARGET, measured $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# tools/anom_collector_cost.sh (app 900, phase 3). Target = oradb1/FREEPDB1, observer = ora26ai/ORCLPDB1."
    echo "# SYSDBA on the target read the ANOM_MON link session's V\$SESSTAT / V\$SESS_TIME_MODEL."
    echo
    awk -F'|' '$1 == "TARGETVER" { printf "Target            : Oracle %s, cpu_count %s\n", $2, $3 }' "$WORK/deltaA.out"
    echo "Collector job     : ANOMOPS.ANOM_COLLECT_JOB, every minute at second $JOB_SEC UTC"
    echo
    echo "== Phase A: the reads alone. One session, link already open, collect() $CALLS times in a row"
    awk -F'|' '$1 == "WINDOW" { printf "Window start      : %s\n", $2 }' "$WORK/phaseA.keep"
    echo "Per call, observer side (elapsed ms: two remote queries, the local MERGE, the gap check):"
    awk -F'|' '$1 == "CALL" { printf "  call %2d  %7s ms\n", $2, $3 }' "$WORK/phaseA.keep"
    awk -F'|' '$1 == "ELAPSED" { printf "  %s  %s  %s  %s\n", $2, $3, $4, $5 }' "$WORK/phaseA.keep"
    echo "Target side, the link session ($CALLS calls, before -> after = delta):"
    awk -F'|' -v calls="$CALLS" '$1 == "STAT" {
        d = $5; per = d / calls
        if ($2 == "CPU used by this session") { u = sprintf("= %.1f ms CPU per call (centisecond counter)", per * 10) }
        else if ($2 == "DB CPU" || $2 == "DB time") { u = sprintf("= %.2f ms per call (microsecond counter)", per / 1000) }
        else { u = sprintf("= %.1f per call", per) }
        printf "  %-40s %10s -> %10s  delta %9s  %s\n", $2, $3, $4, d, u }' "$WORK/deltaA.out"
    awk -F'|' '$1 == "STAT" && $2 == "DB CPU" { printf "  Opening the link (logon, link set-up, one trivial query) had already cost %.1f ms DB CPU.\n", $3 / 1000 }' "$WORK/deltaA.out"
    echo
    echo "== Phase B: what one job run costs. A fresh session, collect() once, totals since the link session's logon"
    echo "  run  CPU used(ms)  DB CPU(ms)  DB time(ms)  logical reads  executes  parses  round trips  SQL*Net bytes"
    awk -F'|' '$1 == "RUN" { printf "  %3d  %12.0f  %10.1f  %11.1f  %13s  %8s  %6s  %11s  %13s\n", $2, $3 * 10, $4 / 1000, $5 / 1000, $6, $7, $8, $9, $10 }' "$WORK/runsB.out"
    awk -F'|' '$1 == "RUN" { n++; c += $3 * 10; d += $4 / 1000; t += $5 / 1000 }
               END { printf "  mean %11.1f  %10.1f  %11.1f\n", c / n, d / n, t / n }' "$WORK/runsB.out"
    echo
    awk -F'|' -v calls="$CALLS" '
        FNR == 1 { file++ }
        file == 1 && $1 == "STAT" && $2 == "DB CPU" { q = $5 / calls / 1000 }
        file == 2 && $1 == "RUN" { n++; d += $4 / 1000 }
        END { m = d / n
              printf "Summary: one collection costs the target about %.0f ms of DB CPU a minute: about %.0f ms for the\n", m, q
              printf "two metric reads (phase A) and about %.0f ms for opening the link session, which the job does on\n", m - q
              printf "every run. That is %.2f%% of one CPU (the target has 2).\n", m / 600 }' \
        "$WORK/deltaA.out" "$WORK/runsB.out"
  } > "$OUT"
}

# per-minute rows from the concatenated snapshots ($1: FILE|k markers + snapshot() output), on stdout:
#   TARGETVER|version|cpus, WINDOW|first AT|last AT, and per minute k >= 1
#   ROW|time|kind|sid,serial#|logon|DB CPU ms|CPU used ms|DB time ms|logical reads|executes|parses|round trips|FREEPDB1 logons
# kind: SAME = the same single link session as a minute before (the delta is one collect()); NEW = a link
# session that logged on since (its totals since logon); NONE / MULTI = no or several ANOM_MON sessions.
minute_rows() {
  awk -F'|' '
  $1 == "FILE"   { k = $2 + 0; nk = k; next }
  $1 == "AT"     { at[k] = $2 }
  $1 == "LOGONS" { lg[k] = $2 + 0 }
  $1 == "TARGET" { ver = $2 }
  $1 == "CPUS"   { cpus = $2 }
  $1 == "SNAP"   { key = $2 "|" $3
                   if (!((k, key) in seen)) { seen[k, key] = 1; ns[k]++; one[k] = key; logon[key] = $4 }
                   v[k, key, $5] = $6 }
  END {
    printf "TARGETVER|%s|%s\n", ver, cpus
    printf "WINDOW|%s|%s\n", at[0], at[nk]
    for (k = 1; k <= nk; k++) {
      cur = (ns[k] == 1) ? one[k] : ""; prev = (ns[k - 1] == 1) ? one[k - 1] : ""
      if (ns[k] > 1) kind = "MULTI"; else if (cur == "") kind = "NONE"; else if (cur == prev) kind = "SAME"; else kind = "NEW"
      dcpu = 0; ucpu = 0; dbt = 0; lio = 0; ex = 0; pa = 0; rt = 0
      if (kind == "SAME" || kind == "NEW") {
        b = (kind == "SAME") ? k - 1 : -1
        dcpu = v[k, cur, "DB CPU"] - (b < 0 ? 0 : v[b, cur, "DB CPU"])
        ucpu = v[k, cur, "CPU used by this session"] - (b < 0 ? 0 : v[b, cur, "CPU used by this session"])
        dbt  = v[k, cur, "DB time"] - (b < 0 ? 0 : v[b, cur, "DB time"])
        lio  = v[k, cur, "session logical reads"] - (b < 0 ? 0 : v[b, cur, "session logical reads"])
        ex   = v[k, cur, "execute count"] - (b < 0 ? 0 : v[b, cur, "execute count"])
        pa   = v[k, cur, "parse count (total)"] - (b < 0 ? 0 : v[b, cur, "parse count (total)"])
        rt   = v[k, cur, "SQL*Net roundtrips to/from client"] - (b < 0 ? 0 : v[b, cur, "SQL*Net roundtrips to/from client"])
      }
      shown = cur; gsub(/[|]/, ",", shown)     # the key holds the field separator; never print it raw
      printf "ROW|%s|%s|%s|%s|%.1f|%.0f|%.1f|%d|%d|%d|%d|%d\n", substr(at[k], 12, 8), kind, shown, logon[cur],
             dcpu / 1000, ucpu * 10, dbt / 1000, lio, ex, pa, rt, lg[k] - lg[k - 1]
    }
  }' "$1"
}

# ================================================================================ persistent mode (92 v1.2)
persistent() {
  local k w now_s now_m last_min="" snap_sec action rep state first_at last_at
  log INFO "persistent mode: the collector loop's link session over $MINUTES minutes"

  obs_sysdba job <<'EOF'
whenever sqlerror exit failure
set heading off feedback off pagesize 0 linesize 300 trimspool on
select 'JOB|'||job_action||'|'||repeat_interval||'|'||state||'|'||enabled
  from dba_scheduler_jobs where owner = 'ANOMOPS' and job_name = 'ANOM_COLLECT_JOB';
exit
EOF
  IFS='|' read -r _ action rep state _ < <(grep '^JOB|' "$WORK/job.out") || die "ANOM_COLLECT_JOB not found"
  [[ "$action" == "PKG_ANOM_COLLECT.RUN_LOOP" ]] || die "the collector job runs $action, not the 92 v1.2 loop"
  JOB_SEC="$(sed -n 's/.*BYSECOND=\([0-9][0-9]*\).*/\1/p' <<<"$rep")"
  [[ "$JOB_SEC" =~ ^[0-9]{1,2}$ ]] || die "no BYSECOND in the job's repeat_interval"
  # 30 s after each collection: the collect() of the minute has finished, the next is 30 s away
  snap_sec=$(( (10#$JOB_SEC + 30) % 60 ))

  for ((k = 0; k <= MINUTES; k++)); do
    # the snapshot second of the next minute not yet sampled (at most about 61 s away)
    for ((w = 0; w < 700; w++)); do
      now_s=$((10#$(date -u +%S))); now_m="$(date -u +%H%M)"
      if (( now_s == snap_sec )) && [[ "$now_m" != "$last_min" ]]; then break; fi
      sleep 0.1
    done
    (( w < 700 )) || die "missed the snapshot second"
    last_min="$now_m"
    snapshot "p$k"
    log INFO "snapshot $k of $MINUTES"
  done

  # one row per minute (minute_rows above)
  : > "$WORK/all.out"
  for ((k = 0; k <= MINUTES; k++)); do printf 'FILE|%d\n' "$k" >> "$WORK/all.out"; cat "$WORK/p$k.out" >> "$WORK/all.out"; done
  minute_rows "$WORK/all.out" > "$WORK/rows.out"
  first_at="$(awk -F'|' '$1 == "WINDOW" { print $2 }' "$WORK/rows.out")"
  last_at="$(awk -F'|' '$1 == "WINDOW" { print $3 }' "$WORK/rows.out")"
  [[ "$first_at" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ && \
     "$last_at" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || die "no snapshot times"

  # the LOGONS signal as the collector stored it, for the intervals that lie inside the window
  {
    cat <<EOF
whenever sqlerror exit failure
set heading off feedback off pagesize 0 linesize 300 trimspool on
variable f varchar2(20)
variable t varchar2(20)
variable r varchar2(20)
exec :f := '$first_at'; :t := '$last_at'; :r := '$REF_END';
EOF
    cat <<'EOF'
select 'LG|'||to_char(begin_time, 'HH24:MI:SS')||'|'||to_char(logons, 'FM0.0000')||'|'
       ||round(logons * intsize_csec / 100)||'|'||sessions||'|'||source
  from ANOMOPS.METRIC_MINUTE
 where begin_time >= to_date(:f, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') and end_time <= to_date(:t, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
 order by begin_time;
select 'WIN|'||count(*)||'|'||to_char(avg(logons), 'FM0.0000')||'|'||to_char(avg(logons * intsize_csec / 100), 'FM990.00')
  from ANOMOPS.METRIC_MINUTE
 where begin_time >= to_date(:f, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') and end_time <= to_date(:t, 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
select 'REF|'||count(*)||'|'||to_char(avg(logons), 'FM0.0000')||'|'||to_char(avg(logons * intsize_csec / 100), 'FM990.00')
       ||'|'||to_char(min(begin_time), 'HH24:MI')||'|'||to_char(max(begin_time), 'HH24:MI')
  from (select logons, intsize_csec, begin_time from ANOMOPS.METRIC_MINUTE
         where :r is not null and begin_time < to_date(:r, 'YYYY-MM-DD"T"HH24:MI"Z"')
         order by begin_time desc fetch first 30 rows only);
exit
EOF
  } | obs_sysdba logons

  # ---------------------------------------------------------------- transcript
  mkdir -p "$(dirname "$OUT")"
  {
    echo "# v1.0 - transcript: the persistent-session collector's cost on the TARGET, measured $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# tools/anom_collector_cost.sh --persistent (app 900, phase 4 tune; 92 v1.2). Target = oradb1/FREEPDB1,"
    echo "# observer = ora26ai/ORCLPDB1. SYSDBA on the target (from CDB\$ROOT, filtered on FREEPDB1) read the ANOM_MON"
    echo "# link session's V\$SESSTAT / V\$SESS_TIME_MODEL and FREEPDB1's 'logons cumulative' once a minute, 30 s after"
    echo "# each collection. Nothing logged on as ANOMOPS for this measurement."
    echo
    awk -F'|' '$1 == "TARGETVER" { printf "Target            : Oracle %s, cpu_count %s\n", $2, $3 }' "$WORK/rows.out"
    echo "Collector job     : ANOMOPS.ANOM_COLLECT_JOB, $action, $rep (UTC), $state"
    echo "Window            : $first_at to $last_at, $MINUTES minutes, snapshots at second $snap_sec UTC"
    echo
    echo "== Per minute: the ANOM_MON link session between two snapshots (one collect() each)"
    echo "  snapshot  session          logon     DB CPU(ms)  CPU used(ms)  DB time(ms)  logical reads  executes  parses  round trips  FREEPDB1 logons"
    awk -F'|' '$1 == "ROW" {
        if ($3 == "SAME" || $3 == "NEW")
          printf "  %-8s  %-15s  %-8s  %10.1f  %12.0f  %11.1f  %13d  %8d  %6d  %11d  %15d%s\n", $2, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                 ($3 == "NEW" ? "  (new session: totals since logon)" : "")
        else
          printf "  %-8s  %-15s  %-8s  %10s  %12s  %11s  %13s  %8s  %6s  %11s  %15d  (%s ANOM_MON session)\n", $2, "-", "-", "-", "-", "-", "-", "-", "-", "-", $13,
                 ($3 == "NONE" ? "no" : "more than one") }' "$WORK/rows.out"
    awk -F'|' '$1 == "ROW" && $3 == "SAME" { n++; d += $6; c += $7; t += $8; l += $9; e += $10; p += $11; r += $12 }
               $1 == "ROW" { lg += $13; rows++ }
               $1 == "ROW" && $3 == "NEW" { newer++ }
               $1 == "ROW" && $3 != "NEW" && $3 != "SAME" { odd++ }
               END { if (n) printf "  mean (%d minutes, same session)       %10.1f  %12.0f  %11.1f  %13.1f  %8.1f  %6.1f  %11.1f\n", n, d / n, c / n, t / n, l / n, e / n, p / n, r / n
                     printf "Link logons in the window: %d; minutes without exactly one link session: %d; FREEPDB1 logons (all users): %d in %d minutes\n", newer + 0, odd + 0, lg, rows }' "$WORK/rows.out"
    echo
    echo "== LOGONS on the target, the signal the collector stores (ANOMOPS.METRIC_MINUTE, intervals inside the window)"
    echo "  begin(UTC)  Logons Per Sec  logons  Session Count  source"
    awk -F'|' '$1 == "LG" { printf "  %-10s  %14s  %6s  %13s  %s\n", $2, $3, $4, $5, $6 }' "$WORK/logons.out"
    awk -F'|' '$1 == "WIN" { printf "  mean over %s intervals: %s logons/s (%s a minute)\n", $2, $3, $4 }' "$WORK/logons.out"
    awk -F'|' '$1 == "REF" && $2 > 0 { printf "  reference, the %s intervals %s-%s UTC before the change: %s logons/s (%s a minute)\n", $2, $5, $6, $3, $4 }' "$WORK/logons.out"
    echo
    awk -F'|' '$1 == "ROW" && $3 == "SAME" { n++; d += $6; l += $9 }
               $1 == "ROW" && $3 == "NEW" { newer++ }
               END { if (!n) { print "Summary: no minute with one persistent link session; nothing to summarise."; exit }
                     printf "Summary: with one link session kept open, a collection costs the target about %.1f ms of DB CPU and\n", d / n
                     printf "%.1f logical reads a minute (%.3f%% of one CPU; the target has 2), and the collector logged on %d time(s)\n", l / n, d / n / 600, newer + 0
                     printf "in %d minutes. Phase 3 measured about 116 ms a minute for the per-run job (02-collector-cost-over-link.txt),\n", n + newer
                     printf "about 103 ms of it the link logon it repeated every minute; the loop logs on once an hour.\n" }' "$WORK/rows.out"
  } > "$OUT"
}

case "$MODE" in
  per-run)    per_run ;;
  persistent) persistent ;;
esac
guard "$OUT"
cat "$OUT"
log INFO "transcript written to $OUT"
