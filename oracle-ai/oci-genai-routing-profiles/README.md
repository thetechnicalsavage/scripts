# OCI Generative AI routing profiles and model discovery

The scripts behind
[OCI Generative AI routing profiles and model discovery, tested](https://thetechnicalsavage.com/blog/oci-genai-routing-profiles-model-discovery/).

A Python runner signs every OCI request with an existing API key and works in one region, us-phoenix-1.
It takes a model discovery snapshot, plans routing profiles from it, creates them, and calls each model
twice over: by name and through its profile, with the same body at the same endpoint. An Oracle AI
Database 26ai schema, `ROUTE_LAB`, loads the snapshot into `JSON_TABLE` views and makes the same calls
from PL/SQL (`DBMS_CLOUD.SEND_REQUEST`) and Select AI (`DBMS_CLOUD_AI`). Every call is counted against a
budget before it is sent.

## Results, 10 October 2026, us-phoenix-1

| What | Result |
|---|---|
| Model discovery | 18 probes, all HTTP 200; 87 models; 63 chat models on demand in Phoenix |
| Routing profiles created | 4 (Grok 4.3, Grok 4.20 0309 reasoning, Gemini 2.5 Flash, Gemini 2.5 Pro), ACTIVE in 0.2 to 0.8 s |
| Chat API through a profile, REST | 20 of 20 HTTP 200 and correct over the four profiles; by name, 20 of 20, same returned model |
| Chat API through a profile, PL/SQL | 6 of 6 HTTP 200 and correct (Grok 4.3 and Gemini 2.5 Flash profiles); by name, 6 of 6 |
| Select AI with a profile as its model | Grok 4.3 profile 3 of 3; Gemini 2.5 Flash profile 3 of 3 `ORA-20400` on `topK` |
| Responses API with the profile | 6 of 6 HTTP 400; the model name answered 5 of 6 |
| Cost | 81 calls, about USD 0.13 by our estimate at the 10 October price list |

Every profile listed us-phoenix-1 only, so nothing here routed a call to another region. The numbers come
from the files in [`results/`](results/).

## The scripts

| Step | Script | Run on |
|---|---|---|
| the runner: preflight, discovery snapshot, plan, direct calls, profile creates, routing tests, Responses API, clean-up | [`code/poc/b14_run.py`](code/poc/b14_run.py) | host with the API key |
| region lock, request signing, budget, manifest, chat and profile calls | [`code/poc/b14lib/`](code/poc/b14lib/) | host |
| loads the snapshot, prices and results into the database, sets the credential | [`code/poc/db_register.py`](code/poc/db_register.py) | host, python-oracledb |
| offline analysis of a discovery snapshot | [`code/analysis/analyze_discovery.py`](code/analysis/analyze_discovery.py) | anywhere |
| `ROUTE_LAB` user, grants, the network ACE for the Phoenix inference host | [`code/sql/00_route_lab_prereqs.sql`](code/sql/00_route_lab_prereqs.sql) | database, as SYSDBA |
| tables, discovery views over the snapshot, the catalogue view | [`code/sql/01_route_lab_objects.sql`](code/sql/01_route_lab_objects.sql) | as ROUTE_LAB |
| `PKG_ROUTE_LAB`: the budgeted `SEND_REQUEST` and Select AI calls | [`code/sql/02_route_lab_pkg.sql`](code/sql/02_route_lab_pkg.sql) | as ROUTE_LAB |
| Select AI profiles whose `model` is a routing profile OCID | [`code/sql/03_select_ai_profiles.sql`](code/sql/03_select_ai_profiles.sql) | as ROUTE_LAB |
| the database's calls, direct and through a profile | [`code/sql/04_db_calls.sql`](code/sql/04_db_calls.sql) | as ROUTE_LAB |
| read-only state check | [`code/sql/05_state_check.sql`](code/sql/05_state_check.sql) | as ROUTE_LAB |
| the discovery queries shown in the post | [`code/sql/06_shot_queries.sql`](code/sql/06_shot_queries.sql) | as ROUTE_LAB |
| clean-up | [`98`](code/sql/98_route_lab_own_cleanup.sql) as ROUTE_LAB, [`99`](code/sql/99_cleanup.sql) as SYSDBA | database |
| tests (no network, no database) | [`code/tests/`](code/tests/) | anywhere |

`python3 code/poc/b14_run.py --help` lists the steps. The key and its settings are read from `--env` and
`--key` files on the host, never printed; nothing in this folder holds a secret, an OCID or a tenancy name.

## Before you run it

- **Permissions.** Routing profiles are their own IAM resource type, `generative-ai-routing-profile`. Oracle's
  routing profile permissions page also says a call through a profile needs
  `GENERATIVE_AI_ROUTING_PROFILE_READ` on top of the inference permission.
- **Lab guards you will want to change.** `b14lib/phoenix.py` refuses any region but us-phoenix-1 in the
  environment, the URL, the request body and the stored profile. `b14lib/windows.py` and the first block of
  every SQL script refuse database work in two windows that belonged to the lab (daily 01:50 to 04:00 UTC,
  and 12 October 2026 09:00 to 16:00 UTC). The budget caps are in `b14lib/budget.py`.
- **Headers** refer to the lab's plan and to tools that are not in this folder.

Tests, in a virtual environment built from the pinned files:

```
cd code
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r requirements.txt
.venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r requirements-test.txt
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests
```

## Not in this folder

- The APEX lab application, its generator and helper scripts.
- The tools that rendered the screenshots, checked the post's numbers and made the backup.
- Two read-only probes from the study before the test, which called regions other than Phoenix.

## Changed from the lab copy

- `code/sql/01_route_lab_objects.sql`: the four views only the APEX pages read and their SELECT grants are
  left out. `V_CATALOGUE` stays.
- `code/tests/test_sql_static.py`: the checks of the APEX helper scripts are left out.

Every other file is as it ran.

## Results files

| File | What it holds |
|---|---|
| [`results/s1/summary.json`](results/s1/summary.json) | the 18 discovery probes: HTTP status and item counts |
| [`results/s4/records.json`](results/s4/records.json) | every create attempt: HTTP status, the service's message, what was stored |
| [`results/s5/summary.json`](results/s5/summary.json) | the 40 REST calls, direct against profile |
| [`results/s9/responses.json`](results/s9/responses.json), [`responses_r2.json`](results/s9/responses_r2.json) | the Responses API runs |
| [`results/db_logs/s6_04_partA.log`](results/db_logs/s6_04_partA.log), [`s7_04_partB.log`](results/db_logs/s7_04_partB.log) | the database's calls as run: PL/SQL `SEND_REQUEST` and Select AI |
| [`results/budget_status.json`](results/budget_status.json) | the ledger: calls and estimated cost by step |

Written against the OCI Generative AI API version 20231130 and Oracle AI Database 26ai 23.26.1, on
10 October 2026. One tenancy, one region: an observation, not a benchmark.
