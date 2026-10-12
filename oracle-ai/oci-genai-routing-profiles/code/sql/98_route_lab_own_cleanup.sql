-- v1.0 - brief 14 ROUTE_LAB: drop what ROUTE_LAB owns outside plain tables (Select AI profiles, the DBMS_CLOUD
--        credential) before the user is dropped. Run as ROUTE_LAB. v1.0: first version (PLAN.md v1.0 section 12
--        step 2). Written in the plan stage; NOT run yet.
--
-- Usage : sqlplus -s -L ROUTE_LAB@<alias> @98_route_lab_own_cleanup.sql   (password on stdin)
-- Then  : 99_cleanup.sql as SYSDBA.
-- Re-run: safe; each drop skips what is already gone.
-- Why a separate step: Select AI keeps its profiles in C##CLOUD$SERVICE's dictionary, so they are dropped through
-- DBMS_CLOUD_AI by their owner rather than left to DROP USER.
whenever sqlerror exit failure rollback
set verify off feedback off serveroutput on size unlimited define off

declare
  l_now timestamp := sys_extract_utc(systimestamp);
  l_hm  varchar2(4) := to_char(l_now, 'HH24MI');
begin
  if l_hm >= '0150' and l_hm < '0400' then
    raise_application_error(-20140, '98: refused, 01:50-04:00 UTC window');
  end if;
  if l_now >= timestamp '2026-10-12 09:00:00' and l_now < timestamp '2026-10-12 16:00:00' then
    raise_application_error(-20140, '98: refused, Monday 12-Oct 09:00-16:00 UTC (brief 11 live shots)');
  end if;
  if user != 'ROUTE_LAB' then
    raise_application_error(-20140, '98: run as ROUTE_LAB');
  end if;
end;
/

begin
  for p in (select profile_name from user_cloud_ai_profiles where profile_name like 'RL\_SAI\_%' escape '\') loop
    dbms_cloud_ai.drop_profile(profile_name => p.profile_name, force => true);
    dbms_output.put_line('dropped Select AI profile ' || p.profile_name);
  end loop;
  for c in (select credential_name from user_credentials where credential_name = 'RL_CRED') loop
    dbms_cloud.drop_credential(credential_name => c.credential_name);
    dbms_output.put_line('dropped credential ' || c.credential_name);
  end loop;
end;
/

select (select count(*) from user_cloud_ai_profiles where profile_name like 'RL\_SAI\_%' escape '\') as ai_profiles_left,
       (select count(*) from user_credentials) as credentials_left
  from dual;
