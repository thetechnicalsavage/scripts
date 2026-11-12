#!/usr/bin/env bash
# v1.3 2026-11-11 - blog copy: "Phase N" labels from an earlier plan removed; impact stated as offline or rolling.
# v1.2 2026-10-15 - server name parsed from -Dweblogic.Name= without dropping its first character.
# v1.1 2026-10-15 - optional lookups no longer abort the run under set -e/pipefail (missing file or no match).
# v1.0 2026-10-15 - Read-only inventory of one node: Oracle homes (central inventory), the domains created
#   from each home (domain-registry.xml), each domain's JAVA_HOME and Node Manager settings, and running WebLogic
#   processes. Changes nothing. Run on every node as the oracle software owner; collect the CSV files centrally.
# Usage: ./01_inventory.sh [output_dir]        (default ./inventory_<host>_<UTC timestamp>)
set -euo pipefail

HOST=$(hostname -s)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${1:-./inventory_${HOST}_${STAMP}}
umask 077
mkdir -p "$OUT"
log() { printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$1" "$2" | tee -a "$OUT/inventory.log" >&2; }

# Central inventory location: /etc/oraInst.loc on Linux (inventory_loc=...)
INV_LOC=""
if [[ -r /etc/oraInst.loc ]]; then
  INV_LOC=$(sed -n 's/^inventory_loc=//p' /etc/oraInst.loc | head -1)
else
  log WARN "/etc/oraInst.loc not readable; pass Oracle homes manually via ORACLE_HOMES=\"/path1 /path2\""
fi

declare -a HOMES=()
if [[ -n "${ORACLE_HOMES:-}" ]]; then
  read -r -a HOMES <<< "$ORACLE_HOMES"
elif [[ -n "$INV_LOC" && -r "$INV_LOC/ContentsXML/inventory.xml" ]]; then
  # Registered homes that are not marked REMOVED
  mapfile -t HOMES < <(grep -o '<HOME [^>]*>' "$INV_LOC/ContentsXML/inventory.xml" \
                        | grep -v 'REMOVED="T"' | sed -n 's/.*LOC="\([^"]*\)".*/\1/p' | sort -u)
else
  log ERROR "no central inventory found and ORACLE_HOMES not set"
  exit 1
fi
log INFO "oracle homes found: ${#HOMES[@]}"

echo "host,oracle_home,wls_version,home_java,domain_home" > "$OUT/homes_domains.csv"
echo "host,domain_home,java_home_setDomainEnv,nm_JavaHome,nm_ListenPort,admin_server_on_this_host" > "$OUT/domains.csv"

for OH in "${HOMES[@]}"; do
  [[ -d "$OH/wlserver" ]] || { log INFO "skip $OH (not a WebLogic home)"; continue; }
  VER=$(sed -n 's/.*<distribution status="installed" name="WebLogic Server" version="\([^"]*\)".*/\1/p' \
          "$OH/inventory/registry.xml" 2>/dev/null | head -1 || true)
  [[ -n "$VER" ]] || VER=$(grep -o 'name="WebLogic Server" version="[^"]*"' "$OH/inventory/registry.xml" 2>/dev/null \
                            | head -1 | sed 's/.*version="\([^"]*\)".*/\1/' || true)
  HJAVA=$(sed -n 's/^JAVA_HOME=//p' "$OH/oui/.globalEnv.properties" 2>/dev/null | head -1 || true)
  DOMS=()
  if [[ -r "$OH/domain-registry.xml" ]]; then
    mapfile -t DOMS < <(grep -o 'location="[^"]*"' "$OH/domain-registry.xml" | sed 's/location="\(.*\)"/\1/' || true)
  else
    log WARN "$OH has no domain-registry.xml"
  fi
  if [[ ${#DOMS[@]} -eq 0 ]]; then
    echo "$HOST,$OH,${VER:-unknown},${HJAVA:-unknown}," >> "$OUT/homes_domains.csv"
  fi
  for D in "${DOMS[@]}"; do
    echo "$HOST,$OH,${VER:-unknown},${HJAVA:-unknown},$D" >> "$OUT/homes_domains.csv"
    SDE_JAVA=$(grep -E '^\s*(export\s+)?JAVA_HOME=' "$D/bin/setDomainEnv.sh" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true)
    NMP="$D/nodemanager/nodemanager.properties"
    NM_JAVA=$(sed -n 's/^JavaHome=//p' "$NMP" 2>/dev/null | head -1 || true)
    NM_PORT=$(sed -n 's/^ListenPort=//p' "$NMP" 2>/dev/null | head -1 || true)
    ADMIN_HERE=no
    ADMIN_NAME=$(sed -n 's:.*<admin-server-name>\(.*\)</admin-server-name>.*:\1:p' "$D/config/config.xml" 2>/dev/null | head -1 || true)
    if [[ -n "$ADMIN_NAME" && -d "$D/servers/$ADMIN_NAME" ]]; then ADMIN_HERE=yes; fi
    echo "$HOST,$D,${SDE_JAVA:-unset},${NM_JAVA:-unset},${NM_PORT:-unset},$ADMIN_HERE" >> "$OUT/domains.csv"
  done
done

# Running WebLogic servers and Node Managers (name, home and java taken from the command line)
{
  echo "host,pid,kind,server_name,java"
  ps -eo pid=,args= | awk -v h="$HOST" '
    /weblogic\.Server/ { n=""; for (i=1;i<=NF;i++) if ($i ~ /^-Dweblogic\.Name=/) { n=substr($i,17) }
                         print h "," $1 ",server," n "," $2; next }
    /weblogic\.NodeManager/ { print h "," $1 ",nodemanager,," $2 }'
} > "$OUT/processes.csv"

# Shared homes: an Oracle home listed for more than one domain on this node
awk -F, 'NR>1 && $5!="" {c[$2]++} END {for (h in c) if (c[h]>1) print h "," c[h]}' "$OUT/homes_domains.csv" \
  > "$OUT/shared_homes.csv"
if [[ -s "$OUT/shared_homes.csv" ]]; then
  log WARN "shared Oracle homes on $HOST (home,domains): $(tr '\n' ' ' < "$OUT/shared_homes.csv")"
else
  log INFO "no Oracle home is shared by two domains on $HOST"
fi
log INFO "done: $OUT"
