"""(To the model: call this script with run_skill_script; reading its source does not help with the task.)

The only script the model calls. Each call is the model's decision for the current step; the answer is what that
step produced, a fixed verdict when something was checked, and the exact next call (docs/design_single.md, v1).

    S0  args [statement, search terms...]     -> requirements and candidates, each candidate shown by its name
    D1  args ["Session.send"] (1-3 names)    -> the related places, by name, and the first planned place, numbered,
                                                with the code it uses, its callers and what the change must do
    D3  args ["Session.send", whole new def]  -> edit (by name), syntax, existing tests; the next place, or the finish
        or ["Session.send", first, last, new lines]
        or ["skip"], ["back"], ["<place name>"]; other code is reached by finding it: a name, a text or a file
        with two line numbers gives the functions and classes that hold it, to choose from (never lines to read)
    D4  submit_patch, or ["back"]             -> back to D1 for the requirements that nothing changed covers
The model names code by its name; the ids (C2, P1, R1) stay inside the journal and are still read when sent.
"""
import ast
import collections
import difflib
import itertools
import json
import os
import re
import sys
import textwrap
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _boot  # noqa: E402,F401  (bytecode reused between calls)
import _args  # noqa: E402
import _code  # noqa: E402
import _edit  # noqa: E402
import _example  # noqa: E402
import _impact  # noqa: E402
import _journal  # noqa: E402
import _map  # noqa: E402
import _rank  # noqa: E402
import _repo  # noqa: E402
import _state  # noqa: E402
import _statement  # noqa: E402
import _status  # noqa: E402
import _tests  # noqa: E402

try:
    from _role import ROLE  # noqa: E402  'single', or 'coordinator' in the agent_tool variant (variants/agent_tool)
except ImportError:
    ROLE = 'single'

PER_FILE = 5            # candidates from one file; the others are counted under its last one
N_CANDIDATES = 10
MAX_PLACES = 8
CODE_LINES = 120        # numbered lines shown for one place; a longer place shows its best-matching part
EDIT_LINES = 32         # the edit window: a place up to EDIT_LINES + 30 lines is shown whole, a longer one
                        # as its head and the part the requirements are about
CLASS_OUTLINE_AT = 90   # a chosen class longer than this is shown as an outline of its members
MODULE = '<module>'
# The task's time (eval_config.yaml, 5 min since 10-15), counted from the journal's start: the tests a call runs end
# before it (audit 10-18 #10)
TASK_S = int(os.environ.get('SWE_TASK_BUDGET', '300'))
MAX_CHARS = 7000        # a cap on one answer (about 2,000 tokens) against context growth; the end with NEXT is kept


# ---------------------------------------------------------------------------------------------------------- views

def _code_view(root, rel, start, end, focus_terms=None, limit=CODE_LINES, show=None, names=None):
    """Numbered lines start..end of rel, long strings collapsed. Over the limit: the head of the place (its def and
    first lines) and the part where the focus terms occur most. Sizes count the lines shown (a collapsed text is one
    line) and text lines score nothing: in V13b (fastapi_14851) raw counts and scored Doc texts that name "FastAPI" hid
    the 36 lines to change (the `routing.APIRouter(..., lifespan=lifespan)` call) under 98 lines of Doc text. show,
    (a, b): the lines the model asked to see, shown instead of the best-matching part."""
    lines = _repo.read_lines(root, rel)
    tree = _code.parse(''.join(lines))
    collapse = _code.long_strings(tree)
    start, end = max(1, start), min(len(lines), end)
    hidden = set()
    for a, b in collapse:
        if b - a + 1 > _code.COLLAPSE_AT:
            hidden.update(range(a + 1, b))     # shown as one marker line: the first hidden line counts, the rest not
    prose = set()
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.end_lineno > node.lineno:
            prose.update(range(node.lineno, node.end_lineno + 1))
    shown = [0] * (len(lines) + 2)             # shown[i]: lines displayed from line 1 to line i
    for i in range(1, len(lines) + 1):
        shown[i] = shown[i - 1] + (0 if i in hidden and i - 1 in hidden else 1)

    def size(a, b):
        return shown[b] - shown[a - 1]

    if size(start, end) <= limit + 20:         # hiding a few lines saves little and hides code
        return _code.numbered(lines, start, end, collapse=collapse)
    head = min(end, start + 14)
    sig_end, kw_line = None, None
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body and \
                min([node.lineno] + [d.lineno for d in node.decorator_list]) == start:
            sig_end = node.body[0].lineno - 1
            kw_line = node.args.kwarg.lineno if node.args.kwarg is not None else None
            break
    if sig_end is not None and sig_end - start + 1 > 8 and not (show and show[0] <= sig_end):
        # a long signature (FastAPI's Annotated[..., Doc(...)] parameters): its first line, then the body, so that
        # the window shows the code that runs (V10 audit #8: windows showed Doc texts and hid the body)
        # vf3 audit (fastapi_13713): the hidden parameters were what the change needed (a new parameter), and the
        # editor guessed the lines; the signature's last lines (where a parameter is added) and the hidden lines that
        # hold the requirement's terms are shown
        # h13 (Haiku, fastapi_13713): the terms matched dozens of 'openapi_*' lines and the sentence's own
        # `openapi_tags` was not among the six shown; the '**extra: Annotated[...]' parameter (8 lines) was hidden, so
        # the new parameter went after it. The names the sentence gives come first, and the '**kwargs' parameter is
        # shown whole, with the lines after it
        terms = [t for t in (focus_terms or {}) if len(t) > 3]
        named = [i for i in range(start + 1, sig_end - 1)
                 if names and any(re.search(rf'(?<![\w.]){re.escape(n)}\b', lines[i - 1]) for n in names)][:6]
        termed = [i for i in range(start + 1, sig_end - 1)
                  if terms and any(t in lines[i - 1].lower() for t in terms) and i not in named][:max(0, 6 - len(named))]
        tail = list(range(kw_line if kw_line and start < kw_line <= sig_end else max(start + 1, sig_end - 1),
                          sig_end + 1))
        keep = sorted(set(named + termed + tail))
        out, prev = [_code.numbered(lines, start, start, collapse=collapse)], start
        for i in keep:
            if i > prev + 1:
                out.append(f'      ... ({i - prev - 1} parameter lines not shown) ...')
            out.append(_code.numbered(lines, i, i, collapse=collapse))
            prev = i
        body = _code_view(root, rel, sig_end + 1, end, focus_terms, limit=limit - 2 - len(keep), show=show, names=names)
        return '\n'.join(out + [body])
    width = limit - size(start, head)
    toks = focus_terms or {}
    score = [0] * (end + 2)
    for i in range(head + 1, end + 1):
        if toks and i not in prose and not lines[i - 1].lstrip().startswith('#'):
            score[i] = sum(toks.get(t, 0) for w in re.findall(r'[A-Za-z_]\w*', lines[i - 1]) for t in _rank.tokens(w))
        if names and i not in prose and any(re.search(rf'(?<![\w]){re.escape(n)}\b', lines[i - 1]) for n in names):
            score[i] += 20          # a line the place's sentence names (h13, fastapi_13713: self.openapi_tags = ...)
    total = [0] * (end + 2)
    for i in range(head + 1, end + 1):
        total[i] = total[i - 1] + score[i]
    best, best_end, best_score = head + 1, head + 1, -1
    b = head + 1
    for a in range(head + 1, end + 1):        # the window a..b with `width` shown lines and the highest score
        b = max(b, a)
        while b < end and size(a, b + 1) <= width:
            b += 1
        got = total[b] - total[a - 1]
        if got > best_score:
            best, best_end, best_score = a, b, got
        if b == end:
            break
    a, b = best, best_end
    if show:
        a = min(max(show[0], head + 1), end)
        b = a
        while b < end and size(a, b + 1) <= width:
            b += 1
    out = [_code.numbered(lines, start, head, collapse=collapse)]
    if a > head + 1:
        out.append(f'      ... (lines {head + 1}-{a - 1} not shown) ...')
    out.append(_code.numbered(lines, a, b, collapse=collapse))
    if b < end:
        out.append(f'      ... (lines {b + 1}-{end} not shown) ...')
    return '\n'.join(out)


def _outline(root, rel, sym):
    """A long class as its own first lines and one skeleton line per member."""
    lines = _repo.read_lines(root, rel)
    syms = _code.symbols(_code.parse(''.join(lines)))
    members = [s for s in syms if s.name.startswith(sym.name + '.') and '.' not in s.name[len(sym.name) + 1:]]
    first = min([m.start for m in members] + [sym.end + 1]) - 1
    head = _code.numbered(lines, sym.start, min(first, sym.start + 30), collapse=_code.long_strings(
        _code.parse(''.join(lines))))
    return head + '\n' + '\n'.join(f'      {_code.skeleton(rel, m)}' for m in members)


def _place_line(p):
    if p['name'] == '<exports>':
        return f'{p["handle"]} — {p["reason"]}'
    if ROLE == 'coordinator':
        # the coordinator never reads code: line numbers invited 'file.py:676-751' reads (a10, a04)
        return f'{p["handle"]} ({p["rel"]}) — {p["reason"]}'
    return f'{p["handle"]} ({p["rel"]}, lines {p["start"]}-{p["end"]}) — {p["reason"]}'


DEF_LINE = re.compile(r'^\s*(@|(async\s+)?def\s|class\s)', re.M)


# ---------------------------------------------------------------------------------------------------------- names
# The model sees and sends code by its name (Session.send), not by an id (C2, P1): a name says what the code is, keeps
# its meaning after the harness compacts the history (the list that defined C2 is gone), and is how the model refers
# to code anyway (V10: 55 of 118 edit-step calls named code; the local 12B merged ids with names, "C11:file::Name").
# Code-localization tools address code the same way (LocAgent: src/utils.py:MathUtils.calculate_sum; Moatless span
# ids; Agentless "class: X / function: Y"). The ids stay inside the journal.

def _base_handle(x):
    if x['name'] == MODULE:
        return f'{x["rel"]} top level'
    if x['name'] == '<exports>':
        return f'{x["rel"]} imports'
    return x['name']


def _set_handles(items):
    """item['handle']: its qualified name, or 'file::name' when another item has the same name; a file's top-level
    code is the file path, its exports 'file imports'."""
    counts = collections.Counter(_base_handle(x) for x in items)
    for x in items:
        b = _base_handle(x)
        x['handle'] = b if counts[b] == 1 or x['name'] in (MODULE, '<exports>') else f'{x["rel"]}::{x["name"]}'


def _norm_name(text):
    t = _args.clean(text or '')
    t = re.sub(r'^(?:async\s+def|def|class)\s+', '', t)
    t = re.sub(r'\s*::\s*', '::', t)
    if '::' in t and t.endswith(' top level'):
        t = t.rsplit('::', 1)[1]            # 'fastapi/routing.py::fastapi/routing.py top level' (V13b, 14448)
    t = re.sub(r'\s+[(—-].*$', '', t) if not t.startswith('(') else t     # "Name (file, lines ..) — reason" copied
    t = re.sub(r'\(\)$|:$', '', t.strip())
    return t.strip().strip('`')


def _match(items, text, fuzzy=True):
    """The item (candidate or place) that text names: its handle, its qualified name, a unique last part of it
    ("send" for Session.send), its file for top-level code, an old id (C2, P1), or a close spelling of a handle."""
    if '\n' in (text or '').strip():
        return None
    t = _norm_name(text)
    if not t:
        return None
    by = {x['handle']: x for x in items if 'handle' in x}
    if t in by:
        return by[t]
    if re.fullmatch(r'(?i)[CP]\d+', t):
        return next((x for x in items if x.get('id') == t.upper()), None)
    rel, _, name = t.rpartition('::')
    rel = rel.strip()
    for test in (lambda x: x['name'] == name, lambda x: x['name'].endswith('.' + name)):
        hits = [x for x in items if test(x) and (not rel or _rank.names_file(rel, x['rel']))]
        if len(hits) == 1 or (hits and rel):
            return hits[0]
    hits = [x for x in items if x['name'] == MODULE and _rank.names_file(t, x['rel'])]
    if hits:
        return hits[0]
    if not fuzzy:
        return None
    close = difflib.get_close_matches(t, list(by), n=1, cutoff=0.85)
    return by[close[0]] if close else None


def _pick(items, args, fuzzy=True):
    """(matched items, texts that name none of them) from the model's choice: one name per item, or several names in
    one item separated by commas or lines."""
    found, others = [], []
    for a in args:
        if not (a or '').strip():
            continue
        x = _match(items, a, fuzzy)
        if x is not None:
            found.append(x)
            continue
        parts = [q for q in re.split(r'[,;\n]+', a) if q.strip()]
        got = [_match(items, q, fuzzy) for q in parts] if len(parts) > 1 else [None]
        if all(g is not None for g in got):
            found += got
        else:
            others.append(_args.clean(a))
    seen, out = set(), []
    for x in found:
        if id(x) not in seen:
            seen.add(id(x))
            out.append(x)
    return out, others


def _focus(state, entry=None):
    """{token: weight} that a long place is cut around: the plan line's names (3) and the requirements' (1)."""
    out = {}
    texts = [(' '.join(r['text'] for r in state['requirements']), 1)] + ([(entry['intent'], 3)] if entry else [])
    for text, w in texts:
        for term in _statement.terms(text):
            for ident in re.findall(r'[A-Za-z_]\w*', term):
                for t in _rank.tokens(ident):
                    out[t] = max(out.get(t, 0), w)
    return out


def _window(root, state, i, show=None):
    """The decision window of plan entry i: one line with the place and the goal it serves, the patch's change there,
    the code, and the related code that shares the issue's words; the forms are in NEXT and in the system instruction.

    Every answer stays in the history until the harness compacts it (14,336 tokens, the last 5 events kept), and a
    compaction removes the window the model is deciding on: V15 averaged 10-11k prompt tokens per call in the 9 tasks
    it lost and peaked at 8k in the one it solved. So the window carries only what changes this decision, measured in
    tokens (docs/design_single.md, "The decision window"): the statement is in the system instruction (never compacted),
    static forms there too; here the code (numbers cost a third of its tokens, so no padding), the goal and at most 3
    related names."""
    entry = state['plan'][i]
    p = _place(state, entry['place'])
    if p['name'] == MODULE and not os.path.exists(os.path.join(root, p['rel'])):
        spec = f"\nWhat to put in it (the specification): {entry['intent']}" if _specified(entry) else ''
        return (f"Create {p['rel']}: it does not exist yet.{spec}\nSend the whole file: ['{p['rel']}', '<its whole "
                f"content: imports, then the functions and classes>'].")
    _refresh(root, state, p['rel'])
    p = _place(state, entry['place'])
    goal = _goal(state, p)
    head = (f'Now edit {p["handle"]} ({p["rel"]}).' if _narrow(state) else
            f'Now edit {p["handle"]} ({p["rel"]}; place {i + 1} of {len(state["plan"])}).')
    owner = _owner_class(root, p)
    if owner:
        head += f' It is a method of {owner}.'
    if p['reason'] != 'chosen' and not (ROLE == 'editor' and _specified(entry)):
        head += f" Related to the chosen code ({p['reason']}): change it only if the goal needs it here, else ['skip']."
    head += (f'\nThe issue\'s requirement (context): {goal}' if _narrow(state) and _specified(entry) else
             f'\nGoal: {goal}')
    if ROLE == 'editor' and _specified(entry) and entry['intent'] != 'no change':
        head += f'\nWhat to change here (the specification): {entry["intent"]}'
        for e2 in state['plan']:
            q = _place(state, e2['place'])
            if q and q['id'] != p['id'] and _specified(e2) and e2['intent'] != 'no change' and \
                    re.search(r'\b' + re.escape(q['name'].split('.')[-1]) + r'\b', entry['intent']):
                # 'same as is_async_gen_callable': the sentence it refers to (the editor does not see that place)
                head += f'\n  ({q["handle"]}, named there, is specified: {e2["intent"]})'
    for line in _deferred_lines(state):
        head += f'\n{line}'
    if p['name'] == '<exports>':
        n = len(_repo.read_lines(root, p['rel']))
        body = _code_view(root, p['rel'], 1, n, _focus(state, entry))
    else:
        if show is None and (entry.get('intent') or '').startswith('Add the new '):
            show = (max(p['start'], p['end'] - 40), p['end'])     # where the new definition is added
        body = _code_view(root, p['rel'], p['start'], p['end'], _focus(state, entry),
                          limit=EDITOR_EDIT_LINES if ROLE == 'editor' else EDIT_LINES, show=show)
        if re.search(r'\(lines \d+-\d+ not shown\)', body):
            body += (f'\n(Not all of {p["handle"]} is shown: send it with two line numbers inside it to see other '
                     'lines; change the lines shown by their numbers, or rewrite a member whole.)')
        elif '(the parameters, lines' in body:
            body += f'\n(The parameters of {p["handle"]} are not shown: change its body by line numbers.)'
        members = _members(root, p)
        if members:
            body = members + '\n' + body
    here = '' if ROLE == 'editor' and state.get('editor_review') else _patch_here(root, p)   # (the diff is above)
    parts = [head, here, body] if here else [head, body]
    context = _context_view(root, p, _focus(state, entry))
    if context:
        parts.append(context)
    if ROLE == 'editor' and not _narrow(state):
        others = _plan_view(root, state, p)
        if others:
            parts.append(others)
    test = _test_once(root, state, p)
    if test:
        parts.append(test)
    return _seen_mark(p['rel'], '\n'.join(parts))


def _plan_view(root, state, p):
    """The other places of the plan, for the editor of p: the code of a short one, the signature of a longer one. V18:
    the editor of is_coroutine_callable opened is_gen_callable, is_async_gen_callable and _unwrapped_call one by one
    (20 reads) to make the same change there."""
    out, room = [], PLAN_CHARS
    for e in state['plan']:
        q = _place(state, e['place'])
        if q is None or q['id'] == p['id'] or q['name'] in (MODULE, '<exports>'):
            continue
        lines = _repo.read_lines(root, q['rel'])
        if q['end'] - q['start'] + 1 <= PLAN_PLACE_LINES:
            code = _code.numbered(lines, q['start'], q['end'], collapse=[])
            if q['rel'] != p['rel']:
                _seen_mark(q['rel'], code)      # (the window marks the lines of p's file)
            text = f"  {q['handle']} ({q['rel']}, {q['reason']}):\n" + code
        else:
            text = f"  {q['handle']} ({q['rel']}:{q['start']}-{q['end']}, {q['reason']}): {lines[q['start'] - 1].strip()[:90]}"
        if len(text) > room:
            break
        out.append(text)
        room -= len(text)
    if not out:
        return ''
    return ('The other places of the plan (each has its editor; if the same change is needed there, you may edit it '
            'here by its name):\n' + '\n'.join(out))


_SEEN = {}      # the state of this call, for _seen_mark (set by main)
UNSEEN_OK = 3   # an edit may replace this many non-blank lines that were never shown


def _seen_mark(rel, text):
    """Records the numbered lines of rel shown in text ('184|...'), so that an edit can be checked against what the
    model has read."""
    st = _SEEN.get('state')
    if st is None or not rel:
        return text
    seen = set(st.setdefault('seen', {}).get(rel, []))
    seen.update(int(m) for m in re.findall(r'(?m)^\s*(\d+)\|', text))
    st['seen'][rel] = sorted(seen)
    return text


def _unseen(root, state, rel, start, end):
    """The non-blank lines start..end of rel that were never shown (a09: the editor replaced lines 174-281 after
    reading 174-233, and the tests undid it three times while its reasoning, cut by the budget, never read the rest)."""
    seen = set((state.get('seen') or {}).get(rel, []))
    lines = _repo.read_lines(root, rel)
    return [n for n in range(start, min(end, len(lines)) + 1) if n not in seen and lines[n - 1].strip()]


def _show_unseen(root, state, p, start, end, unseen):
    a, b = unseen[0], min(unseen[-1], unseen[0] + PLACE_LINES - 1)
    # whole lines, nothing collapsed: b9 refused the same edit 3 times because the unseen lines were long strings
    # that every view collapsed into '... (N lines of text) ...', so they never counted as shown
    lines = _repo.read_lines(root, p['rel'])
    view = _seen_mark(p['rel'], f'Lines {a}-{b} of {p["handle"]}:\n' + _code.numbered(lines, a, b, collapse=[]))
    spans, s0 = [], unseen[0]
    for x, y in zip(unseen, unseen[1:] + [None]):
        if y != x + 1:
            spans.append(f'{s0}-{x}' if x != s0 else str(s0))
            s0 = y
    return (f'Not applied yet: lines {", ".join(spans[:4])} are inside the edit\'s range {start}-{end} but were not shown, so '
            'the edit would replace code not read. Here they are. Send the edit again with new lines for the whole range, '
            f'or with a smaller range. Nothing was changed.\n{view}')


def _goal(state, p):
    """The requirement this place most likely serves (the most words shared with the place's name and file), cut at
    200 characters, and how many others there are."""
    reqs = state['requirements']
    if not reqs:
        return '(see the issue)'
    words = set(_rank.tokens(p['name'].replace('.', '_'))) | set(_rank.tokens(p['rel'].rsplit('/', 1)[-1][:-3]))
    best = max(reqs, key=lambda r: len(words & {t for w in re.findall(r'[A-Za-z_]\w*', r['text'])
                                                for t in _rank.tokens(w)}))
    text = best['text'] if len(best['text']) <= 200 else best['text'][:197] + '...'
    more = f' ({len(reqs) - 1} more requirement{"s" if len(reqs) > 2 else ""} in the issue)' if len(reqs) > 1 else ''
    return text + more


def _test_once(root, state, p):
    """An existing test that uses a chosen place, the first time the place is shown (how it is used today); later
    windows of the same place do not repeat it."""
    if p['name'] in (MODULE, '<exports>') or p['reason'] != 'chosen':
        return ''
    key = f'{p["rel"]}::{p["name"]}'
    shown = state.setdefault('tests_shown', [])
    if key in shown:
        return ''
    shown.append(key)
    found = _tests.example(root, p['rel'], p['name'])
    if not found:
        return ''
    rel, lines = found
    return f'A test that uses it today ({rel}):\n' + '\n'.join('  ' + l for l in lines)


def _patch_here(root, p):
    """The patch's change inside place p, as git diff hunks, or ''. A place opened again after an edit shows the code as
    it is now; without its diff the model took the new code for the old and edited again (local run of rich_3006 with
    the 26B: "I haven't actually applied the fix yet ... the code already has `is`", 20 calls after the right edit)."""
    if p['name'] == '<exports>':
        return ''
    diff = _repo.git(root, 'diff', '-U1', '--', p['rel']) or ''
    hunks, cur = [], None
    for line in diff.splitlines():
        m = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
        if m:
            a = int(m.group(1))
            b = a + max(int(m.group(2) if m.group(2) is not None else 1), 1) - 1
            cur = [line] if a <= p['end'] and p['start'] <= b else None
            if cur is not None:
                hunks.append(cur)
        elif cur is not None and line[:1] in ' +-':
            cur.append(line)
    if not hunks:
        return ''
    body = '\n'.join(l for h in hunks for l in h[1:])
    return (f'Your patch already changes {p["handle"]} (the numbered code below is the code now, with this change):\n'
            f'{body}')


def _owner_class(root, p):
    """'class X(Base) (line n)' for a method place, so that its class and bases are known without asking."""
    if '.' not in p['name'] or p['name'] in (MODULE, '<exports>'):
        return ''
    try:
        syms = _code.symbols(_code.parse(''.join(_repo.read_lines(root, p['rel']))))
    except OSError:
        return ''
    cls = next((x for x in syms if x.name == p['name'].rsplit('.', 1)[0] and x.kind == 'class'), None)
    return f'{_code.signature(cls)} (line {cls.def_line})' if cls else ''


def _members(root, p):
    """One line listing the methods of a class place with their lines, so that the window answers which methods
    exist even when only part of the class is shown."""
    try:
        syms = _code.symbols(_code.parse(''.join(_repo.read_lines(root, p['rel']))))
    except (OSError, SyntaxError, ValueError):
        return ''
    own = next((x for x in syms if x.name == p['name']), None)
    if own is None or own.kind != 'class' or own.end - own.start + 1 <= EDIT_LINES + 30:
        return ''
    members = [x for x in syms if x.name.startswith(p['name'] + '.') and '.' not in x.name[len(p['name']) + 1:]]
    first = next((x for x in members if not x.name.endswith('.__init__')), members[0]) if members else None
    tail = '. A member is changed by sending its whole new code' if first else ''
    return 'Members: ' + ', '.join(f'{x.name.split(".")[-1]} {x.start}-{x.end}' for x in members) + tail


USES = 3        # definitions the place calls or reads
USES_CHARS = 900    # their total size
SHORT_MEMBER = 15   # a member of the place's own class up to this many lines is shown as code
CALLERS = 1     # code that calls the place, one line each
# The editor (agent_tool variant) works on one place in a fresh 32k context; V18's editors made 289 reads in three
# tasks (docs/feedback_audit.md, "What the editor reads"): code the place uses (31), code that calls it (33), other
# planned places (25), ranges of a long place (60). Its window carries more of that from the start.
EDITOR_EDIT_LINES = 90   # a place up to this + 30 lines is shown whole
EDITOR_USES = 5
EDITOR_USES_CHARS = 2000
EDITOR_CALLERS = 4
PLAN_CHARS = 2500        # the other planned places shown in the window
PLAN_PLACE_LINES = 25    # a planned place up to this many lines is shown as code
SPEC_USES = 4            # the specifier: the definitions each place uses, and its callers
SPEC_USES_CHARS = 1500
SPEC_CALLERS = 3
# vf4 lab (fastapi_14419): Dependant.computed_scope (7 lines) was shown as a signature and the specifier named it
# with 'missing' to read it
SPEC_SHORT_MEMBER = 10


def class_attributes(node):
    """{attribute: line} of the attributes a class declares: its class-level assignments (dataclass fields, class
    attributes) and the self.<attribute> its methods assign (the first assignment of each)."""
    out = {}
    for st in node.body:
        if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            out.setdefault(st.target.id, st.lineno)
        elif isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, st.lineno)
    for n in ast.walk(node):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == 'self':
                    out.setdefault(t.attr, n.lineno)
    return out


