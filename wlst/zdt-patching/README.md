<!-- v1.12 2026-11-11 - Codex r2: staging test also covers a newline in the work-folder answer and a symlinked parent.
     v1.11 2026-11-11 - Codex r1: test_stage_jdk.py also covers a malformed work-folder answer and a world-writable parent.
     v1.10 2026-11-11 - security review: 03 stages the JDK in a private folder with a checksum; tests/test_stage_jdk.py.
     v1.9 2026-11-11 - scripts repo copy: link to the post; .gitignore for env files and run output.
     v1.8 2026-11-11 - Codex r2: the three-node rule in the guide's words ("use ZDT Patching only if ... three or more
     nodes"), beside the preparing page's two-node minimum.
     v1.7 2026-11-11 - blog copy (Codex r1): no claim that every release behaves the same; credential paths under /u01; versions
     written in a form the leak gate accepts.
     v1.6 2026-11-11 - blog copy: "Outage" column now "Service impact" (offline, or rolling one node at a time).
     v1.5 2026-10-15 - Node Manager prerequisites for the hook; JDK of the other domains.
     v1.4 2026-10-15 - hook stop check on the node's processes; t3s example.
     v1.3 2026-10-15 - common-home hooks for a home used by several domains (hooks/).
     v1.2 2026-10-15 - works on the existing common Oracle homes: no per-domain homes, no custom hooks.
     v1.1 2026-10-15 - 06 checks this domain's servers and, with --expect-java, their JDK; offline test added.
     v1.0 2026-10-15 - WebLogic and JDK patching with Oracle Zero Downtime Patching (ZDT): script package. -->

# WebLogic & JDK patching with Zero Downtime Patching: scripts

