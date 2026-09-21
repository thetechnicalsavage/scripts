-- v1.0 | 03_task_and_team.sql | CHANGES STATE
-- The task and the team. With routing moved into PL/SQL the task has one job
-- left, which is wording the answer, and the fast non-reasoning model is enough
-- for that. That is where the latency saving came from.

declare
  l_task clob;
  l_team clob;
begin
  select json_object(
           'instruction' value
             'You are a billing assistant. On every turn call BILL_ASSIST with '
           ||'the customer message unchanged, then reply in plain language using '
           ||'only the facts it returned. Never invent an amount, a date or an '
           ||'account detail. If it returns nothing, say you could not find it.',
           'tools' value json_array('BILL_ASSIST' returning clob)
           returning clob) into l_task from dual;

  begin dbms_cloud_ai_agent.drop_task('BILL_CHAT_TASK', force => true);
  exception when others then null; end;
  dbms_cloud_ai_agent.create_task(task_name  => 'BILL_CHAT_TASK',
                                  attributes => l_task);

  select json_object(
           'agents' value json_array(
              json_object('name' value 'BILL_AGENT',
                          'task' value 'BILL_CHAT_TASK'
                          returning clob)
              returning clob),
           'profile_name' value '&&profile_name.'
           returning clob) into l_team from dual;

  begin dbms_cloud_ai_agent.drop_team('BILL_CHAT_TEAM', force => true);
  exception when others then null; end;
  dbms_cloud_ai_agent.create_team(team_name  => 'BILL_CHAT_TEAM',
                                  attributes => l_team);
end;
/

-- Registering the team also creates a profile named AGENT$BILL_CHAT_TEAM.
select profile_name from user_cloud_ai_profiles
 where profile_name = 'AGENT$BILL_CHAT_TEAM';
