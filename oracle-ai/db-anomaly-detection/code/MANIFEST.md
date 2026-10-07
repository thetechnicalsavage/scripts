<!-- v1.0 2026-10-07  Brief 10 code manifest: every script POST.md cites, mapped to its source in ai_demos.
     v1.0: the APEX layer is left out of this public folder: 97_anom_ui.sql, tools/anom_ash_enable.sql and the
           suites anom_test_phase5c.sql and anom_test_phase5d.sql, which call its package; their rows move to section 4.
     v0.9: public copy: host name and local-time references removed; the 18 copies this changed are marked `public` with
           their new version and sha256 (line counts unchanged); the secrets file's path is gone (a placeholder in the
           copies); the brief's own tools are not in this folder, so Verify, sections 3 and 5 say so.
     v0.8: (phase 7 write-up) 86 v1.3's source pushed in ai_demos 5f9187f (07-Oct-2026, after a pattern and a value
           secret scan); every source is read at that commit and the working-tree declaration is removed. No copy
           changed.
     v0.7: (phase 7 screenshots) the brief's own tools: capture_app.mjs v1.1 (set: and press: steps) and its tests
           tests/test_capture_app.py v1.1; no copy of an ai_demos source changed.
     v0.6: (phase 6.3) 86 v1.3 copied from the ai_demos working tree (V$SERVICEMETRIC no longer granted, revoked where
           held; run on the target 07-Oct 01:45:37Z), declared with **Sources read from** until a push records its commit;
           make_architecture.py v1.1 and test_render_tools.py v1.1 (shot 01 shows four views).
     v0.5: sources pushed in ai_demos 7ab548d (02-Oct-2026); the working-tree declaration is removed.
     v0.4: (phase 5d review, Codex) anom_test_phase5d.sql v1.1 and anom_gap_deploy.sql v1.1 refreshed from the working
           tree; Verify names check_package v1.3's --publish gate, which fails while the line below declares the
           working tree; check_package v1.3, its tests v1.3.
     v0.3: (phase 5d) 7 copies refreshed (86 v1.2, 94 v1.3, 95 v1.2, 96 v1.5, 97 v1.8, anom_test_phase4 v1.4,
           anom_obs_run.sh v1.8) and 6 added (tools anom_target_users_run.sh, anom_train_exclude.sql,
           anom_ash_enable.sql, anom_gap_deploy.sql; tests anom_test_phase5c.sql, anom_test_phase5d.sql) from the
           ai_demos working tree (df78886 plus phases 5b-5d, not pushed yet), declared with **Sources read from**
           until the next push records its commit here; check_package v1.2; the spike row cites `PLAN.md` (v1.7) lines.
     v0.2: (phase 5b review, Codex) section 5 no longer says no e-mail address is in any copy: 88 generates
           synthetic customer addresses at example.com; the spike row cites PLAN v1.6 lines; check_package v1.1.
     v0.1: first version (write-up builder W, phase 7 draft): 39 copies from ai_demos commit df78886, 3 of them with
           the loopback address written as localhost; the 2 spike scripts that belong to this brief; the brief's own
           tools. Verify with code/tools/check_package.py. -->

# Brief 10: code manifest

What `code/` holds, where each copy came from, and how to prove it still matches.

- **ai_demos commit:** `5f9187f` ("App 900 phase 6.3: ANOM_MON keeps four V$ metric views (86 v1.3 revokes V$SERVICEMETRIC), page 5 text (f900 v1.4)", 07-Oct-2026, pushed after a secret scan). Every copy's source is read at this commit; every source except `apps/900-db-anomaly/sql/86_anom_target_users.sql` is unchanged since `7ab548d` (02-Oct-2026).
- **Copy paths** are relative to `code/`; **source paths** are relative to the ai_demos root. Line numbers in POST.md cite the copies, and a copy marked `none` has the same line numbers as its source. A `public` copy keeps every line number too: its edits change text within lines, never add or remove one.
- **change** `none`: byte-identical to the source. `loopback`: the only difference is that the IPv4 loopback address is written as `localhost` (brief rule: no IP address anywhere in the package). On the demo VM the containers publish their listeners on the IPv4 loopback address; anyone running the copies sets `host` (or the DSN) to whatever reaches the listener on their machine. `original`: written in this brief, no ai_demos source. `public`: changed for this public folder only: the demo VM's host name in comments is written as "the demo VM", a local-time stamp is written in UTC, the secrets file's default path is the placeholder `<secrets-file>` (set `ANOM_SECRETS` for the shell runners, `--secrets` for iforest.py, `[secrets] file` in driver.ini), and the version comment is bumped with a one-line changelog. No code path changed beyond that default.
- **Version** is the version comment in the first five lines of the copy. The sha256 values are of the whole file.
- **Verify:** `sha256sum` a copy (from `code/`) and compare it with the last hash column of its row. The brief's
  own checker, which also re-read every source at the recorded commit, is not part of this folder.

