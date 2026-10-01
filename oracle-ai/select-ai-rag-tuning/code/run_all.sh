#!/usr/bin/env bash
# v1.8 - RAG tuning lab (brief 09): run the lab on the database host, stage by stage, one step
#        at a time, resumable from experiments.csv.
#        v1.8: comment at lab_script reworded (a sanitizer false positive); nothing else changed.
#        v1.7: public copy: the SNAP stage and every isolation re-check (a lab-internal snapshot of
#              the existing HR app on the same database) removed with ISOLATION_BASELINE.
#        v1.6: GO-1: 00's password goes as 'define lab_pwd' before the @ line (stdin): ACCEPT ... HIDE reads
#              nothing under docker exec -i without a terminal, so 00 refused an empty value.
#        v1.5: verifier gaps: every docker, python and bash child starts from env -i plus an
#              allowlist (PATH, HOME, TZ=UTC, LANG/LC_ALL/TMPDIR when set, and the non-secret
#              variables that child needs), and the main shell keeps only that base exported, so
#              no key, token or OCID the operator's shell holds reaches a child (v1.4 un-exported
#              *PWD*, *PASSW* and OCI_COMPARTMENT only); a RAG_LAB_PWD from the environment meets
#              the same 12-64 letters-and-digits rule as one from rag_lab.env; CRED hands
#              02_create_credential.py the password file like the other python steps.
#        v1.4: Codex adversarial review: children start from a scrubbed environment (every exported
#              *PWD* / *PASSW* variable and OCI_COMPARTMENT un-exported before the first child); step
#              logs are also scanned for OCIDs and the compartment (shredded, run stops); python
#              children get the RAG_LAB password as a 0600 file (RAG_LAB_PWD_FILE, shredded on exit),
#              not its value; one lab lock (results/.lab.lock) shared with 01 and 99b; MODELS checks
#              the staged ONNX's sha256 inside the container against 01's pin right before 03 and
#              hands 03 the pin; a lab pipeline found not STOPPED is stopped (force only when a plain
#              stop leaves it running) and the run halts for investigation.
#        v1.3: VM-safety review: MODELS and BUILD need a P0 PASS (results/probes/p0.status);
#              MODELS skips staging for a model already in ASKORACLE, defaults MODEL_KEYS to the
#              pinned keys, and hands 03 the SYSDBA capacity figures; BUILD marks a row 'blocked'
#              (model not loaded, no P2 pass) and goes on, with SKIP_PARITY_GATE_KEYS per key;
#              every stage starts and ends with a check that no RAG_LAB pipeline runs; step
#              output reaches the terminal only after a password check (shredded if it holds
#              one), and a script must be readable in the container before anything follows
#              it; docker must be the local daemon's /ora26ai; eval stamps are kept in
#              results/*.stamp and EVAL_RAG resumes its stamp until done; the eval calls match
#              the harness (--stamp, --pause-file); CORPUS_REPLACE counts failed rows; the
#              CLEANUP user check reads its own log. Codex review: raw step output goes to
#              <log>.part until scanned (trap; stray .part files shredded at start); the P0 line
#              must be exactly 'P0 PASS <YYYY-MM-DDTHH:MM:SSZ>'.
#        v1.2: Codex re-review: BUILD refuses a row whose model has no recorded P2 parity pass
#              (results/model_parity.csv; M0 is Oracle's prebuilt baseline and exempt).
#        v1.1: Codex review: a row becomes 'built' only after the isolation snapshot passed;
#              BUILD and EVAL_RAG re-check isolation before they start; EVAL_RAG takes only rows
#              whose retrieval evaluation finished; OCI_CRED_REPLACE=1 recreates the credential.
#        v1.0: first version, PLAN.md v1.2 sections 7 and 9 (brief 08 run_all.sh conventions).
#
# Run as : the VM OS user on the host that runs container ora26ai, from a copy of this code
#          directory. SQL*Plus runs inside the container (docker exec); python runs on the host.
# Usage  : STAGES=<list> [VAR=value ...] ./run_all.sh
#          e.g. GO-1 start : STAGES=CORPUS CORPUS_FORMAT=pdf, then the P0 probe, then
#                            STAGES=ADMIN,CRED,MODELS,PROFILES ... (README_RUN.md, GO-1)
#               GO-2       : STAGES=BUILD,EVAL_RET ONLY_CONFIGS=1 ...
#          DRY_RUN=1 prints the plan: no docker, no database, no secrets, no file changes.
# Stages : run in this fixed order whatever order STAGES lists them in:
#   (SNAP, a snapshot of the existing HR app on the same database, was lab-internal: removed)
#   CORPUS    01_stage_files.sh corpus $CORPUS_FORMAT (CORPUS_REPLACE=1 only before any build;
#             01 refuses to touch the existing HR app's corpus directory paths)
#   ADMIN     00_admin_prereqs.sql and 00b_directories.sql as SYSDBA
#   CRED      02_create_credential.py (OCI_PROFILE selects the ~/.oci/config profile; an existing
#             credential is kept unless OCI_CRED_REPLACE=1)
#   MODELS    needs a P0 PASS in results/probes/p0.status. For each key in MODEL_KEYS: when
#             ASKORACLE already has the model, 03 only checks and grants it (no staging);
#             otherwise stage the pinned ONNX file, read the capacity figures as SYSDBA, run
#             03_load_models.sql as ASKORACLE (it refuses a load without room), unstage the file
#   PROFILES  06_profiles.sql for each model key used in experiments.csv (or PROFILE_KEYS);
#             a key whose model is not loaded yet is skipped with a note
#   BUILD     needs a P0 PASS. 07_build_index.sql for each row with status planned, building or
#             blocked, in file order. A row whose model is not loaded, or (not M0) has no P2
#             parity pass in results/model_parity.csv, is marked 'blocked' and the run goes on
#   EVAL_RET  eval/eval_retrieval.py after each build, and for built rows not yet evaluated
#   EVAL_RAG  eval/eval_rag.py over the answer-layer configs whose retrieval eval is done (RUNS
#             runs), under one stamp kept in results/eval_rag.stamp until eval_rag.py is done
#   CLEANUP   99_cleanup.sql lab and admin, then 99b_cleanup_files.sh; needs
#             CONFIRM_CLEANUP=RAG_LAB (the operator keeps the lab until told otherwise)
#   The lab's *.sql files are copied into /opt/oracle/kb/rag_lab/sql before any SQL stage.
#   Every stage starts and ends with a check that no RAG_LAB pipeline is anything but STOPPED
#   (PLAN.md 2.5); one that is (a lab object) is stopped as RAG_LAB, plain stop first and force
#   only when it still is not STOPPED, named in the log, and the run halts for investigation.
#   MODELS: right before 03 loads a staged file, sha256sum runs on it inside the container and
#   must equal the pin 01_stage_files.sh model-pin gives; a mismatch unstages it and stops. 03
#   gets the pin as 'define rag_model_sha256' (na when nothing is staged).
# Env    : GENAI_HOST       inference.generativeai.<region>.oci.oraclecloud.com (ADMIN, PROFILES, CLEANUP)
#          OCI_COMPARTMENT  compartment OCID (PROFILES); OCI_REGION derived from GENAI_HOST if unset
#          CORPUS_FORMAT    pdf | docx (CORPUS); BUILD uses the format that was staged
#          MODEL_KEYS       default: M0 plus the keys 01_stage_files.sh model-keys finds pinned (the
#                           list of passed models, the exceptions file, Oracle's M1 file)
#          ONLY_CONFIGS     comma list of config ids for BUILD / EVAL_RET (default: all eligible)
#          BUILD_TIMEOUT_MIN  per-build wait limit; default by model (30 small, 90 M2, 180 large)
#          RETRY_FAILED=1   let BUILD retry rows marked failed
#          SKIP_PARITY_GATE_KEYS  comma list of model keys BUILD may use without a recorded P2 pass
#                           (operator decision; logged). The old global SKIP_PARITY_GATE is refused.
#          RUNS             answer-layer runs (default 3)
#          RESULTS_DIR      default ../results;   PAUSE_FILE default ./PAUSE
#          SECRETS_DIR      default ~/oracle26ai/secrets: rag_lab.env (mode 600, RAG_LAB_PWD) and
#                           oracle.env (APP_SCHEMA_PWD, the ASKORACLE password). RAG_LAB_PWD in the
#                           environment wins over rag_lab.env; either way it must be 12-64 letters
#                           and digits, checked before any use
#          PY               default ~/rag-lab/venv/bin/python, else python3 (01 gets it only when set)
#          CORPUS_STAGING, MODEL_SRC_DIR, ORACLE_PREBUILT_DIR, MODELS_SHA256, MODELS_EXCEPTIONS
#                           passed to 01_stage_files.sh when set (see its header)
#          RAG_LAB_DSN      python-oracledb DSN, default localhost:1521/orclpdb1
#          STAGE_METHOD     passed to 01_stage_files.sh (auto | mount | docker-cp)
#          LAB_TMP_DIR      default ~/rag-lab/tmp (mode 700, this user's): the per-run password file
#          DOCKER_HOST, DOCKER_CONTEXT  must be unset: only the local daemon's ora26ai is used
# Re-run : safe. Every script is idempotent; BUILD skips rows already built; a stopped or
#          paused run resumes at the next unfinished row of experiments.csv; MODELS skips models
#          already loaded; EVAL_RAG resumes the stamp in results/eval_rag.stamp. One lab script at
#          a time: run_all.sh holds RESULTS_DIR/.lab.lock (flock, fd 9) for its whole run and its
#          children 01_stage_files.sh / 99b_cleanup_files.sh use that descriptor (RAG_LAB_LOCK_FD);
#          started on their own while run_all.sh runs, they refuse.
# Pause  : touch the PAUSE file; the run stops cleanly before its next step (exit 3).
# Secrets: read from the secrets files (or the environment) into unexported shell variables. The
#          first thing this script does is un-export every variable but PATH, HOME, LANG, LC_ALL,
#          TZ and TMPDIR (and every exported function); the values stay readable here. Every
#          docker, python and bash child then starts from env -i with that base (TZ=UTC) plus the
#          non-secret variables named where it is started (lab_env): docker none, so SQL*Plus in
#          the container gets NLS_LANG only; the python steps RAG_LAB_PWD_FILE and RAG_LAB_DSN;
#          01 / 99b the lock, staging and file-location variables of their headers.
#          Passwords reach SQL*Plus on stdin only. The python children (02_create_credential.py,
#          eval_retrieval.py, eval_rag.py) get RAG_LAB_PWD_FILE, the path of a 0600 file under
#          LAB_TMP_DIR that holds the RAG_LAB password and is shredded when run_all.sh exits (a
#          file left by a killed run is shredded by the next one). Never an argument, never
#          printed, never logged. The compartment OCID reaches SQL*Plus on stdin only. Each
#          SQL*Plus step writes to its log first; the log reaches the terminal only when it holds
#          no password, no 8-character piece of one, no OCID and not the compartment, and is
#          shredded otherwise (the run stops).
set -euo pipefail
shopt -s inherit_errexit
umask 077

