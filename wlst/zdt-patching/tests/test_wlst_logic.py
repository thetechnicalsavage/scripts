#!/usr/bin/env python3
# v1.4 2026-10-15 - scenario: missing JAVA_HOME_NEW is refused up front (exit 2, no rollout call).
# v1.3 2026-10-15 - mock exit() raises an Exception subclass (Jython 2.2 semantics), so an exit swallowed by an
#                    'except' fails the test; scenario: two new workflows (ambiguous, exit 3).
# v1.2 2026-10-15 - scenarios: submit fails during a long Admin Server outage (workflow found later, exit 0) and
#                    submit rejected outright (nothing started, exit 2).
# v1.1 2026-10-15 - mock Java exceptions derive from BaseException, so "except Exception" alone fails the test.
# v1.0 2026-10-15 - offline test of the WLST scripts' control flow (no WebLogic needed). The Jython 2 sources are
#   converted with lib2to3 and run against a mock of the WLST functions and the rollout MBeans. Scenarios: Administration
#   Server restart in the middle of a rollout (Java exceptions from the MBean proxy, reconnect, reattach by id), the
#   rollout call raising after the server accepted it, a workflow ending REVERTED, and a successful rollback.
# Usage: python3 tests/test_wlst_logic.py      (exit 0 when every scenario gives the expected exit code)
import os
import sys
import types
import warnings

warnings.filterwarnings("ignore")
from lib2to3 import refactor  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
TOOL = refactor.RefactoringTool(refactor.get_fixers_from_package("lib2to3.fixes"))


def py3(path):
    return str(TOOL.refactor_string(open(path).read() + "\n", path))


class Throwable(BaseException):      # stands in for java.lang.Throwable: not caught by "except Exception", as on Jython 2.2
    pass


class PeerGone(Throwable):
    pass


class WLSTException(Exception):
    pass


class WlstExit(Exception):            # Jython 2.2: SystemExit derives from Exception, so 'except' clauses can catch it
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


java = types.ModuleType("java")
java_lang = types.ModuleType("java.lang")
java_lang.Throwable = Throwable
java.lang = java_lang
sys.modules["java"], sys.modules["java.lang"] = java, java_lang


class Domain:
    """Admin Server state, workflows, and the scenario's failure plan."""
    def __init__(self, down_after_polls=None, down_for_attempts=0, final="SUCCESS", raise_on_submit=False,
                 reject_submit=False, down_on_submit=0, foreign_workflow=False):
        self.admin_up = True
        self.polls = 0
        self.down_after_polls, self.down_for, self.down_left = down_after_polls, down_for_attempts, 0
        self.final, self.raise_on_submit = final, raise_on_submit
        self.reject_submit, self.down_on_submit = reject_submit, down_on_submit
        self.foreign_workflow = foreign_workflow
        self.workflows = {"wf0001": Workflow(self, "wf0001", "old", running=False, status="SUCCESS")}
        self.calls = []


class Workflow:
    def __init__(self, dom, wid, target, running=True, status="STARTED"):
        self.dom, self.wid, self.target, self.running, self.status, self.steps = dom, wid, target, running, status, 0

    def _check(self):
        if not self.dom.admin_up:
            raise PeerGone("peer gone: admin server restarting")

    def getWorkflowId(self):
        self._check(); return self.wid

    def getWorkflowTarget(self):
        self._check(); return self.target

    def isRunning(self):
        self._check()
        d = self.dom
        d.polls += 1
        if d.down_after_polls is not None and d.polls == d.down_after_polls:
            d.admin_up, d.down_left = False, d.down_for
            raise PeerGone("connection lost")
        self.steps += 1
        if self.steps >= 5:
            self.running, self.status = False, d.final
        return self.running

    def getProgressString(self):
        self._check(); return "Workflow %s Running: %d/5" % (self.wid, self.steps)

    def getStatus(self):
        self._check(); return self.status

    def getError(self):
        self._check(); return "step failed (mock)"


