# v1.3 2026-10-10  Brief 14 tests: clean-up by manifest OCID and its verification (b14lib/cleanup.py).
#   v1.3: (Codex review 31, finding 1) another run's live entry in the same manifest is never deleted, is reported
#         under other_runs and blocks a clean verdict; verify_state() flags it too.
#   v1.2: verify_state() (GET-only restart check): consistent world; a profile gone, a stranger, a non-Phoenix stored
#         profile and an unresolved INTENT each make it inconsistent; it never sends POST, PUT or DELETE.
#   v1.1: ownership check before every DELETE (Codex plan review r1, finding 2); an INTENT entry blocks a clean
#         verdict (Codex code review r1, finding 1). v1.0: first version.
import pytest
from conftest import FakeResponse, fake_ocid
from test_profiles import SNAP, FakeRP, snapshot, spec

from b14lib import cleanup, profiles
from b14lib.manifest import Manifest
from b14lib.ocisign import sha16

RP = "generativeairoutingprofile"


@pytest.fixture
def man(secrets_dir):
    return Manifest(secrets_dir / "b14_manifest.json", "run-a")


def projects_empty(session):
    session.on("GET", "/generativeAiProjects?", lambda m, u, b: FakeResponse(200, {"items": []}))


def setup_world(client, session, man, extra=()):
    api = FakeRP(session, list_extra=list(extra))
    projects_empty(session)
    base = cleanup.baseline(client)
    plan = profiles.build_plan(snapshot(), "run-a", SNAP)
    for a in ("P1", "P3"):
        profiles.create(client, man, spec(plan, a), sleep=lambda s: None)
    return api, base


def test_deletes_only_manifest_ocids_and_verifies(client, session, man):
    theirs = {"id": fake_ocid(RP, tail="operators"), "displayName": "prod-router", "lifecycleState": "ACTIVE"}
    api, base = setup_world(client, session, man, extra=[theirs])
    assert base["routing_profiles"] == [sha16(theirs["id"])]
    rep = cleanup.run(client, man, base, sleep=lambda s: None)
    deletes = [c["url"] for c in session.calls if c["method"] == "DELETE"]
    ours = {e["ocid"] for e in man.entries()}
    assert len(deletes) == 2 and all(u.rsplit("/", 1)[1] in ours for u in deletes)
    assert theirs["id"] not in "".join(deletes)
    assert rep["clean"] is True and rep["verify"]["routing_profiles_equal_baseline"]
    assert all(e["state"] == "DELETED" for e in man.entries())


def test_a_stranger_b14_profile_is_reported_not_deleted(client, session, man):
    stranger = {"id": fake_ocid(RP, tail="stranger"), "displayName": "b14-p9-unknown", "lifecycleState": "ACTIVE"}
    api, base = setup_world(client, session, man)
    api.list_extra.append(stranger)
    rep = cleanup.run(client, man, base, sleep=lambda s: None)
    assert rep["strangers"] == [{"display_name": "b14-p9-unknown", "id_sha16": sha16(stranger["id"])}]
    assert stranger["id"] not in "".join(c["url"] for c in session.calls if c["method"] == "DELETE")
    assert rep["clean"] is False


def test_unknown_entries_block_a_clean_verdict(client, session, man):
    api, base = setup_world(client, session, man)
    e = man.intent("P2", RP, "b14-p2")
    man.unknown(e["seq"], "ReadTimeout")
    rep = cleanup.run(client, man, base, sleep=lambda s: None)
    assert rep["unresolved"] == [{"seq": e["seq"], "alias": "P2", "state": "UNKNOWN"}] and rep["clean"] is False


def test_a_failed_delete_is_recorded(client, session, man):
    api, base = setup_world(client, session, man)
    session.on("DELETE", "/routingProfiles/", lambda m, u, b: FakeResponse(409, {"code": "Conflict",
                                                                                 "message": "busy"}))
    rep = cleanup.run(client, man, base, sleep=lambda s: None)
    assert rep["clean"] is False and {e["state"] for e in man.entries()} == {"DELETE_FAILED"}
    assert all(d["delete_status"] == 409 for d in rep["deleted"])


def test_a_corrupted_manifest_entry_cannot_delete_a_foreign_profile(client, session, man):
    theirs = {"id": fake_ocid(RP, tail="operators"), "displayName": "prod-router", "lifecycleState": "ACTIVE",
              "compartmentId": client.sec.compartment, "freeformTags": {}}
    api, base = setup_world(client, session, man, extra=[theirs])
    api.store[theirs["id"]] = theirs                          # GET answers for it
    e = man.intent("P4", RP, "b14-p4")
    man.created(e["seq"], theirs["id"], 200)                  # a stale or corrupted entry points at their profile
    rep = cleanup.run(client, man, base, sleep=lambda s: None)
    deletes = [c["url"] for c in session.calls if c["method"] == "DELETE"]
    assert theirs["id"] not in "".join(deletes) and theirs["id"] in api.store
    refused = next(d for d in rep["deleted"] if d["alias"] == "P4")
    assert "display name does not start with b14-" in refused["refused"]
    assert "it existed before the POC (S0 baseline)" in refused["refused"]
    assert rep["clean"] is False


