-- v1.1 - ANOMOPS in ora26ai / ORCLPDB1 (the observer) for application 900: the signal catalogue, the per-minute
--        metric store, the application feed table, the collector log and the FEATURE_MINUTE view.
--        Run as ANOMOPS (tools/anom_obs_run.sh run 90_anom_tables.sql <log>). No password is substituted here.
--        v1.1: METRIC_MINUTE.minute_key is a stored column set by the collector (92), no longer the virtual
--              trunc(begin_time + 30 s, 'MI'). On oradb1 the 60-s intervals begin at second 28-30, right on that
--              formula's rounding edge, so one-second jitter gave two intervals the same key and left a minute
--              with none (7 of 75 rows on 01-Oct). The key is still the interval's nearest whole minute, measured
--              against the target's stable metric phase (the circular mean of the begin second over the previous
--              5 intervals and this one) instead of each row's jittering second. An existing virtual column is
--              replaced in place and the keys of existing rows are computed with the same rule.
--        v1.0: first version, 01-Oct-2026.
--
--        SIGNAL_DEF      the 35 pre-registered signals of PLAN.md section 5, seeded by MERGE. source_name is the
--                        exact V$CON_SYSMETRIC metric name, the wait class name, or the APP_MINUTE column.
--                        A re-run refreshes source, source_name, unit and display_seq but keeps in_model, which
--                        phase 4 sets to 'N' for a signal that is constant over the training window.
--        METRIC_MINUTE   one row per 60-second metric interval of the target, keyed on the interval's begin time
--                        (a DATE in the target's clock, which is UTC). minute_key = the whole minute the interval
--                        stands for (see v1.1), the join key to APP_MINUTE. source 'L' = read live (wait columns
--                        filled), 'H' = back-filled from V$CON_SYSMETRIC_HISTORY (no wait-class history exists,
--                        so the wait columns are null). Wait columns are centiseconds waited per second.
--        APP_MINUTE      the load driver's client-side minute, written by ANOM_FEED with MERGE. ANOM_FEED gets
--                        SELECT, INSERT and UPDATE on this table and nothing else in the schema.
--        COLLECT_LOG     collector failures, alignment warnings and back-fills only; success is not logged.
--        FEATURE_MINUTE  METRIC_MINUTE left join APP_MINUTE on ts_minute = minute_key: ts + 35 signals.
--
--        Idempotent: tables are created only when absent and then checked for the expected columns; the trigger,
--        the view and the grants are re-applied; privileges ANOM_FEED holds on any other ANOMOPS object are revoked.
set serveroutput on size unlimited format wrapped
set verify off
set linesize 200
set feedback off
whenever sqlerror exit failure rollback

begin
  if sys_context('USERENV', 'CON_NAME') = 'CDB$ROOT' then
    raise_application_error(-20900, '90: run in the PDB (ORCLPDB1), not in CDB$ROOT');
  end if;
  if sys_context('USERENV', 'SESSION_USER') != 'ANOMOPS' then
    raise_application_error(-20900, '90: run as ANOMOPS');
  end if;
end;
/

-- ------------------------------------------------------------------ tables (created only when absent)
declare
  procedure mk (p_table varchar2, p_ddl varchar2) is
    n number;
  begin
    select count(*) into n from user_tables where table_name = p_table;
    if n = 0 then
      execute immediate p_ddl;
      dbms_output.put_line('  table '||p_table||' created');
    else
      dbms_output.put_line('  table '||p_table||' already present');
    end if;
  end;
begin
  mk('SIGNAL_DEF', q'[
    create table SIGNAL_DEF (
      signal_code  varchar2(20)  not null,
      source       varchar2(10)  not null,
      source_name  varchar2(64)  not null,
      unit         varchar2(40),
      in_model     char(1)       default 'Y' not null,
      display_seq  number(4)     not null,
      constraint signal_def_pk     primary key (signal_code),
      constraint signal_def_src_ck check (source in ('SYSMETRIC', 'WAITCLASS', 'APP')),
      constraint signal_def_inm_ck check (in_model in ('Y', 'N')),
      constraint signal_def_uk     unique (source, source_name)
    )]');

  mk('METRIC_MINUTE', q'[
    create table METRIC_MINUTE (
      begin_time    date          not null,
      end_time      date          not null,
      minute_key    date,         -- set by PKG_ANOM_COLLECT (92), which also makes it NOT NULL
      collected_ts  timestamp     not null,
      intsize_csec  number,
      aas number, dbtime number, cpu number, cpu_txn number, wait_ratio number, lio_txn number, lio number,
      pio number, pio_bytes number, pwrites number, redo number, blkchg number, commits number, txn number,
      calls number, execs number, hardparse number, parse number, logons number, sessions number,
      rt_txn number, sql_rt number, enq_waits number, temp number, longscans number,
      w_userio number, w_commit number, w_concur number, w_appl number, w_config number, w_other number,
      source        char(1)       not null,
      constraint metric_minute_pk     primary key (begin_time),
      constraint metric_minute_src_ck check (source in ('L', 'H')),
      constraint metric_minute_end_ck check (end_time > begin_time)
    )]');

  mk('APP_MINUTE', q'[
    create table APP_MINUTE (
      ts_minute    date        not null,
      n_ok         number      not null,
      n_err        number      not null,
      app_tps      number,
      app_p50_ms   number,
      app_p95_ms   number,
      app_err_pct  number,
      loaded_ts    timestamp   not null,
      constraint app_minute_pk     primary key (ts_minute),
      constraint app_minute_min_ck check (ts_minute = trunc(ts_minute, 'MI')),
      constraint app_minute_cnt_ck check (n_ok >= 0 and n_err >= 0),
      constraint app_minute_pct_ck check (app_err_pct is null or app_err_pct between 0 and 100)
    )]');

  mk('COLLECT_LOG', q'[
    create table COLLECT_LOG (
      log_ts    timestamp       not null,
      severity  varchar2(5)     not null,
      message   varchar2(4000)  not null,
      constraint collect_log_sev_ck check (severity in ('INFO', 'WARN', 'ERROR'))
    )]');
end;
/

-- v1.1 migration: a virtual minute_key (v1.0) becomes a stored column; existing rows get their key by the
-- collector's rule, computed here with analytic sums over the same 6 begin seconds (5 previous + this one)
alter session set ddl_lock_timeout = 30;
declare
  n number;
begin
  select count(*) into n from user_tab_cols
   where table_name = 'METRIC_MINUTE' and column_name = 'MINUTE_KEY' and virtual_column = 'YES';
  if n = 1 then
    execute immediate 'alter table METRIC_MINUTE drop column minute_key';
    execute immediate 'alter table METRIC_MINUTE add (minute_key date)';
    execute immediate q'[
      merge into METRIC_MINUTE m
      using (select begin_time,
                    trunc(begin_time + round(mod(30 - mod(atan2(ss, sc) * 30 / acos(-1) + 60, 60) + 60, 60)) / 86400,
                          'MI') k
               from (select begin_time,
                            sum(sin(a)) over (order by begin_time rows between 5 preceding and current row) ss,
                            sum(cos(a)) over (order by begin_time rows between 5 preceding and current row) sc
                       from (select begin_time, to_number(to_char(begin_time, 'SS')) * acos(-1) / 30 a
                               from METRIC_MINUTE))) s
         on (m.begin_time = s.begin_time)
       when matched then update set m.minute_key = s.k]';
    dbms_output.put_line('  METRIC_MINUTE.minute_key: virtual column replaced by a stored one; '||sql%rowcount||' rows keyed');
    commit;
  end if;
