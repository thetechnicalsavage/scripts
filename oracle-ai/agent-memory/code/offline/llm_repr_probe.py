# v1.0 2026-10-09  Offline: does an oracleagentmemory Llm object expose OCI auth values through repr(), str() or its
#                  __dict__? Fake values only; no network call (set LITELLM_LOCAL_MODEL_COST_MAP=True), no OCI, no LLM.
from oracleagentmemory.core.llms import Llm

FAKES = ["FAKECOMP", "FAKEREGION", "FAKEUSER", "FAKEFP", "FAKETEN", "FAKE_key.pem"]
llm = Llm(model="oci/xai.grok-4.20-non-reasoning", temperature=0, oci_compartment_id="FAKECOMP",
          oci_region="FAKEREGION", oci_user="FAKEUSER", oci_fingerprint="FAKEFP", oci_tenancy="FAKETEN",
          oci_key_file="/fake/FAKE_key.pem")
views = {"repr": repr(llm), "str": str(llm), "__dict__": repr(getattr(llm, "__dict__", {}))}
for name, text in views.items():
    print(f"Llm {name:8} leaks: {[f for f in FAKES if f in text]}")
