<!-- v1.3 2026-10-07 - public copy: host name and local-time references removed. v1.2 2026-10-07 - final claims review: line 172 restated (single-minute SQL_RT/W_USERIO crossings after each drop,
     never three running; fa_f02, fa_f10); line 27 names the training and test-day TPS ranges. No number changed.
     v1.1 2026-10-07 - Codex review: 3 findings accepted; 2 disputed with evidence, both retracted on re-review, their
     wording made explicit: SESSIONS wording; the cron job's role split into established (timing, nothing else at :17)
     and likely (cause, script not separated); the driver's rate at :17 (F04 v1.1); fstrim/storage and I/O-path
     alternatives in the limits; the AutoTask's interval in later hours. Queries v1.1, re-run 02:09:07-02:09:23Z.
     v1.0 2026-10-07 - phase 6.3 of app 900 / brief 10: why MSET-SPRT raised 3 false alarms on the test day, and what
     the hourly ":17 bump" seen in training is. Read-only investigation; nothing in either database, the graded data,
     the models or the demo host was changed. Every database number comes from results/false_alarms_queries.sql (as
     ANOMOPS), kept as results/csv/fa_*.csv. Demo-host facts read over ssh (sections 3, 10). Times UTC. -->
# MSET-SPRT's three test-day false alarms and the hourly :17 bump

## 1. Answer

The monitor did not cause them. All three false alarms open 2.5 to 4.5 minutes after an hourly event on the demo
host: at HH:17:00 a root cron job drops the Linux page cache (`echo 1 > /proc/sys/vm/drop_caches`) and runs
`fstrim /`. Nothing else on the host or in the database is scheduled at that minute. For 10-20 minutes after it, the
target's reads take several times longer than usual and its SQL response time roughly doubles at first. Each false alarm opens on an interval where Oracle's own hourly SYS scheduler
jobs add one or two logons (at :19 and :21), while the response time is still high. MSET-SPRT names LOGONS first every
time. The same cron job is the ":17 bump" seen all through training.

| Question | Finding | Status |
|---|---|---|
| When does the hourly :17 bump (app p95, AAS, SQL_RT) start, and what runs then? | At HH:17:00-:17:14, when the demo host's `/etc/cron.hourly` runs `free` (drop the page cache) and `fstrim /`. No other hourly event at :17 exists in the host's crontabs, cron.d or systemd timers, or in either database's job logs read here | **Established** |
| Is that cron job the cause of the bump? | Its signature is an OS-level one: the same number of reads, 5-15x the wait, major page faults in that 10-minute slot every hour, then a gradual recovery. Whether `drop_caches` or `fstrim` does most of it is not separated, and the job was not switched off to test it | **Likely** (strongly supported) |
| Did the monitor's own footprint cause the false alarms (link hand-overs, collector reconnect, truth loop, app 900 jobs)? | No. Its logons fall at HH:59:48-HH:00:48, it did not reconnect, and no observer job starts in :10-:30 | **Established: 0 of 3** |
| Did the load driver cause them? | No. Its transaction rate dips slightly at :17 (41.0 TPS in training against 43.1-43.9 in the minutes either side; 40.8 on the test day against 42.1-43.9), it does the same number of reads, its session count does not change, and its code has no hourly logic. Its sessions are the ones slowed down | **Established: not a cause** |
| Did app 600 or DBOPS touch oradb1 then? | No sign of it. No DBOPS session in ASH from about 17:09Z to EVAL_TO; app 600 has no reference to oradb1; neither is scheduled at :17-:22 | **Not a cause in the evidence available** (ASH no longer covers 10:19 and 14:21) |
| Whose logons does MSET-SPRT name in the opening intervals? | Oracle's own hourly SYS jobs: `OBJNUM_REUSE_MAINTAIN_JOB$$` at :19; cleanup jobs at :21-:22 (`CLEANUP_TOBE_DROPPED_IDX` seen) | **Established** for :19; **likely** for :21-:22 (one of the 3-4 logons identified) |
| Did Oracle's 15-minute AutoTask play a part? | Its cycle (about 3 extra logons over 2-3 minutes) drifts about 2.2 minutes a day. On the test day they landed at :15-:17, inside the drop window. In training they fell elsewhere | **Established** (the drift); its share in the flags **not separable** |
| What caused the 3 false alarms? | The post-drop slowdown together with Oracle's hourly job logons. They were stronger on the test day because the data had grown (61.8 physical reads/s against 4.8-32.1 on the training days) and the AutoTask had drifted onto the drop | **Likely** (all 3 sit in the post-drop window and none fall outside it, but no counterfactual run exists) |
| Did a false alarm cost a catch? | Yes. False alarm 1 was still open when run 122 (conn_leak LOW) started. MSET-SPRT flagged run 122 at 1.47 min, but under the pre-registered rule that flag extended the open episode, so run 122 counts as missed. Run 122 is the one incident that separates MSET-SPRT from the static rival | **Established** (grade() applied as written) |

