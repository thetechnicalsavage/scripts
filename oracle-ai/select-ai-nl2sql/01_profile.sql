-- v1.0 | 01_profile.sql | CHANGES STATE
--
-- A Select AI NL2SQL profile for use in front of a real user.
-- The first attribute is the one that matters: `comments` DEFAULTS TO FALSE,
-- so every COMMENT ON COLUMN you wrote is invisible to the model until you
-- turn it on, and the model is guessing what your columns hold.

accept compartment char prompt 'OCI compartment OCID: '

declare
  l_attr clob;
begin
  select json_object(
           'provider'           value 'oci',
           'credential_name'    value 'OCI_GENAI_CRED',
           'region'             value '&compartment_region.',
           'oci_compartment_id' value '&compartment.',
           'model'              value '&model_name.',
           'max_tokens'         value 900,

           -- THE ONE THAT MATTERS. Defaults to false.
           'comments'              value 'true'  format json,
           'constraints'           value 'true'  format json,
           'annotations'           value 'true'  format json,

           -- The model writes a literal the way it chose to capitalise it.
           -- It asked for charge_type = 'Data' against a column holding 'data'.
           'case_sensitive_values' value 'false' format json,

           -- It may not reach past the listed views whatever it is asked.
           -- Belt to the VPD braces; this is not the guardrail on its own.
           'enforce_object_list'   value 'true'  format json,

           -- Same question, same SQL, every time. A demo that answers
           -- differently on the second run is not a demo.
           'temperature'           value 0,
           'seed'                  value 42,

           -- OFF, deliberately. See the README: with this on, the model
           -- answered a question about late payments from a context that was
           -- about data usage, and was confidently wrong.
           'conversation'          value 'false' format json,

           'object_list' value json_array(
              json_object('owner' value '&app_schema.', 'name' value 'MY_BILL'),
              json_object('owner' value '&app_schema.', 'name' value 'MY_CHARGES'),
              json_object('owner' value '&app_schema.', 'name' value 'MY_USAGE'),
              json_object('owner' value '&app_schema.', 'name' value 'MY_PAYMENTS'),
              json_object('owner' value '&app_schema.', 'name' value 'MY_PLAN')
              returning clob)
           returning clob) into l_attr from dual;

  begin dbms_cloud_ai.drop_profile('BILLCHAT', force => true);
  exception when others then null; end;

  dbms_cloud_ai.create_profile(profile_name => 'BILLCHAT', attributes => l_attr);
end;
/
