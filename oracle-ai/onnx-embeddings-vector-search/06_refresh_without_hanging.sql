-- v1.0 | 06_refresh_without_hanging.sql | CHANGES STATE
--
-- RUN_PIPELINE_ONCE requires the pipeline STOPPED, or you get ORA-20044, and
-- it then runs in the FOREGROUND for roughly 50 seconds. Called from a web page
-- process that is a frozen browser.
--
-- Submit it as a one-off job and return immediately. Let the page poll for
-- state rather than wait on the call.

declare
  l_pipe varchar2(128) := '&1';
  l_job  varchar2(128) := 'VEC_REFRESH_'||to_char(systimestamp, 'YYYYMMDDHH24MISSFF3');
begin
  dbms_scheduler.create_job(
    job_name   => l_job,
    job_type   => 'PLSQL_BLOCK',
    job_action => 'begin '
               || '  dbms_cloud_pipeline.stop_pipeline('''||l_pipe||''', force => true); '
               || '  dbms_cloud_pipeline.run_pipeline_once('''||l_pipe||'''); '
               || '  dbms_cloud_pipeline.start_pipeline('''||l_pipe||'''); '
               || 'end;',
    enabled    => true,
    auto_drop  => true);
  dbms_output.put_line('submitted '||l_job||'; poll for state, do not wait here');
end;
/