def test_ownership_rules(sec):
    from b14lib.manifest import ownership_problems
    oc = fake_ocid(RP)
    entry = {"ocid": oc, "type": RP, "run_id": "run-a"}
    good = {"id": oc, "displayName": "b14-p1", "compartmentId": sec.compartment,
            "freeformTags": {"brief": "b14", "run": "run-a"}}
    assert ownership_problems(good, entry, sec.compartment) == []
    assert ownership_problems(dict(good, compartmentId="other"), entry, sec.compartment) == ["not in our compartment"]
    assert ownership_problems(dict(good, freeformTags={}), entry, sec.compartment)
    assert ownership_problems(dict(good, freeformTags={}), entry, sec.compartment, require_tags=False) == []
    assert ownership_problems(good, entry, sec.compartment, [sha16(oc)]) == ["it existed before the POC (S0 baseline)"]
    other = fake_ocid(RP, "iad")
    assert "not in the Phoenix region" in ownership_problems(dict(good, id=other), dict(entry, ocid=other),
                                                             sec.compartment)


def test_a_crash_after_the_write_ahead_blocks_a_clean_verdict(client, session, man):
    api, base = setup_world(client, session, man)
    e = man.intent("P2", RP, "b14-p2")                    # crashed before the create answer was recorded
    rep = cleanup.run(client, man, base, sleep=lambda s: None)
    assert {"seq": e["seq"], "alias": "P2", "state": "INTENT"} in rep["unresolved"] and rep["clean"] is False


# ------------------------------------------------------------------------------------------- verify_state (GET only)
def _no_writes(calls):
    return not [c for c in calls if c["method"] in ("POST", "PUT", "DELETE")]


def test_verify_state_consistent_and_read_only(client, session, man):
    theirs = {"id": fake_ocid(RP, tail="operators"), "displayName": "prod-router", "lifecycleState": "ACTIVE"}
    api, base = setup_world(client, session, man, extra=[theirs])
    before = len(session.calls)
    rep = cleanup.verify_state(client, man, base)
    assert rep["consistent"] is True and rep["strangers"] == []
    assert rep["routing_profiles"] == {"now": 3, "baseline": 1, "manifest_live": 2, "equal_baseline_plus_live": True,
                                       "baseline_missing": 0, "unexplained": 0, "live_missing": 0}
    assert all(r["as_expected"] and r["stored_regions"] == ["us-phoenix-1"] for r in rep["entries"])
    assert _no_writes(session.calls[before:])
    assert "ocid" + "1." not in str(rep)


def test_verify_state_flags_a_missing_profile_and_a_stranger(client, session, man):
    api, base = setup_world(client, session, man)
    gone = man.entries()[0]["ocid"]
    del api.store[gone]                                           # removed outside the POC
    api.list_extra.append({"id": fake_ocid(RP, tail="stranger"), "displayName": "b14-p9-unknown",
                           "lifecycleState": "ACTIVE"})
    before = len(session.calls)
    rep = cleanup.verify_state(client, man, base)
    assert rep["consistent"] is False and _no_writes(session.calls[before:])
    first = next(r for r in rep["entries"] if r["seq"] == man.entries()[0]["seq"])
    assert first["as_expected"] is False and first["http_status"] == 404
    assert rep["routing_profiles"]["live_missing"] == 1 and len(rep["strangers"]) == 1


def test_verify_state_flags_a_non_phoenix_stored_profile_and_an_intent(client, session, man):
    api, base = setup_world(client, session, man)
    oc = man.entries()[0]["ocid"]
    api.store[oc] = dict(api.store[oc], regionRoutingPolicy={"allowedRegions": ["us-phoenix-1", "elsewhere"]})
    e = man.intent("P2", RP, "b14-p2")
    rep = cleanup.verify_state(client, man, base)
    rows = {r["seq"]: r for r in rep["entries"]}
    assert "stored regions are not exactly the Phoenix region" in rows[man.entries()[0]["seq"]]["problems"]
    assert rows[e["seq"]]["as_expected"] is False and rows[e["seq"]]["checked"] is False
    assert rep["consistent"] is False


def test_another_runs_entry_is_never_deleted(client, session, man, secrets_dir):
    api, base = setup_world(client, session, man)
    other = Manifest(secrets_dir / "b14_manifest.json", "run-b")
    plan_b = profiles.build_plan(snapshot(), "run-b", SNAP)
    profiles.create(client, other, spec(plan_b, "P2"), sleep=lambda s: None)
    theirs = next(e for e in other.entries() if e["run_id"] == "run-b")
    rep = cleanup.run(client, man, base, sleep=lambda s: None)
    deletes = "".join(c["url"] for c in session.calls if c["method"] == "DELETE")
    assert theirs["ocid"] not in deletes and len([c for c in session.calls if c["method"] == "DELETE"]) == 2
    assert rep["other_runs"] == [{"seq": theirs["seq"], "alias": "P2", "state": "CREATED"}]
    assert rep["clean"] is False
    assert next(e for e in man.entries() if e["seq"] == theirs["seq"])["state"] == "CREATED"
    v = cleanup.verify_state(client, man, base)
    assert v["consistent"] is False and any(r.get("other_run") for r in v["entries"])
