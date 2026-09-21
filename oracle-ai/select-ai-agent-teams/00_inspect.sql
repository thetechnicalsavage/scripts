-- v1.0 | 00_inspect.sql | READ-ONLY
-- What the agent framework has registered, and the one view that matters.

set pagesize 200 linesize 160 feedback off

prompt === tools, tasks, teams ===
select tool_name    from user_ai_agent_tools order by 1;
select task_name    from user_ai_agent_tasks order by 1;
select team_name    from user_ai_agent_teams order by 1;

prompt
prompt === registering a team also creates a profile named AGENT$<TEAM_NAME> ===
prompt     This surprises people. They sit alongside hand-made profiles.
select profile_name from user_cloud_ai_profiles
 where profile_name like 'AGENT$%' order by 1;

prompt
prompt === task history: the view that lets you read what the user REALLY typed ===
prompt     While a tool runs there is a RUNNING row here whose INPUT is the
prompt     message word for word, before the model paraphrased it.
select team_name, state, start_date, substr(input, 1, 60) as input_head
  from user_ai_agent_task_history
 order by start_date desc
 fetch first 20 rows only;

set feedback on
