# v1.1 2026-10-10  Brief 14 tests: the resource manifest (b14lib/manifest.py).
#   v1.1: INTENT is live (Codex code review r1, finding 1). v1.0: first version.
import json
import stat

import pytest
from conftest import fake_ocid

from b14lib.manifest import Manifest, ManifestError, retry_token
from b14lib.phoenix import RegionLockError

RP = "generativeairoutingprofile"


@pytest.fixture
def man(secrets_dir):
    return Manifest(secrets_dir / "b14_manifest.json", "run-a")


def test_full_lifecycle_and_mode_600(man):
    e = man.intent("P1", RP, "b14-p1-xai-grok-4-3")
    assert e["state"] == "INTENT" and e["retry_token"] == retry_token("run-a", "P1", e["seq"])
    assert stat.S_IMODE(man.path.stat().st_mode) == 0o600
    oc = fake_ocid(RP)
    man.created(e["seq"], oc, 200)
    assert man.live()[0]["ocid"] == oc and man.count_created(RP) == 1
    man.deleted(e["seq"], 204)
    assert man.live() == [] and man.count_created(RP) == 1          # a deleted profile still counts toward 5


def test_write_ahead_entry_is_reused_until_resolved(man):
    a = man.intent("P1", RP, "b14-p1")
    b = man.intent("P1", RP, "b14-p1")
    assert a["seq"] == b["seq"] and a["retry_token"] == b["retry_token"]
    man.unknown(a["seq"], "ReadTimeout")
    c = man.intent("P1", RP, "b14-p1")
    assert c["seq"] == a["seq"] and man.pending("P1")["state"] == "UNKNOWN"
    man.created(a["seq"], fake_ocid(RP), 200)
    with pytest.raises(ManifestError, match="already exists"):
        man.intent("P1", RP, "b14-p1")
    assert man.pending("P1") is None


def test_a_refused_alias_gets_a_new_entry_and_token(man):
    a = man.intent("N2", RP, "b14-n2")
    man.refused(a["seq"], 400, "InvalidParameter", "only one model")
    b = man.intent("N2", RP, "b14-n2")
    assert b["seq"] != a["seq"] and b["retry_token"] != a["retry_token"]


def test_counts_include_intent_and_unknown(man):
    for k in range(3):
        man.intent(f"P{k + 1}", RP, f"b14-p{k + 1}")
    man.unknown(2, "timeout")
    assert man.count_created(RP) == 3


def test_ocid_checks(man):
    e = man.intent("P1", RP, "b14-p1")
    with pytest.raises(ManifestError, match="no OCID"):
        man.created(e["seq"], "not-an-ocid", 200)
    with pytest.raises(ManifestError, match="type"):
        man.created(e["seq"], fake_ocid("generativeaiproject"), 200)
    with pytest.raises(RegionLockError):
        man.created(e["seq"], fake_ocid(RP, "ord"), 200)
    assert man.entries()[0]["state"] == "INTENT"                    # nothing was written by the refusals


def test_transitions_and_names(man):
    e = man.intent("P1", RP, "b14-p1")
    man.refused(e["seq"], 401, "NotAuthenticated", "x")
    with pytest.raises(ManifestError, match="not allowed"):
        man.deleted(e["seq"], 204)
    with pytest.raises(ManifestError, match="b14-"):
        man.intent("P2", RP, "prod-profile")
    with pytest.raises(RegionLockError):
        man.intent("P2", RP, "b14-p2", region="us-chicago-1")
    with pytest.raises(ManifestError, match="unknown resource type"):
        man.intent("P2", "computeinstance", "b14-p2")


def test_redacted_copy_has_no_ocid(man):
    e = man.intent("P1", RP, "b14-p1")
    man.created(e["seq"], fake_ocid(RP), 200)
    red = man.redacted()
    assert "ocid" not in red[0] and red[0]["ocid_sha16"] and "ocid" + "1." not in json.dumps(red)


def test_loose_permissions_and_foreign_files_are_refused(man, secrets_dir):
    man.intent("P1", RP, "b14-p1")
    man.path.chmod(0o644)
    with pytest.raises(ManifestError, match="mode 600"):
        man.entries()
    man.path.chmod(0o600)
    man.path.write_text(json.dumps({"brief": "other", "entries": []}))
    with pytest.raises(ManifestError, match="not the brief 14"):
        man.entries()


def test_missing_folder_is_refused(tmp_path):
    m = Manifest(tmp_path / "nope" / "b14_manifest.json", "run-a")
    with pytest.raises(ManifestError, match="folder"):
        m.intent("P1", RP, "b14-p1")


def test_live_is_newest_first(man):
    for k in (1, 2):
        e = man.intent(f"P{k}", RP, f"b14-p{k}")
        man.created(e["seq"], fake_ocid(RP, tail=f"t{k}"), 200)
    assert [e["alias"] for e in man.live()] == ["P2", "P1"]


def test_an_intent_without_an_answer_is_live(man):
    e = man.intent("P1", RP, "b14-p1")
    assert [x["seq"] for x in man.live()] == [e["seq"]]
