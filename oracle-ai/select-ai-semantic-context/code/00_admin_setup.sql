-- v1.2 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.1 2026-11-11 - CREATE MINING MODEL for SAI_SEM_LAB: the file source needs an embedding model and OCI's
--                    embedText returned ORA-20404 for the Cohere models in this tenancy, so an ONNX model is loaded in-database.
-- v1.0 2026-11-11 - brief 15 lab on Autonomous AI Database 26ai (23.26.4): two lab users for semantic context, their
--   rights, the outbound rule to OCI Generative AI and a directory for the file source. Run as ADMIN, re-runnable:
--     as ADMIN, with the binds :SAI_SEM_LAB_PWD and :SAI_SEM_USER_PWD set first (README)
--   Passwords arrive as binds, never as script text. An existing user keeps its password.
--   SAI_SEM_LAB owns the data, the semantic sources and the profiles; SAI_SEM_USER is a second profile owner, used to
--   test GRANT_SEMANTIC_SOURCE_ACCESS. Region: us-phoenix-1 (change the host below for another region).

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
  ensure_user('SAI_SEM_LAB',  :SAI_SEM_LAB_PWD);
  ensure_user('SAI_SEM_USER', :SAI_SEM_USER_PWD);
end;
/

grant create session, create table, create view, create procedure, create sequence to sai_sem_lab;
grant create session, create table, create view, create procedure to sai_sem_user;
alter user sai_sem_lab quota 500m on data;
alter user sai_sem_user quota 50m on data;

grant execute on dbms_cloud to sai_sem_lab;
grant execute on dbms_cloud_ai to sai_sem_lab;
grant execute on dbms_ai_sem to sai_sem_lab;
grant execute on dbms_cloud to sai_sem_user;
grant execute on dbms_cloud_ai to sai_sem_user;
grant execute on dbms_ai_sem to sai_sem_user;

-- the file source builds a vector index through a pipeline (scheduler job)
grant execute on dbms_cloud_pipeline to sai_sem_lab;
grant create job to sai_sem_lab;
grant create mining model to sai_sem_lab;

create or replace directory sai_sem_dir as 'sai_sem';
grant read, write on directory sai_sem_dir to sai_sem_lab;

begin
  for u in (select column_value usr from table(sys.odcivarchar2list('SAI_SEM_LAB', 'SAI_SEM_USER'))) loop
    dbms_network_acl_admin.append_host_ace(
      host => 'inference.generativeai.us-phoenix-1.oci.oraclecloud.com',   -- sanitize-ok: hostname-fqdn (Oracle's public endpoint)
      ace  => xs$ace_type(privilege_list => xs$name_list('connect', 'resolve', 'http'),
                          principal_name => u.usr, principal_type => xs_acl.ptype_db));
  end loop;
end;
/

select username, account_status from dba_users where username like 'SAI\_SEM%' escape '\' order by 1;
select grantee, table_name, privilege from dba_tab_privs where grantee like 'SAI\_SEM%' escape '\' order by 1, 2;
select principal, host, privilege from dba_host_aces where principal like 'SAI\_SEM%' escape '\' order by 1, 3;