def _keyword_sites(root, table, callee, keyword, trees, limit=2):
    """[(rel, caller, line number, line text, value node)] of calls of callee (a short name) that pass keyword=."""
    out = []
    for r, c, n in table.calls.iter(callee):        # (files read only until limit sites are found)
        if _repo.is_test_path(r) or _repo.is_doc_path(r):
            continue
        if r not in trees:
            trees[r] = _code.parse(_repo.read_text(root, r))
        tree = trees[r]
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.lineno == n:
                f = node.func
                if (f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', None)) != callee:
                    continue
                for kw in node.keywords:
                    if kw.arg == keyword:
                        out.append((r, c, kw.value.lineno, _repo.read_lines(root, r)[kw.value.lineno - 1].strip(),
                                    kw.value))
        if len(out) >= limit:
            break
    return out[:limit]


ATTRS = 4               # attributes of the place's class shown with where they are declared and set
ATTRS_CHARS = 1200


def _attributes_view(root, table, p, cls, used, defined):
    """The attributes of its own class the place reads that are data, not methods: the line that declares each and
    the calls that set it (the class built with attribute=..., one step further when the value is a parameter of
    the function that builds it). vf4 lab (fastapi_14419, the 31B): Dependant.cache_key reads
    self.security_scopes; the window showed neither the field nor get_dependant, which sets it, and the
    specifier asked for them three times through 'missing' (checklist W3, B16)."""
    cls_sym = next((x for x in table.syms.get(p['rel'], []) if x.name == cls and x.kind == 'class'), None)
    if cls_sym is None or getattr(cls_sym, 'node', None) is None:
        return ''
    decl = class_attributes(cls_sym.node)
    lines = _repo.read_lines(root, p['rel'])
    names = [n for n in dict.fromkeys(n for _, n, h in sorted(used) if h == 'self') if n in decl and n not in defined]
    trees, rows, chars = {}, [], 0
    short_cls = cls.split('.')[-1]
    for name in names[:ATTRS]:
        row = [f'  self.{name}: declared in {cls} ({p["rel"]}:{decl[name]}): {lines[decl[name] - 1].strip()[:100]}']
        for r, c, n, text, value in _keyword_sites(root, table, short_cls, name, trees):
            row.append(f'      set at {r}:{n} in {c}: {text[:90]}')
            fn = next((x for x in table.syms.get(r, []) if x.name == c), None)
            params = {a.arg for a in fn.node.args.args + fn.node.args.kwonlyargs} \
                if fn is not None and isinstance(fn.node, (ast.FunctionDef, ast.AsyncFunctionDef)) else set()
            if isinstance(value, ast.Name) and value.id in params:
                for r2, c2, n2, text2, _ in _keyword_sites(root, table, c.split('.')[-1], value.id, trees, limit=1):
                    row.append(f'        {c.split(".")[-1]} gets {value.id}= at {r2}:{n2} in {c2}: {text2[:80]}')
        text = '\n'.join(row)
        if chars + len(text) > ATTRS_CHARS:
            break
        rows.append(text)
        chars += len(text)
    rest = [n for n in decl if n not in names and n not in defined and not n.startswith('__')]
    if rows and rest:
        # the requirement may turn on a field the place does not read yet (fastapi_14419: 'sub-dependencies' is the
        # field dependencies): its other fields by name, one line
        rows.append(f'  {cls} also declares: ' + ', '.join(rest[:30]))
    return ('Attributes it reads (where each is declared and where it is set):\n' + '\n'.join(rows)) if rows else ''


def _context_view(root, p, focus=None):
    """The repository code the place calls or reads (where it is defined, its signature and first doc line; a short
    member of the place's own class as its code) and the code that calls the place (the line of the call). These are what the model asked about in the edit step of
    the local runs; showing them makes the questions unnecessary (Agentless: prepared inputs, the model never asks
    for code; CodePlan: the dependencies of the edited code)."""
    if p['name'] in (MODULE, '<exports>'):
        return ''
    try:
        lines = _repo.read_lines(root, p['rel'])
        tree = _code.parse(''.join(lines))
    except OSError:
        return ''
    if tree is None:
        return ''
    table = _impact.Table(root, docs=False)
    own = p['name']
    cls = own.rsplit('.', 1)[0] if '.' in own else (own if any(
        x.name == own and x.kind == 'class' for x in table.syms.get(p['rel'], [])) else '')
    calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    imported = {}               # name -> module it is imported from in this file ('' for a relative import's level)
    modules = {}                # name -> the module an `import m` / `import m as name` binds
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for al in n.names:
                imported[al.asname or al.name] = (('.' * n.level) + (n.module or ''), al.name)
        elif isinstance(n, ast.Import):
            for al in n.names:
                modules[al.asname or al.name.split('.')[0]] = al.name if al.asname else al.name.split('.')[0]
    here = {x.name for x in table.syms.get(p['rel'], [])}
    used = []                   # (line, name, how): f() / obj.f(), self.attr, a Name of this file or imported
    for node in ast.walk(tree):
        if not (p['start'] <= getattr(node, 'lineno', 0) <= p['end']):
            continue
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in ('self', 'cls'):
                used.append((node.lineno, node.attr, 'self'))
            elif id(node) in calls:
                used.append((node.lineno, node.attr, 'call'))
            else:
                used.append((node.lineno, node.attr, 'attr'))      # obj.prop: a property of another class
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in here or node.id in imported:
                used.append((node.lineno, node.id, 'name'))

    assigned_self = {}          # class -> the self attributes its methods assign (instance attributes)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assigned_self[node.name] = {t.attr for n in ast.walk(node) if isinstance(n, (ast.Assign, ast.AnnAssign))
                                        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                        and t.value.id == 'self'}
    if cls and '.' in cls:
        assigned_self[cls] = assigned_self.get(cls.split('.')[-1], set())

    def module_of(rel):
        return _repo.module_name(root, rel)

    def resolve(name, how):
        found = table.find(name)
        if how == 'self':
            own_cls = [(r, x) for r, x in found if r == p['rel'] and cls and x.name == f'{cls}.{name}']
            if own_cls or not cls:
                return own_cls
            if cls in assigned_self and name in assigned_self[cls]:
                # p3 (rich_3296): self.padding of Syntax, set in its __init__, was shown as Table.padding
                return []
            # a11: self.resolve_redirects in Session.send is defined in its base SessionRedirectMixin
            return [(r, x) for r, x in found if x.name.endswith('.' + name) and x.kind == 'def'][:1] \
                if len([1 for _, x in found if x.name.endswith('.' + name)]) <= 2 else []
        if how == 'name':
            if name in here:
                return [(r, x) for r, x in found if r == p['rel'] and x.name == name]
            mod, orig = imported[name]
            mod = mod.lstrip('.')
            return [(r, x) for r, x in table.find(orig) if x.name == orig
                    and (not mod or module_of(r).endswith(mod) or module_of(r).endswith(mod + '.__init__'))]
        defs = [(r, x) for r, x in found if x.name.split('.')[-1] == name]
        if how == 'attr':
            return defs if len(defs) <= 2 else []      # an attribute read: only a name the repository defines rarely
        return defs if len(defs) <= 3 else []          # a common method name (get, copy of many classes) says little

    uses, seen, chars = [], set(), 0
    focus = focus or {}
    wide = ROLE == 'editor' and not _narrow(_SEEN.get('state') or {})
    n_uses, uses_chars, n_callers = ((EDITOR_USES, EDITOR_USES_CHARS, EDITOR_CALLERS) if wide
                                     else (SPEC_USES, SPEC_USES_CHARS, SPEC_CALLERS) if ROLE == 'specifier'
                                     else (USES, USES_CHARS, CALLERS))

    def weight(item):
        name, how = item
        shared = sum(focus.get(t, 0) for t in _rank.tokens(name))
        if how in ('self', 'attr'):
            return 10 + shared              # members and properties the place reads: its direct dependencies
        return shared

    # code that shares words with the requirements first; at most 2 that share none (near-miss context costs more than
    # it gives: Chroma, P7)
    order = sorted(dict.fromkeys((n, h) for _, n, h in sorted(used)), key=lambda it: -weight(it))
    table.prefetch([n for n, _ in order] + [orig for _, orig in imported.values()])
    plain = 0
    for name, how in order:
        defs = [(r, x) for r, x in resolve(name, how)
                if not (r == p['rel'] and (x.name == own or x.name.startswith(own + '.')))]
        mine = how in ('self', 'attr')                  # short definitions are shown as code
        if not defs:
            continue
        if weight((name, how)) == 0:                    # counted only when it would be shown
            plain += 1
            if plain > 0:
                continue
        for r, x in defs[:2]:
            if (r, x.name) in seen:
                continue
            seen.add((r, x.name))
            if mine and x.end - x.start + 1 <= (SHORT_MEMBER if ROLE == 'editor' else SPEC_SHORT_MEMBER):
                # a short member of the same class (a property, a helper): its code, since its name says little
                text = f'  {x.name} ({r}):\n' + _code.numbered(_repo.read_lines(root, r), x.start, x.end, collapse=[])
            else:
                sig = _short_signature(x)
                text = f'  {x.name} ({r}:{x.start}) {sig if len(sig) <= 70 else sig[:67] + "..."}'
                body = _repo.read_lines(root, r)[x.start - 1:x.end]
                ys = [(x.start + k, l.strip()) for k, l in enumerate(body) if re.match(r'\s*yield\b', l)]
                if ys:
                    # a11: the editor of Session.send took resolve_redirects to yield the first response too and lost
                    # the place on that; what a generator yields is not in its signature (W3)
                    text += '\n      it yields: ' + '; '.join(f'{c[:60]} (line {n})' for n, c in ys[:2])
            if chars + len(text) > uses_chars:
                break
            uses.append(text)
            chars += len(text)
        if len(uses) >= n_uses:
            break
    siblings = []
    if cls and own != cls and focus:
        # p3 (rich_3296): the fix was Syntax._get_base_style (which applies the background_color override), a member of
        # the place's own class that the place does not call. Members that use what the place uses (the same calls and
        # attributes: _get_base_style calls self._theme.get_background_style() as the place does) come first, the
        # issue's words break ties
        lines_ = _repo.read_lines(root, p['rel'])
        words = {t for t in focus if focus[t] > 0} - set(_rank.tokens(cls.split('.')[-1]))
        mine_used = {n for _, n, _ in used}
        scored = []
        for x in table.syms.get(p['rel'], []):
            if not x.name.startswith(cls + '.') or x.name == own or x.name.count('.') != cls.count('.') + 1 or \
                    (p['rel'], x.name) in seen or x.name.split('.')[-1].startswith('__'):
                continue
            body = ''.join(lines_[x.start - 1:x.end])
            toks = set(_rank.tokens(x.name.split('.')[-1])) | {t for w in re.findall(r'[A-Za-z_]\w+', body)
                                                               for t in _rank.tokens(w)}
            hit = toks & words
            node_names = {n.attr for n in ast.walk(x.node) if isinstance(n, ast.Attribute)} if getattr(x, 'node', None) \
                else set(re.findall(r'\.(\w+)', body))
            shared = len(mine_used & node_names)
            # vf4 lab (fastapi_14419): is_gen_callable and is_async_gen_callable shared only self.call with
            # cache_key and were shown in every window (P7); rich_3296's _get_base_style shares two names
            if shared >= 2:
                scored.append((-shared, -len(hit), x.end - x.start, x.start, x))
        for *_, x in sorted(scored, key=lambda t: t[:4])[:2]:
            if x.end - x.start + 1 <= 12:
                text = (f'  {x.name} ({p["rel"]}):\n'
                        + _code.numbered(lines_, x.start, x.end, collapse=[]))
            else:
                text = f'  {x.name} ({p["rel"]}:{x.start}): {_short_signature(x)[:70]}'
            if chars + len(text) > uses_chars + 600:
                break
            seen.add((p['rel'], x.name))
            # gk12: listed under 'Code that on_event uses', include_router and add_api_route read as code the place
            # calls (checklist B11, B12); they are siblings that use the same code, under their own heading
            siblings.append(text)
            chars += len(text)
    lib = _library_uses(root, p, tree, used, imported, modules, table, cls)
    room = uses_chars - chars
    for text in lib:
        if len(text) > room:
            break
        uses.append(text)
        room -= len(text)
    short = own.split('.')[-1]
    callers = () if short.startswith('__') else (
        (r, c, n) for r, c, n in table.calls.iter(short, order=lambda r: (r != p['rel'], r)) if c and (r, c) != (
            p['rel'], own) and not _repo.is_doc_path(r))
    calls = []
    for r, c, n in itertools.islice(callers, n_callers):    # (read only as far as the callers shown)
        text = _repo.read_lines(root, r)[n - 1].strip()
        calls.append(f'  {r}:{n} in {c}: {text[:80]}')
    out = []
    if uses:
        out.append(f'Code that {short} uses:\n' + '\n'.join(uses))
    if cls and own != cls:
        defined = {x.name.split('.')[-1] for x in table.syms.get(p['rel'], []) if x.name.startswith(cls + '.')}
        attrs = _attributes_view(root, table, p, cls, used, defined)
        if attrs:
            out.append(attrs)
    if siblings:
        out.append(f'Other members of its class that use the same code ({short} does not call them):\n'
                   + '\n'.join(siblings))
    if calls:
        more = f' ({len(callers)} calls, first {n_callers})' if len(callers) > n_callers else ''
        out.append(f'Code that calls {short}{more}:\n' + '\n'.join(calls))
    return '\n'.join(out)


LIBRARY_USES = 2   # library definitions shown in one window


def _library_uses(root, p, tree, used, imported, modules, table, cls):
    """Library code the place calls: names imported from a library, and methods of its class that only a library
    base class defines (V14 fastapi_14851: APIRouter.on_event calls self.add_event_handler, which Starlette's Router
    defines; the model read 12 slices looking for it). Each as its qualified name and signature, or its code when it
    is short. Read only."""
    def module_of(name):
        if name in imported:
            mod, orig = imported[name]
            return f'{mod}.{orig}' if mod and not mod.startswith('.') else None
        return modules.get(name)

    sym = next((x for x in table.syms.get(p['rel'], []) if x.name == p['name']), None)
    body = sym.node.body[0].lineno if sym is not None and getattr(sym.node, 'body', None) else p['start']
    wanted = []
    for line, name, how in sorted(used):
        if line < body:
            continue                    # the signature's annotations (Annotated, Doc): not what the code does
        if how in ('name', 'call') and name in imported and not table.find(imported[name][1]):
            dotted = module_of(name)
            if dotted and dotted.split('.')[0] not in sys.stdlib_module_names:
                wanted.append(dotted)
        elif how == 'self' and cls and not any(x.name.split('.')[-1] == name for _, x in table.find(name)):
            node = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls.split('.')[-1]),
                        None)
            for base in (node.bases if node is not None else []):
                try:
                    parts = ast.unparse(base).split('.')
                except Exception:       # noqa: BLE001
                    continue
                head = module_of(parts[0])
                if head:
                    wanted.append('.'.join([head] + parts[1:] + [name]))
    out, seen = [], set()
    for dotted in dict.fromkeys(wanted):
        if len(out) >= LIBRARY_USES:
            break
        try:
            hit = _library_def(root, dotted)
        except Exception:               # noqa: BLE001  (a library that fails to import is not shown)
            hit = None
        if hit is None or hit[3] in seen:
            continue
        seen.add(hit[3])
        path, a, b, qual = hit
        lines = _repo.read_lines('/', path)
        short = path.split('site-packages/')[-1] if 'site-packages/' in path else os.path.basename(path)
        if b - a + 1 <= SHORT_MEMBER:
            out.append(f'  {qual} (library, read only; {short}):\n' + _code.numbered(lines, a, b, collapse=[]))
        else:
            d = next((i for i in range(a, b + 1) if re.match(r'\s*(async\s+def|def|class)\s', lines[i - 1])), a)
            out.append(f'  {qual} (library, read only; {short}, lines {a}-{b}): {lines[d - 1].strip()[:140]}')
    return out


def _requirements_view(state):
    out = ['Requirements (from the statement):']
    for r in state['requirements']:
        kind = r['type'] if r['type'] != 'new' else 'new: ' + ', '.join(f'`{n}`' for n in r['names'])
        out.append(f'  - [{kind}] {r["text"]}')
    out.extend(_deferred_lines(state))
    return '\n'.join(out)


def _deferred_lines(state):
    """What the issue itself leaves for later, in its own words (not part of this fix)."""
    return [f'Left for later by the issue (not part of this fix): {d}' for d in state.get('deferred') or []]


def _candidates_view(state, title='Candidates (the code most related to the statement):'):
    out = [title]
    for c in state['candidates']:
        if ROLE == 'coordinator':
            # no line numbers for the coordinator, which never reads code (they invited 'file.py:a-b' reads)
            out.append(f'  {c["handle"]}  ' + re.sub(r', lines \d+-\d+', '', c['line']))
            out.extend('      ' + re.sub(r'^\d+:\s*', '', e) for e in c.get('evidence', []))
            continue
        out.append(f'  {c["handle"]}  {c["line"]}')
        out.extend(f'      {e}' for e in c.get('evidence', []))
    if not state['candidates']:
        out.append("  (none found: name the code to change as '<file>::<Name>')")
    return '\n'.join(out)


# ---------------------------------------------------------------------------------------------------------- state

def _place(state, pid):
    for p in state['places']:
        if p['id'] == pid:
            return p
    return None


def _name_of(state, pid):
    p = _place(state, pid)
    return p.get('handle', pid) if p else pid


def _refresh(root, state, rel):
    """Line ranges of the places of rel after an edit moved them (found again by name)."""
    try:
        syms = _code.symbols(_code.parse(''.join(_repo.read_lines(root, rel))))
    except OSError:
        return
    for p in state['places']:
        if p['rel'] == rel and p['name'] not in (MODULE, '<exports>'):
            found = [s for s in syms if s.name == p['name']]
            if found:
                p['start'], p['end'] = found[0].start, found[0].end


DOC_ARG = re.compile(r"""Doc\((?:'[^']*'|"[^"]*"|[^()])*\)""")
EVIDENCE_LINES = 2


def _short_signature(sym):
    """The signature without `Doc(...)` texts and `Annotated[...]` wrappers, at most 100 characters."""
    sig = DOC_ARG.sub('', _code.signature(sym))
    sig = re.sub(r'Annotated\[(.*?),\s*\]', r'\1', sig)
    return sig if len(sig) <= 100 else sig[:97] + '...'


def _evidence(root, doc, literals, toks):
    """The 1-2 lines of a candidate that hold the most of the statement's code literals and terms, numbered: what
    makes the right candidate recognizable (V10: the model skipped the right candidate for want of the line that
    holds `Parameters.empty`)."""
    all_lines = _repo.read_lines(root, doc.rel)
    lines = all_lines[doc.start - 1:doc.end]
    first = doc.sym.def_line if doc.sym is not None else 0
    prose = set()                       # lines inside docstrings and other multi-line texts: words, not code
    tree = _code.parse(''.join(all_lines))
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and (node.end_lineno or 0) > node.lineno:
            prose.update(range(node.lineno, node.end_lineno + 1))
    scored = []
    for i, line in enumerate(lines, doc.start):
        text = line.strip()
        if not text or i == first or i in prose or text.startswith(('#', '"""', "'''", '@')):
            continue
        score = 3 * sum(lit in line for lit in literals)
        score += sum(toks.get(t, 0) for w in re.findall(r'[A-Za-z_]\w*', line) for t in _rank.tokens(w))
        if score > 0:
            scored.append((score, i, text))
    best = sorted(scored, key=lambda x: (-x[0], x[1]))[:EVIDENCE_LINES]
    return [f'{i}: {t[:110]}' for _, i, t in sorted(best, key=lambda x: x[1])]


def _candidate(index, doc, matched, note=None, root=None, literals=(), toks=None):
    if doc.sym is None:
        line = f'(lines {doc.start}-{doc.end}: imports and top-level code)'
    else:
        doc_line = _code.first_doc_line(doc.sym)
        line = f'({doc.rel}, {doc.sym.kind}, lines {doc.sym.start}-{doc.sym.end}) {_short_signature(doc.sym)}' + (
            f' — {doc_line[:80]}' if doc_line else '')
    words = [m for m in matched if len(m) >= 4 and m not in _statement.STOP]
    line += f'  [{note}]' if note else (f'  [matches: {", ".join(words[:4])}]' if words else '')
    ev = _evidence(root, doc, literals, toks or {}) if root else []
    return {'rel': doc.rel, 'name': doc.name, 'start': doc.start, 'end': doc.end, 'line': line, 'evidence': ev}


def _rank_candidates(root, text, extra_items, requirements, n=N_CANDIDATES, only_rel=None, within=None):
    index = _rank.Index(root)
    terms = _statement.terms(text)
    mt, mpaths = _statement.model_terms(extra_items)
    for k, v in mt.items():
        terms[k] = max(v, terms.get(k, 0))
    out, seen = [], set()
    literals = [x for x in dict.fromkeys(
        re.findall(r'`([^`\n]{4,60})`', text) + [n.split('.')[-1] for n in _statement.code_names(text)]) if len(x) >= 4]
    toks = {}
    for term, w in terms.items():
        for ident in re.findall(r'[A-Za-z_]\w*', term):
            for t in _rank.tokens(ident):
                if len(t) >= 4 and t not in _statement.STOP:
                    toks[t] = max(toks.get(t, 0), 1 if w < 2 else 2)
    for r in requirements if within is None else ():
        if r['type'] != 'new':
            continue
        for name in r['names']:
            if '.' not in name:
                continue
            var, new = name.rsplit('.', 1)
            owner = _rank.owner_class(index, var.split('.')[-1])
            if owner and owner not in seen:
                doc = index.doc(*owner)
                if doc:
                    seen.add(owner)
                    out.append(_candidate(index, doc, [], note=f'where the new `{new}` would go', root=root,
                                          literals=literals, toks=toks))
    ranked = index.rank(terms, _statement.paths(text), n=None, guessed=mpaths)
    per_file, more = {}, {}
    for _, doc, matched in ranked:
        if only_rel and doc.rel != only_rel or within and not doc.name.startswith(within + '.'):
            continue
        if (doc.rel, doc.name) in seen:
            continue
        if len(out) >= n:
            break
        if not only_rel and not within and per_file.get(doc.rel, 0) >= PER_FILE:
            # h1-s1 (fastapi_14583): 8 of the 10 candidates were the Param classes of one file, and the places to
            # change (in other files) were not on the list; the rest of a file is counted and named by its path
            more[doc.rel] = more.get(doc.rel, 0) + 1
            continue
        seen.add((doc.rel, doc.name))
        per_file[doc.rel] = per_file.get(doc.rel, 0) + 1
        out.append(_candidate(index, doc, matched, root=root, literals=literals, toks=toks))
    for c in out:
        if more.get(c['rel']) and c is [x for x in out if x['rel'] == c['rel']][-1]:
            c['line'] += f'\n      (and {more[c["rel"]]} more in {c["rel"]}: send the path to list them)'
    for i, c in enumerate(out, 1):
        c['id'] = f'C{i}'
    _set_handles(out)
    return out, index


# ---------------------------------------------------------------------------------------------------------- steps

def s0(root, state, args):
    _tests.record_preexisting(root)
    items = [a for a in args if a and a.strip()]
    if not items or len(items[0].strip()) < 10:
        return 'The first call copies the issue statement. Nothing was searched yet.'
    statement = items[0]
    while len(items) > 1 and re.match(r'(?i)\s*(fix(es|ed)?|close[sd]?|resolve[sd]?|refs?)\b.*(https?://|#\d)', items[1]):
        # h1 (rich_3469): the statement's second line 'Fixes https://…' sent as the second item; the script said only
        # one line was read, and the resend lost the term that had ranked the right place third
        statement += '\n' + items.pop(1)
    if '\n' not in statement.strip() and '\\n' in statement:
        # written with escaped line breaks (V10: the requirements then held 'nThis', 'nShould')
        statement = statement.replace('\\r\\n', '\n').replace('\\n', '\n')
    # any copy is accepted, even one line: copying long text exactly is what models do worst (B2), refusing it cost a
    # call in 5 of 10 V10 runs, and the whole statement is in the system instruction anyway
    extra = [t.strip() for a in items[1:] for t in re.split(r'[\n,;]', a) if t.strip()]
    text = _statement.clean(statement) or statement
    _state.save('statement', {'text': text, 'terms': extra})
    try:
        _example.start(root, statement)          # the issue's example on the original code (F2: output to repair from)
    except Exception as e:                      # never a traceback: the example is an aid
        _state.record({'error': f'example {type(e).__name__}: {e}'})
    state['deferred'] = _statement.deferred(statement)
    reqs = []
    index = _rank.Index(root)
    for i, r in enumerate(_statement.requirements(text) or [text[:300]], 1):
        kind, names = _statement.change_type(r, index.names.__contains__)
        old_names = [names[0].split('.')[-1]] if kind == 'rename' and names and \
            names[0].split('.')[-1] in index.names else []          # "A -> B", "rename A to B": A is the old name
        reqs.append({'id': f'R{i}', 'text': r, 'type': kind, 'names': names, 'old': old_names})
    cands, _ = _rank_candidates(root, text, extra, reqs)
    _journal.start(state, reqs, cands)
    # V13b fastapi_14986 and 14851: the model copied only the title, and every window's "what the change must do" held
    # the title alone; the issue's concrete sentences never reached the edit step. Asked once, never refused.
    short = len([l for l in statement.splitlines() if l.strip()]) <= 1 and not state.get('statement_asked')
    state['statement_short'] = short
    state['statement_asked'] = True
    more = ('\n\nOnly one line of the issue was read, so the requirements come from that line alone. If the issue has '
            "more text, send all of it now as ['<the whole issue statement>'] and the requirements and candidates are "
            'made again from it; if that line was all of it, go on.') if short else ''
    return f'{_requirements_view(state)}\n\n{_candidates_view(state)}{more}\n\nChoose the code to change.'


def _more_statement(state, args):
    """True when, after a one-line copy, the model sends more of the statement at the choose step: a long text item
    (several lines, or 20 words or more) that names no candidate."""
    items = [a for a in args if a and a.strip()]
    if not state.get('statement_short') or state['step'] != 'D1' or not items:
        return False
    first = items[0].replace('\\n', '\n')
    long_text = len([l for l in first.splitlines() if l.strip()]) >= 2 or len(first.split()) >= 20
    return long_text and _match(state['candidates'], first) is None


NAME_LIKE = re.compile(r'([\w./-]+::)?[A-Za-z_][\w.]*(\(\))?|([\w./-]+::)?[\w./-]+\.py( top level)?')
BIG_FILE = 60       # a file longer than this is never one place: it is chosen through its functions and classes


# ---------------------------------------------------------------------------------------------------------- finding
# V14: 51 of 161 calls were reads of line ranges, and the model said what they were for: "I can't grep", "search for
# super().__init__", "where is root_path_in_servers defined", "find test_split_cells", "search for pad(1)". The reads
# were a search done by hand (fastapi_14986 read fastapi/applications.py in 16 slices looking for one parameter and
# timed out). So code outside the window is reached by finding, never by reading: a name, a text or a line range is
# answered with the functions and classes that hold it, each with the lines that match (grep's answer, numbered), and
# their code is shown only once one is chosen. Choosing outside the places replaces the places not edited yet, so
# every look is a decision that leaves a trace (B9), and no answer shows code that no decision asked for.

SEARCH_LINES = 3    # matching lines shown under one candidate (as grep would show them)
SEARCH_WORDS = ('search', 'grep', 'find', 'look for', 'search for')


def _is_tests(text):
    """A test directory or test file named without line numbers ('tests/', 'tests/test_repr.py')."""
    t = _args.clean(text or '').strip('/')
    return bool(t) and '\n' not in t and (t in _repo.TEST_DIRS or _repo.is_test_path(t if t.endswith('.py') else
                                                                                       t + '/x.py'))


