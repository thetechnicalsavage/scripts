# v1.1 2026-11-11 - WALLET_DIR note: sqlnet.ora's WALLET_LOCATION must name the same folder.
# v1.0 2026-11-11 - connection helper for the Select AI labs on Autonomous AI Database: read_env() and connect(), the
#   two functions the drivers here use. Nothing in this file names a path: the Instant Client and the wallet come from
#   the environment.
#
#   ORACLE_IC_DIR  Oracle Instant Client directory. Thick mode, as the lab ran with the database's mTLS wallet.
#                  On Linux also export LD_LIBRARY_PATH=$ORACLE_IC_DIR before Python starts: the client's own
#                  libraries (libnnz.so) resolve through the loader path, not through lib_dir.
#   WALLET_DIR     the unzipped Autonomous database wallet (tnsnames.ora, cwallet.sso, sqlnet.ora). In its sqlnet.ora,
#                  set WALLET_LOCATION's DIRECTORY to this same folder: the downloaded file points at
#                  ?/network/admin, so mTLS cannot find cwallet.sso otherwise.
#   Login file     KEY=VALUE, mode 600: ADB_USER, ADB_PWD, optional ADB_DSN (a tnsnames.ora alias, default adb_low).
import os
import stat


def read_env(path):
    """KEY=VALUE file; refuses a file other users can read, so a password file cannot leak by mode."""
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f'{os.path.basename(path)}: must be mode 600 (it may hold a password)')
    vals = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                vals[k.strip()] = v.strip()
    return vals


def _need(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f'set {name} (see lab_conn.py)')
    return value


def connect(login, dsn=None):
    """Thick-mode connection through the wallet in WALLET_DIR; returns (connection, dsn used)."""
    import oracledb
    os.environ.setdefault('ORA_SDTZ', 'UTC')     # as the database's own sessions; read by the client at connect time
    oracledb.init_oracle_client(lib_dir=_need('ORACLE_IC_DIR'), config_dir=_need('WALLET_DIR'))
    dsn = dsn or login.get('ADB_DSN') or 'adb_low'
    conn = oracledb.connect(user=login['ADB_USER'], password=login['ADB_PWD'], dsn=dsn)   # sanitize-ok: credential-assignment
    conn.module = 'SAILAB'
    return conn, dsn
