-- v1.1 | 00_diagnose.sql | READ-ONLY
-- v1.1 2026-09-27: runs as the calling schema with no DBA_* views; five checks; wallet read from DATABASE_PROPERTIES.
--
-- Why SELECT AI does not work on your on-premise or container Oracle Database.
-- Five things have to be true. The DBMS_CLOUD family is not installed by
-- default, and the other four are setup that follows the install.
--
-- Run as the schema that will call DBMS_CLOUD, in the PDB, not as SYS. The
-- point is to ask what THIS user has, and SYS answers a different question.
-- Every view below is one that schema can read.
--
-- Check 3, the host ACE for C##CLOUD$SERVICE, lives in CDB$ROOT and only a
-- DBA can see it. This script prints a pointer for it. Run
-- 00_diagnose_root.sql as SYS in CDB$ROOT to check it.
--
-- Touches nothing. The FIRST check that returns no rows is your problem.
-- Stop there.

set pagesize 200 linesize 160 feedback off
column owner           format a18
column object_name     format a30
column property_name   format a14
column property_value  format a70
column host            format a40
column privilege       format a24
column status          format a10
column wallet_path     format a70
column credential_name format a30
column username        format a40

prompt
prompt === 1. Is the DBMS_CLOUD package family installed, and can you see it? ===
prompt     Empty here means PLS-00201 when you call it. The owner is the common
prompt     user C##CLOUD$SERVICE, not SYS. ALL_OBJECTS lists only what this
prompt     schema can access: if 00_diagnose_root.sql lists the packages and this
prompt     does not, the install is there and the gap is this schema's access.
select owner, object_name, object_type, status
  from all_objects
 where owner = 'C##CLOUD$SERVICE'
   and object_name like 'DBMS\_CLOUD%' escape '\'
   and object_type = 'PACKAGE'
 order by object_name;

prompt
prompt === 2. Does the SSL_WALLET database property name a wallet? ===
prompt     No SSL_WALLET row counts as empty. HTTP_PROXY is listed beside it, as
prompt     in Oracle's own verification query. This shows the property is set. It
prompt     cannot show that the wallet is auto-login or holds the right roots.
select property_name, property_value
  from database_properties
 where property_name in ('SSL_WALLET','HTTP_PROXY')
 order by property_name;

prompt
prompt === 3. Host ACE for C##CLOUD$SERVICE, in CDB$ROOT ===
prompt     Not visible from here. USER_HOST_ACES shows only this schema's own
prompt     ACEs, and DBA_HOST_ACES needs DBA or SELECT_CATALOG_ROLE.
prompt     Run 00_diagnose_root.sql as SYS in CDB$ROOT. Oracle documents an
prompt     HTTP 401 error from DBMS_CLOUD calls when this ACE is missing.

prompt
prompt === 4. Does THIS schema have its own host ACE and wallet ACE? ===
prompt     Oracle: the DBMS_CLOUD family runs with invoker's rights, and each
prompt     calling user gets both ACEs. Two queries. Either one empty counts.
select host, lower_port, upper_port, privilege, status
  from user_host_aces
 order by host, privilege;

select wallet_path, privilege, status
  from user_wallet_aces
 order by wallet_path, privilege;

prompt
prompt === 5. Is there a credential to sign the request with? ===
select credential_name, username, enabled
  from user_credentials
 order by credential_name;

prompt
prompt === context: ssl_wallet and wallet_root ===
prompt     V$PARAMETER ssl_wallet and wallet_root can both be null. That is
prompt     expected. Oracle's setup puts the wallet location in the SSL_WALLET
prompt     database property, which check 2 reads. On the instance measured,
prompt     both parameters were null and the property was set in root and PDB.
prompt     No query here: V$PARAMETER needs privileges a normal schema may not
prompt     have, and an ORA-00942 would only muddy the output above.

set feedback on
