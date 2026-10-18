"""(To the model: call the script your instructions name with run_skill_script; reading this source does not help.)

The stages build (docs/design_stages.md): one responsibility per agent, driven by the list of requirements.

  0 prepare     script    the index, the impact table, the existing tests' map and baseline (lazily, step.py's modules)
  1 requirements agent    names from the statement -> their signatures; then 1-6 requirements quoting the statement
  2 reproduce   agent     one test of one requirement, kept only when it fails on the original code (optional)
  3 locate      agent     per requirement: where its behaviour is decided (1-3 names)
  4 scope       agent     which related places must change too; the script orders the plan
  5 specify     agent     per place: one sentence of what changes there
  6 edit        agent     per place: the whole new definition (checked, undone when it breaks existing tests)
  7 verify      script    the existing tests on the whole patch, the reproduction test if there is one
  8 review      agent     per requirement: done, or the place that is wrong or missing
  9 submit      script    the last good state; the coordinator calls submit_patch

The coordinator only routes: it calls route.py, which answers with the next tool and its request. Each agent calls
its own script; the answer of every call ends with NEXT. The state is one JSON document (_state 'stages'); the
existing helpers of step.py (ranking, search, views, whole-definition edits) and _tests.py (checks) are reused."""
import ast
import difflib
import json
import os
import re
import subprocess
import sys
import time

import _args
import _code
import _impact
import _rank
import _repo
import _state
import _statement
import _tests
import step

MODULE = step.MODULE
MAX_REQS = 6
MAX_ASKS = 2            # a stage's tool is asked this many times for one target; then the script moves on
MAX_ROUNDS = 2          # review rounds (F6)
MAX_FAILS = 2           # undone edits of one place
MAX_EDITS = 4           # edits of one place (applied: kept or undone by the checks)
MAX_REFUSED = 6         # sends of one place refused before anything was applied
MAX_SENTENCE = 1500
LONG_EDIT = 80          # a place longer than this is edited by replacing lines, not sent whole
BIG_CLASS = 120         # a class place longer than this is split into the members that change
# then the work goes to submit; eval_config.yaml stops the agent at 5 min (the harness then takes the diff), so the
# route submits with 30 s left
BUDGET_S = int(os.environ.get('SWE_STAGE_BUDGET', str(4 * 60 + 30)))
# targets one agent call handles in a row (V22: the ceremony of an agent call is about 21 s); specify keeps 3 (V23), so
# that its views (up to 12,000 characters each) stay under the harness's compaction at 14,336 tokens
BATCH = {'locate': 6, 'review': 6, 'specify': 3}
REPRO_LEFT_S = 360    # V22: reproduce took 30-225 s; it runs only when this much time is left
REVIEW_LEFT_S = 75    # a review agent call took 15-55 s
REPRO_NAME = 'test_swe_reproduce_issue.py'
REPRO_TIMEOUT = 40
ANSWER_CHARS = 12000   # gm11: the specify view with the whole place (150 lines) and its deciding calls
# The per-edit test check (stage 6), off for now at the user's request (2026-10-14): in requests it cost about 70 s per
# edit, and the first edit of a task built the call map with a whole-suite run (up to 150 s). SWE_EDIT_TESTS=1 turns
# it back on. The edit still has to compile (the whole-definition and replace forms refuse code that does not).
EDIT_TESTS = os.environ.get('SWE_EDIT_TESTS', '0') == '1'
# Stage 7 (verify), off for now at the user's request (2026-10-14): it ran the selected existing tests on the whole
# patch, built the whole-suite call map (up to 150 s) and ran the reproduction test again. SWE_VERIFY=1 turns it on.
VERIFY = os.environ.get('SWE_VERIFY', '0') == '1'

DONE = 'NEXT: no call: reply with one sentence for the coordinator.'

# ---------------------------------------------------------------------------- the call forms (one source)
# Every NEXT line that calls a script is built here, from these forms: an answer cannot offer a form its script
# refuses, or leave out one it takes. vf7/vf8 lab: two hand-written copies disagreed with the rules (a bare
# ['no change'] offered where it needs its line; a refusal without the three-part form, sent again three times) (B12).
FORM = {
    'names': "['<name>', '<name>', ...]",
    'pairs': "['<sentence from the issue>', '<what must happen>', ...]",
    'test': "['<the test>']",
    'locate': "['<name>'] (1 to 3 names from the list)",
    'find': "['<a name or text to find>']",
    'scope': "['<name>', ...] (the related code that must change too)",
    'none': "['none']",
    'finding': "['<the lines that make it fail>', '<what they do now>', '<what they must do instead>']",
    'sentence': "['<one sentence: what changes here>']",
    'no change': "['no change']",
    'no change + line': "['no change', '<the line that already does it>']",
    'missing': "['missing', '<name of other code>', '<what must change there>']",
    'replace': "['replace', '<old lines>', '<new lines>']",
    'replace groups': "['replace', '<old lines>', '<new lines>', ...] (every group of lines in one call)",
    'missing edit': "['missing', '<name of other code this change needs>']",
    'done': "['done']",
    'not done': "['not done', '<the place that is wrong or missing>', '<what is missing there>']",
    'cannot tell': "['cannot tell']",
    'next': "['next']",
}


def next_call(script, *forms, sep=' or '):
    """The NEXT line that calls a script with one of the forms (names in FORM, or a form written out)."""
    return (f'NEXT: call run_skill_script with skill_name fix-issue, file_path {script} and args '
            + sep.join(FORM.get(f, f) for f in forms))


def _specify_forms(p, may_name=True):
    """The forms the specify script takes for this place now: the three-part finding for a place it has not judged
    yet, a sentence once its finding is saved (or for new code, or an edited place), 'no change' with its line where
    that line is checked, and 'missing' unless the place was itself named by another specifier."""
    checked = DIAGNOSE and not p.get('new') and not p.get('edited')
    forms = ['finding' if _diagnosing(p) else 'sentence', 'no change + line' if checked else 'no change']
    return forms + (['missing'] if may_name else [])
FAST = False            # the fast build (docs/design_fast.md): stage agents in a pipeline, workers in parallel


# ------------------------------------------------------------------------------------------------------- state

def _new():
    return {'t0': time.time(), 'paused0': _paused(), 'names': None, 'reqs': [], 'repro': None, 'repro_tries': 0, 'repro_done': False,
            'located': {}, 'cands': {}, 'scope_done': False, 'plan': [], 'asked': {}, 'current': None,
            'round': 0, 'reviews': {}, 'verified': None, 'regressions': [], 'last_good': None, 'missing': [],
            'submitted': False, 'log': []}


def _paused():
    """Seconds the run was held between model calls by tools/step_gate.py (local step-by-step runs only; 0 elsewhere):
    left out of the clock, so that the model sees the time it would have seen without the pauses."""
    try:
        with open(os.environ['SWE_PAUSED_FILE'], encoding='utf-8') as fh:
            return float(fh.read().strip() or 0)
    except (KeyError, OSError, ValueError):
        return 0.0


def _elapsed(st):
    return int(time.time() - st['t0'] - (_paused() - st.get('paused0', 0.0)))


def _over_budget(st):
    return _elapsed(st) > BUDGET_S


def _left(st):
    return BUDGET_S - _elapsed(st)


def _place_by(st, text):
    return step._match(st['plan'], text, fuzzy=False) if st['plan'] else None


def _key(p):
    return f'{p["rel"]}::{p["name"]}'


def _handles(st):
    step._set_handles(st['plan'])


def _req(st, rid):
    return next((r for r in st['reqs'] if r['id'] == rid), None)


def _req_line(r):
    q = f' (from the issue: {r["quote"][:200]})' if r.get('quote') else ''      # B15: no quotes of our own
    return f'{r["id"]}: {r["what"]}{q}'


def _focus(reqs):
    out = {}
    for r in reqs:
        for term in _statement.terms(r['what'] + ' ' + r.get('quote', '')):
            for ident in re.findall(r'[A-Za-z_]\w*', term):
                for t in _rank.tokens(ident):
                    out[t] = max(out.get(t, 0), 2)
    return out


def _progress(root, st):
    rows = []
    try:
        rows = step._status.summary(root)
    except Exception:
        pass
    held = ('The patch changes ' + '; '.join(f'{n} in {r} (+{a} -{d})' for r, n, a, d in rows[:6]) + '.') if rows \
        else 'The patch is empty so far.'
    plan = ''
    if st['plan']:
        word = {'todo': 'to edit', 'done': 'edited', 'nochange': 'no change', 'failed': 'failed', None: 'to specify'}
        plan = ' The plan: ' + ', '.join(f'{p["handle"]} ({word.get(p.get("status"), p.get("status"))})'
                                         for p in st['plan'][:10]) + '.'
    return f'{held}{plan} Time used: {_elapsed(st) // 60} min {_elapsed(st) % 60} s of {BUDGET_S // 60} min' + (f' {BUDGET_S % 60} s' if BUDGET_S % 60 else '') + '.'  # B10


# ------------------------------------------------------------------------------------------------------ routing

def _reset_asks(st, stage, p):
    """Every ask count of a place for a stage starts again. vf3 audit (fastapi_14361): after 'missing' the reset used
    another key than the fast build's claims, the third ask was refused, and the place got the requirement's text as
    its sentence without being specified again."""
    for k in [k for k in st['asked'] if k.startswith(f'{stage} {_key(p)} ')]:
        st['asked'].pop(k, None)


def _ask(st, key):
    """True while the tool for key may be asked again (MAX_ASKS); counts the ask."""
    n = st['asked'].get(key, 0)
    if n >= MAX_ASKS:
        return False
    st['asked'][key] = n + 1
    return True


def _next_tool(root, st):
    """(tool, request) of the next agent, or ('submit_patch', None). Script stages (verify, the fallbacks of an agent
    that did not deliver) run here."""
    if st['submitted']:
        return 'done', None
    if _over_budget(st):
        return _submit(root, st)
    # 1 requirements
    if not st['reqs']:
        if _ask(st, 'requirements'):
            st['current'] = ('requirements', None)
            return 'requirements', 'the issue'
        st['reqs'] = [{'id': 'R1', 'quote': '', 'what': 'Fix the issue as the statement describes.',
                       'names': list(st.get('names') or []), 'new': []}]
        _save_statement(st)
    # 2 reproduce (optional): only for a requirement whose issue sentence is concrete
    if not st['repro_done'] and not any(r.get('concrete') for r in st['reqs']):
        st['repro_done'] = True
    if not st['repro_done'] and _left(st) < REPRO_LEFT_S:
        # V22 (Kaggle, 5 min): the optional reproduction left no time for the edits
        st['repro_done'] = True
    if not st['repro_done']:
        if _ask(st, 'reproduce'):
            st['current'] = ('reproduce', _repro_req(root, st)['id'])
            return 'reproduce', st['current'][1]
        st['repro_done'] = True
    # 3 locate, per requirement
    for r in st['reqs']:
        if r['id'] not in st['located']:
            if _ask(st, 'locate ' + r['id']):
                st['current'] = ('locate', r['id'])
                return 'locate', r['id']
            st['located'][r['id']] = _fallback_locate(root, st, r)
    # 4 scope
    if not st['scope_done']:
        if not any(st['located'].values()):
            return _submit(root, st)
        # docs/time_proposals (C1, F1): the scope agent answered 'none' in 6 of 8 decisions and the places it or the
        # script added were never edited; the plan is the located places and their identical copies, and the related
        # code is shown to each specifier, who adds what must change too with 'missing'
        _make_plan(root, st, [])
    _add_missing(root, st)
    # docs/time_proposals (D1): the place chosen first for the first requirement is specified and edited before the
    # others, so that a patch exists early (V22-V23: the first diff came at about 240 s, after every place was specified)
    main = _main_place(st)
    if main is not None and main.get('sentence') is None and not main.get('copy_of'):
        if _ask(st, f'specify {_key(main)} {st["round"]} {main.get("respecs", 0)}'):
            st['current'] = ('specify', main['handle'])
            return 'specify', main['handle']
    if main is not None and main.get('status') == 'todo':
        if _try_literal(root, st, main):
            pass
        elif _ask(st, f'edit {_key(main)} {st["round"]} {main.get("respecs", 0)}'):
            st['current'] = ('edit', main['handle'])
            main['opened'] = False
            return 'edit', main['handle']
    # 5 specify, per place
    for p in st['plan']:
        if p.get('sentence') is None:
            q = _copy_of(root, st, p)
            if q is not None and q.get('sentence') is not None and q.get('status') != 'failed' and not p.get('note'):
                # V23 (httpx_3672): the same method in httpx/ and ahttpx/, specified and edited one by one; a copy whose
                # original code is the same takes the other's sentence
                p['sentence'], p['status'], p['copy_of'] = q['sentence'], ('nochange' if q.get('status') == 'nochange'
                                                                           else 'todo'), _key(q)
                continue
            if _ask(st, f'specify {_key(p)} {st["round"]} {p.get("respecs", 0)}'):
                st['current'] = ('specify', p['handle'])
                return 'specify', p['handle']
            p['sentence'] = '; '.join(_req(st, rid)['what'] for rid in p['reqs'] if _req(st, rid)) or 'no change'
            p['status'] = 'todo'
    # 6 edit, per place
    for p in st['plan']:
        if p.get('status') == 'todo':
            q = _copy_of(root, st, p)
            if q is not None and q.get('status') == 'done' and q.get('sentence') == p.get('sentence') \
                    and _apply_copy(root, st, p, q):
                continue
            if _try_literal(root, st, p):
                continue
            if _ask(st, f'edit {_key(p)} {st["round"]} {p.get("respecs", 0)}'):
                st['current'] = ('edit', p['handle'])
                p['opened'] = False
                return 'edit', p['handle']
            p['status'] = 'failed'
    # 7 verify (script)
    _verify(root, st)
    if st.get('recheck') and not any(p.get('sentence') is None or p.get('status') == 'todo' for p in st['plan']):
        st['round'] += 1
        for rid in st['recheck']:
            st['reviews'].pop(rid, None)
        st['recheck'] = []
    # 8 review, per requirement
    for r in st['reqs']:
        if r['id'] not in st['reviews'] and not VERIFY and st['repro'] and st['repro'].get('rid') == r['id'] \
                and _patch_files(root):
            # cost (the table after gk15): the review agent of a requirement whose own test passes on the patch and
            # whose existing tests still pass only confirmed it (2-3 model calls); the test decides it here
            if _rerun_repro(root, st) and (_regression_check(root, st) or {}).get('verdict') != 'BROKEN':
                st['reviews'][r['id']] = {'verdict': 'done', 'by': 'the reproduction test passes'}
                continue
        if r['id'] not in st['reviews']:
            if st['repro'] and st['repro'].get('rid') == r['id'] and st['repro'].get('now_passes'):
                st['reviews'][r['id']] = {'verdict': 'done', 'by': 'the reproduction test passes'}
                continue
            if _left(st) < REVIEW_LEFT_S:
                # V22: no time for a review agent call; the script's own checks (verify, the kept test) stand
                st['reviews'][r['id']] = {'verdict': 'not reviewed (time)'}
                continue
            if _ask(st, f'review {r["id"]} {st["round"]}'):
                st['current'] = ('review', r['id'])
                return 'review', r['id']
            st['reviews'][r['id']] = {'verdict': 'cannot tell'}
    # a round back for what the review or the verification found
    todo = [p for p in st['plan'] if p.get('sentence') is None or p.get('status') == 'todo']
    if todo and st['round'] < MAX_ROUNDS:
        st['round'] += 1
        st['reviews'] = {}
        return _next_tool(root, st)
    return _submit(root, st)


def route(root, st, args):
    _tests.record_preexisting(root)
    tool, req = _next_tool(root, st)
    head = _progress(root, st)
    if tool == 'done':
        return head + '\n\nThe patch is submitted.\n\n' + DONE.replace('the coordinator', 'the user')
    if tool == 'submit_patch':
        return head + '\n\nThe work is finished.\n\nNEXT: call the tool submit_patch with no arguments, then write one ' \
                      'sentence about the change.'
    st['log'].append([_elapsed(st), tool, req])
    # gm7 (Gemma): "when it replies, call route.py" contradicted the line the agent returns (checklist B12); it called
    # route.py instead of that line 4 times out of 9
    # B15: no double quotes of our own (every answer reaches the model as JSON, where they read as \")
    return f"{head}\n\nNEXT: call the tool {tool} with request '{req}'. If it replies with a line that ends with " \
           "Next: call the tool ..., make that call next; if it replies with anything else, call run_skill_script " \
           "with skill_name fix-issue, file_path route.py and args ['next']."


def _submit(root, st):
    _drop_repro(root)
    good = st.get('last_good')
    if good and good['score'] > _score(root, st):
        for rel, text in good['files'].items():
            if text is None:
                if os.path.exists(os.path.join(root, rel)):
                    os.remove(os.path.join(root, rel))
            else:
                _repo.write_lines(root, rel, text.splitlines(keepends=True))
    st['submitted'] = True
    return 'submit_patch', None


# ------------------------------------------------------------------------------------------- 1 requirements

COMMAND = re.compile(r'^\s*(?:grep|rg|find|ls|cat|cd|sed|awk|git|python3?|pytest|pip|head|tail|echo)\s')


def _looks_like_requirements(items):
    return len(items) >= 2 and len(items) % 2 == 0 and all(len(x.split()) >= 3 for x in items[1::2])


def _copied(r):
    """Whether a requirement's 'what must happen' only repeats the issue's sentence."""
    a = re.sub(r'\W+', ' ', (r.get('what') or '').lower()).strip()
    b = re.sub(r'\W+', ' ', (r.get('quote') or '').lower()).strip()
    return bool(a) and bool(b) and difflib.SequenceMatcher(None, a, b).ratio() > 0.9


def _sentence(a):
    """A sentence as sent, without the quotes that wrap it whole. _args.clean strips every leading backtick, and
    '`solve_dependencies` calls `inspect.unwrap`' lost its first one (local run h2, fastapi_9555): the backticks no
    longer paired, and the words between them were read as new code ('decide', 'solves')."""
    a = a.strip().rstrip(',').strip()
    for w in '`"\'':
        if len(a) > 1 and a[0] == w and a[-1] == w and a.count(w) == 2:
            a = a[1:-1].strip()
    return a


def _library(root, name):
    """A name of library code (inspect.unwrap, functools.cache) or a dunder attribute (__wrapped__): not new code
    of the repository."""
    head = name.split('.')[0]
    if head.startswith('__') and head.endswith('__'):
        return True
    if '.' not in name:
        return False
    try:
        import importlib.util
        spec = importlib.util.find_spec(head)
    except (ImportError, ValueError):
        return False
    # the repository's own package is not library code
    origin = os.path.realpath(getattr(spec, 'origin', None) or '') if spec else ''
    return bool(spec) and not origin.startswith(os.path.realpath(root) + os.sep)


def _library_modules(root, top=8):
    """The third-party modules the repository's code imports most (pydantic, starlette for fastapi), once per task."""
    cached = _state.load('library_modules')
    if cached is not None:
        return cached
    import collections
    import _example
    own = set(_example._packages(root))
    count = collections.Counter()
    for rel in _repo.iter_py(root, docs=False):
        tree = _code.parse(_repo.read_text(root, rel))
        for node in ast.walk(tree) if tree is not None else ():
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
            for m in mods:
                head = m.split('.')[0]
                if head not in own and head not in sys.stdlib_module_names and not head.startswith('_'):
                    count[head] += 1
    out = [m for m, _ in count.most_common(top)]
    _state.save('library_modules', out)
    return out


def _library_homes(root, name):
    """'pydantic.Tag' for a name a library the repository uses defines. gm11 (fastapi_12942, the 31B): the lookup
    showed only fastapi.openapi.models.Tag, and the reproduction test defined its own class Tag instead of importing
    pydantic's; both tries failed for that reason."""
    short = name.split('.')[-1]
    if not re.fullmatch(r'[A-Za-z_]\w*', short):
        return []
    out = []
    import importlib
    for mod in _library_modules(root):
        try:
            m = importlib.import_module(mod)
        except Exception:
            continue
        if getattr(m, short, None) is not None and not short.startswith('_'):
            out.append(f'{mod}.{short}')
    return out[:2]


