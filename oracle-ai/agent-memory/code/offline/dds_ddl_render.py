# v1.0 2026-10-12  Brief 12 step 9 (PLAN v0.3.8 6 row 9): the Deep Data Security DDL that oracleagentmemory 26.8.0
#                  would issue for store OPS of schema AGENTMEM, rendered OFFLINE from core/deepsec/ (no database, no
#                  network, no LLM). v1.0: first version (phase 2).
"""Render the SDK's Deep Data Security statements without a database.

The SDK's public administration functions (core/deepsec/admin.pyc) take an open connection and issue their DDL through
it. Here they get a recording stand-in: every cursor.execute / callproc is written down and every query returns no
row (so the SDK sees no existing data role or data grant and takes its "create" path). Nothing reaches a database:
the stand-in has no network code. What is recorded is exactly the text the 26.8.0 wheel builds.

Calls rendered (policies = UserOwnRowsDeepDataSecurityPolicy, GlobalMemoriesDeepDataSecurityPolicy):
  1. add_deep_data_security_policies(conn, owner_schema='AGENTMEM', memory_store_id='OPS', policies)
  2. grant_agent_memory_policies(conn, memory_store_id='OPS', owner_schema='AGENTMEM',
       principals=[LocalEndUserPrincipal('SUPPORT_AGENT_1'), OciGroupPrincipal('agent-memory-support')], policies)
The principal names are invented placeholders for the render; no such user or group exists.

Usage: python code/offline/dds_ddl_render.py <out-file>     (with the hash-pinned venv; LITELLM_LOCAL_MODEL_COST_MAP)
Exit: 0 rendered; 1 a call raised (the error is written to the output and the run fails).
"""
from __future__ import annotations

import sys
import textwrap


class _Var:
    def getvalue(self, *a):
        return None

    def setvalue(self, *a):
        return None


class RecordingCursor:
    """A cursor that records what it is asked to run and returns no rows."""

    def __init__(self, log: list) -> None:
        self.log = log
        self.description = None
        self.rowcount = 0

    def execute(self, sql, *args, **kwargs):
        self.log.append(("execute", sql, args, kwargs))

    def executemany(self, sql, *args, **kwargs):
        self.log.append(("executemany", sql, args, kwargs))

    def callproc(self, name, *args, **kwargs):
        self.log.append(("callproc", name, args, kwargs))

    def callfunc(self, name, *args, **kwargs):
        self.log.append(("callfunc", name, args, kwargs))
        return None

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def fetchmany(self, *a):
        return []

    def var(self, *a, **k):
        return _Var()

    def __iter__(self):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        return None


class RecordingConnection:
    def __init__(self) -> None:
        self.log: list = []

    def cursor(self):
        return RecordingCursor(self.log)

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))


def _fmt(entry) -> str:
    kind = entry[0]
    if kind in ("commit", "rollback"):
        return f"-- {kind}"
    if kind in ("execute", "executemany"):
        sql = textwrap.dedent(entry[1]).strip()
        binds = entry[2] or ()
        kw = entry[3] or {}
        tail = ""
        if binds or kw:
            tail = f"\n-- binds: {list(binds)} {dict(kw)}"
        return sql + (";" if not sql.rstrip().endswith(";") else "") + tail
    return f"-- {kind} {entry[1]} {list(entry[2])} {dict(entry[3])}"


def render() -> tuple[str, int]:
    import oracleagentmemory
    from oracleagentmemory.core import deepsec as d

    version = getattr(getattr(oracleagentmemory, "version", None), "__version__", None) or \
        getattr(oracleagentmemory, "__version__", "?")
    out = [f"-- oracleagentmemory {version} core/deepsec, rendered offline by code/offline/dds_ddl_render.py v1.0 "
           "(recording connection: no database)",
           "-- Every query below returned no row to the SDK, so it took its create path. Not run anywhere.",
           "-- Artifact of the recording: the OCI group's mapped data role is looked up once per policy and, since the",
           "-- lookup returns no row here, CREATE DATA ROLE ... MAPPED TO appears twice; a real database would find the",
           "-- role the first statement created and issue it once.", ""]
    policies = [d.UserOwnRowsDeepDataSecurityPolicy(), d.GlobalMemoriesDeepDataSecurityPolicy()]
    rc = 0
    calls = [
        ("add_deep_data_security_policies(conn, owner_schema='AGENTMEM', memory_store_id='OPS', "
         "policies=[UserOwnRows, GlobalMemories])",
         lambda c: d.add_deep_data_security_policies(c, owner_schema="AGENTMEM", memory_store_id="OPS",
                                                     policies=policies)),
        ("grant_agent_memory_policies(conn, memory_store_id='OPS', owner_schema='AGENTMEM', principals=["
         "LocalEndUserPrincipal('SUPPORT_AGENT_1'), OciGroupPrincipal('agent-memory-support')], policies=[...])",
         lambda c: d.grant_agent_memory_policies(c, memory_store_id="OPS", owner_schema="AGENTMEM",
                                                 principals=[d.LocalEndUserPrincipal("SUPPORT_AGENT_1"),
                                                             d.OciGroupPrincipal("agent-memory-support")],
                                                 policies=policies)),
    ]
    totals = []
    for title, fn in calls:
        conn = RecordingConnection()
        out.append(f"-- ===== {title}")
        try:
            fn(conn)
        except Exception as exc:          # reported in the output; the run then exits 1
            out.append(f"-- RAISED {type(exc).__name__}: {exc}")
            rc = 1
        ddl = [e for e in conn.log if e[0] in ("execute", "executemany")
               and not textwrap.dedent(e[1]).lstrip().upper().startswith("SELECT")]
        sel = [e for e in conn.log if e[0] in ("execute", "executemany")
               and textwrap.dedent(e[1]).lstrip().upper().startswith("SELECT")]
        totals.append((title.split("(")[0], len(ddl), len(sel)))
        for e in conn.log:
            out.append(_fmt(e))
            out.append("")
    out.append("-- ===== totals (statements other than SELECT, SELECT queries) per call")
    for name, n_ddl, n_sel in totals:
        out.append(f"-- {name}: {n_ddl} DDL/grant statement(s), {n_sel} read quer(ies)")
    return "\n".join(out) + "\n", rc


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(__doc__)
        return 2
    text, rc = render()
    with open(argv[1], "w", encoding="utf-8") as fh:
        fh.write(text)
    sys.stdout.write(f"dds_ddl_render: wrote {argv[1]} ({text.count(chr(10))} lines), exit {rc}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
