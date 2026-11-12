# v1.9 2026-11-11 - blog copy (Codex r1): release written 12.2.1.4.0 in the header (comment only).
# v1.8 2026-10-15 - extensionProperties option added when HOOK_PROPS is set (shared-home hooks).
# v1.7 2026-10-15 - check_env() validates settings before any try; zconnect() no longer calls need()/exit().
# v1.6 2026-10-15 - no exit() inside a try (Jython 2.2 SystemExit is an Exception and would be swallowed): an
#                    ambiguous result is returned as AMBIGUOUS and handled by the caller.
# v1.5 2026-10-15 - find_new_workflow() tells 'not started' (server reachable, nothing new) from 'unknown' (server
#                    unreachable for the whole reconnect window).
# v1.4 2026-10-15 - errors caught as (Exception, java.lang.Throwable): on Jython 2.2 a dropped connection can raise a
#                    Java exception that 'except Exception' does not catch; no bare except left.
# v1.3 2026-10-15 - recovery searches AllWorkflows (also finished ones), prefers the matching target, stops if ambiguous;
#                    usable when the rollout call itself raised.
# v1.2 2026-10-15 - workflow id recovered after a reconnect from the active workflows that were not there before.
# v1.1 2026-10-15 - workflow id read with retry; final status read inside the reconnect handling.
# v1.0 2026-10-15 - shared WLST helpers for 04_zdt_rollout.py and 05_zdt_rollback.py (loaded with execfile).
#   Uses only documented WLST commands and MBeans: connect() with a user config/key file, the rollout commands'
#   returned WorkflowTaskRuntimeMBean (getWorkflowId, getProgressString, getStatus, isRunning, getError) and, after the
#   Administration Server restarts during a domain rollout, RolloutService/rollout-service getWorkflowTask(id).
#   Jython 2.2-compatible (WLST 12.2.1.4.0 / 14.1.1); also runs on later WLST versions.
import sys
import time

from java.lang import Throwable

ERRS = (Exception, Throwable)   # Python and Java exceptions (WLST, MBean proxies, RMI)

POLL_SECONDS = 60
RECONNECT_TRIES = 40          # 40 x 30 s = 20 minutes for the Administration Server to come back
RECONNECT_WAIT = 30


def zlog(level, msg):
    print '%s %-5s %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), level, msg)


def read_env(path):
    """KEY=VALUE file (the same <domain>.env the shell scripts use). Quotes are stripped; no shell expansion."""
    vals = {}
    f = open(path, 'r')
    try:
        for line in f.readlines():
            line = line.strip()
            if not line or line[0] == '#' or line.find('=') < 0:
                continue
            k, v = line.split('=', 1)
            v = v.split(' #')[0].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            vals[k.strip()] = v
    finally:
        f.close()
    return vals


def need(env, key):
    v = env.get(key, '')
    if not v:
        zlog('ERROR', 'missing %s in the env file' % key)
        exit(defaultAnswer='y', exitcode=2)
    return v


def check_env(env, keys):
    """Call before any try block: exit() must not run inside one (Jython 2.2 SystemExit is an Exception)."""
    missing = [k for k in keys if not env.get(k)]
    if missing:
        zlog('ERROR', 'missing in the env file: %s' % ', '.join(missing))
        exit(defaultAnswer='y', exitcode=2)


def zconnect(env):
    """Settings were validated by check_env(); no exit() here, it is called inside try blocks."""
    connect(userConfigFile=env['WLST_USER_CONFIG'], userKeyFile=env['WLST_USER_KEY'], url=env['ADMIN_URL'])


def build_options(env, dry_run):
    """Options string as the ZDT guide documents it: comma-separated name=value pairs.
    isAutoRevertOnFailure is left at its documented default (TRUE)."""
    opts = ['isDryRun=%s' % (dry_run and 'TRUE' or 'FALSE'),
            'shutdownTimeout=%s' % need(env, 'SHUTDOWN_TIMEOUT')]
    if env.get('DELAY_BETWEEN_NODES'):
        opts.append('DelayBetweenNodes=%s' % env['DELAY_BETWEEN_NODES'])
    if env.get('MIGRATION_PROPERTIES'):
        opts.append('migrationProperties=%s' % env['MIGRATION_PROPERTIES'])
    if env.get('HOOK_PROPS'):              # shared home: the hooks stop/start the other domains on each node
        opts.append('extensionProperties=%s' % env['HOOK_PROPS'])
    return ','.join(opts)


