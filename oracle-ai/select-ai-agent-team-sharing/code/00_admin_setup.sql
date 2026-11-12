-- v1.1 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.0 2026-11-11 - brief 16 lab on Autonomous AI Database 26ai (23.26.4): a team owner, two consumers and an
--   outsider, a role for role-based team access, their rights and the outbound rule to OCI Generative AI. Run as ADMIN,
--   re-runnable:
--     as ADMIN, with the binds :SAI_TEAM_OWNER_PWD, :SAI_TEAM_APP_PWD, :SAI_TEAM_APP2_PWD, :SAI_TEAM_OUTSIDER_PWD set (README)
--   Passwords arrive as binds, never as script text. An existing user keeps its password.
--   SAI_TEAM_OWNER  owns the team, its agent, task and tool function
--   SAI_TEAM_APP    consumer with a direct team grant
--   SAI_TEAM_APP2   consumer that gets team access only through the role SAI_TEAM_RUNNER
--   SAI_TEAM_OUTSIDER  same rights as the consumers, never granted the team
--   Package rights are direct grants (not DWROLE), so disabling roles in a session tests only the team role.

declare
  procedure ensure_user(p_user varchar2, p_pwd varchar2) is
    l_n pls_integer;
  begin
    select count(*) into l_n from dba_users where username = p_user;
    if l_n > 0 then
      dbms_output.put_line('user '||p_user||' exists: password left as it is');
    else
      if not regexp_like(p_pwd, '^[A-Za-z][A-Za-z0-9]{11,29}$') then
        raise_application_error(-20001, 'the password for '||p_user||' is not 12-30 letters and digits');
      end if;
      execute immediate 'create user '||dbms_assert.simple_sql_name(p_user)||' identified by "'||p_pwd||'"';
      dbms_output.put_line('user '||p_user||' created');
    end if;
  end;
begin
  ensure_user('SAI_TEAM_OWNER',    :SAI_TEAM_OWNER_PWD);
  ensure_user('SAI_TEAM_APP',      :SAI_TEAM_APP_PWD);
  ensure_user('SAI_TEAM_APP2',     :SAI_TEAM_APP2_PWD);
  ensure_user('SAI_TEAM_OUTSIDER', :SAI_TEAM_OUTSIDER_PWD);
end;
/

declare
  l_n pls_integer;
begin
  select count(*) into l_n from dba_roles where role = 'SAI_TEAM_RUNNER';
  if l_n = 0 then
    execute immediate 'create role sai_team_runner';
    dbms_output.put_line('role SAI_TEAM_RUNNER created');
  end if;
end;
/

begin
  for u in (select column_value usr from table(sys.odcivarchar2list(
              'SAI_TEAM_OWNER', 'SAI_TEAM_APP', 'SAI_TEAM_APP2', 'SAI_TEAM_OUTSIDER'))) loop
    execute immediate 'grant create session, create table, create view, create procedure, create sequence to '||u.usr;
    execute immediate 'alter user '||u.usr||' quota 50m on data';
    execute immediate 'grant execute on dbms_cloud to '||u.usr;
    execute immediate 'grant execute on dbms_cloud_ai to '||u.usr;
    execute immediate 'grant execute on dbms_cloud_ai_agent to '||u.usr;
    dbms_network_acl_admin.append_host_ace(
      host => 'inference.generativeai.us-phoenix-1.oci.oraclecloud.com',   -- sanitize-ok: hostname-fqdn (Oracle's public endpoint)
      ace  => xs$ace_type(privilege_list => xs$name_list('connect', 'resolve', 'http'),
                          principal_name => u.usr, principal_type => xs_acl.ptype_db));
  end loop;
end;
/

grant sai_team_runner to sai_team_app2;

select username, account_status from dba_users where username like 'SAI\_TEAM%' escape '\' order by 1;
select grantee, granted_role, default_role from dba_role_privs where granted_role = 'SAI_TEAM_RUNNER';
select grantee, table_name, privilege from dba_tab_privs where grantee like 'SAI\_TEAM%' escape '\' order by 1, 2;
select principal, host, privilege from dba_host_aces where principal like 'SAI\_TEAM%' escape '\' order by 1, 3;
