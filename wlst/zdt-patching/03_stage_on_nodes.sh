#!/usr/bin/env bash
# v1.8 2026-11-11 - Codex r2: the work-folder answer is compared as a whole string (dirname/basename dropped trailing
#                    newlines); the parent check uses stat -L (a symlinked parent is judged by its target) and fails
#                    closed when stat fails.
# v1.7 2026-11-11 - Codex r1 on v1.6: the work-folder answer must be exactly one generated name under the target's
#                    parent (else it is never used or removed); a world-writable, non-sticky parent is refused; the
#                    checksum is taken from stdin; a failed unpack removes the folder; mv -T never nests the JDK.
# v1.6 2026-11-11 - security review: the JDK tarball goes to a private mktemp -d folder beside JAVA_HOME_NEW, not a
#                    predictable /tmp path, and its sha256 is checked on the node before unpacking.
# v1.5 2026-11-11 - blog copy: "Phase N" labels from an earlier plan removed; impact stated as offline or rolling.
# v1.4 2026-10-15 - hook files written to a temporary name and renamed (safe when the build host is also a node).
# v1.3 2026-10-15 - shared-home hooks: HOOK_JAR, HOOK_PROPS and HOOK_CONFIG copied to the same paths on every node.
# v1.2 2026-10-15 - files go to the nodes through ssh into an escaped path (no scp path parsing); a cacerts password
#                    containing a line break is refused.
# v1.1 2026-10-15 - remote arguments shell-escaped; remote steps run with pipefail and their exit status is checked;
#                    the cacerts password goes to the node over stdin, never on a command line; an existing
#                    JAVA_HOME_NEW that is not a JDK is refused instead of nesting the new JDK inside it.
# v1.0 2026-10-15 - Offline; servers keep running. From the build host, for every node of the domain:
#   - copy the patched Oracle home archive into the parent folder of the Oracle home (ZDT does not distribute it)
#     and verify its checksum on the node;
#   - check that the backup path the rollout will use is still free;
#   - optionally unpack the new JDK at JAVA_HOME_NEW (same path on every node, installed while Node Manager and the
#     servers keep running, as the ZDT guide asks) and report CA certificates present in the current JDK's cacerts
#     but missing from the new one (carry-over is our recommendation; the ZDT guide does not cover cacerts).
#   Nothing is stopped or changed in the running Oracle home or JDK. Safe to re-run.
# Usage: ./03_stage_on_nodes.sh <domain>.env [--jdk-only|--oh-only]
set -euo pipefail
umask 077

ENV_FILE=${1:?usage: $0 <domain>.env [--jdk-only|--oh-only]}
MODE=${2:-all}
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${DOMAIN_NAME:?}" "${NODES:?}" "${SSH_USER:?}" "${ORACLE_HOME:?}" "${STAGE_DIR:?}" "${CYCLE:?}"

LOG="$STAGE_DIR/logs/stage_${CYCLE}_$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p "$STAGE_DIR/logs"
log() { printf '%s %-5s %s\n' "$(date -u +%FT%TZ)" "$1" "$2" | tee -a "$LOG" >&2; }
fail() { log ERROR "$1"; exit 1; }
q() { printf '%q' "$1"; }                       # quote one argument for the remote shell
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)
# copy a local file to an exact path on a node (path escaped like every other remote argument)
put() { "${SSH[@]}" "$SSH_USER@$1" "cat > $(q "$3")" < "$2"; }
# run a bash script (stdin) on a node with escaped arguments
remote() { local node=$1; shift; local a=""; for x in "$@"; do a+=" $(q "$x")"; done
           "${SSH[@]}" "$SSH_USER@$node" "bash -s --$a"; }

PATCHED_JAR="$STAGE_DIR/$(basename "$ORACLE_HOME")_patched_${CYCLE}.jar"
OH_PARENT=$(dirname "$ORACLE_HOME")
BACKUP_OH="${ORACLE_HOME}_backup_${CYCLE}"
REMOTE_JAR="$OH_PARENT/$(basename "$PATCHED_JAR")"
SUM=""

if [[ "$MODE" != "--jdk-only" ]]; then
  [[ -r "$PATCHED_JAR" && -r "$PATCHED_JAR.sha256" ]] || fail "archive or checksum missing: run 02 first"
  SUM=$(cut -d' ' -f1 "$PATCHED_JAR.sha256")
  [[ "$(sha256sum "$PATCHED_JAR" | cut -d' ' -f1)" == "$SUM" ]] || fail "local archive checksum mismatch"
fi
DO_JDK=no
if [[ "$MODE" != "--oh-only" && -n "${JDK_TARBALL:-}" ]]; then
  : "${JAVA_HOME_NEW:?}" "${JAVA_HOME_CURRENT:?}"
  [[ -r "$JDK_TARBALL" ]] || fail "JDK_TARBALL not readable: $JDK_TARBALL"
  JDK_SUM=$(sha256sum < "$JDK_TARBALL" | cut -d' ' -f1)   # checked on every node before unpacking; stdin, so no name escaping
  DO_JDK=yes
