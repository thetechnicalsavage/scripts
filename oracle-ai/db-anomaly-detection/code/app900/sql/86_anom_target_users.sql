-- v1.3 - SYSDBA in oradb1 / FREEPDB1 (the target, the demo's "production" database) for application 900.
--        Run as SYSDBA in the PDB, never in CDB$ROOT, with stdin closed:
--          sqlplus -s -L / as sysdba  then  alter session set container=FREEPDB1;
--          @86_anom_target_users.sql '<SHOP pwd>' '<SHOP_APP pwd>' '<ANOM_MON pwd>' '<CHAOS_CTL pwd>' '<ASH Y|N>'
--        v1.3: (phase 6.3, after EVAL_TO) ANOM_MON keeps SELECT on the four metric views the collector reads (92);
--              V$SERVICEMETRIC, granted by v1.0-v1.2 and never read, is no longer granted and is revoked where
--              held (contract 13.9, open item 1). 07-Oct-2026.
--        v1.2: (phase 5b review, Codex finding) argument 5 = Y grants only V$ACTIVE_SESSION_HISTORY, the one view
--              the drill-down reads (97 PKG_ANOM_UI.read_ash). v1.0-v1.1 also granted V$SQLSTATS (it holds SQL
--              text) and V$EVENT_NAME, which nothing reads; every run now revokes those two where ANOM_MON holds
--              them. Argument 5 = N now also revokes V$ACTIVE_SESSION_HISTORY where held (N says the pack is not
--              licensed here; v1.1 left an earlier grant in place and printed "no ASH grant"). NOT yet run on
--              oradb1: the 02-Oct-2026 01:20:44Z run was v1.1 and left the two extras in place.
--        v1.1: header only - CHAOS_CTL's grant and PKG_CHAOS are in 93, not 88 (no change to the statements;
--              nothing to re-run), 01-Oct-2026.
--        v1.0: first version, 01-Oct-2026.
--
--        Four accounts, each with the least it needs:
--          SHOP       owns the demo application: tables and PKG_SHOP (87, 88), PKG_CHAOS (93). Demo only.
--          SHOP_APP   the application's login, used by the load driver; object grants come from 87.
--          ANOM_MON   the ONLY account a production database would get: CREATE SESSION and SELECT on the
--                     metric views below. It owns nothing and can change nothing.
--          CHAOS_CTL  demo only: EXECUTE on SHOP.PKG_CHAOS (granted in 93), so the observer can start a
--                     scenario through its own link without holding SHOP's password.
--        Argument 5 = Y also grants ANOM_MON the ASH drill-down view (v1.2: V$ACTIVE_SESSION_HISTORY only);
--        N removes it where held. Use Y only where the Diagnostics Pack is licensed (that view is in the pack).
--
--        Idempotent: the tablespace and the users are created only when absent; an existing user keeps
--        its password. Every password is an argument, refused when blank or when it holds a quote, an
--        ampersand, whitespace or a control character. Nothing here touches DBOPS or any other account.
set serveroutput on size unlimited
set verify off
set feedback off
whenever sqlerror exit failure rollback

define shop_pwd     = &1
define shopapp_pwd  = &2
define mon_pwd      = &3
define chaos_pwd    = &4
define ash_grant    = &5

declare
  procedure chk (p_name varchar2, p_val varchar2) is
  begin
    -- chr(38) is the ampersand, kept out of the literal so SQL*Plus substitution never sees it
    if p_val is null or length(p_val) < 12 or regexp_like(p_val, '[''"[:space:][:cntrl:]]')
       or instr(p_val, chr(38)) > 0 then
      raise_application_error(-20900, '86: argument for '||p_name||' is missing or not acceptable');
    end if;
  end;
