-- v1.0 - brief 09 follow-up probe P13b: can the pipeline poisoned by the Arabic-named file be recovered?
--        RAG_LAB only, probe index RAG_PRB_P1. No LLM calls. Ends with the pipeline STOPPED.
set serveroutput on size unlimited feedback off linesize 200 pagesize 100
whenever sqlerror continue
declare
  c_pipe constant varchar2(128) := 'RAG_PRB_P1$VECPIPELINE';
  l_st   varchar2(128);
  l_n    number;
  procedure counts(tag varchar2) is
  begin
    execute immediate 'select count(*) from "RAG_PRB_P1$VECTAB"' into l_n;
    dbms_output.put_line(rpad(tag, 26) || 'chunks ' || l_n);
    for r in (select json_value(attributes, '$.object_name') obj, count(*) n
                from "RAG_PRB_P1$VECTAB" group by json_value(attributes, '$.object_name') order by 1) loop
      dbms_output.put_line('   ' || rpad(case when regexp_like(r.obj, '^[ -~]+$') then r.obj else '<non-ASCII name>' end, 40) || r.n);
    end loop;
  end;
  procedure status_rows(tag varchar2) is
    c sys_refcursor; f varchar2(4000); s varchar2(64); e varchar2(4000);
  begin
    select status_table into l_st from user_cloud_pipelines where pipeline_name = c_pipe;
    dbms_output.put_line(tag || ' status table rows (file, status, error):');
    if l_st is null then dbms_output.put_line('   none'); return; end if;
    open c for 'select name, status, substr(error_message, 1, 60) from "' || dbms_assert.simple_sql_name(l_st) || '" order by name';
    loop
      fetch c into f, s, e; exit when c%notfound;
      dbms_output.put_line('   ' || rpad(case when regexp_like(f, '^[ -~]+$') then f else '<non-ASCII name>' end, 34) || rpad(s, 11) || nvl(e, '-'));
    end loop;
    close c;
  exception when others then dbms_output.put_line('   (status table not readable: ' || sqlerrm || ')');
  end;
  procedure run_once(tag varchar2) is
  begin
    dbms_cloud_pipeline.run_pipeline_once(pipeline_name => c_pipe);
    dbms_output.put_line(tag || ' RUN_PIPELINE_ONCE: OK');
  exception when others then dbms_output.put_line(tag || ' RUN_PIPELINE_ONCE: ' || substr(sqlerrm, 1, 90));
  end;
begin
  dbms_output.put_line('== P13b  recovering RAG_PRB_P1''s pipeline after the Arabic-named file (file already deleted)');
  counts('before:'); status_rows('before:');
  run_once('1. as it is:');
  begin
    dbms_cloud_pipeline.reset_pipeline(pipeline_name => c_pipe, purge_data => false);
    dbms_output.put_line('2. RESET_PIPELINE(purge_data => false): OK');
  exception when others then dbms_output.put_line('2. RESET_PIPELINE: ' || substr(sqlerrm, 1, 90));
  end;
  status_rows('after reset:');
  run_once('3. after reset:');
  counts('after reset + run:'); status_rows('after reset + run:');
  begin
    dbms_cloud_pipeline.stop_pipeline(pipeline_name => c_pipe, force => true);
  exception when others then null;
  end;
  select status into l_st from user_cloud_pipelines where pipeline_name = c_pipe;
  dbms_output.put_line('pipeline status at the end: ' || l_st);
end;
/