fi
if [[ "${CACERTS_PASS:-}" == *$'\n'* || "${CACERTS_PASS:-}" == *$'\r'* ]]; then
  fail "CACERTS_PASS contains a line break; refusing to send it"
fi

stage_oh() {   # $1 = node
  local node=$1 state
  # prints one word: BACKUP_EXISTS | IN_PLACE | MISSING, plus free and needed KB
  state=$(remote "$node" "$BACKUP_OH" "$REMOTE_JAR" "$SUM" "$OH_PARENT" "$ORACLE_HOME" <<'REMOTE'
set -euo pipefail
backup=$1; jar=$2; sum=$3; parent=$4; oh=$5
free_kb=$(df -Pk "$parent" | awk 'NR==2{print $4}')
oh_kb=$(du -sk "$oh" | cut -f1)
if [ -e "$backup" ]; then echo "BACKUP_EXISTS $free_kb $oh_kb"; exit 0; fi
if [ -r "$jar" ] && echo "$sum  $jar" | sha256sum -c --status; then echo "IN_PLACE $free_kb $oh_kb"; else echo "MISSING $free_kb $oh_kb"; fi
REMOTE
  ) || { log ERROR "$node: remote check failed"; return 1; }
  read -r word free_kb oh_kb <<< "$state"
  case $word in
    BACKUP_EXISTS) log ERROR "$node: backup path $BACKUP_OH already exists (the rollout moves the current home there)"; return 1;;
    IN_PLACE) log INFO "$node: archive already in place, checksum OK";;
    MISSING)
      put "$node" "$PATCHED_JAR" "$REMOTE_JAR.part" || { log ERROR "$node: archive copy failed"; return 1; }
      remote "$node" "$REMOTE_JAR" "$SUM" <<'REMOTE' || { log ERROR "$node: checksum failed after copy"; return 1; }
set -euo pipefail
jar=$1; sum=$2
echo "$sum  $jar.part" | sha256sum -c --status
mv "$jar.part" "$jar"
REMOTE
      log INFO "$node: archive copied to $REMOTE_JAR, checksum OK";;
    *) log ERROR "$node: unexpected answer: $state"; return 1;;
  esac
  # the node will hold the archive, the new home and the backup home at the same time
  if (( free_kb < oh_kb + 1048576 )); then
    log WARN "$node: only $((free_kb/1024)) MB free under $OH_PARENT for a new home of ~$((oh_kb/1024)) MB"
  fi
}

