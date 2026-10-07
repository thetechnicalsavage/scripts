-- v1.0 - app 900 (phase 5c): a training exclusion window for one of the observer's planned contacts with the target
--        during training (contract v1.8 section 13; 94 v1.3 TRAIN_EXCLUDE). Run as ANOMOPS in ORCLPDB1:
--          tools/anom_obs_run.sh run anom_train_exclude.sql <log> -- ADD  <code> <minutes> <back>
--          tools/anom_obs_run.sh run anom_train_exclude.sql <log> -- END  <code> 0 0
--          tools/anom_obs_run.sh run anom_train_exclude.sql <log> -- LIST ALL 0 0
--        ADD : [this minute - <back> min, this minute + <minutes>] (UTC; minutes 1-120, back 2-120), through
--              PKG_ANOM_TRAIN.add_exclusion, creator 'tools/anom_train_exclude.sql'. Run it BEFORE the contact
--              with back = 2. A contact that came first (the deploy of 94 v1.3, which creates the table) is
--              covered afterwards with back = the minutes since it began + 2.
--        END : the newest window of <code> made by this tool gets to_ts = this minute + 3 min (the contact is over:
--              a long ADD is cut back, an overrun is covered). from_ts never moves.
--        LIST: every window, oldest first.
--        The runner passes no spaces, so each reason is fixed here by its code:
--          86_REVOKE   86 v1.2 revoke of V$SQLSTATS/V$EVENT_NAME
--          DEPLOY_5C   phase 5c: 94 v1.3 deploy (truth loop stopped and restarted, the install's INCIDENT_TRUTH
--                      refresh over ANOM_CHAOS_LINK)
--          SUITES_5C   phase 5c: SQL test suites that refresh INCIDENT_TRUTH over ANOM_CHAOS_LINK (phase4, phase4b,
--                      phase5c)
--        No password here. Nothing touches the target.
--        v1.0: first version, 02-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 220
whenever sqlerror exit failure rollback
variable a_act  varchar2(10)
variable a_code varchar2(30)
variable a_min  varchar2(10)
variable a_back varchar2(10)
exec :a_act := upper('&1'); :a_code := upper('&2'); :a_min := '&3'; :a_back := '&4';
set define off

declare
  c_by   constant varchar2(60) := 'tools/anom_train_exclude.sql';
  l_now  date := cast(sys_extract_utc(systimestamp) as date);
  l_min  date := trunc(cast(sys_extract_utc(systimestamp) as date), 'MI');
  l_n    number := to_number(trim(:a_min) default null on conversion error, '999');
  l_back number := to_number(trim(:a_back) default null on conversion error, '999');
  l_rsn  varchar2(400);
  l_id   number;
  l_from date;
  l_to   date;
begin
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' or sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, 'anom_train_exclude: run as ANOMOPS in ORCLPDB1');
  end if;
  l_rsn := case :a_code
             when '86_REVOKE' then '86 v1.2 revoke of V$SQLSTATS/V$EVENT_NAME'
             when 'DEPLOY_5C' then 'phase 5c: 94 v1.3 deploy (truth loop stopped and restarted, the install''s '
                                   || 'INCIDENT_TRUTH refresh over ANOM_CHAOS_LINK)'
             when 'SUITES_5C' then 'phase 5c: SQL test suites that refresh INCIDENT_TRUTH over ANOM_CHAOS_LINK '
                                   || '(phase4, phase4b, phase5c)'
           end;
  if :a_act = 'ADD' then
    if l_rsn is null then
      raise_application_error(-20900, 'anom_train_exclude: unknown code ' || :a_code);
    end if;
    if l_n is null or l_n != trunc(l_n) or l_n not between 1 and 120 then
      raise_application_error(-20900, 'anom_train_exclude: minutes must be a whole number from 1 to 120');
    end if;
    if l_back is null or l_back != trunc(l_back) or l_back not between 2 and 120 then
      raise_application_error(-20900, 'anom_train_exclude: back must be a whole number from 2 to 120');
    end if;
    l_id := PKG_ANOM_TRAIN.add_exclusion(l_min - l_back / 1440, l_min + l_n / 1440, l_rsn, c_by);
    dbms_output.put_line('ADD id ' || l_id || ': ' || to_char(l_min - l_back / 1440, 'YYYY-MM-DD HH24:MI') || 'Z to '
                         || to_char(l_min + l_n / 1440, 'YYYY-MM-DD HH24:MI') || 'Z, at '
                         || to_char(l_now, 'HH24:MI:SS') || 'Z: ' || l_rsn);
  elsif :a_act = 'END' then
    if l_rsn is null then
      raise_application_error(-20900, 'anom_train_exclude: unknown code ' || :a_code);
    end if;
    select max(exclude_id) into l_id from TRAIN_EXCLUDE where reason = l_rsn and created_by = c_by;
    if l_id is null then
      raise_application_error(-20900, 'anom_train_exclude: no window of ' || :a_code || ' to end');
    end if;
    select from_ts, to_ts into l_from, l_to from TRAIN_EXCLUDE where exclude_id = l_id;
    if l_min + 3 / 1440 <= l_from then
      raise_application_error(-20900, 'anom_train_exclude: the window of ' || :a_code || ' starts later');
    end if;
    update TRAIN_EXCLUDE set to_ts = l_min + 3 / 1440 where exclude_id = l_id;
    commit;
    dbms_output.put_line('END id ' || l_id || ': ' || to_char(l_from, 'YYYY-MM-DD HH24:MI') || 'Z to '
                         || to_char(l_min + 3 / 1440, 'YYYY-MM-DD HH24:MI') || 'Z (was to '
                         || to_char(l_to, 'HH24:MI') || 'Z), at ' || to_char(l_now, 'HH24:MI:SS') || 'Z');
  elsif :a_act != 'LIST' then
    raise_application_error(-20900, 'anom_train_exclude: argument 1 must be ADD, END or LIST');
  end if;
  for r in (select exclude_id, from_ts, to_ts, reason, created_by, created_ts from TRAIN_EXCLUDE order by from_ts,
                   exclude_id) loop
    dbms_output.put_line('  #' || r.exclude_id || '  ' || to_char(r.from_ts, 'YYYY-MM-DD HH24:MI:SS') || 'Z - '
                         || to_char(r.to_ts, 'HH24:MI:SS') || 'Z  ' || round((r.to_ts - r.from_ts) * 1440) || ' min  '
                         || substr(r.created_by, 1, 30) || '  ' || substr(r.reason, 1, 120));
  end loop;
end;
/
exit
