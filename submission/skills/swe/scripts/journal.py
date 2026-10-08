"""journal.py   |   journal.py show   |   journal.py <phase> <note ...>

With the procedure of the single agent (assets/procedure.json): prints the steps to follow, what the scripts have
really done so far, the current step and the exact next call (see _journal.py).

Without it: work log kept in /tmp across calls and pipeline stages. Phases: locate, edit, verify, submit. Each call records
the note, flags a step that repeats an earlier one, and prints the log plus the next step for the current phase.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402,F401  (tees output to the call log)
import time

STATE = _common._state_path('journal.jsonl')
PHASES = ['locate', 'edit', 'verify', 'submit']
NEXT = {
    'locate': 'run locate.py with names from the statement, view the best candidate with show.py, then report it.',
    'edit': 'view the numbered code with show.py, change it with edit.py [file, start, end, new lines], then run check.py.',
    'verify': 'run check.py; when the verdict is OK the change is ready.',
    'submit': 'call submit_patch once.',
}


def norm(text):
    return re.sub(r'[^a-z0-9_./]+', ' ', text.lower()).strip()


def load():
    if not os.path.exists(STATE):
        return []
    with open(STATE) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def procedure_view():
    import _journal
    s = _journal.state()
    print('PROCEDURE (follow these steps in order):')
    for n, step in enumerate(_journal.PROC['steps'], 1):
        mark = '->' if n == _journal.STEP_NO[s['step']] else '  '
        print(f'{mark} {n}. {step}')
    done = [e for e in _journal.events() if e['script'] != 'journal.py']
    print(f'Done so far ({len(done)} script calls):')
    for e in done[-12:]:
        what = e.get('outcome') or (f'{e["seen"]["file"]} lines {e["seen"]["start"]}-{e["seen"]["end"]}'
                                    if e.get('seen') else '')
        print(f'  - {e["script"]}' + (' (not run)' if e.get('refused') else '') + (f': {what}' if what else ''))
    print(_journal.journal_line(s))


def main():
    if _common.PROCEDURE_ON:
        procedure_view()
        return
    args = sys.argv[1:]
    entries = load()
    if args and args[0] != 'show':
        phase = args[0].lower() if args[0].lower() in PHASES else (entries[-1]['phase'] if entries else 'locate')
        note = ' '.join(args[1:] if args[0].lower() in PHASES else args)
        repeated = [e for e in entries if norm(e['note']) == norm(note) and note]
        entry = {'t': time.time(), 'phase': phase, 'note': note}
        with open(STATE, 'a') as fh:
            fh.write(json.dumps(entry) + '\n')
        entries.append(entry)
        if repeated:
            print(f'REPEATED STEP: you already did "{note}" (step {entries.index(repeated[0]) + 1}). '
                  'Its result is already in the conversation; take the next step instead.')
    if not entries:
        print('Journal is empty. Record a step with: journal.py <phase> <what you do and why>')
        return
    phase = entries[-1]['phase']
    in_phase = sum(1 for e in entries if e['phase'] == phase)
    print(f'Journal ({len(entries)} steps):')
    for i, e in enumerate(entries[-12:], max(1, len(entries) - 11)):
        print(f'  {i}. [{e["phase"]}] {e["note"][:140]}')
    print(f'Current phase: {phase} ({in_phase} steps in it).')
    if phase == 'locate' and in_phase >= 4:
        print('You have searched enough: report the best candidate you have now.')
    print('NEXT: ' + NEXT.get(phase, NEXT['locate']))


if __name__ == '__main__':
    main()
