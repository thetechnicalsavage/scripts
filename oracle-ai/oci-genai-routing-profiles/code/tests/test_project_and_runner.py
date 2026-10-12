# v1.2 2026-10-10  Brief 14 tests: the S9 project create (b14lib/project.py), the runner's Phoenix-only probe set and
#   summary (b14_run.py), and db_register.py's offline parts.
#   v1.2: the runner's `verify` step (GET only): a redacted report under results/state, exit 0 when consistent and
#         1 when not. The `responses` re-run: it reuses this run's ACTIVE project (no second create), keeps the first
#         run's record, draws from the reserve with its reason; an unusable project stops it before any call.
#   v1.1: the project must pass the ownership check once ACTIVE (Codex code review r1, finding 3). v1.0: first version.
import json

import pytest
from conftest import FakeResponse, fake_ocid

import b14_run
import db_register
from b14lib import phoenix, project
from b14lib.manifest import Manifest


@pytest.fixture
def man(secrets_dir):
    return Manifest(secrets_dir / "b14_manifest.json", "run-a")


def test_project_refusal_ends_s9_and_is_recorded(client, session, man):
    session.on("POST", "/generativeAiProjects", lambda m, u, b: FakeResponse(
        404, {"code": "NotAuthorizedOrNotFound", "message": "Authorization failed or requested resource not found"}))
    rec = project.create_project(client, man, "run-a")
    assert rec["outcome"] == "REFUSED" and man.entries()[0]["state"] == "REFUSED"
    assert man.entries()[0]["type"] == "generativeaiproject"


def _project_api(session, client, tags):
    oc = fake_ocid("generativeaiproject")
    state = {"deleted": False, "states": iter(["CREATING", "ACTIVE"])}

    def get(m, u, b):
        if state["deleted"]:
            return FakeResponse(404, {"code": "NotFound"})
        return FakeResponse(200, {"id": oc, "displayName": "b14-resp", "compartmentId": client.sec.compartment,
                                  "freeformTags": tags, "lifecycleState": next(state["states"], "ACTIVE")})

    def delete(m, u, b):
        state["deleted"] = True
        return FakeResponse(202)
    session.on("POST", "/generativeAiProjects", lambda m, u, b: FakeResponse(201, {"id": oc, "timeCreated": "t"}))
    session.on("GET", "/generativeAiProjects/", get)
    session.on("DELETE", "/generativeAiProjects/", delete)
    return oc, state


def test_project_created_and_polled(client, session, man):
    oc, _ = _project_api(session, client, {"brief": "b14", "run": "run-a", "kind": "project"})
    rec = project.create_project(client, man, "run-a", sleep=lambda s: None)
    assert rec["outcome"] == "CREATED" and rec["state"] == "ACTIVE" and man.live()[0]["ocid"] == oc
    assert "ocid" + "1." not in json.dumps(rec)


def test_project_without_our_marks_is_deleted_and_stops(client, session, man):
    from b14lib.profiles import OwnershipError
    oc, state = _project_api(session, client, {})
    with pytest.raises(OwnershipError):
        project.create_project(client, man, "run-a", sleep=lambda s: None)
    assert state["deleted"] and man.entries()[0]["state"] == "DELETED"


def test_snapshot_probes_never_leave_phoenix():
    for name, path, params in b14_run.SNAPSHOT_PROBES:
        assert path in ("/modelDiscovery", "/models", "/routingProfiles"), name
        phoenix.check_url(phoenix.MGMT_BASE + path)
        phoenix.check_body(dict(params))
        assert "identity" not in path and "limits" not in path
    names = [n for n, _, _ in b14_run.SNAPSHOT_PROBES]
    assert "md_all" in names and "lm_chat" in names and not any(n.startswith("md_from_") for n in names)


def test_summary_keeps_the_model_constant_and_counts():
    rows = []
    for kind, status, ms in (("direct", 200, 500), ("profile", 200, 700), ("profile", 404, 90)):
        rows.append({"profile_alias": "P1", "model": "xai.grok-4.3", "kind": kind,
                     "final": {"status": status, "latency_ms": ms, "answer_ok": status == 200,
                               "model_id_shape": "<generativeaimodel:phx>", "completion_tokens": 3,
                               "code": None if status == 200 else "NotAuthorizedOrNotFound"}})
    s = b14_run.summarize(rows)["P1"]
    assert s["model"] == "xai.grok-4.3" and s["direct"]["n"] == 1 and s["profile"]["n"] == 2
    assert s["profile"]["http_200"] == 1 and s["profile"]["error_codes"] == ["NotAuthorizedOrNotFound"]
    assert s["profile"]["latency_p50_ms"] == 700


def test_key_body_strips_the_armour(tmp_path):
    k = tmp_path / "k.pem"
    k.write_text("-----" + "BEGIN PRIVATE KEY-----\nAAAA\nBBBB\n-----" + "END PRIVATE KEY-----\n")
    assert db_register.key_body(k) == "AAAABBBB"
    k.write_text("-----" + "BEGIN PRIVATE KEY-----\n-----" + "END PRIVATE KEY-----\n")
    with pytest.raises(db_register.SecretsError):
        db_register.key_body(k)


