#!/bin/bash
# Enable the ORDS MCP server. Every command here is idempotent.
#
# Tested on ORDS 26.2.3 running standalone in the official container image
# against Oracle AI Database 26ai EE. The MCP runtime is ONLY available in
# standalone deployments - on Tomcat or WebLogic the /mcp path is never
# registered.
#
# Each step is a global setting, so all of this needs an ORDS RESTART to take
# effect. On a container with APEX mounted that restart took about 6 minutes.
set -euo pipefail

CFG=/etc/ords/config
ORDS="ords --config $CFG"

IDP_ISSUER=${IDP_ISSUER:-https://your-idp.example.com/}
MCP_AUDIENCE=${MCP_AUDIENCE:-http://localhost:8080/mcp}
JWKS_URL=${JWKS_URL:-https://your-idp.example.com/.well-known/jwks.json}
POOL=${POOL:-mcpdemo}
DB_USER=${DB_USER:-MCP_READER}
DB_URL=${DB_URL:-jdbc:oracle:thin:@//db-host:1521/ORCLPDB1}

echo "== 1. register the /mcp endpoint =="
$ORDS config set --global feature.mcp true

echo "== 2. the global MCP JWT profile =="
# Without all three of these, /mcp answers 503 "ORDS MCP requires a global
# JWT Profile." There is no database-side object to create: despite
# ORDS_METADATA.OAUTH.CREATE_JWT_PROFILE existing for REST APIs, the MCP
# profile is configuration, not a row.
$ORDS config set --global mcp.security.jwt.profile.issuer   "$IDP_ISSUER"
$ORDS config set --global mcp.security.jwt.profile.audience "$MCP_AUDIENCE"
$ORDS config set --global mcp.security.jwt.profile.jwk.url  "$JWKS_URL"

# Optional. Setting it switches pool authorisation from SCOPE mode to ROLE
# mode, and pools then need mcp.role instead of mcp.scope.
# $ORDS config set --global mcp.security.jwt.profile.role.claim.name /roles

# Optional. If omitted, MCP clients must be told the authorization server
# location themselves.
# $ORDS config set --global mcp.security.jwt.profile.authorization.server.url "$IDP_ISSUER"

echo "== 3. a dedicated connection pool for MCP =="
# Deliberately NOT the pool that serves APEX. A separate pool means the MCP
# run-SQL tool executes as a database user you chose for it, and the blast
# radius of the whole feature is that user's grants.
$ORDS config --db-pool "$POOL" set db.username       "$DB_USER"
$ORDS config --db-pool "$POOL" set db.connectionType customurl
$ORDS config --db-pool "$POOL" set db.customURL      "$DB_URL"
$ORDS config --db-pool "$POOL" set db.description    "Product catalogue, read only"
# db.description is surfaced to the AI client in the database_list tool, so it
# is worth writing for a reader rather than for yourself.

read -rsp "password for $DB_USER: " PW; echo
printf '%s' "$PW" | $ORDS config --db-pool "$POOL" secret --password-stdin db.password

echo "== 4. who may reach this pool (SCOPE mode) =="
$ORDS config --db-pool "$POOL" set mcp.scope urn:oracle:dbtools:ords:mcpserver:all

echo "== 5. prove the pool connects before restarting =="
$ORDS config --db-pool "$POOL" verify

cat <<'MSG'

Now restart ORDS. Then:
  curl -i http://<host>:8080/.well-known/oauth-protected-resource/mcp
  curl -i http://<host>:8080/mcp        # 503 until the JWT profile is complete

Note the path: /mcp at the server root. NOT /ords/mcp - that returns 404.
MSG
