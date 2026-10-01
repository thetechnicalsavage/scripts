#!/usr/bin/env bash
# v1.6 - brief 09: convert the Hugging Face embedding models one at a time, run the parity gates on
#        each, and record the sha256 of every file that passes.
#        v1.6: EXTRA_<KEY>=--per-channel adds per-channel INT8 for one retry (arctic missed 0.98 by 0.0013).
#        v1.5: M2-M5 keep their feed-forward output MatMuls FP32 (converter --keep-fp32): per-tensor INT8 of
#              every MatMul failed e5-base's cosine gates (0.968/0.948) and per-channel was worse (0.905).
#        v1.4: operator decisions of 29-Sep applied. M6 builds in FP32 by default (M6_QUANTIZE=none) and,
#              like M1Q, is verified with --allow-token-divergence (token-id gates reported and waived,
#              every cosine gate enforced); each such pass also gets a line in converted/models_exceptions.txt
#              ("KEY sha256 reason", the format 01_stage_files.sh reads), removed again when the file is
#              rejected or rebuilt. At the start and the end of every run the file is reconciled: a line
#              that is malformed, names another key, or whose sha256 models.sha256 does not list for that
#              key's file is dropped with a warning (Codex review).
#        v1.3: a rejected file also loses its line in models.sha256 (FORCE=1 rebuild that then fails).
#        v1.2 (Codex review): an existing output that is not verified is moved to rejected/ before the
#              guards run, so a stale file never stays in converted/; extra flags are an array (no globbing).
#        v1.1: default batch M2 M4 M5 M3. M6 and M1Q run only when named: both failed a gate on 29-Sep and
#              wait for an operator decision (README "M6", "M1Q"); M6_QUANTIZE=none builds M6 in FP32.
#
# Run as : the build host's OS user. No sudo, no docker, no database. Everything stays under
#          $RAG_LAB_HOME (default ~/rag-lab): venv/, oracle-prebuilt/, hf/, work/, converted/,
#          rejected/, reports/, logs/, tmp/. The scripts live in $RAG_LAB_HOME/code/ together with
#          parity_passages.json (copy code/models/* there).
# Usage  : ~/rag-lab/code/run_conversions.sh [KEY ...]      default order: M2 M4 M5 M3
#          ~/rag-lab/code/run_conversions.sh M6 M1Q            the two operator-accepted exceptions (README)
#          long runs:  nohup ~/rag-lab/code/run_conversions.sh > ~/rag-lab/logs/run_conversions.out 2>&1 < /dev/null &
# Env    : RAG_LAB_HOME   default ~/rag-lab
#          MIN_AVAIL_GB   "available" column of free -g needed before each model (default 16)
#          MIN_DISK_GB    free disk under RAG_LAB_HOME needed before each model (default 20)
#          WAIT_MIN       minutes to wait for memory before giving up on a model (default 30)
#          THREADS        torch / onnxruntime threads, 1..6 (default 6)
#          FORCE=1        rebuild a model even if its verified file is already listed
#          KEEP_WORK=1    keep work/<model>/ (FP32 export, INT8 transformer) after a success
#          M6_QUANTIZE    none (default: FP32 transformer, ~540 MB, the accepted build) | int8 (re-measure
#                         only: fails the cosine gate, and gets no token-id waiver)
# Re-run : safe. A model already listed in converted/models.sha256 whose file still matches is skipped.
#          A model that fails conversion or a gate is moved to rejected/, never left in converted/.
#          Only one copy runs at a time (flock on $RAG_LAB_HOME/.convert.lock).
# Output : converted/<model>.onnx + .build.json, reports/<KEY>_parity.json, logs/<KEY>_<UTC>.log,
#          converted/models.sha256 (sha256sum format; lines starting with # are ignored by sha256sum -c),
#          converted/models_exceptions.txt ("KEY sha256 reason" for M6 and M1Q; the same sha256 is in
#          models.sha256). Both are printed at the end; copy them, and reports/*.json, back to the
#          workstation: code/models/models.sha256, code/models/models_exceptions.txt, results/models/.
# Exit   : 0 all requested models passed (or were already verified), 1 at least one failed, 2 setup error.
set -euo pipefail