## 2. The three episodes

From `csv/fa_f01_openings.csv` and `csv/fa_f02_context.csv`. LOGONS is "Logons Per Sec" × 60. W_USERIO is centiseconds waited
on User I/O per second. SQL_RT is in centiseconds per call.

| Episode | Opens | Closes | Named (weight) | Logons/min | SQL_RT | W_USERIO | AAS | SESSIONS |
|---|---|---|---|---|---|---|---|---|
| 7 | 10:19:31 | 11:00:30 (41 min) | LOGONS .6, SQL_RT .2 | 2.01 | 0.1043 | 9.19 | 0.279 | 11 |
| 9 | 14:21:30 | 14:41:29 (20 min) | LOGONS .6, SQL_RT .4 | 3.01 | 0.1131 | 11.04 | 0.286 | 11 |
| 13 | 21:19:30 | 21:31:29 (12 min) | LOGONS .6 | 2.01 | 0.1185 | 9.60 | 0.252 | 11 |

The run-up is the same in all three hours. W_USERIO is 0.41-0.53 in the :15 interval (10:15:30 and 21:15:30; the
context for the second episode starts at 14:16:30). In the interval that begins at :16:30 (it holds 17:00) it jumps to
14.02, 12.14 and 9.80. SQL_RT rises to 0.18, 0.16 and 0.15. MSET-SPRT's score
starts climbing in that same interval (0.17-0.33) and reaches its flag 3 to 5 intervals later, on an interval with
2 or 3 logons. SESSIONS is 11 in every interval from 5 minutes before each opening to 10 minutes after it, so no
session was added. (It reaches 51 only at 21:30:30, when run 128, conn_leak HIGH, starts.) None of the three is near an injected run:
the nearest runs ended at 09:20, 13:34 and 20:10, and their cool-downs ended by 09:50, 14:04 and 20:40. The next
runs started at 10:33, 14:47 and 21:31.

## 3. The hourly :17 bump starts with the demo host's cron job

**What the target shows** (`csv/fa_f03_minute_profile.csv`, `fa_f04_app_p95.csv`, `fa_f11_jump_hours.csv`; run windows and
their 30-minute cool-downs left out):

| Interval begins at | Physical reads/s (train / dev / test) | W_USERIO (train / dev / test) | SQL_RT (train / dev / test) |
|---|---|---|---|
| :15 | 13.9 / 44.2 / 67.4 | 0.64 / 0.57 / 0.77 | 0.063 / 0.058 / 0.063 |
| :16 (holds :17:00) | 12.6 / 38.4 / 68.6 | 2.38 / 5.30 / 9.07 | 0.118 / 0.135 / 0.150 |
| :17 | 12.1 / 40.1 / 61.5 | 2.14 / 6.46 / 10.53 | 0.069 / 0.090 / 0.111 |
| :19 | 11.0 / 34.8 / 58.5 | 2.16 / 6.27 / 9.00 | 0.073 / 0.095 / 0.105 |

