# v1.1 2026-10-10  Brief 14 tests: the GET-only export of resource definitions (b14lib/export.py, b14_run.py export).
#   v1.1: (Codex review 31, findings 4-5) a key outside the schema is withheld with its value; the project's create
#         body must equal project.create_project()'s whole body, and a drifted description fails matches_code.
#   v1.0: first version. Happy path: profiles, the accepted-and-deleted invalid attempt and the project are written
#         with named placeholders only, the organisation's tag namespaces and the creator never appear, and nothing
#         but GET is sent. Failure paths: a resource that is gone, one without our tags, one not ACTIVE, and a guard
#         hit (an identifier that survived) each stop that resource or the whole export.
import json

import pytest
from conftest import FakeResponse, fake_ocid
from test_profiles import SNAP, FakeRP, snapshot, spec

import b14_run
from b14lib import export, profiles
from b14lib.manifest import Manifest

PT = "generativeaiproject"
ORG_NS = "AcmeFinance"                                  # a tenant's own defined-tag namespace (must never be written)
CREATOR = "someone" + "@" + "example.com"


@pytest.fixture
def man(secrets_dir):
    return Manifest(secrets_dir / "b14_manifest.json", "run-a")


def world(client, session, man, project_state="ACTIVE", project_tags=None):
    """P1 and P3 live, N3 accepted and deleted at once, PROJ live; the S0 baseline was empty."""
    api = FakeRP(session)
    session.on("GET", "/generativeAiProjects?", lambda m, u, b: FakeResponse(200, {"items": []}))
    plan = profiles.build_plan(snapshot(), "run-a", SNAP)
    for a in ("P1", "N3", "P3"):
        profiles.create(client, man, spec(plan, a), sleep=lambda s: None)
    oc = fake_ocid(PT, tail="proj1")
    e = man.intent("PROJ", PT, "b14-resp")
    man.created(e["seq"], oc, 201, SNAP)
    proj = {"id": oc, "displayName": "b14-resp", "compartmentId": client.sec.compartment,
            "description": "Brief 14 lab, Responses API step; deleted after the post",
            "freeformTags": project_tags or {"brief": "b14", "run": "run-a", "kind": "project"},
            "definedTags": {"Oracle-Tags": {"CreatedBy": CREATOR, "CreatedOn": "2026-10-10T08:57:29.682Z"},
                            ORG_NS: {"CostCentre": "cc-4711"}},
            "systemTags": {"orcl-cloud": {"free-tier-retained": "true"}},
            "lifecycleState": project_state, "lifecycleDetails": "tenancy acmefin project ready",
            "timeCreated": "2026-10-10T08:57:29.682Z", "acmefinNamespace": {"bucket": "acmefin-b"},
            "conversationConfig": {"conversationsRetentionInHours": 720, "responsesRetentionInHours": 720}}
    session.on("GET", "/generativeAiProjects/", lambda m, u, b: FakeResponse(200, proj) if u.endswith(oc)
               else FakeResponse(404, {"code": "NotFound"}))
    base = {"routing_profiles": [], "projects": []}
    return api, plan, base, oc


def test_export_writes_placeholders_only_and_sends_only_get(client, session, man):
    api, plan, base, oc = world(client, session, man)
    n_before = len(session.calls)
    out = export.export_all(client, man, base, plan, "run-a")
    assert {c["method"] for c in session.calls[n_before:]} == {"GET"}
    assert out["ok"] is True and sorted(out["definitions"]) == ["N3", "P1", "P3", "PROJ"]
    text = json.dumps(out)
    for bad in ("ocid" + "1.", CREATOR, ORG_NS, "CostCentre", "cc-4711", "acmefin", "bucket",
                client.sec.compartment):
        assert bad not in text
    p1 = out["definitions"]["P1"]
    plan_body = dict(spec(plan, "P1")["body"])
    assert p1["create_details"] == dict(plan_body, compartmentId="<COMPARTMENT_OCID>")
    assert p1["as_stored"]["id"] == "<P1_OCID>" and p1["create_details"]["regionRoutingPolicy"] == {
        "allowedRegions": ["us-phoenix-1"]}
    pj = out["definitions"]["PROJ"]
    assert pj["as_stored"]["definedTags"]["Oracle-Tags"]["CreatedBy"] == "<CREATED_BY>"
    assert pj["as_stored"]["lifecycleDetails"] == "<WITHHELD>"
    assert "lifecycleDetails" in pj["withheld_paths"]
    assert any(w.startswith("<UNKNOWN_KEY_") for w in pj["withheld_paths"])
    assert pj["as_stored"]["conversationConfig"] == {"conversationsRetentionInHours": 720,
                                                     "responsesRetentionInHours": 720}
    assert all(ORG_NS not in p for p in pj["withheld_paths"])        # the path names the placeholder key
    assert pj["create_details"] == {"compartmentId": "<COMPARTMENT_OCID>", "displayName": "b14-resp",
                                    "description": "Brief 14 lab, Responses API step; deleted after the post",
                                    "freeformTags": {"brief": "b14", "run": "run-a", "kind": "project"}}
    n3 = out["definitions"]["N3"]
    assert n3["as_stored"] is None and "do_not_recreate" in n3 and n3["source"].startswith("S2 plan")
    refused = [r for r in out["rows"] if r["manifest_state"] == "REFUSED"]
    assert all(r["written"] is False for r in refused)


