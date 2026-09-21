-- v1.0 | 02_current_prompt.sql | CHANGES STATE (creates a function)
--
-- The model rewrites its own tool arguments. Given a full sentence it called
-- the tool with the single word 'Hi'. No tool description fixes that.
--
-- The framework, meanwhile, already has the real text: while a tool is running
-- there is a RUNNING row in USER_AI_AGENT_TASK_HISTORY whose INPUT is the
-- user's message word for word. Read that instead of trusting the argument.
--
-- THE COUNT CHECK IS THE SAFETY RULE, not decoration. Two chats running at the
-- same instant both have a RUNNING row and nothing in the view separates them,
-- so ambiguity falls back to the model's argument rather than risk answering
-- one customer's message against another's session. The time window stops a
-- crashed run leaving a row that blocks this for good.

create or replace function current_prompt (p_team in varchar2)
  return varchar2 is
  l varchar2(4000);
  n pls_integer;
begin
  select count(*) into n
    from user_ai_agent_task_history
   where team_name  = p_team
     and state      = 'RUNNING'
     and start_date > systimestamp - interval '2' minute;

  if n <> 1 then
    return null;                       -- ambiguous: let the caller fall back
  end if;

  select substr(input, 1, 4000) into l
    from user_ai_agent_task_history
   where team_name  = p_team
     and state      = 'RUNNING'
     and start_date > systimestamp - interval '2' minute;

  return l;
exception
  when no_data_found then return null;
  when too_many_rows then return null;
end current_prompt;
/
