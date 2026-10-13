# v1.2 2026-10-09  v1.2: Literal aliases printed with get_args. v1.1: compact signatures (NOTSET marker, short type names). v1.0: offline API introspection of oracleagentmemory 26.8.0 (no DB, no network). Prints signature,
#                  installed .pyc path, source line from the code object, and the docstring head for each target.
import inspect, importlib, sys, os, enum, re, typing
import oracleagentmemory.core as c
from oracleagentmemory.core import (OracleAgentMemory, OracleDBMemoryStore, OracleThread, OracleSearchResult,
    OracleSearchResultFormatConfig, MemoryExtractionConfig, MemoryLinkExtractionMode, SchemaPolicy, SearchStrategy,
    SearchIndexSyncMode, PruningMemorySearchConfig, TopKMemorySearchConfig, PruningEvaluationMode, MemoryLinkType,
    MemoryLinkOppositeType, Reranker, MemoryExtractionMode, MemoryExtractionImageContext, OracleSummary,
    MemoryRetentionConfig, RecordRelation)
from oracleagentmemory.core.llms import Llm
from oracleagentmemory.core.embedders import OracleDBEmbedder, Embedder
from oracleagentmemory.apis import RecordStatus
import oracleagentmemory.observability as obs
SITE = os.path.dirname(os.path.dirname(c.__file__))
def where(obj):
    f = inspect.unwrap(obj)
    code = getattr(f, '__code__', None)
    mod = inspect.getmodule(f)
    pyc = os.path.relpath(mod.__file__, SITE) if mod and getattr(mod, '__file__', None) else '?'
    return f"{pyc}:{code.co_firstlineno}" if code else pyc
def compact(sig):
    sig = re.sub(r"<oracleagentmemory\._notset\._NotSetMarker object at 0x[0-9a-f]+>", "NOTSET", sig)
    sig = re.sub(r" \| oracleagentmemory\._notset\._NotSetMarker", "", sig)
    sig = re.sub(r"Union\[(.*?), oracleagentmemory\._notset\._NotSetMarker\]", r"\1", sig)
    sig = re.sub(r"(?:oracleagentmemory|typing)\.[a-zA-Z_.]*\.(?=[A-Z])", "", sig)
    sig = re.sub(r"typing\.", "", sig)
    return sig
def show(label, obj, doclines=6):
    try: sig = compact(str(inspect.signature(obj)))
    except Exception as e: sig = f'<no signature: {e}>'
    doc = (inspect.getdoc(obj) or '').strip().splitlines()
    print(f"### {label}\n  at: {where(obj)}\n  sig: {sig}")
    for l in doc[:doclines]: print('  | ' + l)
def members(label, E):
    mod = inspect.getmodule(E)
    print(f"### {label} (enum, {os.path.relpath(mod.__file__, SITE)}): " + ', '.join(f"{m.name}={m.value!r}" for m in E))
targets = [
 ('OracleDBMemoryStore.__init__', OracleDBMemoryStore.__init__),
 ('OracleAgentMemory.__init__', OracleAgentMemory.__init__),
 ('OracleAgentMemory.add_memory', OracleAgentMemory.add_memory),
 ('OracleAgentMemory.create_thread', OracleAgentMemory.create_thread),
 ('OracleAgentMemory.get_thread', getattr(OracleAgentMemory, 'get_thread', None)),
 ('OracleThread.add_messages', OracleThread.add_messages),
 ('OracleThread.add_image', OracleThread.add_image),
 ('OracleAgentMemory.add_image', getattr(OracleAgentMemory, 'add_image', None)),
 ('OracleThread.wait_for_memory_extraction', getattr(OracleThread, 'wait_for_memory_extraction', None)),
 ('OracleAgentMemory.wait_for_memory_extraction', getattr(OracleAgentMemory, 'wait_for_memory_extraction', None)),
 ('OracleAgentMemory.search', OracleAgentMemory.search),
 ('OracleAgentMemory.search_async', OracleAgentMemory.search_async),
 ('OracleThread.search', getattr(OracleThread, 'search', None)),
 ('OracleDBMemoryStore.search', OracleDBMemoryStore.search),
 ('OracleSearchResult.format_content', OracleSearchResult.format_content),
 ('OracleSearchResultFormatConfig', OracleSearchResultFormatConfig),
 ('OracleAgentMemory.link_records', OracleAgentMemory.link_records),
 ('OracleAgentMemory.update_record_link', OracleAgentMemory.update_record_link),
 ('OracleAgentMemory.delete_record_link', OracleAgentMemory.delete_record_link),
 ('OracleThread.link_records', getattr(OracleThread, 'link_records', None)),
 ('MemoryExtractionConfig', MemoryExtractionConfig),
 ('TopKMemorySearchConfig', TopKMemorySearchConfig),
 ('PruningMemorySearchConfig', PruningMemorySearchConfig),
 ('OracleThread.get_summary', getattr(OracleThread, 'get_summary', None)),
 ('OracleAgentMemory.list_memories', getattr(OracleAgentMemory, 'list_memories', None)),
 ('OracleAgentMemory.list_images', getattr(OracleAgentMemory, 'list_images', None)),
 ('OracleAgentMemory.list_threads', getattr(OracleAgentMemory, 'list_threads', None)),
 ('OracleAgentMemory.list_users', getattr(OracleAgentMemory, 'list_users', None)),
 ('Llm.__init__', Llm.__init__),
 ('OracleDBEmbedder.__init__', OracleDBEmbedder.__init__),
 ('Embedder.__init__', Embedder.__init__),
 ('Reranker.__init__', Reranker.__init__),
 ('MemoryRetentionConfig', MemoryRetentionConfig),
 ('observability.register_event_listeners', obs.register_event_listeners),
 ('observability.EventListener', obs.EventListener),
]
for label, obj in targets:
    if obj is None: print(f"### {label}: NOT PRESENT"); continue
    show(label, obj, doclines=int(sys.argv[1]) if len(sys.argv) > 1 else 4)
for label, E in [('SchemaPolicy', SchemaPolicy), ('SearchStrategy', SearchStrategy), ('SearchIndexSyncMode', SearchIndexSyncMode),
    ('MemoryLinkExtractionMode', MemoryLinkExtractionMode), ('MemoryLinkType', MemoryLinkType),
    ('MemoryLinkOppositeType', MemoryLinkOppositeType), ('PruningEvaluationMode', PruningEvaluationMode),
    ('MemoryExtractionMode', MemoryExtractionMode), ('MemoryExtractionImageContext', MemoryExtractionImageContext),
    ('RecordStatus', RecordStatus)]:
    if isinstance(E, type) and issubclass(E, enum.Enum): members(label, E)
    else: print(f"### {label} (typing alias, not an enum): {E!r}; values = {typing.get_args(E)}")
print('### iscoroutinefunction(OracleAgentMemory.search) =', inspect.iscoroutinefunction(OracleAgentMemory.search),
      '; search_async =', inspect.iscoroutinefunction(OracleAgentMemory.search_async))
print('### OracleSearchResult fields:', [n for n in getattr(OracleSearchResult, '__dataclass_fields__', {}) ] or
      [n for n in dir(OracleSearchResult) if not n.startswith('_')])
