-- v1.1 - Recreate a Select AI profile in place, with exactly its current attributes, so
--        SELECT AI stops returning translations cached before a metadata change.
--        v1.1: profile name validated with DBMS_ASSERT.
--
-- Run as : the profile owner.
-- Usage  : @recreate_profile.sql <PROFILE_NAME>
-- Re-run : safe; each run drops and recreates the same profile.
--
-- Why this exists (tested on 26ai 23.26.1): SELECT AI <action> <prompt> is served through a
-- SQL translation profile named AI$<PROFILE>. The translation of each distinct statement
-- text is cached (visible in V$MAPPED_SQL) and survives SET_ATTRIBUTE, DISABLE_PROFILE /
-- ENABLE_PROFILE and a purge of the cursors. Recreating the profile creates a new
-- translation profile, and the next SELECT AI goes back to the model.
-- DBMS_CLOUD_AI.GENERATE is NOT affected - it calls the model every time.
-- Caution: anything tied to the profile's lifetime (e.g. its feedback index) may go with it.

set serveroutput on feedback off verify off
whenever sqlerror exit failure
define prof = &1

begin
  if dbms_assert.simple_sql_name('&prof') is null then null; end if;   -- raises ORA-44003 if not a name
end;
/

declare
  l_attrs  json_object_t := json_object_t();
  l_desc   clob;
  l_status varchar2(30);
  l_old    number;
  l_new    number;
begin
  select description, lower(status) into l_desc, l_status
    from user_cloud_ai_profiles where profile_name = upper('&prof');
  select max(object_id) into l_old from user_objects where object_name = 'AI$' || upper('&prof');

  for a in (select attribute_name n, attribute_value v
              from user_cloud_ai_profile_attributes
             where profile_name = upper('&prof') and attribute_value is not null) loop
    if regexp_like(a.v, '^\s*[\[{]') then
      l_attrs.put(a.n, json_element_t.parse(a.v));          -- object_list and other JSON
    elsif lower(a.v) in ('true', 'false') then
      l_attrs.put(a.n, lower(a.v) = 'true');
    elsif regexp_like(a.v, '^-?[0-9]+(\.[0-9]+)?$') then
      l_attrs.put(a.n, to_number(a.v));
    else
      l_attrs.put(a.n, a.v);
    end if;
  end loop;

  dbms_cloud_ai.drop_profile(profile_name => upper('&prof'), force => true);
  dbms_cloud_ai.create_profile(profile_name => upper('&prof'), attributes => l_attrs.to_clob,
                               status => l_status, description => l_desc);
  select max(object_id) into l_new from user_objects where object_name = 'AI$' || upper('&prof');
  dbms_output.put_line('recreated ' || upper('&prof') || ' with ' || l_attrs.get_size ||
                       ' attributes; translation profile ' || l_old || ' -> ' || l_new);
end;
/
