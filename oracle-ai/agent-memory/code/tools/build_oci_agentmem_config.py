#!/usr/bin/env python3
# v1.3 2026-10-10  Brief 12 D4 (PLAN 10.4, 5.1 (f) (g)): build ~/oracle26ai/secrets/oci_agentmem/config for LiteLLM's
#                  oci/ route from the operator's EXISTING OCI API user and key on the demo VM (the ones the Select AI
#                  profiles of other schemas use).
#                  v1.3: (Codex r3, ISSUES 2) every read of an existing config is a regular-file check plus a
#                  guarded read: an odd or vanishing file gives a clean refusal (exit 2/4), never a traceback.
#                  v1.2: (Codex re-review r2) an existing directory is used only if it holds exactly one config
#                  carrying this tool's header; a leftover .config.tmp stops the run instead of being removed.
#                  v1.1: (Codex adversarial review r1, findings 7 and 8) the env file is streamed and a value is kept
#                  only for an allow-listed key; --out must be a directory named oci_agentmem; an existing directory
#                  is used only if it is ours (mode 700, nothing but our config), never re-permissioned, and an
#                  existing config is replaced only if it carries this tool's header. v1.0: first version (day 1).
"""Build the POC's OCI config from existing files, read-only, printing value NAMES only.

Run as : the demo user on the demo VM, with the POC venv's python (it needs `cryptography`, pinned in
         code/requirements.txt).
Usage  : .venv/bin/python tools/build_oci_agentmem_config.py [--env FILE] [--keydir DIR] [--out DIR] [--check]
           --check  verify an existing config (shape, modes, key match) without writing anything
Inputs : the estate's secrets file (default ~/oracle26ai/secrets/oracle.env; parsed, never sourced; only the five
         keys OCI_USER_OCID, OCI_TENANCY_OCID, OCI_FINGERPRINT, OCI_COMPARTMENT_ID, OCI_REGION are kept, every other
         line is discarded unread beyond its key name) and the private-key candidates oci_key.pem, oci_key_fixed.pem,
         oci_key_pk8.pem in the same directory (the one whose public key has the configured fingerprint is chosen).
Output : <out>/config (default ~/oracle26ai/secrets/oci_agentmem/config): an INI [DEFAULT] section with user,
         tenancy, fingerprint, region, compartment_id and key_file = the absolute path of the EXISTING key file (the
         key is referenced, never copied). Directory created 700, file written 600 under umask 077 through a temporary
         file in the same directory and an atomic replace; modes checked after the write.
Safety : prints only names ("found", "missing", "malformed"); never a value, a path or a length. Values pass through
         memory only: no command line, no environment variable, no log. Existing files are never changed, moved or
         re-permissioned. A missing or malformed value stops the build before anything is written (exit 3) and the
         names are reported, so the operator can supply them. Nothing is read from a database credential.
Exit   : 0 written (or already identical, or --check passed); 2 usage or environment; 3 a value is missing or
         malformed (nothing written); 4 a mode check failed.
Re-run : safe; an identical config is left untouched, a different one is replaced atomically.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import io
import os
import re
import stat
import sys
from pathlib import Path

WANTED = {  # env-file key -> config key, and the shape each value must have
    "OCI_USER_OCID": ("user", re.compile(r"^ocid1\.user\.[a-z0-9._-]+$")),
    "OCI_TENANCY_OCID": ("tenancy", re.compile(r"^ocid1\.tenancy\.[a-z0-9._-]+$")),
    "OCI_FINGERPRINT": ("fingerprint", re.compile(r"^(?:[0-9a-f]{2}:){15}[0-9a-f]{2}$")),
    "OCI_COMPARTMENT_ID": ("compartment_id", re.compile(r"^ocid1\.(?:compartment|tenancy)\.[a-z0-9._-]+$")),
    "OCI_REGION": ("region", re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")),
}
KEY_CANDIDATES = ("oci_key.pem", "oci_key_fixed.pem", "oci_key_pk8.pem")
CONFIG_KEYS = ("user", "tenancy", "fingerprint", "region", "compartment_id", "key_file")
OUR_HEADER = "# v1.0 Brief 12 POC: OCI config for LiteLLM's oci/ route"
OUT_NAME = "oci_agentmem"


def say(msg: str) -> None:
    sys.stdout.write(f"build_oci_agentmem_config: {msg}\n")


def read_env(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return the wanted values (stripped, one pair of matching quotes removed) and the names of malformed ones.
    The file is streamed line by line; a line's value is looked at only when its key is on the allow-list."""
    vals: dict[str, str] = {}
    key_re = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                m = key_re.match(line)
                if not m or m.group(1) not in WANTED:
                    continue
                v = line[m.end():].strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1].strip()
                vals[m.group(1)] = v        # the last line for a key wins, as a shell would read it
    except OSError as exc:
        say(f"cannot read the estate secrets file ({type(exc).__name__})")
        raise SystemExit(2)
    malformed = [k for k, v in vals.items() if v and not WANTED[k][1].match(v.lower() if k == "OCI_FINGERPRINT" else v)]
    return vals, malformed


