-- v1.1 - Brief 09 (Select AI RAG tuning): probe P5, the database's own text extraction of the
--        probe files (PLAN.md 8.1): DBMS_VECTOR_CHAIN.UTL_TO_TEXT on each file in RAG_PROBE_DIR,
--        and, once p1 exists, the text the index itself stored per file.
--        v1.1: public copy: the tools/capture.py usage line removed (lab-internal, not shipped).
--        v1.0: first version.
--
-- Run as : RAG_LAB, connected to the PDB, after the probe files are staged (probe_files.txt).
-- Usage  : @05_extraction_gate.sql
--          (the lab ran it through tools/capture.py, a lab-internal transcript wrapper not shipped here)
--          SQL*Plus must run with NLS_LANG=AMERICAN_AMERICA.AL32UTF8 (checked).
-- Re-run : safe; read-only. Files are read through BFILENAME; nothing is created or changed.
-- Then   : python3 04_probes.py p5 scores word recall against the corpus sources with the SAME
--          normaliser as tools/build_corpus.py, counts reversed numbers, and prints the
--          PDF-or-DOCX decision. This script prints the counts that need no source text.
--
-- What is counted in the extracted text, per file:
--   '?'      a question mark (the source documents hold none; Oracle Text shows glyphs it
--            cannot map, typical of embedded fonts, as '?')      [D5]
--   U+FFFD   replacement character      U+00BF  inverted question mark
--   U+0640   tatweel (kashida): justification or rendering artefact, breaks Arabic tokens
--   AR       Arabic letters U+0621-U+064A      PF  Arabic presentation forms (U+FB50-U+FDFF,
--            U+FE70-U+FEFF): glyph-level extraction, the tokenizer sees other code points
-- In the text samples '?' is printed as <?>, U+FFFD as <U+FFFD> and U+00BF as <U+00BF>, so the
-- transcript gate (which refuses lost characters) can tell a finding from a broken NLS_LANG.

set verify off echo off feedback off termout on heading off pagesize 0
set linesize 240 trimout on trimspool on
set serveroutput on size unlimited format wrapped
set sqlblanklines on
whenever sqlerror exit failure
whenever oserror exit failure

declare
  c_dir constant varchar2(30) := 'RAG_PROBE_DIR';
  c_idx constant varchar2(30) := 'RAG_PRB_P1';
  type t_list is table of varchar2(128);
  -- the stage=initial rows of probe_files.txt (tests/test_probes.py keeps the two in step)
  l_files t_list := t_list('PRB-AR-AMIRI-GLF-001-AR.pdf',
                           'PRB-AR-DOCX-GLF-001-AR.docx',
                           'PRB-EN-CHROMIUM-GLF-001-EN.pdf',
                           'PRB-EN-REPORTLAB-HRP-001.pdf',
                           'PRB-AR-NOTO-GLF-001-AR.pdf');
  l_ar_class varchar2(16) := '[' || unistr('\0621') || '-' || unistr('\064A') || ']';
  l_pf_class varchar2(32) := '[' || unistr('\FB50') || '-' || unistr('\FDFF') || unistr('\FE70') || '-'
                             || unistr('\FEFF') || ']';
  l_bf    bfile;
  l_blob  blob;
  l_txt   clob;
  l_dst   integer;
  l_src   integer;
  l_bytes number;
  l_err   varchar2(4000);
  l_n     number;

  procedure p(s varchar2) is
  begin
    dbms_output.put_line(s);
  end;

  function red(s varchar2) return varchar2 is
    r varchar2(32767) := s;
  begin
    r := regexp_replace(r, 'ocid1\.[A-Za-z0-9._-]+', '<ocid>');
    r := regexp_replace(r, '(https?|file)://[^ "'']*', '<url>');
    r := regexp_replace(r, '[a-z]+-[a-z]+-[0-9]+', '<region>');
    r := regexp_replace(r, '(^|[^0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9.]|$)', '\1<ip>\3');
    r := regexp_replace(r, '/home/[A-Za-z0-9._-]+', '/home/<user>');
    r := regexp_replace(r, '/(opt|u01|u02|var|tmp|mnt)/[^ ]*', '<path>');
    return r;
  end;

  -- a sample that a transcript gate can carry: whitespace folded, lost-character marks spelled out
  function sample(t clob, n pls_integer) return varchar2 is
    s varchar2(32767);
  begin
    s := regexp_replace(dbms_lob.substr(t, n, 1), '[[:space:]]+', ' ');
    s := replace(s, '?', '<?>');
    s := replace(s, unistr('\FFFD'), '<U+FFFD>');
    s := replace(s, unistr('\00BF'), '<U+00BF>');
    return s;
  end;

  procedure counts(label varchar2, t clob) is
  begin
    p('   ' || rpad(label, 13)
      || 'chars ' || lpad(nvl(dbms_lob.getlength(t), 0), 7)
      || '   ? ' || lpad(regexp_count(t, '\?'), 5)
      || '   U+FFFD ' || lpad(regexp_count(t, unistr('\FFFD')), 4)
      || '   U+00BF ' || lpad(regexp_count(t, unistr('\00BF')), 4)
      || '   U+0640 ' || lpad(regexp_count(t, unistr('\0640')), 4)
      || '   AR ' || lpad(regexp_count(t, l_ar_class), 6)
      || '   PF ' || lpad(regexp_count(t, l_pf_class), 4)
      || '   Latin ' || lpad(regexp_count(t, '[A-Za-z]'), 6));
  end;
