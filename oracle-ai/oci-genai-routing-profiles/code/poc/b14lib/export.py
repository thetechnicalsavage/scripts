# v1.1 2026-10-10  Brief 14: definitions of the OCI resources this POC created, so the POC can be set up again from
#   the ai_demos backup (S10, before any deletion; operator 10-Oct: "make sure we have all in github before deletion
#   scripts and all so that it can be setup later").
#   v1.1: (Codex adversarial review 31, findings 4-5) every dict key must be a property name of the two resources'
#         schemas (KNOWN_KEYS) or a tag key we set, otherwise key and value are withheld; the project's create body
#         must equal the code's whole body (description and compartment placeholder included), as a profile's must.
#   v1.0: first version. GET only: nothing is created, changed or deleted.
"""Export every resource of this run's manifest as JSON a person can re-create it from, identifiers replaced by
named placeholders.

CREATED entries (the live routing profiles and the project): GET through the Phoenix lock; the answer must be HTTP
200, lifecycle ACTIVE and pass manifest.ownership_problems() against the S0 baseline, otherwise the entry is
reported and nothing of it is written. Two views are kept:
  * as_stored: the GET answer with every string value classified by _scrub(): an OCID becomes a named placeholder
    (<P1_OCID> ... for the manifest's own resources, <COMPARTMENT_OCID>, <TENANCY_OCID>, <USER_OCID>, else
    <OTHER_OCID:type>); Oracle-Tags.CreatedBy becomes <CREATED_BY>; any other string is kept only if it has a
    known-safe shape (timestamps, lifecycle words, the Phoenix region id, model ids, b14 names, this POC's
    descriptions and tag values, true/false, enum words under the schema's enum fields) and is otherwise written
    as <WITHHELD> with its key path listed. Keys are held to the same rule: a key that is not a property name of
    the two schemas (KNOWN_KEYS) or of the tags (our freeform keys, Oracle-Tags CreatedBy/CreatedOn, Oracle's
    orcl-* system tags) is written as a placeholder and its value withheld. A tenancy namespace or tenancy name
    therefore cannot be written, although this code never knows either value.
  * create_details: the create body as this POC's code sent it (CreateRoutingProfileDetails or
    CreateGenerativeAiProjectDetails; for a profile the same keys are the OCI CLI's --from-json names), taken from
    as_stored. It must equal the body the code builds (a profile's S2 plan body, the project's PROJECT_BODY), with
    the compartment as <COMPARTMENT_OCID>, and a profile's must pass the Phoenix body check.
DELETED entries that were created (N3, accepted and deleted at once) are written from the S2 plan only, marked
"do not re-create"; REFUSED entries never existed and are not written.
A last guard refuses the whole export if the JSON text still holds an OCID prefix, an IPv4 address, an e-mail address
or any signing value.
"""
import json
import re

from . import phoenix
from .manifest import ownership_problems
from .ociclient import OciClient
from .ocisign import IP_RE, OCID_RE

RP_TYPE = "generativeairoutingprofile"
PROJECT_TYPE = "generativeaiproject"
FORMAT = "b14-oci-definition v1.1"
API = {RP_TYPE: "POST /20231130/routingProfiles (CreateRoutingProfile)",
       PROJECT_TYPE: "POST /20231130/generativeAiProjects (CreateGenerativeAiProject)"}
CREATE_KEYS = {RP_TYPE: ("compartmentId", "displayName", "description", "freeformTags", "modelRoutingPolicy",
                         "regionRoutingPolicy"),
               PROJECT_TYPE: ("compartmentId", "displayName", "description", "freeformTags")}
LIFECYCLE = {"CREATING", "ACTIVE", "UPDATING", "DELETING", "DELETED", "FAILED", "INACTIVE", "NEEDS_ATTENTION"}
OUR_TAG_KEYS = {"brief", "run", "kind"}
ORACLE_TAG_KEYS = {"CreatedBy", "CreatedOn"}
# every property name of RoutingProfile and GenerativeAiProject and of the schemas they reference, read from
# research/src/api-spec-generative-ai-mgmt.yaml (saved 10-Oct)
KNOWN_KEYS = frozenset({
    "allowedModels", "allowedRegions", "compartmentId", "condenserConfig", "conversationConfig",
    "conversationsRetentionInHours", "definedTags", "description", "displayName", "embeddingConfig",
    "extractionConfig", "freeformTags", "id", "isEnabled", "lifecycleDetails", "lifecycleState", "llmSelection",
    "llmSelectionType", "longTermMemoryConfig", "modelConfigType", "modelId", "modelRoutingPolicy", "previousState",
    "regionRoutingPolicy", "responsesRetentionInHours", "shortTermMemoryOptimizationConfig",
    "standardLongTermMemoryStrategy", "systemTags", "timeCreated", "timeUpdated"})
