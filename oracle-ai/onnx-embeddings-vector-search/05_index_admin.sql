-- v1.0 | 05_index_admin.sql | READ-ONLY helpers
--
-- Pattern A admin. Two things here are load-bearing.
--
-- 1. WHITELIST THE INDEX NAME. The vector table is named <INDEX_NAME>$VECTAB,
--    so the index name reaches dynamic SQL. Resolving it against the
--    dictionary first is the whole injection defence, and it is three lines.
--
-- 2. A package-private function cannot be called from inside a SQL statement
--    (PLS-00231), so attribute lookups are resolved into locals first.

create or replace function safe_index_name (p_index in varchar2)
  return varchar2 is
  l varchar2(128);
begin
  select index_name into l
    from user_cloud_vector_indexes
   where index_name = upper(trim(p_index));
  return l;
exception when no_data_found then
  raise_application_error(-20310,
    'Unknown vector index "'||substr(p_index, 1, 60)||'". Choose one from the list.');
end safe_index_name;
/

-- Chunks per source document. The source filename is in the ATTRIBUTES JSON,
-- which is how a file list gets its "indexed / not indexed" column.
-- Substitute your index name; it is whitelisted above before it gets here.
select json_value(attributes, '$.object_name') as source_file,
       count(*)                                as chunks
  from "DBA_KB_IDX$VECTAB"
 group by json_value(attributes, '$.object_name')
 order by chunks desc;
