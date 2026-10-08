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


def call_signature(script):
    return script + ' ' + ' '.join(a.strip().lower() for a in sys.argv[1:])


def insisted(script):
    """The same call was refused before: run it this once (a model that repeats a refused read is stuck on it)."""
    sig = call_signature(script)
    return any(e.get('refused') and e.get('sig') == sig for e in events())


def classify(script, text):
    """What a finished script call did, read from its own output."""
    event = {'script': script, 'sig': call_signature(script)}
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
    if re.search(r'^(NOTE: you already ran|REPEATED CALL|REPEATED EDIT)', text, re.M):
        event['repeat'] = True  # nothing new was read
    if script == 'edit.py':
        event['outcome'] = ('applied' if re.search(r'^(EDITED|CREATED) ', text, re.M) else
                            'repeated' if re.search(r'^REPEATED EDIT', text, re.M) else
                            'no_change' if re.search(r'^NO CHANGE', text, re.M) else 'not_applied')
        m = re.search(r'^(?:EDITED|CREATED) (\S+?):? (lines \d+-\d+ replaced by \d+ line\(s\)|with \d+ line\(s\))', text, re.M)
        if m:
            event['what'] = f'{m.group(1)} {m.group(2)}'
    elif script == 'check.py':
        verdict = next((l for l in reversed(text.splitlines()) if l.startswith('VERDICT')), '')
        m = re.search(r'so your edit breaks them: (.+)$', text, re.M)
        if m:
            event['broke_tests'] = [t.split('::')[-1] for t in m.group(1).split(', ')][:3]
        event['outcome'] = ('ok' if verdict.startswith('VERDICT: OK') else
                            'none' if 'NO CHANGES' in verdict else
                            'timeout' if 'TIMED OUT' in verdict else
                            'broke' if 'BREAKS' in verdict or 'SYNTAX' in verdict else
                            'fail' if verdict else 'unknown')
    return event


def worktree():
    """(diff text, True when the diff is exactly what check.py approved)."""
    try:
        diff = _common.worktree_diff(_common.repo_root()) or ''
        good = open(_common.GOOD).read() if os.path.exists(_common.GOOD) else ''
    except Exception:  # noqa: BLE001
        return '', False
    return diff, bool(diff.strip()) and diff == good


REQS = _common._state_path('requirements.json')


def requirements(diff):
    """The requirements hints.py saved, each with covered=True when one of its names is on an added line of the
    diff (None when it names nothing the diff could show)."""
    try:
        with open(REQS) as fh:
            reqs = json.load(fh)
    except (OSError, ValueError):
        return []
    added = '\n'.join(l[1:] for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))
    touched = touched_symbols(diff)
    for r in reqs:
        r['covered'] = (any(n in touched or re.search(r'\b' + re.escape(n) + r'\b', added) for n in r['names'])
                        if r['names'] else None)
    return reqs


