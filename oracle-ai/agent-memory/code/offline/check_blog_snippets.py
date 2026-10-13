# v1.1 2026-10-09  v1.1: S4 tests the post's label expression on a real RecordRelation tuple. v1.0: offline check of the announcement's code snippets against the installed oracleagentmemory 26.8.0.
#                  No database, no network, no LLM: imports, signature binding, coroutine checks, and the client's
#                  own search-scope validator called on a bare instance. Each check prints PASS/FAIL and the evidence.
import asyncio, inspect, importlib, sys
import oracleagentmemory
from oracleagentmemory.core import (OracleAgentMemory, OracleDBMemoryStore, SchemaPolicy, MemoryExtractionConfig,
                                    MemoryLinkExtractionMode, OracleSearchResult, RecordRelation)
from oracleagentmemory.apis.searchscope import SearchScope
results = []
def check(name, ok, evidence):
    results.append((name, ok)); print(f"[{'WORKS' if ok else 'FAILS'}] {name}\n        {evidence}")
print("oracleagentmemory", oracleagentmemory.version.__version__ if hasattr(oracleagentmemory.version, '__version__') else oracleagentmemory.__version__)
# S1  from oracleagentmemory.stores.oracledb import OracleDBMemoryStore, SchemaPolicy
try:
    importlib.import_module("oracleagentmemory.stores.oracledb"); check("S1 import path oracleagentmemory.stores.oracledb", True, "imported")
except ModuleNotFoundError as e:
    check("S1 import path oracleagentmemory.stores.oracledb", False, f"{type(e).__name__}: {e}; corrected: from oracleagentmemory.core import OracleDBMemoryStore, SchemaPolicy")
# S2  memory.add_memory(user_id=..., agent_id=..., text=..., metadata=...)
sig = inspect.signature(OracleAgentMemory.add_memory)
try:
    sig.bind(object(), user_id="U", agent_id="A", text="t", metadata={}); check("S2 add_memory(text=...)", True, "binds")
except TypeError as e:
    check("S2 add_memory(text=...)", False, f"TypeError: {e} (text= is swallowed by **store_kwargs; content is positional-or-keyword "
          "and required); corrected: memory.add_memory(item['text'], user_id=..., agent_id=..., metadata=..., memory_type='preference')")
# S3  await memory.search(query=..., num_hops=0)   (sync method, and no user_id)
is_coro = inspect.iscoroutinefunction(OracleAgentMemory.search)
async def _await_list():
    return await [1]  # what 'await memory.search(...)' does with the list the sync search returns
try:
    asyncio.run(_await_list()); awaited = "awaiting a list worked"
except TypeError as e:
    awaited = f"TypeError: {e}"
check("S3a await memory.search(...)", is_coro, f"iscoroutinefunction(search)={is_coro}, search_async={inspect.iscoroutinefunction(OracleAgentMemory.search_async)}; {awaited}; "
      "corrected: memory.search(...) without await, or await memory.search_async(...)")
bare = object.__new__(OracleAgentMemory)
try:
    bare._validate_search_scope(SearchScope()); check("S3b search without user_id", True, "accepted")
except Exception as e:
    check("S3b search without user_id", False, f"{type(e).__name__}: {e} (core/oracleagentmemory.pyc:837); corrected: add user_id=CUSTOMER_ID")
# S4  linked_results(result) / result_text(linked) helpers and getattr(linked, 'link_type' | 'relationship')
has_helper = any(hasattr(importlib.import_module(m), "linked_results") for m in ("oracleagentmemory.core", "oracleagentmemory.apis"))
rel = RecordRelation(id="r1", source_record_id="new", source_record_type="memory", target_record_id="old",
                     target_record_type="memory", relation_type="supersedes", opposite_relation_type="is_superseded_by",
                     timestamp=None, metadata=None, created_at=None)
linked = (rel, None)  # the element shape of OracleSearchResult.linked_results (docstring: RecordRelation, SearchResult pair)
label = getattr(linked, "link_type", getattr(linked, "relationship", "linked"))  # the post's expression, verbatim
check("S4 linked_results loop: relationship label", label == "supersedes" and has_helper,
      f"SDK exports no linked_results() (the post relies on an unshown notebook helper); each element is a tuple, so the "
      f"post's getattr chain returns {label!r} for a 'supersedes' link; corrected: for rel, child in result.linked_results: rel.relation_type")
# S5  top_result.format_content()
check("S5 format_content()", hasattr(OracleSearchResult, "format_content"), f"signature {inspect.signature(OracleSearchResult.format_content)}")
# S6  MemoryExtractionConfig(memory_extraction_frequency=1, memory_link_extraction_mode=POST_EXTRACTION)
try:
    MemoryExtractionConfig(memory_extraction_frequency=1, memory_link_extraction_mode=MemoryLinkExtractionMode.POST_EXTRACTION)
    check("S6 MemoryExtractionConfig(..., POST_EXTRACTION)", True, "constructed offline (import from oracleagentmemory.core)")
except Exception as e:
    check("S6 MemoryExtractionConfig", False, repr(e))
# S7  memory.link_records(source_record_id=..., source_record_type="preference", ..., relation_type="supersedes", metadata=...)
try:
    inspect.signature(OracleAgentMemory.link_records).bind(object(), source_record_id="n", source_record_type="preference",
        target_record_id="o", target_record_type="preference", relation_type="supersedes", metadata={"reason": "x"})
    check("S7 link_records(...) keywords", True, "binds; but S2 stores memory_type 'memory', so type 'preference' is expected to hit "
          "'A relation endpoint has an incompatible record type.' (core/oracledbmemorystore.pyc:4858) - to confirm in the POC")
except TypeError as e:
    check("S7 link_records", False, str(e))
# S8  oracledb.defaults.program = "..."
import oracledb
check("S8 oracledb.defaults.program", hasattr(oracledb.defaults, "program"), f"oracledb {oracledb.__version__}: defaults.program present={hasattr(oracledb.defaults, 'program')}")
# S9  OracleDBMemoryStore(**store_kwargs, schema_policy=SchemaPolicy.CREATE_IF_NECESSARY)  (after the S1 import fix)
try:
    inspect.signature(OracleDBMemoryStore).bind(embedder=None, pool=object(), memory_store_id="DEMO", schema_policy=SchemaPolicy.CREATE_IF_NECESSARY)
    check("S9 OracleDBMemoryStore(**store_kwargs, schema_policy=...)", True, "binds once the import is fixed")
except TypeError as e:
    check("S9 OracleDBMemoryStore", False, str(e))
print(f"\nSUMMARY: {sum(1 for _, ok in results if ok)} work, {sum(1 for _, ok in results if not ok)} fail as written")
