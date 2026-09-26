-- Run as a DBA. Creates the account the ORDS MCP pool connects as.
--
-- This is the whole security boundary of the feature. The MCP run-SQL tool
-- executes arbitrary SELECTs as this user, so "what can the AI reach" is
-- exactly "what did I grant here" - nothing more, and nothing less.
--
-- This one gets the product catalogue and nothing else: no subscribers, no
-- invoices, no personal data. CREATE SESSION and SELECT, no quota, no
-- CREATE TABLE, no PL/SQL execute.
set serveroutput on size unlimited
set verify off
define mcp_pwd = &1

declare
  n number;
begin
  select count(*) into n from dba_users where username = 'MCP_READER';
  if n = 0 then
    execute immediate 'create user MCP_READER identified by "&mcp_pwd." '||
                      'default tablespace USERS quota 0 on USERS';
    dbms_output.put_line('  user MCP_READER created');
  else
    execute immediate 'alter user MCP_READER identified by "&mcp_pwd."';
    dbms_output.put_line('  user MCP_READER already existed, password reset');
  end if;
end;
/

grant create session to MCP_READER;

-- Grant only what the AI is meant to see. Enumerate it explicitly rather than
-- granting a role, so the list is auditable.
begin
  for t in (select table_name from dba_tables where owner = 'TELCO_CAT') loop
    execute immediate 'grant select on TELCO_CAT."'||t.table_name||'" to MCP_READER';
  end loop;
end;
/

-- What the AI can actually reach, as a query you can re-run in a review.
select owner, table_name, privilege
  from dba_tab_privs where grantee = 'MCP_READER' order by owner, table_name;
select privilege from dba_sys_privs where grantee = 'MCP_READER';
exit
