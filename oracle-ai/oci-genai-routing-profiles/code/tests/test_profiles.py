# v1.1 2026-10-10  Brief 14 tests: profile plan, create, verify, delete-on-accept (b14lib/profiles.py).
#   v1.1: tags_ok recorded after create (Codex plan review r1, finding 3); a stored profile without our marks is
#         deleted at once and stops the run (Codex code review r1, finding 2).
#   v1.0: first version. A fake Generative AI management endpoint stands in for OCI.
import json

import pytest
from conftest import FakeResponse, fake_ocid

from b14lib import phoenix, profiles
from b14lib.manifest import Manifest
from b14lib.phoenix import RegionLockError

SNAP = "2026-10-10T02:14:00Z"
RP = "generativeairoutingprofile"


def item(mid, vendor, avail, caps=("CHAT",)):
    return {"modelId": mid, "vendor": vendor, "capabilities": list(caps), "availability": avail}


def av(region, modes=("ON_DEMAND",), retired=None, deprecated=None):
    return {"realm": "oc1", "region": region, "servingModes": list(modes), "timeOnDemandRetired": retired,
            "timeDeprecated": deprecated}


def snapshot(**over):
    md = {
        "xai.grok-4.3": item("xai.grok-4.3", "xai", [av("us-phoenix-1"), av("us-chicago-1")]),
        "xai.grok-4.20-0309-reasoning": item("xai.grok-4.20-0309-reasoning", "xai", [av("us-phoenix-1")]),
        "google.gemini-2.5-flash": item("google.gemini-2.5-flash", "google", [av("us-phoenix-1")]),
        "google.gemini-2.5-pro": item("google.gemini-2.5-pro", "google", [av("us-phoenix-1")]),
        "xai.grok-4.7": item("xai.grok-4.7", "xai", [av("us-phoenix-1")]),
        "xai.grok-4.6": item("xai.grok-4.6", "xai", [av("us-phoenix-1")]),
        "openai.gpt-oss-120b": item("openai.gpt-oss-120b", "openai", [av("us-phoenix-1", ("DEDICATED",)),
                                                                       av("us-chicago-1")]),
        "meta.llama-3.3-70b-instruct": item("meta.llama-3.3-70b-instruct", "meta", [av("us-chicago-1")]),
    }
    md.update(over)
    return [v for v in md.values() if v is not None]


def test_plan_is_phoenix_only_and_follows_the_slate():
    plan = profiles.build_plan(snapshot(), "run-a", SNAP)
    assert [p["model"] for p in plan["profiles"]] == ["xai.grok-4.3", "xai.grok-4.20-0309-reasoning",
                                                       "google.gemini-2.5-flash", "google.gemini-2.5-pro",
                                                       "xai.grok-4.7"]
    bodies = [p["body"] for p in plan["profiles"]] + [n["body"] for n in plan["invalid"] if "body" in n]
    assert len(bodies) == 10
    for b in bodies:
        phoenix.check_profile_body(b)
        assert b["compartmentId"] is None and b["displayName"].startswith("b14-")
        assert phoenix.find_region_ids(b) == ["us-phoenix-1"]
    inv = {n["alias"]: n for n in plan["invalid"]}
    assert inv["N2"]["models"] == ["xai.grok-4.3", "google.gemini-2.5-flash"]
    assert inv["N3"]["models"] == ["openai.gpt-oss-120b"] and inv["N4"]["premise_ok"]
    assert "modelRoutingPolicy" not in inv["N5"]["body"]


def test_rules_drop_and_fall_back():
    plan = profiles.build_plan(snapshot(**{
        "xai.grok-4.7": None,                                                         # gone from discovery
        "google.gemini-2.5-pro": item("google.gemini-2.5-pro", "google",
                                      [av("us-phoenix-1", retired="2026-10-23T00:00:00.000Z")]),
        "xai.grok-4.20-0309-reasoning": item("xai.grok-4.20-0309-reasoning", "xai", [av("us-chicago-1")]),
    }), "run-a", SNAP)
    models = [p["model"] for p in plan["profiles"]]
    assert models == ["xai.grok-4.3", "google.gemini-2.5-flash", "xai.grok-4.6"]
    reasons = {(d["alias"], d["reason"]) for d in plan["dropped"]}
    assert ("P4", "on-demand retirement within 30 days") in reasons
    assert ("P2", "not on demand in Phoenix") in reasons and ("P5", "not in discovery") in reasons


def test_invalid_premises_checked_against_the_snapshot():
    plan = profiles.build_plan(snapshot(**{
        "xai.grok-4.20-non-reasoning": item("xai.grok-4.20-non-reasoning", "xai", [av("us-phoenix-1")]),
        "meta.llama-3.3-70b-instruct": item("meta.llama-3.3-70b-instruct", "meta", [av("us-phoenix-1")]),
        "openai.gpt-oss-120b": None,
    }), "run-a", SNAP)
    inv = {n["alias"]: n for n in plan["invalid"]}
    assert not inv["N1"]["premise_ok"] and "body" not in inv["N1"]
    assert not inv["N3"]["premise_ok"] and not inv["N4"]["premise_ok"]