end;
/

declare
  n number;
begin
  select count(*) into n from user_indexes where index_name = 'METRIC_MINUTE_KEY_IX';
  if n = 0 then
    execute immediate 'create index METRIC_MINUTE_KEY_IX on METRIC_MINUTE (minute_key)';
    dbms_output.put_line('  index METRIC_MINUTE_KEY_IX created');
  end if;
  select count(*) into n from user_indexes where index_name = 'COLLECT_LOG_TS_IX';
  if n = 0 then
    execute immediate 'create index COLLECT_LOG_TS_IX on COLLECT_LOG (log_ts)';
    dbms_output.put_line('  index COLLECT_LOG_TS_IX created');
  end if;
end;
/

-- an existing table must have every column this version expects (a table left by another version fails here)
declare
  procedure need (p_table varchar2, p_cols varchar2) is
    l_missing varchar2(4000);
  begin
    -- split the comma list into rows and keep the names the table does not have
    select listagg(c.col, ',') within group (order by c.col) into l_missing
      from (select regexp_substr(p_cols, '[^,]+', 1, level) col
              from dual connect by level <= regexp_count(p_cols, ',') + 1) c
     where not exists (select 1 from user_tab_columns t
                        where t.table_name = p_table and t.column_name = c.col);
    if l_missing is not null then
      raise_application_error(-20900, '90: '||p_table||' lacks column(s) '||l_missing);
    end if;
  end;
