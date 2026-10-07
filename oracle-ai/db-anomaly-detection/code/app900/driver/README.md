<!-- v1.2 2026-10-07 - app 900 load driver: install, start, stop, status, logs, control. v1.0: first version. v1.1: public copy: host name and local-time references removed; the secrets file path is a placeholder. v1.2: the private repo path removed from the test lines. -->
# App 900 load driver

The SHOP application's client. It runs the five contract transactions against `SHOP_APP@oradb1/FREEPDB1`,
follows a UTC daily load shape, obeys `SHOP.DRIVER_CONTROL`, and writes one row of client-side metrics per UTC
minute to `ANOMOPS.APP_MINUTE` on the observer (`ANOM_FEED@ora26ai/ORCLPDB1`). The names and shapes are set in
`../docs/contract.md` (sections 2 and 3); the design is in `PLAN.md` of brief 10.

| File | What |
|---|---|
| `anomaly_driver.py` | the driver (Python 3.12, python-oracledb 3.4.2 thin) |
| `driver.ini` | hosts, ports, services, users, workers, seed, data ranges, log settings, the secrets file path. **No secrets.** |
| `requirements.txt` | the pinned dependency closure |
| `anomaly-driver.service` | user-level systemd unit |
| `install.sh` | idempotent installer (does not start the service) |
| `test_driver.py` | pytest, no database (a fake oracledb); not included in this public copy |

## Install or update (on the demo VM)
```bash
# from the staging copy of the repo's driver/ directory
bash ~/app900/driver/install.sh
```
It creates `~/anomaly/` (mode 700), `~/anomaly/logs/`, the venv `~/anomaly/venv`, pip-installs the pinned
requirements, copies the code, `driver.ini` (a different existing one is kept as `driver.ini.bak.<UTC stamp>`)
and the unit to `~/.config/systemd/user/`, runs `systemctl --user daemon-reload`, and validates the config and the
secrets file keys with `--check-config` (no database connection). Re-running it is safe. It never starts,
enables or restarts the service; after an update of a running driver, restart it yourself.

Check the venv: `~/anomaly/venv/bin/python -c 'import oracledb; print(oracledb.__version__)'` prints `3.4.2`.

## Start, stop, status, logs
```bash
systemctl --user start anomaly-driver          # start now
systemctl --user enable anomaly-driver         # also start at boot (linger is on)
systemctl --user stop anomaly-driver           # SIGTERM: finishes transactions, flushes finished minutes
systemctl --user restart anomaly-driver
systemctl --user status anomaly-driver
journalctl --user -u anomaly-driver -n 50      # warnings and errors (stderr)
tail -f ~/anomaly/logs/driver.log              # every JSON line (rotating, 10 x 5 MB)
grep '"msg": "minute"' ~/anomaly/logs/driver.log | tail -5    # the per-minute counts
```
The unit restarts the driver 10 s after a failure. Exit code 2 (a configuration or secrets error) is not
restarted: fix the file, then start it again. A clean stop exits 0.

## What it does
- **Pool**: `SHOP_APP` at `localhost:1522/FREEPDB1`, min 2, max 16, program `anomaly-driver`; module/action of
  each session = `anomaly-driver` / the transaction name (visible in ASH).
- **Workers** (default 6, hard max 16): pick a transaction by the mix (browse 45, search 10, place_order 20,
  pay 15, order_status 10), run it with binds, record latency (ms, monotonic clock, connection acquire and commit
  included) and outcome, then wait for the next start. Starts follow a seeded Poisson process at the worker's
  share of the target rate; when a transaction takes longer than the gap, the next starts at once (no backlog), so
  a slow database lowers throughput like a real application. Any database error rolls the transaction back and
  counts as an error for the minute; the worker continues.
- **Load shape** (fraction of `peak_tps`, UTC): 23:00-04:00 0.25; 04:00-06:00 ramp to 1.0; 06:00-09:00 1.0;
  09:00-15:00 0.6; 15:00-18:00 1.0; 18:00-23:00 ramp down to 0.25. Times (1 + jitter), the jitter drawn once per
  UTC minute from the seed (+/- 5%), times `DRIVER_CONTROL.load_pct / 100` (clamped 0-300).
- **Orders**: 1-4 distinct products in ascending id order (fixed lock order, no deadlocks), 20% include one hot
  product (1-50). `pay` pays the oldest of this worker's last 50 NEW orders; before the first order exists it
  places one instead. The amount is a seeded draw (5-500), the method one of `pay_methods`.
- **Seed**: `[load] seed` drives every draw (mix, think times, binds, jitter); it is written in the start line.

## SHOP.DRIVER_CONTROL (read every 10 s)
| Column | Effect |
|---|---|
| `enabled` | `N` pauses the workers (minutes are still written, with zero counts) |
| `load_pct` | multiplies the load shape (clamped 0-300) |
| `conn_leak_target` | extra standalone sessions opened and held idle (clamped 0-40); 0 closes them |
| `logon_storm` | `Y`: every transaction connects and disconnects standalone instead of using the pool |
| `error_pct` | share of place_order calls sent with a product that does not exist (they fail with -20001) |

NULLs take the column defaults; out-of-range values are clamped and logged. If the read fails, the last state stays.

## The per-minute row (`ANOMOPS.APP_MINUTE`, MERGE as ANOM_FEED)
`ts_minute` = start of the UTC minute just finished (DATE); `n_ok`, `n_err`; `app_tps` = (ok + err) / 60;
`app_p50_ms`, `app_p95_ms`; `app_err_pct` = 100 x err / (ok + err), 0 when nothing ran; `loaded_ts` = UTC time of
the write. A transaction belongs to the minute in which it finished. The minute the driver started in is partial
and is not sent, nor is the open minute at stop. Minutes with no transactions are sent as zero rows (percentiles
NULL). If the observer is unreachable, finished minutes stay in memory (up to 120, the oldest dropped beyond that
with a warning) and the whole buffer is sent in time order, in one transaction, on the next minute that reaches
the observer. A failure is a warning, never a crash.

**Percentiles** are exact nearest-rank over every transaction that completed in the minute (successful and
failed): sort the n latencies ascending and take the value at 1-based rank `ceil(p * n / 100)` (integer
arithmetic). No interpolation, no sampling, no buckets. Example: 20 values, p95 = the 19th.

## Logs
JSON lines: `ts` (UTC, ms, `Z`), `level`, `logger`, `thread`, `msg`, and fields. One INFO `minute` line per
minute with the counts, the feed status (`ok`, `buffered`) and the control state. Warnings for failed transactions
(other than the expected ORA-20001) are limited to one per error code per minute. Every value of the secrets file
is masked as `***` in every line, including exceptions; unhandled exceptions go through the same formatter.

## Secrets
Read once at start from `[secrets] file` (`<secrets-file>`: set it to your own file, mode 600, `KEY=VALUE`), keys
`SHOP_APP_PWD` and `ANOM_FEED_PWD`. Never in `driver.ini`, a command line, an environment variable or a log.

## Tests
```bash
python3 -m pytest -q test_driver.py   # the unit tests are not included in this public copy
```
No database: oracledb is replaced by a fake. They cover the load shape, the mix over 100k draws, nearest-rank
percentiles, minute rollover, the offline buffer, DRIVER_CONTROL parsing, error_pct routing, secret masking, and a
real SIGTERM against a subprocess.