def make_wlst(dom):
    ns = {}

    class Cmo:
        def getAllWorkflows(self):
            return list(dom.workflows.values())

        def getActiveWorkflows(self):
            return [w for w in dom.workflows.values() if w.running]

        def getWorkflowTask(self, wid):
            return dom.workflows.get(wid)

        def getServerLifeCycleRuntimes(self):
            class R:
                def __init__(self, n): self.n = n
                def getName(self): return self.n
                def getState(self): return "RUNNING"
            return [R("AdminServer"), R("ms1")]

    def connect(userConfigFile=None, userKeyFile=None, url=None):
        dom.calls.append("connect")
        if not dom.admin_up:
            dom.down_left -= 1
            if dom.down_left <= 0:
                dom.admin_up = True          # the Administration Server is RUNNING again
            raise WLSTException("cannot connect")

    def submit(target, *args, **kw):
        dom.calls.append(("rollout", target, args, kw.get("options")))
        if dom.reject_submit:
            raise WLSTException("invalid argument (mock): nothing submitted")
        if dom.foreign_workflow:
            dom.workflows["wf9999"] = Workflow(dom, "wf9999", dom.foreign_workflow)
        wid = "wf%04d" % (len(dom.workflows) + 1)
        dom.workflows[wid] = Workflow(dom, wid, target)
        if dom.raise_on_submit:
            if dom.down_on_submit:
                dom.admin_up, dom.down_left = False, dom.down_on_submit
            raise PeerGone("reply lost after submit")
        return dom.workflows[wid]

    def wlst_exit(defaultAnswer=None, exitcode=0):
        raise WlstExit(exitcode)

    ns.update(dict(connect=connect, disconnect=lambda: None, domainRuntime=lambda: None, serverConfig=lambda: None,
                   cd=lambda p: None, cmo=Cmo(), exit=wlst_exit, rolloutUpdate=submit, rolloutOracleHome=submit,
                   rolloutJavaHome=submit, __name__="__main__"))
    return ns


def run(script, args, dom, drop_key=None):
    env = os.path.join(HERE, "test.env")
    with open(env, "w") as fh:
        fh.write("DOMAIN_NAME=domainA\nADMIN_URL=t3://admin:7001\nWLST_USER_CONFIG=/x/cfg\nWLST_USER_KEY=/x/key\n"
                 "ORACLE_HOME=/u01/app/oracle/product/domainA/fmw\nCYCLE=2026Q4\nSHUTDOWN_TIMEOUT=1800\n"
                 "JAVA_HOME_NEW=/u01/app/oracle/product/domainA/jdk-17.0.17\n"
                 "JAVA_HOME_PREVIOUS=/u01/app/oracle/product/domainA/jdk-17.0.16\n")
    if drop_key:
        lines = [l for l in open(env).read().splitlines() if not l.startswith(drop_key + "=")]
        open(env, "w").write("\n".join(lines) + "\n")
    ns = make_wlst(dom)
    common = py3(os.path.join(SRC, "zdt_common.py"))
    src = open(os.path.join(SRC, script)).read()
    line = "execfile(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'zdt_common.py'))"
    assert line in src, "execfile line not found in " + script
    main = str(TOOL.refactor_string(src.replace(line, "") + "\n", script))   # zdt_common is loaded separately
    sys.argv = [os.path.join(SRC, script), env] + args
    import time as _t
    sleep, _t.sleep = _t.sleep, (lambda s: None)
    try:
        exec(compile(common, "zdt_common.py", "exec"), ns)
        exec(compile(main, script, "exec"), ns)
        code = 0
    except WlstExit as e:
        code = e.code
    finally:
        _t.sleep = sleep
        os.remove(env)
    return code


CASES = [
    ("rollout, Admin Server restarts mid-run", "04_zdt_rollout.py", ["update", "--execute"],
     Domain(down_after_polls=2, down_for_attempts=3), 0),
    ("rollout call raises after submit", "04_zdt_rollout.py", ["update", "--execute"],
     Domain(raise_on_submit=True), 0),
    ("submit fails, Admin Server down 10 tries", "04_zdt_rollout.py", ["update", "--execute"],
     Domain(raise_on_submit=True, down_on_submit=10), 0),
    ("submit rejected, nothing started", "04_zdt_rollout.py", ["update", "--execute"],
     Domain(reject_submit=True), 2),
    ("submit fails, other target also new", "04_zdt_rollout.py", ["update", "--execute"],
     Domain(raise_on_submit=True, foreign_workflow="otherTarget"), 0),
    ("submit fails, same target twice", "04_zdt_rollout.py", ["update", "--execute"],
     Domain(raise_on_submit=True, foreign_workflow="domainA"), 3),
    ("rollout ends REVERTED", "04_zdt_rollout.py", ["oraclehome", "--execute"],
     Domain(final="REVERTED"), 1),
    ("dry run", "04_zdt_rollout.py", ["javahome"], Domain(), 0),
    ("rollback succeeds", "05_zdt_rollback.py", ["oraclehome", "--execute"], Domain(down_after_polls=3, down_for_attempts=2), 0),
]

failed = 0
CASES.append(("missing JAVA_HOME_NEW refused up front", "04_zdt_rollout.py", ["update", "--execute"], Domain(), 2,
              "JAVA_HOME_NEW"))
for case in CASES:
    name, script, args, dom, expect = case[:5]
    code = run(script, args, dom, case[5] if len(case) > 5 else None)
    ok = code == expect
    failed += not ok
    roll = [c for c in dom.calls if isinstance(c, tuple)]
    print("%-4s %-40s exit=%s expected=%s  call=%s" % ("PASS" if ok else "FAIL", name, code, expect,
                                                       roll[0][1:] if roll else None))
sys.exit(1 if failed else 0)
