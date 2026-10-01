<!-- v1.8 - brief 09 run book for the lab scripts (setup, build, cleanup).
     v1.8 (1-Oct): status line: the scripts have run (29-30 Sep).
     v1.7 (1-Oct): public copy: the lab-internal pieces are not shipped and their sections are cut
           (the 00c snapshot of the existing HR app and the SNAP stage, 10_capture_shots.py,
           tools/capture.py, the unit tests); the other app is named the existing HR app.
     v1.6 (30-Sep): 10_capture_shots.py, the screenshot transcripts from the database and the result
           files, and its runner section (order, the after-GO-3 knob shots, exit codes); after Codex
           review 1: the 00c publish whitelist, no-lab-program check, 62 left to the operator.
     v1.5: verifier gaps: run_all's child environment (env -i and an allowlist), the password file
           (RAG_LAB_PWD_FILE, LAB_TMP_DIR) and the rule for RAG_LAB_PWD from the environment, the
           shared lab lock, 01's model-pin and the in-container check before 03, the stop-then-halt
           of a lab pipeline found running, 04_probes.sql's probe-name guard, 00c's privilege line
           fix, eval_rag's repair of a cut-off line, and the accepted residuals.
     v1.4: Codex adversarial review: SQL*Plus-level argument guard in every script (allowed
           characters, password rule); 07's create, wait and stop in one protected block with the
           pipeline recorded at once (PIPELINE| line, RAG_BUILD_LOG); 00c records the privileges on
           ASKORACLE's models and RAG_MODEL_DIR; 99 revokes them and refuses unknown objects or
           ACEs before any change; 03 prints the expected sha256; rebuild wording.
     v1.3: VM-safety review: P0 before ADMIN/MODELS and a P0 gate on MODELS and BUILD; 03's
           capacity gate; model pins (passed list, exceptions, Oracle's M1 file); blocked rows;
           per-key parity skip; pipeline checks around every stage; password-safe step logs;
           docker target checks; eval stamps; hand-run quoting risk; operator decisions of 29-Sep.
           Codex review of the fix: .part step output, exact P0 stamp, STOPPED-only in 07.
     v1.2: after the Codex re-review: P2 parity gate before BUILD (results/model_parity.csv).
     v1.1: after the Codex adversarial review: isolation check before a row counts as built,
           answers only on retrieval-evaluated rows, credential kept unless replaced, docker-cp checks.
     v1.0: first version, written with the scripts; nothing here has been run against a database yet. -->

# Brief 09 lab scripts: how to run them

These scripts set up the RAG tuning lab on the database host, build the vector indexes listed in
`experiments.csv`, and remove the lab again when the operator asks. They follow PLAN.md v1.2,
sections 2, 3 (model loading), 4.3, 7 and 9. The probe scripts (`04_*`, `05_*`) and the evaluation
harness (`eval/`) live beside them and have their own notes.

**Status: run.** These scripts built and evaluated the lab on Oracle AI Database 26ai 23.26.1 on
29 and 30 September 2026; the results are in `../results/`. The unit tests are not shipped.

## Files

| File | Run as | What it does |
|---|---|---|
| `00_admin_prereqs.sql <genai_host>` | SYSDBA in the PDB | user `RAG_LAB` (password by hidden prompt, statement output hidden), quota 3G on USERS, CREATE SESSION/TABLE, EXECUTE on the cloud and vector packages (the owner of `DBMS_VECTOR_CHAIN` is looked up: CTXSYS here), one host ACE. Refuses unless ASKORACLE already holds connect, resolve and http on that exact host |
| `00b_directories.sql` | SYSDBA in the PDB | `RAG_CORPUS_DIR`, `RAG_PROBE_DIR`, `RAG_MODEL_DIR` under `/opt/oracle/kb/rag_lab`; never repoints; refuses any overlap with the existing HR app's corpus path; READ only |
| `01_stage_files.sh` | host OS user | stages the corpus, model files, probe files and the lab's SQL under `/opt/oracle/kb/rag_lab` |
| `02_create_credential.py` | host OS user (python-oracledb) | credential `RAG_LAB_OCI_CRED` from `~/.oci/config`; every value bound, none printed |
| `03_load_models.sql <KEY>` | ASKORACLE | loads one ONNX model into ASKORACLE (idempotent) after a capacity gate, checks dimensions and norms, grants SELECT ON MINING MODEL to RAG_LAB; prints the expected sha256 run_all.sh passed |
| `06_profiles.sql <KEY> <compartment> <region>` | RAG_LAB | the index-side profile `RAG_EMB_<KEY>` |
| `07_build_index.sql <cfg> <KEY> <chunk> <overlap> <metric> <k> <thr>` | RAG_LAB | one lab index: create, wait until the pipeline reports its run over and the counts hold still, and stop the pipeline, all in one block that stops the pipeline on any error; record counts, create `RAG_P_...`, pairing guard |
| `08_set_query_knobs.sql <index> <k> <thr>` | RAG_LAB | match_limit and similarity_threshold on one lab index |
| `99_cleanup.sql lab|admin <genai_host>` | RAG_LAB, then SYSDBA | removes the lab's indexes, profiles, credential, user, ACE and directories, and revokes the lab's privileges on ASKORACLE's models and RAG_MODEL_DIR; refuses before any change if RAG_LAB owns anything that is not a lab object; models stay |
| `99b_cleanup_files.sh [--yes]` | host OS user | removes `/opt/oracle/kb/rag_lab` and nothing else |
| `run_all.sh` | host OS user | runs the stages in order, resumable from `experiments.csv` |
| `experiments.csv` | | one row per build in PLAN.md 4.3 (probe indexes excluded) |

## Names (the shared contract)

| Thing | Name |
|---|---|
| Lab schema | `RAG_LAB` |
| Models | in `ASKORACLE`, permanent: M0 `ALL_MINILM_L12_V2`, M1 `MULTILINGUAL_E5_SMALL`, M2 `MULTILINGUAL_E5_BASE`, M3 `MULTILINGUAL_E5_LARGE`, M4 `BGE_M3`, M5 `ARCTIC_EMBED_L_V2`, M6 `ARABIC_TRIPLET_V2`, M1Q `MULTILINGUAL_E5_SMALL_Q` |
| Model files | `<lowercase model name>.onnx`, staged into `RAG_MODEL_DIR` |
| Index profile | `RAG_EMB_<KEY>`, `embedding_model` = `database:ASKORACLE.<MODEL>` |
| Index | `RAG_<KEY>_C<chunk>_O<overlap>_<COS|DOT|EUC|MAN>`, e.g. `RAG_M0_C1024_O128_COS` |
| Vector table | read from the dictionary (`<index>$VECTAB` by default) |
| RAG profile | `RAG_P_<index without RAG_>`, e.g. `RAG_P_M0_C1024_O128_COS`; same `embedding_model`, `conversation` false, `temperature` 0, `max_tokens` 1024 |
| Credential | `RAG_LAB_OCI_CRED` |
| Build record | `RAG_LAB.RAG_BUILD_LOG` (utc, cfg, index_name, pipeline_name, event), created by 07 |

## Before GO-1: what must be on the database host

1. A copy of this `code/` directory (and `results/` beside it, or set `RESULTS_DIR`).
2. The corpus as built: `~/rag-lab/corpus/manifest.csv`, `pdf/` (84 files) and `docx/` (36 files).
   Set `CORPUS_STAGING` if it lives elsewhere.
3. The models, each with a pin (01 stages nothing else, because a loaded model stays for good):
   - converted models in `~/rag-lab/converted/` listed in its `models.sha256`, the list of models
     that passed `verify_models.py`, as `run_conversions.sh` keeps it (`MODEL_SRC_DIR`,
     `MODELS_SHA256` to point elsewhere);
   - operator-accepted exceptions in `code/models/models_exceptions.txt`, one `KEY sha256 reason`
     line each (29-Sep: M6 `ARABIC_TRIPLET_V2` in FP32, `M6_QUANTIZE=none`, with the token-id
     exception for the in-database BERT tokenizer's Arabic behaviour, its cosine gates still
     passing; M1Q `MULTILINGUAL_E5_SMALL_Q`, exploratory, with its one-in-70 SentencePiece
     divergence disclosed). The models owner writes that file;
   - M1 is Oracle's own `multilingual_e5_small.onnx` from `~/rag-lab/oracle-prebuilt`
     (`ORACLE_PREBUILT_DIR`), pinned in 01 by Oracle's published sha256.
   A converter `<file>.build.json` beside a file never pins it; when present it must agree.
   `./01_stage_files.sh model-keys` prints the keys that have a pin (MODELS uses them by default).
4. `~/rag-lab/venv` with python-oracledb (the scripts fall back to `python3`).
5. `~/oracle26ai/secrets/rag_lab.env`, mode 600, with `RAG_LAB_PWD=` (12 to 64 letters and digits).
   `run_all.sh` reads the ASKORACLE password from `APP_SCHEMA_PWD` in `oracle.env` in the same folder.
   `RAG_LAB_PWD` set in the environment wins over the file, and meets the same rule: anything but
   12 to 64 letters and digits stops the run before SQL*Plus or python sees it.
6. An OCI API key in `~/.oci/config` for `02_create_credential.py` (profile via `OCI_PROFILE`).

Values given at run time only, never written into a file: `GENAI_HOST`
(`inference.generativeai.<region>.oci.oraclecloud.com`) and `OCI_COMPARTMENT`. The region comes
from `GENAI_HOST` unless `OCI_REGION` is set.

## Order of work

Every step below is one `run_all.sh` call. `DRY_RUN=1` in front of any of them prints the plan
without touching docker, the database, the secrets or `experiments.csv`.

**GO-1**

```bash
./01_stage_files.sh model-keys                             # the keys with a pin (local files, no docker, no lock)
./01_stage_files.sh model-pin M4                           # one key's pinned sha256 (local files, no docker, no lock)
# (the lab first took a snapshot of the existing HR app here, STAGES=SNAP: lab-internal, removed)
STAGES=CORPUS CORPUS_FORMAT=pdf ./run_all.sh               # 01: stage the corpus (India is always PDF)
#   P0 and p0-df (PROBES.md): read-only capacity probe, SYSDBA. It writes results/probes/p0.status;
#   MODELS and BUILD refuse to run until its last P0 line is 'P0 PASS YYYY-MM-DDTHH:MM:SSZ'. (29-Sep's ad hoc
#   read passed: AL32UTF8, PGA limit 19 GB, NOARCHIVELOG, USERS/SYSAUX autoextend, ASKORACLE
#   unlimited on USERS, 450 GB free; transcripts/p0-capacity.txt. The probe still writes the file.)
STAGES=ADMIN,CRED GENAI_HOST=... ./run_all.sh              # 00, 00b, 02
STAGES=MODELS ./run_all.sh                                 # 03 for M0 and every pinned key (MODEL_KEYS to narrow)
#   P2 (PROBES.md): DB-vs-local parity per model, appended to results/model_parity.csv
STAGES=PROFILES GENAI_HOST=... OCI_COMPARTMENT=... ./run_all.sh     # 06 (skips models not loaded)
#   ... the other probes (04_*, 05_*) ...
STAGES=CORPUS CORPUS_FORMAT=docx CORPUS_REPLACE=1 ./run_all.sh      # only if P5 says DOCX, before any build
STAGES=BUILD,EVAL_RET ONLY_CONFIGS=1 ./run_all.sh          # S1, then its retrieval evaluation
```

MODELS is resumable: a model already in ASKORACLE is not staged again (03 only checks and grants
it), and each load is preceded by a SYSDBA read of the capacity figures that 03 checks:
ASKORACLE's quota on its default tablespace (unlimited, or at least twice the file), room there
including autoextend headroom (at least twice the file) and, in ARCHIVELOG, at least 5 GB of
recovery-area headroom. A short figure stops 03 before `LOAD_ONNX_MODEL`.

The last step before 03 loads a staged file (the load is permanent) is a hash of that file where
the database will read it: `docker exec ora26ai sha256sum` on `/opt/oracle/kb/rag_lab/models/<file>`,
compared with the pin `./01_stage_files.sh model-pin <KEY>` prints from local files (the list of
passed models, an exception line, or Oracle's M1 file). A mismatch unstages the file and stops the
run, so 03 never runs and no hand run of 03 can load it either. After a match the only container
call before 03 is `test -r` on the 03 script. 03 gets the pin as `define rag_model_sha256` (`na`
when nothing was staged) and prints it in its `MODEL|` line.

**GO-2**: `STAGES=BUILD,EVAL_RET ./run_all.sh` builds every row whose status is `planned` (or
`building`, or `blocked`), one at a time, each followed by its retrieval
evaluation. A row whose model is not loaded in ASKORACLE, or (not M0) has no P2 pass in
`results/model_parity.csv` (below), is marked `blocked` and the run goes on with the next row; the
next BUILD takes it again. Rows 13 (M6) and 18 (M1Q) stay `planned` per the operator's 29-Sep
decisions and build once their models are loaded and pass P2. Before a row with
`pending_decision` can run, fill its TBD fields (model, chunk size, k, index and profile names by
the naming rule) and set its status to `planned`. Rows cut after P12 get the status `cut`.

**GO-3**: `STAGES=EVAL_RAG RUNS=3 ./run_all.sh` takes the answer-layer configs whose retrieval
evaluation has finished (status `evaluated`), re-checks their chunk counts, then runs
the answer harness once over all of them under one stamp. The stamp, configs and runs are kept in
`results/eval_rag.stamp` and passed as `--stamp` on every later EVAL_RAG run until `eval_rag.py`
exits 0, so a paused (`code/PAUSE`, exit 3) or stopped run resumes without repeating a GENERATE
call; the file is then removed and the stamp logged in `results/eval_stamps.log`. The retrieval
harness gets the same treatment per config (`results/eval_retrieval_<cfg>.stamp`), except that a
stamp whose retrieval file already exists is replaced: `eval_retrieval.py` does not resume inside a
file, and `stats.py` refuses a file with two headers for one index.

**GO-4**: only on the operator's word (PLAN.md section 10 keeps the lab for now):
`STAGES=CLEANUP CONFIRM_CLEANUP=RAG_LAB GENAI_HOST=... ./run_all.sh`. It runs `99_cleanup.sql lab`
as RAG_LAB, `99_cleanup.sql admin` as SYSDBA, and
`99b_cleanup_files.sh --yes`. No model is removed at any point; RAG_LAB's SELECT on ASKORACLE's
models and ASKORACLE's READ on RAG_MODEL_DIR are revoked.

Phase lab stops before its first drop when RAG_LAB owns a vector index, profile, pipeline or
credential whose name does not start with `RAG_`. After the index drops it removes the status
tables its `RAG_` pipelines left behind. Phase admin stops before any change when RAG_LAB owns an
object that is not a lab object, or holds a host ACE other than the connect, resolve and http
grants that 00 appended on `GENAI_HOST`, with no ports. Lab objects are names starting `RAG_`
(the vector tables, `RAG_PRB_LOG`, `RAG_BUILD_LOG`), plus the indexes, LOB segments and identity
sequences of such a table, the `VECTOR$<index>$...` tables of a vector index on one, and their
recycle-bin copies. The error lists what it found (ACE hosts masked); the operator decides, then
runs the phase again.

## experiments.csv

Columns: `config_id,stage,model_key,model_name,chunk_size,chunk_overlap,metric,match_limit,similarity_threshold,corpus_format,index_name,rag_profile,answer_layer,status,notes`.

| status | meaning |
|---|---|
| `pending_decision` | a TBD value waits for a dev-split winner or a probe; BUILD skips it |
| `planned` | ready; BUILD takes it in file order |
| `blocked` | BUILD found its model not loaded, or no P2 pass for it; the next BUILD tries it again |
| `building` | BUILD started it and did not finish; a re-run resumes it (07 is idempotent) |
| `built` | index loaded, pipeline stopped, counts in `results/builds.csv` |
| `evaluated` | retrieval evaluation done |
| `failed` | 07 failed; BUILD stops. Fix the cause, then `RETRY_FAILED=1` (add `REBUILD_CONFIGS=<id>` to drop and rebuild an index whose load stopped part way) |
| `cut` | dropped from the matrix by a decision |

`corpus_format` stays `TBD` until the row is built; BUILD fills in the staged format and refuses a
row that names the other one. Config `1r` is the identical rebuild of `1`: 07 drops and rebuilds the
index, and run_all stops if its chunk count or character count differs from config `1`.

## Outputs (under `RESULTS_DIR`)

| Path | Content |
|---|---|
| `builds.csv` | one line per build: files, chunks, characters, seconds, dims, knobs, embedding model, pipeline status |
| `models_loaded.txt` | the `MODEL|...` line of every 03 run (ends with `sha256_expected=`, the pin run_all checked, or `na`) |
| `probes/p0.status` | written by the P0 probe; MODELS and BUILD need `P0 PASS YYYY-MM-DDTHH:MM:SSZ` as its last P0 line |
| `model_parity.csv` | written by the P2 probe: `model_key,status,min_cosine,utc`; the last row per key decides |
| `eval_rag.stamp`, `eval_retrieval_<cfg>.stamp`, `eval_stamps.log` | stamps in use (removed when done) and the finished ones |
| `corpus_manifest_staged.csv` | what was staged: id, format, file, bytes, sha256, and whether each India file equals the existing HR app's copy |
| `logs/` | one SQL*Plus log per step and one log per run (UTC stamps); mode 600. A 07 log holds `PIPELINE|cfg=..|index=..|pipeline=..|event=..` |
| `RAG_LAB.RAG_BUILD_LOG` (in the database) | one row per 07 run, committed the moment the index is created (or found): the pipeline's name, readable while the load runs |

## run_all.sh: child environment, password file and lock

**Child environment.** The first thing `run_all.sh` does is un-export every variable except
`PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ` and `TMPDIR` (and every exported function). The values stay
readable inside the script, but no child inherits them, so an API key, a token or an OCID in the
operator's shell (`GENAI_API_KEY`, `OCI_COMPARTMENT_ID`, ...) goes nowhere. Every docker, python and
bash child then starts from `env -i` with `PATH`, `HOME`, `TZ=UTC`, and `LANG`, `LC_ALL` and
`TMPDIR` when set, plus the few non-secret variables it needs:

| Child | Gets on top of that base |
|---|---|
| `docker` (SQL*Plus included) | nothing; SQL*Plus in the container gets `NLS_LANG` through `docker exec -e` and its input on stdin |
| `02_create_credential.py`, `eval/eval_retrieval.py`, `eval/eval_rag.py` | `RAG_LAB_PWD_FILE`, `RAG_LAB_DSN` |
| `01_stage_files.sh`, `99b_cleanup_files.sh` | `DOCKER`, `RESULTS_DIR`, `STAGE_METHOD`, `RAG_LAB_LOCK_FD` while the lock is held, and `PY`, `CORPUS_STAGING`, `MODEL_SRC_DIR`, `ORACLE_PREBUILT_DIR`, `MODELS_SHA256`, `MODELS_EXCEPTIONS` when set |
| the `experiments.csv` helper (`python -`) | nothing |

`LAB_TMP_DIR` and `CHROME_BIN` are not passed: no child of `run_all.sh` reads them.

**The password file.** The python steps never get the RAG_LAB password's value. `run_all.sh`
writes it once per run into a 0600 file made with `mktemp` in `LAB_TMP_DIR` (default
`~/rag-lab/tmp`; created mode 700 if missing, and it must belong to this user and be closed to
others, mode 700, or the run stops) and passes its path as `RAG_LAB_PWD_FILE`. The main shell's
exit shreds the file, and INT, TERM and HUP are turned into an exit so that happens too. A file
left by a run that was killed outright (`run_all_pwd.*`) is shredded by the next run while it holds
the lab lock; nothing else in that directory is touched. `02_create_credential.py`,
`eval_retrieval.py` (and through it `eval_rag.py`) and `04_probes.py` read
`RAG_LAB_PWD_FILE` first, then `RAG_LAB_PWD`, then a hidden prompt, and drop both variables once
read. The file must be a regular file (no symlink), this user's, closed to others and one line;
an error never shows its path or its content. By hand:
`RAG_LAB_PWD_FILE=<a 0600 file> python3 02_create_credential.py`, or no variable and the prompt.

**One lab script at a time.** `run_all.sh`, `01_stage_files.sh` and `99b_cleanup_files.sh` share
one lock, `RESULTS_DIR/.lab.lock` (flock). `run_all.sh` holds it on fd 9 for its whole run and
hands its children the descriptor (`RAG_LAB_LOCK_FD`); a child takes the lock only through that
inherited descriptor, after checking it is open on that same file, so setting the variable by
hand gets nobody round it. Started on their own while `run_all.sh` runs (a long EVAL_RAG, say),
`01_stage_files.sh` (every command that touches the container) and `99b_cleanup_files.sh --yes`
refuse at once with a message naming `.lab.lock`: wait for the run to finish. `model-keys` and
`model-pin` read local files only and take no lock. The lock files (`.lab.lock`,
`rag_<stamp>.lock`, `p17_chat.jsonl.lock`) stay on disk after a run; they are empty and harmless.

**A lab pipeline found running.** Every stage starts and ends with a check that no RAG_LAB
pipeline is anything but `STOPPED` (PLAN.md 2.5). If one is, `run_all.sh` connects as RAG_LAB (its
owner) and stops it: a plain stop first, `force => true` only when it still is not `STOPPED`, and
only names that start with `RAG_`; anything else is reported and left alone. Each result is logged
(name, how it was stopped, status now) and the run then stops with `HALTED for investigation`.
Something ran that should not have: find out what started it before re-running.

## Stop conditions built in

- A RAG_LAB pipeline is anything but `STOPPED` at the start or end of any stage (PLAN.md 2.5): it
  is stopped first, then the run halts (above).
- `RAG_LAB_PWD`, from `rag_lab.env` or the environment, is not 12 to 64 letters and digits.
- `LAB_TMP_DIR` is not a plain directory of this user's closed to others.
- Another lab script holds `RESULTS_DIR/.lab.lock`.
- MODELS: the staged model file's sha256 inside the container differs from its pin.
- No `P0 PASS` in `results/probes/p0.status` (MODELS, BUILD).
- SQL*Plus exits non-zero, or its output shows `SP2-`, `ORA-01017` or "not connected".
- A step's output holds a password or an 8-character piece of one, an OCID or the compartment: it
  is shredded, never shown. Raw output sits in `<log>.part` until that scan; a `.part` left by a
  killed step is shredded when the next run starts.
- A staged script is not readable inside the container (checked before anything is sent).
- `DOCKER_HOST` or another `DOCKER_CONTEXT` is set, the current docker context is not `default`, or
  the container is not `/ora26ai`.
- 07 prints no `RESULT` line, or not every corpus file reached the vector table.
- The corpus directory holds anything other than the manifest's files, or a staged file changed.
- The `PAUSE` file exists (exit code 3). Remove it and re-run to resume.
- A chunk or character count differs from the recorded build before an evaluation.

## Guards

- Every drop, update, stop or profile replacement in 07, 08 and 99 first checks that the session
  user is `RAG_LAB` and that the object name starts with `RAG_`. Index and profile names are
  derived from validated arguments and pass `DBMS_ASSERT` before any dynamic SQL.
- The existing HR app's directory is only read, by 00b for the path check.
- Every file write goes through one guard in `01_stage_files.sh` (99b uses the same code): the
  container path must be under `/opt/oracle/kb/rag_lab`, must not overlap the existing HR app's corpus path,
  and in mount mode the host path must be the mount source plus the same suffix with no symlink.
- Passwords never appear as arguments: SQL*Plus gets them on stdin (the `connect` line, or the
  answer to 00's hidden `accept`), the python steps the path of a 0600 file (`RAG_LAB_PWD_FILE`).
  `whenever oserror exit failure` precedes every `@`, and `docker exec ora26ai test -r` checks the
  script first, so a missing script never lets SQL*Plus read the password line as a command.
  00 turns termout off around its two `identified by` lines. Each step's output goes to its log
  first and reaches the terminal only when no password, or 8-character piece of one, is in it.
- `03_load_models.sql` never replaces a loaded model: a size mismatch stops it. Equal size does not
  prove identity; the file's pinned sha256 (checked by 01) and the P2 parity gate do. Nothing in
  this directory removes a model.
- `07_build_index.sql` runs create, wait and stop in one PL/SQL block (step 2/4). Its handler
  stops the index's own pipeline on any error, a cancel included, and then lets the error go on.
  The pipeline is found from the index's `pipeline_name` attribute, then `<index>$VECPIPELINE`,
  then the one `<index>$...` pipeline. Right after the create, its name is printed as a
  `PIPELINE|` line and committed as a row of `RAG_BUILD_LOG`. If the create itself raised, the
  handler looks for its pipeline for 30 seconds more. If it still finds none, it prints
  `PIPELINE|...|event=error_unresolved` and says a pipeline may still be running. Step 3/4 checks
  the pipeline is `STOPPED` (stopping it again if a refresh restarted it) under the same kind of
  handler.
- `07_build_index.sql` counts a load as complete only when the pipeline's status table has rows
  and none PENDING or RUNNING (or, if that table is unreadable, its latest history run ended with
  success), every file is in the vector table, and the counts held still over 3 polls (4 for M2
  and M6, 6 for M3 to M5); the 'existing' path uses the same test. It then stops the pipeline with
  `force => false`; `force => true` is kept for the timeout and failure paths.
- `07_build_index.sql` records a build only when its pipeline status is exactly `STOPPED` after
  the stop.
- `07_build_index.sql` stops when a RAG profile or index of the same name exists with other
  settings. The one deliberate drop is the identical rebuild (config ids ending in `r`, or
  `REBUILD_CONFIGS`), which the contract requires under the same index name. Step 1/4 checks
  every input, the timeout included, then drops the old lab index FIRST and says so in the log;
  if step 2/4 then fails, no index of that name exists until a re-run of the same config id
  builds it. Lab indexes are disposable; this is by design.
- `02_create_credential.py` keeps an existing credential; `OCI_CRED_REPLACE=1` (or `--replace`)
  drops and recreates it.

## Staging method

`01_stage_files.sh` writes through the host directory behind the container's bind mount when a
writable bind mount covers `/opt/oracle/kb/rag_lab` (found with `docker inspect`, read-only).
Otherwise it uses `docker cp`, straight from the verified source file (no copy under `/tmp`; the
source must be world-readable and not world-writable, since docker cp keeps its mode); those
files are root-owned and read-only for the database, and
`99b_cleanup_files.sh` then removes the tree with `docker exec -u 0 ... rm -rf` on that one
constant path. In that mode `realpath` inside the container is checked before every write or
delete: no symlink inside the lab tree, and no overlap with the existing HR app's path resolved the same way. `STAGE_METHOD=mount` or `docker-cp` forces a method, and
`01_stage_files.sh where` shows which one applies.

## Running a script by hand

Inside the container, from `/opt/oracle/kb/rag_lab/sql` after `01_stage_files.sh sql`:

```sql
-- as RAG_LAB
define rag_build_timeout_min = 30
@07_build_index.sql 1 M0 1024 128 COS 5 0
@08_set_query_knobs.sql RAG_M0_C1024_O128_COS 8 0
```

`07` needs `rag_build_timeout_min` defined first. run_all sets 30 minutes for M0, M1, M6 and M1Q,
90 for M2 and 180 for M3 to M5 (`BUILD_TIMEOUT_MIN` overrides). `03` needs the four `rag_cap_*`
defines and `rag_model_sha256` (see its header); `na` makes it read the capacity figures itself,
and it stops when it cannot. `rag_model_sha256` is the pin run_all has just checked inside the
container; 03 only prints it (`na` by hand). A define that is missing is prompted once.

**Quoting risk in hand runs.** SQL*Plus splices `&1`, `&2` ... into the script text before any
check in the script runs. An argument holding a single quote (`@07_build_index.sql 1' ...`,
`@08_set_query_knobs.sql RAG_X'...`) would close a literal early and could run its own PL/SQL as
the connected user, past `assert_lab`. So every script first defines its arguments and accepted
values, then runs one guard query that holds each value only inside a `q'{...}'` literal and
checks it against `[A-Za-z0-9_.:-]`, written out character by character so no NLS setting can
widen it. `04_probes.sql` does the same for its probe name with letters, digits and `_` only
(P0 runs as SYSDBA). A value from that set holds no brace and no quote, so it cannot end the literal. Output
is off while the guard runs, so a rejected value (a compartment OCID, say) is never echoed. The
block after it stops the script with ORA-20000 unless the verdict is exactly YES, before any
PL/SQL or DDL sees a value. The password 00 reads at its hidden prompt must be 12 to 64 letters
and digits and is checked the same way (ORA-20005), with output off. That check's cursor is then
purged from the shared pool, where the literal would otherwise stay until it aged out; a DBA
account without EXECUTE on DBMS_SHARED_POOL gets a note instead.

By hand, pass only values from that set, and only 12 to 64 letters and digits as the password.
Never paste an argument you have not read. The guard is there to catch accidents. It does not
make a hostile value safe for someone who already holds the connected account. `run_all.sh`
also validates every argument before SQL*Plus sees it.

## The P2 parity gate

BUILD blocks a row whose `model_key` is not M0 until the P2 probe has written a pass for that
model to `RESULTS_DIR/model_parity.csv` (`model_key,status,min_cosine,utc`; `pass` or anything
else; one row per check, the last row for a key deciding). A blocked row does not stop the run.
M0 is Oracle's prebuilt model that the existing HR app already uses, and is exempt.
`SKIP_PARITY_GATE_KEYS=M1,...` builds on the named keys without a pass; the run log records that
the operator chose it. The old global `SKIP_PARITY_GATE` is refused. The size check in 03 does not
prove that a model is the one that was converted; the pinned sha256 (checked by 01 before
staging) and P2 do.

## The evaluation calls (checked against the harness's argparse, v1.1)

- `eval/eval_retrieval.py --config <id> --experiments <csv> --out <results dir> --stamp <UTC> --pause-file <PAUSE>`
- `eval/eval_rag.py --configs <id,id,...> --experiments <csv> --runs <n> --out <results dir> --stamp <UTC> --pause-file <PAUSE>`

Both take the password from `RAG_LAB_PWD_FILE` (run_all passes its per-run file), else
`RAG_LAB_PWD`, else a hidden prompt, and the DSN from `RAG_LAB_DSN`. Exit 0 is done, exit 3 is a
pause or an infra-error stop (run_all exits 3 and keeps the stamp), anything else stops the run.
`eval_rag.py` resumes a stamp from its `rag_<stamp>_r<run>.jsonl` files. A line that a kill cut off
is moved to `<file>.torn` and cut from the file (under the stamp lock, before anything is
appended), and its call is made again; a complete line that does not parse stops the run (exit 2)
for a look before any call.

## Accepted residuals

Recorded by the reviews and accepted; they are not open defects.

- **A `}'` in a hand-run argument can break out of the guard.** The guard holds each value in a
  `q'{...}'` literal. A value containing `}'` ends that literal early inside the guard SELECT and
  can force the verdict to YES. Only someone already connected as that account can type such a
  value, so it gains nothing beyond that account's own rights. `run_all.sh` (every argument, and
  `RAG_LAB_PWD` whether it comes from `rag_lab.env` or the environment) checks
  every value before SQL*Plus sees it, so the scripted paths never send one. Removing the
  splice altogether (SQL*Plus `VARIABLE` with binds only, and 00's DDL built from a bind) would
  change the scripts' contract and is not verified on the container's SQL*Plus; that is the
  operator's call.
- **00's password check sends the password as a literal.** The guard SELECT that checks the
  RAG_LAB password (run as SYS) holds it in a `q'{...}'` literal. 00 purges that cursor from the
  shared pool right after (`DBMS_SHARED_POOL.PURGE`; it prints how many it purged, 0 meaning the
  cursor ages out instead). A unified audit policy that records the statement text of SYS's
  SELECTs would still capture it. The default policies do not; if unsure, look at
  `AUDIT_UNIFIED_ENABLED_POLICIES` before ADMIN.

## Not verified yet (the probes settle these)

- That SQL*Plus answers 00's `accept ... hide` from piped stdin inside `docker exec -i`. If it does
  not, 00 fails at CREATE USER and nothing is changed.
- The pipeline status table named in `USER_CLOUD_PIPELINES.STATUS_TABLE` (columns `name`, `status`;
  `PENDING`/`RUNNING`/`FAILED` words) and `USER_CLOUD_PIPELINE_HISTORY` (`start_date`, `end_date`,
  `status`). 07 needs one of the two to prove a load finished and stops the pipeline and itself
  when neither can be read (P6 shows which). The pipeline status word `STOPPED` is assumed.
- That `whenever oserror` catches a script SQL*Plus cannot open (the `test -r` check comes first
  either way).
- That `MODEL_SIZE` equals the ONNX file size for the converted models, as it does for MiniLM.
- That `CREATE_VECTOR_INDEX` accepts `vector_dimension` for a directory location on 23.26.1 (P9).
- Which privileges a file-backed index needs beyond READ (P6).
- That `CREATE_VECTOR_INDEX` names the pipeline `<index>$VECPIPELINE` when the `pipeline_name`
  attribute is empty (07 tries the attribute first, then that name, then the one `<index>$...`).
- That `DBA_TAB_PRIVS` lists privileges on mining models, for 99's revokes, whose `left:` lines show
  what remains.
- That SQL*Plus sends 00's `/*rag_lab_secret_guard*/` comment to the server, so the purge finds
  the check's cursor; 00 prints how many it purged (0 means it ages out instead).
- That `REMOVE_HOST_ACE` takes one privilege per call, as 99 has done since v1.0 (Oracle documents
  it as removing privileges from the matching ACEs). In 99 it is the first change of phase admin,
  so if it fails nothing else has changed.
- What else a Select AI index or pipeline leaves in RAG_LAB after phase lab (job, log or status
  tables under other names). Phase admin lists any such object and stops rather than guess.

## Screenshot transcripts

The lab captured the blog's screenshot transcripts with `10_capture_shots.py`. That script is
lab-internal and is not shipped here.

## Tests

The lab's unit tests (`test_lab_scripts.py`, `tests/`) are lab-internal and not shipped here.
The shell scripts can still be linted:

```bash
shellcheck -x 01_stage_files.sh 99b_cleanup_files.sh run_all.sh
```
