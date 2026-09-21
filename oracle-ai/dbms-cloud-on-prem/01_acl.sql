-- v1.0 | 01_acl.sql | CHANGES STATE
--
-- Grant one schema the right to make outbound network calls.
-- Run as SYS in the PDB. Repeat for every schema that will call DBMS_CLOUD.
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