# ------------------------------------------------------------------------------------------- fake management API
class FakeRP:
    def __init__(self, session, stored_regions=("us-phoenix-1",), create_status=200, list_extra=(), drop_tags=False):
        self.drop_tags = drop_tags
        self.store, self.n = {}, 0
        self.stored_regions = list(stored_regions)
        self.create_status = create_status
        self.list_extra = list(list_extra)
        self.tokens = {}
        session.on("POST", "/routingProfiles", self.create)
        session.on("GET", "/routingProfiles?", self.list)
        session.on("GET", "/routingProfiles/", self.get)
        session.on("DELETE", "/routingProfiles/", self.delete)
        self.session = session

    def create(self, m, u, b):
        status = self.create_status() if callable(self.create_status) else self.create_status
        if status != 200:
            return FakeResponse(status, {"code": "InvalidParameter", "message": f"refused {b['displayName']}"})
        token = self.session.calls[-1]["headers"].get("opc-retry-token")
        if token in self.tokens:
            return FakeResponse(200, self.store[self.tokens[token]])
        self.n += 1
        oc = fake_ocid(RP, tail=f"rp{self.n}")
        self.store[oc] = dict(b, id=oc, lifecycleState="ACTIVE", timeCreated=SNAP,
                              regionRoutingPolicy={"allowedRegions": self.stored_regions})
        if self.drop_tags:
            self.store[oc]["freeformTags"] = {}
        self.tokens[token] = oc
        return FakeResponse(200, self.store[oc])

    def list(self, m, u, b):
        return FakeResponse(200, {"items": list(self.store.values()) + self.list_extra})

    def get(self, m, u, b):
        oc = u.rsplit("/", 1)[1]
        return FakeResponse(200, self.store[oc]) if oc in self.store else FakeResponse(404, {"code": "NotFound"})

    def delete(self, m, u, b):
        oc = u.rsplit("/", 1)[1]
        if oc not in self.store:
            return FakeResponse(404, {"code": "NotFound"})
        del self.store[oc]
        return FakeResponse(204)


@pytest.fixture
def man(secrets_dir):
    return Manifest(secrets_dir / "b14_manifest.json", "run-a")


@pytest.fixture
def plan():
    return profiles.build_plan(snapshot(), "run-a", SNAP)


def spec(plan, alias):
    return next(s for s in plan["profiles"] + plan["invalid"] if s["alias"] == alias)


def test_create_profile_records_and_verifies(client, session, man, plan):
    api = FakeRP(session)
    rec = profiles.create(client, man, spec(plan, "P1"), sleep=lambda s: None)
    assert rec["outcome"] == "CREATED" and rec["state"] == "ACTIVE" and rec["stored_regions"] == ["us-phoenix-1"]
    assert rec["tags_ok"] is True
    e = man.entries()[0]
    assert e["state"] == "CREATED" and e["ocid"] in api.store
    post = next(c for c in session.calls if c["method"] == "POST")
    assert post["body"]["compartmentId"] == client.sec.compartment
    assert post["headers"]["opc-retry-token"] == e["retry_token"]
    assert "ocid" + "1." not in json.dumps(rec)


def test_accepted_invalid_attempt_is_deleted_at_once(client, session, man, plan):
    api = FakeRP(session)
    rec = profiles.create(client, man, spec(plan, "N2"), sleep=lambda s: None)
    assert rec["outcome"] == "CREATED" and rec["deleted_at_once"]["gone"] is True
    assert api.store == {} and man.entries()[0]["state"] == "DELETED"
    assert rec["profiles_before"] == rec["profiles_after"] == 0


def test_refused_attempt_is_recorded_verbatim(client, session, man, plan):
    FakeRP(session, create_status=400)
    rec = profiles.create(client, man, spec(plan, "N3"), sleep=lambda s: None)
    assert rec["outcome"] == "REFUSED" and rec["code"] == "InvalidParameter" and "b14-n3" in rec["message"]
    assert man.entries()[0]["state"] == "REFUSED" and rec["profiles_after"] == 0


def test_lost_answer_is_unknown_then_resolved_with_the_same_token(client, session, man, plan):
    statuses = iter([503, 503, 503, 503, 200])
    api = FakeRP(session, create_status=lambda: next(statuses))
    rec = profiles.create(client, man, spec(plan, "P1"), sleep=lambda s: None)
    assert rec["outcome"] == "UNKNOWN" and man.entries()[0]["state"] == "UNKNOWN"
    rec2 = profiles.create(client, man, spec(plan, "P1"), sleep=lambda s: None)
    assert rec2["outcome"] == "CREATED" and len(man.entries()) == 1 and len(api.store) == 1
    tokens = {c["headers"]["opc-retry-token"] for c in session.calls if c["method"] == "POST"}
    assert len(tokens) == 1


def test_stored_region_violation_deletes_and_stops(client, session, man, plan):
    api = FakeRP(session, stored_regions=("us-phoenix-1", "us-chicago-1"))
    with pytest.raises(RegionLockError):
        profiles.create(client, man, spec(plan, "P1"), sleep=lambda s: None)
    assert api.store == {} and man.entries()[0]["state"] == "DELETED"


def test_profile_cap_counts_transient_ones(client, session, man, plan):
    FakeRP(session)
    for a in ("P1", "P2", "P3", "N1", "N2"):
        profiles.create(client, man, spec(plan, a), sleep=lambda s: None)
    with pytest.raises(profiles.CapReached):
        profiles.create(client, man, spec(plan, "P4"), sleep=lambda s: None)


def test_skipped_when_the_premise_fails(client, session, man):
    plan = profiles.build_plan(snapshot(**{"openai.gpt-oss-120b": None}), "run-a", SNAP)
    rec = profiles.create(client, man, spec(plan, "N3"))
    assert rec["outcome"] == "SKIPPED" and session.calls == [] and man.entries() == []


def test_a_profile_stored_without_our_tags_is_deleted_and_stops(client, session, man, plan):
    api = FakeRP(session, drop_tags=True)
    with pytest.raises(profiles.OwnershipError, match="freeform tags"):
        profiles.create(client, man, spec(plan, "P1"), sleep=lambda s: None)
    assert api.store == {} and man.entries()[0]["state"] == "DELETED"