ENUM_KEYS = {"lifecycleState", "llmSelectionType", "modelConfigType"}
ENUM_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,40}$")
PROJECT_DESCRIPTION = "Brief 14 lab, Responses API step; deleted after the post"      # as project.create_project
SAFE_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"),   # timestamps
    re.compile(r"^b14(?:-[a-z0-9]+)+$"),                                  # display names, the run id
    re.compile(r"^(?:xai|google|openai|meta|cohere)\.[a-z0-9][a-z0-9.\-]{0,80}$"),   # model ids
    re.compile(r"^Brief 14 lab, [A-Za-z0-9 ,;()\-]{1,120}$"),             # this POC's descriptions
)
SAFE_LITERALS = {phoenix.REGION, "b14", "profile", "invalid", "project", "true", "false"}
OCID_PREFIX = "ocid" + "1."                       # assembled, so this file carries no OCID prefix
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


class ExportError(RuntimeError):
    pass


class _Scrubber:
    def __init__(self, ocid_names: dict, sec, safe=()):
        self.names = dict(ocid_names)
        self.safe = set(SAFE_LITERALS) | set(safe)              # e.g. this run's id, a tag value we set
        self.names.update({sec.compartment: "<COMPARTMENT_OCID>", sec.tenancy: "<TENANCY_OCID>",
                           sec.user: "<USER_OCID>"})
        self.withheld = []

    def value(self, v: str, path: str) -> str:
        if v in self.names:
            return self.names[v]
        m = OCID_RE.fullmatch(v)
        if m:
            return f"<OTHER_OCID:{m.group(1)}>"
        if v in self.safe or v in LIFECYCLE or any(p.fullmatch(v) for p in SAFE_PATTERNS):
            return v
        if path.rsplit(".", 1)[-1] in ENUM_KEYS and ENUM_RE.fullmatch(v):
            return v
        self.withheld.append(path)
        return "<WITHHELD>"

    @staticmethod
    def key(path: str, k: str, i: int) -> tuple[str, bool]:
        """(the key to write, whether its value may be written). Tag namespaces and keys can name the organisation,
        and a key outside the schema could be anything, so only known keys are written as they are."""
        if path == "freeformTags":
            return (k, True) if k in OUR_TAG_KEYS else (f"<FREEFORM_TAG_KEY_{i}>", False)
        if path == "definedTags":
            return (k, True) if k == "Oracle-Tags" else (f"<DEFINED_TAG_NAMESPACE_{i}>", False)
        if path == "definedTags.Oracle-Tags":
            return (k, True) if k in ORACLE_TAG_KEYS else (f"<ORACLE_TAG_KEY_{i}>", False)
        if path == "systemTags":
            return (k, True) if re.fullmatch(r"orcl-[a-z0-9-]{1,40}", k) else (f"<SYSTEM_TAG_NAMESPACE_{i}>", False)
        if path.startswith("systemTags."):
            return (k, True) if re.fullmatch(r"[a-z][a-z0-9-]{0,40}", k) else (f"<SYSTEM_TAG_KEY_{i}>", False)
        return (k, True) if k in KNOWN_KEYS else (f"<UNKNOWN_KEY_{i}>", False)

    def walk(self, obj, path: str = ""):
        if isinstance(obj, dict):
            out = {}
            for i, (k, v) in enumerate(obj.items()):
                key, keep = self.key(path, k, i)
                p = f"{path}.{key}" if path else key   # the path names the placeholder, never the original key
                if not keep:
                    out[key] = "<WITHHELD>"
                    self.withheld.append(p)
                elif path == "definedTags.Oracle-Tags" and k == "CreatedBy":
                    out[key] = "<CREATED_BY>"
                else:
                    out[key] = self.walk(v, p)
            return out
        if isinstance(obj, list):
            return [self.walk(v, f"{path}[{i}]") for i, v in enumerate(obj)]
        if isinstance(obj, str):
            return self.value(obj, path)
        return obj                                   # numbers, booleans, null


def placeholder(alias: str) -> str:
    return f"<{alias}_OCID>"


def project_body(run_id: str) -> dict:
    """The body project.create_project() sends (compartment aside); test_export checks the two stay equal."""
    return {"compartmentId": None, "displayName": "b14-resp", "description": PROJECT_DESCRIPTION,
            "freeformTags": {"brief": "b14", "run": run_id, "kind": "project"}}


def guard(text: str, sec, extra=()) -> list:
    """Why this JSON text must not be written ([] when it may)."""
    why = []
    if OCID_PREFIX in text.lower():
        why.append("an OCID is left")
    if IP_RE.search(text):
        why.append("an IPv4 address is left")
    if EMAIL_RE.search(text):
        why.append("an e-mail address is left")
    if any(v and v in text for v in list(sec.scrub) + [sec.user, sec.tenancy, sec.compartment] + list(extra)):
        why.append("a signing value is left")
    return why


