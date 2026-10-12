# v1.2 2026-10-10  Brief 14: chat bodies, the budgeted inference call, response parsing (PLAN.md v1.1 S3, S5, S9).
#   v1.2: (S9 first run, 08:57Z: five 400s recorded with no code or message) the Responses API answers errors in the
#         OpenAI shape {"error": {"message", "type", "code", "param"}}, which Result.error_code()/error_message() (OCI
#         shape) do not read: call_responses now records that shape, and the redacted raw body (first 500 characters)
#         when neither shape is present; it also takes use_reserve (a re-run from the reserve bucket, with a reason).
#   v1.1: MAX_TOKENS comes from budget.py, so the per-call bound and the request cannot drift apart.
#   v1.0: first version.
"""Inference at the Phoenix endpoint, one attempt at a time, every attempt counted in the budget first.

A Target names what goes into servingMode.modelId: a model name (kind 'direct') or a routing profile OCID (kind
'profile'). The model behind it is always known (target.model) so the budget can price it and S5 can hold it
constant. Records returned by call() hold no OCID: returned model ids are reduced to type and region key.
"""
import time
from dataclasses import dataclass

from . import phoenix
from .budget import MAX_TOKENS, BudgetExceeded, Ledger
from .ociclient import OciClient
from .ocisign import OCID_RE, ocid_shape, redact

CHAT_URL = f"{phoenix.INFER_BASE}/actions/chat"
RESPONSES_URL = f"{phoenix.OPENAI_BASE}/responses"
PROMPTS = (
    ("ALPHA", "Reply with exactly one word: ALPHA"),
    ("BRAVO", "Reply with exactly one word: BRAVO"),
    ("CHARLIE", "Reply with exactly one word: CHARLIE"),
    ("DELTA", "Reply with exactly one word: DELTA"),
    ("ECHO", "Reply with exactly one word: ECHO"),
)
RETRY_429_WAITS = (4, 8)          # at most two retries after a 429, each counted as a call


@dataclass(frozen=True)
class Target:
    alias: str          # P1..P5 for profiles, M:<model> for direct calls
    kind: str           # 'direct' | 'profile'
    model: str          # the model behind it (price, and the constant in S5)
    serving_id: str     # what goes into servingMode.modelId (model name, or the profile OCID)

    def __post_init__(self):
        if self.kind not in ("direct", "profile"):
            raise ValueError("kind must be 'direct' or 'profile'")
        if self.kind == "direct" and self.serving_id != self.model:
            raise ValueError("a direct target sends the model name itself")
        if self.kind == "profile" and not OCID_RE.fullmatch(self.serving_id or ""):
            raise ValueError("a profile target sends a routing profile OCID")


def chat_body(compartment: str, serving_id: str, prompt: str, max_tokens: int = MAX_TOKENS) -> dict:
    return {
        "compartmentId": compartment,
        "servingMode": {"servingType": "ON_DEMAND", "modelId": serving_id},
        "chatRequest": {
            "apiFormat": "GENERIC",
            "messages": [{"role": "USER", "content": [{"type": "TEXT", "text": prompt}]}],
            "maxTokens": max_tokens,
            "temperature": 0,
        },
    }


def parse_chat(body) -> dict:
    """Fields S5 records from a ChatResult (inference spec ChatResult / GenericChatResponse / Usage)."""
    out = {"model_id_shape": None, "model_id_is_name": None, "model_version": None, "finish_reason": None,
           "text": None, "prompt_tokens": None, "completion_tokens": None, "reasoning_tokens": None}
    if not isinstance(body, dict):
        return out
    mid = body.get("modelId")
    if isinstance(mid, str):
        if OCID_RE.fullmatch(mid):
            out["model_id_shape"], out["model_id_is_name"] = ocid_shape(mid), False
        else:
            out["model_id_shape"], out["model_id_is_name"] = mid[:100], True
    out["model_version"] = body.get("modelVersion")
    resp = body.get("chatResponse") or {}
    choices = resp.get("choices") or []
    if choices and isinstance(choices[0], dict):
        out["finish_reason"] = choices[0].get("finishReason")
        content = ((choices[0].get("message") or {}).get("content")) or []
        out["text"] = "".join(c.get("text", "") for c in content if isinstance(c, dict))[:200]
    usage = resp.get("usage") or {}
    out["prompt_tokens"] = usage.get("promptTokens")
    out["completion_tokens"] = usage.get("completionTokens")
    out["reasoning_tokens"] = (usage.get("completionTokensDetails") or {}).get("reasoningTokens")
    return out


def answer_ok(text: str | None, word: str) -> bool:
    return bool(text) and word.upper() in text.upper()


