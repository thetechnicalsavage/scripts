-- v1.0 | 02_vpd.sql | CHANGES STATE
--
-- The rows are the guardrail. A generated query is one wrong join predicate
-- away from returning somebody else's data, and no prompt wording fixes that,
-- because the prompt is not what executes.
--
-- THE IMPORTANT PART IS THE NULL. Returning null means "unrestricted", which
-- is what lets you add this to a database that already has other applications
-- on it: every session that does not set the context sees exactly what it saw
-- before.

-- A SECURE application context: only code inside the named package may write it.
create or replace context BILLCHAT_CTX using SPA_APP.PKG_BILLCHAT;

create or replace function by_subscriber (p_schema varchar2, p_object varchar2)
  return varchar2 is
begin
  if sys_context('BILLCHAT_CTX','SUBSCRIBER_ID') is null then
    return null;                      -- unrestricted: this is every other app
  end if;
  return 'subscriber_id = to_number(sys_context(''BILLCHAT_CTX'',''SUBSCRIBER_ID''))';
end by_subscriber;
/

-- CONTEXT_SENSITIVE, not DYNAMIC. The predicate is re-evaluated when the
-- context changes and cached otherwise. DYNAMIC would re-run this function on
-- every parse for every session in the database, including all the ones that
-- will always get null back.
begin
  dbms_rls.add_policy(
    object_schema   => 'TELCO_SUBS',
    object_name     => 'INVOICE',
    policy_name     => 'INVOICE_BY_SUB',
    function_schema => 'SPA_APP',
    policy_function => 'BY_SUBSCRIBER',
    statement_types => 'SELECT',
    policy_type     => dbms_rls.context_sensitive);
end;
/

-- Confirm. Expect one row per policied table, all ENABLE = YES.
select object_name, policy_name, function, enable
  from all_policies
 where object_owner = 'TELCO_SUBS'
 order by object_name;
