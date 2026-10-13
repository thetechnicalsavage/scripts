# v1.8 2026-10-12  Brief 12 PLAN 11.4: the manifest's ownership rules (pure functions, no database).
#                  v1.8: (stores.py v1.10, PLAN v0.3.6) the VECTOR$DR$<ID>_ prefix: a hybrid index's IVF centroid
#                  tables and their system-named indexes and LOBs belong to the store (the eleven objects S4's
#                  second attempt left unlisted on 10-Oct); look-alikes and other stores' names stay unowned.
#                  v1.7: (stores.py v1.9) a sweep accepts POC_RESULT only when 84 recorded it under POC, never merges a
#                  POC row, and reports an unrecorded POC_RESULT as unlisted.
#                  v1.6: (APEX stage, stores.py v1.8) POC_RESULT and its key are owned by 'POC'; a look-alike name
#                  (another table called POC_RESULTS, or POC_RESULT as a view) stays unlisted.
#                  v1.5: disable_purge_jobs waits for a RUNNING job and retries on ORA-27478, bounded; other errors
#                  propagate (S2's first attempt).
#                  v1.4: (Codex r4 of step 1C) a manifest row under another store id is no proof (preopen_check) and
#                  is reported as a conflict (manifest_conflicts).
#                  v1.3: (Codex r2 of step 3, finding 1) sweep() issues no DDL and refuses without the manifest.
#                  v1.2: (Codex r1 of step 1C, finding 1) store_objects() is read-only: no DDL, and a missing manifest
#                  raises ManifestMissing. v1.1: preopen_check closure test (fake cursor). v1.0: first version (day 1).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
from lib import stores  # noqa: E402


def test_longest_store_id_wins():
    assert stores.owner_store("DEMO_MEMORY") == "DEMO"
    assert stores.owner_store("DEMO_AUTO_MEMORY") == "DEMO_AUTO"
    assert stores.owner_store("DR$RV_KEY_RECORD_CHUNKS_I$I") == "RV_KEY"
    assert stores.owner_store("VECTOR$DEMO_RECORD_CHUNKS_HNSW$1") == "DEMO"
    assert stores.owner_store("DEMOX_TABLE") is None
    assert stores.owner_store("OTHER_TABLE") is None



HYB_IVF = "VECTOR$DR$RV_HYB_RECORD_CHUNKS_CONTENT_HVX_I$VI$228107_228120_0$IVF_FLAT_CENTROIDS"
HYB_IVF_P = "VECTOR$DR$RV_HYB_RECORD_CHUNKS_CONTENT_HVX_I$VI$228107_228120_0$IVF_FLAT_CENTROID_PARTITIONS"
# the eleven objects the 10-Oct 19:17Z sweep reported unlisted (results/s4/second_attempt_1917Z/manifest_sweep.json)
HYB_UNLISTED_1917Z = [(HYB_IVF, "TABLE"), ("SYS_C0018836", "INDEX"), ("SYS_IL0000228121C00002$$", "INDEX"),
                      ("SYS_LOB0000228121C00002$$", "LOB"), (HYB_IVF_P, "TABLE PARTITION"), (HYB_IVF_P, "TABLE"),
                      ("SYS_C0018838", "INDEX"), ("SYS_IL0000228125C00003$$", "INDEX PARTITION"),
                      ("SYS_IL0000228125C00003$$", "INDEX"), ("SYS_LOB0000228125C00003$$", "LOB PARTITION"),
                      ("SYS_LOB0000228125C00003$$", "LOB")]
HYB_INDEXES = [("SYS_C0018836", HYB_IVF), ("SYS_IL0000228121C00002$$", HYB_IVF), ("SYS_C0018838", HYB_IVF_P),
               ("SYS_IL0000228125C00003$$", HYB_IVF_P)]
HYB_LOBS = [("SYS_LOB0000228121C00002$$", "SYS_IL0000228121C00002$$", HYB_IVF),
            ("SYS_LOB0000228125C00003$$", "SYS_IL0000228125C00003$$", HYB_IVF_P)]


def test_hybrid_index_vector_internals_belong_to_their_store():
    assert stores.owner_store(HYB_IVF) == "RV_HYB"
    assert stores.owner_store(HYB_IVF_P) == "RV_HYB"
    assert stores.owner_store("VECTOR$DR$DEMO_AUTO_RECORD_CHUNKS_CONTENT_HVX_I$VI$1_2_0$IVF_FLAT_CENTROIDS") == "DEMO_AUTO"
    assert stores.owner_store("VECTOR$DR$DEMO_RECORD_CHUNKS_CONTENT_HVX_I$VI$1_2_0$IVF_FLAT_CENTROIDS") == "DEMO"
    assert stores.owner_store("VECTOR$DR$RV_HYBX_TABLE") is None          # not the store id followed by "_"
    assert stores.owner_store("VECTOR$DR$OTHER_TABLE$VI$1_2_0$IVF_FLAT_CENTROIDS") is None
    assert stores.owner_store("VECTOR$DRRV_HYB_TABLE") is None


