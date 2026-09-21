# DBMS_CLOUD on a non-Autonomous database

Every Select AI article assumes Autonomous Database, where `DBMS_CLOUD` is already
there. On an on-premise or container database it is not, and nothing in the error
says so. You get:

```
PLS-00201: identifier 'DBMS_CLOUD_AI' must be declared
```

Four things have to be true before `SELECT AI` works. Each one fails differently
and none of the errors names the missing piece.

| | Check | What its absence looks like |
|---|---|---|
| 1 | The `DBMS_CLOUD` package family is installed | `PLS-00201`, with no hint that a whole common user is missing |
| 2 | A host ACE lets **your schema** make outbound calls | The call is refused before it leaves the database |
| 3 | A wallet ACE lets your schema use the CA wallet | HTTPS fails certificate validation and the wallet is never mentioned |
| 4 | A credential exists | The request has nothing to sign with |

## The scripts

| | |
|---|---|
| [`00_diagnose.sql`](00_diagnose.sql) | **READ-ONLY.** All four checks. The first one that returns nothing is your problem. Start here. |
| [`01_acl.sql`](01_acl.sql) | Host ACE for one schema |
| [`02_wallet_acl.sql`](02_wallet_acl.sql) | Wallet ACE for one schema |
| [`03_credential.sql`](03_credential.sql) | `CREATE_CREDENTIAL` in its OCI form, prompted, nothing stored |
| [`04_smoke_test.sql`](04_smoke_test.sql) | One `SEND_REQUEST`. 200 means all four are satisfied |

Run `00_diagnose.sql` as the schema that will call `DBMS_CLOUD`, not as `SYS`.
The question is whether *that user* can make the call, and `SYS` answers a
different question.

## What is not here

**Installing the package family itself.** That procedure is delivered through My
Oracle Support, it has changed across releases, and reproducing it here would be
republishing Oracle's material. These scripts cover the other three
prerequisites, which are the ones people actually get wrong.

## Two things worth knowing before you start

**Your application schema needs its own ACEs.** The cloud user having them does
not cover you. The schema calling `DBMS_CLOUD` is the one opening the connection.

**`ssl_wallet` and `wallet_root` can both be null and everything still works.**
The wallet is reached through the wallet ACE, not through an instance parameter.
If you are searching for why those parameters are empty, that is the answer.

Written against Oracle AI Database 26ai Enterprise Edition in a container. Not
tested on other editions. Autonomous Database needs none of this.
