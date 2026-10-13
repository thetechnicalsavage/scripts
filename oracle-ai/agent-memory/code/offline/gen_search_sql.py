# v1.0 2026-10-10  Brief 12 (PLAN v0.3.4 section 8.1): render, without a database, the SQL and binds Agent Memory
#                  26.8.0 builds for one memory search under each strategy (VECTOR, KEYWORD after the store's own
#                  _escape_oracle_text_literal, HYBRID with its DBMS_HYBRID_VECTOR.SEARCH request), with the arguments
#                  the store passes for a memory-only search of one user (the SDK's four MEMORY_TYPES, source column
#                  content, include_invalid_results False, k 10, hybrid candidate limit k). APEX page 3 mirrors these.
#                  v1.0: first version. Run with the hash-installed venv: python gen_search_sql.py > <out>
import oracleagentmemory.core._db.sql as s
from oracleagentmemory.core.oracledbmemorystore import MEMORY_TYPES, _escape_oracle_text_literal

T = sorted(MEMORY_TYPES)
COMMON = dict(content_source_emb_column="content", include_message=False, message_table_name="RV_X_MESSAGE",
              include_memory=True, memory_table_name="RV_X_MEMORY", include_actor_profile=False,
              actor_profile_table_name="RV_X_ACTOR_PROFILE", actor_profile_source_emb_column="content",
              allowed_record_types=T, metadata_filter=None, thread_id=None, user_id="BIZ_0001", agent_id=None,
              exact_thread_match=False, exact_user_match=True, exact_agent_match=False, limit=10,
              include_document=False, document_table_name="RV_X_DOCUMENT", document_source_emb_column="description")
Q = "why was TKT-48213 refunded?"
print("MEMORY_TYPES:", T)
sql, b = s.build_keyword_record_chunks_search_sql_and_binds(query=_escape_oracle_text_literal(Q),
                                                             record_chunks_table="RV_KEY_RECORD_CHUNKS",
                                                             include_invalid_results=False, **COMMON)
print("=== KEYWORD\n" + sql + "\nbinds:", b)
sql, b = s.build_record_chunks_similarity_search_query(query_vector=[0.1, 0.2], record_chunks_table="RV_VEC_RECORD_CHUNKS",
                                                       include_invalid_results=False, **COMMON)
print("=== VECTOR\n" + sql + "\nbinds:", {k: ("<vector>" if k == "query_vector" else v) for k, v in b.items()})
sql, b = s.build_hybrid_record_chunks_search_sql_and_binds(query=Q, query_vector=None,
                                                           hybrid_index_name="RV_HYB_RECORD_CHUNKS_CONTENT_HVX_I",
                                                           record_chunks_table="RV_HYB_RECORD_CHUNKS",
                                                           include_invalid_results=False, hybrid_candidate_limit=10,
                                                           **COMMON)
print("=== HYBRID\n" + sql + "\nbinds:", b)
print("=== ESCAPE", _escape_oracle_text_literal("a}b{c"), _escape_oracle_text_literal("a\\b"))