def requirements(root, st, args):
    items = [_args.clean(a) if len(a) < 300 else a.strip() for a in args if a and a.strip()]
    if st['names'] is not None or _looks_like_requirements(items):
        items = [_sentence(a) for a in args if a and a.strip()]
    form = ("Send the requirements: ['<a sentence copied from the issue>', '<what must happen: the situation and the "
            "expected result, with the exact names>', ...], one pair per requirement, 1 to 6 pairs.")
    lookup = not _looks_like_requirements(items) and (st['names'] is None or (
        items and all(len(x.split()) <= 4 for x in items) and not any(COMMAND.match(x) for x in items)
        and st.get('lookups', 0) < 3))
    if lookup and st['names'] is not None and len(items) % 2 == 0 and items == st.get('last_lookup'):
        # g11 (rich_4076, whose whole issue is "preserve newlines"): ['preserve newlines', 'preserve newlines'] was
        # read as two names twice and saved the third time; a call sent again unchanged after its lookup answer is
        # the agent's requirements
        lookup = False
        items = [_sentence(a) for a in args if a and a.strip()]
    st['last_lookup'] = items if lookup else None
    if lookup:
        # vf3 audit (rich_3180, rich_3772): a second names call (['wrap', 'fold']) was saved as requirement R1 because
        # it came second; a call is read by its content: short names are a lookup, pairs with sentences are requirements
        st['lookups'] = st.get('lookups', 0) + 1
        if any(COMMAND.match(x) for x in items):
            items = []          # vf3: a shell command is not a name (req.py runs no commands)
        if not items:
            return ('Send the names the issue mentions (functions, classes, parameters, files), one per item. req.py '
                    'runs no commands: it shows what the repository defines for those names.\n\n'
                    + next_call('req.py', 'names'))
        table = _impact.Table(root, docs=False)
        lines, names, index = [], [], None
        for raw in items[:20]:
            name = re.sub(r'\(.*$', '', raw).strip('`.: ')
            if not name:
                continue
            names.append(name)
            found = table.find(name.split('.')[-1]) if re.fullmatch(r'[A-Za-z_][\w.]*', name) else []
            found = [(r, s) for r, s in found if s.name == name or s.name.endswith('.' + name) or
                     s.name.endswith('.' + name.split('.')[-1])]
            exact = [(r, s) for r, s in found if s.name == name or s.name.endswith('.' + name)]
            if exact and '.' in name:
                found = exact           # gk11: 'Session.send' also showed BaseAdapter.send
            shown = []
            for rel, s in found:
                sig = _sig(s)
                if sig not in shown and len(shown) < 2:
                    shown.append(sig)
                    lines.append(f'  {name}: {sig}  ({rel}, {s.kind} {s.name})')
            homes = _library_homes(root, name)
            if homes:
                st.setdefault('lib_names', []).extend(h for h in homes if h not in st.get('lib_names', []))
            if found and homes:
                lines.append(f'  {name}: also in the library: ' + ', '.join(homes) + ' (an example of the issue may '
                             'import it from there)')
            mod = _module_of(root, table, name) if not found and not homes else None
            if mod:
                # vf3 audit: a file path or module was called "a parameter, attribute or variable"
                rel, defs = mod
                lines.append(f'  {name}: the module {rel}; it defines ' + (', '.join(defs[:12]) or 'no function or class'))
                continue
            owners = _param_owners(table, name) if not found and not homes else []
            assigned = step.assignment(root, table, name) if not found and not homes and not owners and \
                re.fullmatch(r'[A-Za-z_]\w*', name) else None
            if assigned:
                # the template CONSOLE_HTML_FORMAT (rich_3043) read as "not a function or class"
                lines.append(f'  {name}: a module-level assignment in {assigned["rel"]} (lines {assigned["start"]}-'
                             f'{assigned["end"]})')
            elif owners:
                # hk30 (rich_4075): 'end' was only "a parameter, attribute or variable", not tied to Console.print, and
                # the requirement named no code (W6, W3): the functions that take it as a parameter, public first
                more = f' and {len(owners) - 3} more' if len(owners) > 3 else ''
                lines.append(f'  {name}: a parameter of ' + ', '.join(f'{q} ({r})' for r, q in owners[:3]) + more)
            elif not found:
                index = index or _rank.Index(root)
                lines.append(f'  {name}: ' + (f'library code: {", ".join(homes)}' if homes else
                                              'not a function or class; the code uses this name (a parameter, '
                                              'attribute or variable)' if name.split('.')[-1] in index.names else
                                              "not in the repository (code of the issue's own example, new code the "
                                              'issue asks for, or library code)'))
        st['names'] = list(dict.fromkeys((st.get('names') or []) + names))
        # g11: the same pair sent twice got the same lookup answer; the answer says how the call was read (B5)
        read = ('This call was read as names to look up: each item is short. Requirements are saved when each '
                "'what must happen' is a sentence of 3 or more words, or when this same call is sent again.\n\n"
                if len(items) % 2 == 0 and st.get('lookups', 0) > 1 else '')
        return ('What the repository defines for those names:\n' + '\n'.join(lines) + '\n\n' + read + form + '\n\n'
                + next_call('req.py', 'pairs'))
    if any(COMMAND.match(x) for x in items) or (len(items) == 1 and len(items[0].split()) < 6):
        # vf3 (Gemma, requests_7205 and 3 more): req.py was called with 'grep -r "netrc" src/requests', the command was
        # saved as R1, and every later try to send the requirements was told the part was finished
        return ('Not saved: req.py runs no commands and does not search the code (the other agents find the code); '
                'it saves the requirements, as pairs. ' + form + '\n\n' + next_call('req.py', 'pairs'))
    if len(items) == 1:
        items = ['', items[0]]
    if len(items) % 2:
        return (f'An odd number of items was sent ({len(items)}): every requirement is a pair. {form}\n\n'
                + next_call('req.py', 'pairs'))
    index = _rank.Index(root)
    reqs = []
    for i in range(0, min(len(items), 2 * MAX_REQS), 2):
        quote, what = items[i].strip(), items[i + 1].strip()
        if not what:
            continue
        names = [n for n in _statement.code_names(what + ' ' + quote)]
        new = [n for n in names if n.split('.')[-1] not in index.names and not _library(root, n)]
        # concrete: the issue's own sentence holds code, a value or an example (backticks, quotes, digits, calls);
        # only such a requirement gets a reproduction test (s-st2, rich_3521: 'Segment.split_cells fix' became an
        # invented case of combining marks, and the test and the fix chased it)
        # gm11d (the 31B, fastapi_12942): both quotes were prose, but the requirements named `Dessert`, a name of the
        # issue's own example (sent first, defined neither in the repository nor in a library): concrete too
        example_names = set(st.get('names') or [])
        concrete = bool(re.search(r'`[^`]+`|"[^"]+"|\'[^\']+\'|\d|\w\(', quote)) or any(n in example_names for n in new)
        reqs.append({'id': f'R{len(reqs) + 1}', 'quote': quote, 'what': what[:600], 'names': names, 'new': new,
                     'concrete': concrete})
    if not reqs:
        return 'No requirement was read. ' + form
    dropped = []
    if len(reqs) > 1:
        # gk11, gm8: a requirement whose "what must happen" repeats the issue's sentence states no situation and no
        # result; locate then got nonsense candidates and review said "done" to anything (weak point 22)
        keep = [r for r in reqs if not _copied(r)]
        if keep:
            dropped = [r for r in reqs if r not in keep]
            reqs = keep
            for i, r in enumerate(reqs):
                r['id'] = f'R{i + 1}'
    st['reqs'] = reqs
    _save_statement(st)
    listed = '\n'.join('  ' + _req_line(r) + (f' [new: {", ".join(r["new"])}]' if r['new'] else '') for r in reqs)
    note = ('\nLeft out, because what must happen repeats the issue\'s sentence (no situation and result): ' +
            '; '.join(f'"{r["what"][:80]}"' for r in dropped)) if dropped else ''
    return f'Saved {len(reqs)} requirements:\n{listed}{note}\n\n{DONE}'


def _module_of(root, table, name):
    """(rel, top-level names) when name is a file of the repository or a dotted module path, else None."""
    cand = [name] if name.endswith('.py') or '/' in name else []
    if re.fullmatch(r'[A-Za-z_][\w.]*', name) and '.' in name:
        base = name.replace('.', '/')
        cand += [base + '.py', base + '/__init__.py']
    for c in cand:
        rel = next((r for r in table.syms if r == c or r.endswith('/' + c)), None)
        if rel:
            tree = _code.parse(_repo.read_text(root, rel))
            assigned = [t.id for n in (tree.body if tree else []) if isinstance(n, (ast.Assign, ast.AnnAssign))
                        for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)]
            return rel, [x.name for x in table.syms[rel] if '.' not in x.name] + assigned[:6]
    return None


def _param_owners(table, name):
    """[(rel, qualified name)] of the functions that take name as a parameter, public ones and package code first."""
    if not re.fullmatch(r'[A-Za-z_]\w*', name or ''):
        return []
    out = []
    for rel, syms in table.syms.items():
        for sym in syms:
            if sym.kind == 'class' or not hasattr(sym.node, 'args'):
                continue
            a = sym.node.args
            params = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs} | {x.arg for x in (a.vararg, a.kwarg) if x}
            if name in params:
                out.append((rel, sym.name))
    # the most called first (the public entry points an issue talks about): rich's 'end' is in 16 functions
    out.sort(key=lambda x: (any(part.startswith('_') and not part.startswith('__') for part in x[1].split('.')),
                            _repo.is_doc_path(x[0]), -len(table.calls.get(x[1].split('.')[-1], [])), x[0], x[1]))
    return out


def _sig(sym, limit=300):
    sig = step.DOC_ARG.sub('', _code.signature(sym))
    sig = re.sub(r'Annotated\[(.*?),\s*\]', r'\1', sig)
    return sig if len(sig) <= limit else sig[:limit - 3] + '...'


def _save_statement(st):
    text = '\n'.join((r.get('quote') or '') + '\n' + r['what'] for r in st['reqs'])
    terms = list(st.get('names') or []) + [n for r in st['reqs'] for n in r['names']]
    _state.save('statement', {'text': text, 'terms': terms})


# ------------------------------------------------------------------------------------------- 2 reproduce

def _repro_req(root, st):
    index = _rank.Index(root)
    for r in [r for r in st['reqs'] if r.get('concrete')] + st['reqs']:
        if any(n.split('.')[-1] in index.names for n in r['names']):
            return r
    return st['reqs'][0]


def _repro_path(root, template):
    base = os.path.dirname(template) if template else ('tests' if os.path.isdir(os.path.join(root, 'tests')) else '')
    return os.path.join(base, REPRO_NAME) if base else REPRO_NAME


def _drop_repro(root):
    for dirpath, _, files in os.walk(root):
        if '.git' in dirpath:
            continue
        if REPRO_NAME in files:
            try:
                os.remove(os.path.join(dirpath, REPRO_NAME))
            except OSError:
                pass


def _template(root, r):
    table = _impact.Table(root, docs=False)
    for n in r['names']:
        for rel, s in table.find(n.split('.')[-1])[:2]:
            ex = _tests.example(root, rel, s.name)
            if ex:
                return ex
    return _word_template(root, (r.get('quote') or '') + ' ' + r['what'])


def _import_lines(src):
    """The file's top-level imports, each whole on one line (a parenthesized import spans lines in the file)."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return [l.rstrip() for l in src.splitlines() if re.match(r'(from\s+\S+\s+import|import)\s', l)]
    return [ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]


def _word_template(root, text, max_lines=14):
    """(test file, lines) of a short existing test whose text shares the most words with the requirement, when the
    requirement names no code of the repository. gm5 (fastapi_14583, Gemma): with no names, the view showed no test,
    and the model wrote "Since I don't know the repository name" and tested the pydantic library twice."""
    words = {w.lower() for w in re.findall(r'[A-Za-z_][A-Za-z0-9_]{3,}', text)} - {
        'when', 'with', 'that', 'this', 'from', 'using', 'should', 'must', 'issue', 'issues', 'into', 'have', 'does'}
    if not words:
        return None
    best = None
    for rel in _repo.iter_py(root, tests=True):
        if not _repo.is_test_path(rel) or os.path.basename(rel) == 'conftest.py':
            continue
        src = _repo.read_text(root, rel)
        low = src.lower()
        if not any(w in low for w in words):
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            continue
        lines = src.splitlines()
        for f in tree.body:
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith('test'):
                body = lines[f.lineno - 1:f.end_lineno]
                if len(body) > max_lines:
                    continue
                score = sum(1 for w in words if w in '\n'.join(body).lower() or w in rel.lower())
                key = (score, -len(body))
                if score >= 2 and (best is None or key > best[0]):   # gk11: one word ('response') gave test_hooks
                    best = (key, rel, body)
    return (best[1], best[2]) if best else None


def _signatures(root, names, limit=6):
    table = _impact.Table(root, docs=False)
    out = []
    for n in names:
        for rel, s in table.find(n.split('.')[-1])[:1]:
            line = f'  {s.name}: {_sig(s)}  ({rel})'
            if line not in out:             # gk10: 'FastAPI', 'FastAPI(on_startup=...)' gave one signature three times
                out.append(line)
        if len(out) >= limit:
            break
    return out


def _run_repro(root, rel, code):
    """Writes the test, runs it, removes it: (outcomes {id: outcome}, messages {id: text}, functions it calls, output)."""
    path = os.path.join(root, rel)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(code if code.endswith('\n') else code + '\n')
    try:
        res = _tests.run(root, [[rel]], timeout=REPRO_TIMEOUT, callmap=True, stubs=tuple(_state.load('stubs', [])))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    mine = {k: v for k, v in res['outcomes'].items() if k.split('::')[0].endswith(REPRO_NAME)}
    msgs = {k: '\n'.join(x for x in res['msgs'].get(k, ('', '')) if x) for k in mine}
    if not mine and res['timeouts']:
        # rich_3052: a prompt that never gets a valid answer reads its stream forever
        mine = {f'{rel}::(the test)': 'hangs'}
        msgs = {f'{rel}::(the test)': f'the test did not finish within {REPRO_TIMEOUT} s (it waits or loops)'}
    calls = sorted(k for k, ids in res['calls'].items() if any(i in mine for i in ids)
                   and k.split('::')[0].endswith('.py') and not _repo.is_test_path(k.split('::')[0])
                   and os.path.isfile(os.path.join(root, k.split('::')[0])))
    global LAST_FRAMES
    LAST_FRAMES = _repo_frames(root, [f for k in mine for f in res.get('frames', {}).get(k, [])])
    return mine, msgs, _function_calls(root, calls), res['output']


LAST_FRAMES = []     # the repository frames of the last _run_repro's failure, innermost last


def _repo_frames(root, frames):
    """'path:line' traceback entries inside the repository's own code (no tests, no libraries), as (rel, line)."""
    out = []
    real = os.path.realpath(root)
    for f in frames:
        path, _, line = f.rpartition(':')
        if not line.isdigit():
            continue
        full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
        if not full.startswith(real + os.sep):
            continue
        rel = os.path.relpath(full, real)
        if rel.endswith('.py') and not _repo.is_test_path(rel) and not rel.endswith(REPRO_NAME):
            out.append((rel, int(line)))
    return out


def _function_calls(root, calls):
    """The calls that are functions or methods of the repository. Importing a module runs its top-level code and its
    class bodies ('<module>', '<genexpr>', 'GenerateJsonSchema'), which shows nothing about a behaviour: gm6 and gk6
    (fastapi_14583) kept tests that only imported or reloaded fastapi._compat.v1, and locate was then pointed at
    top-level code."""
    table = _impact.Table(root, docs=False)
    out = []
    for k in calls:
        rel, _, qual = k.partition('::')
        qual = qual.replace('.<locals>', '')               # nested: 'get_websocket_app.<locals>.app'
        sym = table.get(rel, qual)
        if sym is not None and sym.kind == 'def':
            # stored by the table's name, so that locate marks the nested candidate (gm9/gk9: the test ran
            # FastAPI.setup.<locals>.openapi and the candidate FastAPI.setup.openapi had no mark)
            out.append(f'{rel}::{qual}')
    return out


BAD_REASON = re.compile(r"SyntaxError|IndentationError|fixture '[\w]+' not found|ModuleNotFoundError: No module named "
                        r"'(?!{pkg})")


