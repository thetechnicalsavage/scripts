#!/usr/bin/env python3
# v1.5 2026-10-15 - lead Node Manager properties supplied to every hook run; missing file and 'ListenPort = n' form.
# v1.4 2026-10-15 - other domain on the lead domain's Node Manager port -> refused (exit 2).
# v1.3 2026-10-15 - leftover process looks like a WebLogic server and runs from the real path behind a symlinked
#                    domain home.
# v1.2 2026-10-15 - node process check after stop (leftover java process -> fail); Node Manager fallback when the
#                    Administration Server is unreachable.
# v1.1 2026-10-15 - stop verification (admin still answering, managed server not stopping) and start idempotency.
# v1.0 2026-10-15 - offline tests of the shared-home hooks (no WebLogic needed).
#   Part 1: the hook shell scripts with a stand-in wlst.sh - stop, start, revert, no rows for this node, WLST failure,
#           missing settings file; entry scripts run without execute permission on the body (as after jar extraction).
#   Part 2: the WLST helper zdt_other_domains.py, converted with lib2to3 and run against a mock domain - graceful
#           shutdown arguments, Administration Server last, Node Manager stopped; start order NM -> admin -> managed.
# Usage: python3 tests/test_hooks.py      (exit 0 when all checks pass)
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import warnings

warnings.filterwarnings("ignore")
from lib2to3 import refactor  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks", "src", "scripts", "unix")
TOOL = refactor.RefactoringTool(refactor.get_fixers_from_package("lib2to3.fixes"))
failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print("%-4s %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail.strip()[:300]) if detail and not ok else ""))


# ---------- part 1: hook shell scripts ----------
work = tempfile.mkdtemp()
hooks = os.path.join(work, "patching")                 # copy without execute bits, like a jar extraction
shutil.copytree(HOOKS, hooks)
for f in os.listdir(hooks):
    os.chmod(os.path.join(hooks, f), 0o644)
mw = os.path.join(work, "fmw")
os.makedirs(os.path.join(mw, "oracle_common/common/bin"))
dom = os.path.join(work, "domB")
os.makedirs(os.path.join(dom, "bin"))
os.makedirs(os.path.join(dom, "nodemanager"))
calls = os.path.join(work, "calls.log")
with open(os.path.join(mw, "oracle_common/common/bin/wlst.sh"), "w") as fh:
    fh.write('#!/bin/sh\necho "wlst $*" >> %s\n[ "$2" = nmcheck ] && exit 1\nexit ${FAKE_WLST_RC:-0}\n' % calls)
os.chmod(os.path.join(mw, "oracle_common/common/bin/wlst.sh"), 0o755)
with open(os.path.join(dom, "bin/startNodeManager.sh"), "w") as fh:
    fh.write('#!/bin/sh\necho "startNodeManager" >> %s\n' % calls)
os.chmod(os.path.join(dom, "bin/startNodeManager.sh"), 0o755)
host = socket.gethostname().split(".")[0]
cfg = os.path.join(work, "other_domains.csv")
with open(cfg, "w") as fh:
    fh.write("# node,domain,...\n%s,domainB,%s,t3://h:7101,AdminB,/c,/k,5557,/nc,/nk,ssl,msB1;AdminB\n"
             "otherhost,domainC,/x,t3://o:7201,AdminC,/c,/k,5558,/nc,/nk,ssl,msC1\n" % (host, dom))


lead = os.path.join(work, "domLead")
os.makedirs(os.path.join(lead, "nodemanager"))
with open(os.path.join(lead, "nodemanager", "nodemanager.properties"), "w") as fh:
    fh.write("ListenPort = 5556\n")                     # spaces around '=' are valid in a properties file


def hook(script, extra):
    open(calls, "w").close()
    env = dict(os.environ)
    env.update({"mwHome": mw, "configFile": cfg, "shutdownTimeout": "900", "domainDir": lead})
    env.update(extra)
    p = subprocess.run(["sh", os.path.join(hooks, script)], env=env, capture_output=True, text=True, timeout=60)
    time.sleep(0.3)                                    # let the background Node Manager stand-in write its line
    return p.returncode, open(calls).read(), p.stdout + p.stderr


rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "false"})
check("stop hook: exit 0, WLST 'stop' with timeout 900", rc == 0 and " stop " in log and log.rstrip().endswith("900"), out + log)
check("stop hook: no Node Manager start", "startNodeManager" not in log, log)
rc, log, out = hook("zdt_start_other_domains.sh", {"isRevert": "false"})
check("start hook: Node Manager started, then WLST 'start'", rc == 0 and "startNodeManager" in log and " start " in log, out + log)
rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "TRUE"})
check("stop hook on revert runs 'start'", rc == 0 and " start " in log and " stop " not in log, out + log)
rc, log, out = hook("zdt_start_other_domains.sh", {"isRevert": "true"})
check("start hook on revert runs 'stop'", rc == 0 and " stop " in log and "startNodeManager" not in log, out + log)
rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "false", "FAKE_WLST_RC": "1"})
check("WLST failure -> exit 1 (the workflow rolls back)", rc == 1, out)
rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "false", "configFile": "/nonexistent.csv"})
check("missing settings file -> exit 2", rc == 2, out)
rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "false", "domainDir": "/nonexistent"})
check("lead Node Manager port unknown -> exit 2, nothing stopped", rc == 2 and log == "", out + log)
javadir = os.path.join(work, "jdk", "bin")
os.makedirs(javadir)
shutil.copy("/bin/sleep", os.path.join(javadir, "java"))
link = os.path.join(work, "domB_link")                 # the settings file names the domain home through a symlink
os.symlink(dom, link)
with open(cfg, "w") as fh:
    fh.write("%s,domainB,%s,t3s://h:7102,AdminB,/c,/k,5557,/nc,/nk,ssl,msB1;AdminB\n" % (host, link))