TESTS_NOTE = ('Test files are not changed here: the harness removes every change to a test file from the patch, and '
              'the hidden tests, written by the issue\'s authors (with the test the issue mentions), replace them. So a '
              'test written here never counts. Every edit is checked by running the existing tests that use the '
              'changed code ("Kept and checked" means they passed).')
TESTS_READ = ' To see how a test uses some code, send the test file with two line numbers.'   # (single role only)


RUN_NOTE = ('Code is not run here: there is no shell and no Python command. The check of a change is the existing '
            'tests that use the changed code, run after every edit ("Kept and checked" means they passed); the hidden '
            'tests, with the issue\'s own example, run after submit_patch.')


def _is_code_to_run(text):
    """A command or a script the model wants to run to check its change ('python -c ...', 'pytest ...', a reproduction
    starting with imports), seen in every local run of rich_3006 with the 26B."""
    t = (text or '').strip().strip('`').strip()
    first = t.split('\n', 1)[0].strip()
    if re.match(r'(?i)(python3?|pytest|pip|bash|sh|ls|cat)(\s|$)', first):
        return True
    # a script: it starts with imports (a definition sent as an edit starts with its def, class or decorator)
    return '\n' in t and re.match(r'(import \w|from [\w.]+ import )', first) is not None


def _search_query(items):
    """The text of a search the model wrote as one ('search', 'X') or ('search X'), else None."""
    if len(items) == 2 and _args.clean(items[0]).lower().strip(' :') in SEARCH_WORDS:
        return _search_text(items[1])
    if len(items) == 2 and re.fullmatch(r'[\w./-]+\.py', _args.clean(items[0]).strip('/')) and \
            not DEF_LINE.search(items[1]) and '\n' not in items[1].strip() and \
            not _args.clean(items[1]).isdigit():
        # ['rich/repr.py', 'param.empty']: the text in that file (local run of rich_3006: 6 such calls)
        return f'{_search_text(items[1])} in {_args.clean(items[0]).strip("/")}'
    if len(items) == 2 and re.fullmatch(r'[\w./-]+\.py', _args.clean(items[1]).strip('/')) and \
            not DEF_LINE.search(items[0]) and '\n' not in items[0].strip() and not _args.clean(items[0]).isdigit():
        # ['async', 'fastapi/dependencies/utils.py'] (m1): the text first, then the file
        return f'{_search_text(items[0])} in {_args.clean(items[1]).strip("/")}'
    if len(items) == 1:
        m = re.match(r'(?i)^\s*(?:search for|search|grep|find|look for)\s*:?\s+(.+)$', items[0].strip())
        if m and '\n' not in m.group(1):
            return _args.clean(m.group(1)).strip('`"\'')
    return None


def _search_pattern(text):
    """A whole word for an identifier, else the text literally with any run of spaces matching any spaces."""
    t = text.strip()
    if re.fullmatch(r'[A-Za-z_]\w*', t):
        return re.compile(rf'(?<![\w]){re.escape(t)}(?![\w])')
    return re.compile(r'\s+'.join(re.escape(w) for w in t.split()))


def _search(root, text, n=N_CANDIDATES, near=(), only=None):
    """(candidates, test lines, total matches): the innermost function or class (or a file's top-level code) of each
    line that holds text, package code first and most matches first, each with its matching lines; and the matching
    lines of the tests (read only: the hidden tests replace them). The files of near (the places and candidates the
    work is on) come first; only, a file, limits the search to it."""
    pat = _search_pattern(text)
    groups, tests, total = collections.OrderedDict(), [], 0
    # only the files that hold the text's longest word are read (_map: git grep; audit 10-26)
    holding = _map.having(root, max(text.split(), key=len), word=False) if text.split() else set()
    for rel in _repo.iter_py(root, tests=True, docs=True):
        if only and rel != only or rel not in holding:
            continue
        lines = _repo.read_lines(root, rel)
        hits = [i for i, l in enumerate(lines, 1) if pat.search(l)]
        if not hits:
            continue
        total += len(hits)
        if _repo.is_test_path(rel):
            tests += [f'{rel}:{i}: {lines[i - 1].strip()[:110]}' for i in hits]
            continue
        syms = _code.symbols(_code.parse(''.join(lines)))
        owner = _code.owner_map(syms, len(lines))
        for i in hits:
            o = owner[i] if i < len(owner) else None
            groups.setdefault((rel, o.name if o else MODULE), []).append(i)
    if not groups:
        return [], tests, total
    index = _rank.Index(root)
    order = sorted(groups, key=lambda k: (_repo.is_doc_path(k[0]), k[0] not in near, -len(groups[k]), k[0],
                                          groups[k][0]))
    out = []
    for key in order:
        doc = index.doc(*key)
        if doc is None or len(out) >= n:
            continue
        c = _candidate(index, doc, [])
        lines = _repo.read_lines(root, key[0])
        found = groups[key]
        c['evidence'] = [f'{i}: {lines[i - 1].strip()[:110]}' for i in found[:SEARCH_LINES]] + (
            [f'... and {len(found) - SEARCH_LINES} more lines'] if len(found) > SEARCH_LINES else [])
        out.append(c)
    for i, c in enumerate(out, 1):
        c['id'] = f'C{i}'
    _set_handles(out)
    return out, tests, total


def _search_text(raw):
    """The text to find: the item without the wrapping quotes and backticks, or the item as sent when that leaves
    fewer than 2 letters or digits (V15 rich_3469: '" "' became an empty search that matched 39,000 lines)."""
    t = _args.clean(raw or '').strip('`')
    return t if len(re.sub(r'\W', '', t)) >= 2 else (raw or '').strip()


def _said(text, what='the text sent'):
    """A short name is named back; longer text sent by the model is described, not quoted (a failed input quoted back
    is repeated: Feedback That Backfires, docs/feedback_audit.md R3)."""
    t = (text or '').strip()
    return t if len(t) <= 40 and '\n' not in t else what


def _search_view(root, state, text, prefer=None):
    """The answer to a search, or None when nothing holds text: the library code a name refers to (read only), the
    code that holds the text as candidates to choose from, and the tests' matching lines. The candidates replace the
    list; the step becomes the choose step."""
    if len(text.strip()) < 2 or not text.strip():
        return None
    only = None
    m = re.match(r'^(.+?)\s+in\s+`?([\w./-]+\.py)`?$', text.strip())     # "super().__init__ in fastapi/applications.py"
    if m:
        only = next((r for r in _repo.iter_py(root, tests=True, docs=True) if _rank.names_file(m.group(2), r)), None)
        if only:
            text = m.group(1).strip().strip('`')
    lib = _library_view(root, text, prefer=prefer) if re.fullmatch(r'[A-Za-z_][\w.]*', text.strip()) else ''
    near = {x['rel'] for x in (state.get('places') or []) + (state.get('candidates') or [])}
    cands, tests, total = _search(root, text, near=near, only=only)
    elsewhere = None
    if only and not cands:
        # c14: ['fastapi/dependencies/utils.py', 'isgeneratorfunction'] answered 'Nothing in the code holds it' while
        # models.py holds it; the coordinator searched 12 more times. A search in a file that does not hold the
        # text is answered with the code elsewhere that holds it.
        cands, tests, total = _search(root, text, near=near)
        elsewhere = only
    if not cands and not tests and not lib:
        return None
    _state.record({'look': 'search', 'q': text[:120], 'step': state['step'], 'found': len(cands)})
    said = _said(text)
    head = f'Where the code holds {said}' if total else f'Nothing in the code holds {said}'
    if elsewhere and total:
        head = f'Nothing in {elsewhere} holds {said}. Where the rest of the code holds it'
    out = []
    if lib:
        out.append(lib)
    if cands and ROLE == 'editor' and state['step'] != 'D1' and state.get('editor_place'):
        # c9: an editor's open.py ['top level'] was searched, and the search sent the work back to choosing: the plan
        # lost its three places. An editor's search is a read: where the text is, by name, and the plan stays.
        mine_name = state.get('editor_name') or _name_of(state, state['editor_place'])
        lines = []
        for c in cands[:N_CANDIDATES]:
            label = c.get('handle') or c['name']
            lines.append(f'  {label} ({c["rel"]})' + ''.join(f'\n      {e}' for e in (c.get('evidence') or [])[:2]))
        out.append(f'{head} ({total} lines; the functions and classes that hold them, read only):\n' + '\n'.join(lines))
        return '\n\n'.join(out) + (f"\n\nSend one name to read it. Back to {mine_name}: send its edit, or ['skip'] "
                                    'if it needs no change.')
    if cands:
        if state['step'] == 'D1':
            state['candidates'] = cands
        else:
            dropped = _dropped(state)
            _journal.requirement_back(state, cands)
            state['rechosen'] = state.get('rechosen', 0) + 1
            if dropped:
                out.append(dropped)
        shown = sum(1 for c in cands)
        out.append(_candidates_view(state, title=f'{head} ({total} lines; the functions and classes that hold '
                                    f'them{", first " + str(shown) if shown >= N_CANDIDATES else ""}):'))
    if tests:
        more = f' (first {N_CANDIDATES} of {len(tests)})' if len(tests) > N_CANDIDATES else ''
        out.append(f'In the tests{more} (read only: the hidden tests replace the test files):\n'
                   + '\n'.join('  ' + t for t in tests[:N_CANDIDATES]))
    if not cands:
        out.append('No function or class of the package holds it.')
        if state['step'] == 'D1':
            out.append(_candidates_view(state))
        else:
            return '\n\n'.join(out) + '\n\nNothing was changed.' + ('\n\n' + _window(root, state, state['current'])
                                                                    if state.get('current') is not None else '')
    return '\n\n'.join(out) + '\n\nChoose the code to change.'


def _dropped(state):
    """The sentence naming the places not edited yet, said before a search or a range replaces the plan (an implicit
    change of state is recovered far less often than one that is said: ToolMaze, docs/feedback_audit.md R11)."""
    left = [_name_of(state, e['place']) for e in state.get('plan') or [] if e['status'] == 'todo']
    return f'The places not edited yet ({", ".join(left[:4])}) are no longer planned.' if left else ''


def _owner_candidates(root, state, rel, a, b):
    """The functions and classes (or the top-level code) that hold lines a..b of rel, as candidates with the lines of
    each that bear on the statement."""
    st = _state.load('statement', {})
    text = st.get('text', '')
    lines = _repo.read_lines(root, rel)
    syms = _code.symbols(_code.parse(''.join(lines)))
    owner = _code.owner_map(syms, len(lines))
    keys = []
    for i in range(max(1, a), min(b, len(lines)) + 1):
        if not lines[i - 1].strip():
            continue                    # a blank line between methods belongs to the class: it says nothing
        o = owner[i] if i < len(owner) else None
        k = (rel, o.name if o else MODULE)
        if k not in keys:
            keys.append(k)
    index = _rank.Index(root)
    literals = [x for x in dict.fromkeys(re.findall(r'`([^`\n]{4,60})`', text)) if len(x) >= 4]
    toks = {}
    for term in _statement.terms(text):
        for ident in re.findall(r'[A-Za-z_]\w*', term):
            for t in _rank.tokens(ident):
                if len(t) >= 4 and t not in _statement.STOP:
                    toks[t] = 1
    out = [_candidate(index, index.doc(*k), [], root=root, literals=literals, toks=toks) for k in keys[:N_CANDIDATES]
           if index.doc(*k) is not None]
    for i, c in enumerate(out, 1):
        c['id'] = f'C{i}'
    _set_handles(out)
    return out


def _resolve(root, names):
    """Candidates for code the model named off the list: 'file::Name', 'Name' or 'pkg/file.py'."""
    table = _impact.Table(root, docs=True)
    out = []
    for raw in names:
        raw = _args.clean(raw)
        top = re.fullmatch(r'(?:[\w./-]+::)?([\w./-]+\.py) top level', raw.strip())
        if top:                             # a file's top-level code by its handle, also for a big file
            index = _rank.Index(root)
            doc = next((index.doc(r, None) for r in index.files if _rank.names_file(top.group(1), r)
                        and index.doc(r, None) is not None), None)
            if doc is not None:
                out.append({'rel': doc.rel, 'name': MODULE, 'start': doc.start, 'end': doc.end, 'line': ''})
            continue
        rel, _, name = raw.partition('::') if '::' in raw else ('', '', raw)
        rel = rel.strip()
        name = name.strip().strip('()')
        if not name and rel:
            name = MODULE
        if name.endswith('.py') and not rel:
            rel, name = name, MODULE
        if rel and name == MODULE:
            match = [r for r in table.syms if _rank.names_file(rel, r)]
            if match:
                n = len(_repo.read_lines(root, match[0]))
                if n <= BIG_FILE:               # a bigger file is chosen through its functions (d1 lists them)
                    out.append({'rel': match[0], 'name': MODULE, 'start': 1, 'end': n, 'line': ''})
            continue
        found = table.find(name) if name else []
        if not found and name and not rel:
            found = _dotted_or_case(table, name)
        if not found and name and '.' in name:
            # vf4 lab (fastapi_14419): 'Dependant.security_scopes' (a dataclass field) and 'Dependant.__init__' (made
            # by @dataclass) were answered as new code; they are the class's own code (checklist B11)
            found = _attribute_owner(table, name)
        if not found and name and re.fullmatch(r'[A-Za-z_]\w*', name):
            found = _import_alias(root, table, name)
        if rel:
            found = [(r, s) for r, s in found if _rank.names_file(rel, r)] or found
        found.sort(key=lambda x: (_repo.is_doc_path(x[0]), x[0]))
        for r, s in found[:1]:
            out.append({'rel': r, 'name': s.name, 'start': s.start, 'end': s.end, 'line': ''})
        if not found and name and re.fullmatch(r'[A-Za-z_]\w*', name):
            # hk30 (rich_3043): the template CONSOLE_HTML_FORMAT, a module-level constant, was "not found in the
            # repository"; a top-level assignment is a place of its own (the file named first, then any file)
            hit = assignment(root, table, name, rel)
            if hit:
                out.append(hit)
                continue
        if not found and len(raw) >= 4 and not rel and not re.fullmatch(r'[A-Za-z_][\w.]*', raw):
            # a code fragment (not a bare name the repository does not define, such as a library's): the function or
            # class that contains it
            hit = _find_text(root, table, raw)
            if hit:
                out.append(hit)
    return out


def assignment_span(text, name):
    """(first, last) line of the top-level assignment of name in a module's text ('NAME = ...', 'NAME: T = ...'), or
    None."""
    if not re.search(rf'(?m)^{re.escape(name)}\s*(?::[^=\n]*)?=(?!=)', text or ''):
        return None
    tree = _code.parse(text)
    for node in tree.body if tree is not None else ():
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else [])
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return node.lineno, node.end_lineno
    return None


def assignment(root, table, name, rel=''):
    """A place for the top-level assignment of name: in the file rel names when it has one, else package code first."""
    files = sorted(table.where(name), key=lambda r: (not (rel and _rank.names_file(rel, r)), _repo.is_doc_path(r), r))
    for r in files:
        span = assignment_span(_repo.read_text(root, r), name)
        if span:
            return {'rel': r, 'name': name, 'start': span[0], 'end': span[1], 'line': ''}
    return None


def _import_alias(root, table, name):
    """[(rel, Sym)] for a name the repository only imports under another name ('_SupportsRead' from
    `from ._types import SupportsRead as _SupportsRead`): the definition it stands for (requests_7502, local run p1:
    the specifier asked for _SupportsRead and was told no pair was read)."""
    out = []
    for rel in table.where(name):
        text = _repo.read_text(root, rel)
        if f'as {name}' not in text:
            continue
        tree = _code.parse(text)
        for node in ast.walk(tree) if tree is not None else ():
            if not isinstance(node, ast.ImportFrom):
                continue
            for al in node.names:
                if al.asname != name:
                    continue
                mod = (node.module or '').replace('.', '/')
                base = os.path.dirname(rel) if node.level else ''
                for _ in range(max(node.level - 1, 0)):
                    base = os.path.dirname(base)
                path = '/'.join(x for x in (base, mod) if x)
                hits = table.find(al.name)
                out += [h for h in hits if h[0] in (f'{path}.py', f'{path}/__init__.py')] or hits
    return list(dict.fromkeys(out))


def _dotted_or_case(table, name):
    """[(rel, Sym)] for 'fastapi.applications.FastAPI' (a module path, then a qualified name in it) or a qualified
    name spelled with other capitals ('fastapi.setup.openapi' for FastAPI.setup.openapi). V17: the 31B's editor sent
    both forms 9 times and was told 'Nothing in the repository defines' each time."""
    parts = name.split('.')
    for k in range(len(parts) - 1, 0, -1):
        mod, rest = '/'.join(parts[:k]), '.'.join(parts[k:])
        for rel in table.syms:
            if rel.endswith(f'{mod}.py') or rel.endswith(f'{mod}/__init__.py'):
                sym = table.get(rel, rest)
                if sym is not None:
                    return [(rel, sym)]
    low = name.lower()
    holding = _map.having(table.root, name.split('.')[-1], ignore_case=True) if _map.IDENT.match(
        name.split('.')[-1]) else set()
    hits = [(rel, s) for rel in table.files() if rel in holding for s in table.syms.get(rel, [])
            if s.name.lower() == low]
    return hits[:1]


def _find_text(root, table, text):
    """The innermost function or class whose lines contain text literally (package code before docs and scripts)."""
    needle = re.sub(r'\s+', ' ', text.strip())
    holding = _map.having(table.root, max(needle.split(' '), key=len), word=False) if needle else set()
    for rel in sorted((r for r in table.syms if r in holding), key=lambda r: (_repo.is_doc_path(r), r)):
        lines = _repo.read_lines(root, rel)
        for i, line in enumerate(lines, 1):
            if needle in re.sub(r'\s+', ' ', line):
                syms = table.syms[rel]
                owner = _code.owner_map(syms, len(lines))[i]
                if owner is None:
                    if len(lines) > BIG_FILE:
                        continue                # top-level code of a big file (an import line): not a place
                    return {'rel': rel, 'name': MODULE, 'start': 1, 'end': len(lines), 'line': ''}
                return {'rel': rel, 'name': owner.name, 'start': owner.start, 'end': owner.end, 'line': ''}
    return None


def d1(root, state, args):
    if _args.plan(args) == 'back':
        return _candidates_view(state) + '\n\nChoose the code to change.'
    if len(args) == 1 and (_is_done(args[0]) or _is_skip(args[0])):
        return ('In this step code is chosen; nothing is open to skip or finish. ' + _candidates_view(state)
                + '\n\nChoose the code to change.')
    items = [a for a in args if a and a.strip()]
    if len(items) == 1 and _is_tests(items[0]):
        _state.record({'look': 'tests', 'q': items[0][:120], 'step': state['step']})
        return (TESTS_NOTE + (TESTS_READ if ROLE == 'single' else '') + '\n\n' + _candidates_view(state)
                + '\n\nChoose the code to change.')
    edited = _edit_of_other(root, state, args)
    if edited is not None:
        return edited
    query = _search_query(items)
    if query:
        return _search_view(root, state, query) or (f'Nothing in the code holds {_said(query)}. Nothing was opened.\n\n'
                                                    + _candidates_view(state))
    big = []                            # files over BIG_FILE lines sent at the choose step
    for a in items:
        x = _args.clean(a).strip('/')
        if re.fullmatch(r'[\w./-]+\.py', x):
            r = next((r for r in _repo.iter_py(root, docs=True) if _rank.names_file(x, r)), None)
            if r and len(_repo.read_lines(root, r)) > BIG_FILE:
                big.append((a, r))
    # exact names first, then code the repository defines, then near spellings of a candidate: a close spelling must
    # not turn Panel._title (defined, off the list) into the listed Panel._subtitle
    picked, others = _pick(state['candidates'], [a for a in items if a not in {b for b, _ in big}], fuzzy=False)
    chosen = picked[:3]
    if others and len(chosen) < 3:
        # names choose code; a text (a code fragment) is found instead, so that it gives a list, not the first match
        named = [o for o in others if NAME_LIKE.fullmatch(_args.clean(o).strip().strip('`'))]
        unresolved = []
        for o in named:
            got = _resolve(root, [o])
            if got:
                chosen += got[:1]
            else:
                unresolved.append(o)
        near, _ = _pick(state['candidates'], unresolved)
        chosen = (chosen + near)[:3]
        others = [o for o in others if o not in named] + [o for o in unresolved if _match(state['candidates'], o)
                                                          is None]
    if not chosen and big:
        # a file sent at the choose step: its functions and classes become the candidates, best first, so the choice
        # stays a pick from a short list of names (W1) instead of opening a whole file (or its top lines) as a place
        rel = big[0][1]
        st = _state.load('statement', {})
        cands, _ = _rank_candidates(root, st.get('text', ''), st.get('terms', []), state['requirements'], only_rel=rel)
        if cands:
            state['candidates'] = cands
            return (_candidates_view(state, title=f'Candidates in {rel} (its functions and classes most related to '
                                     'the issue):') + '\n\nChoose the code to change.')
    if not chosen:
        # a name the repository does not define, or a text: where the code holds it, to choose from (the first of
        # several that something holds: the local run sent ['param.empty', 'Parameter.empty'])
        for o in others[:3]:
            if '\n' in o.strip() or len(o) > 200:
                continue
            raw = next((a for a in items if _args.clean(a) == o), o)
            found = _search_view(root, state, _search_text(raw))
            if found:
                return found
    if not chosen:
        msg = (f'Nothing in the repository holds {_said(", ".join(_search_text(o) for o in others[:3]), "the names or text sent")} (library and '
               'standard-library code is not searched). ' if others else 'No candidate was chosen. ')
        return msg + 'Nothing was opened.\n\n' + _candidates_view(state)
    chosen = _big_class_members(root, state, chosen)
    table = _impact.Table(root, docs=True)
    places = []
    syms_chosen = [(c['rel'], c['name']) for c in chosen if c['name'] != MODULE]
    for c in chosen:
        if c['name'] == MODULE:
            places.append({'rel': c['rel'], 'name': MODULE, 'start': c['start'], 'end': c['end'], 'reason': 'chosen'})
    for p in _impact.related(table, syms_chosen):
        places.append(dict(p._asdict()))
    # kept by kind when there are too many: the chosen code, the places planned with it, then the readers outside the
    # class, then the rest (s4, fastapi_14448: three chosen properties filled the 8 places with their callers and
    # copies, and _solve_generator, the reader whose branch order the fix must swap, was cut)
    rank = lambda p: (0 if p['reason'] == 'chosen' else
                      1 if p['reason'].startswith(('async/sync twin', 'same pattern', 'reads ', 'same definition')) else
                      2 if p['reason'].startswith('uses ') else 3)
    places = sorted(places, key=rank)[:MAX_PLACES]
    for i, p in enumerate(places, 1):
        p['id'] = f'P{i}'
    _set_handles(places)
    _journal.choose(state, places)
    state['statement_short'] = False            # code is chosen: more statement text is no longer asked for
    out = []
    focus = _focus(state)
    new = [(r, n) for r in state['requirements'] if r['type'] == 'new' for n in r['names']]
    for r, name in new[:1]:
        owner = next((c for c in chosen if 'where the new' in c.get('line', '')), None)
        if owner is None:
            continue
        keywords = re.findall(r'(\w+)\s*=', r['text'])
        sib = _impact.sibling(table, owner['rel'], owner['name'], name.split('.')[-1], keywords)
        if sib is not None:
            out.append(f'An existing method of {owner["name"]} to model the new `{name.split(".")[-1]}` on:\n'
                       + _code_view(root, owner['rel'], sib.start, sib.end, focus, limit=40))
    places_view = '\n'.join('  ' + _place_line(p) for p in places)
    # No separate plan step: the chosen places, with their copies in a file of the same name and their async/sync
    # twins (the same change is needed there), are the plan, and this answer is the first edit window (a free-text
    # plan was the step where the local model looped or ran away in 4 of 5 runs). Each place's code is shown once,
    # in its own window. Other related places are opened by their name.
    bases = {c['rel'].split('/')[-1] for c in chosen}
    planned = [p['id'] for p in places if p['reason'] == 'chosen'
               or p['reason'].startswith(('async/sync twin', 'same pattern', 'reads '))
               or (p['reason'].startswith('same definition') and p['rel'].split('/')[-1] in bases)]
    if not planned:
        _journal.back(state)
        return ('The chosen code was not found in the parsed files (nothing was opened). Choose other candidates.\n\n'
                + _candidates_view(state))
    _journal.set_plan(state, [(pid, '') for pid in planned])
    _tests.save_verified(root)
    first = _place(state, planned[0])['handle']
    if ROLE == 'coordinator':
        out.append(f'Places (the chosen code and the code related to it):\n{places_view}\nNext the tool specify_fix '
                   f'reads the code of all of them and writes what changes at each; then each place is edited by the '
                   f'tool edit_place, one at a time, starting with {first}.')
        return '\n\n'.join(out)
    out.append(f'Places (the chosen code and the code related to it):\n{places_view}\n'
               f'They are edited one at a time, starting with {first}. A related place outside the plan needs an '
               f'edit only when the change must be made there too: send its name to open it.')
    return '\n\n'.join(out + [_window(root, state, state['current'])])


BIG_CLASS_MEMBERS = 2   # members of a chosen class over CLASS_OUTLINE_AT lines that become its places


def _big_class_members(root, state, chosen):
    """A chosen class longer than CLASS_OUTLINE_AT lines is replaced by its members most related to the issue (at most
    BIG_CLASS_MEMBERS, those that match the statement's words). httpx_3672 chose HTTPParser (340 lines): the window
    showed an outline and the editor spent its calls reading ranges of it and ran out of time. A member fits in one
    window; a new member is added after it (_whole_edit), the class is still opened by its name. A class with no
    matching member stays whole."""
    out = []
    st = _state.load('statement', {})
    for c in chosen:
        if c['name'] == MODULE or c['end'] - c['start'] + 1 <= CLASS_OUTLINE_AT:
            out.append(c)
            continue
        sym = next((s for s in _code.symbols(_code.parse(_repo.read_text(root, c['rel']))) if s.name == c['name']),
                   None)
        if sym is None or sym.kind != 'class':
            out.append(c)
            continue
        members, _ = _rank_candidates(root, st.get('text', ''), st.get('terms', []), state['requirements'],
                                      n=BIG_CLASS_MEMBERS, only_rel=c['rel'], within=c['name'])
        members = [m for m in members if '[matches:' in m['line']]
        out += [m for m in members if (m['rel'], m['name']) not in {(x['rel'], x['name']) for x in out}] or [c]
    return out[:3 + BIG_CLASS_MEMBERS]