def reproduce(root, st, args):
    rid = (st['current'] or (None, None))[1]
    r = _req(st, rid) or _repro_req(root, st)
    items = [a for a in args if a and a.strip()]
    first = _args.clean(items[0]).lower() if items else ''
    form = ("Send the test: ['<a whole pytest test function, with the imports it needs above it>'], or ['skip'] if "
            "this behaviour cannot be shown by a test.")
    nxt = next_call('repro.py', 'test')
    if not items or first in ('open', 'start', 'the issue', rid.lower() if rid else 'r1'):
        tpl = _template(root, r)
        sig = _signatures(root, r['names'])
        view = [f'Write one test that shows this requirement is not met yet (it must fail on the code as it is now, '
                f'and pass once the issue is fixed):\n  {_req_line(r)}']
        if sig:
            view.append('Signatures in the repository:\n' + '\n'.join(sig))
        if st.get('lib_names'):
            view.append('Names of the issue that a library defines (import them from it): '
                        + ', '.join(st['lib_names'][:8]) + '.')
        import _example
        pkgs = sorted(_example._packages(root))
        if pkgs:
            view.append('The repository is the package ' + ', '.join(pkgs) + ': the test imports it ('
                        + '; '.join(f'import {p}' for p in pkgs[:2]) + ') and shows the requirement through its code.')
        if tpl:
            head = _import_lines(_repo.read_text(root, tpl[0]))[:15]
            view.append(f'An existing test of this code ({tpl[0]}), with its file\'s imports, for its fixtures and '
                        'style:\n' + '\n'.join(head + [''] + [l.rstrip() for l in tpl[1]]))
        if st['repro'] is None and st.get('repro_last'):
            view.append('The previous test was refused: ' + st['repro_last'])
        return '\n\n'.join(view) + '\n\n' + form + '\n\n' + nxt
    if first in ('skip', 'no test', 'none'):
        st['repro_done'] = True
        return 'No reproduction test: the work goes on without one.\n\n' + DONE
    code = _args.code(items[0] if len(items) == 1 else '\n'.join(items))
    try:
        ast.parse(code)
    except SyntaxError as e:
        return f'The test does not compile ({e.msg}, line {e.lineno}). Send it again whole.\n\n{nxt}'
    own = {n.name for n in ast.parse(code).body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    shadow = [h for h in st.get('lib_names') or [] if h.split('.')[-1] in own]
    if shadow:
        # gm11c (the 31B, fastapi_12942): the test defined its own class Tag, so pydantic rejected the union and the
        # refusal ("a reason of its own") did not say why; checked before the run, so it costs no try
        h = shadow[0]
        mod, short = h.rsplit('.', 1)
        return (f'The test defines its own {short}, but the issue\'s {short} is the library\'s {h}: delete that '
                f'definition and import it (from {mod} import {short}). Send the test again.\n\n{nxt}')
    tpl = _template(root, r)
    rel = _repro_path(root, tpl[0] if tpl else '')
    outcomes, msgs, calls, output = _run_repro(root, rel, code)
    st['repro_tries'] += 1
    pkg = '|'.join(re.escape(n.split('.')[0]) for n in r['names']) or 'x^'
    reason = None
    if not outcomes:
        reason = 'pytest collected no test from it:\n' + '\n'.join(output.strip().splitlines()[-8:])
    elif all(v == 'passed' for v in outcomes.values()):
        reason = 'it passes on the code as it is now, so it does not show the issue'
    elif any(v == 'hangs' for v in outcomes.values()):
        reason = f'it did not finish within {REPRO_TIMEOUT} s on the code as it is now (it waits for input or loops)'
    else:
        allowed = {n.split('.')[-1] for n in r['names']}
        bad = [k for k, v in outcomes.items() if v == 'error'] + [
            k for k in outcomes if re.search(BAD_REASON.pattern.replace('{pkg}', pkg), msgs.get(k, ''))] + [
            k for k in outcomes for m in re.findall(r"NameError: name '(\w+)' is not defined", msgs.get(k, ''))
            if m not in allowed]
        async_only = [k for k in outcomes if re.search(r'not natively supported|install a suitable plugin|- pytest-asyncio',
                                                        msgs.get(k, '') + output)]
        if async_only:
            # fastapi_9555 (local 31B): an async def test with no async plugin installed fails before its body runs
            reason = ('pytest here cannot run an async def test (no async plugin is installed): write a plain def '
                      'test that runs the async code with asyncio.run(...) or through the repository\'s own test '
                      'client')
        elif bad:
            reason = 'it fails for a reason of its own, not the issue\'s:\n' + msgs.get(bad[0], '')[:600]
            # gm11f (the 31B): Tag and Discriminator imported from fastapi.openapi.models, the repository's own classes
            # of the same names, failed with "BaseModel.__init__() takes 1 positional argument"; the refusal now names
            # the import (any name the issue uses that a library defines and the test takes from elsewhere)
            libs = {h.rsplit('.', 1)[1]: h for h in st.get('lib_names') or []}
            for node in ast.walk(ast.parse(code)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for a in node.names:
                        h = libs.get(a.name)
                        if h and node.module.split('.')[0] != h.split('.')[0]:
                            reason += (f'\nIt imports {a.name} from {node.module}; the issue\'s {a.name} is the '
                                       f"library's {h} (from {h.rsplit('.', 1)[0]} import {a.name}).")
        elif not calls and getattr(sys, 'monitoring', None) is not None:
            # a test that fails before it calls any function of the repository shows nothing about the issue
            reason = ('it calls no function of the repository (importing or reloading a module does not count), so '
                      'the failure is not the issue\'s: call the code the way the issue\'s user does (a function, a '
                      'class, an endpoint), as the existing test does:\n' + msgs.get(next(iter(outcomes)), '')[:600])
    if reason:
        st['repro_last'] = reason[:700]
        if st['repro_tries'] >= 2:
            st['repro_done'] = True
            return f'Refused: {reason}\nThat was the last try: the work goes on without a test.\n\n{DONE}'
        return f'Refused: {reason}\n\n{form}\n\n{nxt}'
    failing = [k for k, v in outcomes.items() if v == 'failed']
    st['repro'] = {'rid': r['id'], 'rel': rel, 'code': code, 'ids': sorted(outcomes), 'before': msgs.get(failing[0], '')[:900],
                   'calls': calls[:60], 'now_passes': False, 'now': '', 'frames': LAST_FRAMES[-6:]}
    st['repro_done'] = True
    return (f'Kept: the test fails on the code as it is now:\n{st["repro"]["before"][:600]}\nIt runs '
            f'{len(calls)} functions of the repository. ' + ('It will be run again after the edits.' if VERIFY else
            'The other agents see which code it runs and how it fails.') + f'\n\n{DONE}')


# ------------------------------------------------------------------------------------------- 3 locate

def _new_members(root, st, r):
    """Candidates for the members a requirement adds ('app.frontend'), or that the reproduction test found missing
    ("'FastAPI' object has no attribute 'frontend'"): the new member in the class it goes in. gk13 (fastapi_15800):
    locate listed swagger and docs code, the searches for 'frontend' found nothing, and locate gave up; the class
    FastAPI, where the new method goes, was not on the list."""
    asked = [n for n in r.get('new') or [] if '.' in n]
    if st['repro'] and st['repro'].get('rid') == r['id']:
        asked += [f'{c}.{a}' for c, a in re.findall(r"'(\w+)' object has no attribute '(\w+)'",
                                                     st['repro'].get('before') or '')]
    if not asked:
        return []
    index = _rank.Index(root)
    out, seen = [], set()
    for n in asked:
        var, short = n.rsplit('.', 1)
        owner = _rank.owner_class(index, var.split('.')[-1])
        if not owner or short in index.names and any(
                s.name == f'{owner[1]}.{short}' for s in _code.symbols(_code.parse(_repo.read_text(root, owner[0])))):
            continue
        sym = next((s for s in _code.symbols(_code.parse(_repo.read_text(root, owner[0]))) if s.name == owner[1]), None)
        if sym is None or (owner, short) in seen:
            continue
        seen.add((owner, short))
        out.append({'rel': owner[0], 'name': f'{owner[1]}.{short}', 'start': sym.start, 'end': sym.end, 'new': True,
                    'owner': owner[1], 'line': f'({owner[0]}, new) does not exist yet: the new `{short}` the issue '
                                               f'asks for, added to the class {owner[1]}' + (
                                                   f' ({var} = {owner[1]}(...) in the code)' if var.split('.')[-1]
                                                   != owner[1].split('.')[-1] else '')})
    return out


def _failing_places(root, st, r, limit=3):
    """The functions of the repository the reproduction test's failure went through, innermost first (the last frames
    of its traceback in the repository's own code), as candidates: gk13-gk15 located by words alone, and a title-only
    statement gives them nothing. The innermost one often only checks and raises (rich_3894: Text.append raises, the
    fix is in its caller Inspect._get_signature), so its callers on the traceback come with it."""
    repro = st.get('repro') or {}
    if repro.get('rid') not in (r['id'], None) or not repro.get('frames'):
        return []
    table = _impact.Table(root, docs=False)
    out, seen = [], set()
    for i, (rel, line) in enumerate(reversed(repro['frames'])):
        syms = [x for x in table.syms.get(rel, []) if x.kind == 'def' and x.start <= line <= x.end]
        if not syms:
            continue
        sym = max(syms, key=lambda x: x.start)            # the innermost definition holding the line
        if (rel, sym.name) in seen:
            continue
        seen.add((rel, sym.name))
        lines = _repo.read_lines(root, rel)
        text = lines[line - 1].strip() if line <= len(lines) else ''
        how = 'the reproduction test fails here' if not out else 'the failure passes through here'
        out.append({'rel': rel, 'name': sym.name, 'start': sym.start, 'end': sym.end,
                    'line': f'({rel}, def) {_sig(sym, 100)}  [{how}: {text[:80]}]'})
        if len(out) >= limit:
            break
    return out


def _candidates(root, st, r, n=8):
    text = (r.get('quote') or '') + '\n' + r['what']
    cands, _ = step._rank_candidates(root, text, r['names'], [], n=n)
    failing = _failing_places(root, st, r)
    for f in reversed(failing):
        same = next((c for c in cands if _key(c) == _key(f)), None)
        if same is not None:
            cands.remove(same)
        cands.insert(0, f)
    if failing:
        step._set_handles(cands)
    new = _new_members(root, st, r)
    if new:
        cands = new + [c for c in cands if not any(_key(c) == _key(x) for x in new)]
        step._set_handles(cands)
    if st['repro'] and st['repro'].get('calls'):
        called = set(st['repro']['calls'])
        hit = [c for c in cands if _key(c) in called]
        for c in hit:
            c['line'] += '  [the reproduction test runs it]'
        index = _rank.Index(root)
        q = index.query({**_statement.terms(text), **{x: 3 for x in r['names']}})
        extra = []
        for d in index.docs:
            if f'{d.rel}::{d.name}' in called and not any(_key(c) == f'{d.rel}::{d.name}' for c in cands):
                s, m = index.score(d, q)
                if s > 0:
                    extra.append((s, d, m))
        extra.sort(key=lambda x: -x[0])
        top = max([index.score(d, q)[0] for d in index.docs if any(_key(c) == f'{d.rel}::{d.name}' for c in cands)]
                  or [0])
        extra = [x for x in extra if x[0] >= 0.5 * top]     # the test's own setup (Console.__init__) says little
        for _, d, m in extra[:3]:
            c = step._candidate(index, d, m, note='the reproduction test runs it', root=root)
            cands.append(c)
        step._set_handles(cands)
    return _with_callers(root, cands)


def _with_callers(root, cands, top=3, limit=4):
    """The candidates and, after them, the code that calls the first ones. gk3-gm7 (fastapi_14583, a title-only
    statement): the words found annotation_is_pydantic_v1 and Param.__init__, never get_dependant, which calls
    analyze_param, where the reference warns; one step up from the candidates is where such code is."""
    table = _impact.Table(root, docs=False)
    seen = {_key(c) for c in cands}
    added = []
    def eligible(c):
        if c.get('name') in (None, MODULE) or ', class)' in (c.get('line') or ''):
            return False
        short = c['name'].split('.')[-1]
        parent = c['name'].rsplit('.', 1)[0] if '.' in c['name'] else None
        psym = table.get(c['rel'], parent) if parent else None
        # not a dunder, a nested function, or a name called in many places ('app'): their callers say little
        return not (short.startswith('__') or short in COMMON_METHODS or (psym is not None and psym.kind == 'def')
                    or len(table.calls.get(short, [])) > 8)

    for c in [c for c in cands if eligible(c)][:top]:
        sites = table.calls.get(c['name'].split('.')[-1], [])
        for r, caller, line in sites:
            if not caller or _repo.is_test_path(r) or _repo.is_doc_path(r) or f'{r}::{caller}' in seen:
                continue
            res = step._resolve(root, [f'{r}::{caller}'])
            if res:
                d = res[0]
                sym = table.get(r, caller)
                sig = _sig(sym)[:100] if sym is not None else ''
                d['line'] = f'({r}, def) {sig}  [calls {c["name"]}]'
                seen.add(f'{r}::{caller}')
                added.append(d)
            if len(added) >= limit:
                break
        if len(added) >= limit:
            break
    if added:
        cands = cands + added
        step._set_handles(cands)
    return cands


def locate(root, st, args):
    rid = (st['current'] or (None, None))[1]
    r = _req(st, rid)
    if r is None:
        return 'Nothing to locate now.\n\n' + DONE
    items = [a for a in args if a and a.strip()]
    nxt = next_call('locate.py', 'locate', 'find', sep=', or ')
    cands = st['cands'].get(rid)
    if cands is None:
        cands = _candidates(root, st, r)
        st['cands'][rid] = cands
    if not items or _args.clean(items[0]).lower() in ('open', 'start', rid.lower()):
        others = sorted({p['name'] for ps in st['located'].values() for p in ps})
        view = [f'Where is this decided in the code?\n  {_req_line(r)}',
                'Candidates (functions and classes, the most related first):\n' +
                '\n'.join(_cand_line(root, c, cands) for c in cands)]
        if others:
            view.append('Chosen for the other requirements: ' + ', '.join(others) + '.')
        return '\n\n'.join(view) + '\n\nChoose the 1 to 3 places whose code decides this behaviour.\n\n' + nxt
    path = _args.clean(items[0])
    if len(items) == 1 and path.endswith('.py') and os.path.isfile(os.path.join(root, path)):
        text = (r.get('quote') or '') + '\n' + r['what']
        more, _ = step._rank_candidates(root, text, r['names'], [], n=15, only_rel=path)
        st['cands'][rid] = cands + [c for c in more if not any(_key(c) == _key(x) for x in cands)]
        step._set_handles(st['cands'][rid])
        return (f'In {path}:\n' + '\n'.join(f'  {c["handle"]}  ' + re.sub(r', lines [0-9]+-[0-9]+', '', c['line'])
                                              for c in more) + '\n\nChoose from these or the list before.\n\n' + nxt)
    found, others = step._pick(cands, items)
    if not found and others:
        # hk30 (fastapi_14361): a text fragment ('$ref') resolved silently to the first function holding it, which was
        # not the one that decides; text is searched and every function holding it is listed to choose from (W1, B11)
        others = [re.sub(r'^(?:async\s+)?(?:def|class)\s+(\w+)\s*[(:]?\s*$', r'\1', _args.clean(o) or '') or o
                  for o in others]      # 'class Depends' (hk30 fastapi_14372) is the name Depends
        is_name = re.fullmatch(r'(?:[\w./-]+\.py::)?[A-Za-z_][\w.]*|[\w./-]+\.py', others[0])
        res = [c for c in step._resolve(root, others) if not _repo.is_doc_path(c['rel'])] if is_name else []
        if res:
            found = res
        else:
            hits, _, _ = step._search(root, step._search_text(others[0]))
            if hits:
                st['cands'][rid] = cands + [h for h in hits if not any(_key(h) == _key(c) for c in cands)]
                step._set_handles(st['cands'][rid])
                return ('Found in the code:\n' + '\n'.join(f'  {h["handle"]}  ' + '; '.join(h.get('evidence', [])[:2])
                                                          for h in hits[:8]) + '\n\nChoose from these or the list '
                        'before.\n\n' + nxt)
            news = [c['handle'] for c in cands if c.get('new')]
            return ('No Python file of the repository holds that name or text (only .py files are searched: '
                    'configuration and data files are not places of a fix here)' + (
                f' yet: it is new code, and the list has where it goes ({", ".join(news)}): choose it.' if news else
                '. Choose from the list.') + f'\n\n{nxt}')
    if not found:
        return 'No place was read. Send 1 to 3 names from the list.\n\n' + nxt
    lost = []
    if others and found and len(found) < 3:
        # local run h3 (fastapi_9555): ['solve_dependencies', 'Dependant.is_coroutine_callable'] kept only the first,
        # silently, because the second was off this requirement's list (chosen for R1); a name off the list is
        # looked up in the repository like a name sent alone
        for o in others:
            res = [c for c in step._resolve(root, [o]) if not _repo.is_doc_path(c['rel'])]
            if not res:
                lost.append(o)
                continue
            # gk11: names already found (resolved above when none was on the list) were reported "not found"
            res = [c for c in res if not any(_key(c) == _key(f) for f in found)]
            if res:
                found.append(res[0])
    st['located'][rid] = [{'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end'],
                           **({'new': True, 'owner': c['owner']} if c.get('new') else {})} for c in found[:3]]
    note = (f' {len(lost)} other name{"s" if len(lost) > 1 else ""} matched no function or class, so '
            f'{"they were" if len(lost) > 1 else "it was"} not chosen.') if lost else ''
    return (f'{rid} is decided at: ' + ', '.join(c.get('handle') or c['name'] for c in found[:3]) + '.' + note
            + '\n\n' + DONE)


def _cand_line(root, c, cands):
    """One candidate's line, its 'uses' note before the "(and N more in <file>)" line (gk13b: the note read as the
    file's)."""
    line = re.sub(r', lines [0-9]+-[0-9]+', '', c['line'])
    head, nl, tail = line.partition('\n')
    return f'  {c["handle"]}  {head}{_uses_note(root, c, cands)}{nl}{tail}'


# methods of the builtin types: 'req.scope.get(...)' is not a use of APIRouter.get (gm9, gk9)
COMMON_METHODS = set(dir(dict)) | set(dir(list)) | set(dir(str)) | set(dir(set)) | set(dir(bytes))


def _uses_note(root, c, cands):
    """' [it uses X, Y from this list]' when the candidate reads or calls other candidates. g1 (fastapi_9555, the local
    31B): the issue named solve_dependencies, which only reads Dependant.is_gen_callable and its siblings, where the
    decision is made; they were on the list, but nothing said that one uses the others."""
    if c.get('name') in (None, MODULE) or ', class)' in (c.get('line') or '') or _repo.is_doc_path(c['rel']):
        # gk12: the class FastAPI and decorated docs examples were marked as using on_event
        return ''
    used = _used_names(_repo.read_lines(root, c['rel']), c['name']) or set()
    others = [d['handle'] for d in cands if d is not c and d.get('name') not in (None, MODULE)
              and d['name'].split('.')[-1] in used and d['name'] != c['name']
              and not d['name'].split('.')[-1].startswith('__') and d['name'].split('.')[-1] not in COMMON_METHODS]
    return f'  [it uses {", ".join(others[:4])} from this list]' if others else ''


def _fallback_locate(root, st, r):
    cands = st['cands'].get(r['id']) or _candidates(root, st, r, n=3)
    return [{'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end']} for c in cands[:1]]


# ------------------------------------------------------------------------------------------- 4 scope

# 'reads <property>' left this list (gated run gh4, fastapi_9555): Dependant.computed_scope reads is_async_gen_callable
# and was added as needing "the same change"; a reader may need no change, so it is related code for scope to decide
AUTO = ('async/sync twin', 'same pattern')


def _related(root, st, located):
    table = _impact.Table(root, docs=True)
    located = [p for p in located if not p.get('new')]      # a new member has no code yet to relate
    chosen = [(p['rel'], p['name']) for p in located if p['name'] != MODULE]
    rel_places = _impact.related(table, chosen) if chosen else []
    auto, options = [], []
    bases = {p['rel'].split('/')[-1] for p in located}
    for q in rel_places:
        if q.reason == 'chosen':
            continue
        d = {'rel': q.rel, 'name': q.name, 'start': q.start, 'end': q.end, 'reason': q.reason}
        if q.reason.startswith(AUTO) or (q.reason.startswith('same definition') and q.rel.split('/')[-1] in bases):
            auto.append(d)
        elif q.name != '<exports>':
            options.append(d)
    seen = {(d['rel'], d['name']) for d in auto + options} | set(chosen)
    ran = (st.get('repro') or {}).get('calls') or ()
    for d in _callees(root, table, located, ran=ran) + _importers(root, table, located):
        if (d['rel'], d['name']) not in seen:
            seen.add((d['rel'], d['name']))
            options.append(d)
    return auto, options


def _callees(root, table, located, per_place=5, ran=()):
    """The repository's functions a chosen place calls: self.<method>() of its class, and plain names defined once in
    the repository. gk12 (fastapi_14851): scope listed copies, callers and readers, and "No other related code was
    found"; the reference changes what on_event calls (add_event_handler, APIRouter.__init__, the lifespan).
    First the ones the reproduction test runs, then the ones a branch's condition calls, then by line: gk14
    (fastapi_12942) listed five calls of analyze_param in ast.walk order and left out the condition
    `elif not field_annotation_is_scalar(...)`, where body or query is decided."""
    out = []
    ran = set(ran)
    for p in located:
        if p['name'] == MODULE:
            continue
        sym = table.get(p['rel'], p['name'])
        if sym is None:
            continue
        cls = p['name'].rsplit('.', 1)[0] if '.' in p['name'] else None
        in_test = {id(n) for t in ast.walk(sym.node) if isinstance(t, (ast.If, ast.While, ast.IfExp, ast.Assert))
                   for n in ast.walk(t.test)}
        found, seen = [], set()
        for node in ast.walk(sym.node):
            if not isinstance(node, ast.Call):
                continue
            f, target = node.func, None
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == 'self' and cls:
                target = table.get(p['rel'], f'{cls}.{f.attr}')
                rel = p['rel']
            elif isinstance(f, ast.Name) and f.id not in COMMON_METHODS:
                defs = [(r, d) for r, d in table.find(f.id) if d.name == f.id and not _repo.is_test_path(r)]
                if len(defs) == 1:
                    rel, target = defs[0]
            if target is not None and target.name != p['name'] and target.kind == 'def' and \
                    (rel, target.name) not in seen:
                seen.add((rel, target.name))
                lines = _repo.read_lines(root, p['rel'])
                key = (f'{rel}::{target.name}' not in ran, id(node) not in in_test, node.lineno)
                found.append((key, {'rel': rel, 'name': target.name, 'start': target.start, 'end': target.end,
                                    'reason': f'called by {p["name"].split(".")[-1]} (line {node.lineno})',
                                    'link': lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else ''}))
        out += [d for _, d in sorted(found, key=lambda x: x[0])[:per_place]]
    return out


def _importers(root, table, located, limit=4):
    """The code that imports a module whose top-level code was chosen (gm4-gm7, fastapi_14583: no related code at all
    for fastapi/_compat/v1.py, while the reference changes the code that uses it)."""
    out = []
    for p in located:
        if p['name'] != MODULE or not p['rel'].endswith('.py'):
            continue
        mods = _tests._module_names(p['rel'])
        last = os.path.basename(p['rel'])[:-3]
        pat = re.compile(r'^\s*(from\s+(' + '|'.join(re.escape(m) for m in mods) + r'|\.+' + re.escape(last) +
                         r')\s+import|import\s+(' + '|'.join(re.escape(m) for m in mods) + r')\b)', re.M)
        for rel in table.syms:
            if rel == p['rel'] or _repo.is_test_path(rel) or _repo.is_doc_path(rel):
                continue
            text = _repo.read_text(root, rel)
            m = pat.search(text)
            if m:
                line = text[:m.start()].count('\n') + 1
                out.append({'rel': rel, 'name': MODULE, 'start': 1, 'end': len(text.splitlines()),
                            'reason': f'imports {os.path.basename(p["rel"])} (line {line})'})
                if len(out) >= limit:
                    break
    return out


def _link_line(root, d):
    if d.get('link') is not None:
        return d['link']               # the line is in the chosen place's file (a callee's link)
    m = re.search(r'\(line (\d+)\)', d['reason'])
    lines = _repo.read_lines(root, d['rel'])
    n = int(m.group(1)) if m else d['start']
    return lines[n - 1].strip()[:120] if 0 < n <= len(lines) else ''


def _new_places(root, st):
    """Places for the new members the requirements ask for: 'var.name' (app.frontend) at the end of the class var is
    made from. A bare new name (a parameter, a helper) is left to the specifier's sentence or to a 'missing' answer."""
    index = _rank.Index(root)
    out = []
    for r in st['reqs']:
        for n in r['new']:
            if '.' in n:
                var, short = n.rsplit('.', 1)
                owner = _rank.owner_class(index, var.split('.')[-1])
                if owner:
                    sym = next((s for s in _code.symbols(_code.parse(_repo.read_text(root, owner[0])))
                                if s.name == owner[1]), None)
                    if sym:
                        out.append({'rel': owner[0], 'name': f'{owner[1]}.{short}', 'start': sym.start,
                                    'end': sym.end, 'new': True, 'owner': owner[1], 'reqs': [r['id']]})
    seen, uniq = set(), []
    for p in out:
        if _key(p) not in seen:
            seen.add(_key(p))
            uniq.append(p)
    return uniq


