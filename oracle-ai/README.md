# Oracle AI on 26ai

Scripts referenced from the Database & AI posts on
[thetechnicalsavage.com](https://thetechnicalsavage.com). Written against Oracle AI
Database 26ai Enterprise Edition in a container, September 2026.

| | |
|---|---|
| [`dbms-cloud-on-prem/`](dbms-cloud-on-prem/) | Why `SELECT AI` fails on your own database, and the four prerequisites |
| [`onnx-embeddings-vector-search/`](onnx-embeddings-vector-search/) | Two vector patterns, and how to choose |
| [`select-ai-nl2sql/`](select-ai-nl2sql/) | The profile, and the row-level security underneath |
| [`oci-speech-from-plsql/`](oci-speech-from-plsql/) | Driving an OCI AI service with `SEND_REQUEST` |
| [`oml-bakeoff/`](oml-bakeoff/) | Five mining functions, five selection metrics |
| [`select-ai-agent-teams/`](select-ai-agent-teams/) | Where the routing belongs |
| [`ords-mcp-server/`](ords-mcp-server/) | An AI client talking to your own database over MCP |

**Start with [`dbms-cloud-on-prem/00_diagnose.sql`](dbms-cloud-on-prem/00_diagnose.sql).**
Every other directory assumes `DBMS_CLOUD` is already working, and on a non-Autonomous
database it is not installed by default.

## No credentials, anywhere

Every OCID, compartment, namespace, bucket, key and wallet path in here is a
substitution variable prompted at run time. If you find a real value, it is a bug,
please open an issue.

## These are one engineer's results on one build

Nothing here is an Oracle recommendation. Measured claims are measured: where a thing
was observed once, on one model, on one date, the script or the README says so.
Synthetic data produces illustrative numbers, not benchmarks.
