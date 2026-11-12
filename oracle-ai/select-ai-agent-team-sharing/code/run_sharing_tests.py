#!/usr/bin/env python3
# v2.6 2026-11-11 - published copy: imports lab_conn.py from this folder instead of the lab host's own helper; SAILAB_SECRETS required, no default path; creates transcripts/.
# v2.5 2026-11-11 - neutral lab cities (blog privacy rule; must match 10/11 setup v1.2); log times in UTC ('Z').
# v2.4 2026-11-11 - Codex r5: the stored expectation is sanitized like the stored text (conversation ids -> <id>), so
#                    as_expected can be re-derived from the file.
# v2.3 2026-11-11 - Codex r4: an answer passes only in one of the controlled forms (the city alone, or the sentence
#                    form, optionally ending with the owner's source suffix), case-insensitive; S27 must raise
#                    ORA-20050 for the supplied conversation id; Z07, V01-V04 and A01 are each guarded and recorded,
#                    and the audit runs even if clean-up raised.
# v2.2 2026-11-11 - Codex r3: expectations are exact texts, not bare ORA codes ("does not exist" steps name the team);
#                    an answer passes only when it names the expected city as a whole word, names no other lab city
#                    and has no negation; the reliability summary fails unless every repeat passed; automatic
#                    conversation creation is timed and recorded when it fails; each clean-up action is fully guarded
#                    and only 'not granted' (ORA-01927) is accepted besides success; connections close in a separate
#                    finally; M01-M03 record the run manifest (database banner, the profiles' provider/region/model).
# v2.1 2026-11-11 - cleanup steps record the error they raised as their expectation (T3 counted ORA-01927 'not
#                    granted' as a failure); the end state is checked by V01-V04.
# v2.0 2026-11-11 - Codex r2 (brief 16): every step declares its expected outcome (OK, or the ORA code it must raise,
#                    plus the expected city / text) and records whether it matched; the run exits 1 if any step did not.
#                    City check is strict: the expected city present and no other lab city. Bound values are recorded
#                    (conversation ids replaced by <id>), helper steps are timed and recorded. Cleanup runs in finally,
#                    one revoke per step, each attempted on its own, then verified (no EXECUTE grants left, no TRANSLATE
#                    SQL grant left, the consumers' runs read as missing). A run id whose results file exists is
#                    refused. New step S24b: the role consumer runs after the owner's change too. A01 reads
#                    DBA_AI_AGENT_TEAM_HISTORY as ADMIN for this run's time window only.
# v1.1 2026-11-11 - T1 showed RUN_TEAM needs a conversation_id in params when no SET_TEAM is active (ORA-20053).
# v1.0 2026-11-11 - brief 16: the team-sharing behaviour, step by step, across four logins.
#   The team is built by 10_owner_setup.sql; consumers by 11_consumer_setup.sql. Logins come from env files in the
#   directory named by SAILAB_SECRETS (required), never printed.
# Usage: run_sharing_tests.py --run T3 [--reps 5]
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
SECRETS = os.path.expanduser(os.environ.get('SAILAB_SECRETS', ''))   # folder of the login files
TEAM = 'ACCOUNT_TEAM'
QTEAM = 'SAI_TEAM_OWNER.ACCOUNT_TEAM'
PROMPT = 'What is the billing city for account blue_harbor?'
CITY = {'SAI_TEAM_OWNER': 'Lisbon', 'SAI_TEAM_APP': 'Oslo', 'SAI_TEAM_APP2': 'Denver', 'SAI_TEAM_OUTSIDER': 'Osaka'}
MARK = '(source: account desk)'
INSTR_BASE = ('Answer the account question for {report_audience}. Always call ACCOUNT_LOOKUP_TOOL with the account '
              'name from the question first, then answer in one sentence using only the tool result. User question: {query}')
INSTR_CHANGED = ('Answer the account question for {report_audience}. Always call ACCOUNT_LOOKUP_TOOL with the account '
                 'name from the question first, then answer in one sentence using only the tool result, and end the '
                 'answer with the words "' + MARK + '". User question: {query}')
