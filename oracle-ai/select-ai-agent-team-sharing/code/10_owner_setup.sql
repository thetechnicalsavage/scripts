-- v1.3 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.2 2026-11-11 - neutral lab cities (blog privacy rule): owner Lisbon, second account Porto.
-- v1.1 2026-11-11 - Codex r2: the drop of ACCOUNTS ignores only ORA-00942 (first run); the check query uses
--                    USER_AI_AGENT_TEAMS.AGENT_TEAM_NAME.
-- v1.0 2026-11-11 - brief 16: the shared team, built once in the owner schema. One agent, one task, one tool. The tool
--   function is invoker-rights (AUTHID CURRENT_USER) and reads ACCOUNTS in the schema of whoever runs the team, so the
--   same team answers from each consumer's own data. Two runtime placeholders are deliberate: {llm_profile} (the
--   agent's AI profile, an attribute variable) and {report_audience} (in the task instruction, an instruction
--   variable). The owner also has an ACCOUNTS table and a profile, to run its own team.
--   Run as SAI_TEAM_OWNER, re-runnable: any SQL client, with ${OCI_COMPARTMENT_ID} replaced by your compartment OCID (README)

-- 1. the owner's own profile and data (each schema in this lab has the same ACCOUNTS shape, different cities)
begin
  dbms_cloud_ai.drop_profile('OWNER_GROK', force => true);
  dbms_cloud_ai.create_profile(profile_name => 'OWNER_GROK', attributes => '{
    "provider": "oci", "region": "us-phoenix-1", "credential_name": "SAI_GENAI_CRED",
    "oci_compartment_id": "${OCI_COMPARTMENT_ID}", "model": "xai.grok-4.20-non-reasoning", "temperature": 0}');
end;
/
declare
  e_no_table exception;
  pragma exception_init(e_no_table, -942);
begin
  execute immediate 'drop table accounts purge';
exception
  when e_no_table then null;   -- first run: nothing to drop
end;
/
create table accounts (
  account_name  varchar2(30) primary key,
  billing_city  varchar2(30) not null,
  plan          varchar2(20) not null,
  status        varchar2(10) not null
);
insert into accounts values ('blue_harbor', 'Lisbon', 'GOLD', 'ACTIVE');
insert into accounts values ('red_canyon', 'Porto', 'SILVER', 'ACTIVE');
commit;

-- 2. the tool function: reads ACCOUNTS of the CURRENT_USER (the team's caller), never the owner's by accident
create or replace function account_lookup(account_name in clob) return clob
authid current_user is
  l_name   varchar2(30) := lower(trim(dbms_lob.substr(account_name, 30, 1)));
  l_schema varchar2(128) := sys_context('userenv', 'current_user');
  l_city   varchar2(30);
  l_plan   varchar2(20);
  l_status varchar2(10);
begin
  execute immediate 'select billing_city, plan, status from '
                    || dbms_assert.enquote_name(l_schema, false) || '.accounts where account_name = :1'
    into l_city, l_plan, l_status using l_name;
  return 'ACCOUNT=' || l_name || ';BILLING_CITY=' || l_city || ';PLAN=' || l_plan || ';STATUS=' || l_status
         || ';READ_FROM_SCHEMA=' || l_schema;
exception
  when no_data_found then
    return 'ACCOUNT_NOT_FOUND=' || l_name || ';READ_FROM_SCHEMA=' || l_schema;
  when others then
    return 'TOOL_ERROR: ' || replace(sqlerrm, chr(10), ' ');
end account_lookup;
/

-- 3. tool, agent, task, team (dropped first so the script re-runs)
begin
  dbms_cloud_ai_agent.drop_team('ACCOUNT_TEAM', force => true);
  dbms_cloud_ai_agent.drop_agent('ACCOUNT_AGENT', force => true);
  dbms_cloud_ai_agent.drop_task('ACCOUNT_TASK', force => true);
  dbms_cloud_ai_agent.drop_tool('ACCOUNT_LOOKUP_TOOL', force => true);

  dbms_cloud_ai_agent.create_tool(tool_name => 'ACCOUNT_LOOKUP_TOOL', attributes => '{
    "instruction": "Look up one customer account by its account name (for example blue_harbor). Returns the billing city, plan and status.",
    "function": "SAI_TEAM_OWNER.ACCOUNT_LOOKUP",
    "tool_inputs": [{"name": "account_name", "description": "The account name, lower case, for example blue_harbor", "mandatory": "true"}]}');

  dbms_cloud_ai_agent.create_agent(agent_name => 'ACCOUNT_AGENT', attributes => '{
    "profile_name": "{llm_profile}",
    "role": "You are an account desk assistant. You answer only from what ACCOUNT_LOOKUP_TOOL returns.",
    "enable_human_tool": "false"}',
    description => 'Answers account questions from the account lookup tool');

  dbms_cloud_ai_agent.create_task(task_name => 'ACCOUNT_TASK', attributes => '{
    "instruction": "Answer the account question for {report_audience}. Always call ACCOUNT_LOOKUP_TOOL with the account name from the question first, then answer in one sentence using only the tool result. User question: {query}",
    "tools": ["ACCOUNT_LOOKUP_TOOL"]}');

  dbms_cloud_ai_agent.create_team(team_name => 'ACCOUNT_TEAM', attributes => '{
    "agents": [{"name": "ACCOUNT_AGENT", "task": "ACCOUNT_TASK"}],
    "process": "sequential"}',
    description => 'Shared account desk team');
end;
/
select agent_team_name, status from user_ai_agent_teams;
select agent_name, status from user_ai_agents;
select task_name, status from user_ai_agent_tasks;
select tool_name, status from user_ai_agent_tools;