def call(client: OciClient, ledger: Ledger, step: str, target: Target, prompt_no: int, *,
         use_reserve: str | None = None, sleep=time.sleep) -> dict:
    """One prompt to one target. Each attempt (first try and each 429 retry) is reserved before it is sent."""
    word, prompt = PROMPTS[prompt_no]
    body = chat_body(client.sec.compartment, target.serving_id, prompt)
    attempts = []
    for i in range(1 + len(RETRY_429_WAITS)):
        try:
            ticket = ledger.reserve(step, target.model, kind=target.kind, alias=target.alias,
                                    use_reserve=use_reserve if i == 0 else (use_reserve or "429 retry"))
        except BudgetExceeded as exc:
            attempts.append({"budget_refused": str(exc)})
            break
        res = client.request("POST", CHAT_URL, json_body=body, max_retries=0)
        p = parse_chat(res.body) if res.ok else parse_chat(None)
        ledger.settle(ticket, http_status=res.status, prompt_tokens=p["prompt_tokens"],
                      completion_tokens=p["completion_tokens"], reasoning_tokens=p["reasoning_tokens"])
        attempts.append({"ticket": ticket, "status": res.status, "latency_ms": res.latency_ms,
                         "error": res.error, "code": None if res.ok else res.error_code(),
                         "message": None if res.ok else res.error_message(client.sec), **p,
                         "answer_ok": answer_ok(p["text"], word) if res.ok else False,
                         "header_names": res.header_names})
        if res.status != 429 or i == len(RETRY_429_WAITS):
            break
        sleep(RETRY_429_WAITS[i])
    final = attempts[-1] if attempts else {}
    return {"step": step, "alias": target.alias, "kind": target.kind, "model": target.model,
            "prompt_no": prompt_no, "word": word, "attempts": len([a for a in attempts if "ticket" in a]),
            "final": {k: (redact(v, client.sec) if isinstance(v, str) else v) for k, v in final.items()},
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def responses_body(serving_id: str, prompt: str, max_output_tokens: int = MAX_TOKENS) -> dict:
    """OpenAI Responses API shape, store=false so no response object is kept in the project (S9)."""
    return {"model": serving_id, "input": prompt, "max_output_tokens": max_output_tokens, "store": False}


def parse_responses(body) -> dict:
    """Fields S9 records from an OpenAI-format Response object."""
    out = {"model_id_shape": None, "model_id_is_name": None, "model_version": None, "finish_reason": None,
           "text": None, "prompt_tokens": None, "completion_tokens": None, "reasoning_tokens": None}
    if not isinstance(body, dict):
        return out
    mid = body.get("model")
    if isinstance(mid, str):
        is_ocid = bool(OCID_RE.fullmatch(mid))
        out["model_id_shape"], out["model_id_is_name"] = (ocid_shape(mid), False) if is_ocid else (mid[:100], True)
    out["finish_reason"] = body.get("status")
    texts = []
    for item in body.get("output") or []:
        for c in (item.get("content") or []) if isinstance(item, dict) else []:
            if isinstance(c, dict) and c.get("type") == "output_text":
                texts.append(c.get("text", ""))
    out["text"] = "".join(texts)[:200] or None
    usage = body.get("usage") or {}
    out["prompt_tokens"] = usage.get("input_tokens")
    out["completion_tokens"] = usage.get("output_tokens")
    out["reasoning_tokens"] = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    return out


def openai_error(body) -> tuple:
    """(code, message) from an OpenAI-shape error body {"error": {"message", "type", "code", "param"}}, else Nones."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return None, None
    code = err.get("code") or err.get("type")
    msg = err.get("message")
    if msg is not None and err.get("param"):
        msg = f"{msg} (param: {err.get('param')})"
    return (None if code is None else str(code)), (None if msg is None else str(msg))


def call_responses(client: OciClient, ledger: Ledger, step: str, target: Target, prompt_no: int,
                   project_ocid: str, *, use_reserve: str | None = None) -> dict:
    """One Responses API call (S9): no retry, one budget ticket, the project named in the OpenAI-Project header."""
    if not OCID_RE.fullmatch(project_ocid or ""):
        raise ValueError("S9 needs the project OCID from the manifest")
    word, prompt = PROMPTS[prompt_no]
    try:
        ticket = ledger.reserve(step, target.model, kind=target.kind, alias=target.alias, use_reserve=use_reserve)
    except BudgetExceeded as exc:
        return {"step": step, "alias": target.alias, "kind": target.kind, "prompt_no": prompt_no,
                "final": {"budget_refused": str(exc)}}
    res = client.request("POST", RESPONSES_URL, json_body=responses_body(target.serving_id, prompt),
                         extra_headers={"OpenAI-Project": project_ocid}, max_retries=0)
    p = parse_responses(res.body) if res.ok else parse_responses(None)
    ledger.settle(ticket, http_status=res.status, prompt_tokens=p["prompt_tokens"],
                  completion_tokens=p["completion_tokens"], reasoning_tokens=p["reasoning_tokens"])
    code = message = None
    if not res.ok:
        code, message = openai_error(res.body)
        code = code or res.error_code()
        message = message or res.error_message(client.sec) or (res.text[:500] or None)   # res.text is redacted
    final = {"ticket": ticket, "status": res.status, "latency_ms": res.latency_ms, "error": res.error,
             "code": code, "message": message, **p,
             "answer_ok": answer_ok(p["text"], word) if res.ok else False, "header_names": res.header_names}
    return {"step": step, "alias": target.alias, "kind": target.kind, "model": target.model, "prompt_no": prompt_no,
            "word": word, "attempts": 1,
            "final": {k: (redact(v, client.sec) if isinstance(v, str) else v) for k, v in final.items()},
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
