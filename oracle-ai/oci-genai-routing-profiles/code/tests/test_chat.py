# v1.1 2026-10-10  Brief 14 tests: budgeted inference calls (b14lib/chat.py).
#   v1.1: Responses API errors in the OpenAI shape and as raw text are recorded (S9 first run lost them); a
#         re-run draws from the reserve bucket with its reason. v1.0: first version.
import json

import pytest
from conftest import FakeResponse, fake_ocid

from b14lib import chat
from b14lib.budget import Ledger

GROK = "xai.grok-4.3"


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "budget.json")


def ok_body(word="ALPHA", model_id=None):
    return {"modelId": model_id or fake_ocid("generativeaimodel", "phx"), "modelVersion": "1.0",
            "chatResponse": {"apiFormat": "GENERIC", "timeCreated": "2026-10-10T00:00:00Z",
                             "choices": [{"index": 0, "finishReason": "stop",
                                          "message": {"role": "ASSISTANT",
                                                      "content": [{"type": "TEXT", "text": word}]}}],
                             "usage": {"promptTokens": 20, "completionTokens": 3,
                                       "completionTokensDetails": {"reasoningTokens": 40}}}}


def test_targets_are_validated():
    chat.Target("M:x", "direct", GROK, GROK)
    chat.Target("P1", "profile", GROK, fake_ocid("generativeairoutingprofile"))
    with pytest.raises(ValueError):
        chat.Target("M:x", "direct", GROK, "xai.grok-4.20-0309-reasoning")     # the model would not be constant
    with pytest.raises(ValueError):
        chat.Target("P1", "profile", GROK, GROK)
    with pytest.raises(ValueError):
        chat.Target("P1", "other", GROK, GROK)


def test_body_shape_holds_the_model_constant():
    a = chat.chat_body("c", GROK, "Reply with exactly one word: ALPHA")
    b = chat.chat_body("c", fake_ocid("generativeairoutingprofile"), "Reply with exactly one word: ALPHA")
    assert a["servingMode"]["servingType"] == "ON_DEMAND" and a["chatRequest"]["apiFormat"] == "GENERIC"
    a["servingMode"].pop("modelId"), b["servingMode"].pop("modelId")
    assert a == b                                    # only modelId differs between direct and profile


def test_parse_chat_reduces_the_returned_ocid():
    p = chat.parse_chat(ok_body())
    assert p["model_id_shape"] == "<generativeaimodel:phx>" and p["model_id_is_name"] is False
    assert (p["prompt_tokens"], p["completion_tokens"], p["reasoning_tokens"]) == (20, 3, 40)
    assert chat.parse_chat(None)["text"] is None
    assert chat.parse_chat({"modelId": GROK})["model_id_is_name"] is True


def test_call_counts_and_settles(client, session, ledger):
    session.on("POST", "/actions/chat", lambda m, u, b: FakeResponse(200, ok_body("ALPHA")))
    t = chat.Target("P1", "profile", GROK, fake_ocid("generativeairoutingprofile"))
    r = chat.call(client, ledger, "s5_routing", t, 0)
    assert r["final"]["status"] == 200 and r["final"]["answer_ok"] is True and r["attempts"] == 1
    assert "ocid" + "1." not in json.dumps(r)
    st = json.loads(ledger.path.read_text())
    assert st["calls"][0]["settled"] and st["calls"][0]["completion_tokens"] == 3
    assert session.calls[0]["body"]["servingMode"]["modelId"] == t.serving_id


def test_429_retries_are_counted_in_the_reserve(client, session, ledger):
    seq = iter([FakeResponse(429, {"code": "TooManyRequests", "message": "slow down"}),
                FakeResponse(429, {"code": "TooManyRequests", "message": "slow down"}),
                FakeResponse(200, ok_body("BRAVO"))])
    session.on("POST", "/actions/chat", lambda m, u, b: next(seq))
    waits = []
    t = chat.Target("M:" + GROK, "direct", GROK, GROK)
    r = chat.call(client, ledger, "s5_routing", t, 1, sleep=waits.append)
    assert r["attempts"] == 3 and r["final"]["status"] == 200 and waits == [4, 8]
    st = json.loads(ledger.path.read_text())
    assert [c["bucket"] for c in st["calls"]] == ["s5_routing", "reserve", "reserve"]
    assert [c["usd"] for c in st["calls"][:2]] == [0.0, 0.0]


