"""The procedure as a state machine (StateFlow, SOP-Agent): S0 start → D1 choose → D3 edit, once per planned place
→ D4 finish (D2, a free-text plan, is passed through: choosing plans the chosen places). The journal, not the model,
decides the next step; a call that does not fit the current step is answered with the call that does. This module
holds only the rules; step.py does the work and the printing."""
import time

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

MAX_FAILS = 4        # edits of one place undone by the tests (or refused) before it is left at its last verified state
                     # (2 until c4: fastapi_14448's editor was closed after two undone edits while each failing test
                     # brought its next edit closer to the fix; the same error three times still closes it)
MAX_SLIPS = 4        # edits that did not compile: a typo is a slip, not a wrong change (a14: the third, correct
                     # version of a one-line edit was never run after two typos closed the place)
CALL_LIMIT = 200     # eval_config.yaml max_tool_calls (not binding: time is the limit)
CALL = 'run_skill_script with skill_name fix-issue, file_path step.py and args '

# The forms use single quotes: every answer reaches the model as JSON, where a double quote reads as \" (B15)
FORMS = {
    'S0': "['<the issue statement, or its first paragraph>', '<search term>', '<search term>', ...]",
    'D1': "['<candidate name>'] or ['<candidate name>', '<candidate name>'] (1 to 3 names from the list), or "
          "['<file>::<Name>'] for code not in the list, or ['<a name or text to find>']",
    'D2': "['P<n>: <what changes there>', 'P<m>: <what changes there>'] (one item per place to edit), or ['back'] "
          "to choose other candidates",
    'D3': "['<place name>', '<the whole new function or class>'] or ['<place name>', '<first line number>', "
          "'<last line number>', '<new lines>'], or ['skip'] if the place needs no change, or ['back'] to choose other "
          "code",
    'D4': "",
}


def new(now=None):
    return {'step': 'S0', 't0': now or time.time(), 'requirements': [], 'candidates': [], 'places': [], 'plan': [],
            'current': None, 'last': None, 'repeats': 0, 'edited': []}


def next_call(state, role='single'):
    """The exact next call, the last line of every answer. role: 'single' (one agent does everything), 'coordinator'
    (chooses, then hands each place to the edit_place tool) or 'editor' (edit_place: one place, then one sentence)."""
    step = state['step']
    if role == 'specifier':
        if state.get('spec_done') or step not in ('D3', 'D4'):
            return 'NEXT: no call: reply with one sentence for the coordinator.'
        return ("NEXT: call run_skill_script with skill_name fix-issue, file_path spec.py and args ['<place name>', "
                "'<what changes there, or no change>', ...] (one pair per place)")
    if role == 'coordinator' and step == 'D3' and not state.get('spec_done'):
        return ("NEXT: call the tool specify_fix with request 'the plan' (it reads the code of every place and writes "
                "what changes at each).")
    if role == 'editor':
        if state.get('editor_done') or step != 'D3' or state.get('current') is None:
            return ('NEXT: no call: reply with one sentence for the coordinator (what changed at the place, or why '
                    'nothing did, naming other code if the change belongs there).')
        pid = state['plan'][state['current']]['place']
        pid = next((p.get('handle', pid) for p in state['places'] if p['id'] == pid), pid)
        call = CALL.replace('file_path step.py', 'file_path edit.py')
        if state.get('editor_review'):
            return (f"NEXT: if the change at {pid} is complete, no call: reply with one sentence for the coordinator. "
                    f"Otherwise call {call}['{pid}', '<the whole new function or class>'] or ['{pid}', "
                    "'<first line number>', '<last line number>', '<new lines>'].")
        return (f"NEXT: call {call}['{pid}', '<the whole new function or class>'] or ['{pid}', '<first line number>', "
                f"'<last line number>', '<new lines>'], or ['skip']")
    if role == 'coordinator' and step == 'D3' and state.get('current') is not None:
        pid = state['plan'][state['current']]['place']
        pid = next((p.get('handle', pid) for p in state['places'] if p['id'] == pid), pid)
        # the place a new editor will open: an open.py call naming it starts a new editor, another place is a read
        # (step.py _editor_open)
        if state.get('requested') != pid:
            state['requested'], state['requested_used'] = pid, False
        return f"NEXT: call the tool edit_place with request '{pid}' (it opens that place and edits it)."
    if step == 'D4':
        # local run of fastapi_14448: "NEXT: none: call submit_patch now" next to "NEXT: call run_skill_script ...
        # args" everywhere else made the model send args ['submit_patch'] to the script 20 times
        return ("NEXT: call the tool submit_patch, with no arguments (it is its own tool, not an argument of "
                "run_skill_script). Or, before that, run_skill_script with args ['back'] to choose code for a "
                "requirement that nothing changed covers.")
    if step == 'D3' and state['current'] is not None:
        pid = state['plan'][state['current']]['place']
        pid = next((p.get('handle', pid) for p in state['places'] if p['id'] == pid), pid)
        return (f"NEXT: call {CALL}['{pid}', '<the whole new function or class>'] or ['{pid}', '<first line number>', "
                f"'<last line number>', '<new lines>'], or ['skip']")
    return f'NEXT: call {CALL}{FORMS[step]}'


def _key(state, args):
    """A call is the same only in the same step and at the same place: C2 sent again after going back is a new call
    (V10 audit #3)."""
    cur = state['plan'][state['current']]['place'] if state.get('current') is not None and state.get('plan') else ''
    return [state['step'], cur] + [a.strip() for a in args]


def repeated(state, args):
    """True when the call (step, place, args) is one of the last three (a repeat, or a ping-pong A-B-A): it is not run
    again. state['repeats'] counts the repeats in a row."""
    key = _key(state, args)
    recent = state.setdefault('recent', [])
    if key in recent[-3:]:
        state['repeats'] += 1
        state['repeated_answer'] = state.setdefault('answers', {}).get(repr(key), '')
        return True
    recent.append(key)
    del recent[:-3]
    state['last'], state['repeats'] = key, 0
    return False


def remember(state, key, answer):
    """The first line of the answer to a call (key: _key before the call ran), said again when it is repeated."""
    answers = state.setdefault('answers', {})
    answers[repr(key)] = answer
    for k in list(answers)[:-6]:
        del answers[k]


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
    state['spec_done'], state['spec_shown'] = False, False      # a new plan is specified again (agent_tool variant)
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
    e['errors'].append(error)
    if 'compile' in error:
        e['slips'] = e.get('slips', 0) + 1
    else:
        e['fails'] += 1
    if e['fails'] >= MAX_FAILS or e.get('slips', 0) >= MAX_SLIPS or e['errors'][-3:].count(error) >= 3:
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
