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
MAX_EDITS = 4           # edits of one place
MAX_SENTENCE = 1500
LONG_EDIT = 80          # a place longer than this is edited by replacing lines, not sent whole
BIG_CLASS = 120         # a class place longer than this is split into the members that change
BUDGET_S = int(os.environ.get('SWE_STAGE_BUDGET', str(45 * 60)))   # then the work goes to submit
REPRO_NAME = 'test_swe_reproduce_issue.py'
REPRO_TIMEOUT = 40
ANSWER_CHARS = 9000
# The per-edit test check (stage 6), off for now at the user's request (2026-10-14): in requests it cost about 70 s per
# edit, and the first edit of a task built the call map with a whole-suite run (up to 150 s). SWE_EDIT_TESTS=1 turns
# it back on. The edit still has to compile (the whole-definition and replace forms refuse code that does not).
EDIT_TESTS = os.environ.get('SWE_EDIT_TESTS', '0') == '1'
# Stage 7 (verify), off for now at the user's request (2026-10-14): it ran the selected existing tests on the whole
# patch, built the whole-suite call map (up to 150 s) and ran the reproduction test again. SWE_VERIFY=1 turns it on.
VERIFY = os.environ.get('SWE_VERIFY', '0') == '1'

DONE = 'NEXT: no call: reply with one sentence for the coordinator.'


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


def _place_by(st, text):
    return step._match(st['plan'], text, fuzzy=False) if st['plan'] else None


def _key(p):
    return f'{p["rel"]}::{p["name"]}'


def _handles(st):
    step._set_handles(st['plan'])


def _req(st, rid):
    return next((r for r in st['reqs'] if r['id'] == rid), None)


def _req_line(r):
    q = f' (the issue: "{r["quote"][:200]}")' if r.get('quote') else ''
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
    return f'{held}{plan} Time used: {_elapsed(st) // 60} min {_elapsed(st) % 60} s.'


# ------------------------------------------------------------------------------------------------------ routing

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
        if _ask(st, 'scope'):
            st['current'] = ('scope', None)
            return 'scope', 'the plan'
        _make_plan(root, st, [])
    _add_missing(root, st)
    # 5 specify, per place
    for p in st['plan']:
        if p.get('sentence') is None:
            if _ask(st, f'specify {_key(p)} {st["round"]} {p.get("respecs", 0)}'):
                st['current'] = ('specify', p['handle'])
                return 'specify', p['handle']
            p['sentence'] = '; '.join(_req(st, rid)['what'] for rid in p['reqs'] if _req(st, rid)) or 'no change'
            p['status'] = 'todo'
    # 6 edit, per place
    for p in st['plan']:
        if p.get('status') == 'todo':
            if _ask(st, f'edit {_key(p)} {st["round"]} {p.get("respecs", 0)}'):
                st['current'] = ('edit', p['handle'])
                p['opened'] = False
                return 'edit', p['handle']
            p['status'] = 'failed'
    # 7 verify (script)
    _verify(root, st)
    # 8 review, per requirement
    for r in st['reqs']:
        if r['id'] not in st['reviews']:
            if st['repro'] and st['repro'].get('rid') == r['id'] and st['repro'].get('now_passes'):
                st['reviews'][r['id']] = {'verdict': 'done', 'by': 'the reproduction test passes'}
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
    return f"{head}\n\nNEXT: call the tool {tool} with request '{req}'. If it replies with a line \"call the tool ...\", " \
           "make that call next; if it replies with anything else, call run_skill_script with skill_name fix-issue, " \
           "file_path route.py and args ['next']."


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

def _looks_like_requirements(items):
    return len(items) >= 2 and len(items) % 2 == 0 and all(len(x.split()) >= 3 for x in items[1::2])


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