def touched_symbols(diff):
    """Names of the functions and classes (every level) that contain a changed line of the diff."""
    root = _common.repo_root()
    changed = {}
    current = None
    for line in diff.splitlines():
        m = re.match(r'^\+\+\+ b/(\S+)', line)
        if m:
            current = m.group(1)
            changed.setdefault(current, [])
            continue
        m = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
        if m and current:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            changed[current] += list(range(start, start + max(count, 1)))
    names = set()
    for rel, lines in changed.items():
        if not rel.endswith('.py'):
            continue
        syms = _common.symbols(_common.parse(_common.read_text(root, rel)))
        for qual, _, start, end in syms:
            if not lines or any(start <= n <= end for n in lines):
                names.update(qual.split('.'))
    return names


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
    shown = [e['seen'] for e in done if e.get('seen') and e['script'] != 'edit.py']
    s = {
        'diff': bool(diff.strip()), 'good': good,
        'elapsed': time.time() - evts[0]['t'] if evts else 0,
        'located': any(e['script'] == 'locate.py' for e in done),
        'hints': any(e['script'] == 'hints.py' for e in done),
        'seen': seen[-1] if seen else None,
        'shown': shown[-1] if shown else None,
        'reads': sum(1 for e in done[last_edit + 1:] if e['script'] in READERS and not e.get('repeat')),
        'edited': bool(applied),
        'tries': sum(1 for e in done if e['script'] == 'try.py'),
        'last_edit_failed': bool(done) and done[-1]['script'] == 'edit.py' and done[-1].get('outcome') == 'not_applied',
        'last_edit_repeated': bool(done) and done[-1]['script'] == 'edit.py' and
                              done[-1].get('outcome') in ('repeated', 'no_change'),
        'same_failed_edit': len(done) > 1 and all(e['script'] == 'edit.py' and e.get('outcome') == 'not_applied'
                                                  for e in done[-2:]) and done[-1].get('sig') == done[-2].get('sig'),
        'last_check': last_check[1].get('outcome', '') if last_check[0] > last_edit else '',
        'broke': bool(checks) and checks[-1][1].get('outcome') == 'broke' and checks[-1][0] > last_edit,
        # After the undone edit, has the code been shown again (so the next call is edit.py, not show.py)?
        'reshown': bool(checks) and any(e['script'] == 'show.py' for e in done[checks[-1][0] + 1:]),
        'fails': sum(1 for _, e in checks if e.get('outcome') == 'fail'),
        'counts': {},
        'reqs': requirements(diff),
        'oks': sum(1 for _, e in checks if e.get('outcome') == 'ok'),
    }
    s['open'] = [r for r in s['reqs'] if r['covered'] is False]
    # Edits that check.py undid, with the tests they broke: kept in every JOURNAL line, so a model whose history was
    # compacted does not make the same edit again.
    s['undone'] = []
    for i, e in enumerate(done):
        if e['script'] == 'check.py' and e.get('outcome') == 'broke':
            edits = [x.get('what') for x in done[:i] if x['script'] == 'edit.py' and x.get('outcome') == 'applied']
            if edits and edits[-1]:
                s['undone'].append(f'{edits[-1]} (broke {", ".join(e.get("broke_tests") or ["the syntax or tests"])})')
    for e in done:
        s['counts'][e['script']] = s['counts'].get(e['script'], 0) + 1
    late = s['elapsed'] > PROC.get('late_after_seconds', 200)
    if s['diff'] and good and s['open'] and s['oks'] < 3 and not late:
        s['step'] = 'EDIT'  # checked, but a requirement of the statement is not covered yet
    elif s['diff'] and (good or s['last_check'] == 'timeout' or late or s['fails'] >= 2):
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
            return ('call hints.py with the sentences or bullets of the statement that ask for something, one per '
                    'item: it lists the requirements and where each name is defined, or that it is new.')
        c = candidate()
        if c:
            return (f'call show.py ["{c["file"]}", "{c["symbol"]}"] to see the numbered code of the best candidate '
                    '(or show.py on another candidate of the locate.py list if that one fits the statement better).')
        return 'call show.py [file, symbol] on the best candidate of the locate.py list.'
    if step == 'EDIT':
        if s['good'] and s['open']:
            r = s['open'][0]
            names = f' (it names {", ".join(r["names"])}; hints.py said where they are)' if r['names'] else ''
            return (f'requirement {r["id"]} of the statement may not be covered yet: "{r["text"]}"{names}. If it asks '
                    'for a change your edits do not make, make it with show.py and edit.py, then check.py; if your change '
                    'already covers it, call the submit_patch tool.')
        if s['broke'] and seen and not s['reshown']:
            return (f'your last edit was undone because it broke tests. Call show.py [{_place(s["shown"] or seen)}] to see the '
                    'current code, then edit.py with a corrected change that keeps the existing behaviour.')
        if s['last_check'] == 'fail':
            return ('read the failing tests above. If your edit caused them, fix it with edit.py; if not, call the '
                    'submit_patch tool.')
        if s['last_edit_repeated']:
            return ('that edit changed nothing (it was a repeat, or its lines were the same as the file). Send a '
                    'different edit.py call whose new lines contain the fix' +
                    (', or call check.py [] to test the change already made.' if s['diff'] else '.'))
        if s['last_edit_failed']:
            if s['same_failed_edit']:
                return ('this exact edit was already rejected: do not send it again. Read the error above and change '
                        'the text: write the new lines with real line breaks and the indentation of the file, or '
                        'replace fewer lines.')
            return 'call edit.py again for the same lines with corrected text (see the error above).'
        if seen:
            return (f'call edit.py ["{seen["file"]}", A, B, new lines]: A-B are the lines to replace, inside lines '
                    f'{seen["start"]}-{seen["end"]} shown above (or the lines of other code you have seen that the fix '
                    'needs); new lines is the fixed code with its indentation and without the line numbers.')
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
    reqs = ''
    tracked = [r for r in s['reqs'] if r['covered'] is not None]
    if tracked:
        reqs = (f' Requirements covered by your edits: {sum(1 for r in tracked if r["covered"])}/{len(tracked)}' +
                (' (open: ' + ', '.join(f'{r["id"]} {r["names"][0]}' for r in s['open']) + ')' if s['open'] else '') + '.')
    undone = ''
    if s.get('undone'):
        undone = ' Undone edits, do not repeat them: ' + '; '.join(s['undone'][-2:]) + '.'
    return f'JOURNAL: step {STEP_NO[s["step"]]}/5 {s["step"]}. Done: {done}.{reqs}{undone} NEXT: {next_call(s)}'


def gate(script):
    """Reason to refuse this call in the current step, or '' to run it."""
    if script in ('journal.py', 'check.py'):
        return ''
    s = state()
    if script == 'edit.py':
        return edit_gate(s)
    limits = PROC.get('limits', {})
    if s['step'] == 'SUBMIT':
        if s['good']:
            return ('your fix is done and passed check.py. The hidden tests are already written, so there is nothing '
                    'to search, read or test any more (even if the statement says tests were added)')
        return 'time is almost up and your change is in place; there is no time left to read more'
    if script in READERS:
        cap = limits.get('reads_after_edit', 4) if s['edited'] else limits.get('reads_before_edit', 6)
        if s['reads'] >= cap and not insisted(script):
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


def edit_gate(s):
    """Refuse an edit that overlaps the edit just made before check.py has tested it: rewriting the same lines again
    and again shifts them and corrupts the file. Edits elsewhere (another place, the sync/async copy) are fine."""
    if s['step'] != 'VERIFY':
        return ''
    done = [e for e in events() if not e.get('refused')]
    last = next((e for e in reversed(done) if e['script'] == 'edit.py' and e.get('outcome') == 'applied'), None)
    m = re.match(r'(\S+) lines (\d+)-\d+ replaced by (\d+) line', (last or {}).get('what', ''))
    args = sys.argv[1:]
    nums = re.findall(r'\d+', ' '.join(args[1:3])) if len(args) > 2 else []
    if not m or not nums or not re.fullmatch(r'[\d\s,:\-]+', args[1]):
        return ''
    a, n = int(m.group(2)), int(m.group(3))
    lo, hi = min(int(x) for x in nums[:2]), max(int(x) for x in nums[:2])
    if args[0].lstrip('./') == m.group(1) and lo <= a + max(n, 1) - 1 and hi >= a:
        return (f'your last edit already changed {m.group(1)} lines {a}-{a + max(n, 1) - 1}, which this edit overlaps; '
                'test it first with check.py [] (it shows the updated code), then fix it if needed')
    return ''