# Before any other program starts (even dirname below): an allowlist, not a list of bad names.
# Only PATH, HOME, LANG, LC_ALL, TZ and TMPDIR stay exported (for the small tools this shell runs:
# date, grep, sed ...); every other variable it was given, an API key, a token or an OCID
# included, stays a shell variable that this script can read and no child inherits. Exported
# functions are un-exported too. The docker, python and bash children get less still (lab_env
# below). If anything cannot be un-exported (a readonly variable), the run stops here. (The
# process substitutions fork this shell to run the builtins compgen and declare and exec nothing:
# the fork holds exactly the environment block this process already has.)
RAG_LAB_PWD="${RAG_LAB_PWD:-}"
export -n RAG_LAB_PWD            # never inherited by any child
scrub_env() {
  local v f
  local -a names=()
  mapfile -t names < <(compgen -e)
  for v in "${names[@]}"; do
    case "$v" in
      PATH | HOME | LANG | LC_ALL | TZ | TMPDIR) ;;
      *) export -n -- "${v?}" 2>/dev/null || true ;;          # checked just below
    esac
  done
  mapfile -t names < <(declare -Fx)                          # 'declare -fx NAME' lines
  for f in "${names[@]}"; do export -nf -- "${f##* }"; done
  mapfile -t names < <(compgen -e)
  for v in "${names[@]}"; do
    case "$v" in
      PATH | HOME | LANG | LC_ALL | TZ | TMPDIR) ;;
      *) printf 'run_all.sh: STOP: %s stays exported (readonly?); start run_all.sh without it\n' "$v" >&2; exit 1 ;;
    esac
  done
}
scrub_env

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly CONTAINER="ora26ai"
readonly PDB="ORCLPDB1"
readonly DSN_IN_CONTAINER="//localhost:1521/orclpdb1"
readonly C_SQL_DIR="/opt/oracle/kb/rag_lab/sql"
readonly C_MODEL_DIR="/opt/oracle/kb/rag_lab/models"     # RAG_MODEL_DIR (00b_directories.sql)
readonly ORDER=(CORPUS ADMIN CRED MODELS PROFILES BUILD EVAL_RET EVAL_RAG CLEANUP)
readonly ALL_KEYS=(M0 M1 M2 M3 M4 M5 M6 M1Q)
readonly VERSION="v1.8"
DOCKER="${DOCKER:-docker}"
STAGE_PY="${PY:-}"               # 01_stage_files.sh gets PY only when the operator gave one
PY="${PY:-$HOME/rag-lab/venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
RESULTS_DIR="${RESULTS_DIR:-$HERE/../results}"
EXPERIMENTS="${EXPERIMENTS:-$HERE/experiments.csv}"
PAUSE_FILE="${PAUSE_FILE:-$HERE/PAUSE}"
SECRETS_DIR="${SECRETS_DIR:-$HOME/oracle26ai/secrets}"
RAG_LAB_DSN="${RAG_LAB_DSN:-localhost:1521/orclpdb1}"
DRY_RUN="${DRY_RUN:-0}"
STAGES="${STAGES:-}"
STAGES="${STAGES// /}"
RUNS="${RUNS:-3}"
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SQL_STAGED=0
LAST_LOG=""
RUN_LOG=""
APP_PWD=""
HAVE_MODELS=""
LAB_TMP_DIR="${LAB_TMP_DIR:-$HOME/rag-lab/tmp}"
PW_FILE=""                       # the per-run RAG_LAB password file (python children), or ""
STAGE_METHOD="${STAGE_METHOD:-auto}"
LAB_LOCK_FD=""                   # the lab lock's descriptor once main() holds it (01 and 99b use it)

# ----------------------------------------------------------------------------- child environment
# Every docker, python and bash child starts from env -i with this base: PATH, HOME, TZ=UTC, and
# LANG, LC_ALL and TMPDIR when set. Each call names on its own line the few non-secret pairs its
# child needs on top: lab_env NAME=value ... command args.
CHILD_BASE=()
set_child_base() {
  CHILD_BASE=("PATH=$PATH" "HOME=$HOME" "TZ=UTC")
  if [[ -n "${LANG:-}" ]]; then CHILD_BASE+=("LANG=$LANG"); fi
  if [[ -n "${LC_ALL:-}" ]]; then CHILD_BASE+=("LC_ALL=$LC_ALL"); fi
  if [[ -n "${TMPDIR:-}" ]]; then CHILD_BASE+=("TMPDIR=$TMPDIR"); fi
}
set_child_base
lab_env() {   # [NAME=value ...] command [argument ...]
  env -i "${CHILD_BASE[@]}" "$@"
}

# 01_stage_files.sh and 99b_cleanup_files.sh: the base plus what their headers list under Env,
# none of it a secret (docker, the lab lock's directory and, when held, its descriptor, the
# staging method, and the file locations and PY when the operator set them).
lab_script() {   # $1 script in this directory, then its arguments
  local s="$1" v
  local -a e=("DOCKER=$DOCKER" "RESULTS_DIR=$RESULTS_DIR" "STAGE_METHOD=$STAGE_METHOD")
  shift
  if [[ -n "$LAB_LOCK_FD" ]]; then e+=("RAG_LAB_LOCK_FD=$LAB_LOCK_FD"); fi
  if [[ -n "$STAGE_PY" ]]; then e+=("PY=$STAGE_PY"); fi
  for v in CORPUS_STAGING MODEL_SRC_DIR ORACLE_PREBUILT_DIR MODELS_SHA256 MODELS_EXCEPTIONS; do
    if [[ -n "${!v:-}" ]]; then e+=("$v=${!v}"); fi
  done
  lab_env "${e[@]}" bash "$HERE/$s" "$@"
}

