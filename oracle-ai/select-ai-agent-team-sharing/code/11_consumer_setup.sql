-- v1.3 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.2 2026-11-11 - neutral lab cities (blog privacy rule): Oslo, Denver, Osaka; second account Calgary.
-- v1.1 2026-11-11 - Codex r2: the drop of ACCOUNTS ignores only ORA-00942 (first run).
-- v1.0 2026-11-11 - brief 16: what each consumer brings (and the outsider, for the refusal test): its own AI profile
--   and its own ACCOUNTS table. The billing city differs per schema, so an answer shows whose data the shared team read.
--   Run as SAI_TEAM_APP, SAI_TEAM_APP2 and SAI_TEAM_OUTSIDER, re-runnable:
--     as each consumer, with ${OCI_COMPARTMENT_ID} replaced by your compartment OCID (README)
begin
  dbms_cloud_ai.drop_profile('APP_GROK', force => true);
  dbms_cloud_ai.create_profile(profile_name => 'APP_GROK', attributes => '{
    "provider": "oci", "region": "us-phoenix-1", "credential_name": "SAI_GENAI_CRED",
    "oci_compartment_id": "${OCI_COMPARTMENT_ID}", "model": "xai.grok-4.20-non-reasoning", "temperature": 0}');
end;
/
declare
  e_no_table exception;
  pragma exception_init(e_no_table, -942);
begin
  execute immediate 'drop table accounts purge';
exception
  when e_no_table then null;   -- first run: nothing to drop
end;
/
create table accounts (
  account_name  varchar2(30) primary key,
  billing_city  varchar2(30) not null,
  plan          varchar2(20) not null,
  status        varchar2(10) not null
);
insert into accounts
select 'blue_harbor',
       case sys_context('userenv', 'current_user') when 'SAI_TEAM_APP' then 'Oslo'
                                                   when 'SAI_TEAM_APP2' then 'Denver' else 'Osaka' end,
       'GOLD', 'ACTIVE' from dual;
insert into accounts values ('green_valley', 'Calgary', 'BRONZE', 'SUSPENDED');
commit;
select user, account_name, billing_city, plan, status from accounts order by 2;
