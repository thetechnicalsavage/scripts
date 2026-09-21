# ONNX embeddings and AI Vector Search

The embedding model is a database object. `VECTOR_EMBEDDING(MY_MODEL USING txt AS data)`
is a SQL expression that runs in the same process as the query calling it, so the text
never leaves, there is no key to rotate, and the vector can sit beside the row it
describes.

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
screen.** And the consequence people miss is at query time — pattern B can rank on
distance and filter on `line_type` or price in the same statement. A document index
cannot.

## The scripts

| | |
|---|---|
| [`00_check.sql`](00_check.sql) | **READ-ONLY.** Is the model there, how many dimensions, what indexes exist |
| [`01_grants.sql`](01_grants.sql) | `DBMS_VECTOR` grants, and `EXECUTE` on the model |
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

## What is not here

**Loading the ONNX model.** The model on the instance these came from was loaded by hand
and the artefacts are gone, so the load procedure is not documented here rather than
guessed at. Everything in these scripts is the state of a database where it had already
been done.

## Prerequisites

Pattern B needs only `DBMS_VECTOR` and `DBMS_VECTOR_CHAIN` granted to the calling schema.
Pattern A additionally needs `DBMS_CLOUD` enabled, which on a non-Autonomous database is
[four separate prerequisites](../dbms-cloud-on-prem/).

Written against Oracle AI Database 26ai Enterprise Edition in a container, measured
2026-09-21. Model `ALL_MINILM_L12_V2`, 384 dimensions.
