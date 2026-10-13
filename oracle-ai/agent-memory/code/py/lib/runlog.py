# v1.0 2026-10-10  Brief 12 PLAN 6 and 10.3: start of every step script (redaction first, secrets registered,
#                  environment checked) and the staging writer for its results and transcript.
#                  v1.0: first version (day 1).
"""Step scaffolding.

    from lib import runlog                # first import of every step script
    run = runlog.start("s1")              # redaction installed, secrets registered, litellm switches asserted
    run.write_json("results", "statuses.json", obj)
    run.transcript("...")                 # transcripts/<step>/<step>_transcript.md, line by line
    run.finish(summary)                   # run summary json; then tools/stage_scan.py <step> moves it out

Everything goes to ~/agentmem-poc/staging/<step>/{results,transcripts}/ first (outside every git repository);
tools/stage_scan.py scans it for the secret values and the leak patterns and only then moves it to
~/agentmem-poc/out/<step>/, from where it is copied into the brief (PLAN 10.3).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from lib import redact  # noqa: E402  (must be first; nothing else is imported before the handlers exist)


class Run:
    def __init__(self, step: str, base: Path, tag: str | None = None) -> None:
        self.step = step
        self.tag = tag or step                 # file-name prefix: several scripts can share one step folder
        self.base = base
        self.t0 = time.time()
        self.results = base / "results"
        self.transcripts = base / "transcripts"
        self.results.mkdir(parents=True, exist_ok=True)
        self.transcripts.mkdir(parents=True, exist_ok=True)
        self._tx = self.transcripts / f"{self.tag}_transcript.md"
        self.log = logging.getLogger(f"poc.{step}")

    def path(self, kind: str, name: str) -> Path:
        d = {"results": self.results, "transcripts": self.transcripts}[kind]
        if "/" in name or name.startswith("."):
            raise ValueError("flat file names only")
        return d / name

    def write_json(self, kind: str, name: str, obj) -> Path:
        p = self.path(kind, name)
        text = json.dumps(obj, indent=1, sort_keys=True, default=str, ensure_ascii=False) + "\n"
        p.write_text(redact.redact(text), encoding="utf-8")
        return p

    def write_text(self, kind: str, name: str, text: str) -> Path:
        p = self.path(kind, name)
        p.write_text(redact.redact(text), encoding="utf-8")
        return p

    def transcript(self, text: str = "") -> None:
        with self._tx.open("a", encoding="utf-8") as fh:
            fh.write(redact.redact(text) + "\n")

    def finish(self, summary: dict) -> None:
        summary = dict(summary)
        summary["step"] = self.step
        summary["script_tag"] = self.tag
        summary["wall_seconds"] = round(time.time() - self.t0, 3)
        summary["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.write_json("results", f"{self.tag}_run_summary.json", summary)
        self.log.info("%s finished in %.1f s", self.tag, summary["wall_seconds"])


def start(step: str, *, tag: str | None = None, need_oci: bool = False) -> Run:
    for x in (step, tag or step):
        if not x.replace("_", "").isalnum():
            raise ValueError("bad step or tag name")
    from lib import amconf
    base = amconf.STAGING / step
    base.mkdir(parents=True, exist_ok=True)
    redact.install(base / "transcripts" / f"{tag or step}_run.log")
    amconf.register_all_secrets()
    if need_oci and not amconf.OCI_CONFIG.exists():
        raise amconf.ConfigError("oci_agentmem/config missing: build it first (tools/build_oci_agentmem_config.py)")
    amconf.prepare_environment()
    run = Run(step, base, tag)
    run.log.info("%s started; staging folder %s", run.tag, base.name)
    return run
