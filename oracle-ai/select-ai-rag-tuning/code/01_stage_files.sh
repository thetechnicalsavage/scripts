#!/usr/bin/env bash
# v1.6 - RAG tuning lab (brief 09): stage files for the database under /opt/oracle/kb/rag_lab in
#        container ora26ai: the corpus, the ONNX models, probe files and the lab's SQL scripts.
#        v1.6: public copy: the lab-internal isolation baseline (ISOLATION_BASELINE, rl_live_hr) removed;
#              the existing HR app's fixed corpus paths are still refused.
#        v1.5: GO-1 29-Sep: probe-rm accepts --allow-non-ascii for the p13 row of probe_files.txt only (P15
#              needs the Arabic-named P13 copy gone: the pipeline fails it with ORA-22288 and the whole
#              run with ORA-20003).
#        v1.4: Codex adversarial review: every command that touches the container holds the shared
#              lab lock (RESULTS_DIR/.lab.lock) for its whole run, or run_all.sh's inherited
#              descriptor of it (RAG_LAB_LOCK_FD); new 'model-pin' prints a key's pinned sha256 for
#              run_all.sh's in-container check before 03.
#        v1.3: VM-safety review: a model is pinned only by the list of passed models
#              (MODEL_SRC_DIR/models.sha256), an explicit exception line (models/models_exceptions.txt)
#              or Oracle's own M1 file; the converter's .build.json only cross-checks. New
#              'model-keys'. The existing HR app's live corpus path (from the isolation baseline) joins the
#              refused paths, and CORPUS refuses without a baseline. Only the local daemon's
#              ora26ai is used (DOCKER_HOST/DOCKER_CONTEXT refused, context and name checked).
#              docker cp copies straight from the verified source (no /tmp copy of a model).
#        v1.2: Codex re-review: mount-mode rl_mkdirs checks every existing lab directory for a
#              symlink before any mkdir or chmod (chmod follows links).
#        v1.1: Codex review: docker-cp mode proves with realpath inside the container that the lab
#              tree has no symlink and does not overlap the existing HR app's path, at resolve time and before
#              every write or delete (mount mode checks the parent directory before each write).
#        v1.0: first version, PLAN.md v1.2 sections 0.7, 2.3 and 7 (01).
#
# Run as : the VM OS user on the host that runs container ora26ai (not root).
# Usage  : 01_stage_files.sh corpus <pdf|docx> [--replace] [--manifest-copy FILE]
#          01_stage_files.sh verify <pdf|docx>          check the corpus dir, change nothing
#          01_stage_files.sh model <KEY>                 KEY = M1 M2 M3 M4 M5 M6 M1Q
#          01_stage_files.sh model-keys                  keys with a pin (below); no docker
#          01_stage_files.sh model-pin <KEY>             that key's pinned sha256 on stdout; no docker
#          01_stage_files.sh unstage-model <KEY>         after 03_load_models.sql succeeded
#          01_stage_files.sh probe <src_dir> [--allow-non-ascii]
#          01_stage_files.sh probe-rm <file_name> [--allow-non-ascii]   (non-ASCII: the P13 copy only)
#          01_stage_files.sh sql <src_dir>               the *.sql files run_all.sh runs
#          01_stage_files.sh where                       print the method in use, change nothing
# Env    : CORPUS_STAGING  dir holding manifest.csv, pdf/ and docx/ as built on the build host
#                          (default ~/rag-lab/corpus)
#          MODEL_SRC_DIR   converted ONNX files, <lowercase model name>.onnx (default ~/rag-lab/converted)
#          ORACLE_PREBUILT_DIR  Oracle's own multilingual_e5_small.onnx for M1
#                          (default ~/rag-lab/oracle-prebuilt)
#          MODELS_SHA256   the list of models that passed verify_models.py, "sha256  file" lines,
#                          as run_conversions.sh keeps it (default MODEL_SRC_DIR/models.sha256)
#          MODELS_EXCEPTIONS  operator-accepted exceptions, one "KEY sha256 reason" line each
#                          (default <this dir>/models/models_exceptions.txt; written by the models
#                          owner, e.g. M6 in FP32 and M1Q after the operator's 29-Sep decisions)
#          RESULTS_DIR     default ../results: its .lab.lock is the lab lock (flock) that this
#                          script, run_all.sh and 99b_cleanup_files.sh share; another lab script
#                          holding it stops this one at once (model-keys and model-pin need none)
#          RAG_LAB_LOCK_FD set by run_all.sh: the descriptor on which it holds that lock
#          STAGE_METHOD    auto (default) | mount | docker-cp
#                            mount     : write into the host directory behind the container's bind
#                                        mount that covers /opt/oracle/kb/rag_lab (found read-only
#                                        with docker inspect); files stay owned by this OS user
#                            docker-cp : docker cp into the container; files are root-owned and
#                                        read-only for the database; removal needs docker exec -u 0
#                            auto      : mount when a writable bind mount covers the lab root,
#                                        otherwise docker-cp
# Re-run : safe. A file already staged with the same sha256 is left alone. The corpus dir is
#          frozen after GO-1: a different file under an expected name, or any other entry in it,
#          stops the script unless --replace is given (run_all.sh allows that only before the
#          first index is built).
#
# Model pins (rl_model_pin): a file is staged only when its sha256 equals a pin from the list of
# passed models, from an exception line for its key, or (M1 only) Oracle's published file.
# Pins from more than one source must agree. A <file>.build.json sidecar never pins on its own:
# when present it must agree with the pin. The load itself (03) is permanent, so nothing that
# skipped the gates may reach RAG_MODEL_DIR.
#
# Guards (every write goes through rl_put / rl_rm / rl_mkdirs, which call rl_guard_cpath):
#   - docker talks to the local daemon only: DOCKER_HOST or another DOCKER_CONTEXT is refused,
#     the current context must be 'default' and the container must be named /ora26ai;
#   - the container path must be /opt/oracle/kb/rag_lab or under it, plain characters, no '..';
#   - it must not equal, contain or lie under the existing HR app's corpus path (the two paths it
#     has used, RL_HR_PATHS_FIXED);
#   - in mount mode the host path must be the bind-mount source plus the same suffix, with no
#     symlink inside the lab tree, and must not overlap the host path behind the existing HR app's corpus;
#   - in docker-cp mode realpath inside the container must give back the same path, and the existing HR app's
#     path (resolved the same way) must not overlap it;
#   - symlinks, directories or hidden files inside the corpus dir are refused.
# The corpus dir holds document files only; the staged manifest is written next to it
# (/opt/oracle/kb/rag_lab/corpus_manifest.csv), never inside it.
# Nothing here reads or prints a credential.

