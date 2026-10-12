-- v1.1 - brief 14 ROUTE_LAB: Select AI profiles that name a routing profile OCID (S7). Run as ROUTE_LAB.
--        v1.1: every profile sets max_tokens 1,024, the per-call bound the budget holds (Codex code review r1,
--              finding 5).
--        v1.0: first version (PLAN.md v1.0 section 6 S7). Written in the plan stage; NOT run yet.
--
-- Usage : sqlplus -s -L ROUTE_LAB@<alias> @03_select_ai_profiles.sql   (password on stdin)
-- Needs : 01, 02, and poc/db_register.py credential/config/profiles already run (RL_CRED, RL_CONFIG, RL_PROFILE).
-- Re-run: safe; each profile is dropped and recreated.
--
--   RL_SAI_P1, RL_SAI_P3  provider oci, region us-phoenix-1, model = the routing profile OCID (read from RL_PROFILE,
--                         never written here), oci_apiformat GENERIC: what the Select AI docs ask for when `model`
--                         is an OCID (research/src/adb-dbms-cloud-ai.txt:207,239-246). Whether 23.26.1 accepts a
--                         routing profile OCID there is the question S7 answers (study Q7).
--   RL_SAI_DIRECT         the control: P1's model by name, in the shape the estate's Select AI profiles already use
--                         (brief 08: provider, credential, region, compartment, model, temperature).
-- Creating a Select AI profile makes no inference call.
whenever sqlerror exit failure rollback
set verify off feedback off serveroutput on size unlimited define off

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '03: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '03: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if user != 'ROUTE_LAB' then
    raise_application_error(-20140, '03: run as ROUTE_LAB');
  end if;
end;
/

declare
  l_comp  rl_config.value%type;
  l_model rl_profile.model_id%type;

  procedure mk (p_name varchar2, p_model varchar2, p_apiformat boolean) is
    l_attr clob;
  begin
    for p in (select profile_name from user_cloud_ai_profiles where profile_name = p_name) loop
      dbms_cloud_ai.drop_profile(profile_name => p.profile_name, force => true);
    end loop;
    if p_apiformat then
      select json_object('provider' value 'oci', 'credential_name' value 'RL_CRED', 'region' value 'us-phoenix-1',
                         'oci_compartment_id' value l_comp, 'model' value p_model,
                         'oci_apiformat' value 'GENERIC', 'temperature' value 0, 'max_tokens' value 1024
                         returning clob)
        into l_attr from dual;
    else
      select json_object('provider' value 'oci', 'credential_name' value 'RL_CRED', 'region' value 'us-phoenix-1',
                         'oci_compartment_id' value l_comp, 'model' value p_model,
                         'temperature' value 0, 'max_tokens' value 1024 returning clob)
        into l_attr from dual;
    end if;
    begin
      dbms_cloud_ai.create_profile(profile_name => p_name, attributes => l_attr, status => 'enabled',
                                   description => 'Brief 14 lab (Phoenix only); dropped at clean-up');
      dbms_output.put_line(p_name || ': created');
    exception
      when others then
        -- a refusal here is a finding (S7): record it, keep going with the other profiles
        dbms_output.put_line(p_name || ': REFUSED ' || PKG_ROUTE_LAB.redact(sqlerrm));
    end;
  end mk;
begin
  select value into l_comp from rl_config where name = 'COMPARTMENT_ID';
  for r in (select alias, ocid from rl_profile where alias in ('P1', 'P3') order by alias) loop
    if r.ocid is not null then
      mk('RL_SAI_' || r.alias, r.ocid, true);
    else
      dbms_output.put_line('RL_SAI_' || r.alias || ': not created (S4 created no profile for ' || r.alias || ')');
    end if;
  end loop;
  select model_id into l_model from rl_profile where alias = 'P1';
  mk('RL_SAI_DIRECT', l_model, false);
end;
/

prompt Select AI profiles (OCIDs reduced to type and region key):
set linesize 160 pagesize 50
column profile_name    format a14
column attribute_name  format a20
column attribute_value format a60
select a.profile_name, a.attribute_name,
       case when a.attribute_name = 'oci_compartment_id' then '<compartment>'
            else PKG_ROUTE_LAB.ocid_shape(dbms_lob.substr(a.attribute_value, 400, 1)) end as attribute_value
  from user_cloud_ai_profile_attributes a
 where a.profile_name like 'RL\_SAI\_%' escape '\'
 order by a.profile_name, a.attribute_name;
