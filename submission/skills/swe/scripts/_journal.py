"""The procedure engine behind journal.py.

It is active only when the skill ships assets/procedure.json (the single agent). It derives the phase of the work
from what the scripts really did (the events below, the git diff and check.py's last verdict), never from notes the
model writes, and it:
- appends one JOURNAL line to the output of every script: the step, what is done, and the exact next call;
- refuses a call that does not belong to the current step (reading after the read budget, experiments after the
  try budget), so the model cannot drift away from the procedure.
"""
import json
import os
import re
import subprocess
import sys
import time

import _common

HERE = os.path.dirname(os.path.abspath(__file__))
PROC_PATH = os.path.join(HERE, '..', 'assets', 'procedure.json')
EVENTS = _common._state_path('events.jsonl')
READERS = ('locate.py', 'show.py', 'callers.py')
STEP_NO = {'LOCATE': 1, 'UNDERSTAND': 2, 'EDIT': 3, 'VERIFY': 4, 'SUBMIT': 5}


def load_procedure():
    try:
        with open(PROC_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


PROC = load_procedure()


def events():
    try:
        with open(EVENTS) as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError):
        return []


def record(event):
    event['t'] = time.time()
    try:
        with open(EVENTS, 'a') as fh:
            fh.write(json.dumps(event) + '\n')
    except OSError:
        pass


SEEN_RE = [
    re.compile(r'^(?:Definition of \S+: )?(?P<file>\S+\.py) :: (?P<symbol>\S+) \(\w+\) lines (?P<start>\d+)-(?P<end>\d+)'),
    re.compile(r'^(?P<file>\S+\.py) lines (?P<start>\d+)-(?P<end>\d+)'),
    re.compile(r'^EDITED (?P<file>\S+\.py): lines (?P<start>\d+)-'),
]


def classify(script, text):
    """What a finished script call did, read from its own output."""
    event = {'script': script}
    # Code the model has seen in full: show.py, edit.py, and locate.py when it prints one definition.
    for line in text.splitlines() if script in ('show.py', 'edit.py', 'locate.py') else []:
        for rx in SEEN_RE:
            m = rx.match(line.strip())
            if m and script == 'locate.py' and not line.startswith('Definition of'):
                m = None
            if m:
                g = m.groupdict()
                event['seen'] = {'file': g['file'], 'symbol': g.get('symbol') or '', 'start': int(g['start']),
                                 'end': int(g.get('end') or g['start'])}
                break
        if 'seen' in event:
            break
    if script == 'edit.py':
        event['outcome'] = 'applied' if re.search(r'^EDITED ', text, re.M) else 'not_applied'
    elif script == 'check.py':
        verdict = next((l for l in reversed(text.splitlines()) if l.startswith('VERDICT')), '')
        event['outcome'] = ('ok' if verdict.startswith('VERDICT: OK') else
                            'none' if 'NO CHANGES' in verdict else
                            'timeout' if 'TIMED OUT' in verdict else
                            'broke' if 'BREAKS' in verdict or 'SYNTAX' in verdict else
                            'fail' if verdict else 'unknown')
    return event


def worktree():
    """(diff text, True when the diff is exactly what check.py approved)."""
    try:
        root = _common.repo_root()
        r = subprocess.run(['git', '-c', 'safe.directory=*', 'diff', '--binary'], cwd=root, capture_output=True,
                           text=True, timeout=20)
        diff = r.stdout if r.returncode == 0 else ''
        good = open(_common.GOOD).read() if os.path.exists(_common.GOOD) else ''
    except Exception:  # noqa: BLE001
        return '', False
    return diff, bool(diff.strip()) and diff == good


