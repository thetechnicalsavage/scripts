-- v1.0 | 01_pipeline_pkg.sql | CHANGES STATE
--
-- The whole round trip. Five REST calls, and the one that catches everyone is
-- step four: Speech does NOT return the transcript. It returns a job, and the
-- answer is written back to Object Storage as a separate object.
--
-- Substitute your own region, namespace and bucket. Nothing here stores a
-- credential value; the credential is created separately and referenced by name.

create or replace package PKG_SPEECH_INGEST as
  procedure start_speech   (p_int in number, p_audio in blob);
  procedure collect_speech (p_int in number, p_job   in varchar2);
  procedure tick;                      -- driven by the scheduler, one row a go
end PKG_SPEECH_INGEST;
/

create or replace package body PKG_SPEECH_INGEST as

  c_cred      constant varchar2(60) := 'OCI_GENAI_CRED';
  c_region    constant varchar2(40) := '&&region.';
  c_namespace constant varchar2(60) := '&&namespace.';
  c_bucket    constant varchar2(60) := '&&bucket.';
  c_compart   constant varchar2(200):= '&&compartment.';

  function obj_base return varchar2 is
  begin
    return 'https://objectstorage.'||c_region||'.oraclecloud.com'
        || '/n/'||c_namespace||'/b/'||c_bucket;
  end obj_base;

  function speech_base return varchar2 is
  begin
    return 'https://speech.aiservice.'||c_region||'.oci.oraclecloud.com/20220101';
  end speech_base;

  ---------------------------------------------------------------------------
  -- 1. PUT the audio. The body is the BLOB straight off a file upload item:
  --    no temp file, no middle tier, no local Oracle client.
  -- 2. POST the job.
  ---------------------------------------------------------------------------
  procedure start_speech (p_int in number, p_audio in blob) is
    l_resp   dbms_cloud_types.resp;
    l_code   number;
    l_obj    varchar2(400) := 'in/interaction-'||p_int||'.wav';
    l_body   clob;
    l_full   clob;
    l_txt    varchar2(4000);
    l_job_id varchar2(200);
  begin
    l_resp := dbms_cloud.send_request(
                credential_name => c_cred,
                uri             => obj_base()||'/o/'||utl_url.escape(l_obj),
                method          => 'PUT',
                body            => p_audio);

    l_code := dbms_cloud.get_response_status_code(l_resp);
    if l_code not between 200 and 299 then
      update INTERACTION
         set status = 'FAILED',
             fail_reason = 'Object Storage upload returned HTTP '||l_code
       where interaction_id = p_int;
      commit;
      return;
    end if;

    select json_object(
             'compartmentId' value c_compart,
             'inputLocation' value json_object(
                'locationType'    value 'OBJECT_LIST_INLINE_INPUT_LOCATION',
                'objectLocations' value json_array(
                   json_object('namespaceName' value c_namespace,
                               'bucketName'    value c_bucket,
                               'objectNames'   value json_array(l_obj)
                               returning clob)
                   returning clob)
                returning clob),
             'outputLocation' value json_object(
                'namespaceName' value c_namespace,
                'bucketName'    value c_bucket,
                'prefix'        value 'out'
                returning clob),
             'modelDetails'  value json_object(
                'domain'       value 'GENERIC',
                'languageCode' value '&&language_code.')
             returning clob)
      into l_body from dual;

    l_resp := dbms_cloud.send_request(
                credential_name => c_cred,
                uri             => speech_base()||'/transcriptionJobs',
                method          => 'POST',
                body            => utl_raw.cast_to_raw(l_body));

    l_code := dbms_cloud.get_response_status_code(l_resp);

    -- PARSE THE WHOLE THING, THEN TRUNCATE.
    -- Truncating first and parsing second produced ORA-40441 on every single
    -- job: the create-job reply is well over a thousand characters and the cut
    -- landed mid-object. Truncation belongs on the failure message only.
    l_full := dbms_cloud.get_response_text(l_resp);
    l_txt  := substr(l_full, 1, 1000);

    if l_code between 200 and 299 then
      l_job_id := json_object_t.parse(l_full).get_string('id');
      update INTERACTION
         set stt_job_id = l_job_id, status = 'TRANSCRIBING'
       where interaction_id = p_int;
    else
      update INTERACTION
         set status = 'FAILED',
             fail_reason = substr('Speech job rejected: '||l_txt, 1, 400)
       where interaction_id = p_int;
    end if;
    commit;
  end start_speech;

  ---------------------------------------------------------------------------
  -- 3. Poll. 4. Find the output object. 5. Fetch and parse it.
  ---------------------------------------------------------------------------
  procedure collect_speech (p_int in number, p_job in varchar2) is
    l_resp   dbms_cloud_types.resp;
    l_state  varchar2(40);
    l_list   json_object_t;
    l_items  json_array_t;
    l_name   varchar2(1000);
    l_text   clob;
  begin
    l_resp := dbms_cloud.send_request(
                credential_name => c_cred,
                uri             => speech_base()||'/transcriptionJobs/'||p_job,
                method          => 'GET');

    if dbms_cloud.get_response_status_code(l_resp) not between 200 and 299 then
      return;                                   -- transient: try the next tick
    end if;

    l_state := json_object_t.parse(dbms_cloud.get_response_text(l_resp))
                 .get_string('lifecycleState');

    if l_state in ('ACCEPTED','IN_PROGRESS') then
      return;                                   -- still working
    elsif l_state <> 'SUCCEEDED' then
      update INTERACTION
         set status = 'FAILED', fail_reason = 'Speech job '||l_state
       where interaction_id = p_int;
      commit;
      return;
    end if;

    -- SUCCEEDED, and the transcript is NOT in that response.
    -- LIST the job's output folder rather than reconstructing the filename.
    -- The convention observed was
    --   out/job-<last OCID segment>/<ns>_<bucket>_<object path>.json
    -- with the object path's slashes PRESERVED, which is not what a reasonable
    -- person guesses first. Listing survives OCI changing it again.
    l_resp := dbms_cloud.send_request(
                credential_name => c_cred,
                uri             => obj_base()||'/o?prefix='
                                || utl_url.escape('out/job-'
                                || regexp_substr(p_job,'[^.]+$')||'/'),
                method          => 'GET');

    l_list  := json_object_t.parse(dbms_cloud.get_response_text(l_resp));
    l_items := l_list.get_array('objects');

    if l_items is null or l_items.get_size = 0 then
      update INTERACTION
         set status = 'FAILED',
             fail_reason = 'Speech reported SUCCEEDED but wrote no output object'
       where interaction_id = p_int;
      commit;
      return;
    end if;

    l_name := treat(l_items.get(0) as json_object_t).get_string('name');

    l_resp := dbms_cloud.send_request(
                credential_name => c_cred,
                uri             => obj_base()||'/o/'||utl_url.escape(l_name),
                method          => 'GET');

    l_text := dbms_cloud.get_response_text(l_resp);

    update INTERACTION
       set transcript        = l_text,
           transcript_source = 'OCI_SPEECH',   -- provenance, see the README
           status            = 'TRANSCRIBED'
     where interaction_id = p_int;
    commit;
  end collect_speech;

  ---------------------------------------------------------------------------
  -- One row per tick, under a named lock so two workers cannot drive the same
  -- interaction. An asynchronous pipeline needs its collector written at the
  -- same time as its submitter, or rows sit in TRANSCRIBING for ever with no
  -- error anywhere and nobody notices for a week.
  ---------------------------------------------------------------------------
  procedure tick is
    l_handle varchar2(128);
    l_status pls_integer;
  begin
    dbms_lock.allocate_unique('SPEECH_INGEST_TICK', l_handle);
    l_status := dbms_lock.request(l_handle, dbms_lock.x_mode, 0, true);
    if l_status <> 0 then
      return;                                   -- another worker has it
    end if;

    for r in (select interaction_id, stt_job_id
                from INTERACTION
               where status = 'TRANSCRIBING'
                 and stt_job_id is not null
               order by interaction_id
               fetch first 1 rows only) loop
      collect_speech(r.interaction_id, r.stt_job_id);
    end loop;

    l_status := dbms_lock.release(l_handle);
  end tick;

end PKG_SPEECH_INGEST;
/