Companion to [WebLogic Zero Downtime Patching in practice: from one domain to many](https://thetechnicalsavage.com/blog/weblogic-zero-downtime-patching-in-practice-from-one-domain-to-many/).
The rollout and rollback scripts default to a dry run and change nothing without `--execute`. The shell scripts build
the patched archive, copy files to the nodes or only read; none of them stops a server. Tested offline only (see Tests):
no rollout was run against a live domain for the post.

These scripts carry out Oracle's documented ZDT patching workflow, as described in *Administering Zero Downtime Patching
Workflows* (written against the 14.1.1 and 15.1.1 guides; check the guide for your release). They add nothing to the workflow itself: the rollout,
monitoring and rollback are the documented WLST commands. The shell scripts only prepare inputs and check results.

| Step in the Oracle guide | Script | Service impact |
|---|---|---|
| (before) inventory: homes, domains, JDKs, Node Manager per node | `01_inventory.sh` (read-only) | none (read-only) |
| Preparing to patch: create the patched Oracle home archive (copyBinary, pasteBinary, OPatch, copyBinary) | `02_prepare_patched_home.sh` | none (offline, on the build host) |
| Copy the archive to each node (parent folder of the Oracle home); install the new JDK at the same path on every node | `03_stage_on_nodes.sh` | none (servers keep running) |
| Create and run the workflow (rolloutUpdate / rolloutOracleHome / rolloutJavaHome), monitor it | `04_zdt_rollout.py` (WLST) | rolling: one node's servers stop and restart at a time; sessions per isSessionCompatible and shutdownTimeout |
| Roll back (rolloutUpdate with isRollback=true, or rolloutJavaHome to the previous JDK) | `05_zdt_rollback.py` (WLST) | rolling, like the rollout |
| (after) check patches and Java version on every node | `06_verify.sh` (read-only) | none (read-only) |

## Before the first rollout (per domain)

1. The domain meets the ZDT restrictions: Managed Servers in clusters that span two or more nodes (preparing page),
   and the guide says to use ZDT Patching only if the domain contains three or more nodes (patching-existing page), Node Manager running on all nodes, the Administration Server started
   with Node Manager, all targeted Managed Servers running, the domain outside the Oracle home, and the Oracle home
   installed locally on each node (same path on every node recommended). Applications deployed to the Administration
   Server cannot be updated without downtime.
2. The existing Oracle home and JDK stay as they are. Clusters that share an Oracle home must be in the same
   workflow, so the scripts target the domain. If one home is used by several domains on the same nodes
   (01_inventory.sh lists this in shared_homes.csv), use the common-home hooks below; the ZDT guide does not cover this
   case, so confirm the approach with Oracle Support before the pilot.
3. Copy `00_zdt.env.example` to `<domain>.env` and edit it (chmod 600). It holds paths only.
4. Create the WLST credential files once, interactively, on the host that runs WLST:
   ```
   wlst.sh
   connect('<admin user>', '<password>', 't3s://<admin host>:<ssl port>')
   storeUserConfig('/u01/app/oracle/zdt-keys/domainA_config.secure', '/u01/app/oracle/zdt-keys/domainA_key.secure')
   ```
   Keep both files mode 600. No script stores or prints a password.
5. JMS/JTA: services with exactly-once (migratable targets) or Always (cluster-targeted JMS) migrate on graceful
   shutdown. For other policies, write the migration JSON file (see the guide) and set `MIGRATION_PROPERTIES`.
   Expect a brief pause of each singleton service while it moves.

## Each patch cycle

```
./02_prepare_patched_home.sh domainA.env          # build host; refuses patches not marked FMW_ROLLING_*
./03_stage_on_nodes.sh domainA.env                 # archive (+ JDK) to every node, checksums, free-space check
wlst.sh 04_zdt_rollout.py domainA.env update       # dry run (isDryRun=TRUE): must end SUCCESS
wlst.sh 04_zdt_rollout.py domainA.env update --execute
./06_verify.sh domainA.env --expect-java=/u01/app/oracle/product/jdk_2026Q4
```
`update` rolls out Oracle home and JDK together; use `oraclehome` or `javahome` for one of them.
The default target is the domain, so the Administration Server is updated first, as the guide requires for Oracle
home rollouts. WLST loses its connection while the Administration Server restarts; the script reconnects and finds
the workflow again by its id.

`06_verify.sh` fails when OPatch fails, when the nodes report different patch lists, when a node runs no server of
this domain (found by `DOMAIN_HOME`), or, with `--expect-java`, when a server runs another JDK.

## Common Oracle home used by several domains: hooks

A ZDT workflow belongs to one domain: on each node it stops only that domain's servers before it replaces the home.
Other domains running from the same home on that node would keep running while the home is replaced. The hooks use
the workflow's documented extension points (ZDT guide, "Modifying Workflows Using Custom Hooks"):

| Extension point | Hook | What it does on the node being patched |
|---|---|---|
| ep_OfflineBeforeUpdate | zdt_stop_other_domains.sh | Graceful shutdown of the other domains' servers on this node (through their Administration Server; the Administration Server last if it runs here), then their Node Manager |
| ep_OfflineAfterUpdate | zdt_start_other_domains.sh | Their Node Manager, their Administration Server if it runs here (Node Manager), then their Managed Servers |

So on each node every server of every domain on the common Oracle home restarts once, on the patched home; the other nodes
keep serving. If a hook fails, the workflow rolls back. On a revert each hook does the opposite of its normal action.

1. `hooks/build_hook_jar.sh /u01/zdt/hooks/zdt_shared_home_hooks.jar` (layout from the guide: resources/, scripts/unix/).
2. Fill `other_domains.csv` (one row per other domain and node) and `extensionProperties.json` from the `.example`
   files. Create the storeUserConfig file pairs for those domains' Administration Server and Node Manager on each node.
3. Set `HOOK_JAR`, `HOOK_PROPS`, `HOOK_CONFIG` in `<domain>.env`; `03_stage_on_nodes.sh` copies them to every node and
   `04`/`05` pass `extensionProperties=` to the workflow.
4. The other domains' servers on the shared-home nodes must be started with Node Manager (as ZDT expects for the
   lead domain) and run as the same OS user as the lead domain. Each of them needs its own (per-domain) Node Manager
   with `QuitEnabled=true` in nodemanager.properties, so the hook can stop it with stopNodeManager(); the hook refuses
   to run if an other domain's Node Manager port equals the lead domain's. After a stop the hook checks the node itself: if a java process still runs from an other domain's
   home after `stopWait` seconds (default 300), the hook fails and the workflow rolls back. WebLogic processes of
   other OS users count when their command line uses the common Oracle home; unrelated installations are ignored.
