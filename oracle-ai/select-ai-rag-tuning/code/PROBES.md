<!-- v1.2 2026-10-01  Runbook for the GO-1 probes of brief 09 (PLAN.md v1.2 section 8.1).
     v1.2: public copy: the other app is named the existing HR app; the lab-internal 00c snapshot,
           tools/capture.py, tools/leak_scan.py and tests/ are not shipped (noted where used).
     v1.1: P0 runs first and writes results/probes/p0.status (the MODELS gate), with the mapping of the
           29-Sep read-only run; P2 (DB-vs-local parity per model, results/model_parity.csv) right after
           MODELS; P8 and the P6/P10/P14 builds leave no pipeline running; exit code 5.
     v1.0: first version. Written and unit-tested only; nothing here has run against a database.
           It runs after the Codex adversarial review of the scripts (PLAN.md step A7). -->

# Brief 09 probes P0 to P17: runbook

The probes answer the questions the experiment matrix depends on before any measured build: is
the database safe to load about 2.8 GB of models into, what does each model actually read, which file
format survives the database's own text extraction, what a file-backed index needs, and which
profile embeds the question. Each probe prints a transcript-ready, leak-safe result and a
`VERDICT` line; the decision rules are below.

| File | What it holds |
|---|---|
| `04_probes.sql` | SQL*Plus probes P0, P1, P1A, P3, P4B, P6, P9, P10, P12, P13, P14 (builds p4), P15. One argument: the probe id |
| `04_probes.py` | Python probes p2, p4, p5, p7, p8, p11, p14, p16, p17, and the no-database helpers `files`, `stage-dir`, `p0-df`, `p0-status`, `make-noto-control` |
| `05_extraction_gate.sql` | P5, database side: `DBMS_VECTOR_CHAIN.UTL_TO_TEXT` on each probe file, then what p1 stored |
| `probe_files.txt` | The probe files, where each comes from, and the source text each is scored against |

## 1. Before the probes

GO-1 order (PLAN.md section 9, with P0 moved to the front):

1. **P0** and `04_probes.py p0-status` (section 1a). Nothing is loaded until `results/probes/p0.status`
   reads `P0 PASS <utc>`: run_all.sh MODELS is gated on it.
2. `01_stage_files.sh corpus`, `00`, `00b`, `02`.
3. **MODELS**: `03_load_models.sql` for M0 to M6 and M1Q.
4. **P2** for every model just loaded (section 1b). BUILD uses a model other than M0 only when
   `results/model_parity.csv` holds a `pass` as its last row for that key.
5. `06_profiles.sql M0` and `06_profiles.sql M1`, then the probes in section 3.

The probes need:

- `RAG_EMB_M0` (index profile of every probe index) and `RAG_EMB_M1` (P16 only);
- `ASKORACLE.ALL_MINILM_L12_V2` and `ASKORACLE.MULTILINGUAL_E5_SMALL` granted to RAG_LAB;
- the credential `RAG_LAB_OCI_CRED` (every probe that calls the LLM);
- the lab venv with python-oracledb, and a copy of the brief's `code/` and `corpus/` (`src`,
  `pdf`, `docx`, `probe`). When `corpus/` is elsewhere, pass `--corpus-dir DIR` to `04_probes.py`.

### 1a. P0 and `results/probes/p0.status`