def test_classify_owns_the_hybrid_ivf_tables_and_their_system_objects():
    records, unlisted = stores.classify(HYB_UNLISTED_1917Z, HYB_INDEXES, HYB_LOBS, [])
    assert unlisted == []
    assert sorted(records) == sorted((n, t, "RV_HYB") for n, t in HYB_UNLISTED_1917Z)


def test_without_the_parent_table_rows_system_objects_stay_unlisted():
    # an IVF table's system-named LOB is owned only through USER_INDEXES / USER_LOBS, never by its name
    records, unlisted = stores.classify(HYB_UNLISTED_1917Z, [], [], [])
    assert {n for n, _, _ in records} == {HYB_IVF, HYB_IVF_P}
    assert set(unlisted) == {(n, t) for n, t in HYB_UNLISTED_1917Z if n.startswith("SYS_")}

def test_classify_follows_tables_to_system_named_objects():
    objects = [("DEMO_MEMORY", "TABLE"), ("SYS_C0012345", "INDEX"), ("SYS_LOB0000123C00008$$", "LOB"),
               ("SYS_IL0000123C00008$$", "INDEX"), ("ISEQ$$_99", "SEQUENCE"), ("DEMO_MEMORY_GRAPH", "PROPERTY GRAPH"),
               ("AGENT_MEMORY_STORES", "TABLE"), ("SYS_C0099", "INDEX"), ("DBMS_AGENT_MEMORY_STORE", "PACKAGE"),
               ("DBMS_AGENT_MEMORY_STORE", "PACKAGE BODY"), ("POC_MANIFEST", "TABLE"), ("POC_MANIFEST_PK", "INDEX")]
    indexes = [("SYS_C0012345", "DEMO_MEMORY"), ("SYS_IL0000123C00008$$", "DEMO_MEMORY"),
               ("SYS_C0099", "AGENT_MEMORY_STORES"), ("POC_MANIFEST_PK", "POC_MANIFEST")]
    lobs = [("SYS_LOB0000123C00008$$", "SYS_IL0000123C00008$$", "DEMO_MEMORY")]
    idents = [("DEMO_MEMORY", "ISEQ$$_99")]
    records, unlisted = stores.classify(objects, indexes, lobs, idents)
    got = {(n, t): s for n, t, s in records}
    assert got[("SYS_C0012345", "INDEX")] == "DEMO"
    assert got[("SYS_LOB0000123C00008$$", "LOB")] == "DEMO"
    assert got[("ISEQ$$_99", "SEQUENCE")] == "DEMO"
    assert got[("SYS_C0099", "INDEX")] == "SDK"
    assert got[("DBMS_AGENT_MEMORY_STORE", "PACKAGE BODY")] == "SDK"
    assert ("POC_MANIFEST", "TABLE") not in got and unlisted == []


def test_results_table_and_its_key_belong_to_poc():
    objects = [("POC_RESULT", "TABLE"), ("POC_RESULT_PK", "INDEX")]
    indexes = [("POC_RESULT_PK", "POC_RESULT")]
    records, unlisted = stores.classify(objects, indexes, [], [])
    assert sorted(records) == [("POC_RESULT", "TABLE", "POC"), ("POC_RESULT_PK", "INDEX", "POC")]
    assert unlisted == []


def test_results_table_look_alikes_stay_unlisted():
    objects = [("POC_RESULTS", "TABLE"), ("POC_RESULT", "VIEW"), ("SYS_C0888", "INDEX")]
    indexes = [("SYS_C0888", "POC_RESULTS")]
    records, unlisted = stores.classify(objects, indexes, [], [])
    assert records == []
    assert set(unlisted) == {("POC_RESULTS", "TABLE"), ("POC_RESULT", "VIEW"), ("SYS_C0888", "INDEX")}


def test_foreign_object_in_window_is_unlisted_not_recorded():
    objects = [("DEMO_MEMORY", "TABLE"), ("SOMEONE_ELSES_TABLE", "TABLE"), ("SYS_C0777", "INDEX")]
    indexes = [("SYS_C0777", "SOMEONE_ELSES_TABLE")]
    records, unlisted = stores.classify(objects, indexes, [], [])
    assert [r[0] for r in records] == ["DEMO_MEMORY"]
    assert set(unlisted) == {("SOMEONE_ELSES_TABLE", "TABLE"), ("SYS_C0777", "INDEX")}


