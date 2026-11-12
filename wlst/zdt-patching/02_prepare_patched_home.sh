#!/usr/bin/env bash
# v1.1 2026-11-11 - blog copy: "Phase N" labels from an earlier plan removed; impact stated as offline or rolling.
# v1.0 2026-10-15 - Offline; servers keep running. On the build host: clone the domain's Oracle home, patch the clone
#   with OPatch (or a Stack Patch Bundle with SPBAT), list its inventory, and package it as the archive that
#   rolloutOracleHome / rolloutUpdate rolls out. The running home is never touched. Steps follow "Preparing to
#   Patch" in the Oracle WebLogic Server ZDT guide: copyBinary -> pasteBinary -> OPatch -> copyBinary.
#   The build host needs a home identical to production (same version and patch level).
# Usage: ./02_prepare_patched_home.sh <domain>.env
set -euo pipefail
umask 077

ENV_FILE=${1:?usage: $0 <domain>.env}
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${DOMAIN_NAME:?}" "${ORACLE_HOME:?}" "${JAVA_HOME_INSTALL:?}" "${STAGE_DIR:?}" "${CYCLE:?}"

LOG_DIR="$STAGE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/prepare_${CYCLE}_$(date -u +%Y%m%dT%H%M%SZ).log"
log() { printf '%s %-5s %s\n' "$(date -u +%FT%TZ)" "$1" "$2" | tee -a "$LOG" >&2; }
fail() { log ERROR "$1"; exit 1; }
trap 'log ERROR "failed at line $LINENO (exit $?)"' ERR

BASE_JAR="$STAGE_DIR/base_${CYCLE}.jar"
CLONE="$STAGE_DIR/oh_${CYCLE}"
PATCHED_JAR="$STAGE_DIR/$(basename "$ORACLE_HOME")_patched_${CYCLE}.jar"

# Pre-checks: tools present, outputs absent (copyBinary refuses an existing archive, pasteBinary a non-empty target)
[[ -x "$ORACLE_HOME/oracle_common/bin/copyBinary.sh" ]] || fail "copyBinary.sh not found under $ORACLE_HOME"
[[ -x "$JAVA_HOME_INSTALL/bin/java" ]] || fail "JAVA_HOME_INSTALL is not a JDK: $JAVA_HOME_INSTALL"
case "$STAGE_DIR/" in "$ORACLE_HOME"/*) fail "STAGE_DIR must be outside the Oracle home";; esac
for p in "$BASE_JAR" "$PATCHED_JAR"; do [[ ! -e "$p" ]] || fail "$p already exists; use a new CYCLE or remove it"; done
[[ ! -e "$CLONE" ]] || [[ -z "$(ls -A "$CLONE")" ]] || fail "$CLONE exists and is not empty"
if [[ -z "${SPB_DIR:-}" && -z "${PATCH_IDS:-}" ]]; then fail "set PATCH_IDS or SPB_DIR"; fi

# ZDT suitability: the guide's test is the patch-uptime-option in each patch's etc/config/patchdeploy.xml
if [[ -n "${PATCH_IDS:-}" ]]; then
  : "${PATCH_STAGE:?}"
  IFS=',' read -r -a IDS <<< "$PATCH_IDS"
  for id in "${IDS[@]}"; do
    pd="$PATCH_STAGE/$id/etc/config/patchdeploy.xml"
    [[ -r "$pd" ]] || fail "patch $id: $pd not found"
    if grep -Eq 'FMW_ROLLING_ORACLE_HOME|FMW_ROLLING_SESSION' "$pd"; then
      log INFO "patch $id: ZDT-capable ($(grep -Eo 'FMW_ROLLING_[A-Z_]+' "$pd" | head -1))"
    else
      fail "patch $id is not marked FMW_ROLLING_ORACLE_HOME or FMW_ROLLING_SESSION: not suitable for ZDT"
    fi
  done
else
  log WARN "SPB mode: check each bundled patch's patchdeploy.xml as described in the SPB README before rollout"
fi

cd "$ORACLE_HOME/oracle_common/bin"
log INFO "1/4 copyBinary $ORACLE_HOME -> $BASE_JAR"
./copyBinary.sh -javaHome "$JAVA_HOME_INSTALL" -archiveLoc "$BASE_JAR" -sourceOracleHomeLoc "$ORACLE_HOME" \
  2>&1 | tee -a "$LOG"

log INFO "2/4 pasteBinary $BASE_JAR -> $CLONE"
./pasteBinary.sh -javaHome "$JAVA_HOME_INSTALL" -archiveLoc "$BASE_JAR" -targetOracleHomeLoc "$CLONE" \
  -targetOracleHomeName "zdt_${DOMAIN_NAME}_${CYCLE}" 2>&1 | tee -a "$LOG"

log INFO "3/4 patch the clone $CLONE"
export ORACLE_HOME="$CLONE"                      # OPatch acts on ORACLE_HOME; it now points at the clone only
if [[ -n "${SPB_DIR:-}" ]]; then
  cd "$SPB_DIR/tools/spbat/generic/SPBAT"
  ./spbat.sh -phase apply -oracle_home "$CLONE" 2>&1 | tee -a "$LOG"
else
  cd "$PATCH_STAGE"
  # shellcheck disable=SC2086   # OPATCH_EXTRA_ARGS is a deliberate word list
  "$CLONE/OPatch/opatch" napply -oh "$CLONE" -id "$PATCH_IDS" ${OPATCH_EXTRA_ARGS:-} 2>&1 | tee -a "$LOG"
fi
"$CLONE/OPatch/opatch" lsinventory -oh "$CLONE" > "$LOG_DIR/lsinventory_${CYCLE}.txt" 2>&1
log INFO "inventory of the patched clone: $LOG_DIR/lsinventory_${CYCLE}.txt"
if [[ -n "${PATCH_IDS:-}" ]]; then
  for id in "${IDS[@]}"; do
    grep -q "$id" "$LOG_DIR/lsinventory_${CYCLE}.txt" || fail "patch $id not in the clone's inventory"
  done
fi

log INFO "4/4 copyBinary $CLONE -> $PATCHED_JAR"
cd "$CLONE/oracle_common/bin"
./copyBinary.sh -javaHome "$JAVA_HOME_INSTALL" -archiveLoc "$PATCHED_JAR" -sourceOracleHomeLoc "$CLONE" \
  2>&1 | tee -a "$LOG"
sha256sum "$PATCHED_JAR" > "$PATCHED_JAR.sha256"
log INFO "archive ready: $PATCHED_JAR ($(du -h "$PATCHED_JAR" | cut -f1)); next: test it, then 03_stage_on_nodes.sh"
