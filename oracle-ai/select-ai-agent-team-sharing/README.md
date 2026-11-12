<!-- v1.0 | 2026-11-11 | Scripts for the Select AI agent team sharing post. -->
# Select AI agent team sharing, step by step

The scripts behind
[Share a Select AI agent team across schemas: the grants it needs](https://thetechnicalsavage.com/blog/share-a-select-ai-agent-team-across-schemas-the-grants-it-needs/).

One owner schema builds a minimal agent team (one agent, one task, one invoker-rights tool) and shares it with
`DBMS_CLOUD_AI_AGENT.GRANT_TEAM_ACCESS`: one consumer by a direct grant, one through a role, and an outsider that is
never granted. A Python driver walks every grant around it and records each step with its expected outcome. Tested on
Oracle Autonomous AI Database 26ai release 23.26.4.1.0 in us-phoenix-1 with `xai.grok-4.20-non-reasoning` on OCI <!-- sanitize-ok: version -->
Generative AI, 11 November 2026. Team sharing is not in the on-premises 26ai 23.26.1 build.

| File (run order) | Run as | What |
|---|---|---|
| `code/00_admin_setup.sql` | ADMIN | Four lab users (`SAI_TEAM_OWNER`, `SAI_TEAM_APP`, `SAI_TEAM_APP2`, `SAI_TEAM_OUTSIDER`), the role `SAI_TEAM_RUNNER`, their rights, the outbound rule to OCI Generative AI |
| `code/10_owner_setup.sql` | SAI_TEAM_OWNER | The `ACCOUNTS` table, the invoker-rights tool function, the agent, task, tool and team, the owner's AI profile |
| `code/11_consumer_setup.sql` | each consumer and the outsider | Its own `ACCOUNTS` row and its own AI profile |
| `code/run_sharing_tests.py` | all four users, ADMIN read-only | The steps: grants, runs, roles, conversations, `SET_TEAM`, a change by the owner, revoke, audit, clean-up |
| `results/` | | `RESULTS.md` and the scored run `T7.jsonl` (56 records, each with its expected outcome) |

## No credentials, anywhere

Every password, OCID and connection detail comes from you at run time. If you find a real value here, it is a bug,
please open an issue.

**SQL scripts.** Run them with any SQL client as the user named above.
- `00_admin_setup.sql` takes the four new users' passwords as the binds `:SAI_TEAM_OWNER_PWD`, `:SAI_TEAM_APP_PWD`,
  `:SAI_TEAM_APP2_PWD` and `:SAI_TEAM_OUTSIDER_PWD`, so they never sit in the script text. In SQL*Plus or SQLcl, declare
  each with `variable` and set it with `exec` first. An existing user keeps its password.
- `10_owner_setup.sql` and `11_consumer_setup.sql` contain `${OCI_COMPARTMENT_ID}`: replace it with your compartment
  OCID before running.
- The AI profiles use a `DBMS_CLOUD` credential named `SAI_GENAI_CRED` for OCI Generative AI (the setup scripts name
  it). Create it under that name in all four lab schemas, from your own OCI API key with
  `DBMS_CLOUD.CREATE_CREDENTIAL`, before running `10_owner_setup.sql` and `11_consumer_setup.sql`.

**The driver** (python-oracledb in thick mode with the database wallet, `requirements.txt`). `code/lab_conn.py` reads
the connection from the environment and from login files:
- `ORACLE_IC_DIR` the Instant Client directory, and on Linux `LD_LIBRARY_PATH` set to the same directory before Python
  starts; `WALLET_DIR` the unzipped database wallet. In the wallet's `sqlnet.ora`, set the `DIRECTORY` of
  `WALLET_LOCATION` to that same folder: the downloaded file points at `?/network/admin`.
- A login file is `KEY=VALUE`, mode 600 (the helper refuses anything looser): `ADB_USER`, `ADB_PWD` and optionally
  `ADB_DSN`, an alias from the wallet's `tnsnames.ora` (default `adb_low`).
- `run_sharing_tests.py --run T1` reads `sailab_sai_team_owner.env`, `sailab_sai_team_app.env`,
  `sailab_sai_team_app2.env`, `sailab_sai_team_outsider.env` and, for the read-only audit query, `admin.env` from the
  folder named by `SAILAB_SECRETS`. It writes `results/<run>.jsonl` and `transcripts/<run>.txt`, and refuses to
  overwrite an existing run.

The driver cleans up what it granted and checks that nothing is left: no EXECUTE or `TRANSLATE SQL` grant, and both
consumers' runs read as missing.
