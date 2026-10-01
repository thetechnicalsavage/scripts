-- v1.2 - RAG tuning lab (brief 09): the three lab directory objects.
--        v1.2: public copy: the other app's references renamed to the existing HR app (text only).
--        v1.1: P6 finding (GO-1, 29-Sep): CREATE_VECTOR_INDEX on a directory location needs WRITE as well
--              as READ; with READ only it fails "ORA-20000: Directory object RAG_PROBE_DIR does not exist"
--              (the app directories carry READ and WRITE for ASKORACLE). RAG_LAB gets READ and WRITE on
--              its two document directories; ASKORACLE keeps READ only on the model directory.
--        v1.0: first version, PLAN.md v1.2 sections 0.7 and 2.3.
--
-- Run as : SYSDBA (or a DBA account), connected to the PDB, after 00_admin_prereqs.sql.
-- Usage  : @00b_directories.sql
-- Re-run : safe. A directory that already points at its lab path is left alone; one that
--          points anywhere else stops the script (it is never repointed). Grants are re-applied.
--
--   RAG_CORPUS_DIR  /opt/oracle/kb/rag_lab/corpus   READ, WRITE to RAG_LAB   (document files only)
--   RAG_PROBE_DIR   /opt/oracle/kb/rag_lab/probe    READ, WRITE to RAG_LAB   (probe gate files)
--   RAG_MODEL_DIR   /opt/oracle/kb/rag_lab/models   READ to ASKORACLE (ONNX files it loads)
--
-- The existing HR app's corpus directory must never be written or repointed. Its path is read from the
-- dictionary (read-only), and any lab path equal to it, under it or containing it is
-- refused, in both directions. The two paths it has had are refused as well even when the
-- dictionary says something else. The file system itself is prepared by 01_stage_files.sh.

set verify off echo off feedback off termout on
set serveroutput on size unlimited format wrapped
whenever sqlerror exit failure
whenever oserror exit failure

declare
  c_root   constant varchar2(64) := '/opt/oracle/kb/rag_lab';
  l_names  sys.odcivarchar2list := sys.odcivarchar2list('RAG_CORPUS_DIR', 'RAG_PROBE_DIR', 'RAG_MODEL_DIR');
  l_paths  sys.odcivarchar2list := sys.odcivarchar2list(c_root || '/corpus', c_root || '/probe', c_root || '/models');
  -- paths the existing HR app's corpus directory is known to have used; the live one is added below
  l_hr     sys.odcivarchar2list := sys.odcivarchar2list('/opt/oracle/kb/hr', '/opt/oracle/oradata/kb/hr');
  l_live   varchar2(4000);
  l_cur    varchar2(4000);
  l_n      pls_integer;

  function norm(p varchar2) return varchar2 is
  begin
    return rtrim(trim(p), '/');
  end;

  -- true when a and b are the same path or one lies under the other (no LIKE: '_' is a wildcard)
  function clash(a varchar2, b varchar2) return boolean is
    x varchar2(4000) := norm(a);
    y varchar2(4000) := norm(b);
  begin
    return x = y
        or substr(x, 1, length(y) + 1) = y || '/'
        or substr(y, 1, length(x) + 1) = x || '/';
  end;
begin
  if sys_context('userenv', 'con_name') = 'CDB$ROOT' then
    raise_application_error(-20001, 'connect to the PDB, not CDB$ROOT');
  end if;
  for u in (select column_value username from table(sys.odcivarchar2list('RAG_LAB', 'ASKORACLE'))) loop
    select count(*) into l_n from dba_users where username = u.username;
    if l_n = 0 then
      raise_application_error(-20002, 'user ' || u.username || ' does not exist (run 00_admin_prereqs.sql first)');
    end if;
  end loop;

  -- read-only look-up of the existing HR app's corpus directory
  begin
    select directory_path into l_live from dba_directories where directory_name = 'HR_KB_DIR';
    l_hr.extend;
    l_hr(l_hr.count) := norm(l_live);
    dbms_output.put_line('existing HR app corpus directory (read-only check): ' || norm(l_live));
  exception
    when no_data_found then
      dbms_output.put_line('existing HR app corpus directory not found; the known paths are still refused');
  end;

  for i in 1 .. l_names.count loop
    -- the lab path itself: exactly one level under the lab root, plain characters only
    if not regexp_like(l_paths(i), '^/opt/oracle/kb/rag_lab/(corpus|probe|models)$') then
      raise_application_error(-20003, 'lab path ' || l_paths(i) || ' is not under ' || c_root);
    end if;
    for h in 1 .. l_hr.count loop
      if clash(l_paths(i), l_hr(h)) or clash(c_root, l_hr(h)) then
        raise_application_error(-20004, 'refusing ' || l_paths(i) || ': it overlaps the existing HR app path ' || l_hr(h));
      end if;
    end loop;

    -- nobody else's directory object may point into the lab tree
    for d in (select directory_name, directory_path from dba_directories
               where directory_name not in ('RAG_CORPUS_DIR', 'RAG_PROBE_DIR', 'RAG_MODEL_DIR')) loop
      if clash(d.directory_path, l_paths(i))
         and substr(norm(d.directory_path), 1, length(c_root)) = c_root then
        raise_application_error(-20005, 'directory ' || d.directory_name ||
          ' already points into the lab tree (' || d.directory_path || '); refusing to share it');
      end if;
    end loop;

    -- refuse-to-repoint, as in deploy_hr_kb.sql section 1
    begin
      select directory_path into l_cur from dba_directories where directory_name = l_names(i);
    exception
      when no_data_found then l_cur := null;
    end;
    if l_cur is null then
      execute immediate 'create directory ' || dbms_assert.simple_sql_name(l_names(i)) ||
                        ' as ' || dbms_assert.enquote_literal(l_paths(i));
      dbms_output.put_line(rpad(l_names(i), 16) || ' created -> ' || l_paths(i));
    elsif norm(l_cur) = l_paths(i) then
      dbms_output.put_line(rpad(l_names(i), 16) || ' already correct -> ' || l_cur);
    else
      raise_application_error(-20006, l_names(i) || ' already exists and points at ' || l_cur ||
        '. Refusing to repoint it; another object may depend on that path.');
    end if;
  end loop;

  -- READ and WRITE: P6 (29-Sep) showed a file-backed Select AI vector index fails with READ only
  -- ("Directory object ... does not exist"). Both directories are the lab's own, under the lab path.
  execute immediate 'grant read, write on directory ' || dbms_assert.simple_sql_name('RAG_CORPUS_DIR') || ' to RAG_LAB';
  execute immediate 'grant read, write on directory ' || dbms_assert.simple_sql_name('RAG_PROBE_DIR')  || ' to RAG_LAB';
  execute immediate 'grant read on directory ' || dbms_assert.simple_sql_name('RAG_MODEL_DIR')  || ' to ASKORACLE';
end;
/

prompt
prompt Lab directories and who may use them:
set linesize 160 pagesize 100 heading on
column directory_name format a16
column directory_path format a34
column grantee        format a12
column privilege      format a10
select d.directory_name, d.directory_path, p.grantee, p.privilege
  from dba_directories d
  left join dba_tab_privs p on p.owner = 'SYS' and p.table_name = d.directory_name and p.type = 'DIRECTORY'
 where d.directory_name in ('RAG_CORPUS_DIR', 'RAG_PROBE_DIR', 'RAG_MODEL_DIR')
 order by 1, 3, 4;