def requirements(root, st, args):
    items = [_args.clean(a) if len(a) < 300 else a.strip() for a in args if a and a.strip()]
    if st['names'] is not None or _looks_like_requirements(items):
        items = [_sentence(a) for a in args if a and a.strip()]
    form = ("Send the requirements: ['<a sentence copied from the issue>', '<what must happen: the situation and the "
            "expected result, with the exact names>', ...], one pair per requirement, 1 to 6 pairs.")
    if st['names'] is None and not _looks_like_requirements(items):
        if not items:
            return ('Send the names the issue mentions (functions, classes, parameters, files), one per item.\n\n'
                    "NEXT: call run_skill_script with skill_name fix-issue, file_path req.py and args ['<name>', "
                    "'<name>', ...]")
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
            shown = []
            for rel, s in found:
                sig = _sig(s)
                if sig not in shown and len(shown) < 2:
                    shown.append(sig)
                    lines.append(f'  {name}: {sig}  ({rel}, {s.kind} {s.name})')
            if not found:
                index = index or _rank.Index(root)
                lines.append(f'  {name}: ' + ('not a function or class; the code uses this name (a parameter, '
                                              'attribute or variable)' if name.split('.')[-1] in index.names else
                                              'not in the repository (new code the issue asks for, or library code)'))
        st['names'] = names
        return ('What the repository defines for those names:\n' + '\n'.join(lines) + '\n\n' + form + '\n\n'
                "NEXT: call run_skill_script with skill_name fix-issue, file_path req.py and args ['<sentence from "
                "the issue>', '<what must happen>', ...]")
    if len(items) == 1:
        items = ['', items[0]]
    if len(items) % 2:
        return f'An odd number of items was sent ({len(items)}): every requirement is a pair. {form}\n\n' \
               "NEXT: call run_skill_script with skill_name fix-issue, file_path req.py and args ['<sentence from " \
               "the issue>', '<what must happen>', ...]"
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
        concrete = bool(re.search(r'`[^`]+`|"[^"]+"|\'[^\']+\'|\d|\w\(', quote))
        reqs.append({'id': f'R{len(reqs) + 1}', 'quote': quote, 'what': what[:600], 'names': names, 'new': new,
                     'concrete': concrete})
    if not reqs:
        return 'No requirement was read. ' + form
    st['reqs'] = reqs
    _save_statement(st)
    listed = '\n'.join('  ' + _req_line(r) + (f' [new: {", ".join(r["new"])}]' if r['new'] else '') for r in reqs)
    return f'Saved {len(reqs)} requirements:\n{listed}\n\n{DONE}'


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
                if score and (best is None or key > best[0]):
                    best = (key, rel, body)
    return (best[1], best[2]) if best else None


def _signatures(root, names, limit=6):
    table = _impact.Table(root, docs=False)
    out = []
    for n in names:
        for rel, s in table.find(n.split('.')[-1])[:1]:
            out.append(f'  {s.name}: {_sig(s)}  ({rel})')
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
    return mine, msgs, _function_calls(root, calls), res['output']


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
    nxt = "NEXT: call run_skill_script with skill_name fix-issue, file_path repro.py and args ['<the test>']"
    if not items or first in ('open', 'start', 'the issue', rid.lower() if rid else 'r1'):
        tpl = _template(root, r)
        sig = _signatures(root, r['names'])
        view = [f'Write one test that shows this requirement is not met yet (it must fail on the code as it is now, '
                f'and pass once the issue is fixed):\n  {_req_line(r)}']
        if sig:
            view.append('Signatures in the repository:\n' + '\n'.join(sig))
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
                   'calls': calls[:60], 'now_passes': False, 'now': ''}
    st['repro_done'] = True
    return (f'Kept: the test fails on the code as it is now:\n{st["repro"]["before"][:600]}\nIt runs '
            f'{len(calls)} functions of the repository. ' + ('It will be run again after the edits.' if VERIFY else
            'The other agents see which code it runs and how it fails.') + f'\n\n{DONE}')


# ------------------------------------------------------------------------------------------- 3 locate

def _candidates(root, st, r, n=8):
    text = (r.get('quote') or '') + '\n' + r['what']
    cands, _ = step._rank_candidates(root, text, r['names'], [], n=n)
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
    return cands