- The number of physical reads does not change at :16. On average the :16 interval has 0.93x, 1.00x and 1.05x the
  reads of the :15 interval, while User I/O wait is 5.5x, 11.8x and 15.5x higher (train, dev, test). The same reads
  take longer, and the wait falls back gradually over the next 10-20 minutes, as a cold cache refills. This happens
  outside the database: Oracle's buffer cache was not emptied, but the operating system's file cache beneath it was.
  That reading assumes Oracle's datafile reads go through the OS cache, which the flat read count with a 5-15x wait
  implies; the target's I/O parameters were not read (ANOM_MON has no grant for them).
- It happens in most hours. The jump (more than twice the :15 value and 0.5 cs/s above it) shows in 46 of 72 clean
  training hours, 8 of 8 clean dev hours and 8 of 8 clean test hours. In training, 38 of the 46 jump hours are not
  one of the 12 hours in which ANOM_RETRAIN_INTERIM ran at :17, so the interim retraining is not the cause. That job
  has been disabled since TRAIN_TO.
- The application feels it. The driver's p95 for the minute starting at :17 averages 72.8 ms in training (max 141)
  and 93.7 ms on the test day (max 138), against 15-31 ms in the minutes either side. This is the ":17 p95 bump" of
  the STATUS notes (first logged 02-Oct 10:18Z). The driver does not send more work then: its rate in that
  minute is 41.0 TPS in training and 40.8 on the test day, against 42.1-43.9 in the minutes either side (`fa_f04`).
  Its workers wait for each call, so slower calls mean slightly fewer of them.
- ASH on the target puts the onset at :17:00 (`csv/fa_a04_ash_userio_onset.csv`, 06-Oct 17Z to 07-Oct 00Z, 8 hours
  pooled). The load driver's User I/O samples go from 0-2 per 15-second slot between :15:00 and :16:59 to 25 in the
  :17:00-:17:14 slot (in 7 of the 8 hours), then 4-21 per slot through :19:59. In the two hours that ASH holds clear
  of every run and cool-down (`fa_a03`), the driver is sampled 13.5 times an hour in the :16 interval against 2-7 in
  each of :01-:15, and User I/O makes up 10 of those 27 samples against 0-2 in each earlier minute, in `order_status`
  and `place_order`.
- No other session in the PDB does reads on that scale then. The only non-driver samples in any :16:30-:17:30 interval
  are single samples of routine work that also shows at other times: the collector's open session, DBWR, the AWR
  raw-metrics capture, an idle dispatcher poll and, in hour 17 only, the drifting AutoTask (17:17:27-29). In the later
  hours the AutoTask's samples fall at :17:32-:17:55, in the next interval (`fa_a02`).

**What the demo host shows** (read-only, 07-Oct about 01:45-01:50Z):
- Ubuntu 24.04.4; its time zone is a whole-hour offset from UTC, so minute :17 local is minute :17 UTC.
- `/etc/crontab`: `17 * * * * root cd / && run-parts --report /etc/cron.hourly`, Ubuntu's default hourly slot.
- `/etc/cron.hourly` holds two scripts that are not part of Ubuntu, both dated 2026-04-24 08:02Z, long before
  this project: `free` = `echo 1 > /proc/sys/vm/drop_caches` (drops the clean page cache) and `fstrim` = `fstrim /`.
- sysstat (`sar -B`, the 24 hours from 05-Oct 21:00Z to 06-Oct 21:00Z): the 10-minute
  records that cover :10-:20 show major page faults of 0.5/s or more in 24 of 24 hours. The other five 10-minute slots
  show it in 0 or 1 of 23-24 hours. Mean major faults are 1.3/s in that slot against 0.0-0.2/s elsewhere, which is
  what happens when processes fault their file pages back in after a cache drop.
- Nothing else hourly at :17 on the host: `/etc/cron.d` holds e2scrub (03:10 daily and 03:30 Sundays), sysstat (every
  10 minutes) and a boot-time route. The systemd timers are daily or weekly, except sysstat (every 10 minutes) and a
  user watchdog (every minute, since boot). This account's crontab has nine entries, all daily or weekly and none at
  :17 (schedule fields and script names read, not their arguments).