## 1. Copies from ai_demos (app 900)

| Copy | Source | Version | Lines | sha256 of the source | sha256 of the copy | change | Role |
|---|---|---|---|---|---|---|---|
| `app900/driver/anomaly_driver.py` | `apps/900-db-anomaly/driver/anomaly_driver.py` | v1.2 | 1279 | 4ad0ef6b2f575f6b77b6d656cfcfd2868219afc4984e5aab0dcc16b65b6d5181 | d018833ff55dea4c0247ab7dc466ad942bba5f853fead8c011663ed0cbb177eb | public | the load driver: five transactions, load shape, client-side minute feed (APP_MINUTE) |
| `app900/driver/anomaly-driver.service` | `apps/900-db-anomaly/driver/anomaly-driver.service` | v1.1 | 25 | cf1ba9205dfa2601c4bbe4c8f48548b4d37fb17e8fe9660ac9287978c18b9f43 | 006975e32364011205e312cde01479c7311976570460b998cecbd1da5095b519 | public | its user-level systemd unit |
| `app900/driver/driver.ini` | `apps/900-db-anomaly/driver/driver.ini` | v1.3 | 78 | d6083b183f784b3e28570d08fed7299e4805e284f23e2fbc8c09cd267e353e62 | 66d8b9e080dfb616cfb99918c024dbab940a29e8b0cf3e6e8a07a6e3487cb555 | loopback, public | driver configuration (peak_tps 70, 6 workers, seed 20261001); no secrets, it names the secrets file |
| `app900/driver/install.sh` | `apps/900-db-anomaly/driver/install.sh` | v1.1 | 82 | f518d4a66e91833e4e1e95ab33bbed3f733e0e82f1d0d3b43422a1644d169d65 | 94e1c65c4b7eee3ec726272b931e755746790d624946053047f893b867d873c2 | public | installs or updates the driver (never starts it) |
| `app900/driver/README.md` | `apps/900-db-anomaly/driver/README.md` | v1.1 | 102 | 8a94232c1978407c805c9ad2f514a0ce4ca716ada60106ef56c0abd19960bf61 | 9e5bd793052b7d3d0394e30bd771b6e5ee43d83c7f56c4a800f8369751fe6026 | loopback, public | driver install, start, stop, logs |
| `app900/driver/requirements.txt` | `apps/900-db-anomaly/driver/requirements.txt` | v1.0 | 9 | c4c37474438292b30029ba7959a6fc8379cf19c203c0f1ce109780c822ff919d | c4c37474438292b30029ba7959a6fc8379cf19c203c0f1ce109780c822ff919d | none | pinned python-oracledb 3.4.2 closure |
| `app900/sql/86_anom_target_users.sql` | `apps/900-db-anomaly/sql/86_anom_target_users.sql` | v1.3 | 169 | d492d4d3c65d278f978a47f0970903d615dc636c62966d8ff9314a3290a12c59 | d492d4d3c65d278f978a47f0970903d615dc636c62966d8ff9314a3290a12c59 | none | target users; ANOM_MON = CREATE SESSION + SELECT on four V$ metric views (v1.3 revokes V$SERVICEMETRIC where held); ASH grant optional |
| `app900/sql/87_anom_shop_schema.sql` | `apps/900-db-anomaly/sql/87_anom_shop_schema.sql` | v1.0 | 560 | 4877f9ed4a10ccdad47ff5a19d01306f7fbb28bb4caa159868619d2e04245156 | 4877f9ed4a10ccdad47ff5a19d01306f7fbb28bb4caa159868619d2e04245156 | none | SHOP application: tables, PKG_SHOP, DRIVER_CONTROL, nightly settlement |
| `app900/sql/88_anom_shop_seed.sql` | `apps/900-db-anomaly/sql/88_anom_shop_seed.sql` | v1.0 | 452 | a08cf9750588f582c18aecfb28757b22fa120801caffe6eef5611d4d7c6a364e | a08cf9750588f582c18aecfb28757b22fa120801caffe6eef5611d4d7c6a364e | none | deterministic seed data |
| `app900/sql/89_anom_observer_users.sql` | `apps/900-db-anomaly/sql/89_anom_observer_users.sql` | v1.0 | 91 | 8e1cc8e22f7dc285b6c4147021cd2aad1f3f8a5031c877a30b707c0c203d484b | 8e1cc8e22f7dc285b6c4147021cd2aad1f3f8a5031c877a30b707c0c203d484b | none | observer users ANOMOPS and ANOM_FEED |
| `app900/sql/90_anom_tables.sql` | `apps/900-db-anomaly/sql/90_anom_tables.sql` | v1.1 | 299 | 7c2dbc4736ef8278d6b949205f13e8939f7378c642187ce795bbf7140c6200a2 | 7c2dbc4736ef8278d6b949205f13e8939f7378c642187ce795bbf7140c6200a2 | none | SIGNAL_DEF, METRIC_MINUTE, APP_MINUTE, FEATURE_MINUTE |
| `app900/sql/91_anom_links.sql` | `apps/900-db-anomaly/sql/91_anom_links.sql` | v1.1 | 131 | eb33ac9e83e7128c099d8a914e80ccac23c8d4d717197db437b33048d7bcac9e | eb33ac9e83e7128c099d8a914e80ccac23c8d4d717197db437b33048d7bcac9e | none | ANOM_MON_LINK and ANOM_CHAOS_LINK (passwords on stdin) |
| `app900/sql/92_anom_collect.sql` | `apps/900-db-anomaly/sql/92_anom_collect.sql` | v1.3 | 804 | 2f7c4348a6df4b662dfa91f5db9b6063a4d9195af653328116a70c9856f4aa9d | 2f7c4348a6df4b662dfa91f5db9b6063a4d9195af653328116a70c9856f4aa9d | none | the collector: one persistent link session an hour, the per-minute read |
| `app900/sql/93_anom_chaos.sql` | `apps/900-db-anomaly/sql/93_anom_chaos.sql` | v1.2 | 1692 | 4baeac4480e46a95dbff6345ef3e5ef2d882ce04ceff6de9c40ee6360e26805c | 4baeac4480e46a95dbff6345ef3e5ef2d882ce04ceff6de9c40ee6360e26805c | none | PKG_CHAOS: 12 scenarios, dispatchers, CHAOS_PLAN, reaper (demo only) |
| `app900/sql/94_anom_models.sql` | `apps/900-db-anomaly/sql/94_anom_models.sql` | v1.3 | 1662 | 9b65c4d3405050431759f8fa4aa00836c8a45c62c8bb23a0a18fbcf54e41e0e6 | 9b65c4d3405050431759f8fa4aa00836c8a45c62c8bb23a0a18fbcf54e41e0e6 | none | trainer: MSET-SPRT, SVM, EM, PCA residual, STATIC, SEASONAL; training-row filter |
| `app900/sql/95_anom_score.sql` | `apps/900-db-anomaly/sql/95_anom_score.sql` | v1.2 | 951 | abf709b298430c28db0c09bbbebf3abbb5a7010f8c0defdf1c51f03f8b1b3c45 | abf709b298430c28db0c09bbbebf3abbb5a7010f8c0defdf1c51f03f8b1b3c45 | none | scoring, PREDICTION_DETAILS parsing, alert episodes |
| `app900/sql/96_anom_grade.sql` | `apps/900-db-anomaly/sql/96_anom_grade.sql` | v1.6 | 981 | da02f5483dbb6fa4dd3c6c39cab1bde71a32cf20e554006f2e28fada64e7ea8d | 40acacb5fe093b87352f36b7b91def6fbbc4d1f748325128d078e8d20d960ef2 | public | hidden-incident plan, grading (PLAN 7a amended: ATTR_P, tie_chance), exact McNemar |
| `app900/sql/98_anom_jobs.sql` | `apps/900-db-anomaly/sql/98_anom_jobs.sql` | v1.1 | 543 | 39e2e07fdc035c8a40e8111326671d8bc119190983ff288a8cb1561028685a3e | 39e2e07fdc035c8a40e8111326671d8bc119190983ff288a8cb1561028685a3e | none | soak clock, TUNING_GRID (PLAN 7a), interim and final training jobs |
| `app900/test/anom_target_app_tx.sql` | `apps/900-db-anomaly/test/anom_target_app_tx.sql` | v1.0 | 75 | c89a11eecf57d5e6017cad6e319c4c6ac22a7b1847716b155aec9d5281a8f783 | c89a11eecf57d5e6017cad6e319c4c6ac22a7b1847716b155aec9d5281a8f783 | none | transcript 01: the five transactions as SHOP_APP |
| `app900/test/anom_test_chaos_ctl.sql` | `apps/900-db-anomaly/test/anom_test_chaos_ctl.sql` | v1.2 | 154 | c4c1cd7ca4b437e088da5ef99e9e2e329ccd34dda82b78e77a45bbb25f69ee71 | c4c1cd7ca4b437e088da5ef99e9e2e329ccd34dda82b78e77a45bbb25f69ee71 | none | CHAOS_CTL's privileges (run by anom_chaos_test.sh) |
| `app900/test/anom_test_chaos.sql` | `apps/900-db-anomaly/test/anom_test_chaos.sql` | v1.5 | 1312 | 9e9f46886cab8fcfe4b2bde10c7dcc124215016fe45db6253043ffa08f131e5b | 9e9f46886cab8fcfe4b2bde10c7dcc124215016fe45db6253043ffa08f131e5b | none | transcripts 05 and 05b: each scenario does its thing and restores |
| `app900/test/anom_test_phase4.sql` | `apps/900-db-anomaly/test/anom_test_phase4.sql` | v1.4 | 1046 | 21d5a0fc62c96d172931c159190c1e4bc68b3c3b54155afb6a0850745c4eeb67 | 21d5a0fc62c96d172931c159190c1e4bc68b3c3b54155afb6a0850745c4eeb67 | none | transcript 30: P21, P29-P36, P41-P43 |
| `app900/tools/anom_chaos_run.sh` | `apps/900-db-anomaly/tools/anom_chaos_run.sh` | v1.1 | 204 | 7152d0b52392bb2cfb5b77a678a9e45e111778d8c7e407eee28f41618229fb9d | 552c96e9765d45b6417bca31f469a3e3a9e593cdff1d33eeb5a46a7b644e4298 | public | runner for target scripts (SHOP, CHAOS_CTL); scans its logs for secrets |
| `app900/tools/anom_chaos_test.sh` | `apps/900-db-anomaly/tools/anom_chaos_test.sh` | v1.2 | 65 | f09411db6fd7f41d6ad849b79720d63a4205cef67f1362d5ce8f15746d3f10eb | fa325fdec09e300578bcfa60cea1e3eb3abe4cdc07ea1a6322b5e2abc333ede7 | public | the chaos test sequence with quiet gaps |
| `app900/tools/anom_chaos_window.py` | `apps/900-db-anomaly/tools/anom_chaos_window.py` | v1.2 | 302 | 22f9adb4c9857a42b4044e3709baf6ddcd0bdd17736b714b0e9c1bfeba24290a | bf6e019c689be4ab22b0a134c5db4e577d4683d5bb6b94c6f28ed40a11e8bf21 | public | what a chaos run did to the signals (transcripts 04, 05) |
| `app900/tools/anom_collector_cost.sh` | `apps/900-db-anomaly/tools/anom_collector_cost.sh` | v1.7 | 584 | 0999c19eac5b27524409bc1e038acaba49ac86abc2ca18d7c627127bf9d5d518 | 7a89c1a0be84f303b69421b19b70923c3812cd4f2cad5d8a791a41da89d8f549 | public | collector cost on the target (transcripts 02, 02b) |
| `app900/tools/anom_detect_dev.sql` | `apps/900-db-anomaly/tools/anom_detect_dev.sql` | v1.0 | 70 | 106e66e3060ce5ab4b9d5fae9b88814d6b3a5a9a8aaaac44fdc77c143dce824f | 106e66e3060ce5ab4b9d5fae9b88814d6b3a5a9a8aaaac44fdc77c143dce824f | none | detectors' development run and cost (transcript 30) |
| `app900/tools/anom_live_interim.sql` | `apps/900-db-anomaly/tools/anom_live_interim.sql` | v1.0 | 36 | 6b4c8edb80bd0af31582ef92e591c248ffc1f545055a2df9e07cb8dbc12c09c1 | 6b4c8edb80bd0af31582ef92e591c248ffc1f545055a2df9e07cb8dbc12c09c1 | none | interim training for the live test (transcript 04) |
| `app900/tools/anom_live_report.sql` | `apps/900-db-anomaly/tools/anom_live_report.sql` | v1.0 | 213 | 5774ae83d39f8580b26b4d77ed5148df5a2b7f0cd00551df54be208beb40efd5 | 5774ae83d39f8580b26b4d77ed5148df5a2b7f0cd00551df54be208beb40efd5 | none | what the live detectors did in a window (transcript 04) |
| `app900/tools/anom_live_start.sql` | `apps/900-db-anomaly/tools/anom_live_start.sql` | v1.1 | 60 | 187fdea588530fa95fb3162c2e906c4ac92d6101c15136a8cfbbf197d607e732 | 187fdea588530fa95fb3162c2e906c4ac92d6101c15136a8cfbbf197d607e732 | none | starts a scenario the way the Control Center does (transcript 04) |
| `app900/tools/anom_live_test.sh` | `apps/900-db-anomaly/tools/anom_live_test.sh` | v1.1 | 66 | 33f60dde07dbacf8355dbe9b4df14a26d1f3b3ed44032e009a226a634fe9f0e9 | dd65832b3d668efebd3ccb4cab44d8be16c66ed53ad4d9cedc9b0ef8127f4890 | public | the first end-to-end live detection test (transcript 04) |
| `app900/tools/anom_obs_run.sh` | `apps/900-db-anomaly/tools/anom_obs_run.sh` | v1.9 | 204 | 228ea146933e5b4b7ccfd2a63cd60819798c56657c0a735288e7cc2413869634 | ad7175bcbcead1e2ba00d3bcb1259db3c113cb9de39033a9420dc8fc34b9d48a | public | runner for observer scripts (ANOMOPS); `scan <log>` checks a log for secrets |
| `app900/tools/anom_plan_clear.sql` | `apps/900-db-anomaly/tools/anom_plan_clear.sql` | v1.0 | 44 | f102b185852ace7464a6aea85c7f215d43a76b4fe1afe6845137f9674b07b8ae | f102b185852ace7464a6aea85c7f215d43a76b4fe1afe6845137f9674b07b8ae | none | deletes test plan rows 9000-9099 on the target |
| `app900/tools/anom_plan_test.sql` | `apps/900-db-anomaly/tools/anom_plan_test.sql` | v1.1 | 195 | 64a8bed41a6971b232446619eb4eb8f91fa11f31e22f3328c13d9ed910c1ace2 | 64a8bed41a6971b232446619eb4eb8f91fa11f31e22f3328c13d9ed910c1ace2 | none | injector-invisibility tests through the real plan path (transcript 06) |
| `app900/tools/anom_soak_check.sh` | `apps/900-db-anomaly/tools/anom_soak_check.sh` | v1.3 | 278 | 9948f2d00c6a32090d853dd12b2a6548bac3915afc23eeb951e4420019d7a358 | 903db301e75316fbaea1b28550ef76c625b1e68c7988eec13a5519fd0b7f8d26 | public | read-only soak health check (transcript 03) |
| `app900/tools/anom_soak_minutes.py` | `apps/900-db-anomaly/tools/anom_soak_minutes.py` | v1.2 | 221 | a895f7ff3eb37cebff49e7c58cf2f8a99c3762b24f45c5f3e97d1abef5d24ae7 | 14d50fff6ad81cdb8fb37ff4207573c9257c405f1922c99902ad453dcaa73265 | public | its minute-table helper |
| `app900/tools/iforest.py` | `apps/900-db-anomaly/tools/iforest.py` | v1.1 | 325 | e34d38853aa39b717cb1fd1c6112c4848ae8eae0dfb36b796f1d64d6fad339f5 | 00f219f561e5a32512e57052fcfb7932180e8da4557e35c7ce7d73aaf3277abc | loopback, public | Isolation Forest reference (D5), outside the database |
| `app900/tools/requirements-ml.txt` | `apps/900-db-anomaly/tools/requirements-ml.txt` | v1.1 | 17 | c8ab9e3d0545bb423868ff9dddb025541e1fb59ed32fe6651f631cc3ac73f76f | 0b2e9b5852f0a63bae84f8e491ae66f7c2826eb5232816eeab1a7fe144dfc64e | public | pinned scikit-learn 1.9.1 closure for iforest.py |
| `app900/tools/anom_gap_deploy.sql` | `apps/900-db-anomaly/tools/anom_gap_deploy.sql` | v1.1 | 100 | 8ddad10678e7be6077b848067b5e2d2749854d4d8857f7373f70b36a389ada79 | 8ddad10678e7be6077b848067b5e2d2749854d4d8857f7373f70b36a389ada79 | none | runs one of 95-98 in the truth loop's hourly hand-over, then compiles invalid objects (phase 5d) |
| `app900/tools/anom_target_users_run.sh` | `apps/900-db-anomaly/tools/anom_target_users_run.sh` | v1.1 | 175 | d9838a4ec8392cb74c095c4ee8d9d80f5e8e2281bbece5c02a355bae2d93f5b0 | 1e7d7d33c8be051192a666fbe8a60c785e3e1efd4c9c3793b59910722f41fed0 | public | runner for sql/86 on the target as SYSDBA (four passwords on stdin); `run <log> Y` adds ANOM_MON's ASH grant |
| `app900/tools/anom_train_exclude.sql` | `apps/900-db-anomaly/tools/anom_train_exclude.sql` | v1.0 | 96 | 828a7661d0e9a43ad14561aca20783c3da84af2612afd472772b159d9305512f | 828a7661d0e9a43ad14561aca20783c3da84af2612afd472772b159d9305512f | none | a training exclusion window for a planned contact with the target during training (TRAIN_EXCLUDE) |