def reattach(env, wf_id):
    """After the Administration Server restarts (domain target), reconnect and look the workflow up again."""
    for attempt in range(1, RECONNECT_TRIES + 1):
        try:
            try:
                disconnect()
            except ERRS:                 # the old session is already gone; nothing to clean up
                pass
            zconnect(env)
            domainRuntime()
            cd('/RolloutService/rollout-service')
            wf = cmo.getWorkflowTask(wf_id)
            if wf is not None:
                zlog('INFO', 'reattached to workflow %s (attempt %d)' % (wf_id, attempt))
                return wf
            zlog('WARN', 'workflow %s not found yet (attempt %d)' % (wf_id, attempt))
        except ERRS, e:
            zlog('WARN', 'Administration Server not reachable yet (attempt %d/%d): %s' % (attempt, RECONNECT_TRIES, e))
        time.sleep(RECONNECT_WAIT)
    zlog('ERROR', 'could not reattach to workflow %s; check it with cmo.getWorkflowTask() later' % wf_id)
    exit(defaultAnswer='y', exitcode=3)


def known_workflow_ids():
    """Ids of all workflows the domain knows before a new rollout starts (AllWorkflows on RolloutServiceRuntimeMBean)."""
    ids = []
    domainRuntime()
    cd('/RolloutService/rollout-service')
    for w in cmo.getAllWorkflows():
        ids.append(w.getWorkflowId())
    serverConfig()
    return ids


NOT_STARTED = 'NOT_STARTED'
UNKNOWN = 'UNKNOWN'
AMBIGUOUS = 'AMBIGUOUS'


def find_new_workflow(env, known, target, tries, absent_means_not_started=0):
    """Reconnect and return the id of the workflow started by this script: the one in AllWorkflows (active or already
    finished) that was not there before, preferring one whose WorkflowTarget matches.
    Returns NOT_STARTED when the Administration Server answers and absent_means_not_started is set but there is no new
    workflow, and UNKNOWN when nothing could be established within the tries."""
    for attempt in range(1, tries + 1):
        try:
            try:
                disconnect()
            except ERRS:                 # the old session is already gone; nothing to clean up
                pass
            zconnect(env)
            domainRuntime()
            cd('/RolloutService/rollout-service')
            new, matched = [], []
            for w in cmo.getAllWorkflows():
                wid = w.getWorkflowId()
                if wid in known:
                    continue
                new.append(wid)
                if str(w.getWorkflowTarget()) == target:
                    matched.append(wid)
            if len(matched) == 1:
                zlog('INFO', 'found workflow %s for target %s' % (matched[0], target))
                return matched[0]
            if len(matched) == 0 and len(new) == 1:
                zlog('WARN', 'one new workflow %s; its target did not compare equal to %s, using it' % (new[0], target))
                return new[0]
            if len(new) > 1:
                zlog('ERROR', 'several new workflows %s: another rollout is running; resolve by hand' % new)
                return AMBIGUOUS
            if absent_means_not_started:
                zlog('INFO', 'the Administration Server answers and shows no new workflow: it did not start')
                return NOT_STARTED
            zlog('WARN', 'no new workflow yet (attempt %d/%d)' % (attempt, tries))
        except ERRS, e:
            zlog('WARN', 'Administration Server not reachable yet (attempt %d/%d): %s' % (attempt, tries, e))
        time.sleep(RECONNECT_WAIT)
    return UNKNOWN


def workflow_id(env, progress, known, target):
    """Id of the workflow just started, from the rollout call's handle or, if that is unusable, by reconnecting."""
    if progress is not None:
        for attempt in range(1, 4):
            try:
                return progress.getWorkflowId()
            except ERRS, e:
                zlog('WARN', 'could not read the workflow id (attempt %d/3): %s' % (attempt, e))
                time.sleep(5)
    wf_id = find_new_workflow(env, known, target, RECONNECT_TRIES)
    if wf_id in (NOT_STARTED, UNKNOWN, AMBIGUOUS):
        zlog('ERROR', 'workflow id unknown; list workflows with cmo.getAllWorkflows() under RolloutService/rollout-service')
        exit(defaultAnswer='y', exitcode=3)
    return wf_id


def watch(env, progress, known, target):
    """Poll until the workflow ends; return its final status string. Every remote read is covered by reattach()."""
    wf_id = workflow_id(env, progress, known, target)
    try:
        progress.getWorkflowId()                 # is the handle from the rollout call still usable?
    except ERRS:
        progress = reattach(env, wf_id)
    zlog('INFO', 'workflow %s started' % wf_id)
    status = None
    while status is None:
        try:
            running = progress.isRunning()
            zlog('INFO', progress.getProgressString())
            if not running:
                status = str(progress.getStatus())
                break
        except ERRS, e:
            zlog('WARN', 'lost the connection while polling (expected while the Administration Server restarts): %s' % e)
            progress = reattach(env, wf_id)
            continue
        time.sleep(POLL_SECONDS)
    zlog('INFO', 'workflow %s finished with status %s' % (wf_id, status))
    if status != 'SUCCESS':
        try:
            zlog('ERROR', 'workflow error: %s' % progress.getError())
        except ERRS, e:
            zlog('WARN', 'no error detail available: %s' % e)
    return status


def server_states():
    domainRuntime()
    for r in cmo.getServerLifeCycleRuntimes():
        zlog('INFO', 'server %-30s %s' % (r.getName(), r.getState()))