def _orig_text(root, rel, name):
    """The definition's text in the original commit (HEAD), or None."""
    text = _repo.git(root, 'show', 'HEAD:' + rel)
    if not text:
        return None
    lines = text.splitlines(keepends=True)
    sym = next((x for x in _code.symbols(_code.parse(text)) if x.name == name), None)
    return ''.join(lines[sym.start - 1:sym.end]) if sym else None


def _cur_span(root, rel, name):
    lines = _repo.read_lines(root, rel)
    sym = next((x for x in _code.symbols(_code.parse(''.join(lines))) if x.name == name), None)
    return (lines, sym.start, sym.end) if sym else (lines, None, None)


def _same_code(root, d, places):
    """d has the same qualified name and original code as one of these places (a copy kept in another module)."""
    mine = _orig_text(root, d['rel'], d['name'])
    return bool(mine) and any(q['name'] == d['name'] and q['rel'] != d['rel'] and not q.get('new')
                              and _orig_text(root, q['rel'], q['name']) == mine for q in places)


def _copy_of(root, st, p):
    """Another place of the plan with the same qualified name in another file and the same original code (a module
    kept in two copies, such as a sync package and its async twin), or None."""
    if p.get('new') or p['name'] == MODULE:
        return None
    mine = _orig_text(root, p['rel'], p['name'])
    if not mine:
        return None
    return next((q for q in st['plan'] if q is not p and q['name'] == p['name'] and q['rel'] != p['rel']
                 and not q.get('new') and _orig_text(root, q['rel'], q['name']) == mine), None)


def _apply_copy(root, st, p, q):
    """q's accepted edit made in p as well, when p still has the original code they share: the same new definition
    and the imports q's edit added. True when it is applied and the file still parses."""
    lines, s0, e0 = _cur_span(root, p['rel'], p['name'])
    qlines, qs, qe = _cur_span(root, q['rel'], q['name'])
    if s0 is None or qs is None or ''.join(lines[s0 - 1:e0]) != _orig_text(root, p['rel'], p['name']):
        return False
    new = lines[:s0 - 1] + qlines[qs - 1:qe] + lines[e0:]
    if _code.parse(''.join(new)) is None:
        return False
    head = (_repo.git(root, 'show', 'HEAD:' + q['rel']) or '').splitlines()
    added = [l.strip() for l in qlines if IMPORT.match(l.strip()) and not l.startswith((' ', '\t'))
             and l.rstrip('\n') not in head]
    _repo.write_lines(root, p['rel'], new)
    _add_imports(root, p['rel'], added)
    if _code.parse(_repo.read_text(root, p['rel'])) is None:
        _repo.git(root, 'checkout', '--', p['rel'])
        return False
    p['status'], p['edited'], p['copied_from'] = 'done', True, _key(q)
    _refresh(root, p)
    return True


def _main_place(st):
    """The plan's place chosen first for the first requirement that has one."""
    for r in st['reqs']:
        for c in st['located'].get(r['id']) or []:
            p = next((q for q in st['plan'] if _key(q) == _key(c)), None)
            if p is not None:
                return p
    return None


LITERAL = re.compile(r"^\s*(?:in\s+`[^`]+`\s*,\s*)?replace\s+(?:the\s+\w+(?:\s+\w+)?\s+)?`([^`]+)`\s+with\s+`([^`]*)`\s*\.?\s*$",
                     re.I | re.S)


def _try_literal(root, st, p):
    """A sentence that is one literal replacement ("replace `A` with `B`", 6 of the 15 sentences of V23) is applied by
    the script through the editor's own checks, when A occurs once in the place; True when the edit is kept. The editor
    only copied it (20-29 s a place)."""
    m = LITERAL.match(p.get('sentence') or '')
    if not m or p.get('lit_tried') or p.get('new') or p['name'] == MODULE:
        return False
    p['lit_tried'] = True
    old, new = _unescaped(m.group(1)), _unescaped(m.group(2))
    lines, s0, e0 = _cur_span(root, p['rel'], p['name'])
    if s0 is None:
        return False
    text = ''.join(lines[s0 - 1:e0])
    if text.count(old) != 1:
        return False
    saved = {k: p.get(k) for k in ('fails', 'edits', 'status')}
    cur, tent = st.get('current'), st.get('tentative')
    st['current'] = ('edit', p['handle'])
    edit(root, st, [text.replace(old, new).rstrip('\n')])
    st['current'], st['tentative'] = cur, tent
    if p.get('status') == 'done':
        p['literal'] = True
        return True
    for k, v in saved.items():
        p[k] = v
    return False


def _located_places(st):
    located = []
    for rid, ps in st['located'].items():
        for p in ps:
            q = next((x for x in located if _key(x) == _key(p)), None)
            if q:
                q['reqs'].append(rid)
            else:
                located.append({**p, 'reqs': [rid]})
    return located


def _scope_options(root, st):
    """The related code scope would offer (empty: nothing to decide)."""
    _, options = _related(root, st, _located_places(st))
    return options


def scope(root, st, args):
    located = []
    for rid, ps in st['located'].items():
        for p in ps:
            q = next((x for x in located if _key(x) == _key(p)), None)
            if q:
                q['reqs'].append(rid)
            else:
                located.append({**p, 'reqs': [rid]})
    auto, options = _related(root, st, located)
    step._set_handles(options)
    st['scope_options'] = options[:12]
    newp = _new_places(root, st)
    items = [a for a in args if a and a.strip()]
    nxt = next_call('scope.py', 'scope', 'none', sep=', or ')
    if not items or _args.clean(items[0]).lower() in ('open', 'start', 'the plan', 'plan'):
        view = ['The places chosen for the requirements:\n' + '\n'.join(
            f'  {p["name"]} ({p["rel"]}) for {", ".join(p["reqs"])}' for p in located)]
        view.append('Requirements:\n' + '\n'.join('  ' + _req_line(r) for r in st['reqs']))
        if auto:
            view.append('Added with them (the same change is needed there): ' + ', '.join(
                f'{d["name"]} ({d["reason"]})' for d in auto[:6]) + '.')
        if newp:
            view.append('New code the requirements ask for (added): ' + ', '.join(p['name'] for p in newp) + '.')
        if options:
            view.append('Related code (each with the line that links it). Which of these must change too, so that the '
                        'requirements hold everywhere?\n' + '\n'.join(
                            f'  {d.get("handle") or d["name"]} ({d["rel"]}) — {d["reason"]}: {_link_line(root, d)}'
                            for d in options[:12]))
        else:
            view.append('No other related code was found.')
        if st['repro'] and st['repro'].get('calls'):
            ran = [k.split('::')[1] for k in st['repro']['calls'] if not any(
                k == _key(p) for p in located + auto + options) and not k.split('.')[-1].startswith('__')]
            if ran:
                view.append('Other functions the reproduction test runs: ' + ', '.join(ran[:10]) + '.')
        return '\n\n'.join(view) + '\n\nSend the names of the related code that must change too, or [\'none\'].\n\n' + nxt
    extra = []
    if not (len(items) == 1 and re.fullmatch(r'(?i)\W*(none|no|nothing|no other|nothing else)\W*', items[0])):
        pool = [dict(d) for d in options]
        step._set_handles(pool)
        found, others = step._pick(pool, items)
        extra += found
        if others:
            extra += [c for c in step._resolve(root, others) if not _repo.is_doc_path(c['rel'])]
    _make_plan(root, st, extra, located=located, auto=auto, newp=newp)
    return 'The plan: ' + ', '.join(p['handle'] for p in st['plan']) + '.\n\n' + DONE


def _make_plan(root, st, extra, located=None, auto=None, newp=None):
    if located is None:
        located = []
        for rid, ps in st['located'].items():
            for p in ps:
                q = next((x for x in located if _key(x) == _key(p)), None)
                if q:
                    q['reqs'].append(rid)
                else:
                    located.append({**p, 'reqs': [rid]})
        auto, _ = _related(root, st, located)
        auto = [d for d in auto if _same_code(root, d, located)]
        newp = _new_places(root, st)
    all_reqs = [r['id'] for r in st['reqs']]
    plan = []
    for p in newp + located + [dict(d, reqs=list(located[0]['reqs']) if located else all_reqs) for d in auto] + [
            dict(d, reqs=all_reqs) for d in extra]:
        if any(_key(q) == _key(p) for q in plan):
            continue
        p = {k: p[k] for k in ('rel', 'name', 'start', 'end', 'reqs', 'new', 'owner') if k in p}
        p.update({'sentence': None, 'status': None})
        plan += _split_class(root, st, p)
    for p in plan:
        _refresh(root, p)
    st['plan'] = _order(root, plan)
    _handles(st)
    st['scope_done'] = True


def _split_class(root, st, p):
    """A class place over BIG_CLASS lines is replaced by its members most related to its requirements."""
    if p.get('new') or p['name'] == MODULE or p['end'] - p['start'] + 1 <= BIG_CLASS:
        return [p]
    sym = next((s for s in _code.symbols(_code.parse(_repo.read_text(root, p['rel']))) if s.name == p['name']), None)
    if sym is None or sym.kind != 'class':
        return [p]
    reqs = [_req(st, rid) for rid in p['reqs'] if _req(st, rid)]
    text = '\n'.join((r.get('quote') or '') + ' ' + r['what'] for r in reqs)
    members, _ = step._rank_candidates(root, text, [n for r in reqs for n in r['names']], [], n=2, only_rel=p['rel'],
                                       within=p['name'])
    out = [{'rel': m['rel'], 'name': m['name'], 'start': m['start'], 'end': m['end'], 'reqs': list(p['reqs']),
            'sentence': None, 'status': None} for m in members if '[matches:' in m['line']]
    return out or [p]


def _order(root, plan):
    """New code first, then each place after the places it calls (a callee is edited before its callers)."""
    table = _impact.Table(root, docs=False)
    keys = {_key(p) for p in plan}
    callees = {}
    for p in plan:
        callers = {f'{r}::{c}' for r, c, _ in table.calls.get(p['name'].split('.')[-1], []) if c}
        for k in callers & keys:
            if k != _key(p):
                callees.setdefault(k, set()).add(_key(p))
    out = [p for p in plan if p.get('new')]
    left = [p for p in plan if not p.get('new')]
    while left:
        waiting = {_key(q) for q in left}
        ready = [p for p in left if not (callees.get(_key(p), set()) & waiting)]
        nxt = ready[0] if ready else left[0]
        out.append(nxt)
        left.remove(nxt)
    return out


def _missing_names(text):
    """The code names in an agent's 'missing' text: 'PromptBase.__init__ and Prompt.ask' gives both."""
    names = re.findall(r'[\w./-]+\.py::[A-Za-z_][\w.]*|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+|[A-Za-z_]\w*', text or '')
    names = [n for n in dict.fromkeys(names) if ('.' in n or '_' in n or n[:1].isupper())
             and ('::' in n or not re.search(r'(/|\.py$)', n)) and not _repo.is_test_path(n.split('::')[0])
             and not n.split('::')[0].split('/')[-1].startswith('test')]
    dotted = [n for n in names if '.' in n or '::' in n]
    return (dotted or names)[:3]


def _names_code(root, text):
    """Whether a 'missing' text names code: a definition of the repository, a new member of an existing class or
    module, or a Python file path."""
    if re.fullmatch(r'\s*[\w./-]+\.py\s*', _args.clean(text) or ''):
        return True
    for n in _missing_names(text):
        if any(not _repo.is_doc_path(c['rel']) for c in step._resolve(root, [n])):
            return True
        if '.' in n and step._new_definition_owner(root, n):
            return True
    return False


def _add_missing(root, st):
    """Code an editor or the review named as missing: added to the plan before the place that needs it, to specify."""
    while st['missing']:
        entry = st['missing'].pop(0)
        text, note, rids = entry[0], entry[1], entry[2]
        requester = entry[3] if len(entry) > 3 else None
        added = []
        for name in _missing_names(text) or [text]:
            if _place_by(st, name):
                p = _place_by(st, name)
                if p.get('status') in ('done', 'nochange', 'failed'):
                    p['old_sentence'] = p.get('sentence')
                    p['sentence'], p['note'] = None, note
                continue
            found = [c for c in step._resolve(root, [name]) if not _repo.is_doc_path(c['rel'])]
            if found:
                c = found[0]
                if any(_key(q) == _key(c) for q in st['plan'] + added):
                    continue
                added.append({'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end'], 'reqs': rids,
                              'sentence': None, 'status': None, 'note': note})
                auto, _ = _related(root, st, [added[-1]])
                for d in auto:
                    if not any(_key(q) == _key(d) for q in st['plan'] + added):
                        added.append({**{k: d[k] for k in ('rel', 'name', 'start', 'end')}, 'reqs': rids,
                                      'sentence': None, 'status': None})
            elif '.' in name:
                owner = step._new_definition_owner(root, name)
                if owner:
                    c, short = owner
                    added.append({'rel': c['rel'], 'name': f'{c["name"]}.{short}', 'start': c['start'],
                                  'end': c['end'], 'new': True, 'owner': c['name'], 'reqs': rids,
                                  'sentence': None, 'status': None, 'note': note})
        for p in added:
            _refresh(root, p)
        at = next((i for i, q in enumerate(st['plan']) if _key(q) == requester), len(st['plan']))
        st['plan'][at:at] = added
        _handles(st)


# ------------------------------------------------------------------------------------------- 5 specify

def _params(fn):
    a = fn.args
    pos = [x.arg for x in a.posonlyargs + a.args if x.arg not in ('self', 'cls')]
    kw = [x.arg + '=' for x in a.kwonlyargs]
    names = pos + kw
    return ', '.join(names[:4]) + (', ...' if len(names) > 4 else '')


def _lib_base(root, rel, base):
    """The library class a class of the repository derives from ('routing.Router' with `from starlette import
    routing`), or None."""
    tree = _code.parse(_repo.read_text(root, rel))
    head, _, rest = base.partition('.')
    target = None
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for al in node.names:
                if (al.asname or al.name) == head:
                    target = (f'{node.module}.{al.name}', rest) if rest else (node.module, al.name)
        elif isinstance(node, ast.Import):
            for al in node.names:
                if (al.asname or al.name) == head and rest:
                    target = (al.name, rest)
    if target is None:
        return None
    try:
        import importlib
        mod = importlib.import_module(target[0])
        if os.path.realpath(getattr(mod, '__file__', '') or '').startswith(os.path.realpath(root) + os.sep):
            return None                                   # the repository's own code, listed by the table
        obj = mod
        for part in target[1].split('.'):
            obj = getattr(obj, part)
        return obj if isinstance(obj, type) else None
    except Exception:
        return None


def _members_note(root, p):
    """What the class a new member goes in already has: its own public methods and those it inherits from a library
    class, which the new code can call as self.<name>. gk13b (fastapi_15800): the specifier was told only "new code, at
    the end of class APIRouter" and wrote "registers a static frontend", saying nothing of how; APIRouter inherits
    mount() from Starlette's Router."""
    owner = p.get('owner')
    if not owner or owner == MODULE:
        return ''
    table = _impact.Table(root, docs=False)
    depth = owner.count('.') + 1
    own = [x for x in table.syms.get(p['rel'], []) if x.kind == 'def' and x.name.startswith(owner + '.')
           and x.name.count('.') == depth and not x.name.split('.')[-1].startswith('_')]
    out = []
    if own:
        out.append(f'Methods {owner} has (the new code can call them as self.<name>(...)): ' + '; '.join(
            f'{x.name.split(".")[-1]}({_params(x.node)})' for x in own[:30]))
    cls = table.get(p['rel'], owner)
    names = {x.name.split('.')[-1] for x in own}
    import inspect
    for base in [ast.unparse(b) for b in getattr(getattr(cls, 'node', None), 'bases', [])]:
        lib = _lib_base(root, p['rel'], base)
        if lib is None:
            continue
        meths = []
        for n, f in inspect.getmembers(lib, inspect.isfunction):
            if n.startswith('_') or n in names:
                continue
            try:
                meths.append(f'{n}{inspect.signature(f)}'.replace('(self, ', '(').replace('(self)', '()').replace("'", ''))
            except (TypeError, ValueError):
                meths.append(f'{n}(...)')
        if meths:
            out.append(f'Inherited from {lib.__module__}.{lib.__qualname__} (library code): '
                       + '; '.join(m[:90] for m in meths[:15]))
    return '\n'.join(out)[:2500]


def _brief(sentence):
    """The sentence again, whole (gk14b: cut at 400 characters with '...', it read as the script's output cut off,
    and the editor worked from the part it saw). A sentence is at most MAX_SENTENCE characters."""
    return ' '.join((sentence or '').split())


def _deciding_calls(root, st, p, ctx, limit=3):
    """The functions the place calls in a condition or that the reproduction test runs, when the context view left them
    out: gk14b (fastapi_12942) showed Body, ParamTypes and FieldInfo as the code analyze_param uses, but not
    field_annotation_is_scalar, the call in `elif not field_annotation_is_scalar(...)` where body or query is decided."""
    if p.get('new') or p['name'] == MODULE:
        return ''
    table = _impact.Table(root, docs=False)
    ran = (st.get('repro') or {}).get('calls') or ()
    rows = []
    # whole bodies while they fit a line budget, larger when the place itself is short: gm11 (a 6-line cap) showed
    # field_annotation_is_scalar whole, but gm11e's specifier of that one-line wrapper did not see the 11 lines of
    # field_annotation_is_complex it delegates to, guessed, and answered 'no change'
    budget = 60 if p['end'] - p['start'] + 1 <= 20 else 24
    calls = [d for d in _callees(root, table, [p], per_place=limit, ran=ran)
             if not re.search(rf'(?m)^\s+\S*\b{re.escape(d["name"])} \(', ctx or '')]   # not shown by the context view
    syms = [table.get(d['rel'], d['name']) for d in calls]
    whole = set()
    for i in sorted(range(len(calls)), key=lambda i: syms[i].end - syms[i].start if syms[i] is not None else 1e9):
        size = syms[i].end - syms[i].start + 1 if syms[i] is not None else 0
        if syms[i] is not None and size <= min(budget, 30):
            budget -= size
            whole.add(i)                               # the smallest first, so that more of them are whole
    for i, d in enumerate(calls):
        sym = syms[i]
        if i in whole:
            rows.append(f'  {d["name"]} ({d["rel"]}), called here: {d["link"]}\n'
                        + _code.numbered(_repo.read_lines(root, d['rel']), sym.start, sym.end, collapse=[]))
        else:
            rows.append(f'  {d["name"]} ({d["rel"]}) {_sig(sym, 120) if sym is not None else ""}\n'
                        f'      called here: {d["link"]}')
    return ('Functions it calls in a condition or that the reproduction test runs:\n' + '\n'.join(rows)) if rows else ''


def _place_view(root, st, p, limit=80):
    reqs = [_req(st, rid) for rid in p['reqs'] if _req(st, rid)]
    if p.get('new'):
        where = f'the end of class {p["owner"]}' if p.get('owner') != MODULE else f'the end of {p["rel"]}'
        note = _members_note(root, p)
        return (f'{p["handle"]} does not exist yet: it is new code, added at {where} ({p["rel"]}).'
                + (f'\n{note}' if note else ''))
    if _assign_place(root, p) or (p['name'] == MODULE and p['end'] - p['start'] < limit):
        # hk30 (rich_3043): a template's text is what changes; long strings are not collapsed here. hf7 (Haiku,
        # rich_3043): the same template as the module's top level was shown '... (16 lines of text) ...', and the
        # finding could not copy its lines
        body = _code.numbered(_repo.read_lines(root, p['rel']), p['start'], p['end'], collapse=())
    else:
        body = step._code_view(root, p['rel'], p['start'], p['end'], _focus(reqs), limit=limit)
    return f'{p["handle"]} ({p["rel"]}, lines {p["start"]}-{p["end"]}):\n{body}'


def _refresh(root, p):
    """The place's current lines. A name defined several times in a row (typing.overload variants, a property and its
    setter) is one place spanning them all: rich_3052's PromptBase.ask has two overloads and the implementation."""
    if p.get('new') and p.get('status') != 'done':
        return
    text = _repo.read_text(root, p['rel'])
    syms = _code.symbols(_code.parse(text))
    same = [x for x in syms if x.name == p['name']]
    if same:
        p['start'], p['end'] = min(x.start for x in same), max(x.end for x in same)
        p['group'] = len(same) > 1
        p['new'] = False
    elif p['name'] != MODULE:
        span = step.assignment_span(text, p['name'])
        if span:
            p['start'], p['end'] = span
            p['new'] = False


def _assign_place(root, p):
    """Whether the place is a top-level assignment (a constant, a template), not a function or class."""
    if p.get('new') or p['name'] == MODULE or '.' in p['name']:
        return False
    text = _repo.read_text(root, p['rel'])
    return step.assignment_span(text, p['name']) is not None and \
        not any(x.name == p['name'] for x in _code.symbols(_code.parse(text)))


NUMBERED = re.compile(r'(?m)^(\s*)\d+\|')