ts()  { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$*" | tee -a "${RUN_LOG:-/dev/null}" >&2; }
die() { log "STOP: $*"; exit 1; }
dry() { [[ "$DRY_RUN" == 1 ]]; }

# The main shell's exit (normal, die, or a signal turned into exit) shreds the password file.
# Subshells (pipes, $(...)) reset this trap, so only the main shell removes it.
on_exit() {
  if [[ -n "$PW_FILE" && -f "$PW_FILE" ]]; then shred -u -- "$PW_FILE" 2>/dev/null || rm -f -- "$PW_FILE"; fi
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

check_pause() {
  if [[ -e "$PAUSE_FILE" ]]; then
    log "PAUSE file present: stopping before the next step (remove it and re-run to resume)"
    exit 3
  fi
}

want() {   # is stage $1 in STAGES?
  [[ ",${STAGES^^}," == *",$1,"* ]]
}

# ----------------------------------------------------------------------------- secrets
read_kv() {   # $1 file, $2 key -> value (surrounding quotes removed); never echoed to the terminal
  local v
  v="$(grep -E "^$2=" -- "$1" | tail -n 1 | cut -d= -f2-)" || true
  v="${v%$'\r'}"
  if [[ "$v" == \"*\" || "$v" == \'*\' ]]; then v="${v:1:${#v}-2}"; fi
  printf '%s' "$v"
}

# RAG_LAB_PWD from the environment (it wins) or rag_lab.env; whichever it came from, it must be
# 12-64 letters and digits before anything uses it: 00's guard holds it in a q'{...}' literal, and
# a value with a quote or brace could end that literal (README_RUN.md, accepted residuals)
load_lab_pwd() {
  dry && return 0
  [[ -n "$RAG_LAB_PWD" ]] || read_lab_pwd_file
  [[ "$RAG_LAB_PWD" =~ ^[A-Za-z0-9]{12,64}$ ]] || die "RAG_LAB_PWD: 12-64 letters and digits (checked, not shown)"
}

read_lab_pwd_file() {
  local f="$SECRETS_DIR/rag_lab.env" mode
  [[ -f "$f" ]] || die "missing $f (RAG_LAB_PWD=...)"
  mode="$(stat -c %a -- "$f")"
  [[ "$mode" =~ ^[4-7]00$ ]] || die "rag_lab.env must be readable by its owner only (chmod 600)"
  RAG_LAB_PWD="$(read_kv "$f" RAG_LAB_PWD)"
}

load_app_pwd() {
  dry && return 0
  [[ -n "$APP_PWD" ]] && return 0
  local f="$SECRETS_DIR/oracle.env"
  [[ -f "$f" ]] || die "missing $f (APP_SCHEMA_PWD, the ASKORACLE password)"
  APP_PWD="$(read_kv "$f" APP_SCHEMA_PWD)"
  [[ -n "$APP_PWD" ]] || die "APP_SCHEMA_PWD missing from oracle.env"
  case "$APP_PWD" in
    *[\"\&\ ]* | *$'\n'* | *$'\t'*) die "APP_SCHEMA_PWD holds a quote, ampersand or blank; SQL*Plus cannot take it safely" ;;
  esac
}

# The RAG_LAB password for the python children: a 0600 file in LAB_TMP_DIR (a directory of this
# user's, closed to others), created once per run in the main shell, shredded by on_exit. The
# children get its path in RAG_LAB_PWD_FILE, never the value. Call it in the main shell only.
ensure_pw_file() {
  local pwd mode owner
  if [[ -n "$PW_FILE" && -f "$PW_FILE" ]]; then return 0; fi
  load_lab_pwd
  if [[ ! -e "$LAB_TMP_DIR" && ! -L "$LAB_TMP_DIR" ]]; then
    mkdir -p -- "$(dirname -- "$LAB_TMP_DIR")"          # umask 077: a new parent is 700 too
    mkdir -m 700 -- "$LAB_TMP_DIR"
  fi
  [[ -d "$LAB_TMP_DIR" && ! -L "$LAB_TMP_DIR" ]] || die "LAB_TMP_DIR is not a plain directory"
  owner="$(stat -c %u -- "$LAB_TMP_DIR")"
  mode="$(stat -c %a -- "$LAB_TMP_DIR")"
  [[ "$owner" == "$(id -u)" && "$mode" =~ ^[1-7]00$ ]] \
    || die "LAB_TMP_DIR must belong to $(id -un) and be closed to others (chmod 700 it)"
  PW_FILE="$(mktemp -- "$LAB_TMP_DIR/run_all_pwd.XXXXXX")"
  chmod 600 -- "$PW_FILE"
  pwd="$RAG_LAB_PWD"
  printf '%s' "$pwd" >"$PW_FILE"
}

# password files left by a run that was killed (on_exit never ran): this lab's own prefix, in
# its own directory, owned by this user; called with the lab lock held, so no live run owns one
shred_stale_pw_files() {
  [[ -d "$LAB_TMP_DIR" && ! -L "$LAB_TMP_DIR" ]] || return 0
  find "$LAB_TMP_DIR" -maxdepth 1 -type f -name 'run_all_pwd.*' -user "$(id -u)" -exec shred -u -- {} + 2>/dev/null || true
  find "$LAB_TMP_DIR" -maxdepth 1 -type f -name 'run_all_pwd.*' -user "$(id -u)" -delete 2>/dev/null || true
}

# ----------------------------------------------------------------------------- docker target
# Only the local daemon's ora26ai: DOCKER_HOST or another DOCKER_CONTEXT could send the lab's SQL
# (including its drops) or docker cp to another daemon that has a container of the same name.
docker_target_check() {
  local ctx name
  dry && return 0
  [[ -z "${DOCKER_HOST:-}" ]] || die "DOCKER_HOST is set: this lab talks only to the local docker daemon (unset it)"
  [[ -z "${DOCKER_CONTEXT:-}" || "${DOCKER_CONTEXT:-}" == default ]] \
    || die "DOCKER_CONTEXT names another context: this lab talks only to the local docker daemon (unset it)"
  ctx="$(lab_env "$DOCKER" context show 2>/dev/null)" || die "docker context show failed"
  [[ "$ctx" == default ]] || die "docker's current context is not 'default': refusing"
  name="$(lab_env "$DOCKER" inspect --type container --format '{{.Name}}' "$CONTAINER" 2>/dev/null)" \
    || die "docker inspect $CONTAINER failed (is the container running?)"
  [[ "$name" == "/$CONTAINER" ]] || die "the container answering to $CONTAINER is not /$CONTAINER: refusing"
  log "docker target: local daemon, container /$CONTAINER"
}

# ----------------------------------------------------------------------------- password check
# every 8-character piece of each password loaded in this run (the whole one when shorter):
# SQL*Plus quotes about 10 characters of an unknown command, so a full-match check is not enough
secret_windows() {
  local s i
  for s in "$RAG_LAB_PWD" "$APP_PWD"; do
    [[ -n "$s" ]] || continue
    if ((${#s} <= 8)); then printf '%s\n' "$s"; continue; fi
    for ((i = 0; i + 8 <= ${#s}; i++)); do printf '%s\n' "${s:i:8}"; done
  done
}

output_leaks_secret() {   # $1 file; the patterns reach grep through a pipe, never its argv
  local w
  w="$(secret_windows)"
  [[ -n "$w" ]] || return 1
  grep -qaFf <(printf '%s\n' "$w") -- "$1"
}

# any OCID (the ocid1 prefix, then type, realm and region, dot-separated), or the compartment
# value itself (through a pipe, never grep's argv). 06_profiles.sql runs with verify off, but SQL*Plus quotes a failing
# statement's line, which would carry the substituted compartment.
output_leaks_ocid() {   # $1 file
  if grep -qaiE 'ocid1\.[a-z0-9_-]+\.[a-z0-9_-]*\.' -- "$1"; then return 0; fi
  [[ -n "${OCI_COMPARTMENT:-}" ]] || return 1
  grep -qaFf <(printf '%s\n' "$OCI_COMPARTMENT") -- "$1"
}

# ----------------------------------------------------------------------------- SQL*Plus in the container
# Output goes to a per-step log, and to the terminal only after the password and OCID checks. A
# step fails when SQL*Plus exits non-zero or its output shows an error SQL*Plus does not turn into
# an exit code (SP2-, not connected, ORA-01017), or when the output holds a password, an OCID or
# the compartment (log shredded).
# The body is a subshell: its EXIT/INT traps can never replace the main shell's on_exit (which
# shreds the password file), however the function is called.
run_in_container() (   # stdin: the SQL*Plus input; $1 sqlplus mode (sysdba|nolog); $2 step log
  local mode="$1" steplog="$2" cmd rc=0 part
  if [[ "$mode" == sysdba ]]; then cmd='sqlplus -s -L / as sysdba'; else cmd='sqlplus -s -L /nolog'; fi
  # the raw output lands in <log>.part (mode 600) and becomes the step log only after the scan;
  # an interrupted step leaves no unscanned output behind (trap), and main() shreds any stray .part
  part="$steplog.part"
  trap 'shred -u -- "$part" 2>/dev/null || rm -f -- "$part"' EXIT
  trap 'exit 130' INT TERM HUP
  lab_env "$DOCKER" exec -i -e NLS_LANG=AMERICAN_AMERICA.AL32UTF8 "$CONTAINER" bash -lc "$cmd" >"$part" 2>&1 || rc=$?
  if output_leaks_secret "$part"; then
    shred -u -- "$part" 2>/dev/null || rm -f -- "$part"
    log "STOP: the output of step $(basename -- "$steplog") held a password or part of one; it was shredded and not shown"
    return 1
  fi
  if output_leaks_ocid "$part"; then
    shred -u -- "$part" 2>/dev/null || rm -f -- "$part"
    log "STOP: the output of step $(basename -- "$steplog") held an OCID or the compartment; it was shredded and not shown"
    return 1
  fi
  mv -f -- "$part" "$steplog"
  cat -- "$steplog"
  if [[ "$rc" != 0 ]]; then
    log "STOP: SQL*Plus exited $rc (log: $(basename -- "$steplog"))"
    return 1
  fi
  if grep -Eq '^(SP2-[0-9]+|ORA-01017|ERROR)|[Nn]ot connected' "$steplog"; then
    log "STOP: SQL*Plus reported an error (log: $(basename -- "$steplog"))"
    return 1
  fi
)

ensure_sql_staged() {
  ((SQL_STAGED)) && return 0
  if dry; then log "DRY: 01_stage_files.sh sql"; SQL_STAGED=1; return 0; fi
  lab_script 01_stage_files.sh sql "$HERE" || die "staging the SQL scripts failed"
  SQL_STAGED=1
}

# The staged script must be readable in the container before its line (and a password after it)
# is sent: SQL*Plus that cannot open a script would read the next stdin line as a command.
script_readable() {
  [[ "$1" =~ ^[0-9A-Za-z_]+\.sql$ ]] || die "bad script name $1"
  lab_env "$DOCKER" exec "$CONTAINER" test -r "$C_SQL_DIR/$1" \
    || die "$1 is not readable in $CONTAINER under the lab's sql dir; nothing was sent (restage with 01_stage_files.sh sql)"
}

steplog() { printf '%s/logs/%s_%s.log' "$RESULTS_DIR" "$(date -u +%Y%m%dT%H%M%SZ)" "$1"; }

sql_sysdba() {   # $1 script, $2 args (validated, no secrets), $3 optional stdin answer, $4 optional lines before the script
  local script="$1" args="$2" answer="${3:-}" pre="${4:-}" sl
  ensure_sql_staged
  if dry; then log "DRY: sysdba @$script $args"; return 0; fi
  script_readable "$script"
  sl="$(steplog "${script%.sql}")"
  LAST_LOG="$sl"
  log "sysdba @$script ${args%% *}"
  {
    printf 'whenever sqlerror exit failure\n'
    printf 'whenever oserror exit failure\n'
    printf 'alter session set container = %s;\n' "$PDB"
    # lines before the script, e.g. 00's password as a define: stdin only, never argv; SQL*Plus -s does
    # not echo stdin, and ACCEPT ... HIDE reads nothing without a terminal (GO-1, 29-Sep)
    if [[ -n "$pre" ]]; then printf 'set verify off\n%s\n' "$pre"; fi
    printf '@%s/%s %s\n' "$C_SQL_DIR" "$script" "$args"
    if [[ -n "$answer" ]]; then printf '%s\n' "$answer"; fi
    printf 'exit\n'
  } | run_in_container sysdba "$sl"
}

sql_as() {   # $1 RAG_LAB|ASKORACLE, $2 script, $3 args, $4 optional SQL*Plus lines before the script
  local user="$1" script="$2" args="$3" pre="${4:-}" pwd sl
  ensure_sql_staged
  if dry; then log "DRY: $user @$script $args"; return 0; fi
  case "$user" in
    RAG_LAB) load_lab_pwd; pwd="$RAG_LAB_PWD" ;;
    ASKORACLE) load_app_pwd; pwd="$APP_PWD" ;;
    *) die "unknown user $user" ;;
  esac
  script_readable "$script"
  sl="$(steplog "${script%.sql}")"
  LAST_LOG="$sl"
  log "$user @$script ${args%% *}"
  {
    printf 'set define off\n'
    printf 'connect %s/"%s"@%s\n' "$user" "$pwd" "$DSN_IN_CONTAINER"
    printf 'set define on\n'
    printf 'whenever sqlerror exit failure\n'
    printf 'whenever oserror exit failure\n'
    if [[ -n "$pre" ]]; then printf '%s\n' "$pre"; fi
    printf '@%s/%s %s\n' "$C_SQL_DIR" "$script" "$args"
    printf 'exit\n'
  } | run_in_container nolog "$sl"
}

# Fixed SQL on stdin (a heredoc or here-string, never a pipe: LAST_LOG must be set in this shell)
sql_inline() {   # $1 RAG_LAB|sysdba, stdin: fixed SQL text built from validated names only
  local who="$1" sl pwd
  sl="$(steplog inline)"
  LAST_LOG="$sl"
  if [[ "$who" == sysdba ]]; then
    { printf 'whenever sqlerror exit failure\nalter session set container = %s;\n' "$PDB"; cat; printf 'exit\n'; } \
      | run_in_container sysdba "$sl" >/dev/null
  else
    load_lab_pwd
    pwd="$RAG_LAB_PWD"
    { printf 'set define off\n'; printf 'connect RAG_LAB/"%s"@%s\n' "$pwd" "$DSN_IN_CONTAINER"
      printf 'whenever sqlerror exit failure\n'; cat; printf 'exit\n'; } | run_in_container nolog "$sl" >/dev/null
  fi
}

# ----------------------------------------------------------------------------- read-only checks
# PLAN.md 2.5: no lab pipeline may run between steps. RAG_LAB's pipelines are read through its own
# USER_ view (the DBA view is not verified on 23.26.1); before ADMIN there is no RAG_LAB.
check_pipelines() {   # $1 label (stage and start/end)
  local label="$1" users bad
  if dry; then log "DRY: pipeline check ($label)"; return 0; fi
  sql_inline sysdba <<'SQL'
set heading off feedback off pagesize 0
select 'USERS|' || count(*) from dba_users where username = 'RAG_LAB';
SQL
  users="$(grep -a '^USERS|' "$LAST_LOG" | tail -n 1)"
  users="${users#USERS|}"
  [[ "$users" =~ ^[01]$ ]] || die "pipeline check ($label): could not tell whether RAG_LAB exists"
  if [[ "$users" == 0 ]]; then return 0; fi
  sql_inline RAG_LAB <<'SQL'
set heading off feedback off pagesize 0 linesize 400
select 'PIPE|' || pipeline_name || '|' || status from user_cloud_pipelines
 where upper(nvl(status, '-')) <> 'STOPPED' order by pipeline_name;
select 'PIPES|checked' from dual;
SQL
  grep -aq '^PIPES|checked' "$LAST_LOG" || die "pipeline check ($label): no answer from RAG_LAB"
  bad="$(grep -a '^PIPE|' "$LAST_LOG" | cut -d'|' -f2- | tr '\n' ' ' || true)"
  if [[ -n "$bad" ]]; then
    log "pipeline check ($label): RAG_LAB pipeline(s) not STOPPED: ${bad% }. They are lab objects: stopping them as RAG_LAB, then halting (PLAN.md 2.5)"
    stop_lab_pipelines "$label"
  fi
  log "pipeline check ($label): every RAG_LAB pipeline is STOPPED"
}

# A lab pipeline that is not STOPPED: stop it as RAG_LAB (its owner), a plain stop first and
# force only when it still is not STOPPED after that, as 07_build_index.sql does. Only RAG_
# names are touched (the lab's naming rule); anything else is reported and left alone. Then the
# run halts: something ran that should not have, and it has to be understood before going on.
stop_lab_pipelines() {   # $1 label
  local label="$1" rc=0 report line
  sql_inline RAG_LAB <<'SQL' || rc=$?
set serveroutput on size unlimited feedback off heading off pagesize 0 linesize 400
declare
  l_st varchar2(128);
  function st(n varchar2) return varchar2 is
    l varchar2(128);
  begin
    select status into l from user_cloud_pipelines where pipeline_name = n;
    return nvl(l, '-');
  exception
    when no_data_found then return '<absent>';
  end;
begin
  for p in (select pipeline_name from user_cloud_pipelines
             where upper(nvl(status, '-')) <> 'STOPPED' order by pipeline_name) loop
    if substr(p.pipeline_name, 1, 4) <> 'RAG_' or sys_context('userenv', 'session_user') <> 'RAG_LAB' then
      dbms_output.put_line('STOPPIPE|' || p.pipeline_name || '|not a lab name, left alone|' || st(p.pipeline_name));
    else
      begin
        dbms_cloud_pipeline.stop_pipeline(pipeline_name => p.pipeline_name, force => false);
      exception
        when others then
          dbms_output.put_line('STOPNOTE|' || p.pipeline_name || '|plain stop failed: ' || substr(sqlerrm, 1, 150));
      end;
      l_st := st(p.pipeline_name);
      if upper(l_st) not in ('STOPPED', '<ABSENT>') then
        begin
          dbms_cloud_pipeline.stop_pipeline(pipeline_name => p.pipeline_name, force => true);
        exception
          when others then
            dbms_output.put_line('STOPNOTE|' || p.pipeline_name || '|forced stop failed: ' || substr(sqlerrm, 1, 150));
        end;
        dbms_output.put_line('STOPPIPE|' || p.pipeline_name || '|force => true|' || st(p.pipeline_name));
      else
        dbms_output.put_line('STOPPIPE|' || p.pipeline_name || '|force => false|' || l_st);
      end if;
    end if;
  end loop;
  dbms_output.put_line('STOPPIPES|done');
end;
/
SQL
  if ((rc)) || ! grep -aq '^STOPPIPES|done' "$LAST_LOG"; then
    die "pipeline check ($label): stopping the RAG_LAB pipeline(s) failed (log: $(basename -- "$LAST_LOG")); stop them as RAG_LAB (DBMS_CLOUD_PIPELINE.STOP_PIPELINE) and find out why before re-running"
  fi
  grep -a '^STOPNOTE|' "$LAST_LOG" | while IFS= read -r line; do log "  ${line#STOPNOTE|}"; done || true
  report="$(grep -a '^STOPPIPE|' "$LAST_LOG" | cut -d'|' -f2- | tr '\n' ' ' || true)"
  die "pipeline check ($label): a RAG_LAB pipeline was running. run_all stopped it (name|how|status now): ${report% }. HALTED for investigation (PLAN.md 2.5): find out what started it before re-running"
}

# ASKORACLE's mining models, read as SYSDBA (read-only); HAVE_MODELS holds " NAME NAME ... "
askoracle_models() {
  HAVE_MODELS=" "
  dry && return 0
  sql_inline sysdba <<'SQL'
set heading off feedback off pagesize 0 linesize 200
select 'HAVE|' || model_name from dba_mining_models where owner = 'ASKORACLE' order by model_name;
select 'HAVE_END|' from dual;
SQL
  grep -aq '^HAVE_END|' "$LAST_LOG" || die "could not list ASKORACLE's models"
  HAVE_MODELS=" $(grep -a '^HAVE|' "$LAST_LOG" | cut -d'|' -f2 | tr '\n' ' ' || true)"
}

have_model() { [[ "$HAVE_MODELS" == *" $1 "* ]]; }

# 03's capacity gate reads these when ASKORACLE cannot see the DBA and V$ views
no_cap_defs() {
  printf 'define rag_cap_ts_room_bytes = na\ndefine rag_cap_log_mode = na\n'
  printf 'define rag_cap_fra_room_bytes = na\ndefine rag_cap_fra_limit_bytes = na\n'
}

capacity_defines() {   # SYSDBA read (read-only) just before a load; prints the four define lines
  local line room logm fra lim
  if dry; then no_cap_defs; return 0; fi
  sql_inline sysdba <<'SQL'
set heading off feedback off pagesize 0 linesize 400
select 'CAP|' || room || '|' || log_mode || '|' || fra_room || '|' || fra_limit
  from (select (select nvl(sum(f.bytes), 0) from dba_free_space f where f.tablespace_name = u.default_tablespace)
             + (select nvl(sum(case when d.autoextensible = 'YES' then greatest(d.maxbytes, d.bytes) else d.bytes end)
                           - sum(d.bytes), 0)
                  from dba_data_files d where d.tablespace_name = u.default_tablespace) room,
               (select log_mode from v$database) log_mode,
               (select nvl(sum(space_limit), 0) - nvl(sum(space_used), 0) + nvl(sum(space_reclaimable), 0)
                  from v$recovery_file_dest) fra_room,
               (select nvl(sum(space_limit), 0) from v$recovery_file_dest) fra_limit
          from dba_users u
         where u.username = 'ASKORACLE');
SQL
  line="$(grep -a '^CAP|' "$LAST_LOG" | tail -n 1)"
  IFS='|' read -r _ room logm fra lim <<<"$line"
  [[ "$room" =~ ^[0-9]{1,20}$ ]] || room=na
  [[ "$logm" =~ ^(NOARCHIVELOG|ARCHIVELOG)$ ]] || logm=na
  [[ "$fra" =~ ^[0-9]{1,20}$ ]] || fra=na
  [[ "$lim" =~ ^[0-9]{1,20}$ ]] || lim=na
  log "capacity (SYSDBA, read-only): room in ASKORACLE's default tablespace $room bytes, $logm, recovery-area headroom $fra of $lim bytes"
  printf 'define rag_cap_ts_room_bytes = %s\ndefine rag_cap_log_mode = %s\n' "$room" "$logm"
  printf 'define rag_cap_fra_room_bytes = %s\ndefine rag_cap_fra_limit_bytes = %s\n' "$fra" "$lim"
}

# P0 (04_probes.sql P0, read-only, SYSDBA) writes results/probes/p0.status; its last P0 line decides
p0_gate() {   # $1 stage name
  local f="$RESULTS_DIR/probes/p0.status" last
  last="$(grep -aE '^P0 (PASS|FAIL)' "$f" 2>/dev/null | tail -n 1 || true)"
  if [[ "$last" =~ ^P0\ PASS\ [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    log "P0 gate for $1: ${last}"
    return 0
  fi
  if dry; then log "DRY: $1 would stop here: no 'P0 PASS <utc>' in probes/p0.status"; return 0; fi
  die "$1 refused: results/probes/p0.status has no 'P0 PASS <utc>' as its last P0 line. Run the P0 capacity probe first (README_RUN.md, GO-1)."
}

# ----------------------------------------------------------------------------- experiments.csv
csvx() {   # rows <statuses> [ids] | set <cfg> <status> [format] | keys | count <statuses> | last_build <cfg>
  lab_env "$PY" - "$EXPERIMENTS" "$RESULTS_DIR/builds.csv" "$@" <<'PY'
import csv, os, re, sys, tempfile
path, builds, cmd, args = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
HEADER = ["config_id", "stage", "model_key", "model_name", "chunk_size", "chunk_overlap", "metric",
          "match_limit", "similarity_threshold", "corpus_format", "index_name", "rag_profile",
          "answer_layer", "status", "notes"]
with open(path, newline="", encoding="utf-8") as f:
    rd = csv.DictReader(f)
    if rd.fieldnames != HEADER:
        sys.exit("experiments.csv: header differs from the contract")
    rows = list(rd)
ids = [r["config_id"] for r in rows]
if len(ids) != len(set(ids)):
    sys.exit("experiments.csv: duplicate config_id")

def write():
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".experiments.", suffix=".tmp")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)

if cmd == "rows":
    want = set(args[0].split(","))
    only = set(x for x in (args[1].split(",") if len(args) > 1 else []) if x)
    for r in rows:
        if r["status"] in want and (not only or r["config_id"] in only):
            vals = [r[k] for k in HEADER[:14]]
            if any("|" in v or not v for v in vals):
                sys.exit(f"config {r['config_id']}: empty field or '|' in a field")
            print("|".join(vals))
elif cmd == "set":
    cfg, status = args[0], args[1]
    fmt = args[2] if len(args) > 2 else ""
    hit = [r for r in rows if r["config_id"] == cfg]
    if len(hit) != 1:
        sys.exit(f"config {cfg} not found")
    if fmt:
        if hit[0]["corpus_format"] not in ("TBD", fmt):
            sys.exit(f"config {cfg} says corpus_format {hit[0]['corpus_format']}, staged is {fmt}")
        hit[0]["corpus_format"] = fmt
    hit[0]["status"] = status
    write()
elif cmd == "keys":
    order = ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "M1Q"]
    used = {r["model_key"] for r in rows if r["model_key"] in order and r["status"] != "cut"}
    print(" ".join(k for k in order if k in used))
elif cmd == "count":
    want = set(args[0].split(","))
    print(sum(1 for r in rows if r["status"] in want))
elif cmd == "answer_cfgs":
    print(",".join(r["config_id"] for r in rows if r["answer_layer"] == "yes" and r["status"] == "evaluated"))
elif cmd == "parity":
    # results/model_parity.csv (written by the P2 probe): columns include model_key and status;
    # the last row for the key decides
    key, pfile = args[0], args[1]
    last = None
    if os.path.exists(pfile):
        with open(pfile, newline="", encoding="utf-8") as f:
            for p in csv.DictReader(f):
                if (p.get("model_key") or "").strip().upper() == key:
                    last = (p.get("status") or "").strip().lower()
    print(last or "none")
elif cmd == "last_build":
    last = None
    if os.path.exists(builds):
        with open(builds, newline="", encoding="utf-8") as f:
            for b in csv.DictReader(f):
                if b["config_id"] == args[0]:
                    last = b
    if last:
        print(f"{last['chunks']}|{last['content_chars']}|{last['index_name']}")
else:
    sys.exit(f"unknown csvx command {cmd}")
PY
}

append_build() {   # one line per build into results/builds.csv (header written once)
  local f="$RESULTS_DIR/builds.csv"
  if [[ ! -f "$f" ]]; then
    printf 'utc,config_id,index_name,rag_profile,action,files,chunks,content_chars,elapsed_s,dims,metric,match_limit,similarity_threshold,embedding_model,pipeline_status,corpus_format\n' >"$f"
  fi
  printf '%s\n' "$1" >>"$f"
}

# ----------------------------------------------------------------------------- validation
model_of() {   # model key -> ASKORACLE model name, or nothing for an unknown key
  case "$1" in
    M0) echo ALL_MINILM_L12_V2 ;; M1) echo MULTILINGUAL_E5_SMALL ;; M2) echo MULTILINGUAL_E5_BASE ;;
    M3) echo MULTILINGUAL_E5_LARGE ;; M4) echo BGE_M3 ;; M5) echo ARCTIC_EMBED_L_V2 ;;
    M6) echo ARABIC_TRIPLET_V2 ;; M1Q) echo MULTILINGUAL_E5_SMALL_Q ;;
    *) return 0 ;;
  esac
}

valid_row() {   # cfg key mname chunk ovl metric k thr index rag
  local cfg="$1" key="$2" mname="$3" chunk="$4" ovl="$5" metric="$6" k="$7" thr="$8" index="$9" rag="${10}" m
  [[ "$cfg" =~ ^[0-9]{1,3}r?$ ]] || die "config $cfg: bad config_id"
  m="$(model_of "$key")"
  [[ -n "$m" ]] || die "config $cfg: model_key $key is not decided (set it and status planned first)"
  [[ "$mname" == "$m" ]] || die "config $cfg: model_name $mname does not match $key ($m)"
  [[ "$chunk" =~ ^[0-9]{2,4}$ && "$ovl" =~ ^[0-9]{1,4}$ ]] || die "config $cfg: chunk_size/chunk_overlap not decided"
  [[ "$metric" =~ ^(COS|DOT|EUC|MAN)$ ]] || die "config $cfg: metric must be COS, DOT, EUC or MAN"
  [[ "$k" =~ ^[0-9]{1,3}$ ]] || die "config $cfg: match_limit not decided"
  [[ "$thr" =~ ^[0-9](\.[0-9]{1,4})?$ ]] || die "config $cfg: similarity_threshold not decided"
  [[ "$index" == "RAG_${key}_C${chunk}_O${ovl}_${metric}" ]] || die "config $cfg: index_name $index does not follow the naming rule"
  [[ "$rag" == "RAG_P_${index#RAG_}" ]] || die "config $cfg: rag_profile $rag does not follow the naming rule"
}

genai_region() {
  [[ "${GENAI_HOST:-}" =~ ^inference\.generativeai\.([a-z]+-[a-z]+-[0-9]+)\.oci\.oraclecloud\.com$ ]] \
    || die "GENAI_HOST must be inference.generativeai.<region>.oci.oraclecloud.com"
  OCI_REGION="${OCI_REGION:-${BASH_REMATCH[1]}}"
  [[ "$OCI_REGION" =~ ^[a-z]+-[a-z]+-[0-9]+$ ]] || die "OCI_REGION is not a region name"
}

# why a row on model $1 cannot be built now (printed), or nothing. P2 (04_probes, after MODELS)
# appends model_key,status,min_cosine,utc rows to results/model_parity.csv; the last row per key
# decides. M0 is Oracle's prebuilt model that the existing HR app already uses, and is exempt.
block_reason() {
  local key="$1" m st
  m="$(model_of "$key")"
  if ! have_model "$m"; then
    printf 'ASKORACLE.%s is not loaded (run MODELS, then PROFILES)' "$m"
    return 0
  fi
  [[ "$key" == M0 ]] && return 0
  if [[ ",${SKIP_PARITY_GATE_KEYS:-}," == *",$key,"* ]]; then
    log "WARNING: SKIP_PARITY_GATE_KEYS names $key: building without a recorded P2 pass (operator decision)"
    return 0
  fi
  st="$(csvx parity "$key" "$RESULTS_DIR/model_parity.csv")"
  if [[ "$st" != pass ]]; then printf 'no P2 parity pass for %s in model_parity.csv (last status: %s)' "$key" "$st"; fi
}

default_timeout() {
  case "$1" in
    M2) echo 90 ;;
    M3 | M4 | M5) echo 180 ;;
    *) echo 30 ;;
  esac
}