begin
  need('SIGNAL_DEF',    'SIGNAL_CODE,SOURCE,SOURCE_NAME,UNIT,IN_MODEL,DISPLAY_SEQ');
  need('METRIC_MINUTE', 'BEGIN_TIME,END_TIME,MINUTE_KEY,COLLECTED_TS,INTSIZE_CSEC,AAS,DBTIME,CPU,CPU_TXN,WAIT_RATIO,'
                     || 'LIO_TXN,LIO,PIO,PIO_BYTES,PWRITES,REDO,BLKCHG,COMMITS,TXN,CALLS,EXECS,HARDPARSE,PARSE,'
                     || 'LOGONS,SESSIONS,RT_TXN,SQL_RT,ENQ_WAITS,TEMP,LONGSCANS,W_USERIO,W_COMMIT,W_CONCUR,'
                     || 'W_APPL,W_CONFIG,W_OTHER,SOURCE');
  need('APP_MINUTE',    'TS_MINUTE,N_OK,N_ERR,APP_TPS,APP_P50_MS,APP_P95_MS,APP_ERR_PCT,LOADED_TS');
  need('COLLECT_LOG',   'LOG_TS,SEVERITY,MESSAGE');
  dbms_output.put_line('  table shapes checked');
end;
/

-- ------------------------------------------------------------------ the 35 signals (PLAN.md section 5)
-- source_name spellings were read from v$con_sysmetric@ANOM_MON_LINK (group 18) on 01-Oct-2026; units are the
-- target's METRIC_UNIT for the V$ metrics. in_model is set on insert only.
merge into SIGNAL_DEF d
using (
  select 'AAS'         code, 'SYSMETRIC' src, 'Average Active Sessions'           sname, 'Active Sessions'          unit, 10  seq from dual union all
  select 'DBTIME',           'SYSMETRIC',     'Database Time Per Sec',                  'CentiSeconds Per Second',        20  from dual union all
  select 'CPU',              'SYSMETRIC',     'CPU Usage Per Sec',                      'CentiSeconds Per Second',        30  from dual union all
  select 'CPU_TXN',          'SYSMETRIC',     'CPU Usage Per Txn',                      'CentiSeconds Per Txn',           40  from dual union all
  select 'WAIT_RATIO',       'SYSMETRIC',     'Database Wait Time Ratio',               '% Wait/DB_Time',                 50  from dual union all
  select 'LIO_TXN',          'SYSMETRIC',     'Logical Reads Per Txn',                  'Reads Per Txn',                  60  from dual union all
  select 'LIO',              'SYSMETRIC',     'Logical Reads Per Sec',                  'Reads Per Second',               70  from dual union all
  select 'PIO',              'SYSMETRIC',     'Physical Reads Per Sec',                 'Reads Per Second',               80  from dual union all
  select 'PIO_BYTES',        'SYSMETRIC',     'Physical Read Total Bytes Per Sec',      'Bytes Per Second',               90  from dual union all
  select 'PWRITES',          'SYSMETRIC',     'Physical Writes Per Sec',                'Writes Per Second',              100 from dual union all
  select 'REDO',             'SYSMETRIC',     'Redo Generated Per Sec',                 'Bytes Per Second',               110 from dual union all
  select 'BLKCHG',           'SYSMETRIC',     'DB Block Changes Per Sec',               'Blocks Per Second',              120 from dual union all
  select 'COMMITS',          'SYSMETRIC',     'User Commits Per Sec',                   'Commits Per Second',             130 from dual union all
  select 'TXN',              'SYSMETRIC',     'User Transaction Per Sec',               'Transactions Per Second',        140 from dual union all
  select 'CALLS',            'SYSMETRIC',     'User Calls Per Sec',                     'Calls Per Second',               150 from dual union all
  select 'EXECS',            'SYSMETRIC',     'Executions Per Sec',                     'Executes Per Second',            160 from dual union all
  select 'HARDPARSE',        'SYSMETRIC',     'Hard Parse Count Per Sec',               'Parses Per Second',              170 from dual union all
  select 'PARSE',            'SYSMETRIC',     'Total Parse Count Per Sec',              'Parses Per Second',              180 from dual union all
  select 'LOGONS',           'SYSMETRIC',     'Logons Per Sec',                         'Logons Per Second',              190 from dual union all
  select 'SESSIONS',         'SYSMETRIC',     'Session Count',                          'Sessions',                       200 from dual union all
  select 'RT_TXN',           'SYSMETRIC',     'Response Time Per Txn',                  'CentiSeconds Per Txn',           210 from dual union all
  select 'SQL_RT',           'SYSMETRIC',     'SQL Service Response Time',              'CentiSeconds Per Call',          220 from dual union all
  select 'ENQ_WAITS',        'SYSMETRIC',     'Enqueue Waits Per Sec',                  'Waits Per Second',               230 from dual union all
  select 'TEMP',             'SYSMETRIC',     'Temp Space Used',                        'bytes',                          240 from dual union all
  select 'LONGSCANS',        'SYSMETRIC',     'Long Table Scans Per Sec',               'Scans Per Second',               250 from dual union all
  select 'W_USERIO',         'WAITCLASS',     'User I/O',                               'CentiSeconds Waited Per Second', 260 from dual union all
  select 'W_COMMIT',         'WAITCLASS',     'Commit',                                 'CentiSeconds Waited Per Second', 270 from dual union all
  select 'W_CONCUR',         'WAITCLASS',     'Concurrency',                            'CentiSeconds Waited Per Second', 280 from dual union all
  select 'W_APPL',           'WAITCLASS',     'Application',                            'CentiSeconds Waited Per Second', 290 from dual union all
  select 'W_CONFIG',         'WAITCLASS',     'Configuration',                          'CentiSeconds Waited Per Second', 300 from dual union all
  select 'W_OTHER',          'WAITCLASS',     'Other',                                  'CentiSeconds Waited Per Second', 310 from dual union all
  select 'APP_TPS',          'APP',           'APP_TPS',                                'Transactions Per Second',        320 from dual union all
  select 'APP_P50_MS',       'APP',           'APP_P50_MS',                             'Milliseconds',                   330 from dual union all
  select 'APP_P95_MS',       'APP',           'APP_P95_MS',                             'Milliseconds',                   340 from dual union all
  select 'APP_ERR_PCT',      'APP',           'APP_ERR_PCT',                            'Percent',                        350 from dual
) s
on (d.signal_code = s.code)
when matched then update
   set d.source = s.src, d.source_name = s.sname, d.unit = s.unit, d.display_seq = s.seq
 where d.source != s.src or d.source_name != s.sname or decode(d.unit, s.unit, 0, 1) = 1 or d.display_seq != s.seq
