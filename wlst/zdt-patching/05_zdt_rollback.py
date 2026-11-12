# v1.7 2026-11-11 - blog copy: "Phase N" labels from an earlier plan removed; impact stated as offline or rolling.
# v1.6 2026-10-15 - all settings checked before the try block (no exit() inside it).
# v1.5 2026-10-15 - an ambiguous recovery (several new workflows) exits 3 like an unknown outcome.
# v1.4 2026-10-15 - after a failed rollout call: exit 2 only when the server shows nothing started, exit 3 when unknown.
# v1.3 2026-10-15 - if the rollout call raises, check whether the workflow started anyway before giving up.
# v1.2 2026-10-15 - workflow ids recorded before the rollback so a lost first reply can be recovered.
# v1.1 2026-10-15 - exit code fixed (and/or idiom returned 1 on success).
# v1.0 2026-10-15 - Rolling, like the rollout. Rolls a domain back with the documented ZDT rollback, which is
#   itself a rolling workflow:
#     oraclehome  rolloutUpdate(target, <backup home of the last rollout>, <new backup path>, 'true', options)
#                 (isRollback=true; no Java home is passed: the guide says Java returns to the one used with that home)
#     javahome    rolloutJavaHome(target, JAVA_HOME_PREVIOUS, options)   (the previous JDK is still installed)
#   For a workflow that stopped part-way, resume or revert it instead (RolloutServiceRuntimeMBean executeWorkflow /
#   revertWorkflow, see README). Default is a dry run; add --execute to run it.
# Usage: wlst.sh 05_zdt_rollback.py <domain>.env <oraclehome|javahome> [--execute] [--target=<name[,name]>]
import os
import sys

execfile(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'zdt_common.py'))

args = sys.argv[1:]
if len(args) < 2 or args[1] not in ('oraclehome', 'javahome'):
    print 'usage: wlst.sh 05_zdt_rollback.py <domain>.env <oraclehome|javahome> [--execute] [--target=...]'
    exit(defaultAnswer='y', exitcode=2)
env = read_env(args[0])
kind = args[1]
execute = '--execute' in args
check_env(env, ['DOMAIN_NAME', 'ADMIN_URL', 'WLST_USER_CONFIG', 'WLST_USER_KEY', 'ORACLE_HOME',
               'CYCLE', 'SHUTDOWN_TIMEOUT'] + (kind == 'javahome' and ['JAVA_HOME_PREVIOUS'] or []))
target = need(env, 'DOMAIN_NAME')
for a in args[2:]:
    if a.startswith('--target='):
        target = a.split('=', 1)[1]

oh = need(env, 'ORACLE_HOME')
cycle = need(env, 'CYCLE')
restore_from = '%s_backup_%s' % (oh, cycle)          # where the last rollout moved the previous home
park_failed = '%s_rolledback_%s' % (oh, cycle)        # where the home being removed is moved to
opts = build_options(env, not execute)

zlog('INFO', 'domain=%s target=%s kind=%s mode=%s' % (env['DOMAIN_NAME'], target, kind, execute and 'EXECUTE' or 'DRY RUN'))
known = None
progress = None
try:
    zconnect(env)
    known = known_workflow_ids()
    if kind == 'oraclehome':
        zlog('INFO', 'restore %s, move the current home to %s' % (restore_from, park_failed))
        progress = rolloutUpdate(target, restore_from, park_failed, 'true', options=opts)
    else:
        zlog('INFO', 'point the domain back to %s' % env['JAVA_HOME_PREVIOUS'])
        progress = rolloutJavaHome(target, env['JAVA_HOME_PREVIOUS'], options=opts)
except ERRS, e:
    zlog('ERROR', 'the rollout call failed: %s' % e)
    if known is None:                          # failed before anything was submitted
        exit(defaultAnswer='y', exitcode=2)
    zlog('INFO', 'checking whether the rollback workflow started anyway')
    found = find_new_workflow(env, known, target, RECONNECT_TRIES, absent_means_not_started=1)
    if found == NOT_STARTED:
        exit(defaultAnswer='y', exitcode=2)
    if found in (UNKNOWN, AMBIGUOUS):
        zlog('ERROR', 'outcome unknown: check RolloutService/rollout-service before running anything again')
        exit(defaultAnswer='y', exitcode=3)

status = watch(env, progress, known, target)
try:
    server_states()
except ERRS, e:
    zlog('WARN', 'could not read server states: %s' % e)
if status == 'SUCCESS':
    exit(defaultAnswer='y', exitcode=0)
exit(defaultAnswer='y', exitcode=1)
