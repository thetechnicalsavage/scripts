#!/usr/bin/env bash
# v1.5 2026-11-11 - blog copy: "Phase N" labels from an earlier plan removed; impact stated as offline or rolling.
# v1.4 2026-10-15 - no '!/awk/' filter (it could hide servers whose paths contain 'awk').
# v1.3 2026-10-15 - server name parsed without dropping its first character.
# v1.2 2026-10-15 - this domain's servers found by working directory (DOMAIN_HOME); a node with none fails, and
#                    --expect-java=<path> fails any server not running that JDK.
# v1.1 2026-10-15 - remote arguments shell-escaped; a node fails when OPatch fails or reports no patches, so empty
#                    lists can no longer pass as "the same on every node".
# v1.0 2026-10-15 - Read-only. After a rollout or rollback, on every node of the domain: the patches in the
#   Oracle home (opatch lsinventory), the Java version each running WebLogic server uses, and whether the backup home
#   is present for a later rollback. Fails if the nodes do not report the same patch list.
# Usage: ./06_verify.sh <domain>.env [--expect-java=<JDK path>]
set -euo pipefail
umask 077

ENV_FILE=${1:?usage: $0 <domain>.env [--expect-java=<JDK path>]}
EXPECT_JAVA=""
[[ "${2:-}" == --expect-java=* ]] && EXPECT_JAVA=${2#--expect-java=}
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${DOMAIN_NAME:?}" "${NODES:?}" "${SSH_USER:?}" "${ORACLE_HOME:?}" "${DOMAIN_HOME:?}" "${CYCLE:?}"

OUT=./verify_${DOMAIN_NAME}_${CYCLE}_$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$OUT"
log() { printf '%s %-5s %s\n' "$(date -u +%FT%TZ)" "$1" "$2" | tee -a "$OUT/verify.log" >&2; }
q() { printf '%q' "$1"; }
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)

errors=0
for node in $NODES; do
  log INFO "== $node"
  if ! "${SSH[@]}" "$SSH_USER@$node" "bash -s -- $(q "$ORACLE_HOME") $(q "$CYCLE") $(q "$DOMAIN_HOME")" > "$OUT/$node.txt" 2>&1 <<'REMOTE'
set -euo pipefail
oh=$1; cycle=$2; dom=$3
inv=$(mktemp); trap 'rm -f "$inv"' EXIT
ORACLE_HOME="$oh" "$oh/OPatch/opatch" lsinventory -oh "$oh" > "$inv" 2>&1 || { echo "opatch lsinventory failed:"; tail -5 "$inv"; exit 1; }
echo "### patches"
grep -E '^Patch +[0-9]+' "$inv" | awk '{print $2}' | sort -u || true
echo "### servers"
# servers of this domain run with the domain home as working directory; the binary path tells the JDK in use
ps -eo pid=,args= | awk '/weblogic\.Server/ {n="-"; for (i=1;i<=NF;i++) if ($i ~ /^-Dweblogic\.Name=/) n=substr($i,17); print $1, n}' |
while read -r pid name; do
  [ "$(readlink "/proc/$pid/cwd" 2>/dev/null || true)" = "$dom" ] || continue
  exe=$(readlink "/proc/$pid/exe" 2>/dev/null || echo unknown)
  echo "$name $exe $("$exe" -version 2>&1 | head -1 || true)"
done
echo "### backup"
if [ -d "${oh}_backup_${cycle}" ]; then echo "backup home present: ${oh}_backup_${cycle}"; else echo "no backup home ${oh}_backup_${cycle}"; fi
REMOTE
  then
    log ERROR "$node: remote check failed (see $OUT/$node.txt)"; errors=$((errors+1)); continue
  fi
  sed -n '/^### patches/,/^### servers/p' "$OUT/$node.txt" | grep -E '^[0-9]+$' > "$OUT/$node.patches" || true
  npatch=$(wc -l < "$OUT/$node.patches")
  if (( npatch == 0 )); then
    log ERROR "$node: no patches parsed from lsinventory (see $OUT/$node.txt)"; errors=$((errors+1)); continue
  fi
  sed -n '/^### servers/,/^### backup/p' "$OUT/$node.txt" | grep -v '^###' > "$OUT/$node.servers" || true
  nsrv=$(wc -l < "$OUT/$node.servers")
  if (( nsrv == 0 )); then
    log ERROR "$node: no running server of this domain ($DOMAIN_HOME)"; errors=$((errors+1)); continue
  fi
  if [[ -n "$EXPECT_JAVA" ]]; then
    while read -r name exe _; do
      case "$exe" in "$EXPECT_JAVA"/*) ;; *) log ERROR "$node: $name runs $exe, expected under $EXPECT_JAVA"; errors=$((errors+1));; esac
    done < "$OUT/$node.servers"
  fi
  log INFO "$node: $npatch patches; $nsrv server(s) of this domain running"
done

# every node must report the same patch list
first=""
for node in $NODES; do
  [[ -s "$OUT/$node.patches" ]] || continue
  if [[ -z "$first" ]]; then first=$node; continue; fi
  if ! diff -q "$OUT/$first.patches" "$OUT/$node.patches" > /dev/null; then
    log ERROR "patch list differs between $first and $node"; errors=$((errors+1))
  fi
done
(( errors == 0 )) || { log ERROR "$errors problem(s); details in $OUT"; exit 1; }
log INFO "all nodes report the same patches; details in $OUT"
