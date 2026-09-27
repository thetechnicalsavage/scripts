-- v1.1 - NL2SQL accuracy lab: remove everything the lab created.
--        v1.1: genai_host validated before use.
--
-- Run as : SYSDBA (or a DBA account) connected to the PDB.
-- Usage  : @99_cleanup.sql <genai_host>
-- Re-run : safe; each step skips what is already gone.
--
-- Dropping the user removes its tables, views, profiles, credential, vector indexes and
-- the pipeline job. The host ACE and the grants on other schemas' objects are not owned
-- by the user, so they are removed explicitly first.

set verify off feedback off serveroutput on
whenever sqlerror exit failure
define genai_host = &1

begin
  if not regexp_like('&genai_host', '^[a-z0-9.-]+$') then
    raise_application_error(-20001, 'genai_host must be a DNS name');
  end if;
end;
/

begin
  dbms_network_acl_admin.remove_host_ace(
    host             => '&genai_host',
    ace              => xs$ace_type(privilege_list => xs$name_list('connect', 'resolve', 'http'),
                                    principal_name => 'NL2SQL_LAB',
                                    principal_type => xs_acl.ptype_db),
    remove_empty_acl => true);
  dbms_output.put_line('host ACE removed');
exception
  when others then dbms_output.put_line('host ACE: ' || sqlerrm);
end;
/

begin
  for j in (select job_name from dba_scheduler_jobs where owner = 'NL2SQL_LAB') loop
    dbms_scheduler.drop_job('NL2SQL_LAB.' || j.job_name, force => true);
    dbms_output.put_line('dropped job ' || j.job_name);
  end loop;
end;
/

drop user if exists NL2SQL_LAB cascade;

select count(*) as lab_users_left from dba_users where username = 'NL2SQL_LAB';
select count(*) as lab_aces_left  from dba_host_aces where principal = 'NL2SQL_LAB';
