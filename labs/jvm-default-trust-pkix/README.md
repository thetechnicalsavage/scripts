<!-- v1.1 | 2026-11-11 | Publication review: WORK_DIR must be a new folder outside the JDK; cleanup removes only what the lab made; server on loopback. -->
<!-- v1.0 | 2026-11-11 | Lab for the Axis2 PKIX post: the JVM default trust store versus the application trust store. -->
# JVM default trust store vs the application trust store

Reproduces the trust side of
[Axis2 PKIX path building failed in WebLogic after a JDK patch](https://thetechnicalsavage.com/blog/axis2-pkix-path-building-failed-in-weblogic-after-a-jdk-patch/)
on your own machine, in a few minutes. Built and run on Temurin 1.8.0_512 with OpenSSL 3.

The lab does not run WebLogic or Axis2. `CallBackend.java` makes one HTTPS GET over a socket from
`SSLSocketFactory.getDefault()`, which is the path Apache Commons HttpClient 3 takes by default, so it reads trust
from the same place the Axis2 call in the post did: `javax.net.ssl.trustStore`, else `jssecacerts`, else `cacerts`.

| File | What it does | Changes anything |
|---|---|---|
| [`lab/make_pki.sh`](lab/make_pki.sh) | A private root CA, an issuing CA and a `localhost` leaf, all named Example Corp; then `app-trust.jks` (root + issuing CA) and `root-only.jks` | Rebuilds `lab/pki/` and the two `.jks` files |
| [`lab/CallBackend.java`](lab/CallBackend.java) | One HTTPS GET through the JVM default socket factory, host name checked | No |
| [`lab/run_captures.sh`](lab/run_captures.sh) | Starts `openssl s_server` on 127.0.0.1:9443 sending only its leaf, runs every scenario, writes the transcripts to `src/captures/` | Copies your JDK 8 into `$WORK_DIR` for the patch simulation; your JDK itself is never written to |

## Run it

```bash
export JAVA8_HOME=/path/to/jdk8 WORK_DIR="$(mktemp -d)"     # a new, empty folder outside the JDK
export LAB_STOREPASS='any lab-only value, 6 characters or more'
export CACERTS_PASS='your JDK cacerts store password'
lab/make_pki.sh
"$JAVA8_HOME/bin/javac" -source 8 -target 8 lab/CallBackend.java
lab/run_captures.sh
```

The copy in `$WORK_DIR` gets its own `cacerts` as a regular file before anything is imported, so a JDK whose
`cacerts` is a link into the system store is never changed. Passwords are read from the environment and passed to `keytool` with `-storepass:env`, so they never appear on a
command line. The CA keys are lab-only and expire after 60 days.

## What each scenario shows

| Transcript | Result |
|---|---|
| `01_error.txt` | No trust settings: `PKIX path building failed` |
| `02_debug_default.txt` | `-Djavax.net.debug=ssl:trustmanager`: `trustStore is: .../cacerts` |
| `03_keytool.txt` | `cacerts` holds no Example Corp CA; `app-trust.jks` holds the root and the issuing CA |
| `04_chain.txt` | The server sends only its leaf, and a root-only trust store still fails |
| `05_fix.txt` | `-Djavax.net.ssl.trustStore` pointing at `app-trust.jks`: HTTP 200 |
| `06_jdk_patch.txt` | The same JDK unpacked twice: CAs imported by hand into one `cacerts` are missing from the other |

One more check worth running: point `-Djavax.net.ssl.trustStore` at a file that does not exist. On 1.8.0_512 the JVM
logs `Inaccessible trust store:` and quietly falls back to `cacerts`, so a typo in the path looks like a fix that
did nothing.

Clean up only what the lab made:

```bash
(cd lab && rm -rf pki app-trust.jks root-only.jks CallBackend.class)
rm -f src/captures/0[1-6]_*.txt
rm -rf "$WORK_DIR/jdk_before_patch" "$WORK_DIR/jdk_after_patch" && rmdir "$WORK_DIR"
```