begin
  if sys_context('userenv', 'session_user') <> 'RAG_LAB' then
    raise_application_error(-20001, 'run as RAG_LAB');
  end if;
  if sys_context('userenv', 'con_name') = 'CDB$ROOT' then
    raise_application_error(-20002, 'connect to the PDB, not CDB$ROOT');
  end if;
  if unistr('\0627\0644\0639\0631\0628\064A\0629') <> 'العربية' then
    raise_application_error(-20003,
      'the SQL*Plus client is not UTF-8: set NLS_LANG=AMERICAN_AMERICA.AL32UTF8 (Arabic would be lost)');
  end if;

  p('== P5  the database''s own extraction: DBMS_VECTOR_CHAIN.UTL_TO_TEXT on each probe file');
  p('   (in samples: <?> = U+003F, <U+FFFD>, <U+00BF> = the characters themselves)');
  for i in 1 .. l_files.count loop
    p('');
    p('-- ' || l_files(i));
    l_bf := bfilename(c_dir, l_files(i));
    if dbms_lob.fileexists(l_bf) = 0 then
      p('   not in ' || c_dir || ' - skipped' || case when l_files(i) like 'PRB-AR-NOTO-%'
                                                   then ' (the negative control is optional)' end);
      continue;
    end if;
    l_err := null;
    l_txt := null;
    l_bytes := null;
    begin
      dbms_lob.fileopen(l_bf, dbms_lob.file_readonly);
      l_bytes := dbms_lob.getlength(l_bf);
      dbms_lob.createtemporary(l_blob, true);
      l_dst := 1;
      l_src := 1;
      dbms_lob.loadblobfromfile(l_blob, l_bf, dbms_lob.lobmaxsize, l_dst, l_src);
      dbms_lob.fileclose(l_bf);
      -- dynamic, so this file compiles even where DBMS_VECTOR_CHAIN is not granted
      execute immediate 'begin :t := dbms_vector_chain.utl_to_text(:b); end;' using out l_txt, in l_blob;
      dbms_lob.freetemporary(l_blob);
    exception
      when others then
        l_err := red(sqlerrm);
        if dbms_lob.fileisopen(l_bf) = 1 then
          dbms_lob.fileclose(l_bf);
        end if;
        if l_blob is not null and dbms_lob.istemporary(l_blob) = 1 then
          dbms_lob.freetemporary(l_blob);
        end if;
    end;
    p('   file bytes   ' || nvl(to_char(l_bytes), 'unknown'));
    if l_err is not null then
      p('!! UTL_TO_TEXT failed: ' || l_err);
      continue;
    end if;
    counts('UTL_TO_TEXT', l_txt);
    p('   first 300 characters:');
    p('   ' || sample(l_txt, 300));
  end loop;

  -- the text the index stored for the same files, once P6 has built p1
  select count(*) into l_n from user_tables where table_name = c_idx || '$VECTAB';
  p('');
  if l_n = 0 then
    p('-- ' || c_idx || ' not built yet: re-run this script after P6 to see what the index stored');
  else
    p('== P5 + p1  what ' || c_idx || ' stored per file (chunks overlap, so counts include repeats)');
    for i in 1 .. l_files.count loop
      execute immediate 'select count(*) from "' || dbms_assert.simple_sql_name(c_idx || '$VECTAB')
                     || '" where json_value(attributes, ''$.object_name'' returning varchar2(1024)) = :1'
        into l_n using l_files(i);
      p('');
      p('-- ' || l_files(i) || ': ' || l_n || ' chunks');
      if l_n > 0 then
        dbms_lob.createtemporary(l_txt, true);
        declare
          rc sys_refcursor;
          c  clob;
        begin
          open rc for 'select content from "' || dbms_assert.simple_sql_name(c_idx || '$VECTAB')
                   || '" where json_value(attributes, ''$.object_name'' returning varchar2(1024)) = :1'
                   || ' order by rowid' using l_files(i);
          loop
            fetch rc into c;
            exit when rc%notfound;
            if c is not null and dbms_lob.getlength(c) > 0 then
              dbms_lob.append(l_txt, c);
              dbms_lob.writeappend(l_txt, 1, chr(10));
            end if;
          end loop;
          close rc;
        end;
        counts('in the index', l_txt);
        p('   stored text (chunks in rowid order), first 300 characters:');
        p('   ' || sample(l_txt, 300));
        dbms_lob.freetemporary(l_txt);
      end if;
    end loop;
  end if;
  p('');
  p('Next: python3 04_probes.py p5  (word recall against the source, reversed numbers, the PDF-or-DOCX decision)');
end;
/
