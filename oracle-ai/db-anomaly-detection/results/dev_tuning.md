<!-- v1.1 2026-10-07 - public copy: host name and local-time references removed (the sha256 test_results.md cites is v1.0's). v1.0 2026-10-07 - phase 6.1 of app 900 / brief 10: dev tuning on EVAL_DEV, exactly per docs/phase6.md 6.1 and
     PLAN.md v1.7 sections 7 and 7a. Written at the moment of choice, before anything of the test day
     [2026-10-06 00:27Z, 2026-10-07 00:27Z) was scored, graded or queried. Its sha256 is recorded in STATUS.md. -->
# Dev tuning (phase 6.1): the chosen sensitivity per detector

Dev day EVAL_DEV = [2026-10-05 00:27Z, 2026-10-06 00:27Z) = [EVAL_FROM, EVAL_FROM + 1 day). All times UTC.
Observer ANOMOPS on ORCLPDB1 (Oracle AI Database 26ai 23.26.1). Database work 2026-10-07 00:32-00:48Z and file reviewed (Codex) by 00:57Z, all after EVAL_TO
(2026-10-07 00:27Z).

## 1. The choice

| Detector | Chosen step | Chosen value | Model for the test day | Dev result (caught, false alarms per 24 h) | How the rule decided |
|---|---|---|---|---|---|
| MSET (D1) | 4 (default) | MSET_ALERT_COUNT 3, MSET_ALERT_WINDOW 5 | ANOM_MSET_FINAL_202610050007 | 13/14, 1 | step 5 (2,5) has 5 false alarms; step 4 is the most sensitive value with at most 2 |
| SVM (D2) | 5 | SVMS_OUTLIER_RATE .02 | ANOM_SVM_TUNE_0_02_202610070037 | 9/14, 0 | the most sensitive value meets the limit |
| EM (D3) | 5 | EMCS_OUTLIER_RATE .02 | ANOM_EM_TUNE_0_02_202610070037 | 11/14, 0 | the most sensitive value meets the limit |
| PCA (D4) | 5 | residual threshold = 99th percentile | ANOM_PCA_TUNE_99_202610070037 | 10/14, 0 | the most sensitive value meets the limit |
| IFOREST (D5) | 5 | contamination .02 | IFOREST_0.02 (tools/iforest.py, random_state 20261004) | 9/14, 0 | the most sensitive value meets the limit |
| STATIC (R1) | 4 (default) | upper threshold percentile 99.5 | ANOM_STATIC_FINAL_202610050007 | 11/14, 0 | step 5 (99) has 3 false alarms; step 4 is the most sensitive value with at most 2 |
| SEASONAL (R2) | 5 | k = 3 | ANOM_SEASONAL_TUNE_3_202610070037 | 0/14, 1 | the most sensitive value meets the limit; see 6.1: it catches nothing |

Every detector had at least one value within the limit, so the "take the least sensitive" fallback was never used.
SEASONAL's choice is what the rule gives when applied exactly as written. Section 6.1 explains why it catches nothing on
the dev day.

## 2. The rule, as pre-registered, and how it was applied

PLAN.md v1.7 section 7: *"Tuning (same budget for every detector). Each detector has one sensitivity knob with 5
pre-listed values (...). Rule: on dev, pick the most sensitive value with at most 2 false-alarm episodes per 24 h; ties
go to the shorter median time to detect."*

PLAN.md v1.7 section 7a, *"The five tuning values per detector (ordered from least to most sensitive)"*: D1 MSET
(MSET_ALERT_COUNT, MSET_ALERT_WINDOW) = (6,10), (5,8), (4,6), (3,5), (2,5), MSET_ALPHA_PROB at its default .01; D2
SVMS_OUTLIER_RATE and D3 EMCS_OUTLIER_RATE = .001, .0025, .005, .01, .02; D4 PCA residual threshold = the 99.95th,
99.9th, 99.8th, 99.5th, 99th percentile of the training residuals; D5 IsolationForest contamination = .001, .0025, .005,
.01, .02; R1 upper threshold percentile = 99.95, 99.9, 99.8, 99.5, 99; R2 k = 8, 6, 5, 4, 3.

docs/phase6.md 6.1: *"pick per detector by PLAN 7: the most sensitive value with at most 2 false-alarm episodes per
24 h; ties to the shorter median time to detect. If no value meets the limit, take the least sensitive and say so."*

Applied as:
- "Most sensitive" is PLAN 7a's pre-registered order, which is TUNING_GRID's step (1 = least, 5 = most sensitive; step
  4 = the default the FINAL models carry). phase6.md's fallback ("take the least sensitive") uses the same word for the
  same order. This reading was written into the choice query (appendix A.6, Q3) before any graded number was looked at.
- The limit: GRADE_RESULT.FA_PER_24H <= 2. The dev day is exactly 24 h, so FA_PER_24H = N_FALSE_ALARMS. A false alarm
  is grade()'s: an ALERT_EVAL episode that starts in the dev day outside every chaos run's [start, end + 30 min], any
  source.
- The tie clause did not apply. The pre-registered order is strict, so two admissible values never share a step.
- Chosen = the highest step with FA_PER_24H <= 2. If no step qualified, step 1 would be taken, but that never
  happened.

Reading note, for transparency. If "most sensitive" were read as "most incidents caught on dev" instead, then among the
values within the limit the choice would change for two detectors. MSET would get (4,6), because steps 1-3 each catch
14/14 and step 3 has the shortest median time to detect at 2.50 min. SEASONAL would get k = 8, the only value with a
catch (4/14). PCA would be left with a tie: steps 4 and 5 both catch 10/14 with the same median time to detect. SVM
would also be tied, between steps 4 and 5 (9/14 each, same median). EM, IFOREST and STATIC would not change. That
reading was not applied. It conflicts with PLAN 7a's explicit order and with phase6.md's fallback wording, and it is
reported here only so the choice can be audited.

## 3. Preconditions (phase6.md 6.0)

Only the dev-day half of 6.0 was checked here. This task forbids scoring, grading or even querying the test day.

Every query in the appendix on a time-series or event table (FEATURE_MINUTE, METRIC_MINUTE, APP_MINUTE, INCIDENT_PLAN,
INCIDENT_TRUTH, TRAIN_EXCLUDE, COLLECT_LOG, SCORE_EVAL, SCORE_EVAL_DETAIL, ALERT_EVAL, GRADE_INCIDENT, GRADE_RESULT,
SCORE_MINUTE) does one of three things:
- **(a) Time-bounded.** It bounds its time to before EVAL_FROM + 1 day.
- **(b) Selects by a DEVTUNE run tag.** Every row under those tags lies in the dev day:
  - A.4 refuses a scored minute outside it.
  - A.7 X5 shows SCORE_EVAL's DEVTUNE rows span 10-05 00:27:30 to 10-06 00:26:30, and ALERT_EVAL's last DEVTUNE
    episode minute is 10-06 00:26:30.
  - grade() was called with the dev-day range.
- **(c) An untimed isolation check by tag or model name.** None of these returned a row of the test day:
  - A.9 (4) counts SCORE_EVAL rows per run tag before anything was scored, and found only phase 4's DEV_P4 (749 rows of
    01-Oct).
  - A.9 (4) also counts DEVTUNE / TUNE rows, and found 0.
  - A.9 (6) counts the DEV_P4 rows left, 749 rows of 01-Oct.
  - A.7 X5 counts GRADE_RESULT rows under EVAL_DEV / EVAL_TEST, and found 0.
  - A.7 X5 also counts SCORE_MINUTE rows of TUNE models, and found 0.

Inside the packages, three reads span every date, and none of them returns anything to the caller:
- **grade()'s false-alarm test** checks a dev-day episode against INCIDENT_TRUTH runs of every date. A run that starts
  after the dev day can never contain an episode start from before it, so it cannot change a dev-day result.
- **The trainer's chaos-window filter** in `training_query` and `window_signals` does the same against INCIDENT_TRUTH.
  A run of the evaluation cannot reach back into the training window.
- **The trainer's one batch refresh** (A.2, `batch_truth_begin`) merges the target's whole CHAOS_RUN into
  INCIDENT_TRUTH. The truth loop makes that same copy every 5 minutes, and it already held every run.

The test-day half of 6.0 is **deferred to the opening of 6.2**. That half covers the day-1 plan rows, their CHAOS_RUN
match, any UI/TEST run, and completeness
and null minutes of the test day. The only evidence for it so far is STATUS.md's checks made during the run (all 28
planned runs STARTED and DONE, 0 skipped, maxgap 61-62 s), and it was not re-queried. MODEL_REGISTRY and the scheduler's
job log were read over the whole evaluation. They describe model and job lifecycles and contain no test-day
observation.

