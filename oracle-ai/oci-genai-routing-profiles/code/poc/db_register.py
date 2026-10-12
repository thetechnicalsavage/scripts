#!/usr/bin/env python3
# v1.0 2026-10-10  Brief 14: the python-oracledb steps of ROUTE_LAB (PLAN.md v1.0 sections 7 and 10).
#   v1.0: first version. Written in the plan stage; NOT run yet. Every value reaches the database as a bind variable;
#         nothing secret is printed, logged or put on a command line.
"""Usage: db_register.py <action> --run-id <id> [options]

Actions (run as ROUTE_LAB on ORCLPDB1, from the demo VM's venv):
  credential  RL_CRED from the existing OCI API key (as brief 08's 02_create_credential.py): drop-and-recreate
  config      RL_CONFIG: compartment (from the env file), region us-phoenix-1, credential name
  prices      RL_PRICE from b14lib.budget.PRICES
  profiles    RL_PROFILE: every plan profile's alias and model; the OCID only for profiles the manifest has CREATED
  snapshot    MD_SNAPSHOT from <home>/results/s1/*.json (already redacted)
  results     RL_RESULT from <home>/results/s3, s4, s5, s9 (already redacted)
  allot       --step s6_plsql|s7_select_ai --calls N --usd X : move a block from the Python ledger to RL_BUDGET
  settle      --step ... : book RL_BUDGET's use back into the ledger and close the row

Connection: ~/oracle26ai/secrets/route_lab.env (600) with ROUTE_LAB_PWD and ROUTE_LAB_DSN (a host alias, never an
IP address). Database steps are refused inside the time windows of PLAN.md rule 6.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from b14lib import phoenix, windows  # noqa: E402
from b14lib.budget import PRICES, BudgetExceeded, Ledger  # noqa: E402
from b14lib.manifest import Manifest  # noqa: E402
from b14lib.ocisign import OCID_RE, SecretsError, read_env_keys, redact, sha16  # noqa: E402

LOG = logging.getLogger("b14.db")
SECRETS_DIR = Path("~/oracle26ai/secrets").expanduser()
DB_STEPS = ("s6_plsql", "s7_select_ai")


def key_body(path: Path) -> str:
    """PEM file -> the single-line key body DBMS_CLOUD expects (header and footer lines dropped)."""
    try:
        lines = [ln.strip() for ln in Path(path).read_text(encoding="ascii").splitlines()]
    except (OSError, UnicodeDecodeError) as exc:
        raise SecretsError(f"cannot read the key file ({type(exc).__name__})") from None
    body = "".join(ln for ln in lines if ln and not ln.startswith("-----"))
    if not body:
        raise SecretsError("no key material in the key file")
    return body


def connect(env_file: Path):
    import oracledb   # imported here so the offline actions and the tests do not need the driver
    v = read_env_keys(env_file, keys=("ROUTE_LAB_PWD", "ROUTE_LAB_DSN"))
    try:
        return oracledb.connect(user="ROUTE_LAB", password=v["ROUTE_LAB_PWD"], dsn=v["ROUTE_LAB_DSN"])
    except oracledb.Error as exc:
        raise SystemExit(f"cannot connect as ROUTE_LAB: {redact(str(exc).splitlines()[0])}") from None


# ---------------------------------------------------------------------------------------------- actions
def do_credential(con, a) -> None:
    import oracledb
    v = read_env_keys(a.env)
    cur = con.cursor()
    try:
        cur.execute("""
            declare
              l_n pls_integer;
            begin
              select count(*) into l_n from user_credentials where credential_name = 'RL_CRED';
              if l_n > 0 then
                dbms_cloud.drop_credential(credential_name => 'RL_CRED');
              end if;
              dbms_cloud.create_credential(credential_name => 'RL_CRED', user_ocid => :u, tenancy_ocid => :t,
                                           private_key => :k, fingerprint => :f);
            end;""", u=v["OCI_USER_OCID"], t=v["OCI_TENANCY_OCID"], k=key_body(a.key), f=v["OCI_FINGERPRINT"])
    except oracledb.Error as exc:  # the block drops before it creates, so say so plainly
        raise SystemExit("RL_CRED NOT created (any previous one was already dropped): "
                         + redact(str(exc).splitlines()[0])) from None
    cur.execute("select enabled from user_credentials where credential_name = 'RL_CRED'")
    row = cur.fetchone()
    if not row:
        raise SystemExit("RL_CRED was not created")
    LOG.info("credential RL_CRED created, enabled=%s", row[0])


def do_config(con, a) -> None:
    v = read_env_keys(a.env)
    phoenix.check_env_region(v["OCI_REGION"])
    cur = con.cursor()
    for name, value in (("COMPARTMENT_ID", v["OCI_COMPARTMENT_ID"]), ("REGION", phoenix.REGION),
                        ("CREDENTIAL", "RL_CRED")):
        cur.execute("merge into rl_config c using (select :n name, :v value from dual) s on (c.name = s.name) "
                    "when matched then update set c.value = s.value "
                    "when not matched then insert (name, value) values (s.name, s.value)", n=name, v=value)
    con.commit()
    LOG.info("RL_CONFIG: 3 rows")


def do_prices(con, a) -> None:
    cur = con.cursor()
    for model, (p_in, p_out) in PRICES.items():
        cur.execute("merge into rl_price p using (select :m model_id, :i usd_in_1m, :o usd_out_1m from dual) s "
                    "on (p.model_id = s.model_id) when matched then update set p.usd_in_1m = s.usd_in_1m, "
                    "p.usd_out_1m = s.usd_out_1m when not matched then insert values (s.model_id, s.usd_in_1m, "
                    "s.usd_out_1m)", m=model, i=p_in, o=p_out)
    con.commit()
    LOG.info("RL_PRICE: %d rows", len(PRICES))


def do_profiles(con, a) -> None:
    plan = json.loads((a.home / "results/s2/profile_plan.json").read_text(encoding="utf-8"))
    if plan.get("run_id") != a.run_id:
        raise SystemExit("the plan belongs to another run id")
    created = {e["alias"]: e["ocid"] for e in Manifest(a.manifest, a.run_id).entries()
               if e["run_id"] == a.run_id and e["state"] == "CREATED" and e["type"] == "generativeairoutingprofile"}
    cur = con.cursor()
    for p in plan["profiles"]:
        ocid = created.get(p["alias"])
        cur.execute("merge into rl_profile r using (select :a alias, :m model_id, :o ocid, :h ocid_sha16 from dual) s "
                    "on (r.alias = s.alias) when matched then update set r.model_id = s.model_id, r.ocid = s.ocid, "
                    "r.ocid_sha16 = s.ocid_sha16 when not matched then insert (alias, model_id, ocid, ocid_sha16) "
                    "values (s.alias, s.model_id, s.ocid, s.ocid_sha16)",
                    a=p["alias"], m=p["model"], o=ocid, h=sha16(ocid) if ocid else None)
        LOG.info("RL_PROFILE %s %s profile=%s", p["alias"], p["model"], "yes" if ocid else "no")
    con.commit()


def do_snapshot(con, a) -> None:
    rows = []
    for f in sorted((a.home / "results/s1").glob("*.json")):
        if f.name == "summary.json":
            continue
        body = f.read_text(encoding="utf-8")
        if OCID_RE.search(body):
            raise SystemExit(f"{f.name} holds an unredacted OCID; refusing to load it")
        rows.append([f.stem, json.loads(body).get("probed_at_utc", ""), body])
    cur = con.cursor()
    import oracledb
    cur.setinputsizes(None, None, oracledb.DB_TYPE_CLOB)
    for r in rows:
        cur.execute("merge into md_snapshot m using (select :1 probe, :2 taken_utc, :3 body from dual) s "
                    "on (m.probe = s.probe) when matched then update set m.taken_utc = s.taken_utc, m.body = s.body "
                    "when not matched then insert (probe, taken_utc, body) values (s.probe, s.taken_utc, s.body)", r)
    con.commit()
    LOG.info("MD_SNAPSHOT: %d probes", len(rows))


def do_results(con, a) -> None:
    import oracledb
    batches = []
    s3 = a.home / "results/s3/smoke.json"
    if s3.exists():
        batches += [("S3", i, r.get("alias"), r) for i, r in enumerate(json.loads(s3.read_text()), 1)]
    s4 = a.home / "results/s4/records.json"
    if s4.exists():
        batches += [("S4", i, r.get("alias"), r) for i, r in enumerate(json.loads(s4.read_text()), 1)]
    s5 = a.home / "results/s5/summary.json"
    if s5.exists():
        batches += [("S5", i, alias, dict(v, alias=alias))
                    for i, (alias, v) in enumerate(sorted(json.loads(s5.read_text()).items()), 1)]
    s9 = a.home / "results/s9/responses.json"
    if s9.exists():
        batches += [("S9", 1, "PROJ", json.loads(s9.read_text()))]
    cur = con.cursor()
    cur.setinputsizes(None, None, None, oracledb.DB_TYPE_CLOB)
    for kind, seq, alias, payload in batches:
        text = json.dumps(payload, default=str)
        if OCID_RE.search(text):
            raise SystemExit(f"a {kind} result holds an unredacted OCID; refusing to load it")
        cur.execute("merge into rl_result r using (select :1 kind, :2 seq, :3 alias, :4 payload from dual) s "
                    "on (r.kind = s.kind and r.seq = s.seq) when matched then update set r.alias = s.alias, "
                    "r.payload = s.payload when not matched then insert (kind, seq, alias, payload) "
                    "values (s.kind, s.seq, s.alias, s.payload)", [kind, seq, alias, text])
    con.commit()
    LOG.info("RL_RESULT: %d rows", len(batches))


def do_allot(con, a) -> None:
    if a.step not in DB_STEPS or not a.calls or not a.usd:
        raise SystemExit("allot needs --step s6_plsql|s7_select_ai, --calls and --usd")
    cur = con.cursor()
    cur.execute("select count(*) from rl_budget where step = :s and closed_utc is null", s=a.step)
    if cur.fetchone()[0]:
        raise SystemExit(f"{a.step} already has an open allotment; settle it first")
    import oracledb
    ledger = Ledger(a.home / "state/budget.json")
    n = ledger.allot_db(a.step, a.calls, a.usd, use_reserve=a.use_reserve)
    try:
        cur.execute("merge into rl_budget b using (select :s step from dual) x on (b.step = x.step) "
                    "when matched then update set allotment_no = :n, calls_allotted = :c, usd_allotted = :u, "
                    "calls_used = 0, usd_used_est = 0, opened_utc = sys_extract_utc(systimestamp), "
                    "closed_utc = null when not matched then insert (step, allotment_no, calls_allotted, "
                    "usd_allotted) values (:s, :n, :c, :u)", s=a.step, n=n, c=a.calls, u=a.usd)
        con.commit()
    except oracledb.Error as exc:
        con.rollback()
        ledger.settle_db(n, 0, 0.0)   # nothing reached the database: release the block in the ledger
        raise SystemExit(f"allotment not written to RL_BUDGET (released in the ledger): "
                         f"{redact(str(exc).splitlines()[0])}") from None
    LOG.info("allotment %d: %s %d calls, USD %.2f", n, a.step, a.calls, a.usd)


def do_settle(con, a) -> None:
    cur = con.cursor()
    cur.execute("select allotment_no, calls_used, usd_used_est from rl_budget "
                "where step = :s and closed_utc is null for update", s=a.step)
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"no open allotment for {a.step}")
    n, used, usd = row
    Ledger(a.home / "state/budget.json").settle_db(int(n), int(used), float(usd))
    cur.execute("update rl_budget set closed_utc = sys_extract_utc(systimestamp) where step = :s", s=a.step)
    con.commit()
    LOG.info("allotment %d settled: %d calls, USD %.4f (estimate)", n, used, usd)


ACTIONS = {"credential": do_credential, "config": do_config, "prices": do_prices, "profiles": do_profiles,
           "snapshot": do_snapshot, "results": do_results, "allot": do_allot, "settle": do_settle}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=sorted(ACTIONS))
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--home", type=Path, default=Path("~/b14-poc").expanduser())
    ap.add_argument("--env", type=Path, default=SECRETS_DIR / "oracle.env")
    ap.add_argument("--key", type=Path, default=SECRETS_DIR / "oci_key.pem")
    ap.add_argument("--db-env", type=Path, default=SECRETS_DIR / "route_lab.env")
    ap.add_argument("--manifest", type=Path, default=SECRETS_DIR / "b14_manifest.json")
    ap.add_argument("--step", choices=DB_STEPS)
    ap.add_argument("--calls", type=int)
    ap.add_argument("--usd", type=float)
    ap.add_argument("--use-reserve", default=None, help="draw from the reserve bucket, with this reason")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        windows.require_db_window()
        con = connect(a.db_env)
        try:
            ACTIONS[a.action](con, a)
        finally:
            con.close()
    except (windows.WindowClosed, BudgetExceeded, SecretsError, phoenix.RegionLockError) as exc:
        LOG.error("refused: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
