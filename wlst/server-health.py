# v1.0 | 2026-09-10 | READ-ONLY. Changes nothing.
#
# Reports every server's state and health, plus the subsystems that are not OK.
# The state alone is not enough: a server can sit in RUNNING with a failed JDBC
# subsystem, and the console shows green until someone clicks into it.
#
# Usage:
#   export WLS_USER=weblogic
#   export WLS_PASS='...'          # or use a userConfigFile, see below
#   export WLS_URL=t3://host01.example.internal:7001
#   java weblogic.WLST server-health.py
#
# Credentials come from the environment, never from this file. To avoid putting a
# password in a shell at all, create a user-config file once:
#   storeUserConfig('/opt/app/wlst/wls-config', '/opt/app/wlst/wls-key')
# then set WLS_CONFIG and WLS_KEY instead of WLS_USER and WLS_PASS.

import os
import sys

HEALTH = {
    0: 'OK',
    1: 'WARN',
    2: 'CRITICAL',
    3: 'FAILED',
    4: 'OVERLOADED',
    5: 'UNKNOWN',
}


def connect_from_env():
    """Connect using either a user-config file or user/password. Fail loudly."""
    url = os.environ.get('WLS_URL')
    if not url:
        print('WLS_URL is not set. Example: t3://host01.example.internal:7001')
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
    except Exception, e:                      # noqa: E722  (WLST runs on Jython 2.7)
        print('Connect failed: %s' % e)
        sys.exit(1)


def report():
    domainRuntime()
    servers = domainRuntimeService.getServerRuntimes()
    if not servers:
        print('No server runtimes returned. Are you connected to the admin server?')
        return 1

    print('%-22s %-12s %-10s %s' % ('SERVER', 'STATE', 'HEALTH', 'SUBSYSTEMS NOT OK'))
    print('-' * 78)

    unhealthy = 0
    for s in servers:
        name = s.getName()
        state = s.getState()
        try:
            hs = s.getHealthState()
            health = HEALTH.get(hs.getState(), 'UNKNOWN')
            # The interesting part: which subsystem is unhappy, not just that one is.
            bad = [str(x) for x in hs.getSymptoms()] if hs.getState() != 0 else []
        except Exception, e:
            health, bad = 'UNREADABLE', [str(e)]

        if state != 'RUNNING' or health != 'OK':
            unhealthy += 1

        print('%-22s %-12s %-10s %s' % (name, state, health, ', '.join(bad) if bad else '-'))

    print('-' * 78)
    print('%d server(s), %d not fully healthy' % (len(servers), unhealthy))
    return 1 if unhealthy else 0


connect_from_env()
try:
    rc = report()
finally:
    try:
        disconnect()
    except Exception:
        pass
exit(rc)
