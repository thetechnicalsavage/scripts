-- v1.0 | 01_one_tool.sql | CHANGES STATE
--
-- ONE tool, called on every turn.
--
-- The four-tool version, with a task telling the model which to call when,
-- failed on SEQUENCING rather than on wording: it called identify with no
-- number at all, then called nothing when handed the number, then invented an
-- amount and a due date rather than calling summary. A reasoning model fixed
-- the invented figures and none of the sequencing, at twice the latency.
--
-- "Call this every turn" is a far easier instruction to follow than a four-way
-- branch on what the customer just said. Choosing the next step from what the
-- user said and what the last step returned is a business process, and business
-- processes belong in code you can test.

declare
  l_attr clob;
begin
  select json_object(
           'tool_type'   value 'FUNCTION',
           'tool_inputs' value json_array(
              json_object('name'        value 'message',
                          'description' value 'What the customer just typed, verbatim'
                          returning clob)
              returning clob),
           'function'    value '&&app_schema..BILL_TOOLS.T_ASSIST',
           'instruction' value
             'Call this on every turn, passing the customer message unchanged. '
           ||'It returns the facts and a suggested wording. Reply using only '
           ||'those facts. Never invent an amount, a date or an account detail.'
           returning clob) into l_attr from dual;

  begin dbms_cloud_ai_agent.drop_tool('BILL_ASSIST', force => true);
  exception when others then null; end;

  dbms_cloud_ai_agent.create_tool(tool_name  => 'BILL_ASSIST',
                                  attributes => l_attr);
end;
/

select tool_name from user_ai_agent_tools where tool_name = 'BILL_ASSIST';