def d2(root, state, args):
    plan = _args.plan(args)
    if plan == 'back':
        _journal.back(state)
        return _candidates_view(state) + '\n\nChoose the code to change.'
    ids, others = _args.ids(args, 'C')
    if not plan and ids and not others:
        _journal.back(state)
        return d1(root, state, args)
    known = {p['id'] for p in state['places']}
    bad = [p for p, _ in plan if p not in known]
    if plan and all(_args.is_placeholder(i) for _, i in plan):
        return ('The plan still has the placeholder "<what changes there>": write what must change at each place. '
                'Nothing was planned.\nPlaces:\n' + '\n'.join('  ' + _place_line(p) for p in state['places']))
    if not plan:
        # a place id without the colon ("P2 rich/x.py :: f ..." copied from the list) plans that place
        for a in args:
            for line in (a or '').split('\n'):
                m = re.match(r'\W*(P\d+)\b\W*(.*)$', line.strip())
                if m:
                    rest = m.group(2).strip()
                    plan.append((m.group(1).upper(), '' if '::' in rest or not rest else _args.clean(rest)))
        bad = [p for p, _ in plan if p not in known]
    if not plan or bad:
        msg = (f'{", ".join(bad)} is not a listed place. ' if bad else
               'No plan was read: each item must start with a place id, like "P1: <what changes>". ')
        return msg + 'Nothing was planned.\nPlaces:\n' + '\n'.join('  ' + _place_line(p) for p in state['places'])
    seen, entries = set(), []
    for p, intent in plan:
        if p not in seen:
            seen.add(p)
            entries.append((p, intent))
    _journal.set_plan(state, entries)
    _tests.save_verified(root)
    return _window(root, state, state['current'])


def _is_skip(text):
    return _args.clean(text or '').lower().strip('[]"\' .') in ('skip', 'no change', 'none', 'no edit')


def _is_done(text):
    return _args.clean(text or '').lower().strip('[]"\' .!') in ('done', 'finish', 'finished', 'submit', 'all done')


def _listed_place(state, text):
    """The id of the listed place that text names (its name, or an old id like P2), or None. Only listed places are
    opened here: candidates and other code are chosen after ["back"]."""
    x = _match(state['places'], text, fuzzy=False)
    if x is None:
        # a near spelling only when the text names no code of its own: V15 fastapi_14583 sent an edit of
        # 'fastapi/_compat/v1.py top level' while 'fastapi/_compat/may_v1.py top level' was the place, and the near
        # match applied the edit to the wrong file
        t = _norm_name(text)
        if t and '/' not in t and '.py' not in t and not _resolve(_repo.repo_root(), [t]):
            x = _match(state['places'], text)
    return x['id'] if x else None


def _place_args(state, args):
    """args with a first item that names a listed place turned into its internal id, so that the edit readers get
    ["P2", ...]; a single whole definition gets the place of the same name (or the open one) in front of it."""
    items = [x for x in args if x is not None]
    if not items:
        return items
    if len(items) == 1 and '\n' in items[0].strip() and DEF_LINE.search(items[0]):
        tree = _code.parse(textwrap.dedent(_args.code(items[0])))
        names = [n.name for n in (tree.body if tree else []) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                            ast.ClassDef))]
        plan = [_place(state, e['place']) for e in state['plan']]
        hit = next((q for q in plan for n in names[:1] if q['name'].split('.')[-1] == n), None)
        cur = state['plan'][state['current']]['place'] if state.get('current') is not None else None
        pid = hit['id'] if hit else cur
        return [pid, items[0]] if pid else items
    if len(items) >= 2 and not re.fullmatch(r'(?i)\W*P\d+\W*', items[0]):
        pid = _listed_place(state, items[0])
        if pid:
            return [pid] + items[1:]
    return items




def _open_named(root, state, text):
    """In the edit step, code named off the list of places. A function or class of the repository is chosen: it and the
    code related to it replace the places not edited yet (the edits made stay), so naming code is a decision, as at the
    choose step (B12: a name means the same in every step). A big file gives its functions and classes to choose from;
    any other name or text gives the code that holds it to choose from. None when nothing matches."""
    x = _args.clean(text).strip('/')
    cur = state.get('current')
    here = _place(state, state['plan'][cur]['place'])['rel'] if cur is not None and state.get('plan') else None
    if re.fullmatch(r'[\w./-]+\.py', x):
        rel = next((r for r in _repo.iter_py(root, docs=True) if _rank.names_file(x, r)), None)
        if rel and len(_repo.read_lines(root, rel)) > BIG_FILE:
            st = _state.load('statement', {})
            cands, _ = _rank_candidates(root, st.get('text', ''), st.get('terms', []), state['requirements'],
                                        only_rel=rel)
            if cands:
                _state.record({'look': 'file', 'q': rel, 'step': state['step']})
                _journal.requirement_back(state, cands)
                state['rechosen'] = state.get('rechosen', 0) + 1
                return ('The edits made so far stay. ' + _candidates_view(
                    state, title=f'Candidates in {rel} (its functions and classes most related to the issue):')
                    + '\n\nChoose the code to change.')
            return None
    named = re.fullmatch(r'([\w./-]+::)?[A-Za-z_][\w.]*(\(\))?', x) or re.fullmatch(r'(?:[\w./-]+::)?[\w./-]+\.py top level', x)
    found = _resolve(root, [x]) if named else []
    if found and _repo.is_doc_path(found[0]['rel']) and '::' not in x:
        found = []              # V15 fastapi_14851: 'lifespan' chose an example in docs_src; a bare name is found
    if not found and '.' in x:
        inherited = _inherited_library(root, x)
        if inherited:
            _state.record({'look': 'library', 'q': x[:120], 'step': state['step']})
            return inherited + '\n\n' + _current_view(root, state)
    if not found:
        return _search_view(root, state, _search_text(text), prefer=here)
    _state.record({'look': 'name', 'q': x[:120], 'step': state['step']})
    c = found[0]
    if any(_place(state, e['place'])['rel'] == c['rel'] for e in state.get('plan') or []):
        # code of a file the plan works in (its top level, a sibling method) joins the plan, which stays: the system
        # instruction says to add an import this way, and replacing the plan made the model ping-pong between members
        # of one class, each choice dropping the other (local run of fastapi_14448: Dependant._unwrapped_call and
        # Dependant.is_coroutine_callable chosen in turn). Code elsewhere still replaces the places not edited yet.
        old = next((q for q in state['places'] if (q['rel'], q['name']) == (c['rel'], c['name'])), None)
        if old is None:
            old = {'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end'], 'reason': 'chosen',
                   'id': f'P{len(state["places"]) + 1}'}
            state['places'].append(old)
            _set_handles(state['places'])
        i = _journal.target(state, old['id'])
        if state['plan'][i]['status'] == 'skipped':
            state['plan'][i]['status'] = 'todo'
        state['current'], state['step'] = i, 'D3'
        return f'{old["handle"]} joins the places and is open now; the other places stay.\n\n' + _window(root, state, i)
    head, out = _choose_code(root, state, c)
    return head + '\n\n' + out


def _inherited_library(root, dotted):
    """'Class.method' where the repository's Class gets the method from a library base class (V15 fastapi_14851:
    APIRouter.add_event_handler is Starlette's Router.add_event_handler): that library code, read only, else ''."""
    cls_name, _, attr = dotted.rpartition('.')
    table = _impact.Table(root, docs=False)
    hits = [(r, x) for r, x in table.find(cls_name.split('.')[-1]) if x.kind == 'class']
    for rel, sym in hits[:2]:
        tree = _code.parse(''.join(_repo.read_lines(root, rel)))
        names = {}
        for n in ast.walk(tree) if tree is not None else ():
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                for al in n.names:
                    names[al.asname or al.name] = f'{n.module}.{al.name}'
            elif isinstance(n, ast.Import):
                for al in n.names:
                    names[al.asname or al.name.split('.')[0]] = al.name if al.asname else al.name.split('.')[0]
        for base in getattr(sym.node, 'bases', []):
            try:
                parts = ast.unparse(base).split('.')
            except Exception:       # noqa: BLE001
                continue
            if parts[0] not in names:
                continue
            try:
                hit = _library_def(root, '.'.join([names[parts[0]]] + parts[1:] + [attr]))
            except Exception:       # noqa: BLE001
                hit = None
            if hit:
                path, a, b, qual = hit
                short = path.split('site-packages/')[-1] if 'site-packages/' in path else os.path.basename(path)
                return (f'{dotted} is not defined in the repository: {sym.name} inherits it from library code, read '
                        f'only: {qual} ({short}, lines {a}-{b}).\n' + _code_view(root, path, a, b, limit=LIBRARY_LINES))
    return ''


def _choose_code(root, state, found):
    """Chooses code off the places (a dict with rel, name, start, end): it and the code related to it become the
    places, the edits made stay, the places not edited yet are no longer planned. (head, answer of the choice)."""
    c = dict(found)
    # the chosen code first, then the candidates of before, so that ['back'] still finds them
    rest = [dict(q) for q in state.get('candidates') or [] if (q['rel'], q['name']) != (c['rel'], c['name'])]
    cands = [c] + rest[:N_CANDIDATES - 1]
    for i, q in enumerate(cands, 1):
        q['id'] = f'C{i}'
    _set_handles(cands)
    left = [_name_of(state, e['place']) for e in state.get('plan') or [] if e['status'] == 'todo']
    _journal.requirement_back(state, cands)
    state['rechosen'] = state.get('rechosen', 0) + 1
    out = d1(root, state, [cands[0]['handle']])
    dropped = f' The places not edited yet ({", ".join(left[:4])}) are no longer planned.' if left else ''
    return (f'{cands[0]["handle"]} is chosen: it and the code related to it are the places now. The edits made so far '
            f'stay.{dropped}', out)


def _edit_of_other(root, state, args):
    """An edit that names code which is not a place (a candidate, or any function or class of the repository): an edit
    is the clearest decision there is, so that code is chosen and the edit is applied to it. None for anything else
    (a file path with line numbers is a range, not an edit)."""
    items = [x for x in args if x is not None and str(x).strip()]
    if len(items) == 2:
        if not DEF_LINE.search(items[1]):
            return None
    elif len(items) != 4 or not all(_args.clean(str(n)).isdigit() for n in items[1:3]):
        return None
    name = _norm_name(items[0])
    if not name or re.fullmatch(r'(?i)P\d+|[\w./-]+\.py', name) or not NAME_LIKE.fullmatch(name):
        return None
    if _match(state.get('places') or [], name, fuzzy=False) is not None:
        return None                         # a place: the edit step reads it
    cand = _match(state.get('candidates') or [], name, fuzzy=False)
    found = [cand] if cand is not None else _resolve(root, [name])
    if not found:
        return None
    _state.record({'look': 'edit of other code', 'q': name[:120], 'step': state['step']})
    if _OTHER_DEPTH:
        return None             # (V15 replay: a recursion with d3, see below)
    if ROLE == 'editor' and state['step'] == 'D3' and state.get('editor_place'):
        # c2 (fastapi_14448): the editor of is_coroutine_callable edited Dependant._unwrapped_call, the helper its place
        # reads; choosing it replaced the plan (the planned siblings were dropped) and left the editor on a place id
        # that no longer existed (TypeError). An editor's edit of related code adds that code as a place and keeps
        # the plan; _after_edit then brings the editor back to its own place.
        _add_place(state, found[0], f'edited by the editor of {_name_of(state, state["editor_place"])}')
        return _edit_chosen(root, state, items, found[0])
    head, _ = _choose_code(root, state, found[0])
    if state['step'] != 'D3':
        return None
    return head + '\n\n' + _edit_chosen(root, state, items, found[0])


_OTHER_DEPTH = []


def _edit_chosen(root, state, items, c):
    """The edit items applied to the code c just chosen. A large class is chosen as its members (_big_class_members),
    so the class name is not a place: the edit goes to the member that holds its lines, or whose definition it sends
    (V15 replay of fastapi_14448: ['Dependant', '101', '108', ...] chose the class, the name was still no place, and d3
    called _edit_of_other again without end until the command timed out)."""
    args = list(items)
    if _match(state['places'], _norm_name(items[0]), fuzzy=False) is None:
        mine = [q for q in state['places'] if q['rel'] == c['rel'] and q['name'] not in (MODULE, '<exports>')
                and c['start'] <= q['start'] and q['end'] <= c['end']]
        if len(items) == 4:
            a, b = int(_args.clean(items[1])), int(_args.clean(items[2]))
            q = next((q for q in mine if q['start'] <= a and b <= q['end']), None)
        else:
            q = next((q for q in mine if _whole_target_is_place(items[1], q)), None)
        if q is None:
            return (f'{_norm_name(items[0])} is chosen as the places listed below; the edit was not applied because no '
                    'one of them holds it. Send it again with the name of the place it changes.\n\n'
                    + (_window(root, state, state['current']) if state.get('current') is not None
                       else _current_view(root, state)))
        args[0] = q['handle']
    _OTHER_DEPTH.append(1)
    try:
        return d3(root, state, args)
    finally:
        _OTHER_DEPTH.pop()


def _add_place(state, c, reason):
    """Code (a dict with rel, name, start, end) added to the places and to the plan, the plan kept as it is."""
    if any((q['rel'], q['name']) == (c['rel'], c['name']) for q in state['places']):
        return
    n = max([int(q['id'][1:]) for q in state['places'] if re.fullmatch(r'P\d+', q['id'])] or [0]) + 1
    state['places'].append({'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end'],
                            'reason': reason, 'id': f'P{n}'})
    _set_handles(state['places'])
    _journal.target(state, f'P{n}')


def _whole_target_is_place(text, p):
    """True when the new code's first definition has the place's own (last) name: it replaces the place."""
    m = re.search(r'(?m)^\s*(?:async\s+)?(?:def|class)\s+(\w+)', text)
    return bool(m) and m.group(1) == p['name'].rsplit('.', 1)[-1]


def _whole_args(items):
    """(place id, code) for ["P1", "<whole function or class>"], else None."""
    if len(items) != 2 or not re.fullmatch(r'(?i)\W*P\d+\W*', items[0]) or not DEF_LINE.search(items[1]):
        return None
    return _args.clean(items[0]).upper(), items[1]


def _target(syms, p, name):
    """The definition that a new definition called name replaces: the place itself, a member of it, a method of the
    place's class, or the only definition of that name in the file. None for a new name."""
    pname = p['name'] if p['name'] not in (MODULE, '<exports>') else ''
    cls = pname.rsplit('.', 1)[0] if '.' in pname else ''
    for q in (pname if pname.split('.')[-1] == name else '', f'{pname}.{name}' if pname else '',
              f'{cls}.{name}' if cls else '', name):
        hits = [x for x in syms if q and x.name == q]
        if hits:
            # h14 (requests_7505): with @overload stubs the name has several definitions; the last one is the
            # implementation, the one a new definition without @overload replaces
            return hits[-1]
    same = [x for x in syms if x.name.split('.')[-1] == name]
    return same[0] if len(same) == 1 else None


def _indent_of(line):
    return line[:len(line) - len(line.lstrip(' \t'))]


def _whole_edit(root, p, text):
    """A whole function or class sent without line numbers (["P1", "<code>"]): each definition in the code replaces
    the definition of the same name (see _target), re-indented to it; a name that does not exist yet is added after
    the place (inside it when the place is a class). Line numbers are the hardest part of an edit for a small model
    (packed, swapped or stale numbers in the local runs); a definition is found by its name. Returns an _edit.Result;
    nothing is changed unless every definition applies."""
    rel = p['rel']
    code = _args.code(text)
    src, tree = code, None
    # c2: '    @cached_property\ndef f(self):\n    ...' (the first line indented as in the file, the rest not)
    # c10: '@cached_property\n    def f(self):\n        ...' (the first line not indented, the rest as in the file)
    head, _, rest = code.partition('\n')
    second = rest.split('\n', 1)[0]
    first_as_second = _indent_of(second) + head.lstrip(' \t') + '\n' + rest if rest else code
    for cand in (code, code.replace('\\n', '\n').replace('\\"', '"'), code.lstrip(' \t'), first_as_second):
        src = textwrap.dedent(cand)
        tree = _code.parse(src)
        if tree is not None:
            break
    if tree is None:
        err = _edit._compiles([src])
        return _edit.Result(False, p['start'], p['end'], f'the new code does not compile ({err}); nothing was changed',
                            [], [])
    defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    other = [n for n in tree.body if n not in defs]
    if not defs or other:
        return _edit.Result(False, p['start'], p['end'],
                            'the new code must be whole functions or classes only (a def or class line and its body); '
                            f"to change other lines send their numbers: ['{p['handle']}', '<first line number>', "
                            "'<last line number>', '<new lines>']; nothing was changed", [], [])
    src_lines = src.split('\n')
    try:
        before = _repo.read_lines(root, rel)
    except OSError:
        return _edit.Result(False, p['start'], p['end'], f'{rel} does not exist', [], [])
    repairs, warnings, names, unchanged = [], [], [], 0
    for d in defs:
        first = min([d.lineno] + [x.lineno for x in d.decorator_list])
        piece = '\n'.join(src_lines[first - 1:d.end_lineno])
        lines = _repo.read_lines(root, rel)
        syms = _code.symbols(_code.parse(''.join(lines)))
        t = _target(syms, p, d.name)
        if t is None and len(defs) == 1 and not isinstance(d, ast.ClassDef):
            own = next((x for x in syms if x.name == p['name'] and x.kind == 'def'), None)
            if own is not None:
                old_body = ''.join(lines[own.def_line:own.end])
                new_body = '\n'.join(src_lines[d.lineno:d.end_lineno])
                if difflib.SequenceMatcher(None, old_body.split(), new_body.split(), autojunk=False).ratio() >= 0.6:
                    # a rename (V18, httpx_3672: complete sent back as reset): the place is replaced, not kept beside
                    # the new copy; the answer lists the code that still uses the old name
                    t = own
                    repairs.append(f'{p["name"].split(".")[-1]} renamed to {d.name}')
        if t is not None and not re.search(r'@(typing\.)?overload\b', piece):
            # h14 (Haiku, requests_7505): the place _encode_params spans its four @overload stubs and the
            # implementation; the implementation alone was sent and the stubs were deleted. Without @overload in the
            # new code, only the implementation (the last def of the name, with its own decorators) is replaced
            old = lines[t.start - 1:t.end]
            if any(re.match(r'\s*@(typing\.)?overload\b', l) for l in old):
                defs_at = [i for i, l in enumerate(old) if re.match(rf'\s*(async\s+)?def\s+{re.escape(d.name)}\b', l)]
                if len(defs_at) > 1:
                    k = defs_at[-1]
                    while k > 0 and old[k - 1].lstrip().startswith('@') and \
                            not re.match(r'\s*@(typing\.)?overload\b', old[k - 1]):
                        k -= 1
                    t = types.SimpleNamespace(start=t.start + k, end=t.end)
        if t is not None:
            pad = _indent_of(lines[t.start - 1])
            r = _edit.apply(root, rel, t.start, t.end, textwrap.indent(piece, pad))
            if not r.applied and r.error.startswith('the new lines are the same'):
                unchanged += 1
                names.append(d.name)
                continue
        else:
            own = next((x for x in syms if x.name == p['name']), None)
            if own is not None:
                anchor = own.end
                # h13 (Haiku, requests_7505): the class SupportsRead and, after it at column 0, a module-level
                # def is_supports_read were sent together; the def went into the class and the module's import of it
                # failed. A def sent beside its class is a sibling; only a def sent alone with a class is a member
                sent_with_class = any(isinstance(x, ast.ClassDef) and x.name == p['name'].split('.')[-1] for x in defs)
                inner = own.kind == 'class' and not isinstance(d, ast.ClassDef) and not sent_with_class
                pad = _indent_of(lines[own.def_line - 1]) + ('    ' if inner else '')
            else:
                anchor, pad = len(lines), ''
            r = _edit.apply(root, rel, anchor, anchor,
                            lines[anchor - 1].rstrip('\r\n') + '\n\n' + textwrap.indent(piece, pad))
            if r.applied:
                repairs.append(f'added the new {d.name} after line {anchor}')
        if not r.applied:
            _repo.write_lines(root, rel, before)
            return _edit.Result(False, p['start'], p['end'], f'{d.name}: {r.error}', repairs + r.repairs, [])
        names.append(d.name)
        repairs += [x for x in r.repairs if x not in repairs]
        warnings += [x for x in r.warnings if x not in warnings]
    if unchanged == len(defs):
        return _edit.Result(False, p['start'], p['end'], "the new code is the same as the old one: nothing changes (if this place needs no change, send ['skip'])",
                            [], [])
    syms = _code.symbols(_code.parse(''.join(_repo.read_lines(root, rel))))
    spans = [t for t in (_target(syms, p, n) for n in names) if t is not None]
    start = min([t.start for t in spans] or [p['start']])
    end = max([t.end for t in spans] or [p['end']])
    return _edit.Result(True, start, end, None, repairs, warnings)


# ---------------------------------------------------------------------------------------------------------- line ranges

# The model writes line ranges to see code ("Let's look at lines 960-999"; V13b sent them as DELETEs, V14 as reads:
# 51 reads, 16 in one task). A range is answered by the code that holds it: inside the open place (or a listed one) it
# opens that place with those lines in view; elsewhere it gives the functions and classes there to choose from. No
# answer shows lines that no place holds, so a range is never a way to browse (B9) and never needs an edit (B16).

DELETE_CONFIRM = 30      # a DELETE of more lines is shown first and applied when the same call comes again
LIBRARY_LINES = 60


def _read_target(root, state, text):
    """(rel, label) of the file a range names: a file path, a listed place or candidate, or code of the repository."""
    x = _args.clean(text or '').strip('/')
    x = re.sub(r'\s+top level$', '', x)        # 'file.py top level' with two numbers is a range of that file (b11)
    if re.fullmatch(r'[\w./-]+\.py', x):
        rel = next((r for r in _repo.iter_py(root, tests=True, docs=True) if _rank.names_file(x, r)), None)
        return (rel, rel) if rel else (None, None)
    for items in (state.get('places') or [], state.get('candidates') or []):
        hit = _match(items, text)
        if hit is not None:
            return hit['rel'], hit['handle']
    found = _resolve(root, [x]) if re.fullmatch(r'([\w./-]+::)?[A-Za-z_][\w.]*', x) else []
    return (found[0]['rel'], found[0]['name']) if found else (None, None)


def _lines_view(root, rel, a, b, label):
    """Lines a..b of rel, numbered (the lines of a large deletion, shown before it is made)."""
    lines = _repo.read_lines(root, rel)
    a, b = max(1, min(a, b)), min(len(lines), max(a, b))
    body = _code.numbered(lines, a, b, collapse=_code.long_strings(_code.parse(''.join(lines))))
    return f'Lines {a}-{b} of {label}:\n{body}'


TEST_DEFS = 2       # test functions shown for one range of a test file


def _test_view(root, state, rel, a, b):
    """A range of a test file: the test functions that hold it, whole and read only, then the current step again.
    Tests are not places (the hidden tests replace them), but how the code is tested today is evidence for the change
    (V14 rich_3521 read tests/test_segment.py in 8 slices looking for test_split_cells and its cases)."""
    lines = _repo.read_lines(root, rel)
    syms = _code.symbols(_code.parse(''.join(lines)))
    owner = _code.owner_map(syms, len(lines))
    tops = []
    for i in range(max(1, a), min(b, len(lines)) + 1):
        o = owner[i] if i < len(owner) else None
        while o is not None and '.' in o.name and o.kind == 'def':      # a nested helper: its test function
            o = next((x for x in syms if x.name == o.name.rsplit('.', 1)[0]), None)
        if o is not None and o not in tops and lines[i - 1].strip():
            tops.append(o)
    _state.record({'look': 'test', 'q': f'{rel} {a}-{b}', 'step': state['step']})
    shown = [f'{t.name} ({rel}, lines {t.start}-{t.end}):\n' + _code_view(root, rel, t.start, t.end, limit=60)
             for t in tops[:TEST_DEFS]]
    if not tops:                # the test file's top level: its cases and fixtures data
        shown = [_code_view(root, rel, a, min(b, len(lines)), limit=60)]
    more = f' ({len(tops)} tests hold these lines; the first {TEST_DEFS})' if len(tops) > TEST_DEFS else ''
    return (f'Lines {a}-{b} of {rel} are in tests{more}, read only (the hidden tests replace the test files):\n'
            + '\n\n'.join(shown) + '\n\n' + _current_view(root, state))


PLACE_LINES = 60    # lines of the open place shown for one range


def _place_lines(root, p, a, b):
    """Lines a..b of the place p, which is open: only those lines (at most PLACE_LINES, inside the place), not the
    window again. Local run of requests_7328 with the decision window: the model read Session.send in 6 ranges and each
    answer repeated the whole window (about 1,400 tokens), so the history reached the compaction again."""
    a, b = max(a, p['start']), min(b, p['end'])
    if b - a + 1 > PLACE_LINES:
        b = a + PLACE_LINES - 1
    lines = _repo.read_lines(root, p['rel'])
    body = _code.numbered(lines, a, b, collapse=_code.long_strings(_code.parse(''.join(lines))))
    more = f' (lines {b + 1}-{p["end"]} follow)' if b < p['end'] else ''
    return _seen_mark(p['rel'], f'Lines {a}-{b} of {p["handle"]}, the open place{more}:\n{body}')


def _range_request(root, state, args):
    """The answer to ['<file or name>', '<first line>', '<last line>'], or None when args are not one. At the choose
    4-item call is one only when it names a file that is not a place (the model's ['fastapi/routing.py', 1, 50,
    'DELETE'] in V13b): code is edited through its name."""
    items = [x for x in args if x is not None and str(x).strip()]
    if len(items) not in (3, 4) or not all(_args.clean(str(n)).isdigit() for n in items[1:3]):
        return None
    if len(items) == 4:
        is_file = re.fullmatch(r'[\w./-]+\.py', _args.clean(items[0]).strip('/') or '')
        rel, _ = _read_target(root, state, items[0])
        if not (is_file and rel and _match(state.get('places') or [], items[0]) is None):
            return None             # an edit of a place: d3 reads it
    rel, _ = _read_target(root, state, items[0])
    if not rel:
        return None
    a, b = sorted((int(_args.clean(items[1])), int(_args.clean(items[2]))))
    n = len(_repo.read_lines(root, rel))
    if a > n:
        return f'{rel} has {n} lines; lines {a}-{b} do not exist. Nothing was changed.'
    b = min(b, n)
    if state['step'] in ('D3', 'D4') and state.get('plan'):
        _refresh(root, state, rel)
        cur = state.get('current')
        order = ([cur] if cur is not None else []) + [i for i in range(len(state['plan'])) if i != cur]
        for i in order:
            p = _place(state, state['plan'][i]['place'])
            if p['rel'] == rel and p['name'] != '<exports>' and p['start'] <= a and b <= p['end']:
                _state.record({'look': 'range in a place', 'q': f'{rel} {a}-{b}', 'step': state['step']})
                state['step'], state['current'] = 'D3', i
                if state['plan'][i]['status'] == 'skipped':
                    state['plan'][i]['status'] = 'todo'
                return _place_lines(root, p, a, b)
        for p in state['places']:
            if p['rel'] == rel and p['name'] != '<exports>' and p['start'] <= a and b <= p['end']:
                i = _journal.target(state, p['id'])
                state['step'], state['current'] = 'D3', i
                state['plan'][i]['status'] = 'todo' if state['plan'][i]['status'] == 'skipped' else \
                    state['plan'][i]['status']
                _state.record({'look': 'range in a place', 'q': f'{rel} {a}-{b}', 'step': state['step']})
                return _place_lines(root, p, a, b)
    if _repo.is_test_path(rel):
        return _test_view(root, state, rel, a, b)
    cands = _owner_candidates(root, state, rel, a, b)
    if not cands:
        return None
    _state.record({'look': 'range', 'q': f'{rel} {a}-{b}', 'step': state['step']})
    if state['step'] == 'D1':
        state['candidates'] = cands
        stay = ''
    else:
        dropped = _dropped(state)
        _journal.requirement_back(state, cands)
        state['rechosen'] = state.get('rechosen', 0) + 1
        stay = 'The edits made so far stay. ' + (dropped + ' ' if dropped else '')
    return (f'Code is shown through the function or class that holds it. {stay}'
            + _candidates_view(state, title=f'The code at lines {a}-{b} of {rel}:') + '\n\nChoose the code to change.')


