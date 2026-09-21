-- v1.0 | 02_schedule.sql | CHANGES STATE
-- The collector has to exist and has to run. Start the job and you have not
-- finished: the first version of this pipeline posted jobs and never collected
-- them, so every row sat at TRANSCRIBING for ever with no error anywhere.

begin
  begin dbms_scheduler.drop_job('SPEECH_COLLECT_JOB', force => true);
  exception when others then null; end;

  dbms_scheduler.create_job(
    job_name        => 'SPEECH_COLLECT_JOB',
    job_type        => 'PLSQL_BLOCK',
    job_action      => 'begin PKG_SPEECH_INGEST.tick; end;',
    repeat_interval => 'FREQ=MINUTELY;INTERVAL=1',
    enabled         => true);
end;
/

select job_name, enabled, state, next_run_date
  from user_scheduler_jobs where job_name = 'SPEECH_COLLECT_JOB';

-- Anything stuck? This is the query that tells you the collector is missing.
select status, count(*) from INTERACTION group by status order by 1;
