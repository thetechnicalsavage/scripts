-- v1.0 | 00_diagnose_root.sql | READ-ONLY
--
-- The part of the diagnosis a normal schema cannot see. Run as SYS in
-- CDB$ROOT.
--
-- 00_diagnose.sql runs as the calling schema in the PDB. It cannot read
-- check 3, the host ACE for C##CLOUD$SERVICE, because that needs a DBA.
-- This script shows check 3, plus checks 1 and 2 as the root sees them.
-- The numbering matches 00_diagnose.sql.
--
-- Touches nothing. A container check and three queries.

set pagesize 200 linesize 160 feedback off
column con_name       format a20
column owner          format a18
column object_name    format a30
column property_name  format a14
column property_value format a70
column host           format a40
column principal      format a20
column privilege      format a24

prompt
prompt === where am I: this must say CDB$ROOT ===
select sys_context('userenv','con_name') as con_name from dual;

prompt
prompt === 1. Packages owned by C##CLOUD$SERVICE, and their status ===
prompt     On the instance measured: 13 DBMS_CLOUD* packages, all VALID, in the
prompt     root and in the PDB. This reads CDB$ROOT only. To check a PDB the
prompt     same way, run this query as SYS in that PDB.
select owner, object_name, object_type, status
  from dba_objects
 where owner = 'C##CLOUD$SERVICE'
   and object_type = 'PACKAGE'
 order by object_name;

prompt
prompt === 2. The SSL_WALLET and HTTP_PROXY database properties ===
prompt     No SSL_WALLET row means the property is not set.
select property_name, property_value
  from database_properties
 where property_name in ('SSL_WALLET','HTTP_PROXY')
 order by property_name;

prompt
prompt === 3. Host ACEs for C##CLOUD$SERVICE ===
prompt     Oracle: without this ACE, DBMS_CLOUD calls fail with an HTTP 401 error.
select host, lower_port, upper_port, principal, privilege
  from dba_host_aces
 where principal = 'C##CLOUD$SERVICE'
 order by host, privilege;

set feedback on
