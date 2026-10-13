# v1.0 2026-10-09  Offline: second-pass API introspection of oracleagentmemory 26.8.0 for the gaps left by introspect.py
#                  v1.2 (public method inventory, config defaults, record_types values, store-id rule, search_config
#                  routing, summaries, oam CLI). No database, no network: run with LITELLM_LOCAL_MODEL_COST_MAP=True.
import inspect, os, re, sys, dataclasses
import oracleagentmemory.core as c
from oracleagentmemory.core import (OracleAgentMemory, OracleThread, MemoryExtractionConfig, TopKMemorySearchConfig,
                                    PruningMemorySearchConfig, OracleSummary, OracleSearchResultFormatConfig)

SITE = os.path.dirname(os.path.dirname(c.__file__))
NOTSET_RE = re.compile(r"<oracleagentmemory\._notset\._NotSetMarker object at 0x[0-9a-f]+>")


def where(obj):
    f = inspect.unwrap(obj)
    code = getattr(f, "__code__", None)
    mod = inspect.getmodule(f)
    pyc = os.path.relpath(mod.__file__, SITE) if mod and getattr(mod, "__file__", None) else "?"
    return f"{pyc}:{code.co_firstlineno}" if code else pyc


def sig(obj):
    try:
        s = str(inspect.signature(obj))
    except (TypeError, ValueError) as exc:  # builtins / C slots have no signature
        return f"<no signature: {exc}>"
    s = NOTSET_RE.sub("NOTSET", s)
    s = re.sub(r" \| oracleagentmemory\._notset\._NotSetMarker", "", s)
    return re.sub(r"(?:oracleagentmemory|typing|collections\.abc)\.[a-zA-Z_.]*?\.?(?=[A-Z])", "", s)


def inventory(cls):
    print(f"## public methods of {cls.__name__} (defined where)")
    for name, obj in sorted(inspect.getmembers(cls)):
        if name.startswith("_") or not callable(obj):
            continue
        print(f"- {name}  at {where(obj)}")


def defaults(cls):
    print(f"## {cls.__name__} defaults")
    try:
        inst = cls() if cls is not PruningMemorySearchConfig else None
    except Exception as exc:  # report, never hide: a required argument is itself a finding
        print(f"  cannot construct with no arguments: {type(exc).__name__}: {exc}")
        inst = None
    if inst is not None:
        fields = getattr(inst, "model_fields", None) or getattr(cls, "__dataclass_fields__", {})
        for f in fields:
            print(f"  {f} = {NOTSET_RE.sub('NOTSET', repr(getattr(inst, f, '?')))}")
    else:
        print(f"  sig: {sig(cls)}")


def doc_grep(obj, pattern, label):
    doc = inspect.getdoc(obj) or ""
    hits = [ln.strip() for ln in doc.splitlines() if re.search(pattern, ln, re.I)]
    print(f"## docstring lines matching /{pattern}/ in {label} ({where(obj)})")
    for h in hits:
        print(f"  | {h}")


if __name__ == "__main__":
    for cls in (OracleAgentMemory, OracleThread, OracleSummary):
        inventory(cls)
    for cls in (MemoryExtractionConfig, TopKMemorySearchConfig, OracleSearchResultFormatConfig):
        defaults(cls)
    print(f"## PruningMemorySearchConfig sig: {sig(PruningMemorySearchConfig)}")
    doc_grep(OracleAgentMemory.search, r"record_types|search_config|max_results|token_budget|prun|rerank", "search")
    doc_grep(OracleAgentMemory.__init__, r"search_config|pruner|schema_owner|REQUIRE_EXISTING|memory_store_id", "client init")
    doc_grep(OracleThread.get_summary, r"summary|persist|reuse|refresh|LLM", "get_summary")
    doc_grep(OracleAgentMemory.create_thread, r"summary|enable_context_summary", "create_thread")
    doc_grep(OracleAgentMemory.link_records, r"invalid|valid|status|supersed|refine|duplicat", "link_records")
    doc_grep(OracleAgentMemory.update_record_link, r"invalid|valid|status|type", "update_record_link")
    doc_grep(OracleAgentMemory.add_image, r"caption|description|llm|search", "add_image")
    sys.stdout.flush()