proc = subprocess.Popen(["java -Dweblogic.Name=msB1 weblogic.Server", "30"], executable=os.path.join(javadir, "java"), cwd=dom)
time.sleep(0.3)
rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "false", "stopWait": "0"})
check("stop hook: java process still running from the other domain's home -> exit 1", rc == 1 and "still running" in out, out)
proc.kill(); proc.wait()
rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "false", "stopWait": "0"})
check("stop hook: process gone -> exit 0", rc == 0 and "no process of the other domains left" in out, out)
leaddom = os.path.join(work, "domA")
os.makedirs(os.path.join(leaddom, "nodemanager"))
with open(os.path.join(leaddom, "nodemanager", "nodemanager.properties"), "w") as fh:
    fh.write("ListenPort=5557\n")
rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "false", "domainDir": leaddom})
check("other domain on the lead domain's Node Manager port -> exit 2, nothing stopped", rc == 2 and log == "", out + log)
with open(cfg, "w") as fh:
    fh.write("otherhost,domainC,/x,t3://o:7201,AdminC,/c,/k,5558,/nc,/nk,ssl,msC1\n")
rc, log, out = hook("zdt_stop_other_domains.sh", {"isRevert": "false"})
check("no other domain on this node -> exit 0, no WLST call", rc == 0 and log == "", out + log)
shutil.rmtree(work)


# ---------- part 2: WLST helper against a mock domain ----------
class Throwable(BaseException):        # java.lang.Throwable: not caught by 'except Exception', as on Jython 2.2
    pass


class WlstExit(Exception):             # Jython 2.2: SystemExit derives from Exception
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


java = types.ModuleType("java")
java_lang = types.ModuleType("java.lang")
java_lang.Throwable = Throwable
java.lang = java_lang
sys.modules["java"], sys.modules["java.lang"] = java, java_lang