def fingerprint_of(pem: bytes) -> str | None:
    """MD5 fingerprint of the public half, as OCI shows it; None if the file is not an unencrypted RSA PEM key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError):
        return None
    if not isinstance(key, rsa.RSAPrivateKey):
        return None
    der = key.public_key().public_bytes(serialization.Encoding.DER,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    return ":".join(f"{b:02x}" for b in hashlib.md5(der, usedforsecurity=False).digest())


def pick_key(keydir: Path, fingerprint: str) -> Path | None:
    want = fingerprint.lower()
    for i, name in enumerate(KEY_CANDIDATES, 1):
        p = keydir / name
        if not p.is_file():
            say(f"key candidate #{i}: absent")
            continue
        try:
            fp = fingerprint_of(p.read_bytes())
        except OSError as exc:
            say(f"key candidate #{i}: unreadable ({type(exc).__name__})")
            continue
        if fp is None:
            say(f"key candidate #{i}: not an unencrypted RSA PEM key")
        elif fp == want:
            say(f"key candidate #{i}: matches the configured fingerprint")
            return p.resolve()
        else:
            say(f"key candidate #{i}: does not match the configured fingerprint")
    return None


def read_regular(path: Path) -> bytes | None:
    """Bytes of a regular, non-symlink file, or None (absent, odd type, unreadable, vanished)."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes()
    except OSError:
        return None


def render(cfg: dict[str, str]) -> bytes:
    cp = configparser.ConfigParser(interpolation=None)
    cp["DEFAULT"] = {k: cfg[k] for k in CONFIG_KEYS}
    buf = io.StringIO()
    buf.write(OUR_HEADER + " (D4: the existing API user and key, the key\n"
              "# referenced by path, never copied). Built by tools/build_oci_agentmem_config.py. Never print.\n")
    cp.write(buf)
    return buf.getvalue().encode("utf-8")


def check_modes(out_dir: Path, cfg_file: Path) -> bool:
    ok = True
    try:
        d = stat.S_IMODE(out_dir.stat().st_mode)
        f = stat.S_IMODE(cfg_file.lstat().st_mode)
    except OSError:
        say("mode check: the directory or the config vanished")
        return False
    if d != 0o700:
        say(f"config directory mode is {oct(d)}, expected 0o700")
        ok = False
    if f != 0o600:
        say(f"config file mode is {oct(f)}, expected 0o600")
        ok = False
    return ok


def write_atomic(out_dir: Path, data: bytes) -> str:
    cfg_file = out_dir / "config"
    if read_regular(cfg_file) == data:
        return "unchanged"
    tmp = out_dir / ".config.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, cfg_file)
    return "written"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    home = Path.home()
    ap.add_argument("--env", type=Path, default=home / "oracle26ai/secrets/oracle.env")
    ap.add_argument("--keydir", type=Path, default=home / "oracle26ai/secrets")
    ap.add_argument("--out", type=Path, default=home / "oracle26ai/secrets/oci_agentmem")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    os.umask(0o077)

    vals, malformed = read_env(a.env)
    missing = [k for k in WANTED if not vals.get(k)]
    for k in WANTED:
        state = "missing" if k in missing else ("malformed" if k in malformed else "found")
        say(f"{k}: {state}")
    key_path = None
    if "OCI_FINGERPRINT" not in missing and "OCI_FINGERPRINT" not in malformed:
        key_path = pick_key(a.keydir, vals["OCI_FINGERPRINT"])
    say(f"key_file: {'found' if key_path else 'missing'}")
    if missing or malformed or key_path is None:
        bad = missing + malformed + ([] if key_path else ["key_file"])
        say("STOP: not on the VM in a file (or malformed): " + ", ".join(dict.fromkeys(bad)) +
            "; nothing was written")
        return 3

    cfg = {WANTED[k][0]: (vals[k].lower() if k == "OCI_FINGERPRINT" else vals[k]) for k in WANTED}
    cfg["key_file"] = str(key_path)
    data = render(cfg)
    vals.clear()
    cfg.clear()

    if a.check:
        cfg_file = a.out / "config"
        existing = read_regular(cfg_file)
        if existing is None:
            say("check: config absent or not a readable regular file")
            return 4
        same = existing == data
        say(f"check: config {'matches' if same else 'DIFFERS from'} the existing files")
        return 0 if (same and check_modes(a.out, cfg_file)) else 4

    if a.out.name != OUT_NAME:
        say(f"--out must be a directory named {OUT_NAME}")
        return 2
    if a.out.exists():
        # Only our own directory is ever used: mode 700, holding nothing but a config this tool wrote.
        if not a.out.is_dir() or a.out.is_symlink():
            say("output path exists and is not a plain directory: not ours, nothing written")
            return 2
        if stat.S_IMODE(a.out.stat().st_mode) != 0o700:
            say("output directory exists with a mode other than 0o700: not ours, nothing written")
            return 2
        names = sorted(p.name for p in a.out.iterdir())
        if names != ["config"]:
            say(f"output directory holds {len(names)} entr(y/ies) other than exactly one config: not ours "
                "(or a leftover temporary file), nothing written")
            return 2
        cfg_file = a.out / "config"
        existing = read_regular(cfg_file)
        if existing is None or not existing.startswith(OUR_HEADER.encode("utf-8")):
            say("the existing config was not written by this tool: nothing written")
            return 2
    else:
        a.out.mkdir(mode=0o700)
        os.chmod(a.out, 0o700)          # the directory we just created (mkdir's mode is masked by the umask)
    result = write_atomic(a.out, data)
    ok = check_modes(a.out, a.out / "config")
    say(f"config {result}; keys: {', '.join(CONFIG_KEYS)}")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