staged_format() {   # the one format recorded by 01_stage_files.sh corpus
  local f="$RESULTS_DIR/corpus_manifest_staged.csv" fmts
  [[ -f "$f" ]] || die "no staged corpus manifest in RESULTS_DIR (run STAGES=CORPUS)"
  fmts="$(awk -F, 'NR > 1 && $3 == "gulf" { print $4 }' "$f" | sort -u)"
  [[ "$fmts" == pdf || "$fmts" == docx ]] || die "staged corpus has no single Gulf format"
  printf '%s' "$fmts"
}

# ----------------------------------------------------------------------------- steps
# (snapshot / recheck_isolation, a lab-internal snapshot of the existing HR app, removed)

verify_counts() {   # $1 cfg, $2 index: chunk count and characters must equal the recorded build
  local cfg="$1" index="$2" rec now
  dry && { log "DRY: verify counts of $index"; return 0; }
  rec="$(csvx last_build "$cfg")"
  [[ -n "$rec" ]] || die "config $cfg: no recorded build to compare with"
  [[ "$index" =~ ^RAG_[A-Z0-9_]+$ ]] || die "bad index name"
  sql_inline RAG_LAB <<SQL
set serveroutput on feedback off verify off
declare
  l_t varchar2(200);
  l_n number;
  l_c number;
begin
  select nvl(max(dbms_lob.substr(attribute_value, 200, 1)), '${index}\$VECTAB') into l_t
    from user_cloud_vector_index_attributes
   where index_name = '${index}' and attribute_name = 'vector_table_name';
  execute immediate 'select count(*), nvl(sum(length(content)), 0) from ' ||
    dbms_assert.enquote_name(dbms_assert.simple_sql_name(l_t), false) into l_n, l_c;
  dbms_output.put_line('COUNTS|' || l_n || '|' || l_c);
end;
/
SQL
  now="$(grep -a '^COUNTS|' "$LAST_LOG" | tail -n 1)"
  [[ "${now#COUNTS|}" == "${rec%|*}" ]] \
    || die "config $cfg: $index changed since its build (recorded ${rec%|*}, now ${now#COUNTS|})"
  log "config $cfg: counts unchanged (${now#COUNTS|})"
}