- Not seen: the cron run lines themselves. `/var/log/syslog` needs the `adm` group, which this account lacks, and
  nothing was changed to read it.
- Not separated: `drop_caches` and `fstrim` run in the same second. The slow decay over 10-20 minutes fits a cache
  that is refilling, not a trim that ends in seconds, but this data cannot split the two.

## 4. The logons in the opening intervals

The extra logons are hourly and come from fixed sources. CHAOS_REAPER's job session gives the baseline of 1 a minute
(`fa_f03`).

| Interval begins at | Logons/min (train / dev / test) | Share of hours with ≥ 2 | Source |
|---|---|---|---|
| :59 | 4.31 / 5.26 / 4.41 | 1.00 | app 900's hourly hand-over: the injector's two dispatcher jobs on the target start new sessions at HH:59:50, and the truth loop (HH:00:20) and the collector (HH:00:48) open their link sessions again (`transcripts/06-injector-invisible.txt:128-129`); SESSIONS dips to 10 |
| :00 | 2.11 / 2.88 / 2.33 | 1.00 | the rest of the hand-over |
| :19 | 2.00 / 2.00 / 2.00 | 1.00 | SYS `OBJNUM_REUSE_MAINTAIN_JOB$$`: ASH at 21:19:52 (`fa_a02`); phase 4b's scheduler log named it at 20:19 on 01-Oct (`transcripts/06-injector-invisible.txt:127`) |
| :21 | 3.54 / 3.49 / 3.39 | 1.00 | Oracle SYS maintenance jobs; ASH caught `CLEANUP_TOBE_DROPPED_IDX` at 19:22:43-44 and KTSJ space slaves at 19:21:49. The others in this cluster ran too briefly to be sampled |
| :22 | 2.29 / 2.10 / 2.00 | 0.96-1.00 | as :21 |
| drifting, every 15 min | ~ +3 over 2-3 min | n/a | Oracle AutoTask: MMON's `AutoTask Dispatcher`, then SYS `SYS_AUTO_STS_MODULE / CAPTURE_CURSOR_CACHE` and `ORA$_ATSK_IVFIBGSCHT` (ASH 17:17:27-17:18:27, 18:17:32-33, 21:17:49-50, ...) |

- **The AutoTask drifts.** Its capture slips about 1.4 s per 15-minute run: ASH shows it at 17:17:28 and 21:17:50,
  22 s apart over 4 hours (`fa_a02`). By minute of the hour mod 15, its excess logons sit at 7-9 on 02-Oct, 9-11 on
  03-Oct, 11-13 on 04-Oct, 13-14 and 0 on 05-Oct (dev), 0-2 on 06-Oct (test) and 2-3 on 07-Oct (`fa_f06`). On the
  test day it landed on minutes :15-:17, :30-:32, :45-:47 and :00-:02. One of those is the cache drop's minute. During
  training it was at :07-:13 (and :22-:28 and so on).
- **What MSET-SPRT names** is the logon count of the opening interval: 2.01 at 10:19:31 and 21:19:30 (the reaper plus
  the :19 SYS job) and 3.01 at 14:21:30 (the :21 cluster). Those logons are Oracle's own and occur every hour of the
  run, training included. What was new on the test day is the state of the other signals around them.

## 5. The hypotheses, one by one

