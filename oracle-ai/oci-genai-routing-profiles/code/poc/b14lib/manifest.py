# v1.1 2026-10-10  Brief 14: the resource manifest (PLAN.md v1.1 section 8).
#   v1.1: ownership_problems(): what a resource must show before anything deletes it (Codex plan review r1, finding 2);
#         INTENT counts as live: a crash between the write-ahead and the answer leaves a resource that may exist
#         (Codex code review r1, finding 1).
#   v1.0: first version.
"""Every OCI resource the POC creates, written ahead of the create and updated after it.

The file holds real OCIDs, so it lives only in the secrets folder on the demo VM, mode 600, and is rewritten
atomically under an exclusive lock. redacted() is the only view that may leave the VM.

States: INTENT -> CREATED | REFUSED | UNKNOWN -> DELETED | DELETE_FAILED (and UNKNOWN -> CREATED/REFUSED on resolution).
"""
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from . import phoenix
from .ocisign import OCID_RE, sha16

TYPES = ("generativeairoutingprofile", "generativeaiproject")
LIVE_STATES = ("INTENT", "CREATED", "UNKNOWN", "DELETE_FAILED")      # may exist in OCI (INTENT: Codex r1 finding 1)
TRANSITIONS = {
    "INTENT": {"CREATED", "REFUSED", "UNKNOWN"},
    "UNKNOWN": {"CREATED", "REFUSED"},
    "CREATED": {"DELETED", "DELETE_FAILED"},
    "DELETE_FAILED": {"DELETED", "DELETE_FAILED"},
    "REFUSED": set(),
    "DELETED": set(),
}


class ManifestError(RuntimeError):
    pass


def ownership_problems(obj: dict, entry: dict, compartment: str, baseline_sha16=(), require_tags: bool = True) -> list:
    """Why a resource (as GET returned it) must NOT be deleted as this entry's, or [] when it may.

    Checked before every DELETE (PLAN.md section 12): the OCID is the entry's, of its type and Phoenix region key;
    the display name starts with 'b14-'; it sits in our compartment; it was not there at the S0 baseline; and (unless
    the OCID came from our own create answer in this process) it carries our freeform tags brief=b14 and this run.
    """
    problems = []
    oc = obj.get("id") or ""
    m = OCID_RE.fullmatch(oc)
    if oc != entry.get("ocid"):
        problems.append("OCID differs from the manifest entry")
    if not m or m.group(1) != entry.get("type"):
        problems.append("not the entry's resource type")
    elif m.group(3) != phoenix.REGION_KEY:
        problems.append("not in the Phoenix region")
    if not str(obj.get("displayName", "")).startswith("b14-"):
        problems.append("display name does not start with b14-")
    if obj.get("compartmentId") != compartment:
        problems.append("not in our compartment")
    if oc and sha16(oc) in set(baseline_sha16):
        problems.append("it existed before the POC (S0 baseline)")
    if require_tags:
        tags = obj.get("freeformTags") or {}
        if tags.get("brief") != "b14" or tags.get("run") != entry.get("run_id"):
            problems.append("freeform tags brief=b14 and this run id are missing")
    return problems


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def retry_token(run_id: str, alias: str, seq: int) -> str:
    """Deterministic per run, alias and manifest entry: re-sending the same entry's create within 24 h returns the
    original resource, while a new attempt after a refusal (a new entry) gets a new token."""
    return hashlib.sha256(f"b14|{run_id}|{alias}|{seq}".encode()).hexdigest()[:32]