# vf6 lab (the 31B, step by step): 15 of 16 specify decisions were cut at the thinking budget, and 8 of those were
# 'no change' or no call; the thought restarts from nothing each turn (earlier thoughts are not sent back) and 43% of
# it copied code. The decision is split: first the lines that make the requirement fail and what they do now (kept
# in the conversation as the call's own text), then what they must do; 'no change' names the line that already does
# what the requirement says.
DIAGNOSE = True
NC_REFUSALS = 1          # 'no change' without a line of the place is refused at most this many times (g10: 2 cost calls)


def _collapse(s):
    return re.sub(r'\s+', ' ', s or '').strip()


HEADER_LINE = re.compile(r'^\s*(@|(async\s+)?def\s|class\s|"""|\'\'\'|\)\s*(->.*)?:\s*$)')


def _in_place(root, p, text, body_only=False):
    """Whether a line of text (whitespace aside, at least 4 characters) is in the place's body. Its signature,
    decorators and docstring quotes do not count: vf7 lab (rich_3934) gave 'def stop(self) -> None:' as the line that
    already does what the requirement says."""
    lines = _repo.read_lines(root, p['rel'])
    start = p['start']
    if not body_only:
        # hf7 (Haiku, fastapi_14372): the fault was the @dataclass decorator; a finding may name any line of the place
        return any(len(l) >= 4 and l in _collapse(''.join(lines[start - 1:p['end']]))
                   for l in (_collapse(_args.clean(x)).strip('`') for x in re.split(r'\n|\\n', text or '')))
    node = next((n for n in ast.walk(_code.parse(''.join(lines)) or ast.Module(body=[], type_ignores=[]))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.lineno >= p['start']
                 and n.end_lineno == p['end']), None)
    if node is not None and node.body:
        # vf7 lab (fastapi_14419): the parameter lines of get_dependant's signature passed as that line; only the body
        # counts, without its docstring
        first = node.body[0]
        doc = isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant) \
            and isinstance(first.value.value, str)
        start = (node.body[1].lineno if doc and len(node.body) > 1 else first.end_lineno + 1 if doc else first.lineno)
    body = [l for l in lines[start - 1:p['end']] if not HEADER_LINE.match(l)]
    code = _collapse(''.join(body))
    lines = [_collapse(_args.clean(l)).strip('`') for l in re.split(r'\n|\\n', text or '')]
    return any(len(l) >= 4 and l in code for l in lines)


def _diagnosing(p):
    return DIAGNOSE and not p.get('new') and not p.get('diag') and not p.get('edited')


def specify(root, st, args):
    name = (st['current'] or (None, None))[1]
    p = _place_by(st, name) if name else None
    if p is None:
        return 'Nothing to specify now.\n\n' + DONE
    _refresh(root, p)
    items = [a for a in args if a and a.strip()]
    # g11 (fastapi_13537): after 'missing' named a planned place, the next refusal offered 'missing' again and the same
    # call was sent a second time; that place's change is kept, so this place is asked only about itself
    may_name = not (p.get('note') or '').startswith('the specifier of') and not p.get('named_planned')
    nxt = next_call('specify.py', *_specify_forms(p, may_name))
    if not items or _args.clean(items[0]).lower() in ('open', 'start', p['handle'].lower()):
        reqs = [_req(st, rid) for rid in p['reqs'] if _req(st, rid)]
        view = [_place_view(root, st, p, limit=150),
                'It serves these requirements:\n' + '\n'.join('  ' + _req_line(r) for r in reqs)]
        if not p.get('new'):
            ctx = step._context_view(root, p, _focus(reqs))
            if ctx:
                view.append(ctx[:2500])
            deciding = _deciding_calls(root, st, p, ctx)
            if deciding:
                view.append(deciding)
            if not (p.get('note') or '').startswith('the specifier of'):
                # the scope agent's question, asked here (docs/time_proposals C1, F1): related code that may need the
                # same change, each with the line that links it
                _, options = _related(root, st, [p])
                options = [d for d in options if not any(_key(q) == _key(d) for q in st['plan'])
                           and d['name'] != '<exports>'][:6]
                if options:
                    view.append('Related code (callers, overrides, copies; it keeps its code unless you name it with '
                                "'missing'):\n" + '\n'.join(f'  {d["name"]} ({d["rel"]}) — {d["reason"]}: '
                                                             f'{_link_line(root, d)}' for d in options))
        others = [f'  {q["handle"]}: {q["sentence"]}' if q.get('sentence') else f'  {q["handle"]}: (to specify)'
                  for q in st['plan'] if q is not p]
        if others:
            view.append('The other places of the fix (each is edited by its own editor):\n' + '\n'.join(others))
        if st['repro']:
            ran = _key(p) in set(st['repro'].get('calls') or [])
            view.append('The reproduction test fails on the original code' + (' and runs this place' if ran else '')
                        + ':\n' + st['repro']['before'][:500])
        if p.get('hints'):
            view.append('What other specifiers said must change here:\n' + '\n'.join('  ' + h for h in p['hints']))
        if p.get('note'):
            label = ('Why it is in the plan: ' if p['note'].startswith('the specifier of') else
                     'Why it is specified again: ' if p['note'].startswith('you named') else 'Found after the edits: ')
            view.append(label + p['note'][:900].replace('named this code;', f'named {p["handle"]} for the change;'))
            diff = step._patch_here(root, p)
            if diff:
                view.append(diff[:1500])
            if p.get('old_sentence'):
                view.append('The sentence before: ' + p['old_sentence'])
            if p.get('edited'):
                # gm11 (the 31B): 'no change' for a place sent back by the review left the edit that broke a test
                view.append(f'Its edit is in the patch now. Send the sentence for {p["handle"]} as it must end up, or '
                            "['no change'] to undo its edit and keep its original code.")
        if p.get('diag'):
            view.append(f'Your finding: these lines make it fail:\n{p["diag"]["lines"]}\nWhat they do now: '
                        f'{p["diag"]["now"]}')
        ids = ', '.join(r['id'] for r in reqs) or 'its requirements'
        # B1 (gm4-gm9, gk14b, gk15): the numbered view was the anchor the sentences used ("after line 56", "line
        # 103"), then refused; the specifier names code by its text, so it reads it without numbers
        text = NUMBERED.sub(r'\1', '\n\n'.join(view))
        # gm11: "lines 356-519" and "(lines 371-454 not shown)" gave the sentence its "line 373"
        text = re.sub(r', lines \d+-\d+\)', ')', text)
        text = re.sub(r' \(line \d+\):', ':', text)     # the related code's link lines (B1)
        text = re.sub(r'\(lines (\d+)-(\d+) not shown\)', lambda m: f'({int(m.group(2)) - int(m.group(1)) + 1} lines not '
                      'shown)', text)
        plural = ',' in ids or ids == 'its requirements'
        if _diagnosing(p):
            return (text + f'\n\n{ids} {"do" if plural else "does"} not hold in the current code: that is why the issue '
                    f'exists, and {p["handle"]} was located as where {"they are" if plural else "it is"} decided. First '
                    f'find what breaks {ids} here, then what must change, in one call: the lines of {p["handle"]} that '
                    f'make {ids} fail (copied as written above), what they do now, and what they must do instead (one '
                    'sentence, complete in itself, with the exact names: the editor sees only this place). If this '
                    f"place already does what {ids} {'say' if plural else 'says'}, send ['no change', '<the line that "
                    "already does it>']; if the change belongs in other code (a function this place calls, for "
                    "example), send ['missing', '<its name>', '<what must change there>'].\n\n" + nxt)
        return (text + f'\n\nWrite what must change in {p["handle"]} so that {ids} ' + ('hold' if plural else 'holds') + ': one '
                'sentence, complete in itself (its editor sees only this place), with the exact names. Or no change. '
                'If the change belongs in other code (a function this place calls, for example), name that code with '
                "'missing': it joins the plan and is specified first, then this place again."
                '\n\n' + nxt)
    self_named = False
    if _args.clean(items[0]).lower() == 'missing':
        # gm11b (the 31B, fastapi_12942): the fix was in field_annotation_is_scalar, which analyze_param calls; the
        # specifier saw it, believed it could only change analyze_param, and wrote a workaround there
        if len(items) < 2:
            return "Name the other code: ['missing', '<its name>', '<what must change there>'].\n\n" + nxt
        name, what = _args.clean(items[1]), ' '.join(items[2:]).strip()
        q = next((_place_by(st, n) for n in _missing_names(name) or [name] if _place_by(st, n)), None)
        if q is p:
            if not what:
                return f'{name} is this place. Send what changes here, or [\'no change\'].\n\n' + nxt
            # V23 (rich_3521): the specifier named its own place with what must change there; that is its sentence
            items, self_named = [what], True
    if not self_named and _args.clean(items[0]).lower() == 'missing':
        if (p.get('note') or '').startswith('the specifier of'):
            # V23 (fastapi_14986): get_openapi named OpenAPI, which named Server, then both were 'no change' (170 s);
            # code named by a specifier names no further code
            # vf3 audit (fastapi_14361): the rule came without its reason while NEXT still offered 'missing' (P3, B12)
            return (f'{p["handle"]} was itself named for this change by another specifier, and code named that way '
                    'names no further code: each step down the calls costs about a minute of the 5-minute budget, so '
                    f"the fix stays one step from where the requirement was located. Send what changes in {p['handle']}, "
                    "or that it keeps its code.\n\n" + next_call('specify.py', *_specify_forms(p, may_name=False)))
        if q is not None and q.get('status') in ('nochange', 'done', 'failed'):
            # V23 (rich_3521): Segment._split_cells had been specified 'no change'; the specifier of split_cells named
            # it for the change three times and was refused each time
            pass
        elif q is not None:
            # vf8 lab (fastapi_14419, rich_3180, rich_3934): 'missing' naming a planned place was answered "its own
            # specifier writes its change" (false when the same specifier gets it next) with a NEXT that left out the
            # form this place needed; the call was sent again 3 times. What it says is kept for that place (B5, B11)
            said = f' (its sentence: {q["sentence"]})' if q.get('sentence') else ''
            if what:
                q.setdefault('hints', [])
                if what not in q['hints']:
                    q['hints'].append(f'the specifier of {p["handle"]}: {what[:300]}')
            kept = (' What you said must change there is kept and shown when it is specified.' if what and
                    not q.get('sentence') else '')
            p['named_planned'] = True
            return (f'{q["handle"]} is already a place of the fix{said}.{kept} Now say what changes in {p["handle"]} '
                    'itself, or that it keeps its code.\n\n' + next_call('specify.py', *_specify_forms(p, may_name=False)))
        n0 = (_missing_names(name) or [name])[0]
        if not any(not _repo.is_doc_path(c['rel']) for c in step._resolve(root, [n0])) \
                and not ('.' in n0 and step._new_definition_owner(root, n0)):
            return (f'No code named {name} was found. Send its qualified name (Class.method, or path.py::name), or what '
                    f"changes here.\n\n" + nxt)
        # gm12 (the 31B, rich_3052): "the specifier of PromptBase.ask says: Add case_sensitive ... set
        # self.case_sensitive" was read by the specifier of PromptBase.__init__ as a change for ask; the note now says
        # that the change is for the code being specified (its name is put in when the view is shown)
        st['missing'].append([name, f'the specifier of {p["handle"]} named this code; what it says must change here: '
                              f'{what[:300] or "(not said)"}',
                              list(p['reqs']), _key(p)])
        p['sentence'], p['note'] = None, f'you named {name} for the change; specify this place again, seeing its sentence'
        _reset_asks(st, 'specify', p)
        return (f'{name} goes into the plan: it is specified next, then {p["handle"]} again.\n\n{DONE}')
    ids = ', '.join(p['reqs']) or 'the requirements'
    first = _args.clean(items[0])
    if step.NO_CHANGE.match(first or '') and not p.get('edited') and not p.get('new') and DIAGNOSE:
        evidence = ' '.join(items[1:]).strip()
        if not _in_place(root, p, evidence, body_only=True) and p.get('nc_refused', 0) < NC_REFUSALS:
            p['nc_refused'] = p.get('nc_refused', 0) + 1
            # lab 10-18 (rich_3180, twice): the def line was refused as "a line that is not in" the place, which is false
            what = ('names no line' if not evidence else
                    f'names only the signature, decorators or docstring of {p["handle"]}, which do not do what a '
                    f'requirement says' if _in_place(root, p, evidence) else
                    f'names a line that is not in {p["handle"]}')
            return (f"This 'no change' {what}. 'no change' means a line of {p['handle']} already does what {ids} "
                    f"says: send it with that line copied as written, ['no change', '<that line>']. If no line does "
                    f'it, {ids} fails here: send the lines that make it fail and what they do now.\n\n'
                    + nxt)
        items = ['no change']
    elif _diagnosing(p) and len(items) >= 3 and _in_place(root, p, items[0]):
        # g10: the finding and the sentence in one call (each decision call fills the thinking budget; two calls per
        # place doubled specify's time); the finding is written first, the sentence last
        p['diag'] = {'lines': items[0].strip()[:800], 'now': items[1].strip()[:400]}
        items = [' '.join(x.strip() for x in items[2:])]
    elif _diagnosing(p) and len(items) >= 2:
        lines, now = items[0], ' '.join(items[1:]).strip()
        if not _in_place(root, p, lines) and p.get('diag_refused', 0) < NC_REFUSALS:
            p['diag_refused'] = p.get('diag_refused', 0) + 1
            return (f'None of these lines is in {p["handle"]} as written: copy one or more lines from its code above, '
                    'then say what they do now.\n\n' + nxt)
        p['diag'] = {'lines': lines.strip()[:800], 'now': now[:400]}
        return (f'Saved: these lines of {p["handle"]} make {ids} fail:\n{p["diag"]["lines"]}\nWhat they do now: '
                f'{p["diag"]["now"]}\n\nNow write what {p["handle"]} must do instead so that {ids} '
                f"{'hold' if ',' in ids else 'holds'}: one sentence, complete in itself (its editor sees only this "
                'place), with the exact names.\n\n' + nxt)
    sentence = ' '.join(x.strip() for x in items)
    if re.search(r'\blines?\s+\d+', sentence, re.I) and not p.get('ln_warned'):
        # gm4-gm9 (Gemma): "After line 56, add ...", "Replace line 154 ..."; numbers move after each edit and the editor
        # sees its own window (checklist B1)
        p['ln_warned'] = True
        return ("Name the code by its text, not by line numbers (they move after each edit, and the editor's window "
                "numbers its own lines): for example 'after the line `PYDANTIC_V2 = ...`' or 'replace "
                "`return self.router.on_event(event_type)` with ...'. Send the sentence again.\n\n" + nxt)
    if len(sentence) > MAX_SENTENCE:
        return f'Too long ({len(sentence)} characters, at most {MAX_SENTENCE}). Send it shorter.\n\n{nxt}'
    if step.NO_CHANGE.match(sentence) and FAST and not p.get('nc_warned') and not any(
            q.get('sentence') not in ('no change',) and q is not p for q in st['plan']):
        # vf3 audit (rich_4075, rich_4076): every place got 'no change' and the patch stayed empty; this answer is the
        # last that could carry a change, a new fact, said once (F9; not a bare 'are you sure', F4)
        p['nc_warned'] = True
        ids = ', '.join(p['reqs']) or 'the requirements'
        return (f'With this, no place of the fix changes: the patch stays empty and {ids} would not be met. If '
                f"{p['handle']} must change for {ids}, send what changes here; if the change belongs elsewhere, send "
                "['no change'] again.\n\n" + nxt)
    if step.NO_CHANGE.match(sentence):
        p['sentence'], p['status'] = 'no change', 'nochange'
        if p.get('edited'):
            others = [q for q in st['plan'] if q is not p and q['rel'] == p['rel'] and q.get('edited')]
            if not others:
                _repo.git(root, 'checkout', '--', p['rel'])
                p['edited'] = False
                _refresh(root, p)
                p.pop('note', None)
                return f'{p["handle"]} keeps its original code: its edit is undone.\n\n{DONE}'
    else:
        p['sentence'], p['status'] = sentence, 'todo'
        p['fails'], p['edits'], p['refused'] = 0, 0, 0
    p.pop('note', None)
    if p['status'] == 'nochange':
        return f'{p["handle"]} keeps its code: it is not edited.\n\n{DONE}'
    if LITERAL.match(sentence) and not p.get('new'):
        return f'Saved for {p["handle"]}: the script makes this replacement.\n\n{DONE}'
    return f'Saved for {p["handle"]}: its editor writes the change from this sentence.\n\n{DONE}'


# ------------------------------------------------------------------------------------------- 6 edit

IMPORT = re.compile(r'^(from\s+\S+\s+import\s+.+|import\s+\S.*)$')


def _split_imports(code):
    lines = code.split('\n')
    imports = []
    while lines and (IMPORT.match(lines[0].strip()) or not lines[0].strip()):
        if lines[0].strip():
            imports.append(lines[0].strip())
        lines.pop(0)
    return imports, '\n'.join(lines)


def _add_imports(root, rel, imports):
    if not imports:
        return []
    lines = _repo.read_lines(root, rel)
    have = {l.strip() for l in lines}
    todo = [i for i in imports if i not in have]
    if not todo:
        return []
    tree = _code.parse(''.join(lines))
    at = 0
    for node in (tree.body if tree else []):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            at = node.end_lineno
        elif isinstance(node, ast.Expr) and isinstance(getattr(node, 'value', None), ast.Constant) and at == 0:
            at = node.end_lineno
        else:
            break
    new = lines[:at] + [i + '\n' for i in todo] + lines[at:]
    _repo.write_lines(root, rel, new)
    return todo


def _edit_target(root, p):
    """The place dict _whole_edit works on: the place itself, or for new code its class or its file's top level."""
    if p.get('new') and p.get('status') != 'done':
        if p.get('owner') and p['owner'] != MODULE:
            return {'rel': p['rel'], 'name': p['owner'], 'start': p['start'], 'end': p['end'], 'handle': p['owner']}
        n = len(_repo.read_lines(root, p['rel']))
        return {'rel': p['rel'], 'name': MODULE, 'start': 1, 'end': n, 'handle': p['rel']}
    return {'rel': p['rel'], 'name': p['name'], 'start': p['start'], 'end': p['end'], 'handle': p['handle']}


ELIDED = re.compile(r'^(\s*)(async\s+)?def\s+(\w+)\s*\(\s*\.\.\.\s*\)\s*(->[^:]*)?:\s*$', re.M)


def _keep_signature(root, p, body):
    """'def name(...):' and a new body: the place's own decorators, signature and docstring are kept and only the rest
    of its body is replaced (fastapi_14873: APIRouter.__init__'s 217 lines of parameters are hidden in the window,
    and the editor could not send the whole definition). Returns the whole new definition, or None."""
    m = ELIDED.search(body)
    if not m:
        return None
    lines = _repo.read_lines(root, p['rel'])
    tree = _code.parse(''.join(lines))
    node = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == m.group(3) and p['start'] <= n.lineno <= p['end']), None) if tree else None
    if node is None:
        return None
    first = node.body[0]
    keep_to = first.lineno - 1
    new_body = body[m.end():].strip('\n')
    has_doc = isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant) \
        and isinstance(first.value.value, str)
    if has_doc and not re.match(r'\s*[rRbBuU]?("""|\'\'\'|"|\')', new_body):
        keep_to = first.end_lineno
    pad = step._indent_of(lines[first.lineno - 1])
    head = ''.join(lines[min(d.lineno for d in node.decorator_list) - 1 if node.decorator_list else node.lineno - 1:keep_to])
    return head, pad, step.textwrap.dedent(new_body), node


def _unescaped(text):
    """Lines sent with their breaks written as \\n (checklist B15: every answer reaches the model as JSON) as real
    lines, when the text holds no real line break. gm4 (fastapi_14583, Gemma): three replace calls with '\\n7|\\n8|from
    ._compat.may_v1 import ...' were all 'not found'."""
    if '\n' not in text.strip('\n') and '\\n' in text:
        text = text.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\t', '\t')
    return text


def _replace_text(root, p, old, new):
    """['replace', old lines, new lines] inside the place: the old lines are found by their text (leading and trailing
    spaces ignored, once in the place), the new lines are re-indented to the first old line. (start, end, text) or an
    error string."""
    lines = _repo.read_lines(root, p['rel'])
    want = [l.strip() for l in old.strip('\n').split('\n') if l.strip()]
    if not want:
        return 'the old lines are empty'
    hits = []
    for i in range(p['start'] - 1, min(p['end'], len(lines))):
        if lines[i].strip() != want[0]:
            continue                # a match starts on its first old line (requests_7328: counted twice from a blank)
        j, k = i, 0
        while j < len(lines) and k < len(want):
            if not lines[j].strip():
                j += 1
                continue
            if lines[j].strip() != want[k]:
                break
            j, k = j + 1, k + 1
        if k == len(want):
            hits.append((i + 1, j))
    if not hits:
        return 'the old lines were not found in this place (send them as the window shows them)'
    if len(hits) > 1:
        return f'the old lines occur {len(hits)} times in this place: send more lines around them'
    a, b = hits[0]
    pad = step._indent_of(lines[a - 1])
    text = step.textwrap.indent(step.textwrap.dedent(new.strip('\n')), pad) if new.strip() else ''
    return a, b, text


