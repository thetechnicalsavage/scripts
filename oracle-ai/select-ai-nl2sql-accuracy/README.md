# Select AI NL2SQL accuracy, one practice at a time

The scripts behind the post
[Select AI NL2SQL accuracy on 26ai: 24% to 97%, one practice at a time](https://thetechnicalsavage.com/blog/select-ai-nl2sql-accuracy-26ai/).

Oracle's post *Best practices to improve NL2SQL accuracy with Oracle Select AI* explains the
practices. This measures them: each one applied on its own to a schema built to be hard, and
scored against 22 business questions with checked answers, three runs per stage.

| Stage | Correct per run (of 22) | Mean |
|---|---|---|
| Baseline: whole schema, no metadata | 5, 6, 5 | 24% |
| + table and column comments | 17, 16, 17 | 76% |
| + annotations | 19, 20, 18 | 86% |
| + primary and foreign keys | 19, 20, 18 | 86% |
| + curated object list, enforced | 19, 19, 19 | 86% |
| + views offered next to the base tables | 13, 13, 13 | 59% |
| views replace the tables they cover | 19, 19, 19 | 86% |
| + the vocabulary moved onto the views | 18, 20, 19 | 86% |
| + feedback, 5 corrections | 22, 21, 21 | 97% |
| Automated object selection, no feedback | 15, 16, 15 | 70% |

The same questions reworded, never given feedback themselves: 80% before feedback, 92% after.

## The scripts

| Step | Script | Run as |
|---|---|---|
| 0 | [`code/00_admin_prereqs.sql`](code/00_admin_prereqs.sql) `<lab_password> <genai_host>` | SYSDBA or DBA |
| 0b | [`code/00b_grant_embedding_model.sql`](code/00b_grant_embedding_model.sql) `<model_owner> <model_name>` | SYSDBA or DBA |
| 1 | [`code/01_lab_schema.sql`](code/01_lab_schema.sql) | NL2SQL_LAB |
| 2 | [`code/02_create_credential.py`](code/02_create_credential.py), reads `~/.oci/config`, binds every value, prints none | any client |
| 3 | [`code/03_profile_baseline.sql`](code/03_profile_baseline.sql) `<compartment_ocid> <region>` | NL2SQL_LAB |
| 4-10 | [`04_comments.sql`](code/04_comments.sql), [`05_annotations.sql`](code/05_annotations.sql), [`06_constraints.sql`](code/06_constraints.sql), [`07_object_list.sql`](code/07_object_list.sql), [`08_views.sql`](code/08_views.sql), [`09_views_replace_tables.sql`](code/09_views_replace_tables.sql), [`10_view_vocabulary.sql`](code/10_view_vocabulary.sql) | NL2SQL_LAB |
| 11 | [`code/11_feedback.sql`](code/11_feedback.sql) `<model_owner> <model_name>` | NL2SQL_LAB |
| 12 | [`code/12_automated_object_list.sql`](code/12_automated_object_list.sql) `<compartment_ocid> <region> <model_owner> <model_name>` | NL2SQL_LAB |
| score | [`code/eval/eval_nl2sql.py`](code/eval/eval_nl2sql.py) `--profile ... --stage ... --runs 3 [--set paraphrase]` | any client |
| re-grade | [`code/eval/rescore.py`](code/eval/rescore.py), then [`code/eval/sensitivity.py`](code/eval/sensitivity.py) | any client |
| clean up | [`code/99_cleanup.sql`](code/99_cleanup.sql) `<genai_host>` | SYSDBA or DBA |

Every script's header gives its account, arguments and re-run behaviour.
[`code/eval/questions.json`](code/eval/questions.json) holds the 22 questions, their gold SQL
and their reworded twins. [`code/tools/recreate_profile.sql`](code/tools/recreate_profile.sql)
is the fix for the `SELECT AI` translation cache; `purge_select_ai_cache.sql` is kept because it
does **not** fix it.

## One command

```bash
cd code
export LAB_DSN='<host>:1521/<pdb>'           # ADMIN_CONNECT and LAB_PASSWORD are prompted for if not set
export OCI_COMPARTMENT='<compartment ocid>'
export GENAI_HOST='inference.generativeai.<region>.oci.oraclecloud.com'
export EMBED_OWNER='<model owner>' EMBED_MODEL='<model name>'
./run_all.sh            # RUNS=3 by default; results in ../results/summary.csv
```

About 800 model calls and roughly an hour with `RUNS=3`, most of it waiting on the model.

## Prerequisites

- A non-Autonomous 26ai database with `DBMS_CLOUD` and Select AI installed. On-premises it is
  not installed by default: see [`../dbms-cloud-on-prem/`](../dbms-cloud-on-prem/).
- An OCI account with Generative AI access and an API signing key in `~/.oci/config`.
- For feedback and automated object selection, an in-database ONNX embedding model: see
  [`../onnx-embeddings-vector-search/`](../onnx-embeddings-vector-search/).
- `sqlplus` or SQLcl, and `python3` with `python-oracledb`.

## Results

[`results/`](results/) holds every generated SQL, verdict and timing (`runs.jsonl`), the scores
per stage and run as scored at the time (`summary.csv`) and after the uniform re-grade
(`rescored.csv`), the stricter scoring rules (`sensitivity.csv`), the `case_sensitive_values`
A/B (`experiments/`) and the clean-slate proof run (`verify_run/`).

Written against Oracle AI Database 26ai Enterprise Edition 23.26.1.0.0, on-premises in a
container, provider `oci`, model `xai.grok-4.20-non-reasoning`, `temperature` 0, September 2026.
Not a benchmark: another schema, question set or model gives other numbers. Not tested on
Autonomous Database.