when not matched then insert (signal_code, source, source_name, unit, in_model, display_seq)
  values (s.code, s.src, s.sname, s.unit, 'Y', s.seq);

-- a signal code no longer in the list would be a pre-registration change: report it, do not delete it
declare
  n number;
begin
  select count(*) into n from SIGNAL_DEF;
  dbms_output.put_line('  SIGNAL_DEF rows: '||n);
  if n != 35 then
    raise_application_error(-20900, '90: SIGNAL_DEF holds '||n||' rows, 35 expected');
  end if;
end;
/
commit;

-- ------------------------------------------------------------------ APP_MINUTE keeps its own load time
create or replace trigger APP_MINUTE_BIU
before insert or update on APP_MINUTE
for each row
begin
  -- v1.0: the time the row last arrived from the driver, UTC, whatever the driver's MERGE sets
  :new.loaded_ts := sys_extract_utc(systimestamp);
end;
/

-- ------------------------------------------------------------------ the feature view (ts + 35 signals)
create or replace view FEATURE_MINUTE as
select m.begin_time ts,
       m.aas, m.dbtime, m.cpu, m.cpu_txn, m.wait_ratio, m.lio_txn, m.lio, m.pio, m.pio_bytes, m.pwrites,
       m.redo, m.blkchg, m.commits, m.txn, m.calls, m.execs, m.hardparse, m.parse, m.logons, m.sessions,
       m.rt_txn, m.sql_rt, m.enq_waits, m.temp, m.longscans,
       m.w_userio, m.w_commit, m.w_concur, m.w_appl, m.w_config, m.w_other,
       a.app_tps, a.app_p50_ms, a.app_p95_ms, a.app_err_pct
  from METRIC_MINUTE m
  left join APP_MINUTE a on a.ts_minute = m.minute_key;

