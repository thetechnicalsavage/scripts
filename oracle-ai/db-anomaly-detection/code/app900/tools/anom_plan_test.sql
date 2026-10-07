-- v1.1 - app 900 phase 4b: the injector-invisibility tests through the real plan path, in ora26ai / ORCLPDB1 as
--        ANOMOPS: test rows written to INCIDENT_PLAN and pushed once to the target with PKG_ANOM_GRADE.push_plan
--        (one link logon, long before the windows); the target's dispatchers start them (source SCHEDULE); the
--        report reads only the observer (METRIC_MINUTE, INCIDENT_TRUTH and INCIDENT_PLAN as 94's truth loop keeps
--        them), so watching the windows adds nothing on the target. Test plan ids are 9000-9099 (the real plan uses
--        1-99; 9100-9199 belong to test/anom_test_chaos.sql). No password here.
--          tools/anom_obs_run.sh run anom_plan_test.sql <log> -- push_artefact <lead_min> 0
--              plan 9011 app_error_burst LOW 5 min at the first whole minute >= now + lead_min (default 35), plan 9012
--              cpu_hog LOW 5 min 20 minutes after the first one ends; both windows kept clear of HH:58-HH:01 (the hourly
--              session hand-overs) so a 5-minute window and its 30-minute baseline are compared fairly
--          tools/anom_obs_run.sh run anom_plan_test.sql <log> -- push_test 5 0
--              plan 9001 app_error_burst LOW 3 min at now + 5 min, plan 9002 cpu_hog LOW 3 min at now + 15 min
--          tools/anom_obs_run.sh run anom_plan_test.sql <log> -- report <first_id> <last_id>
--              per test row: the plan status (mirrored), the run (INCIDENT_TRUTH), and every target interval from 30
--              minutes before the start to 5 minutes after the end with SESSIONS and LOGONS (per second as collected,
--              and per minute); then the means before / during (all intervals, and without the hand-over interval)
--          tools/anom_obs_run.sh run anom_plan_test.sql <log> -- clear <first_id> <last_id>
--              deletes the observer's test rows (9000-9099 only); the target's rows: tools/anom_plan_clear.sql as SHOP
--        Arguments are not secrets (the runner allows [A-Za-z0-9_.:=-] only); verify is off all the same.
--        v1.1: the report's per-minute columns are wide enough for a logon storm (they printed ### above 999.99).
--        v1.0: first version, 01-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 220 pagesize 0 trimout on
whenever sqlerror exit failure rollback

define a_mode = "&1"
define a_one = "&2"
define a_two = "&3"

