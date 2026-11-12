-- v1.1 2026-11-11 - published copy: how to run it is in the README (the lab ran it through its own wrapper).
-- v1.0 2026-11-11 - brief 15: what each arm sends for one prompt (SHOWCONTEXT, SHOWPROMPT) and the SQL it writes.
--   Run as SAI_SEM_LAB after 03: any SQL client (README)
select dbms_cloud_ai.generate(prompt => 'List at-risk customers by region', profile_name => 'SALES_SEM', action => 'showcontext') sem_showcontext from dual;
select dbms_cloud_ai.generate(prompt => 'List at-risk customers by region', profile_name => 'SALES_BASE', action => 'showcontext') base_showcontext from dual;
select dbms_cloud_ai.generate(prompt => 'List at-risk customers by region', profile_name => 'SALES_SEM', action => 'showprompt') sem_showprompt from dual;
select dbms_cloud_ai.generate(prompt => 'List at-risk customers by region', profile_name => 'SALES_ANNOT', action => 'showprompt') annot_showprompt from dual;
select dbms_cloud_ai.generate(prompt => 'List at-risk customers by region', profile_name => 'SALES_BASE', action => 'showsql') base_sql from dual;
select dbms_cloud_ai.generate(prompt => 'List at-risk customers by region', profile_name => 'SALES_SEM', action => 'showsql') sem_sql from dual;
select dbms_cloud_ai.generate(prompt => 'List at-risk customers by region', profile_name => 'SALES_ANNOT', action => 'showsql') annot_sql from dual;
