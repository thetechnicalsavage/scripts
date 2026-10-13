# v1.1 2026-10-10  Brief 12 step 0 (PLAN 6 row 0, 10.5, D5): the LLM through the SDK's Llm and LiteLLM's oci/
#                  route with the D4 config: "Reply OK", one structured-output call with the SDK's extraction schema,
#                  the model choice by PLAN 10.5's fallback order, then the vision check.
#                  v1.1: (Codex review) the litellm counter enforces the budget before every dispatch.
#                  v1.0: first version (day 1).
"""Step 0, LLM part. Run on the demo VM from ~/agentmem-poc/code with the POC venv:
    .venv/bin/python py/00_smoke_llm.py
For each model in PLAN 10.5's order (grok-4.20-non-reasoning, -reasoning, -0309-non-reasoning), until one passes:
  L1 "Reply OK": one call; passes when the reply contains OK.
  L2 structured output: one call with response_json_schema = the SDK's MEMORY_EXTRACTION_JSON_SCHEMA on a fictional
     two-message exchange; passes when the reply validates against the SDK's own pydantic model; on failure retried
     once with enable_structured_output_reminder=True (10.5) before the next model.
  Each failed call is retried at most twice, after 5 s and 20 s (10.5); the error type and its redacted text are kept.
The first model passing L1 and L2 is written to state/llm_choice.json (model, structured_reminder) and results.
  L3 vision (D5: only to decide step 6's caption): the frozen 64x64 PNG (a white circle on a blue background) with
     supports_vision=True on the chosen model; passes when the reply names blue, white and a round shape (rule fixed
     here before the run: contains "blue" and "white" and one of circle, round, disc, disk, dot, ball, sphere, oval).
     A vision failure only cancels step 6's caption (PLAN 6 row 0).
If no model passes L1 and L2, the run stops (exit 1): fallback (3), an ILlm over the OCI SDK, needs the operator.
Every LLM call is counted by the allow-listed listener and the litellm counter; the POC ledger enforces D5's budget.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s0", tag="s0_llm", need_oci=True)

from lib import amconf, meter  # noqa: E402

LITELLM = amconf.configure_litellm()

from oracleagentmemory.apis.message import ImageContent, ImageMimeType, Message, TextContent  # noqa: E402
from oracleagentmemory.core.extractors._oneshotextractor import (  # noqa: E402
    MEMORY_EXTRACTION_JSON_SCHEMA, _MemoryExtractionPayload)
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

CODE = Path(__file__).resolve().parents[1]
VISION_PNG = CODE / "data" / "frozen" / "images" / "vision_check_64.png"
ROUND_WORDS = ("circle", "round", "disc", "disk", "dot", "ball", "sphere", "oval")
STRUCT_PROMPT = [
    {"role": "system", "content": "You extract durable memories about the customer from a support conversation. "
                                  "Return only a JSON object that follows the provided JSON schema."},
    {"role": "user", "content": "Conversation (fictional):\n"
                                "customer: Please contact me on WhatsApp only, never by phone call.\n"
                                "agent: Noted. Your current plan is the family plan PLN-FAM-4 with four lines."},
]
RETRY_SLEEPS = (5, 20)

m = meter.Meter()
ledger = meter.Ledger(amconf.STATE / "token_ledger.json", amconf.TOKEN_BUDGET)
lc = meter.LitellmCounter(meter=m, ledger=ledger)
lc.install()
guard = meter.BudgetGuard(ledger, m, lc)


def call(fn):
    """One logical call with at most two retries (10.5); returns (ok, value_or_error_record, attempts)."""
    errors = []
    for attempt in range(len(RETRY_SLEEPS) + 1):
        guard.check()
        t = time.perf_counter()
        try:
            val = fn()
            return True, val, attempt + 1, round((time.perf_counter() - t) * 1000, 1), errors
        except meter.BudgetExceeded:
            raise
        except Exception as exc:                      # kept verbatim after redaction; never retried past 2
            errors.append({"error_type": type(exc).__name__, "error": str(exc)[:1500]})
            RUN.log.warning("call failed (%s), attempt %d", type(exc).__name__, attempt + 1)
            if attempt < len(RETRY_SLEEPS):
                time.sleep(RETRY_SLEEPS[attempt])
    return False, None, len(RETRY_SLEEPS) + 1, None, errors


def try_model(model: str) -> dict:
    rec: dict = {"model": model}
    llm = amconf.make_llm(model)
    ok, resp, att, ms, errs = call(lambda: llm.generate("Reply OK"))
    text = getattr(resp, "text", "") if ok else ""
    rec["L1_reply_ok"] = {"passed": ok and "OK" in text.upper(), "reply": text[:80], "attempts": att,
                          "latency_ms": ms, "errors": errs}
    if not rec["L1_reply_ok"]["passed"]:
        return rec
    rec["L2_structured"] = []
    for reminder in (False, True):
        llm2 = llm if not reminder else amconf.make_llm(model, structured_reminder=True)
        ok, resp, att, ms, errs = call(lambda: llm2.generate(STRUCT_PROMPT,
                                                              response_json_schema=MEMORY_EXTRACTION_JSON_SCHEMA))
        r2 = {"structured_reminder": reminder, "attempts": att, "latency_ms": ms, "errors": errs}
        if ok:
            try:
                payload = _MemoryExtractionPayload.model_validate_json(resp.text)
                r2.update(passed=True, memories=[{"record_type": x.record_type, "text": x.text}
                                                 for x in payload.memories])
            except Exception as exc:                  # schema not honoured: recorded, then the reminder retry
                r2.update(passed=False, validation_error=type(exc).__name__, reply_head=resp.text[:300])
        else:
            r2["passed"] = False
        rec["L2_structured"].append(r2)
        if r2["passed"]:
            rec["structured_reminder"] = reminder
            break
    rec["passed"] = any(x["passed"] for x in rec["L2_structured"])
    return rec


def vision(model: str) -> dict:
    png = VISION_PNG.read_bytes()
    llm = amconf.make_llm(model, supports_vision=True)
    prompt = [Message(role="user", content=[
        TextContent("Describe this image in one short sentence: name the shape and its colours."),
        ImageContent(png, ImageMimeType.PNG)])]
    ok, resp, att, ms, errs = call(lambda: llm.generate(prompt))
    text = getattr(resp, "text", "") if ok else ""
    low = text.lower()
    passed = ok and "blue" in low and "white" in low and any(w in low for w in ROUND_WORDS)
    return {"model": model, "passed": passed, "reply": text[:300], "attempts": att, "latency_ms": ms,
            "errors": errs, "rule": "contains blue and white and one of " + ", ".join(ROUND_WORDS)}


tried: list[dict] = []
choice = None
vis = None
try:
    guard.check()
    with register_event_listeners([m]):
        for model in amconf.LLM_CANDIDATES:
            rec = try_model(model)
            tried.append(rec)
            RUN.log.info("model %s: L1 %s, structured %s", model,
                         rec["L1_reply_ok"]["passed"], rec.get("passed", False))
            if rec.get("passed"):
                choice = {"model": model, "structured_reminder": bool(rec.get("structured_reminder")),
                          "chosen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "rule": "PLAN 10.5 order; first model passing Reply OK and the structured-output call"}
                break
        if choice:
            vis = vision(choice["model"])
            choice["vision_check_passed"] = vis["passed"]
finally:
    tok, calls = meter.step_tokens(m, lc)
    ledger.commit("s0_llm", tok, calls)

if choice:
    amconf.STATE.mkdir(parents=True, exist_ok=True)
    amconf.LLM_CHOICE_FILE.write_text(json.dumps(choice, indent=1, sort_keys=True) + "\n", encoding="utf-8")
RUN.write_json("results", "smoke_llm.json", {"tried": tried, "choice": choice, "vision_check": vis,
                                              "meter": m.summary(), "litellm_counter": lc.summary(),
                                              "ledger_total_tokens": ledger.total, "litellm_switches": LITELLM})
RUN.transcript(f"- models tried: {[t['model'] for t in tried]}")
RUN.transcript(f"- chosen: {choice['model'] if choice else 'NONE'}")
RUN.transcript(f"- vision check: {'PASS' if vis and vis['passed'] else 'FAIL or not run'}")
RUN.finish({"chosen": choice, "vision_passed": bool(vis and vis["passed"]),
            "llm_calls": m.summary()["llm_calls"], "tokens": m.summary()["tokens"]})
sys.exit(0 if choice else 1)
