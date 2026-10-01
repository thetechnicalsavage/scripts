# Select AI RAG tuning, one setting at a time, in English and Arabic

The scripts behind two posts:
[Select AI RAG tuning on 26ai: 40% to 75%, one setting at a time](https://thetechnicalsavage.com/blog/select-ai-rag-tuning-26ai/)
and
[Select AI RAG in Arabic on 26ai: what breaks before the model answers](https://thetechnicalsavage.com/blog/select-ai-rag-arabic-26ai/).

Select AI RAG on a non-Autonomous 26ai, over a fictional bilingual HR corpus, with seven embedding
models loaded into the database as ONNX. Settings were chosen on a dev split by rules written in
advance; five claims were tested once on a test split, with Holm correction.

Meridian Systems and MyMeridian are invented for this lab; they have no connection to any real
company or product of that name.

## Results

Answer accuracy on the 56 answerable test questions, three runs each:

| Configuration | Embedding model, chunk/overlap | k / threshold | Correct per run (of 56) | Mean | Majority of 3 |
|---|---|---|---|---|---|
| Oracle's default index settings | all-MiniLM-L12-v2, 1024/128 | 5 / 0 | 22, 23, 22 | 40% | 23 |
| An existing HR app's settings | all-MiniLM-L12-v2, 2000/300 | 6 / 0.3 | 22, 23, 21 | 39% | 23 |
| Swap the model | multilingual-e5-small, 1024/128 | 5 / 0 | 32, 30, 31 | 55% | 31 |
| Swap the model | bge-m3, 1024/128 | 5 / 0 | 41, 43, 42 | 75% | 41 |
| Swap the model | Arabic-Triplet-Matryoshka-V2, 1024/128 | 5 / 0 | 25, 24, 24 | 43% | 25 |
| Tuned by the rules | arctic-embed-l-v2.0, 1024/128 | 10 / 0.59 | 21, 20, 19 | 36% | 20 |

| Claim, declared in advance | Result | p | Holm p |
|---|---|---|---|
| H1 MiniLM finds Arabic evidence less often than English, same 16 facts | 3/16 against 12/16 | 0.003906 | 0.015624 |
| H2 arctic-embed-l-v2.0 beats MiniLM on Arabic evidence | 15/16 against 3/16 | 0.000488 | 0.00244 |
| H3 the rule-tuned configuration beats the defaults on answers | 20/56 against 23/56 | 0.607239 | 1.0 |
| H4 the HR app's settings against the defaults, evidence found | 22/56 each | 1.0 | 1.0 |
| H5 the chunk_size winner against 1024 | dev kept 1024: nothing to test | 1.0 | 1.0 |

Everything outside H1 to H5 is exploratory, bge-m3's 75% included.

## The scripts

| Stage | Script | Run as |
|---|---|---|
| directories, user, grants, ACE | [`code/00_admin_prereqs.sql`](code/00_admin_prereqs.sql), [`code/00b_directories.sql`](code/00b_directories.sql) | SYSDBA |
| stage files | [`code/01_stage_files.sh`](code/01_stage_files.sh) | host OS user |
| OCI credential | [`code/02_create_credential.py`](code/02_create_credential.py), reads `~/.oci/config`, binds every value, prints none | host OS user |
| load the ONNX models | [`code/03_load_models.sql`](code/03_load_models.sql) | the model-owner schema |
| probes P0 to P17 | [`code/04_probes.sql`](code/04_probes.sql), [`code/04_probes.py`](code/04_probes.py), [`code/PROBES.md`](code/PROBES.md) | RAG_LAB |
| extraction gate | [`code/05_extraction_gate.sql`](code/05_extraction_gate.sql) | RAG_LAB |
| index-side profiles | [`code/06_profiles.sql`](code/06_profiles.sql) | RAG_LAB |
| one vector index and its RAG profile | [`code/07_build_index.sql`](code/07_build_index.sql) | RAG_LAB |
| `match_limit`, `similarity_threshold` | [`code/08_set_query_knobs.sql`](code/08_set_query_knobs.sql) | RAG_LAB |
| pipeline recovery after a bad file name | [`code/p13b_pipeline_recovery.sql`](code/p13b_pipeline_recovery.sql) | RAG_LAB |
| clean up (models stay) | [`code/99_cleanup.sql`](code/99_cleanup.sql), [`code/99b_cleanup_files.sh`](code/99b_cleanup_files.sh) | RAG_LAB, then SYSDBA |
| retrieval and answer harness | [`code/eval/`](code/eval/): `eval_retrieval.py`, `eval_rag.py`, `eval_norm.py` (the scorer), `chunk_coverage.py` | host OS user |
| dev rules and statistics | [`code/eval/decide_dev.py`](code/eval/decide_dev.py), [`code/eval/stats.py`](code/eval/stats.py) | host OS user |
| gold set | [`code/eval/questions.json`](code/eval/questions.json) v2.4: 108 questions plus 8 dialect paraphrases | |
| model conversion and parity | [`code/models/`](code/models/) | build host |
| corpus, charts, tables | [`code/tools/`](code/tools/) | build host |

[`code/run_all.sh`](code/run_all.sh) runs the stages in order, resumable from
[`code/experiments.csv`](code/experiments.csv); [`code/README_RUN.md`](code/README_RUN.md) is the
run book. `DRY_RUN=1` prints the plan without touching anything:

```bash
cd code
export GENAI_HOST='inference.generativeai.<region>.oci.oraclecloud.com'
export OCI_COMPARTMENT='<compartment ocid>'
DRY_RUN=1 STAGES=CORPUS,ADMIN,CRED,MODELS,PROFILES,BUILD,EVAL_RET,EVAL_RAG ./run_all.sh   # the plan only
# then GO-1 to GO-3 in README_RUN.md, which ends with the answer layer:
STAGES=EVAL_RAG RUNS=3 ./run_all.sh    # 6 configs x 108 questions x 3 runs
```

## Prerequisites

- A non-Autonomous 26ai database with `DBMS_CLOUD` and Select AI installed. On-premises it is not
  installed by default: see [`../dbms-cloud-on-prem/`](../dbms-cloud-on-prem/).
- An OCI account with Generative AI access and an API signing key in `~/.oci/config`.
- The ONNX models: all-MiniLM-L12-v2 and multilingual-e5-small as Oracle ships them; the others
  converted with [`code/models/`](code/models/). bge-m3 and snowflake-arctic-embed-l-v2.0 are not
  on Oracle's list of available embedding models; validate them as your own.
- `sqlplus` with `NLS_LANG=AMERICAN_AMERICA.AL32UTF8`, and `python3` with `python-oracledb`.

## The corpus

[`corpus/src/`](corpus/src/) holds the 84 source texts: 36 Gulf policies (12 English and Arabic
twin pairs, 6 Arabic-only, 6 English-only) and 48 India policies. The Arabic was written by an LLM
in Modern Standard Arabic and has not been reviewed by a native speaker.

- [`code/tools/build_corpus.py`](code/tools/build_corpus.py) renders the Gulf set to PDF and DOCX.
  The lab staged the Gulf set as DOCX, because the Arabic PDFs failed the extraction gate.
- The lab staged the India set as the existing HR app's own PDFs, which are not shipped. Render
  `corpus/src/india/` yourself; your chunks will differ from the lab's.
- One line in each of three India policies (HRP-010, HRP-014, HRP-023) was replaced with
  `[line removed for publication]`. None of them holds a gold fact.
  [`code/models/parity_passages.json`](code/models/parity_passages.json) was rebuilt from this
  published corpus with `verify_models.py passages`, so 3 of its 50 passages differ from the file
  the lab's parity numbers were measured on.

## Results files

[`results/`](results/) holds the rules as written before the dev results were read
(`dev_rules_v1.0.md`, `dev_rules.md`), what they decided (`dev_decisions_*.json`), the answer
configurations (`go3_configs.md`), every GO-3 answer with its verdict and status
(`rag_20260930T023755Z_r{1,2,3}.jsonl`), the statistics (`stats_answers_*.json`,
`stats_family_*.json`), the flattened rows the charts are drawn from (`charts_input/`), the
one-run dev threshold sweeps (`supp_v24/s6/`), and a blind second rating of 496 answers by a model
(`model_adjudication.jsonl`), which is not a human audit.

Not shipped: the per-index retrieval and chunk-coverage files (about 67 MB; `stats.py` re-runs and
the H1, H2, H4 and H5 rows need them), the unit tests, the screenshot tooling, and the lab-internal
snapshot of the existing HR app. `experiments.csv` is the plan as written, every row `planned`.

Written against Oracle AI Database 26ai Enterprise Edition 23.26.1.0.0 <!-- sanitize-ok: version -->, on-premises in a container,
provider `oci`, chat model `xai.grok-4.20-non-reasoning`, `temperature` 0, 29 and 30 September
2026. Not a benchmark: one synthetic corpus, one chat model, one build. Not tested on Autonomous
Database.