RUN_RE = re.compile(r'^[A-Za-z0-9_-]{1,30}$')
MISSING = 'Team "SAI_TEAM_OWNER"."ACCOUNT_TEAM" does not exist.'
CONV_RE = re.compile(r'[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}', re.I)
log = logging.getLogger('sharing_tests')


def sanitize(x):
    return CONV_RE.sub('<id>', x) if isinstance(x, str) else x


class Lab:
    def __init__(self, run):
        import oracledb
        from lab_conn import connect, read_env
        if not SECRETS:
            raise SystemExit('set SAILAB_SECRETS to the folder holding the login files')
        self.oracledb = oracledb
        self.connect, self.read_env = connect, read_env
        self.conns = {}
        for user in CITY:
            self.conns[user] = self._login(f'sailab_{user.lower()}.env', user)
        self.run = run
        self.failed = []
        self.jsonl = open(os.path.join(BASE, 'results', f'{run}.jsonl'), 'x', encoding='utf-8')
        self.txt = open(os.path.join(BASE, 'transcripts', f'{run}.txt'), 'w', encoding='utf-8')
        cur = self.conns['SAI_TEAM_OWNER'].cursor()
        cur.execute("select to_char(systimestamp at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.FF3') from dual")
        self.started_utc = cur.fetchone()[0]

    def _login(self, fname, user):
        try:
            conn, _ = self.connect(self.read_env(os.path.join(SECRETS, fname)))
        except self.oracledb.Error as e:
            raise SystemExit(f'cannot connect as {user}: {str(e).splitlines()[0]}') from e
        conn.call_timeout = 180000
        conn.action = 'brief16'
        return conn

    def city_ok(self, text, expect):
        """Only the controlled answer forms pass: '<City>' or 'The billing city for account blue_harbor is <City>.',
        each optionally followed by the owner's source suffix. Anything else (hedges, negations, two cities) fails."""
        form = (r'(?:the billing city for account blue_harbor is )?' + re.escape(expect)
                + r'\.?(?: \(source: account desk\))?\.?')
        return re.fullmatch(form, (text or '').strip(), re.I) is not None

    def record(self, step, actor, call, binds, outcome, text, ms, expect, city=None, must_contain=None):
        if expect == 'OK':
            ok = outcome == 'OK'
            if ok and city:
                ok = self.city_ok(text, city)
            if ok and must_contain:
                ok = must_contain in (text or '')
        else:
            ok = outcome == 'ERROR' and expect in (text or '')
        if not ok:
            self.failed.append(step)
        row = {'run': self.run, 'step': step, 'actor': actor, 'action': sanitize(call),
               'binds': {k: sanitize(v) for k, v in (binds or {}).items()}, 'outcome': outcome,
               'text': sanitize(text or '')[:1500], 'elapsed_ms': ms, 'expected': sanitize(expect), 'expected_city': city,
               'must_contain': must_contain, 'as_expected': ok}
        self.jsonl.write(json.dumps(row) + '\n')
        self.jsonl.flush()
        self.txt.write(f'--- {step}  as {actor}\n{sanitize(call)}\n'
                       + (f'binds: {json.dumps(row["binds"])}\n' if binds else '')
                       + f'=> {outcome} ({ms} ms); expected {sanitize(expect)}'
                       + (f', city {city}' if city else '') + (f', text "{must_contain}"' if must_contain else '')
                       + f': {"as expected" if ok else "NOT AS EXPECTED"}\n{sanitize(text or "").strip()[:1500]}\n\n')
        self.txt.flush()
        log.info('%s %s: %s %s %s', step, actor, outcome, 'ok' if ok else 'UNEXPECTED',
                 sanitize(text or '').replace('\n', ' ')[:100])
        return row

    def plsql(self, step, actor, block, binds=None, out=False, expect='OK', city=None, must_contain=None, label=None):
        cur = self.conns[actor].cursor()
        b = dict(binds or {})
        res = cur.var(self.oracledb.DB_TYPE_CLOB) if out else None
        if out:
            b['res'] = res
        t0 = time.monotonic()
        try:
            cur.execute(block, b)
            text = res.getvalue() if out else 'done'
            text = text.read() if hasattr(text, 'read') else (text or '')
            outcome = 'OK'
        except self.oracledb.Error as e:
            text, outcome = str(e).split('\nHelp:')[0], 'ERROR'
        return self.record(step, actor, label or ' '.join(block.split()), binds, outcome, text,
                           int((time.monotonic() - t0) * 1000), expect, city, must_contain)

    def new_conversation(self, actor):
        return self.conns[actor].cursor().callfunc('dbms_cloud_ai.create_conversation', self.oracledb.DB_TYPE_VARCHAR)

    def run_team(self, step, actor, team, params, expect='OK', city=None, must_contain=None, conversation=True):
        if conversation and 'conversation_id' not in params:
            t0 = time.monotonic()
            try:
                conv = self.new_conversation(actor)
            except self.oracledb.Error as e:
                return self.record(step, actor, 'DBMS_CLOUD_AI.CREATE_CONVERSATION() before RUN_TEAM', None, 'ERROR',
                                   str(e).split('\nHelp:')[0], int((time.monotonic() - t0) * 1000), expect, city,
                                   must_contain)
            params = params[:-1] + f', "conversation_id": "{conv}"}}'
        return self.plsql(step, actor,
                          'begin :res := dbms_cloud_ai_agent.run_team(team_name => :t, user_prompt => :p, params => :pa); end;',
                          {'t': team, 'p': PROMPT, 'pa': params}, out=True, expect=expect, city=city,
                          must_contain=must_contain,
                          label=f"RUN_TEAM(team_name => '{team}', user_prompt => '{PROMPT}', params => '{params}')")

    def sql(self, step, actor, stmt, expect='OK', city=None, conn=None, must_contain=None):
        cur = (conn or self.conns[actor]).cursor()
        t0 = time.monotonic()
        try:
            cur.execute(stmt)
            rows = cur.fetchall() if cur.description else []
            text = '\n'.join(' | '.join('' if v is None else str(v.read() if hasattr(v, 'read') else v) for v in r)
                             for r in rows) or ('no rows' if cur.description else 'done')
            outcome = 'OK'
        except self.oracledb.Error as e:
            text, outcome = str(e).split('\nHelp:')[0], 'ERROR'
        return self.record(step, actor, stmt, None, outcome, text, int((time.monotonic() - t0) * 1000), expect, city,
                           must_contain)