def run_helper(args, states, nm_fail_first=0, admin="AdminB", stuck=()):
    ops, st, nm = [], dict(states), {"fails": nm_fail_first}
    ns = {"__name__": "__main__"}

    class Cmo:
        name = None
        def getState(self):
            return st[self.name]
    cmo = Cmo()

    def cd(path):
        cmo.name = path.rsplit("/", 1)[-1]

    def nmConnect(**kw):
        if nm["fails"] > 0:
            nm["fails"] -= 1
            raise Throwable("node manager not up")
        ops.append(("nmConnect", kw["host"], kw["port"], kw["domainName"], kw["nmType"]))

    def shutdown(*a):
        ops.append(("shutdown",) + a)
        if a[0] not in stuck:              # a 'stuck' server ignores the shutdown request
            st[a[0]] = "SHUTDOWN"
        if a[0] == admin:
            raise Throwable("connection closed")

    def connect(**kw):
        if st.get(admin) != "RUNNING":
            raise Throwable("connection refused")
        ops.append(("connect", kw["url"]))

    def start(name, typ, block=None):
        ops.append(("start", name, typ, block))
        st[name] = "RUNNING"

    def nmStart(name, home):
        ops.append(("nmStart", name))
        st[name] = "RUNNING"

    def wexit(defaultAnswer=None, exitcode=0):
        raise WlstExit(exitcode)

    def nmServerStatus(name):
        return st.get(name, "UNKNOWN")

    def nmKill(name):
        ops.append(("nmKill", name))
        st[name] = "SHUTDOWN"

    ns.update(dict(connect=connect, disconnect=lambda: None,
                   nmDisconnect=lambda: None, nmConnect=nmConnect, shutdown=shutdown, start=start, nmStart=nmStart,
                   stopNodeManager=lambda: ops.append(("stopNodeManager",)), domainRuntime=lambda: None,
                   nmServerStatus=nmServerStatus, nmKill=nmKill,
                   serverConfig=lambda: None, cd=cd, cmo=cmo, exit=wexit))
    src = str(TOOL.refactor_string(open(os.path.join(HOOKS, "zdt_other_domains.py")).read() + "\n", "h.py"))
    sl, time.sleep = time.sleep, (lambda s: None)
    old_argv, sys.argv = sys.argv, ["zdt_other_domains.py"] + args
    try:
        exec(compile(src, "zdt_other_domains.py", "exec"), ns)
        code = 0
    except WlstExit as e:
        code = e.code
    finally:
        time.sleep, sys.argv = sl, old_argv
    return code, ops


rowsf = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
rowsf.write("n1,domainB,/u01/domB,t3://n1:7101,AdminB,/c,/k,5557,/nc,/nk,ssl,msB1;AdminB;msB2\n")
rowsf.close()
code, ops = run_helper(["stop", rowsf.name, "900"], {"msB1": "RUNNING", "msB2": "SHUTDOWN", "AdminB": "RUNNING"})
shut = [o for o in ops if o[0] == "shutdown"]
check("helper stop: exit 0", code == 0, str(ops))
check("helper stop: graceful (ignoreSessions false, timeout, force false, block true)",
      bool(shut) and shut[0] == ("shutdown", "msB1", "Server", "false", 900, "false", "true"), str(shut))
check("helper stop: server already down is skipped", all(o[1] != "msB2" for o in shut), str(shut))
check("helper stop: Administration Server last, then Node Manager stopped",
      bool(shut) and shut[-1][1] == "AdminB" and ops[-1] == ("stopNodeManager",), str(ops))
code, ops = run_helper(["start", rowsf.name, "900"], {"msB1": "SHUTDOWN", "msB2": "SHUTDOWN", "AdminB": "SHUTDOWN"},
                       nm_fail_first=2)
order = [o[0] for o in ops if o[0] in ("nmConnect", "nmStart", "connect", "start")]
check("helper start: exit 0 after Node Manager retries", code == 0, str(ops))
check("helper start: order NM -> nmStart(admin) -> connect -> start managed",
      order[:3] == ["nmConnect", "nmStart", "connect"] and order.count("start") == 2, str(order))
code, ops = run_helper(["start", rowsf.name, "900"], {"msB1": "SHUTDOWN", "msB2": "SHUTDOWN", "AdminB": "SHUTDOWN"},
                       nm_fail_first=99)
check("helper start: Node Manager never answers -> exit 1", code == 1, str(ops))
code, ops = run_helper(["stop", rowsf.name, "900"], {"msB1": "RUNNING", "msB2": "SHUTDOWN", "AdminB": "SHUTDOWN"})
check("helper stop: Administration Server unreachable -> nmKill running servers, then Node Manager stopped",
      code == 0 and ("nmKill", "msB1") in ops and ("nmKill", "msB2") not in ops and ops[-1] == ("stopNodeManager",), str(ops))
code, ops = run_helper(["stop", rowsf.name, "900"], {"msB1": "RUNNING", "msB2": "SHUTDOWN", "AdminB": "RUNNING"},
                       stuck=("msB1",))
check("helper stop: Managed Server not stopping -> exit 1", code == 1, str(ops))
code, ops = run_helper(["start", rowsf.name, "900"], {"msB1": "SHUTDOWN", "msB2": "RUNNING", "AdminB": "RUNNING"})
check("helper start: Administration Server already up -> no nmStart, managed started",
      code == 0 and ("nmStart", "AdminB") not in ops and ("start", "msB1", "Server", "true") in ops, str(ops))
code, ops = run_helper(["bogus", rowsf.name, "900"], {})
check("helper: bad arguments -> exit 2", code == 2, str(ops))
os.unlink(rowsf.name)
sys.exit(1 if failed else 0)
