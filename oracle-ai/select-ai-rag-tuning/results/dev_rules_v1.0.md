# v1.0 - brief 09: dev-split decision rules, pre-registered 30-Sep-2026 before any dev result of
#        builds 2-18 was read (only the 2-question plumbing smoke test had run; its metrics were not used).
#        Source: PLAN.md 4.1 (chunk_size, overlap, match_limit, threshold rules), 4.2 (S8, S10), 5.4 (H2).
#        Where PLAN.md leaves a rule open (how "best multilingual model" is ranked), this file fixes it.

Inputs: the retrieval JSONL of every built index (eval_retrieval.py, full run, no --only), all on the
same questions seal. Only records with split == "dev" are read. Bucket D (retrieval_only) is excluded.

Common definitions
- unmasked = run_label "unmasked" (the question against the whole index, as a user would ask it).
- evidence hit at k = eval_retrieval.hit_at(rec, k, "evidence", None) (no threshold).
- budget k = stats.budget_k(chunk_size) = round(6000 / chunk_size): 640 -> 9, 1024 -> 6, 1536 -> 4, 2000 -> 3.
- containable-conditional rate = hits / n over records whose own "containable" is true in that index's run.
- "3 pp or less keeps the default": if best_rate - default_rate <= 0.03 the default wins.

R1 S3 chunk_size (M0, overlap 128): candidates configs 3 (640), 1 (1024), 4 (1536), 5 (2000).
   Metric: containable-conditional evidence hit at budget k, dev, answerable, unmasked.
   Winner: the highest rate; ties go to the size nearer 1024 (then the smaller); 3 pp or less keeps 1024.
R2 S4 overlap at the R1 winner w: candidates overlap 0 (config 6), round(0.2 * w) (config 7) and 128
   (the R1 winner's own index). Same metric and k = budget_k(w). 3 pp or less keeps 128. Ties keep 128.
   Config 6/7 rows: chunk_size w, match_limit budget_k(w), threshold 0, index names
   RAG_M0_C<w>_O<ovl>_COS, profiles RAG_P_M0_C<w>_O<ovl>_COS.
R3 model ranking (S8, fixed chunk 1024/128): multilingual candidates M1..M5 (configs 8..12).
   Primary: evidence hit@5, dev, answerable, unmasked, unconditional (all dev answerable non-D questions).
   Tie-break 1 (difference below 1e-9): mean over the four masked directions EN->EN, AR->AR, EN->AR,
   AR->EN of evidence hit@5 on dev bucket T. Tie-break 2: the smaller model (parameters: M1 118M,
   M2 278M, M3 560M, M4 568M, M5 568M; then key order).
   Rank 1 = BEST_MULTILINGUAL (binds stats.py H2 to its 1024/128 index); ranks 1 and 2 get builds 14-17.
   M0 and M6 are ranked too (reported), but only M1..M5 are candidates.
R4 best chunk_size of each top-2 model: its 1024/128 index vs its 1536/128 (k 4) and 2000/128 (k 3)
   builds. Same metric as R1. 3 pp or less keeps 1024.
   Rows 14/15 = rank-1 model at 1536/2000; rows 16/17 = rank-2 model at 1536/2000
   (index RAG_<key>_C<size>_O128_COS, profile RAG_P_<key>_C<size>_O128_COS, match_limit = budget k).
R5 S10 model and chunking: the best of ALL models (M0..M6 and M1Q excluded as exploratory) by the R3
   primary metric at 1024/128, then that model's best chunk_size (R4 if it is a top-2 model, else 1024),
   and overlap: the R2 winner applies to M0 only; for another model overlap stays 128 unless R2 chose a
   different overlap, in which case build 19 = best model x best chunk x R2 overlap (PLAN 4.3 row 19).
   If build 19 is not needed, S10 reuses the existing index.
R6 match_limit for S10 (S5 rule): the smallest k in {3,5,6,8,10,12} with dev unmasked evidence
   hit@k >= hit@12 - 0.03 (unconditional, answerable).
R7 similarity_threshold for S10 (S6 rule, per model, SCORE units per P14): P14 measured that for a
   COSINE index Select AI's SCORE is 1 - cosine distance rounded to 2 dp (max error 4.9e-3 = rounding).
   Candidate thresholds t in {0.00, 0.01, ..., 0.99}. Maximise balanced accuracy =
   mean( share of dev answerable questions with round(best_evidence similarity, 2) >= t,
         share of dev unanswerable questions with round(top-1 hit similarity, 2) < t ).
   Ties go to the lower value. Unmasked records only.

Output: results/dev_decisions.json (inputs with sha256, every candidate's n / x / rate, the winner of
each rule and why) and the experiments.csv rows 6, 7, 14-17 and 19 filled in (status planned), or
status "skipped" with a reason for 19 when it is not needed.
