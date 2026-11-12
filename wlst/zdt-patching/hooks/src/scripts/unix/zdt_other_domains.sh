#!/bin/sh
# v1.7 2026-10-15 - lead Node Manager port parsed with optional spaces and ':' (Java properties forms); hook fails
#                    closed when it cannot be determined.
# v1.6 2026-10-15 - refuses to run when an other domain's Node Manager port equals the lead domain's (a shared
#                    Node Manager would be stopped under the workflow).
# v1.5 2026-10-15 - ps output checked on its own (a failed ps no longer reads as 'nothing running'); no '!/awk/'
#                    exclusion (it could hide real servers whose paths contain 'awk').
# v1.4 2026-10-15 - stop check: own-user WebLogic processes must be inspectable and not run from an other domain's
#                    home; other users' WebLogic processes count when their command line uses the shared home.
# v1.3 2026-10-15 - stop check finds WebLogic processes by command line (weblogic.Server / weblogic.NodeManager),
#                    fails if one cannot be inspected, and compares against the canonical (symlink-free) domain home.
# v1.2 2026-10-15 - after 'stop' the node's processes are checked: no java process may still run from an other
#                    domain's home (waits up to stopWait, default 300 s); otherwise the hook fails (fail closed).
# v1.1 2026-10-15 - settings-file read checked (a failed read no longer means 'nothing to do'); every WLST call
#                    bounded by hookTimeout (default 3600 s) when coreutils timeout is available.
# v1.0 2026-10-15 - body of the two ZDT hooks. Runs on the node being updated, inside the lead domain's workflow, for a
#   shared Oracle home that other domains also run from on this node:
#     stop   gracefully stop the other domains' servers on this node, then their Node Manager
#     start  start their Node Manager, their Administration Server if it is here, then their Managed Servers
#   ZDT passes mwHome, isRevert, ... and the extension parameters (configFile, shutdownTimeout) as environment
#   variables. Exit 0 = success; any other exit code makes the workflow roll back. On a revert each hook does the
#   opposite of its normal action, so the other domains follow the home they run from.
# Usage (from the two hook scripts): zdt_other_domains.sh stop|start
set -u
MODE=${1:?mode stop|start}
HERE=$(cd "$(dirname "$0")" && pwd)
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) zdt-hook $MODE: $*"; }    # goes to the Administration Server log

case "$(printf '%s' "${isRevert:-false}" | tr '[:upper:]' '[:lower:]')" in
  true) if [ "$MODE" = stop ]; then ACTION=start; else ACTION=stop; fi; log "revert: running '$ACTION'";;
  *)    ACTION=$MODE;;
esac
CONFIG=${configFile:-}
[ -n "$CONFIG" ] && [ -r "$CONFIG" ] || { log "configFile not set or not readable: '$CONFIG'"; exit 2; }
WLST="${mwHome:-}/oracle_common/common/bin/wlst.sh"
[ -x "$WLST" ] || { log "wlst.sh not found under mwHome='${mwHome:-}'"; exit 2; }

# rows of the settings file for this node (short or full host name)
HS=$(hostname -s 2>/dev/null || hostname); HF=$(hostname -f 2>/dev/null || hostname)
ROWFILE=$(mktemp) || { log "mktemp failed"; exit 2; }
trap 'rm -f "$ROWFILE"' EXIT
if ! awk -F, -v s="$HS" -v f="$HF" '$0 !~ /^[[:space:]]*(#|$)/ && ($1 == s || $1 == f)' "$CONFIG" > "$ROWFILE"; then
  log "could not read $CONFIG"; exit 2
fi
# an outer time limit for every WLST call, so a stuck call fails the hook instead of hanging the workflow
LIMIT=""
if command -v timeout > /dev/null 2>&1; then LIMIT="timeout ${hookTimeout:-3600}"; fi
if [ ! -s "$ROWFILE" ]; then log "no other domain on this node ($HS): nothing to do"; exit 0; fi
log "$(wc -l < "$ROWFILE" | tr -d ' ') other domain(s) on $HS"

