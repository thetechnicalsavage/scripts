# v1.1 2026-10-12  Brief 12 tests for py/lib/c1db.py (step 4 read-back of a HYBRID store's vectors): read-only SQL
#                  through a fake connection; happy path, missing index or view, a refused store id or index name.
#                  v1.1: coverage() (Codex 31 r1, finding 1): both anti-joins, bound user, refused store id.
#                  v1.0: first version (phase 2).
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
from lib import c1db  # noqa: E402

VIEW = "RV_HYB_RECORD_CHUNKS_CONTENT_HVX_I$VECTORS"
COLS = ["DOC_ROWID", "DOC_CHUNK_ID", "DOC_CHUNK_COUNT", "DOC_CHUNK_OFFSET", "DOC_CHUNK_LENGTH", "DOC_CHUNK_TEXT",
        "DOC_CHUNK_EMBEDDING"]


class _Cur:
    def __init__(self, conn):
        self.conn, self.q = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a, **k):
        self.q = " ".join(sql.split()).lower()
        self.conn.sql.append((self.q, k))

    def fetchall(self):
        if "from user_indexes" in self.q:
            return [(n,) for n in self.conn.indexes]
        if "from user_tab_columns" in self.q:
            return [(c,) for c in self.conn.cols]
        raise AssertionError(self.q)

    def fetchone(self):
        if self.q.startswith("select count(*) from rv_hyb_memory m where"):
            return (self.conn.no_chunk,)
        if self.q.startswith("select count(*) from rv_hyb_record_chunks c where") and "_memory m" in self.q:
            return (self.conn.orphan,)
        if "count(distinct doc_rowid)" in self.q:
            return self.conn.view_counts
        if "not exists" in self.q:
            return self.conn.missing
        raise AssertionError(self.q)


class _Conn:
    def __init__(self, indexes=("RV_HYB_RECORD_CHUNKS_CONTENT_HVX_I",), cols=COLS, view_counts=(400, 400, 400),
                 missing=(400, 0)):
        self.indexes, self.cols, self.view_counts, self.missing, self.sql = list(indexes), cols, view_counts, missing, []
        self.no_chunk, self.orphan = 0, 0

    def cursor(self):
        return _Cur(self)


def test_happy_path_reads_the_view_and_only_selects():
    c = _Conn()
    out = c1db.hybrid_vectors(c, "RV_HYB")
    assert out["view"] == VIEW and out["view_columns"] == COLS
    assert (out["vector_rows"], out["distinct_docs"], out["rows_with_embedding"]) == (400, 400, 400)
    assert (out["chunks"], out["chunks_without_vector"]) == (400, 0) and "error" not in out
    assert all(q.startswith("select ") for q, _ in c.sql)
    assert any(f'from "{VIEW.lower()}"' in q for q, _ in c.sql)
    assert any("from rv_hyb_record_chunks c where c.source_record_type in" in q for q, _ in c.sql)


def test_chunks_missing_their_vector_are_counted():
    out = c1db.hybrid_vectors(_Conn(view_counts=(397, 397, 397), missing=(400, 3)), "RV_HYB")
    assert out["chunks_without_vector"] == 3


def test_no_hybrid_index_or_two_is_an_error_not_a_query():
    for idx in ((), ("RV_HYB_A_I", "RV_HYB_B_I")):
        c = _Conn(indexes=idx)
        out = c1db.hybrid_vectors(c, "RV_HYB")
        assert "error" in out and "chunks_without_vector" not in out and len(c.sql) == 1


def test_a_view_without_the_documented_columns_is_an_error():
    out = c1db.hybrid_vectors(_Conn(cols=[]), "RV_HYB")
    assert "not found" in out["error"]
    out = c1db.hybrid_vectors(_Conn(cols=["DOC_ROWID", "DOC_CHUNK_TEXT"]), "RV_HYB")
    assert "DOC_CHUNK_EMBEDDING" in out["error"]


def test_refuses_other_store_ids_and_odd_index_names():
    with pytest.raises(c1db.C1DbError):
        c1db.hybrid_vectors(_Conn(), "DEMO")
    for bad in ('RV_HYB_X"; drop table t; --', "OTHER_RECORD_CHUNKS_HVX_I", "rv_hyb_lower_i"):
        with pytest.raises(c1db.C1DbError):
            c1db.hybrid_vectors(_Conn(indexes=(bad,)), "RV_HYB")


def test_coverage_runs_both_anti_joins_with_the_user_bound():
    c = _Conn()
    c.no_chunk, c.orphan = 1, 2
    assert c1db.coverage(c, "RV_HYB", "BIZ_0001") == {"memories_without_chunk": 1, "chunks_without_memory": 2}
    assert len(c.sql) == 2 and all(q.startswith("select count(*)") for q, _ in c.sql)
    assert all(k == {"u": "BIZ_0001"} for _, k in c.sql)
    assert "c.source_id = m.record_id" in c.sql[0][0] and "m.record_id = c.source_id" in c.sql[1][0]
    with pytest.raises(c1db.C1DbError):
        c1db.coverage(_Conn(), "DEMO", "BIZ_0001")
