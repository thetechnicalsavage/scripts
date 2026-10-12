# v1.0 2026-10-10  Brief 14 tests: signed HTTP through the lock (b14lib/ociclient.py). v1.0: first version.
import pytest
import requests
from conftest import FakeResponse, fake_ocid

from b14lib import phoenix
from b14lib.ociclient import OciClient
from b14lib.phoenix import RegionLockError

MG = phoenix.MGMT_BASE


def test_client_refuses_a_non_phoenix_environment(sec, session):
    sec.region = "us-chicago-1"
    with pytest.raises(RegionLockError):
        OciClient(sec, session=session)


def test_other_region_url_is_refused_before_any_request(client, session):
    with pytest.raises(RegionLockError):
        client.request("GET", "https://generativeai.us-chicago-1.oci.oraclecloud.com/20231130/routingProfiles")
    assert session.calls == []


def test_body_naming_another_region_is_refused_before_any_request(client, session):
    with pytest.raises(RegionLockError):
        client.request("POST", MG + "/routingProfiles",
                       json_body={"regionRoutingPolicy": {"allowedRegions": ["us-ashburn-1"]}})
    assert session.calls == []


def test_get_retries_429_then_succeeds(sec, session):
    seq = iter([FakeResponse(429, {"code": "TooManyRequests"}), FakeResponse(200, {"items": []})])
    session.on("GET", "/modelDiscovery", lambda m, u, b: next(seq))
    waits = []
    c = OciClient(sec, session=session, sleep=waits.append)
    r = c.request("GET", MG + "/modelDiscovery?limit=1", repeatable=True)
    assert r.status == 200 and r.attempts == 2 and waits == [2]


def test_non_repeatable_post_is_not_retried(client, session):
    session.on("POST", "/actions/chat", lambda m, u, b: FakeResponse(500, {"code": "InternalError"}))
    r = client.request("POST", phoenix.INFER_BASE + "/actions/chat", json_body={"x": 1}, max_retries=0)
    assert r.status == 500 and r.attempts == 1 and len(session.calls) == 1


def test_transport_error_is_a_result_not_an_exception(client, session):
    session.on("GET", "/routingProfiles", lambda m, u, b: requests.ConnectionError("down"))
    r = client.request("GET", MG + "/routingProfiles", repeatable=True, max_retries=2)
    assert r.status is None and r.error == "ConnectionError" and r.attempts == 3 and not r.ok


def test_identifier_headers_are_dropped_and_messages_redacted(client, session):
    leaked = fake_ocid("generativeairoutingprofile")
    hdrs = {"content-type": "application/json", "etag": "e1", "opc-" + "req" + "uest-id": "abc", "x-client-id": "z"}
    session.on("GET", "/routingProfiles", lambda m, u, b: FakeResponse(
        404, {"code": "NotAuthorizedOrNotFound", "message": f"no access to {leaked}"}, hdrs))
    r = client.request("GET", MG + "/routingProfiles", repeatable=True)
    assert r.header_names == ["content-type", "etag"]
    assert r.error_code() == "NotAuthorizedOrNotFound"
    assert "ocid" + "1." not in r.error_message(client.sec) and "<generativeairoutingprofile:phx>" in r.error_message(
        client.sec)
    assert "ocid" + "1." not in r.text


def test_requests_are_signed(client, session):
    session.on("GET", "/models", lambda m, u, b: FakeResponse(200, {"items": []}))
    client.request("GET", MG + "/models?capability=CHAT")
    h = session.calls[0]["headers"]
    assert h["authorization"].startswith('Signature version="1"') and "date" in h and "host" in h