5. Run the lead domain's workflow as usual. The lead domain must have servers on every node of the common Oracle home,
   otherwise that node is not patched.
6. JDK: the workflow repoints the common Oracle home's scripts and the lead domain's scripts; the other domains keep their own
   JDK setting until their own `rolloutJavaHome`. Prove both in the UAT pilot.

## Rollback

```
wlst.sh 05_zdt_rollback.py domainA.env oraclehome            # dry run
wlst.sh 05_zdt_rollback.py domainA.env oraclehome --execute  # back to <home>_backup_<cycle>, rolling
wlst.sh 05_zdt_rollback.py domainA.env javahome --execute    # JDK only: back to JAVA_HOME_PREVIOUS
```
A workflow that failed is reverted automatically by default (isAutoRevertOnFailure defaults to TRUE). A workflow that
stopped can be resumed or reverted in WLST:
```
domainRuntime(); cd('/RolloutService/rollout-service')
wf = cmo.getWorkflowTask('<workflow id>')
cmo.executeWorkflow(wf)      # continue forward
cmo.revertWorkflow(wf)       # or undo
```

## Points to confirm in the first UAT run

- Option names: the current ZDT guides call the auto-revert option `isAutoRevertOnFailure`; the WLST Command Reference
  says `autoRevertOnFailure`. The scripts leave it at its default (TRUE) and do not pass it.
- `rolloutUpdate` argument order: the documented signature is (target, archive, backup, isRollback, javaHome, options);
  one guide example passes the JDK 4th. The scripts follow the signature; the dry run shows how your WLST reads it.
- OPatch runs interactively (it asks before applying). Add flags through `OPATCH_EXTRA_ARGS` only if `opatch napply
  -help` on your OPatch version lists them.
- SPB: `spbat.sh -phase apply -oracle_home <home>` comes from the Stack Patch Bundle README (My Oracle Support); check
  the README of the bundle you download.
- cacerts: the ZDT guide does not cover keystores. `03_stage_on_nodes.sh` lists CA entries present in the current JDK
  but missing from the new one (set `CACERTS_PASS`); importing them is our recommendation.

## Tests

`python3 tests/test_hooks.py` checks the hooks (revert, failures, graceful arguments, start order).
`python3 tests/test_stage_jdk.py` runs `03_stage_on_nodes.sh --jdk-only` against a stand-in ssh: the tarball goes to a
private folder (mode 700) beside the new JDK path, never a shared /tmp; a tarball swapped on the node is refused by its
checksum; an unexpected work-folder answer (including one with an embedded newline) is never used or removed; a world-writable
parent without the sticky bit is refused, a symlinked parent is judged by its target, and a failed check refuses. The
parent of `JAVA_HOME_NEW` must be writable only by trusted users.
`python3 tests/test_wlst_logic.py` runs the WLST scripts' control flow offline against a mock of WLST and the rollout
MBeans: Administration Server restart mid-rollout, a rollout call that fails after the workflow was accepted, a workflow
ending REVERTED, a dry run and a rollback. It needs Python 3.12 or earlier (lib2to3) and no WebLogic. The shell
scripts were tested with stand-ins for copyBinary, pasteBinary, OPatch, ssh and keytool. Nothing replaces the first UAT
dry run against a real domain.

## Sources (docs.oracle.com)

- Administering Zero Downtime Patching Workflows, 14.1.1 and 15.1.1: intro, preparing, configuring_patching, customhooks.
- WLST Command Reference for WebLogic Server 14.1.1: rollout commands, connect, storeUserConfig, exit.
- Patching with OPatch 12.2.1.4.0; Administering Oracle Fusion Middleware 12.2.1.4.0, copyBinary/pasteBinary reference.
- High Availability Guide 12.2.1.4.0 (JMS/JTA data in the database); Administering Clusters 14.1.1 (database leasing).