| Hypothesis | Test | Evidence | Verdict |
|---|---|---|---|
| The observer's hourly link hand-over (truth loop, collector) | When do its logons fall? | ANOM_COLLECT_JOB and ANOM_TRUTH_JOB each ran 24 times on the test day, starting at HH:59:48 and HH:59:50, one run an hour (`fa_f08`; contract sections 4 and 12.2). Their logons fall in the :59/:00 intervals (4.41 and 2.33 logons/min on the test day), about 19-22 minutes before every opening. Between hand-overs each keeps one session (ASH: the same collector session at 18:30:48 and 18:57:48, and at 22:16:48, 22:29:48 and 22:59:48) | **Rejected** |
| A collector reconnect | Errors, extra runs, extra sessions | COLLECT_LOG has 0 rows on the test day. All 24 collector runs succeeded. No run started off the hour. SESSIONS stays at 11 in every opening interval | **Rejected** |
| The truth loop's 5-minute refresh | Timing | It runs 3 queries in its open session at HH:x0:20 and HH:x5:20 every hour of every day, training included, with no logon. It is spread evenly, not tied to :19-:21 | **Rejected** |
| App 900's observer jobs | Scheduler log | ANOM_SCORE_JOB runs every minute at second 8 on the observer (1,440 runs, 0 failed) and does not contact the target. No other ANOMOPS job started in minutes :10-:30 of any test-day hour (`fa_f08`). ANOM_RETRAIN_INTERIM (BYMINUTE=17, every 6 h) was disabled at TRAIN_TO | **Rejected** for the test day. During training it added one link logon at :17 in 4 hours a day; 38 of the 46 training hours with the bump had no retraining |
| App 600 / DBOPS | Files and ASH | `dbops/db_ops_pkg.sql` (in the operator's wls15c tree) is an on-demand agent tool over ORADB1_LINK, not a schedule. App 600's hourly items are host-side: Apache log rotation and a purge timer (`logai-purge.timer`, `OnCalendar=hourly`, i.e. :00), and its files never name oradb1. ASH from about 17:09Z to EVAL_TO holds only SYS, SHOP, ANOM_MON and CHAOS_CTL besides the driver: no DBOPS session | **Rejected** as far as the evidence goes (ASH no longer covers 10:19 and 14:21) |
| Oracle auto-tasks | ASH, logon drift | The AutoTask (AUTOSTS + IVF index tasks) is Oracle's own and drifted onto :15-:17 on the test day (section 4). Hourly SYS jobs give the logons at :19 and :21-:22 | **Contributes**: they supply the logons MSET-SPRT names. **Not** the cause of the :17 bump, whose minute is fixed while the AutoTask drifts |
| The load driver | Code, rate, reads, sessions, ASH | `driver/anomaly_driver.py` has no hourly logic; its shape and jitter are continuous. Its rate dips slightly at :17 (41.0 / 40.8 TPS against 42.1-43.9, `fa_f04`), the target's reads stay flat (`fa_f11`), and SESSIONS is 11 in all three openings. Its sessions are the ones whose User I/O jumps at :17:00 | **Rejected** (it is affected, not the cause) |
| The build's own checks | Logons at their minutes | The 15-minute progress checks (STATUS lines at :12, :27, :42, :57) left no logon: 1.0 a minute at those minutes on the test day (`fa_f03`). Phase 4b's 10-minute target check no longer runs (0.99-1.0 at :08, :28, :38, :58) | **Rejected** |
| The demo host's hourly cron | Crontab, scripts, onset, sysstat, other schedules | Section 3 | **Established** as the event at which the :17 bump starts; **likely** as its cause |

## 6. Why the test day, and not training or the dev day

- **The drop's effect grew with the data** (`fa_f05_daily.csv`). The driver inserts orders all day, and the
  target's physical reads per second grew with the tables, so more of each minute's reads depend on the cache that
  the cron job empties:

  | UTC calendar day | Physical reads/s | W_USERIO :16-:25 | W_USERIO :40-:55 | SQL_RT :16-:25 | SQL_RT :40-:55 |
  |---|---|---|---|---|---|
  | 02-Oct (training) | 4.8 | 0.54 | 0.12 | 0.0646 | 0.0523 |
  | 03-Oct (training) | 19.0 | 1.99 | 0.54 | 0.0766 | 0.0565 |
  | 04-Oct (training) | 32.1 | 3.00 | 1.06 | 0.0696 | 0.0527 |
  | 05-Oct (mostly the dev day; clean minutes only) | 39.6 | 5.14 | 1.30 | 0.0854 | 0.0594 |
  | 06-Oct (mostly the test day; clean minutes only) | 61.8 | 7.87 | 1.23 | 0.1001 | 0.0620 |

  Outside the drop window the test day stayed close to late training (W_USERIO 1.23 against 1.06 on 04-Oct, SQL_RT
  0.062 against 0.053). Inside it, the test day was well past anything the 72 training hours had shown.
- **The AutoTask moved onto the drop.** On the test day its logons came at :16-:17 (2.38 and 2.00 a minute against
  1.17 and 1.00 in training), so the post-drop minutes had more logons as well as slower SQL.
- **MSET-SPRT reacted to the joint state, not to one signal.** Each value on its own lies inside training: the :59
  hand-over intervals average 4.31 logons a minute with SQL_RT 0.120 and W_USERIO 5.55, at 10 sessions (`fa_f03`),
  and the static rival's p99.5 lines sit above every opening value (below). The combination of 2-3 logons, 11
  sessions and a post-drop User I/O wait of 9-11 was new.
- **Dev versus test, MSET-SPRT's own scores** (`fa_f07_mset_hourly.csv`, minutes :14-:29 of hours clear of every run
  and cool-down). Dev day: 6 such hours, highest score 0.33, no flag. Test day: 7 such hours. Four stay at or below
  0.17 (02, 05, 08, 00 h). Three reach 0.75 and flag: 10, 14 and 21 h, the three false alarms.