RAG_LAB_HOME="${RAG_LAB_HOME:-$HOME/rag-lab}"
MIN_AVAIL_GB="${MIN_AVAIL_GB:-16}"
MIN_DISK_GB="${MIN_DISK_GB:-20}"
WAIT_MIN="${WAIT_MIN:-30}"
THREADS="${THREADS:-6}"
FORCE="${FORCE:-0}"
KEEP_WORK="${KEEP_WORK:-0}"
M6_QUANTIZE="${M6_QUANTIZE:-none}"

log() { printf '%s %-5s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2"; }
die() { log ERROR "$1"; exit 2; }

for v in MIN_AVAIL_GB MIN_DISK_GB WAIT_MIN THREADS; do
  [[ "${!v}" =~ ^[0-9]+$ ]] || die "$v must be a whole number"
done
(( THREADS >= 1 && THREADS <= 6 )) || die "THREADS must be 1..6 (host politeness rule)"
[[ "$M6_QUANTIZE" =~ ^(int8|none)$ ]] || die "M6_QUANTIZE must be int8 or none"

CODE="$RAG_LAB_HOME/code"
PY="$RAG_LAB_HOME/venv/bin/python"
TEMPLATES="$RAG_LAB_HOME/oracle-prebuilt"
OUT="$RAG_LAB_HOME/converted"
REJ="$RAG_LAB_HOME/rejected"
REPORTS="$RAG_LAB_HOME/reports"
LOGS="$RAG_LAB_HOME/logs"
WORK="$RAG_LAB_HOME/work"
SUMS="$OUT/models.sha256"
EXC="$OUT/models_exceptions.txt"
PASSAGES="$CODE/parity_passages.json"

for f in "$PY" "$CODE/convert_hf_to_oracle.py" "$CODE/verify_models.py" "$PASSAGES" \
         "$TEMPLATES/multilingual_e5_small.onnx" "$TEMPLATES/all_MiniLM_L12_v2.onnx"; do
  [ -e "$f" ] || die "missing: ${f/#$HOME/\~}"
done
mkdir -p "$OUT" "$REJ" "$REPORTS" "$LOGS" "$WORK" "$RAG_LAB_HOME/tmp" "$RAG_LAB_HOME/hf/cache" "$RAG_LAB_HOME/hf/home"

# every temporary file, Hugging Face cache and thread pool stays inside the lab directory and the thread cap
export TMPDIR="$RAG_LAB_HOME/tmp" HF_HOME="$RAG_LAB_HOME/hf/home" HF_HUB_DISABLE_TELEMETRY=1
export TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS="$THREADS" MKL_NUM_THREADS="$THREADS" OPENBLAS_NUM_THREADS="$THREADS"

exec 9>"$RAG_LAB_HOME/.convert.lock"
flock -n 9 || die "another run_conversions.sh holds ${RAG_LAB_HOME/#$HOME/\~}/.convert.lock"

# key -> "hf_id revision MODEL_NAME pooling max_tokens template [extra converter flags]"
# Revisions pinned 29-Sep-2026 (HfApi().model_info(id).sha). M1Q keeps Oracle's own e5-small INT8
# transformer and only splices the ids of "query: " after <s>, so M1 vs M1Q isolates the prefix.
spec() {
  case "$1" in
    M2)  echo "intfloat/multilingual-e5-base d128750597153bb5987e10b1c3493a34e5a4502a MULTILINGUAL_E5_BASE mean 512 e5s --keep-fp32 layer[.][0-9]+/output/dense/MatMul" ;;
    M3)  echo "intfloat/multilingual-e5-large 3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3 MULTILINGUAL_E5_LARGE mean 512 e5s --keep-fp32 layer[.][0-9]+/output/dense/MatMul" ;;
    M4)  echo "BAAI/bge-m3 5617a9f61b028005a4858fdac845db406aefb181 BGE_M3 cls 1024 e5s --keep-fp32 layer[.][0-9]+/output/dense/MatMul" ;;
    M5)  echo "Snowflake/snowflake-arctic-embed-l-v2.0 ac6544c8a46e00af67e330e85a9028c66b8cfd9a ARCTIC_EMBED_L_V2 cls 1024 e5s --keep-fp32 layer[.][0-9]+/output/dense/MatMul" ;;
    M6)  echo "Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2 408d483803e83aaea0aceec550deac66e5f8dc11 ARABIC_TRIPLET_V2 mean 512 minilm --lower 0 --strip-accents 0 --sep-id 3 --pad-id 0" ;;
    M1Q) echo "intfloat/multilingual-e5-small 614241f622f53c4eeff9890bdc4f31cfecc418b3 MULTILINGUAL_E5_SMALL_Q mean 512 e5s --reuse-template-transformer --prefix-ids 41,1294,12" ;;
    *)   return 1 ;;
  esac
}