def locate(root, st, args):
    rid = (st['current'] or (None, None))[1]
    r = _req(st, rid)
    if r is None:
        return 'Nothing to locate now.\n\n' + DONE
    items = [a for a in args if a and a.strip()]
    nxt = ("NEXT: call run_skill_script with skill_name fix-issue, file_path locate.py and args ['<name>'] (1 to 3 "
           "names from the list), or ['<a name or text to find>']")
    cands = st['cands'].get(rid)
    if cands is None:
        cands = _candidates(root, st, r)
        st['cands'][rid] = cands
    if not items or _args.clean(items[0]).lower() in ('open', 'start', rid.lower()):
        others = sorted({p['name'] for ps in st['located'].values() for p in ps})
        view = [f'Where is this decided in the code?\n  {_req_line(r)}',
                'Candidates (functions and classes, the most related first):\n' +
                '\n'.join(f'  {c["handle"]}  {re.sub(r", lines [0-9]+-[0-9]+", "", c["line"])}{_uses_note(root, c, cands)}'
                          for c in cands)]
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
        res = [c for c in step._resolve(root, others) if not _repo.is_doc_path(c['rel'])]
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
            return f'Nothing in the repository holds {others[0][:80]!r}. Choose from the list.\n\n{nxt}'
    if not found:
        return 'No place was read. Send 1 to 3 names from the list.\n\n' + nxt
    lost = []
    if others and found and len(found) < 3:
        # local run h3 (fastapi_9555): ['solve_dependencies', 'Dependant.is_coroutine_callable'] kept only the first,
        # silently, because the second was off this requirement's list (chosen for R1); a name off the list is
        # looked up in the repository like a name sent alone
        for o in others:
            res = [c for c in step._resolve(root, [o]) if not _repo.is_doc_path(c['rel'])
                   and not any(_key(c) == _key(f) for f in found)]
            if res:
                found.append(res[0])
            else:
                lost.append(o)
    st['located'][rid] = [{'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end']}
                          for c in found[:3]]
    note = (' Not found in the repository, so not chosen: ' + ', '.join(lost) + '.') if lost else ''
    return (f'{rid} is decided at: ' + ', '.join(c.get('handle') or c['name'] for c in found[:3]) + '.' + note
            + '\n\n' + DONE)


# methods of the builtin types: 'req.scope.get(...)' is not a use of APIRouter.get (gm9, gk9)
COMMON_METHODS = set(dir(dict)) | set(dir(list)) | set(dir(str)) | set(dir(set)) | set(dir(bytes))


def _uses_note(root, c, cands):
    """' [it uses X, Y from this list]' when the candidate reads or calls other candidates. g1 (fastapi_9555, the local
    31B): the issue named solve_dependencies, which only reads Dependant.is_gen_callable and its siblings, where the
    decision is made; they were on the list, but nothing said that one uses the others."""
    if c.get('name') in (None, MODULE):
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
    return auto, options


def _link_line(root, d):
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
    st['scope_options'] = options[:12]
    newp = _new_places(root, st)
    items = [a for a in args if a and a.strip()]
    nxt = ("NEXT: call run_skill_script with skill_name fix-issue, file_path scope.py and args ['<name>', ...] (the "
           "related code that must change too), or ['none']")
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
                            f'  {d["name"]} ({d["rel"]}) — {d["reason"]}: {_link_line(root, d)}' for d in options[:12]))
        else:
            view.append('No other related code was found.')
        if st['repro'] and st['repro'].get('calls'):
            ran = [k.split('::')[1] for k in st['repro']['calls'] if not any(
                k == _key(p) for p in located + auto + options)]
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

def _place_view(root, st, p, limit=80):
    reqs = [_req(st, rid) for rid in p['reqs'] if _req(st, rid)]
    if p.get('new'):
        where = f'the end of class {p["owner"]}' if p.get('owner') != MODULE else f'the end of {p["rel"]}'
        return (f'{p["handle"]} does not exist yet: it is new code, added at {where} ({p["rel"]}).')
    body = step._code_view(root, p['rel'], p['start'], p['end'], _focus(reqs), limit=limit)
    return f'{p["handle"]} ({p["rel"]}, lines {p["start"]}-{p["end"]}):\n{body}'


