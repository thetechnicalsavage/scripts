#!/usr/bin/env python3
# v1.2 2026-11-11 - Codex r2: an answer with an embedded newline is refused; a symlinked parent is judged by its target;
#                    a failing permission check refuses.
# v1.1 2026-11-11 - Codex r1: the work folder is mode 700 when the copy lands; a malformed work-folder answer is never
#                    used or removed; a world-writable parent without the sticky bit is refused; a tarball path with a
#                    backslash still verifies; a failed unpack leaves nothing.
# v1.0 2026-11-11 - offline tests of the JDK staging in 03_stage_on_nodes.sh (no WebLogic, no real ssh).
#   A stand-in ssh runs each remote command locally and records it, so the test sees exactly what reaches a node:
#   the tarball never goes to a shared /tmp path, a swapped tarball is refused by its checksum, and the private work
#   folder beside JAVA_HOME_NEW is gone afterwards.
# Usage: python3 tests/test_stage_jdk.py      (exit 0 when all checks pass)
import glob
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(os.path.dirname(HERE), "03_stage_on_nodes.sh")
failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print("%-4s %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail.strip()[:300]) if detail and not ok else ""))


def make_jdk_tarball(path, top, marker, java=True):
    src = tempfile.mkdtemp()
    os.makedirs(os.path.join(src, top, "bin"))
    if java:
        exe = os.path.join(src, top, "bin", "java")
        with open(exe, "w") as fh:
            fh.write('#!/bin/sh\necho \'openjdk version "%s"\' >&2\n' % marker)
        os.chmod(exe, 0o755)
    with tarfile.open(path, "w:gz") as tf:
        tf.add(os.path.join(src, top), arcname=top)
    shutil.rmtree(src)


def run(work, tarball, **fake):
    """Run 03 --jdk-only against one stand-in node; return (exit code, output, remote commands, product dir, modes)."""
    product, real = os.path.join(work, "product"), os.path.join(work, "product_real")
    if os.path.islink(product):
        os.unlink(product)
    for d in (product, real):
        if os.path.isdir(d):
            shutil.rmtree(d)
    if fake.get("parent_symlink"):
        os.makedirs(real)
        os.chmod(real, 0o755)
        os.symlink(real, product)
    else:
        os.makedirs(product)
    if fake.get("parent_mode"):
        os.chmod(product, fake["parent_mode"])
    calls, modes = os.path.join(work, "ssh_calls.log"), os.path.join(work, "modes.log")
    for f in (calls, modes):
        if os.path.exists(f):
            os.unlink(f)
    env_file = os.path.join(work, "t.env")
    with open(env_file, "w") as fh:
        fh.write("DOMAIN_NAME=domainT\nNODES=nodeT\nSSH_USER=oracle\n")
        fh.write("ORACLE_HOME=%s/fmw\nSTAGE_DIR=%s/stage\nCYCLE=T1\n" % (product, work))
        fh.write("JDK_TARBALL='%s'\n" % tarball)          # single quotes: the env file is sourced by bash
        fh.write("JAVA_HOME_CURRENT=%s/jdk_old\nJAVA_HOME_NEW=%s/jdk_new\n" % (product, product))
    env = dict(os.environ, PATH=os.path.join(work, "bin") + os.pathsep + os.environ["PATH"],
               FAKE_SSH_LOG=calls, FAKE_SSH_MODES=modes, FAKE_SSH_SWAP=fake.get("swap", ""),
               FAKE_SSH_MKTEMP_REPLY=fake.get("mktemp_reply", ""), FAKE_SSH_MKDIR=fake.get("mkdir", ""),
               FAKE_STAT_FAIL=fake.get("stat_fail", ""))
    env.pop("CACERTS_PASS", None)                      # cacerts comparison is skipped without it
    p = subprocess.run(["bash", STAGE, env_file, "--jdk-only"], env=env, capture_output=True, text=True, timeout=120)
    recorded = open(calls).read().splitlines() if os.path.exists(calls) else []
    seen = open(modes).read().split() if os.path.exists(modes) else []
    return p.returncode, p.stdout + p.stderr, recorded, product, seen


def leftovers(product):
    return glob.glob(os.path.join(product, ".zdt_jdk.*"))


work = tempfile.mkdtemp()
os.makedirs(os.path.join(work, "bin"))
# stand-in ssh: drop options and the user@host argument, record the remote command, run it locally.
#   FAKE_SSH_SWAP         a copy into a jdk tarball path receives that file instead (an attacker's swap)
#   FAKE_SSH_MKTEMP_REPLY the remote script that creates the work folder answers this text instead
#   FAKE_SSH_MKDIR        ... after creating this folder, so a reply through it resolves
#   FAKE_SSH_MODES        the mode of the folder each copy lands in is recorded here
with open(os.path.join(work, "bin", "ssh"), "w") as fh:
    fh.write(r"""#!/bin/bash
while [ "$1" = -o ]; do shift 2; done
shift
cmd="$*"
printf '%s\n' "$cmd" >> "$FAKE_SSH_LOG"
if [[ "$cmd" == "cat > "* ]]; then
  eval "dest=${cmd#cat > }"
  stat -c %a "$(dirname "$dest")" >> "$FAKE_SSH_MODES"
  if [ -n "$FAKE_SSH_SWAP" ] && [[ "$dest" == *jdk* ]]; then
    cat > /dev/null
    exec bash -c "$cmd" < "$FAKE_SSH_SWAP"
  fi
  exec bash -c "$cmd"
fi
if [[ "$cmd" == "bash -s"* ]]; then
  script=$(cat)
  if [ -n "$FAKE_SSH_MKTEMP_REPLY" ] && [[ "$script" == *"mktemp -d"* ]]; then
    [ -n "$FAKE_SSH_MKDIR" ] && mkdir -p "$FAKE_SSH_MKDIR"
    printf '%s\n' "$FAKE_SSH_MKTEMP_REPLY"; exit 0
  fi
  exec bash -c "$cmd" <<< "$script"
fi
exec bash -c "$cmd"
""")
os.chmod(os.path.join(work, "bin", "ssh"), 0o755)
# stand-in stat: with FAKE_STAT_FAIL set, the node's permission check (stat -L) fails
with open(os.path.join(work, "bin", "stat"), "w") as fh:
    fh.write('#!/bin/bash\nif [ -n "$FAKE_STAT_FAIL" ] && [ "$1" = -L ]; then echo "stat: injected" >&2; exit 1; fi\n'
             'exec /usr/bin/stat "$@"\n')
os.chmod(os.path.join(work, "bin", "stat"), 0o755)
good = os.path.join(work, "jdk.tar.gz")
make_jdk_tarball(good, "jdk-good", "good")
make_jdk_tarball(os.path.join(work, "evil.tar.gz"), "jdk-evil", "evil")
make_jdk_tarball(os.path.join(work, "nojava.tar.gz"), "jdk-broken", "broken", java=False)

code, out, recorded, product, modes = run(work, good)
check("stage: exit 0", code == 0, out)
check("stage: the JDK lands at JAVA_HOME_NEW", os.path.exists(os.path.join(product, "jdk_new", "bin", "java")), out)
copies = [c for c in recorded if c.startswith("cat > ")]
check("stage: the tarball is copied once", len(copies) == 1, "\n".join(recorded))
check("stage: the tarball never goes to a shared /tmp path",
      all(os.path.dirname(c[len("cat > "):].strip("'")) != "/tmp" for c in copies), "\n".join(copies))
check("stage: the copy goes into a private folder beside JAVA_HOME_NEW",
      all(".zdt_jdk." in c and product in c for c in copies), "\n".join(copies))
check("stage: that folder is mode 700 when the copy lands", modes == ["700"], str(modes))
check("stage: the work folder is removed", leftovers(product) == [], str(os.listdir(product)))

code, out, recorded, product, _ = run(work, good, swap=os.path.join(work, "evil.tar.gz"))
check("swap: a different tarball on the node fails the run", code != 0, out)
check("swap: nothing is installed at JAVA_HOME_NEW", not os.path.exists(os.path.join(product, "jdk_new")), out)
check("swap: the work folder is removed", leftovers(product) == [], str(os.listdir(product)))

code, out, recorded, product, _ = run(work, os.path.join(work, "nojava.tar.gz"))
check("no bin/java: the run fails", code != 0, out)
check("no bin/java: nothing installed, work folder removed",
      not os.path.exists(os.path.join(product, "jdk_new")) and leftovers(product) == [], str(os.listdir(product)))

# a reply that starts like a generated folder but resolves outside it, to a folder with a file worth keeping
victim = os.path.join(work, "victim")
os.makedirs(victim)
open(os.path.join(victim, "keep.txt"), "w").close()
fake_dir = os.path.join(work, "product", ".zdt_jdk.AbC123")
code, out, recorded, product, _ = run(work, good, mkdir=fake_dir, mktemp_reply=fake_dir + "/../../victim")
check("malformed answer: the run fails", code != 0, out)
check("malformed answer: nothing copied through it",
      not any(c.startswith("cat > ") for c in recorded), "\n".join(recorded))
check("malformed answer: the folder it points at is untouched",
      os.path.exists(os.path.join(victim, "keep.txt")) and os.listdir(victim) == ["keep.txt"], str(os.listdir(work)))

code, out, recorded, product, _ = run(work, good, parent_mode=0o777)
check("world-writable parent without the sticky bit: refused before any copy",
      code != 0 and not any(c.startswith("cat > ") for c in recorded) and leftovers(product) == [], out)
code, out, recorded, product, _ = run(work, good, parent_mode=0o1777)
check("world-writable parent with the sticky bit: allowed", code == 0, out)

# a reply that equals parent + newline + generated name: dirname/basename checks are fooled by it
twin = os.path.join(work, "product\n", ".zdt_jdk.AbC123")
code, out, recorded, product, _ = run(work, good, mkdir=twin, mktemp_reply=twin)
check("newline in the answer: the run fails and nothing is copied",
      code != 0 and not any(c.startswith("cat > ") for c in recorded), out)
check("newline in the answer: the folder it names is left alone",
      os.path.isdir(twin) and os.listdir(twin) == [], str(os.listdir(os.path.dirname(twin))))

code, out, recorded, product, _ = run(work, good, parent_symlink=True)
check("symlinked parent (target mode 755): allowed and installed",
      code == 0 and os.path.exists(os.path.join(product, "jdk_new", "bin", "java")), out)

code, out, recorded, product, _ = run(work, good, stat_fail="1")
check("permission check fails: refused before any copy",
      code != 0 and not any(c.startswith("cat > ") for c in recorded) and leftovers(product) == [], out)

odd = os.path.join(work, "jdk\\odd.tar.gz")  # GNU sha256sum escapes such a name in its output
shutil.copy(good, odd)
code, out, recorded, product, _ = run(work, odd)
check("backslash in the tarball path: still verifies and installs",
      code == 0 and os.path.exists(os.path.join(product, "jdk_new", "bin", "java")), out)

shutil.rmtree(work)
sys.exit(1 if failed else 0)
