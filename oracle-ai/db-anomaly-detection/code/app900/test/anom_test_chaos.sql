-- v1.5 - SHOP in oradb1 / FREEPDB1: app 900 chaos tests (docs/contract.md sections 5 and 12), after 93. One part per
--        call:
--          tools/anom_chaos_run.sh run anom_test_chaos.sql <log> -- <part>
--        (tools/anom_chaos_test.sh runs every part in order, with a 3-minute gap after each part that injects).
--        v1.5: (phase 4b, 93 v1.2) scenarios run in the two permanent dispatcher sessions: a start is QUEUED and
--              claimed by CHAOS_DISPATCH_1 in its existing session within seconds, the dispatchers' sessions do not
--              change during or after a run (no hand-over at an incident), stop_all ends a run through its STOP flag,
--              a failure is recorded on the run while the dispatcher goes on, the reaper and a restarted dispatcher
--              end an orphaned run. New parts: dispatch (the dispatcher and the STOP flag), plan (CHAOS_PLAN: the
--              maximum-minutes check, a due row STARTED with source SCHEDULE, a row SKIPPED while another run is
--              active, a late row SKIPPED as missed), and high_<scenario> for the 11 HIGH smoke tests (contract
--              section 10; cpu_hog HIGH is cpu_hog_high). Test plan rows use plan ids 9100-9199 and are deleted.
--        v1.4: a check's detail is printed on one line (an error stack's second line could start with ORA-).
--        v1.3: end_checks waits up to 30 s for restored = Y: the reaper (like stop_all) marks the run first and
--              restores after it, so a run can show STOPPED a moment before restored = Y (reaper_overstay saw that);
--              the reaper's log check no longer claims it logs failed runs only (the default job class logs all).
--        v1.2: blocking_chain_error accepts ORA-00054 as well as ORA-30006: 26ai reports a FOR UPDATE WAIT timeout
--              as ORA-00054 ("Failed to acquire a lock ... TX").
--        v1.1: plan_regression's "browse uses PROD_CAT_IX before" is checked before the start (it raced the job).
--        v1.0: first version, 01-Oct-2026.
--
--        Parts
--          basic                 validation (-20101 .. -20105, -20107, -20108), run() and dispatch() outside a
--                                dispatcher job (-20106), objects, grants, the jobs, CHAOS_PLAN's maximum-minutes
--                                check (rolled back), set_load without a change. Injects nothing.
--          dispatch              app_error_burst LOW 3 min: QUEUED, claimed by dispatcher 1 in its own session, the
--                                dispatcher sessions unchanged, stopped by stop_all through the STOP flag after 20 s.
--          plan                  CHAOS_PLAN rows due now: a late one SKIPPED (missed), one STARTED (source SCHEDULE),
--                                one SKIPPED while that run is active; then stop_all and the test rows deleted.
--          <scenario>            one of the 12 at LOW for 2 minutes, source TEST: it starts, does its thing (a
--                                check per scenario while it runs), ends by itself at its planned end, and the
--                                target is fully restored. app_error_burst also checks one-active-run-at-a-time.
--          high_<scenario>       the same at HIGH for 2 minutes (the 11 other than cpu_hog), HIGH checks.
--          cpu_hog_high          cpu_hog HIGH: both dispatchers run it; after a minute stop_all stops both halves.
--          stop_all              plan_regression LOW, stopped by stop_all: the index is VISIBLE again.
--          blocking_chain_error  blocking_chain LOW made to fail (this session holds hot product 5, the job's
--                                second cycle times out: ORA-30006, which 26ai reports as ORA-00054): FAILED,
--                                restored, no lock left.
--          reaper_orphan         plan_regression LOW whose dispatcher job is stopped outside PKG_CHAOS: the run
--                                stays RUNNING with the index invisible until the restarted dispatcher (or
--                                CHAOS_REAPER) marks it FAILED and restores.
--          reaper_overstay       a RUNNING row 2.5 minutes past its planned end (no job) with leftovers: the
--                                reaper stops and restores it.
--          reaper_repair         nothing RUNNING, the state broken by hand (index invisible, a drift rule, a
--                                CHAOS_SCRATCH row, conn_leak_target 10): the reaper repairs it within 90 s.
--          final                 nothing active, clean, both dispatchers running, no v1.1 scenario job, no test plan
--                                row left, the reaper never failed; the TEST runs.
--        Every part that injects is a real incident on the soak and is recorded in CHAOS_RUN (source TEST); the
--        two hand-made rows are recorded too. Training excludes every CHAOS_RUN window (contract section 6).
--        Every check prints PASS or FAIL; the exit code is the number of failures. On an unexpected error the
--        part rolls back, calls stop_all for a run it started, and fails.
set serveroutput on size unlimited format wrapped
set verify off
set feedback off
set sqlblanklines on
set linesize 220 tab off trimout on
whenever sqlerror exit failure rollback
whenever oserror exit failure

define part = "&1"

variable n_pass number
variable n_fail number

