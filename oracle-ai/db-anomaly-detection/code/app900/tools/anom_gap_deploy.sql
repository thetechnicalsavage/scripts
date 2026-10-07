-- v1.1 - app 900 (phase 5d): runs one observer script of 95-98 in the truth loop's hourly hand-over, in ora26ai /
--        ORCLPDB1 as ANOMOPS (no password is substituted here):
--          tools/anom_obs_run.sh run anom_gap_deploy.sql <log> -- 96_anom_grade.sql
--        Why. 94's truth loop (ANOM_TRUTH_JOB, contract section 12.2) keeps one session for an hour and calls
--        PKG_ANOM_GRADE.mirror_plan in it after every 5-minute refresh. Replacing a package that a session has
--        already used discards that session's package state, so its next call fails once (ORA-04068): one plan
--        mirror skipped and one WARN logged in the training window. The hand-over avoids it without any contact
--        with the target: the loop's session ends at HH:59:50, the scheduler starts the next run at once, and that
--        session calls the package for the first time after its first refresh at HH:00:20. A script run between
--        HH:59:52 and HH:00:12 UTC, while the running loop (if any) is younger than 25 seconds, replaces the package
--        before any long-lived session holds it. The scoring and collector jobs never call 96.
--        What it does: refuses (-20930) any name that is not a 95-98 observer script; waits (from any time of the
--        hour, up to 65 minutes) for that window; runs /tmp/app900/<script> (staged by tools/anom_obs_run.sh stage);
--        then compiles every INVALID object of ANOMOPS (the dependants of a changed specification, e.g. 98's
--        PKG_ANOM_LIVE body after 96 v1.5) and fails when one stays invalid. Missing the window is a failure and the
--        script is not run. Target contact: none (no link, no truth refresh here; the scripts it accepts make none
--        at install). Times UTC.
--        v1.1: (phase 5d review) 94 is refused: its install refreshes INCIDENT_TRUTH over ANOM_CHAOS_LINK (a target
--              contact) and stops and restarts the truth loop itself, so it needs no hand-over and, in training, a
--              tools/anom_train_exclude.sql window around it. v1.0 accepted 94-98 and said none of them contacts the
--              target. 02-Oct-2026.
--        v1.0: first version, 02-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 200
whenever sqlerror exit failure rollback

begin
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' or sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20930, 'anom_gap_deploy: run as ANOMOPS in ORCLPDB1');
  end if;
  -- v1.1: 95-98 only (94 contacts the target at install and handles the truth loop itself)
  if not regexp_like('&1', '^9[5-8]_anom_[a-z_]+\.sql$') then
    raise_application_error(-20930, 'anom_gap_deploy: the argument must name an observer script of 95-98 (94 '
                                    || 'refreshes INCIDENT_TRUTH over ANOM_CHAOS_LINK at install: run it directly, '
                                    || 'inside a training exclusion window)');
  end if;
end;
/

-- the hand-over window: [HH:59:52, HH:00:12] UTC and no truth loop session older than 25 s
declare
  c_from_s constant pls_integer := 59 * 60 + 52;    -- seconds into the hour
  c_to_s   constant pls_integer := 12;
  c_young  constant pls_integer := 25;              -- the new loop session starts at about HH:59:50
  l_dead   timestamp := sys_extract_utc(systimestamp) + interval '65' minute;
  l_now    timestamp;
  l_sec    number;
  l_age    number;
begin
  loop
    l_now := sys_extract_utc(systimestamp);
    l_sec := extract(minute from l_now) * 60 + extract(second from l_now);
    select max(extract(day from elapsed_time) * 86400 + extract(hour from elapsed_time) * 3600
               + extract(minute from elapsed_time) * 60 + extract(second from elapsed_time))
      into l_age
      from user_scheduler_running_jobs where job_name = 'ANOM_TRUTH_JOB';
    exit when (l_sec >= c_from_s or l_sec <= c_to_s) and (l_age is null or l_age < c_young);
    if l_now > l_dead then
      raise_application_error(-20930, 'anom_gap_deploy: no hand-over window within 65 minutes (truth loop age '
                                      || nvl(to_char(round(l_age)), 'none') || ' s); nothing was run');
    end if;
    -- coarse until the last minute of the hour, then once a second
    dbms_session.sleep(case when l_sec >= 3540 or l_sec <= c_to_s then 1 else 20 end);
  end loop;
  dbms_output.put_line('anom_gap_deploy: window open at ' || to_char(l_now, 'YYYY-MM-DD HH24:MI:SS') || 'Z (truth loop '
                       || nvl(to_char(round(l_age)) || ' s old', 'not running') || '): running &1');
end;
/

@/tmp/app900/&1

-- dependants of a changed specification: compiled now, not at their next call
declare
  n number;
begin
  for o in (select object_name, object_type from user_objects
             where status = 'INVALID'
               and object_type in ('PACKAGE', 'PACKAGE BODY', 'VIEW', 'PROCEDURE', 'FUNCTION', 'TRIGGER')
             order by case object_type when 'PACKAGE' then 1 when 'VIEW' then 2 else 3 end, object_name) loop
    begin
      execute immediate 'alter ' || case o.object_type when 'PACKAGE BODY' then 'package' else lower(o.object_type) end
                        || ' "' || o.object_name || '" compile'
                        || case when o.object_type = 'PACKAGE BODY' then ' body' end;
      dbms_output.put_line('anom_gap_deploy: compiled ' || lower(o.object_type) || ' ' || o.object_name);
    exception
      when others then
        dbms_output.put_line('anom_gap_deploy: ' || lower(o.object_type) || ' ' || o.object_name || ': ' || sqlerrm);
    end;
  end loop;
  select count(*) into n from user_objects
   where status = 'INVALID' and object_type in ('PACKAGE', 'PACKAGE BODY', 'VIEW', 'PROCEDURE', 'FUNCTION', 'TRIGGER');
  if n > 0 then
    raise_application_error(-20930, 'anom_gap_deploy: ' || n || ' object(s) still INVALID after the script');
  end if;
  dbms_output.put_line('anom_gap_deploy: done at ' || to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD HH24:MI:SS')
                       || 'Z; no INVALID object');
end;
/