def _import_bindings(root, first, prefer=None):
    """The dotted import paths the repository binds the name first to (from M import X as first; import M as first),
    those of the file prefer first, then package code, tests last."""
    holding = _map.having(root, first, word=False)
    rels = sorted((r for r in _repo.iter_py(root, tests=True, docs=False) if r in holding),
                  key=lambda r: (r != prefer, _repo.is_test_path(r), r))
    out = []
    for rel in rels:
        text = _repo.read_text(root, rel)
        if first not in text:
            continue
        tree = _code.parse(text)
        for node in ast.walk(tree) if tree is not None else ():
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                out += [f'{node.module}.{al.name}' for al in node.names if (al.asname or al.name) == first]
            elif isinstance(node, ast.Import):
                out += [al.name if al.asname == first else first for al in node.names
                        if al.asname == first or (not al.asname and al.name.split('.')[0] == first)]
    return list(dict.fromkeys(out))


def _module_source(module):
    """(source file, whether it is a package) of a module, found without importing anything (audit 10-18 #16:
    find_spec of a submodule imports its parent packages, in the script's own process and without a time limit): the
    top-level module through the import system's finders, its submodules in the package's directories."""
    import importlib.util
    loaded = sys.modules.get(module)
    if loaded is not None:
        spec = getattr(loaded, '__spec__', None)
        origin = getattr(spec, 'origin', None) or getattr(loaded, '__file__', None)
        return (origin, bool(getattr(spec, 'submodule_search_locations', None))) if origin else None
    head, *rest = module.split('.')
    try:
        spec = importlib.util.find_spec(head)
    except (ImportError, ValueError, AttributeError):
        return None
    if spec is None:
        return None
    origin, dirs = spec.origin, list(spec.submodule_search_locations or [])
    for part in rest:
        if not dirs:
            return None
        found = None
        for d in dirs:
            if os.path.isfile(os.path.join(d, part, '__init__.py')):
                found = (os.path.join(d, part, '__init__.py'), [os.path.join(d, part)])
            elif os.path.isfile(os.path.join(d, part + '.py')):
                found = (os.path.join(d, part + '.py'), [])
            elif os.path.isdir(os.path.join(d, part)):
                found = (None, [os.path.join(d, part)])        # a namespace package
            if found is not None:
                break
        if found is None:
            return None
        origin, dirs = found
    return (origin, bool(dirs)) if origin else None


def _library_def(root, dotted, depth=0):
    """(file, start, end, qualified name) of a library definition from its dotted path, following re-exports; None
    when it is not found or lives in the repository. A name of the repository's own packages is never library code,
    even when a copy of the package is installed (audit 10-18 #8: the installed copy was shown as 'library, read
    only')."""
    parts = dotted.split('.')
    if parts[0] in _repo.own_names(root):
        return None
    for k in range(len(parts) - 1, 0, -1):
        module, attrs = '.'.join(parts[:k]), parts[k:]
        try:
            found = _module_source(module)
        except (OSError, ValueError):
            found = None
        if found is None or not found[0] or not found[0].endswith('.py'):
            continue
        path = os.path.realpath(found[0])
        if path.startswith(os.path.realpath(root) + os.sep):
            return None                 # the repository's own code: it is reached by name, not as a library
        tree = _code.parse(_repo.read_text('/', path))
        if tree is None:
            return None
        body, node = tree.body, None
        for j, name in enumerate(attrs):
            defs = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and n.name == name]
            node = defs[-1] if defs else None       # the implementation comes after its @overload stubs
            if node is None:
                if j == 0 and depth < 3:          # re-exported: from ._utils import name
                    for n in body:
                        if isinstance(n, ast.ImportFrom):
                            for al in n.names:
                                if (al.asname or al.name) == name:
                                    base = module if found[1] else module.rsplit('.', 1)[0]
                                    src = n.module or ''
                                    if n.level:
                                        up = base.split('.')
                                        up = up[:len(up) - (n.level - 1)] if n.level > 1 else up
                                        src = '.'.join(up + ([src] if src else []))
                                    return _library_def(root, '.'.join([src, al.name] + attrs[1:]), depth + 1)
                return None
            body = node.body
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        return path, start, node.end_lineno, '.'.join([module] + attrs)
    return None


def _library_view(root, name, prefer=None):
    """The library code a name refers to through the repository's imports (Router for `routing.Router`,
    is_async_callable from starlette), read only; '' when there is none. V10 audit #9 and V13b fastapi_14448: the
    model needed Starlette's code and could not reach it."""
    name = _args.clean(name).strip('`').rstrip('()')
    if not re.fullmatch(r'[A-Za-z_][\w.]*', name):
        return ''
    parts = name.split('.')
    hit = next((h for h in (_library_def(root, '.'.join([b] + parts[1:]))
                            for b in _import_bindings(root, parts[0], prefer)[:6]) if h), None)
    if hit is None:
        return ''
    path, a, b, qual = hit
    shown = _code_view(root, path, a, b, limit=LIBRARY_LINES)
    short = path.split('site-packages/')[-1] if 'site-packages/' in path else os.path.basename(path)
    return (f'{name} is library code, read only (not in the repository, so it cannot be edited here): {qual} '
            f'({short}, lines {a}-{b}).\n{shown}')


NEW_FILE = re.compile(r'^\s*([\w./-]+\.py)(?:::([\w.]+))?\s*$')


def _new_file_path(root, text):
    """The repository path of a .py file that does not exist yet ('pkg/new.py' or 'pkg/new.py::Name'), else None.
    Its directory must exist or be a package path under the repository (review: 11 of 129 dev tasks add a file and
    no form could create one)."""
    m = NEW_FILE.match(_args.clean(text or '').strip('/'))
    if not m or '..' in m.group(1) or m.group(1).startswith('/'):
        return None
    rel = m.group(1)
    if os.path.exists(os.path.join(root, rel)) or rel.split('/')[-1].startswith('test'):
        return None
    top = rel.split('/')[0]
    return rel if '/' in rel and os.path.isdir(os.path.join(root, top)) else None


def _add_new_file_place(state, rel, reason):
    """A file to create, as a place of the plan (its whole content is its code)."""
    if not any(q['rel'] == rel and q['name'] == MODULE for q in state['places']):
        _add_place(state, {'rel': rel, 'name': MODULE, 'start': 1, 'end': 1}, reason)
        state['places'][-1]['new'] = True
        # it is created before the places still to do: an import of it at one of them fails its tests until it exists
        plan = state['plan']
        # (an editor's own open place stays first; the specifier and a single agent open the new file first)
        k = next((j for j, e in enumerate(plan[:-1])
                  if e['status'] == 'todo' and (ROLE != 'editor' or j != state.get('current'))), None)
        if k is not None:
            plan.insert(k, plan.pop())
            if ROLE != 'editor' and state.get('step') == 'D3':
                state['current'] = k
            elif state.get('current') is not None and state['current'] >= k:
                state['current'] += 1
    return next(q for q in state['places'] if q['rel'] == rel and q['name'] == MODULE)


def _create_file(root, state, rel, text):
    """Writes the new file rel with the code sent; refused when it does not compile. The file becomes a place."""
    code = _args.code(text)
    if code.lstrip().startswith(rel):
        code = code.lstrip()[len(rel):].lstrip(':\n ')
    src = textwrap.dedent(code).strip('\n') + '\n'
    if _code.parse(src) is None:
        err = _edit._compiles([src])
        return f'Not created: the content of {rel} does not compile ({err}). Send the whole file again.'
    os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
    _repo.write_lines(root, rel, src.splitlines(keepends=True))
    q = _add_new_file_place(state, rel, 'new file')
    q['end'] = len(src.splitlines())
    i = _journal.target(state, q['id'])
    _journal.edit_done(state, i)
    _tests.save_verified(root)
    _state.record({'look': 'new file', 'q': rel, 'step': state['step']})
    if ROLE == 'editor':
        state['current'], state['editor_review'] = i, True
    return (f'Created {rel} ({q["end"]} lines); it is part of the patch. Existing code that must use it (an import, '
            'a call) is changed at its own place.')


def d3(root, state, args):
    """The edit step (SOP-Agent: only the valid actions of the step). Accepted: an edit of a listed place (its whole
    new function or class, or a line range), ["skip"], ["back"] and a listed place's name (opens it). Questions
    are not answered: the window already holds the code the place uses and the code that calls it (Agentless: the
    model never asks for code). Anything else is refused and changes nothing; only failed edits count against a
    place."""
    if _args.plan(args) == 'back':
        _journal.back(state)
        return 'The edits made so far stay. ' + _candidates_view(state) + '\n\nChoose the code to change.'
    pre = [x for x in args if x and str(x).strip()]
    if len(pre) == 2 and ROLE in ('single', 'editor'):
        rel_new = _new_file_path(root, pre[0])
        if rel_new is None:
            p_open = _place(state, state['plan'][state['current']]['place']) if state.get('current') is not None else None
            if p_open is not None and p_open['name'] == MODULE and not os.path.exists(os.path.join(root, p_open['rel'])) \
                    and _match([p_open], _args.clean(pre[0]), fuzzy=False) is not None:
                rel_new = p_open['rel']
        if rel_new is not None:
            return _create_file(root, state, rel_new, pre[1])
    args = _place_args(state, args)                    # a place's name in front: its internal id
    raw = [x for x in args if x is not None]
    if len(raw) == 4 and not raw[3].strip() and re.fullmatch(r'(?i)\W*P\d+\W*', raw[0] or ''):
        name = _name_of(state, _args.clean(raw[0]).upper())
        return (f"The new lines are empty, which is unclear. To delete lines {raw[1]}-{raw[2]} of {name} send "
                f"['{name}', '{raw[1]}', '{raw[2]}', 'DELETE']. Nothing was changed.")
    items = [x for x in raw if x.strip()]
    single = _args.clean(items[0].replace('\\"', '"').replace("\\'", "'")) if len(items) == 1 else ''
    cur = state['current']
    if _is_skip(single) and cur is not None:
        pid = state['plan'][cur]['place']
        move = _journal.skip(state, cur)
        head = f'{_name_of(state, pid)} skipped: it keeps its code.'
        if ROLE == 'editor':
            return _editor_done(state, head, _name_of(state, pid))
        if move == 'next':
            return head + '\n\n' + _window(root, state, state['current'])
        if move == 'back':
            return head + ' Nothing was changed, so choose again.\n\n' + _candidates_view(state)
        return head + '\n\n' + _finish_view(root, state)
    if single and _is_tests(single):
        _state.record({'look': 'tests', 'q': single[:120], 'step': state['step']})
        return (TESTS_NOTE + (TESTS_READ if ROLE == 'single' else '') + '\n\n'
                + (_window(root, state, cur) if cur is not None else _finish_view(root, state)))
    query = _search_query(items)
    if query:
        here = _place(state, state['plan'][cur]['place'])['rel'] if cur is not None else None
        found = _search_view(root, state, query, prefer=here)
        if found:
            return found
        return f'Nothing in the code holds {_said(query)}. Nothing was changed.' + (
            '\n\n' + _window(root, state, cur) if cur is not None else '')
    listed = _listed_place(state, single) if single else None
    if listed:
        i = _journal.target(state, listed)               # a listed place: open it
        if state['plan'][i]['status'] == 'skipped':
            state['plan'][i]['status'] = 'todo'
        state['current'] = i
        return _window(root, state, i)
    if single and '\n' not in single and len(single) <= 200:
        opened = _open_named(root, state, single)
        if opened:
            return opened
        if not DEF_LINE.search(single):
            # V15 and the local run: 'inspect.Parameter.empty' was answered "Not run: this step edits ...", which
            # reads as a wrong form; it is a search that found nothing
            return (f'Nothing in the repository holds {_said(_search_text(single))} (library and standard-library code is '
                    'not searched). Nothing was changed.' + ('\n\n' + _window(root, state, cur) if cur is not None
                                                            else '\n\n' + _finish_view(root, state)))
    if not re.fullmatch(r'(?i)\W*P\d+\W*', items[0] if items else ''):
        edited = _edit_of_other(root, state, raw)
        if edited is not None:
            return edited
    whole = _whole_args(items)
    if whole:
        pid, text = whole
        p = _place(state, pid)
        if p is None:
            return ('The first item is not the name of a listed place. Nothing was changed.\nPlaces:\n'
                    + '\n'.join('  ' + _place_line(q) for q in state['places']))
        i = _journal.target(state, pid)
        unseen = _unseen(root, state, p['rel'], p['start'], p['end']) if p['name'] != MODULE else []
        if len(unseen) > UNSEEN_OK and _whole_target_is_place(text, p):
            return _show_unseen(root, state, p, p['start'], p['end'], unseen)
        before = _repo.read_lines(root, p['rel'])
        return _after_edit(root, state, i, p, _whole_edit(root, p, text), before)
    e = _args.edit(args)
    if e is None:
        if len(items) == 4 or (len(items) > 1 and re.fullmatch(r'(?i)\W*P\d+\W*', items[0] or '')):
            why = ('the second and third items must be line numbers, like 79 and 82.' if len(items) >= 4 else
                   f'it has {len(items)} items: send the place name and the whole new function or class, or the place '
                   'name, two line numbers and the new lines.')
            q = _place(state, state['plan'][cur]['place']) if cur is not None else None
            if q is not None and len(items) == 2 and q['name'] not in (MODULE, '<exports>'):
                # c15: ['Dependant.is_gen_callable', 'is_gen_callable'] four times against the generic text: the
                # second item shown filled with the place's own first lines (W4)
                lines = _repo.read_lines(root, q['rel'])[q['start'] - 1:q['start'] + 1]
                first = '\n'.join(l.rstrip('\r\n') for l in lines)
                why = (f'the second item must be the whole new code of {q["handle"]}, starting with its first lines '
                       f"as they are now: ['{q['handle']}', '{first}\n        <the new body>'].")
            msg = (f'The edit was not read: {why} Nothing was changed, and this form is never read, so sending it '
                   'again gives the same answer.')
        elif ROLE == 'editor':
            msg = ("Not run: this form is never accepted here, so sending it again gives the same answer. Accepted: the "
                   "whole new function or class ['<place name>', '<code>'], a line edit ['<place name>', '<first line "
                   "number>', '<last line number>', '<new lines>'], ['skip'] if the place needs no change"
                   + (". Nothing was changed." if _narrow(state) else ", or the name of related code to read it. "
                      "Nothing was changed."))
        else:
            msg = ("Not run: this form is never accepted in this step, so sending it again gives the same answer. "
                   "Accepted: the whole new function or class ['<place name>', '<code>'], a line edit ['<place name>', "
                   "'<first line number>', '<last line number>', '<new lines>'], ['skip'] if the place needs no change, "
                   "['back'] to choose other code, or the name of code, a text to find or a file with two line numbers "
                   "to choose other code. Nothing was opened or changed.")
        if cur is None:
            return msg + '\nPlaces:\n' + '\n'.join('  ' + _place_line(q) for q in state['places'])
        return msg + '\n\n' + _window(root, state, cur)      # refused, but the place stays open
    pid, start, end, text = e
    p = _place(state, pid)
    if p is None:
        return ('The first item is not the name of a listed place. Nothing was changed.\nPlaces:\n'
                + '\n'.join('  ' + _place_line(q) for q in state['places']))
    _refresh(root, state, p['rel'])
    p = _place(state, pid)
    if p['name'] not in (MODULE, '<exports>') and _whole_target_is_place(text, p) and text.lstrip().startswith(('@', 'def ', 'async ', 'class ')):
        # c2: the editor sent is_async_gen_callable whole with its numbers from before an edit above it moved it
        # (95-109 for 99-109): the range cut into is_gen_callable. A whole definition of the place replaces the
        # place by its name; the numbers are not needed (B1). Also with the right numbers: c4 sent the first line
        # indented and the rest not, which only the whole-definition reader repairs.
        i = _journal.target(state, pid)
        before = _repo.read_lines(root, p['rel'])
        return _after_edit(root, state, i, p, _whole_edit(root, p, text), before)
    big = [pid, start, end]
    if text.strip() == 'DELETE' and end - start + 1 > DELETE_CONFIRM and state.get('pending_delete') != big:
        state['pending_delete'] = big
        return (f'Nothing was deleted yet: {end - start + 1} lines is a large deletion, so here they are first. To '
                f"delete them, send the same call again: ['{p['handle']}', '{start}', '{end}', 'DELETE'].\n"
                + _seen_mark(p['rel'], _lines_view(root, p['rel'], start, end, p['handle'])))
    if text.strip() != 'DELETE':
        unseen = _unseen(root, state, p['rel'], start, end)
        if len(unseen) > UNSEEN_OK:
            return _show_unseen(root, state, p, start, end, unseen)
    state.pop('pending_delete', None)
    i = _journal.target(state, pid)
    before = _repo.read_lines(root, p['rel'])
    return _after_edit(root, state, i, p, _edit.apply(root, p['rel'], start, end, text), before)


SHOW_CHANGED = 40   # diff lines shown after an edit


def _changed_view(before, after, rel=''):
    """The edit as a unified diff (git's format, which models have read most): removed lines with -, added lines
    with +, 2 lines around, so that a line removed by mistake is visible (V10: an edit dropped a public parameter and
    the answer showed only the new lines)."""
    lines = list(difflib.unified_diff([l.rstrip('\r\n') for l in before], [l.rstrip('\r\n') for l in after],
                                      fromfile=f'a/{rel}', tofile=f'b/{rel}', n=2, lineterm=''))
    if len(lines) > SHOW_CHANGED:
        lines = lines[:SHOW_CHANGED] + [f'... ({len(lines) - SHOW_CHANGED} more diff lines)']
    return '\n'.join(lines)


def _after_edit(root, state, i, p, r, before):
    """The verdict of an edit r of plan entry i (place p; before: its file's lines before the edit): not applied,
    BROKEN (undone), or OK / not verified with the changed lines, then the next place or the finish."""
    pid = p['id']
    if not r.applied:
        return _failed(root, state, i, f'Not applied: {r.error}.', r.error)
    _refresh(root, state, p['rel'])
    new_lines = _repo.read_lines(root, p['rel'])[r.start - 1:r.end]
    changed = {}
    for e2 in state['plan']:
        q = _place(state, e2['place'])
        if e2['status'] == 'done' or e2['place'] == pid:
            changed.setdefault(q['rel'], []).append(q['name'].split('.')[-1])
    changed.setdefault(p['rel'], []).extend(l.strip() for l in new_lines)
    after = _repo.read_lines(root, p['rel'])
    # the removed lines name the behaviour a deletion takes away: their tests must run too (fastapi_14986, V13b: the
    # deleted root_path code selected no test and the deletion was kept unchecked)
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag in ('delete', 'replace'):
            changed[p['rel']].extend(l.strip() for l in before[i1:i2])
    renamed = sorted({n for r_ in state['requirements'] for n in r_.get('old', [])})
    cover = _status.changed_statements(before, after)
    verdict, detail, new_failures = _tests.check(root, changed, renamed, cover={p['rel']: cover} if cover else None,
                                                 undo=(p['rel'], before))
    notes = ''.join(f'\n  note: {x}' for x in r.repairs + r.warnings)
    if verdict == 'BROKEN' and _pending_elsewhere(state, i, before, after, detail):
        # gold check (httpx_3672): renaming complete -> reset in the parser broke the pool's tests with an
        # AttributeError until the pool's call was renamed too, in every order: one editor per place could never keep
        # the correct fix. A failure that names only what this edit renamed, added or removed, while other places of
        # the plan are still to edit, keeps the edit; their edits are checked against the same tests
        verdict = 'PENDING'
        detail = ('kept for now: these tests fail on a name this edit changes, and the other planned places still '
                  'have to change with it (they are checked after their edits):\n' + detail)
    shown = _changed_view(before, _repo.read_lines(root, p['rel']), p['rel'])
    if verdict == 'BROKEN' and _asserts_old_behaviour(state['plan'][i], detail, new_failures):
        # review: an existing test that asserts the behaviour the issue changes undid every correct edit. The same
        # tests failing again on their own assertions (no exception) after the editor was told keep the edit: the
        # hidden tests replace such a test, and the warning says which tests now disagree
        verdict = 'KEPT_AGAINST'
        _state.save('accepted_failures', sorted(set(_state.load('accepted_failures', [])) | set(new_failures)))
        detail = ('kept although these existing tests fail on their assertions a second time; they check the old '
                  'behaviour, which the issue changes:\n' + detail)
    if verdict == 'BROKEN':
        asserts = _assert_only(detail, new_failures)
        _remember_failed(state, p, r, new_failures, asserts)
        gone = _old_name_uses(root, before, _repo.read_lines(root, p['rel']), p['rel'])
        if gone:
            # a rename breaks the callers' tests: they are named, to rename together (edit them here by their names)
            detail += ('\n' + gone + ('\nA rename needs these uses renamed too. Reply with one sentence for the '
                                      'coordinator, without a call, naming them: each gets its own editor.'
                                      if _narrow(state) else '\nA rename needs these uses renamed too: edit each by '
                                      'the name of the function that holds it, then the place again.'))
        _tests.restore_verified(root)
        _refresh(root, state, p['rel'])
        n = len(new_failures)
        if detail.startswith(('the changed code cannot be imported', 'the changed code imports')):
            # (h1, fastapi_15800: an edit that used os without importing it was undone as '2 existing tests failed')
            how = (f'A name the new code uses must be defined or imported: add a missing import at {p["rel"]} top '
                   f'level (its own edit, by line numbers), then send {p["handle"]} again.'
                   if re.search(r'NameError|ImportError|ModuleNotFoundError|is not defined', detail) else
                   f'Fix what the error names, then send {p["handle"]} again.')    # a SyntaxError is not an import
            return _failed(root, state, i, f'Undone: after the edit {p["rel"]} cannot be imported, so {p["handle"]} is '
                           f'back as it was before the edit.\n{detail}\n{how}', 'BROKEN ' + ' '.join(new_failures[:3]))
        return _failed(root, state, i, f'Undone: {n} existing test{"s" if n > 1 else ""} that passed before the edit '
                       f'failed after it, so {p["handle"]} is back as it was before the edit.\n{detail}\n'
                       f'Send {p["handle"]} again with a change that keeps {"these tests" if n > 1 else "this test"} '
                       'passing.' + (f' If the issue asks for exactly the value {"these tests reject" if n > 1 else "this test rejects"}, '
                                     'send the same edit again: it is then kept.' if asserts else ''),
                       'BROKEN ' + ' '.join(new_failures[:3]))
    _tests.save_verified(root)
    # the numbers after the edit moved by the change in length: the marks follow them (c8: dropping every mark of the
    # file after an edit of _unwrapped_call refused the edit of is_gen_callable, read before, as 'not shown'); the
    # changed lines are in the diff shown with this answer
    delta = len(_repo.read_lines(root, p['rel'])) - len(before)
    marks = (state.get('seen') or {}).get(p['rel'])
    if marks is not None:
        old_end = r.end - delta
        state['seen'][p['rel']] = sorted({m for m in marks if m < r.start} | {m + delta for m in marks if m > old_end}
                                         | set(range(r.start, r.end + 1)))
    what = {'OK': 'Kept and checked', 'KEPT_AGAINST': 'Kept', 'PENDING': 'Kept'}.get(verdict, 'Kept, not checked')
    head = f'{what}: {p["handle"]} lines {r.start}-{r.end} changed; {detail}.{notes}\n{shown}'
    gone = _old_name_uses(root, before, _repo.read_lines(root, p['rel']), p['rel'])
    if gone:
        head += '\n' + gone
    move = _journal.edit_done(state, i)
    if ROLE == 'editor' and state.get('editor_place') and state['plan'][i]['place'] != state['editor_place']:
        j = _journal.target(state, state['editor_place'])
        if state['plan'][j]['status'] == 'todo':
            # an edit of other code by this editor is kept; its own place is still to decide
            state['current'], state['step'] = j, 'D3'
            return head + f'\n\nBack to {_name_of(state, state["editor_place"])}:\n\n' + _window(root, state, j)
        # its own place is edited already: the review stays on it (review: current pointed at the other code, so
        # NEXT and the window named it)
        state['current'], state['step'], state['editor_review'] = j, 'D3', True
        own = _name_of(state, state['editor_place'])
        return (head + f'\n\nThe change at {own} is made too. If the work is complete, reply with one sentence for '
                'the coordinator, without a call.')
    if ROLE == 'editor':
        # the place stays open with the diff: run a04 (requests_7328) saw its own wrong deletion in this diff and
        # sent the right fix, which a closed editor refused 5 times (F1: told where, it corrects well)
        state['current'], state['step'], state['editor_review'] = i, 'D3', True
        ex = _example_view(root)
        if ex:
            head += '\n\n' + ex
        return (head + f'\n\nIf this is the whole change {p["handle"]} needs, reply with one sentence for the '
                'coordinator, without a call. If it is wrong or incomplete, send the corrected edit of the code '
                'below (the code as it is now).\n\n' + _window(root, state, i, show=(max(p['start'], r.start - 5),
                                                                       r.end)))
    if move == 'next':
        return head + '\n\n' + _window(root, state, state['current'])
    return head + '\n\n' + _finish_view(root, state)


OLD_NAME_USES = 8


