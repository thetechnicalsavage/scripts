<!-- v1.1 - public copy: the decision time is given in UTC instead of the operator's local time. -->
# Brief 09 - GO-3 answer-layer configs (decided 30-Sep-2026 ~02:40 UTC, before any answer call)

v1.1. Applies PLAN.md 5.3 ("Configs (up to 9): S1, S2, best MiniLM, M1, the two best of M2-M5, M6, S10, and S10 + `query: ` only if an e5 model wins. Identical indexes are de-duplicated.") to the dev decisions in `dev_decisions_20260930T023500Z.json` (decide_dev.py v1.3, rules `dev_rules.md` v1.1).

| PLAN slot | Resolves to | Config | Index | Answer knobs |
|---|---|---|---|---|
| S1 baseline | S1 | 1 | RAG_M0_C1024_O128_COS | k 5, thr 0 |
| S2 production settings | S2 | 2 | RAG_M0_C2000_O300_COS | k 6, thr 0.3 |
| best MiniLM | R1 keeps 1024 and R2 keeps 128, so it is S1 | (1) | de-duplicated | - |
| M1 | M1 | 8 | RAG_M1_C1024_O128_COS | k 5, thr 0 |
| two best of M2-M5 | R3 ranking M5, M4 | 11, 12 | RAG_M4_C1024_O128_COS, RAG_M5_C1024_O128_COS | M4: k 5, thr 0; M5: see S10 |
| M6 | M6 | 13 | RAG_M6_C1024_O128_COS | k 5, thr 0 |
| S10 tuned | R5: M5, chunk 1024 (R4), overlap 128 (R2), existing index; R6 k 10; R7 thr 0.59 | 12 | RAG_M5_C1024_O128_COS | **k 10, thr 0.59** |
| S10 + `query: ` | M5 is not an e5 model | - | not run | - |

**De-duplication of S10 and config 12.** match_limit and similarity_threshold belong to the vector index (`08_set_query_knobs.sql` calls UPDATE_VECTOR_INDEX), and S10 resolved to config 12's own index. Following PLAN's de-duplication rule, config 12 is answered once, with S10's calibrated knobs, and is labelled S10. Consequences, reported with the results:
- there is no answer-layer run of M5 at the default k 5 / threshold 0; the effect of the calibration itself is shown by the S5/S6 end-to-end dev sweeps on this index (illustrative, 1 run);
- the exploratory answer comparison of M4 (default knobs) with M5 (S10 knobs) mixes model and knobs.

**Calls:** 6 configs x 108 questions (bucket D is retrieval-only) x 3 runs = 1,944 GENERATE narrate calls, serial, interleaved by seeded shuffle (PLAN 5.3). H3 compares S10 with S1 on the test split.

**Note on the calibrated threshold.** R7 maximises balanced accuracy over 32 dev answerable and 8 dev unanswerable questions: at 0.59 all 8 unanswerable are refused and 18 of 32 answerable keep their evidence chunk above the threshold (BA 0.78). With only 8 unanswerable dev questions the rule favours refusing; this is the pre-registered outcome and is reported as such.