wait_for_memory() {
  local deadline avail
  deadline=$(( $(date +%s) + WAIT_MIN * 60 ))
  while :; do
    avail=$(free -g | awk '/^Mem:/ {print $7}')
    [[ "$avail" =~ ^[0-9]+$ ]] || { log ERROR "cannot read free -g"; return 1; }
    if (( avail >= MIN_AVAIL_GB )); then log INFO "memory: ${avail} GB available"; return 0; fi
    if (( $(date +%s) >= deadline )); then
      log ERROR "memory: only ${avail} GB available after ${WAIT_MIN} min (need ${MIN_AVAIL_GB})"; return 1
    fi
    log WARN "memory: ${avail} GB available, need ${MIN_AVAIL_GB}; waiting 60 s"
    sleep 60
  done
}

disk_ok() {
  local free_gb
  free_gb=$(df -BG --output=avail "$RAG_LAB_HOME" | tail -1 | tr -dc '0-9')
  if (( free_gb < MIN_DISK_GB )); then log ERROR "disk: ${free_gb} GB free, need ${MIN_DISK_GB}"; return 1; fi
  log INFO "disk: ${free_gb} GB free"
}

# drop one file's line from models.sha256; idempotent
unlist() {
  local file="$1" tmp
  [ -f "$SUMS" ] || return 0
  tmp="$(mktemp "$OUT/.models.sha256.XXXXXX")"
  awk -v f="$file" '!(NF == 2 && ($2 == f || $2 == "*" f))' "$SUMS" > "$tmp"
  mv "$tmp" "$SUMS"
}

# replace (or add) the line for one file in models.sha256; idempotent
record_sum() {
  local file="$1" line
  line="$(cd "$OUT" && sha256sum "$file")"
  unlist "$file"
  [ -f "$SUMS" ] || printf '# v1.0 - brief 09: sha256 of the converted embedding models that passed verify_models.py\n' > "$SUMS"
  printf '%s\n' "$line" >> "$SUMS"
}

# the operator's 29-Sep exceptions: M1Q, and M6 in FP32 only. One line each: "KEY sha256 reason"
# (whitespace-separated, as 01_stage_files.sh parses it; a reason holds no tab or newline).
needs_exception() {
  [ "$1" = "M1Q" ] || { [ "$1" = "M6" ] && [ "$M6_QUANTIZE" = "none" ]; }
}

exception_reason() {
  case "$1" in
    M6)  echo "FP32 (M6_QUANTIZE=none); token-id gates waived for the in-database BERT tokenizer op's Arabic behaviour (Arabic punctuation is not split off; a partly covered word keeps its pieces plus [UNK]); every cosine gate passed, EN and AR separately; operator decision 29-Sep-2026; report reports/M6_parity.json" ;;
    M1Q) echo "exploratory; token-id gates waived for the disclosed SentencePiece tie-break divergence of the spliced 'query: ' prefix (1 of 70 inputs on 29-Sep); every cosine gate passed, EN and AR separately; operator decision 29-Sep-2026; report reports/M1Q_parity.json" ;;
    *)   return 1 ;;
  esac
}

# drop one key's line from models_exceptions.txt; idempotent
unlist_exception() {
  local key="$1" tmp
  [ -f "$EXC" ] || return 0
  tmp="$(mktemp "$OUT/.models_exceptions.XXXXXX")"
  awk -v k="$key" '$1 != k' "$EXC" > "$tmp"
  mv "$tmp" "$EXC"
}

