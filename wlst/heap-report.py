# v1.0 | 2026-09-10 | READ-ONLY. Changes nothing.
#
# Heap used, free and max per server, as a percentage rather than raw bytes.
# Raw bytes tell you nothing at a glance; 94% used tells you where to look first.
#
# Usage: see server-health.py for the credential setup. Same environment variables.

import os
import sys

MB = 1024.0 * 1024.0


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


def report(warn_at):
    domainRuntime()
    servers = domainRuntimeService.getServerRuntimes()
    if not servers:
        print('No server runtimes returned.')
        return 1

    print('%-22s %10s %10s %10s %7s' % ('SERVER', 'USED MB', 'FREE MB', 'MAX MB', 'USED %'))
    print('-' * 64)

    hot = 0
    for s in servers:
        try:
            jvm = s.getJVMRuntime()
            free = jvm.getHeapFreeCurrent()
            cur = jvm.getHeapSizeCurrent()
            mx = jvm.getHeapSizeMax()
            used = cur - free
            pct = (used * 100.0 / mx) if mx else 0.0
        except Exception, e:
            print('%-22s  unreadable: %s' % (s.getName(), e))
            continue

        flag = ''
        if pct >= warn_at:
            flag = '  <-- above %d%%' % warn_at
            hot += 1

        print('%-22s %10.0f %10.0f %10.0f %6.1f%%%s'
              % (s.getName(), used / MB, free / MB, mx / MB, pct, flag))

    print('-' * 64)
    print('%d server(s), %d above %d%% of max heap' % (len(servers), hot, warn_at))
    # A single high reading is not a leak. Run this on a schedule and compare.
    return 1 if hot else 0


warn_at = int(os.environ.get('WLS_HEAP_WARN', '85'))
connect_from_env()
try:
    rc = report(warn_at)
finally:
    try:
        disconnect()
    except Exception:
        pass
exit(rc)
