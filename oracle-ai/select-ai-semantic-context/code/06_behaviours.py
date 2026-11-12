#!/usr/bin/env python3
# v1.5 2026-11-11 - published copy: creates transcripts/ as well as results/ (a fresh checkout has neither).
# v1.4 2026-11-11 - published copy: imports lab_conn.py from this folder instead of the lab host's own helper; no default path. SAILAB_SECRETS is required.
# v1.3 2026-11-11 - Codex r3: writing a record can no longer stop the run (log writes are guarded); every clean-up
#                    action is attempted on its own and only success or 'not granted' (ORA-01927) is accepted; the
#                    verification covers every target (test sources incl. SALES_ONTOLOGY, test profiles, SAI_SEM_USER's
#                    USER_SEM, the grant); connections close in a separate finally.
# v1.2 2026-11-11 - Codex r2: every step declares its expected outcome (OK, or the ORA code it must raise, and text the
#                    answer must contain) and the run exits 1 if any step did not match; binds are recorded (the
#                    compartment OCID masked); a run id whose results file exists is refused; clean-up runs in finally,
#                    one action per step. Steps fixed: "enable restores" is now checked (B09); the drop under a disabled
#                    profile uses its own source (B13-B15); GET_DEFINITION's output is replayed (B16-B18); the
#                    revoke step is checked on the ALL_ view (the grantee's profile can never be created, B22-B26).
# v1.1 2026-11-11 - B1 showed a profile cannot be created on an ungranted cross-schema source.
# v1.0 2026-11-11 - brief 15: semantic-source lifecycle and access, step by step across two logins (SAI_SEM_LAB owns
#   the sources; SAI_SEM_USER is another profile owner). Test sources GLOSSARY_COPY*, never the evaluation's
#   SALES_GLOSSARY (which is only read, granted and revoked). Logins: env files in $SAILAB_SECRETS.
# Usage: 06_behaviours.py --run B3
import argparse
import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # lab_conn.py
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
SECRETS = os.path.expanduser(os.environ.get('SAILAB_SECRETS', ''))   # folder of the sailab_*.env login files
RUN_RE = re.compile(r'^[A-Za-z0-9_-]{1,30}$')
PROMPT = 'How many at-risk customers are there in each region?'
log = logging.getLogger('behaviours')


class Lab:
    def __init__(self, run):
        import oracledb
        from lab_conn import connect, read_env
        if not SECRETS:
            raise SystemExit('set SAILAB_SECRETS to the folder holding the sailab_*.env login files')
        self.oracledb = oracledb
        self.comp = read_env(os.path.join(SECRETS, 'sailab_oci.env'))['OCI_COMPARTMENT_ID']
        self.conns = {}
        for user in ('SAI_SEM_LAB', 'SAI_SEM_USER'):
            try:
                self.conns[user], _ = connect(read_env(os.path.join(SECRETS, f'sailab_{user.lower()}.env')))
            except oracledb.Error as e:
                raise SystemExit(f'cannot connect as {user}: {str(e).splitlines()[0]}') from e
            self.conns[user].call_timeout = 180000
        self.run = run
        self.failed = []
        self.jl = open(os.path.join(BASE, 'results', f'{run}.jsonl'), 'x', encoding='utf-8')
        self.tx = open(os.path.join(BASE, 'transcripts', f'{run}.txt'), 'w', encoding='utf-8')

    def write(self, row, text):
        """Record a step; a failing write is logged and counted, never allowed to stop the run."""
        try:
            self.jl.write(json.dumps(row) + '\n')
            self.jl.flush()
            self.tx.write(text)
            self.tx.flush()
        except Exception as e:  # disk full, closed file: keep going, fail the run at the end
            log.error('could not record %s: %s', row.get('step'), e)
            self.failed.append(row.get('step', '?') + ' (not recorded)')

    def mask(self, x):
        return x.replace(self.comp, '<compartment OCID>') if isinstance(x, str) else x

    def step(self, name, actor, stmt, binds=None, clob_out=False, query=False, expect='OK', contains=None):
        cur = self.conns[actor].cursor()
        b = dict(binds or {})
        t0 = time.monotonic()
        try:
            if query:
                cur.execute(stmt, b)
                rows = cur.fetchall()
                text = '\n'.join(' | '.join('' if v is None else str(v.read() if hasattr(v, 'read') else v) for v in r)
                                 for r in rows) or 'no rows'
            elif clob_out:
                res = cur.var(self.oracledb.DB_TYPE_CLOB)
                b['res'] = res
                cur.execute(stmt, b)
                v = res.getvalue()
                text = v.read() if hasattr(v, 'read') else (v or '')
            else:
                cur.execute(stmt, b)
                text = 'done'
            outcome = 'OK'
        except self.oracledb.Error as e:
            text, outcome = str(e).split('\nHelp:')[0], 'ERROR'
        ms = int((time.monotonic() - t0) * 1000)
        if expect == 'OK':
            ok = outcome == 'OK' and (contains is None or contains in text)
        else:
            ok = outcome == 'ERROR' and expect in text
        if not ok:
            self.failed.append(name)
        shown = self.mask(' '.join(stmt.split()))
        row = {'run': self.run, 'step': name, 'actor': actor, 'call': shown,
               'binds': {k: self.mask(v) for k, v in (binds or {}).items()}, 'outcome': outcome,
               'text': self.mask(text)[:3000], 'elapsed_ms': ms, 'expected': expect, 'must_contain': contains,
               'as_expected': ok}
        self.write(row, f'--- {name}  as {actor}\n{shown}\n' + (f'binds: {json.dumps(row["binds"])}\n' if binds else '')
                   + f'=> {outcome} ({ms} ms); expected {expect}' + (f' containing "{contains}"' if contains else '')
                   + f': {"as expected" if ok else "NOT AS EXPECTED"}\n{self.mask(text).strip()[:3000]}\n\n')
        log.info('%s: %s %s %s', name, outcome, 'ok' if ok else 'UNEXPECTED', self.mask(text).replace('\n', ' ')[:100])
        return row