def _old_name_uses(root, before, after, rel=''):
    """After an edit that removed or renamed a definition: the lines of the package that still use the old name. V18
    (httpx_3672): after renaming HTTPParser.complete to reset, the editor read the server and pool files by ranges
    (50 reads) to find the callers of complete."""
    def names(lines):
        t = _code.parse(''.join(lines))
        return {x.name.split('.')[-1] for x in _code.symbols(t)} if t is not None else set()
    removed = sorted(n for n in names(before) - names(after) if not n.startswith('__'))
    if not removed:
        return ''
    out = []
    for name in removed[:2]:
        # uses as code only: an attribute (.complete), a call (complete(...)); not comments or the word in a text
        pat = re.compile(r'\.' + re.escape(name) + r'\b|(?<![\w.])' + re.escape(name) + r'\s*\(')
        hits = []
        home = rel.split('/')[:-1]
        holding = _map.having(root, name)
        for f in sorted((f for f in _repo.iter_py(root, docs=False) if f in holding),
                        key=lambda f: f.split('/')[:-1] != home):
            lines = _repo.read_lines(root, f)
            owner = None
            for n, line in enumerate(lines, 1):
                code = line.split('#', 1)[0]
                if pat.search(code) and not re.match(r'\s*(async\s+)?def\s', line):
                    if owner is None:
                        syms = _code.symbols(_code.parse(''.join(lines)))
                        owner = _code.owner_map(syms, len(lines))
                    o = owner[n] if n < len(owner) else None
                    where = f' in {o.name}' if o is not None else ''
                    hits.append(f'  {f}:{n}{where}: {line.strip()[:80]}')
        if hits:
            more = f' (first {OLD_NAME_USES} of {len(hits)})' if len(hits) > OLD_NAME_USES else ''
            out.append(f'Code that still uses the old name {name}{more}:\n' + '\n'.join(hits[:OLD_NAME_USES]))
    return '\n'.join(out)


def _edit_key(args):
    """The identity of an edit: its items without spacing differences."""
    return '\x00'.join(re.sub(r'\s+', ' ', str(a)).strip() for a in args if a is not None and str(a).strip())


def _pending_elsewhere(state, i, before, after, detail):
    """True when every new failure of the edit of plan entry i is an AttributeError, ImportError or NameError on a
    name that the edit removed or added, and another place of the plan is still to edit (a rename or a new name
    that spans places)."""
    if not any(e['status'] == 'todo' for j, e in enumerate(state['plan']) if j != i):
        return False
    blocks = re.split(r'(?m)^  \S.*\(.*\):$', detail)[1:]
    if not blocks:
        return False
    removed = set(IDENT_RE.findall(''.join(before))) ^ set(IDENT_RE.findall(''.join(after)))
    for b in blocks:
        # "AttributeError: 'HTTPParser' object has no attribute 'complete'", "cannot import name 'reset'",
        # "NameError: name 'reset' is not defined": one of the quoted names is the changed one
        line = next((l for l in b.splitlines() if re.search(r'AttributeError|ImportError|NameError|cannot import', l)),
                    None)
        if line is None or not set(re.findall(r"'(\w+)'", line)) & removed:
            return False
    return True


IDENT_RE = re.compile(r'[A-Za-z_]\w*')


def _assert_only(detail, failures):
    """True when each new failing test fails on an assertion of the test itself (expected vs actual), not on an
    exception of the code: the test checks a value, which the issue may change on purpose."""
    if not failures:
        return False
    blocks = re.split(r'(?m)^  \S.*\(.*\):$', detail)[1:]
    if not (bool(blocks) and len(blocks) == min(len(failures), 3) and all(
            re.search(r'(?m)^    the test runs: assert\b', b) and not re.search(r'\b(?!Assertion)\w+(Error|Exception)\b', b)
            for b in blocks)):
        return False
    # V21 (fastapi_14986): three existing tests rightly rejected a deletion that the issue did not ask for, and the
    # editor resent it to keep it. Only a value the issue itself writes counts: the new value each test rejects
    # ('assert <new> == <old>') must appear in the statement
    text = re.sub(r'\s+', ' ', (_state.load('statement', {}) or {}).get('text', ''))
    for b in blocks:
        m = re.search(r'(?m)^    (?:AssertionError: )?assert (.+?) (?:==|!=|in|not in) ', b)
        new = (m.group(1).strip() if m else '').strip('\'"')
        if len(new) < 2 or new not in text:
            return False
    return True


def _asserts_old_behaviour(e, detail, failures):
    """True when an edit of plan entry e broke the same existing tests as the edit before it, on assertions only."""
    before = [x for x in e.get('errors') or [] if x.startswith('BROKEN ')]
    return bool(before) and before[-1] == 'BROKEN ' + ' '.join(failures[:3]) and _assert_only(detail, failures)


def _assert_resend(state, args):
    """True when args resend an edit undone only by test assertions: it runs again (and is kept, _after_edit)."""
    v = (state.get('failed_edits') or {}).get(_edit_key(args))
    return bool(v) and len(v) > 2 and bool(v[2])


def _remember_failed(state, p, r, failures, asserts=False):
    """An edit the tests undid, kept with the tests it broke, so that the same edit sent again is answered at once."""
    key = state.get('edit_key')
    if key:
        failed = state.setdefault('failed_edits', {})
        failed[key] = [p['handle'], failures[:3], asserts]
        for k in list(failed)[:-8]:
            del failed[k]


EDITOR_DONE = ('The work on {name} is finished here. Reply with one sentence for the coordinator, without a call: '
               'what changed at {name}, or why nothing did (name other code if the change belongs there).')
EDITOR_CLOSED = ('Edits of {name} are closed, so this call was not run, and no further call runs in this editor. The '
                 'coordinator waits for one sentence: what changed at {name}, or why nothing did. If you have nothing '
                 'to add, reply with this sentence: "{done}"')


def _editor_done(state, head, name):
    """In the editor role (edit_place), the place is decided: the editor ends with one sentence, and the coordinator,
    whose history holds only that sentence, hands the next place to a fresh editor (no window is ever compacted)."""
    name = name or 'this place'
    state['editor_done'], state['editor_review'] = True, False
    return head + '\n\n' + EDITOR_DONE.format(name=name)


def _failed(root, state, i, message, error):
    name = _name_of(state, state['plan'][i]['place'])  # before the plan may be emptied
    failed_place = state['plan'][i]['place']
    n_tries = len(state['plan'][i]['errors']) + 1
    move = _journal.edit_failed(state, i, error)       # may empty the plan (back to choosing)
    if move == 'retry':
        return message + '\n\n' + _window(root, state, i)
    # the place is left: the sentence asking to send it again would contradict NEXT (docs/feedback_audit.md #4)
    message = re.sub(r'\nSend \S+ again with a change that keeps (these tests|this test) passing\.( If the issue asks '
                     r'[^\n]*kept\.)?', '', message)
    left = f'{name} was not changed after {n_tries} tries, so it keeps its last checked code.'
    if ROLE == 'editor':
        mine = state.get('editor_place')
        if mine and failed_place != mine and _journal.plan_entry(state, mine) is not None:
            # a08: the editor of Session.send edited resolve_redirects (done by an earlier editor) and failed twice;
            # that closed the editor before it decided anything on its own place. Its own place stays open
            j = _journal.target(state, mine)
            if state['plan'][j]['status'] != 'done' or state.get('editor_review'):
                state['current'], state['step'] = j, 'D3'
                return f'{message}\n{left}\n\nBack to {_name_of(state, mine)}:\n\n' + _window(root, state, j)
        return _editor_done(state, f'{message}\n{left}', name)
    if state['step'] == 'D3':
        return f'{message}\n{left}\n\n' + _window(root, state, state['current'])
    if state['step'] == 'D1':
        return f'{message}\n{left} Nothing was changed, so choose again.\n\n' + _candidates_view(state)
    return f'{message}\n{left}\n\n' + _finish_view(root, state)


def _added_text(root):
    diff = _repo.worktree_diff(root) or ''
    return '\n'.join(l[1:] for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))


def _uncovered(state, root):
    """The requirements the code shows are not met yet: a new name that nothing defines. When none is known, all."""
    added = _added_text(root)
    out = [r for r in state['requirements']
           if any('not defined anywhere' in f for f in _status.requirement_facts(root, r, added))]
    return out or list(state['requirements'])


def _example_view(root):
    try:
        return _example.view(root)
    except Exception as e:                      # never a traceback: the example is an aid
        _state.record({'error': f'example {type(e).__name__}: {e}'})
        return ''


def _finish_view(root, state):
    """What the patch holds, read from git (not from the plan, which forgets edits after a way back), the places that
    were looked at and not changed, and per requirement the facts the code can show. No question is asked: a
    question without a new fact makes the model undo correct work (Huang et al.; FlipFlop)."""
    rows = _status.summary(root)
    if rows:
        out = ['The patch changes:'] + [f'  - {name} in {rel} (+{a} -{r})' for rel, name, a, r in rows]
    else:
        out = ['The patch is empty: nothing is changed.']
    changed = {(rel, name) for rel, name, _, _ in rows}

    def touched(q):             # a class place counts as changed when one of its members changed
        return any(rel == q['rel'] and (name == q['name'] or name.startswith(q['name'] + '.') or
                                        q['name'] == MODULE and name == 'top level') for rel, name in changed)
    left = [_place(state, e['place'])['handle'] + (f' ({len(e["errors"])} edits there were refused or undone)'
                                                   if e.get('errors') else '')
            for e in state['plan'] if not touched(_place(state, e['place']))]
    if left:
        out.append('Looked at and not changed: ' + ', '.join(left) + '.')
    out.append('The requirements, with what the code shows:')
    added = _added_text(root)
    for r in state['requirements']:
        facts = _status.requirement_facts(root, r, added)
        out.append(f'  - {r["text"][:160]}')
        if facts:
            # (p2: 'no fact of the code to show for it here' was read as 'unverified: write a test')
            out.append('      ' + '; '.join(facts) + '.')
    ex = _example_view(root) if rows else ''
    if ex:
        out.append(ex)
    if rows:
        # p1 (requests_7502): after 'nothing here can be checked automatically' the coordinator tried pytest, 'ls
        # tests' and a second specify_fix to write a test, instead of submitting a correct patch
        out.append('The hidden tests, with the issue\'s own test, run on this patch after submit_patch; tests are not '
                   'written here (changes to test files are dropped from the patch).')
    return '\n'.join(out)


def d4(root, state, args):
    """Finish: submit_patch, or ["back"] to choose code for the requirements that nothing changed covers (the script
    picks them from the coverage map: no requirement id to look up), or an edit of a listed place again."""
    ids, others = _args.ids(args, 'R')
    if _args.plan(args) == 'back' or (ids and not others):
        reqs = [r for r in state['requirements'] if r['id'] in ids] or _uncovered(state, root)
        st = _state.load('statement', {})
        text = '\n'.join(r['text'] for r in reqs)
        cands, _ = _rank_candidates(root, text, st.get('terms', []), reqs)
        _journal.requirement_back(state, cands)
        about = '; '.join(r['text'][:80] for r in reqs[:3])
        return (f'Candidates for: {about}. The edits made so far stay.\n'
                + _candidates_view(state, title='Candidates:') + '\n\nChoose the code to change.')
    args = _place_args(state, args)
    if _args.edit(args) is not None or _whole_args([x for x in args if x and x.strip()]) is not None:
        state['step'] = 'D3'
        out = d3(root, state, args)
        if state['step'] == 'D3' and state['current'] is None:
            state['step'] = 'D4'
        return out
    items = [x for x in args if x and x.strip()]
    if _search_query(items) or (len(items) == 1 and '\n' not in items[0].strip() and len(items[0]) <= 200
                                and not _is_done(items[0]) and not _is_skip(items[0])):
        # a place's name, other code or a text: the same as in the edit step
        state['step'] = 'D3'
        out = d3(root, state, args)
        if state['step'] == 'D3' and state['current'] is None:
            state['step'] = 'D4'        # nothing was opened: d3's answer stands (the tests note, a search that found
        return out                      # nothing), and the step stays the finish
    return 'Nothing left to do in this step. ' + _finish_view(root, state)


STEPS = {'S0': s0, 'D1': d1, 'D2': d2, 'D3': d3, 'D4': d4}


def _is_statement_again(args):
    """The issue statement sent again (its first words match the statement copied at the start)."""
    first = (args[0] if args else '') or ''
    if len(first.strip()) < 40:
        return False
    norm = lambda t: re.sub(r'\W+', ' ', t).strip().lower()
    known = norm(_state.load('statement', {}).get('text', ''))
    head = norm(first)[:60]
    return bool(known) and len(head) >= 30 and head in known


def _move_on(root, state):
    """The third same call in a row: the work moves on from where it is stuck."""
    if state['step'] == 'D3' and state.get('current') is not None:
        name = _name_of(state, state['plan'][state['current']]['place'])
        done = state['plan'][state['current']]['status'] == 'done'      # (read before the skip moves current)
        move = _journal.skip(state, state['current'])
        # (c15: 'keeps its code' after an accepted edit of the place was false)
        head = (f'The same call came three times, so the work on {name} moves on; '
                + ('its accepted edit stays in the patch.' if done else 'it keeps its code.'))
        if ROLE == 'editor':
            return _editor_done(state, head, name)
        if move == 'next':
            return head + '\n\n' + _window(root, state, state['current'])
        if move == 'back':
            return head + ' Nothing is changed yet, so choose other code.\n\n' + _candidates_view(state)
        return head + '\n\n' + _finish_view(root, state)
    if state['step'] == 'D2' and state['places']:
        chosen = [(p['id'], '') for p in state['places'] if p['reason'] == 'chosen'] or [(state['places'][0]['id'], '')]
        _journal.set_plan(state, chosen)
        _tests.save_verified(root)
        return 'The chosen code is planned now, so edit it.\n\n' + _window(root, state, state['current'])
    if state['step'] == 'D1' and state['candidates']:
        return ('The same call came three times, so the first candidate is opened.\n\n'
                + d1(root, state, [state['candidates'][0]['id']]))
    if state['step'] == 'D4':
        return 'The same call came three times. Nothing else is done in this step.\n\n' + _finish_view(root, state)
    return ('The same call came three times, so it was not run again. Send the name of the code to change, or a name '
            'or text to find it.')


def _progress(root, state):
    """The first line of every answer (recap: Laban et al. +16 to +17.5 points; Manus todo list): what the patch
    holds, read from git, what is open now and what is left."""
    now, left = '', []
    if state['step'] == 'D3' and state.get('current') is not None:
        now = _name_of(state, state['plan'][state['current']]['place'])
        left = [_name_of(state, e['place']) for i, e in enumerate(state['plan'])
                if e['status'] == 'todo' and i != state['current']]
    elif state['step'] == 'D1':
        now = 'choosing the code to change'
    elif state['step'] == 'D4':
        now = 'finishing'
    plan = ''
    if ROLE in ('coordinator', 'specifier') and state['step'] in ('D3', 'D4') and state.get('plan'):
        # P8: the plan and where each place stands, recited in every answer, so that a compacted history still knows
        # it (s3, fastapi_14986: after a compaction the coordinator went back and a second specify_fix was told 'no
        # places are chosen yet' while the patch held three edits)
        word = {'done': 'edited', 'skipped': 'no change', 'todo': 'to edit'}
        plan = ' The plan: ' + ', '.join(f'{_name_of(state, e["place"])} ({word.get(e["status"], e["status"])})'
                                         for e in state['plan'][:10]) + '.'
    try:
        return _status.progress_line(root, now, left) + plan
    except Exception as e:      # the recap must never cost the answer
        _state.record({'error': f'progress: {type(e).__name__}: {e}'})
        return ''


def _current_view(root, state):
    if ROLE == 'coordinator' and state['step'] == 'D3':
        return _coordinator_status(root, state)      # (m2: a repeated call showed the coordinator an editor's window)
    if state['step'] == 'D3' and state['current'] is not None:
        return _window(root, state, state['current'])
    if state['step'] == 'D4':
        return _finish_view(root, state)
    return _requirements_view(state) + '\n\n' + _candidates_view(state) + '\n\nChoose the code to change.'


def _coordinator_status(root, state):
    """The coordinator's answer while places are left: which, and that edit_place edits them."""
    left = [_name_of(state, e['place']) for e in state['plan'] if e['status'] == 'todo']
    if not state.get('spec_done'):
        bare = [_name_of(state, e['place']) for e in state['plan'] if e['status'] == 'todo' and not _specified(e)]
        return ('Not specified yet: ' + ', '.join(bare or left) + '. The tool specify_fix reads the code of the places '
                'and writes what changes at each.')
    return ('Places still to edit: ' + ', '.join(left) + '. Each is edited by the tool edit_place (the script does '
            'not edit for the coordinator).')


def _coordinator_choice(root, state, item):
    """The coordinator names code after the choose step (its instruction: when an editor says the change belongs to
    other code, choose that code): a listed place is planned and is next; other code of the repository is chosen as
    at the choose step. None when the item names no code (a07: the name was answered with the status)."""
    name = _args.clean(item)
    if not NAME_LIKE.fullmatch(name) or _is_skip(name) or name.lower() in ('status', 'state', 'next') or \
            _is_tests(name) or _is_code_to_run(name):
        return None
    hit = _match(state.get('places') or [], name, fuzzy=False)
    if hit is not None:
        i = _journal.target(state, hit['id'])
        e = state['plan'][i]
        if e['status'] == 'done':
            return f'{hit["handle"]} is edited already.\n\n' + _coordinator_status(root, state)
        if state.get('spec_done') and e.get('intent') == 'no change':
            return f'{hit["handle"]} was specified as no change.\n\n' + _coordinator_status(root, state)
        e['status'] = 'todo'
        state['current'], state['step'] = i, 'D3'
        if state.get('spec_done') and not _specified(e):
            state['spec_done'], state['spec_shown'], state['spec_views'] = False, False, 0   # specify_fix writes it
            return f'{hit["handle"]} joins the plan.\n\n' + _coordinator_status(root, state)
        return f'{hit["handle"]} is planned and is next.\n\n' + _coordinator_status(root, state)
    found = [c for c in _resolve(root, [name]) if not _repo.is_doc_path(c['rel'])]
    if not found:
        return None
    if state.get('spec_done'):
        # m2: after the specification the coordinator named the class Dependant; choosing it replaced the plan and
        # dropped the specified places. Code named now joins the plan, the specification of the others stays
        _add_place(state, found[0], 'named by the coordinator')
        state['spec_done'], state['spec_shown'], state['spec_views'] = False, False, 0
        hit = next(q for q in state['places'] if (q['rel'], q['name']) == (found[0]['rel'], found[0]['name']))
        state['current'], state['step'] = _journal.target(state, hit['id']), 'D3'      # (also from the finish)
        return f'{hit["handle"]} joins the plan.\n\n' + _coordinator_status(root, state)
    head, out = _choose_code(root, state, found[0])
    return head + '\n\n' + out


def _editor_gate(root, state, args):
    """In the editor role (edit_place), the calls that concern the editor's own scope, answered before the repeat guard
    so that they never change the shared plan; None for an ordinary call on the editor's own place.

    a04 and a05 (docs/feedback_audit.md #1-#3, #9): a closed editor kept calling and got the same text each time,
    while its calls still reached the repeat guard, whose move-on skipped another place and sent the plan back to
    choosing; a deciding editor that named related code to read it was refused and guessed wrong."""
    items = [str(a) for a in args if a is not None and str(a).strip()]
    places = state.get('places') or []
    name = _args.clean(items[0]) if items else ''
    hit = _match(places, name, fuzzy=False) if (places and len(items) == 1) else None
    mine = state.get('editor_place')
    if OPENING and items and (hit is not None or mine is None or state.get('editor_done') or state['step'] == 'D1'):
        return _editor_open(root, state, name, hit)
    # (b10: an open editor sent open.py with file names, 'ls -R' and 'file:line' to browse; those are answered as
    # edit.py answers them: related code read only, or the usual refusals)
    own = next((e for e in state.get('plan') or [] if e['place'] == mine), None) if mine else None
    # deciding: its place still to edit, or an accepted edit under review (until the editor replies and the
    # coordinator's next call to the script moves the work on)
    deciding = own is not None and (own['status'] == 'todo' or state.get('editor_review')) \
        and not state.get('editor_done')
    if deciding and own['status'] != 'todo' and hit is not None:
        nxt = next((e['place'] for e in state['plan'] if e['status'] == 'todo'), None)
        deciding = hit['id'] != nxt     # the next place to edit: a new editor (the coordinator did not call status)
    if hit is not None and state['step'] in ('D3', 'D4') and (hit['id'] != mine or state.get('editor_done')) \
            and not deciding:
        # a new editor opens its place (its first call names it, often the coordinator's last call word for word)
        state.update(editor_done=False, editor_place=hit['id'], editor_review=False, editor_reads=[], reads_again={}, seen={},
                     editor_name=hit['handle'])
        state['recent'], state['repeats'] = [], 0
        i = _journal.target(state, hit['id'])
        if state['plan'][i]['status'] == 'skipped':
            state['plan'][i]['status'] = 'todo'
        state['current'], state['step'] = i, 'D3'
        return _window(root, state, i)
    # (the name is kept: a skip of the only place sends the plan back to choosing and the places are gone)
    mine_name = state.get('editor_name') or (_name_of(state, mine) if mine else '')
    if state['step'] == 'D1' and len(items) == 1 and NAME_LIKE.fullmatch(name):
        # a13: the plan went back to choosing (the only place failed twice) and the coordinator called edit_place
        # with other code; the request is the coordinator's choice, so it is chosen here and its editor opens it
        c = _match(state.get('candidates') or [], name, fuzzy=False)
        if c is None:
            found = [x for x in _resolve(root, [name]) if not _repo.is_doc_path(x['rel'])]
            c = found[0] if found else None
        if c is not None:
            _choose_code(root, state, c)
            hit = _match(state.get('places') or [], c.get('handle') or c['name'], fuzzy=False)
            if hit is not None:
                state.update(editor_done=False, editor_place=hit['id'], editor_review=False, editor_reads=[], reads_again={}, seen={},
                             editor_name=hit['handle'])
                state['recent'], state['repeats'] = [], 0
                i = _journal.target(state, hit['id'])
                state['current'], state['step'] = i, 'D3'
                return _window(root, state, i)
    if state.get('editor_done'):
        return _editor_gate_closed(root, state)
    if mine is None or own is None or state['step'] not in ('D3', 'D4'):
        if not places or state['step'] not in ('D3', 'D4'):
            return ('No place is open for this editor: choosing code is the coordinator\'s step. Reply with one sentence '
                    'for the coordinator, without a call, saying what the change needs.')
        req = state.get('requested')
        return ("This editor opens its place first: call run_skill_script with file_path open.py and args "
                + (f"['{req}']" if req else "['<the place name from the request>']") + '.')
    used = _editor_tokens(state)
    is_edit = (len(items) == 2 and DEF_LINE.search(items[1]) is not None) or \
        (len(items) == 4 and all(_args.clean(x).isdigit() for x in items[1:3]))
    # V17: the 31B's edits of is_gen_callable and is_async_gen_callable (the fix the reference makes) were refused by
    # this estimate, never checked against Kaggle's real prompt sizes; an edit is a short answer and always runs
    if used > EDITOR_CLOSE_AT and not is_edit and not _is_skip(name):
        # (the place is not closed: an edit or a skip after this still runs)
        return (f'No more code is shown in this editor: its history is near the model\'s context limit '
                f"({CONTEXT_TOKENS} tokens). Send the edits it still needs with what was shown, or reply with one "
                'sentence for the coordinator, without a call.')
    is_read = (len(items) == 1 and NAME_LIKE.fullmatch(name) and not _is_skip(name) and hit is None or
               (hit is not None and hit['id'] != mine)) or \
        (len(items) == 3 and all(_args.clean(x).isdigit() for x in items[1:3]))
    if used > EDITOR_READS_UNTIL and is_read:
        return (f'No more code is shown in this editor: its history is near the model\'s context limit. Edit '
                f'{mine_name} with what was shown, or send [\'skip\'] if it needs no change.')
    i_mine = _journal.target(state, mine)
    if state['step'] == 'D3' and state.get('current') != i_mine:
        state['current'] = i_mine           # the editor's calls act on its own place only
    back = f"\n\nBack to {mine_name}: send its edit, or ['skip'] if it needs no change."
    if len(items) == 1 and _args.plan(items) == 'back':
        return ("Choosing other code is the coordinator's step, not this editor's. If the change belongs to other "
                f"code, reply with one sentence naming it, without a call; if {mine_name} needs no change, send "
                "['skip'].")
    if _narrow(state) and not is_edit and not _is_skip(name):
        p_mine = _place(state, mine)
        own_range = (len(items) == 3 and all(_args.clean(x).isdigit() for x in items[1:3])
                     and _match([p_mine], _args.clean(items[0]), fuzzy=False) is not None)
        own_name = len(items) == 1 and hit is not None and hit['id'] == mine
        # its own file's top level, where an import is added (review: the prompt says how, the read was refused)
        low = name.lower().strip('. ')
        own_top = len(items) == 1 and (low in ('top level', 'imports', 'the imports', 'top-level', 'module') or
                                       low.endswith('top level') and _rank.names_file(low[:-9].strip(), p_mine['rel']))
        if (len(items) in (1, 3) or _search_query(items)) and not own_range and not own_name and not own_top:
            return _narrow_refusal(mine_name, state)
    if hit is not None and hit['id'] != mine:
        return _editor_read(root, state, hit, mine_name) + back
    if len(items) == 1 and name.lower().strip('. ') in ('top level', 'imports', 'the imports', 'top-level', 'module'):
        # c9: 'top level' from the editor of Dependant.is_gen_callable (searched, it dropped the plan): its file's
        name = f"{_place(state, mine)['rel']} top level"
    if len(items) == 1 and hit is None and NAME_LIKE.fullmatch(name) and not _is_skip(name) and not _is_tests(name) \
            and name.lower() not in ('status', 'state', 'next') and _match(places, name, fuzzy=False) is None \
            and not _is_code_to_run(name):
        if name.startswith(('self.', 'cls.')):
            # b9: 'self._unwrapped_call' from the editor of Dependant.is_async_gen_callable: a member of its class
            own_cls = _place(state, mine)['name'].rsplit('.', 1)[0] if '.' in _place(state, mine)['name'] else ''
            name = (own_cls + '.' if own_cls else '') + name.split('.', 1)[1]
        found = [c for c in _resolve(root, [name]) if not _repo.is_doc_path(c['rel'])]
        if found:
            return _editor_read(root, state, found[0], mine_name) + back
        inherited = _inherited_library(root, name) if '.' in name else None
        if inherited:       # b9: FastAPI.lifespan / on_startup come from Starlette, read only
            return inherited + back
        listed = _file_names(root, state, name)
        if listed:
            return listed + back
        return f'Nothing in the repository defines {_said(name)} (library code is not read here).' + back
    if len(items) == 3 and all(_args.clean(x).isdigit() for x in items[1:3]):
        rel, label = _read_target(root, state, items[0])
        p = _place(state, mine)
        a, b = sorted((int(_args.clean(items[1])), int(_args.clean(items[2]))))
        if rel == p['rel'] and a <= p['end'] and b >= p['start'] and not (p['start'] <= a and b <= p['end']):
            # a range that overlaps the editor's own place is read as that place's lines (b9: 1100-1120 around a
            # nested function of 1107-1113 was shown 'read only' from the file)
            return _place_lines(root, p, max(a, p['start']), min(b, p['end']))
        if rel and not _repo.is_test_path(rel) and not (rel == p['rel'] and p['start'] <= a and b <= p['end']):
            n = len(_repo.read_lines(root, rel))
            if a > n:
                return f'{rel} has {n} lines; lines {a}-{b} do not exist.' + back
            b = min(b, n, a + PLACE_LINES - 1)
            label = rel if (not label or label == mine_name) else label
            return _seen_mark(rel, _lines_view(root, rel, a, b, f'{label}, read only')) + back
    return None


