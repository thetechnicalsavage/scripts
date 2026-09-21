-- v1.0 | 03_credential.sql | CHANGES STATE
--
-- Create an OCI credential from an API signing key.
-- Run as the application schema.
--
-- NOTHING REAL GOES IN THIS FILE. Every value is a substitution variable and
-- is prompted for at run time. If you edit real values in, do not commit it.
--
-- private_key is the PEM body: the BEGIN and END lines removed and every
-- newline stripped, so it is one long string.

accept cred_name    char prompt 'Credential name: '
accept user_ocid    char prompt 'User OCID: '
accept tenancy_ocid char prompt 'Tenancy OCID: '
accept fingerprint  char prompt 'Key fingerprint: '
accept private_key  char prompt 'Private key (PEM body, one line): ' hide

begin
  dbms_cloud.create_credential(
    credential_name => '&cred_name.',
    user_ocid       => '&user_ocid.',
    tenancy_ocid    => '&tenancy_ocid.',
    private_key     => '&private_key.',
    fingerprint     => '&fingerprint.');
end;
/

-- username comes back as the user OCID. That is the tell that this is the OCI
-- form of the call rather than the username/password form.
select credential_name, username, enabled
  from user_credentials
 where credential_name = upper('&cred_name.');
