# v1.1 2026-10-10  Brief 12 PLAN 10.3-10.5: pools, the in-database embedder, the LLM (D4 config, D5 model), the
#                  litellm switches and secret registration for redaction.
#                  v1.1: (Codex review) a malformed OCI config raises a generic ConfigError (no parser text).
#                  v1.0: first version (day 1).
"""Configuration shared by every POC step script.

Secrets are read from the two 600 files under ~/oracle26ai/secrets/ (agentmem.env: database passwords;
oci_agentmem/config: the OCI values D4 built from the existing key) and registered with lib.redact before any of
them is used. They are passed as keyword arguments only, never on a command line, in an environment variable or a
log. The litellm switches of PLAN 10.3 are set and asserted by configure_litellm().
"""
from __future__ import annotations

import configparser
import json
import os
import stat
from pathlib import Path

from lib import redact

POC = Path(os.environ.get("AGENTMEM_POC", "~/agentmem-poc")).expanduser()
SECRETS_DIR = Path("~/oracle26ai/secrets").expanduser()
AGENTMEM_ENV = SECRETS_DIR / "agentmem.env"
OCI_CONFIG = SECRETS_DIR / "oci_agentmem" / "config"
DSN = os.environ.get("AGENTMEM_DSN", "localhost:1521/ORCLPDB1")   # a host name, never an IP address (5.4)
STAGING = POC / "staging"
STATE = POC / "state"
EMBED_MODEL = "ASKORACLE.ALL_MINILM_L12_V2"
EMBED_DIM = 384
EMBED_MAX_TOKENS = 256
TOKEN_BUDGET = 5_000_000                                         # D5
LLM_CANDIDATES = (                                               # PLAN 10.5, in order
    "oci/xai.grok-4.20-non-reasoning",
    "oci/xai.grok-4.20-reasoning",
    "oci/xai.grok-4.20-0309-non-reasoning",
)
LLM_CHOICE_FILE = STATE / "llm_choice.json"
MAX_CONCURRENT = 2                                               # 5.3


class ConfigError(RuntimeError):
    pass


def _require_600(path: Path) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        raise ConfigError(f"{path.name} is missing or unreadable ({type(exc).__name__})") from None
    if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600:
        raise ConfigError(f"{path.name} must be a regular file with mode 600")


def _env_value(path: Path, key: str) -> str:
    _require_600(path)
    val = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(key + "="):
                val = line[len(key) + 1:].strip()
    if not val:
        raise ConfigError(f"{key} missing in {path.name}")
    return val


def db_password(user: str) -> str:
    key = {"AGENTMEM": "AGENTMEM_PWD", "AGENTMEM_RT": "AGENTMEM_RT_PWD"}[user]
    pw = _env_value(AGENTMEM_ENV, key)
    redact.add_secrets([pw])
    return pw


def oci_kwargs() -> dict[str, str]:
    """The six oci_* keyword arguments LiteLLM's oci/ route reads (B32), from the D4 config."""
    _require_600(OCI_CONFIG)
    cp = configparser.ConfigParser(interpolation=None)
    try:
        with OCI_CONFIG.open(encoding="utf-8") as fh:
            cp.read_file(fh)
    except (configparser.Error, OSError, UnicodeError):
        raise ConfigError("oci_agentmem/config cannot be parsed") from None
    d = cp["DEFAULT"]
    try:
        kw = {"oci_user": d["user"], "oci_tenancy": d["tenancy"], "oci_fingerprint": d["fingerprint"],
              "oci_region": d["region"], "oci_compartment_id": d["compartment_id"], "oci_key_file": d["key_file"]}
    except KeyError as exc:
        raise ConfigError(f"oci_agentmem/config lacks {exc.args[0]}") from None
    redact.add_secrets(kw.values())
    return kw


def register_all_secrets() -> None:
    """Load every secret value the run could see into the redactor, before anything else runs."""
    for user in ("AGENTMEM", "AGENTMEM_RT"):
        try:
            db_password(user)
        except ConfigError:
            pass                      # AGENTMEM_RT may not be needed by a step; a missing AGENTMEM fails later
    if OCI_CONFIG.exists():
        oci_kwargs()