# replace (or add) the line for one key, with the sha256 of the file as it is now; idempotent
record_exception() {
  local key="$1" file="$2" sum reason
  reason="$(exception_reason "$key")" || { log ERROR "$key: no exception reason defined"; return 1; }
  sum="$(cd "$OUT" && sha256sum "$file")"
  sum="${sum%% *}"
  unlist_exception "$key"
  [ -f "$EXC" ] || printf '%s\n%s\n' \
    '# v1.0 - brief 09: converted models that passed verify_models.py under an operator-accepted exception' \
    '# format: KEY sha256 reason   (the same sha256 is listed in models.sha256)' > "$EXC"
  printf '%s %s %s\n' "$key" "$sum" "$reason" >> "$EXC"
}

exception_key() { [ "$1" = "M6" ] || [ "$1" = "M1Q" ]; }

# keep only well-formed M6/M1Q lines whose sha256 models.sha256 lists for that key's file (the last line
# per key wins); comments are kept. Idempotent; run at the start and the end of every batch.
reconcile_exceptions() {
  local tmp line key sum reason name file why
  [ -f "$EXC" ] || return 0
  tmp="$(mktemp "$OUT/.models_exceptions.XXXXXX")"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) printf '%s\n' "$line" >> "$tmp"; continue ;;
    esac
    key="" sum="" reason="" why=""
    read -r key sum reason <<<"$line" || true
    if ! exception_key "$key"; then why="not an exception key"
    elif ! [[ "$sum" =~ ^[0-9a-f]{64}$ ]] || [ -z "$reason" ]; then why="malformed"
    else
      read -r _ _ name _ <<<"$(spec "$key")"
      file="$(tr '[:upper:]' '[:lower:]' <<<"$name").onnx"
      if ! { [ -f "$SUMS" ] && { grep -qxF "$sum  $file" "$SUMS" || grep -qxF "$sum *$file" "$SUMS"; }; }; then
        why="its sha256 is not listed for $file in models.sha256"
      fi
    fi
    if [ -n "$why" ]; then
      log WARN "models_exceptions.txt: dropped the line for '${key:-?}' ($why)"
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$EXC"
  # one line per key, the last one, as 01_stage_files.sh refuses two
  awk '/^#/ || NF == 0 { keep[NR] = 1 } !/^#/ && NF > 0 { last[$1] = NR; key[NR] = $1 }
       { line[NR] = $0 } END { for (i = 1; i <= NR; i++) if (keep[i] || last[key[i]] == i) print line[i] }' \
    "$tmp" > "$tmp.1"
  mv "$tmp.1" "$EXC"
  rm -f "$tmp"
}

has_exception() {
  local key="$1" file="$2" sum
  [ -f "$EXC" ] || return 1
  sum="$(cd "$OUT" && sha256sum "$file")"
  sum="${sum%% *}"
  awk -v k="$key" -v s="$sum" '$1 == k && $2 == s { found = 1 } END { exit !found }' "$EXC"
}

already_verified() {
  local file="$1"
  [ "$FORCE" = "1" ] && return 1
  [ -f "$OUT/$file" ] && [ -f "$SUMS" ] || return 1
  grep -qE "^[0-9a-f]{64}  \*?${file//./\\.}$" "$SUMS" || return 1
  (cd "$OUT" && grep -E "  \*?${file//./\\.}$" "$SUMS" | sha256sum -c --status -)
}

reject() {
  local file="$1" stamp="$2" key="${3:-}"
  unlist "$file"
  [ -n "$key" ] && unlist_exception "$key"
  for f in "$OUT/$file" "$OUT/$file.build.json" "$OUT/$file.partial"; do
    [ -e "$f" ] && mv -f "$f" "$REJ/$(basename "$f").$stamp"
  done
  return 0
}

