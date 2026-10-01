#!/usr/bin/env python3
# v1.3 - RAG tuning lab (brief 09): create the DBMS_CLOUD credential RAG_LAB_OCI_CRED that the
#        lab's Select AI profiles use to call OCI Generative AI, from an OCI CLI config profile.
#        v1.3: Codex adversarial review (B2): the password comes from RAG_LAB_PWD_FILE first (the 0600
#              file run_all.sh writes per run: a regular file of this user's, closed to others, one
#              line), then RAG_LAB_PWD, then a hidden prompt; both variables leave the environment
#              once read. A bad file stops the script and the message shows neither path nor value.
#        v1.2: Codex re-review: the existing-credential check runs before the OCI config and key
#              are read, so a re-run needs neither.
#        v1.1: Codex review: an existing credential is kept unless --replace is given (drop and
#              recreate could leave none if the create failed).
#        v1.0: copied from brief 08 v1.1 (binds every value, prints none); lab user fixed to
#              RAG_LAB; password from RAG_LAB_PWD or a hidden prompt, never an argument;
#              refuses passphrase-protected keys; checks it is connected as RAG_LAB.
#
# Run as : the VM OS user, with python-oracledb (thin mode) and an OCI API key in ~/.oci/config.
# Usage  : RAG_LAB_DSN=localhost:1521/orclpdb1 python3 02_create_credential.py \
#            [--oci-config ~/.oci/config] [--oci-profile DEFAULT] [--credential RAG_LAB_OCI_CRED] [--replace]
#          The password comes from the file named by RAG_LAB_PWD_FILE (run_all.sh passes its
#          per-run 0600 file), else from RAG_LAB_PWD, else, when a terminal is attached, from a
#          hidden prompt. There is deliberately no password option.
# Re-run : safe - an existing credential of the same name is kept and reported. --replace drops
#          and recreates it (a new API key, say); if that create fails, the log says the old one
#          is gone.
#
# Why a script and not SQL: DBMS_CLOUD.CREATE_CREDENTIAL needs the private key body, and
# pasting a key into a SQL*Plus prompt is error-prone. This reads the key file and passes
# every value as a bind variable. Nothing is printed, logged or written to disk.
import argparse
import configparser
import getpass
import logging
import os
import re
import stat
import sys

log = logging.getLogger("create_credential")

LAB_USER = "RAG_LAB"
DEFAULT_DSN = "localhost:1521/orclpdb1"
CRED_RE = re.compile(r"^RAG_LAB_[A-Z0-9_]{1,100}$")
PWD_FILE_VAR = "RAG_LAB_PWD_FILE"


def key_body(path: str) -> str:
    """PEM file -> the single-line key body DBMS_CLOUD expects (no header/footer)."""
    try:
        with open(os.path.expanduser(path), encoding="ascii") as f:
            raw = f.read()
    except OSError as e:
        raise SystemExit(f"cannot read the key file named in the OCI profile: {e.strerror}") from e
    if "ENCRYPTED" in raw:
        raise SystemExit("the private key is passphrase-protected; DBMS_CLOUD needs an unencrypted API key")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.startswith("-----")]
    if not lines:
        raise SystemExit("no key material found in the key file named in the OCI profile")
    return "".join(lines)