stage_jdk() {  # $1 = node
  local node=$1 work parent rest ver missing
  if remote "$node" "$JAVA_HOME_NEW" <<'REMOTE'
set -euo pipefail
target=$1
if [ -x "$target/bin/java" ]; then exit 0; fi
if [ -e "$target" ]; then echo "exists but is not a JDK: $target" >&2; exit 2; fi
exit 1
REMOTE
  then
    log INFO "$node: $JAVA_HOME_NEW already present"
  else
    rc=$?
    (( rc == 1 )) || { log ERROR "$node: $JAVA_HOME_NEW exists and is not a JDK; remove it or choose another path"; return 1; }
    # a private work folder (mktemp -d, mode 700) beside the target: the tarball never sits in a shared /tmp, where
    # another local user could plant or swap the file between the copy and the unpack. This assumes the parent of
    # JAVA_HOME_NEW is writable only by trusted users (the software owner, its install group); a world-writable parent
    # without the sticky bit is refused, because then nothing created beneath it can be trusted.
    work=$(remote "$node" "$JAVA_HOME_NEW" <<'REMOTE'
set -euo pipefail
parent=$(dirname "$1")
mkdir -p -- "$parent"
mode=$(stat -L -c %a -- "$parent")     # -L: a symlinked parent is judged by its target; a failed stat exits (set -e)
if (( (8#$mode & 8#1002) == 8#0002 )); then
  echo "$parent is writable by every user (mode $mode); refusing to stage the JDK there" >&2; exit 1
fi
mktemp -d "$parent/.zdt_jdk.XXXXXX"
REMOTE
    ) || { log ERROR "$node: could not create a private work folder beside $JAVA_HOME_NEW"; return 1; }
    # exactly one generated name directly under the parent, compared as whole strings (dirname and basename in a
    # command substitution drop trailing newlines); any other answer is never used, let alone removed
    parent=$(dirname "$JAVA_HOME_NEW")
    rest=${work#"$parent/"}
    if [[ "$work" != "$parent/$rest" || ! "$rest" =~ ^\.zdt_jdk\.[A-Za-z0-9]{6}$ ]]; then
      log ERROR "$node: unexpected work folder answer, not used: $work"; return 1
    fi
    put "$node" "$JDK_TARBALL" "$work/jdk.tar.gz" || {
      log ERROR "$node: JDK copy failed"; remote "$node" "$work" <<< 'rm -rf -- "$1"' || true; return 1; }
    # check the copy against the local checksum, unpack inside the work folder, then rename the JDK directory to the
    # exact JAVA_HOME_NEW path; the work folder goes whatever happens (trap there, and again from here on failure)
    if ! remote "$node" "$work" "$JDK_SUM" "$JAVA_HOME_NEW" <<'REMOTE'
set -euo pipefail
work=$1; sum=$2; target=$3
trap 'rm -rf -- "$work"' EXIT
echo "$sum  $work/jdk.tar.gz" | sha256sum -c --status || { echo "JDK tarball checksum mismatch" >&2; exit 1; }
mkdir "$work/x"
tar -xzf "$work/jdk.tar.gz" -C "$work/x"
top=$(find "$work/x" -mindepth 1 -maxdepth 1 -type d | head -1)
[ -n "$top" ] && [ -x "$top/bin/java" ] || { echo "no bin/java in the archive" >&2; exit 1; }
mv -T -- "$top" "$target"           # -T: fails rather than nesting if the target appeared meanwhile
REMOTE
    then
      log ERROR "$node: JDK checksum or unpack failed"
      remote "$node" "$work" <<< 'rm -rf -- "$1"' || true
      return 1
    fi
    log INFO "$node: JDK unpacked to $JAVA_HOME_NEW"
  fi
  ver=$(remote "$node" "$JAVA_HOME_NEW" <<'REMOTE'
set -euo pipefail
"$1/bin/java" -version 2>&1 | head -1
REMOTE
  ) || { log ERROR "$node: $JAVA_HOME_NEW/bin/java does not run"; return 1; }
  log INFO "$node: new JDK reports: $ver"

  if [[ -z "${CACERTS_PASS:-}" ]]; then
    log WARN "$node: CACERTS_PASS not set, cacerts comparison skipped"; return 0
  fi
  # first stdin line carries the password to the remote 'read'; it never appears in a process argument list
  missing=$( { printf 'IFS= read -r CACERTS_PASS; export CACERTS_PASS\n%s\n' "$CACERTS_PASS"; cat <<'REMOTE'
set -euo pipefail
cac() { for f in "$1/lib/security/cacerts" "$1/jre/lib/security/cacerts"; do [ -f "$f" ] && { echo "$f"; return 0; }; done; return 1; }
old=$(cac "$1") || { echo "no cacerts under $1" >&2; exit 1; }
new=$(cac "$2") || { echo "no cacerts under $2" >&2; exit 1; }
list() { "$2/bin/keytool" -list -keystore "$1" -storepass:env CACERTS_PASS > "$3"; awk -F, '/[Ee]ntry,/{print $1}' "$3" | sort; }
o=$(mktemp); n=$(mktemp); trap 'rm -f "$o" "$n" "$o.l" "$n.l"' EXIT
list "$old" "$2" "$o.l" > "$o"
list "$new" "$2" "$n.l" > "$n"
[ -s "$o" ] && [ -s "$n" ] || { echo "keytool listed no entries" >&2; exit 1; }
comm -23 "$o" "$n"
REMOTE
  } | "${SSH[@]}" "$SSH_USER@$node" "bash -s -- $(q "$JAVA_HOME_CURRENT") $(q "$JAVA_HOME_NEW")" ) \
    || { log ERROR "$node: cacerts comparison failed (wrong CACERTS_PASS or keystore not found)"; return 1; }
  if [[ -n "$missing" ]]; then
    log WARN "$node: CA entries in the current JDK but not in the new one (import them before the rollout): $(echo "$missing" | tr '\n' ' ')"
  else
    log INFO "$node: no CA entries missing from the new JDK cacerts"
  fi
}

errors=0
for node in $NODES; do
  log INFO "== $node"
  if ! "${SSH[@]}" "$SSH_USER@$node" true; then log ERROR "$node: ssh failed"; errors=$((errors+1)); continue; fi
  if [[ "$MODE" != "--jdk-only" ]]; then stage_oh "$node" || { errors=$((errors+1)); continue; }; fi
  if [[ "$DO_JDK" == yes ]]; then stage_jdk "$node" || errors=$((errors+1)); fi
  if [[ -n "${HOOK_JAR:-}" ]]; then
    for f in "$HOOK_JAR" "${HOOK_PROPS:?HOOK_PROPS needed with HOOK_JAR}" "${HOOK_CONFIG:?HOOK_CONFIG needed with HOOK_JAR}"; do
      [[ -r "$f" ]] || { log ERROR "hook file not readable here: $f"; errors=$((errors+1)); continue; }
      { remote "$node" "$(dirname "$f")" <<< 'mkdir -p "$1"' && put "$node" "$f" "$f.zdtpart" \
          && remote "$node" "$f.zdtpart" "$f" <<< 'mv -f "$1" "$2"'; } \
        && log INFO "$node: hook file in place: $f" || { log ERROR "$node: hook file copy failed: $f"; errors=$((errors+1)); }
    done
  fi
done

(( errors == 0 )) || fail "$errors node(s) failed; fix and re-run (the script is safe to re-run)"
log INFO "all nodes staged for $DOMAIN_NAME ($CYCLE)"