declare
  c_mode   constant varchar2(20) := lower('&a_mode');
  c_iso    constant varchar2(30) := 'YYYY-MM-DD"T"HH24:MI:SS"Z"';
  l_one    number;
  l_two    number;
  l_now    date := cast(sys_extract_utc(systimestamp) as date);
  l_t1     date;
  l_t2     date;
  l_from   date;
  n        number;

  function clear_of_handover (p_start date, p_minutes number) return boolean is
    l_m number;
  begin
    -- no minute of [start - 1, end + 1] is HH:58, HH:59, HH:00 or HH:01
    for k in -1 .. p_minutes + 1 loop
      l_m := to_number(to_char(p_start + k / 1440, 'MI'));
      if l_m in (58, 59, 0, 1) then
        return false;
      end if;
    end loop;
    return true;
  end;

  procedure add_row (p_id number, p_from date, p_sc varchar2, p_in varchar2, p_start date, p_min number) is
  begin
    insert into INCIDENT_PLAN (plan_id, plan_from, day_no, seq_in_day, scenario, intensity, planned_start, minutes,
                               status, seed, created_ts, message)
    values (p_id, p_from, 0, p_id, p_sc, p_in, p_start, p_min, 'PLANNED', 0, sys_extract_utc(systimestamp),
            'phase 4b test row (anom_plan_test.sql)');
    dbms_output.put_line('PLAN '||p_id||' '||rpad(p_sc, 16)||' '||p_in||' '||p_min||' min at '||to_char(p_start, c_iso));
  end;

  procedure report (p_first number, p_last number) is
    l_s      date;
    l_e      date;
    l_role   varchar2(10);
    l_b_n    number;
    l_b_ses  number;
    l_b_lpm  number;
    l_b_ses2 number;
    l_b_lpm2 number;
    l_d_n    number;
    l_d_ses  number;
    l_d_lpm  number;
    l_d_lpm2 number;
  begin
    for p in (select i.plan_id, i.scenario, i.intensity, i.minutes, i.planned_start, i.status, i.run_id, i.message,
                     t.source, t.status run_status, coalesce(t.start_ts, t.requested_ts) s, t.end_ts e, t.refreshed_ts
                from INCIDENT_PLAN i left join INCIDENT_TRUTH t on t.run_id = i.run_id
               where i.plan_id between p_first and p_last order by i.plan_id) loop
      dbms_output.put_line('== plan '||p.plan_id||': '||p.scenario||' '||p.intensity||' '||p.minutes||' min, planned '
                           ||to_char(p.planned_start, c_iso)||'; plan status '||p.status||' ('||p.message||')');
      if p.run_id is null or p.s is null then
        dbms_output.put_line('   no run yet (INCIDENT_TRUTH copy refreshed every 5 minutes by the truth loop)');
        continue;
      end if;
      l_s := p.s;
      l_e := nvl(p.e, l_now);
      dbms_output.put_line('   run '||p.run_id||' source '||p.source||', '||p.run_status||', '||to_char(l_s, c_iso)||' - '
                           ||nvl(to_char(p.e, c_iso), 'still running')||' (start '
                           ||round((l_s - p.planned_start) * 86400)||' s after the planned minute; copy of '
                           ||to_char(p.refreshed_ts, c_iso)||')');
      dbms_output.put_line('   interval begin (UTC)  role      SESSIONS   LOGONS/s  logons/min');
      for m in (select begin_time, sessions, logons,
                       case when begin_time + 60 / 86400 <= l_s then 'before'
                            when begin_time <= l_s then 'start'
                            when begin_time + 60 / 86400 <= l_e then 'during'
                            when begin_time < l_e then 'end'
                            else 'after' end role,
                       case when to_char(begin_time + 60 / 86400, 'MI') in ('00', '01')
                              or to_char(begin_time, 'MI') = '00' then 'Y' else 'N' end hand_over
                  from METRIC_MINUTE
                 where begin_time >= l_s - 31 / 1440 and begin_time < l_e + 5 / 1440
                 order by begin_time) loop
        dbms_output.put_line('   '||to_char(m.begin_time, 'YYYY-MM-DD HH24:MI:SS')||'   '||rpad(m.role, 8)
                             ||lpad(to_char(m.sessions), 9)||lpad(to_char(round(m.logons, 4), 'fm99990.0000'), 11)
                             ||lpad(to_char(round(m.logons * 60, 2), 'fm9999990.00'), 12)
                             ||case when m.hand_over = 'Y' then '   hourly hand-over interval' end);
      end loop;
      -- before: the 30 intervals that end before the start; during: every interval that overlaps the run
      select count(*), avg(sessions), avg(logons * 60),
             avg(case when not (to_char(begin_time + 60 / 86400, 'MI') in ('00', '01') or to_char(begin_time, 'MI') = '00')
                      then sessions end),
             avg(case when not (to_char(begin_time + 60 / 86400, 'MI') in ('00', '01') or to_char(begin_time, 'MI') = '00')
                      then logons * 60 end)
        into l_b_n, l_b_ses, l_b_lpm, l_b_ses2, l_b_lpm2
        from (select begin_time, sessions, logons from METRIC_MINUTE
               where begin_time + 60 / 86400 <= l_s order by begin_time desc fetch first 30 rows only);
      select count(*), avg(sessions), avg(logons * 60), max(logons * 60)
        into l_d_n, l_d_ses, l_d_lpm, l_d_lpm2
        from METRIC_MINUTE where begin_time + 60 / 86400 > l_s and begin_time < l_e;
      dbms_output.put_line('   SUMMARY plan '||p.plan_id||' '||p.scenario||': before ('||l_b_n||' intervals) SESSIONS mean '
                           ||to_char(round(l_b_ses, 3), 'fm990.000')||', logons/min mean '
                           ||to_char(round(l_b_lpm, 3), 'fm990.000')||' (without the hand-over interval: SESSIONS '
                           ||to_char(round(l_b_ses2, 3), 'fm990.000')||', logons/min '||to_char(round(l_b_lpm2, 3), 'fm990.000')
                           ||'); during ('||l_d_n||' intervals) SESSIONS mean '||to_char(round(l_d_ses, 3), 'fm990.000')
                           ||', logons/min mean '||to_char(round(l_d_lpm, 3), 'fm990.000')||', max '
                           ||to_char(round(l_d_lpm2, 2), 'fm990.00'));
    end loop;
  end;
