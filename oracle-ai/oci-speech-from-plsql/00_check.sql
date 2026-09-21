-- v1.0 | 00_check.sql | READ-ONLY
-- Before any of this works you need DBMS_CLOUD, an ACL, a wallet ACE and a
-- credential. If any of these come back empty, stop and run
-- ../dbms-cloud-on-prem/00_diagnose.sql instead.

set pagesize 100 linesize 140 feedback off
prompt === credential to sign the calls with ===
select credential_name, username, enabled from user_credentials order by 1;
prompt
prompt === can this schema reach the two endpoints it needs? ===
select host, principal, privilege from dba_host_aces
 where principal = user order by privilege;
prompt
prompt === CA wallet, or every HTTPS call dies in certificate validation ===
select wallet_path, principal, privilege from dba_wallet_aces
 where principal = user order by privilege;
set feedback on