keys=("$@")
[ ${#keys[@]} -gt 0 ] || keys=(M2 M4 M5 M3)
for k in "${keys[@]}"; do spec "$k" > /dev/null || die "unknown model key: $k (known: M2 M3 M4 M5 M6 M1Q)"; done
reconcile_exceptions

failed=()
for key in "${keys[@]}"; do
  read -r hf_id rev name pooling cap template extra_str <<<"$(spec "$key")"
  extra=()
  [ -n "${extra_str:-}" ] && read -r -a extra <<<"$extra_str"      # split on spaces, never globbed
  # EXTRA_<KEY> (e.g. EXTRA_M5=--per-channel): extra converter flags for one retry. Only
  # --per-channel is accepted, so nothing else can reach the converter from the environment.
  xvar="EXTRA_${key}"
  if [ -n "${!xvar:-}" ]; then
    [ "${!xvar}" = "--per-channel" ] || die "$xvar may only be --per-channel"
    extra+=("--per-channel")
    log INFO "$key: extra converter flag from $xvar: --per-channel"
  fi
  file="$(tr '[:upper:]' '[:lower:]' <<<"$name").onnx"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  logf="$LOGS/${key}_${stamp}.log"
  if already_verified "$file"; then
    log INFO "$key: $file already verified and listed; skipped (FORCE=1 rebuilds)"
    if needs_exception "$key" && ! has_exception "$key" "$file"; then
      record_exception "$key" "$file"
      log WARN "$key: its exception line was missing or stale; written again for the listed file"
    fi
    continue
  fi
  log INFO "$key: $hf_id -> $file (log ${logf/#$HOME/\~})"
  if [ -e "$OUT/$file" ] || [ -e "$OUT/$file.build.json" ]; then
    log WARN "$key: $file exists (FORCE=1, or not verified and listed); moved to rejected/ before rebuilding"
    reject "$file" "$stamp" "$key"
  fi
  if ! wait_for_memory || ! disk_ok; then failed+=("$key"); continue; fi
  oracle_ref=()
  [ "$key" = "M1Q" ] && oracle_ref=(--oracle-ref "$TEMPLATES/multilingual_e5_small.onnx")
  prefix=()
  [ "$key" = "M1Q" ] && prefix=(--prefix-text "query: ")
  quant=()
  [ "$key" = "M6" ] && [ "$M6_QUANTIZE" = "none" ] && quant=(--quantize none)
  waiver=()
  if needs_exception "$key"; then
    waiver=(--allow-token-divergence)
    log WARN "$key: verified under the operator's 29-Sep exception (token-id gates reported and waived; cosine gates enforced)"
  fi
  set +e
  {
    nice -n 10 "$PY" "$CODE/convert_hf_to_oracle.py" --hf-id "$hf_id" --revision "$rev" --model-name "$name" \
      --pooling "$pooling" --max-tokens "$cap" --template "$template" --template-dir "$TEMPLATES" \
      --out "$OUT/$file" --work-dir "$WORK/${file%.onnx}" --cache-dir "$RAG_LAB_HOME/hf/cache" \
      --threads "$THREADS" --min-avail-gb "$MIN_AVAIL_GB" "${prefix[@]}" "${quant[@]}" "${extra[@]}" \
    && nice -n 10 "$PY" "$CODE/verify_models.py" verify --key "$key" --model "$OUT/$file" \
      --passages "$PASSAGES" --report-dir "$REPORTS" --cache-dir "$RAG_LAB_HOME/hf/cache" \
      --threads "$THREADS" "${oracle_ref[@]}" "${waiver[@]}"
  } > "$logf" 2>&1
  rc=$?
  set -e
  if [ $rc -eq 0 ]; then
    record_sum "$file"
    log INFO "$key: PASSED; $(grep -E "  \*?${file//./\\.}$" "$SUMS")"
    if needs_exception "$key"; then
      record_exception "$key" "$file"
      log INFO "$key: exception recorded in ${EXC/#$HOME/\~}"
    else
      unlist_exception "$key"
    fi
    [ "$KEEP_WORK" = "1" ] || rm -rf "${WORK:?}/${file%.onnx}"
  else
    log ERROR "$key: FAILED (rc $rc); last lines of the log:"
    tail -n 5 "$logf" | sed 's/^/    /'
    reject "$file" "$stamp" "$key"
    failed+=("$key")
  fi
done

reconcile_exceptions
log INFO "models.sha256:"
[ -f "$SUMS" ] && cat "$SUMS"
if [ -f "$EXC" ]; then
  log INFO "models_exceptions.txt:"
  cat "$EXC"
fi
if [ ${#failed[@]} -gt 0 ]; then log ERROR "failed: ${failed[*]}"; exit 1; fi
log INFO "all requested models passed"
