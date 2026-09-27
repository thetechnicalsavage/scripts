-- v1.1 | 01_acl.sql | CHANGES STATE
-- v1.1 2026-09-27: header only, names Oracle's per-user step and why it is per user; SQL unchanged.
--
-- Grant one schema the right to make outbound network calls.
-- Run as SYS in the PDB. Repeat for every schema that will call DBMS_CLOUD.
--
-- This is the host half of Oracle's per-user step, "Configure ACEs for a
-- User or Role to Use DBMS_CLOUD":
-- https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/configure-aces-user-role-use-dbms_cloud.html
-- Oracle: "The DBMS_CLOUD family of packages have the INVOKER right
-- privilege." That is why each calling user needs its own host ACE and
-- wallet ACE (02_wallet_acl.sql), on top of the database-level step in
-- 01_root_ace_and_wallet.sql. Oracle's page grants http and http_proxy;
-- this script also grants connect and resolve.
--
-- host => '*' is wide. It is fine on a disposable demo box and wrong anywhere
-- else. Narrow it to the endpoints you actually call before this goes near a
-- real environment.

define app_schema = '&1'
define acl_host   = '&2'

begin
  dbms_network_acl_admin.append_host_ace(
    host => '&acl_host.',
    ace  => xs$ace_type(
              privilege_list => xs$name_list('connect','resolve',
                                             'http','http_proxy'),
              principal_name => upper('&app_schema.'),
              principal_type => xs_acl.ptype_db));
end;
/

-- Prove it landed.
select host, principal, privilege
  from dba_host_aces
 where principal = upper('&app_schema.')
 order by privilege;