def read_password_file(path: str) -> str:
    """The password held in <path>: a regular file (no symlink) owned by this OS user and closed to
    group and others, one line (as eval/eval_retrieval.py reads it). run_all.sh writes one per run
    (0600) and shreds it on exit. Error messages never show the path or the content."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        raise SystemExit(f"{PWD_FILE_VAR}: cannot open the password file ({e.strerror})") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SystemExit(f"{PWD_FILE_VAR}: not a regular file")
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SystemExit(f"{PWD_FILE_VAR}: the file must belong to this user and be closed to others (chmod 600)")
        data = b""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 4096:
                raise SystemExit(f"{PWD_FILE_VAR}: the file is too large for a password")
    finally:
        os.close(fd)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"{PWD_FILE_VAR}: the file is not UTF-8") from None
    if text.endswith("\n"):
        text = text[:-1]
    if text.endswith("\r"):
        text = text[:-1]
    if not text or "\n" in text or "\r" in text:
        raise SystemExit(f"{PWD_FILE_VAR}: the file must hold one non-empty line")
    return text


def lab_password(env=os.environ, stdin=sys.stdin, prompt=getpass.getpass) -> str:
    """RAG_LAB_PWD_FILE first, then RAG_LAB_PWD, then a hidden prompt when a terminal is attached.
    Both variables leave `env` here; a bad file stops (it never falls back to the variable)."""
    path = env.pop(PWD_FILE_VAR, None)
    pwd = env.pop("RAG_LAB_PWD", None)
    if path:
        return read_password_file(path)
    if pwd:
        return pwd
    if not stdin.isatty():
        raise SystemExit("neither RAG_LAB_PWD_FILE nor RAG_LAB_PWD is set, and no terminal is attached "
                         "for a hidden prompt")
    pwd = prompt("RAG_LAB password: ")
    if not pwd:
        raise SystemExit("no password given")
    return pwd


def oci_profile(config_path: str, profile: str) -> dict:
    cfg = configparser.ConfigParser()
    if not cfg.read(os.path.expanduser(config_path)):
        raise SystemExit(f"cannot read {config_path}")
    if profile not in cfg and profile != "DEFAULT":
        raise SystemExit(f"profile {profile} not in {config_path}")
    p = cfg[profile]
    missing = [k for k in ("user", "tenancy", "fingerprint", "key_file") if not p.get(k)]
    if missing:
        raise SystemExit(f"profile {profile} lacks: {', '.join(missing)}")
    if p.get("pass_phrase"):
        raise SystemExit(f"profile {profile} uses a key passphrase; DBMS_CLOUD needs an unencrypted API key")
    return {k: p[k] for k in ("user", "tenancy", "fingerprint", "key_file")}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="create RAG_LAB's OCI credential (no secret is printed)")
    ap.add_argument("--oci-config", default="~/.oci/config")
    ap.add_argument("--oci-profile", default="DEFAULT")
    ap.add_argument("--credential", default="RAG_LAB_OCI_CRED")
    ap.add_argument("--replace", action="store_true", help="drop and recreate an existing credential")
    a = ap.parse_args(argv)
    cred = a.credential.upper()
    if not CRED_RE.match(cred):
        raise SystemExit("--credential must look like RAG_LAB_<NAME>")

    dsn = os.environ.get("RAG_LAB_DSN") or DEFAULT_DSN
    pwd = lab_password()

    import oracledb   # imported here so the helpers above are testable without the driver

    try:
        con = oracledb.connect(user=LAB_USER, password=pwd, dsn=dsn)
    except oracledb.Error as e:
        raise SystemExit(f"cannot connect as {LAB_USER}: {str(e).splitlines()[0]}") from e
    finally:
        pwd = None   # noqa: F841 - drop the reference as early as possible

    row = None
    try:
        cur = con.cursor()
        cur.execute("select sys_context('userenv', 'session_user') from dual")
        who = cur.fetchone()[0]
        if who != LAB_USER:
            log.error("connected as %s, expected %s; nothing changed", who, LAB_USER)
            return 1
        cur.execute("select count(*) from user_credentials where credential_name = :c", c=cred)
        if cur.fetchone()[0] > 0 and not a.replace:
            log.info("credential %s already exists; kept (use --replace to recreate it)", cred)
            return 0
        prof = oci_profile(a.oci_config, a.oci_profile)
        body = key_body(prof["key_file"])
        cur.execute("""
            declare
              l_n pls_integer;
            begin
              select count(*) into l_n from user_credentials where credential_name = :cred;
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
            cred=cred, user_ocid=prof["user"], tenancy_ocid=prof["tenancy"],
            private_key=body, fingerprint=prof["fingerprint"])
        cur.execute("select credential_name, enabled from user_credentials where credential_name = :c", c=cred)
        row = cur.fetchone()
    except oracledb.Error as e:
        # the block drops an existing credential before creating it: say so if the create failed
        log.error("credential %s NOT created (any previous one was already dropped): %s",
                  cred, str(e).splitlines()[0])
        return 1
    finally:
        con.close()
    if not row:
        log.error("credential %s was not created", cred)
        return 1
    log.info("credential %s created, enabled=%s", row[0], row[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
