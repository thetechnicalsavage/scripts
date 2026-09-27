#!/usr/bin/env python3
# v1.1 - NL2SQL accuracy lab: create the DBMS_CLOUD credential Select AI uses to call
#        OCI Generative AI, from an existing OCI CLI config profile.
#        v1.1: connection always closed; a failed create after the drop says so plainly.
#
# Run as : any machine with python-oracledb and an OCI API key (~/.oci/config).
# Usage  : LAB_USER=NL2SQL_LAB LAB_DSN=host:1521/pdb LAB_PASSWORD=... \
#            python3 02_create_credential.py [--oci-profile DEFAULT] [--credential NL2SQL_LAB_CRED]
# Re-run : safe - an existing credential of the same name is dropped and recreated.
#
# Why a script and not SQL: DBMS_CLOUD.CREATE_CREDENTIAL needs the private key body, and
# pasting a key into a SQL*Plus prompt is error-prone. This reads the key file and passes
# every value as a bind variable. Nothing is printed, logged or written to disk.
import argparse
import configparser
import logging
import os
import sys

import oracledb

log = logging.getLogger("create_credential")


def key_body(path: str) -> str:
    """PEM file -> the single-line key body DBMS_CLOUD expects (no header/footer)."""
    with open(os.path.expanduser(path), encoding="ascii") as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("-----")]
    if not lines:
        raise SystemExit(f"no key material found in {path}")
    return "".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--oci-config", default="~/.oci/config")
    ap.add_argument("--oci-profile", default="DEFAULT")
    ap.add_argument("--credential", default="NL2SQL_LAB_CRED")
    a = ap.parse_args()

    cfg = configparser.ConfigParser()
    if not cfg.read(os.path.expanduser(a.oci_config)):
        raise SystemExit(f"cannot read {a.oci_config}")
    if a.oci_profile not in cfg and a.oci_profile != "DEFAULT":
        raise SystemExit(f"profile {a.oci_profile} not in {a.oci_config}")
    p = cfg[a.oci_profile]
    missing = [k for k in ("user", "tenancy", "fingerprint", "key_file") if not p.get(k)]
    if missing:
        raise SystemExit(f"profile {a.oci_profile} lacks: {', '.join(missing)}")

    for v in ("LAB_USER", "LAB_PASSWORD", "LAB_DSN"):
        if not os.environ.get(v):
            raise SystemExit(f"environment variable {v} is not set")

    try:
        con = oracledb.connect(user=os.environ["LAB_USER"], password=os.environ["LAB_PASSWORD"],
                               dsn=os.environ["LAB_DSN"])
    except oracledb.Error as e:
        raise SystemExit(f"cannot connect: {e}") from e

    try:
        cur = con.cursor()
        cur.execute("""
            declare
              l_n pls_integer;
            begin
              select count(*) into l_n from user_credentials where credential_name = upper(:cred);
              if l_n > 0 then
                dbms_cloud.drop_credential(credential_name => :cred);
              end if;
              dbms_cloud.create_credential(
                credential_name => :cred,
                user_ocid       => :user_ocid,
                tenancy_ocid    => :tenancy_ocid,
                private_key     => :private_key,
                fingerprint     => :fingerprint);
            end;""",
            cred=a.credential, user_ocid=p["user"], tenancy_ocid=p["tenancy"],
            private_key=key_body(p["key_file"]), fingerprint=p["fingerprint"])
        cur.execute("select credential_name, enabled from user_credentials where credential_name = :c",
                    c=a.credential)
        row = cur.fetchone()
    except oracledb.Error as e:
        # the block drops an existing credential before creating it: say so if the create failed
        log.error("credential %s NOT created (any previous one was already dropped): %s",
                  a.credential, str(e).splitlines()[0])
        return 1
    finally:
        con.close()
    if not row:
        log.error("credential %s was not created", a.credential)
        return 1
    log.info("credential %s created, enabled=%s", row[0], row[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
