# v1.2 2026-10-15 - Administration Server unreachable at stop (e.g. revert of a failed start): servers on this
#                    node stopped through Node Manager (nmKill, logged as forced). The final 'nothing left running'
#                    check is done by the shell hook on the node's processes, not inferred from connection errors.
# v1.1 2026-10-15 - every stop is verified (Managed Server state SHUTDOWN; Administration Server no longer accepts
#                    connections) and a failed stop fails the hook; nmStart skipped when the Administration Server
#                    is already up (idempotent after a partial stop).
# v1.0 2026-10-15 - WLST part of the shared-home hooks. On this node, stops or starts the servers of the OTHER domains
#   that run from the Oracle home the lead domain's ZDT workflow is replacing. Documented WLST commands only:
#   connect/shutdown/start through each domain's Administration Server, nmConnect/nmStart/stopNodeManager for its
#   Node Manager on this node. Graceful: ignoreSessions=false, force=false, with the configured timeout.
#   Jython 2.2-compatible: Java exceptions are caught with Python ones, and exit() is only called outside try blocks.
# Usage: wlst.sh zdt_other_domains.py stop|start <rows file> <timeout s>
#        wlst.sh zdt_other_domains.py nmcheck <rows file> <domain>       (exit 0 if its Node Manager answers)
# Row: node,domain,domain_home,admin_url,admin_server,user_config,user_key,nm_port,nm_user_config,nm_user_key,
#      nm_type,servers   (servers on this node separated by ';', the Administration Server included if it is here)
import sys
import time

from java.lang import Throwable

ERRS = (Exception, Throwable)
TRIES = 30                     # x 10 s = 5 minutes for a Node Manager or Administration Server to answer


def zlog(msg):
    print '%s zdt-hook-wlst: %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), msg)


def rows(path):
    out = []
    f = open(path, 'r')
    try:
        for line in f.readlines():
            line = line.strip()
            if not line or line[0] == '#':
                continue
            c = line.split(',')
            if len(c) != 12:
                raise ValueError('expected 12 fields, got %d: %s' % (len(c), line))
            out.append({'node': c[0], 'domain': c[1], 'home': c[2], 'url': c[3], 'admin': c[4], 'ucfg': c[5],
                        'ukey': c[6], 'nm_port': c[7], 'nm_ucfg': c[8], 'nm_ukey': c[9], 'nm_type': c[10] or 'ssl',
                        'servers': [s for s in c[11].split(';') if s]})
    finally:
        f.close()
    return out


def admin_connect(r):
    connect(userConfigFile=r['ucfg'], userKeyFile=r['ukey'], url=r['url'])


def nm_connect(r):
    nmConnect(userConfigFile=r['nm_ucfg'], userKeyFile=r['nm_ukey'], host=r['node'], port=r['nm_port'],
              domainName=r['domain'], domainDir=r['home'], nmType=r['nm_type'])


def quietly(fn):
    try:
        fn()
    except ERRS:                      # already disconnected; nothing to release
        pass


def retry(what, fn):
    for attempt in range(1, TRIES + 1):
        try:
            fn()
            return 1
        except ERRS, e:
            zlog('%s: not yet (%d/%d): %s' % (what, attempt, TRIES, e))
            time.sleep(10)
    return 0


def server_state(name):
    domainRuntime()
    cd('/ServerLifeCycleRuntimes/' + name)
    st = str(cmo.getState())
    serverConfig()
    return st


def admin_is_up(r):
    try:
        admin_connect(r)
    except ERRS:
        return 0
    quietly(disconnect)
    return 1


RUNNING_STATES = ('RUNNING', 'ADMIN', 'STANDBY', 'STARTING', 'RESUMING', 'SUSPENDING', 'SHUTTING_DOWN')


def stop_domain(r, timeout):
    try:
        admin_connect(r)
        reachable = 1
    except ERRS, e:
        reachable = 0
        zlog('%s: Administration Server not reachable (%s); using its Node Manager on this node' % (r['domain'], e))
    if reachable:
        for s in [x for x in r['servers'] if x != r['admin']]:
            st = server_state(s)
            if st in ('SHUTDOWN', 'FAILED_NOT_RESTARTABLE'):
                zlog('%s/%s already %s' % (r['domain'], s, st))
                continue
            zlog('%s/%s: graceful shutdown (timeout %s s)' % (r['domain'], s, timeout))
            shutdown(s, 'Server', 'false', int(timeout), 'false', 'true')
            st = server_state(s)
            if st != 'SHUTDOWN':
                raise RuntimeError('%s/%s is %s after shutdown' % (r['domain'], s, st))
        if r['admin'] in r['servers']:
            zlog('%s/%s: graceful shutdown of the Administration Server' % (r['domain'], r['admin']))
            try:
                shutdown(r['admin'], 'Server', 'false', int(timeout), 'false', 'true')
            except ERRS, e:           # the connection closes as the server goes down; the hook checks processes after
                zlog('%s: shutdown call ended with: %s' % (r['domain'], e))
        quietly(disconnect)
    nm_connect(r)                      # its Node Manager on this node also runs from the shared home
    if not reachable:
        for s in r['servers']:
            st = str(nmServerStatus(s)).strip()
            if st in RUNNING_STATES:
                zlog('%s/%s: %s and no Administration Server: FORCED stop with nmKill' % (r['domain'], s, st))
                nmKill(s)
    zlog('%s: stopping its Node Manager' % r['domain'])
    stopNodeManager()


def start_domain(r):
    if not retry('%s Node Manager' % r['domain'], lambda: nm_connect(r)):
        raise RuntimeError('%s: Node Manager did not answer' % r['domain'])
    if r['admin'] in r['servers'] and not admin_is_up(r):
        zlog('%s/%s: starting the Administration Server with Node Manager' % (r['domain'], r['admin']))
        nmStart(r['admin'], r['home'])
    quietly(nmDisconnect)
    if not retry('%s Administration Server' % r['domain'], lambda: admin_connect(r)):
        raise RuntimeError('%s: Administration Server did not answer' % r['domain'])
    for s in [x for x in r['servers'] if x != r['admin']]:
        if server_state(s) == 'RUNNING':
            zlog('%s/%s already RUNNING' % (r['domain'], s))
            continue
        zlog('%s/%s: starting' % (r['domain'], s))
        start(s, 'Server', block='true')
        st = server_state(s)
        if st != 'RUNNING':
            raise RuntimeError('%s/%s is %s after start' % (r['domain'], s, st))
    quietly(disconnect)


args = sys.argv[1:]
code = 0
if len(args) != 3 or args[0] not in ('stop', 'start', 'nmcheck'):
    zlog('usage: zdt_other_domains.py stop|start <rows> <timeout> | nmcheck <rows> <domain>')
    code = 2
else:
    mode = args[0]
    try:
        todo = rows(args[1])
        if mode == 'nmcheck':
            mine = [r for r in todo if r['domain'] == args[2]]
            if mine:
                nm_connect(mine[0])
                quietly(nmDisconnect)
            else:
                code = 2
        elif mode == 'stop':
            for r in todo:
                stop_domain(r, args[2])
        else:
            for r in todo:
                start_domain(r)
    except ERRS, e:
        zlog('%s failed: %s' % (mode, e))
        code = 1
    if code == 0:
        zlog('%s complete' % mode)
exit(defaultAnswer='y', exitcode=code)
