#!/usr/bin/env bash
# v1.1 - install (or update) the app 900 load driver for the current user on the demo VM. Idempotent.
#        Creates ~/anomaly and ~/anomaly/logs (mode 700), the venv ~/anomaly/venv (Python 3.12) when absent,
#        pip-installs the pinned requirements, copies the code, driver.ini (an existing different one is kept
#        as driver.ini.bak.<UTC stamp>) and the user unit, runs `systemctl --user daemon-reload`, then
#        validates the config and the secrets file keys without connecting to anything.
#        It does NOT start, enable or restart the service.
#        Usage: bash install.sh        (from the directory holding the driver files)
#        v1.0: first version, 01-Oct-2026. v1.1: public copy: host name and local-time references removed.
set -Eeuo pipefail
umask 077

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/anomaly"
VENV="$DEST/venv"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="anomaly-driver.service"
PY="${PYTHON:-python3.12}"
WANT_ORACLEDB="3.4.2"

log() { printf '%s install.sh: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
trap 'die "failed at line $LINENO (exit $?)"' ERR

# ---- preconditions
for f in anomaly_driver.py driver.ini requirements.txt "$UNIT"; do
  [[ -f "$SRC/$f" ]] || die "missing $SRC/$f"
done
command -v "$PY" >/dev/null 2>&1 || die "$PY not found"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' || die "$PY is not Python 3.12"
command -v systemctl >/dev/null 2>&1 || die "systemctl not found"
# guard: the pinned file must hold exact pins only (no ranges, no unpinned names). grep -c, not -q: under
# pipefail an early -q exit can SIGPIPE the first grep and hide the result.
unpinned="$(grep -vE '^[[:space:]]*(#|$)' "$SRC/requirements.txt" \
            | grep -cvE '^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+[[:space:]]*$' || true)"
[[ "$unpinned" == "0" ]] || die "requirements.txt has $unpinned entry(ies) not pinned with =="

# ---- directories
mkdir -p "$DEST/logs"
chmod 700 "$DEST" "$DEST/logs"

# ---- venv (created once; a venv built with another Python is refused, not silently replaced)
if [[ ! -x "$VENV/bin/python" ]]; then
  log "creating venv $VENV"
  "$PY" -m venv "$VENV"
else
  log "venv present: $VENV"
fi
"$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' \
  || die "$VENV is not a Python 3.12 venv; move it aside and re-run"

log "pip install -r requirements.txt (pinned)"
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-input --quiet -r "$SRC/requirements.txt"
got="$("$VENV/bin/python" -c 'import oracledb; print(oracledb.__version__)')"
[[ "$got" == "$WANT_ORACLEDB" ]] || die "oracledb $got in the venv, expected $WANT_ORACLEDB"
log "oracledb $got in the venv"

# ---- code and config
install -m 0600 "$SRC/anomaly_driver.py" "$DEST/anomaly_driver.py"
install -m 0600 "$SRC/requirements.txt" "$DEST/requirements.txt"
if [[ -f "$SRC/README.md" ]]; then install -m 0600 "$SRC/README.md" "$DEST/README.md"; fi
if [[ -f "$DEST/driver.ini" ]] && ! cmp -s "$SRC/driver.ini" "$DEST/driver.ini"; then
  bak="$DEST/driver.ini.bak.$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$DEST/driver.ini" "$bak"
  log "existing driver.ini differs: kept as $bak"
fi
install -m 0600 "$SRC/driver.ini" "$DEST/driver.ini"

# ---- user unit (installed and loaded, never started here)
mkdir -p "$UNIT_DIR"
install -m 0644 "$SRC/$UNIT" "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
log "unit installed: $UNIT_DIR/$UNIT (state: $(systemctl --user is-active "$UNIT" || true))"

# ---- validate config + secrets keys; connects to nothing, prints no secret
"$VENV/bin/python" "$DEST/anomaly_driver.py" --config "$DEST/driver.ini" --check-config \
  || die "config check failed (see the JSON line above)"

log "done; the service was NOT started. Start it with: systemctl --user start anomaly-driver"
if systemctl --user is-active --quiet "$UNIT"; then
  log "note: the service is running the previous code; restart it to pick up this install"
fi