class Manifest:
    def __init__(self, path: Path, run_id: str):
        self.path = Path(path)
        self.run_id = run_id

    # ------------------------------------------------------------------ storage
    @contextmanager
    def _locked(self):
        if not self.path.parent.is_dir():
            raise ManifestError("the manifest folder does not exist (it must be the existing secrets folder)")
        lock = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            st = self._load()
            yield st
            self._save(st)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "brief": "b14", "run_id": self.run_id, "created_utc": _now(), "entries": []}
        mode = stat.S_IMODE(self.path.stat().st_mode)
        if mode & 0o077:
            raise ManifestError(f"the manifest must be mode 600, found {oct(mode)}")
        st = json.loads(self.path.read_text(encoding="utf-8"))
        if st.get("brief") != "b14":
            raise ManifestError("this file is not the brief 14 manifest")
        return st

    def _save(self, st: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".b14_manifest.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(st, fh, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @staticmethod
    def _entry(st: dict, seq: int) -> dict:
        e = next((x for x in st["entries"] if x["seq"] == seq), None)
        if e is None:
            raise ManifestError(f"no manifest entry {seq}")
        return e

    @staticmethod
    def _move(e: dict, new: str) -> None:
        if new not in TRANSITIONS[e["state"]]:
            raise ManifestError(f"entry {e['seq']}: {e['state']} -> {new} is not allowed")
        e["state"] = new

    # ------------------------------------------------------------------ writes
    def intent(self, alias: str, rtype: str, display_name: str, region: str = phoenix.REGION) -> dict:
        """Write-ahead record before a create. Re-calling for an alias still in INTENT/UNKNOWN returns that entry."""
        if rtype not in TYPES:
            raise ManifestError(f"unknown resource type {rtype!r}")
        if region != phoenix.REGION:
            raise phoenix.RegionLockError("a manifest entry must be in the Phoenix region")
        if not display_name.startswith("b14-"):
            raise ManifestError("display names must start with 'b14-'")
        with self._locked() as st:
            for e in st["entries"]:
                if e["alias"] == alias and e["run_id"] == self.run_id and e["state"] in ("INTENT", "UNKNOWN"):
                    return dict(e)
                if e["alias"] == alias and e["run_id"] == self.run_id and e["state"] in ("CREATED", "DELETE_FAILED"):
                    raise ManifestError(f"{alias} already exists in this run (entry {e['seq']})")
            seq = 1 + max((e["seq"] for e in st["entries"]), default=0)
            e = {"seq": seq, "run_id": self.run_id, "alias": alias, "type": rtype, "display_name": display_name,
                 "region": region, "state": "INTENT", "retry_token": retry_token(self.run_id, alias, seq),
                 "intent_utc": _now(), "ocid": None, "ocid_sha16": None, "created_utc": None, "http_status": None,
                 "error_code": None, "error_message": None, "deleted_utc": None, "delete_status": None}
            st["entries"].append(e)
            return dict(e)

    def created(self, seq: int, ocid: str, http_status: int, created_utc: str | None = None) -> None:
        m = OCID_RE.fullmatch(ocid or "")
        if not m:
            raise ManifestError("the create answer carried no OCID")
        with self._locked() as st:
            e = self._entry(st, seq)
            if m.group(1) != e["type"]:
                raise ManifestError(f"entry {seq}: the OCID's type does not match the entry")
            if m.group(3) and m.group(3) != phoenix.REGION_KEY:
                raise phoenix.RegionLockError(f"entry {seq}: the OCID's region key is not {phoenix.REGION_KEY}")
            self._move(e, "CREATED")
            e.update({"ocid": ocid, "ocid_sha16": sha16(ocid), "http_status": http_status,
                      "created_utc": created_utc or _now()})

    def refused(self, seq: int, http_status: int | None, code: str | None, message: str | None) -> None:
        with self._locked() as st:
            e = self._entry(st, seq)
            self._move(e, "REFUSED")
            e.update({"http_status": http_status, "error_code": code, "error_message": message})

    def unknown(self, seq: int, error: str) -> None:
        with self._locked() as st:
            e = self._entry(st, seq)
            self._move(e, "UNKNOWN")
            e.update({"error_code": "UNKNOWN_OUTCOME", "error_message": error})

    def deleted(self, seq: int, http_status: int) -> None:
        with self._locked() as st:
            e = self._entry(st, seq)
            self._move(e, "DELETED")
            e.update({"deleted_utc": _now(), "delete_status": http_status})

    def delete_failed(self, seq: int, http_status: int | None, message: str | None) -> None:
        with self._locked() as st:
            e = self._entry(st, seq)
            self._move(e, "DELETE_FAILED")
            e.update({"delete_status": http_status, "error_message": message})

    # ------------------------------------------------------------------ reads
    def entries(self) -> list:
        return [dict(e) for e in self._load()["entries"]]

    def pending(self, alias: str) -> dict | None:
        """This run's INTENT/UNKNOWN entry for an alias (a create whose outcome is not recorded yet), or None."""
        return next((e for e in self.entries()
                     if e["alias"] == alias and e["run_id"] == self.run_id and e["state"] in ("INTENT", "UNKNOWN")),
                    None)

    def live(self) -> list:
        """Entries that may exist in OCI, newest first (the clean-up order)."""
        return sorted((e for e in self.entries() if e["state"] in LIVE_STATES), key=lambda e: -e["seq"])

    def count_created(self, rtype: str) -> int:
        """Resources of this type that were created, or may have been (INTENT and UNKNOWN count as created)."""
        return sum(1 for e in self.entries()
                   if e["type"] == rtype
                   and e["state"] in ("INTENT", "UNKNOWN", "CREATED", "DELETED", "DELETE_FAILED"))

    def redacted(self) -> list:
        keep = ("seq", "run_id", "alias", "type", "display_name", "region", "state", "intent_utc", "created_utc",
                "http_status", "error_code", "error_message", "deleted_utc", "delete_status", "ocid_sha16")
        return [{k: e.get(k) for k in keep} for e in self.entries()]