stage_corpus() {
  local fmt="${CORPUS_FORMAT:-}" extra=()
  [[ "$fmt" == pdf || "$fmt" == docx ]] || die "CORPUS_FORMAT must be pdf or docx"
  if [[ "${CORPUS_REPLACE:-0}" == 1 ]]; then
    # a failed row's index exists too, and its pipeline may have been left running
    [[ "$(csvx count built,evaluated,building,failed)" == 0 ]] \
      || die "CORPUS_REPLACE refused: indexes exist (rows built, evaluated, building or failed; the corpus is frozen)"
    extra+=(--replace)
  fi
  if dry; then log "DRY: 01_stage_files.sh corpus $fmt ${extra[*]}"; return 0; fi
  lab_script 01_stage_files.sh corpus "$fmt" "${extra[@]}" --manifest-copy "$RESULTS_DIR/corpus_manifest_staged.csv"
}

stage_admin() {
  genai_region
  load_lab_pwd
  sql_sysdba 00_admin_prereqs.sql "$GENAI_HOST" "" "define lab_pwd = \"$RAG_LAB_PWD\""
  sql_sysdba 00b_directories.sql ""
}

stage_cred() {
  if dry; then log "DRY: 02_create_credential.py"; return 0; fi
  log "02_create_credential.py"
  local extra=()
  if [[ "${OCI_CRED_REPLACE:-0}" == 1 ]]; then extra+=(--replace); fi
  # like the other python steps: the path of the per-run 0600 password file, never the value
  ensure_pw_file
  lab_env RAG_LAB_PWD_FILE="$PW_FILE" RAG_LAB_DSN="$RAG_LAB_DSN" \
    "$PY" "$HERE/02_create_credential.py" --oci-profile "${OCI_PROFILE:-DEFAULT}" "${extra[@]}"
}

