# DBMS_CLOUD on a non-Autonomous database

Every Select AI article assumes Autonomous Database, where `DBMS_CLOUD` is already
there. On an on-premise or container database it is not, and nothing in the error
says so. You get:

```
PLS-00201: identifier 'DBMS_CLOUD_AI' must be declared
```

Oracle's PL/SQL Packages reference says it plainly: "The DBMS_CLOUD family of
packages is not pre-installed or configured with Oracle AI Database."

Five things have to be true before `SELECT AI` works. They live in different places,
so the table says where to look for each one.

| | Check | Where it lives | What a missing one looks like |
|---|---|---|---|
| 1 | The `DBMS_CLOUD` package family is installed, owned by `C##CLOUD$SERVICE`, and the calling schema has `EXECUTE` on it | `CDB$ROOT` and every PDB; the grant in the PDB | `PLS-00201` |
| 2 | The `SSL_WALLET` database property names an auto-login wallet of trusted root certificates | Set in `CDB$ROOT`. Read it from `DATABASE_PROPERTIES` | Not tested here |
| 3 | A host ACE for `C##CLOUD$SERVICE` | `CDB$ROOT` | Oracle documents an HTTP 401 error from `DBMS_CLOUD` calls |
| 4 | A host ACE and a wallet ACE for each user that calls `DBMS_CLOUD` | The PDB, one set per user or role | Not tested here |
| 5 | A credential | The calling schema, in `USER_CREDENTIALS` | Not tested here |

Number 4 is per user because, in Oracle's words, "The DBMS_CLOUD family of packages
have the INVOKER right privilege."

## Installing the package family

**From 26ai 23.7 on, the install scripts ship in the Oracle home.** Oracle: "The code
and installation scripts for DBMS_CLOUD are part of the Oracle distribution."
`catclouduser.sql` creates `C##CLOUD$SERVICE`, and `dbms_cloud_install.sql` installs
the packages into it. Oracle runs them as two separate `catcon.pl` runs, as `SYS`, in
this order:

```sh
$ORACLE_HOME/perl/bin/perl $ORACLE_HOME/rdbms/admin/catcon.pl -u sys -force_pdb_mode 'READ WRITE' -b dbms_cloud_install -d $ORACLE_HOME/rdbms/admin/ -l /tmp catclouduser.sql

$ORACLE_HOME/perl/bin/perl $ORACLE_HOME/rdbms/admin/catcon.pl -u sys -force_pdb_mode 'READ WRITE' -b dbms_cloud_install -d $ORACLE_HOME/rdbms/admin/ -l /tmp dbms_cloud_install.sql
```

That installs into `CDB$ROOT` and every PDB. Oracle says the procedure is idempotent,
and to run it again after a release update that ships a new `DBMS_CLOUD`. The full
page is [Installing DBMS_CLOUD](https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/installing-dbms_cloud.html).

**On 19c, 21c, and 26ai before RU 7,** Oracle's
[DBMS_CLOUD Family of Packages](https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/dbms_cloud-family-packages.html)
page points to a My Oracle Support note instead: KB54212, "How To Setup And Use
DBMS_CLOUD Package" (Doc ID 2748362.1),
[here](https://support.oracle.com/epmos/faces/DocumentDisplay?id=2748362.1). Login
required.

## The scripts

| | |
|---|---|
| [`00_diagnose.sql`](00_diagnose.sql) | **READ-ONLY.** Run as the calling schema in the PDB. Checks 1 to 5 in order, with a pointer for check 3, which only a DBA can see. The first one that returns nothing is your problem. Start here. |
| [`00_diagnose_root.sql`](00_diagnose_root.sql) | **READ-ONLY.** Run as `SYS` in `CDB$ROOT`. The packages, the `SSL_WALLET` property and the `C##CLOUD$SERVICE` host ACE |
| [`01_root_ace_and_wallet.sql`](01_root_ace_and_wallet.sql) | Oracle's database-level step, as `SYS` in `CDB$ROOT`: the `C##CLOUD$SERVICE` host ACE and the `SSL_WALLET` property |
| [`01_acl.sql`](01_acl.sql) | Host ACE for one calling user, as `SYS` in the PDB |
| [`02_wallet_acl.sql`](02_wallet_acl.sql) | Wallet ACE for one calling user, as `SYS` in the PDB |
| [`03_credential.sql`](03_credential.sql) | `CREATE_CREDENTIAL` in its OCI form, prompted, nothing stored |
| [`04_smoke_test.sql`](04_smoke_test.sql) | One `SEND_REQUEST`. 200 means the whole outbound path works |

`01_root_ace_and_wallet.sql` follows Oracle's
[Configure the Database with ACEs for DBMS_CLOUD](https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/configure-dateabase-aces-for-dbms_cloud.html).
`01_acl.sql` and `02_wallet_acl.sql` are the two halves of
[Configure ACEs for a User or Role to Use DBMS_CLOUD](https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/configure-aces-user-role-use-dbms_cloud.html).

Run `00_diagnose.sql` as the schema that will call `DBMS_CLOUD`, not as `SYS`.
The question is whether *that user* has what it needs, and `SYS` answers a
different question. Check 3 is the exception. It lives in `CDB$ROOT`, so run
`00_diagnose_root.sql` as `SYS` for it.

## What is not here

**Building the wallet.** The certificates are not part of the Oracle distribution.
Oracle's source is `dbc_certs.tar`, linked from
[Create SSL Wallet with Certificates](https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/create-ssl-wallet-with-certificates.html).
The wallet has to be auto-login. These scripts start from a wallet that already exists.

## Two things worth knowing before you start

**Your application schema needs its own ACEs.** Oracle says the `DBMS_CLOUD` family
runs with invoker's rights, and its per-user page has you grant a host ACE and a
wallet ACE to each user or role that calls it. On the instance measured, `DBMS_CLOUD`,
`DBMS_CLOUD_AI`, `DBMS_CLOUD_AI_AGENT` and `DBMS_CLOUD_PIPELINE` are all
`AUTHID CURRENT_USER`. I have not tested which principal's ACE the outbound call is
checked against.

**`ssl_wallet` and `wallet_root` can both be null. That is expected.** Oracle's setup
puts the wallet location in the `SSL_WALLET` database property, set in `CDB$ROOT`.
Look in `DATABASE_PROPERTIES`, not `V$PARAMETER`. On the instance measured, both
parameters were null and `SSL_WALLET` was set, in the root and in the PDB.

Written against Oracle AI Database 26ai Enterprise Edition in a container. Measured
2026-09-27 on 23.26.1.0.0, a CDB with one PDB: the home ships `catclouduser.sql` and
`dbms_cloud_install.sql`, and `C##CLOUD$SERVICE` owns 13 `DBMS_CLOUD*` packages, all
`VALID` in the root and the PDB. Not tested on other editions. Autonomous Database
needs none of this.
