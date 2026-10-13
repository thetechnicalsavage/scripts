# v1.1 2026-10-10  Brief 12 step 0 (PLAN 6, row 0): database smoke checks as AGENTMEM, no LLM.
#                  v1.1: (after the first run) D5 checks what PLAN step 0 names: DBMS_VECTOR_CHAIN and DBMS_HYBRID_VECTOR
#                  visible and VALID to AGENTMEM; Oracle Text's status comes from 01 as SYSDBA (CONTEXT and CTX_DDL
#                  VALID). v1.0 also required CTXSYS.CTX_DDL to be visible to AGENTMEM, which it is not (CTX_DDL is
#                  granted to CTXAPP, not PUBLIC); that visibility is now reported, not required: the KEYWORD store
#                  creates a CONTEXT index, and a privilege error there is handled by PLAN 12 (ask the operator).
#                  v1.0: first version (day 1).
"""Step 0, database part. Run on the demo VM from ~/agentmem-poc/code with the POC venv:
    .venv/bin/python py/00_smoke_db.py
Checks (each pass/fail with latency, error text verbatim after redaction):
  D1 session: container, session user, product version (PRODUCT_COMPONENT_VERSION; banner_full comes from 01 as SYSDBA)
  D2 model dims: VECTOR_DIMENSION_COUNT(VECTOR_EMBEDDING(ASKORACLE.ALL_MINILM_L12_V2 USING 'hello' AS data)) = 384
  D3 the 256-token limit (PLAN 2, R15): a 300-word text of single-token words is embedded, then its prefixes of
     240..300 words; the smallest prefix whose vector equals the full text's vector (cosine distance < 1e-6) gives the
     effective input limit. Pass if that prefix is 254 words (254 + [CLS] + [SEP] = 256 tokens); anything else is
     reported as found.
  D4 AGENTMEM's privileges (SESSION_PRIVS, USER_TAB_PRIVS) against PLAN 11.1's list
  D5 DBMS_VECTOR_CHAIN and DBMS_HYBRID_VECTOR visible and VALID to AGENTMEM; CTX_DDL visibility reported (Oracle
     Text's status is read by 01 as SYSDBA)
  D6 the SDK's OracleDBEmbedder embeds through the database: dimension 384 and the meter's EmbedderStartedEvent
     concrete_class is OracleDBEmbedder; litellm embedding calls 0
  D7 the SDK's resolved defaults (extraction window, frequency, token limits, mode, link mode; pruning defaults;
     index sync per strategy) written to the transcript
  D8 the litellm switches of PLAN 10.3 asserted
Any failed check makes the script exit 1 (PLAN 12: a step 0 failure stops the run).
Results: staging/s0/results/smoke_db.json; transcript staging/s0/transcripts/.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import runlog  # noqa: E402  (first: redaction before anything else)

RUN = runlog.start("s0", tag="s0_db")

from lib import amconf, meter  # noqa: E402

LITELLM = amconf.configure_litellm()

import oracledb  # noqa: E402
from oracleagentmemory.observability import register_event_listeners  # noqa: E402

WORDS = ("red", "blue", "green", "black", "white", "small", "large", "old", "new", "good")
EXPECTED_PRIVS = {"CREATE SESSION", "CREATE TABLE", "CREATE PROCEDURE", "CREATE TRIGGER", "CREATE PROPERTY GRAPH",
                  "CREATE JOB"}
checks: list[dict] = []


def check(cid: str, desc: str):
    def deco(fn):
        t = time.perf_counter()
        rec = {"id": cid, "check": desc}
        try:
            ok, detail = fn()
            rec.update(passed=bool(ok), detail=detail)
        except Exception as exc:                       # recorded verbatim (redacted), the run then fails
            rec.update(passed=False, error_type=type(exc).__name__, error=str(exc)[:2000])
        rec["latency_ms"] = round((time.perf_counter() - t) * 1000, 1)
        checks.append(rec)
        RUN.log.info("%s %s: %s", cid, desc, "PASS" if rec["passed"] else "FAIL")
        return fn
    return deco


pool = amconf.make_pool("AGENTMEM", 1, 2)


@check("D1", "session and version")
def _d1():
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("select sys_context('userenv','con_name'), sys_context('userenv','session_user') from dual")
        con, usr = cur.fetchone()
        cur.execute("select product, version_full from product_component_version")
        prods = [list(r) for r in cur.fetchall()]
    return (con == "ORCLPDB1" and usr == "AGENTMEM"), {"container": con, "session_user": usr, "products": prods}


@check("D2", "model dimension through VECTOR_EMBEDDING")
def _d2():
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("select vector_dimension_count(vector_embedding(ASKORACLE.ALL_MINILM_L12_V2 using :t as data)) "
                    "from dual", t="hello")
        (dims,) = cur.fetchone()
    return dims == amconf.EMBED_DIM, {"dims": dims}


@check("D3", "256-token input limit (vector of the full text equals the vector of a prefix)")
def _d3():
    words = [WORDS[i % len(WORDS)] for i in range(300)]
    full = " ".join(words)
    dist = {}
    with pool.acquire() as c, c.cursor() as cur:
        for k in range(240, 301):
            cur.execute("select vector_distance(vector_embedding(ASKORACLE.ALL_MINILM_L12_V2 using :a as data), "
                        "vector_embedding(ASKORACLE.ALL_MINILM_L12_V2 using :b as data), cosine) from dual",
                        a=full, b=" ".join(words[:k]))
            dist[k] = float(cur.fetchone()[0])
    equal = [k for k, d in dist.items() if d < 1e-6]
    smallest = min(equal) if equal else None
    detail = {"words_in_full_text": 300, "smallest_equal_prefix_words": smallest,
              "distance_at": {str(k): dist[k] for k in (250, 252, 253, 254, 255, 256, 300)},
              "rule": "one word = one wordpiece token; 254 words + [CLS] + [SEP] = 256 tokens"}
    return smallest == 254, detail


@check("D4", "AGENTMEM privileges against PLAN 11.1")
def _d4():
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("select privilege from session_privs order by 1")
        privs = [r[0] for r in cur.fetchall()]
        cur.execute("select privilege, owner, table_name, type from user_tab_privs where grantee = user "
                    "order by owner, table_name, privilege")
        tab = [list(r) for r in cur.fetchall()]
    model = any(t[1] == "ASKORACLE" and t[2] == "ALL_MINILM_L12_V2" for t in tab)
    missing = sorted(EXPECTED_PRIVS - set(privs))
    extra = sorted(set(privs) - EXPECTED_PRIVS)
    return (not missing and not extra and model), {"session_privs": privs, "missing": missing, "extra": extra,
                                                   "tab_privs": tab, "model_grant": model}


@check("D5", "vector and hybrid packages visible and VALID to AGENTMEM (CTX_DDL visibility reported)")
def _d5():
    with pool.acquire() as c, c.cursor() as cur:
        cur.execute("select owner, object_name, status from all_objects where object_type = 'PACKAGE' "
                    "and object_name in ('DBMS_VECTOR_CHAIN','DBMS_HYBRID_VECTOR','CTX_DDL') order by 2")
        rows = [list(r) for r in cur.fetchall()]
    names = {r[1] for r in rows if r[2] == "VALID"}
    return {"DBMS_VECTOR_CHAIN", "DBMS_HYBRID_VECTOR"} <= names, {
        "packages": rows, "ctx_ddl_visible_to_agentmem": "CTX_DDL" in names,
        "note": "CTX_DDL is granted to CTXAPP, not PUBLIC; Oracle Text status from 01 (SYSDBA)"}


m = meter.Meter()
lc = meter.LitellmCounter()
lc.install()


@check("D6", "SDK OracleDBEmbedder embeds in the database")
def _d6():
    emb = amconf.make_embedder(pool)
    with register_event_listeners([m]):
        vecs = emb.embed(["hello world", "roaming add-on charged twice"])
    s = m.summary()
    classes = s["embedder_events_by_class"]
    started = {k.split(":", 1)[1] for k in classes if k.startswith("EmbedderStartedEvent:")}
    ok = (len(vecs) == 2 and all(len(v) == amconf.EMBED_DIM for v in vecs) and started == {"OracleDBEmbedder"}
          and lc.summary()["calls"].get("embedding", 0) == 0 and lc.summary()["calls"].get("aembedding", 0) == 0)
    return ok, {"vectors": len(vecs), "dims": [len(v) for v in vecs], "embedder_events": classes,
                "litellm_calls": lc.summary()["calls"]}


@check("D7", "SDK resolved defaults")
def _d7():
    from oracleagentmemory.core import MemoryExtractionConfig, SearchIndexSyncMode, SearchStrategy
    from oracleagentmemory.core._db.hybrid import resolve_search_strategy
    from oracleagentmemory.core._postsearch import _pruning
    from oracleagentmemory.core.extractors._oneshotextractor import _ResolvedMemoryExtractionConfig
    from oracleagentmemory._notset import NOT_SET_MARKER
    r = _ResolvedMemoryExtractionConfig.from_public_config(MemoryExtractionConfig())
    ext = {k: (v.value if hasattr(v, "value") else v) for k, v in vars(r).items()}
    prune = {k: v for k, v in vars(_pruning).items() if k.isupper() and isinstance(v, (int, float, str))}
    sync = {}
    for st in (SearchStrategy.VECTOR, SearchStrategy.HYBRID, SearchStrategy.KEYWORD):
        try:
            s, mode = resolve_search_strategy(st, NOT_SET_MARKER)
            sync[st.value] = mode.value if mode is not None else None
        except Exception as exc:                       # recorded, not hidden
            sync[st.value] = f"error {type(exc).__name__}"
    ok = (ext.get("memory_extraction_window") == -1 and ext.get("memory_extraction_token_limit") == 100000
          and ext.get("memory_link_extraction_token_limit") == 100000)
    return ok, {"extraction_defaults": ext, "pruning_module_constants": prune, "index_sync_default": sync,
                "sync_modes": [x.value for x in SearchIndexSyncMode]}


@check("D8", "litellm switches (PLAN 10.3)")
def _d8():
    return True, LITELLM


pool.close()
failed = [c["id"] for c in checks if not c["passed"]]
RUN.write_json("results", "smoke_db.json", {"checks": checks, "failed": failed, "meter": m.summary(),
                                             "litellm_counter": lc.summary()})
for c in checks:
    RUN.transcript(f"- {c['id']} {c['check']}: {'PASS' if c['passed'] else 'FAIL'} ({c['latency_ms']} ms)")
RUN.finish({"failed": failed, "checks": len(checks)})
sys.exit(1 if failed else 0)