begin
  if sys_context('USERENV','CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '86: run in the PDB (FREEPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV','SESSION_USER') != 'SYS' then
    raise_application_error(-20900, '86: run as SYSDBA');
  end if;
  chk('SHOP', '&shop_pwd.'); chk('SHOP_APP', '&shopapp_pwd.');
  chk('ANOM_MON', '&mon_pwd.'); chk('CHAOS_CTL', '&chaos_pwd.');
  if '&ash_grant.' not in ('Y','N') then
    raise_application_error(-20900, '86: argument 5 must be Y or N');
  end if;
end;
/

-- ------------------------------------------------------------------ tablespace for SHOP
declare
  n     number;
  l_dir varchar2(512);
begin
  select count(*) into n from dba_tablespaces where tablespace_name = 'SHOP_DATA';
  if n = 0 then
    -- no OMF destination in this container: put the file next to the PDB's SYSTEM datafile
    select substr(file_name, 1, instr(file_name, '/', -1)) into l_dir
      from dba_data_files where tablespace_name = 'SYSTEM' and rownum = 1;
    execute immediate 'create tablespace SHOP_DATA datafile '''||l_dir||'shop_data01.dbf'''
                   || ' size 512M autoextend on next 128M maxsize 6G';
    dbms_output.put_line('  tablespace SHOP_DATA created');
  else
    dbms_output.put_line('  tablespace SHOP_DATA already present');
  end if;
end;
/

-- ------------------------------------------------------------------ users (created only when absent)
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
  mk('SHOP',      '&shop_pwd.',    ' default tablespace SHOP_DATA quota unlimited on SHOP_DATA');
  mk('SHOP_APP',  '&shopapp_pwd.', ' default tablespace SHOP_DATA');
  mk('ANOM_MON',  '&mon_pwd.',     ' default tablespace SHOP_DATA');
  mk('CHAOS_CTL', '&chaos_pwd.',   ' default tablespace SHOP_DATA');
end;
/

grant create session, create table, create view, create sequence, create procedure,
      create job, create trigger, create type, create synonym to SHOP;
grant create session to SHOP_APP;
grant create session to CHAOS_CTL;

-- ANOM_MON: the production footprint. Metric views only.
grant create session to ANOM_MON;
grant select on sys.v_$con_sysmetric          to ANOM_MON;
grant select on sys.v_$con_sysmetric_history  to ANOM_MON;
grant select on sys.v_$con_waitclassmetric    to ANOM_MON;
grant select on sys.v_$system_wait_class      to ANOM_MON;

-- v1.3: v1.0-v1.2 also granted V$SERVICEMETRIC, which nothing reads; it goes wherever it is held
-- (idempotent: a privilege ANOM_MON does not hold is left alone, never revoked blind)
begin
  for r in (select table_name from dba_tab_privs
             where grantee = 'ANOM_MON' and owner = 'SYS' and privilege = 'SELECT'
               and table_name = 'V_$SERVICEMETRIC') loop
    execute immediate 'revoke select on sys.' || dbms_assert.simple_sql_name(r.table_name) || ' from ANOM_MON';
    dbms_output.put_line('  ANOM_MON: SELECT on ' || r.table_name || ' revoked (never read by the collector)');
  end loop;
end;
/

-- optional, licensed: the ASH drill-down for the incident page. v1.2: the one view it reads, nothing more
begin
  if '&ash_grant.' = 'Y' then
    execute immediate 'grant select on sys.v_$active_session_history to ANOM_MON';
    dbms_output.put_line('  ANOM_MON: ASH drill-down view granted (V$ACTIVE_SESSION_HISTORY, Diagnostics Pack)');
  else
    -- v1.2: N converges: a grant left by an earlier Y run goes (the pack is not licensed here)
    for r in (select table_name from dba_tab_privs
               where grantee = 'ANOM_MON' and owner = 'SYS' and privilege = 'SELECT'
                 and table_name = 'V_$ACTIVE_SESSION_HISTORY') loop
      execute immediate 'revoke select on sys.' || dbms_assert.simple_sql_name(r.table_name) || ' from ANOM_MON';
      dbms_output.put_line('  ANOM_MON: SELECT on ' || r.table_name || ' revoked (argument 5 = N)');
    end loop;
    dbms_output.put_line('  ANOM_MON: no ASH grant (metric views only)');
  end if;
  -- v1.2: v1.0-v1.1 granted these two with the ASH view; nothing reads them, so they go wherever they are held
  -- (idempotent: a privilege ANOM_MON does not hold is left alone, never revoked blind)
  for r in (select table_name from dba_tab_privs
             where grantee = 'ANOM_MON' and owner = 'SYS' and privilege = 'SELECT'
               and table_name in ('V_$SQLSTATS', 'V_$EVENT_NAME')
             order by table_name) loop
    execute immediate 'revoke select on sys.' || dbms_assert.simple_sql_name(r.table_name) || ' from ANOM_MON';
    dbms_output.put_line('  ANOM_MON: SELECT on ' || r.table_name || ' revoked (never read by the drill-down)');
  end loop;
end;
/

select '  '||username||': '||account_status||', default tablespace '||default_tablespace as state
  from dba_users where username in ('SHOP','SHOP_APP','ANOM_MON','CHAOS_CTL') order by username;

undefine shop_pwd
undefine shopapp_pwd
undefine mon_pwd
undefine chaos_pwd
undefine ash_grant
undefine 1
undefine 2
undefine 3
undefine 4
undefine 5
