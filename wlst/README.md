# WLST scripts

All read-only unless the header says otherwise. None of them change state.

| Script | What it tells you |
|---|---|
| [`server-health.py`](server-health.py) | State and health per server, plus which subsystem is unhappy |
| [`heap-report.py`](heap-report.py) | Heap used, free and max per server, as a percentage |
| [`app-status.py`](app-status.py) | Deployment state per application **per target** |

## Credentials

No password goes in a script or on a command line. Either set:

```bash
export WLS_URL=t3://host01.example.internal:7001
export WLS_USER=weblogic
export WLS_PASS='...'
```

or, better, create a user-config file once from a WLST session:

```python
connect('weblogic', '...', 't3://host01.example.internal:7001')
storeUserConfig('/opt/app/wlst/wls-config', '/opt/app/wlst/wls-key')
```

then:

```bash
export WLS_CONFIG=/opt/app/wlst/wls-config
export WLS_KEY=/opt/app/wlst/wls-key
```

The second form keeps the password out of your shell history and out of `ps`.

## Running

```bash
java weblogic.WLST server-health.py
```

Each script exits non-zero when it finds something wrong, so they drop straight into
a monitoring check or a cron job without wrapping.

## A note on the health check

`server-health.py` reports the failed **subsystem**, not just the overall state. A
server sitting in `RUNNING` with a failed JDBC subsystem looks green in a state-only
check, and that is the case that produces errors nobody can reproduce.