readonly RL_CONTAINER="ora26ai"
readonly RL_ROOT_C="/opt/oracle/kb/rag_lab"
readonly RL_HR_PATHS_FIXED=("/opt/oracle/kb/hr" "/opt/oracle/oradata/kb/hr")
readonly RL_MIN_FREE_EXTRA=$((5 * 1024 * 1024 * 1024))
# Oracle's published multilingual_e5_small.onnx (M1), used as it ships (PLAN.md section 3)
readonly RL_M1_ORACLE_SHA256="3edf789dad194922c6131dce3b4435d9541dfa4abfaea5f6a6a6f1d6dbf2bbf2"
readonly RL_MODEL_KEYS=(M1 M2 M3 M4 M5 M6 M1Q)
RL_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER="${DOCKER:-docker}"
PY="${PY:-python3}"
RL_METHOD=""
RL_ROOT_H=""
RL_TMP=""
RL_TARGET_OK=0
RL_HR_PATHS_C=("${RL_HR_PATHS_FIXED[@]}")

rl_log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
rl_die() { rl_log "ERROR: $*"; exit 1; }

# The lab lock: one lab script at a time (run_all.sh, this script, 99b_cleanup_files.sh), so a
# cleanup can never remove the lab tree under a staging run. Held until the script exits. Under
# run_all.sh the inherited descriptor RAG_LAB_LOCK_FD is used: it must be open on this very lock
# file, and flock on it succeeds only because it is run_all.sh's own open file (a lock taken
# through any other open of the file blocks it). Otherwise the lock is taken on fd 9 here.
rl_lab_lock() {
  local dir lockf fd="${RAG_LAB_LOCK_FD:-}"
  dir="${RESULTS_DIR:-$RL_HERE/../results}"
  lockf="$dir/.lab.lock"
  if [[ "$fd" =~ ^[3-9]$ && -e "/dev/fd/$fd" && -e "$lockf" && "/dev/fd/$fd" -ef "$lockf" ]]; then
    flock -n "$fd" || rl_die "another lab script holds $(basename -- "$dir")/.lab.lock (the inherited descriptor is not its holder); wait for it to finish"
    return 0
  fi
  mkdir -p -- "$dir"
  exec 9>>"$lockf"
  flock -n 9 || rl_die "another lab script (run_all.sh, 01_stage_files.sh or 99b_cleanup_files.sh) holds $(basename -- "$dir")/.lab.lock; wait for it to finish"
}

