# OCI Speech from PL/SQL

There is no `DBMS_SPEECH`. `DBMS_CLOUD.SEND_REQUEST` signs a REST call with an OCI
credential, which is enough to drive any OCI AI service from PL/SQL with nothing in
the path.

Speech is the awkward worked example, because it is asynchronous **and indirect**.

| Step | Call | What comes back |
|---|---|---|
| 1 | `PUT` the audio to Object Storage | an HTTP status |
| 2 | `POST` a transcription job | a job `id`, and nothing else |
| 3 | `GET` the job, repeatedly | a `lifecycleState` |
| 4 | `GET` the job's output folder, **listed** | the name of an object |
| 5 | `GET` that object | the JSON you wanted |


## The scripts

| | |
|---|---|
| [`00_check.sql`](00_check.sql) | **READ-ONLY.** Credential, host ACE, wallet ACE. Empty here means start at `../dbms-cloud-on-prem/` |
| [`01_pipeline_pkg.sql`](01_pipeline_pkg.sql) | The whole round trip: upload, create job, poll, list the output folder, fetch, parse |
| [`02_schedule.sql`](02_schedule.sql) | The collector job, and the query that tells you it is missing |

## Four things that cost time

**`ORA-40441` on every job.** The create-job reply runs well over a thousand
characters and the code truncated before parsing, so the cut landed mid-object.
Parse the whole CLOB; truncate only for the failure message, where truncation belongs.

**Speech does not return the transcript.** It returns a job. The transcript is written
back to Object Storage as a separate object.

**Do not reconstruct the output filename.** The convention observed was
`out/job-<last OCID segment>/<namespace>_<bucket>_<object path>.json` with the object
path's slashes *preserved*, which is not what a reasonable person guesses first.
Listing the folder survives the convention changing again.

**Start the job and you have not finished.** The first version posted jobs and never
collected them, so rows sat in `TRANSCRIBING` for ever with no error anywhere. Write
the collector at the same time as the submitter.

## Hardening worth copying

- the worker takes a **named lock** so two runs cannot drive the same row
- a failed transcription is marked `FAILED` with the reason, not left in limbo
- a `transcript_source` column records which path produced the text, so the
  provenance of every transcript is visible later

Needs `DBMS_CLOUD`, which on a non-Autonomous database is
[four separate prerequisites](../dbms-cloud-on-prem/).
