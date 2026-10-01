#!/usr/bin/env bash
# v1.4 - RAG tuning lab (brief 09): remove the lab's files, /opt/oracle/kb/rag_lab in container
#        ora26ai, and nothing else.
#        v1.4: public copy: the lab-internal isolation-baseline read (rl_live_hr) removed.
#        v1.3: Codex adversarial review: holds the shared lab lock (RESULTS_DIR/.lab.lock, the same
#              one as 01_stage_files.sh and run_all.sh) for its whole run, so it can never remove
#              the lab tree while a staging run writes into it.
#        v1.2: VM-safety review: the existing HR app's live corpus path from the isolation baseline joins the
#              refused paths, and the docker target (local daemon, /ora26ai) is checked first
#              (both through 01_stage_files.sh).
#        v1.1: Codex review: in docker-cp mode realpath inside the container must confirm the path first.
#        v1.0: first version, PLAN.md v1.2 section 7 (99b) and review item 23.
#
# Run as : the VM OS user on the host that runs ora26ai, AFTER 99_cleanup.sql (both phases):
#          the directory objects must be gone before their files are.
# Usage  : 99b_cleanup_files.sh            show what would be removed, remove nothing
#          99b_cleanup_files.sh --yes      remove it
# Env    : STAGE_METHOD  auto (default) | mount | docker-cp, resolved exactly as 01_stage_files.sh
#                        does (this script sources it, so the path guard is the same code)
#          RESULTS_DIR   as in 01_stage_files.sh: .lab.lock there is the shared lab lock; when
#                        another lab script holds it this script stops before looking at anything
# Re-run : safe; an absent tree is reported and left absent.
#
# The one path this script deletes is the constant /opt/oracle/kb/rag_lab. It goes through the
# same guard as every write of 01_stage_files.sh (under the lab root, never overlapping the
# existing HR app's corpus path), and in mount mode the host path must be the bind-mount source plus
# /rag_lab with no symlink in it. rm runs with --one-file-system.
set -euo pipefail
shopt -s inherit_errexit
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=01_stage_files.sh
source "$HERE/01_stage_files.sh"

main() {
  local yes=0 host
  case "${1:-}" in
    "") ;;
    --yes) yes=1 ;;
    *) rl_die "usage: 99b_cleanup_files.sh [--yes]" ;;
  esac
  rl_lab_lock                    # held until this script exits
  # (the lab-internal isolation-baseline read, rl_live_hr, ran here: removed)
  rl_guard_cpath "$RL_ROOT_C"
  rl_resolve

  if [[ "$RL_METHOD" == "mount" ]]; then
    host="$RL_ROOT_H"
    [[ "$host" == /*/rag_lab && "$(realpath -m -- "$host")" == "$host" ]] || rl_die "unexpected host path: refusing"
    if [[ ! -e "$host" ]]; then
      rl_log "$RL_ROOT_C is already absent (mount)"
      return 0
    fi
    [[ -d "$host" && ! -L "$host" ]] || rl_die "the lab root is not a plain directory: refusing"
    rl_log "would remove $RL_ROOT_C (host side of the bind mount): $(find "$host" -type f | wc -l) files"
    ((yes)) || { rl_log "dry run; pass --yes to remove"; return 0; }
    rm -rf --one-file-system -- "$host"
    [[ ! -e "$host" ]] || rl_die "$RL_ROOT_C still present after rm"
  else
    if ! "$DOCKER" exec "$RL_CONTAINER" test -e "$RL_ROOT_C"; then
      rl_log "$RL_ROOT_C is already absent (container)"
      return 0
    fi
    "$DOCKER" exec "$RL_CONTAINER" test ! -L "$RL_ROOT_C" || rl_die "the lab root is a symlink: refusing"
    rl_c_check_path "$RL_ROOT_C"
    rl_log "would remove $RL_ROOT_C inside $RL_CONTAINER: $("$DOCKER" exec "$RL_CONTAINER" find "$RL_ROOT_C" -type f | wc -l) files"
    ((yes)) || { rl_log "dry run; pass --yes to remove"; return 0; }
    # docker cp left the files root-owned, so root removes them; the path is the guarded constant
    "$DOCKER" exec -u 0 "$RL_CONTAINER" rm -rf --one-file-system -- "$RL_ROOT_C"
    if "$DOCKER" exec "$RL_CONTAINER" test -e "$RL_ROOT_C"; then rl_die "$RL_ROOT_C still present after rm"; fi
  fi
  rl_log "removed $RL_ROOT_C"
}

main "$@"