def test_db_steps_are_refused_inside_a_window(monkeypatch, tmp_path):
    def closed(*a, **k):
        raise db_register.windows.WindowClosed("database step refused: test window")

    def never(*a, **k):
        raise AssertionError("must not connect inside a window")

    monkeypatch.setattr(db_register.windows, "require_db_window", closed)
    monkeypatch.setattr(db_register, "connect", never)
    assert db_register.main(["config", "--run-id", "run-a", "--home", str(tmp_path)]) == 1


def _verify_args(tmp_path, secrets_dir):
    import argparse
    home = tmp_path / "home"
    (home / "state").mkdir(parents=True)
    return argparse.Namespace(home=home, manifest=secrets_dir / "b14_manifest.json", run_id="run-a")


def test_verify_step_writes_a_redacted_report(client, session, man, tmp_path, secrets_dir, sec):
    from test_cleanup import setup_world
    api, base = setup_world(client, session, man)
    a = _verify_args(tmp_path, secrets_dir)
    (a.home / "state/baseline.json").write_text(json.dumps(base))
    assert b14_run.step_verify(a, sec, client) == 0
    out = list((a.home / "results/state").glob("verify_*.json"))
    assert len(out) == 1
    text = out[0].read_text()
    assert json.loads(text)["consistent"] is True and "ocid" + "1." not in text


def test_verify_step_fails_when_oci_differs_from_the_manifest(client, session, man, tmp_path, secrets_dir, sec):
    from test_cleanup import setup_world
    api, base = setup_world(client, session, man)
    api.store.clear()                                     # both profiles gone outside the POC
    a = _verify_args(tmp_path, secrets_dir)
    (a.home / "state/baseline.json").write_text(json.dumps(base))
    assert b14_run.step_verify(a, sec, client) == 1


def _rerun_world(client, session, man, tmp_path, secrets_dir, monkeypatch, state="ACTIVE"):
    from b14lib import chat
    oc = fake_ocid("generativeaiproject")
    e = man.intent("PROJ", "generativeaiproject", "b14-resp")
    man.created(e["seq"], oc, 201)
    session.on("GET", "/generativeAiProjects/", lambda m, u, b: FakeResponse(200, {
        "id": oc, "displayName": "b14-resp", "compartmentId": client.sec.compartment, "lifecycleState": state,
        "freeformTags": {"brief": "b14", "run": "run-a", "kind": "project"}}))
    body = {"model": "grok-4.3", "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ALPHA BRAVO CHARLIE"}]}],
            "usage": {"input_tokens": 200, "output_tokens": 120, "output_tokens_details": {"reasoning_tokens": 118}}}
    session.on("POST", "/openai/v1/responses", lambda m, u, b: FakeResponse(200, body))
    prof = chat.Target("P1", "profile", "xai.grok-4.3", fake_ocid("generativeairoutingprofile"))
    monkeypatch.setattr(b14_run, "_profile_targets", lambda a: [prof])
    monkeypatch.setattr(b14_run.time, "sleep", lambda s: None)
    a = _verify_args(tmp_path, secrets_dir)
    a.use_reserve = "S9 re-run: error text not captured"
    (a.home / "state/baseline.json").write_text(json.dumps({"routing_profiles": [], "projects": []}))
    (a.home / "results/s9").mkdir(parents=True)
    (a.home / "results/s9/responses.json").write_text('{"first": true}')
    return a


def test_responses_rerun_reuses_the_project(client, session, man, tmp_path, secrets_dir, sec, monkeypatch):
    a = _rerun_world(client, session, man, tmp_path, secrets_dir, monkeypatch)
    assert b14_run.step_responses(a, sec, client) == 0
    assert not [c for c in session.calls if c["method"] == "POST" and "/generativeAiProjects" in c["url"]]
    assert json.loads((a.home / "results/s9/responses.json").read_text()) == {"first": True}
    r2 = json.loads((a.home / "results/s9/responses_r2.json").read_text())
    assert r2["project"]["outcome"] == "REUSED" and len(r2["calls"]) == 6
    from b14lib.budget import Ledger
    b = Ledger(a.home / "state/budget.json").summary()["buckets"]
    assert b["reserve"]["calls"] == 6 and b["s9_responses"]["calls"] == 0


def test_responses_rerun_stops_on_an_unusable_project(client, session, man, tmp_path, secrets_dir, sec, monkeypatch):
    from b14lib.profiles import OwnershipError
    a = _rerun_world(client, session, man, tmp_path, secrets_dir, monkeypatch, state="DELETING")
    with pytest.raises(OwnershipError):
        b14_run.step_responses(a, sec, client)
    assert not [c for c in session.calls if c["method"] in ("POST", "DELETE")]


def test_use_reserve_only_with_a_reason_and_for_responses(tmp_path):
    for argv in (["status", "--run-id", "r", "--use-reserve", "x"],
                 ["responses", "--run-id", "r", "--use-reserve", " "]):
        with pytest.raises(SystemExit) as exc:
            b14_run.main(argv + ["--home", str(tmp_path)])
        assert exc.value.code == 2
