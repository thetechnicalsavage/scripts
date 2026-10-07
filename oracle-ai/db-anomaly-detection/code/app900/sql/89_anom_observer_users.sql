-- v1.0 - SYSDBA in ora26ai / ORCLPDB1 (the observer) for application 900.
--        Run as SYSDBA in the PDB, never in CDB$ROOT, with stdin closed:
--          sqlplus -s -L / as sysdba  then  alter session set container=ORCLPDB1;
--          @89_anom_observer_users.sql '<ANOMOPS pwd>' '<ANOM_FEED pwd>'
--        v1.0: first version, 01-Oct-2026.
--
--        ANOMOPS   owns the observer: metric tables, the models, scoring, grading, the scheduler jobs and the
--                  two database links to the target. No ANY privilege.
--        ANOM_FEED the load driver's login on the observer. CREATE SESSION only here; 90 grants it INSERT
--                  and UPDATE on ANOMOPS.APP_MINUTE and nothing else (an APM agent's footprint).
--        Idempotent: tablespace and users are created only when absent; an existing user keeps its password.
set serveroutput on size unlimited
set verify off
set feedback off
whenever sqlerror exit failure rollback

define ops_pwd  = &1
define feed_pwd = &2

declare
  procedure chk (p_name varchar2, p_val varchar2) is
  begin
    -- chr(38) is the ampersand, kept out of the literal so SQL*Plus substitution never sees it
    if p_val is null or length(p_val) < 12 or regexp_like(p_val, '[''"[:space:][:cntrl:]]')
       or instr(p_val, chr(38)) > 0 then
      raise_application_error(-20900, '89: argument for '||p_name||' is missing or not acceptable');
    end if;
  end;
begin
  if sys_context('USERENV','CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '89: run in the PDB (ORCLPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV','SESSION_USER') != 'SYS' then
    raise_application_error(-20900, '89: run as SYSDBA');
  end if;
  chk('ANOMOPS', '&ops_pwd.'); chk('ANOM_FEED', '&feed_pwd.');
end;
/

declare
  n     number;
  l_dir varchar2(512);
begin
  select count(*) into n from dba_tablespaces where tablespace_name = 'ANOM_DATA';
  if n = 0 then
    begin
      execute immediate 'create tablespace ANOM_DATA datafile size 256M autoextend on next 64M maxsize 4G';
    exception
      when others then
        -- no OMF destination: next to the PDB's SYSTEM datafile, as 75 does
        select substr(file_name, 1, instr(file_name, '/', -1)) into l_dir
          from dba_data_files where tablespace_name = 'SYSTEM' and rownum = 1;
        execute immediate 'create tablespace ANOM_DATA datafile '''||l_dir||'anom_data01.dbf'''
                       || ' size 256M autoextend on next 64M maxsize 4G';
    end;
    dbms_output.put_line('  tablespace ANOM_DATA created');
  else
    dbms_output.put_line('  tablespace ANOM_DATA already present');
  end if;
end;
/

declare
  procedure mk (p_user varchar2, p_pwd varchar2, p_extra varchar2) is
    n number;
  begin
    select count(*) into n from dba_users where username = p_user;
    if n = 0 then
      execute immediate 'create user '||p_user||' identified by "'||p_pwd||'"'||p_extra;
      dbms_output.put_line('  '||p_user||' created');
    else
      dbms_output.put_line('  '||p_user||' already present; password unchanged');
    end if;
  end;
begin
  mk('ANOMOPS',   '&ops_pwd.',  ' default tablespace ANOM_DATA quota unlimited on ANOM_DATA');
  mk('ANOM_FEED', '&feed_pwd.', ' default tablespace ANOM_DATA');
end;
/

grant create session, create table, create view, create sequence, create procedure, create job,
      create trigger, create type, create synonym, create mining model, create database link to ANOMOPS;
grant create session to ANOM_FEED;

select '  '||username||': '||account_status||', default tablespace '||default_tablespace as state
  from dba_users where username in ('ANOMOPS','ANOM_FEED') order by username;

undefine ops_pwd
undefine feed_pwd
undefine 1
undefine 2
