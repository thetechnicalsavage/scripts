# v1.1 2026-10-12  Brief 12 step 4 (PLAN 6 row 4, 7.1 frozen and unchanged): read-only database helpers for the C1
#                  load read-back.
#                  v1.1: (Codex 31 r1, finding 1) coverage(): identity, not only counts: valid memories of the user
#                  with no chunk, and memory chunks whose source is not such a memory, must both be 0.
#                  v1.0: first version (phase 2, after S4's second attempt at 10-Oct 19:17Z stopped on
#                  its own precondition "RV_HYB: None of 400 chunks have an embedding": a HYBRID store's chunk table has
#                  no EMBEDDING column; its vectors live in the hybrid vector index, readable through the
#                  <index>$VECTORS view that Oracle creates with the index).
"""Where a HYBRID store keeps its vectors, and whether chunks and memories match one to one; read-only.

A hybrid vector index (USER_INDEXES: DOMAIN, ITYP_NAME CONTEXT_V2) on <ID>_RECORD_CHUNKS has a view <index>$VECTORS in
the index owner's schema with DOC_ROWID (the chunk table's rowid, lazy deletes excluded), DOC_CHUNK_ID, ...,
DOC_CHUNK_EMBEDDING (Oracle AI Vector Search User's Guide 26ai, "<index name>$VECTORS"). hybrid_vectors() reports, for
the store's one hybrid index, the view's columns, its row counts and how many of the store's memory chunks have no row
with an embedding there (the precondition, lib.c1run.check_loaded, needs 0). Only SELECTs; identifiers come from the
fixed store ids and from USER_INDEXES, checked against a strict pattern before they are quoted into SQL.
"""
from __future__ import annotations

import re

STORE_IDS = ("RV_HYB", "RV_VEC", "RV_KEY")              # lib.c1run.STORE_OF values (PLAN 7.1)
MEMORY_TYPES = ("fact", "guideline", "memory", "preference")
VIEW_NEEDS = ("DOC_ROWID", "DOC_CHUNK_EMBEDDING")
_IDENT = re.compile(r"^[A-Z][A-Z0-9_$#]{0,119}$")         # leaves room for the 8-character "$VECTORS" suffix


class C1DbError(ValueError):
    """A store id or an index name that this helper refuses to put into SQL."""


def hybrid_vectors(conn, store_id: str) -> dict:
    """Read-only. Returns {"chunk_table", "hybrid_indexes", "view", "view_columns", "vector_rows", "distinct_docs",
    "rows_with_embedding", "chunks", "chunks_without_vector"}, or the same dict with an "error" key and the counts
    absent when the index or its view is not what the docs describe (the caller turns that into a stop)."""
    if store_id not in STORE_IDS:
        raise C1DbError(f"store id {store_id} is not one of PLAN 7.1's three")
    chk_t = f"{store_id}_RECORD_CHUNKS"
    out: dict = {"chunk_table": chk_t}
    with conn.cursor() as cur:
        cur.execute("select index_name from user_indexes where table_name = :t and index_type = 'DOMAIN' "
                    "and ityp_name = 'CONTEXT_V2' order by index_name", t=chk_t)
        idx = [r[0] for r in cur.fetchall()]
        out["hybrid_indexes"] = idx
        if len(idx) != 1:
            out["error"] = f"expected exactly one hybrid vector index (CONTEXT_V2) on {chk_t}, found {len(idx)}"
            return out
        name = idx[0]
        if not _IDENT.fullmatch(name) or not name.startswith(f"{store_id}_"):
            raise C1DbError(f"unexpected hybrid index name {name!r} on {chk_t}")
        view = f"{name}$VECTORS"
        out["view"] = view
        cur.execute("select column_name from user_tab_columns where table_name = :v order by column_id", v=view)
        cols = [r[0] for r in cur.fetchall()]
        out["view_columns"] = cols
        missing = [c for c in VIEW_NEEDS if c not in cols]
        if missing:
            out["error"] = f"view {view} not found or without {missing}"
            return out
        cur.execute(f'select count(*), count(distinct doc_rowid), '
                    f'sum(case when doc_chunk_embedding is not null then 1 else 0 end) from "{view}"')
        n, nd, ne = cur.fetchone()
        out.update({"vector_rows": n, "distinct_docs": nd, "rows_with_embedding": ne or 0})
        types = ", ".join(f"'{t}'" for t in MEMORY_TYPES)
        cur.execute(f"select count(*), sum(case when not exists (select 1 from \"{view}\" v "
                    f"where v.doc_rowid = c.rowid and v.doc_chunk_embedding is not null) then 1 else 0 end) "
                    f"from {chk_t} c where c.source_record_type in ({types})")
        nc, nmiss = cur.fetchone()
        out.update({"chunks": nc, "chunks_without_vector": nmiss or 0})
    return out


def coverage(conn, store_id: str, user_id: str) -> dict:
    """Read-only identity check of one loaded store: valid memories of user_id that have no chunk, and memory chunks
    (source_record_type a memory type) whose source_id is not a valid memory of user_id. Both must be 0 (the counts
    alone can hide a missing chunk behind an orphan one)."""
    if store_id not in STORE_IDS:
        raise C1DbError(f"store id {store_id} is not one of PLAN 7.1's three")
    mem_t, chk_t = f"{store_id}_MEMORY", f"{store_id}_RECORD_CHUNKS"
    types = ", ".join(f"'{t}'" for t in MEMORY_TYPES)
    with conn.cursor() as cur:
        cur.execute(f"select count(*) from {mem_t} m where m.user_id = :u and m.status = 'valid' "
                    f"and not exists (select 1 from {chk_t} c where c.source_id = m.record_id "
                    f"and c.source_record_type in ({types}))", u=user_id)
        no_chunk = cur.fetchone()[0]
        cur.execute(f"select count(*) from {chk_t} c where c.source_record_type in ({types}) "
                    f"and not exists (select 1 from {mem_t} m where m.record_id = c.source_id "
                    f"and m.user_id = :u and m.status = 'valid')", u=user_id)
        orphan = cur.fetchone()[0]
    return {"memories_without_chunk": no_chunk, "chunks_without_memory": orphan}