def params(profile, audience='the account desk', extra=''):
    s = json.dumps({'attribute_variables': {'llm_profile': profile},
                    'instruction_variables': {'report_audience': audience}})
    return s[:-1] + extra + '}' if extra else s


def manifest(lab):
    lab.sql('M01 manifest: database release', 'SAI_TEAM_OWNER', 'select banner_full from v$version',
            must_contain='Oracle AI Database 26ai')
    lab.sql('M02 manifest: owner profile (provider, region, model)', 'SAI_TEAM_OWNER',
            "select profile_name, attribute_name, dbms_lob.substr(attribute_value, 60, 1) from user_cloud_ai_profile_attributes"
            " where profile_name = 'OWNER_GROK' and attribute_name in ('provider', 'region', 'model') order by 2")
    lab.sql('M03 manifest: consumer profile (provider, region, model)', 'SAI_TEAM_APP',
            "select profile_name, attribute_name, dbms_lob.substr(attribute_value, 60, 1) from user_cloud_ai_profile_attributes"
            " where profile_name = 'APP_GROK' and attribute_name in ('provider', 'region', 'model') order by 2")


def steps(lab, reps):
    O, A, A2, X = 'SAI_TEAM_OWNER', 'SAI_TEAM_APP', 'SAI_TEAM_APP2', 'SAI_TEAM_OUTSIDER'
    lab.run_team('S00 owner runs its own team without a conversation id', O, TEAM, params('OWNER_GROK'),
                 expect='Conversation_id is not set in the session.', conversation=False)
    lab.run_team('S01 owner runs its own team', O, TEAM, params('OWNER_GROK'), city=CITY[O])
    lab.run_team('S02 outsider, never granted', X, QTEAM, params('APP_GROK'), expect=MISSING)
    lab.run_team('S03 consumer before any grant', A, QTEAM, params('APP_GROK'), expect=MISSING)
    lab.plsql('S04 owner grants the team to SAI_TEAM_APP', O,
              "begin dbms_cloud_ai_agent.grant_team_access(team_name => 'ACCOUNT_TEAM', user_or_role_name => 'SAI_TEAM_APP'); end;")
    lab.run_team('S05 consumer, team name not qualified', A, TEAM, params('APP_GROK'), expect='Team "ACCOUNT_TEAM" does not exist.')
    lab.run_team('S06 consumer, qualified, no EXECUTE on the tool function yet', A, QTEAM, params('APP_GROK'),
                 expect='Procedure not found: SAI_TEAM_OWNER.SAI_TEAM_OWNER.ACCOUNT_LOOKUP')
    lab.sql('S07 owner grants EXECUTE on the tool function', O, 'grant execute on account_lookup to sai_team_app')
    lab.run_team('S08 consumer, qualified, own profile and audience', A, QTEAM,
                 params('APP_GROK', 'regional sales managers'), city=CITY[A])
    lab.run_team('S09 consumer, no llm_profile value', A, QTEAM,
                 json.dumps({'instruction_variables': {'report_audience': 'regional sales managers'}}),
                 expect='Invalid profile - {llm_profile}')
    lab.run_team("S10 consumer names the owner's profile", A, QTEAM, params('OWNER_GROK'),
                 expect='Profile "OWNER_GROK" does not exist.')
    lab.run_team('S11 consumer, extra variable with no placeholder', A, QTEAM,
                 params('APP_GROK', extra=', "state_variables": {"unused_key": "no placeholder anywhere"}'), city=CITY[A])
    lab.plsql('S12 owner grants the team and the function to role SAI_TEAM_RUNNER', O,
              "begin dbms_cloud_ai_agent.grant_team_access(team_name => 'ACCOUNT_TEAM', user_or_role_name => 'SAI_TEAM_RUNNER');"
              " execute immediate 'grant execute on account_lookup to sai_team_runner'; end;")
    lab.sql('S13 role consumer: enabled roles', A2, 'select role from session_roles order by 1')
    lab.run_team('S14 role consumer, role enabled', A2, QTEAM, params('APP_GROK'), city=CITY[A2])
    lab.plsql('S15 role consumer disables its roles', A2, "begin dbms_session.set_role('NONE'); end;")
    lab.run_team('S16 role consumer, roles disabled', A2, QTEAM, params('APP_GROK'), expect=MISSING)
    lab.plsql('S17 role consumer enables its roles again', A2, "begin dbms_session.set_role('ALL'); end;")
    lab.plsql('S18 consumer SET_TEAM without the SQL translation grant', A,
              'begin dbms_cloud_ai_agent.set_team(team_name => :t, params => :pa); end;',
              {'t': QTEAM, 'pa': params('APP_GROK')}, expect='ORA-01031: insufficient privileges')
    lab.sql('S19 consumer grants TRANSLATE SQL on itself to the owner', A, 'grant translate sql on user sai_team_app to sai_team_owner')
    lab.plsql('S20 consumer SET_TEAM after the grant', A, 'begin dbms_cloud_ai_agent.set_team(team_name => :t, params => :pa); end;',
              {'t': QTEAM, 'pa': params('APP_GROK')})
    lab.sql('S21 consumer asks through SELECT AI AGENT', A, 'select ai agent ' + PROMPT, city=CITY[A])
    lab.plsql('S22 consumer clears the session team', A, 'begin dbms_cloud_ai_agent.clear_team; end;')
    lab.plsql('S23 owner changes the task instruction once', O,
              "begin dbms_cloud_ai_agent.set_attribute(object_name => 'ACCOUNT_TASK', object_type => 'task',"
              " attribute_name => 'instruction', attribute_value => :v); end;", {'v': INSTR_CHANGED})
    lab.run_team('S24 consumer runs again, nothing changed on its side', A, QTEAM, params('APP_GROK'),
                 city=CITY[A], must_contain=MARK)
    lab.run_team('S24b role consumer runs again, nothing changed on its side', A2, QTEAM, params('APP_GROK'),
                 city=CITY[A2], must_contain=MARK)
    t0 = time.monotonic()
    try:
        conv = lab.new_conversation(A)
        lab.record('S25 consumer creates a conversation', A, 'DBMS_CLOUD_AI.CREATE_CONVERSATION()', None, 'OK',
                   '<id>', int((time.monotonic() - t0) * 1000), 'OK')
    except lab.oracledb.Error as e:
        lab.record('S25 consumer creates a conversation', A, 'DBMS_CLOUD_AI.CREATE_CONVERSATION()', None, 'ERROR',
                   str(e).split('\nHelp:')[0], int((time.monotonic() - t0) * 1000), 'OK')
        conv = '00000000-0000-0000-0000-000000000000'
    lab.run_team('S26 consumer runs in its conversation', A, QTEAM,
                 params('APP_GROK', extra=f', "conversation_id": "{conv}"'), city=CITY[A])
    lab.run_team("S27 role consumer passes the other consumer's conversation id", A2, QTEAM,
                 params('APP_GROK', extra=f', "conversation_id": "{conv}"'), expect='ORA-20050: Conversation id=' + conv)
    ok = 0
    for i in range(1, reps + 1):
        row = lab.run_team(f'R{i:02d} reliability run {i} of {reps}', A, QTEAM, params('APP_GROK'), city=CITY[A])
        ok += 1 if row['as_expected'] else 0
    lab.record('R-SUM reliability', A, f'{reps} granted runs', None, 'OK' if ok == reps else 'ERROR',
               f'{ok} of {reps} answers named {CITY[A]} only', 0, 'OK')
    lab.sql('S28 owner: history rows of its team', O,
            'select team_name, state, count(*) runs from user_ai_agent_team_history group by team_name, state order by 1, 2')
    lab.sql('S29 consumer: history rows it can see', A,
            'select team_name, state, count(*) runs from user_ai_agent_team_history group by team_name, state order by 1, 2')
    lab.sql('S30 consumer: teams in its own schema', A, 'select agent_team_name from user_ai_agent_teams')
    lab.plsql('S31 owner revokes the team from SAI_TEAM_APP', O,
              "begin dbms_cloud_ai_agent.revoke_team_access(team_name => 'ACCOUNT_TEAM', user_or_role_name => 'SAI_TEAM_APP'); end;")
    lab.run_team('S32 consumer after the revoke', A, QTEAM, params('APP_GROK'), expect=MISSING)
    lab.run_team('S33 owner still runs its team', O, TEAM, params('OWNER_GROK'), city=CITY[O], must_contain=MARK)