def prof(comp, extra):
    return ('{"provider": "oci", "region": "us-phoenix-1", "credential_name": "SAI_GENAI_CRED", '
            '"oci_compartment_id": "' + comp + '", "model": "xai.grok-4.20-non-reasoning", "temperature": 0, '
            '"object_list": [{"owner": "SAI_SEM_LAB", "name": "CUSTOMERS"}], ' + extra + '}')


FILE_SRC = ('{"scope":"global","source_endpoint":"SAI_SEM_DIR:sales_glossary.txt","profile_name":"SEM_EMBED"}')
SHOWCTX = "begin :res := dbms_cloud_ai.generate(prompt => :p, profile_name => :pr, action => 'showcontext'); end;"
SHOWSQL = "begin :res := dbms_cloud_ai.generate(prompt => :p, profile_name => :pr, action => 'showsql'); end;"
MKSRC = """begin dbms_ai_sem.drop_semantic_source(:s, force => true);
  dbms_ai_sem.create_semantic_source(source_name => :s, type => 'file', attributes => :a); end;"""
MKPROF = "begin dbms_cloud_ai.drop_profile(:n, force => true); dbms_cloud_ai.create_profile(:n, :a); end;"


def steps(lab):
    L, U, c = 'SAI_SEM_LAB', 'SAI_SEM_USER', lab.comp
    lab.step('B01 GET_DEFINITION of the glossary source', L,
             "begin :res := dbms_ai_sem.get_definition('SALES_GLOSSARY'); end;", clob_out=True,
             contains='DBMS_AI_SEM.CREATE_SEMANTIC_SOURCE')
    lab.step('B02 the sources in the dictionary', L,
             "select source_name, source_type, status from user_ai_semantic_sources order by 1", query=True,
             contains='SALES_GLOSSARY')
    # disable and enable
    lab.step('B03 create test source GLOSSARY_COPY (same file)', L, MKSRC, {'s': 'GLOSSARY_COPY', 'a': FILE_SRC})
    lab.step('B04 profile SEM_COPY uses it', L, MKPROF, {'n': 'SEM_COPY', 'a': prof(c, '"semantic_source": ["GLOSSARY_COPY"]')})
    lab.step('B05 SHOWCONTEXT through SEM_COPY', L, SHOWCTX, {'p': PROMPT, 'pr': 'SEM_COPY'}, clob_out=True,
             contains='Domain Knowledge')
    lab.step('B06 disable the source', L, "begin dbms_ai_sem.disable_semantic_source('GLOSSARY_COPY'); end;")
    lab.step('B07 SHOWSQL through SEM_COPY, source disabled', L, SHOWSQL, {'p': PROMPT, 'pr': 'SEM_COPY'},
             clob_out=True, expect='is disabled')
    lab.step('B08 enable it again', L, "begin dbms_ai_sem.enable_semantic_source('GLOSSARY_COPY'); end;")
    lab.step('B09 SHOWCONTEXT through SEM_COPY after enable', L, SHOWCTX, {'p': PROMPT, 'pr': 'SEM_COPY'},
             clob_out=True, contains='Domain Knowledge')
    # drop while an enabled profile uses it
    lab.step('B10 drop the source while SEM_COPY (enabled) uses it', L,
             "begin dbms_ai_sem.drop_semantic_source('GLOSSARY_COPY'); end;")
    lab.step('B11 SHOWCONTEXT through SEM_COPY after the drop', L, SHOWCTX, {'p': PROMPT, 'pr': 'SEM_COPY'},
             clob_out=True, expect='ORA-20047')
    # drop while only a disabled profile uses it
    lab.step('B12 create GLOSSARY_COPY2 and profile SEM_COPY2', L,
             """begin dbms_ai_sem.drop_semantic_source('GLOSSARY_COPY2', force => true);
                  dbms_ai_sem.create_semantic_source(source_name => 'GLOSSARY_COPY2', type => 'file', attributes => :a);
                  dbms_cloud_ai.drop_profile('SEM_COPY2', force => true); dbms_cloud_ai.create_profile('SEM_COPY2', :p); end;""",
             {'a': FILE_SRC, 'p': prof(c, '"semantic_source": ["GLOSSARY_COPY2"]')})
    lab.step('B13 disable the profile SEM_COPY2', L, "begin dbms_cloud_ai.disable_profile('SEM_COPY2'); end;")
    lab.step('B14 drop GLOSSARY_COPY2 while only a disabled profile uses it', L,
             "begin dbms_ai_sem.drop_semantic_source('GLOSSARY_COPY2'); end;")
    lab.step('B15 is GLOSSARY_COPY2 gone?', L,
             "select count(*) || ' source(s) named GLOSSARY_COPY2' from user_ai_semantic_sources where source_name = 'GLOSSARY_COPY2'",
             query=True, contains='0 source(s)')
    # replay GET_DEFINITION
    lab.step('B16 create GLOSSARY_COPY3', L, MKSRC, {'s': 'GLOSSARY_COPY3', 'a': FILE_SRC})
    d = lab.step('B17 GET_DEFINITION of GLOSSARY_COPY3, then drop it', L,
                 """declare l clob; begin l := dbms_ai_sem.get_definition('GLOSSARY_COPY3');
                      dbms_ai_sem.drop_semantic_source('GLOSSARY_COPY3'); :res := l; end;""", clob_out=True,
                 contains='GLOSSARY_COPY3')
    definition = re.sub(r'\s*/\s*$', '', d['text'].strip()) if d['outcome'] == 'OK' else 'begin null; end;'
    lab.step('B18 run the returned definition', L, definition)
    lab.step('B19 is GLOSSARY_COPY3 back?', L,
             "select source_name, source_type, status from user_ai_semantic_sources where source_name = 'GLOSSARY_COPY3'",
             query=True, contains='GLOSSARY_COPY3 | file | ENABLED')
    # a profile naming a source that does not exist
    lab.step('B20 profile naming a source of its own schema that does not exist', L, MKPROF,
             {'n': 'SEM_MISSING', 'a': prof(c, '"semantic_source": ["NO_SUCH_SOURCE"]')})
    lab.step('B21 SHOWCONTEXT through it', L, SHOWCTX, {'p': PROMPT, 'pr': 'SEM_MISSING'}, clob_out=True,
             expect='ORA-20047')
    # another owner's profile, before and after the grant
    xprof = prof(c, '"semantic_source": ["SAI_SEM_LAB.SALES_GLOSSARY"]')
    lab.step('B22 SAI_SEM_USER: profile naming SAI_SEM_LAB.SALES_GLOSSARY, before any grant', U, MKPROF,
             {'n': 'USER_SEM', 'a': xprof}, expect='ORA-20000')
    lab.step('B23 owner grants the source to SAI_SEM_USER', L,
             "begin dbms_ai_sem.grant_semantic_source_access('SALES_GLOSSARY', 'SAI_SEM_USER'); end;")
    lab.step('B24 SAI_SEM_USER: the same profile after the grant', U, MKPROF, {'n': 'USER_SEM', 'a': xprof},
             expect='ORA-20000')
    lab.step('B25 SAI_SEM_USER: what it sees of the source', U,
             "select owner, source_name, source_type, status from all_ai_semantic_sources order by 1, 2", query=True,
             contains='SAI_SEM_LAB | SALES_GLOSSARY')
    lab.step('B26 SAI_SEM_USER: try to change the granted source', U,
             "begin dbms_ai_sem.update_semantic_source('SAI_SEM_LAB.SALES_GLOSSARY', 'refresh_rate', '5'); end;",
             expect='not owned by current user')
    lab.step('B27 owner grants to itself', L,
             "begin dbms_ai_sem.grant_semantic_source_access('SALES_GLOSSARY', 'SAI_SEM_LAB'); end;",
             expect='cannot be the current session user')
    lab.step('B28 owner revokes the grant', L,
             "begin dbms_ai_sem.revoke_semantic_source_access('SALES_GLOSSARY', 'SAI_SEM_USER'); end;")
    lab.step('B29 SAI_SEM_USER: what it sees after the revoke', U,
             "select count(*) || ' visible source(s) of SAI_SEM_LAB' from all_ai_semantic_sources where owner = 'SAI_SEM_LAB'",
             query=True, contains='0 visible')
    # source types this release accepts
    lab.step('B30 an ontology source', L,
             """begin dbms_ai_sem.create_semantic_source(source_name => 'SALES_ONTOLOGY', type => 'ontology',
                  attributes => '{"scope":"all"}'); end;""", expect='Invalid semantic source type')