def _refresh(root, p):
    """The place's current lines. A name defined several times in a row (typing.overload variants, a property and its
    setter) is one place spanning them all: rich_3052's PromptBase.ask has two overloads and the implementation."""
    if p.get('new') and p.get('status') != 'done':
        return
    syms = _code.symbols(_code.parse(_repo.read_text(root, p['rel'])))
    same = [x for x in syms if x.name == p['name']]
    if same:
        p['start'], p['end'] = min(x.start for x in same), max(x.end for x in same)
        p['group'] = len(same) > 1
        p['new'] = False


def specify(root, st, args):
    name = (st['current'] or (None, None))[1]
    p = _place_by(st, name) if name else None
    if p is None:
        return 'Nothing to specify now.\n\n' + DONE
    _refresh(root, p)
    items = [a for a in args if a and a.strip()]
    nxt = ("NEXT: call run_skill_script with skill_name fix-issue, file_path specify.py and args ['<one sentence: what "
           "changes here>'] or ['no change']")
    if not items or _args.clean(items[0]).lower() in ('open', 'start', p['handle'].lower()):
        reqs = [_req(st, rid) for rid in p['reqs'] if _req(st, rid)]
        view = [_place_view(root, st, p),
                'It serves these requirements:\n' + '\n'.join('  ' + _req_line(r) for r in reqs)]
        if not p.get('new'):
            ctx = step._context_view(root, p, _focus(reqs))
            if ctx:
                view.append(ctx[:2500])
        others = [f'  {q["handle"]}: {q["sentence"]}' if q.get('sentence') else f'  {q["handle"]}: (to specify)'
                  for q in st['plan'] if q is not p]
        if others:
            view.append('The other places of the fix (each is edited by its own editor):\n' + '\n'.join(others))
        if st['repro']:
            ran = _key(p) in set(st['repro'].get('calls') or [])
            view.append('The reproduction test fails on the original code' + (' and runs this place' if ran else '')
                        + ':\n' + st['repro']['before'][:500])
        if p.get('note'):
            view.append('Found after the edits: ' + p['note'][:900])
            diff = step._patch_here(root, p)
            if diff:
                view.append(diff[:1500])
            if p.get('old_sentence'):
                view.append('The sentence before: ' + p['old_sentence'])
        return ('\n\n'.join(view) + '\n\nWrite what must change in this place so that its requirements hold: one '
                'sentence, complete in itself (its editor sees only this place), with the exact names. Or no change.'
                '\n\n' + nxt)
    sentence = ' '.join(x.strip() for x in items)
    if len(sentence) > MAX_SENTENCE:
        return f'Too long ({len(sentence)} characters, at most {MAX_SENTENCE}). Send it shorter.\n\n{nxt}'
    if step.NO_CHANGE.match(sentence):
        p['sentence'], p['status'] = 'no change', 'nochange'
    else:
        p['sentence'], p['status'] = sentence, 'todo'
        p['fails'], p['edits'] = 0, 0
    p.pop('note', None)
    if p['status'] == 'nochange':
        return f'{p["handle"]} keeps its code: it is not edited.\n\n{DONE}'
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
        return 'the old lines were not found in this place (send them as the window shows them, without line numbers)'
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
    items = [a for a in args if a and a.strip()]
    what = ('top-level code of ' + p['rel'] if p['name'] == MODULE
            else 'definition of ' + p['handle'].split('::')[-1].split('.')[-1])
    nxt = ("NEXT: call run_skill_script with skill_name fix-issue, file_path edit.py and args ['<the whole new "
           + what + ", with any new imports above it>'], or "
           "['replace', '<old lines>', '<new lines>'], or ['no change'], or ['missing', '<name of other code this "
           "change needs>']")
    first = _args.clean(items[0]).lower() if items else ''
    # gm9 (Gemma): ['open', 'FastAPI.setup_openapi'] was read as code to apply; a first item 'open' opens the place
    if not items or first in ('open', 'start') or (first == p['handle'].lower() and len(items) == 1):
        reqs = [_req(st, rid) for rid in p['reqs'] if _req(st, rid)]
        view = [_place_view(root, st, p, limit=150), f'What to change here: {p["sentence"]}']
        if not p.get('new'):
            ctx = step._context_view(root, p, _focus(reqs))
            if ctx:
                view.append(ctx[:2500])
            diff = step._patch_here(root, p)
            if diff:
                view.append(diff[:1500])
        length = p['end'] - p['start'] + 1
        if length > LONG_EDIT and not p.get('new'):
            # g1 (fastapi_9555, the local 31B): the whole 140-line solve_dependencies, sent again after two replace
            # edits, came out malformed three times (2 minutes, about 2,100 tokens each)
            view.append(f"This place is long ({length} lines): change only the lines that change, with ['replace', "
                        "'<the old lines, as shown, without their numbers>', '<the new lines>'], one call per group "
                        "of lines; the rest stays as it is. A new import can open the new lines, not indented: it is moved "
                        "to the top of the file.")
            nxt = ("NEXT: call run_skill_script with skill_name fix-issue, file_path edit.py and args ['replace', "
                   "'<old lines>', '<new lines>'], or ['no change'], or ['missing', '<name of other code this change "
                   "needs>']")
            return '\n\n'.join(view) + '\n\n' + nxt
        view.append('Send the whole new definition (from its def or class line, with its decorators); new imports go '
                    'above it and are moved to the top of the file. A new helper function can come with it.')
        if re.search(r'not shown\)', view[0]):
            view.append("Part of this place is not shown. Then either send 'def <name>(...):' followed by the whole "
                        "new body (the parameters, decorators and docstring stay as they are), or change some lines: "
                        "['replace', '<the old lines, as shown, without their numbers>', '<the new lines>'].")
        return '\n\n'.join(view) + '\n\n' + nxt
    if first in ('no change', 'skip', 'nothing'):
        if p.get('status') != 'done':
            p['status'] = 'nochange'
        return f'{p["handle"]} keeps its code.\n\n{DONE}'
    if first == 'missing' and len(items) >= 2:
        done = [q for n in _missing_names(items[1]) for q in [_place_by(st, n)] if q is not None
                and q is not p and q.get('status') == 'done']
        if done:
            # s-st4 (fastapi_14851): the editor of on_event named APIRouter.__init__ as missing four times after
            # it was edited, and each answer sent __init__ back to be specified and edited again
            diffs = '\n'.join((step._patch_here(root, q) or f'{q["handle"]} is edited.')[:1500] for q in done[:2])
            return (f'{", ".join(q["handle"] for q in done)} is already changed by this fix:\n{diffs}\n'
                    f'Make the change of {p["handle"]} with that code in place.\n\n{nxt}')
        st['missing'].append([_args.clean(items[1]), f'the editor of {p["handle"]} needs it: ' + ' '.join(items[2:])[:300],
                              list(p['reqs']), _key(p)])
        st['asked'].pop(f'edit {_key(p)} {st["round"]} {p.get("respecs", 0)}', None)   # asked again after the new code
        return (f'{_args.clean(items[1])} goes into the plan before {p["handle"]}; {p["handle"]} is edited after it.'
                f'\n\n{DONE}')
    p['edits'] = p.get('edits', 0) + 1
    if p['edits'] > MAX_EDITS:
        return f'{p["handle"]} has had {MAX_EDITS} edits; it stays as it is.\n\n{DONE}'
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
        if isinstance(res, str):
            if head:
                _repo.write_lines(root, p['rel'], before)
                _refresh(root, p)
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
    kept = _keep_signature(root, p, body) if not p.get('new') else None
    if kept:
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


