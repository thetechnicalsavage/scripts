#!/usr/bin/env bash
# v1.3 - 2026-11-11 - Publication review: OLD_JDK's cacerts becomes an independent regular file before the import (a
#        symlinked cacerts, as on distribution JDKs, would otherwise send the import into the system store); WORK_DIR
#        must lie outside the JDK; the lab server listens on 127.0.0.1 only.
# v1.2 - 2026-11-11 - Codex: CACERTS_PASS from env; delete only generated captures 01-06 (07 is hand-made).
# v1.1 - 2026-11-11 - Lab port 9443.
# v1.0 - 2026-11-11 - Runs every lab scenario and saves the terminal transcripts used for the screenshots.
#
# Each transcript line "$ <cmd>" is the exact command run (with $JAVA_HOME, $LAB, $OLD_JDK, $NEW_JDK left as
# variables); in the output the real paths are shortened back to those variable names. Nothing else is edited.
#
# Needs: JAVA8_HOME (JDK 8), CACERTS_PASS (JDK cacerts password), LAB_STOREPASS (same value used by make_pki.sh), WORK_DIR (scratch dir for a JDK copy),
#        openssl, port 9443 free. Re-runnable: overwrites ../src/captures/*.txt.
set -uo pipefail
export LC_ALL=C TZ=UTC

: "${JAVA8_HOME:?set JAVA8_HOME}"; : "${CACERTS_PASS:?set CACERTS_PASS}"; : "${LAB_STOREPASS:?set LAB_STOREPASS}"; : "${WORK_DIR:?set WORK_DIR (scratch)}"
LAB="$(cd "$(dirname "$0")" && pwd)"
CAP="$LAB/../src/captures"
mkdir -p "$CAP" "$WORK_DIR"
export JAVA_HOME="$JAVA8_HOME" LAB
export PATH="$JAVA_HOME/bin:$PATH"
export CACERTS_PASS                    # passed to keytool via -storepass:env, never on the command line