def candidate():
    try:
        with open(_common.CANDIDATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def state(evts=None):
    """The current step of the procedure and the facts it was derived from."""
    evts = events() if evts is None else evts
    done = [e for e in evts if not e.get('refused')]
    diff, good = worktree()
    applied = [i for i, e in enumerate(done) if e['script'] == 'edit.py' and e.get('outcome') == 'applied']
    last_edit = applied[-1] if applied else -1
    checks = [(i, e) for i, e in enumerate(done) if e['script'] == 'check.py' and e.get('outcome') != 'none']
    last_check = checks[-1] if checks else (-1, {})
    seen = [e['seen'] for e in done if e.get('seen')]
    s = {
        'diff': bool(diff.strip()), 'good': good,
        'elapsed': time.time() - evts[0]['t'] if evts else 0,
        'located': any(e['script'] == 'locate.py' for e in done),
        'hints': any(e['script'] == 'hints.py' for e in done),
        'seen': seen[-1] if seen else None,
        'reads': sum(1 for e in done[last_edit + 1:] if e['script'] in READERS),
        'edited': bool(applied),
        'tries': sum(1 for e in done if e['script'] == 'try.py'),
        'last_edit_failed': bool(done) and done[-1]['script'] == 'edit.py' and done[-1].get('outcome') == 'not_applied',
        'last_check': last_check[1].get('outcome', '') if last_check[0] > last_edit else '',
        'broke': bool(checks) and checks[-1][1].get('outcome') == 'broke' and checks[-1][0] > last_edit,
        'fails': sum(1 for _, e in checks if e.get('outcome') == 'fail'),
        'counts': {},
    }
    for e in done:
        s['counts'][e['script']] = s['counts'].get(e['script'], 0) + 1
    late = s['elapsed'] > PROC.get('late_after_seconds', 200)
    if s['diff'] and (good or s['last_check'] == 'timeout' or late or s['fails'] >= 2):
        s['step'] = 'SUBMIT'
    elif s['diff'] and s['last_check'] == 'fail':
        s['step'] = 'EDIT'
    elif s['diff']:
        s['step'] = 'VERIFY'
    elif not s['located'] and not s['seen']:
        s['step'] = 'LOCATE'
    elif late or s['broke'] or s['edited']:
        s['step'] = 'EDIT'
    elif not s['hints'] or not s['seen']:
        s['step'] = 'UNDERSTAND'
    else:
        s['step'] = 'EDIT'
    s['late'] = late
    return s


def _place(seen):
    return f'"{seen["file"]}", "{seen["symbol"] or str(seen["start"]) + "-" + str(seen["end"])}"'


def next_call(s):
    """The exact next action for the current step."""
    step, seen = s['step'], s['seen']
    if step == 'LOCATE':
        return ('call locate.py with the function, class and option names and the error text of the statement, '
                'for example ["Client.send", "timeout"].')
    if step == 'UNDERSTAND':
        if not s['hints']:
            return 'call hints.py with the key words of the statement, to get the checklist of what the fix must cover.'
        c = candidate()
        if c:
            return (f'call show.py ["{c["file"]}", "{c["symbol"]}"] to see the numbered code of the best candidate '
                    '(or show.py on another candidate of the locate.py list if that one fits the statement better).')
        return 'call show.py [file, symbol] on the best candidate of the locate.py list.'
    if step == 'EDIT':
        if s['broke'] and seen:
            return (f'your last edit was undone because it broke tests. Call show.py [{_place(seen)}] to see the '
                    'current code, then edit.py with a corrected change that keeps the existing behaviour.')
        if s['last_check'] == 'fail':
            return ('read the failing tests above. If your edit caused them, fix it with edit.py; if not, call the '
                    'submit_patch tool.')
        if s['last_edit_failed']:
            return 'call edit.py again for the same lines with corrected text (see the error above).'
        if seen:
            return (f'call edit.py ["{seen["file"]}", A, B, new lines]: A-B are the lines to replace, inside lines '
                    f'{seen["start"]}-{seen["end"]} shown above; new lines is the fixed code with its indentation and '
                    'without the line numbers.')
        return 'call edit.py [file, old lines, new lines] with the code you have seen.'
    if step == 'VERIFY':
        return 'call check.py [] to run the related tests on your change.'
    if s['late'] and not s['good']:
        return 'time is almost up: call the submit_patch tool now, then reply with one sentence.'
    return ('your change passed check.py: call the submit_patch tool now, then reply with one sentence naming the '
            'files and the change. If the statement asks for another change, make it with edit.py first.')


def journal_line(s=None):
    s = state() if s is None else s
    done = ', '.join(f'{k[:-3]} x{v}' for k, v in s['counts'].items() if k != 'journal.py') or 'nothing yet'
    return f'JOURNAL: step {STEP_NO[s["step"]]}/5 {s["step"]}. Done: {done}. NEXT: {next_call(s)}'


def gate(script):
    """Reason to refuse this call in the current step, or '' to run it."""
    if script in ('journal.py', 'check.py', 'edit.py'):
        return ''
    s = state()
    limits = PROC.get('limits', {})
    if s['step'] == 'SUBMIT':
        if s['good']:
            return ('your fix is done and passed check.py. The hidden tests are already written, so there is nothing '
                    'to search, read or test any more (even if the statement says tests were added)')
        return 'time is almost up and your change is in place; there is no time left to read more'
    if script in READERS:
        cap = limits.get('reads_after_edit', 4) if s['edited'] else limits.get('reads_before_edit', 6)
        if s['reads'] >= cap:
            return (f'you have used your {cap} reading calls for this step; the code you need is already in the '
                    'conversation above' + quick_answer())
    if script == 'try.py' and s['tries'] >= limits.get('tries', 2):
        return f'try.py was already used {s["tries"]} times; experiments are over'
    if script == 'hints.py' and s['edited']:
        return 'the checklist is already in the conversation above'
    return ''


def quick_answer():
    """For a refused read: where each name the model asked about is defined, or that it does not exist yet (a new
    name the statement introduces), so the model gets its answer without another reading call."""
    root = _common.repo_root()
    names = []
    for a in sys.argv[1:]:
        for word in re.findall(r'[A-Za-z_][A-Za-z0-9_.]*', a):
            word = word.strip('.')
            if word and not word.endswith('.py') and '/' not in a.strip() and word not in names and len(word) > 2:
                names.append(word)
    lines = []
    for name in names[:4]:
        try:
            defs = _common.find_definitions(root, name, limit=2)
        except Exception:  # noqa: BLE001
            continue
        if defs:
            lines.append(f'{name} is defined in ' + ' and in '.join(
                f'{rel} :: {qual} ({kind}) lines {start}-{end}' for rel, (qual, kind, start, end) in defs))
        else:
            lines.append(f'{name} is not defined anywhere in the repository: it is a new name the statement '
                         'introduces, so create it (or rename the existing code) with edit.py')
    return ('. Quick answer: ' + '; '.join(lines)) if lines else ''