def _after_edit(root, st, p, r, before, names, nxt):
    if not r.applied:
        _repo.write_lines(root, p['rel'], before)
        err = r.error
        if 'line number' in err:
            # the base procedure's numbered form is not an answer form here (gm9: Gemma answered it with 'ls -R')
            err = ("what was sent is not a whole definition (a def or class line and its body); to change some of "
                   "its lines send ['replace', '<the old lines, as shown, without their numbers>', '<the new lines>']")
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
            detail = ('the edit uses names that this file neither defines nor imports: ' + ', '.join(new_names[:5])
                      + '. Import them (an import line above the definition, or opening the new lines, not indented)')
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
                return (f'Undone: {why}:\n{detail}\n{p["handle"]} goes '
                        f'back to be specified again, with these tests.\n\n{DONE}')
            p['status'] = 'failed'
            return (f'Undone: {why}:\n{detail}\nThat was the last try for '
                    f'{p["handle"]}: it keeps its code.\n\n{DONE}')
        return (f'Undone: {why}, so {p["handle"]} is back as it was:\n'
                f'{detail}\nSend a corrected definition that fixes this.\n\n{nxt}')
    p['status'] = 'done'
    if p.get('new'):
        p['new'] = False
        p['name'] = (p['owner'] + '.' if p.get('owner') not in (None, MODULE) else '') + p['name'].split('.')[-1]
    _refresh(root, p)
    word = {'OK': 'Kept and checked', 'UNCHECKED': 'Kept, not checked'}.get(verdict, 'Kept')
    dropped = _dropped_uses(root, p, before, after)
    if dropped:
        lines = [f'  {d}, still used by ' + ', '.join(f'{u} ({r}:{l})' for r, u, l in users) for d, users in dropped]
        first = dropped[0][0]
        return (f'{word}: {detail}.\n{shown}\n\nThis edit stops using code that other code still uses, and that '
                'code keeps the old behaviour:\n' + '\n'.join(lines) + '\nIf the requirement must hold there too, '
                f"the change belongs in that code itself: send ['missing', '{first}', '<what must change in it>'] (it "
                'goes into the plan and is changed next). If not, reply with one sentence for the coordinator.'
                f"\n\nNEXT: call run_skill_script with skill_name fix-issue, file_path edit.py and args ['missing', "
                f"'{first}', '<what must change in it>'], or no call: reply with one sentence for the coordinator.")
    fix = ("another ['replace', '<old lines>', '<new lines>']" if p['end'] - p['start'] + 1 > LONG_EDIT
           else 'the corrected whole definition')
    return (f'{word}: {detail}.\n{shown}\n\nIf this is the whole change, reply with one sentence for the coordinator, '
            f'without a call. If it is wrong or incomplete, send {fix}.\n\nNEXT: no call: '
            f'reply with one sentence for the coordinator (or send {fix} to edit.py).')


