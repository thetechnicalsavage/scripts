# v1.0 | 2026-09-10 | READ-ONLY. Changes nothing.
#
# Deployment state per application PER TARGET. A deployment reported as active at
# domain level can still be failed on one managed server, and that is exactly the
# case that produces intermittent errors nobody can reproduce.
#
# Usage: see server-health.py for the credential setup.

import os
import sys


def connect_from_env():
    url = os.environ.get('WLS_URL')
    if not url:
        print('WLS_URL is not set.')
        sys.exit(2)
    cfg, key = os.environ.get('WLS_CONFIG'), os.environ.get('WLS_KEY')
    try:
        if cfg and key:
            connect(userConfigFile=cfg, userKeyFile=key, url=url)
        else:
            user, pwd = os.environ.get('WLS_USER'), os.environ.get('WLS_PASS')
            if not user or not pwd:
                print('Set WLS_CONFIG and WLS_KEY, or WLS_USER and WLS_PASS.')
                sys.exit(2)
            connect(user, pwd, url)
    except Exception, e:
        print('Connect failed: %s' % e)
        sys.exit(1)


def report():
    domainRuntime()
    servers = domainRuntimeService.getServerRuntimes()

    rows = []
    for s in servers:
        server = s.getName()
        try:
            arts = s.getApplicationRuntimes()
        except Exception, e:
            rows.append((server, '(unreadable)', str(e)))
            continue
        for a in arts:
            try:
                # ACTIVE, PREPARED, NEW, FAILED. Anything not ACTIVE deserves a look.
                state = a.getApplicationRuntimeState() if hasattr(a, 'getApplicationRuntimeState') else 'UNKNOWN'
            except Exception:
                state = 'UNKNOWN'
            rows.append((server, a.getName(), str(state)))

    if not rows:
        print('No application runtimes found.')
        return 1

    print('%-22s %-38s %s' % ('SERVER', 'APPLICATION', 'STATE'))
    print('-' * 76)
    notactive = 0
    for server, app, state in sorted(rows):
        mark = ''
        if state not in ('ACTIVE', 'STATE_ACTIVE'):
            mark = '  <--'
            notactive += 1
        print('%-22s %-38s %s%s' % (server, app, state, mark))
    print('-' * 76)
    print('%d deployment instance(s), %d not active' % (len(rows), notactive))
    return 1 if notactive else 0


connect_from_env()
try:
    rc = report()
finally:
    try:
        disconnect()
    except Exception:
        pass
exit(rc)