# true when a and b are the same path or one lies under the other
rl_clash() {
  local a="${1%/}" b="${2%/}"
  [[ "$a" == "$b" || "$a" == "$b"/* || "$b" == "$a"/* ]]
}

# a container path this script may WRITE or DELETE: under the lab root, never the existing HR app's
rl_guard_cpath() {
  local p="$1" h
  [[ "$p" =~ ^/[A-Za-z0-9._/-]+$ ]] || rl_die "refusing path with unexpected characters: $p"
  case "$p" in
    *//* | */./* | */. | */../* | */..) rl_die "refusing non-canonical path: $p" ;;
  esac
  [[ "$p" == "$RL_ROOT_C" || "$p" == "$RL_ROOT_C"/* ]] || rl_die "refusing $p: not under $RL_ROOT_C"
  for h in "${RL_HR_PATHS_C[@]}"; do
    if rl_clash "$p" "$h"; then rl_die "refusing $p: it overlaps the existing HR app's corpus path"; fi
  done
}

# docker must reach the local daemon's ora26ai and nothing else (checked once, before any
# docker call that reads or writes the container)
rl_docker_target() {
  local ctx name
  ((RL_TARGET_OK)) && return 0
  [[ -z "${DOCKER_HOST:-}" ]] || rl_die "DOCKER_HOST is set: this lab talks only to the local docker daemon (unset it)"
  [[ -z "${DOCKER_CONTEXT:-}" || "${DOCKER_CONTEXT:-}" == default ]] \
    || rl_die "DOCKER_CONTEXT names another context: this lab talks only to the local docker daemon (unset it)"
  ctx="$("$DOCKER" context show 2>/dev/null)" || rl_die "docker context show failed"
  [[ "$ctx" == default ]] || rl_die "docker's current context is not 'default': refusing"
  name="$("$DOCKER" inspect --type container --format '{{.Name}}' "$RL_CONTAINER" 2>/dev/null)" \
    || rl_die "docker inspect $RL_CONTAINER failed (is the container running?)"
  [[ "$name" == "/$RL_CONTAINER" ]] || rl_die "the container answering to $RL_CONTAINER is not /$RL_CONTAINER: refusing"
  RL_TARGET_OK=1
}

# (rl_live_hr, which read the existing HR app's live path from a lab-internal snapshot, removed)

# container path -> "rw|ro<TAB>canonical-source<TAB>suffix" of the bind mount covering it, or nothing
rl_mount_of() {
  local cpath="$1" json
  rl_docker_target
  json="$("$DOCKER" inspect --type container --format '{{json .Mounts}}' "$RL_CONTAINER")" \
    || rl_die "docker inspect $RL_CONTAINER failed (is the container running?)"
  RL_JSON="$json" "$PY" - "$cpath" <<'PY'
import json, os, sys
cpath = sys.argv[1]
best = None
for m in json.loads(os.environ["RL_JSON"]) or []:
    if m.get("Type") != "bind":
        continue
    d = (m.get("Destination") or "").rstrip("/") or "/"
    if cpath == d or cpath.startswith(d.rstrip("/") + "/"):
        if best is None or len(d) > len(best[0]):
            best = (d, m.get("Source") or "", bool(m.get("RW")))
if best:
    d, src, rw = best
    suffix = cpath[len(d):] if d != "/" else cpath
    print(("rw" if rw else "ro") + "\t" + os.path.realpath(src) + "\t" + suffix)
PY
}

# decide mount or docker-cp once; sets RL_METHOD and RL_ROOT_H
rl_resolve() {
  [[ -n "$RL_METHOD" ]] && return 0
  local want="${STAGE_METHOD:-auto}" line="" rw="" src="" suf="" host anc h hl hhost
  case "$want" in auto | mount | docker-cp) ;; *) rl_die "STAGE_METHOD must be auto, mount or docker-cp" ;; esac
  rl_docker_target
  if [[ "$want" != "docker-cp" ]]; then
    line="$(rl_mount_of "$RL_ROOT_C")"
  fi
  if [[ -n "$line" ]]; then
    IFS=$'\t' read -r rw src suf <<<"$line"
    host="${src%/}${suf}"
    [[ "$host" == /*/rag_lab ]] || rl_die "resolved host path does not end in /rag_lab: refusing"
    # no symlink anywhere inside the lab tree (the mount source itself is already canonical)
    [[ "$(realpath -m -- "$host")" == "$host" ]] || rl_die "a symlink sits inside the lab tree on the host: refusing"
    anc="$host"
    while [[ ! -e "$anc" ]]; do anc="$(dirname -- "$anc")"; done
    if [[ "$rw" == "rw" && -d "$anc" && -w "$anc" ]]; then
      RL_METHOD="mount"
      RL_ROOT_H="$host"
    elif [[ "$want" == "mount" ]]; then
      rl_die "the bind mount covering $RL_ROOT_C is read-only or not writable by $(id -un)"
    fi
  elif [[ "$want" == "mount" ]]; then
    rl_die "no bind mount of $RL_CONTAINER covers $RL_ROOT_C (use STAGE_METHOD=docker-cp or add a mount)"
  fi
  [[ -n "$RL_METHOD" ]] || RL_METHOD="docker-cp"
  if [[ "$RL_METHOD" == "docker-cp" ]]; then
    rl_c_check_tree
  fi

  # the host directory behind the existing HR app's corpus must not overlap the lab tree either
  if [[ "$RL_METHOD" == "mount" ]]; then
    for h in "${RL_HR_PATHS_C[@]}"; do
      hl="$(rl_mount_of "$h")"
      [[ -n "$hl" ]] || continue
      IFS=$'\t' read -r rw src suf <<<"$hl"
      hhost="$(realpath -m -- "${src%/}${suf}")"
      if rl_clash "$RL_ROOT_H" "$hhost"; then
        rl_die "the lab tree and the existing HR app's corpus share a host directory: refusing"
      fi
    done
  fi
}

# docker-cp mode: realpath inside the container must show no symlink inside the lab tree, and
# the existing HR app's paths, resolved the same way, must not overlap it
rl_c_real() {
  local out
  out="$("$DOCKER" exec "$RL_CONTAINER" realpath -m -- "$1")" || rl_die "realpath failed in $RL_CONTAINER"
  printf '%s' "$out"
}

rl_c_check_path() {   # $1 container path that is about to be written or deleted
  local p="$1" h real base base_real
  rl_guard_cpath "$p"
  # a link above the lab root (e.g. /opt/oracle/kb itself) is tolerated; none inside the lab tree
  base="${RL_ROOT_C%/*}"
  base_real="$(rl_c_real "$base")"
  real="$(rl_c_real "$p")"
  [[ "$real" == "$base_real${p#"$base"}" ]] || rl_die "in $RL_CONTAINER, $p resolves elsewhere (symlink): refusing"
  for h in "${RL_HR_PATHS_C[@]}"; do
    if rl_clash "$real" "$(rl_c_real "$h")"; then rl_die "in $RL_CONTAINER, $p overlaps the existing HR app's corpus: refusing"; fi
  done
}

rl_c_check_tree() {
  local d
  for d in "" /corpus /probe /models /sql; do
    rl_c_check_path "$RL_ROOT_C$d"
  done
}

# mount mode: the host directory a write goes into must be the lab path itself (no symlink)
rl_h_check_parent() {
  local hdir
  hdir="$(dirname -- "$(rl_h "$1")")"
  [[ "$(realpath -m -- "$hdir")" == "$hdir" && ! -L "$hdir" ]] || rl_die "symlink inside the lab tree: $(dirname -- "$1")"
}

# container path -> host path (mount mode; the caller has guarded the container path)
rl_h() { printf '%s%s' "$RL_ROOT_H" "${1#"$RL_ROOT_C"}"; }

rl_c_exists_dir() {
  if [[ "$RL_METHOD" == "mount" ]]; then [[ -d "$(rl_h "$1")" ]]
  else "$DOCKER" exec "$RL_CONTAINER" test -d "$1"; fi
}

# "<type> <name>" per entry (type: f file, d dir, l symlink, ...) of a lab directory
rl_list() {
  local d="$1"
  rl_guard_cpath "$d"
  rl_c_exists_dir "$d" || return 0
  if [[ "$RL_METHOD" == "mount" ]]; then
    find "$(rl_h "$d")" -mindepth 1 -maxdepth 1 -printf '%y %f\n' | LC_ALL=C sort
  else
    "$DOCKER" exec "$RL_CONTAINER" find "$d" -mindepth 1 -maxdepth 1 -printf '%y %f\n' | LC_ALL=C sort
  fi
}

rl_sha() {
  local f="$1" out
  rl_guard_cpath "$f"
  if [[ "$RL_METHOD" == "mount" ]]; then out="$(sha256sum -- "$(rl_h "$f")")"
  else out="$("$DOCKER" exec "$RL_CONTAINER" sha256sum -- "$f")"; fi
  printf '%s' "${out%% *}"
}

rl_mkdirs() {
  local d tmp
  rl_guard_cpath "$RL_ROOT_C"
  if [[ "$RL_METHOD" == "mount" ]]; then
    for d in "" /corpus /probe /models /sql /.incoming; do
      if [[ -e "$RL_ROOT_H$d" || -L "$RL_ROOT_H$d" ]]; then
        [[ ! -L "$RL_ROOT_H$d" && -d "$RL_ROOT_H$d" && "$(realpath -m -- "$RL_ROOT_H$d")" == "$RL_ROOT_H$d" ]] \
          || rl_die "not a plain directory inside the lab tree: $RL_ROOT_C$d"
      fi
    done
    for d in "" /corpus /probe /models /sql /.incoming; do
      mkdir -p -- "$RL_ROOT_H$d"
      chmod 0755 -- "$RL_ROOT_H$d"
      [[ ! -L "$RL_ROOT_H$d" && "$(realpath -m -- "$RL_ROOT_H$d")" == "$RL_ROOT_H$d" ]] \
        || rl_die "symlink inside the lab tree: $RL_ROOT_C$d"
    done
    chmod 0700 -- "$RL_ROOT_H/.incoming"
  else
    rl_c_check_path "$(dirname -- "$RL_ROOT_C")/rag_lab"
    # no root shell needed: copy an empty skeleton; docker cp merges into an existing tree.
    # The skeleton (empty directories) lives in this run's private temp dir, removed on exit.
    [[ -n "$RL_TMP" && -d "$RL_TMP" ]] || rl_die "internal: no private temp dir"
    tmp="$RL_TMP/skel"
    rm -rf -- "$tmp"
    mkdir -p -- "$tmp/rag_lab/corpus" "$tmp/rag_lab/probe" "$tmp/rag_lab/models" "$tmp/rag_lab/sql"
    chmod -R 0755 -- "$tmp/rag_lab"
    "$DOCKER" cp "$tmp/rag_lab" "$RL_CONTAINER:$(dirname -- "$RL_ROOT_C")/"
    rm -rf -- "$tmp"
    rl_c_check_tree
  fi
}

# copy one local file to a container path, atomically in mount mode (mode 0644 there). docker-cp
# mode copies straight from the source, which the caller has verified: no host-side copy.
rl_put() {
  local src="$1" dst="$2" hdst tmp mode
  rl_guard_cpath "$dst"
  [[ -f "$src" && ! -L "$src" ]] || rl_die "source is not a regular file: $src"
  if [[ "$RL_METHOD" == "mount" ]]; then
    rl_h_check_parent "$dst"
    hdst="$(rl_h "$dst")"
    [[ ! -L "$hdst" && ! -d "$hdst" ]] || rl_die "target is a symlink or a directory: $dst"
    tmp="$RL_ROOT_H/.incoming/$(basename -- "$dst").$$.part"
    install -m 0644 -- "$src" "$tmp"
    mv -f -T -- "$tmp" "$hdst"
  else
    rl_c_check_path "$(dirname -- "$dst")"
    rl_c_check_path "$dst"
    [[ "$(basename -- "$src")" == "$(basename -- "$dst")" ]] || rl_die "internal: docker cp keeps the source name"
    # docker cp keeps the source's mode and the copy is root-owned: the database reads it only
    # when the source is world-readable, and a world-writable source is refused
    mode="$(stat -c %a -- "$src")"
    (((8#$mode & 8#004) != 0 && (8#$mode & 8#002) == 0)) \
      || rl_die "source must be world-readable and not world-writable (e.g. chmod 0644): $(basename -- "$src")"
    "$DOCKER" cp "$src" "$RL_CONTAINER:$dst"
  fi
}

rl_rm() {
  local f="$1"
  rl_guard_cpath "$f"
  [[ "$f" != "$RL_ROOT_C" ]] || rl_die "rl_rm removes files, not the lab root"
  if [[ "$RL_METHOD" == "mount" ]]; then
    rl_h_check_parent "$f"
    rm -f -- "$(rl_h "$f")"
  else
    rl_c_check_path "$f"
    "$DOCKER" exec -u 0 "$RL_CONTAINER" rm -f -- "$f"
  fi
}

# bytes available where the lab tree lives
rl_free_bytes() {
  local anc
  if [[ "$RL_METHOD" == "mount" ]]; then
    anc="$RL_ROOT_H"
    while [[ ! -e "$anc" ]]; do anc="$(dirname -- "$anc")"; done
    df -PB1 -- "$anc" | awk 'NR == 2 { print $4 }'
  else
    "$DOCKER" exec "$RL_CONTAINER" df -PB1 "$(dirname -- "$RL_ROOT_C")" | awk 'NR == 2 { print $4 }'
  fi
}

rl_need_space() {
  local bytes="$1" free need
  free="$(rl_free_bytes)"
  [[ "$free" =~ ^[0-9]+$ ]] || rl_die "could not read free space"
  need=$((3 * bytes + RL_MIN_FREE_EXTRA))
  ((free >= need)) || rl_die "not enough space: $free bytes free, need 3 x $bytes + 5 GiB = $need"
  rl_log "space ok: $free bytes free, need $need"
}

# read-only: sha256 of the existing HR app's copy of an India file (in the first fixed path), or
# 'unavailable'
rl_hr_sha() {
  local name="$1" hl rw src suf out base
  base="${RL_HR_PATHS_FIXED[0]}"
  [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.pdf$ ]] || { printf 'unavailable'; return 0; }
  if [[ "$RL_METHOD" == "mount" ]]; then
    hl="$(rl_mount_of "$base")"
    if [[ -n "$hl" ]]; then
      IFS=$'\t' read -r rw src suf <<<"$hl"
      if out="$(sha256sum -- "${src%/}${suf}/$name" 2>/dev/null)"; then printf '%s' "${out%% *}"; return 0; fi
    fi
  else
    if out="$("$DOCKER" exec "$RL_CONTAINER" sha256sum -- "$base/$name" 2>/dev/null)"; then
      printf '%s' "${out%% *}"; return 0
    fi
  fi
  printf 'unavailable'
}

# ------------------------------------------------------------------------------ corpus
# expected corpus: one line per document id, "id<TAB>lang<TAB>region<TAB>file<TAB>sha|-<TAB>source path"
rl_expected_corpus() {
  local fmt="$1" staging="$2"
  "$PY" - "$fmt" "$staging" <<'PY'
import csv, os, re, sys
fmt, staging = sys.argv[1], sys.argv[2]
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(pdf|docx)$")
path = os.path.join(staging, "manifest.csv")
with open(path, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
if not rows:
    sys.exit("manifest.csv is empty")
seen, names = set(), set()
for r in rows:
    rid, region = r["id"].strip(), r["region"].strip()
    if not re.match(r"^[A-Z]{3}-[A-Z0-9]{3}(-(EN|AR))?$", rid) or rid in seen:
        sys.exit(f"bad or duplicate id in manifest: {rid!r}")
    seen.add(rid)
    use = "pdf" if region == "india" else fmt          # India is always the existing HR app's PDF
    name = r[use].strip()
    if not NAME.match(name) or not name.endswith("." + use):
        sys.exit(f"{rid}: file name is not an ASCII {use} name: {name!r}")
    if name in names:
        sys.exit(f"{rid}: file name used twice: {name}")
    names.add(name)
    sha = r["pdf_sha256"].strip().lower() if use == "pdf" else "-"
    if use == "pdf" and not re.match(r"^[0-9a-f]{64}$", sha):
        sys.exit(f"{rid}: manifest has no sha256 for {name}")
    src = os.path.join(staging, use, name)
    if not os.path.isfile(src) or os.path.islink(src):
        sys.exit(f"{rid}: missing in the staging dir: {use}/{name}")
    print("\t".join([rid, r["lang"].strip(), region, name, sha, src]))
PY
}

rl_check_corpus_dir() {   # $1 expected file (lines as above); $2 replace 0|1; prints stray names
  local exp="$1" replace="$2" t n listing names
  listing="$(rl_list "$RL_ROOT_C/corpus")"
  names="$(cut -f4 "$exp")"
  while read -r t n; do
    [[ -n "$n" ]] || continue
    if [[ "$t" != "f" ]]; then rl_die "corpus dir holds a non-file entry ($t): $n"; fi
    if ! grep -qxF -- "$n" <<<"$names"; then
      if [[ "$replace" == 1 ]]; then printf '%s\n' "$n"
      else rl_die "corpus dir holds a file that is not in the manifest: $n (frozen; --replace only before GO-2)"; fi
    fi
  done <<<"$listing"
}

rl_corpus() {   # $1 fmt, $2 replace 0|1, $3 manifest copy path or ''
  local fmt="$1" replace="$2" copy="$3" staging exp id lang region name sha src have got bytes total=0
  local stray manifest_tmp hr kb n listing match
  staging="${CORPUS_STAGING:-$HOME/rag-lab/corpus}"
  [[ -d "$staging" ]] || rl_die "CORPUS_STAGING not found"
  exp="$RL_TMP/expected.tsv"
  manifest_tmp="$RL_TMP/corpus_manifest.csv"
  rl_expected_corpus "$fmt" "$staging" >"$exp" || rl_die "manifest check failed"

  # sources: sha256 of every PDF must match the manifest (India = the existing HR app's originals)
  while IFS=$'\t' read -r id lang region name sha src; do
    got="$(sha256sum -- "$src")"; got="${got%% *}"
    if [[ "$sha" != "-" && "$got" != "$sha" ]]; then rl_die "$id: $name does not match the manifest sha256"; fi
    bytes="$(stat -c %s -- "$src")"
    total=$((total + bytes))
  done <"$exp"
  rl_log "manifest ok: $(wc -l <"$exp") documents, $total bytes, format $fmt (India always pdf)"

  rl_resolve
  rl_mkdirs
  rl_need_space "$total"
  stray="$(rl_check_corpus_dir "$exp" "$replace")"
  if [[ -n "$stray" ]]; then
    while read -r n; do
      [[ -n "$n" ]] || continue
      rl_log "--replace: removing $n from the corpus dir"
      rl_rm "$RL_ROOT_C/corpus/$n"
    done <<<"$stray"
  fi

  printf 'id,lang,region,format,file_name,bytes,sha256,manifest_sha_match,kb_hr_match\n' >"$manifest_tmp"
  listing="$(rl_list "$RL_ROOT_C/corpus")"
  while IFS=$'\t' read -r id lang region name sha src; do
    got="$(sha256sum -- "$src")"; got="${got%% *}"
    have=""
    if grep -qxF -- "f $name" <<<"$listing"; then have="$(rl_sha "$RL_ROOT_C/corpus/$name")"; fi
    if [[ -z "$have" ]]; then
      rl_put "$src" "$RL_ROOT_C/corpus/$name"
      rl_log "staged $name"
    elif [[ "$have" != "$got" ]]; then
      if [[ "$replace" == 1 ]]; then
        rl_put "$src" "$RL_ROOT_C/corpus/$name"
        rl_log "replaced $name"
      else
        rl_die "$name is already staged with other content (frozen; --replace only before GO-2)"
      fi
    fi
    hr="-"
    kb="-"
    if [[ "$region" == "india" ]]; then
      kb="$(rl_hr_sha "$name")"
      if [[ "$kb" == "unavailable" ]]; then hr="unavailable"
      elif [[ "$kb" == "$got" ]]; then hr="identical"
      else hr="different"; fi
    fi
    match="yes"
    if [[ "$sha" == "-" ]]; then match="na"; fi
    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s\n' "$id" "$lang" "$region" "${name##*.}" "$name" \
      "$(stat -c %s -- "$src")" "$got" "$match" "$hr" >>"$manifest_tmp"
  done <"$exp"

  rl_verify_corpus_against "$exp"
  rl_put "$manifest_tmp" "$RL_ROOT_C/corpus_manifest.csv"
  if [[ -n "$copy" ]]; then
    install -m 0644 -- "$manifest_tmp" "$copy"
    rl_log "manifest copy written: $(basename -- "$copy")"
  fi
  rl_log "corpus staged: $(wc -l <"$exp") files in $RL_ROOT_C/corpus ($RL_METHOD); manifest beside it"
  if grep -q ',different$' "$manifest_tmp"; then
    rl_log "NOTE: some India files differ from the existing HR app's copies; S2 cannot be compared with HR_KB_IDX chunk by chunk"
  fi
}

# the corpus dir must hold exactly the expected files with the expected content
rl_verify_corpus_against() {
  local exp="$1" id lang region name sha src got have count listing
  rl_check_corpus_dir "$exp" 0 >/dev/null
  listing="$(rl_list "$RL_ROOT_C/corpus")"
  count="$(grep -c '^f ' <<<"$listing" || true)"
  [[ "$count" == "$(wc -l <"$exp")" ]] || rl_die "corpus dir holds $count files, manifest expects $(wc -l <"$exp")"
  while IFS=$'\t' read -r id lang region name sha src; do
    got="$(sha256sum -- "$src")"; got="${got%% *}"
    have="$(rl_sha "$RL_ROOT_C/corpus/$name")"
    [[ "$have" == "$got" ]] || rl_die "$id: staged $name differs from its source"
  done <"$exp"
  rl_log "corpus dir verified: $count files, every sha256 matches"
}

rl_verify() {
  local fmt="$1" staging exp
  staging="${CORPUS_STAGING:-$HOME/rag-lab/corpus}"
  exp="$RL_TMP/expected.tsv"
  rl_expected_corpus "$fmt" "$staging" >"$exp" || rl_die "manifest check failed"
  rl_resolve
  rl_verify_corpus_against "$exp"
}

# ------------------------------------------------------------------------------ models
rl_model_file() {
  case "${1^^}" in
    M1) echo multilingual_e5_small.onnx ;;
    M2) echo multilingual_e5_base.onnx ;;
    M3) echo multilingual_e5_large.onnx ;;
    M4) echo bge_m3.onnx ;;
    M5) echo arctic_embed_l_v2.onnx ;;
    M6) echo arabic_triplet_v2.onnx ;;
    M1Q) echo multilingual_e5_small_q.onnx ;;
    M0) rl_die "M0 (ALL_MINILM_L12_V2) is already loaded; nothing to stage" ;;
    *) rl_die "unknown model key: $1 (M1 M2 M3 M4 M5 M6 M1Q)" ;;
  esac
}

