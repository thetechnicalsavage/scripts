# ONNX embeddings and AI Vector Search

The embedding model is a database object. `VECTOR_EMBEDDING(MY_MODEL USING txt AS data)`
is a SQL expression that runs inside the database, as part of the SQL statement that
calls it, so the text never leaves, there is no key to rotate, and the vector can sit
beside the row it describes.

There are **two patterns**, for two genuinely different shapes of problem. Picking the
wrong one produces an application nobody wants to use.

| | A, managed index | B, vector column |
|---|---|---|
| Built with | `DBMS_CLOUD_AI.CREATE_VECTOR_INDEX` | `CREATE TABLE ... VECTOR(384, FLOAT32)` |
| Source of text | a directory of files | a query over your own tables |
| Chunking | done for you | yours, if you need it |
| Refresh | a pipeline on an interval | whenever your data changes |
| Storage | `<INDEX>$VECTAB` | a column on your own row |
| Queried by | Select AI RAG, through a profile | your own `VECTOR_DISTANCE` SQL |
| Filtering | `similarity_threshold`, `match_limit` | **any `WHERE` clause** |
| Right when | someone uploads documents | the text comes from data you already own |

**The decision rule: if there is no file, there is no reason for a file-management
screen.** And the consequence people miss is at query time: pattern B can rank on
distance and filter on `line_type` or price in the same statement. A document index
cannot.

## The scripts

| | |
|---|---|
| [`00_check.sql`](00_check.sql) | **READ-ONLY.** Is the model there, how many dimensions, what indexes exist |
| [`01_grants.sql`](01_grants.sql) | `SELECT ON MINING MODEL` for a model in another schema, and the optional `DBMS_VECTOR` grants |
| [`02_load_model.sql`](02_load_model.sql) | Load Oracle's prebuilt `all_MiniLM_L12_v2`, the way Oracle documents it |
| [`03_vector_column.sql`](03_vector_column.sql) | Pattern B: the table and the embedding `UPDATE` |
| [`04_search_with_fallback.sql`](04_search_with_fallback.sql) | Approximate search with an exact fallback |
| [`05_index_admin.sql`](05_index_admin.sql) | Pattern A: whitelist the index name, chunks per document |
| [`06_refresh_without_hanging.sql`](06_refresh_without_hanging.sql) | Refresh as a job, not in the page process |

## Three things that bit us on pattern A

**The pipeline only adds.** Deleting a file left its chunks in the vector store, so a
document withdrawn for a reason kept answering questions. Deletion has to purge the
matching chunks, and something has to sweep chunk groups whose source file is gone.

**Refreshing hangs the page.** `RUN_PIPELINE_ONCE` needs the pipeline stopped, else
`ORA-20044`, and runs in the foreground for about 50 seconds. Submit it as a job.

**`PLS-00231`.** A package-private function cannot be called from inside a SQL statement,
so dictionary lookups get resolved into locals first.

## Loading the model

**The model on the instance measured is Oracle's prebuilt augmented `all_MiniLM_L12_v2`.**
Its `MODEL_SIZE` is 133,322,334 bytes, the same as the `.onnx` file in Oracle's zip.
The zip comes from Oracle's
[Import Pretrained Models in ONNX Format](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/import-pretrained-models-onnx-format-vector-generation-database.html)
page. [`02_load_model.sql`](02_load_model.sql) is Oracle's documented load procedure,
written out.

## Prerequisites

Pattern B uses only the SQL functions `VECTOR_EMBEDDING` and `VECTOR_DISTANCE`, so it
needs no package grant. If the model lives in another schema, the caller needs
`SELECT` on it (`GRANT SELECT ON MINING MODEL`). A mining model has no `EXECUTE`
privilege. On the instance measured, the application schema held only that grant on
the model and embedded all 33 rows of its table with it. Pattern A additionally needs
`DBMS_CLOUD` enabled, which on a non-Autonomous database is
[five separate prerequisites](../dbms-cloud-on-prem/).

Written against Oracle AI Database 26ai Enterprise Edition in a container, measured
2026-09-21. Model `ALL_MINILM_L12_V2`, 384 dimensions. Model size and grants re-checked
2026-09-27.