# the hook stops the other domains' own Node Manager: it must not be the one the workflow uses on this node
NMP="${domainDir:-}/nodemanager/nodemanager.properties"
LEAD_NM_PORT=$(sed -n 's/^[[:space:]]*ListenPort[[:space:]]*[=:][[:space:]]*\([0-9][0-9]*\).*/\1/p' "$NMP" 2>/dev/null | head -1)
if [ -z "$LEAD_NM_PORT" ]; then
  log "cannot read the lead domain's Node Manager ListenPort from $NMP; refusing to continue"
  exit 2
fi
if awk -F, -v p="$LEAD_NM_PORT" '$8 == p {found=1} END {exit !found}' "$ROWFILE"; then
  log "an other domain uses Node Manager port $LEAD_NM_PORT, the lead domain's; per-domain Node Managers required"
  exit 2
fi

# their Node Manager is started from the shell before the WLST part of 'start'
if [ "$ACTION" = start ]; then
  while IFS=, read -r _node domain dhome _rest; do
    if $LIMIT "$WLST" "$HERE/zdt_other_domains.py" nmcheck "$ROWFILE" "$domain" < /dev/null > /dev/null 2>&1; then
      log "$domain: Node Manager already running"; continue
    fi
    log "$domain: starting Node Manager"
    nohup "$dhome/bin/startNodeManager.sh" < /dev/null > "$dhome/nodemanager/zdt_nm_start.out" 2>&1 &
  done < "$ROWFILE"
fi

if ! $LIMIT "$WLST" "$HERE/zdt_other_domains.py" "$ACTION" "$ROWFILE" "${shutdownTimeout:-1800}" < /dev/null; then
  log "failed; the workflow will roll back"; exit 1
fi

# after a stop, trust only the node itself: no WebLogic process may still run from an other domain's home
if [ "$ACTION" = stop ]; then
  CANON=$(mktemp) || { log "mktemp failed"; exit 1; }
  trap 'rm -f "$ROWFILE" "$CANON"' EXIT
  while IFS=, read -r _node domain dhome _rest; do
    real=$(cd "$dhome" 2>/dev/null && pwd -P) || { log "$domain: domain home not found: $dhome"; exit 1; }
    echo "$domain $real" >> "$CANON"
  done < "$ROWFILE"
  ME=$(id -u)
  PSF=$(mktemp) || { log "mktemp failed"; exit 1; }
  trap 'rm -f "$ROWFILE" "$CANON" "$PSF" "$PSF.all"' EXIT
  waited=0
  while :; do
    left=""
    ps -eo pid=,uid=,args= > "$PSF.all" 2>/dev/null && [ -s "$PSF.all" ] || { log "ps failed"; exit 1; }
    awk '/weblogic\.(Server|NodeManager)/' "$PSF.all" > "$PSF" || { log "could not filter ps output"; exit 1; }
    while read -r pid uid args; do
      if [ "$uid" != "$ME" ]; then
        # another OS user: cannot inspect it, so it counts if it runs from the shared home being replaced
        case "$args" in *"$mwHome"*) left="$left other-user:$pid";; esac
        continue
      fi
      cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || { [ -d "/proc/$pid" ] && left="$left uninspectable:$pid"; continue; }
      while read -r domain real; do
        case "$cwd" in "$real"|"$real"/*) left="$left $domain:$pid";; esac
      done < "$CANON"
    done < "$PSF"
    [ -z "$left" ] && break
    if [ "$waited" -ge "${stopWait:-300}" ]; then
      log "still running after ${waited}s:$left; the workflow will roll back"; exit 1
    fi
    sleep 10; waited=$((waited + 10))
  done
  log "no process of the other domains left on $HS"
fi
log "done"
exit 0
