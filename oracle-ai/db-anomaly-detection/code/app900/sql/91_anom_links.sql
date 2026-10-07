-- v1.1 - ANOMOPS in ora26ai / ORCLPDB1 (the observer) for application 900: the database links to the target.
--        Run as ANOMOPS, both passwords on SQL*Plus's standard input, never on a command line:
--          tools/anom_obs_run.sh run 91_anom_links.sql <log> --pwd ANOM_MON_PWD --pwd CHAOS_CTL_PWD
--        which hands SQL*Plus:  @91_anom_links.sql "<ANOM_MON pwd>" "<CHAOS_CTL pwd>"
--        v1.1: ANOM_CHAOS_LINK (contract section 9): connects to CHAOS_CTL, the demo's fault-injection login that
--              holds EXECUTE on SHOP.PKG_CHAOS and SELECT on SHOP.CHAOS_RUN and DRIVER_CONTROL (93). Created only
--              when absent, like ANOM_MON_LINK. Its proof checks the link user and reports (does not require)
--              PKG_CHAOS and CHAOS_RUN, which are built in parallel. Takes a second argument.
--        v1.0: first version, 01-Oct-2026.
--
--        ANOM_MON_LINK   connects to ANOM_MON at //oradb1:1521/FREEPDB1 (the docker network name of the target).
--                        ANOM_MON holds CREATE SESSION and SELECT on five V$ metric views (86), nothing else, so
--                        the link can read metrics and can change nothing.
--        ANOM_CHAOS_LINK connects to CHAOS_CTL at the same service. Demo only: in production it would not exist.
--                        Used by INCIDENT_TRUTH's refresh (94) and the hidden-incident scheduler (96).
--        Both are created only when absent: an existing link is kept as it is (its stored password is not
--        replaced). Each argument is refused when blank or when it holds a quote, an ampersand, whitespace or a
--        control character. verify is off so SQL*Plus never prints a line with a substituted password.
set serveroutput on size unlimited
set verify off
set feedback off
whenever sqlerror exit failure rollback

define mon_pwd   = &1
define chaos_pwd = &2

declare
  procedure chk (p_name varchar2, p_val varchar2) is
  begin
    -- chr(38) is the ampersand, kept out of the literal so SQL*Plus substitution never sees it
    if p_val is null or length(p_val) < 12 or regexp_like(p_val, '[''"[:space:][:cntrl:]]')
       or instr(p_val, chr(38)) > 0 then
      raise_application_error(-20900, '91: argument for '||p_name||' is missing or not acceptable');
    end if;
  end;
begin
  if sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '91: run in the PDB (ORCLPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, '91: run as ANOMOPS');
  end if;
  chk('ANOM_MON', '&mon_pwd.');
  chk('CHAOS_CTL', '&chaos_pwd.');
end;
/

declare
  n number;
begin
  -- global_names is FALSE here; a domain suffix on the stored name is tolerated
  select count(*) into n from user_db_links where db_link = 'ANOM_MON_LINK' or db_link like 'ANOM_MON_LINK.%';
  if n = 0 then
    execute immediate 'create database link ANOM_MON_LINK connect to ANOM_MON identified by "&mon_pwd."'
                   || ' using ''//oradb1:1521/FREEPDB1''';
    dbms_output.put_line('  ANOM_MON_LINK created');
  else
    dbms_output.put_line('  ANOM_MON_LINK already present; kept as it is');
  end if;

  -- v1.1: the fault-injection link (demo only)
  select count(*) into n from user_db_links where db_link = 'ANOM_CHAOS_LINK' or db_link like 'ANOM_CHAOS_LINK.%';
  if n = 0 then
    execute immediate 'create database link ANOM_CHAOS_LINK connect to CHAOS_CTL identified by "&chaos_pwd."'
                   || ' using ''//oradb1:1521/FREEPDB1''';
    dbms_output.put_line('  ANOM_CHAOS_LINK created');
  else
    dbms_output.put_line('  ANOM_CHAOS_LINK already present; kept as it is');
  end if;
end;
/

undefine mon_pwd
undefine chaos_pwd
undefine 1
undefine 2

-- prove the metric link: the target's PDB metrics are visible, and the link user is the metric user
declare
  n_metrics number;
  n_hist    number;
  n_wait    number;
  l_user    varchar2(128);
  l_host    varchar2(2000);
begin
  select username, host into l_user, l_host from user_db_links
   where db_link = 'ANOM_MON_LINK' or db_link like 'ANOM_MON_LINK.%';
  select count(*) into n_metrics from v$con_sysmetric@ANOM_MON_LINK where group_id = 18;
  select count(distinct begin_time) into n_hist from v$con_sysmetric_history@ANOM_MON_LINK where group_id = 18;
  select count(*) into n_wait from v$con_waitclassmetric@ANOM_MON_LINK;
  commit;  -- ends the distributed transaction the remote reads opened
  execute immediate 'alter session close database link ANOM_MON_LINK';
  dbms_output.put_line('  link user '||l_user||', service '||regexp_substr(l_host, '[^/]+$'));
  dbms_output.put_line('  group-18 metrics visible: '||n_metrics||'; history intervals: '||n_hist
                       ||'; wait classes: '||n_wait);
  if l_user != 'ANOM_MON' then
    raise_application_error(-20900, '91: ANOM_MON_LINK connects as '||l_user||', not ANOM_MON');
  end if;
  if n_metrics < 25 or n_wait = 0 then
    raise_application_error(-20900, '91: the link does not show the metric views');
  end if;
end;
/

-- v1.1: prove the chaos link: it logs on as CHAOS_CTL. PKG_CHAOS and CHAOS_RUN (93) are reported, not required:
-- the observer tolerates their absence until 93 is installed (INCIDENT_TRUTH's refresh logs and skips).
declare
  l_user   varchar2(128);
  l_remote varchar2(128);
  n_pkg    number;
  n_run    number;
begin
  select username into l_user from user_db_links
   where db_link = 'ANOM_CHAOS_LINK' or db_link like 'ANOM_CHAOS_LINK.%';
  -- USER and SYS_CONTEXT against dual@link are evaluated locally; USER_USERS is read on the target
  select username into l_remote from user_users@ANOM_CHAOS_LINK;
  select count(case when object_name = 'PKG_CHAOS' and object_type = 'PACKAGE' then 1 end),
         count(case when object_name = 'CHAOS_RUN' and object_type = 'TABLE' then 1 end)
    into n_pkg, n_run
    from all_objects@ANOM_CHAOS_LINK
   where owner = 'SHOP' and object_name in ('PKG_CHAOS', 'CHAOS_RUN');
  commit;  -- ends the distributed transaction
  execute immediate 'alter session close database link ANOM_CHAOS_LINK';
  dbms_output.put_line('  ANOM_CHAOS_LINK user '||l_user||' (remote session user '||l_remote||')'
                       ||'; SHOP.PKG_CHAOS visible: '||case when n_pkg > 0 then 'yes' else 'not yet' end
                       ||'; SHOP.CHAOS_RUN visible: '||case when n_run > 0 then 'yes' else 'not yet' end);
  if l_user != 'CHAOS_CTL' or l_remote != 'CHAOS_CTL' then
    raise_application_error(-20900, '91: ANOM_CHAOS_LINK connects as '||l_remote||', not CHAOS_CTL');
  end if;
end;
/