# ------------------------------------------------------------------------------------------- 7 verify

def _patch_files(root):
    return {rel: (''.join(_repo.read_lines(root, rel)) if os.path.exists(os.path.join(root, rel)) else None)
            for rel in _tests.changed_files(root) if not rel.endswith(REPRO_NAME)}


def _score(root, st):
    repro = 1 if st['repro'] and st['repro'].get('now_passes') else 0
    return [repro, -len(st['regressions']), len(_patch_files(root)) > 0]


def _verify(root, st):
    files = _patch_files(root)
    sig = hash(tuple(sorted((k, v or '') for k, v in files.items())))
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
    nxt = ("NEXT: call run_skill_script with skill_name fix-issue, file_path review.py and args ['done'], or "
           "['not done', '<the place that is wrong or missing>', '<what is missing there>'], or ['cannot tell']")
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
                outcomes, msgs, _, _ = _run_repro(root, st['repro']['rel'], st['repro']['code'])
                passed = bool(outcomes) and all(v == 'passed' for v in outcomes.values())
                st['repro']['now_passes'] = passed
                failing = [k for k, v in outcomes.items() if v != 'passed']
                st['repro']['now'] = ('it passes' if passed else 'it still fails:\n' + (
                    msgs.get(failing[0], '') if failing else 'it did not run')[:500])
            view.append('The reproduction test, on the original code:\n' + st['repro']['before'][:400] +
                        '\nWith the patch: ' + (st['repro']['now'] or 'not run again')[:500])
        if st['regressions']:
            view.append('Existing tests the whole patch breaks: ' + ', '.join(t for t, _ in st['regressions'][:5]))
        _, options = _related(root, st, places)
        near = [d['name'] for d in options[:8]]
        if near:
            view.append('Other code near these places (if the change is missing there): ' + ', '.join(near) + '.')
        return ('\n\n'.join(view) + "\n\nAnswer with what the code shows: ['done'] if the patch does this, ['not done', "
                "'<place>', '<what is missing>'] naming the one place that is wrong or missing, or ['cannot tell'].\n\n"
                + nxt)
    if first in ('done', 'yes', 'cannot tell', "can't tell", 'unknown'):
        repro = st['repro'] if st['repro'] and st['repro'].get('rid') == rid else None
        if repro and repro.get('now') and not repro.get('now_passes') and not st.get('review_refused', {}).get(rid):
            # the test's output decides, not the opinion (design_stages.md, stage 8): s-st2 fastapi_14448 was
            # reviewed 'done' while its reproduction test still failed
            st.setdefault('review_refused', {})[rid] = True
            return ('The test of this requirement still fails with the patch:\n' + (repro.get('now') or '')[:600] +
                    '\nSo the patch does not do it yet: name the place that is wrong or missing and what is missing '
                    "there: ['not done', '<place>', '<what is missing>'].\n\n" + nxt)
        st['reviews'][rid] = {'verdict': 'done' if first in ('done', 'yes') else 'cannot tell'}
        return f'{rid}: {st["reviews"][rid]["verdict"]}.\n\n{DONE}'
    if first in ('not done', 'no') and len(items) >= 2:
        name, what = _args.clean(items[1]), ' '.join(items[2:])[:600]
        st['reviews'][rid] = {'verdict': 'not done', 'place': name, 'what': what}
        if st['round'] < MAX_ROUNDS:
            p = _place_by(st, name)
            if p is not None:
                p['old_sentence'] = p.get('sentence')
                p['sentence'], p['note'] = None, f'the review of {rid} says: {what}'
            else:
                st['missing'].append([name, f'the review of {rid} says: {what}', [rid], None])
        return f'{rid}: not done; {name} goes back to be specified and edited.\n\n{DONE}'
    return 'The answer was not read. ' + nxt


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
    st['chained'] = call
    if stage == 'requirements':
        done = f"{len(st['reqs'])} requirements saved ({', '.join(r['id'] for r in st['reqs'])})"
    else:
        done = next((l.strip() for l in out.split('\n') if l.strip()), '').rstrip(':.')[:160]
    line = f'{stage} is done: {done}. Next: {call}.'
    # rich_3521 (gated run gk2): locate and review took the line for an order to themselves ("that isn't part of my
    # task, so I ignored it"); it is said to be the coordinator's next call, for the agent to pass on
    return out[:-len(DONE)] + ('Your part is done. The coordinator (the agent that called you) makes its next call from '
                               'your reply, so pass this line on to it: it is not a task for you.\n'
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
    try:
        if pending and stage == 'route':
            # the coordinator called route.py instead of the line an agent returned: the same next step, not a new
            # ask (a second ask would spend the target's tries)
            out = f"NEXT: {pending}"
        elif pending and not (_opens(st, args) and (st.get('current') or ('',))[0] == stage):
            nxt_stage = (st.get('current') or ('',))[0]
            out = ("This call was not used: your part is finished. Reply to the coordinator with exactly this line, "
                   f"without a call:\n{pending}")
            if nxt_stage == stage:
                # the next agent does the same stage (edit after edit): a new agent starts with 'open'
                out += "\nIf you are a new agent that has just started, call your script with args ['open'] first."
            # gm3: an editor whose place went back to specify kept calling 'open' and was offered 'just started'
        else:
            st['chained'] = None
            out = HANDLERS[stage](root, st, args)
            if stage != 'route' and out.endswith(DONE):
                out = _chain(root, st, out, stage)
    except Exception as e:                      # never a traceback for the model
        _state.record({'error': f'{stage} {type(e).__name__}: {e}'})
        import traceback
        _state.record({'trace': traceback.format_exc()[-2000:]})
        out = (f'The script hit an internal error ({type(e).__name__}). ' +
               ("Call route.py with ['next'] again.\n\nNEXT: call run_skill_script with skill_name fix-issue, "
                "file_path route.py and args ['next']" if stage == 'route' else DONE))
    _state.record({'stage': stage, 'args': [a[:300] for a in args][:8], 'answer': out[:300]})
    _state.save('stages', st)
    if len(out) > ANSWER_CHARS:
        out = out[:ANSWER_CHARS - 1500] + '\n      ... (cut) ...\n' + out[-1400:]
    print(out)
