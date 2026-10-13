# v1.0 2026-10-09  Offline: render the SDK's runtime (relational) and unused (SQL/PGQ) graph-expansion SQL for 1-2 hops
#                  with placeholder table names. No database connection; pure string building inside the wheel.
from oracleagentmemory.core._db import sql
common = dict(root_ids=["M1", "M2"], max_linked_results=3, thread_id=None, user_id="CUST_0001", agent_id=None,
              exact_thread_match=False, exact_user_match=True, exact_agent_match=False, record_types=None,
              metadata_filter=None)
for hops in (1, 2):
    q, b = sql.build_query_memory_graph_expansion_sql_and_binds("DEMO_MEMORY", "DEMO_MEMORY_LINK", num_hops=hops, **common)
    open(f"offline/relational_{hops}hop.sql", "w").write(q + "\n")
    p, pb = sql.build_memory_graph_expansion_sql_and_binds("DEMO_MEMORY", "DEMO_MEMORY_GRAPH", num_hops=hops, **common)
    open(f"offline/pgq_{hops}hop.sql", "w").write(p + "\n")
    print(hops, "relational chars", len(q), "UNION ALL", q.upper().count("UNION ALL"), "GRAPH_TABLE", q.upper().count("GRAPH_TABLE"),
          "| pgq chars", len(p), "GRAPH_TABLE", p.upper().count("GRAPH_TABLE"), "MATCH", p.upper().count("MATCH"),
          "| binds", sorted(b)[:8])