def cleanup(lab):
    """Each undo fully guarded and attempted on its own; success or 'not granted' (ORA-01927) is fine, anything else is
    a failure; the end state is then verified."""
    O, A, A2 = 'SAI_TEAM_OWNER', 'SAI_TEAM_APP', 'SAI_TEAM_APP2'
    undo = [
        (O, "begin dbms_cloud_ai_agent.revoke_team_access(team_name => 'ACCOUNT_TEAM', user_or_role_name => 'SAI_TEAM_APP'); end;"),
        (O, "begin dbms_cloud_ai_agent.revoke_team_access(team_name => 'ACCOUNT_TEAM', user_or_role_name => 'SAI_TEAM_RUNNER'); end;"),
        (O, "begin execute immediate 'revoke execute on account_lookup from sai_team_app'; end;"),
        (O, "begin execute immediate 'revoke execute on account_lookup from sai_team_runner'; end;"),
        (A, "begin execute immediate 'revoke translate sql on user sai_team_app from sai_team_owner'; end;"),
        (A, 'begin dbms_cloud_ai_agent.clear_team; end;'),
    ]
    for i, (actor, block) in enumerate(undo, 1):
        t0 = time.monotonic()
        try:
            lab.conns[actor].cursor().execute(block)
            outcome, text = 'OK', 'done'
        except Exception as e:  # any failure (connection included) is recorded; the next action still runs
            outcome, text = 'ERROR', str(e).split('\nHelp:')[0]
        allowed = 'OK' if outcome == 'OK' else ('ORA-01927' if 'ORA-01927' in text else 'OK')
        lab.record(f'Z{i:02d} cleanup', actor, ' '.join(block.split()), None, outcome, text,
                   int((time.monotonic() - t0) * 1000), allowed)
    guarded(lab, 'Z07 cleanup: task instruction back to the original', O, lambda: lab.plsql('Z07 cleanup: task instruction back to the original', O,
              "begin dbms_cloud_ai_agent.set_attribute(object_name => 'ACCOUNT_TASK', object_type => 'task',"
              " attribute_name => 'instruction', attribute_value => :v); end;", {'v': INSTR_BASE}))
    guarded(lab, 'V01 verify', O, lambda: lab.sql('V01 verify: EXECUTE grants left on the tool function', O,
            "select case count(*) when 0 then 'NONE LEFT' else 'LEFT: ' || count(*) end from user_tab_privs_made"
            " where table_name = 'ACCOUNT_LOOKUP'", must_contain='NONE LEFT'))
    guarded(lab, 'V02 verify', A, lambda: lab.sql('V02 verify: TRANSLATE SQL grants left', A,
            "select case count(*) when 0 then 'NONE LEFT' else 'LEFT: ' || count(*) end from user_tab_privs_made"
            " where privilege = 'TRANSLATE SQL'", must_contain='NONE LEFT'))
    guarded(lab, 'V03 verify', A, lambda: lab.run_team('V03 verify: direct consumer has no team access', A, QTEAM,
                                                       params('APP_GROK'), expect=MISSING))
    guarded(lab, 'V04 verify', A2, lambda: lab.run_team('V04 verify: role consumer has no team access', A2, QTEAM,
                                                        params('APP_GROK'), expect=MISSING))


