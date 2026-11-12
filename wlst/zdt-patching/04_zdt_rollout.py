# v1.6 2026-11-11 - blog copy: "Phase N" labels from an earlier plan removed; impact stated as offline or rolling.
# v1.5 2026-10-15 - all settings checked before the try block (no exit() inside it).
# v1.4 2026-10-15 - an ambiguous recovery (several new workflows) exits 3 like an unknown outcome.
# v1.3 2026-10-15 - after a failed rollout call: exit 2 only when the server shows nothing started, exit 3 when unknown.
# v1.2 2026-10-15 - if the rollout call raises, check whether the workflow started anyway before giving up.
# v1.1 2026-10-15 - workflow ids recorded before the rollout so a lost first reply can be recovered.
# v1.0 2026-10-15 - Rolling: one node's servers at a time. Creates and runs the Oracle ZDT patching workflow for one
#   domain with the documented WLST rollout commands, then monitors it to the end:
#     update     rolloutUpdate(target, archive, backupOracleHome, 'false', javaHome, options)   Oracle home + JDK
#     oraclehome rolloutOracleHome(target, archive, backupOracleHome, options)                   Oracle home only
#     javahome   rolloutJavaHome(target, javaHome, options)                                      JDK only
#   Default is a dry run (isDryRun=TRUE: the workflow is evaluated but not run). Add --execute to run it.
#   Target defaults to the domain, so the Administration Server is updated first, as the guide requires for Oracle home
#   rollouts. The archive must already be on every node (03_stage_on_nodes.sh).
#   Note: the documented rolloutUpdate signature puts isRollback 4th and javaHome 5th; one guide example passes the JDK
#   4th. This script follows the signature; the dry run confirms it on your WLST version before --execute.
# Usage: wlst.sh 04_zdt_rollout.py <domain>.env <update|oraclehome|javahome> [--execute] [--target=<name[,name]>]
import os
import sys

execfile(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'zdt_common.py'))

args = sys.argv[1:]
if len(args) < 2 or args[1] not in ('update', 'oraclehome', 'javahome'):
    print 'usage: wlst.sh 04_zdt_rollout.py <domain>.env <update|oraclehome|javahome> [--execute] [--target=...]'
    exit(defaultAnswer='y', exitcode=2)
env = read_env(args[0])
kind = args[1]
execute = '--execute' in args
check_env(env, ['DOMAIN_NAME', 'ADMIN_URL', 'WLST_USER_CONFIG', 'WLST_USER_KEY', 'ORACLE_HOME',
               'CYCLE', 'SHUTDOWN_TIMEOUT'] + (kind != 'oraclehome' and ['JAVA_HOME_NEW'] or []))
target = need(env, 'DOMAIN_NAME')
for a in args[2:]:
    if a.startswith('--target='):
        target = a.split('=', 1)[1]

oh = need(env, 'ORACLE_HOME')
cycle = need(env, 'CYCLE')
archive = '%s/%s_patched_%s.jar' % (os.path.dirname(oh), os.path.basename(oh), cycle)
backup = '%s_backup_%s' % (oh, cycle)
opts = build_options(env, not execute)

zlog('INFO', 'domain=%s target=%s kind=%s mode=%s' % (env['DOMAIN_NAME'], target, kind, execute and 'EXECUTE' or 'DRY RUN'))
zlog('INFO', 'archive=%s backup=%s options=%s' % (archive, backup, opts))
if target != env['DOMAIN_NAME'] and kind != 'javahome':
    zlog('WARN', 'non-domain target: the patched Oracle home must already be rolled out to the Administration Server')

known = None
progress = None
try:
    zconnect(env)
    known = known_workflow_ids()
    if kind == 'update':
        progress = rolloutUpdate(target, archive, backup, 'false', env['JAVA_HOME_NEW'], options=opts)
    elif kind == 'oraclehome':
        progress = rolloutOracleHome(target, archive, backup, options=opts)
    else:
        progress = rolloutJavaHome(target, env['JAVA_HOME_NEW'], options=opts)
except ERRS, e:
    zlog('ERROR', 'the rollout call failed: %s' % e)
    if known is None:                          # failed before anything was submitted
        exit(defaultAnswer='y', exitcode=2)
    zlog('INFO', 'checking whether the workflow started anyway')
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
    zlog('INFO', execute and 'rollout complete; run 06_verify.sh' or 'dry run passed; re-run with --execute')
    exit(defaultAnswer='y', exitcode=0)
exit(defaultAnswer='y', exitcode=1)
