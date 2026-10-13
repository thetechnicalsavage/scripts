# v1.4 2026-10-10  Brief 12 PLAN 11.4 (v0.2): the end-of-stage manifest sweep. `start <stage>` records the database's
#                  clock (sysdate minus one second) in state/; `end <stage>` merges every object created since then
#                  that a known store owns into AGENTMEM.POC_MANIFEST and reports anything unlisted.
#                  v1.4: (Codex r4 of step 1C, finding 2) `end` also fails (exit 1) on a manifest ownership conflict:
#                  an object of the window, or any existing object, listed under a store id other than its 11.4 owner.
#                  v1.3: (Codex r3 of step 1C, finding 1) `start` only reads the database clock (a fresh S1 creates the
#                  manifest in its first store open, after `start`); `end` requires the manifest before it merges.
#                  v1.2: (Codex r2 of step 3, finding 1) no DDL: the manifest is required (lib.stores.require_manifest),
#                  never created here.
#                  v1.1: (Codex r1 of step 3, finding 3) optional LABEL ([a-z0-9_]{1,24}): the window file, the log tag
#                  and the result file carry it (manifest_sweep_<label>.json), so a stage run twice keeps both records.
#                  v1.0: first version (day 1).
"""Usage: .venv/bin/python py/sweep_manifest.py start|end <stage> [<label>]
Writes staging/<stage>/results/manifest_sweep[_<label>].json on `end` (recorded count per store, objects created in
the window that no rule owns, and every AGENTMEM object the manifest does not list now). Exit 0; 1 if something is
unlisted (the stage then stops: the rollback guard would refuse, so the operator must see it); 2 usage.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402

import re  # noqa: E402

if (len(sys.argv) not in (3, 4) or sys.argv[1] not in ("start", "end") or not sys.argv[2].isalnum()
        or (len(sys.argv) == 4 and not re.fullmatch(r"[a-z0-9_]{1,24}", sys.argv[3]))):
    sys.stderr.write(__doc__)
    sys.exit(2)
MODE, STAGE = sys.argv[1], sys.argv[2]
LABEL = sys.argv[3] if len(sys.argv) == 4 else ""
SUFFIX = f"_{LABEL}" if LABEL else ""
RUN = runlog.start(STAGE, tag=f"{STAGE}{SUFFIX}_sweep_{MODE}")

from lib import amconf, stores  # noqa: E402

T0_FILE = amconf.STATE / f"stage_t0_{STAGE}{SUFFIX}.json"
pool = amconf.make_pool("AGENTMEM", 1, 1)
rc = 0
with pool.acquire() as conn:
    if MODE == "start":                           # read-only: the database clock only
        t0 = stores.db_t0(conn)
        amconf.STATE.mkdir(parents=True, exist_ok=True)
        T0_FILE.write_text(json.dumps({"t0_db": t0.strftime("%Y-%m-%d %H:%M:%S")}) + "\n", encoding="utf-8")
        RUN.log.info("stage window starts at the database clock")
    else:
        stores.require_manifest(conn)             # raises if POC_MANIFEST is incomplete: the stage stops
        t0 = dt.datetime.strptime(json.loads(T0_FILE.read_text(encoding="utf-8"))["t0_db"], "%Y-%m-%d %H:%M:%S")
        res = stores.sweep(conn, t0)
        res["unlisted_now"] = stores.unlisted_now(conn)
        res["manifest_conflicts_now"] = stores.manifest_conflicts(conn)
        RUN.write_json("results", f"manifest_sweep{SUFFIX}.json", res)
        if res["unlisted"] or res["unlisted_now"]:
            RUN.log.error("unlisted AGENTMEM objects: %s", res["unlisted_now"] or res["unlisted"])
            rc = 1
        if res["conflicts"] or res["manifest_conflicts_now"]:
            RUN.log.error("manifest ownership conflicts: %s", res["manifest_conflicts_now"] or res["conflicts"])
            rc = 1
pool.close()
sys.exit(rc)