comment on table SIGNAL_DEF is 'App 900: the 35 pre-registered signals (PLAN.md section 5) and where each is read';
comment on table METRIC_MINUTE is 'App 900: one row per 60-s metric interval of the target (V$CON_SYSMETRIC group 18 + wait classes), UTC';
comment on column METRIC_MINUTE.begin_time is 'Interval begin time from the target, UTC (primary key)';
comment on column METRIC_MINUTE.minute_key is 'The whole minute the interval stands for (nearest minute against the target''s stable metric phase, set by PKG_ANOM_COLLECT); joins APP_MINUTE';
comment on column METRIC_MINUTE.source is 'L = read live (waits filled); H = back-filled from V$CON_SYSMETRIC_HISTORY (waits null)';
comment on table APP_MINUTE is 'App 900: the load driver''s client-side metrics per UTC minute (written by ANOM_FEED)';
comment on table COLLECT_LOG is 'App 900: collector failures, alignment warnings and back-fills (success is not logged)';
comment on table FEATURE_MINUTE is 'App 900: one row per metric interval, ts + the 35 signals, the detectors'' input';

-- ------------------------------------------------------------------ grants: ANOM_FEED gets APP_MINUTE only
grant select, insert, update on APP_MINUTE to ANOM_FEED;

begin
  for g in (select table_name, privilege from user_tab_privs_made
             where grantee = 'ANOM_FEED'
               and not (table_name = 'APP_MINUTE' and privilege in ('SELECT', 'INSERT', 'UPDATE'))) loop
    execute immediate 'revoke '||g.privilege||' on "'||g.table_name||'" from ANOM_FEED';
    dbms_output.put_line('  revoked '||g.privilege||' on '||g.table_name||' from ANOM_FEED');
  end loop;
end;
/

select '  ANOM_FEED: '||listagg(privilege, ', ') within group (order by privilege)||' on '||table_name as grants
  from user_tab_privs_made where grantee = 'ANOM_FEED' group by table_name;

select '  '||source||': '||count(*)||' signals' as signal_def
  from SIGNAL_DEF group by source order by min(display_seq);

select '  FEATURE_MINUTE columns: '||count(*) as feature_view from user_tab_columns where table_name = 'FEATURE_MINUTE';