# where a key's file comes from: Oracle's own file for M1, the converted models otherwise
rl_model_srcdir() {
  if [[ "${1^^}" == M1 ]]; then printf '%s' "${ORACLE_PREBUILT_DIR:-$HOME/rag-lab/oracle-prebuilt}"
  else printf '%s' "${MODEL_SRC_DIR:-$HOME/rag-lab/converted}"; fi
}

rl_passed_list() { printf '%s' "${MODELS_SHA256:-${MODEL_SRC_DIR:-$HOME/rag-lab/converted}/models.sha256}"; }
rl_exceptions_file() { printf '%s' "${MODELS_EXCEPTIONS:-$RL_HERE/models/models_exceptions.txt}"; }

# sha256 of <file> in the list of passed models (sha256sum format, '#' lines ignored), or nothing
rl_passed_sha() {
  local file="$1" list
  list="$(rl_passed_list)"
  [[ -f "$list" ]] || return 0
  awk -v f="$file" '/^[[:space:]]*(#|$)/ { next }
                    { n = $2; sub(/^\*/, "", n); sub(/.*\//, "", n); if (n == f) print tolower($1) }' "$list" | tail -n 1
}

# "sha256<TAB>reason" of the exception line for <KEY>, or nothing; a malformed file stops
rl_exception_of() {
  local key="$1" f
  f="$(rl_exceptions_file)"
  [[ -f "$f" ]] || return 0
  "$PY" - "$f" "$key" <<'PY'
import re, sys
path, want = sys.argv[1], sys.argv[2]
keys = {"M1", "M2", "M3", "M4", "M5", "M6", "M1Q"}
hits = []
with open(path, encoding="utf-8") as f:
    for n, line in enumerate(f, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split(None, 2)
        if len(parts) < 3 or parts[0] not in keys or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[1]):
            sys.exit(f"models_exceptions.txt line {n}: expected 'KEY sha256 reason'")
        if parts[0] == want:
            hits.append((parts[1].lower(), parts[2].replace("\t", " ")))
if len(hits) > 1:
    sys.exit(f"models_exceptions.txt: {len(hits)} lines for {want}; keep one")
if hits:
    print(hits[0][0] + "\t" + hits[0][1])
PY
}

# the pinned sha256 of a key's file. Sources: the list of passed models, an exception line, and
# (M1) Oracle's published file; all that exist must agree. The converter's sidecar only
# cross-checks. Prints the pin; logs where it came from.
rl_model_pin() {
  local key="${1^^}" file="$2" srcdir="$3" passed exc excpin="" reason="" oracle="" side="" pin="" from="" ent
  passed="$(rl_passed_sha "$file")"
  exc="$(rl_exception_of "$key")" || rl_die "cannot read the model exceptions file"
  if [[ -n "$exc" ]]; then excpin="${exc%%$'\t'*}"; reason="${exc#*$'\t'}"; fi
  if [[ "$key" == M1 ]]; then oracle="$RL_M1_ORACLE_SHA256"; fi
  for ent in "passed:$passed" "exception:$excpin" "oracle-prebuilt:$oracle"; do
    [[ -n "${ent#*:}" ]] || continue
    [[ "${ent#*:}" =~ ^[0-9a-f]{64}$ ]] || rl_die "$file: malformed ${ent%%:*} pin"
    if [[ -n "$pin" && "$pin" != "${ent#*:}" ]]; then rl_die "$file: the $from and ${ent%%:*} pins disagree"; fi
    pin="${ent#*:}"
    from="${from:+$from+}${ent%%:*}"
  done
  [[ -n "$pin" ]] || rl_die "$file: no pin. It is not in the list of passed models ($(basename -- "$(rl_passed_list)")), has no exception line and is not Oracle's M1 file; refusing (a .build.json alone never pins)"
  if [[ -f "$srcdir/$file.build.json" ]]; then
    side="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["output"]["sha256"].lower())' "$srcdir/$file.build.json" 2>/dev/null || true)"
    [[ "$side" == "$pin" ]] || rl_die "$file: its .build.json does not agree with the $from pin"
  fi
  if [[ -n "$reason" ]]; then rl_log "$file: operator exception for $key: $reason"; fi
  rl_log "$file: pinned by $from"
  printf '%s' "$pin"
}

# keys that have a pin (M1 always: Oracle's file), in lab order; reads local files only
rl_model_keys() {
  local key file out=() exc
  for key in "${RL_MODEL_KEYS[@]}"; do
    file="$(rl_model_file "$key")"
    exc="$(rl_exception_of "$key")" || rl_die "cannot read the model exceptions file"
    if [[ "$key" == M1 || -n "$(rl_passed_sha "$file")" || -n "$exc" ]]; then out+=("$key"); fi
  done
  printf '%s\n' "${out[*]}"
}

rl_check_models_dir() {
  local t n listing
  listing="$(rl_list "$RL_ROOT_C/models")"
  while read -r t n; do
    [[ -n "$n" ]] || continue
    [[ "$t" == "f" && "$n" =~ ^[a-z0-9_]+\.onnx$ ]] || rl_die "models dir holds an unexpected entry ($t): $n"
  done <<<"$listing"
}

rl_model() {
  local key="$1" replace="$2" file srcdir src pin got have bytes listing
  file="$(rl_model_file "$key")"
  srcdir="$(rl_model_srcdir "$key")"
  src="$srcdir/$file"
  [[ -f "$src" && ! -L "$src" ]] || rl_die "not found: $file (M1: ORACLE_PREBUILT_DIR; others: MODEL_SRC_DIR)"
  pin="$(rl_model_pin "$key" "$file" "$srcdir")"
  got="$(sha256sum -- "$src")"; got="${got%% *}"
  [[ "$got" == "$pin" ]] || rl_die "$file: sha256 does not match its pinned value"
  bytes="$(stat -c %s -- "$src")"
  ((bytes < 2000000000)) || rl_die "$file is $bytes bytes; the database loads single files under 2 GB"
  rl_resolve
  rl_mkdirs
  rl_check_models_dir
  have=""
  listing="$(rl_list "$RL_ROOT_C/models")"
  if grep -qxF -- "f $file" <<<"$listing"; then have="$(rl_sha "$RL_ROOT_C/models/$file")"; fi
  if [[ "$have" == "$got" ]]; then
    rl_log "$file already staged with the pinned sha256"
    return 0
  fi
  if [[ -n "$have" && "$replace" != 1 ]]; then rl_die "$file is staged with other content; use --replace"; fi
  rl_need_space "$bytes"
  rl_put "$src" "$RL_ROOT_C/models/$file"
  have="$(rl_sha "$RL_ROOT_C/models/$file")"
  [[ "$have" == "$got" ]] || rl_die "$file: staged copy does not match"
  rl_log "staged $file ($bytes bytes, sha256 verified) in $RL_ROOT_C/models"
}

rl_unstage_model() {
  local file listing
  file="$(rl_model_file "$1")"
  rl_resolve
  listing="$(rl_list "$RL_ROOT_C/models")"
  if grep -qxF -- "f $file" <<<"$listing"; then
    rl_rm "$RL_ROOT_C/models/$file"
    rl_log "removed $file from $RL_ROOT_C/models (the model stays loaded in the database)"
  else
    rl_log "$file is not staged; nothing to remove"
  fi
}

# ------------------------------------------------------------------------------ probe and sql
rl_probe() {
  local srcdir="$1" allow="$2" f name t n listing
  [[ -d "$srcdir" ]] || rl_die "probe source dir not found"
  rl_resolve
  rl_mkdirs
  listing="$(rl_list "$RL_ROOT_C/probe")"
  while read -r t n; do
    [[ -z "$n" || "$t" == "f" ]] || rl_die "probe dir holds a non-file entry ($t): $n"
  done <<<"$listing"
  for f in "$srcdir"/*; do
    [[ -f "$f" && ! -L "$f" ]] || continue
    name="$(basename -- "$f")"
    if [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
      rl_put "$f" "$RL_ROOT_C/probe/$name"
    elif [[ "$allow" == 1 && "$name" != .* && "$name" != *[[:cntrl:]]* ]]; then
      # P13: a multibyte name on purpose. The guard checks the directory, then docker cp or
      # install writes the one file under it.
      rl_guard_cpath "$RL_ROOT_C/probe"
      if [[ "$RL_METHOD" == "mount" ]]; then
        rl_h_check_parent "$RL_ROOT_C/probe/x"
        install -m 0644 -- "$f" "$(rl_h "$RL_ROOT_C/probe")/$name"
      else
        rl_c_check_path "$RL_ROOT_C/probe"
        "$DOCKER" cp "$f" "$RL_CONTAINER:$RL_ROOT_C/probe/$name"
      fi
    else
      rl_die "probe file name is not ASCII (use --allow-non-ascii for P13 only): $name"
    fi
    rl_log "probe file staged: $name"
  done
}

rl_probe_rm() {
  local name="$1" allow="${2:-0}"
  # A non-ASCII name is accepted only with --allow-non-ascii and only when probe_files.txt lists it
  # (the P13 multibyte copy), the same rule as staging; nothing else can be named that way.
  if [[ ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    [[ "$allow" == 1 ]] || rl_die "probe-rm takes one ASCII file name (use --allow-non-ascii for the P13 copy only)"
    [[ "$name" != */* && "$name" != .* && "$name" != *$'\n'* ]] || rl_die "bad probe file name"
    awk -F'|' -v n="$name" '!/^#/ && $1 == n && $2 == "p13" {f = 1} END {exit !f}' "$RL_HERE/probe_files.txt" \
      || rl_die "probe-rm: a non-ASCII name must be a p13 row of probe_files.txt"
  fi
  rl_resolve
  rl_rm "$RL_ROOT_C/probe/$name"
  rl_log "probe file removed: $name"
}

