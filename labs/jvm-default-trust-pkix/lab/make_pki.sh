#!/usr/bin/env bash
# v1.1 - 2026-11-11 - Codex: LAB_STOREPASS must be 6+ chars (keytool minimum).
# v1.0 - 2026-11-11 - Lab PKI: private root CA -> issuing CA -> backend leaf (CN=localhost), plus two trust stores.
#
# Re-runnable: wipes and rebuilds lab/pki and the two .jks files each time.
# Needs: openssl, keytool (JAVA8_HOME/bin), env LAB_STOREPASS (lab-only trust store password, any value).
set -euo pipefail
export LC_ALL=C

: "${JAVA8_HOME:?set JAVA8_HOME to a JDK 8 home}"
: "${LAB_STOREPASS:?set LAB_STOREPASS (lab-only password for the .jks files)}"
(( ${#LAB_STOREPASS} >= 6 )) || { echo "LAB_STOREPASS must be at least 6 characters" >&2; exit 2; }
KT="$JAVA8_HOME/bin/keytool"
cd "$(dirname "$0")"
rm -rf pki app-trust.jks root-only.jks
mkdir -p pki


# Root CA (self-signed)
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 60 \
  -keyout pki/root.key -out pki/root.pem -subj "/CN=Example Corp Root CA/O=Example Corp" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null

# Issuing (intermediate) CA, signed by the root
openssl req -newkey rsa:2048 -nodes -keyout pki/issuing.key -out pki/issuing.csr \
  -subj "/CN=Example Corp Issuing CA 1/O=Example Corp" 2>/dev/null
printf '%s\n' "basicConstraints=critical,CA:TRUE,pathlen:0" "keyUsage=critical,keyCertSign,cRLSign" \
  "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid" > pki/issuing.ext
openssl x509 -req -in pki/issuing.csr -CA pki/root.pem -CAkey pki/root.key -CAcreateserial \
  -days 60 -sha256 -extfile pki/issuing.ext -out pki/issuing.pem 2>/dev/null

# Backend server leaf, signed by the issuing CA. The lab server sends only this leaf (no intermediate).
openssl req -newkey rsa:2048 -nodes -keyout pki/backend.key -out pki/backend.csr \
  -subj "/CN=localhost/O=Example Corp" 2>/dev/null
printf '%s\n' "basicConstraints=CA:FALSE" "keyUsage=critical,digitalSignature,keyEncipherment" \
  "extendedKeyUsage=serverAuth" "subjectAltName=DNS:localhost" "authorityKeyIdentifier=keyid" > pki/backend.ext
openssl x509 -req -in pki/backend.csr -CA pki/issuing.pem -CAkey pki/issuing.key -CAcreateserial \
  -days 60 -sha256 -extfile pki/backend.ext -out pki/backend.pem 2>/dev/null

# Application trust store (what WebLogic's Custom Trust keystore holds): root + issuing CA
"$KT" -importcert -noprompt -alias example-root-ca      -file pki/root.pem    -keystore app-trust.jks -storetype JKS -storepass:env LAB_STOREPASS >/dev/null
"$KT" -importcert -noprompt -alias example-issuing-ca-1 -file pki/issuing.pem -keystore app-trust.jks -storetype JKS -storepass:env LAB_STOREPASS >/dev/null
# Root only (to show why the intermediate is needed when the server sends only its leaf)
"$KT" -importcert -noprompt -alias example-root-ca      -file pki/root.pem    -keystore root-only.jks -storetype JKS -storepass:env LAB_STOREPASS >/dev/null

chmod 600 pki/*.key
echo "lab PKI ready: $(pwd)/pki, app-trust.jks (2 CAs), root-only.jks (1 CA)"
