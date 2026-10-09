"""The procedure as a state machine (StateFlow, SOP-Agent): S0 start → D1 choose → D3 edit, once per planned place
→ D4 finish (D2, a free-text plan, is passed through: choosing plans the chosen places). The journal, not the model,
decides the next step; a call that does not fit the current step is answered with the call that does. This module
holds only the rules; step.py does the work and the printing."""
import time

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

MAX_FAILS = 2        # failed edits of one place before it is left at its last verified state
EDIT_STOP = 340      # seconds after the start: no edit is accepted later (the run has 420 s, a check up to 60)
CALL = 'run_skill_script with skill_name "fix-issue", file_path "scripts/step.py" and args '

FORMS = {
    'S0': '["<the issue statement, copied>", "<search term>", "<search term>", ...]',
    'D1': '["C<n>"] or ["C<n>", "C<m>"] (1 to 3 candidate ids), or ["<file>::<Name>"] for code not in the list',
    'D2': '["P<n>: <what changes there>", "P<m>: <what changes there>"] (one item per place to edit), or ["back"] '
          'to choose other candidates',
    'D3': '["P<n>", "<the whole new function or class>"] or ["P<n>", "<first line number>", "<last line number>", '
          '"<new lines>"], or ["skip"] if the place needs no change, or ["back"] to choose other code',
    'D4': 'none: call submit_patch now. Or args ["R<n>"] to go back for a requirement that is not covered, or '
          '["P<n>", "<the whole new function or class>"] to edit a place again',
}


def new(now=None):
    return {'step': 'S0', 't0': now or time.time(), 'requirements': [], 'candidates': [], 'places': [], 'plan': [],
            'current': None, 'last': None, 'repeats': 0, 'edited': []}


def next_call(state):
    """The exact next call, the last line of every answer."""
    step = state['step']
    if step == 'D4':
        return 'NEXT: ' + FORMS['D4']
    if step == 'D3' and state['current'] is not None:
        pid = state['plan'][state['current']]['place']
        return (f'NEXT: call {CALL}["{pid}", "<the whole new function or class>"] or ["{pid}", "<first line number>", '
                f'"<last line number>", "<new lines>"], or ["skip"]')
    return f'NEXT: call {CALL}{FORMS[step]}'


def repeated(state, args):
    """True when args are the same as one of the last three calls' (a repeat, or a ping-pong A-B-A): the call is not
    run again."""
    key = [a.strip() for a in args]
    recent = state.setdefault('recent', [])
    if key in recent[-3:]:
        state['repeats'] += 1
        state['repeated_answer'] = state.setdefault('answers', {}).get(repr(key), '')
        return True
    recent.append(key)
    del recent[:-3]
    state['last'], state['repeats'] = [state['step'], key], 0
    return False


def remember(state, args, answer):
    """The first line of the answer to args, quoted when the same call is repeated."""
    answers = state.setdefault('answers', {})
    answers[repr([a.strip() for a in args])] = answer
    for k in list(answers)[:-6]:
        del answers[k]


def time_is_up(state, now=None):
    return (now or time.time()) - state['t0'] > EDIT_STOP


def start(state, requirements, candidates):
    state.update(requirements=requirements, candidates=candidates, step='D1')


def choose(state, places):
    state.update(places=places, plan=[], current=None, step='D2')


def back(state):
    """From D2 (or from a place that cannot be fixed) to D1, with the same candidates."""
    state.update(places=[], plan=[], current=None, step='D1')


def requirement_back(state, candidates):
    """From D4 to D1 with the candidates of an uncovered requirement."""
    state.update(candidates=candidates, places=[], plan=[], current=None, step='D1')


def set_plan(state, entries):
    """entries: [(place id, intent)] already checked against the places."""
    state['plan'] = [{'place': p, 'intent': i, 'status': 'todo', 'fails': 0, 'errors': []} for p, i in entries]
    state['current'], state['step'] = 0, 'D3'


def plan_entry(state, pid):
    for i, e in enumerate(state['plan']):
        if e['place'] == pid:
            return i
    return None


def target(state, pid):
    """The plan index an edit of place pid goes to; a listed place outside the plan is added to it."""
    i = plan_entry(state, pid)
    if i is None:
        state['plan'].append({'place': pid, 'intent': '(not in the plan)', 'status': 'todo', 'fails': 0,
                              'errors': []})
        i = len(state['plan']) - 1
    return i


def edit_done(state, i):
    """A verified (or unverifiable) edit of plan entry i: it is done; go to the next place still to do, or to D4."""
    e = state['plan'][i]
    e['status'] = 'done'
    if e['place'] not in state['edited']:
        state['edited'].append(e['place'])
    return advance(state)


def edit_failed(state, i, error):
    """A failed edit of plan entry i. Returns 'retry' (same place again) or 'skipped' (the place stays at its last
    verified state and the next place comes) after MAX_FAILS failures or the same error three times."""
    e = state['plan'][i]
    e['fails'] += 1
    e['errors'].append(error)
    if e['fails'] >= MAX_FAILS or e['errors'][-3:].count(error) >= 3:
        if e['status'] != 'done':                     # a done place keeps its verified edit
            e['status'] = 'skipped'
        advance(state)
        return 'skipped'
    state['current'] = i
    return 'retry'


def skip(state, i):
    """The model says plan entry i needs no change: it keeps its code and the next place comes."""
    if state['plan'][i]['status'] != 'done':          # a done place opened again keeps its edit
        state['plan'][i]['status'] = 'skipped'
    return advance(state)


def advance(state):
    """Moves to the first place still to do; when none is left, to D4 (or back to D1 when nothing was edited)."""
    for i, e in enumerate(state['plan']):
        if e['status'] == 'todo':
            state['current'], state['step'] = i, 'D3'
            return 'next'
    state['current'] = None
    if not state['edited']:
        back(state)
        return 'back'
    state['step'] = 'D4'
    return 'finish'