def edit(root, st, args):
    name = (st['current'] or (None, None))[1]
    p = _place_by(st, name) if name else None
    if p is None:
        return 'Nothing to edit now.\n\n' + DONE
    _refresh(root, p)
    if p.get('coupled_by'):
        return (f'{p["handle"]} was changed together with {p["coupled_by"]} in one edit (its diff):\n'
                + (step._patch_here(root, p) or '')[:1500] + f'\nNothing more is needed for it.\n\n{DONE}')
    items = [a for a in args if a and a.strip()]
    assign = _assign_place(root, p)
    what = ('top-level code of ' + p['rel'] if p['name'] == MODULE else f'assignment {p["name"]} = ...' if assign
            else 'definition of ' + p['handle'].split('::')[-1].split('.')[-1])
    nxt = next_call('edit.py', f"['<the whole new {what}, with any new imports above it>']", 'replace', 'no change',
                    'missing edit', sep=', or ')
    first = _args.clean(items[0]).lower() if items else ''
    long_place = p['end'] - p['start'] + 1 > LONG_EDIT and not p.get('new')
    if long_place:
        # vf3 audit (fastapi_13713): after the first answer a long place was offered the whole-definition form the
        # window forbids (B12); one form for every answer of the place, every group of lines in one call
        nxt = next_call('edit.py', 'replace groups', 'no change', 'missing edit', sep=', or ')
    # gm9 (Gemma): ['open', 'FastAPI.setup_openapi'] was read as code to apply; a first item 'open' opens the place
    if not items or first in ('open', 'start') or (first == p['handle'].lower() and len(items) == 1):
        reqs = [_req(st, rid) for rid in p['reqs'] if _req(st, rid)]
        view = [_place_view(root, st, p, limit=150), f'What to change here: {p["sentence"]}']
        if not p.get('new'):
            ctx = step._context_view(root, p, _focus(reqs))
            if ctx:
                view.append(ctx[:2500])
            deciding = _deciding_calls(root, st, p, ctx)
            if deciding:
                view.append(deciding)
            diff = step._patch_here(root, p)
            if diff:
                view.append(diff[:1500])
        length = p['end'] - p['start'] + 1
        if length > LONG_EDIT and not p.get('new'):
            # g1 (fastapi_9555, the local 31B): the whole 140-line solve_dependencies, sent again after two replace
            # edits, came out malformed three times (2 minutes, about 2,100 tokens each)
            view.append(f'The change: {_brief(p["sentence"])}')
            # vf3 audit (rich_3718, fastapi_13713): "one call per group of lines" while the first accepted replace
            # closed the place: half of each change was lost
            view.append(f"This place is long ({length} lines): change only the lines that change, and send every "
                        "group of lines this change needs in one call: ['replace', '<old lines 1, as shown>', '<new "
                        "lines 1>', '<old lines 2>', '<new lines 2>', ...]; the rest stays as it is. A new import can "
                        "open the new lines, not indented: it is moved to the top of the file.")
            return '\n\n'.join(view) + '\n\n' + nxt
        # P6: the decision again just before NEXT (the views run to 7,000 characters)
        view.append(f'Send the whole new assignment {p["name"]} = ... that does this: {_brief(p["sentence"])}'
                    if assign else
                    f'Send the whole new definition of {p["handle"]} (from its def or class line, with its '
                    f'decorators) that does this: {_brief(p["sentence"])} New imports go above it and are moved to the '
                    'top of the file. A new helper function can come with it.')
        if re.search(r'not shown\)', view[0]):
            view.append("Part of this place is not shown. Then either send 'def <name>(...):' followed by the whole "
                        "new body (the parameters, decorators and docstring stay as they are), or change some lines: "
                        "['replace', '<the old lines, as shown>', '<the new lines>'].")
        return '\n\n'.join(view) + '\n\n' + nxt
    if first in ('no change', 'skip', 'nothing'):
        if p.get('status') != 'done':
            p['status'] = 'nochange'
        return f'{p["handle"]} keeps its code.\n\n{DONE}'
    if first == 'missing' and len(items) >= 2 and not _names_code(root, items[1]):
        # hk30 (fastapi_13713): 'parameter list (lines 65-822)' was taken as other code; the plan got nothing new and
        # the same window came back four times. The form is described, not quoted (checklist B5)
        p['refused'] = p.get('refused', 0) + 1
        return ("'missing' names other code by its name (Class.method, a function, or path.py::name) and no code of "
                f"that name was found. To change lines of {p['handle']} itself, send ['replace', '<the old lines, as "
                f"shown>', '<the new lines>'] or the whole new definition.\n\n{nxt}")
    if first == 'missing' and len(items) >= 2:
        done = [q for n in _missing_names(items[1]) for q in [_place_by(st, n)] if q is not None
                and q is not p and q.get('status') == 'done']
        if done:
            # s-st4 (fastapi_14851): the editor of on_event named APIRouter.__init__ as missing four times after
            # it was edited, and each answer sent __init__ back to be specified and edited again
            diffs = '\n'.join((step._patch_here(root, q) or f'{q["handle"]} is edited.')[:1500] for q in done[:2])
            return (f'{", ".join(q["handle"] for q in done)} is already changed by this fix:\n{diffs}\n'
                    f'Make the change of {p["handle"]} with that code in place.\n\n{nxt}')
        pending = [q for n in _missing_names(items[1]) for q in [_place_by(st, n)] if q is not None and q is not p
                   and q.get('status') in (None, 'todo')]
        if pending and FAST:
            # vf3 audit (fastapi_14372): 'missing' named a place already in the plan; the answer said it went into the
            # plan before this one, and gave this place straight back (B12): the fact, and the two ways on
            q = pending[0]
            who = (_claims(st).get(f'edit {q["handle"]}') or _claims(st).get(f'specify {q["handle"]}'))
            return (f'{q["handle"]} is already a place of this fix' + (f', edited now by {who}' if who else '')
                    + (f' from its sentence: {q["sentence"]}' if q.get('sentence') not in (None, 'no change') else '')
                    + f'. Make the change of {p["handle"]} as if {q["handle"]} is changed that way, or, when both '
                    f'definitions are in {p["rel"]} and must change together, send the whole new definitions of both '
                    'in one item: they are applied and checked together.\n\n' + nxt)
        st['missing'].append([_args.clean(items[1]), f'the editor of {p["handle"]} needs it: ' + ' '.join(items[2:])[:300],
                              list(p['reqs']), _key(p)])
        _reset_asks(st, 'edit', p)   # asked again after the new code
        return (f'{_args.clean(items[1])} goes into the plan before {p["handle"]}; {p["handle"]} is edited after it.'
                f'\n\n{DONE}')
    if p.get('edits', 0) >= MAX_EDITS or p.get('refused', 0) >= MAX_REFUSED:
        return f'{p["handle"]} has had {MAX_EDITS} edits; it stays as it is.\n\n{DONE}'
    # hk30 (rich_3043): sends refused before anything was applied counted as edits, and the place was given up after
    # four refusals (checklist F6: refusals stay off the failure count); they are counted apart, with a higher bound
    p['edits'] = p.get('edits', 0) + 1
    if first == 'replace' and len(items) >= 5 and len(items) % 2 == 1:
        # several groups of lines in one call: applied in order, all or none
        before = _repo.read_lines(root, p['rel'])
        groups = [(_args.code(_unescaped(items[k])), _args.code(_unescaped(items[k + 1]))) for k in range(1, len(items), 2)]
        r = None
        for n, (old_text, new_text) in enumerate(groups, 1):
            res = _replace_text(root, p, old_text, new_text)
            if not isinstance(res, str):
                r = step._edit.apply(root, p['rel'], res[0], res[1], res[2] or 'DELETE')
                if not r.applied:
                    res = r.error
            if isinstance(res, str):
                _repo.write_lines(root, p['rel'], before)
                _refresh(root, p)
                _unspent(p)
                return f'Not applied (nothing was changed): group {n} of {len(groups)}: {res}.\n\n{nxt}'
            _refresh(root, p)
        return _after_edit(root, st, p, r, before, [p['name'].split('.')[-1]], nxt)
    if first == 'replace' and len(items) >= 2:
        before = _repo.read_lines(root, p['rel'])
        items = [items[0]] + [_unescaped(x) for x in items[1:]]
        new_text = _args.code(items[2]) if len(items) > 2 else ''
        head = []
        rest = new_text.split('\n')
        old_text = _args.code(items[1])
        while p['name'] != MODULE and rest and (not rest[0].strip() or (
                rest[0][:1] not in (' ', '\t') and IMPORT.match(rest[0].strip()) and rest[0].strip() not in old_text)):
            # gm3 (fastapi_14583, Gemma): in a file's top-level code an import line is the place's own content; moving
            # 'from collections.abc import Sequence' (already there) to the top deleted it
            # an import at column 0 opening the new lines goes to the top of the file
            if rest[0].strip():
                head.append(rest[0].strip())
            rest.pop(0)
        if head:
            new_text = '\n'.join(rest)
            _add_imports(root, p['rel'], head)
            _refresh(root, p)
        res = _replace_text(root, p, _args.code(items[1]), new_text)
        if isinstance(res, str) and 'not found' in res and new_text.strip() and \
                not isinstance(_replace_text(root, p, new_text, new_text), str):
            # gm11 (the 31B): after an accepted edit the editor sent the same replace again and read 'not found' as a
            # failure, then reopened the place
            if head:
                _repo.write_lines(root, p['rel'], before)
                _refresh(root, p)
            line = (f"edit is done: {p['handle']} is changed. Next: {st['tentative']}." if st.get('tentative')
                    else None)
            _unspent(p)
            if FAST:
                return ('Already applied: this place already holds these new lines (your earlier edit is in the '
                        f'patch).\n\n{DONE}')
            return ('Already applied: this place already holds these new lines (your earlier edit is in the patch). '
                    + (f'If the change is complete, reply to the coordinator without a call, with exactly this line:\n'
                       f'{line}\n\nNEXT: no call: reply with exactly that line.' if line else
                       f'If the change is complete, reply with one sentence for the coordinator.\n\n{DONE}'))
        if isinstance(res, str):
            if head:
                _repo.write_lines(root, p['rel'], before)
                _refresh(root, p)
            _unspent(p)
            return f'Not applied: {res}.\n\n{nxt}'
        r = step._edit.apply(root, p['rel'], res[0], res[1], res[2] or 'DELETE')
        return _after_edit(root, st, p, r, before, [p['name'].split('.')[-1]], nxt)
    code = _args.code('\n'.join(items) if len(items) > 1 else items[0])
    imports, body = _split_imports(code)
    target = _edit_target(root, p)
    before = _repo.read_lines(root, p['rel'])
    added = _add_imports(root, p['rel'], imports)
    if added:
        _refresh(root, p)
        target = _edit_target(root, p)
    kept = _keep_signature(root, p, body) if not p.get('new') and not assign else None
    if assign:
        new = step.textwrap.dedent(body).strip('\n')
        tree = _code.parse(new)
        if tree is None or len(tree.body) != 1 or step.assignment_span(new, p['name']) is None:
            if added:
                _repo.write_lines(root, p['rel'], before)
                _refresh(root, p)
            _unspent(p)
            return (f'Not applied: what was sent is not one assignment {p["name"]} = ... (whole, as Python code); to '
                    f"change some of its lines send ['replace', '<the old lines, as shown>', '<the new lines>'].\n\n"
                    + nxt)
        r = step._edit.apply(root, p['rel'], p['start'], p['end'], new)
    elif kept:
        head, pad, new_body, node = kept
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        r = step._edit.apply(root, p['rel'], start, node.end_lineno,
                             head.rstrip('\n') + '\n' + step.textwrap.indent(new_body.strip('\n'), pad))
    elif p.get('group') and not p.get('new'):
        # several definitions of one name: the code sent replaces all of them (re-indented to the first)
        lines_now = _repo.read_lines(root, p['rel'])
        pad = step._indent_of(lines_now[p['start'] - 1])
        r = step._edit.apply(root, p['rel'], p['start'], p['end'],
                             step.textwrap.indent(step.textwrap.dedent(body).strip('\n'), pad))
    else:
        r = step._whole_edit(root, target, body)
    names = sorted({m for m in re.findall(r'(?m)^\s*(?:async\s+)?(?:def|class)\s+(\w+)', body)})
    return _after_edit(root, st, p, r, before, names, nxt)


IMPORT_PROBE = ('import importlib, json, sys, traceback\nres = {}\n'
                'for m in sys.argv[1:]:\n'
                '    try:\n        importlib.import_module(m)\n'
                '    except BaseException as e:\n'
                '        res[m] = traceback.format_exception_only(type(e), e)[-1].strip()\n'
                'print("SWEIMPORT" + json.dumps(res))\n')


def _probe_imports(root, mods):
    try:
        out = subprocess.run([sys.executable, '-c', IMPORT_PROBE] + mods, cwd=root, env=_tests._env(root),
                             capture_output=True, text=True, timeout=60).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r'SWEIMPORT(\{.*\})', out)
    return json.loads(m.group(1)) if m else None


def _import_errors(root, rel, before):
    """[(module, error)] for the edited module when it no longer imports but did with the code just before the edit
    (no copy of the original code is needed: the file is put back for a second probe only when the first one fails).
    About a second; the per-edit test run is off, this check stays (the user's, 2026-10-14)."""
    if not rel.endswith('.py') or _repo.is_test_path(rel) or _repo.is_doc_path(rel):
        return []
    mods = _tests._module_names(rel)
    now = _probe_imports(root, mods)
    if not now:
        return []
    after = _repo.read_lines(root, rel)
    try:
        _repo.write_lines(root, rel, before)
        earlier = _probe_imports(root, list(now)) or {}
    finally:
        _repo.write_lines(root, rel, after)
    return [(m, err) for m, err in now.items() if m not in earlier]


def _used_names(lines, name):
    """The attributes read as <expr>.<attr> (not self) and the plain names called inside the definition `name`."""
    tree = _code.parse(''.join(lines))
    sym = next((x for x in _code.symbols(tree) if x.name == name), None) if tree is not None else None
    if sym is None:
        return None
    out = set()
    for n in ast.walk(sym.node):
        if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load) and not (
                isinstance(n.value, ast.Name) and n.value.id == 'self'):
            out.add(n.attr)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out.add(n.func.id)
    return out


def _dropped_uses(root, p, before, after):
    """Code of the repository the edit stopped using in this place that other code still uses: [(definition,
    [(rel, user, line)])]. fastapi_9555 (local 31B): the edit replaced dependant.is_gen_callable in solve_dependencies
    by its own inspect.unwrap check, while _solve_generator, called right after, still read is_gen_callable and kept
    the old behaviour (the reference changes the property itself)."""
    old, new = _used_names(before, p['name']), _used_names(after, p['name'])
    if not old or new is None:
        return []
    table, out = _impact.Table(root, docs=False), []
    for a in sorted(old - new):
        defs = [(r, d) for r, d in table.find(a) if not _repo.is_test_path(r)]
        if len(defs) != 1 or a.startswith('__'):
            continue
        r, d = defs[0]
        users = table.uses.get(a, []) + table.reads.get(a, []) + [c for c in table.calls.get(a, []) if c[1]]
        if len(users) > _impact.MAX_USES:
            continue
        users = [u for u in dict.fromkeys(users) if (u[0], u[1]) != (p['rel'], p['name']) and u[1] != d.name
                 and not _repo.is_test_path(u[0]) and not _repo.is_doc_path(u[0])]
        if users:
            out.append((d.name, users[:4]))
    return out[:3]