def test_a_profile_gone_is_reported_and_not_written(client, session, man):
    api, plan, base, oc = world(client, session, man)
    p3 = next(e for e in man.entries() if e["alias"] == "P3")
    del api.store[p3["ocid"]]
    out = export.export_all(client, man, base, plan, "run-a")
    assert out["ok"] is False and "P3" not in out["definitions"]
    row = next(r for r in out["rows"] if r["alias"] == "P3")
    assert row["written"] is False and "HTTP 404" in row["why"]


def test_a_project_without_our_tags_or_not_active_is_not_written(client, session, man):
    api, plan, base, oc = world(client, session, man, project_tags={"brief": "b14", "run": "other-run"})
    out = export.export_all(client, man, base, plan, "run-a")
    assert "PROJ" not in out["definitions"] and out["ok"] is False
    man2_dir = man.path.parent / "second"
    man2_dir.mkdir()
    man2 = Manifest(man2_dir / "b14_manifest.json", "run-a")
    api, plan, base, oc = world(client, session, man2, project_state="DELETING")
    out = export.export_all(client, man2, base, plan, "run-a")
    row = next(r for r in out["rows"] if r["alias"] == "PROJ")
    assert row["written"] is False and "not ACTIVE" in row["why"]


def test_the_guard_refuses_any_surviving_identifier(client, session, man, monkeypatch):
    api, plan, base, oc = world(client, session, man)
    monkeypatch.setattr(export._Scrubber, "value", lambda self, v, path: v)      # a scrubber that lets all through
    with pytest.raises(export.ExportError):
        export.export_all(client, man, base, plan, "run-a")
    sec = client.sec
    assert export.guard(json.dumps({"a": ".".join(("10", "1", "2", "3"))}), sec) == ["an IPv4 address is left"]
    assert export.guard(json.dumps({"a": CREATOR}), sec) == ["an e-mail address is left"]
    assert export.guard(json.dumps({"a": sec.fingerprint}), sec) == ["a signing value is left"]
    assert export.guard('{"a": "<P1_OCID>"}', sec) == []


def test_export_step_writes_redacted_files(client, session, man, tmp_path, secrets_dir, sec):
    import argparse
    api, plan, base, oc = world(client, session, man)
    home = tmp_path / "home"
    (home / "state").mkdir(parents=True)
    (home / "results/s2").mkdir(parents=True)
    (home / "state/baseline.json").write_text(json.dumps(base))
    (home / "results/s2/profile_plan.json").write_text(json.dumps(plan))
    a = argparse.Namespace(home=home, manifest=secrets_dir / "b14_manifest.json", run_id="run-a")
    assert b14_run.step_export(a, sec, client) == 0
    files = sorted(p.name for p in (home / "results/s10/definitions").iterdir())
    assert files == ["N3.json", "P1.json", "P3.json", "PROJ.json", "index.json"]
    for p in (home / "results/s10/definitions").iterdir():
        assert "ocid" + "1." not in p.read_text()
    idx = json.loads((home / "results/s10/definitions/index.json").read_text())
    assert idx["ok"] is True and "<COMPARTMENT_OCID>" in idx["placeholders"]


def test_project_body_matches_what_create_project_sends(client, session, man):
    from b14lib import project
    sent = {}

    def post(m, u, b):
        sent.update(b)
        return FakeResponse(400, {"code": "InvalidParameter", "message": "stop here"})
    session.on("POST", "/generativeAiProjects", post)
    project.create_project(client, man, "run-a")
    assert dict(sent, compartmentId=None) == export.project_body("run-a")


def test_a_drifted_project_description_fails_matches_code(client, session, man):
    api, plan, base, oc = world(client, session, man)
    stored = session.routes[-1][2](None, "x/" + oc, None).json()
    stored["description"] = "Brief 14 lab, something else"
    session.on("GET", "/generativeAiProjects/", lambda m, u, b: FakeResponse(200, stored))
    out = export.export_all(client, man, base, plan, "run-a")
    row = next(r for r in out["rows"] if r["alias"] == "PROJ")
    assert row["matches_code"] is False and out["ok"] is False