begin
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, 'anom_plan_test: run as ANOMOPS');
  end if;
  if not regexp_like('&a_one', '^[0-9]{1,4}$') or not regexp_like('&a_two', '^[0-9]{1,4}$') then
    raise_application_error(-20900, 'anom_plan_test: arguments 2 and 3 are whole numbers');
  end if;
  l_one := to_number('&a_one');
  l_two := to_number('&a_two');
  dbms_output.put_line('anom_plan_test v1.1 '||c_mode||' at '||to_char(sys_extract_utc(systimestamp), c_iso));
  if c_mode = 'push_artefact' then
    select count(*) into n from INCIDENT_PLAN where plan_id between 9011 and 9012;
    if n > 0 then
      raise_application_error(-20900, 'anom_plan_test: plans 9011-9012 exist; clear them first');
    end if;
    l_t1 := trunc(l_now, 'MI') + greatest(nvl(nullif(l_one, 0), 35), 31) / 1440;
    loop
      l_t2 := l_t1 + 25 / 1440;                       -- 5-minute window, then 20 minutes apart
      exit when clear_of_handover(l_t1, 5) and clear_of_handover(l_t2, 5);
      l_t1 := l_t1 + 1 / 1440;
    end loop;
    l_from := to_date('2000-01-01 00:11', 'YYYY-MM-DD HH24:MI');
    add_row(9011, l_from, 'app_error_burst', 'LOW', l_t1, 5);
    add_row(9012, l_from, 'cpu_hog', 'LOW', l_t2, 5);
    commit;
    PKG_ANOM_GRADE.push_plan(l_from);
    dbms_output.put_line('PUSHED at '||to_char(sys_extract_utc(systimestamp), c_iso)
                         ||'; the target starts them; stay off the target until '||to_char(l_t2 + 6 / 1440, c_iso));
  elsif c_mode = 'push_test' then
    select count(*) into n from INCIDENT_PLAN where plan_id between 9001 and 9002;
    if n > 0 then
      raise_application_error(-20900, 'anom_plan_test: plans 9001-9002 exist; clear them first');
    end if;
    l_t1 := trunc(l_now, 'MI') + 5 / 1440;
    loop
      l_t2 := l_t1 + 10 / 1440;
      exit when clear_of_handover(l_t1, 3) and clear_of_handover(l_t2, 3);
      l_t1 := l_t1 + 1 / 1440;
    end loop;
    l_from := to_date('2000-01-01 00:01', 'YYYY-MM-DD HH24:MI');
    add_row(9001, l_from, 'app_error_burst', 'LOW', l_t1, 3);
    add_row(9002, l_from, 'cpu_hog', 'LOW', l_t2, 3);
    commit;
    PKG_ANOM_GRADE.push_plan(l_from);
    dbms_output.put_line('PUSHED at '||to_char(sys_extract_utc(systimestamp), c_iso)
                         ||'; the target starts them; stay off the target until '||to_char(l_t2 + 4 / 1440, c_iso));
  elsif c_mode = 'report' then
    report(l_one, l_two);
  elsif c_mode = 'clear' then
    if l_one < 9000 or l_two > 9099 or l_one > l_two then
      raise_application_error(-20900, 'anom_plan_test: clear takes test plan ids 9000-9099 only');
    end if;
    delete from INCIDENT_PLAN where plan_id between l_one and l_two;
    dbms_output.put_line('CLEARED '||sql%rowcount||' observer test plan row(s) '||l_one||'-'||l_two);
    commit;
  else
    raise_application_error(-20900, 'anom_plan_test: mode is push_artefact, push_test, report or clear');
  end if;
end;
/
undefine a_mode
undefine a_one
undefine a_two
