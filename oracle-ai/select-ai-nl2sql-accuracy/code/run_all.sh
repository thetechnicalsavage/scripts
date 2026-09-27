#!/usr/bin/env bash
# v1.3 - NL2SQL accuracy lab: build the lab and measure every practice, in order.
#        v1.3: compartment OCID, region and embedding-model names validated in the shell before
#              any of them reaches SQL*Plus (they are spliced into DDL/JSON there).
#        v1.2: passwords prompted when not set and never exported to child processes except
#              the python steps that need LAB_PASSWORD; region derived from GENAI_HOST;
#              LAB_PASSWORD checked in the shell (letters and digits only).
#        v1.1: RESULTS_DIR selects where scores go (default ../results).
#
# Needs  : sqlplus (or SQLcl as `sql`) on PATH, python3 with python-oracledb, an OCI API
#          key in ~/.oci/config, network access from the database to OCI Generative AI.
# Env    : ADMIN_CONNECT   e.g. "sys@//dbhost:1521/pdb as sysdba" - prompted if not set (steps 00, 00b)
#          LAB_PASSWORD    password to give the lab schema (letters and digits) - prompted if not set
#          OCI_REGION      optional; derived from GENAI_HOST when not set
#          LAB_DSN         e.g. dbhost:1521/pdb
#          OCI_COMPARTMENT OCID of the compartment allowed to call OCI Generative AI
#          GENAI_HOST      e.g. inference.generativeai.us-phoenix-1.oci.oraclecloud.com
#          EMBED_OWNER / EMBED_MODEL  an in-database ONNX embedding model (see 00b)
#          RUNS            evaluation runs per stage (default 3)
#          RESULTS_DIR     where eval output goes (default ../results)
# Re-run : safe. Every script is idempotent; results are appended with a UTC stamp.
# Nothing here prints or stores a credential. Passwords reach sqlplus on stdin. Do not put
# passwords on the command line (shell history); environment variables are readable by the
# same OS user, which is why they are un-exported here before anything else runs.
set -euo pipefail
cd "$(dirname "$0")"
: "${LAB_DSN:?}" "${OCI_COMPARTMENT:?}" "${GENAI_HOST:?}" "${EMBED_OWNER:?}" "${EMBED_MODEL:?}"
if [ -z "${ADMIN_CONNECT:-}" ]; then read -rsp "Admin connect string (user/pwd@//host:port/pdb as sysdba): " ADMIN_CONNECT; echo; fi
if [ -z "${LAB_PASSWORD:-}" ];  then read -rsp "Password for NL2SQL_LAB: " LAB_PASSWORD; echo; fi
export -n ADMIN_CONNECT LAB_PASSWORD           # keep both out of every child's environment
[[ "$LAB_PASSWORD" =~ ^[A-Za-z0-9]{8,}$ ]] || { echo "LAB_PASSWORD: 8+ letters and digits only" >&2; exit 1; }
[[ "$GENAI_HOST" =~ ^[a-z0-9.-]+$ ]]       || { echo "GENAI_HOST must be a DNS name" >&2; exit 1; }
OCI_REGION="${OCI_REGION:-$(sed -E 's/^inference\.generativeai\.([a-z0-9-]+)\.oci\.oraclecloud\.com$/\1/' <<<"$GENAI_HOST")}"
[[ "$OCI_REGION" =~ ^[a-z]+-[a-z]+-[0-9]+$ ]] || { echo "set OCI_REGION (could not derive it from GENAI_HOST)" >&2; exit 1; }
[[ "$OCI_COMPARTMENT" =~ ^ocid1\.(compartment|tenancy)\.[a-z0-9-]+\.[a-z0-9-]*\.[a-z0-9]+$ ]] || { echo "OCI_COMPARTMENT is not a compartment/tenancy OCID" >&2; exit 1; }
for n in "$EMBED_OWNER" "$EMBED_MODEL"; do
  [[ "$n" =~ ^[A-Za-z][A-Za-z0-9_\$#]{0,127}$ ]] || { echo "EMBED_OWNER/EMBED_MODEL must be plain Oracle names" >&2; exit 1; }
done
RUNS="${RUNS:-3}"
RESULTS_DIR="${RESULTS_DIR:-$(pwd)/../results}"
SQLPLUS="${SQLPLUS:-sqlplus}"
export LAB_USER=NL2SQL_LAB

admin() { printf '%s\n@%s %s\nexit\n' "connect $ADMIN_CONNECT" "$1" "${2:-}" | "$SQLPLUS" -s -L /nolog; }
lab()   { printf 'connect NL2SQL_LAB/"%s"@//%s\n@%s %s\nexit\n' "$LAB_PASSWORD" "$LAB_DSN" "$1" "${2:-}" \
            | "$SQLPLUS" -s -L /nolog; }
score() { LAB_PASSWORD="$LAB_PASSWORD" python3 eval/eval_nl2sql.py --profile "$1" --stage "$2" --runs "$RUNS" --out "$RESULTS_DIR" ${3:+--set "$3"}; }

echo "== prerequisites";          admin 00_admin_prereqs.sql "$LAB_PASSWORD $GENAI_HOST"
                                  admin 00b_grant_embedding_model.sql "$EMBED_OWNER $EMBED_MODEL"
echo "== schema and data";        lab 01_lab_schema.sql
echo "== credential";             LAB_PASSWORD="$LAB_PASSWORD" python3 02_create_credential.py
echo "== baseline";               lab 03_profile_baseline.sql "$OCI_COMPARTMENT $OCI_REGION";  score NL2SQL_LAB_AI S0_baseline
echo "== comments";               lab 04_comments.sql;                 score NL2SQL_LAB_AI S1_comments
echo "== annotations";            lab 05_annotations.sql;              score NL2SQL_LAB_AI S2_annotations
echo "== constraints";            lab 06_constraints.sql;              score NL2SQL_LAB_AI S3_constraints
echo "== curated object list";    lab 07_object_list.sql;              score NL2SQL_LAB_AI S4_object_list
echo "== views next to tables";   lab 08_views.sql;                    score NL2SQL_LAB_AI S5_views
echo "== views replace tables";   lab 09_views_replace_tables.sql;     score NL2SQL_LAB_AI S5b_views_replace
echo "== vocabulary on views";    lab 10_view_vocabulary.sql;          score NL2SQL_LAB_AI S5c_view_vocabulary
                                                                       score NL2SQL_LAB_AI S5c_view_vocabulary paraphrase
echo "== feedback";               lab 11_feedback.sql "$EMBED_OWNER $EMBED_MODEL"; score NL2SQL_LAB_AI S6_feedback
                                                                       score NL2SQL_LAB_AI S6_feedback paraphrase
echo "== automated object list";  lab 12_automated_object_list.sql "$OCI_COMPARTMENT $OCI_REGION $EMBED_OWNER $EMBED_MODEL"
                                  sleep 120   # let the object-list index finish its first load
                                  score NL2SQL_LAB_AUTO S7_automated
echo "== done: see $RESULTS_DIR/summary.csv"