## 2. Originals in this brief, cited by the post

| File | Source | Version | Lines | sha256 of the source | sha256 of the file | change | Role |
|---|---|---|---|---|---|---|---|
| `spike/spike_models.sql` | original | v1.0 | 142 | - | afc52bdf2beb4ad6277a94d3db20d2442ea54de8586cfacade2eaa4fbb75251b | original | phase 0 spike: MSET-SPRT, SVM and EM built and scored on synthetic data (PLAN.md:42-52) |
| `spike/collector_cost.sql` | original | v1.0 | 23 | - | 0d70180f89de69717228da02ab4f5b894bd5c762364ca48261a496477129a734 | original | phase 0 spike: cost of one full metric read, measured locally on the observer (19 ms) |

## 3. The brief's own tools

Not in this folder: the manifest checker, the leak scanner, the transcript and screenshot renderers and their
tests packaged the brief; none of them installs, runs, scores or grades the lab.

## 4. Not copied, and why

| Path in ai_demos | Why it is not here |
|---|---|
| `apps/900-db-anomaly/apex/f900.sql`, `apex/f900-live.sql`, `apex/import_f900.sql` | The APEX export is not cited line by line in the post; it carries the checksum-salt placeholder (DEADBEEF), never a real salt, but publishing an APEX export is the operator's call (series README, "Before anything goes public"). |
| `apps/900-db-anomaly/sql/97_anom_ui.sql`, `tools/anom_ash_enable.sql`, `test/anom_test_phase5c.sql`, `test/anom_test_phase5d.sql` | The APEX layer: the package and views the APEX pages read and call, the tool that drives that package, and the two suites that call it. APEX code stays out of this public folder. Two queries in `results/QUERIES.sql` read objects it creates: `PKG_ANOM_UI.official_day` in Q00 and `ANOM_UI_AUDIT` in Q13. |
| `apps/900-db-anomaly/tools/anom_ui_run.sh`, `anom_shots_*` | The APEX install runner and the temporary screenshot copy (app 9900): SHOTS.md describes them; the post does not cite them. |
| `apps/900-db-anomaly/test/test_*.py` | The static pytest suites hold deliberate leak fixtures (sample addresses, a PEM header, container ids) that test the secret checks; the post cites their results through transcripts 20 and 30, not the files. `test_phase5c_static.py` and `test_phase5d_static.py` (02-Oct) hold no such fixture but are not cited either; add them if the post quotes one. |
| `apps/900-db-anomaly/test/anom_test_phase3.sql`, `anom_test_phase4b.sql`, `anom_test_live.sql`, `anom_test_ui.sql`, `anom_test_apex900.sql`, `anom_test_chaos_link.sql`, `anom_test_target.sql` | Suites the post names only by their pass counts (contract section 12.5); add them here if the post quotes one. |
| `apps/900-db-anomaly/docs/contract.md` | A document, not a script; the post cites it in place as `ai_demos/apps/900-db-anomaly/docs/contract.md`. |
| the secrets file (demo VM) | Never copied, never read for this package. |

## 5. Leak scan of this folder (07-Oct-2026)

The brief's leak scanner, run over this public folder with the lab's private literal patterns, reports 0 findings: no host name, IP address, OCID, key or home path. The only e-mail-shaped values are the 200,000 synthetic customer addresses that 88 generates at example.com, a domain reserved for documentation (`code/app900/sql/88_anom_shop_seed.sql:163`); no real address appears.