# The pinned sha256 of a key's ONNX file, as 01_stage_files.sh pins it (passed list, exception
# line or Oracle's M1 file; local files only)
model_pin() {   # $1 key
  local pin
  pin="$(lab_script 01_stage_files.sh model-pin "$1")" || die "01_stage_files.sh model-pin $1 failed"
  [[ "$pin" =~ ^[0-9a-f]{64}$ ]] || die "$1: 01_stage_files.sh model-pin gave no sha256"
  printf '%s' "$pin"
}

# Right before 03 loads it (the load is permanent): the staged file, as the database will read
# it, i.e. inside the container, must have the pinned sha256. A mismatch unstages the file (it is
# the lab's, under the lab tree) so no hand run of 03 can load it either, and stops the run.
verify_staged_model() {   # $1 key, $2 model name, $3 pinned sha256
  local key="$1" file="${2,,}.onnx" pin="$3" out got
  if dry; then log "DRY: sha256sum of $C_MODEL_DIR/$file inside $CONTAINER must equal the pin"; return 0; fi
  [[ "$pin" =~ ^[0-9a-f]{64}$ ]] || die "$key: no valid pin to check the staged file against"
  [[ "$file" =~ ^[a-z0-9_]+\.onnx$ ]] || die "$key: unexpected model file name"
  out="$(lab_env "$DOCKER" exec "$CONTAINER" sha256sum -- "$C_MODEL_DIR/$file")" \
    || die "$key: sha256sum of the staged $file failed inside $CONTAINER; 03 not run"
  got="${out%% *}"
  if [[ "$got" != "$pin" ]]; then
    log "STOP: $key: inside $CONTAINER the staged $file has sha256 ${got:0:12}..., the pin is ${pin:0:12}...; removing it, 03 not run"
    lab_script 01_stage_files.sh unstage-model "$key" \
      || log "WARNING: could not unstage $file: remove it from $C_MODEL_DIR before any hand run of 03"
    exit 1
  fi
  log "$key: staged $file checked inside $CONTAINER: sha256 equals the pin"
}

stage_models() {
  local key keys line mname staged defs pin
  p0_gate MODELS
  # the SQL scripts first: then nothing is staged between a model file's check and 03
  ensure_sql_staged
  keys="${MODEL_KEYS:-}"
  if [[ -z "$keys" ]]; then
    keys="$(lab_script 01_stage_files.sh model-keys)" || die "01_stage_files.sh model-keys failed"
    keys="M0 $keys"
    log "MODEL_KEYS not set: the pinned keys are $keys"
  fi
  askoracle_models
  for key in $keys; do
    check_pause
    [[ " ${ALL_KEYS[*]} " == *" $key "* ]] || die "unknown model key $key"
    mname="$(model_of "$key")"
    staged=0
    defs="$(no_cap_defs)"
    pin=na
    if [[ "$key" == M0 ]]; then
      :
    elif ! dry && have_model "$mname"; then
      # resumable: no 2 GB copy and no size check for a model that is already in; a copy left
      # behind by an interrupted run is removed first (03 compares sizes only when it is staged)
      log "$key: ASKORACLE.$mname is already loaded; not staged again (03 checks and grants it)"
      lab_script 01_stage_files.sh unstage-model "$key"
    else
      if dry; then log "DRY: 01_stage_files.sh model $key"; else lab_script 01_stage_files.sh model "$key"; fi
      staged=1
      defs="$(capacity_defines)"
      if dry; then log "DRY: 01_stage_files.sh model-pin $key"; else pin="$(model_pin "$key")"; fi
      verify_staged_model "$key" "$mname" "$pin"          # the last step before 03
    fi
    defs+=$'\n'"define rag_model_sha256 = $pin"
    sql_as ASKORACLE 03_load_models.sql "$key" "$defs"
    if ! dry; then
      line="$(grep -a '^MODEL|' "$LAST_LOG" | tail -n 1)"
      [[ -n "$line" ]] || die "03_load_models.sql $key printed no MODEL line"
      mkdir -p "$RESULTS_DIR"
      printf '%s|%s\n' "$(ts)" "$line" >>"$RESULTS_DIR/models_loaded.txt"
    fi
    if ((staged)); then
      if dry; then log "DRY: 01_stage_files.sh unstage-model $key"; else lab_script 01_stage_files.sh unstage-model "$key"; fi
    fi
  done
}