`04_probes.sql P0` ends with one line, `P0 STATUS: PASS <utc>` or `P0 STATUS: FAIL <gates> <utc>`,
where the time is the database's UTC time. `p0-status` reads the saved transcript and writes
`results/probes/p0.status` (under `--results-dir`, default `results/`) as one line (the
`tools/capture.py` lines below are the lab's wrapper, not shipped: see section 2):

```
python3 tools/capture.py --allow-fail --out ../transcripts/07-p0-capacity.txt --sysdba sql 04_probes.sql P0
python3 04_probes.py p0-status --transcript ../transcripts/07-p0-capacity.txt
#   -> results/probes/p0.status:  P0 PASS 2026-09-29T11:02:03Z
#                             or  P0 FAIL askoracle-quota; P0 ran 2026-09-29T11:02:03Z   (exit 5)
```

`--allow-fail` keeps the transcript of a failed P0, so the FAIL is recorded too. A PASS is written only
from a transcript of exactly one P0 run with exactly one `P0 STATUS: PASS <utc>` line and every gate line
present and passing (character set, archive / FRA, ASKORACLE quota, room in ASKORACLE's tablespace and
in SYSAUX). Anything else is a FAIL: no STATUS line (P0 stopped early, or ran from `04_probes.sql` older
than v1.1), a missing or failing gate line, two runs in one file. Each run of `p0-status` rewrites the
file, so a later FAIL replaces an earlier PASS.

The hard gates (any one fails P0): character set not AL32UTF8; ARCHIVELOG with under 5 GB of
recovery-area headroom; ASKORACLE's quota on its default tablespace under 6 GB (unlimited passes);
under 6 GB of room (free plus autoextend) in that tablespace or in SYSAUX. 6 GB is twice the ~2.8 GB of
the seven lab models with M6 in FP32. PGA headroom under 2 GB stays a warning, and the host disk is
`p0-df`'s question.

**The 29-Sep run.** P0 already ran read-only on 29-Sep, before `04_probes.sql` v1.1, as the ad-hoc
query set `results/probes/p0_capacity_adhoc.sql`. Its transcript is `transcripts/p0-capacity.txt`
(AL32UTF8, NOARCHIVELOG, `pga_aggregate_limit` 19312M, USERS and SYSAUX autoextend to 32768 GB,
ASKORACLE unlimited on USERS; the operator reported 450 GB free on the host). That transcript has no
STATUS line, so `--adhoc` applies the same hard gates to its columns and takes the run time from
`--run-utc`, because the ad-hoc output prints none. The transcript file was saved at
2026-09-29T10:25:38Z, which is used as the run time:

```
python3 04_probes.py p0-status --transcript ../transcripts/p0-capacity.txt --adhoc --run-utc 2026-09-29T10:25:38Z
#   -> results/probes/p0.status:  P0 PASS 2026-09-29T10:25:38Z
```

A value the ad-hoc transcript does not show fails closed (for example ARCHIVELOG, whose recovery-area
headroom that query set does not print: then run `04_probes.sql P0`). Re-running P0 with v1.1 before
MODELS is still the better record, because it adds `max_string_size`, the recovery area and the
per-gate lines.

### 1b. P2 right after MODELS: the database embeds like the verified file

P2 settles that the model the database loaded behaves exactly like the file that passed the local gates.
The database exposes no hash of a loaded model, so identity by sha256 is checked before loading
(`01_stage_files.sh` pins the file) and P2 checks behaviour. For each of the
20 token-id strings of `models/verify_models.py`, the database embeds the string **one per call**
(`select vector_embedding(ASKORACLE.<MODEL> using :t as data) from dual`), and each vector is compared
with the local reference of the same ONNX file, also computed one string per call. One per call on both
sides matters: dynamic INT8 gives the same text a different vector inside a batch (cosine 0.9954 to
0.9988 for Oracle's own e5-small). A key passes when every string has cosine >= 0.9999.

References, on the build host with its onnxruntime 1.20.1 (the database's version):

```
~/rag-lab/venv/bin/python ~/rag-lab/code/verify_models.py refvec --model ~/rag-lab/converted/<file>.onnx --out ~/rag-lab/reports/<KEY>_refvec.json
#   M0: --model ~/rag-lab/oracle-prebuilt/all_MiniLM_L12_v2.onnx      (Oracle's file, as the existing HR app loaded it)
#   M1: --model ~/rag-lab/oracle-prebuilt/multilingual_e5_small.onnx  (Oracle's file)
#   M2..M6, M1Q: the file listed in converted/models.sha256
```

Copy `reports/<KEY>_refvec.json` to `results/models/` next to the parity reports. Then, as RAG_LAB on
the database host, once per loaded key:

```
RAG_PYTHON=~/rag-lab/venv/bin/python python3 tools/capture.py --allow-fail --out ../transcripts/p2-parity-M1.txt python 04_probes.py p2 --key M1
```

`p2` checks the reference file first (the key, the 20 strings against the sha256 pinned in
`04_probes.py`, one vector per string, one text per call), then appends `model_key,status,min_cosine,utc`
to `results/model_parity.csv`:

- `pass`: all 20 compared, minimum cosine >= 0.9999 (exit 0);
- `fail`: a lower cosine, or a vector of another dimension: not the verified file (exit 5);
- `error`: a string the database would not embed at all; no verdict, and the gate stays closed (exit 5).

The two 6,000-character strings are 6,000 and 11,021 bytes. Under `max_string_size = STANDARD` the
database refuses a VARCHAR2 input over 4,000 bytes, so each carries a reference for its 4,000-byte
prefix as well. When the full text is refused, the prefix is compared instead and the transcript
says so. The English 4,000-byte prefix is 866 XLM-R tokens (775 for M0's WordPiece; measured with
Oracle's tokenizer sections), longer than the 256- and 512-token caps, so truncation in the database is
exercised for M0 to M3 and M1Q either way. For M4 and M5 (1,024 tokens) it is exercised only when the
full texts are accepted (`max_string_size = EXTENDED`), which the transcript shows; M6's AraBERT count
was not measured. Re-running appends a row (under a lock file, `model_parity.csv.lock`); the last row for a key decides. A model that fails is not
used, and the post says why (PLAN.md 8.1).

### 1c. Probe files

**Probe files.** `01_stage_files.sh` is the only thing that writes into `RAG_PROBE_DIR`.
`04_probes.py stage-dir` only assembles a local directory for it:

```
# on the build machine, once (optional negative control; P5 reports it missing otherwise)
python3 code/04_probes.py make-noto-control          # writes corpus/probe/PRB-AR-NOTO-GLF-001-AR.pdf

# on the database host, before P5
python3 code/04_probes.py files                       # every row, size and sha256
python3 code/04_probes.py stage-dir --stage initial --dir "$PWD/probe-stage"
code/01_stage_files.sh probe "$PWD/probe-stage"
```

The directory must then hold exactly the five `stage=initial` files while P6, P10 and P14 build
p1 to p4, because every probe index reads `RAG_PROBE_DIR:*`.

## 2. How to run a probe

In the lab every probe went through `tools/capture.py`, which ran it, refused to save a transcript that
failed the leak scan, lost characters (`???`, U+00BF, U+FFFD) or echoed a password, and wrote it
behind a neutral `$ ` prompt. That wrapper is lab-internal and not shipped here: run the probe
directly (SQL*Plus connected to the PDB with `NLS_LANG=AMERICAN_AMERICA.AL32UTF8`, then
`@04_probes.sql <PROBE>`, or `python3 04_probes.py <probe>`) and save its output to the file the
`--out` below names. The lab's calls, from `code/`:

```
# SQL*Plus inside the container, file on stdin after "define 1 = <PROBE>"
python3 tools/capture.py --out ../transcripts/07-p0-capacity.txt --sysdba sql 04_probes.sql P0
python3 tools/capture.py --out ../transcripts/p1a-pairing.txt --user ASKORACLE --password-env APP_SCHEMA_PWD sql 04_probes.sql P1A
python3 tools/capture.py --out ../transcripts/19-p6-build-p1.txt sql 04_probes.sql P6

# python on the host; capture.py hands RAG_LAB_PWD to the probe in its environment only
RAG_PYTHON=~/rag-lab/venv/bin/python python3 tools/capture.py --out ../transcripts/15-p4-truncation.txt python 04_probes.py p4
```

A probe whose finding is a failure (P4B stop, P5 STOP) exits non-zero; add `--allow-fail` when
the transcript of that failure is the point. `04_probes.py` also keeps `results/probes/<probe>.txt`
and `<probe>.json` (redacted), and raw Select AI replies under `results/probes/<probe>/`.

## 3. The probes, in the order they run

Run as: **DBA** = `--sysdba`; **ASK** = ASKORACLE; **LAB** = RAG_LAB. LLM = GENERATE calls.

| # | Command | As | Settles | Stop or decision | LLM |
|---|---|---|---|---|---|
| P0 | `sql 04_probes.sql P0` (with `--allow-fail`), `python 04_probes.py p0-status --transcript <it>`, then `python 04_probes.py p0-df --path <host dir behind the oradata volume>`. **Before MODELS** (section 1a) | DBA | character set, `max_string_size`, PGA, USERS/SYSAUX room, ASKORACLE quota, log mode and recovery area, CPUs, host disk | **Stop** (exit non-zero, `P0 FAIL` in `p0.status`) when the character set is not AL32UTF8, ARCHIVELOG has under 5 GB of recovery-area headroom, or ASKORACLE's quota or the room in its tablespace or SYSAUX is under 6 GB: ask the operator. WARN under 2 GB PGA headroom (ask before M3 to M5) | 0 |
| P2 | `python 04_probes.py p2 --key <K>` for each key MODELS loaded (section 1b) | LAB | the database embeds the 20 token-id strings, one per call, like the verified local file (cosine >= 0.9999, against `results/models/<K>_refvec.json`) | a row in `results/model_parity.csv`; `fail` or `error`: the model is not used (BUILD refuses it), and the post says why | 0 |
| P1 | `sql 04_probes.sql P1` | DBA | names of the DBA-level cloud AI / vector index / pipeline views (for 99); pairing through them when they exist | report | 0 |
| P1A | `sql 04_probes.sql P1A` | ASK | read-only re-check: every ASKORACLE index pairs one embedding model on both sides (PLAN 0.4 says all three use `ALL_MINILM_L12_V2`) | a `MISMATCH` line changes the story-1 wording | 0 |
| P3 | `sql 04_probes.sql P3` | LAB | `VECTOR_NORM` per model, EN and AR; dims | not unit length: S7 becomes a real metric sweep | 0 |
| P4 | `python 04_probes.py p4` | LAB | effective window per model and language: the shortest word-boundary prefix whose embedding equals the whole sample's (binary search, about 12 calls per cell) | a result. `NOT MONOTONE` or `DEGENERATE` rows are not used | 0 |
| P5 | `sql 05_extraction_gate.sql` (add `--expect-arabic`), then `python 04_probes.py p5` | LAB | the database's own extraction of the Amiri Arabic PDF, the same document as DOCX, the English Chromium PDF, the India reportlab PDF and the Noto negative control | per file: word recall >= 0.95, 0 reversed numbers, 0 `?` not in the source, 0 U+FFFD/U+00BF, 0 added U+0640. Both PDFs pass: Gulf goes in as **PDF**; either fails: **DOCX**; the DOCX fails: **STOP** (exit 3), decide with the operator | 0 |
| P6 | `sql 04_probes.sql P6` | LAB | p1 = `RAG_PRB_P1` (M0, 1024/128, COSINE) built with READ only on the directory; its RAG profile `RAG_P_PRB_P1`; P12 timing | a privilege error stops it (exit non-zero): record it, grant the smallest privilege it names, re-run P6 | 0 |
| P9 | `sql 04_probes.sql P9` | LAB | what the build left: attributes (whitelisted values), load on create, pipeline, history, per-file status, `$VECTAB` columns and indexes (exact or approximate search) | informs 07 | 0 |
| P5 again | `sql 05_extraction_gate.sql`, `python 04_probes.py p5` | LAB | the same gate on the text p1 stored per file; a file passes only if both extractions pass | as P5 | 0 |
| P4B | `sql 04_probes.sql P4B` | LAB | stored embedding = `VECTOR_EMBEDDING(model USING content)` for 10 chunks (5 Arabic-file, 5 English-file) | **Stop** (exit non-zero) when any differs: find what is embedded before any harness number is used | 0 |
| P7 | `python 04_probes.py p7` | LAB | what `runsql`, `showprompt` and `narrate` return from a RAG profile (JSON rows and keys, whole chunks carried, Sources block) for one EN and one AR prompt | runsql rows: Select AI's ranking is cross-checked; none: harness-only | 6 |
| P8 | `python 04_probes.py p8` | LAB | `SELECT AI showprompt` vs `GENERATE showprompt`, same text, before and after `match_limit` 5 -> 2 (restored). p1's pipeline is stopped again after every `UPDATE_VECTOR_INDEX`, the restore included, even when a call fails | SELECT AI unchanged while GENERATE changes: cached; all measured calls stay on GENERATE | 4 |
| P10 | `sql 04_probes.sql P10` | LAB | p2 640/0 and p3 640/160 (25 %) accepted; x1 640/640 expected to be rejected (dropped again if accepted); longest chunk per index | adjust S4 to what is accepted | 0 |
| P11 | `python 04_probes.py p11` (after P10) | LAB | `GENERATE(..., attributes => ...)`: per-call `vector_index_name` (p2), `max_tokens`, `seed`; `seed` as a profile attribute on scratch `RAG_P_PRB_P1_SEED` (dropped) | per-call `vector_index_name` APPLIED: one RAG profile could serve all indexes; otherwise one per index. `seed` only if accepted | 8 |
| P14 | `sql 04_probes.sql P14`, then `python 04_probes.py p14` | LAB | p4 = `RAG_PRB_P4` (EUCLIDEAN); runsql SCORE against the harness cosine and L2 distances on p1 and p4; which formula fits | thresholds are calibrated in SCORE units with the fitted formula | 8 |
| P12 | `sql 04_probes.sql P12` | LAB | build time per chunk of p1 to p4, beside 07's S1 time | a build extrapolated past 45 min: cut builds 14 to 17 first | 0 |
| P16 | `python 04_probes.py p16` | LAB | A: RAG profile with M1 (`RAG_P_PRB_P1_XM1`, deliberately unpaired, dropped) over the M0 index; B: `UPDATE_VECTOR_INDEX profile_name` -> `RAG_EMB_M1`, then restored; retrieval compared with the harness top-k by M0 and by M1 | publish the trap; pairing stays enforced. Exit 2 if the restore failed; rebuild p1 if the stored vectors changed | 12 |
| P13 | `python 04_probes.py stage-dir --stage p13 --dir "$PWD/probe-p13"`, `01_stage_files.sh probe "$PWD/probe-p13" --allow-non-ascii`, then `sql 04_probes.sql P13` | LAB | an Arabic-named and an ASCII-named copy of the same PDF, p1's pipeline run once | SKIPPED / LOADED / INCONCLUSIVE (the ASCII copy is the control) | 0 |
| P15 | `01_stage_files.sh probe-rm PRB-P13-EN-COPY.pdf`, then `sql 04_probes.sql P15` | LAB | the deleted file's chunks after one more pipeline run | PERSIST / PURGED: a finding either way | 0 |
| P17 | `python 04_probes.py p17` | LAB | `chat` on `RAG_EMB_M0` (no vector index) over the 108 core gold questions, 3 runs, seeded order per run; scored by `eval/eval_norm.py` | a question with every fact produced in any run is FLAGGED: rewrite, log and re-seal before GO-2 | 324 |

Why this order: P4B, P7, P8, P9, P11, P14 and P16 need p1, so they follow P6. P13 and P15 change
the probe directory and re-run p1's pipeline, so they come after every probe that reads p1's
chunks. P16 changes p1's `profile_name` and restores it before P13 runs the pipeline. P17 is its
own step: about 324 calls at 2.7 to 6 s each is 15 to 35 minutes, it shares the OCI quota with
the existing HR app, and it resumes where it stopped (`results/probes/p17_chat.jsonl`). `code/PAUSE` stops it,
and so do 3 infra errors among the last 50 calls (above 5 % of a 50-call window; this can fire
before 50 calls exist, the same rule as `eval_rag.py`), both with exit 4.

Suggested transcript names follow PLAN.md section 6: 07 (P0), 11 and 12 (P5), 13 (P13), 14 (P3),
15 to 17 (P4), 18 (P4B), 19 (P6, P9, P10), 20 and 21 (P7), 22 (P14), 23 (P8), 24 (P15),
25 (P16), 26 (P17).

## 4. How each measurement works

- **P4 window.** The sample is the Gulf sources of one language in file-name order, as a reader
  sees them (`build_corpus.plain_text`), 12,000 characters by default. Once a prefix reaches the
  model's token cap, every longer prefix is cut to the same tokens, so "embedding equals the
  whole sample's" is monotone in the prefix length and a binary search finds the shortest such
  prefix. Two longer prefixes are re-checked (`monotone_ok`). `VECTOR_EMBEDDING` converts its
  input to VARCHAR2, so when the whole sample is refused the probe retries once with 32,000 and
  then 4,000 bytes and says so; a window equal to the whole sample is reported as a lower bound.
- **P5 recall** is `build_corpus.word_integrity`, the same normaliser and word definition as the
  local gate in `corpus/manifest.csv`. Reversed numbers and reversed Arabic words are source
  tokens missing from the extraction whose reversal is present (visual-order extraction). In
  printed samples `?` shows as `<?>` so the transcript gate can tell a finding from a lost
  character.
- **P14.** Each runsql row is matched to its chunk by text (letters and digits only, unique
  match required), then nine formulas (1 - cosine distance, 1/(1 + L2), 1 - L2^2/2, ...) are
  tested against every (SCORE, distance) pair; one fits when its largest error is under 1e-3.
  On unit vectors 1 - cosine distance and 1 - L2^2/2 are the same number, so both can fit.
- **P7, P8, P11, P16** find which chunks a prompt carries by looking for the first and the last
  80 key characters of every p1 chunk in the reply (JSON replies are decoded first).

## 5. Leak safety

- No probe selects a host name, `v$instance`, a file-system path of the database, an OCID or a
  region. Profile and index attributes print from a whitelist (never `credential_name`,
  `oci_compartment_id` or `region`); the RAG profiles copy those three from `RAG_EMB_M0` without
  printing them.
- Error text is redacted before it is printed (OCID, OCI URL and region, request id, IP, e-mail,
  `/home/<user>`), in SQL and in Python; Python also masks key fingerprints, PEM headers and
  12-hex ids, and host names from the git-ignored `~/.config/blog-leak-patterns.txt`.
  (The lab's unit tests ran its leak scan over redacted fixtures; tests and scanner are not shipped.)
- Passwords come from the environment or a hidden prompt, never an argument; `04_probes.py`
  removes `RAG_LAB_PWD` from its environment once read.

## 6. Coordination with the other scripts

- **07_build_index.sql** builds over `RAG_CORPUS_DIR` with `RAG_<KEY>_C.._O.._<M>` names only, so
  the probe indexes are built by `04_probes.sql` (P6, P10, P14) with the same conventions: index
  profile `RAG_EMB_M0`, drop and recreate limited to `RAG_PRB_*` / `RAG_P_PRB_*`, wait for the
  initial load, stop the pipeline (`force => true`, must end `STOPPED`), counts and times logged,
  and any error while the load is awaited (the status table and history are read in a shape 23.26.1
  has not confirmed) stops the pipeline first and is then raised again; when `pipeline_name` was not
  read yet, `<index>$VECPIPELINE` is stopped if it exists,
  RAG profile with the same `embedding_model` and the pairing guard read back from
  `USER_CLOUD_AI_PROFILE_ATTRIBUTES`.
- **01_stage_files.sh** is the only writer of the probe directory (`probe`, `probe-rm`).
- **eval/eval_norm.py** scores P17, so the control is judged exactly like the answer layer.
- The PLAN's P5 list also has an Amiri LibreOffice PDF. It is left out: the corpus PDFs are
  Chromium prints, the decision is PDF-as-built versus DOCX, and the local probe already showed
  LibreOffice extracts well.
- `RAG_LAB.RAG_PRB_LOG` (created by the prelude of `04_probes.sql`) keeps the figures P9 and P12
  read back. `99_cleanup.sql` removes it with the user.

## 7. Assumptions only the database can confirm

Each is handled so that a wrong assumption shows up as a printed finding, not a silent error:

1. `DBMS_CLOUD_AI.GENERATE` has an `attributes` parameter on 23.26.1 (ADB documents it). If not,
   P11 prints the PLS-00306 as REJECTED.
2. The runsql reply of a RAG profile is JSON with a score and the chunk text. If not, P7 says
   harness-only and P14 says "not observable".
3. A vector-index pipeline has a per-file status table (`USER_CLOUD_PIPELINES.STATUS_TABLE`). If
   not, the wait falls back to "every listed file is in `$VECTAB`, or the count held for 60 s".
4. `DBMS_CLOUD.LIST_FILES` works on a directory object on-prem (07 relies on it too). If not, P6
   prints the error and uses the 60-second rule.
5. `SELECT AI showprompt` is accepted from python-oracledb once `SET_PROFILE` ran. If not, P8
   prints the error and reports GENERATE alone.
6. `VECTOR_EMBEDDING` on a CLOB column converts it to VARCHAR2; P4B uses chunks of at most
   1,300 characters so that holds under `max_string_size = STANDARD` (P0 prints the setting).
7. The `$VECTAB` `ATTRIBUTES` carry `$.object_name` equal to the file name (the shared contract);
   P9 prints the column list, and P13 also counts names that start `PRB-P13-AR-` in case the
   Arabic name is stored altered.

## 8. Exit codes

`04_probes.sql`: non-zero on a hard gate (P0), a build that failed or timed out (P6, P10, P14),
a stored-vs-recomputed mismatch (P4B), a missing precondition (P9, P13, P15), or a wrong user or
client character set. `04_probes.py`: 0 done, 1 database error, 2 precondition or guard, 3 P5
STOP, 4 stopped by PAUSE or the infra-error window, 5 a gate failed and was recorded (`p2` wrote
`fail` or `error` to `model_parity.csv`, `p0-status` wrote `P0 FAIL`).