# "Simulated JDK patch": the same JDK build unpacked twice. OLD_JDK gets the private CAs imported into its cacerts
# (what someone did by hand on the old JDK); NEW_JDK is the stock unpacked JDK.
export OLD_JDK="$WORK_DIR/jdk_before_patch" NEW_JDK="$WORK_DIR/jdk_after_patch"
JDK_REAL="$(readlink -f "$JAVA8_HOME")"; WORK_REAL="$(readlink -f "$WORK_DIR")"
case "$WORK_REAL/" in "$JDK_REAL"/*) echo "WORK_DIR must be outside JAVA8_HOME" >&2; exit 1 ;; esac
rm -rf "$OLD_JDK" "$NEW_JDK"; cp -a "$JAVA8_HOME" "$OLD_JDK" || { echo "copy of JDK failed" >&2; exit 1; }
ln -s "$JAVA8_HOME" "$NEW_JDK"   # stock JDK, untouched cacerts
# cp -a keeps symlinks. If cacerts, or a directory above it, links into the system (distribution JDKs link cacerts to
# /etc), the import below would change that shared store. Make OLD_JDK's security directory and cacerts its own.
SEC="$OLD_JDK/jre/lib/security"
case "$(readlink -f "$SEC")/" in "$(readlink -f "$OLD_JDK")"/*) ;; *) echo "$SEC resolves outside OLD_JDK; refusing" >&2; exit 1 ;; esac
SRC_CACERTS="$(readlink -f "$JAVA8_HOME/jre/lib/security/cacerts")"
rm -f "$SEC/cacerts" && cp "$SRC_CACERTS" "$SEC/cacerts" || { echo "could not make an independent cacerts" >&2; exit 1; }
[ -f "$SEC/cacerts" ] && [ ! -L "$SEC/cacerts" ] || { echo "OLD_JDK cacerts is not a regular file" >&2; exit 1; }
for a in root issuing; do
  "$OLD_JDK/bin/keytool" -importcert -noprompt -alias "example-$a-ca" -file "$LAB/pki/$a.pem" \
    -keystore "$OLD_JDK/jre/lib/security/cacerts" -storepass:env CACERTS_PASS >/dev/null \
    || { echo "import into OLD_JDK cacerts failed" >&2; exit 1; }
done

# Lab backend: HTTPS on 9443 with the leaf only (no intermediate), like the real backend.
openssl s_server -accept 127.0.0.1:9443 -cert "$LAB/pki/backend.pem" -key "$LAB/pki/backend.key" -www -quiet >/dev/null 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 1
kill -0 $SRV 2>/dev/null || { echo "lab server did not start (port 9443 busy?)" >&2; exit 1; }

shorten() { sed -e "s#$OLD_JDK#\$OLD_JDK#g" -e "s#$NEW_JDK#\$NEW_JDK#g" -e "s#$JAVA_HOME#\$JAVA_HOME#g" -e "s#$LAB#\$LAB#g"; }
# cap <file> <command>: append "$ command" and its shortened output to the transcript.
cap() { local f="$CAP/$1"; shift; printf '$ %s\n' "$*" >> "$f"; ( cd "$LAB" && eval "$*" ) 2>&1 | shorten >> "$f"; printf '\n' >> "$f"; }

rm -f "$CAP"/0[1-6]_*.txt               # 07_incident_sanitized.txt is hand-made, keep it

# 1. The error
cap 01_error.txt 'java -version 2>&1 | head -2'
cap 01_error.txt 'java CallBackend https://localhost:9443/'

# 2. Which trust store does the JVM default context load?
cap 02_debug_default.txt "java -Djavax.net.debug=ssl:trustmanager CallBackend https://localhost:9443/ 2>&1 | grep -o -E 'trustStore is: .*|Reloaded [0-9]+ trust certs|PKIX path building failed' | uniq"

# 3. cacerts vs the application trust store
cap 03_keytool.txt 'keytool -list -keystore $JAVA_HOME/jre/lib/security/cacerts -storepass:env CACERTS_PASS | grep -c trustedCertEntry'
cap 03_keytool.txt 'keytool -list -v -keystore $JAVA_HOME/jre/lib/security/cacerts -storepass:env CACERTS_PASS | grep -c "Owner: .*Example Corp"'
cap 03_keytool.txt "keytool -list -v -keystore \$LAB/app-trust.jks -storepass:env LAB_STOREPASS | grep -E '^(Alias name|Owner):'"

# 4. The backend sends only its leaf, so the root CA alone is not enough
cap 04_chain.txt "openssl s_client -connect localhost:9443 -showcerts </dev/null 2>/dev/null | grep -E '^ *[0-9]+ s:|^ +i:'"
cap 04_chain.txt 'java -Djavax.net.ssl.trustStore=$LAB/root-only.jks CallBackend https://localhost:9443/'

# 5. The fix: point the JVM default trust store at a store that has the private CAs
cap 05_fix.txt 'java -Djavax.net.ssl.trustStore=$LAB/app-trust.jks -Djavax.net.ssl.trustStoreType=JKS CallBackend https://localhost:9443/'
cap 05_fix.txt "java -Djavax.net.ssl.trustStore=\$LAB/app-trust.jks -Djavax.net.ssl.trustStoreType=JKS -Djavax.net.debug=ssl:trustmanager CallBackend https://localhost:9443/ 2>&1 | grep -o -E 'trustStore is: .*|Reloaded [0-9]+ trust certs' | uniq"

# 6. Why it broke "suddenly": a new JDK comes with a stock cacerts
cap 06_jdk_patch.txt 'stat -c "%y  %n" $OLD_JDK/jre/lib/security/cacerts $NEW_JDK/jre/lib/security/cacerts'
cap 06_jdk_patch.txt '$OLD_JDK/bin/java CallBackend https://localhost:9443/'
cap 06_jdk_patch.txt '$NEW_JDK/bin/java CallBackend https://localhost:9443/'

echo "captures written to $CAP:"; ls -1 "$CAP"
