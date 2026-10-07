# Predictive anomaly detection with OML MSET-SPRT

The scripts behind
[Predictive anomaly detection with OML MSET-SPRT on Oracle 26ai](https://thetechnicalsavage.com/blog/mset-sprt-anomaly-detection-26ai/).

One Oracle AI Database 26ai watches another that plays production, through one read-only user and a
database link. Every minute it reads 35 signals. Oracle Machine Learning's MSET-SPRT learns how
those signals normally move together and raises an alarm when they stop agreeing. Six other
detectors learn from the same three days as a fair comparison, and a demo-only injector causes real
faults on the watched database, on a hidden schedule.

## Results, final test day (14 faults: 8 mild, 6 strong)

| Detector | Mild caught | Strong caught | All caught | 9 in 10 caught within | False alarms in 24 h |
|---|---|---|---|---|---|
| MSET-SPRT (Oracle Machine Learning) | 6 of 8 | 6 of 6 | 12 of 14 | 2.4 min | 3 |
| One-class SVM (Oracle Machine Learning) | 6 of 8 | 6 of 6 | 12 of 14 | 4.4 min | 0 |
| EM anomaly (Oracle Machine Learning) | 7 of 8 | 4 of 6 | 11 of 14 | 5.5 min | 1 |
| PCA residual, in SQL | 5 of 8 | 6 of 6 | 11 of 14 | 2.5 min | 0 |
| Isolation Forest, outside the database | 5 of 8 | 5 of 6 | 10 of 14 | 40.0 min | 0 |
| Static thresholds | 7 of 8 | 6 of 6 | 13 of 14 | 5.1 min | 0 |
| Seasonal baseline | 0 of 8 | 0 of 6 | 0 of 14 | - | 1 |

MSET-SPRT flagged 13 of the 14 faults; one flag fell inside an alert it already had open, so the
scoring rule counts 12. A paired exact McNemar test cannot tell it apart from the best other detector
(p = 1). Its three false alarms all opened a few minutes after an hourly page-cache job on the lab
host (`results/false_alarms.md`). Every number above comes from `results/test_results.md`, and every
one has its read-only query in `results/QUERIES.sql`.

## The scripts

| Step | Script | Run on |
|---|---|---|
| read-only monitoring user (the whole production footprint) | [`code/app900/sql/86_anom_target_users.sql`](code/app900/sql/86_anom_target_users.sql) | watched database, as SYSDBA |
| demo shop schema and seed data | [`87_anom_shop_schema.sql`](code/app900/sql/87_anom_shop_schema.sql), [`88_anom_shop_seed.sql`](code/app900/sql/88_anom_shop_seed.sql) | watched database |
| observer users, tables, links | [`89`](code/app900/sql/89_anom_observer_users.sql), [`90`](code/app900/sql/90_anom_tables.sql), [`91`](code/app900/sql/91_anom_links.sql) | watching database |
| collector, one link session an hour | [`92_anom_collect.sql`](code/app900/sql/92_anom_collect.sql) | watching database |
| demo fault injector, 12 scenarios | [`93_anom_chaos.sql`](code/app900/sql/93_anom_chaos.sql) | watched database |
| train MSET-SPRT and the other detectors | [`94_anom_models.sql`](code/app900/sql/94_anom_models.sql) | watching database |
| score every minute, open alerts | [`95_anom_score.sql`](code/app900/sql/95_anom_score.sql) | watching database |
| hidden schedule and grading | [`96_anom_grade.sql`](code/app900/sql/96_anom_grade.sql) | watching database |
| scheduler jobs and the tuning grid | [`98_anom_jobs.sql`](code/app900/sql/98_anom_jobs.sql) | watching database |
| load driver (Python, python-oracledb thin) | [`code/app900/driver/`](code/app900/driver/) | host |
| runners, measurements, Isolation Forest | [`code/app900/tools/`](code/app900/tools/) | host |
| SQL test suites | [`code/app900/test/`](code/app900/test/) | both |
| MSET-SPRT on synthetic data, and the read cost | [`code/spike/`](code/spike/) | watching database |

Install in number order: 86 to 96, then 98. Every script's header gives its account, its arguments
and how it behaves on a re-run. Passwords are read on standard input or from a secrets file you name
(`ANOM_SECRETS`, the driver's `[secrets] file`, `iforest.py --secrets`); none is in this folder.
[`code/MANIFEST.md`](code/MANIFEST.md) lists every file with its version and sha256.

The APEX application in the post's screenshots is not in this folder, and neither are the package and
views behind its pages. Nothing here needs them to collect, train, score or grade. Two queries in
`results/QUERIES.sql` read objects that came with it: the `official_day` column of Q00 and the
Control Center line of Q13.

## Prerequisites

- Two Oracle AI Database 26ai databases. Here the watcher was Enterprise Edition 23.26.1 and the
  watched one 26ai Free 23.26.3 with 2 CPUs.
- Oracle Machine Learning on the watching database. The 26ai licensing manual: Oracle Machine
  Learning no longer requires an extra cost license. The optional ASH drill-down reads
  `V$ACTIVE_SESSION_HISTORY`, which is part of the Diagnostics Pack.
- Python 3.12 with `python-oracledb` 3.4.2 for the driver, and scikit-learn 1.9.1 for the Isolation
  Forest reference.

## Results files

[`results/`](results/) holds the final-day results (`test_results.md`), the tuning-day choice for
each detector (`dev_tuning.md`), the investigation of MSET-SPRT's three false alarms
(`false_alarms.md`), the queries behind every number (`QUERIES.sql`, `false_alarms_queries.sql`),
their outputs (`csv/`), and the four charts (`charts/`).

Written against Oracle AI Database 26ai 23.26.1 and 23.26.3, 1 to 7 October 2026. One synthetic
workload with injected faults on one small database: not a benchmark. Not tested on Autonomous
Database.
