-- 00b_admin_runtime_user.sql v0.2 - Brief 12 step 8: runtime user AGENTMEM_RT. No quota: it owns
--   nothing. Object grants come from AGENTMEM (11.3). The caller defines agentmem_rt_pwd on stdin. Re-run: safe.
--   v0.2: (day 1, after its first run) the comment that followed the last grant's semicolon on the same line is on its
--   own line: SQL*Plus does not take a semicolon followed by a comment as a terminator, so v0.1's model grant stayed
--   in the buffer and never ran (02 read back AGENTMEM_RT with CREATE SESSION only). v0.1: first version.
whenever sqlerror exit failure
set verify off echo off feedback on termout on
alter session set container = ORCLPDB1;
set termout off
create user if not exists AGENTMEM_RT identified by "&agentmem_rt_pwd"
  default tablespace USERS temporary tablespace TEMP;
set termout on
undefine agentmem_rt_pwd
grant create session to AGENTMEM_RT;
-- AGENTMEM_RT runs the embedding SQL itself. No comment may follow a semicolon on its line (SQL*Plus).
grant select on mining model ASKORACLE.ALL_MINILM_L12_V2 to AGENTMEM_RT;