def guarded(lab, name, actor, fn):
    """Run one clean-up or verification step; if it raises before recording itself, record the failure."""
    t0 = time.monotonic()
    try:
        fn()
    except Exception as e:  # e.g. a broken connection: recorded as not as expected, the next step still runs
        lab.record(name, actor, 'guarded step', None, 'ERROR', str(e).split('\nHelp:')[0],
                   int((time.monotonic() - t0) * 1000), 'OK')


def dba_history(lab):
    from lab_conn import connect, read_env  # ADMIN, read only
    try:
        admin, _ = connect(read_env(os.path.join(SECRETS, 'admin.env')))
    except lab.oracledb.Error as e:
        lab.record('A01 ADMIN: DBA_AI_AGENT_TEAM_HISTORY, this run', 'ADMIN', 'connect', None, 'ERROR',
                   str(e).splitlines()[0], 0, 'OK')
        return
    lab.sql('A01 ADMIN: DBA_AI_AGENT_TEAM_HISTORY, this run', 'ADMIN',
            "select team_owner, team_name, team_invoker, state, count(*) runs from dba_ai_agent_team_history "
            "where team_owner = 'SAI_TEAM_OWNER' and start_date >= to_timestamp_tz('" + lab.started_utc + " UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS.FF3 TZR') group by team_owner, team_name, team_invoker, state order by 3, 4",
            conn=admin)
    admin.close()


def main(argv=None):
    logging.Formatter.converter = time.gmtime   # log times in UTC, written with a trailing Z
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%dT%H:%M:%SZ')
    ap = argparse.ArgumentParser(description='brief 16 team-sharing tests')
    ap.add_argument('--run', required=True)
    ap.add_argument('--reps', type=int, default=5)
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
            manifest(lab)
            steps(lab, a.reps)
        finally:
            try:
                cleanup(lab)
            finally:
                guarded(lab, 'A01 ADMIN: DBA_AI_AGENT_TEAM_HISTORY, this run', 'ADMIN', lambda: dba_history(lab))
    finally:
        for c in lab.conns.values():
            try:
                c.close()
            except Exception:  # a broken connection is already gone
                pass
    if lab.failed:
        log.error('steps not as expected: %s', ', '.join(lab.failed))
        return 1
    log.info('all steps as expected')
    return 0


if __name__ == '__main__':
    sys.exit(main())