- **The static rival stayed silent** because no signal breached its p99.5 line in the three consecutive minutes R1 needs to open an episode. Its lines are LOGONS 4.07 a minute (it
  learned the :59 hand-over), SQL_RT 0.1516 and W_USERIO 13.43 (`fa_f10`). Single minutes did cross after each drop (`fa_f02`): SQL_RT at 10:16:30, 14:16:30 and 21:16:30 (0.1813, 0.1629, 0.1527),
  W_USERIO at 10:16:30, 14:17:30 and 14:19:30 (14.02, 16.82, 13.76), never the same signal three minutes running. The opening intervals were 2-3 logons, 0.104-0.119 and 9.2-11.0.

## 7. False alarm 1 cost MSET-SPRT run 122

Run 122 (conn_leak LOW, 10:33:02-10:50:03) started while episode 7 was open. MSET-SPRT's last flag in it had been at
10:26:30, followed by 7 unflagged intervals (10:27:29-10:33:31). The rule needs 10 to close an episode. MSET-SPRT then
flagged every interval from 10:34:30 to 10:50:30 with SESSIONS first (weight 0.6 rising to 1.0). That is 1.47 minutes
after the start, on the same interval in which the static rival opened its catch, and SESSIONS is one of the
scenario's expected signals (`fa_f09_run122.csv`, `csv/q02_incidents.csv`). Because those flags extended episode 7, no
new episode started inside run 122's window, and grade() counts it as missed. That is correct under the pre-registered
rule. Run 122 is the only discordant incident between MSET-SPRT and the static rival (b = 0, c = 1).

Episode 13 came close to doing the same: it closed at 21:31:29, 28 seconds after run 128 (conn_leak HIGH) started at
21:31:01. The next flag, at 21:32:30, opened a new episode and caught it.

## 8. What this means for the post

- **Allowed:** "The monitor's own footprint caused none of MSET-SPRT's three test-day false alarms." Its logons fall
  at the hour, it never reconnected, and every false alarm opens about 19-22 minutes after the hand-over.
- **Allowed:** "All three opened within 5 minutes of an hourly cron job on the lab host that drops the page cache
  and trims the filesystem (at :17), on an interval in which Oracle's own hourly maintenance jobs logged on." State
  "likely" for the causal link from the cron job to the flags: the timing is 3 of 3, but nothing was run without the
  cron job to prove it.
- **Allowed and worth saying:** the drop is a real, recurring slowdown. The application's p95 latency for that minute
  is roughly 3 to 6 times that of the minutes either side, and the slowdown grew as the data grew. MSET-SPRT
  flagged a real change in how the system behaved. The grading still counts the episodes as false alarms, because nobody injected anything, and the post must
  not relabel them.
