# ORDS MCP server on a non-Autonomous database

An AI client talking to your own Oracle database over the Model Context Protocol,
without writing an MCP server yourself.

**Two global settings are needed, not one**, and the second is what stops people:

| # | Setting | Without it |
|---|---|---|
| 1 | `feature.mcp = true` | `/mcp` returns **404**; the path is never registered |
| 2 | A global MCP **JWT profile** (3 settings) | `/mcp` returns **503 ORDS MCP requires a global JWT Profile.** |

Both are global, so both need an ORDS restart. The endpoint is **`/mcp` at the server
root** — `/ords/mcp` is a 404 forever.

**ORDS MCP is standalone-only.** On Tomcat or WebLogic the `/mcp` path is never
registered.

## The scripts

| | |
|---|---|
| [`01_enable_ords_mcp.sh`](01_enable_ords_mcp.sh) | Every ORDS setting, commented with why. Idempotent |
| [`02_create_mcp_account.sql`](02_create_mcp_account.sql) | The database account, which *is* the security boundary |
| [`04_probe_mcp.sh`](04_probe_mcp.sh) | **READ-ONLY.** Full JSON-RPC handshake and capability enumeration |
| [`05_mint_demo_token.py`](05_mint_demo_token.py) | Mints an RS256 token satisfying the profile. **Demo only** |
| [`06_generate_demo_signing_key.py`](06_generate_demo_signing_key.py) | RSA keypair + JWKS for a demo issuer. **Demo only** |
| [`jwks.example.json`](jwks.example.json) | The shape of the JWKS ORDS fetches |

## The one thing to take away

`sql_run` executes arbitrary SQL **as the pool's database user**. ORDS applies no
statement filtering, no allow-list and no read-only mode. The tool declares
`destructiveHint: true` and means it.

So the account is the design. Grant it what the AI may see and nothing else, and
point the pool at it rather than at the pool serving your applications.

## The trap worth knowing before you build this

A textbook least-privilege account — `CREATE SESSION` plus `SELECT` on tables in
another schema, owning nothing — makes the feature useless in a way that does not
look like a failure:

```
Basic Schema Objects Listing:
OWNER,OBJECT_TYPE,OBJECT_NAME
                                <- empty, and isError is false
```

`schema_information` reports what the connected user **owns**, not what it can read.
The model sees an empty database and cannot discover anything to query.

**Fix: give the MCP account its own views.**

```sql
grant create view to MCP_READER;
create or replace view MCP_READER.V_PRODUCT_OFFERING as
  select * from CATALOGUE_OWNER.PRODUCT_OFFERING;
comment on table MCP_READER.V_PRODUCT_OFFERING is
  'Product catalogue. Read-only view exposed to the ORDS MCP server.';
```

The boundary is unchanged, the model can now discover the schema, and you get a
natural place for column comments and for VPD if you want per-identity rows.

## Demo-only, and not a production pattern

`05` and `06` exist so the JWT profile could be exercised **without an identity
provider**. A self-signed JWKS is not how you run this. Keycloak or OCI IAM is the
real answer, and the JWKS URL must be reachable from the ORDS process itself.

## What was tested

ORDS 26.2.3.r2371104 standalone (official container image) against Oracle AI Database
26ai EE 23.26.1.0.0, on 2026-09-26, in **scope mode**.

Role mode (`mcp.security.jwt.profile.role.claim.name` plus per-pool `mcp.role`) is
documented but was **not** exercised here. Nothing in these scripts should be read as
a claim about it.