CONTEXT_TOKENS = 32768      # the model's context; an agent_tool sub-agent's history is never compacted
# (review: the editor's output can reach 8,192 tokens, so a prompt over 32,768 - 8,192 = 24,576 tokens is refused by
# vLLM; the ceilings keep that room plus one answer)
EDITOR_READS_UNTIL = 18000  # estimated history tokens after which no more code is shown to the editor
EDITOR_CLOSE_AT = 22000     # ... after which no call but an edit, a skip or the reply is answered


# ------------------------------------------------------------------------------------------------- the specification

# The specify_fix sub-agent (agent_tool variant): a fresh context with a large thinking budget sees the code of every
# planned place at once and writes, for each, one sentence of what changes there. The editors then each get one
# specified change and no reading (docs/design_single.md, "Redesign: microtasks"). The sentences are the plan entries'
# intents, which also choose the part of a long place an editor sees (_focus).

SPEC_CHARS = 24000          # the plan's code shown to the specifier (about 7,000 tokens)
SPEC_PLACE_LINES = 80       # lines of one place shown
NO_CHANGE = re.compile(r'(?i)^\W*(no change|none|nothing|skip|no edit|unchanged|keeps? its code)\W*$')


def _spec_view(root, state):
    """Every planned place's numbered code, with the code that calls it and the definitions it uses."""
    out, room = [], SPEC_CHARS
    planned = {e['place']: e for e in state['plan']}
    # the planned places first, then the related ones the plan left out (callers, overrides, exports): c-httpx_3672,
    # the callers of a renamed method are where the rename must go too
    order = [(planned[q['id']], q) for q in state['places'] if q['id'] in planned] + \
            [(None, q) for q in state['places'] if q['id'] not in planned]
    shorts = {_place(state, x['place'])['name'].split('.')[-1] for x in state['plan']
              if _place(state, x['place']) and _place(state, x['place'])['name'] not in (MODULE, '<exports>')}
    for e, p in order:
        tag = p['reason'] if e is not None else f'{p["reason"]}; not planned: give it a sentence if it must change'
        if e is None and p['reason'].startswith('uses '):
            code = ''.join(_repo.read_lines(root, p['rel'])[p['start'] - 1:p['end']])
            both = sorted(n for n in shorts if re.search(r'\.' + re.escape(n) + r'\b', code))
            if len(both) >= 2:
                # s3 (fastapi_14448): the specifier saw _solve_generator, which tests is_gen_callable before
                # is_async_gen_callable, made both true for one callable, and gave it no change
                tag += (f'. It tests {" and ".join(both)}, which the plan changes: after the change, can more than one '
                        'be true for the same object, and does its order of tests still pick the right branch?')
        head = f'== {p["handle"]} ({p["rel"]}, lines {p["start"]}-{p["end"]}; {tag})'
        if e is not None and _specified(e):
            head += f'\nSpecified already ({e["status"]}): {e["intent"][:300]}'
        if p['name'] == '<exports>':
            body = _code_view(root, p['rel'], 1, len(_repo.read_lines(root, p['rel'])), _focus(state), limit=40)
        else:
            at = re.search(r'\(line (\d+)\)$', p['reason']) if e is None else None
            # a caller or reader left out of the plan: the part around the line that calls or reads the planned code
            # (fastapi_14448: the branch order in _solve_generator, 2 lines in a 9-line function, mattered)
            show = (max(p['start'], int(at.group(1)) - 8), min(p['end'], int(at.group(1)) + 12)) if at else None
            body = _code_view(root, p['rel'], p['start'], p['end'], _focus(state, e),
                              limit=SPEC_PLACE_LINES if e is not None else 30, show=show)
        ctx = _context_view(root, p, _focus(state, e)) if e is not None else ''
        text = '\n'.join(x for x in (head, body, ctx) if x)
        if len(text) > room:
            text = head + ' (its code does not fit here: send its name alone to see it)'
        _seen_mark(p['rel'], text)
        out.append(text)
        room -= len(text)
    return '\n\n'.join(out)


def _specified(e):
    return bool(e.get('intent')) and e['intent'] not in ('(not in the plan)',)


def _narrow(state):
    """The editor of a specified change: one place, its sentence, no reading (docs/design_single.md, microtasks)."""
    return ROLE == 'editor' and bool(state.get('spec_done')) and not state.get('spec_failed')


def _narrow_refusal(mine_name, state=None):
    # the rule with its reason and the way out (P3): the change is specified, the editor's view is the place
    e = next((x for x in (state or {}).get('plan') or [] if x['place'] == (state or {}).get('editor_place')), None)
    if e is not None and e['status'] == 'done':
        # m7: the editor of _unwrapped_call, its edit accepted, tried to open the next planned places 6 times and got
        # 'reading is not part of this task': its task is over, so it gets the reply to send
        return (f'The change at {mine_name} is made and kept; the other places are handed to their own editors by '
                'the coordinator. Reply with one sentence for the coordinator, without a call, for example: '
                f'"{mine_name} is changed as specified."')
    return (f'Reading other code is not part of this task: what changes at {mine_name} is specified in its window, '
            'which shows its code and the definitions it uses. Edit it with what is shown, or send [\'skip\'] if the '
            'specification asks for no change here. If the change cannot be made without other code, reply with one '
            'sentence naming that code, without a call.')


_SPEC_N = r'(?:[\w./-]+\.py(?:::[A-Za-z_][\w.]*)?|[A-Za-z_][\w.]*)'     # a new file: its path
SPEC_LINE = re.compile(r'^\s*(' + _SPEC_N + r')(?:\(\))?\s*(?:\([^()\n]*\))?(?:\s*(?::|—|–| - )\s*|\s+|$)(.*)$', re.S)
SPEC_NAME = re.compile(r'^\s*' + _SPEC_N + r'(?:\(\))?\s*(?:\([^()\n]*\))?\s*$')


def _no_sentence(text):
    """True for what the specifier copies from the view instead of a sentence: nothing, a signature, a tag."""
    t = text.strip()
    return not t or bool(re.match(r'(async\s+)?def\s|\w+\s*\(self\b|;?\s*(chosen|same pattern|reads |calls )', t))


def _spec_pairs(items, known=()):
    """([(name, sentence)], [names without a sentence]) from ['A', 'what changes', 'B', 'no change'],
    ['A: what changes'], 'A (file:78) Replace ...', or lines of names copied from the view."""
    lines = []
    for x in items:
        lines += [l for l in x.split('\n') if l.strip()] if '\n' in x and all(
            SPEC_NAME.match(l) or SPEC_LINE.match(l) and _no_sentence(SPEC_LINE.match(l).group(2))
            for l in x.split('\n') if l.strip()) else [x]
    def is_name(x):
        # a short sentence ('loop', 'no change') is not a name: a name is a place's or has the shape of code
        n = SPEC_LINE.match(x).group(1) if SPEC_NAME.match(x) else ''
        return bool(n) and (n in known or n.split('.')[-1] in known or any(c in n for c in '._:'))

    pairs, bare, i = [], [], 0
    while i < len(lines):
        x = lines[i]
        if is_name(x):
            name = SPEC_LINE.match(x).group(1)
            if i + 1 < len(lines) and not is_name(lines[i + 1]) and not _no_sentence(lines[i + 1]) and \
                    not (SPEC_LINE.match(lines[i + 1]) and _match_name_like(lines[i + 1])):
                pairs.append((name, lines[i + 1].strip()))
                i += 2
                continue
            bare.append(name)
            i += 1
            continue
        m = SPEC_LINE.match(x)
        if m and m.group(2).strip() and not _no_sentence(m.group(2)):
            pairs.append((m.group(1), m.group(2).strip()))
        elif m:
            bare.append(m.group(1))
        i += 1
    return pairs, bare


MAX_SENTENCE = 1500


def _new_names_note(root, state):
    """The issue's new names, as filled place names ('FastAPI.frontend' for `app.frontend`), saved for the NEXT line
    (P9, W4: s3 fastapi_15800, a form described in prose only was not used; a filled call form is copied)."""
    new = [n for r in state.get('requirements') or [] if r['type'] == 'new' for n in r.get('names') or []]
    if not new:
        return ''
    index, forms = None, []
    for n in new[:4]:
        if '.' in n:
            var, short = n.rsplit('.', 1)
            index = index or _rank.Index(root)
            owner = _rank.owner_class(index, var.split('.')[-1])
            if owner:
                forms.append(f'{owner[1]}.{short}')
        else:
            first = next((_place(state, e['place']) for e in state['plan']
                          if _place(state, e['place']) and _place(state, e['place'])['name'] != MODULE), None)
            if first:
                forms.append(f'{first["rel"]}::{n}')
    forms = [f for f in dict.fromkeys(forms) if _match(state['places'], f, fuzzy=False) is None]
    state['new_forms'] = forms
    named = ', '.join(f"'{f}'" for f in forms) if forms else "'<Class>.<new name>' or '<path>.py::<new name>'"
    return (f'The issue asks for new code ({", ".join(f"`{n}`" for n in new[:4])}). A new method or function is a place '
            f'too: send its name ({named}) with its sentence, and it is added at the end of that class or file.\n')


def _attribute_owner(table, name):
    """[(rel, class Sym)] for 'Class.attribute' when the class declares or assigns that attribute, or for
    'Class.__init__' when the class has no __init__ of its own; else []."""
    cls, _, attr = name.rpartition('.')
    for r, s in table.find(cls):
        if s.kind != 'class' or getattr(s, 'node', None) is None:
            continue
        own_init = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == '__init__'
                       for n in s.node.body)
        if (attr == '__init__' and not own_init) or attr in class_attributes(s.node):
            return [(r, s)]
    return []


def _new_definition_owner(root, name):
    """(place dict, new short name) for a definition that does not exist yet: 'Class.new' (an existing class) or
    'path.py::new' (an existing file's top level); else None."""
    name = _args.clean(name).strip('`')
    m = re.fullmatch(r'([\w./-]+\.py)::([A-Za-z_]\w*)', name)
    if m and os.path.isfile(os.path.join(root, m.group(1))):
        top = _resolve(root, [f'{m.group(1)} top level'])
        return (top[0], m.group(2)) if top else None
    m = re.fullmatch(r'(?:[\w./-]+\.py::)?([A-Za-z_][\w.]*)\.([A-Za-z_]\w*)', name)
    if not m:
        return None
    for c in _resolve(root, [m.group(1)]):
        if c['name'] != MODULE and not _repo.is_doc_path(c['rel']) and \
                c['name'].split('.')[-1] == m.group(1).split('.')[-1] and _is_class(root, c):
            return c, m.group(2)
    return None


def _is_class(root, c):
    lines = _repo.read_lines(root, c['rel'])
    return any(re.match(r'\s*class\s', l) for l in lines[max(0, c['start'] - 1):c['start'] + 5])


def _match_name_like(line):
    """'Dependant.is_gen_callable (models.py, lines 87-92; chosen)': a copied view line, not a sentence."""
    m = SPEC_LINE.match(line)
    return bool(m) and _no_sentence(m.group(2)) and '.' in m.group(1)


def _specifier(root, state, args):
    open_ = [e for e in state.get('plan') or [] if e['status'] == 'todo' and not _specified(e)]
    items_ = [_args.clean(a) for a in args if a and a.strip() and len(a) < 200]
    known_ = {q['handle'] for q in state.get('places') or []} | {q['name'] for q in state.get('places') or []}
    if state.get('spec_done') and not open_ and state.get('edited') and not _spec_pairs(items_, known_)[0]:
        # (a call with sentences is still read: in V20 a second specifier added a place the edits showed was needed)
        # p1 (requests_7502): the coordinator called specify_fix again after the edit; the new specifier got the whole
        # view, read the fixed code as if nothing was done and spent 10 calls (one cut at 8192 tokens) on it
        done = [f'  {_name_of(state, e["place"])}: {e.get("intent") or "no change"} ({e["status"]})'
                for e in state['plan']]
        return ('The specification was written before and its places are edited; nothing is left to specify:\n'
                + '\n'.join(done) + '\nReply with this sentence for the coordinator, without a call: "The '
                'specification is complete and edited; call step.py with args [\'status\'] for the next call."')
    if state['step'] not in ('D3', 'D4') or not state.get('plan'):
        if _status.summary(root):
            # B12: 'no places are chosen' contradicted a patch with three edits (s3, fastapi_14986)
            return ('The coordinator is choosing code again, so there is no plan to specify right now; the edits made '
                    'so far stay in the patch (see the first line). Reply with this sentence for the coordinator, '
                    'without a call: "Choose the places with step.py first (their names), then call specify_fix."')
        return ('No places are chosen yet, so there is nothing to specify. Reply with one sentence for the coordinator, '
                'without a call, saying so.')
    items = [_args.clean(a) if len(a) < 200 else a.strip() for a in args if a and a.strip()]
    plan_names = [_name_of(state, e['place']) for e in state['plan']]
    form = ("Send one item pair per place: ['<place name>', '<what changes there, in one sentence, or no change>', "
            "...]. A place the change needs that is not listed: its name and its sentence (it is added).")
    known0 = {q['handle'] for q in state['places']} | {q['name'] for q in state['places']}
    asks_view = len(items) == 1 and items[0].lower().strip('. \'"[]') in (
        'the plan', 'plan', 'show', 'start', 'places', 'status', 'show the plan')
    # (review: 'the plan' was read as the name 'the' with the sentence 'plan', so the view never came)
    first_pairs = _spec_pairs(items, known0)[0] if items and not asks_view else []
    if not first_pairs and (not state.get('spec_shown') or not items or (len(items) == 1 and items[0].lower().strip(
            '. ') in ('the plan', 'plan', 'show', 'start', 'places', 'status'))):
        # (review: a first call that already held its sentences was answered with the view and the sentences lost)
        state['spec_views'] = state.get('spec_views', 0) + 1 if state.get('spec_shown') else 1
        if state['spec_views'] >= 3:
            # m7: 'the plan' 6 times, each answered with the whole view (2,500 tokens) in the specifier's history
            return ('The plan was shown above and has not changed. Write the sentences now. ' + form + '\nThe places: '
                    + ', '.join(plan_names) + '.')
        state['spec_shown'] = True
        return ('The places of the plan, with their code, the code that calls them and the definitions they use. For '
                'each place write what must change there so that the issue is fixed: one concrete sentence (what to '
                'add, remove or replace, and with what), or no change. Each place is then edited by its own editor, '
                'which sees only that place and your sentence, so write each sentence so that it can be done from '
                'that place alone.\n\n' + _spec_view(root, state) + '\n\n' + _new_names_note(root, state) + form)
    known = {q['handle'] for q in state['places']} | {q['name'] for q in state['places']} | \
        {q['name'].split('.')[-1] for q in state['places']}
    files = [x for x in items if re.fullmatch(r'\s*[\w./-]+\.py(:\d+(-\d+)?)?\s*', x)]
    if files and len(files) == len(items):
        # m6: 'fastapi/dependencies/models.py' was read as the name 'fastapi' (the class FastAPI) with a sentence
        listed = _file_names(root, state, files[0].split(':')[0])
        if listed:
            return listed.replace('Send one name to read it.', 'Send a name alone to see its code.') + '\n\n' + form
    pairs, bare = _spec_pairs(items, known)
    if not pairs and bare:
        # m5: the specifier sent '_unwrapped_call' (a helper the places use, not a place) 15 times and was told 'no
        # sentence was read': a name alone shows that code, to write its sentence (it joins the places)
        shown = []
        for name in bare[:3]:
            hit = _match(state['places'], name, fuzzy=False)
            if hit is None:
                found = [c for c in _resolve(root, [name]) if not _repo.is_doc_path(c['rel'])]
                if not found:
                    continue
                c = found[0]
                if not any((q['rel'], q['name']) == (c['rel'], c['name']) for q in state['places']):
                    n = max([int(q['id'][1:]) for q in state['places'] if re.fullmatch(r'P\d+', q['id'])] or [0]) + 1
                    state['places'].append({'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end'],
                                            'reason': 'named by the specification', 'id': f'P{n}'})
                    _set_handles(state['places'])
                hit = next(q for q in state['places'] if (q['rel'], q['name']) == (c['rel'], c['name']))
            if hit['name'] in (MODULE, '<exports>'):
                continue
            shown.append(f'== {hit["handle"]} ({hit["rel"]}, lines {hit["start"]}-{hit["end"]})\n'
                         + _code_view(root, hit['rel'], hit['start'], hit['end'], _focus(state), limit=SPEC_PLACE_LINES))
        if shown:
            return ('The code named (give it a sentence if it must change; a place left without one keeps its code):'
                    '\n\n' + '\n\n'.join(shown) + '\n\n' + form)
    if not pairs and bare:
        return (f'Nothing in the repository defines {", ".join(bare[:3])} (library code is not shown here). ' + form
                + '\nThe places: ' + ', '.join(plan_names) + '.')
    if not pairs:
        return 'No place and sentence were read. ' + form + '\nThe places: ' + ', '.join(plan_names) + '.'
    saved, unknown, added, long_ = [], [], [], []
    for name, sentence in pairs:
        hit = _match(state['places'], name, fuzzy=False)
        if hit is None:
            found = [c for c in _resolve(root, [name]) if not _repo.is_doc_path(c['rel'])]
            rel_new = None if found else _new_file_path(root, name)
            if rel_new is not None and not NO_CHANGE.match(sentence):
                # review: a fix that adds a module had no form; a path that does not exist yet is a file to create
                hit = _add_new_file_place(state, rel_new, 'new file (specification)')
                added.append(hit['handle'])
                found = None
            elif not found and os.path.isfile(os.path.join(root, _args.clean(name).split('::')[0])):
                # p2 (requests_7502): ['src/requests/models.py', '<sentence>'] was answered 'Not in the repository'
                listed = _file_names(root, state, _args.clean(name).split('::')[0])
                return ((listed.replace('Send one name to read it.', '') if listed else f'{name} is a file.')
                        + '\nA sentence belongs to a function or class: send it with that name. ' + form)
            elif not found:
                owner = _new_definition_owner(root, name) if not NO_CHANGE.match(sentence) else None
                if owner is None:
                    unknown.append(name)
                    continue
                # fastapi_15800 (h1, h2, s1): the new `APIRouter.frontend` was refused as 'not in the repository' and
                # the specifiers hung it on unrelated places; fastapi_14448 (s1): a new helper _unwrap_chain too. A new
                # method's sentence goes to its class, a new function's to its file's top level: the editor sends the
                # new definition whole and it is added at the end of the class or file.
                c, short = owner
                kind = 'method' if c['name'] != MODULE else 'function or class'
                sentence = f'Add the new {kind} `{short}` (send it whole; it is added at the end): {sentence}'
                found = [c]
        if len(sentence) > MAX_SENTENCE:
            # s1 (fastapi_14448, fastapi_15800): sentences of 649-947 characters were cut at 600 without a word, and
            # the editor never read the end of the change
            long_.append(f'{name} ({len(sentence)} characters)')
            continue
        if hit is None:
            _add_place(state, found[0], 'added by the specification')
            hit = next(q for q in state['places'] if (q['rel'], q['name']) == (found[0]['rel'], found[0]['name']))
            added.append(hit['handle'])
        i = _journal.target(state, hit['id'])
        e = state['plan'][i]
        if NO_CHANGE.match(sentence):
            e['intent'] = 'no change'
            if e['status'] == 'todo':
                e['status'] = 'skipped'
        else:
            old_ = e.get('intent') or ''
            # a new member and a change of its class, or two sentences for one place in one call, are both kept
            more = old_ if _specified(e) and old_ != 'no change' and sentence not in old_ and (
                sentence.startswith('Add the new ') or old_.startswith('Add the new ') or hit['handle'] in saved) else ''
            e['intent'] = (more + ' Also: ' + sentence) if more else sentence
            if e['status'] == 'skipped':
                e['status'] = 'todo'
        saved.append(hit['handle'])
    missing = [_name_of(state, e['place']) for e in state['plan'] if e['status'] == 'todo' and not _specified(e)]
    out = []
    if saved:
        out.append('Saved: ' + ', '.join(saved) + '.')
    if added:
        out.append('Added to the plan: ' + ', '.join(added) + '.')
    if unknown:
        out.append('Not in the repository (nothing saved for them): ' + ', '.join(unknown) + '. A new method is named '
                   "with its class ('<Class>.<new name>'), a new function with its file ('<path>.py::<new name>').")
    if long_:
        out.append(f'Too long, at most {MAX_SENTENCE} characters (nothing saved for them): ' + ', '.join(long_)
                   + '. Send them again shorter: what changes, not how every line is written.')
    if unknown or long_:
        # s1 (fastapi_15800): a refused pair still got 'The specification is complete', and its change was lost
        out.append(form)
        return '\n'.join(out)
    if missing:
        out.append('Still without a sentence: ' + ', '.join(missing) + '. ' + form)
        return '\n'.join(out)
    todo = [(_name_of(state, e['place']), e['intent']) for e in state['plan'] if e['status'] == 'todo']
    if not todo and not state.get('edited'):
        # m9: every place got 'no change'; the plan went back to choosing and each later sentence was refused with
        # 'no places are chosen'. A specification that changes nothing fixes nothing: it is not accepted
        for e in state['plan']:
            if e.get('intent') == 'no change':
                e['intent'], e['status'] = '', 'todo'
        return ('Every place got no change, so the patch would be empty and the issue would stay unfixed. Write what '
                'must change at the place where the behaviour of the issue is decided, or name the code that needs '
                'the change with its sentence (it is added). ' + form + '\nThe places: ' + ', '.join(plan_names) + '.')
    state['spec_done'] = True
    _journal.advance(state)
    out.append('The specification is complete. The places to edit:\n' + '\n'.join(f'  {n}: {t}' for n, t in todo)
               if todo else 'The specification is complete: no place needs a change.')
    out.append('Reply with one sentence for the coordinator, without a call.')
    return '\n'.join(out)


def _editor_gate_closed(root, state):
    """The answer to any call of an editor whose place is closed."""
    mine = state.get('editor_place')
    mine_name = state.get('editor_name') or (_name_of(state, mine) if mine else '')
    nm = mine_name or 'this place'
    q = _place(state, mine) if mine else None
    q = q if q and q.get('handle') == mine_name else None
    changed = q is not None and any(rel_ == q['rel'] and (own_ == q['name'] or own_.startswith(q['name'] + '.'))
                                    for rel_, own_, _, _ in _status.summary(root))
    done = (f'{nm}: the patch changes it as shown before; a later edit was undone.' if changed else
            f'{nm} keeps its code; the change belongs elsewhere or is not needed.')
    return EDITOR_CLOSED.format(name=nm, done=done)


def _editor_tokens(state):
    """The editor's history, estimated: its instruction, every answer of the script (about 3.2 characters a token for
    code) and every reply of the model with its thoughts (kept in the history; about 700 tokens a call measured in
    b10-b11, where an editor reached 31,900 prompt tokens after 40 calls)."""
    # calibrated on nl5 (dumped requests against the server's prompt tokens): the history grows by ~1,450 tokens a
    # call, mostly the model's own thoughts (~3,500 characters a call), the script's answers adding little
    # a larger thinking budget adds to each call: half of the extra budget (thoughts rarely use all of it)
    # fitted on V18's editor calls (the 31B on Kaggle, 451 prompts against the real prompt_tokens): the answers' and
    # the calls' characters predict the history (median error 742 tokens) far better than the call count (1,936; the
    # count refused reads at 6,700 real tokens). The margin is the 90th percentile of the error. The local runner's
    # smaller thinking budget changes little here (the fit has no per-call term to speak of).
    return int(2201 + 12 * state.get('editor_calls', 0) + 0.47 * state.get('editor_chars', 0)
               + 1.106 * state.get('editor_arg_chars', 0)) + 2200


def _editor_open(root, state, name, hit):
    """open.py: a new editor opens the place of its request. The previous editor has replied (its place, if still
    undecided, keeps its code); a name off the places is chosen as the coordinator's choice."""
    # (the previous editor's place stays as it is: b10 showed an editor sending open.py again to look at another
    # place, so an open.py call is not proof that the previous editor ended; a place left to do is still offered)
    if hit is None:
        x = _args.clean(name).strip('/')
        rel = next((r for r in _repo.iter_py(root, docs=True) if _rank.names_file(x, r)), None) \
            if re.fullmatch(r'[\w./-]+\.py', x) else None
        if rel and len(_repo.read_lines(root, rel)) > BIG_FILE:
            # c7: the coordinator handed over 'fastapi/dependencies/models.py'; its top level was opened and the
            # editor read the file by ranges for 7 calls. A long file is opened through one of its definitions.
            listed = _file_names(root, state, name)
            if listed:
                return listed.replace('Send one name to read it.', "Send open.py again with the one name the change "
                                      "needs, to open it.")
        c = _match(state.get('candidates') or [], name, fuzzy=False)
        if c is None:
            found = [x for x in _resolve(root, [name]) if not _repo.is_doc_path(x['rel'])]
            c = found[0] if found else None
        req = state.get('requested')
        if c is None and req and not state.get('requested_used') and (state.get('editor_done')
                                                                     or not state.get('editor_place')):
            # V17: a new editor's first call named 'fastapi.setup.openapi' for the request FastAPI.setup.openapi; the
            # place the coordinator handed over is opened
            hit = _match(state.get('places') or [], req, fuzzy=False)
            if hit is not None:
                return _editor_open(root, state, req, hit)
        if c is None:
            return (f'Nothing in the repository defines {_said(name)}. Reply with one sentence for the coordinator, '
                    'without a call, saying so.')
        if not any((q['rel'], q['name']) == (c['rel'], c['name']) for q in state.get('places') or []):
            _choose_code(root, state, c)
        hit = next((q for q in state['places'] if (q['rel'], q['name']) == (c['rel'], c['name'])), None)
        if hit is None:
            return 'The code of the request could not be opened. Reply with one sentence for the coordinator, saying so.'
    new_editor = state.get('editor_done') or not state.get('editor_place')
    if not new_editor and hit['id'] != state['editor_place']:
        own = next((e for e in state['plan'] if e['place'] == state['editor_place']), None)
        nxt = next((e for e in state['plan'] if e['place'] == hit['id']), None)
        if _narrow(state) and own is not None and own['status'] == 'done' and nxt is not None and \
                nxt['status'] == 'todo':
            # m8: the coordinator called edit_place for is_gen_callable right after the editor of
            # is_async_gen_callable (no status call between); that editor's change is kept and its task is over, so
            # an opening of another place still to do is the next editor's
            new_editor = True
        elif hit['handle'] == state.get('requested') and not state.get('requested_used'):
            new_editor = True       # the place the coordinator was told to hand over: its editor's first call
        else:
            # c5: an open editor sent open.py for the other planned places in turn (8 calls, a full window each, no
            # edit) and every switch reset its context estimate; another place is read like related code, and an
            # edit of it is kept while the editor's own place stays open
            mine_name = state.get('editor_name') or _name_of(state, state['editor_place'])
            if _narrow(state):
                return _narrow_refusal(mine_name, state)
            return _editor_read(root, state, hit, mine_name) + (
                f"\n\nBack to {mine_name}: send its edit, or ['skip'] if it needs no change.")
    if new_editor and hit['handle'] == state.get('requested'):
        state['requested_used'] = True
    if state.get("editor_place") and not new_editor:
        # an editor reopening places to go on thinking (nl3: 14 reopenings, each a full window of ~2,000 tokens)
        used = _editor_tokens(state)
        mine_name = state.get('editor_name') or _name_of(state, state['editor_place'])
        if used > EDITOR_CLOSE_AT:
            return (f'No more code is shown in this editor: its history is near the model\'s context limit. Send the '
                    f'edits {mine_name} still needs with what was shown, or reply with one sentence for the '
                    'coordinator, without a call.')
        if hit['id'] == state['editor_place'] and state.get('editor_done'):
            # c4: a closed place reopened got 'is open already: edit it' while NEXT said no call (B12)
            return _editor_gate_closed(root, state)
        if hit['id'] == state['editor_place'] and state.get('current') is not None:
            # (no close here: nl4 closed the place on the third reopening and the next call was the edit; a reopening
            # is how a reasoning cut by the thinking budget goes on, and the context budget bounds it)
            return (f'{hit["handle"]} is open already: its code was shown above and has not changed. Edit it with '
                    f"what was shown, or send ['skip'] if it needs no change.")
        if used > EDITOR_READS_UNTIL:
            return (f'No more code is shown in this editor: its history is near the model\'s context limit. Edit '
                    f"{mine_name} with what was shown, or send ['skip'] if it needs no change.")
    state['reopen'] = 0
    if not state.get('editor_place') or hit['id'] != state.get('editor_place'):
        # a new editor, or the opening of another place (the script cannot tell them apart: nl5's coordinator called
        # edit_place without status, and the new editor inherited the old count and was cut at 7,000 real tokens): an editor that switches between its places with open.py
        # keeps what it has seen and what its history holds (nl2: the switches reset both, so the context grew to
        # 29,800 tokens unbounded and the same lines were asked for again and again)
        # (the lines already shown are kept: c4's editor switched to a helper and back, and its edit of the place it
        # had read was refused as 'not shown')
        state.update(editor_reads=[], reads_again={}, editor_chars=0, editor_arg_chars=0, editor_calls=0)
        state['recent'], state['repeats'] = [], 0
    state.update(editor_done=False, editor_place=hit['id'], editor_review=False, editor_name=hit['handle'])
    i = _journal.target(state, hit['id'])
    if state['plan'][i]['status'] == 'skipped':
        state['plan'][i]['status'] = 'todo'
    state['current'], state['step'] = i, 'D3'
    return _window(root, state, i)