- **Allowed:** the difference in catches between MSET-SPRT and the static rival comes down to one incident (run 122),
  and MSET-SPRT flagged that incident within 1.5 minutes inside one of these false-alarm episodes. Report this beside
  the official result, not instead of it: no number in test_results.md changes.
- **Threat to validity to state:** the target's normal changed over the five days. Physical reads grew from 4.8/s to
  61.8/s while the shop's tables grew, so in the minutes after the drop the test day was not the training days'
  "normal". The FINAL models' picture of normal was two days old by the test day, and the database had changed.
- **Not allowed:** "the monitor caused N false alarms" (it caused none); "Oracle's auto-tasks caused them" (they supply
  the logons but not the slowdown); any re-scored or adjusted false-alarm count.
- **For the operator, not changed here:** the lab host's `/etc/cron.hourly/free` and `/etc/cron.hourly/fstrim` (dated
  2026-04-24) predate this project. Switching them off for a few hours is the direct test of section 1's "likely",
  and would probably take the bump out of later runs. Both are the operator's call. Doing it would also change the
  workload the FINAL models were trained on.

## 9. Limits

- In-memory ASH on the target held 06-Oct 17:08:41Z onwards at the final run (`fa_a01`; 17:00:48Z at the first look,
  01:44Z), so it holds false alarm 3 but not 1 and 2. For those, the attribution rests on the logons' fixed hourly timing (2.00 a minute at :19 in every clean hour of
  training, dev and test) and the same run-up (section 2).
- ASH samples active sessions once a second. A job or logon shorter than that is often missed, which is why only one
  of the :21-:22 jobs is named.
- The cron run lines were not read (no access to syslog). The schedule, the scripts, the onset at :17:00-:17:14 and
  the major-fault slot are the evidence.
- No counterfactual: the cron job was not disabled and nothing was re-scored. Section 1 says "likely" for the cause of
  the bump and of the flags for that reason.
- Alternatives not excluded: the slowdown could come partly or wholly from `fstrim`'s discard work in the storage
  layer rather than from the cache drop. The major faults in that slot and the 10-20 minute recovery favour the cache
  drop, but do not rule out a share for the trim. The target's I/O path (buffered or direct I/O) was not read, so the
  claim that Oracle's reads go through the OS cache is inferred from the flat read count with a longer wait.
- Counts are small: 3 false alarms, 7 clean test-day hours.

## 10. What was run, and what was not touched

- `results/false_alarms_queries.sql` v1.1 as ANOMOPS on ORCLPDB1 (F01-F11 observer tables, A01-A04 ASH through
  ANOM_MON_LINK), 07-Oct 02:09:07-02:09:23Z. Two earlier runs (01:56Z and 01:58Z, v1.0, without F04's TPS column)
  agreed with it on every block except the ASH span. The CSVs are `results/csv/fa_*.csv` (17 files). Exploratory
  queries before that (01:41-02:05Z) used the same path.
- ASH through ANOM_MON_LINK is the contract's incident drill-down path (contract section 10, page 2; section 13.4: the
  grant is SELECT on V$ACTIVE_SESSION_HISTORY only; the estate has the Diagnostics Pack). It was read after EVAL_TO,
  so no training exclusion window was needed. Each read was a SELECT, the link was closed at the end, nothing was
  written on either side, and ANOM_UI_ASH / ANOM_UI_AUDIT were not used.
- Demo host, read-only over ssh: `/etc/os-release`, `timedatectl`, `/etc/crontab`, the two files in `/etc/cron.hourly`
  and their dates, the schedule fields of `/etc/cron.d`, the system and user systemd timer lists and three timer
  schedules, this account's crontab (schedule fields and script names only, no arguments), and `sar -B` / `sar -r`
  for those 24 hours. No service, process or other user's configuration was read, and nothing was changed.
- Not touched: DBOPS, ORADB1_LINK, any operator session or process; the graded data (SCORE_EVAL, ALERT_EVAL,
  GRADE_*), the FINAL models, the models in ASKORACLE; the target's objects. No password, IP address or OCID is in
  this file or the CSVs (checked by pattern; program names are cut to the process name).
