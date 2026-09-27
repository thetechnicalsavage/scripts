-- v1.1 | 02_wallet_acl.sql | CHANGES STATE
-- v1.1 2026-09-27: header only, names Oracle's per-user step and drops an untested claim about the error; SQL unchanged.
--
-- Grant one schema the right to use the CA wallet.
-- Run as SYS in the PDB. Repeat for every schema that will call DBMS_CLOUD.
--
-- This is the wallet half of Oracle's per-user step, "Configure ACEs for a
-- User or Role to Use DBMS_CLOUD":
-- https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/configure-aces-user-role-use-dbms_cloud.html
-- Oracle: "The DBMS_CLOUD family of packages have the INVOKER right
-- privilege." That is why each calling user needs its own wallet ACE, as well
-- as the host ACE from 01_acl.sql.
--
-- The wallet holds trusted root certificates only, a CA bundle. It is not a
-- user certificate and there is nothing secret in it.
--
-- Pass the wallet path for YOUR instance. Oracle's example writes it as
-- 'file:<dir>'. The path is per-install; do not copy one out of a blog post,
-- including this one.

define app_schema  = '&1'
define wallet_path = '&2'

begin
  dbms_network_acl_admin.append_wallet_ace(
    wallet_path => '&wallet_path.',
    ace         => xs$ace_type(
                     privilege_list => xs$name_list('use_client_certificates',
                                                    'use_passwords'),
                     principal_name => upper('&app_schema.'),
                     principal_type => xs_acl.ptype_db));
end;
/

select wallet_path, principal, privilege
  from dba_wallet_aces
 where principal = upper('&app_schema.')
 order by privilege;