def test_errors_are_recorded_verbatim_and_redacted(client, session, ledger):
    prof = fake_ocid("generativeairoutingprofile")
    session.on("POST", "/actions/chat", lambda m, u, b: FakeResponse(
        404, {"code": "NotAuthorizedOrNotFound", "message": f"Authorization failed or {prof} not found"}))
    r = chat.call(client, ledger, "s5_routing", chat.Target("P1", "profile", GROK, prof), 2)
    assert r["final"]["code"] == "NotAuthorizedOrNotFound" and r["final"]["answer_ok"] is False
    assert "<generativeairoutingprofile:phx>" in r["final"]["message"] and "ocid" + "1." not in json.dumps(r)


def test_budget_refusal_stops_before_sending(client, session, ledger):
    t = chat.Target("M:openai", "direct", "openai.gpt-5", "openai.gpt-5")
    r = chat.call(client, ledger, "s3_smoke", t, 0)
    assert "budget_refused" in r["final"] and session.calls == []


def test_responses_call_names_the_project(client, session, ledger):
    proj = fake_ocid("generativeaiproject")
    body = {"model": GROK, "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "CHARLIE"}]}],
            "usage": {"input_tokens": 12, "output_tokens": 2, "output_tokens_details": {"reasoning_tokens": 30}}}
    session.on("POST", "/openai/v1/responses", lambda m, u, b: FakeResponse(200, body))
    r = chat.call_responses(client, ledger, "s9_responses", chat.Target("M:" + GROK, "direct", GROK, GROK), 2, proj)
    assert r["final"]["answer_ok"] and session.calls[0]["headers"]["OpenAI-Project"] == proj
    assert session.calls[0]["body"]["store"] is False
    with pytest.raises(ValueError):
        chat.call_responses(client, ledger, "s9_responses", chat.Target("M:" + GROK, "direct", GROK, GROK), 0, "x")


def test_responses_openai_shape_error_is_recorded(client, session, ledger):
    proj = fake_ocid("generativeaiproject")
    prof = fake_ocid("generativeairoutingprofile")
    err = {"error": {"message": f"model {prof} is not supported", "type": "invalid_request_error",
                     "code": "model_not_supported", "param": "model"}}
    session.on("POST", "/openai/v1/responses", lambda m, u, b: FakeResponse(400, err))
    r = chat.call_responses(client, ledger, "s9_responses", chat.Target("P1", "profile", GROK, prof), 0, proj)
    f = r["final"]
    assert f["status"] == 400 and f["code"] == "model_not_supported" and f["answer_ok"] is False
    assert "(param: model)" in f["message"] and "<generativeairoutingprofile:phx>" in f["message"]
    assert "ocid" + "1." not in json.dumps(r)
    assert ledger.summary()["buckets"]["s9_responses"]["usd"] == 0.0          # a 400 costs nothing


def test_responses_raw_text_error_and_reserve_rerun(client, session, ledger):
    proj = fake_ocid("generativeaiproject")

    class Raw(FakeResponse):
        def json(self):
            raise ValueError("not json")
    resp = Raw(400)
    resp.content, resp.text = b"Bad Request: unknown project", "Bad Request: unknown project"
    session.on("POST", "/openai/v1/responses", lambda m, u, b: resp)
    r = chat.call_responses(client, ledger, "s9_responses", chat.Target("M:" + GROK, "direct", GROK, GROK), 1, proj,
                            use_reserve="S9 re-run: error text not captured")
    assert r["final"]["message"] == "Bad Request: unknown project" and r["final"]["code"] is None
    b = ledger.summary()["buckets"]
    assert b["reserve"]["calls"] == 1 and b["s9_responses"]["calls"] == 0