rl_sql() {
  local srcdir="$1" f name n=0
  [[ -d "$srcdir" ]] || rl_die "sql source dir not found"
  rl_resolve
  rl_mkdirs
  for f in "$srcdir"/*.sql; do
    [[ -f "$f" && ! -L "$f" ]] || continue
    name="$(basename -- "$f")"
    [[ "$name" =~ ^[0-9A-Za-z_]+\.sql$ ]] || rl_die "unexpected sql file name: $name"
    rl_put "$f" "$RL_ROOT_C/sql/$name"
    n=$((n + 1))
  done
  rl_log "sql scripts staged: $n files in $RL_ROOT_C/sql"
}

rl_main() {
  set -euo pipefail
  shopt -s inherit_errexit
  umask 022
  local cmd="${1:-}" replace=0 copy="" allow=0 key
  shift || true
  # local files only, no container: no lock needed
  if [[ "$cmd" == model-keys ]]; then
    rl_model_keys
    return 0
  fi
  if [[ "$cmd" == model-pin ]]; then
    key="${1:-}"
    [[ " ${RL_MODEL_KEYS[*]} " == *" ${key^^} "* ]] || rl_die "usage: model-pin <KEY> (M1 M2 M3 M4 M5 M6 M1Q)"
    rl_model_pin "$key" "$(rl_model_file "$key")" "$(rl_model_srcdir "$key")"
    printf '\n'
    return 0
  fi
  case "$cmd" in
    corpus | verify | model | unstage-model | probe | probe-rm | sql | where) rl_lab_lock ;;
  esac
  RL_TMP="$(mktemp -d)"
  trap 'rm -rf -- "$RL_TMP"' EXIT
  # (the lab-internal isolation-baseline read, rl_live_hr, ran here: removed)
  case "$cmd" in
    corpus)
      local fmt="${1:-}"
      shift || true
      [[ "$fmt" == pdf || "$fmt" == docx ]] || rl_die "usage: corpus <pdf|docx> [--replace] [--manifest-copy FILE]"
      while (($#)); do
        case "$1" in
          --replace) replace=1 ;;
          --manifest-copy) copy="${2:-}"; [[ -n "$copy" ]] || rl_die "--manifest-copy needs a file"; shift ;;
          *) rl_die "unknown option: $1" ;;
        esac
        shift
      done
      rl_corpus "$fmt" "$replace" "$copy"
      ;;
    verify)
      [[ "${1:-}" == pdf || "${1:-}" == docx ]] || rl_die "usage: verify <pdf|docx>"
      rl_verify "$1"
      ;;
    model)
      [[ -n "${1:-}" ]] || rl_die "usage: model <KEY> [--replace]"
      if [[ "${2:-}" == "--replace" ]]; then replace=1; fi
      rl_model "$1" "$replace"
      ;;
    unstage-model)
      [[ -n "${1:-}" ]] || rl_die "usage: unstage-model <KEY>"
      rl_unstage_model "$1"
      ;;
    probe)
      [[ -n "${1:-}" ]] || rl_die "usage: probe <src_dir> [--allow-non-ascii]"
      if [[ "${2:-}" == "--allow-non-ascii" ]]; then allow=1; fi
      rl_probe "$1" "$allow"
      ;;
    probe-rm)
      if [[ "${2:-}" == "--allow-non-ascii" ]]; then allow=1; fi
      rl_probe_rm "${1:-}" "$allow"
      ;;
    sql)
      rl_sql "${1:-$RL_HERE}"
      ;;
    where)
      rl_resolve
      rl_log "method $RL_METHOD, container path $RL_ROOT_C"
      ;;
    *)
      sed -n '2,/^# Guards/p' "${BASH_SOURCE[0]}" | sed '$d' >&2
      exit 2
      ;;
  esac
}

# sourced (by 99b_cleanup_files.sh): define the functions only
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  rl_main "$@"
fi