declare
  c_part   constant varchar2(40) := lower(trim(substr('&part.', 1, 40)));
  c_disp1  constant varchar2(30) := 'CHAOS_DISPATCH_1';
  c_disp2  constant varchar2(30) := 'CHAOS_DISPATCH_2';
  g_pass   pls_integer := 0;
  g_fail   pls_integer := 0;
  g_run    number;                 -- a run this part started (stopped on an unexpected error)
  e_busy   exception;
  pragma exception_init(e_busy, -54);
  e_check  exception;
  pragma exception_init(e_check, -2290);

  type t_names is table of varchar2(30);
  c_scenarios constant t_names := t_names('blocking_chain', 'plan_regression', 'slow_drift', 'batch_wrong_time',
    'hard_parse_storm', 'commit_storm', 'io_storm', 'cpu_hog', 'temp_spill', 'conn_leak', 'logon_storm',
    'app_error_burst');
  type t_max is table of number index by varchar2(30);
  g_max t_max;

  -- before-snapshot for batch_wrong_time (LOW: the latest settled day; HIGH: the latest 7)
  g_day     date;
  g_last    date;
  g_days    number;
  g_day_n   number;
  g_day_amt number;
  g_day_y   number;

  -- ---------------------------------------------------------------- helpers
  procedure say (p varchar2) is
  begin
    dbms_output.put_line(to_char(sys_extract_utc(systimestamp), 'HH24:MI:SS')||'  '||p);
  end say;

  function one_line (p varchar2) return varchar2 is
  begin
    return translate(p, chr(10)||chr(13), '  ');
  end one_line;

  procedure ok (p_what varchar2, p_cond boolean, p_detail varchar2 default null) is
  begin
    if p_cond then
      g_pass := g_pass + 1;
      dbms_output.put_line('PASS  '||p_what||case when p_detail is not null then '  ['||substr(one_line(p_detail), 1, 300)||']' end);
    else
      g_fail := g_fail + 1;
      dbms_output.put_line('FAIL  '||p_what||case when p_detail is not null then '  ['||substr(one_line(p_detail), 1, 300)||']' end);
    end if;
  end ok;

  function utc_now return timestamp is
  begin
    return sys_extract_utc(systimestamp);
  end utc_now;

  function secs (p_from timestamp, p_to timestamp) return number is
    d interval day(9) to second(6) := p_to - p_from;
  begin
    return extract(day from d) * 86400 + extract(hour from d) * 3600 + extract(minute from d) * 60
           + extract(second from d);
  end secs;

  function since (p timestamp) return number is
  begin
    return secs(p, utc_now);
  end since;

  function ts (p timestamp) return varchar2 is
  begin
    return to_char(p, 'HH24:MI:SS');
  end ts;

  procedure sleep_until (p_t timestamp) is
  begin
    while utc_now < p_t loop
      dbms_session.sleep(least(greatest(secs(utc_now, p_t), 0.1), 5));
    end loop;
  end sleep_until;

  function run_rec (p_id number) return CHAOS_RUN%rowtype is
    r CHAOS_RUN%rowtype;
  begin
    select * into r from CHAOS_RUN where run_id = p_id;
    return r;
  end run_rec;

  function n_active return number is
    n number;
  begin
    select count(*) into n from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
    return n;
  end n_active;

  function n_runs return number is
    n number;
  begin
    select count(*) into n from CHAOS_RUN;
    return n;
  end n_runs;

  function state return varchar2 is
  begin
    return SHOP.PKG_CHAOS.state_problems;
  end state;

  function running_jobs (p_pattern varchar2) return number is
    n number;
  begin
    select count(*) into n from user_scheduler_running_jobs where regexp_like(job_name, p_pattern);
    return n;
  end running_jobs;

  -- the session a dispatcher job runs in now (null while it is not running)
  function disp_sid (p_job varchar2) return number is
    l number;
  begin
    select max(session_id) into l from user_scheduler_running_jobs where job_name = p_job;
    return l;
  end disp_sid;

  -- has an hourly dispatcher hand-over (HH:59:50, the next run at once) happened since p_from? A dispatcher's session
  -- changes then by design, so a "same session" check that spans one is not a finding.
  function handover_since (p_from timestamp) return boolean is
  begin
    return trunc(cast(utc_now + interval '15' second as date), 'HH') > trunc(cast(p_from + interval '15' second as date), 'HH');
  end handover_since;

  -- the dispatcher still runs in session p_sid (or an hourly hand-over since p_from explains a new one)
  function same_session (p_job varchar2, p_sid number, p_from timestamp) return boolean is
  begin
    return disp_sid(p_job) = p_sid or handover_since(p_from);
  end same_session;

  -- "<dispatcher 1 session>/<dispatcher 2 session>", the sessions that are always there
  function disp_sessions return varchar2 is
  begin
    return nvl(to_char(disp_sid(c_disp1)), '-')||'/'||nvl(to_char(disp_sid(c_disp2)), '-');
  end disp_sessions;

  -- both dispatchers running (waits up to p_max seconds: one may be in its hourly hand-over or restarting)
  function dispatchers_up (p_max number default 70) return boolean is
    l_t0 timestamp := utc_now;
  begin
    loop
      if running_jobs('^CHAOS_DISPATCH_[12]$') = 2 then
        return true;
      end if;
      exit when since(l_t0) > p_max;
      dbms_session.sleep(1);
    end loop;
    return false;
  end dispatchers_up;

  function legacy_jobs return number is
    n number;
  begin
    select count(*) into n from user_scheduler_jobs where regexp_like(job_name, '^CHAOS_[0-9]+(_[AB])?$');
    return n;
  end legacy_jobs;

  function exec_of (p_id number) return varchar2 is
    r CHAOS_RUN%rowtype := run_rec(p_id);
  begin
    return nvl(r.exec_a, '-')||'@'||nvl(to_char(r.sid_a), '-')||','||nvl(r.exec_b, '-')||'@'||nvl(to_char(r.sid_b), '-');
  end exec_of;

  -- waits until dispatcher 1 has claimed the run (RUNNING, start stamped, exec_a set)
  function wait_started (p_id number, p_max number default 30) return boolean is
    l_t0 timestamp := utc_now;
    r    CHAOS_RUN%rowtype;
  begin
    loop
      r := run_rec(p_id);
      if r.status = 'RUNNING' and r.start_ts is not null and r.exec_a = c_disp1 then
        return true;
      end if;
      exit when since(l_t0) > p_max or r.status not in ('QUEUED', 'RUNNING');
      dbms_session.sleep(0.5);
    end loop;
    return false;
  end wait_started;

  -- waits until the run is no longer active (at most p_max seconds); returns its status
  function wait_ended (p_id number, p_max number) return varchar2 is
    l_t0 timestamp := utc_now;
    r    CHAOS_RUN%rowtype;
  begin
    loop
      r := run_rec(p_id);
      exit when r.status not in ('QUEUED', 'RUNNING') or since(l_t0) > p_max;
      dbms_session.sleep(1);
    end loop;
    return r.status;
  end wait_ended;

  -- true when another session holds the row lock of the product: probed in an autonomous transaction with
  -- NOWAIT and rolled back at once, so the probe itself holds a free row for well under a millisecond
  function row_locked (p_pid number) return boolean is
    pragma autonomous_transaction;
    l number;
  begin
    select product_id into l from INVENTORY where product_id = p_pid for update nowait;
    rollback;
    return false;
  exception
    when e_busy then
      rollback;
      return true;
  end row_locked;

  function locked_count (p_from number, p_to number) return number is
    n number := 0;
  begin
    for p in p_from .. p_to loop
      if row_locked(p) then
        n := n + 1;
      end if;
    end loop;
    return n;
  end locked_count;

  -- polls up to p_tries seconds for the rows to be free (a driver order can hold one for a few milliseconds)
  function rows_free (p_from number, p_to number, p_tries pls_integer default 5) return number is
    n number;
  begin
    for i in 1 .. p_tries loop
      n := locked_count(p_from, p_to);
      exit when n = 0;
      dbms_session.sleep(1);
    end loop;
    return n;
  end rows_free;

  function num_in (p_text varchar2, p_pattern varchar2) return number is
  begin
    return to_number(regexp_substr(p_text, p_pattern, 1, 1, null, 1));
  end num_in;

  function visibility (p_index varchar2) return varchar2 is
    l varchar2(10);
  begin
    select visibility into l from user_indexes where index_name = p_index;
    return l;
  end visibility;

  function wait_visibility (p_index varchar2, p_want varchar2, p_max number) return varchar2 is
    l    varchar2(10);
    l_t0 timestamp := utc_now;
  begin
    loop
      l := visibility(p_index);
      exit when l = p_want or since(l_t0) > p_max;
      dbms_session.sleep(1);
    end loop;
    return l;
  end wait_visibility;

  -- does the optimizer's plan for the driver's browse statement use PROD_CAT_IX?
  function browse_uses_index return boolean is
    n number;
  begin
    delete from plan_table where statement_id = 'ANOM_TEST_CHAOS';
    execute immediate q'[explain plan set statement_id = 'ANOM_TEST_CHAOS' for
      select p.product_id, p.name, p.price, i.qty_on_hand from SHOP.PRODUCTS p join SHOP.INVENTORY i
          on i.product_id = p.product_id where p.category_id = 7 and p.active = 'Y'
       order by p.popularity desc fetch first 20 rows only]';
    select count(*) into n from plan_table where statement_id = 'ANOM_TEST_CHAOS' and object_name = 'PROD_CAT_IX';
    delete from plan_table where statement_id = 'ANOM_TEST_CHAOS';
    commit;
    return n > 0;
  end browse_uses_index;

  procedure show_run (p_id number) is
    r CHAOS_RUN%rowtype := run_rec(p_id);
  begin
    say('run '||r.run_id||' '||r.scenario||' '||r.intensity||' '||r.source||': requested '||ts(r.requested_ts)
        ||', start '||nvl(ts(r.start_ts), '-')||', planned end '||ts(r.planned_end_ts)||', end '||nvl(ts(r.end_ts), '-')
        ||', '||r.status||', restored '||r.restored||', stop '||r.stop_req||', executed by '||exec_of(p_id));
    say('run '||r.run_id||' note: '||r.note);
  end show_run;

  -- the end of a run: its status, restored flag, the clean target, both dispatchers still there (the session that
  -- ran it carries on, unless p_same_sid is null), no v1.1 per-run job
  procedure end_checks (p_id number, p_label varchar2, p_status varchar2, p_same_sid boolean default true) is
    r      CHAOS_RUN%rowtype := run_rec(p_id);
    l_det  varchar2(4000);
    l_sid  number;
  begin
    -- whoever ends a run marks it and restores (the executor before marking; stop_all and the reaper after): wait
    for i in 1 .. 30 loop
      exit when r.restored = 'Y';
      dbms_session.sleep(1);
      r := run_rec(p_id);
    end loop;
    ok(p_label||': ends '||p_status, r.status = p_status, r.status||' - '||substr(r.note, 1, 200));
    ok(p_label||': end_ts recorded', r.end_ts is not null, ts(r.end_ts));
    ok(p_label||': restored = Y', r.restored = 'Y', r.restored);
    l_det := state;
    ok(p_label||': target fully restored (indexes visible, no drift rules, PROMO_RULES compact, CHAOS_SCRATCH '
       ||'empty, DRIVER_CONTROL chaos columns at defaults)', l_det is null, l_det);
    ok(p_label||': both dispatchers are running', dispatchers_up, disp_sessions);
    if p_same_sid and r.sid_a is not null then
      l_sid := disp_sid(c_disp1);
      ok(p_label||': dispatcher 1 is still the session that ran it (no session change at the incident)',
         same_session(c_disp1, r.sid_a, r.start_ts), 'ran in '||r.sid_a||', now '||nvl(to_char(l_sid), '-')
         ||case when handover_since(r.start_ts) then ' (an hourly hand-over came in between)' end);
    end if;
    ok(p_label||': no per-run scheduler job (v1.1 style) exists', legacy_jobs = 0, legacy_jobs);
    show_run(p_id);
  end end_checks;

  -- ---------------------------------------------------------------- the per-scenario checks while it runs (LOW)
  procedure during (p_name varchar2, p_id number, p_t0 timestamp) is
    r       CHAOS_RUN%rowtype;
    n       number;
    m       number;
    l_a     timestamp;
    l_b     timestamp;
    l_min   number;
    l_valid date;
    l_txt   varchar2(4000);
    l_id2   number;
    l_c0    number;
  begin
    case p_name
    when 'blocking_chain' then
      sleep_until(p_t0 + interval '4' second);
      n := locked_count(1, 5);
      ok('blocking_chain: the run holds the row locks of hot products 1-5 (each FOR UPDATE NOWAIT gets ORA-00054)',
         n = 5, n||' of 5 locked at t0+'||round(since(p_t0))||' s');
      sleep_until(p_t0 + interval '25' second);
      n := rows_free(1, 5);
      ok('blocking_chain: LOW lets them go after 15 s (free at t0+25 s)', n = 0, n||' still locked');
      sleep_until(p_t0 + interval '64' second);
      n := locked_count(1, 5);
      ok('blocking_chain: and takes them again for the next cycle at t0+60 s', n = 5, n||' of 5 locked');
      r := run_rec(p_id);
      ok('blocking_chain: the progress note names cycle 2', r.note like '%cycle 2 holds the row locks of hot products 1-5%',
         r.note);

    when 'plan_regression' then
      l_txt := wait_visibility('PROD_CAT_IX', 'INVISIBLE', 30);
      ok('plan_regression: LOW makes PROD_CAT_IX invisible', l_txt = 'INVISIBLE', l_txt);
      ok('plan_regression: LOW leaves ORD_CUST_IX visible', visibility('ORD_CUST_IX') = 'VISIBLE');
      ok('plan_regression: browse no longer uses PROD_CAT_IX (explain plan)', not browse_uses_index);

    when 'slow_drift' then
      sleep_until(p_t0 + interval '10' second);
      select count(*), min(rule_id), max(valid_to) into n, l_min, l_valid from PROMO_RULES where rule_id >= 9000000;
      ok('slow_drift: minute 1 adds 20,000 rules (40,000 over the 2 minutes, linearly)', n = 20000, n);
      ok('slow_drift: every added rule has rule_id >= 9,000,000', l_min >= 9000000, l_min);
      ok('slow_drift: the added rules are expired: scanned by every place_order, never matched',
         l_valid < date '2001-01-01', to_char(l_valid, 'YYYY-MM-DD'));
      select count(*) into n from PROMO_RULES where rule_id < 9000000;
      ok('slow_drift: the 300 seed rules are untouched', n = 300, n);
      sleep_until(p_t0 + interval '70' second);
      select count(*) into n from PROMO_RULES where rule_id >= 9000000;
      ok('slow_drift: minute 2 brings it to 40,000', n = 40000, n);
      select nvl(sum(blocks), 0) into n from user_segments where segment_name = 'PROMO_RULES';
      ok('slow_drift: the PROMO_RULES segment grew past 8 blocks', n > 16, n||' blocks');

    when 'batch_wrong_time' then
      for i in 1 .. 90 loop
        r := run_rec(p_id);
        exit when r.note like '%pass 1 over 1 settled day(s)%';
        dbms_session.sleep(1);
      end loop;
      ok('batch_wrong_time: pass 1 re-ran the settlement of the latest settled day ('||to_char(g_day, 'YYYY-MM-DD')||')',
         r.note like '%pass 1 over 1 settled day(s) '||to_char(g_day, 'YYYY-MM-DD')||' to '||to_char(g_day, 'YYYY-MM-DD')||'%',
         r.note);
      n := num_in(r.note, '([0-9]+) payment rows re-updated');
      ok('batch_wrong_time: the no-op update covered the day''s settled payments', mod(n, g_day_y) = 0 and n > 0,
         n||' rows so far, '||g_day_y||' settled payments that day');

    when 'hard_parse_storm' then
      sleep_until(p_t0 + interval '65' second);
      r := run_rec(p_id);
      n := num_in(r.note, '([0-9]+) literal statements');
      ok('hard_parse_storm: about 20 literal statements a second (800-1,500 after 60-65 s)', n between 800 and 1500,
         n||' statements: '||r.note);

    when 'commit_storm' then
      sleep_until(p_t0 + interval '30' second);
      select count(*), min(created_ts), max(created_ts) into n, l_a, l_b from CHAOS_SCRATCH where run_id = p_id;
      m := round(n / greatest(secs(l_a, l_b), 1), 1);
      ok('commit_storm: rows committed one by one, about 50 a second (another session sees them)', m between 35 and 60,
         n||' rows, '||m||' a second');

    when 'io_storm' then
      sleep_until(p_t0 + interval '70' second);
      r := run_rec(p_id);
      n := num_in(r.note, '([0-9]+) full scans');
      ok('io_storm: full scans of CLICK_ARCHIVE run, with pauses (LOW)', n >= 1, r.note);

    when 'cpu_hog' then
      sleep_until(p_t0 + interval '65' second);
      r := run_rec(p_id);
      n := num_in(r.note, '([0-9]+) s busy');
      m := num_in(r.note, '([0-9]+) s idle');
      ok('cpu_hog: LOW is busy half the time (busy and idle seconds equal, >= 20 each)',
         n >= 20 and abs(n - m) <= 1, r.note);
      ok('cpu_hog: LOW runs in one session (dispatcher 1 only)', r.exec_a = c_disp1 and r.exec_b is null, exec_of(p_id));

    when 'temp_spill' then
      sleep_until(p_t0 + interval '65' second);
      r := run_rec(p_id);
      n := num_in(r.note, '([0-9]+) spilling queries');
      ok('temp_spill: the spilling queries run, one at a time with pauses (LOW)', n >= 1, r.note);

    when 'conn_leak' then
      sleep_until(p_t0 + interval '5' second);
      select conn_leak_target, error_pct into n, m from DRIVER_CONTROL where id = 1;
      ok('conn_leak: DRIVER_CONTROL.conn_leak_target = 10 (and nothing else changed)', n = 10 and m = 0, n||'/'||m);

    when 'logon_storm' then
      sleep_until(p_t0 + interval '10' second);
      select logon_storm into l_txt from DRIVER_CONTROL where id = 1;
      ok('logon_storm: DRIVER_CONTROL.logon_storm = Y for the first 30 s', l_txt = 'Y', l_txt);
      sleep_until(p_t0 + interval '45' second);
      select logon_storm into l_txt from DRIVER_CONTROL where id = 1;
      ok('logon_storm: N for the next 30 s', l_txt = 'N', l_txt);
      sleep_until(p_t0 + interval '70' second);
      select logon_storm into l_txt from DRIVER_CONTROL where id = 1;
      ok('logon_storm: Y again in the second cycle', l_txt = 'Y', l_txt);

    when 'app_error_burst' then
      sleep_until(p_t0 + interval '5' second);
      select error_pct into n from DRIVER_CONTROL where id = 1;
      ok('app_error_burst: DRIVER_CONTROL.error_pct = 3', n = 3, n);
      -- one active run at a time
      l_c0 := n_runs;
      begin
        l_id2 := SHOP.PKG_CHAOS.start_scenario('cpu_hog', 2, 'LOW', 'TEST');
        ok('one run at a time: a second start is refused with -20104', false, 'started run '||l_id2);
        SHOP.PKG_CHAOS.stop_all('anom_test_chaos: a second run should have been refused');
      exception
        when others then
          ok('one run at a time: a second start is refused with -20104', sqlcode = -20104, sqlerrm);
      end;
      ok('one run at a time: the refused start added no row, one run is active', n_runs = l_c0 and n_active = 1,
         n_runs||' rows, '||n_active||' active');
    end case;
  end during;

  -- ---------------------------------------------------------------- the HIGH checks while it runs (smoke tests)
  procedure during_high (p_name varchar2, p_id number, p_t0 timestamp) is
    r       CHAOS_RUN%rowtype;
    n       number;
    m       number;
    l_a     timestamp;
    l_b     timestamp;
    l_txt   varchar2(4000);
  begin
    case p_name
    when 'blocking_chain' then
      sleep_until(p_t0 + interval '5' second);
      n := locked_count(1, 25);
      ok('blocking_chain HIGH: the run holds the row locks of hot products 1-25', n = 25, n||' of 25 at t0+5 s');
      sleep_until(p_t0 + interval '40' second);
      n := locked_count(1, 25);
      ok('blocking_chain HIGH: still all 25 at t0+40 s (continuously, no 15-of-60 cycle)', n = 25, n||' of 25');
      ok('blocking_chain HIGH: products 26-30 stay free', rows_free(26, 30) = 0);

    when 'plan_regression' then
      l_txt := wait_visibility('ORD_CUST_IX', 'INVISIBLE', 30);
      ok('plan_regression HIGH: ORD_CUST_IX (order_status) is invisible', l_txt = 'INVISIBLE', l_txt);
      ok('plan_regression HIGH: PROD_CAT_IX stays visible', visibility('PROD_CAT_IX') = 'VISIBLE');

    when 'slow_drift' then
      sleep_until(p_t0 + interval '15' second);
      select count(*) into n from PROMO_RULES where rule_id >= 9000000;
      ok('slow_drift HIGH: minute 1 adds 100,000 rules (200,000 over the 2 minutes)', n = 100000, n);
      sleep_until(p_t0 + interval '75' second);
      select count(*) into n from PROMO_RULES where rule_id >= 9000000;
      ok('slow_drift HIGH: minute 2 brings it to 200,000', n = 200000, n);

    when 'batch_wrong_time' then
      for i in 1 .. 110 loop
        r := run_rec(p_id);
        exit when r.note like '%pass 1 over '||g_days||' settled day(s)%';
        dbms_session.sleep(1);
      end loop;
      ok('batch_wrong_time HIGH: pass 1 re-ran the settlement of the latest '||g_days||' settled days',
         g_days = 7 and r.note like '%pass 1 over 7 settled day(s)%', r.note);

    when 'hard_parse_storm' then
      sleep_until(p_t0 + interval '65' second);
      r := run_rec(p_id);
      n := num_in(r.note, '([0-9]+) literal statements');
      ok('hard_parse_storm HIGH: unthrottled (more than LOW''s 1,500 after 60-65 s)', n > 1500, n||' statements: '||r.note);

    when 'commit_storm' then
      sleep_until(p_t0 + interval '30' second);
      select count(*), min(created_ts), max(created_ts) into n, l_a, l_b from CHAOS_SCRATCH where run_id = p_id;
      m := round(n / greatest(secs(l_a, l_b), 1), 1);
      ok('commit_storm HIGH: unthrottled (more than LOW''s ~50 commits a second)', m > 60, n||' rows, '||m||' a second');

    when 'io_storm' then
      sleep_until(p_t0 + interval '75' second);
      r := run_rec(p_id);
      n := num_in(r.note, '([0-9]+) full scans');
      ok('io_storm HIGH: full scans of CLICK_ARCHIVE back to back', n >= 1, r.note);

    when 'temp_spill' then
      sleep_until(p_t0 + interval '65' second);
      r := run_rec(p_id);
      n := num_in(r.note, '([0-9]+) spilling queries');
      ok('temp_spill HIGH: the spilling queries run back to back', n >= 1, r.note);

    when 'conn_leak' then
      sleep_until(p_t0 + interval '5' second);
      select conn_leak_target, error_pct into n, m from DRIVER_CONTROL where id = 1;
      ok('conn_leak HIGH: DRIVER_CONTROL.conn_leak_target = 40 (and nothing else changed)', n = 40 and m = 0, n||'/'||m);

    when 'logon_storm' then
      for t in 1 .. 3 loop
        sleep_until(p_t0 + numtodsinterval(case t when 1 then 10 when 2 then 45 else 70 end, 'SECOND'));
        select logon_storm into l_txt from DRIVER_CONTROL where id = 1;
        ok('logon_storm HIGH: DRIVER_CONTROL.logon_storm = Y at t0+'||case t when 1 then 10 when 2 then 45 else 70 end
           ||' s (throughout)', l_txt = 'Y', l_txt);
      end loop;

    when 'app_error_burst' then
      sleep_until(p_t0 + interval '5' second);
      select error_pct into n from DRIVER_CONTROL where id = 1;
      ok('app_error_burst HIGH: DRIVER_CONTROL.error_pct = 15', n = 15, n);
    end case;
  end during_high;

  -- ---------------------------------------------------------------- a scenario for 2 minutes (LOW, or HIGH smoke)
  procedure scenario_test (p_name varchar2, p_int varchar2 default 'LOW') is
    l_id    number;
    r       CHAOS_RUN%rowtype;
    l_en0   varchar2(1);
    l_load0 number;
    l_en1   varchar2(1);
    l_load1 number;
    l_det   varchar2(4000);
    l_st    varchar2(10);
    l_lbl   varchar2(60) := p_name||case when p_int = 'HIGH' then ' HIGH' end;
    l_s1    number;
    l_s2    number;
    l_t0    timestamp;
    n       number;
  begin
    say('== '||p_name||' '||p_int||', 2 minutes, source TEST');
    l_det := state;
    ok(l_lbl||': precondition - nothing active and the target clean', n_active = 0 and l_det is null, l_det);
    ok(l_lbl||': precondition - both dispatchers are running', dispatchers_up, disp_sessions);
    select enabled, load_pct into l_en0, l_load0 from DRIVER_CONTROL where id = 1;
    if p_name = 'plan_regression' and p_int = 'LOW' then
      ok('plan_regression: browse uses PROD_CAT_IX before the start (explain plan)', browse_uses_index);
    end if;
    if p_name = 'batch_wrong_time' then
      -- the days the run re-settles: LOW the latest settled day, HIGH the latest 7 ([g_day, g_last])
      g_days := case when p_int = 'HIGH' then 7 else 1 end;
      select max(settle_date) into g_last from DAILY_SETTLEMENT;
      select min(settle_date) into g_day
        from (select settle_date from DAILY_SETTLEMENT order by settle_date desc fetch first g_days rows only);
      select sum(n_payments), sum(amount) into g_day_n, g_day_amt
        from DAILY_SETTLEMENT where settle_date between g_day and g_last;
      select count(*) into g_day_y from PAYMENTS
       where paid_ts >= cast(g_day as timestamp) and paid_ts < cast(g_last + 1 as timestamp) and settled = 'Y';
    end if;
    l_t0  := utc_now;
    l_s1 := disp_sid(c_disp1);
    l_s2 := disp_sid(c_disp2);
    l_id  := SHOP.PKG_CHAOS.start_scenario(p_name, 2, p_int, 'TEST');
    g_run := l_id;
    r := run_rec(l_id);
    ok(l_lbl||': start_scenario returns run '||l_id||': QUEUED (or already claimed), '||p_int||', TEST',
       r.status in ('QUEUED', 'RUNNING') and r.intensity = p_int and r.source = 'TEST' and r.scenario = p_name
       and r.restored = 'N' and r.stop_req = 'N',
       r.status||'/'||r.intensity||'/'||r.source);
    ok(l_lbl||': dispatcher 1 claims it within 10 s', wait_started(l_id, 10), exec_of(l_id));
    r := run_rec(l_id);
    ok(l_lbl||': it runs in dispatcher 1''s existing session (no new session for the incident)',
       (r.sid_a = l_s1 or handover_since(l_t0)) and disp_sid(c_disp1) = r.sid_a,
       'claimed by '||r.sid_a||', dispatcher 1 was '||l_s1);
    ok(l_lbl||': planned end = start + 2 minutes', abs(secs(r.start_ts, r.planned_end_ts) - 120) < 0.01,
       ts(r.start_ts)||' - '||ts(r.planned_end_ts));
    say('run '||l_id||' started '||ts(r.start_ts)||' (requested '||ts(r.requested_ts)||'), planned end '
        ||ts(r.planned_end_ts)||', dispatcher sessions '||disp_sessions);

    if p_int = 'HIGH' then
      during_high(p_name, l_id, r.start_ts);
    else
      during(p_name, l_id, r.start_ts);
    end if;
    ok(l_lbl||': dispatcher 2 kept polling in its own session meanwhile', same_session(c_disp2, l_s2, l_t0),
       l_s2||' -> '||nvl(to_char(disp_sid(c_disp2)), '-')
       ||case when handover_since(l_t0) then ' (an hourly hand-over came in between)' end);

    l_st := wait_ended(l_id, greatest(secs(utc_now, r.planned_end_ts), 0) + 90);
    r := run_rec(l_id);
    ok(l_lbl||': ends by itself within 30 s of its planned end',
       r.end_ts between r.planned_end_ts and r.planned_end_ts + interval '30' second,
       'planned '||ts(r.planned_end_ts)||', ended '||nvl(ts(r.end_ts), '-'));
    end_checks(l_id, l_lbl, 'DONE');
    select enabled, load_pct into l_en1, l_load1 from DRIVER_CONTROL where id = 1;
    ok(l_lbl||': DRIVER_CONTROL.enabled and load_pct untouched', l_en1 = l_en0 and l_load1 = l_load0,
       l_en0||'/'||l_load0||' -> '||l_en1||'/'||l_load1);
    -- after-checks
    case p_name
    when 'plan_regression' then
      if p_int = 'LOW' then
        ok('plan_regression: browse uses PROD_CAT_IX again after the restore', browse_uses_index);
      end if;
      ok(l_lbl||': both indexes VISIBLE again', visibility('PROD_CAT_IX') = 'VISIBLE' and visibility('ORD_CUST_IX') = 'VISIBLE');
    when 'slow_drift' then
      select count(*) into n from PROMO_RULES where rule_id >= 9000000;
      ok(l_lbl||': the restore deleted exactly the added rules', n = 0, n);
      select count(*) into n from PROMO_RULES where rule_id < 9000000;
      ok(l_lbl||': the 300 seed rules are still there', n = 300, n);
      select nvl(sum(blocks), 0) into n from user_segments where segment_name = 'PROMO_RULES';
      ok(l_lbl||': PROMO_RULES compact again (moved online: the high-water mark is back)', n <= 16, n||' blocks');
    when 'batch_wrong_time' then
      select count(*) into n from (
        select sum(n_payments) np, sum(amount) amt from DAILY_SETTLEMENT where settle_date between g_day and g_last)
       where np = g_day_n and amt = g_day_amt;
      ok(l_lbl||': DAILY_SETTLEMENT for the re-run day(s) unchanged (the re-run changed nothing)', n = 1,
         g_day_n||' payments, '||g_day_amt||' over '||to_char(g_day, 'YYYY-MM-DD')||' - '||to_char(g_last, 'YYYY-MM-DD'));
      select count(*) into n from PAYMENTS
       where paid_ts >= cast(g_day as timestamp) and paid_ts < cast(g_last + 1 as timestamp) and settled = 'Y';
      ok(l_lbl||': the re-run days'' settled payments unchanged', n = g_day_y, n||' vs '||g_day_y);
    when 'blocking_chain' then
      n := rows_free(1, case when p_int = 'HIGH' then 25 else 5 end);
      ok(l_lbl||': no lock left on the hot products', n = 0, n||' locked');
    else
      null;
    end case;
    g_run := null;
  end scenario_test;

  -- ---------------------------------------------------------------- parts
  procedure part_basic is
    n      number;
    l_c0   number;
    l_id   number;
    l_en   varchar2(1);
    l_load number;
    l_upd0 timestamp;
    l_upd1 timestamp;
    l_txt  varchar2(4000);
    l_sc   varchar2(30);
    l_max  number;
    l_pid  number;
    procedure refused (p_what varchar2, p_code number, p_name varchar2, p_minutes number, p_int varchar2,
                       p_src varchar2) is
    begin
      l_id := SHOP.PKG_CHAOS.start_scenario(p_name, p_minutes, p_int, p_src);
      ok(p_what, false, 'started run '||l_id);
      SHOP.PKG_CHAOS.stop_all('anom_test_chaos: this start should have been refused');
    exception
      when others then
        ok(p_what, sqlcode = p_code, sqlerrm);
    end refused;
  begin
    say('== basic: objects, grants, validation (nothing is injected)');
    select count(*) into n from user_objects
     where object_name = 'PKG_CHAOS' and object_type in ('PACKAGE', 'PACKAGE BODY') and status = 'VALID';
    ok('PKG_CHAOS spec and body are VALID', n = 2);
    select count(*) into n from user_tables where table_name in ('CHAOS_RUN', 'CHAOS_SCRATCH', 'CHAOS_PLAN');
    ok('CHAOS_RUN, CHAOS_SCRATCH and CHAOS_PLAN exist', n = 3);
    select count(*) into n from user_tab_columns
     where table_name = 'CHAOS_RUN' and column_name in ('STOP_REQ', 'EXEC_A', 'SID_A', 'EXEC_B', 'SID_B');
    ok('CHAOS_RUN has the v1.2 columns (STOP_REQ, EXEC_A/B, SID_A/B)', n = 5, n);
    select count(*) into n from user_indexes
     where index_name in ('CHAOS_RUN_ONE_RUNNING_UX', 'CHAOS_RUN_ONE_ACTIVE_UX') and uniqueness = 'UNIQUE'
       and table_name = 'CHAOS_RUN';
    ok('the unique indexes that allow one active (QUEUED or RUNNING) run exist', n = 2);
    select count(*) into n from user_tab_privs_made where grantee = 'CHAOS_CTL' and type != 'USER';
    select count(*) into l_c0 from user_tab_privs_made
     where grantee = 'CHAOS_CTL'
       and ((privilege = 'EXECUTE' and table_name = 'PKG_CHAOS')
         or (privilege = 'SELECT' and table_name in ('CHAOS_RUN', 'DRIVER_CONTROL', 'CHAOS_PLAN'))
         or (privilege = 'INSERT' and table_name = 'CHAOS_PLAN'));
    ok('CHAOS_CTL holds exactly EXECUTE on PKG_CHAOS, SELECT on CHAOS_RUN, DRIVER_CONTROL and CHAOS_PLAN, INSERT on '
       ||'CHAOS_PLAN', n = 5 and l_c0 = 5, n||' privileges, '||l_c0||' of them the contract''s');
    for j in (select enabled, repeat_interval, to_char(start_date, 'TZR') tzr, logging_level,
                     cast(next_run_date at time zone 'UTC' as timestamp) next_run
                from user_scheduler_jobs where job_name = 'CHAOS_REAPER') loop
      ok('CHAOS_REAPER is enabled, every minute at second 15, start date in UTC, logging level FAILED RUNS',
         j.enabled = 'TRUE' and j.repeat_interval = 'FREQ=MINUTELY;INTERVAL=1;BYSECOND=15' and j.tzr = 'UTC'
         and j.logging_level = 'FAILED RUNS',
         j.enabled||' / '||j.repeat_interval||' / '||j.tzr||' / '||j.logging_level);
    end loop;
    select count(*) into n from user_scheduler_jobs
     where job_name in (c_disp1, c_disp2) and enabled = 'TRUE' and repeat_interval = 'FREQ=MINUTELY;INTERVAL=1;BYSECOND=0'
       and to_char(start_date, 'TZR') = 'UTC' and logging_level = 'FAILED RUNS' and job_type = 'PLSQL_BLOCK';
    ok('CHAOS_DISPATCH_1 and _2 are enabled, minutely at second 0, start date in UTC', n = 2, n||' of 2');
    ok('both dispatchers are running', dispatchers_up(5), disp_sessions);
    l_txt := state;
    ok('nothing active, no v1.1 per-run job, the target clean', n_active = 0 and l_txt is null and legacy_jobs = 0, l_txt);

    -- the scenario list and maximum minutes (contract section 5)
    g_max('blocking_chain') := 30;  g_max('plan_regression') := 60; g_max('slow_drift') := 240;
    g_max('batch_wrong_time') := 30; g_max('hard_parse_storm') := 30; g_max('commit_storm') := 30;
    g_max('io_storm') := 30; g_max('cpu_hog') := 30; g_max('temp_spill') := 30; g_max('conn_leak') := 60;
    g_max('logon_storm') := 30; g_max('app_error_burst') := 60;
    n := 0;
    for i in 1 .. c_scenarios.count loop
      if SHOP.PKG_CHAOS.max_minutes(c_scenarios(i)) = g_max(c_scenarios(i)) then
        n := n + 1;
      end if;
    end loop;
    ok('max_minutes knows the 12 scenarios with the contract''s maxima', n = 12, n||' of 12');
    ok('max_minutes is null for any other name', SHOP.PKG_CHAOS.max_minutes('drop_table') is null);

    -- CHAOS_PLAN's own check: a row's minutes never exceed its scenario's maximum (rows rolled back, far future)
    n := 0;
    for i in 1 .. c_scenarios.count loop
      l_sc  := c_scenarios(i);
      l_max := g_max(l_sc);
      l_pid := 9190 + i;
      begin
        insert into CHAOS_PLAN (plan_id, planned_start_ts, scenario, intensity, minutes)
        values (l_pid, timestamp '2099-01-01 00:00:00', l_sc, 'LOW', l_max + 1);
        ok('CHAOS_PLAN refuses '||l_sc||' for '||(l_max + 1)||' minutes', false, 'accepted');
      exception
        when e_check then n := n + 1;
      end;
      begin
        insert into CHAOS_PLAN (plan_id, planned_start_ts, scenario, intensity, minutes)
        values (l_pid, timestamp '2099-01-01 00:00:00', l_sc, 'HIGH', l_max);
      exception
        when others then ok('CHAOS_PLAN accepts '||l_sc||' for its maximum', false, sqlerrm);
      end;
    end loop;
    rollback;
    ok('CHAOS_PLAN refuses every scenario at its maximum + 1 (ORA-02290) and accepts the maximum (rolled back)', n = 12,
       n||' of 12 refused');
    for t in (select 'drop_table' sc, 5 mi from dual union all select 'cpu_hog', 1 from dual
              union all select 'cpu_hog', 2.5 from dual) loop
      begin
        insert into CHAOS_PLAN (plan_id, planned_start_ts, scenario, intensity, minutes)
        values (9199, timestamp '2099-01-01 00:00:00', t.sc, 'LOW', t.mi);
        ok('CHAOS_PLAN refuses '||t.sc||' for '||t.mi||' minutes', false, 'accepted');
      exception
        when e_check then ok('CHAOS_PLAN refuses '||t.sc||' for '||t.mi||' minutes', true, sqlerrm);
      end;
    end loop;
    rollback;

    -- validation: every refusal leaves no row behind
    l_c0 := n_runs;
    refused('unknown scenario: -20101', -20101, 'drop_everything', 5, 'LOW', 'TEST');
    refused('null scenario: -20101', -20101, null, 5, 'LOW', 'TEST');
    refused('a scenario name 300 characters long: -20101', -20101, rpad('x', 300, 'x'), 5, 'LOW', 'TEST');
    refused('intensity MEDIUM: -20102', -20102, 'cpu_hog', 5, 'MEDIUM', 'TEST');
    refused('null intensity: -20102', -20102, 'cpu_hog', 5, null, 'TEST');
    refused('1 minute: -20103', -20103, 'cpu_hog', 1, 'LOW', 'TEST');
    refused('cpu_hog for 31 minutes (max 30): -20103', -20103, 'cpu_hog', 31, 'LOW', 'TEST');
    refused('slow_drift for 241 minutes (max 240): -20103', -20103, 'slow_drift', 241, 'LOW', 'TEST');
    refused('plan_regression for 61 minutes (max 60): -20103', -20103, 'plan_regression', 61, 'LOW', 'TEST');
    refused('2.5 minutes: -20103', -20103, 'cpu_hog', 2.5, 'LOW', 'TEST');
    refused('null minutes: -20103', -20103, 'cpu_hog', null, 'LOW', 'TEST');
    refused('source CRON: -20105', -20105, 'cpu_hog', 5, 'LOW', 'CRON');
    ok('the refused starts added no CHAOS_RUN row', n_runs = l_c0, l_c0||' -> '||n_runs);

    -- run() and dispatch() outside a dispatcher job
    begin
      SHOP.PKG_CHAOS.run(1);
      ok('run() called outside a dispatcher job: -20106', false, 'no error');
    exception
      when others then ok('run() called outside a dispatcher job: -20106', sqlcode = -20106, sqlerrm);
    end;
    begin
      SHOP.PKG_CHAOS.run(-1);
      ok('run() with a bad id: -20106', false, 'no error');
    exception
      when others then ok('run() with a bad id: -20106', sqlcode = -20106, sqlerrm);
    end;
    for k in 1 .. 3 loop
      begin
        SHOP.PKG_CHAOS.dispatch(k);
        ok('dispatch('||k||') called outside its job: -20106', false, 'no error');
      exception
        when others then ok('dispatch('||k||') called outside its job: -20106', sqlcode = -20106, sqlerrm);
      end;
    end loop;
    -- restore of a run that does not exist
    begin
      SHOP.PKG_CHAOS.restore(-1);
      ok('restore of an unknown run: -20107', false, 'no error');
    exception
      when others then ok('restore of an unknown run: -20107', sqlcode = -20107, sqlerrm);
    end;
    begin
      SHOP.PKG_CHAOS.restore(null);
      ok('restore(null): -20107', false, 'no error');
    exception
      when others then ok('restore(null): -20107', sqlcode = -20107, sqlerrm);
    end;

    -- set_load: refusals, then a call that sets the values they already have (changes nothing for the driver)
    for t in (select 'X' en, 100 pct from dual union all select 'Y', -1 from dual union all select 'Y', 201 from dual
              union all select 'Y', null from dual union all select null, 100 from dual) loop
      begin
        SHOP.PKG_CHAOS.set_load(t.en, t.pct);
        ok('set_load('||nvl(t.en, 'null')||', '||nvl(to_char(t.pct), 'null')||'): -20108', false, 'accepted');
      exception
        when others then
          ok('set_load('||nvl(t.en, 'null')||', '||nvl(to_char(t.pct), 'null')||'): -20108', sqlcode = -20108, sqlerrm);
      end;
    end loop;
    select enabled, load_pct, updated_ts into l_en, l_load, l_upd0 from DRIVER_CONTROL where id = 1;
    SHOP.PKG_CHAOS.set_load(lower(l_en), l_load);
    select updated_ts into l_upd1 from DRIVER_CONTROL where id = 1;
    select count(*) into n from DRIVER_CONTROL where id = 1 and enabled = l_en and load_pct = l_load;
    ok('set_load writes DRIVER_CONTROL (same values: '||l_en||', '||l_load||'; updated_ts moves)',
       n = 1 and l_upd1 > l_upd0, ts(l_upd0)||' -> '||ts(l_upd1));

    -- stop_all with nothing active changes nothing
    l_c0 := n_runs;
    SHOP.PKG_CHAOS.stop_all('anom_test_chaos basic: nothing is active');
    ok('stop_all with nothing active: no error, no row changed, target clean', n_runs = l_c0 and state is null);
  end part_basic;

  -- the dispatcher and the STOP flag
  procedure part_dispatch is
    l_id  number;
    r     CHAOS_RUN%rowtype;
    l_s1  number;
    l_s2  number;
    l_t0  timestamp;
    l_sec number;
    n     number;
  begin
    say('== dispatch: app_error_burst LOW 3 min, claimed by dispatcher 1, stopped through its STOP flag after 20 s');
    ok('precondition: nothing active and the target clean', n_active = 0 and state is null, state);
    ok('precondition: both dispatchers are running', dispatchers_up, disp_sessions);
    l_s1 := disp_sid(c_disp1);
    l_s2 := disp_sid(c_disp2);
    l_id  := SHOP.PKG_CHAOS.start_scenario('app_error_burst', 3, 'LOW', 'TEST');
    g_run := l_id;
    r := run_rec(l_id);
    ok('start_scenario queues the run (QUEUED, no start time, no executor yet) unless already claimed',
       (r.status = 'QUEUED' and r.start_ts is null and r.exec_a is null) or (r.status = 'RUNNING' and r.exec_a = c_disp1),
       r.status||', start '||nvl(ts(r.start_ts), '-')||', '||exec_of(l_id));
    l_t0 := utc_now;
    ok('dispatcher 1 claims it within 5 s (it polls every 2 s)', wait_started(l_id, 5), round(since(l_t0), 1)||' s');
    r := run_rec(l_id);
    ok('the run executes in dispatcher 1''s existing session; nothing for half B',
       r.exec_a = c_disp1 and r.sid_a = l_s1 and r.exec_b is null and r.job_name = c_disp1,
       exec_of(l_id)||', dispatcher 1 session '||l_s1);
    ok('the queue delay is not charged to the run: planned end = start + 3 min',
       abs(secs(r.start_ts, r.planned_end_ts) - 180) < 0.01, ts(r.start_ts)||' - '||ts(r.planned_end_ts));
    sleep_until(r.start_ts + interval '5' second);
    select count(*) into n from DRIVER_CONTROL where id = 1 and error_pct = 3;
    ok('the scenario acts: DRIVER_CONTROL.error_pct = 3', n = 1);
    ok('no extra SHOP job session for the incident: exactly the two dispatchers run, in the same sessions',
       running_jobs('^CHAOS_DISPATCH_[12]$') = 2 and disp_sid(c_disp1) = l_s1 and same_session(c_disp2, l_s2, r.requested_ts)
       and running_jobs('^CHAOS_[0-9]+') = 0, disp_sessions);
    sleep_until(r.start_ts + interval '20' second);
    l_t0 := utc_now;
    SHOP.PKG_CHAOS.stop_all('anom_test_chaos dispatch: STOP flag');
    l_sec := since(l_t0);
    r := run_rec(l_id);
    ok('stop_all returns once the dispatcher has ended the run (STOP flag read within 2 s; well under 30 s)',
       l_sec < 15, round(l_sec, 1)||' s');
    ok('the run is STOPPED early, its STOP flag set, the stop noted', r.status = 'STOPPED' and r.stop_req = 'Y'
       and r.end_ts < r.planned_end_ts and r.note like 'stopped by stop_all %: anom_test_chaos dispatch: STOP flag%',
       r.status||'/'||r.stop_req||' - '||r.note);
    ok('the dispatcher job was not stopped: the run ended through its flag (no "dispatcher job ... stopped" note)',
       r.note not like '%dispatcher job%stopped%', r.note);
    end_checks(l_id, 'dispatch + STOP flag', 'STOPPED');
    ok('dispatcher 2 is still the same session (or an hourly hand-over came in between)',
       same_session(c_disp2, l_s2, r.requested_ts), l_s2||' -> '||disp_sid(c_disp2));
    g_run := null;
  end part_dispatch;

  -- CHAOS_PLAN rows started (or skipped) by the target itself
  procedure part_plan is
    l_now  timestamp := utc_now;
    p      CHAOS_PLAN%rowtype;
    r      CHAOS_RUN%rowtype;
    l_t0   timestamp;
    l_s1   number;
    n      number;
    function plan_rec (p_id number) return CHAOS_PLAN%rowtype is
      x CHAOS_PLAN%rowtype;
    begin
      select * into x from CHAOS_PLAN where plan_id = p_id;
      return x;
    end;
    function wait_plan (p_id number, p_max number) return CHAOS_PLAN%rowtype is
      x    CHAOS_PLAN%rowtype;
      l_t  timestamp := utc_now;
    begin
      loop
        x := plan_rec(p_id);
        exit when x.status != 'PLANNED' or since(l_t) > p_max;
        dbms_session.sleep(0.5);
      end loop;
      return x;
    end;
  begin
    say('== plan: CHAOS_PLAN rows due now - a late one, one that starts, one that finds a run active');
    ok('precondition: nothing active and the target clean', n_active = 0 and state is null, state);
    ok('precondition: both dispatchers are running', dispatchers_up, disp_sessions);
    delete from CHAOS_PLAN where plan_id between 9100 and 9189;
    commit;
    l_s1 := disp_sid(c_disp1);
    -- 1. a row 6 minutes late (the dispatchers were not running then): SKIPPED as missed, never started late
    insert into CHAOS_PLAN (plan_id, planned_start_ts, scenario, intensity, minutes)
    values (9103, l_now - interval '6' minute, 'cpu_hog', 'LOW', 2);
    commit;
    p := wait_plan(9103, 10);
    ok('a plan row 6 minutes late is SKIPPED as missed, nothing started', p.status = 'SKIPPED' and p.run_id is null
       and p.note like 'missed: due %', p.status||' - '||p.note);
    -- 2. a row due now: started by the target, source SCHEDULE, claimed by dispatcher 1
    l_t0 := utc_now;
    insert into CHAOS_PLAN (plan_id, planned_start_ts, scenario, intensity, minutes)
    values (9101, l_t0, 'app_error_burst', 'LOW', 2);
    commit;
    p := wait_plan(9101, 10);
    ok('a plan row due now is STARTED by a dispatcher within 10 s (no observer call)',
       p.status = 'STARTED' and p.run_id is not null and p.note like 'started by CHAOS_DISPATCH_%',
       p.status||' run '||p.run_id||' - '||p.note||' after '||round(since(l_t0), 1)||' s');
    if p.run_id is not null then
      g_run := p.run_id;
      ok('its run is claimed by dispatcher 1 in its existing session', wait_started(p.run_id, 10)
         and run_rec(p.run_id).sid_a = l_s1, exec_of(p.run_id));
      r := run_rec(p.run_id);
      ok('the run carries source SCHEDULE and the plan''s scenario, intensity and minutes',
         r.source = 'SCHEDULE' and r.scenario = 'app_error_burst' and r.intensity = 'LOW'
         and abs(secs(r.start_ts, r.planned_end_ts) - 120) < 0.01, r.source||' '||r.scenario||' '||r.intensity);
      -- 3. another row due now while that run is active: SKIPPED, not retried
      l_t0 := utc_now;   -- (a local function cannot be called inside SQL)
      insert into CHAOS_PLAN (plan_id, planned_start_ts, scenario, intensity, minutes)
      values (9102, l_t0, 'cpu_hog', 'LOW', 2);
      commit;
      p := wait_plan(9102, 10);
      ok('a plan row that finds another run active is SKIPPED (-20104), not retried',
         p.status = 'SKIPPED' and p.run_id is null and p.note like '%ORA-20104%', p.status||' - '||p.note);
      dbms_session.sleep(5);
      p := plan_rec(9102);
      select count(*) into n from CHAOS_RUN where status in ('QUEUED', 'RUNNING');
      ok('it stays SKIPPED and only one run is active', p.status = 'SKIPPED' and n = 1, p.status||', '||n||' active');
      SHOP.PKG_CHAOS.stop_all('anom_test_chaos plan: test rows done');
      end_checks(r.run_id, 'plan-started run + stop_all', 'STOPPED');
    end if;
    delete from CHAOS_PLAN where plan_id between 9100 and 9189;
    n := sql%rowcount;
    commit;
    ok('the test plan rows are deleted (CHAOS_RUN keeps the run as the record)', n = 3, n||' rows');
    g_run := null;
  end part_plan;

  procedure part_cpu_hog_high is
    l_id  number;
    r     CHAOS_RUN%rowtype;
    l_ok  boolean := false;
    l_t0  timestamp;
    l_s1  number;
    l_s2  number;
  begin
    say('== cpu_hog HIGH, 2 minutes, both dispatchers; stopped by stop_all after a minute');
    ok('precondition: nothing active and the target clean', n_active = 0 and state is null, state);
    ok('precondition: both dispatchers are running', dispatchers_up, disp_sessions);
    l_s1 := disp_sid(c_disp1);
    l_s2 := disp_sid(c_disp2);
    l_id  := SHOP.PKG_CHAOS.start_scenario('cpu_hog', 2, 'HIGH', 'TEST');
    g_run := l_id;
    ok('cpu_hog HIGH: dispatcher 1 claims it', wait_started(l_id, 10), exec_of(l_id));
    l_t0 := utc_now;
    loop
      r := run_rec(l_id);
      l_ok := r.exec_a = c_disp1 and r.sid_a = l_s1 and r.exec_b = c_disp2 and r.sid_b = l_s2;
      exit when l_ok or since(l_t0) > 15;
      dbms_session.sleep(0.5);
    end loop;
    ok('cpu_hog HIGH: dispatcher 2 runs the second half in its own existing session (two sessions, none new)', l_ok,
       exec_of(l_id)||'; dispatcher sessions were '||l_s1||'/'||l_s2);
    ok('cpu_hog HIGH: the run names both dispatchers', r.job_name = c_disp1||','||c_disp2, r.job_name);
    r := run_rec(l_id);
    say('run '||l_id||' started '||ts(r.start_ts)||', planned end '||ts(r.planned_end_ts));
    sleep_until(r.start_ts + interval '62' second);
    r := run_rec(l_id);
    ok('cpu_hog HIGH: busy without idle seconds', num_in(r.note, '([0-9]+) s busy') >= 20
       and num_in(r.note, '([0-9]+) s idle') = 0, r.note);
    ok('cpu_hog HIGH: both dispatchers still in the same sessions after a minute (busy dispatchers never hand over)',
       disp_sid(c_disp1) = l_s1 and disp_sid(c_disp2) = l_s2, disp_sessions);
    SHOP.PKG_CHAOS.stop_all('anom_test_chaos: stop_all test (cpu_hog HIGH)');
    r := run_rec(l_id);
    ok('stop_all: the run is STOPPED before its planned end', r.status = 'STOPPED' and r.end_ts < r.planned_end_ts,
       r.status||', ended '||ts(r.end_ts)||', planned '||ts(r.planned_end_ts));
    ok('stop_all: the note says who stopped it', r.note like 'stopped by stop_all %: anom_test_chaos: stop_all test%',
       r.note);
    ok('stop_all: both halves ended through the STOP flag; no dispatcher job was stopped',
       r.note not like '%dispatcher job%stopped%' and same_session(c_disp1, l_s1, r.start_ts)
       and same_session(c_disp2, l_s2, r.start_ts), disp_sessions);
    end_checks(l_id, 'cpu_hog HIGH + stop_all', 'STOPPED');
    g_run := null;
  end part_cpu_hog_high;

  procedure part_stop_all is
    l_id  number;
    r     CHAOS_RUN%rowtype;
    l_v   varchar2(10);
  begin
    say('== stop_all on plan_regression LOW (4 minutes), stopped once the index is invisible');
    ok('precondition: nothing active and the target clean', n_active = 0 and state is null, state);
    l_id  := SHOP.PKG_CHAOS.start_scenario('plan_regression', 4, 'LOW', 'TEST');
    g_run := l_id;
    ok('plan_regression: dispatcher 1 claims it', wait_started(l_id, 10));
    l_v := wait_visibility('PROD_CAT_IX', 'INVISIBLE', 30);
    ok('plan_regression: PROD_CAT_IX is invisible', l_v = 'INVISIBLE', l_v);
    dbms_session.sleep(20);
    SHOP.PKG_CHAOS.stop_all('anom_test_chaos: stop_all restores');
    ok('stop_all: PROD_CAT_IX is VISIBLE when stop_all returns', visibility('PROD_CAT_IX') = 'VISIBLE');
    r := run_rec(l_id);
    ok('stop_all: STOPPED early, restored', r.status = 'STOPPED' and r.restored = 'Y' and r.end_ts < r.planned_end_ts,
       r.status||'/'||r.restored);
    end_checks(l_id, 'plan_regression + stop_all', 'STOPPED');
    ok('plan_regression + stop_all: browse uses PROD_CAT_IX again', browse_uses_index);
    g_run := null;
  end part_stop_all;

  procedure part_blocking_chain_error is
    l_id  number;
    r     CHAOS_RUN%rowtype;
    n     number;
    l_x   number;
    l_got boolean := false;
    l_st  varchar2(10);
  begin
    say('== blocking_chain LOW made to fail: this session holds hot product 5 when the second cycle starts');
    ok('precondition: nothing active and the target clean', n_active = 0 and state is null, state);
    l_id  := SHOP.PKG_CHAOS.start_scenario('blocking_chain', 2, 'LOW', 'TEST');
    g_run := l_id;
    ok('blocking_chain: dispatcher 1 claims it', wait_started(l_id, 10));
    r := run_rec(l_id);
    say('run '||l_id||' started '||ts(r.start_ts));
    sleep_until(r.start_ts + interval '20' second);
    n := rows_free(1, 5);
    ok('blocking_chain: the first cycle has let hot products 1-5 go (t0+20 s)', n = 0, n||' locked');
    -- this session takes product 5's row and keeps it (its own open transaction)
    for i in 1 .. 10 loop
      begin
        select product_id into l_x from INVENTORY where product_id = 5 for update nowait;
        l_got := true;
        exit;
      exception
        when e_busy then dbms_session.sleep(0.5);
      end;
    end loop;
    ok('the test holds hot product 5 from t0+20 s', l_got);
    sleep_until(r.start_ts + interval '64' second);
    n := locked_count(1, 4);
    ok('blocking_chain: at t0+64 s the run holds products 1-4 and waits for 5', n = 4, n||' of 4 locked');
    l_st := wait_ended(l_id, 30);
    r := run_rec(l_id);
    ok('blocking_chain: after 10 s the run gives up (ORA-30006; 26ai says ORA-00054) and is FAILED',
       l_st = 'FAILED' and (r.note like '%ORA-30006%' or r.note like '%ORA-00054%'), l_st||' - '||r.note);
    n := rows_free(1, 4);
    ok('blocking_chain: the failed run rolled back - products 1-4 are free while the test still holds 5', n = 0,
       n||' locked');
    rollback;                                        -- releases product 5
    ok('the test let product 5 go', not row_locked(5));
    -- the dispatcher records the failure on the run and goes on in the same session (no session change)
    end_checks(l_id, 'blocking_chain error path', 'FAILED');
    g_run := null;
  end part_blocking_chain_error;

  procedure part_reaper_orphan is
    l_id  number;
    r     CHAOS_RUN%rowtype;
    l_v   varchar2(10);
    l_st  varchar2(10);
  begin
    say('== orphan: plan_regression LOW whose dispatcher job is stopped outside PKG_CHAOS');
    ok('precondition: nothing active and the target clean', n_active = 0 and state is null, state);
    l_id  := SHOP.PKG_CHAOS.start_scenario('plan_regression', 2, 'LOW', 'TEST');
    g_run := l_id;
    ok('plan_regression: dispatcher 1 claims it', wait_started(l_id, 10));
    l_v := wait_visibility('PROD_CAT_IX', 'INVISIBLE', 30);
    ok('plan_regression: PROD_CAT_IX is invisible', l_v = 'INVISIBLE', l_v);
    -- a stopped job's session ends without running its exception handler: nothing restores, the run stays RUNNING
    -- until the restarted dispatcher's sweep (often within seconds: the scheduler starts the job again at once) or
    -- the reaper ends it. Either the orphan is seen, or it has been swept already.
    dbms_scheduler.stop_job(job_name => 'SHOP.'||c_disp1, force => true);
    for i in 1 .. 20 loop
      exit when disp_sid(c_disp1) is null or disp_sid(c_disp1) != run_rec(l_id).sid_a;
      dbms_session.sleep(0.5);
    end loop;
    r := run_rec(l_id);
    ok('the executing session is gone; the run is an orphan (RUNNING, PROD_CAT_IX invisible) or already swept by the '
       ||'restarted dispatcher', (disp_sid(c_disp1) is null or disp_sid(c_disp1) != r.sid_a)
       and ((r.status = 'RUNNING' and visibility('PROD_CAT_IX') = 'INVISIBLE')
            or (r.status = 'FAILED' and r.note like 'dispatcher CHAOS_DISPATCH_1 restarted %')),
       r.status||', ran in '||r.sid_a||', dispatcher 1 now '||nvl(to_char(disp_sid(c_disp1)), 'not running')
       ||', '||visibility('PROD_CAT_IX'));
    say('dispatcher 1 stopped outside PKG_CHAOS at '||ts(utc_now)||'; waiting for its restart or CHAOS_REAPER');
    l_st := wait_ended(l_id, 150);
    r := run_rec(l_id);
    ok('the orphaned run is FAILED by the restarted dispatcher or the reaper',
       l_st = 'FAILED' and (r.note like 'dispatcher CHAOS_DISPATCH_1 restarted %' or r.note like 'reaper %executor session is gone%'),
       l_st||' at '||ts(r.end_ts)||' - '||r.note);
    ok('PROD_CAT_IX is VISIBLE again', wait_visibility('PROD_CAT_IX', 'VISIBLE', 30) = 'VISIBLE');
    end_checks(l_id, 'orphan', 'FAILED', null);
    g_run := null;
  end part_reaper_orphan;

  procedure part_reaper_overstay is
    l_id  number;
    r     CHAOS_RUN%rowtype;
    l_st  varchar2(10);
    l_now timestamp := utc_now;
  begin
    say('== reaper: a RUNNING row 2.5 minutes past its planned end (no executor), with leftovers');
    ok('precondition: nothing active and the target clean', n_active = 0 and state is null, state);
    insert into CHAOS_RUN (scenario, intensity, source, requested_ts, start_ts, planned_end_ts, status, restored, note)
    values ('slow_drift', 'LOW', 'TEST', l_now - interval '270' second, l_now - interval '270' second,
            l_now - interval '150' second, 'RUNNING', 'N',
            'anom_test_chaos: simulated overstay (no executor), one drift rule and one CHAOS_SCRATCH row left')
    returning run_id into l_id;
    g_run := l_id;
    insert into PROMO_RULES (rule_id, category_id, min_total, discount_pct, valid_to)
    values (9999999, 1, 999999, 1, date '2000-01-01');
    insert into CHAOS_SCRATCH (run_id, seq, pad) values (l_id, 1, 'reaper test');
    commit;
    ok('broken state recorded: run RUNNING past its end, a drift rule, a CHAOS_SCRATCH row', state is not null, state);
    l_st := wait_ended(l_id, 90);
    r := run_rec(l_id);
    ok('reaper: the overstayed run is STOPPED with the reaper''s note',
       l_st = 'STOPPED' and r.note like 'reaper %2 min past its planned end%', l_st||' - '||r.note);
    end_checks(l_id, 'reaper overstay', 'STOPPED', null);
    g_run := null;
  end part_reaper_overstay;

  procedure part_reaper_repair is
    l_id  number;
    r     CHAOS_RUN%rowtype;
    l_det varchar2(4000);
    l_t0  timestamp;
    l_now timestamp := utc_now;
  begin
    say('== reaper: nothing active, the state broken by hand');
    ok('precondition: nothing active and the target clean', n_active = 0 and state is null, state);
    -- the record of the deliberate break (not RUNNING: the reaper's "nothing active" branch is what is tested)
    insert into CHAOS_RUN (scenario, intensity, source, requested_ts, start_ts, planned_end_ts, end_ts, status,
                           restored, note)
    values ('plan_regression', 'LOW', 'TEST', l_now, l_now, l_now + interval '2' minute, l_now + interval '2' minute,
            'FAILED', 'N', 'anom_test_chaos: state broken by hand with nothing RUNNING (reaper repair test)')
    returning run_id into l_id;
    commit;
    execute immediate 'alter index SHOP.PROD_CAT_IX invisible';
    insert into PROMO_RULES (rule_id, category_id, min_total, discount_pct, valid_to)
    values (9999998, 1, 999999, 1, date '2000-01-01');
    insert into CHAOS_SCRATCH (run_id, seq, pad) values (l_id, 1, 'reaper test');
    update DRIVER_CONTROL set conn_leak_target = 10, updated_ts = sys_extract_utc(systimestamp) where id = 1;
    commit;
    l_det := state;
    ok('broken: index invisible, a drift rule, a CHAOS_SCRATCH row, conn_leak_target 10',
       l_det like '%PROD_CAT_IX is INVISIBLE%' and l_det like '%1 slow_drift rows%'
       and l_det like '%CHAOS_SCRATCH is not empty%' and l_det like '%DRIVER_CONTROL%', l_det);
    say('state broken at '||ts(utc_now)||'; waiting for CHAOS_REAPER');
    l_t0 := utc_now;
    loop
      l_det := state;
      exit when l_det is null or since(l_t0) > 90;
      dbms_session.sleep(1);
    end loop;
    ok('reaper: repaired within 90 s', l_det is null, round(since(l_t0))||' s; '||l_det);
    update CHAOS_RUN set end_ts = sys_extract_utc(systimestamp) where run_id = l_id;
    commit;
    r := run_rec(l_id);
    ok('reaper: the repair is noted on the latest run and it is marked restored',
       r.note like 'reaper %: repaired %' and r.restored = 'Y', r.restored||' - '||r.note);
    ok('reaper repair: PROD_CAT_IX visible, no rule 9999998, CHAOS_SCRATCH empty, conn_leak_target 0',
       state is null);
    show_run(l_id);
  end part_reaper_repair;

  procedure part_final is
    n     number;
    l_det varchar2(4000);
  begin
    say('== final');
    l_det := state;
    ok('nothing active, no v1.1 per-run job, the target clean', n_active = 0 and legacy_jobs = 0 and l_det is null, l_det);
    ok('both dispatchers are running', dispatchers_up, disp_sessions);
    select count(*) into n from CHAOS_PLAN where plan_id between 9100 and 9199;
    ok('no test plan row (9100-9199) is left in CHAOS_PLAN', n = 0, n);
    select count(*) into n from CHAOS_PLAN where status = 'PLANNED' and planned_start_ts < sys_extract_utc(systimestamp);
    ok('no PLANNED row is overdue', n = 0, n);
    select count(*) into n from user_scheduler_job_run_details
     where job_name in ('CHAOS_REAPER', c_disp1, c_disp2) and status not in ('SUCCEEDED', 'STOPPED');
    ok('CHAOS_REAPER and the dispatchers never failed (no run in the log other than SUCCEEDED, or STOPPED by a test)',
       n = 0, n||' failed runs');
    select count(*) into n from CHAOS_RUN where source = 'TEST' and restored != 'Y';
    ok('every TEST run is restored', n = 0, n||' not restored');
    for r in (select run_id, scenario, intensity, status, restored, start_ts, end_ts
                from CHAOS_RUN where source in ('TEST', 'SCHEDULE') order by run_id) loop
      say(rpad(r.run_id, 5)||rpad(r.scenario, 18)||rpad(r.intensity, 5)||rpad(r.status, 8)||r.restored||'  '
          ||to_char(r.start_ts, 'YYYY-MM-DD HH24:MI:SS')||' - '||to_char(r.end_ts, 'HH24:MI:SS'));
    end loop;
  end part_final;

begin
  if sys_context('USERENV', 'SESSION_USER') != 'SHOP' then
    raise_application_error(-20900, 'anom_test_chaos.sql: run as SHOP, not '||sys_context('USERENV', 'SESSION_USER'));
  end if;
  rollback;
  say('anom_test_chaos v1.5 part '||c_part||' '||to_char(sys_extract_utc(systimestamp), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
  case
    when c_part = 'basic'                then part_basic;
    when c_part = 'dispatch'             then part_dispatch;
    when c_part = 'plan'                 then part_plan;
    when c_part = 'cpu_hog_high'         then part_cpu_hog_high;
    when c_part = 'stop_all'             then part_stop_all;
    when c_part = 'blocking_chain_error' then part_blocking_chain_error;
    when c_part = 'reaper_orphan'        then part_reaper_orphan;
    when c_part = 'reaper_overstay'      then part_reaper_overstay;
    when c_part = 'reaper_repair'        then part_reaper_repair;
    when c_part = 'final'                then part_final;
    when c_part member of c_scenarios    then scenario_test(c_part);
    when c_part like 'high\_%' escape '\' and substr(c_part, 6) member of c_scenarios and c_part != 'high_cpu_hog'
                                         then scenario_test(substr(c_part, 6), 'HIGH');
    else raise_application_error(-20900, 'anom_test_chaos.sql: unknown part "'||c_part||'"');
  end case;
  rollback;
  :n_pass := g_pass;
  :n_fail := g_fail;
exception
  when others then
    rollback;
    dbms_output.put_line('FAIL  unexpected error: '||translate(sqlerrm, chr(10)||chr(13), '  '));
    dbms_output.put_line(translate(dbms_utility.format_error_backtrace, chr(10)||chr(13), '  '));
    if g_run is not null then
      SHOP.PKG_CHAOS.stop_all('anom_test_chaos: aborted after an unexpected error');
      dbms_output.put_line('      stop_all called for run '||g_run);
    end if;
    delete from CHAOS_PLAN where plan_id between 9100 and 9189;
    commit;
    :n_pass := g_pass;
    :n_fail := g_fail + 1;
end;
/

begin
  dbms_output.put_line('anom_test_chaos &part.: '||:n_pass||' passed, '||:n_fail||' failed');
end;
/
undefine part
undefine 1
exit :n_fail rollback
