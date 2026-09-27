-- v1.0 | 01_root_ace_and_wallet.sql | CHANGES STATE
--
-- Oracle's database-level step for DBMS_CLOUD. Run once, as SYS in CDB$ROOT.
-- It follows Oracle's "Configure the Database with ACEs for DBMS_CLOUD":
-- https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/configure-dateabase-aces-for-dbms_cloud.html
--
-- Two changes:
--   1. A host ACE for C##CLOUD$SERVICE on port 443, privileges http and
--      http_proxy. Oracle's note: without it, DBMS_CLOUD calls fail with an
--      HTTP 401 error.
--   2. The SSL_WALLET database property, set to your wallet directory. Oracle
--      sets it only when the container is CDB$ROOT, and so does this script.
--
-- Build the wallet first. It is an auto-login wallet of trusted root
-- certificates. The certificates are not part of the Oracle distribution.
-- Oracle's source is dbc_certs.tar, linked from "Create SSL Wallet with
-- Certificates":
-- https://docs.oracle.com/en/database/oracle/oracle-database/26/sutil/create-ssl-wallet-with-certificates.html
--
-- acl_host: Oracle's example uses '*', which is every host. That is wide.
-- Narrow it to the endpoints you actually call.
--
-- This does not set HTTP_PROXY. Each calling user also needs its own ACEs,
-- granted in the PDB: 01_acl.sql and 02_wallet_acl.sql.
--
-- Run: @01_root_ace_and_wallet.sql <wallet_dir> <acl_host>

whenever sqlerror exit failure

define wallet_dir = '&1'
define acl_host   = '&2'

-- Stop before changing anything if this is not CDB$ROOT.
begin
  if sys_context('userenv','con_name') <> 'CDB$ROOT' then
    raise_application_error(-20001,
      'Run this as SYS in CDB$ROOT, not in '||sys_context('userenv','con_name')||'.');
  end if;
end;
/

-- 1. Host ACE for the common user that owns the DBMS_CLOUD family.
begin
  dbms_network_acl_admin.append_host_ace(
    host       => '&acl_host.',
    lower_port => 443,
    upper_port => 443,
    ace        => xs$ace_type(
                    privilege_list => xs$name_list('http', 'http_proxy'),
                    principal_name => 'C##CLOUD$SERVICE',
                    principal_type => xs_acl.ptype_db));
end;
/

-- 2. The wallet location, as a database property, set from the root only.
begin
  if sys_context('userenv','con_name') = 'CDB$ROOT' then
    execute immediate 'alter database property set ssl_wallet=''&wallet_dir.''';
  end if;
end;
/

-- Prove it landed.
select property_name, property_value
  from database_properties
 where property_name in ('SSL_WALLET','HTTP_PROXY')
 order by property_name;

select host, lower_port, upper_port, principal, privilege
  from dba_host_aces
 where principal = 'C##CLOUD$SERVICE'
 order by host, privilege;

whenever sqlerror continue
