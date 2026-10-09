"""What a script that does not exist answers. Models call plausible script names that were never there (show.py,
grep.py, nonexistent.py in V1, V5, V6), more often when they think (Reasoning Trap); such a call is answered with the
call to make now instead of an error (poka-yoke)."""
import sys

import _journal
import _state

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)


def answer(name):
    try:
        state = _state.load('journal') or _journal.new()
        nxt = _journal.next_call(state)
    except Exception:          # never a traceback: the call below is always right at the start
        nxt = 'NEXT: call ' + _journal.CALL + _journal.FORMS['S0']
    print(f'This skill has one script, scripts/step.py; {name} only points to it, and nothing was run or changed. '
          f'The call to make now:\n\n{nxt}')
    sys.exit(0)
