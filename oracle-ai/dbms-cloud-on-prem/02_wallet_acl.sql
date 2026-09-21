-- v1.0 | 02_wallet_acl.sql | CHANGES STATE
--
-- Grant one schema the right to use the CA wallet. This is the step that is
-- easiest to miss, because skipping it fails at certificate validation and the
-- error never says "wallet".
--
-- The wallet holds trusted root certificates only, a CA bundle. It is not a
-- user certificate and there is nothing secret in it.
--
-- Pass the wallet path for YOUR instance. The path is per-install; do not copy
-- one out of a blog post, including this one.

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