FILE_NAMES = 6      # functions and classes of a long file listed to an editor that sent the file's path


def _file_names(root, state, text):
    """A repository file sent by an editor (c1: 'fastapi/dependencies/models.py', answered 'Nothing in the repository
    defines' because a file over BIG_FILE lines is not one place): its functions and classes most related to the
    issue, by name, to read one. None when the text names no repository file."""
    x = _args.clean(text).strip('/')
    if not re.fullmatch(r'[\w./-]+\.py', x):
        return None
    rel = next((r for r in _repo.iter_py(root, docs=True) if _rank.names_file(x, r)), None)
    if rel is None:
        return None
    st = _state.load('statement', {})
    cands, _ = _rank_candidates(root, st.get('text', ''), st.get('terms', []), state['requirements'], n=FILE_NAMES,
                                only_rel=rel)
    names = [c['name'] for c in cands if c['name'] != MODULE]
    if not names:
        return None
    return (f'{rel} is too long to show whole. Its functions and classes most related to the issue: '
            + ', '.join(names) + '. Send one name to read it.')


def _is_class(root, c):
    return any(s.name == c['name'] and s.kind == 'class'
               for s in _code.symbols(_code.parse(_repo.read_text(root, c['rel']))))


def _editor_read(root, state, c, mine_name):
    """Related code shown read only to the editor of another place (a05: what resolve_redirects yields decided the edit
    of Session.send)."""
    label = c.get('handle') or (c['name'] if c['name'] != MODULE else f'{c["rel"]} top level')
    key = f'{c["rel"]}:{c["start"]}'
    reads = state.setdefault('editor_reads', [])
    if key in reads:
        again = state.setdefault('reads_again', {})
        again[key] = again.get(key, 0) + 1
        if again[key] >= 2 and label != mine_name:
            # c6: the editor of get_dependant read Dependant 5 times, each answered 'shown above', and neither edited
            # nor replied: a repeated read gets the way out, a ready sentence for the coordinator (F7, B5)
            return (f'{label} was shown above and has not changed. Edit it by name, or, if the change belongs there '
                    f'and not in {mine_name}, reply to the coordinator with this sentence and no call: '
                    f'"The change belongs to {label} ({c["rel"]}), not to {mine_name}."')
        return f'{label} was shown above and has not changed.'
    reads.append(key)
    _state.record({'look': 'editor read', 'q': label[:120], 'step': state['step']})
    body = _code_view(root, c['rel'], c['start'], c['end'], _focus(state), limit=CODE_LINES)   # read whole: what it yields or returns
    if ROLE == 'editor' and c['name'] != MODULE:
        # c3: 'this editor changes X alone; if the change belongs to Y, reply' while edits of related code are kept:
        # two calls cut at 4,096 tokens weighing 'the request names the place' against the fix in the helper (B12)
        # (marked as seen: c3 refused the edit of the code it had read as 'not shown')
        how = (f"edit the member it needs by the member's name (['{label}.<member>', '<the whole new function>'])"
               if c['name'] != MODULE and c['end'] - c['start'] > SHORT_MEMBER and _is_class(root, c) else
               f"edit it by its name (['{label}', '<the whole new function or class>'])")
        return _seen_mark(c['rel'], f'{label} ({c["rel"]}, lines {c["start"]}-{c["end"]}). If the fix belongs here, '
                          f'{how}; the edit is kept and {mine_name} stays open.\n{body}')
    # a file's top level: an import is added there by line numbers (the editor's prompt says so; only editors read)
    return _seen_mark(c['rel'], f'{label} ({c["rel"]}, lines {c["start"]}-{c["end"]}). To add an import, edit '
                      f"it by line numbers (['{label}', '<first line number>', '<last line number>', '<new "
                      f"lines>']); {mine_name} stays open.\n{body}")


OPENING = False     # the call came through open.py: an edit_place editor opens the place of its request


def _run_specifier(root, state, args):
    """A call of the specify_fix sub-agent (spec.py): its own answer, never a traceback."""
    try:
        out = _specifier(root, state, args)
    except Exception as e:      # never a traceback for the model
        _state.record({'error': f'spec {type(e).__name__}: {e}'})
        out = (f'The script hit an internal error ({type(e).__name__}: {str(e)[:200]}). Reply with one sentence '
               'for the coordinator, without a call, saying the specification failed.')
        state['spec_done'], state['spec_failed'] = True, True    # editors then work as before (reading allowed)
    _state.record({'spec': [a[:200] for a in args][:12], 'answer': out[:300]})
    _state.save('journal', state)
    out = out.rstrip()
    head = _progress(root, state)
    if head and not out.startswith(head[:40]):
        out = head + '\n\n' + out
    if len(out) > SPEC_CHARS + 3000:
        out = out[:SPEC_CHARS + 500] + '\n      ... (output cut) ...\n' + out[-2400:]
    print(out + '\n\n' + _journal.next_call(state, ROLE))


def _close_editor(state):
    """The coordinator's call after an edit_place: that editor has replied, so it is closed (also after an editor that
    closed itself: c11's closed editor kept its counts, and the next editor was told 'near the context limit' on its
    first call). The script cannot tell one edit_place invocation from the next (a07, where an editor still open made
    the next editor's opening call read as a read of related code). A place it left undecided keeps its code (its
    sentence says why); an accepted edit under review is kept."""
    i = _journal.plan_entry(state, state['editor_place'])
    if i is not None and state['plan'][i]['status'] == 'todo' and not state.get('editor_done'):
        state['plan'][i]['status'] = 'skipped'
    state['editor_review'], state['editor_done'] = False, True
    state.update(editor_place=None, editor_reads=[], reads_again={}, seen={}, editor_chars=0, editor_arg_chars=0,
                 editor_calls=0,
                 recent=[], repeats=0)
    if state['step'] == 'D3':
        _journal.advance(state)


def _normalized(state, args, key):
    """(args, key) with the shapes the model sends for a range made into the usual items."""
    if args and re.fullmatch(r'\s*[\w./-]+\.py:\d+(-\d+)?\s*', str(args[0] or '')):
        # 'src/requests/sessions.py:160' followed by two numbers, or 'file.py:676-751' alone (a13, a10): a range
        m = re.fullmatch(r'\s*([\w./-]+\.py):(\d+)(?:-(\d+))?\s*', str(args[0]))
        rest = [x for x in args[1:] if x and str(x).strip()]
        if len(rest) >= 2:
            args = [m.group(1)] + list(args[1:])
        elif not rest and m.group(3):
            args = [m.group(1), m.group(2), m.group(3)]
        key = _journal._key(state, args)
    if state['step'] in ('D3', 'D4'):
        # a range in one item ('722,735', '722-735', run a04: 5 refusals) is the two line numbers
        flat = [x for x in args if x and x.strip()]
        joined = re.fullmatch(r'\s*(\d+)\s*[,-]\s*(\d+)\s*,(.+)', str(flat[1]), re.S) if len(flat) == 2 else None
        if joined:
            # a10: ['<place>', '174,281,<new code>'], the range and the code in one item
            args = [flat[0], joined.group(1), joined.group(2), joined.group(3).lstrip('\n')]
            key = _journal._key(state, args)
            flat = []
        if len(flat) == 2:
            m = re.fullmatch(r'\s*(\d+)\s*(?:,|-|:|\.\.|–|to)\s*(\d+)\s*', _args.clean(str(flat[1])))
            if m:
                args = [flat[0], m.group(1), m.group(2)]
                key = _journal._key(state, args)
    return args, key


def _range_guard(state, args, key, skip):
    """(skip, reread, range_key) for a range read ([name, first, last])."""
    nums = [x for x in args if x and x.strip()]
    reread = None
    range_key = None
    if len(nums) == 3 and all(_args.clean(str(x)).isdigit() for x in nums[1:3]):
        # a range read again never moves on (the third repeat used to mark the open place skipped, undecided: local
        # run of requests_7328), but it is not shown again either: the editor of the agent_tool variant read the same
        # 14 lines of Session.send 4 times in a context of 8k tokens, so the lines were there; the answer says so
        # and asks for the decision (F7: a repeat gets a different answer)
        seen = state.setdefault('ranges_shown', [])
        # exempt from the repeat guard only when these lines were really shown before (b11: a range refused as an
        # unknown form came 5 times, each answered the same, because every range was exempt)
        skip = key in seen
        # only lines really shown before (not a status, not "do not exist"), only where an edit is the next call
        # (docs/feedback_audit.md #7: the claim was false for the coordinator and contradicted NEXT in D4)
        if key in seen[-6:] and ROLE == 'single' and state['step'] == 'D3':
            # (not in the editor: there a re-read is how a reasoning cut by the thinking budget goes on, and its
            # context is fresh; the agent_tool trace of requests_7328 found the bug on the fourth read)
            reread = (f'Lines {nums[1]}-{nums[2]} were shown a few calls ago and have not changed since: edit them, '
                      'or send [\'skip\'] if this place needs no change.')
        elif ROLE == 'editor' and state['step'] == 'D3' and seen[-6:].count(key) >= 2:
            # a06: the editor of Session.send asked for the same 14 lines again and again ("I'm repeating myself"),
            # each time cut by the thinking budget before deciding; a re-read is still how a cut reasoning goes on,
            # but the third identical one gets a different answer and the fourth moves the work on (F7)
            mine = _name_of(state, state.get('editor_place')) if state.get('editor_place') else 'this place'
            if seen[-6:].count(key) >= 3:
                reread = 'MOVE_ON'
            else:
                reread = (f'Lines {nums[1]}-{nums[2]} were shown in the last answers and have not changed. Decide with '
                          f'what they show: edit them, or send [\'skip\'] if {mine} needs no change.')
        range_key = key
    return skip, reread, range_key


def _repeat_answer(root, state, args):
    """The answer to a call repeated in the last three: answered differently each time and the third time the work
    moves on (structured variation; a repeated failure in the context makes the model repeat it: Feedback That
    Backfires); repeats never count as failed edits."""
    before = str(state.get('repeated_answer') or '')
    pargs = _place_args(state, args) if state['step'] in ('D3', 'D4') and state.get('places') else args
    is_edit = _args.edit(pargs) is not None or _whole_args([x for x in pargs if x and x.strip()]) is not None
    if is_edit and before.startswith(('Kept', 'OK', 'APPLIED')):
        out = 'This edit is already in the patch, so nothing was changed.\n\n' + _current_view(root, state)
    elif state['repeats'] >= 2 and ROLE == 'coordinator' and state['step'] == 'D3':
        # (m3: the move-on skipped an unspecified place; the coordinator does not edit, so nothing is stuck there:
        # it is told the next call again)
        out = ('This call came three times and was not run. ' + _coordinator_status(root, state))
    elif state['repeats'] >= 2:
        out = _move_on(root, state)
    elif is_edit and (state.get('failed_edits') or {}).get(_edit_key(args)):
        # a05: the undone edit sent again got "same call", and the model sent it a third time (RLEF: models resend
        # the same code; the answer says the same text fails again)
        handle, fails = state['failed_edits'][_edit_key(args)][:2]
        out = (f'This exact edit of {handle} was made before and undone: these existing tests failed after it: '
               f'{", ".join(fails) or "(see before)"}. It was not run again: the same text fails the same way. The '
               'code is as it was before that edit.\n\n' + _current_view(root, state))
    else:
        out = ('This is the same call as one of the last three, so it was not run again'
               + (f'; its answer began: {before[:160].rstrip(".")}' if before else '') + '.\n\n' + _current_view(root, state))
    return out


def _scoped_answer(root, state, args, items0):
    """The answer of a call the role or the step answers before the step's own function (a status, the coordinator's
    calls around the specification), else None."""
    scoped = None
    if len(items0) == 1 and _args.clean(items0[0]).lower().strip('[]\'" ') in (
            'status', 'state', 'next'):
        # the coordinator's call after each edit_place: where the work is (never a code name: 'status' chose
        # rich's Console.status in a dry run)
        scoped = (_coordinator_status(root, state) if ROLE == 'coordinator' and state['step'] == 'D3'
                  else _current_view(root, state))
    if scoped is None and ROLE == 'coordinator' and state['step'] == 'D3' and not state.get('spec_done') \
            and items0 and not (len(items0) == 1 and (_match(state.get('places') or [], _args.clean(items0[0]),
                                                                 fuzzy=False) or _args.plan(args) == 'back')):
        # m3, m4: before the specification the coordinator is still choosing (names, files, searches were
        # refused 5-7 times): the call is answered as at the choose step, and a choice makes the plan again
        if len(items0) == 4 and all(_args.clean(x).isdigit() for x in items0[1:3]) or \
                len(items0) == 2 and DEF_LINE.search(items0[1]):
            # (review: a coordinator's edit went through d1 to d3 and was applied; editors edit)
            scoped = ('This call was not run: the coordinator does not edit. specify_fix writes what changes '
                      'at each place and each place is edited by its editor.\n\n'
                      + _coordinator_status(root, state))
        else:
            keep = {k: json.loads(json.dumps(state.get(k))) for k in ('places', 'plan', 'current', 'step')}
            _journal.back(state)
            scoped = d1(root, state, items0)
            if state['step'] != 'D3':
                # a search, a file or a name that found nothing: the plan stays as it was (review: a failed
                # search dropped it), and the answer is the list to choose from
                cands = state.get('candidates')
                state.update(keep)
                state['candidates'] = cands
                scoped = scoped.replace('\n\nChoose the code to change.', '') + (
                    '\n\nThe plan is unchanged. Send a name from this list to choose it instead, or go on with '
                    'specify_fix.')
    if scoped is None and ROLE == 'coordinator' and state['step'] in ('D3', 'D4') and len(items0) == 1 \
            and _args.plan(args) != 'back':
        scoped = _coordinator_choice(root, state, items0[0])
    if scoped is None and ROLE == 'coordinator' and state['step'] in ('D3', 'D4') and state.get('spec_done') \
            and 1 < len(items0) <= 4 and all(NAME_LIKE.fullmatch(_args.clean(x)) for x in items0):
        # (review: after the specification several names were refused; each joins the plan)
        got = [_coordinator_choice(root, state, x) for x in items0]
        got = [g.split('\n\n')[0] for g in got if g]
        if got:
            scoped = '\n'.join(got) + '\n\n' + _coordinator_status(root, state)
    if scoped is None and ROLE == 'coordinator' and state['step'] == 'D3' and _args.plan(args) != 'back' \
            and (state.get('spec_done') or not _search_query(items0) and not
                 (len(items0) == 2 and re.fullmatch(r'[\w./-]+\.py', _args.clean(items0[1]).strip('/')))):
        # (before the specification a search or ['<text>', '<file>'] goes on choosing: m1's coordinator chose
        # _solve_generator and was refused 5 searches with the old 'its editor reads it')
        # a04, a05, a07: the coordinator asked to see the code before delegating and got the same status
        # each time (F7); the answer says why it is not shown (P3: the rule with its reason)
        left = state['plan'][state['current']]['place'] if state.get('current') is not None else None
        scoped = ((f'This call was not run: the code of {_name_of(state, left)} is read by specify_fix, which '
                   'sees the code of every place and writes what changes at each, and then by its editor.\n\n'
                   if left and not state.get('spec_done') else
                   f'This call was not run: {_name_of(state, left)} is edited by its editor from its '
                   'specification.\n\n' if left else '')
                  + _coordinator_status(root, state))
    return scoped


def _answer(root, state, args, reread):
    """The answer of a call that is not a repeat: a guard of the role or of the call's shape, else the step's own
    function."""
    items0 = [a for a in args if a and a.strip()]
    scoped = _scoped_answer(root, state, args, items0)
    state['edit_key'] = _edit_key(args) if len(items0) >= 2 else None
    seen_fail = (state.get('failed_edits') or {}).get(state['edit_key']) if state['edit_key'] else None
    seen_fail = None if seen_fail and len(seen_fail) > 2 and seen_fail[2] else seen_fail
    if reread == 'MOVE_ON':
        read = _move_on(root, state).replace('The same call came three times', 'The same lines were asked for '
                                             'four times')
    elif reread is not None and state['step'] in ('D3', 'D4'):
        read = reread
    elif scoped is not None:
        read = scoped
    elif items0 and all(re.sub(r'[\W_]', '', _args.clean(a).lower()) in ('submitpatch', 'submit', 'submitpatchtool')
                      for a in items0):
        if state['step'] == 'D4':
            read = ('submit_patch is a tool of its own, not an argument of this script, so nothing was done. To '
                    'submit, call the tool submit_patch with no arguments, instead of run_skill_script.\n\n'
                    + _finish_view(root, state))
        else:
            # text and NEXT agree: submit_patch comes when the work reaches the finish (docs/feedback_audit.md #6)
            read = ('submit_patch is a tool of its own, not an argument of this script, so nothing was done. '
                    'This step is still open: make the call in NEXT; the finish offers submit_patch.\n\n'
                    + _current_view(root, state))
    elif seen_fail and state['step'] in ('D1', 'D3', 'D4'):
        # local run of requests_7328: the same failing edit was sent 5 times, each one run with its tests
        # (about 20 s) and undone by the same 3 failures (F6: a third try of the same thing gains nothing)
        handle, fails = seen_fail[:2]
        read = (f'This exact edit of {handle} was made before and undone: these existing tests failed after it: '
                f'{", ".join(fails) or "(see before)"}. It was not run again: the same text fails the same '
                f'way. The code is as it was before that edit.\n\n' + _current_view(root, state))
    elif state['step'] in ('D1', 'D3', 'D4') and len(items0) == 1 and _is_code_to_run(items0[0]):
        _state.record({'look': 'run', 'q': items0[0][:120], 'step': state['step']})
        out = RUN_NOTE + '\n\n' + _current_view(root, state)
        read = out
    else:
        read = _range_request(root, state, args) if state['step'] in ('D1', 'D3', 'D4') else None
    out = read if read is not None else STEPS[state['step']](root, state, args)
    return out


def main(argv, role=None, opening=False):
    global ROLE, OPENING
    if role:
        ROLE = role
    OPENING = opening
    root = _repo.repo_root()
    if not _state.lock():
        print('This call was not run: the call sent before it (or with it) is still running, and two calls at once '
              'would change the same files over each other. Make one call at a time: send this one again when the '
              'other has answered.')
        return
    state = _state.load('journal') or _journal.new()
    # the tests and probes this call runs end before the task's time does (audit 10-18 #10; eval_config.yaml)
    _tests.DEADLINE = max(time.time() + 5, state.get('t0', time.time()) + TASK_S - 10)
    if state['step'] == 'D2':
        _journal.back(state)        # (D2 is only left by a crash between choosing and planning: choose again)
    _SEEN['state'] = state
    argv = list(argv)
    while len(argv) > 1 and argv[-1].strip().lower() in ('false', 'true', 'none'):
        argv.pop()              # bare booleans the model appends to a list (seen after a runaway) are not items
    argv = _args.split_single_quoted(argv)
    args = _args.unpack(argv) if state['step'] in ('D1', 'D2', 'D4') else _args.split_escaped(argv)
    if ROLE == 'specifier':
        _run_specifier(root, state, args)
        return
    if ROLE != 'editor' and state.get('editor_place'):
        _close_editor(state)
    _state.record({'step': state['step'], 'args': [a[:300] for a in args]})
    state['calls'] = state.get('calls', 0) + 1
    key = _journal._key(state, args)          # the call as it is now, before the step changes the state
    skip = state['step'] == 'D3' and len(args) == 1 and _is_skip(args[0])     # one skip per place: not a repeat
    if state['step'] == 'D3' and len(args) == 1 and not skip and state['current'] is not None:
        # opening another listed place is a move, not a repeat
        target = _listed_place(state, args[0])
        skip = target is not None and target != state['plan'][state['current']]['place']
    if any(re.sub(r'[\W_]', '', _args.clean(x).lower()) in ('submitpatch', 'submit') for x in args if x):
        skip = True
    if args and re.fullmatch(r'\s*[\w./-]+\.py:\d+(-\d+)?\s*', str(args[0] or '')) or state['step'] in ('D3', 'D4'):
        args, key = _normalized(state, args, key)
    if ROLE == 'editor' and len([x for x in args if x and x.strip()]) == 1 and state.get('places'):
        # an editor's first call names its place, often the coordinator's last call word for word (run a04: it was
        # answered as a repeat, so the editor never got its place and its name printed empty)
        hit = _match(state['places'], _args.clean(args[0]), fuzzy=False)
        if hit is not None and (state.get('editor_done') or state.get('editor_place') != hit['id']):
            skip = True
    skip, reread, range_key = _range_guard(state, args, key, skip)
    if state['step'] == 'D3' and state.get('pending_delete') and len(args) >= 4:
        e = _args.edit(_place_args(state, args))
        skip = skip or (e is not None and [e[0], e[1], e[2]] == state['pending_delete'])   # the confirmation
    # c3: the unseen-lines answer asks for the same edit again; the repeat guard refused it ('same call')
    skip = skip or str(state.get('answer') or '').startswith('Not applied yet')
    restate = _more_statement(state, args)
    resent = state['step'] != 'S0' and not restate and _is_statement_again(args)
    gate = None
    if ROLE == 'editor':
        try:
            gate = _editor_gate(root, state, args)
        except Exception as e:      # never a traceback: the ordinary path answers
            _state.record({'error': f'gate {type(e).__name__}: {e}'})
    if gate is not None:
        out = gate
        state['answer'] = out.strip().split('\n')[0][:300]
    elif restate:
        st = _state.load('statement', {})
        known, sent = st.get('text', ''), args[0]
        norm = lambda t: re.sub(r'\W+', ' ', t).strip().lower()
        text = sent if norm(known)[:40] and norm(sent).startswith(norm(known)[:40]) else known + '\n\n' + sent
        out = ('The requirements and candidates are made again from the whole statement.\n\n'
               + s0(root, state, [text] + st.get('terms', []) + list(args[1:])))
        state['answer'] = out.strip().split('\n')[0][:300]
    elif resent:
        # after the harness compacts the history the model may start again: say where the work is
        out = ('The start was already made: the requirements and candidates are known and the work is in a later '
               'step. Continue from here (nothing was changed).\n\n' + _current_view(root, state))
    elif state['step'] != 'S0' and not skip and _journal.repeated(state, args) and not _assert_resend(state, args):
        # a repeated call is answered differently each time and the third time the work moves on (structured
        # variation; a repeated failure in the context makes the model repeat it: Feedback That Backfires); repeats
        # never count as failed edits
        out = _repeat_answer(root, state, args)
    else:
        try:
            out = _answer(root, state, args, reread)
        except Exception as e:      # never a traceback for the model: say what happened and the call to make
            _state.record({'error': f'{type(e).__name__}: {e}'})
            # the state as it was before the call: a half-made change (a skip, a plan dropped) is not kept
            state = _state.load('journal') or state
            _SEEN['state'] = state
            if state['step'] in ('D3', 'D4'):
                try:
                    _tests.restore_verified(root)     # (review: an edit written before the error stayed on disk)
                except Exception:
                    pass
            out = (f'The script hit an internal error ({type(e).__name__}: {str(e)[:200]}). '
                   + ("Nothing was changed. Reply with one sentence for the coordinator, without a call, saying so."
                      if ROLE == 'editor' else 'Make the call below; if it fails the same way, call submit_patch.'))
        state['answer'] = out.strip().split('\n')[0][:300]
        _journal.remember(state, key, state['answer'])
        if range_key is not None and out.startswith('Lines ') and ' do not exist' not in out.split('\n')[0]:
            state.setdefault('ranges_shown', []).append(range_key)
            del state['ranges_shown'][:-6]
    if ROLE == 'editor':
        if not OPENING:
            state['reopen'] = 0         # reopenings count only in a row
        state['editor_chars'] = state.get('editor_chars', 0) + len(out)
        state['editor_arg_chars'] = state.get('editor_arg_chars', 0) + len(json.dumps(list(args)))
        state['editor_calls'] = state.get('editor_calls', 0) + 1
    nxt = _journal.next_call(state, ROLE)      # (before the save: it records the place handed over, 'requested')
    _state.save('journal', state)
    out = out.rstrip()
    if state['step'] != 'S0':
        out = _progress(root, state) + '\n\n' + out
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS - 2500] + '\n      ... (output cut) ...\n' + out[-2400:]
    print(out + '\n\n' + nxt)


if __name__ == '__main__':
    main(sys.argv[1:])