def cleanup(lab):
    L, U = 'SAI_SEM_LAB', 'SAI_SEM_USER'
    actions = [
        (L, "begin dbms_ai_sem.revoke_semantic_source_access('SALES_GLOSSARY', 'SAI_SEM_USER'); end;", 'ORA-01927'),
        (U, "begin dbms_cloud_ai.drop_profile('USER_SEM', force => true); end;", None),
        (L, "begin dbms_cloud_ai.drop_profile('SEM_COPY', force => true); end;", None),
        (L, "begin dbms_cloud_ai.drop_profile('SEM_COPY2', force => true); end;", None),
        (L, "begin dbms_cloud_ai.drop_profile('SEM_MISSING', force => true); end;", None),
        (L, "begin dbms_ai_sem.drop_semantic_source('GLOSSARY_COPY', force => true); end;", None),
        (L, "begin dbms_ai_sem.drop_semantic_source('GLOSSARY_COPY2', force => true); end;", None),
        (L, "begin dbms_ai_sem.drop_semantic_source('GLOSSARY_COPY3', force => true); end;", None),
        (L, "begin dbms_ai_sem.drop_semantic_source('SALES_ONTOLOGY', force => true); end;", None),
    ]
    for i, (actor, block, allowed_error) in enumerate(actions, 1):
        t0 = time.monotonic()
        try:
            lab.conns[actor].cursor().execute(block)
            outcome, text = 'OK', 'done'
        except Exception as e:  # recorded; the next action still runs
            outcome, text = 'ERROR', str(e).split('\nHelp:')[0]
        ok = outcome == 'OK' or (allowed_error is not None and allowed_error in text)
        if not ok:
            lab.failed.append(f'Z{i:02d} cleanup')
        lab.write({'run': lab.run, 'step': f'Z{i:02d} cleanup', 'actor': actor, 'call': block, 'binds': {},
                   'outcome': outcome, 'text': text[:500], 'elapsed_ms': int((time.monotonic() - t0) * 1000),
                   'expected': 'OK' + (f' or {allowed_error}' if allowed_error else ''), 'must_contain': None,
                   'as_expected': ok},
                  f'--- Z{i:02d} cleanup as {actor}\n{block}\n=> {outcome}: {"as expected" if ok else "NOT AS EXPECTED"}\n{text[:500]}\n\n')
    checks = [
        ('V01 verify: test sources left', L,
         "select count(*) || ' left' from user_ai_semantic_sources where source_name like 'GLOSSARY_COPY%' or source_name = 'SALES_ONTOLOGY'",
         '0 left'),
        ('V02 verify: test profiles left', L,
         "select count(*) || ' left' from user_cloud_ai_profiles where profile_name in ('SEM_COPY', 'SEM_COPY2', 'SEM_MISSING')", '0 left'),
        ('V03 verify: SAI_SEM_USER profile USER_SEM left', U,
         "select count(*) || ' left' from user_cloud_ai_profiles where profile_name = 'USER_SEM'", '0 left'),
        ('V04 verify: SAI_SEM_USER sees no SAI_SEM_LAB source (grant gone)', U,
         "select count(*) || ' visible' from all_ai_semantic_sources where owner = 'SAI_SEM_LAB'", '0 visible'),
    ]
    for name, actor, sql, want in checks:
        try:
            lab.step(name, actor, sql, query=True, contains=want)
        except Exception as e:  # a broken connection: recorded as a failure, the next check still runs
            lab.failed.append(name)
            log.error('%s could not run: %s', name, e)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ap = argparse.ArgumentParser(description='brief 15 semantic-source behaviours')
    ap.add_argument('--run', required=True)
    a = ap.parse_args(argv)
    if not RUN_RE.match(a.run):
        raise SystemExit('run id: letters, digits, _ and - only')
    if os.path.exists(os.path.join(BASE, 'results', f'{a.run}.jsonl')):
        raise SystemExit(f'results/{a.run}.jsonl exists: use a new run id')
    os.makedirs(os.path.join(BASE, 'results'), exist_ok=True)
    os.makedirs(os.path.join(BASE, 'transcripts'), exist_ok=True)   # the run's transcript goes here
    lab = Lab(a.run)
    try:
        try:
            steps(lab)
        finally:
            cleanup(lab)
    finally:
        for c in lab.conns.values():
            try:
                c.close()
            except Exception:  # already broken
                pass
    if lab.failed:
        log.error('steps not as expected: %s', ', '.join(lab.failed))
        return 1
    log.info('all steps as expected')
    return 0


if __name__ == '__main__':
    sys.exit(main())