def _undefined(lines):
    """Names the file reads but never binds or imports (anywhere in it) and that are not builtins: None when the file
    does not parse or uses a star import. Loose on purpose (any binding anywhere counts), so that it only catches a
    name that nothing provides, the way a missing import is."""
    import builtins
    tree = _code.parse(''.join(lines))
    if tree is None:
        return None
    bound = set(dir(builtins)) | {'__file__', '__name__', '__doc__', '__spec__', '__path__', '__builtins__',
                                  '__loader__', '__package__', '__annotations__', '__class__'}
    loads = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            (loads if isinstance(n.ctx, ast.Load) else bound).add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if a.name == '*':
                    return None
                bound.add((a.asname or a.name).split('.')[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bound.update(n.names)
        elif isinstance(n, ast.alias):
            pass
        elif hasattr(ast, 'MatchAs') and isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            bound.add(n.name)
        elif hasattr(ast, 'TypeVar') and isinstance(n, getattr(ast, 'TypeVar')):
            bound.add(n.name)
    return loads - bound


def _unspent(p):
    """A send that changed nothing (refused before it was applied) is not an edit: it is counted apart."""
    p['edits'] = max(0, p.get('edits', 1) - 1)
    p['refused'] = p.get('refused', 0) + 1


def _after_edit(root, st, p, r, before, names, nxt):
    if not r.applied:
        _repo.write_lines(root, p['rel'], before)
        _unspent(p)
        err = r.error
        if 'line number' in err:
            # the base procedure's numbered form is not an answer form here (gm9: Gemma answered it with 'ls -R')
            err = ("what was sent is not a whole definition (a def or class line and its body); to change some of "
                   "its lines send ['replace', '<the old lines, as shown>', '<the new lines>']")
        return f'Not applied: {err}.\n\n{nxt}'
    after = _repo.read_lines(root, p['rel'])
    changed = {p['rel']: names + [l.strip() for l in after[r.start - 1:r.end]]}
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag in ('delete', 'replace'):
            changed[p['rel']].extend(l.strip() for l in before[i1:i2])
    if EDIT_TESTS:
        cover = step._status.changed_statements(before, after)
        verdict, detail, failures = _tests.check(root, changed, cover={p['rel']: cover} if cover else None,
                                                 undo=(p['rel'], before))
    else:
        verdict, detail, failures = 'UNCHECKED', 'the existing tests are not run after each edit', []
        broken = _import_errors(root, p['rel'], before)
        was, now = _undefined(before), _undefined(after)
        new_names = sorted(now - was) if was is not None and now is not None else []
        if new_names and not broken:
            # gk3 (fastapi_14583, Haiku): the edit called annotation_is_pydantic_v1, which the file does not import;
            # the import check cannot see a name used only inside a function, and the patch raised NameError
            verdict, failures = 'BROKEN', []
            # hk30 (fastapi_13713): the undefined name was a new parameter the sentence asked for, and "Import them"
            # sent the editor after an import (checklist F9, B12): the fact, then both ways
            owner = (f'a parameter of {p["handle"]} (in its def line)' if p['name'] != MODULE and not p.get('group')
                     else 'a parameter of the function that uses it')
            detail = ('the edit uses names that this file neither defines nor imports: ' + ', '.join(new_names[:5])
                      + f'. A value given by the callers is {owner}; code of another module is imported (an import '
                      'line above the definition, or opening the new lines, not indented)')
        elif broken:
            verdict, failures = 'BROKEN', [m for m, _ in broken]
            detail = 'the changed module cannot be imported any more:\n' + '\n'.join(
                f'  importing {m} fails: {err}' for m, err in broken[:3])
    shown = step._changed_view(before, after, p['rel'])
    if verdict == 'BROKEN':
        _repo.write_lines(root, p['rel'], before)
        why = ('after the edit its module cannot be imported' if detail.startswith('the changed module cannot')
               else 'the edit uses undefined names' if detail.startswith('the edit uses names')
               else 'the edit breaks existing tests that passed before')
        p['fails'] = p.get('fails', 0) + 1
        if p['fails'] >= MAX_FAILS:
            if p.get('respecs', 0) < 1:
                # F2: the failing tests go back to the specifier once, with the sentence they refuted (fastapi_14986:
                # 'drop the servers' was refused twice by tests that expect the root path in the response)
                p['respecs'] = p.get('respecs', 0) + 1
                p['old_sentence'], p['sentence'], p['status'] = p.get('sentence'), None, None
                p['note'] = ('edits made from the sentence below were undone twice: the module could not be '
                             'imported after them:\n' + detail[:900]) if why.startswith('after') else (
                             'edits made from the sentence below were undone twice: ' + detail[:900]
                             ) if why.startswith('the edit uses') else (
                             'edits made from the sentence below were undone twice: they break existing tests that '
                             'pass on the original code. Those tests show behaviour the issue keeps: write a change '
                             'that meets the requirement and keeps them passing (if the requirement, as written, '
                             'contradicts them, meet the issue\'s own sentence instead):\n' + detail[:900])
                cause = 'these tests' if why.startswith('the edit breaks') else 'this error'
                later = 'the repair agent writes its sentence again' if FAST else 'it goes back to be specified again'
                return (f'Undone twice: {why}:\n{detail}\nFor {p["handle"]}, {later}, with {cause}; nothing more is '
                        f'needed from you for it.\n\n{DONE}')
            p['status'] = 'failed'
            return (f'Undone: {why}:\n{detail}\nThat was the last try for '
                    f'{p["handle"]}: it keeps its code.\n\n{DONE}')
        same = [q for q in st['plan'] if q is not p and q['rel'] == p['rel'] and q.get('status') in (None, 'todo')]
        pair = (f'\n{p["rel"]} also holds {same[0]["handle"]}, a place of this fix not changed yet: if the two must '
                'change together, send the whole new definitions of both in one item (they are applied and checked '
                'together).') if same and FAST else ''
        fix = 'corrected lines' if p['end'] - p['start'] + 1 > LONG_EDIT and not p.get('new') else 'a corrected definition'
        return (f'Undone: {why}, so {p["handle"]} is back as it was:\n'
                f'{detail}{pair}\nSend {fix} that fixes this.\n\n{nxt}')
    p['status'] = 'done'
    p['edited'] = True
    for q in st['plan']:
        if q is not p and q['rel'] == p['rel'] and q.get('status') in (None, 'todo') and \
                q['name'].split('.')[-1] in set(names) and not q.get('new'):
            # the whole new definitions of two places of one file, sent together (coupled code: a base class and its
            # subclass, fastapi_14372)
            q['status'], q['edited'], q['coupled_by'] = 'done', True, p['handle']
            q['sentence'] = q.get('sentence') or f'changed together with {p["handle"]}'
            for k in [k for k in _claims(st) if k.endswith(' ' + q['handle'])]:
                _claims(st).pop(k, None)
    if p.get('new'):
        p['new'] = False
        p['name'] = (p['owner'] + '.' if p.get('owner') not in (None, MODULE) else '') + p['name'].split('.')[-1]
    _refresh(root, p)
    word = {'OK': 'Kept and checked', 'UNCHECKED': 'Kept, not checked'}.get(verdict, 'Kept')
    dropped = _dropped_uses(root, p, before, after)
    if dropped:
        lines = [f'  {d}, still used by ' + ', '.join(f'{u} ({r}:{l})' for r, u, l in users) for d, users in dropped]
        first = dropped[0][0]
        if FAST:
            return (f'{word}: {detail}.\n{shown}\n\nThis edit stops using code that other code still uses, and that '
                    'code keeps the old behaviour:\n' + '\n'.join(lines) + '\nIf the requirement must hold there too, '
                    f"the change belongs in that code itself: send ['missing', '{first}', '<what must change in it>'] (it "
                    "goes into the plan and is changed after). If not, send ['no change'] to go on."
                    '\n\n' + next_call('edit.py', f"['missing', '{first}', '<what must change in it>']", 'no change',
                                         sep=', or ') + '.')
        return (f'{word}: {detail}.\n{shown}\n\nThis edit stops using code that other code still uses, and that '
                'code keeps the old behaviour:\n' + '\n'.join(lines) + '\nIf the requirement must hold there too, '
                f"the change belongs in that code itself: send ['missing', '{first}', '<what must change in it>'] (it "
                'goes into the plan and is changed next). If not, reply with one sentence for the coordinator.'
                '\n\n' + next_call('edit.py', f"['missing', '{first}', '<what must change in it>']")
                + ', or no call: reply with one sentence for the coordinator.')
    fix = ("another ['replace', '<old lines>', '<new lines>']" if p['end'] - p['start'] + 1 > LONG_EDIT
           else 'the corrected whole definition')
    if FAST:
        # docs/design_fast.md: no coordinator line; the worker goes on with its next place (main_fast)
        return f'{word}: {detail}.\n{shown}\n\n{DONE}'
    call = _tentative(root, st)
    if call:
        # gk12: an accepted edit had no line for the coordinator, which then asked route.py (one call per edit);
        # the next call is computed on a copy of the state and made real when the next agent opens (main)
        st['tentative'] = call
        line = f'edit is done: {p["handle"]} is changed. Next: {call}.'
        return (f'{word}: {detail}.\n{shown}\n\nIf this is the whole change, reply to the coordinator without a call, '
                f'with exactly this line (its next call, which you pass on):\n{line}\nIf it is wrong or incomplete, '
                f'send {fix} instead.\n\nNEXT: no call: reply with exactly that line (or send {fix} to edit.py).')
    return (f'{word}: {detail}.\n{shown}\n\nIf this is the whole change, reply with one sentence for the coordinator, '
            f'without a call. If it is wrong or incomplete, send {fix}.\n\nNEXT: no call: '
            f'reply with one sentence for the coordinator (or send {fix} to edit.py).')


def _tentative(root, st):
    """The coordinator's next call if nothing changes, computed on a copy of the state (route changes it: the asks,
    the current step); None when it is not a call of a tool."""
    import copy
    try:
        out = route(root, copy.deepcopy(st), ['next'])
    except Exception:                                   # never a traceback for the model
        return None
    nxt = out.strip().split('\n')[-1]
    if not nxt.startswith('NEXT: call the tool'):
        return None
    return nxt[len('NEXT: '):].split('. If it replies')[0].split('; when it replies')[0].rstrip('.')


# ------------------------------------------------------------------------------------------- 7 verify

def _patch_files(root):
    return {rel: (''.join(_repo.read_lines(root, rel)) if os.path.exists(os.path.join(root, rel)) else None)
            for rel in _tests.changed_files(root) if not rel.endswith(REPRO_NAME)}


def _patch_sig(root):
    """A stable signature of the patch's files. hash() of a str changes with every process (PYTHONHASHSEED), and every
    script call is a new process: the old per-state cache never hit, and the regression check ran again at every
    review."""
    import hashlib
    files = _patch_files(root)
    h = hashlib.sha1()
    for k, v in sorted(files.items()):
        h.update(k.encode() + b'\0' + (v or '').encode('utf-8', 'replace') + b'\0')
    return h.hexdigest() if files else ''


def _regression_check(root, st):
    """The existing tests of the changed code, chosen by the changed names (no whole-suite call map), on the patch and on
    the original code, once per patch state. gk9 (fastapi_14986): the patch removed root_path from the served document,
    following a misread requirement, broke test_legitimate_root_path_still_appears, and every stage said "done": with
    the per-edit tests off nothing ran an existing test."""
    sig = _patch_sig(root)
    if not sig:
        return None
    cached = (_state.load('reg_cache', {}) or {}).get(sig)     # on disk: shared with the tentative route's copy
    if cached:
        st['reg'] = cached
        return cached
    changed = {}
    for p in st['plan']:
        if p.get('status') == 'done':
            changed.setdefault(p['rel'], [])
            if p['name'] != MODULE:
                changed[p['rel']].append(p['name'].split('.')[-1])
    if not changed:
        return None
    for rel in changed:
        # the changed lines' words choose the tests (the place's own name, 'openapi', is in hundreds of test files)
        diff = _repo.git(root, 'diff', '-U0', '--', rel) or ''
        changed[rel] += [l[1:].strip() for l in diff.splitlines()
                         if l[:1] in '+-' and not l.startswith(('+++', '---')) and l[1:].strip()][:60]
    # hk30 (fastapi_15661): "31 existing tests pass" came from tests that share the bare name 'main' and never ran the
    # changed script; whether they run the changed lines is measured, and said (checklist B11, F3)
    cover = {}
    for rel in changed:
        old = _repo.git(root, 'show', f'HEAD:{rel}')
        lines = step._status.changed_statements((old or '').splitlines(True), _repo.read_lines(root, rel))
        if lines:
            cover[rel] = lines
    verdict, detail, failures = _tests.check(root, changed, use_map=False, cover=cover or None)
    st['reg'] = {'sig': sig, 'verdict': verdict, 'detail': detail[:1200], 'failures': list(failures)[:8]}
    _state.save('reg_cache', {**(_state.load('reg_cache', {}) or {}), sig: st['reg']})
    return st['reg']


def _score(root, st):
    repro = 1 if st['repro'] and st['repro'].get('now_passes') else 0
    return [repro, -len(st['regressions']), len(_patch_files(root)) > 0]


ERROR_KIND = re.compile(r'^(?:E\s+)?([A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt))\b', re.M)


def _error_kind(text):
    """The exception class a failure message ends with ('AttributeError'), or 'AssertionError' for a failed assert."""
    kinds = ERROR_KIND.findall(text or '')
    return kinds[-1].split('.')[-1] if kinds else ''


def _changed_failure(repro):
    """True when the reproduction test still fails with the patch, but with another kind of error than on the original
    code and not on its assertion: gk13c (fastapi_15800) failed first with AttributeError (no frontend) and then with
    RuntimeError (the test's own 'dist' did not exist); that is not the requirement's verdict."""
    before, now = _error_kind(repro.get('before')), _error_kind(repro.get('now'))
    return bool(before and now and before != now and now != 'AssertionError')


def _rerun_repro(root, st):
    """The reproduction test on the patch as it is, once per patch state (cached on disk by the patch's signature)."""
    import hashlib
    sig = _patch_sig(root) + '|' + hashlib.sha1(st['repro']['code'].encode('utf-8', 'replace')).hexdigest()
    cache = _state.load('repro_cache', {}) or {}
    if sig not in cache:
        outcomes, msgs, _, _ = _run_repro(root, st['repro']['rel'], st['repro']['code'])
        passed = bool(outcomes) and all(v == 'passed' for v in outcomes.values())
        failing = [k for k, v in outcomes.items() if v != 'passed']
        cache[sig] = [passed, 'it passes' if passed else 'it still fails:\n' + (
            msgs.get(failing[0], '') if failing else 'it did not run')[:500]]
        _state.save('repro_cache', cache)
    st['repro']['now_passes'], st['repro']['now'] = cache[sig]
    return cache[sig][0]


def _twins(st, places):
    """Plan places that share a short name with these places but serve other requirements (FastAPI.frontend and
    APIRouter.frontend): gk13c's review of R2 did not see that FastAPI.frontend had changed after its own review."""
    shorts = {p['name'].split('.')[-1] for p in places if p['name'] != MODULE}
    return [q for q in st['plan'] if q not in places and q['name'].split('.')[-1] in shorts]


def _verify(root, st):
    files = _patch_files(root)
    sig = _patch_sig(root)
    if st['verified'] == sig or not files:
        return
    st['verified'] = sig
    if not VERIFY:
        # stage 7 is off (the user's, 2026-10-14): no existing tests, no call map, no rerun of the reproduction test;
        # the patch as it is becomes the last good state
        st['regressions'] = []
        st['last_good'] = {'score': _score(root, st), 'files': files}
        return
    changed = {}
    for p in st['plan']:
        if p.get('status') == 'done':
            changed.setdefault(p['rel'], []).append(p['name'].split('.')[-1])
    st['regressions'] = []
    if changed:
        verdict, detail, failures = _tests.check(root, changed)
        if verdict == 'BROKEN':
            m = _tests.callmap(root)
            for t in failures:
                owner = next((p for p in st['plan'] if p.get('status') == 'done' and t in set(m.get(_key(p), []))),
                             None) or next((p for p in st['plan'] if p.get('status') == 'done'), None)
                if owner:
                    st['regressions'].append([t, owner['handle']])
                    if st['round'] < MAX_ROUNDS:
                        owner['old_sentence'] = owner.get('sentence')
                        owner['sentence'], owner['note'] = None, f'the whole patch breaks the existing test {t}:\n{detail[:700]}'
    if st['repro']:
        outcomes, msgs, _, output = _run_repro(root, st['repro']['rel'], st['repro']['code'])
        passed = bool(outcomes) and all(v == 'passed' for v in outcomes.values())
        st['repro']['now_passes'] = passed
        failing = [k for k, v in outcomes.items() if v != 'passed']
        st['repro']['now'] = 'passes' if passed else (msgs.get(failing[0], '') if failing else output[-600:])[:900]
    score = _score(root, st)
    if st['last_good'] is None or score >= st['last_good']['score']:
        st['last_good'] = {'score': score, 'files': files}


# ------------------------------------------------------------------------------------------- 8 review

def review(root, st, args):
    rid = (st['current'] or (None, None))[1]
    r = _req(st, rid)
    if r is None:
        return 'Nothing to review now.\n\n' + DONE
    items = [a for a in args if a and a.strip()]
    nxt = next_call('review.py', 'done', 'not done', 'cannot tell', sep=', or ')
    places = [p for p in st['plan'] if rid in p['reqs']] or st['plan']
    first = _args.clean(items[0]).lower() if items else ''
    if not items or first in ('open', 'start', rid.lower()):
        view = [f'Does the patch do this?\n  {_req_line(r)}']
        for p in places:
            _refresh(root, p)
            diff = step._patch_here(root, p) if p.get('status') == 'done' else ''
            view.append(f'{p["handle"]} — planned: {p.get("sentence") or "(no sentence)"}; '
                        f'{ {"done": "edited", "nochange": "not changed", "failed": "the edit failed"}.get(p.get("status"), "not edited")}'
                        + (f'\n{diff[:1800]}' if diff else ''))
        if st['repro'] and st['repro'].get('rid') == rid:
            if not VERIFY:
                # gk7 (fastapi_14583, Haiku): with stage 7 off the kept test was never run on the patch, and the review
                # said "done" while the test still failed; only this test runs here (seconds)
                _rerun_repro(root, st)
            view.append('The reproduction test, on the original code:\n' + st['repro']['before'][:400] +
                        '\nWith the patch: ' + (st['repro']['now'] or 'not run again')[:500])
            if _changed_failure(st['repro']):
                view.append(f'It fails with another error than before ({_error_kind(st["repro"]["before"])} on the '
                            f'original code, {_error_kind(st["repro"]["now"])} now): either the change is still '
                            'missing a part, or the test fails for a reason of its own (something it uses that does '
                            'not exist). Decide from the code.')
        if st['regressions']:
            view.append('Existing tests the whole patch breaks: ' + ', '.join(t for t, _ in st['regressions'][:5]))
        reg = _regression_check(root, st) or {}
        if reg.get('verdict') == 'BROKEN':
            # hk30 (fastapi_14953): the failing snapshot held the very value the issue changes ('format': 'binary'),
            # and the answer called it "behaviour the issue keeps" (checklist B11, F4, B12): the fact, and what decides
            view.append('Existing tests that pass on the original code fail with the patch:\n' + reg['detail'] +
                        '\nA test that checks a value the issue changes fails as expected (the hidden tests replace '
                        'it); a test of behaviour the issue keeps must keep passing. The issue\'s own sentences decide '
                        'which one each test is.')
        elif reg.get('detail'):
            view.append('Existing tests of the changed code: ' + reg['detail'][:300])
        for q in _twins(st, places):
            diff = step._patch_here(root, q) if q.get('status') == 'done' else ''
            view.append(f'{q["handle"]} has the same name and serves {", ".join(q["reqs"])}: '
                        + ('its diff, for a change the two may need alike:\n' + diff[:1200] if diff else 'not edited.'))
        _, options = _related(root, st, places)
        near = [d['name'] for d in options[:8]]
        if near:
            view.append('Other code near these places (if the change is missing there): ' + ', '.join(near) + '.')
        return ('\n\n'.join(view) + "\n\nAnswer with what the code shows: ['done'] if the patch does this, ['not done', "
                "'<place>', '<what is missing>'] naming the one place that is wrong or missing, or ['cannot tell'].\n\n"
                + nxt)
    if first in ('done', 'yes', 'cannot tell', "can't tell", 'unknown'):
        repro = st['repro'] if st['repro'] and st['repro'].get('rid') == rid else None
        if repro and repro.get('now') and not repro.get('now_passes') and not _changed_failure(repro) \
                and not st.get('review_refused', {}).get(rid):
            # the test's output decides, not the opinion (design_stages.md, stage 8): s-st2 fastapi_14448 was
            # reviewed 'done' while its reproduction test still failed
            st.setdefault('review_refused', {})[rid] = True
            return ('The test of this requirement still fails with the patch:\n' + (repro.get('now') or '')[:600] +
                    '\nSo the patch does not do it yet: name the place that is wrong or missing and what is missing '
                    "there: ['not done', '<place>', '<what is missing>'].\n\n" + nxt)
        reg = st.get('reg') or {}
        if reg.get('verdict') == 'BROKEN' and not st.get('reg_refused', {}).get(rid) and not FAST:
            # (the fast build shows these failures in the open view already: a second ask adds no fact, F4)
            st.setdefault('reg_refused', {})[rid] = True
            return ('The patch breaks existing tests that pass on the original code:\n' + reg['detail'][:700] +
                    "\nIf they check behaviour the issue keeps, the patch is not done: name the place that is wrong "
                    "and what must change there: ['not done', '<place>', '<what is wrong>']. If they check a value "
                    "the issue changes, send ['done'] again.\n\n" + nxt)
        st['reviews'][rid] = {'verdict': 'done' if first in ('done', 'yes') else 'cannot tell'}
        return f'{rid}: {st["reviews"][rid]["verdict"]}.\n\n{DONE}'
    if first in ('not done', 'no') and len(items) >= 2:
        name, what = _args.clean(items[1]), ' '.join(items[2:])[:600]
        st['reviews'][rid] = {'verdict': 'not done', 'place': name, 'what': what}
        if st['round'] < MAX_ROUNDS:
            # gk12 (fastapi_14851): after the re-edit nothing reviewed R2 again; it is reviewed again once the
            # places it sent back are done (_next_tool)
            st.setdefault('recheck', []).append(rid)
            p = _place_by(st, name)
            if p is not None:
                p['old_sentence'] = p.get('sentence')
                p['sentence'], p['note'] = None, f'the review of {rid} says: {what}'
            else:
                st['missing'].append([name, f'the review of {rid} says: {what}', [rid], None])
        return f'{rid}: not done; {name} goes back to be specified and edited.\n\n{DONE}'
    return ("The answer was not read: its first item must be done, not done (then the place and what is missing) "
            "or cannot tell.\n\n" + nxt)


# ------------------------------------------------------------------------------------------- entry

HANDLERS = {'route': route, 'requirements': requirements, 'reproduce': reproduce, 'locate': locate,
            'scope': scope, 'specify': specify, 'edit': edit, 'review': review}
SCRIPT = {'route': 'route.py', 'requirements': 'req.py', 'reproduce': 'repro.py', 'locate': 'locate.py',
          'scope': 'scope.py', 'specify': 'specify.py', 'edit': 'edit.py', 'review': 'review.py'}


def _opens(st, args):
    first = _args.clean(args[0]).lower() if args and args[0] else ''
    target = str((st.get('current') or (None, ''))[1] or '').lower()
    return first in ('open', 'start') or (target and first == target)


def _chain(root, st, out, stage):
    """An agent's finished part ends with the coordinator's next call, computed now (gated run gh4: 78 model calls for
    5 places, a route.py call and the agent call for every agent step): the agent returns that line and the
    coordinator makes the call, without asking route.py. The line first says what the finished stage produced: gm8
    (Gemma) asked route.py instead, because "I don't know what R1 is, I didn't see the requirements"."""
    nxt = route(root, st, ['next']).strip().split('\n')[-1]
    if not nxt.startswith('NEXT: call the tool'):
        return out
    call = nxt[len('NEXT: '):].split('. If it replies')[0].split('; when it replies')[0].rstrip('.')
    m = re.match(r"call the tool (\w+) with request '(.*)'$", call)
    if m and m.group(1) == stage and st.get('batch', 0) + 1 < BATCH.get(stage, 1):
        # V22 (Kaggle, 5 min): each agent call cost about 21 s of ceremony (its open, its final reply, two coordinator
        # calls), 38% of the time; the same agent goes on with the next requirement or place of its own stage
        st['batch'] = st.get('batch', 0) + 1
        st['chained'] = None
        view = HANDLERS[stage](root, st, ['open'])
        return (out[:-len(DONE)].rstrip() + f"\n\nYour part goes on with {m.group(2)}, in the same way:\n\n" + view)
    st['batch'] = 0
    st['chained'] = call
    if stage == 'requirements':
        done = f"{len(st['reqs'])} requirements saved ({', '.join(r['id'] for r in st['reqs'])})"
    else:
        done = next((l.strip() for l in out.split('\n') if l.strip()), '').rstrip(':.')[:160]
    line = f'{stage} is done: {done}. Next: {call}.'
    # V22: a final reply took 328 tokens of thinking to copy one line
    # rich_3521 (gated run gk2): locate and review took the line for an order to themselves ("that isn't part of my
    # task, so I ignored it"); it is said to be the coordinator's next call, for the agent to pass on
    return out[:-len(DONE)] + ('Your part is done. The coordinator (the agent that called you) makes its next call from '
                               'your reply, so pass this line on to it: it is not a task for you.\n'
                               'There is nothing to decide: copy it.\n'
                               'NEXT: no call: reply with exactly this line, and nothing else:\n' + line)


def main(stage, argv):
    root = _repo.repo_root()
    if not _state.lock():
        print('This call was not run: the call sent before it is still running. Send it again when that one has '
              'answered.')
        return
    st = _state.load('stages') or _new()
    args = _args.split_single_quoted(list(argv))
    pending = st.get('chained')
    if st.get('tentative'):
        cur = (st.get('current') or ('',))[0]
        first = _args.clean(args[0]).lower() if args and args[0] else ''
        # gm11 (the 31B): the editor whose edit was accepted called ['open'] again to look at its place, and the next
        # call became real under it ("Nothing to edit now"); an agent opens with the request it received, so an open
        # of the place it already finished is not the next agent
        target = _args.clean(args[1]).lower() if first == 'open' and len(args) > 1 and args[1] else ''
        m = re.search(r"request '([^']*)'", st['tentative'])
        nxt_target = (m.group(1) if m else '').lower()
        cur_target = str((st.get('current') or (None, ''))[1] or '').lower()
        same_agent = bool(target) and target == cur_target and target != nxt_target
        if stage == 'route':
            st['tentative'] = None                  # route.py runs for real below
        elif not same_agent and (stage != cur or first in ('', 'open', 'start')):
            # the next agent opens after an edit that returned the next call: that call becomes real now. gk13b: an
            # edit after an edit (the same stage) was left out, and the second editor was shown the first place
            route(root, st, ['next'])
            st['tentative'] = None
        # else: the same editor sends a corrected edit; the call stays tentative until the next agent opens
    try:
        if pending and stage == 'route':
            # the coordinator called route.py instead of the line an agent returned: the same next step, not a new
            # ask (a second ask would spend the target's tries)
            out = f"NEXT: {pending}"
        elif pending and not (_opens(st, args) and (st.get('current') or ('',))[0] == stage):
            nxt_stage = (st.get('current') or ('',))[0]
            # gm11: the bare 'call the tool ...' was not read as an agent's finished line, and the coordinator asked
            # route.py first (one call more each time)
            out = ("This call was not used: your part is finished. Reply to the coordinator with exactly this line, "
                   f"without a call:\n{stage} is done. Next: {pending}.")
            if nxt_stage == stage:
                # the next agent does the same stage (edit after edit): a new agent starts with 'open'
                out += "\nIf you are a new agent that has just started, call your script with args ['open'] first."
            # gm3: an editor whose place went back to specify kept calling 'open' and was offered 'just started'
        else:
            st['chained'] = None
            out = HANDLERS[stage](root, st, args)
            if stage != 'route' and out.endswith(DONE):
                st['tentative'] = None              # _chain runs route.py for real
                out = _chain(root, st, out, stage)
    except Exception as e:                      # never a traceback for the model
        _state.record({'error': f'{stage} {type(e).__name__}: {e}'})
        import traceback
        _state.record({'trace': traceback.format_exc()[-2000:]})
        out = (f'The script hit an internal error ({type(e).__name__}). ' +
               ("Call route.py with ['next'] again.\n\n" + next_call('route.py', 'next') if stage == 'route' else DONE))
    _state.record({'stage': stage, 'args': [a[:300] for a in args][:8], 'answer': out[:300]})
    _state.save('stages', st)
    if len(out) > ANSWER_CHARS:
        out = out[:ANSWER_CHARS - 1500] + '\n      ... (cut) ...\n' + out[-1400:]
    print(out)


# ------------------------------------------------------------------------------- the fast build (docs/design_fast.md)
#
# No coordinator: the root is a SequentialAgent of stage agents (include_contents: none), and a stage with several
# items runs K identical workers in a ParallelAgent. Each worker has its own entry script (specify2.py: the slot is the
# file name, never sent by the model) and claims the next open item of its stage here; when an item is finished it goes
# on with the next one ("Your part goes on with ..."), and with none left it replies done. Script calls hold the state
# lock (_state.lock), so the workers' calls run one at a time while their model calls run at once.

FAST_DONE = 'NEXT: no call: reply with exactly: done'

REPRO_MIN_S = 150       # reproduce runs beside specify only with this much time left (V22: it took 30-225 s)
WORK_STAGE = {'repair': None}    # repair specifies and edits, by the state of its place


def _claims(st):
    return st.setdefault('claims', {})


def _claimed(st, stage, target, worker):
    owner = _claims(st).get(f'{stage} {target}')
    return owner is not None and owner != worker


def _take(st, stage, target, worker, handler):
    _claims(st)[f'{stage} {target}'] = worker
    st.setdefault('wcur', {})[worker] = [stage, target, handler]
    return target


def _release(st, worker, what=None):
    cur = st.setdefault('wcur', {}).pop(worker, None)
    if cur:
        _claims(st).pop(f'{cur[0]} {cur[1]}', None)
        st.setdefault('wfinished', {}).setdefault(worker, []).append(what or f'{cur[1]} is finished')


def _claim(root, st, stage, worker):
    """The next open item of stage for worker (its target, also set as the worker's current), or None."""
    if stage == 'requirements':
        if not st['reqs'] and not _claimed(st, stage, 'the issue', worker) and _ask(st, 'requirements'):
            return _take(st, stage, 'the issue', worker, 'requirements')
        if not st['reqs']:
            st['reqs'] = [{'id': 'R1', 'quote': '', 'what': 'Fix the issue as the statement describes.',
                           'names': list(st.get('names') or []), 'new': []}]
            _save_statement(st)
        return None
    if stage == 'reproduce':
        if not st['repro_done'] and (not any(r.get('concrete') for r in st['reqs']) or _left(st) < REPRO_MIN_S):
            st['repro_done'] = True
        if not st['repro_done'] and st['reqs'] and _ask(st, 'reproduce'):
            return _take(st, stage, _repro_req(root, st)['id'], worker, 'reproduce')
        return None
    if stage == 'locate':
        for r in st['reqs']:
            if r['id'] in st['located'] or _claimed(st, stage, r['id'], worker):
                continue
            if _ask(st, 'locate ' + r['id']):
                return _take(st, stage, r['id'], worker, 'locate')
            st['located'][r['id']] = _fallback_locate(root, st, r)
        return None
    if stage in ('specify', 'edit', 'repair'):
        if not st['scope_done']:
            if not any(st['located'].values()):
                for r in st['reqs']:
                    if r['id'] not in st['located']:
                        st['located'][r['id']] = _fallback_locate(root, st, r)
            if any(st['located'].values()):
                _make_plan(root, st, [])
        _add_missing(root, st)
    if stage == 'repair':
        # vf3 audit (fastapi_14372, fastapi_13713): repair wrote a sentence and then the code from it, two calls per
        # place, and nothing was applied when it started late; the review's finding is the sentence, and the editor
        # writes the code at once (W2, B10)
        for p in st['plan']:
            if p.get('sentence') is None and str(p.get('note') or '').startswith('the review of'):
                old = p.get('old_sentence') if p.get('old_sentence') not in (None, 'no change') else ''
                p['sentence'] = ((old + ' Also: ' if old else '') + 'what the review found missing here: '
                                 + p['note'].split(' says: ', 1)[-1])[:MAX_SENTENCE]
                p['status'] = 'todo'
                p['fails'], p['edits'], p['refused'] = 0, 0, 0
        if not _patch_files(root) and _left(st) > REVIEW_LEFT_S and st['plan'] and not st.get('empty_retry'):
            # vf3 audit: 2 of 8 tasks submitted an empty patch with 2.5 min left, every place kept as 'no change'
            q = _main_place(st) or st['plan'][0]
            st['empty_retry'] = True
            if q.get('status') in ('nochange', 'failed', None):
                q['sentence'] = ('The patch is empty: every place kept its code, but the issue asks for a change. Make '
                                 'the change here so that this holds: ' + '; '.join(_req(st, rid)['what'] for rid in
                                                                                     q['reqs'] if _req(st, rid)))[:MAX_SENTENCE]
                q['status'] = 'todo'
                q['fails'], q['edits'], q['refused'] = 0, 0, 0
    if stage in ('specify', 'repair'):
        for p in st['plan']:
            if p.get('sentence') is not None or _claimed(st, 'specify', p['handle'], worker):
                continue
            q = _copy_of(root, st, p)
            if q is not None and q.get('sentence') is not None and q.get('status') != 'failed' and not p.get('note'):
                # V23 (httpx_3672): a copy of the same code takes the other's sentence
                p['sentence'], p['status'], p['copy_of'] = q['sentence'], ('nochange' if q.get('status') == 'nochange'
                                                                           else 'todo'), _key(q)
                continue
            if q is not None and q.get('sentence') is None and _claimed(st, 'specify', q['handle'], worker):
                continue            # its copy is being specified now: it takes that sentence
            if _ask(st, f'specify {_key(p)} {p.get("respecs", 0)} {stage}'):
                return _take(st, 'specify', p['handle'], worker, 'specify')
            p['sentence'] = '; '.join(_req(st, rid)['what'] for rid in p['reqs'] if _req(st, rid)) or 'no change'
            p['status'] = 'todo'
        if stage == 'specify':
            return None
    if stage in ('edit', 'repair'):
        for p in st['plan']:
            if p.get('status') != 'todo' or _claimed(st, 'edit', p['handle'], worker):
                continue
            q = _copy_of(root, st, p)
            if q is not None and q.get('status') == 'done' and q.get('sentence') == p.get('sentence') \
                    and _apply_copy(root, st, p, q):
                continue
            if _try_literal(root, st, p):
                continue
            if _ask(st, f'edit {_key(p)} {p.get("respecs", 0)} {stage}'):
                p['opened'] = False
                return _take(st, 'edit', p['handle'], worker, 'edit')
            p['status'] = 'failed'
        return None
    if stage == 'review':
        _verify(root, st)
        for r in st['reqs']:
            if r['id'] in st['reviews'] or _claimed(st, stage, r['id'], worker):
                continue
            if st['repro'] and st['repro'].get('rid') == r['id'] and _patch_files(root) and _rerun_repro(root, st) \
                    and (_regression_check(root, st) or {}).get('verdict') != 'BROKEN':
                st['reviews'][r['id']] = {'verdict': 'done', 'by': 'the reproduction test passes'}
                continue
            if not _patch_files(root) or _left(st) < REVIEW_LEFT_S:
                st['reviews'][r['id']] = {'verdict': 'not reviewed'}
                continue
            if _ask(st, f'review {r["id"]}'):
                return _take(st, stage, r['id'], worker, 'review')
            st['reviews'][r['id']] = {'verdict': 'cannot tell'}
        return None
    return None


IDLE = {'requirements': 'The requirements are already saved.',
        'reproduce': 'No reproduction test is needed now.',
        'locate': 'Every requirement is located or taken by another agent.',
        'specify': 'Every place is specified or taken by another agent.',
        'edit': 'Every place is edited or taken by another agent.',
        'review': 'Nothing to review.',
        'repair': 'Nothing is left to change.'}


LATER = {'requirements': 'the locate agents find the code for each of them',
         'locate': 'the specify agents write what changes at each place',
         'specify': 'the edit agents change the code from each sentence',
         'edit': 'the review checks the patch against every requirement, and the repair agent changes what is still '
                 'missing',
         'review': 'the repair agent changes what the review found missing',
         'repair': 'the patch is submitted'}


def _summary(st, handler, target, body):
    """One line of what a worker did with an item, from the state (not the answer's first line)."""
    if handler == 'specify':
        q = _place_by(st, target)
        if q is not None:
            return f'{q["handle"]}: ' + (f'sentence "{q["sentence"][:140]}"' if q.get('sentence') not in (None, 'no change')
                                         else 'no change' if q.get('sentence') == 'no change' else 'sent back for other code')
    if handler == 'edit':
        q = _place_by(st, target)
        if q is not None:
            return f'{q["handle"]}: ' + {'done': 'edited', 'failed': 'its edits were undone', 'nochange': 'kept its code'
                                         }.get(q.get('status'), 'specified again first')
    if handler == 'locate':
        return f'{target}: located at ' + ', '.join(c['name'] for c in st['located'].get(target, [])) if st['located'].get(target) \
            else f'{target}: located'
    if handler == 'review':
        return f'{target}: ' + (st['reviews'].get(target) or {}).get('verdict', 'reviewed')
    if handler == 'requirements':
        return 'the requirements ' + '; '.join(f'{r["id"]}: {r["what"][:140]}' for r in st['reqs'])
    return f'{target}: ' + (body.split('\n')[0][:140] if body else 'done')


def _part_done(st, stage, worker):
    """The end of a worker's part, with what it did and what comes next (vf3 audit: a bare 'Your part is done' left
    specifiers doubting their result; 3 of 9 called 24-96 s more while the stage waited for them)."""
    did = [d for d in st.get('wfinished', {}).get(worker, [])]
    recap = ('You finished: ' + '; '.join(d[:200] for d in did[-4:]) + '. ') if did else ''
    final = ('until the locate agents start, sending the pairs again replaces them' if stage == 'requirements'
             else 'what you saved is final')
    return (f'Your part is done. {recap}Next, {LATER.get(stage, "the next agents go on")}; {final}, so nothing more is '
            f'needed from you.\n\n{FAST_DONE}')


def _finished(st, stage, done_before):
    """The answer to a worker that calls again after its part ended. vf3 audit: 'Your part is already finished: the
    issue: Saved 1 requirements:.' hid what was saved and why stopping is safe; workers that believed their part
    incomplete called 3 to 12 more times (F9, F7). The fact (this call was not applied), what was done, what happens
    next, and for the requirements how to replace them."""
    if stage == 'requirements' and st['reqs']:
        done = 'the requirements saved are ' + '; '.join(f'{r["id"]}: {r["what"][:160]}' for r in st['reqs'])
        extra = (' Sending the pairs again replaces them while no code is located yet; replying done hands them on.'
                 if not st['located'] else '')
    else:
        done = 'you finished ' + '; '.join(d[:200] for d in done_before[-4:])
        extra = (' Sending other names replaces your last choice until the specify stage starts.'
                 if stage == 'locate' and not st['scope_done'] else '')
    return (f'This call was not applied: your part is finished ({done}).{extra} Next, {LATER.get(stage, "the next agents go on")}, '
            f'so nothing more is needed from you (time used: {_elapsed(st) // 60} min {_elapsed(st) % 60} s).\n\n'
            + FAST_DONE)


def _idle(st, stage, worker):
    """The answer to a worker with nothing to take. vf1 (Gemma, fastapi_12942): the progress line listed places 'to
    specify' while the answer said every place was taken; the idle specifiers opened again 40 times, trying to find the
    place they were missing (checklist B12). The answer names who holds each item and leaves the plan out."""
    held = [(k.split(' ', 1)[1], w) for k, w in _claims(st).items()
            if k.split(' ', 1)[0] == ('specify' if stage == 'repair' else stage) and w != worker]
    who = (' The other agents of this stage hold every open item: ' + ', '.join(f'{t} ({w})' for t, w in held[:6]) + '.'
           if held else '')
    return (f'{IDLE.get(stage, "Nothing to do.")}{who} There is nothing left for you in this stage, so your part ends '
            f'here (time used: {_elapsed(st) // 60} min {_elapsed(st) % 60} s).\n\n{FAST_DONE}')


def _for_worker(out, stage, worker):
    """The handlers' NEXT lines name the stage's own script; a worker calls its own."""
    names = ('specify.py', 'edit.py') if stage == 'repair' else (SCRIPT[stage],)
    for n in names:
        out = out.replace(f'file_path {n}', f'file_path {worker}.py')
    return out


def _view(root, st, stage, worker, target):
    handler = st['wcur'][worker][2]
    st['current'] = (handler, target)
    # the requirements stage opens with its first question (the names), not with an 'open' item
    return HANDLERS[handler](root, st, [] if handler == 'requirements' else ['open'])


def main_fast(stage, worker, argv):
    """One call of a worker of the fast build: stage is its stage ('repair' specifies and edits), worker its script's
    name (specify2)."""
    global FAST
    FAST = True
    root = _repo.repo_root()
    if not _state.lock():
        print('This call was not run: the call sent before it is still running. Send it again when that one has '
              'answered.')
        return
    st = _state.load('stages') or _new()
    # the tests this call runs end before the task's time does (vf7 on Kaggle, rich_3772: 604 s)
    _tests.DEADLINE = time.time() + max(5, _left(st) - 10)
    args = _args.split_single_quoted(list(argv))
    first = _args.clean(args[0]).lower() if args and args[0] else ''
    try:
        _tests.record_preexisting(root)
        cur = st.setdefault('wcur', {}).get(worker)
        last = (st.get('wlast') or {}).get(worker)
        if stage == 'locate' and cur is None and last and not st['scope_done'] and first not in ('', 'open', 'start',
                                                                                                'next', 'done'):
            # vf3 audit (rich_2943, rich_4075): a name sent "to check it" chose it, and the right place sent one call
            # later was refused; until the plan is made, the worker's next names replace its last choice
            st['located'].pop(last, None)
            st['asked'].pop('locate ' + last, None)
            _take(st, 'locate', last, worker, 'locate')
            if st.setdefault('wfinished', {}).get(worker):
                st['wfinished'][worker].pop()
            cur = st['wcur'][worker]
        if stage == 'requirements' and cur is None and st['reqs'] and not st['located'] and len(args) >= 2 \
                and _looks_like_requirements([_sentence(a) for a in args]):
            # vf3: an agent whose saved requirement was wrong sent the right ones and was told it was finished; before
            # anything is located, new requirements replace the old
            st['reqs'] = []
            st.get('wfinished', {}).pop(worker, None)
            st['asked'].pop('requirements', None)
        if _over_budget(st):
            # B10 (docs/design_stages.md, "How the 90 runs ended"): the harness cut 44 of 90 runs while an agent worked
            _release(st, worker)
            if stage == 'repair' and not st['submitted']:
                _submit(root, st)
            out = ('This call was not applied: the time is up. ' + _progress(root, st)
                   + ' The patch as it is now will be submitted.\n\n' + FAST_DONE)
        elif cur is None and not (stage == 'requirements' and first not in ('', 'open', 'start', 'next')
                                  and _claim(root, st, stage, worker) is not None):
            # hf1 (rich_3905): the requirements agent's first call already holds the names (its prompt's step 1): it
            # is answered, not replaced by the first question
            target = _claim(root, st, stage, worker)
            if target is None:
                if stage == 'repair' and not st['submitted']:
                    _submit(root, st)
                done_before = st.setdefault('wfinished', {}).get(worker)
                if done_before:
                    # vf1 (Gemma, rich_3905): the reproduce agent whose test was kept sent it 8 more times, told only
                    # "No reproduction test is needed now", and the stage waited for it until the time was up
                    out = _finished(st, stage, done_before)
                else:
                    out = _idle(st, stage, worker)
            else:
                out = _progress(root, st) + '\n\n' + _view(root, st, stage, worker, target)
        else:
            cur = st['wcur'][worker]
            handler = cur[2]
            st['current'] = (handler, cur[1])
            if first in ('open', 'next', 'start'):
                args = [] if handler == 'requirements' else ['open']
            out = HANDLERS[handler](root, st, args)
            if out.endswith(DONE):
                body = out[:-len(DONE)].rstrip()
                _release(st, worker, _summary(st, handler, cur[1], body))
                st.setdefault('wlast', {})[worker] = cur[1]
                limit = BATCH.get(stage, 3) * 2
                st.setdefault('wdone', {})[worker] = st.setdefault('wdone', {}).get(worker, 0) + 1
                target = _claim(root, st, stage, worker) if st['wdone'][worker] < limit else None
                if target is not None:
                    out = (body + f'\n\nYour part goes on with {target}, in the same way:\n\n'
                           + _view(root, st, stage, worker, target))
                else:
                    if stage == 'repair' and not st['submitted']:
                        _submit(root, st)
                    out = body + '\n\n' + _part_done(st, stage, worker)
        out = _for_worker(out, stage, worker)
    except Exception as e:                      # never a traceback for the model
        _state.record({'error': f'{worker} {type(e).__name__}: {e}'})
        import traceback
        _state.record({'trace': traceback.format_exc()[-2000:]})
        _release(st, worker)
        out = f'The script hit an internal error ({type(e).__name__}).\n\n{FAST_DONE}'
    _state.record({'stage': worker, 'args': [a[:300] for a in args][:8], 'answer': out[:300]})
    _state.save('stages', st)
    if len(out) > ANSWER_CHARS:
        out = out[:ANSWER_CHARS - 1500] + '\n      ... (cut) ...\n' + out[-1400:]
    print(out)
