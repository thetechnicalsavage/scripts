#!/bin/bash
# Exercise the ORDS MCP endpoint over Streamable HTTP (JSON-RPC 2.0).
# Run on the ORDS host. Expects the bearer token at /tmp/token.jwt
set -u
TOK=$(cat /tmp/token.jwt)
URL="http://localhost:8080/mcp"
H_AUTH="Authorization: Bearer $TOK"
H_CT="Content-Type: application/json"
H_ACC="Accept: application/json, text/event-stream"
H_VER="MCP-Protocol-Version: 2025-06-18"

rpc () {  # $1 = label, $2 = json body, $3 = optional session id
  local sid_hdr=()
  [ -n "${3:-}" ] && sid_hdr=(-H "Mcp-Session-Id: $3")
  echo "===== $1 ====="
  echo "--> $2"
  curl -s -D /tmp/mcp_hdrs.txt -m 30 -X POST "$URL" \
       -H "$H_AUTH" -H "$H_CT" -H "$H_ACC" -H "$H_VER" "${sid_hdr[@]}" \
       -d "$2"
  echo
  echo "--- response headers ---"
  grep -iE "^HTTP/|^mcp-session-id|^content-type" /tmp/mcp_hdrs.txt
  echo
}

echo "##### 0. OAuth protected-resource discovery #####"
curl -s -i -m 15 "http://localhost:8080/.well-known/oauth-protected-resource/mcp" | head -25
echo
echo "##### 0b. unauthenticated call, for contrast #####"
curl -s -m 15 -X POST "$URL" -H "$H_CT" -H "$H_ACC" \
  -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}' | head -5
echo; echo

rpc "1. initialize" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ords-mcp-probe","version":"1.0"}}}'
SID=$(grep -i "^mcp-session-id" /tmp/mcp_hdrs.txt | tr -d '\r' | cut -d' ' -f2)
echo "### session id captured: ${SID:-<none>}"
echo

curl -s -m 15 -X POST "$URL" -H "$H_AUTH" -H "$H_CT" -H "$H_ACC" -H "$H_VER" \
  ${SID:+-H "Mcp-Session-Id: $SID"} \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
echo "### sent notifications/initialized"; echo

rpc "2. tools/list" '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' "$SID"
rpc "3. resources/list" '{"jsonrpc":"2.0","id":3,"method":"resources/list","params":{}}' "$SID"
rpc "4. prompts/list" '{"jsonrpc":"2.0","id":4,"method":"prompts/list","params":{}}' "$SID"