stage_profiles() {
  local key comp keys
  genai_region
  comp="${OCI_COMPARTMENT:-}"
  if ! dry; then
    [[ "$comp" =~ ^ocid1\.(compartment|tenancy)\.[a-z0-9-]+\.[a-z0-9-]*\.[a-z0-9]+$ ]] \
      || die "OCI_COMPARTMENT is not a compartment/tenancy OCID"
  fi
  keys="${PROFILE_KEYS:-$(csvx keys)}"
  askoracle_models
  for key in $keys; do
    check_pause
    [[ " ${ALL_KEYS[*]} " == *" $key "* ]] || die "unknown model key $key"
    if dry; then log "DRY: RAG_LAB @06_profiles.sql $key <compartment> $OCI_REGION"; continue; fi
    if ! have_model "$(model_of "$key")"; then
      log "NOTE: $key: ASKORACLE.$(model_of "$key") is not loaded; RAG_EMB_$key skipped (MODELS first, then PROFILES again)"
      continue
    fi
    sql_as RAG_LAB 06_profiles.sql "$key $comp $OCI_REGION"
  done
}

# ----------------------------------------------------------------------------- evaluation stamps
stamp_new() { date -u +%Y%m%dT%H%M%SZ; }

write_kv_file() {   # $1 file, then key=value lines; written atomically, mode 600
  local f="$1" tmp
  shift
  tmp="$(mktemp "$f.XXXXXX")"
  printf '%s\n' "$@" >"$tmp"
  mv -f -- "$tmp" "$f"
}

# eval_retrieval.py (exit 0 ok, 3 paused, else failed). Its stamp is kept in
# results/eval_retrieval_<cfg>.stamp and passed as --stamp until the run is done. It does not
# resume inside a file (a second run under one stamp appends a second header, which stats.py
# refuses), so a stamp whose retrieval file already exists is replaced by a new one; the partial
# file stays as evidence.
eval_retrieval() {   # $1 cfg, $2 index
  local cfg="$1" index="$2" script="$HERE/eval/eval_retrieval.py" sf stamp="" rc=0
  sf="$RESULTS_DIR/eval_retrieval_${cfg}.stamp"
  if dry; then log "DRY: eval/eval_retrieval.py --config $cfg --stamp <kept or new> --pause-file PAUSE"; return 0; fi
  [[ -f "$script" ]] || die "eval/eval_retrieval.py not found (EVAL_RET)"
  ensure_pw_file
  if [[ -f "$sf" ]]; then
    stamp="$(read_kv "$sf" stamp)"
    [[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "$(basename -- "$sf") holds no valid stamp"
  fi
  if [[ -n "$stamp" && -e "$RESULTS_DIR/retrieval_${index}_${stamp}.jsonl" ]]; then
    log "NOTE: config $cfg: retrieval_${index}_${stamp}.jsonl is an unfinished run; a new stamp is used (the file stays; give stats.py the complete one)"
    stamp=""
  fi
  if [[ -z "$stamp" ]]; then
    stamp="$(stamp_new)"
    while [[ -e "$RESULTS_DIR/retrieval_${index}_${stamp}.jsonl" ]]; do sleep 1; stamp="$(stamp_new)"; done
    write_kv_file "$sf" "stamp=$stamp" "config=$cfg" "index=$index"
  fi
  log "eval_retrieval.py --config $cfg --stamp $stamp"
  lab_env RAG_LAB_PWD_FILE="$PW_FILE" RAG_LAB_DSN="$RAG_LAB_DSN" \
    "$PY" "$script" --config "$cfg" --experiments "$EXPERIMENTS" --out "$RESULTS_DIR" \
    --stamp "$stamp" --pause-file "$PAUSE_FILE" || rc=$?
  case "$rc" in
    0)
      printf '%s|eval_retrieval|%s|%s|done\n' "$(ts)" "$cfg" "$stamp" >>"$RESULTS_DIR/eval_stamps.log"
      rm -f -- "$sf"
      ;;
    3)
      log "eval_retrieval.py stopped (PAUSE file or infra window); config $cfg stays built. Remove PAUSE and run STAGES=EVAL_RET to resume"
      exit 3
      ;;
    *) die "eval_retrieval.py --config $cfg exited $rc; config $cfg stays built (STAGES=EVAL_RET retries it)" ;;
  esac
}

build_one() {   # one experiments.csv row, already split
  local cfg="$1" key="$2" mname="$3" chunk="$4" ovl="$5" metric="$6" k="$7" thr="$8" fmt="$9" index="${10}" rag="${11}"
  local staged timeout res action files chunks chars base part run_cfg
  local -a parts=()
  local -A R=()
  valid_row "$cfg" "$key" "$mname" "$chunk" "$ovl" "$metric" "$k" "$thr" "$index" "$rag"
  timeout="${BUILD_TIMEOUT_MIN:-$(default_timeout "$key")}"
  [[ "$timeout" =~ ^[0-9]{1,4}$ ]] || die "BUILD_TIMEOUT_MIN must be minutes"
  if dry; then
    log "DRY: build config $cfg: $index (format $fmt, timeout $timeout min)"
    return 0
  fi
  staged="$(staged_format)"
  [[ "$fmt" == TBD || "$fmt" == "$staged" ]] || die "config $cfg wants corpus_format $fmt but $staged is staged"
  lab_script 01_stage_files.sh verify "$staged"
  csvx set "$cfg" building "$staged"
  # REBUILD_CONFIGS=9,12 asks 07 to drop and rebuild those indexes (a failed load, say)
  run_cfg="$cfg"
  if [[ "$cfg" != *r && ",${REBUILD_CONFIGS:-}," == *",$cfg,"* ]]; then run_cfg="${cfg}r"; fi
  if ! sql_as RAG_LAB 07_build_index.sql "$run_cfg $key $chunk $ovl $metric $k $thr" \
         "define rag_build_timeout_min = $timeout"; then
    csvx set "$cfg" failed
    die "config $cfg: 07_build_index.sql failed (log: $(basename -- "$LAST_LOG")); fix, then RETRY_FAILED=1"
  fi
  res="$(grep -a '^RESULT|' "$LAST_LOG" | tail -n 1 || true)"
  if [[ -z "$res" ]]; then
    csvx set "$cfg" failed
    die "config $cfg: 07_build_index.sql printed no RESULT line (log: $(basename -- "$LAST_LOG"))"
  fi
  IFS='|' read -ra parts <<<"${res#RESULT|}"
  for part in "${parts[@]}"; do R["${part%%=*}"]="${part#*=}"; done
  action="${R[action]:-}"; files="${R[files]:-}"; chunks="${R[chunks]:-}"; chars="${R[content_chars]:-}"
  if [[ "${R[index]:-}" != "$index" || -z "$files" || "${files%/*}" != "${files#*/}" ]]; then
    csvx set "$cfg" failed
    die "config $cfg: RESULT line is incomplete or not every file loaded ($files)"
  fi
  append_build "$(ts),$cfg,$index,$rag,$action,$files,$chunks,$chars,${R[elapsed_s]:-},${R[dims]:-},${R[metric]:-},${R[match_limit]:-},${R[similarity_threshold]:-},${R[embedding_model]:-},${R[pipeline_status]:-},$staged"
  if [[ "$cfg" == *r ]]; then
    base="$(csvx last_build "${cfg%r}")"
    if [[ -n "$base" && "${base%|*}" != "$chunks|$chars" ]]; then
      csvx set "$cfg" failed
      die "config $cfg: the identical rebuild differs from config ${cfg%r} (${base%|*} vs $chunks|$chars)"
    fi
  fi
  # (a lab-internal isolation snapshot of the existing HR app ran here: removed)
  csvx set "$cfg" built
  log "config $cfg built: $index, $chunks chunks, $chars characters, files $files ($action)"
  if want EVAL_RET; then
    eval_retrieval "$cfg" "$index"
    csvx set "$cfg" evaluated
  fi
}