class _Cur:
    def __init__(self, data):
        self.data, self.q = data, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a, **k):
        self.q = " ".join(sql.split()).lower()

    def fetchall(self):
        for key, rows in self.data.items():
            if key in self.q:
                return rows
        return []


class _Conn:
    def __init__(self, data):
        self.data = data

    def cursor(self):
        return _Cur(self.data)


def test_preopen_check_uses_the_full_closure():
    data = {
        "from user_objects": [("DEMO_MEMORY", "TABLE"), ("SYS_C0042", "INDEX"), ("DEMO_AUTO_MEMORY", "TABLE")],
        "from poc_manifest": [("DEMO_MEMORY", "TABLE", "DEMO")],
        "from user_indexes": [("SYS_C0042", "DEMO_MEMORY")],
        "from user_lobs": [],
        "from user_tab_identity_cols": [],
    }
    assert stores.preopen_check(_Conn(data), "DEMO") == [("SYS_C0042", "INDEX")]
    data["from poc_manifest"].append(("SYS_C0042", "INDEX", "DEMO"))
    assert stores.preopen_check(_Conn(data), "DEMO") == []
    data["from poc_manifest"][-1] = ("SYS_C0042", "INDEX", "OPS")      # listed, but under another store id
    assert stores.preopen_check(_Conn(data), "DEMO") == [("SYS_C0042", "INDEX")]


class _RecCur(_Cur):
    def __init__(self, data, log):
        super().__init__(data)
        self.log = log

    def execute(self, sql, *a, **k):
        super().execute(sql, *a, **k)
        self.log.append(self.q)


class _RecConn:
    def __init__(self, data):
        self.data, self.log = data, []

    def cursor(self):
        return _RecCur(self.data, self.log)


def _store_data(with_manifest=True):
    self_rows = [("POC_MANIFEST", "TABLE"), ("POC_MANIFEST_PK", "INDEX")] if with_manifest else []
    return {   # most specific key first: the fake matches the first key contained in the query
        "where (object_name = 'poc_manifest'": self_rows,
        "from user_objects": [("DEMO_THREAD", "TABLE", 1, "VALID", "2026-10-10 04:16:20"),
                              ("SYS_C0042", "INDEX", 2, "VALID", "2026-10-10 04:16:20"),
                              ("DEMO_AUTO_THREAD", "TABLE", 3, "VALID", "2026-10-10 04:16:20")],
        "from poc_manifest": [("DEMO_THREAD", "TABLE", "DEMO"), ("SYS_C0042", "INDEX", "DEMO")],
        "from user_indexes": [("SYS_C0042", "DEMO_THREAD")],
        "from user_lobs": [],
        "from user_tab_identity_cols": [],
    }


def test_store_objects_is_read_only_and_uses_the_closure():
    conn = _RecConn(_store_data())
    got = stores.store_objects(conn, "DEMO")
    assert [(o["object_name"], o["manifest_store_id"]) for o in got] == [("DEMO_THREAD", "DEMO"), ("SYS_C0042", "DEMO")]
    assert all(q.startswith("select") for q in conn.log)


def test_store_objects_refuses_without_the_manifest_and_creates_nothing():
    conn = _RecConn(_store_data(with_manifest=False))
    try:
        stores.store_objects(conn, "DEMO")
    except stores.ManifestMissing:
        pass
    else:
        raise AssertionError("ManifestMissing expected")
    assert all(q.startswith("select") for q in conn.log)


class _TxConn(_RecConn):
    def commit(self):
        self.log.append("commit")


def test_sweep_issues_no_ddl_and_needs_the_manifest():
    data = {"created >= :t0": []}         # nothing appeared in the window (first: the fake matches in order)
    data.update(_store_data())
    conn = _TxConn(data)
    res = stores.sweep(conn, "2026-10-10 08:00:00")
    assert res["recorded"] == 0 and res["unlisted"] == []
    assert not any(q.startswith(("create", "merge", "drop", "alter")) for q in conn.log)
    conn = _TxConn(_store_data(with_manifest=False))
    try:
        stores.sweep(conn, "2026-10-10 08:00:00")
    except stores.ManifestMissing:
        pass
    else:
        raise AssertionError("ManifestMissing expected")
    assert not any(q.startswith("create") for q in conn.log)


