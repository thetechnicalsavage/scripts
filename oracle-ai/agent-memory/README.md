# Oracle AI Agent Memory 26.8 on an on-premises 26ai

The scripts behind
[Oracle AI Agent Memory 26.8 on an on-premises 26ai, measured](https://thetechnicalsavage.com/blog/oracle-ai-agent-memory-26ai/).

The `oracleagentmemory` 26.8.0 SDK, run against Oracle AI Database 26ai Enterprise Edition 23.26.1 on
premises. Every memory store lives in one schema, `AGENTMEM`. Embeddings are computed in the database by
an ONNX model through a SELECT grant; memory and message text goes to an LLM on OCI Generative AI for
extraction, linking, summaries and pruning. All data is synthetic: a fictional mobile operator, its
customers, tickets and bills.

## Results, 10 and 12 October 2026

| Comparison (written down and frozen before any measured run) | Result |
|---|---|
| C1 search, identifier-only queries (n = 40) | KEYWORD put the right memory first 40 times, HYBRID 27, VECTOR 8 |
| C1 search, paraphrases without an identifier (n = 40) | HYBRID 12, VECTOR 12, KEYWORD 0 |
| C1 search, questions containing an identifier (n = 40) | HYBRID 11, VECTOR 4, KEYWORD 1 |
| C2 automatic linking | not measurable: our extraction setting left no gold link reachable; cost and harm only |
| C3 pruning, 40 queries | EXHAUSTIVE kept top-10 gold recall (0.558) with about 36% of the context, at 10 LLM calls per search |

The full tables, the tests and every run are in [`results/`](results/).

## The scripts

| Step | Script | Run as |
|---|---|---|
| owner schema `AGENTMEM`, its privileges and the model grant | [`code/sql/00_admin_prereqs.sql`](code/sql/00_admin_prereqs.sql), then [`00c`](code/sql/00c_grant_create_sequence.sql) (CREATE SEQUENCE) and [`00d`](code/sql/00d_grant_create_view.sql) (CREATE VIEW) | SYSDBA |
| runtime user for step 8 | [`code/sql/00b_admin_runtime_user.sql`](code/sql/00b_admin_runtime_user.sql) | SYSDBA |
| read-only checks before and after each step | [`code/sql/01_preflight_readonly.sql`](code/sql/01_preflight_readonly.sql), [`02_readback_prereqs.sql`](code/sql/02_readback_prereqs.sql) | AGENTMEM |
| the LLM configuration file for LiteLLM's `oci/` route | [`code/tools/build_oci_agentmem_config.py`](code/tools/build_oci_agentmem_config.py) | host |
| 0: database and LLM smoke checks | [`code/py/00_smoke_db.py`](code/py/00_smoke_db.py), [`00_smoke_llm.py`](code/py/00_smoke_llm.py) | host |
| 1: memory evolution on ten memories, no LLM | [`code/py/10_memory_evolution.py`](code/py/10_memory_evolution.py) | host |
| 2: the five link types, status and the delete trigger | [`code/py/20_link_types.py`](code/py/20_link_types.py) | host |
| 3: the memory graph in SQL, and the SDK's SQL against SQL/PGQ | [`code/py/30_graph_sql.py`](code/py/30_graph_sql.py) with [`code/sql/30_graph_queries.sql`](code/sql/30_graph_queries.sql), [`31_sdk_vs_pgq.py`](code/py/31_sdk_vs_pgq.py) | host |
| 4: C1, KEYWORD against HYBRID against VECTOR | [`code/py/40_retrieval_bakeoff.py`](code/py/40_retrieval_bakeoff.py) | host |
| 5: C2, automatic linking modes | [`code/py/50_autolink_bakeoff.py`](code/py/50_autolink_bakeoff.py) | host |
| 6: image memory | [`code/py/60_image_memory.py`](code/py/60_image_memory.py) | host |
| 7: C3, top-K against LLM pruning | [`code/py/70_bounded_context.py`](code/py/70_bounded_context.py) | host |
| 8: summaries, the runtime user, purge jobs | [`code/py/80_operations.py`](code/py/80_operations.py) with [`code/sql/81`](code/sql/81_registry_jobs.sql), [`82a`](code/sql/82a_runtime_grants_docs.sql) and [`82b`](code/sql/82b_runtime_grants_extra.sql) | host, SYSDBA |
| 9: Deep Data Security, read-only | [`code/sql/90_dds_readonly_check.sql`](code/sql/90_dds_readonly_check.sql) | SYSDBA |
| 10: the announcement's snippets on the 26.8.0 SDK | [`code/py/100_snippets_live.py`](code/py/100_snippets_live.py) | host |
| clean-up of a store whose creation failed | [`code/py/11_drop_failed_store.py`](code/py/11_drop_failed_store.py) | host |
| the synthetic data and its generators | [`code/data/`](code/data/) | anywhere |
| offline probes of the SDK (no database) | [`code/offline/`](code/offline/) | anywhere |

Every Python step reads its settings and secrets from files on the host; nothing in this folder holds a
password, an OCID or a key. `code/py/lib/stores.py` keeps its own record of every object a store
creates, in `AGENTMEM.POC_MANIFEST`, and `code/py/sweep_manifest.py` checks it at the end of a step.

## The frozen protocol

[`PLAN.md`](PLAN.md) is section 7 of the lab's plan: the three comparisons, their tests, alphas and
wording rules, frozen by sha256 on 10 October 2026 before any measured run. Check that the data, the
generators, the matcher, the statistics code and that section are unchanged:

```
cd code
python3 -B tools/freeze_check.py --verify --manifest-sha256 fddd8097ff4f643ee8880dbf478b4837db4ba17c38c7ec9f335878622f555089
```

## Tests

In a virtual environment built from the pinned files (`test_meter.py` needs the SDK installed):

```
cd code
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r requirements.txt
.venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r requirements-test.txt
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests
```

191 tests, no network and no database.

## Not in this folder

- The APEX lab application and everything only its pages use: the app and its generator, the grants
  for its parsing schema, and the DDL and loader of the results table it reads. `code/py/lib/stores.py`
  still names that table, `POC_RESULT`, so its object sweep can tell it apart from store objects, and two
  tests in `code/tests/test_stores.py` check that.
- The stage runner and the database runner, which name the lab's hosts, and the lab's own leak scanners,
  screenshot tools and backup tools.
- The rest of the plan and the build log.

Script headers refer to the plan's sections and to those tools as they ran; one comment had a local
time converted to UTC. Every other file is as it ran.

## Results files

| Files | What they hold |
|---|---|
| [`results/s4/`](results/s4/) | C1: Hit@1 per class, the nine McNemar tests, every query's ranks |
| [`results/s5/`](results/s5/) | C2: the verdict, cost per mode, and each run's links as stored |
| [`results/s7/c3_results.json`](results/s7/c3_results.json) | C3: returned context, gold recall, the bootstrap decisions, pruner calls and tokens |
| [`results/s1/`](results/s1/), [`s2/`](results/s2/), [`s3/`](results/s3/) | memory evolution, link types, the graph queries and the SDK against SQL/PGQ |
| [`results/s6/`](results/s6/), [`s8/`](results/s8/), [`s9/`](results/s9/), [`s10/`](results/s10/) | image memory, operations and the runtime user, the Deep Data Security DDL rendered offline, the snippets |

One synthetic account, one LLM, one database: an observation, not a benchmark.
