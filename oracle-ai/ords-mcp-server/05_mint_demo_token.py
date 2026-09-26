#!/usr/bin/env python3
"""Mint an RS256 JWT that satisfies the ORDS global MCP JWT profile.

Demo issuer only: the signing key was generated locally and the JWKS is served
from a container on the same docker network. In production this is your IdP
(Keycloak, OCI IAM/IDCS, Entra) and you never hold the signing key yourself.
"""
import sys, time, json, jwt

KEY   = sys.argv[1] if len(sys.argv) > 1 else "mcp_signing_key.pem"
ISS   = "https://mcpdemo-idp.local/"
AUD   = "http://localhost:8080/mcp"
KID   = "ords-mcp-demo-key"
SCOPE = "urn:oracle:dbtools:ords:mcpserver:all"
SUB   = sys.argv[2] if len(sys.argv) > 2 else "mcp-demo-user"
TTL   = int(sys.argv[3]) if len(sys.argv) > 3 else 3600

now = int(time.time())
claims = {
    "iss": ISS,
    "aud": AUD,
    "sub": SUB,
    "iat": now,
    "nbf": now,
    "exp": now + TTL,
    "scope": SCOPE,              # OAuth2 space-delimited form
    "scp":  [SCOPE],             # array form, some validators prefer this
    "roles": ["ORDS_MCP_DEMO"],  # only used if role mode is enabled
    "client_id": "mcp-demo-client",
}
tok = jwt.encode(claims, open(KEY).read(), algorithm="RS256",
                 headers={"kid": KID, "typ": "JWT"})
print(tok)