def prepare_environment() -> None:
    """Before litellm is imported: the local cost map, no OCI_* or LITELLM_LOG variable (LiteLLM would read them)."""
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    bad = sorted(k for k in os.environ if k.startswith("OCI_") or k == "LITELLM_LOG")
    if bad:
        raise ConfigError("environment must not set: " + ", ".join(bad))


def configure_litellm() -> dict:
    """PLAN 10.3 switches, set and asserted; returns their values for the transcript."""
    import litellm
    litellm.suppress_debug_info = True
    litellm.set_verbose = False
    litellm.turn_off_message_logging = True
    litellm.redact_messages_in_exceptions = True
    litellm.log_raw_request_response = False
    for name in ("callbacks", "success_callback", "failure_callback", "_async_success_callback",
                 "_async_failure_callback"):
        setattr(litellm, name, [])
    checks = {
        "LITELLM_LOCAL_MODEL_COST_MAP": os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP"),
        "LITELLM_LOG_unset": "LITELLM_LOG" not in os.environ,
        "suppress_debug_info": litellm.suppress_debug_info,
        "set_verbose": litellm.set_verbose,
        "turn_off_message_logging": litellm.turn_off_message_logging,
        "redact_messages_in_exceptions": litellm.redact_messages_in_exceptions,
        "log_raw_request_response": litellm.log_raw_request_response,
        **{n: len(getattr(litellm, n)) for n in ("callbacks", "success_callback", "failure_callback",
                                                  "_async_success_callback", "_async_failure_callback")},
        "litellm_version": getattr(litellm, "version", None) or _dist_version("litellm"),
    }
    ok = (checks["LITELLM_LOCAL_MODEL_COST_MAP"] == "True" and checks["LITELLM_LOG_unset"]
          and checks["suppress_debug_info"] is True and checks["set_verbose"] is False
          and checks["turn_off_message_logging"] is True and checks["redact_messages_in_exceptions"] is True
          and checks["log_raw_request_response"] is False
          and all(checks[n] == 0 for n in ("callbacks", "success_callback", "failure_callback",
                                           "_async_success_callback", "_async_failure_callback")))
    if not ok:
        raise ConfigError("litellm switches not as PLAN 10.3 requires")
    redact.harden()
    return checks


def _dist_version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:          # metadata missing: reported as None, nothing depends on it
        return None


def make_pool(user: str = "AGENTMEM", min_: int = 1, max_: int = 4):
    import oracledb
    return oracledb.create_pool(user=user, password=db_password(user), dsn=DSN, min=min_, max=max_, increment=1)


def make_embedder(pool):
    from oracleagentmemory.core.embedders import OracleDBEmbedder
    return OracleDBEmbedder(connection=pool, model=EMBED_MODEL, embedding_dimension=EMBED_DIM,
                            max_input_tokens=EMBED_MAX_TOKENS)


def make_llm(model: str | None = None, *, supports_vision: bool = False, structured_reminder: bool | None = None):
    """An SDK Llm on the chosen model (state/llm_choice.json unless given), temperature 0, 2 concurrent requests,
    supports_vision False unless asked (so the SDK never runs its lazy vision probe)."""
    from oracleagentmemory.core.llms import Llm
    kw = dict(model=model or chosen_model(), temperature=0, supports_vision=supports_vision,
              max_concurrent_requests=MAX_CONCURRENT)
    if structured_reminder is not None:
        kw["enable_structured_output_reminder"] = structured_reminder
    return Llm(**kw, **oci_kwargs())


def chosen_model() -> str:
    try:
        data = json.loads(LLM_CHOICE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError("no LLM chosen yet: run step 0 (py/00_smoke_llm.py)") from None
    model = data.get("model")
    if model not in LLM_CANDIDATES:
        raise ConfigError("state/llm_choice.json names a model outside PLAN 10.5's list")
    return model


def structured_reminder_needed() -> bool:
    try:
        return bool(json.loads(LLM_CHOICE_FILE.read_text(encoding="utf-8")).get("structured_reminder"))
    except (OSError, ValueError):
        return False
