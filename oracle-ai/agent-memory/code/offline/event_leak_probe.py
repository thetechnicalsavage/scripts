# v1.0 2026-10-09  Offline probe: does an SDK LLM event carry the OCI auth kwargs? litellm's completion functions are
#                  replaced by a stub that returns a canned response, so no network call is made. Fake values only.
import json, litellm
from oracleagentmemory.core.llms import Llm
from oracleagentmemory.observability import register_event_listeners, EventListener
FAKE = {"oci_compartment_id": "ocid1.compartment.oc1..FAKECOMP", "oci_region": "us-phoenix-1",
        "oci_user": "ocid1.user.oc1..FAKEUSER", "oci_fingerprint": "aa:bb:FAKEFP", "oci_tenancy": "ocid1.tenancy.oc1..FAKETEN",
        "oci_key_file": "/nonexistent/FAKE_key.pem"}
def stub(*a, **k):
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "OK"}}],
                                 usage={"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8})
async def astub(*a, **k): return stub()
litellm.completion = stub; litellm.acompletion = astub
class Cap(EventListener):
    def __init__(self): self.events = []
    def on_event(self, e): self.events.append(e)
cap = Cap()
llm = Llm(model="oci/xai.grok-4.20-non-reasoning", supports_vision=False, **FAKE)
with register_event_listeners([cap]):
    try:
        r = llm.generate("Reply with OK.")
        print("generate ->", getattr(r, "text", r))
    except Exception as e:
        print("generate raised", type(e).__name__, str(e)[:200])
markers = ["FAKECOMP", "FAKEUSER", "FAKEFP", "FAKETEN", "FAKE_key.pem"]
for e in cap.events:
    views = {"repr": repr(e), "model_dump_json": e.model_dump_json(),
             "to_observability_dict": json.dumps(e.to_observability_dict(), default=str)}
    for name, text in views.items():
        print(f"{type(e).__name__:32s} {name:22s} leaks: {[m for m in markers if m in text]}")
    if hasattr(e, "total_tokens"): print("   tokens in/out/total:", e.input_tokens, e.output_tokens, e.total_tokens)
