-- v1.2 - Purge the SELECT AI cursors of one schema. KEPT AS EVIDENCE: this does NOT stop
--        SELECT AI from returning a cached translation (tested); use recreate_profile.sql.
--        v1.2: header corrected after testing - the purge empties the cursors, not the cache.
--        v1.1: schema name validated with DBMS_ASSERT.
--
-- Run as : SYSDBA (needs V$MAPPED_SQL, V$SQLAREA and DBMS_SHARED_POOL), in the PDB.
-- Usage  : @purge_select_ai_cache.sql <SCHEMA>
-- Re-run : safe; purges whatever matching cursors exist, reports 0 when there are none.
--
-- What we found: SELECT AI <action> <prompt> is handled by the SQL Translation Framework and
-- each translation is listed in V$MAPPED_SQL. After this script purged every matching
-- cursor (V$SQLAREA empty for them), V$MAPPED_SQL still held the translations and the same
-- text still returned the old SQL. The cache belongs to the translation profile AI$<profile>;
-- recreating the Select AI profile (tools/recreate_profile.sql) is what cleared it.

set serveroutput on feedback off verify off
whenever sqlerror exit failure
define owner = &1

begin
  if dbms_assert.simple_sql_name('&owner') is null then null; end if;  -- raises ORA-44003 if not a name
end;
/

declare
  l_n pls_integer := 0;
begin
  for c in (select distinct a.address, a.hash_value, a.sql_id
              from v$mapped_sql m
              join v$sqlarea a on a.sql_id in (m.sql_id, m.mapped_sql_id)
             where lower(m.sql_text) like 'select ai %'
               and a.parsing_schema_name = upper('&owner')) loop
    sys.dbms_shared_pool.purge(c.address || ',' || c.hash_value, 'C');
    l_n := l_n + 1;
  end loop;
  dbms_output.put_line('purged ' || l_n || ' SELECT AI cursor(s) parsed by ' || upper('&owner'));
end;
/
select count(*) as select_ai_mappings_left
  from v$mapped_sql m
 where lower(m.sql_text) like 'select ai %'
   and exists (select 1 from v$sqlarea a
                where a.sql_id = m.sql_id and a.parsing_schema_name = upper('&owner'));
