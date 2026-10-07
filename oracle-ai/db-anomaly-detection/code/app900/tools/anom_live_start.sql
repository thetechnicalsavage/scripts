-- v1.1 - app 900 (phase 4 integrator): starts one chaos scenario on the target the way the Control Center will, in
--        ora26ai / ORCLPDB1 as ANOMOPS: SHOP.PKG_CHAOS.start_scenario through ANOM_CHAOS_LINK with source 'UI'.
--          tools/anom_obs_run.sh run anom_live_start.sql <log> -- <scenario> <minutes> <LOW|HIGH>
--        The scenario name is checked against the fixed list here (PLAN.md section 4, rule 5) and again by PKG_CHAOS
--        (-20101..-20105, e.g. -20104 while another run is RUNNING). The new run is copied into INCIDENT_TRUTH over
--        the same link session (one link logon in all), then the link is closed. Prints RUN_ID=<n>.
--        Arguments are not secrets (the runner allows [A-Za-z0-9_.:=-] only); verify is off all the same.
--        v1.1: (phase 4b, 93 v1.2) the run is QUEUED until a target dispatcher claims it (about 2 s), so RUN_START is
--              the claim time when the copy already has it, else the request time (marked "requested"). 01-Oct-2026.
--        v1.0: first version, 01-Oct-2026.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set linesize 200
whenever sqlerror exit failure rollback

define a_scenario = "&1"
define a_minutes = "&2"
define a_intensity = "&3"

declare
  l_sc    varchar2(40) := lower('&a_scenario');
  l_min   number;
  l_in    varchar2(10) := upper('&a_intensity');
  l_id    number;
  l_start date;
  l_end   date;
  l_note  varchar2(40);
begin
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, 'anom_live_start: run as ANOMOPS');
  end if;
  if l_sc not in ('blocking_chain', 'plan_regression', 'slow_drift', 'batch_wrong_time', 'hard_parse_storm',
                  'commit_storm', 'io_storm', 'cpu_hog', 'temp_spill', 'conn_leak', 'logon_storm', 'app_error_burst') then
    raise_application_error(-20900, 'anom_live_start: unknown scenario '||substr(l_sc, 1, 30));
  end if;
  if l_in not in ('LOW', 'HIGH') then
    raise_application_error(-20900, 'anom_live_start: intensity must be LOW or HIGH');
  end if;
  if not regexp_like('&a_minutes', '^[0-9]{1,3}$') then
    raise_application_error(-20900, 'anom_live_start: minutes must be a whole number');
  end if;
  l_min := to_number('&a_minutes');
  commit;   -- nothing local is pending when the target is called (a remote function that commits)
  execute immediate 'begin :id := SHOP.PKG_CHAOS.start_scenario@ANOM_CHAOS_LINK(:n, :m, :i, ''UI''); end;'
    using out l_id, in l_sc, in l_min, in l_in;
  commit;
  dbms_output.put_line('RUN_ID='||l_id||' scenario='||l_sc||' intensity='||l_in||' minutes='||l_min);
  -- the copy, over the link session start_scenario opened (refresh_truth commits and closes the link; it logs and
  -- re-raises a failure, and the run goes on regardless: the truth job copies it within 5 minutes)
  PKG_ANOM_TRAIN.refresh_truth;
  select coalesce(start_ts, requested_ts), planned_end_ts, case when start_ts is null then ' (requested; QUEUED)' end
    into l_start, l_end, l_note from INCIDENT_TRUTH where run_id = l_id;
  dbms_output.put_line('RUN_START='||to_char(l_start, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')||l_note
                       ||' PLANNED_END='||to_char(l_end, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
/
undefine a_scenario
undefine a_minutes
undefine a_intensity