| 6.0 item | Result |
|---|---|
| EVAL_TO in the past | yes: `PKG_ANOM_LIVE.clock('EVAL_TO')` = 2026-10-07 00:27:00, checked at 00:32:59Z |
| ANOM_TRAIN_FINAL ran once | 1 run, SUCCEEDED 2026-10-05 00:07:00Z (17 s, error# 0); no per-detector failure. ANOM_RETRAIN_INTERIM disabled (COMPLETED, 13 runs, the last 2026-10-04 18:17Z) |
| Every detector's FINAL model ACTIVE on [TRAIN_FROM, TRAIN_TO) | 6 ACTIVE (EM, MSET, PCA, SEASONAL, STATIC, SVM), window [2026-10-01 23:57, 2026-10-04 23:57), all 6 the only ACTIVE model of their detector |
| TRAIN_EXCLUDE and chaos windows honoured | signals_json of every FINAL: rows_in_window 4,320, rows_in_chaos_windows 0, rows_in_exclusion_windows 38, rows_with_null 0, rows_trained 4,282; 34 signals used, APP_ERR_PCT dropped (constant 0). No chaos run's [start - 5, end + 30] window touches the training window. The 38 rows are TRAIN_EXCLUDE windows 1, 2, 7, 8, 9 (02-Oct 01:15-02:37Z). No exclusion window was created after FINAL_AT |
| INCIDENT_PLAN, dev day | 14 rows (plan ids 1-14, day_no 0): 14 STARTED, 0 SKIPPED, 0 MISSED. With no skip, no skip reason was needed |
| CHAOS_RUN SCHEDULE rows match STARTED rows one for one (dev day) | 14 SCHEDULE runs start in the dev day (run ids 81, 101-113), all DONE and restored, none gone; each is the run of exactly one STARTED plan row with the planned minutes; 0 STARTED rows without a run, 0 SCHEDULE runs without a STARTED row. Read from INCIDENT_TRUTH, the observer's copy of CHAOS_RUN, which the truth loop last refreshed at 2026-10-07 00:30:20Z (rows=94, gone=0), after every run had ended |
| No UI or TEST run inside the window (dev day) | none starts in the dev day, and no earlier run's window reaches into it. No exclusion from false-alarm counting needed |
| METRIC_MINUTE / APP_MINUTE completeness (dev day) | METRIC_MINUTE 1,440 rows (00:27:30 to 00:26:30 next day), 0 back-filled ('H'), largest step 62 s, 0 gaps over 90 s; APP_MINUTE 1,440 rows, 0 minutes missing; FEATURE_MINUTE 1,440 rows |
| Null-signal minutes per detector (dev day) | 5 of 1,440 for each of MSET, SVM, EM, PCA, STATIC, SEASONAL and IFOREST (1,435 scored). All 5 lie inside run 112, blocking_chain HIGH, 20:56:00-21:07:01Z: FEATURE_MINUTE 20:56:30, 20:58:30, 21:00:30, 21:02:30, 21:04:30, whose APP_MINUTE rows (20:57, 20:59, 21:01, 21:03, 21:05) have n_ok = n_err = 0. No transaction completed in those minutes while the hot rows were locked, so APP_P50_MS and APP_P95_MS are null. Not scored by any detector (PLAN 7a); every candidate reports them as "Unscored min" |
| Nothing retrained during the evaluation | MODEL_REGISTRY: 0 models created and 0 status changes after the FINAL activation at 00:07Z (checked before this phase trained anything). COLLECT_LOG from FINAL_AT to the end of the dev day holds only the FINAL batch (6 model_trained, 6 model_activated, 1 final_trained, all at 00:07Z) and no other row of any severity |
| FINAL training rows unchanged since FINAL_AT | no METRIC_MINUTE row of the training window collected after FINAL_AT (last 2026-10-04 23:57:48); no APP_MINUTE row loaded after it (last 23:58:00); `window_signals` + `training_query` on the FINAL window now return the same 34 signals and 4,282 rows as every FINAL model |

## 4. What was done

- **Model-setting knobs** (MSET alert count/window, SVM and EM outlier rates) and **threshold knobs** (PCA residual
  percentile, STATIC percentile, SEASONAL k). For steps 1, 2, 3 and 5, `PKG_ANOM_TRAIN.train(det, TRAIN_FROM, TRAIN_TO,
  'TUNE_<value>', <TUNING_GRID settings of that step>, 'N')` was called. That is 24 models, all CANDIDATE, created
  00:36:43-00:37:50Z. Step 4 is the default, and the FINAL model was trained on exactly these rows with exactly that
  value, so the FINAL model is the step-4 candidate. It was scored as it stands, not retrained. `<value>` is the grid
  value with "." written "_", because a variant name allows only A-Z, 0-9 and _ (.001 is written 0_001 and 99.95 is
  written 99_95). MSET's value is written COUNT_WINDOW (6_10). The script checks every name against the grid's JSON
  before it trains.
- **One INCIDENT_TRUTH refresh.** The trainer refreshes the chaos-window copy before training: `batch_truth_begin`, once
  for all 24 models, at the start of the training session (00:36:40-00:36:43Z). That is one read-only read of SHOP.CHAOS_RUN and SHOP.CHAOS_PLAN over the existing
  ANOM_CHAOS_LINK, the same read the truth loop makes every 5 minutes. It came after EVAL_TO. No other link call was
  made, and nothing on oradb1 was changed.
- **Threshold knobs reuse the FINAL model's statistics** (phase6.md 6.1). There is no scoring path that applies another
  threshold to a model's stored statistics, because `score_range` reads the threshold from the model it scores. So each
  threshold value got its own CANDIDATE model on the same rows, and section 5 checks that the statistics are FINAL's.
- **Scoring.** `PKG_ANOM_SCORE.score_range(model, EVAL_FROM, EVAL_FROM + 1, 'DEVTUNE_<DET>_<value>')` scored each of the
  30 in-database candidates into SCORE_EVAL, never SCORE_MINUTE. It wrote 1,440 minutes per candidate, 50,400 SCORE_EVAL
  rows in total including IFOREST's, from 00:27:30 on 10-05 to 00:26:30 on 10-06. MSET also reads 120 minutes of
  context before EVAL_FROM (22:27-00:27Z: the last 90 minutes of the training window and the 30 minutes up to
  EVAL_FROM). They feed MSET's sequential test and are not stored.
- **IsolationForest.** On the demo host, `~/anomaly/venv-ml/bin/python tools/iforest.py --train-from 2026-10-01T23:57Z
  --train-to 2026-10-04T23:57Z --score-from 2026-10-05T00:27Z --score-to 2026-10-06T00:27Z --run-tag
  DEVTUNE_IFOREST_<value> --contamination <c>` was run for c = .001, .0025, .005, .01, .02. The staged copy is
  byte-identical to the repository's (sha256 e34d3885...39f5). It used RANDOM_STATE 20261004 and scikit-learn 1.9.1,
  trained on 4,282 rows and 34 signals (APP_ERR_PCT dropped), and scored 1,435 of 1,440 minutes. Exit 0 every time. A
  secret scan of the five logs on the demo host found 0 hits across 6 values. The registry row IFOREST_0.01, left from
  phase 4's DEV_P4 run, was updated in place by the tool's MERGE. Its DEV_P4 SCORE_EVAL rows are untouched.
- **Grading.** `PKG_ANOM_GRADE.grade('DEVTUNE_<DET>_<value>', EVAL_FROM, EVAL_FROM + 1, 'SCHEDULE', p_refresh => 'N')`
  was run once per tag, 35 tags with one model each. p_refresh = 'N' was used because INCIDENT_TRUTH was current (see
  section 3), and that way grading made no link call. Incidents are the 14 SCHEDULE runs of the dev day: 4 LOW and 10
  HIGH.
- **Not touched.** The FINAL models were not retrained or re-activated, and their status and status_ts are unchanged.
  `score_range` records the batch cost of every model it scores, so the six FINAL rows' SCORE_MS_PER_MIN went from null
  to the dev-day measurement (EM .265, MSET 4.58, PCA 1.138, SEASONAL .173, STATIC .159, SVM 2.808). Nothing else
  changed: no SCORE_MINUTE row exists for a TUNE model, no EVAL_DEV / EVAL_TEST row was written, no model was dropped,
  and no other schema was touched.

## 5. The variants learned from exactly the FINAL training rows

FINAL's training rows are not stored row by row; they are whatever `training_query` returned at 00:07Z. The argument
that the variants saw the same rows has three parts:
1. **The inputs of that query are unchanged since FINAL_AT** (section 3):
   - no FEATURE_MINUTE source row of the window was collected or loaded after it;
   - no chaos window touches the training window;
   - the five TRAIN_EXCLUDE windows inside it were all created on 02-Oct, and none was added later.
   The query is deterministic in those inputs.
2. **Same counts and lists.** All 24 TUNE models have the same window as FINAL ([2026-10-01 23:57, 2026-10-04 23:57)),
   the same row accounting (4,320 in the window, 0 in chaos windows, 38 in exclusion windows, 0 with a null, 4,282
   trained), the same 34 used signals in the same order, and the same dropped list.
3. **Statistical fingerprints of the row set match FINAL's exactly.** SEASONAL's 816 per-signal, per-hour rows (n,
   median, MAD) and PCA's 34 per-signal rows (n, mean, standard deviation) are identical to FINAL's (below).
- **SEASONAL.** Each TUNE model's SEASONAL_BASE (816 rows: median, MAD and n per signal and UTC hour) is identical to
  FINAL's. A MINUS in both directions returns 0 rows for each of the 4. Only k differs.
- **PCA.** The z-scaling (MODEL_SIGNAL_STAT) is identical to FINAL's (MINUS = 0 rows) and K = 10 for every model. Each
  TUNE model's threshold equals the same percentile of the **FINAL model's own** training reconstruction errors,
  recomputed with `pca_resid_sql(FINAL, 10, training_query(FINAL window))` (relative difference 0 to 1.1e-16). The same
  query reproduces FINAL's stored threshold (113.1398, difference 8.8e-17). The thresholds are 469.33 (99.95), 437.47
  (99.9), 276.09 (99.8), 113.14 (99.5, FINAL) and 52.90 (99). The training-error median, 0.4726, is the same in all
  five.
- **STATIC.** Every threshold is the given percentile of these same rows. Across 99.95 / 99.9 / 99.8 / 99.5 (FINAL) /
  99, every signal's upper threshold is non-increasing (0 signals out of order).

## 6. Every candidate, per detector

Columns: caught / incidents (all, LOW, HIGH); time to detect (TTD) is grade()'s, minutes from the incident start to
the start of the interval whose scoring opened the episode, over the caught incidents; false alarms as defined in
section 2; episodes = every ALERT_EVAL episode of the tag; flagged / unscored = SCORE_EVAL minutes with flag 1 /
null; ATTR_P mean, lenient hits and tie median are grade()'s attribution figures (PLAN 7a v1.7), shown for the record
and not used by the choice; IFOREST has no attribution.

### EM

| Step | Value (TUNING_GRID) | Model scored | Run tag | Caught | LOW | HIGH | TTD median (min) | TTD p90 (min) | False alarms (= per 24 h) | Episodes | Flagged min | Unscored min | ATTR_P mean | Lenient hits | Tie median | <= 2 FA/24 h | Chosen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `{"EMCS_OUTLIER_RATE":0.001}` | ANOM_EM_TUNE_0_001_202610070037 | DEVTUNE_EM_0_001 | 6/14 | 1/4 | 5/10 | 2.48 | 4.00 | 0 | 6 | 129 | 5 | 0.333 | 2/6 | 1.5 | yes |  |
| 2 | `{"EMCS_OUTLIER_RATE":0.0025}` | ANOM_EM_TUNE_0_0025_202610070037 | DEVTUNE_EM_0_0025 | 7/14 | 2/4 | 5/10 | 1.50 | 4.10 | 0 | 7 | 153 | 5 | 0.143 | 1/7 | 1.0 | yes |  |
| 3 | `{"EMCS_OUTLIER_RATE":0.005}` | ANOM_EM_TUNE_0_005_202610070037 | DEVTUNE_EM_0_005 | 8/14 | 2/4 | 6/10 | 1.48 | 19.20 | 0 | 8 | 256 | 5 | 0.250 | 2/8 | 1.0 | yes |  |
| 4 (default) | `{"EMCS_OUTLIER_RATE":0.01}` | ANOM_EM_FINAL_202610050007 | DEVTUNE_EM_0_01 | 8/14 | 2/4 | 6/10 | 1.48 | 16.79 | 0 | 8 | 310 | 5 | 0.219 | 2/8 | 2.5 | yes |  |
| 5 | `{"EMCS_OUTLIER_RATE":0.02}` | ANOM_EM_TUNE_0_02_202610070037 | DEVTUNE_EM_0_02 | 11/14 | 4/4 | 7/10 | 1.48 | 2.52 | 0 | 11 | 402 | 5 | 0.182 | 2/11 | 1.0 | yes | **CHOSEN** |

- Step 1 missed: 103 slow_drift HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 2 missed: 103 slow_drift HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 3 missed: 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 4 missed: 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 5 missed: 106 conn_leak HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH

### IFOREST

| Step | Value (TUNING_GRID) | Model scored | Run tag | Caught | LOW | HIGH | TTD median (min) | TTD p90 (min) | False alarms (= per 24 h) | Episodes | Flagged min | Unscored min | ATTR_P mean | Lenient hits | Tie median | <= 2 FA/24 h | Chosen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `{"contamination":0.001}` | IFOREST_0.001 | DEVTUNE_IFOREST_0_001 | 0/14 | 0/4 | 0/10 | - | - | 0 | 0 | 0 | 5 | n/a | n/a | n/a | yes |  |
| 2 | `{"contamination":0.0025}` | IFOREST_0.0025 | DEVTUNE_IFOREST_0_0025 | 0/14 | 0/4 | 0/10 | - | - | 0 | 0 | 3 | 5 | n/a | n/a | n/a | yes |  |
| 3 | `{"contamination":0.005}` | IFOREST_0.005 | DEVTUNE_IFOREST_0_005 | 1/14 | 0/4 | 1/10 | 1.47 | 1.47 | 0 | 1 | 23 | 5 | n/a | n/a | n/a | yes |  |
| 4 (default) | `{"contamination":0.01}` | IFOREST_0.01 | DEVTUNE_IFOREST_0_01 | 7/14 | 1/4 | 6/10 | 1.50 | 66.09 | 0 | 7 | 164 | 5 | n/a | n/a | n/a | yes |  |
| 5 | `{"contamination":0.02}` | IFOREST_0.02 | DEVTUNE_IFOREST_0_02 | 9/14 | 3/4 | 6/10 | 1.50 | 19.47 | 0 | 9 | 315 | 5 | n/a | n/a | n/a | yes | **CHOSEN** |

- Step 1 missed: 81 hard_parse_storm HIGH; 101 logon_storm HIGH; 102 io_storm LOW; 103 slow_drift HIGH; 104 batch_wrong_time HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 109 temp_spill HIGH; 110 app_error_burst HIGH; 111 plan_regression HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 2 missed: 81 hard_parse_storm HIGH; 101 logon_storm HIGH; 102 io_storm LOW; 103 slow_drift HIGH; 104 batch_wrong_time HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 109 temp_spill HIGH; 110 app_error_burst HIGH; 111 plan_regression HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 3 missed: 81 hard_parse_storm HIGH; 101 logon_storm HIGH; 102 io_storm LOW; 103 slow_drift HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 109 temp_spill HIGH; 110 app_error_burst HIGH; 111 plan_regression HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 4 missed: 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 5 missed: 105 commit_storm LOW; 106 conn_leak HIGH; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH

### MSET

| Step | Value (TUNING_GRID) | Model scored | Run tag | Caught | LOW | HIGH | TTD median (min) | TTD p90 (min) | False alarms (= per 24 h) | Episodes | Flagged min | Unscored min | ATTR_P mean | Lenient hits | Tie median | <= 2 FA/24 h | Chosen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `{"MSET_ALERT_COUNT":6,"MSET_ALERT_WINDOW":10}` | ANOM_MSET_TUNE_6_10_202610070036 | DEVTUNE_MSET_6_10 | 14/14 | 4/4 | 10/10 | 4.49 | 8.29 | 0 | 14 | 398 | 5 | 0.423 | 10/14 | 6.5 | yes |  |
| 2 | `{"MSET_ALERT_COUNT":5,"MSET_ALERT_WINDOW":8}` | ANOM_MSET_TUNE_5_8_202610070036 | DEVTUNE_MSET_5_8 | 14/14 | 4/4 | 10/10 | 3.48 | 6.61 | 0 | 14 | 397 | 5 | 0.419 | 10/14 | 6.5 | yes |  |
| 3 | `{"MSET_ALERT_COUNT":4,"MSET_ALERT_WINDOW":6}` | ANOM_MSET_TUNE_4_6_202610070036 | DEVTUNE_MSET_4_6 | 14/14 | 4/4 | 10/10 | 2.50 | 4.91 | 0 | 14 | 398 | 5 | 0.418 | 10/14 | 6.5 | yes |  |
| 4 (default) | `{"MSET_ALERT_COUNT":3,"MSET_ALERT_WINDOW":5}` | ANOM_MSET_FINAL_202610050007 | DEVTUNE_MSET_3_5 | 13/14 | 4/4 | 9/10 | 1.48 | 3.31 | 1 | 14 | 412 | 5 | 0.436 | 10/13 | 9.0 | yes | **CHOSEN** |
| 5 | `{"MSET_ALERT_COUNT":2,"MSET_ALERT_WINDOW":5}` | ANOM_MSET_TUNE_2_5_202610070037 | DEVTUNE_MSET_2_5 | 11/14 | 3/4 | 8/10 | 0.48 | 1.50 | 5 | 16 | 466 | 5 | 0.407 | 8/11 | 11.0 | no |  |

- Step 1 missed: none
- Step 2 missed: none
- Step 3 missed: none
- Step 4 missed: 81 hard_parse_storm HIGH
- Step 5 missed: 81 hard_parse_storm HIGH; 107 cpu_hog LOW; 111 plan_regression HIGH

### PCA

| Step | Value (TUNING_GRID) | Model scored | Run tag | Caught | LOW | HIGH | TTD median (min) | TTD p90 (min) | False alarms (= per 24 h) | Episodes | Flagged min | Unscored min | ATTR_P mean | Lenient hits | Tie median | <= 2 FA/24 h | Chosen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `{"RESID_PCT":99.95}` | ANOM_PCA_TUNE_99_95_202610070037 | DEVTUNE_PCA_99_95 | 8/14 | 2/4 | 6/10 | 1.48 | 1.50 | 0 | 8 | 180 | 5 | 0.750 | 6/8 | 1.0 | yes |  |
| 2 | `{"RESID_PCT":99.9}` | ANOM_PCA_TUNE_99_9_202610070037 | DEVTUNE_PCA_99_9 | 8/14 | 2/4 | 6/10 | 1.48 | 1.50 | 0 | 8 | 181 | 5 | 0.750 | 6/8 | 1.0 | yes |  |
| 3 | `{"RESID_PCT":99.8}` | ANOM_PCA_TUNE_99_8_202610070037 | DEVTUNE_PCA_99_8 | 9/14 | 2/4 | 7/10 | 1.48 | 35.90 | 0 | 9 | 194 | 5 | 0.778 | 7/9 | 1.0 | yes |  |
| 4 (default) | `{"RESID_PCT":99.5}` | ANOM_PCA_FINAL_202610050007 | DEVTUNE_PCA_99_5 | 10/14 | 3/4 | 7/10 | 1.48 | 12.70 | 0 | 10 | 283 | 5 | 0.800 | 8/10 | 1.0 | yes |  |
| 5 | `{"RESID_PCT":99}` | ANOM_PCA_TUNE_99_202610070037 | DEVTUNE_PCA_99 | 10/14 | 3/4 | 7/10 | 1.48 | 9.00 | 0 | 10 | 318 | 5 | 0.800 | 8/10 | 1.0 | yes | **CHOSEN** |

- Step 1 missed: 103 slow_drift HIGH; 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 2 missed: 103 slow_drift HIGH; 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 3 missed: 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 4 missed: 105 commit_storm LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 5 missed: 105 commit_storm LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH

### SEASONAL

| Step | Value (TUNING_GRID) | Model scored | Run tag | Caught | LOW | HIGH | TTD median (min) | TTD p90 (min) | False alarms (= per 24 h) | Episodes | Flagged min | Unscored min | ATTR_P mean | Lenient hits | Tie median | <= 2 FA/24 h | Chosen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `{"K":8}` | ANOM_SEASONAL_TUNE_8_202610070037 | DEVTUNE_SEASONAL_8 | 4/14 | 1/4 | 3/10 | 1.48 | 1.49 | 1 | 5 | 942 | 5 | 0.750 | 3/4 | 1.0 | yes |  |
| 2 | `{"K":6}` | ANOM_SEASONAL_TUNE_6_202610070037 | DEVTUNE_SEASONAL_6 | 0/14 | 0/4 | 0/10 | - | - | 4 | 5 | 1022 | 5 | - | 0/0 | - | no |  |
| 3 | `{"K":5}` | ANOM_SEASONAL_TUNE_5_202610070037 | DEVTUNE_SEASONAL_5 | 0/14 | 0/4 | 0/10 | - | - | 1 | 1 | 1106 | 5 | - | 0/0 | - | yes |  |
| 4 (default) | `{"K":4}` | ANOM_SEASONAL_FINAL_202610050007 | DEVTUNE_SEASONAL_4 | 0/14 | 0/4 | 0/10 | - | - | 1 | 1 | 1181 | 5 | - | 0/0 | - | yes |  |
| 5 | `{"K":3}` | ANOM_SEASONAL_TUNE_3_202610070037 | DEVTUNE_SEASONAL_3 | 0/14 | 0/4 | 0/10 | - | - | 1 | 1 | 1338 | 5 | - | 0/0 | - | yes | **CHOSEN** |

- Step 1 missed: 81 hard_parse_storm HIGH; 101 logon_storm HIGH; 102 io_storm LOW; 103 slow_drift HIGH; 104 batch_wrong_time HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 108 blocking_chain LOW; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 2 missed: 81 hard_parse_storm HIGH; 101 logon_storm HIGH; 102 io_storm LOW; 103 slow_drift HIGH; 104 batch_wrong_time HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 109 temp_spill HIGH; 110 app_error_burst HIGH; 111 plan_regression HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 3 missed: 81 hard_parse_storm HIGH; 101 logon_storm HIGH; 102 io_storm LOW; 103 slow_drift HIGH; 104 batch_wrong_time HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 109 temp_spill HIGH; 110 app_error_burst HIGH; 111 plan_regression HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 4 missed: 81 hard_parse_storm HIGH; 101 logon_storm HIGH; 102 io_storm LOW; 103 slow_drift HIGH; 104 batch_wrong_time HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 109 temp_spill HIGH; 110 app_error_burst HIGH; 111 plan_regression HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 5 missed: 81 hard_parse_storm HIGH; 101 logon_storm HIGH; 102 io_storm LOW; 103 slow_drift HIGH; 104 batch_wrong_time HIGH; 105 commit_storm LOW; 106 conn_leak HIGH; 107 cpu_hog LOW; 108 blocking_chain LOW; 109 temp_spill HIGH; 110 app_error_burst HIGH; 111 plan_regression HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH

### STATIC

| Step | Value (TUNING_GRID) | Model scored | Run tag | Caught | LOW | HIGH | TTD median (min) | TTD p90 (min) | False alarms (= per 24 h) | Episodes | Flagged min | Unscored min | ATTR_P mean | Lenient hits | Tie median | <= 2 FA/24 h | Chosen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `{"PCT":99.95}` | ANOM_STATIC_TUNE_99_95_202610070037 | DEVTUNE_STATIC_99_95 | 9/14 | 2/4 | 7/10 | 1.48 | 36.10 | 0 | 9 | 213 | 5 | 0.889 | 8/9 | 1.0 | yes |  |
| 2 | `{"PCT":99.9}` | ANOM_STATIC_TUNE_99_9_202610070037 | DEVTUNE_STATIC_99_9 | 9/14 | 2/4 | 7/10 | 1.48 | 35.90 | 0 | 9 | 221 | 5 | 0.889 | 8/9 | 1.0 | yes |  |
| 3 | `{"PCT":99.8}` | ANOM_STATIC_TUNE_99_8_202610070037 | DEVTUNE_STATIC_99_8 | 10/14 | 3/4 | 7/10 | 1.48 | 14.30 | 0 | 10 | 293 | 5 | 0.800 | 8/10 | 1.0 | yes |  |
| 4 (default) | `{"PCT":99.5}` | ANOM_STATIC_FINAL_202610050007 | DEVTUNE_STATIC_99_5 | 11/14 | 4/4 | 7/10 | 1.48 | 1.50 | 0 | 11 | 384 | 5 | 0.818 | 9/11 | 1.0 | yes | **CHOSEN** |
| 5 | `{"PCT":99}` | ANOM_STATIC_TUNE_99_202610070037 | DEVTUNE_STATIC_99 | 11/14 | 3/4 | 8/10 | 1.48 | 1.50 | 3 | 15 | 542 | 5 | 0.627 | 7/11 | 1.0 | no |  |

- Step 1 missed: 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 2 missed: 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 3 missed: 105 commit_storm LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 4 missed: 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 5 missed: 108 blocking_chain LOW; 111 plan_regression HIGH; 112 blocking_chain HIGH

### SVM

| Step | Value (TUNING_GRID) | Model scored | Run tag | Caught | LOW | HIGH | TTD median (min) | TTD p90 (min) | False alarms (= per 24 h) | Episodes | Flagged min | Unscored min | ATTR_P mean | Lenient hits | Tie median | <= 2 FA/24 h | Chosen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `{"SVMS_OUTLIER_RATE":0.001}` | ANOM_SVM_TUNE_0_001_202610070037 | DEVTUNE_SVM_0_001 | 8/14 | 2/4 | 6/10 | 1.49 | 3.70 | 0 | 8 | 173 | 5 | 0.875 | 7/8 | 1.5 | yes |  |
| 2 | `{"SVMS_OUTLIER_RATE":0.0025}` | ANOM_SVM_TUNE_0_0025_202610070037 | DEVTUNE_SVM_0_0025 | 8/14 | 2/4 | 6/10 | 1.48 | 3.00 | 0 | 8 | 176 | 5 | 0.875 | 7/8 | 1.5 | yes |  |
| 3 | `{"SVMS_OUTLIER_RATE":0.005}` | ANOM_SVM_TUNE_0_005_202610070037 | DEVTUNE_SVM_0_005 | 8/14 | 2/4 | 6/10 | 1.48 | 3.00 | 0 | 8 | 176 | 5 | 0.875 | 7/8 | 1.5 | yes |  |
| 4 (default) | `{"SVMS_OUTLIER_RATE":0.01}` | ANOM_SVM_FINAL_202610050007 | DEVTUNE_SVM_0_01 | 9/14 | 2/4 | 7/10 | 1.48 | 29.29 | 0 | 9 | 256 | 5 | 0.667 | 6/9 | 1.0 | yes |  |
| 5 | `{"SVMS_OUTLIER_RATE":0.02}` | ANOM_SVM_TUNE_0_02_202610070037 | DEVTUNE_SVM_0_02 | 9/14 | 2/4 | 7/10 | 1.48 | 16.30 | 0 | 9 | 314 | 5 | 0.583 | 6/9 | 2.0 | yes | **CHOSEN** |

- Step 1 missed: 103 slow_drift HIGH; 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 2 missed: 103 slow_drift HIGH; 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 3 missed: 103 slow_drift HIGH; 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 4 missed: 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH
- Step 5 missed: 105 commit_storm LOW; 107 cpu_hog LOW; 110 app_error_burst HIGH; 112 blocking_chain HIGH; 113 app_error_burst HIGH


### 6.1 Findings the choice carries into the test day (stated, not acted on)

- **SEASONAL (R2) at k = 5, 4 and 3: a single all-day episode.** The baseline flags 1,106 / 1,181 / 1,338 of 1,440 dev
  minutes. The breaches come mostly from PIO and PIO_BYTES (677 flagged minutes at k = 4), then AAS, DBTIME, RT_TXN and
  SQL_RT. The first episode opens at 00:41:30Z (k = 5, 4) or 00:29:30Z (k = 3), before the first incident, and it never
  meets 10 unflagged minutes, so it is still OPEN at the end of the day. That makes 1 false alarm and 0 catches, since a
  catch needs an episode to *start* inside the incident window. The rule counts false-alarm episodes, not alarm
  minutes, so k = 3 is within the limit and is chosen. Only k = 8 catches anything (4/14, with 1 false alarm); k = 6
  breaks into 5 episodes with 4 false alarms. Expect the same behaviour on the test day. It is a property of the
  seasonal rival trained on 72 hours under this rule. The rule was applied as written and nothing was adjusted.
- **MSET (D1).** The three least sensitive values catch 14/14 with 0 false alarms. The chosen (3,5) catches 13/14 with
  1 false alarm, and both come from one episode. That episode opens on the interval 01:03:30-01:04:30Z, and run 81
  (hard_parse_storm HIGH) starts inside it, at 01:04:02Z. grade() times an episode from the begin time of its opening
  interval (PLAN 7a), so this one starts 32 s before the incident. It therefore counts as a false alarm, and the
  incident is missed because no new episode starts in its window while this one is open. Whether the interval was set
  off by the storm's first 28 s or by the nightly settlement batch (from 01:00Z; (2,5) opened at 01:00:29Z) is not
  established here. (2,5) splits into 16 episodes, 5 of them false alarms, and catches 11/14. For each of its 3 misses
  (runs 81, 107, 111), an episode of the same tag was already open when the incident started, and none started inside
  the window. The same rule, an episode whose opening interval contains the incident start, will apply on the test day
  to every detector.
- **STATIC (R1).** At 99 the detector catches 11/14 but raises 3 false alarms (PIO/PIO_BYTES at 05:11Z and 15:01Z,
  W_USERIO at 19:18Z), so the rule takes 99.5, the FINAL model.
- **Null minutes.** 5 dev minutes inside run 112 (blocking_chain HIGH) are unscored by every candidate (section 3).

### 6.2 Every false-alarm episode on the dev day

| Run tag | Episode | Start | End | Fired (STATIC/SEASONAL) |
|---|---|---|---|---|
| DEVTUNE_MSET_2_5 | 1 | 10-05 01:00:29 | 01:42:30 | |
| DEVTUNE_MSET_2_5 | 4 | 10-05 05:10:31 | 05:23:30 | |
| DEVTUNE_MSET_2_5 | 9 | 10-05 14:00:29 | 14:31:31 | |
| DEVTUNE_MSET_2_5 | 13 | 10-05 18:57:30 | 19:13:30 | |
| DEVTUNE_MSET_2_5 | 14 | 10-05 19:14:30 | 20:04:30 | |
| DEVTUNE_MSET_3_5 | 1 | 10-05 01:03:30 | 01:41:30 | |
| DEVTUNE_SEASONAL_3 | 1 | 10-05 00:29:30 | open at the end of the day | PIO, PIO_BYTES |
| DEVTUNE_SEASONAL_4 | 1 | 10-05 00:41:30 | open at the end of the day | W_CONCUR |
| DEVTUNE_SEASONAL_5 | 1 | 10-05 00:41:30 | open at the end of the day | W_CONCUR |
| DEVTUNE_SEASONAL_6 | 1, 3, 4, 5 | 00:41:30, 16:02:30, 18:02:30, 19:18:31 | 13:10:30, 17:40:31, 18:55:30, open | W_CONCUR; PIO, PIO_BYTES; PIO_BYTES, PIO; W_USERIO |
| DEVTUNE_SEASONAL_8 | 1 | 10-05 00:41:30 | 13:10:30 | W_CONCUR |
| DEVTUNE_STATIC_99 | 4, 10, 13 | 05:11:30, 15:01:30, 19:18:31 | 05:27:30, 15:41:30, 20:01:30 | PIO, PIO_BYTES; PIO_BYTES, PIO; W_USERIO |

All other 25 candidates: 0 false-alarm episodes.

## 7. For phase 6.2 (not done here)

- Score the test day once with exactly the seven models in section 1 (`score_range` for the six in-database ones;
  `tools/iforest.py --contamination 0.02` for D5), then run the official `grade('EVAL_DEV', ...)` and
  `grade('EVAL_TEST', ...)` (contract 13.1).
- First check 6.0's test-day half, as section 3 deferred it.

## Appendix A: the exact queries used

A.1 to A.7 are the scripts as run (as ANOMOPS, through the scratchpad helper anomops_sql.sh, which passes SQL on standard
input to sqlplus in the ora26ai container and never prints the password). A.8 is the IsolationForest command and A.9
the ad-hoc read-only queries run between the scripts.

### A.1 Phase 6.0 checks, dev-day half (run with `set serveroutput on` first; C6's print line was corrected and C6 re-run, see A.9 (3))

```sql
-- v1.0 - phase 6.0 precondition checks (app 900), as ANOMOPS. Read-only. Every time-series query is bounded to
--        ts < EVAL_FROM + 1 day (the test day is never read). 07-Oct-2026.
set linesize 250 pagesize 500 long 20000 longchunksize 20000
col a format a60
col b format a60
col c format a120
prompt == C1 clock and EVAL_TO past
select to_char(PKG_ANOM_LIVE.clock('TRAIN_FROM'),'YYYY-MM-DD HH24:MI') train_from,
       to_char(PKG_ANOM_LIVE.clock('TRAIN_TO'),'YYYY-MM-DD HH24:MI') train_to,
       to_char(PKG_ANOM_LIVE.clock('EVAL_FROM'),'YYYY-MM-DD HH24:MI') eval_from,
       to_char(PKG_ANOM_LIVE.clock('EVAL_FROM') + 1,'YYYY-MM-DD HH24:MI') dev_to,
       to_char(PKG_ANOM_LIVE.clock('EVAL_TO'),'YYYY-MM-DD HH24:MI') eval_to,
       case when PKG_ANOM_LIVE.clock('EVAL_TO') < cast(sys_extract_utc(systimestamp) as date) then 'PAST' else 'NOT PAST' end eval_to_state
  from dual;

prompt == C2 FINAL models: window, rows, exclusion counts, dropped signals
col model_name format a34
select r.model_name, r.status, to_char(r.train_from,'MM-DD HH24:MI') tf, to_char(r.train_to,'MM-DD HH24:MI') tt, r.n_rows,
       json_value(r.signals_json,'$.rows_in_window') in_win,
       json_value(r.signals_json,'$.rows_in_chaos_windows') chaos,
       json_value(r.signals_json,'$.rows_in_exclusion_windows') excl,
       json_value(r.signals_json,'$.rows_with_null') nulls,
       json_value(r.signals_json,'$.rows_trained') trained,
       (select count(*) from json_table(r.signals_json, '$.used[*]' columns (s varchar2(20) path '$'))) n_used,
       json_query(r.signals_json,'$.dropped' returning varchar2(400)) dropped
  from MODEL_REGISTRY r where r.variant = 'FINAL' order by r.detector;
select r.detector, r.settings_json c from MODEL_REGISTRY r where r.variant = 'FINAL' order by r.detector;

prompt == C3 TRAIN_EXCLUDE windows touching the training window, and any created after FINAL_AT
select exclude_id, to_char(from_ts,'MM-DD HH24:MI') f, to_char(to_ts,'MM-DD HH24:MI') t, to_char(created_ts,'MM-DD HH24:MI:SS') created,
       substr(reason,1,80) a
  from TRAIN_EXCLUDE
 where from_ts < PKG_ANOM_LIVE.clock('TRAIN_TO') and to_ts >= PKG_ANOM_LIVE.clock('TRAIN_FROM') order by from_ts;
select count(*) excl_created_after_final_at from TRAIN_EXCLUDE
 where created_ts >= cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp)
   and from_ts < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1;

prompt == C4 chaos runs whose window [start-5, end+30] touches the training window
select run_id, scenario, intensity, source, status, to_char(coalesce(start_ts, requested_ts),'MM-DD HH24:MI:SS') s,
       to_char(end_ts,'MM-DD HH24:MI:SS') e
  from INCIDENT_TRUTH t
 where coalesce(t.start_ts, t.requested_ts) - 5/1440 < PKG_ANOM_LIVE.clock('TRAIN_TO')
   and coalesce(t.end_ts, t.planned_end_ts, t.start_ts) + 30/1440 >= PKG_ANOM_LIVE.clock('TRAIN_FROM')
 order by run_id;

prompt == C5 training rows unchanged since FINAL_AT (last write of any training-window row)
select count(*) metric_rows, to_char(max(collected_ts),'YYYY-MM-DD HH24:MI:SS') last_collected,
       count(case when collected_ts >= cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp) then 1 end) written_after_final_at
  from METRIC_MINUTE where begin_time >= PKG_ANOM_LIVE.clock('TRAIN_FROM') and begin_time < PKG_ANOM_LIVE.clock('TRAIN_TO');
select count(*) app_rows, to_char(max(loaded_ts),'YYYY-MM-DD HH24:MI:SS') last_loaded,
       count(case when loaded_ts >= cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp) then 1 end) written_after_final_at
  from APP_MINUTE where ts_minute >= trunc(PKG_ANOM_LIVE.clock('TRAIN_FROM'),'MI') - 1/1440
                    and ts_minute < PKG_ANOM_LIVE.clock('TRAIN_TO') + 1/1440;

prompt == C6 the FINAL window's training rows now (window_signals + training_query) vs FINAL
declare
  l_used varchar2(4000);
  l_drop varchar2(32767);
  l_n    number;
  l_fin  varchar2(4000);
begin
  PKG_ANOM_TRAIN.window_signals(PKG_ANOM_LIVE.clock('TRAIN_FROM'), PKG_ANOM_LIVE.clock('TRAIN_TO'), l_used, l_drop);
  execute immediate 'select count(*) from (' || PKG_ANOM_TRAIN.training_query(PKG_ANOM_LIVE.clock('TRAIN_FROM'),
                    PKG_ANOM_LIVE.clock('TRAIN_TO'), l_used) || ')' into l_n;
  dbms_output.put_line('now: used='||(regexp_count(l_used, ',') + 1)||' rows='||l_n||' dropped='||l_drop);
  for m in (select model_name from MODEL_REGISTRY where variant = 'FINAL' order by detector) loop
    l_fin := PKG_ANOM_TRAIN.model_signals(m.model_name);
    dbms_output.put_line(rpad(m.model_name, 36)||'same used list as now: '||case when l_fin = l_used then 'YES' else 'NO' end);
  end loop;
end;
/

prompt == C7 INCIDENT_PLAN rows of the dev day (planned_start < EVAL_FROM + 1)
select plan_id, day_no, scenario, intensity, to_char(planned_start,'MM-DD HH24:MI') ps, minutes, status, run_id,
       substr(message,1,60) a
  from INCIDENT_PLAN
 where planned_start >= PKG_ANOM_LIVE.clock('EVAL_FROM') and planned_start < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
 order by planned_start;
select status, count(*) n from INCIDENT_PLAN
 where planned_start >= PKG_ANOM_LIVE.clock('EVAL_FROM') and planned_start < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
 group by status;
select count(*) plan_rows_before_eval_from from INCIDENT_PLAN where planned_start < PKG_ANOM_LIVE.clock('EVAL_FROM');

prompt == C8 INCIDENT_TRUTH (the copy of CHAOS_RUN) runs starting in the dev day, by source, and their match to STARTED plan rows
select t.run_id, t.scenario, t.intensity, t.source, t.status, t.restored,
       to_char(coalesce(t.start_ts, t.requested_ts),'MM-DD HH24:MI:SS') s, to_char(t.end_ts,'MM-DD HH24:MI:SS') e,
       round((t.end_ts - t.start_ts) * 1440, 1) mins, case when t.gone_ts is null then 'N' else 'Y' end gone,
       p.plan_id, p.status plan_status, p.minutes plan_min, to_char(p.planned_start,'HH24:MI') plan_start
  from INCIDENT_TRUTH t
  left join INCIDENT_PLAN p on p.run_id = t.run_id
 where coalesce(t.start_ts, t.requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM')
   and coalesce(t.start_ts, t.requested_ts) < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
 order by t.run_id;
select source, count(*) n from INCIDENT_TRUTH t
 where coalesce(t.start_ts, t.requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM')
   and coalesce(t.start_ts, t.requested_ts) < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 group by source;
-- STARTED plan rows of the dev day without a SCHEDULE run, and SCHEDULE runs without a STARTED plan row
select 'plan STARTED without SCHEDULE run' a, count(*) n from INCIDENT_PLAN p
 where p.planned_start >= PKG_ANOM_LIVE.clock('EVAL_FROM') and p.planned_start < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
   and p.status = 'STARTED'
   and not exists (select 1 from INCIDENT_TRUTH t where t.run_id = p.run_id and t.source = 'SCHEDULE')
union all
select 'SCHEDULE run without STARTED plan row', count(*) from INCIDENT_TRUTH t
 where coalesce(t.start_ts, t.requested_ts) >= PKG_ANOM_LIVE.clock('EVAL_FROM')
   and coalesce(t.start_ts, t.requested_ts) < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 and t.source = 'SCHEDULE'
   and not exists (select 1 from INCIDENT_PLAN p where p.run_id = t.run_id and p.status = 'STARTED');
-- any run of another source whose window reaches into the dev day from before it
select run_id, scenario, source, to_char(coalesce(start_ts, requested_ts),'MM-DD HH24:MI') s, to_char(end_ts,'MM-DD HH24:MI') e
  from INCIDENT_TRUTH t
 where coalesce(t.start_ts, t.requested_ts) < PKG_ANOM_LIVE.clock('EVAL_FROM')
   and coalesce(t.end_ts, t.planned_end_ts, t.start_ts) + 30/1440 >= PKG_ANOM_LIVE.clock('EVAL_FROM') - 10/1440;
col v format a40
select name, to_char(value_ts,'YYYY-MM-DD HH24:MI:SS') ts, value_txt v from ANOM_STATE where name in ('TRUTH_REFRESH','TRUTH_ATTEMPT');

prompt == C9 METRIC_MINUTE / APP_MINUTE / FEATURE_MINUTE completeness over the dev day
with m as (select begin_time, source, lag(begin_time) over (order by begin_time) prev
             from METRIC_MINUTE where begin_time >= PKG_ANOM_LIVE.clock('EVAL_FROM') - 10/1440
                                  and begin_time < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1)
select count(*) metric_rows, count(case when source = 'H' then 1 end) backfilled_h,
       max(round((begin_time - prev) * 86400)) max_step_s,
       count(case when (begin_time - prev) * 86400 > 90 then 1 end) gaps_over_90s,
       to_char(min(begin_time),'MM-DD HH24:MI:SS') first_ts, to_char(max(begin_time),'MM-DD HH24:MI:SS') last_ts
  from m where begin_time >= PKG_ANOM_LIVE.clock('EVAL_FROM');
select count(*) app_rows,
       1440 - count(*) app_minutes_missing,
       count(case when app_tps is null or app_p50_ms is null or app_p95_ms is null or app_err_pct is null then 1 end) app_null_values
  from APP_MINUTE where ts_minute >= PKG_ANOM_LIVE.clock('EVAL_FROM') and ts_minute < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1;
select count(*) feature_rows from FEATURE_MINUTE
 where ts >= PKG_ANOM_LIVE.clock('EVAL_FROM') and ts < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1;

prompt == C10 null-signal minutes per detector (a null in a used signal: not scored, PLAN 7a)
declare
  l_sig  varchar2(4000);
  l_pred varchar2(8000);
  l_n    number;
  l_tot  number;
begin
  for m in (select model_name, detector from MODEL_REGISTRY where variant = 'FINAL' order by detector) loop
    l_sig := PKG_ANOM_TRAIN.model_signals(m.model_name);
    l_pred := null;
    for i in 1 .. regexp_count(l_sig, ',') + 1 loop
      l_pred := l_pred || case when i > 1 then ' or ' end || 'f.' || regexp_substr(l_sig, '[^,]+', 1, i) || ' is null';
    end loop;
    execute immediate 'select count(*), count(case when ' || l_pred || ' then 1 end) from FEATURE_MINUTE f '
                   || 'where f.ts >= :a and f.ts < :b'
      into l_tot, l_n using PKG_ANOM_LIVE.clock('EVAL_FROM'), PKG_ANOM_LIVE.clock('EVAL_FROM') + 1;
    dbms_output.put_line(rpad(m.detector, 10)||'minutes='||l_tot||' null_signal_minutes='||l_n||' signals='
                         ||(regexp_count(l_sig, ',') + 1));
  end loop;
end;
/

prompt == C11 no retraining during the evaluation (MODEL_REGISTRY lifecycle; COLLECT_LOG of the dev day)
select count(*) models_created_after_final_at from MODEL_REGISTRY
 where created_ts >= cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp) + interval '1' minute;
select count(*) status_changes_after_final_activation from MODEL_REGISTRY
 where status_ts >= cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp) + interval '1' minute;
select detector, count(*) active from MODEL_REGISTRY where status = 'ACTIVE' group by detector order by 1;
select count(*) n, substr(message, 1, 24) a from COLLECT_LOG
 where log_ts >= cast(PKG_ANOM_LIVE.clock('FINAL_AT') as timestamp)
   and log_ts < cast(PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 as timestamp)
   and (message like 'event=model_trained%' or message like 'event=model_activated%' or message like 'event=final_trained%'
        or message like 'event=interim_trained%' or message like 'event=train_failed%')
 group by substr(message, 1, 24);
select severity, count(*) n from COLLECT_LOG
 where log_ts >= cast(PKG_ANOM_LIVE.clock('EVAL_FROM') as timestamp)
   and log_ts < cast(PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 as timestamp) group by severity;
```

### A.2 Training the 24 TUNE variants

```sql
-- v1.0 - phase 6.1 (app 900): trains the dev-tuning variants as ANOMOPS. For every in-database detector and every
--        PLAN 7a value except step 4 (the default, which IS the FINAL model: trained on these rows with that value),
--        PKG_ANOM_TRAIN.train(det, TRAIN_FROM, TRAIN_TO, 'TUNE_<value>', TUNING_GRID settings, activate 'N') on the FINAL
--        window, status CANDIDATE. One INCIDENT_TRUTH refresh for the batch (batch_truth_begin: one read-only read of
--        CHAOS_RUN over ANOM_CHAOS_LINK, as the trainer always does). Never touches a FINAL model. 07-Oct-2026.
--        <value>: the grid value with '.' written '_' (a variant allows only A-Z, 0-9, _); MSET: COUNT_WINDOW.
set serveroutput on size unlimited
set linesize 250
whenever sqlerror exit failure rollback
declare
  type t_row is record (det varchar2(10), step pls_integer, val varchar2(20));
  type t_rows is table of t_row;
  l_rows t_rows := t_rows(
    t_row('MSET', 1, '6_10'),   t_row('MSET', 2, '5_8'),     t_row('MSET', 3, '4_6'),     t_row('MSET', 5, '2_5'),
    t_row('SVM', 1, '0_001'),   t_row('SVM', 2, '0_0025'),   t_row('SVM', 3, '0_005'),    t_row('SVM', 5, '0_02'),
    t_row('EM', 1, '0_001'),    t_row('EM', 2, '0_0025'),    t_row('EM', 3, '0_005'),     t_row('EM', 5, '0_02'),
    t_row('PCA', 1, '99_95'),   t_row('PCA', 2, '99_9'),     t_row('PCA', 3, '99_8'),     t_row('PCA', 5, '99'),
    t_row('STATIC', 1, '99_95'),t_row('STATIC', 2, '99_9'),  t_row('STATIC', 3, '99_8'),  t_row('STATIC', 5, '99'),
    t_row('SEASONAL', 1, '8'),  t_row('SEASONAL', 2, '6'),   t_row('SEASONAL', 3, '5'),   t_row('SEASONAL', 5, '3'));
  l_from   date := PKG_ANOM_LIVE.clock('TRAIN_FROM');
  l_to     date := PKG_ANOM_LIVE.clock('TRAIN_TO');
  l_js     varchar2(400);
  l_name   varchar2(128);
  l_failed varchar2(4000);
  l_chk    varchar2(40);
  n        number;
begin
  -- guard: no TUNE model exists yet (a re-run must not duplicate; check MODEL_REGISTRY first)
  select count(*) into n from MODEL_REGISTRY where variant like 'TUNE\_%' escape '\';
  if n > 0 then
    raise_application_error(-20999, 'p6_61_train: '||n||' TUNE model(s) already registered; not training again');
  end if;
  PKG_ANOM_TRAIN.batch_truth_begin;
  for i in 1 .. l_rows.count loop
    l_js := PKG_ANOM_LIVE.grid_settings(l_rows(i).det, l_rows(i).step);
    -- the name's value must be the grid's value (no hand-typed setting reaches a model)
    l_chk := case l_rows(i).det
               when 'MSET' then json_value(l_js, '$.MSET_ALERT_COUNT') || '_' || json_value(l_js, '$.MSET_ALERT_WINDOW')
               else replace(to_char(json_value(l_js, case l_rows(i).det when 'SVM' then '$.SVMS_OUTLIER_RATE'
                                                   when 'EM' then '$.EMCS_OUTLIER_RATE' when 'PCA' then '$.RESID_PCT'
                                                   when 'STATIC' then '$.PCT' when 'SEASONAL' then '$.K' end
                                                   returning number), 'FM9990.99999'), '.', '_') end;
    l_chk := rtrim(l_chk, '_');
    if l_chk != l_rows(i).val then
      raise_application_error(-20999, 'p6_61_train: '||l_rows(i).det||' step '||l_rows(i).step||' grid '||l_js
                                      ||' does not match the name value '||l_rows(i).val||' ('||l_chk||')');
    end if;
    begin
      l_name := PKG_ANOM_TRAIN.train(l_rows(i).det, l_from, l_to, 'TUNE_' || l_rows(i).val, json(l_js), 'N');
      dbms_output.put_line(rpad(l_rows(i).det, 9)||' step '||l_rows(i).step||' '||rpad(l_js, 46)||' -> '||l_name);
    exception
      when others then
        l_failed := l_failed || case when l_failed is not null then '; ' end
                    || l_rows(i).det || ' step ' || l_rows(i).step || ': ' || sqlerrm;
        dbms_output.put_line(rpad(l_rows(i).det, 9)||' step '||l_rows(i).step||' FAILED: '||sqlerrm);
    end;
  end loop;
  PKG_ANOM_TRAIN.batch_truth_end;
  if l_failed is not null then
    raise_application_error(-20999, 'p6_61_train: '||substr(l_failed, 1, 3000));
  end if;
end;
/
```

### A.3 Verifying the variants against FINAL

```sql
-- v1.0 - phase 6.1 (app 900): checks that every TUNE variant learned from exactly the FINAL training rows and that the
--        threshold variants (PCA, STATIC, SEASONAL) carry the FINAL model's statistics. Read-only, as ANOMOPS. 07-Oct-2026.
set serveroutput on size unlimited
set linesize 250 pagesize 500
col model_name format a36
col fin format a32
prompt == V1 every TUNE model against its detector's FINAL model: window, rows, row accounting, signal lists, status
select t.model_name, t.status,
       case when t.train_from = f.train_from and t.train_to = f.train_to then 'Y' else 'N' end same_window,
       t.n_rows,
       case when json_value(t.signals_json, '$.rows_in_window') = json_value(f.signals_json, '$.rows_in_window')
             and json_value(t.signals_json, '$.rows_in_chaos_windows') = json_value(f.signals_json, '$.rows_in_chaos_windows')
             and json_value(t.signals_json, '$.rows_in_exclusion_windows') = json_value(f.signals_json, '$.rows_in_exclusion_windows')
             and json_value(t.signals_json, '$.rows_with_null') = json_value(f.signals_json, '$.rows_with_null')
             and json_value(t.signals_json, '$.rows_trained') = json_value(f.signals_json, '$.rows_trained')
            then 'Y' else 'N' end same_row_accounting,
       case when PKG_ANOM_TRAIN.model_signals(t.model_name) = PKG_ANOM_TRAIN.model_signals(f.model_name)
             and json_query(t.signals_json, '$.dropped' returning varchar2(400)) = json_query(f.signals_json, '$.dropped' returning varchar2(400))
            then 'Y' else 'N' end same_signals
  from MODEL_REGISTRY t
  join MODEL_REGISTRY f on f.detector = t.detector and f.variant = 'FINAL'
 where t.variant like 'TUNE\_%' escape '\'
 order by t.detector, t.model_name;

prompt == V2 the knob as stored in each candidate (Oracle's stored settings / this package's knobs)
col knob format a90
select r.detector, r.variant,
       case r.detector
         when 'MSET' then 'MSET_ALERT_COUNT='||json_value(r.settings_json,'$.MSET_ALERT_COUNT')||' MSET_ALERT_WINDOW='||json_value(r.settings_json,'$.MSET_ALERT_WINDOW')
                          ||' MSET_ALPHA_PROB='||json_value(r.settings_json,'$.MSET_ALPHA_PROB')
         when 'SVM' then 'SVMS_OUTLIER_RATE='||json_value(r.settings_json,'$.SVMS_OUTLIER_RATE')
         when 'EM' then 'EMCS_OUTLIER_RATE='||json_value(r.settings_json,'$.EMCS_OUTLIER_RATE')
         when 'PCA' then 'RESID_PCT='||json_value(r.settings_json,'$.RESID_PCT')||' K='||json_value(r.settings_json,'$.K')
                         ||' THRESHOLD='||json_value(r.settings_json,'$.THRESHOLD')||' TRAIN_ERR_MEDIAN='||json_value(r.settings_json,'$.TRAIN_ERR_MEDIAN')
         when 'STATIC' then 'PCT='||json_value(r.settings_json,'$.PCT')
         when 'SEASONAL' then 'K='||json_value(r.settings_json,'$.K')||' MIN_ROWS_HOUR='||json_value(r.settings_json,'$.MIN_ROWS_HOUR')
       end knob, r.train_seconds
  from MODEL_REGISTRY r
 where (r.variant like 'TUNE\_%' escape '\' or r.variant = 'FINAL')
 order by r.detector, r.variant;

prompt == V3 SEASONAL: each TUNE model's SEASONAL_BASE against FINAL's (rows only in one of the two; 0 = identical)
select t.model_name, count(*) baseline_rows,
       (select count(*) from (select signal_code, hour_utc, med, mad, n from SEASONAL_BASE where model_name = t.model_name
                              minus
                              select signal_code, hour_utc, med, mad, n from SEASONAL_BASE where model_name = f.model_name)) only_tune,
       (select count(*) from (select signal_code, hour_utc, med, mad, n from SEASONAL_BASE where model_name = f.model_name
                              minus
                              select signal_code, hour_utc, med, mad, n from SEASONAL_BASE where model_name = t.model_name)) only_final
  from MODEL_REGISTRY t
  join MODEL_REGISTRY f on f.detector = 'SEASONAL' and f.variant = 'FINAL'
  join SEASONAL_BASE b on b.model_name = t.model_name
 where t.detector = 'SEASONAL' and t.variant like 'TUNE\_%' escape '\'
 group by t.model_name, f.model_name order by t.model_name;

prompt == V4 PCA: z-scaling (MODEL_SIGNAL_STAT) and K against FINAL; each TUNE threshold against the same percentile of FINAL's own training errors
select t.model_name,
       (select count(*) from (select signal_code, mu, sd, n from MODEL_SIGNAL_STAT where model_name = t.model_name
                              minus
                              select signal_code, mu, sd, n from MODEL_SIGNAL_STAT where model_name = f.model_name)) zscale_diff_rows,
       case when json_value(t.settings_json,'$.K') = json_value(f.settings_json,'$.K') then 'Y' else 'N' end same_k
  from MODEL_REGISTRY t join MODEL_REGISTRY f on f.detector = 'PCA' and f.variant = 'FINAL'
 where t.detector = 'PCA' and t.variant like 'TUNE\_%' escape '\' order by t.model_name;
declare
  l_fin  varchar2(128);
  l_k    pls_integer;
  l_q    varchar2(32767);
  l_thr  number;
  l_med  number;
  l_fthr number;
begin
  select model_name, json_value(settings_json, '$.K' returning number), json_value(settings_json, '$.THRESHOLD' returning number)
    into l_fin, l_k, l_fthr from MODEL_REGISTRY where detector = 'PCA' and variant = 'FINAL';
  l_q := PKG_ANOM_TRAIN.training_query(PKG_ANOM_LIVE.clock('TRAIN_FROM'), PKG_ANOM_LIVE.clock('TRAIN_TO'),
                                       PKG_ANOM_TRAIN.model_signals(l_fin));
  -- FINAL's own threshold, recomputed: the check that this query reproduces FINAL's statistic
  execute immediate 'select percentile_cont(:p) within group (order by err), median(err) from ('
                 || 'select ts, sum(resid * resid) err from (' || PKG_ANOM_TRAIN.pca_resid_sql(l_fin, l_k, l_q) || ') group by ts)'
    into l_thr, l_med using 0.995;
  dbms_output.put_line('FINAL '||l_fin||' stored THRESHOLD='||l_fthr||' recomputed='||l_thr||' rel_diff='
                       ||to_char(abs(l_thr - l_fthr) / l_fthr, 'FM0.0000000000EEEE'));
  for t in (select model_name, json_value(settings_json, '$.RESID_PCT' returning number) p,
                   json_value(settings_json, '$.THRESHOLD' returning number) thr
              from MODEL_REGISTRY where detector = 'PCA' and variant like 'TUNE\_%' escape '\' order by p desc) loop
    execute immediate 'select percentile_cont(:p) within group (order by err) from ('
                   || 'select ts, sum(resid * resid) err from (' || PKG_ANOM_TRAIN.pca_resid_sql(l_fin, l_k, l_q) || ') group by ts)'
      into l_thr using t.p / 100;
    dbms_output.put_line(rpad(t.model_name, 34)||' RESID_PCT='||rpad(t.p, 6)||' own THRESHOLD='||rpad(round(t.thr, 6), 12)
                         ||' FINAL-model errors at that percentile='||rpad(round(l_thr, 6), 12)
                         ||' rel_diff='||to_char(abs(l_thr - t.thr) / t.thr, 'FM0.0000000000EEEE'));
  end loop;
end;
/

prompt == V5 STATIC: thresholds per signal for every percentile (FINAL = 99.5); monotone check
select f.signal_code,
       (select hi from THRESHOLD_DEF d join MODEL_REGISTRY r on r.model_name = d.model_name
         where r.detector = 'STATIC' and r.variant = 'TUNE_99_95' and d.signal_code = f.signal_code) hi_99_95,
       (select hi from THRESHOLD_DEF d join MODEL_REGISTRY r on r.model_name = d.model_name
         where r.detector = 'STATIC' and r.variant = 'TUNE_99_9' and d.signal_code = f.signal_code) hi_99_9,
       (select hi from THRESHOLD_DEF d join MODEL_REGISTRY r on r.model_name = d.model_name
         where r.detector = 'STATIC' and r.variant = 'TUNE_99_8' and d.signal_code = f.signal_code) hi_99_8,
       f.hi hi_99_5_final,
       (select hi from THRESHOLD_DEF d join MODEL_REGISTRY r on r.model_name = d.model_name
         where r.detector = 'STATIC' and r.variant = 'TUNE_99' and d.signal_code = f.signal_code) hi_99
  from THRESHOLD_DEF f join MODEL_REGISTRY fr on fr.model_name = f.model_name
 where fr.detector = 'STATIC' and fr.variant = 'FINAL' order by f.signal_code;
select count(*) non_monotone_signals from (
  select f.signal_code, max(case r.variant when 'TUNE_99_95' then d.hi end) a, max(case r.variant when 'TUNE_99_9' then d.hi end) b,
         max(case r.variant when 'TUNE_99_8' then d.hi end) c, max(case r.variant when 'FINAL' then d.hi end) e,
         max(case r.variant when 'TUNE_99' then d.hi end) g
    from THRESHOLD_DEF d join MODEL_REGISTRY r on r.model_name = d.model_name
    join THRESHOLD_DEF f on f.signal_code = d.signal_code
    join MODEL_REGISTRY fr on fr.model_name = f.model_name and fr.detector = 'STATIC' and fr.variant = 'FINAL'
   where r.detector = 'STATIC' group by f.signal_code)
 where not (a >= b and b >= c and c >= e and e >= g);
```

### A.4 Scoring the dev day (run once per detector with `define det=STATIC`, `SEASONAL`, `PCA`, `SVM`, `EM`, `MSET` first)

```sql
-- v1.0 - phase 6.1 (app 900): scores the dev day EVAL_DEV = [EVAL_FROM, EVAL_FROM + 1 day) for every candidate of one
--        detector (define det first) with PKG_ANOM_SCORE.score_range into SCORE_EVAL (never SCORE_MINUTE), run tag
--        DEVTUNE_<DET>_<value>. Candidates: the four TUNE_<value> models and, for step 4 (the default), the FINAL model.
--        The range end is fixed at EVAL_FROM + 1 and checked: nothing of the test day is scored. 07-Oct-2026.
set serveroutput on size unlimited
set linesize 250
whenever sqlerror exit failure rollback
declare
  l_from date := PKG_ANOM_LIVE.clock('EVAL_FROM');
  l_to   date := PKG_ANOM_LIVE.clock('EVAL_FROM') + 1;
  l_tag  varchar2(40);
  l_t0   timestamp;
  l_n    number;
  l_f    number;
  l_u    number;
begin
  if l_to != PKG_ANOM_LIVE.clock('EVAL_TO') - 1 then
    raise_application_error(-20999, 'p6_61_score: the dev day does not end one day before EVAL_TO');
  end if;
  for m in (select r.model_name, r.detector, r.variant,
                   'DEVTUNE_' || r.detector || '_' ||
                   case when r.variant = 'FINAL' then
                     case r.detector when 'MSET' then '3_5' when 'SVM' then '0_01' when 'EM' then '0_01'
                                     when 'PCA' then '99_5' when 'STATIC' then '99_5' when 'SEASONAL' then '4' end
                   else substr(r.variant, 6) end tag
              from MODEL_REGISTRY r
             where r.detector = upper('&det')
               and (r.variant like 'TUNE\_%' escape '\' or (r.variant = 'FINAL' and r.status = 'ACTIVE'))
             order by r.variant) loop
    -- step 4's tag must name FINAL's own setting (checked against TUNING_GRID's default row)
    if m.variant = 'FINAL' then
      select count(*) into l_n from TUNING_GRID g where g.detector = m.detector and g.is_default = 'Y' and g.step = 4;
      if l_n != 1 then
        raise_application_error(-20999, 'p6_61_score: no default grid row for '||m.detector);
      end if;
    end if;
    l_tag := m.tag;
    l_t0 := systimestamp;
    PKG_ANOM_SCORE.score_range(m.model_name, l_from, l_to, l_tag);
    select count(*), count(case when flag = 1 then 1 end), count(case when flag is null then 1 end), max(ts)
      into l_n, l_f, l_u, l_to
      from SCORE_EVAL where run_tag = l_tag and model_name = m.model_name;
    if l_to >= PKG_ANOM_LIVE.clock('EVAL_FROM') + 1 then
      raise_application_error(-20999, 'p6_61_score: a scored minute lies outside the dev day');
    end if;
    l_to := PKG_ANOM_LIVE.clock('EVAL_FROM') + 1;
    dbms_output.put_line(rpad(l_tag, 26)||rpad(m.model_name, 40)||' minutes='||l_n||' flagged='||l_f||' unscored='||l_u
                         ||' seconds='||round(extract(minute from (systimestamp - l_t0)) * 60
                                              + extract(second from (systimestamp - l_t0)), 1));
  end loop;
end;
/
```

### A.5 Grading every DEVTUNE tag on the dev day

```sql
-- v1.0 - phase 6.1 (app 900): grades every DEVTUNE_<DET>_<value> run tag on the dev day only, as ANOMOPS:
--        PKG_ANOM_GRADE.grade(tag, EVAL_FROM, EVAL_FROM + 1, 'SCHEDULE', p_refresh => 'N', p_commit => 'Y').
--        p_refresh = 'N': INCIDENT_TRUTH is the truth loop's copy (refreshed every 5 min; all runs DONE), so grading makes
--        no link call. Writes only ALERT_EVAL / GRADE_INCIDENT / GRADE_RESULT rows of the DEVTUNE tags. 07-Oct-2026.
set serveroutput on size unlimited
set linesize 250
whenever sqlerror exit failure rollback
declare
  l_from date := PKG_ANOM_LIVE.clock('EVAL_FROM');
  l_to   date := PKG_ANOM_LIVE.clock('EVAL_FROM') + 1;
  n      number;
begin
  for t in (select run_tag, count(distinct model_name) models, max(ts) last_ts
              from SCORE_EVAL where run_tag like 'DEVTUNE\_%' escape '\' group by run_tag order by run_tag) loop
    if t.models != 1 or t.last_ts >= l_to then
      raise_application_error(-20999, 'p6_61_grade: tag '||t.run_tag||' has '||t.models||' models or a minute past the dev day');
    end if;
    PKG_ANOM_GRADE.grade(t.run_tag, l_from, l_to, 'SCHEDULE', 'N', 'Y');
    select count(*) into n from GRADE_RESULT where run_tag = t.run_tag;
    dbms_output.put_line(rpad(t.run_tag, 26)||' graded, result rows='||n);
  end loop;
end;
/
```

### A.6 The results and the choice (Q3 is the rule; written before any graded number was read)

```sql
-- v1.0 - phase 6.1 (app 900): the dev-tuning table and the choice, as ANOMOPS. Read-only. Reads only the DEVTUNE run
--        tags (all graded on [EVAL_FROM, EVAL_FROM + 1 day)) and TUNING_GRID. 07-Oct-2026.
set linesize 300 pagesize 500
col tag format a24
col model_name format a36
col settings format a44
col missed format a120
-- Q1: one row per candidate (detector x PLAN 7a step); step 1 = least sensitive, 5 = most; step 4 = default (FINAL)
with g as (
  select detector, step, is_default, settings_json,
         'DEVTUNE_' || detector || '_' ||
         case detector
           when 'MSET' then json_value(settings_json, '$.MSET_ALERT_COUNT') || '_' || json_value(settings_json, '$.MSET_ALERT_WINDOW')
           else rtrim(replace(to_char(coalesce(json_value(settings_json, '$.SVMS_OUTLIER_RATE' returning number),
                                               json_value(settings_json, '$.EMCS_OUTLIER_RATE' returning number),
                                               json_value(settings_json, '$.RESID_PCT' returning number),
                                               json_value(settings_json, '$.PCT' returning number),
                                               json_value(settings_json, '$.K' returning number),
                                               json_value(settings_json, '$.contamination' returning number)),
                                      'FM9990.99999'), '.', '_'), '_')
         end tag
    from TUNING_GRID)
select g.detector, g.step, g.settings_json settings, g.tag, r.model_name,
       r.n_caught || '/' || r.n_incidents caught,
       (select count(case when i.caught = 'Y' then 1 end) || '/' || count(*) from GRADE_INCIDENT i
         where i.run_tag = g.tag and i.intensity = 'LOW') low,
       (select count(case when i.caught = 'Y' then 1 end) || '/' || count(*) from GRADE_INCIDENT i
         where i.run_tag = g.tag and i.intensity = 'HIGH') high,
       round(r.ttd_median, 2) ttd_med, round(r.ttd_p90, 2) ttd_p90,
       r.n_false_alarms fa, r.fa_per_24h fa_24h,
       (select count(*) from ALERT_EVAL a where a.run_tag = g.tag) episodes,
       (select count(case when s.flag = 1 then 1 end) from SCORE_EVAL s where s.run_tag = g.tag) flagged_min,
       (select count(case when s.flag is null then 1 end) from SCORE_EVAL s where s.run_tag = g.tag) unscored_min,
       round(r.attr_p_mean, 3) attr_p, r.n_attr_hit || '/' || r.n_attr_scored lenient, r.tie_size_median tie_med,
       case when r.fa_per_24h <= 2 then 'Y' else 'N' end meets_limit
  from g left join GRADE_RESULT r on r.run_tag = g.tag
 order by g.detector, g.step;

-- Q2: the incidents each candidate missed (run id, scenario, intensity)
select g.run_tag tag,
       listagg(i.run_id || ' ' || i.scenario || ' ' || i.intensity, '; ') within group (order by i.run_id) missed
  from (select distinct run_tag from GRADE_RESULT where run_tag like 'DEVTUNE\_%' escape '\') g
  left join GRADE_INCIDENT i on i.run_tag = g.run_tag and i.caught = 'N'
 group by g.run_tag order by g.run_tag;

-- Q3: the choice rule (PLAN 7 / phase6.md 6.1): per detector, the most sensitive value (highest PLAN 7a step) with at
--     most 2 false-alarm episodes per 24 h; if none, the least sensitive (step 1). Ties (two admissible values of one
--     step) cannot occur, because the pre-registered order is strict; the median-time-to-detect tie-break is then idle.
with g as (
  select detector, step, settings_json,
         'DEVTUNE_' || detector || '_' ||
         case detector
           when 'MSET' then json_value(settings_json, '$.MSET_ALERT_COUNT') || '_' || json_value(settings_json, '$.MSET_ALERT_WINDOW')
           else rtrim(replace(to_char(coalesce(json_value(settings_json, '$.SVMS_OUTLIER_RATE' returning number),
                                               json_value(settings_json, '$.EMCS_OUTLIER_RATE' returning number),
                                               json_value(settings_json, '$.RESID_PCT' returning number),
                                               json_value(settings_json, '$.PCT' returning number),
                                               json_value(settings_json, '$.K' returning number),
                                               json_value(settings_json, '$.contamination' returning number)),
                                      'FM9990.99999'), '.', '_'), '_')
         end tag
    from TUNING_GRID),
c as (select g.detector, g.step, g.settings_json, g.tag, r.model_name, r.fa_per_24h, r.n_caught, r.ttd_median
        from g join GRADE_RESULT r on r.run_tag = g.tag)
select detector,
       count(*) candidates,
       count(case when fa_per_24h <= 2 then 1 end) meeting_limit,
       nvl(max(case when fa_per_24h <= 2 then step end), 1) chosen_step,
       case when max(case when fa_per_24h <= 2 then step end) is null then 'NO VALUE MEETS THE LIMIT: least sensitive taken'
            else 'most sensitive value meeting the limit' end how,
       max(settings_json) keep (dense_rank first order by case when step = nvl((select max(c2.step) from c c2
                                where c2.detector = c.detector and c2.fa_per_24h <= 2), 1) then 0 else 1 end) chosen_settings,
       max(model_name) keep (dense_rank first order by case when step = nvl((select max(c2.step) from c c2
                                where c2.detector = c.detector and c2.fa_per_24h <= 2), 1) then 0 else 1 end) chosen_model
  from c group by detector order by detector;
```

### A.7 Supporting facts (false alarms, SEASONAL episodes, FINAL registry rows, isolation checks) and the table generator of section 6

```sql
-- v1.0 - phase 6.1 (app 900): supporting facts for dev_tuning.md, as ANOMOPS. Read-only; DEVTUNE tags (dev day) only. 07-Oct-2026.
set linesize 250 pagesize 500
col tag format a24
col fired format a40
prompt == X1 every false-alarm episode of the dev-tuning candidates (grade()'s predicate: start outside every run's [start, end + 30 min])
select a.run_tag tag, a.episode_no, to_char(a.start_ts,'MM-DD HH24:MI:SS') start_ts, to_char(a.end_ts,'MM-DD HH24:MI:SS') end_ts,
       a.status, round((nvl(a.end_ts, a.last_ts) - a.start_ts) * 1440) minutes, a.fired
  from ALERT_EVAL a
 where a.run_tag like 'DEVTUNE\_%' escape '\'
   and a.start_ts >= PKG_ANOM_LIVE.clock('EVAL_FROM') and a.start_ts < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
   and not exists (select 1 from INCIDENT_TRUTH t
                    where t.gone_ts is null
                      and a.start_ts >= coalesce(t.start_ts, t.requested_ts)
                      and a.start_ts <= coalesce(t.end_ts, t.planned_end_ts, t.start_ts, t.requested_ts) + 30 / 1440)
 order by a.run_tag, a.start_ts;
prompt == X2 SEASONAL: every episode of every candidate
select a.run_tag tag, a.episode_no, to_char(a.start_ts,'MM-DD HH24:MI:SS') start_ts, to_char(a.last_ts,'MM-DD HH24:MI:SS') last_ts,
       to_char(a.end_ts,'MM-DD HH24:MI:SS') end_ts, a.status, a.fired
  from ALERT_EVAL a where a.run_tag like 'DEVTUNE\_SEASONAL\_%' escape '\' order by a.run_tag, a.episode_no;
prompt == X3 SEASONAL FINAL (K = 4): how often each signal breaches in a flagged dev minute (top 8)
select sig, count(*) minutes from (
  select regexp_substr(s.breached, '[^,]+', 1, level) sig, s.ts
    from (select ts, breached from SCORE_EVAL where run_tag = 'DEVTUNE_SEASONAL_4' and flag = 1) s
  connect by level <= regexp_count(s.breached, ',') + 1 and prior s.ts = s.ts and prior sys_guid() is not null)
 group by sig order by 2 desc fetch first 8 rows only;
prompt == X4 FINAL models' registry rows after dev scoring (score_range records score_ms_per_min; nothing else changes)
col model_name format a34
select model_name, status, to_char(status_ts,'MM-DD HH24:MI:SS') status_ts, n_rows, score_ms_per_min from MODEL_REGISTRY where variant = 'FINAL' order by detector;
prompt == X5 SCORE_EVAL / ALERT_EVAL rows written under DEVTUNE tags, and their time span (all inside the dev day)
select count(*) rows_, count(distinct run_tag) tags, to_char(min(ts),'MM-DD HH24:MI:SS') first_ts, to_char(max(ts),'MM-DD HH24:MI:SS') last_ts
  from SCORE_EVAL where run_tag like 'DEVTUNE\_%' escape '\';
select count(*) episodes, to_char(min(start_ts),'MM-DD HH24:MI:SS') first_start, to_char(max(nvl(end_ts, last_ts)),'MM-DD HH24:MI:SS') last_minute
  from ALERT_EVAL where run_tag like 'DEVTUNE\_%' escape '\';
select count(*) official_tag_rows from GRADE_RESULT where run_tag in ('EVAL_DEV','EVAL_TEST');
select count(*) score_minute_rows_of_tune_models from SCORE_MINUTE where model_name like 'ANOM\_%\_TUNE\_%' escape '\';
```

### A.7 (cont.) The table generator

```sql
-- v1.0 - phase 6.1 (app 900): prints the per-detector Markdown tables of dev_tuning.md from GRADE_RESULT /
--        GRADE_INCIDENT / ALERT_EVAL / SCORE_EVAL of the DEVTUNE tags (dev day only), as ANOMOPS. Read-only. 07-Oct-2026.
set linesize 600 pagesize 0 heading off trimspool on long 4000 recsep off
with g as (
  select detector, step, is_default, settings_json,
         'DEVTUNE_' || detector || '_' ||
         case detector
           when 'MSET' then json_value(settings_json, '$.MSET_ALERT_COUNT') || '_' || json_value(settings_json, '$.MSET_ALERT_WINDOW')
           else rtrim(replace(to_char(coalesce(json_value(settings_json, '$.SVMS_OUTLIER_RATE' returning number),
                                               json_value(settings_json, '$.EMCS_OUTLIER_RATE' returning number),
                                               json_value(settings_json, '$.RESID_PCT' returning number),
                                               json_value(settings_json, '$.PCT' returning number),
                                               json_value(settings_json, '$.K' returning number),
                                               json_value(settings_json, '$.contamination' returning number)),
                                      'FM9990.99999'), '.', '_'), '_')
         end tag
    from TUNING_GRID),
c as (
  select g.*, r.model_name, r.n_caught, r.n_incidents, r.ttd_median, r.ttd_p90, r.n_false_alarms, r.fa_per_24h,
         r.attr_p_mean, r.n_attr_hit, r.n_attr_scored, r.tie_size_median,
         nvl((select max(c2.step) from TUNING_GRID c2 join GRADE_RESULT r2 on r2.run_tag = 'DEVTUNE_' || c2.detector || '_' ||
                case c2.detector
                  when 'MSET' then json_value(c2.settings_json, '$.MSET_ALERT_COUNT') || '_' || json_value(c2.settings_json, '$.MSET_ALERT_WINDOW')
                  else rtrim(replace(to_char(coalesce(json_value(c2.settings_json, '$.SVMS_OUTLIER_RATE' returning number),
                                                      json_value(c2.settings_json, '$.EMCS_OUTLIER_RATE' returning number),
                                                      json_value(c2.settings_json, '$.RESID_PCT' returning number),
                                                      json_value(c2.settings_json, '$.PCT' returning number),
                                                      json_value(c2.settings_json, '$.K' returning number),
                                                      json_value(c2.settings_json, '$.contamination' returning number)),
                                             'FM9990.99999'), '.', '_'), '_') end
               where c2.detector = g.detector and r2.fa_per_24h <= 2), 1) chosen_step
    from g join GRADE_RESULT r on r.run_tag = g.tag)
select line from (
  select detector, 0 ord, 0 step,
         chr(10) || '### ' || detector || chr(10) || chr(10)
         || '| Step | Value (TUNING_GRID) | Model scored | Run tag | Caught | LOW | HIGH | TTD median (min) | TTD p90 (min) '
         || '| False alarms (= per 24 h) | Episodes | Flagged min | Unscored min | ATTR_P mean | Lenient hits | Tie median | <= 2 FA/24 h | Chosen |'
         || chr(10) || '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|' line
    from c where step = 1
  union all
  select detector, 1, step,
         '| ' || step || case when is_default = 'Y' then ' (default)' end
         || ' | `' || settings_json || '` | ' || model_name || ' | ' || tag
         || ' | ' || n_caught || '/' || n_incidents
         || ' | ' || (select count(case when i.caught = 'Y' then 1 end) || '/' || count(*) from GRADE_INCIDENT i where i.run_tag = c.tag and i.intensity = 'LOW')
         || ' | ' || (select count(case when i.caught = 'Y' then 1 end) || '/' || count(*) from GRADE_INCIDENT i where i.run_tag = c.tag and i.intensity = 'HIGH')
         || ' | ' || nvl(to_char(ttd_median, 'FM990.00'), '-') || ' | ' || nvl(to_char(ttd_p90, 'FM990.00'), '-')
         || ' | ' || n_false_alarms || ' | ' || (select count(*) from ALERT_EVAL a where a.run_tag = c.tag)
         || ' | ' || (select count(case when s.flag = 1 then 1 end) from SCORE_EVAL s where s.run_tag = c.tag)
         || ' | ' || (select count(case when s.flag is null then 1 end) from SCORE_EVAL s where s.run_tag = c.tag)
         || ' | ' || case when detector = 'IFOREST' then 'n/a' else nvl(to_char(attr_p_mean, 'FM0.000'), '-') end
         || ' | ' || case when detector = 'IFOREST' then 'n/a' else n_attr_hit || '/' || n_attr_scored end
         || ' | ' || case when detector = 'IFOREST' then 'n/a' else nvl(to_char(tie_size_median, 'FM990.0'), '-') end
         || ' | ' || case when fa_per_24h <= 2 then 'yes' else 'no' end
         || ' | ' || case when step = chosen_step then '**CHOSEN**' else '' end || ' |'
    from c
  union all
  select detector, 2, step,
         case when step = 1 then chr(10) end
         || '- Step ' || step || ' missed: ' || nvl((select listagg(i.run_id || ' ' || i.scenario || ' ' || i.intensity, '; ')
                                           within group (order by i.run_id) from GRADE_INCIDENT i
                                     where i.run_tag = c.tag and i.caught = 'N'), 'none')
    from c
) order by detector, ord, step;
```

### A.8 IsolationForest

```bash
# v1.0 - phase 6.1 (app 900): the IsolationForest runs as made on the demo host (over ssh), 07-Oct-2026 ~00:41Z.
cd ~/app900
for c in 0.001 0.0025 0.005 0.01 0.02; do
  v=$(echo "$c" | tr . _)
  log=~/app900/logs/p6_devtune_iforest_$v.log
  ~/anomaly/venv-ml/bin/python tools/iforest.py --train-from 2026-10-01T23:57Z --train-to 2026-10-04T23:57Z \
      --score-from 2026-10-05T00:27Z --score-to 2026-10-06T00:27Z --run-tag DEVTUNE_IFOREST_$v --contamination $c > "$log" 2>&1
  echo "contamination=$c rc=$?"; tail -n 1 "$log"
done
# then a secret scan of the five logs on the demo host (python: every value of the secrets file, at least 4 characters,
# counted in every log; only the counts are printed): values_checked=6 files=5 hits=0
```

### A.9 Ad-hoc read-only queries

```sql
-- v1.0 - phase 6.0/6.1 (app 900): the ad-hoc read-only queries run as ANOMOPS besides the scripts, in the order run.
--        Bounded like the scripts (nothing of the test day). 07-Oct-2026.
-- (1) clock, FINAL / ACTIVE models, registry counts (00:32:59Z)
select name, to_char(value_ts,'YYYY-MM-DD HH24:MI:SS') v from ANOM_STATE where name like 'CLOCK%' order by value_ts;
select to_char(cast(sys_extract_utc(systimestamp) as date),'YYYY-MM-DD HH24:MI:SS') now_utc,
       case when PKG_ANOM_LIVE.clock('EVAL_TO') < cast(sys_extract_utc(systimestamp) as date) then 'PAST' else 'NOT PAST' end eval_to_state from dual;
select model_name, detector, variant, status st, to_char(train_from,'MM-DD HH24:MI') tf, to_char(train_to,'MM-DD HH24:MI') tt, n_rows,
       to_char(created_ts,'MM-DD HH24:MI:SS') created, to_char(status_ts,'MM-DD HH24:MI:SS') status_ts, train_seconds, score_ms_per_min
  from MODEL_REGISTRY where variant = 'FINAL' or status = 'ACTIVE' order by detector, created_ts;
select status, count(*) from MODEL_REGISTRY group by status;
select detector, variant, status, count(*) n from MODEL_REGISTRY group by detector, variant, status order by 1,2,3;
-- (2) FEATURE_MINUTE's definition; the training jobs' runs and every job's state
select text_vc from user_views where view_name = 'FEATURE_MINUTE';
select job_name, status, to_char(actual_start_date at time zone 'UTC','YYYY-MM-DD HH24:MI:SS') st, run_duration, error#, substr(additional_info,1,200) err
  from user_scheduler_job_run_details where job_name in ('ANOM_TRAIN_FINAL','ANOM_RETRAIN_INTERIM')
   and actual_start_date >= timestamp '2026-10-04 12:00:00 UTC' order by actual_start_date;
select job_name, enabled, state, run_count, failure_count, to_char(next_run_date at time zone 'UTC','YYYY-MM-DD HH24:MI') nxt
  from user_scheduler_jobs order by job_name;
-- (3) C6 re-run (p6_60_checks.sql's C6 with its print fixed), and the dev day's null APP_MINUTE minutes
--     (the C6 block is the one in A.1)
select to_char(a.ts_minute,'MM-DD HH24:MI') ts, a.n_ok, a.n_err, a.app_tps, a.app_p50_ms, a.app_p95_ms, a.app_err_pct, to_char(a.loaded_ts,'MM-DD HH24:MI:SS') loaded
  from APP_MINUTE a
 where a.ts_minute >= PKG_ANOM_LIVE.clock('EVAL_FROM') and a.ts_minute < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
   and (a.app_tps is null or a.app_p50_ms is null or a.app_p95_ms is null or a.app_err_pct is null) order by 1;
select to_char(f.ts,'MM-DD HH24:MI:SS') ts from FEATURE_MINUTE f
 where f.ts >= PKG_ANOM_LIVE.clock('EVAL_FROM') and f.ts < PKG_ANOM_LIVE.clock('EVAL_FROM') + 1
   and (f.app_tps is null or f.app_p50_ms is null or f.app_p95_ms is null or f.app_err_pct is null) order by 1;
-- (4) before training: packages valid and at the expected versions, no DEVTUNE / TUNE rows yet, IFOREST rows, the grid
select object_name, object_type, status from user_objects where object_name in ('PKG_ANOM_TRAIN','PKG_ANOM_SCORE','PKG_ANOM_GRADE','PKG_ANOM_LIVE') order by 1,2;
select name, substr(text,1,110) l from user_source where name in ('PKG_ANOM_TRAIN','PKG_ANOM_SCORE','PKG_ANOM_GRADE') and type='PACKAGE BODY' and line = 2;
select count(*) devtune_rows from SCORE_EVAL where run_tag like 'DEVTUNE%';
select count(*) tune_models from MODEL_REGISTRY where variant like 'TUNE%';
select model_name, variant, status, to_char(train_from,'MM-DD HH24:MI') tf, to_char(train_to,'MM-DD HH24:MI') tt, n_rows from MODEL_REGISTRY where detector='IFOREST';
select run_tag, count(*) from SCORE_EVAL group by run_tag order by 1;
select detector, step, settings_json, is_default from TUNING_GRID order by detector, step;
-- (5) the variant-name check, dry run over the grid (the same expression as A.2)
-- (PL/SQL block: for every non-IFOREST TUNING_GRID row, print detector, step and the name value A.2 derives)
-- (6) after training and IFOREST: TUNE creation times; IFOREST registry rows
select to_char(min(created_ts),'YYYY-MM-DD HH24:MI:SS') first_tune, to_char(max(created_ts),'YYYY-MM-DD HH24:MI:SS') last_tune, count(*) n
  from MODEL_REGISTRY where variant like 'TUNE\_%' escape '\';
select model_name, variant, status, to_char(train_from,'MM-DD HH24:MI') tf, to_char(train_to,'MM-DD HH24:MI') tt, n_rows,
       json_value(settings_json,'$.random_state') rs, json_value(settings_json,'$.sklearn') sk, json_value(settings_json,'$.threshold') thr
  from MODEL_REGISTRY where detector = 'IFOREST' order by model_name;
select count(*) dev_p4_rows_left from SCORE_EVAL where run_tag = 'DEV_P4';
-- (7) exact median TTD of the candidates the reading note compares; MSET's missed incidents against open episodes
select run_tag, n_caught, to_char(ttd_median,'FM990.000000') ttd_median from GRADE_RESULT
 where run_tag in ('DEVTUNE_PCA_99_5','DEVTUNE_PCA_99','DEVTUNE_SVM_0_01','DEVTUNE_SVM_0_02','DEVTUNE_MSET_4_6','DEVTUNE_MSET_5_8','DEVTUNE_MSET_6_10') order by 1;
select i.run_tag, i.run_id, i.scenario, to_char(i.inc_start,'HH24:MI:SS') inc_start, to_char(i.inc_end,'HH24:MI:SS') inc_end,
       (select listagg(a.episode_no || ' ' || to_char(a.start_ts,'HH24:MI:SS') || '-' || nvl(to_char(a.end_ts,'HH24:MI:SS'),'open'), '; ')
               within group (order by a.start_ts)
          from ALERT_EVAL a where a.run_tag = i.run_tag
           and a.start_ts < i.inc_start and nvl(a.end_ts, a.last_ts) >= i.inc_start) open_at_start,
       (select listagg(to_char(a.start_ts,'HH24:MI:SS'), ', ') within group (order by a.start_ts)
          from ALERT_EVAL a where a.run_tag = i.run_tag and a.start_ts >= i.inc_start and a.start_ts <= i.inc_end + 10/1440) starts_in_window
  from GRADE_INCIDENT i
 where i.run_tag in ('DEVTUNE_MSET_2_5','DEVTUNE_MSET_3_5') and i.caught = 'N' order by 1, 2;
```

