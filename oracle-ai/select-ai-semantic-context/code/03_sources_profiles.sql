-- v1.1 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.0 2026-11-11 - brief 15: the business glossary as a file semantic source, and the three AI profiles compared.
--   A  SALES_BASE   schema metadata only (technical comments, constraints)
--   B  SALES_SEM    A + semantic source SALES_GLOSSARY (glossary file in a database directory, vector-indexed with
--                   an in-database ONNX embedding model)
--   C  SALES_ANNOT  A + "annotations": true (the same glossary text sits in table annotations, see 01_schema.sql)
--   Same model and settings everywhere: xai.grok-4.20-non-reasoning on OCI Generative AI (us-phoenix-1), temperature 0.
--   Needs: SAI_SEM_DIR (00_admin_setup.sql), model ALL_MINILM_L12_V2 in this schema (tools/load_onnx_blob.py).
--   Run as SAI_SEM_LAB, re-runnable: any SQL client, with ${OCI_COMPARTMENT_ID} replaced by your compartment OCID (README)

-- 1. the glossary file (one definition per line; same wording as the annotations)
declare
  f utl_file.file_type;
  procedure w(p varchar2) is begin utl_file.put_line(f, p); end;
begin
  f := utl_file.fopen('SAI_SEM_DIR', 'sales_glossary.txt', 'w', 32767);
  w('Sales glossary (business definitions used in reports).');
  w('Net revenue: GROSS_AMT minus DISC_AMT, summed over orders with STATUS = SHIPPED only; BOOKED, CANCELLED and RETURNED orders add nothing.');
  w('Average order value: net revenue divided by the number of SHIPPED orders.');
  w('Late shipment: a SHIPPED order whose SHIPPED_DATE is more than 2 days after PROMISED_DATE.');
  w('Fiscal year: 1 April to 31 March, named by the year it ends (FY2026 = 1 April 2025 to 31 March 2026); this fiscal year is the one that contains today. Fiscal-year figures and "last N days" windows use ORDER_DATE.');
  w('High-value customer: lifetime net revenue above 50000.');
  w('At-risk customer: a high-value customer with no order other than CANCELLED in the last 90 days and at least one open escalation.');
  w('Active customer: ACCT_STATUS = OPEN and at least one SHIPPED order in the last 365 days.');
  w('Churned customer: ACCT_STATUS = CLOSED, or no SHIPPED order in the last 180 days.');
  w('Repeat customer: 3 or more SHIPPED orders.');
  w('Open ticket: STATUS is OPEN or PENDING.');
  w('Open escalation: a ticket with ESCL_FLAG = Y that is not CLOSED.');
  w('Critical ticket: SEV = 1 (1 is the most severe, 4 the least).');
  utl_file.fclose(f);
exception
  when others then
    if utl_file.is_open(f) then utl_file.fclose(f); end if;
    raise;
end;
/
select object_name, bytes from table(dbms_cloud.list_files('SAI_SEM_DIR')) where object_name = 'sales_glossary.txt';

-- 2. embedding profile for the file source (in-database model: no embedding call leaves the database)
begin
  dbms_cloud_ai.drop_profile('SEM_EMBED', force => true);
  dbms_cloud_ai.create_profile(profile_name => 'SEM_EMBED', attributes => '{
    "provider": "oci", "region": "us-phoenix-1", "credential_name": "SAI_GENAI_CRED",
    "oci_compartment_id": "${OCI_COMPARTMENT_ID}",
    "embedding_model": "database:SAI_SEM_LAB.ALL_MINILM_L12_V2"}');
end;
/

-- 3. the semantic source (detach it from the profiles first so a re-run can drop it)
begin
  for p in (select profile_name from user_cloud_ai_profiles where profile_name in ('SALES_SEM')) loop
    dbms_cloud_ai.drop_profile(p.profile_name, force => true);
  end loop;
  dbms_ai_sem.drop_semantic_source('SALES_GLOSSARY', force => true);
  dbms_ai_sem.create_semantic_source(
    source_name => 'SALES_GLOSSARY',
    type        => 'file',
    attributes  => '{"scope": "global",
                     "source_endpoint": "SAI_SEM_DIR:sales_glossary.txt",
                     "profile_name": "SEM_EMBED"}',
    description => 'Business glossary of the sales reports');
end;
/

-- 4. the three NL2SQL profiles
declare
  c_common constant varchar2(4000) := '"provider": "oci", "region": "us-phoenix-1", "credential_name": "SAI_GENAI_CRED",
    "oci_compartment_id": "${OCI_COMPARTMENT_ID}", "model": "xai.grok-4.20-non-reasoning", "temperature": 0,
    "comments": true, "constraints": true, "case_sensitive_values": true, "enforce_object_list": true,
    "object_list": [{"owner": "SAI_SEM_LAB", "name": "REGIONS"}, {"owner": "SAI_SEM_LAB", "name": "CUSTOMERS"},
                    {"owner": "SAI_SEM_LAB", "name": "ORDERS"}, {"owner": "SAI_SEM_LAB", "name": "TICKETS"}]';
  procedure make(p_name varchar2, p_extra varchar2) is
  begin
    dbms_cloud_ai.drop_profile(p_name, force => true);
    dbms_cloud_ai.create_profile(profile_name => p_name, attributes => '{'||c_common||', '||p_extra||'}');
  end;
begin
  make('SALES_BASE',  '"annotations": false');
  make('SALES_SEM',   '"annotations": false, "semantic_source": ["SALES_GLOSSARY"]');
  make('SALES_ANNOT', '"annotations": true');
end;
/
select source_name, source_type, status from user_ai_semantic_sources where source_name = 'SALES_GLOSSARY';
select attribute_name, dbms_lob.substr(attribute_value, 120, 1) value from user_ai_semantic_source_attributes
 where source_name = 'SALES_GLOSSARY' order by 1;
select profile_name, status from user_cloud_ai_profiles where profile_name in ('SALES_BASE', 'SALES_SEM', 'SALES_ANNOT', 'SEM_EMBED') order by 1;
select pipeline_name, status from user_cloud_pipelines where pipeline_name like 'SALES_GLOSSARY%';
