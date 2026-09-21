-- v1.0 | 00_diagnose.sql | READ-ONLY
--
-- Why SELECT AI does not work on your on-premise or container Oracle Database.
-- Four things have to be true. Each one fails differently, and none of the
-- errors names the thing that is actually missing.
--
-- Run as the schema that will call DBMS_CLOUD, not as SYS. The point is to ask
-- "can THIS user make the call", and SYS answers a different question.
--
-- Touches nothing. Four queries against data dictionary views.
-- The FIRST check that returns no rows is your problem. Stop there.

set pagesize 200 linesize 160 feedback off
column check_name format a34
column detail     format a90

prompt
prompt === 1. Is the DBMS_CLOUD package family installed at all? ===
prompt     Empty here means PLS-00201 when you call it, and nothing tells you
prompt     a whole common user is missing. On installs that have it, the owner
prompt     is a common user, not SYS. That owner is the tell.
select owner, object_name, object_type, status
  from all_objects
 where object_name in ('DBMS_CLOUD','DBMS_CLOUD_AI','DBMS_CLOUD_AI_AGENT',
                       'DBMS_CLOUD_PIPELINE','DBMS_CLOUD_NOTIFICATION')
   and object_type = 'PACKAGE'
 order by object_name;

prompt
prompt === 2. Can THIS schema make an outbound call? ===
prompt     The cloud user having its own ACE does not cover you. The schema
prompt     that calls DBMS_CLOUD is the one opening the connection.
select host, principal, privilege
  from dba_host_aces
 where principal in (user, 'C##CLOUD$SERVICE')
 order by principal, privilege;

prompt
prompt === 3. Is there a CA wallet, and may THIS schema use it? ===
prompt     Without this, every HTTPS call fails certificate validation and the
prompt     error does not mention a wallet. Note that ssl_wallet and
prompt     wallet_root can both be null and the call still works: the wallet is
prompt     reached through this ACE, not through an instance parameter.
select wallet_path, principal, privilege
  from dba_wallet_aces
 where principal in (user, 'C##CLOUD$SERVICE')
 order by principal, privilege;

prompt
prompt === 4. Is there a credential to sign the request with? ===
select credential_name, username, enabled
  from user_credentials
 order by credential_name;

prompt
prompt === context: the two parameters people expect to matter, and do not ===
select name, nvl(value,'(null)') as value
  from v$parameter
 where name in ('ssl_wallet','wallet_root')
 order by name;

set feedback on
