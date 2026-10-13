# v1.1 2026-10-09  v1.1: applies the store's own escaping step (_escape_oracle_text_literal, called by
#                  OracleDBMemoryStore._search_keyword at core/oracledbmemorystore.pyc:4698) before the builder, as
#                  the store does. v1.0: render the SDK's KEYWORD-strategy search SQL and binds for an identifier query,
#                  to see how a query reaches Oracle Text CONTAINS. No database connection.
import oracleagentmemory.core._db.sql as sql
from oracleagentmemory.core.oracledbmemorystore import _escape_oracle_text_literal
for q in ("what happened with TKT-48213 roaming pack?", "TKT-48213", "PLN-UNL-50 OR unlimited"):
    s, b = sql.build_keyword_record_chunks_search_sql_and_binds(
        query=_escape_oracle_text_literal(q), content_source_emb_column="content", record_chunks_table="RV_KEY_RECORD_CHUNKS",
        include_message=False, message_table_name="RV_KEY_MESSAGE", include_memory=True, memory_table_name="RV_KEY_MEMORY",
        include_actor_profile=False, actor_profile_table_name="RV_KEY_ACTOR_PROFILE", actor_profile_source_emb_column="content",
        allowed_record_types=["memory"], metadata_filter=None, thread_id=None, user_id="BIZ_0001", agent_id=None,
        exact_thread_match=False, exact_user_match=True, exact_agent_match=False, limit=10, include_invalid_results=False,
        include_document=False, document_table_name="RV_KEY_DOCUMENT", document_source_emb_column="description")
    print("QUERY:", q)
    i = s.upper().find("CONTAINS(")
    print("  SQL:", s[i:i + 50])
    print("  binds:", {k: v for k, v in b.items() if "user" not in k})
