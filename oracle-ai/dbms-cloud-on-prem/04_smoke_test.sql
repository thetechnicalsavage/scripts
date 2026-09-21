-- v1.0 | 04_smoke_test.sql | READ-ONLY against your database
--
-- Proves the whole outbound path in one call. If this returns 200, all four
-- prerequisites are satisfied. If it fails, run 00_diagnose.sql: the first
-- check that comes back empty is the reason.
--
-- This does make a real outbound HTTPS request to the endpoint you give it.

set serveroutput on
accept cred_name char prompt 'Credential name: '
accept test_uri  char prompt 'HTTPS endpoint to GET: '

declare
  l_resp dbms_cloud_types.resp;
  l_code number;
begin
  l_resp := dbms_cloud.send_request(
              credential_name => '&cred_name.',
              uri             => '&test_uri.',
              method          => 'GET');
  l_code := dbms_cloud.get_response_status_code(l_resp);
  dbms_output.put_line('HTTP '||l_code);
  if l_code between 200 and 299 then
    dbms_output.put_line('All four prerequisites are satisfied.');
  else
    dbms_output.put_line('Reached the endpoint but it refused. The database '||
                         'side is working; this is an authorization or URL '||
                         'problem, not a DBMS_CLOUD setup problem.');
  end if;
end;
/
