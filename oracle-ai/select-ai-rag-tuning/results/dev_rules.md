# v1.1 - (v1.1, 30-Sep ~00:35 UTC, still before any dev result of builds 2-18 was read: answers to the
#        implementer's rule questions and the checker's MEDIUM findings; see 'Amendments v1.1' at the end)
#        brief 09: dev-split decision rules, pre-registered 30-Sep-2026 before any dev result of
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

Amendments v1.1 (orchestrator, written while EVAL_RET on gold v2.4 was running and before any of its
results was opened; they settle the implementer's rule questions and the checker's findings)
A1 Inputs must be complete runs: a file without exactly one summary record, or whose pass-1 unmasked
   query records do not number header n_questions, is refused (exit 2).
A2 Ties above the default (R2, R4): a tie at the top that includes the default keeps the default
   ("ties keep 128"). A tie between two non-default candidates that both clear the 3 pp margin goes to
   the one nearer the default, then the smaller (the R1 tie rule): R2 at w=1024 -> 205 before 0;
   R4 -> 1536 before 2000.
A3 R2 at w = 640: round(0.2 x 640) = 128 is the winner's own index, so R2 compares 0 and 128 only and
   row 7 is set to status 'cut' with the note "20% of 640 = 128, the S3 winner's own overlap".
A4 Parameter counts for tie-breaks: M0 33M, M1 118M, M2 278M, M6 135M, M3 560M, M4 568M, M5 568M
   (then key order).
A5 R5 ranks every model whose 1024/128 index is among the inputs (M0..M6; M1Q excluded) by the R3
   primary metric, with R3's tie-breaks (A4 counts). A model whose index is missing is reported and
   left out, not waited for.
A6 R5 chunk and overlap of the best model: for M0 the R1 winner w and the R2 overlap (an existing
   build: S3/S4 winner). For another model, its R4 best chunk c (1024 if it is not top-2, or if its
   builds 14-17 are cut), and the R2 choice carried over as a rule, not a character count: 128 stays
   128, 0 stays 0, "20%" becomes round(0.2 x c). Build 19 is needed only when that index does not
   already exist. If S10 resolves to S1's own index (M0 wins, R1 and R2 keep the defaults), the tool
   reports it as an operator decision and binds nothing for H3.
A7 Rows with status 'cut' are final: never rewritten, reported as cut_skipped with a WARN; the other
   rows are still written. If builds 14-17 are cut, R4 keeps 1024 for that model.
A8 Row 19 build-time match_limit = budget_k(chunk) and similarity_threshold 0 (as rows 3-7 and 14-17);
   the S10 knobs from R6/R7 are applied afterwards with 08_set_query_knobs.sql.
A9 R7 uses eval_retrieval.calibrate_threshold's definition in SCORE units: an answerable question's
   score is its best evidence chunk, or, where no chunk holds the evidence, its best gold-document chunk
   (counted as a fallback); unanswerable questions without hits are left out. Scores are
   round(similarity, 2) half away from zero (Oracle ROUND), candidates t on the 0.01 grid from 0.00.
A10 corpus_format of a filled row is taken from the inputs' header experiments_row.corpus_format; the
   tool refuses if the inputs disagree.
A11 Output file name results/dev_decisions_<stamp>.json. answer_layer is not decided by these rules
   (kept as is). When S3_WINNER is S1 itself, H5 compares S1 with itself and is reported as such.
