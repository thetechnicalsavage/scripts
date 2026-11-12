<!-- v1.0 | 2026-11-11 | Scripts for the Select AI semantic context post. -->
# Select AI semantic context: business definitions, measured

The scripts behind
[Select AI semantic context: your business definitions, measured](https://thetechnicalsavage.com/blog/select-ai-semantic-context-your-business-definitions-measured/).

A fictional retailer schema, a twelve-line business glossary, and three Select AI profiles that differ in one attribute:
A schema only, B the glossary as a semantic **file** source (`DBMS_AI_SEM`), C the same text as table annotations.
Twelve questions with hand-written gold SQL, three runs each. Tested on Oracle Autonomous AI Database 26ai release
23.26.4.1.0 in us-phoenix-1 with `xai.grok-4.20-non-reasoning` on OCI Generative AI, 11 November 2026. `DBMS_AI_SEM` is <!-- sanitize-ok: version -->
not in the on-premises 26ai 23.26.1 build.

| File (run order) | Run as | What |
|---|---|---|
| `code/00_admin_setup.sql` | ADMIN | Two lab users, package grants, `CREATE MINING MODEL`, directory `SAI_SEM_DIR`, the outbound rule to OCI Generative AI |
| `code/tools/load_onnx_blob.py` | SAI_SEM_LAB | Loads an ONNX embedding model as a BLOB with `DBMS_VECTOR.LOAD_ONNX_MODEL`, no Object Storage |
| `code/01_schema.sql`, `code/02_data.sql` | SAI_SEM_LAB | Tables, technical comments, annotations; deterministic data relative to the run day |
| `code/03_sources_profiles.sql` | SAI_SEM_LAB | The glossary file, the embedding profile, the file source, profiles A, B and C |
| `code/04_inspect.sql` | SAI_SEM_LAB | `SHOWCONTEXT`, `SHOWPROMPT` and `SHOWSQL` for one prompt per profile |
| `code/05_eval_questions.sql`, `code/run_eval.py` | SAI_SEM_LAB | Twelve questions with gold SQL; the evaluation (resumable, re-score and export modes) |
| `code/test_run_eval.py` | local | Ten tests of the answer matcher |
| `code/06_behaviours.py` | SAI_SEM_LAB, SAI_SEM_USER | Lifecycle and sharing steps with expected outcomes; cleans up only its own test objects |
| `results/` | | `RESULTS.md`, the scored run `R1d.jsonl` (+ `R1d.meta.json`, the data fingerprint) and the behaviour run `B4.jsonl` |

## No credentials, anywhere

Every password, OCID and connection detail comes from you at run time. If you find a real value here, it is a bug,
please open an issue.

**SQL scripts.** Run them with any SQL client as the user named above.
- `00_admin_setup.sql` takes the two new users' passwords as the binds `:SAI_SEM_LAB_PWD` and `:SAI_SEM_USER_PWD`, so they
  never sit in the script text. In SQL*Plus or SQLcl, `variable SAI_SEM_LAB_PWD varchar2(30)` and
  `exec :SAI_SEM_LAB_PWD := '...'` first. An existing user keeps its password.
- `03_sources_profiles.sql` contains `${OCI_COMPARTMENT_ID}`: replace it with your compartment OCID before running.
- The profiles use a `DBMS_CLOUD` credential named `SAI_GENAI_CRED` for OCI Generative AI. Create it in each lab schema
  from your own OCI API key with `DBMS_CLOUD.CREATE_CREDENTIAL`.

**Python drivers** (python-oracledb in thick mode with the database wallet, `requirements.txt`). `code/lab_conn.py`
reads the connection from the environment and from login files:
- `ORACLE_IC_DIR` the Instant Client directory, and on Linux `LD_LIBRARY_PATH` set to the same directory before Python
  starts; `WALLET_DIR` the unzipped database wallet. In the wallet's `sqlnet.ora`, set the `DIRECTORY` of
  `WALLET_LOCATION` to that same folder: the downloaded file points at `?/network/admin`.
- A login file is `KEY=VALUE`, mode 600 (the helper refuses anything looser): `ADB_USER`, `ADB_PWD` and optionally
  `ADB_DSN`, an alias from the wallet's `tnsnames.ora` (default `adb_low`).
- `run_eval.py --login <login file> --run R1` and `tools/load_onnx_blob.py --login <login file> --file <model.onnx>
  --model ALL_MINILM_L12_V2` take the file on the command line.
- `06_behaviours.py` reads `sailab_sai_sem_lab.env`, `sailab_sai_sem_user.env` and `sailab_oci.env` (the last one holds
  `OCI_COMPARTMENT_ID`) from the folder named by `SAILAB_SECRETS`.

The lab's model file was an ONNX export of all-MiniLM-L12-v2 for Oracle AI Database, 133,322,334 bytes, 384
dimensions. Any ONNX embedding model that `DBMS_VECTOR.LOAD_ONNX_MODEL` accepts will do; the embedding profile names it
as `database:<SCHEMA>.<MODEL>`.

## Not here

The probes behind the post's custom-callback and cross-schema findings: they are evidence, not reader scripts. The
results of earlier runs that the scored run replaced.