def _url(rtype: str, ocid: str) -> str:
    return phoenix.MGMT_BASE + ("/routingProfiles/" if rtype == RP_TYPE else "/generativeAiProjects/") + ocid


def _plan_body(plan: dict, alias: str) -> dict | None:
    s = next((x for x in plan.get("profiles", []) + plan.get("invalid", []) if x.get("alias") == alias), None)
    return dict(s["body"]) if s and isinstance(s.get("body"), dict) else None


def export_all(client: OciClient, manifest, base: dict, plan: dict, run_id: str) -> dict:
    """GET-only export. Returns {"rows": [...], "definitions": {alias: doc}, "ok": bool}; raises ExportError when the
    guard finds an identifier in what would be written (then nothing may be written)."""
    entries = [e for e in manifest.entries() if e.get("run_id") == run_id]
    names = {e["ocid"]: placeholder(e["alias"]) for e in entries if e.get("ocid")}
    rows, docs = [], {}
    for e in entries:
        row = {"alias": e["alias"], "type": e["type"], "display_name": e["display_name"],
               "manifest_state": e["state"], "ocid_sha16": e.get("ocid_sha16")}
        if e["state"] == "REFUSED" or not e.get("ocid"):
            row.update(written=False, why="never existed (refused by the service)")
            rows.append(row)
            continue
        if e["state"] == "DELETED":
            body = _plan_body(plan, e["alias"])
            if body is None:
                row.update(written=False, why="deleted, and no plan body to write")
                rows.append(row)
                continue
            body["compartmentId"] = "<COMPARTMENT_OCID>"
            docs[e["alias"]] = {"format": FORMAT, "alias": e["alias"], "type": e["type"], "api": API[e["type"]],
                                "source": "S2 plan body (the resource was deleted before this export)",
                                "do_not_recreate": "accepted by the service against the docs, deleted at once, "
                                                   "never called (results/s4/records.json)",
                                "created_utc": e.get("created_utc"), "deleted_utc": e.get("deleted_utc"),
                                "placeholder": placeholder(e["alias"]), "create_details": body, "as_stored": None}
            row.update(written=True, source="plan")
            rows.append(row)
            continue
        if e["state"] != "CREATED":
            row.update(written=False, why=f"manifest state {e['state']}: a person has to look")
            rows.append(row)
            continue
        res = client.request("GET", _url(e["type"], e["ocid"]), repeatable=True)
        got = res.body if res.ok and isinstance(res.body, dict) else {}
        base_ids = base["routing_profiles"] if e["type"] == RP_TYPE else base["projects"]
        problems = ownership_problems(got, e, client.sec.compartment, base_ids) if res.ok \
            else [f"GET answered HTTP {res.status}"]
        if res.ok and got.get("lifecycleState") != "ACTIVE":
            problems.append(f"lifecycle {got.get('lifecycleState')}, not ACTIVE")
        row.update(http_status=res.status, lifecycle=got.get("lifecycleState"))
        if problems:
            row.update(written=False, why="; ".join(problems))
            rows.append(row)
            continue
        sc = _Scrubber(names, client.sec, safe=(run_id,))
        stored = sc.walk(got)
        create = {k: stored[k] for k in CREATE_KEYS[e["type"]] if k in stored}
        if e["type"] == RP_TYPE:
            phoenix.check_profile_body(create)
            expected = _plan_body(plan, e["alias"])
        else:
            expected = project_body(run_id)
        match = expected is not None and create == dict(expected, compartmentId="<COMPARTMENT_OCID>")
        docs[e["alias"]] = {"format": FORMAT, "alias": e["alias"], "type": e["type"], "api": API[e["type"]],
                            "source": "GET of the live resource", "created_utc": e.get("created_utc"),
                            "placeholder": placeholder(e["alias"]), "create_details": create, "as_stored": stored,
                            "withheld_paths": sc.withheld}
        row.update(written=True, source="GET", matches_code=match, withheld=len(sc.withheld))
        rows.append(row)
    text = json.dumps({"rows": rows, "definitions": docs}, sort_keys=True)
    why = guard(text, client.sec, extra=[e["ocid"] for e in entries if e.get("ocid")])
    if why:
        raise ExportError("export refused: " + ", ".join(why))
    ok = all(r.get("written") or r["manifest_state"] == "REFUSED" for r in rows) and \
        all(r.get("matches_code", True) for r in rows)
    return {"rows": rows, "definitions": docs, "ok": ok,
            "placeholders": sorted({placeholder(e["alias"]) for e in entries if e.get("ocid")}
                                   | {"<COMPARTMENT_OCID>", "<TENANCY_OCID>", "<USER_OCID>", "<CREATED_BY>"})}