def test_manifest_conflicts_flags_a_row_under_another_store_id():
    data = _store_data()
    data["from user_objects"] = [r[:2] for r in data["from user_objects"]]     # this query selects two columns
    assert stores.manifest_conflicts(_RecConn(data)) == []
    data["from poc_manifest"] = [("DEMO_THREAD", "TABLE", "DEMO"), ("SYS_C0042", "INDEX", "DEMO_AUTO")]
    assert stores.manifest_conflicts(_RecConn(data)) == [("SYS_C0042", "INDEX", "DEMO", "DEMO_AUTO")]


class _OraErr:
    def __init__(self, code):
        self.code = code
        self.full_code = f"ORA-{code:05d}"


class _JobCur:
    def __init__(self, st):
        self.st, self.rows = st, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a, **k):
        q = " ".join(sql.split()).lower()
        if "select state from user_scheduler_jobs" in q:
            self.rows = [(self.st["state"].pop(0) if len(self.st["state"]) > 1 else self.st["state"][0],)]
        else:   # purge_jobs()
            self.rows = [(n, self.st["enabled"][n], "SCHEDULED", 1) for n in sorted(self.st["enabled"])]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def callproc(self, name, args):
        self.st["calls"].append(args[0])
        if self.st["raise"]:
            err = self.st["raise"].pop(0)
            if err is not None:
                raise Exception(_OraErr(err))
        self.st["enabled"][args[0]] = "FALSE"


class _JobConn:
    def __init__(self, st):
        self.st = st

    def cursor(self):
        return _JobCur(self.st)


def _job_state_fixture(states, raises):
    names = stores.sdk_job_names("DEMO_AUTO")
    return {"enabled": {n: "TRUE" for n in names}, "state": list(states), "raise": list(raises), "calls": []}


def test_disable_waits_for_a_running_job_and_retries_on_ora_27478():
    slept = []
    st = _job_state_fixture(["RUNNING", "RUNNING", "SCHEDULED"], [27478, None, None])
    res = stores.disable_purge_jobs(_JobConn(st), "DEMO_AUTO", sleep=slept.append)
    assert all(r["enabled_after"] == "FALSE" for r in res)
    assert sum(slept) == 6 and res[0]["seconds_waited_for_running_job"] == 6


def test_disable_gives_up_after_the_bound_and_never_swallows_other_errors():
    st = _job_state_fixture(["RUNNING"], [])
    try:
        stores.disable_purge_jobs(_JobConn(st), "DEMO_AUTO", wait_s=4, sleep=lambda s: None)
    except RuntimeError as exc:
        assert "still RUNNING" in str(exc) and st["calls"] == []
    else:
        raise AssertionError("RuntimeError expected")
    st = _job_state_fixture(["SCHEDULED"], [1031])
    try:
        stores.disable_purge_jobs(_JobConn(st), "DEMO_AUTO", sleep=lambda s: None)
    except Exception as exc:
        assert getattr(exc.args[0], "code", None) == 1031
    else:
        raise AssertionError("ORA-01031 must propagate")


def test_job_running_error_is_recognised_by_full_code_too():
    import oracledb
    from oracledb import errors as oerr
    err = oracledb.DatabaseError(oerr._Error("ORA-27478: job is running"))
    assert stores._is_job_running_error(err)
    assert not stores._is_job_running_error(Exception(_OraErr(1031)))


def _poc_window_data(manifest_rows):
    data = {"created >= :t0": [("POC_RESULT", "TABLE"), ("POC_RESULT_PK", "INDEX")]}
    data.update(_store_data())
    data["from user_indexes"] = [("POC_RESULT_PK", "POC_RESULT")]
    data["from poc_manifest"] = manifest_rows
    return data


def test_sweep_accepts_poc_result_only_when_84_recorded_it():
    conn = _TxConn(_poc_window_data([("POC_RESULT", "TABLE", "POC"), ("POC_RESULT_PK", "INDEX", "POC")]))
    res = stores.sweep(conn, "2026-10-10 14:00:00")
    assert res["unlisted"] == [] and res["conflicts"] == [] and res["poc_checked"] == 2 and res["recorded"] == 0
    assert not any(q.startswith("merge") for q in conn.log)


def test_sweep_reports_an_unrecorded_poc_result_and_merges_nothing():
    conn = _TxConn(_poc_window_data([]))
    res = stores.sweep(conn, "2026-10-10 14:00:00")
    assert sorted(res["unlisted"]) == [("POC_RESULT", "TABLE"), ("POC_RESULT_PK", "INDEX")]
    assert not any(q.startswith("merge") for q in conn.log)
    conn = _TxConn(_poc_window_data([("POC_RESULT", "TABLE", "DEMO"), ("POC_RESULT_PK", "INDEX", "POC")]))
    res = stores.sweep(conn, "2026-10-10 14:00:00")
    assert res["conflicts"] == [("POC_RESULT", "TABLE", "POC", "DEMO")]