stage_build() {
  local out line cfg stage key mname chunk ovl metric k thr fmt index rag layer status reason
  local statuses="planned,building,blocked" nblocked=0 sk
  local -a rows=()
  [[ -z "${SKIP_PARITY_GATE:-}" ]] || die "SKIP_PARITY_GATE is no longer read: name the model keys in SKIP_PARITY_GATE_KEYS"
  sk="${SKIP_PARITY_GATE_KEYS:-}"
  for sk in ${sk//,/ }; do
    [[ " ${ALL_KEYS[*]} " == *" $sk "* && "$sk" != M0 ]] || die "SKIP_PARITY_GATE_KEYS: unknown or exempt key $sk"
  done
  p0_gate BUILD
  if [[ "${RETRY_FAILED:-0}" == 1 ]]; then statuses+=",failed"; fi
  out="$(csvx rows "$statuses" "${ONLY_CONFIGS:-}")"
  if [[ -n "$out" ]]; then mapfile -t rows <<<"$out"; fi
  log "BUILD: ${#rows[@]} row(s) to build"
  if ((${#rows[@]})); then askoracle_models; fi   # (lab-internal isolation re-check removed)
  if [[ "$(csvx count failed)" != 0 && "${RETRY_FAILED:-0}" != 1 ]]; then
    log "NOTE: rows marked failed are skipped (RETRY_FAILED=1 retries them)"
  fi
  for line in "${rows[@]}"; do
    check_pause
    IFS='|' read -r cfg stage key mname chunk ovl metric k thr fmt index rag layer status <<<"$line"
    log "config $cfg ($stage, answer layer $layer, status $status)"
    if ! dry; then
      # a missing model or parity pass blocks this row only; the next rows still run
      reason="$(block_reason "$key")"
      if [[ -n "$reason" ]]; then
        csvx set "$cfg" blocked
        nblocked=$((nblocked + 1))
        log "config $cfg BLOCKED: $reason. The run goes on; BUILD takes the row again once that is fixed"
        continue
      fi
    fi
    build_one "$cfg" "$key" "$mname" "$chunk" "$ovl" "$metric" "$k" "$thr" "$fmt" "$index" "$rag"
  done
  if ((nblocked)); then log "BUILD: $nblocked row(s) blocked (see above)"; fi
}

stage_eval_ret() {   # built rows not evaluated yet (rows built in this run were evaluated already)
  local out line cfg index
  local -a rows=()
  out="$(csvx rows built "${ONLY_CONFIGS:-}")"
  if [[ -n "$out" ]]; then mapfile -t rows <<<"$out"; fi
  for line in "${rows[@]}"; do
    check_pause
    cfg="${line%%|*}"
    index="$(cut -d'|' -f11 <<<"$line")"
    verify_counts "$cfg" "$index"
    eval_retrieval "$cfg" "$index"
    dry || csvx set "$cfg" evaluated
  done
}

# One eval_rag.py run over the answer-layer configs, under one stamp kept in
# results/eval_rag.stamp (stamp, configs, runs) until eval_rag.py exits 0. A paused or stopped
# run (exit 3) resumes with the same stamp, so no GENERATE call is repeated and the answers stay
# in one set of files; the configs and runs of that stamp are kept while it resumes.
stage_eval_rag() {
  local script="$HERE/eval/eval_rag.py" sf="$RESULTS_DIR/eval_rag.stamp" stamp="" cfgs cur runs="$RUNS"
  local out line cfg n want rc=0
  cur="$(csvx answer_cfgs)"
  if [[ -f "$sf" ]]; then
    stamp="$(read_kv "$sf" stamp)"
    cfgs="$(read_kv "$sf" configs)"
    runs="$(read_kv "$sf" runs)"
    [[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ && "$cfgs" =~ ^[0-9]{1,3}r?(,[0-9]{1,3}r?)*$ && "$runs" =~ ^[1-9]$ ]] \
      || die "eval_rag.stamp is malformed; look at it before removing it"
    log "EVAL_RAG resumes stamp $stamp (configs $cfgs, runs $runs) until eval_rag.py reports done"
    if [[ "$cfgs" != "$cur" ]]; then
      log "NOTE: the evaluated answer-layer configs are now ${cur:-none}; this stamp keeps its own set, the others follow once it is done"
    fi
    if [[ "$runs" != "$RUNS" ]]; then log "NOTE: RUNS=$RUNS is ignored while stamp $stamp resumes (started with $runs)"; fi
  else
    cfgs="$cur"
    [[ -n "$cfgs" ]] || die "no answer-layer config has finished its retrieval evaluation yet"
  fi
  # (a lab-internal isolation re-check of the existing HR app ran here: removed)
  if dry; then log "DRY: eval/eval_rag.py --configs $cfgs --runs $runs --stamp ${stamp:-<new>} --pause-file PAUSE"; return 0; fi
  [[ -f "$script" ]] || die "eval/eval_rag.py not found (EVAL_RAG)"
  out="$(csvx rows evaluated "$cfgs")"
  n=0
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    n=$((n + 1))
    cfg="${line%%|*}"
    verify_counts "$cfg" "$(cut -d'|' -f11 <<<"$line")"
  done <<<"$out"
  want="$(tr ',' '\n' <<<"$cfgs" | grep -c .)"
  [[ "$n" == "$want" ]] || die "EVAL_RAG: not every config of $cfgs is still 'evaluated'; stamp ${stamp:-<new>} not run"
  if [[ -z "$stamp" ]]; then
    stamp="$(stamp_new)"
    write_kv_file "$sf" "stamp=$stamp" "configs=$cfgs" "runs=$runs"
    log "EVAL_RAG stamp $stamp recorded in eval_rag.stamp"
  fi
  ensure_pw_file
  log "eval_rag.py --configs $cfgs --runs $runs --stamp $stamp"
  lab_env RAG_LAB_PWD_FILE="$PW_FILE" RAG_LAB_DSN="$RAG_LAB_DSN" \
    "$PY" "$script" --configs "$cfgs" --experiments "$EXPERIMENTS" --runs "$runs" --out "$RESULTS_DIR" \
    --stamp "$stamp" --pause-file "$PAUSE_FILE" || rc=$?
  case "$rc" in
    0)
      printf '%s|eval_rag|%s|%s|runs=%s|done\n' "$(ts)" "$cfgs" "$stamp" "$runs" >>"$RESULTS_DIR/eval_stamps.log"
      rm -f -- "$sf"
      log "EVAL_RAG done: stamp $stamp"
      ;;
    3)
      log "eval_rag.py stopped (PAUSE file or infra window). Stamp $stamp is kept: remove PAUSE and run STAGES=EVAL_RAG to resume"
      exit 3
      ;;
    *) die "eval_rag.py exited $rc; stamp $stamp is kept in eval_rag.stamp for the resume" ;;
  esac
}

stage_cleanup() {
  local users
  [[ "${CONFIRM_CLEANUP:-}" == RAG_LAB ]] || die "CLEANUP drops RAG_LAB; set CONFIRM_CLEANUP=RAG_LAB to confirm"
  genai_region
  if dry; then
    log "DRY: RAG_LAB @99_cleanup.sql lab; sysdba @99_cleanup.sql admin; 99b_cleanup_files.sh --yes"
    return 0
  fi
  ensure_sql_staged
  # a here-string, not a pipe: sql_inline must set LAST_LOG in this shell
  sql_inline sysdba <<<"set heading off feedback off pagesize 0
select 'USERS|' || count(*) from dba_users where username = 'RAG_LAB';"
  users="$(grep -a '^USERS|' "$LAST_LOG" | tail -n 1)"
  users="${users#USERS|}"
  [[ "$users" =~ ^[01]$ ]] || die "could not tell whether RAG_LAB exists"
  if [[ "$users" == 1 ]]; then
    sql_as RAG_LAB 99_cleanup.sql "lab $GENAI_HOST"
  fi
  sql_sysdba 99_cleanup.sql "admin $GENAI_HOST"
  # (a lab-internal isolation snapshot of the existing HR app ran here: removed)
  lab_script 99b_cleanup_files.sh --yes
}

# ----------------------------------------------------------------------------- main
main() {
  local s known=" ${ORDER[*]} "
  [[ -n "$STAGES" ]] || { sed -n '2,/^# Secrets/p' "${BASH_SOURCE[0]}" >&2; exit 2; }
  for s in ${STAGES//,/ }; do
    [[ "$known" == *" ${s^^} "* ]] || die "unknown stage $s (known:${known})"
  done
  [[ "$RUNS" =~ ^[1-9]$ ]] || die "RUNS must be 1-9"
  [[ -f "$EXPERIMENTS" ]] || die "experiments.csv not found"
  if ! dry; then
    mkdir -p "$RESULTS_DIR/logs"
    RUN_LOG="$RESULTS_DIR/logs/run_all_${RUN_STAMP}.log"
    # one lab script at a time (run_all.sh, 01_stage_files.sh, 99b_cleanup_files.sh): held on fd 9
    # for the whole run; the children inherit fd 9 and re-take the same lock through it (lab_script
    # hands them RAG_LAB_LOCK_FD=9)
    exec 9>>"$RESULTS_DIR/.lab.lock"
    flock -n 9 || die "another lab script (run_all.sh, 01_stage_files.sh or 99b_cleanup_files.sh) holds $(basename -- "$RESULTS_DIR")/.lab.lock; wait for it to finish"
    LAB_LOCK_FD=9
    # unscanned output of a step that was killed before its password check: never kept
    find "$RESULTS_DIR/logs" -maxdepth 1 -type f -name '*.log.part' -exec shred -u -- {} + 2>/dev/null || true
    find "$RESULTS_DIR/logs" -maxdepth 1 -type f -name '*.log.part' -delete 2>/dev/null || true
    shred_stale_pw_files
  fi
  log "run_all $VERSION start: STAGES=$STAGES DRY_RUN=$DRY_RUN"
  docker_target_check
  for s in "${ORDER[@]}"; do
    want "$s" || continue
    check_pause
    log "== stage $s"
    check_pipelines "$s start"
    case "$s" in
      CORPUS) stage_corpus ;;
      ADMIN) stage_admin ;;
      CRED) stage_cred ;;
      MODELS) stage_models ;;
      PROFILES) stage_profiles ;;
      BUILD) stage_build ;;
      EVAL_RET) stage_eval_ret ;;
      EVAL_RAG) stage_eval_rag ;;
      CLEANUP) stage_cleanup ;;
    esac
    check_pipelines "$s end"
  done
  log "run_all done: STAGES=$STAGES"
}

main "$@"
