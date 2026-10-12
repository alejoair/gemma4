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
import os
import re
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _args  # noqa: E402
import _code  # noqa: E402
import _edit  # noqa: E402
import _impact  # noqa: E402
import _journal  # noqa: E402
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

N_CANDIDATES = 10
MAX_PLACES = 8
CODE_LINES = 120        # numbered lines shown for one place; a longer place shows its best-matching part
EDIT_LINES = 32         # the edit window: a place up to EDIT_LINES + 30 lines is shown whole, a longer one
                        # as its head and the part the requirements are about
CLASS_OUTLINE_AT = 90   # a chosen class longer than this is shown as an outline of its members
MODULE = '<module>'
MAX_CHARS = 7000        # a cap on one answer (about 2,000 tokens) against context growth; the end with NEXT is kept


# ---------------------------------------------------------------------------------------------------------- views

def _code_view(root, rel, start, end, focus_terms=None, limit=CODE_LINES, show=None):
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
    sig_end = None
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body and \
                min([node.lineno] + [d.lineno for d in node.decorator_list]) == start:
            sig_end = node.body[0].lineno - 1
            break
    if sig_end is not None and sig_end - start + 1 > 8 and not (show and show[0] <= sig_end):
        # a long signature (FastAPI's Annotated[..., Doc(...)] parameters): its first line, then the body, so that
        # the window shows the code that runs (V10 audit #8: windows showed Doc texts and hid the body)
        out = [_code.numbered(lines, start, start, collapse=collapse),
               f'      ... (the parameters, lines {start + 1}-{sig_end}, not shown) ...']
        body = _code_view(root, rel, sig_end + 1, end, focus_terms, limit=limit - 2, show=show)
        return '\n'.join(out + [body])
    width = limit - size(start, head)
    toks = focus_terms or {}
    score = [0] * (end + 2)
    for i in range(head + 1, end + 1):
        if toks and i not in prose and not lines[i - 1].lstrip().startswith('#'):
            score[i] = sum(toks.get(t, 0) for w in re.findall(r'[A-Za-z_]\w*', lines[i - 1]) for t in _rank.tokens(w))
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
    _refresh(root, state, p['rel'])
    p = _place(state, entry['place'])
    goal = _goal(state, p)
    head = f'Now edit {p["handle"]} ({p["rel"]}; place {i + 1} of {len(state["plan"])}).'
    owner = _owner_class(root, p)
    if owner:
        head += f' It is a method of {owner}.'
    if p['reason'] != 'chosen':
        head += f" Related to the chosen code ({p['reason']}): change it only if the goal needs it here, else ['skip']."
    head += f'\nGoal: {goal}'
    for line in _deferred_lines(state):
        head += f'\n{line}'
    if p['name'] == '<exports>':
        n = len(_repo.read_lines(root, p['rel']))
        body = _code_view(root, p['rel'], 1, n, _focus(state, entry))
    else:
        body = _code_view(root, p['rel'], p['start'], p['end'], _focus(state, entry), limit=EDIT_LINES, show=show)
        if re.search(r'\(lines \d+-\d+ not shown\)', body):
            body += (f'\n(Not all of {p["handle"]} is shown: send it with two line numbers inside it to see other '
                     'lines; change the lines shown by their numbers, or rewrite a member whole.)')
        elif '(the parameters, lines' in body:
            body += f'\n(The parameters of {p["handle"]} are not shown: change its body by line numbers.)'
        members = _members(root, p)
        if members:
            body = members + '\n' + body
    here = _patch_here(root, p)
    parts = [head, here, body] if here else [head, body]
    context = _context_view(root, p, _focus(state, entry))
    if context:
        parts.append(context)
    test = _test_once(root, state, p)
    if test:
        parts.append(test)
    return _seen_mark(p['rel'], '\n'.join(parts))


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

    def module_of(rel):
        mod = rel[:-3].replace('/', '.')
        return mod[4:] if mod.startswith('src.') else mod

    def resolve(name, how):
        found = table.find(name)
        if how == 'self':
            own_cls = [(r, x) for r, x in found if r == p['rel'] and cls and x.name == f'{cls}.{name}']
            if own_cls or not cls:
                return own_cls
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

    def weight(item):
        name, how = item
        shared = sum(focus.get(t, 0) for t in _rank.tokens(name))
        if how in ('self', 'attr'):
            return 10 + shared              # members and properties the place reads: its direct dependencies
        return shared

    # code that shares words with the requirements first; at most 2 that share none (near-miss context costs more than
    # it gives: Chroma, P7)
    order = sorted(dict.fromkeys((n, h) for _, n, h in sorted(used)), key=lambda it: -weight(it))
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
            if mine and x.end - x.start + 1 <= 6:
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
            if chars + len(text) > USES_CHARS:
                break
            uses.append(text)
            chars += len(text)
        if len(uses) >= USES:
            break
    lib = _library_uses(root, p, tree, used, imported, modules, table, cls)
    room = USES_CHARS - chars
    for text in lib:
        if len(text) > room:
            break
        uses.append(text)
        room -= len(text)
    short = own.split('.')[-1]
    callers = [] if short.startswith('__') else [
        (r, c, n) for r, c, n in table.calls.get(short, []) if c and (r, c) != (p['rel'], own)
        and not _repo.is_doc_path(r)]
    callers.sort(key=lambda c: (c[0] != p['rel'], c[0], c[2]))
    calls = []
    for r, c, n in callers[:CALLERS]:
        text = _repo.read_lines(root, r)[n - 1].strip()
        calls.append(f'  {r}:{n} in {c}: {text[:80]}')
    out = []
    if uses:
        out.append(f'Code that {short} uses:\n' + '\n'.join(uses))
    if calls:
        more = f' ({len(callers)} calls, first {CALLERS})' if len(callers) > CALLERS else ''
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
                doc = next((d for d in index.docs if (d.rel, d.name) == owner), None)
                if doc:
                    seen.add(owner)
                    out.append(_candidate(index, doc, [], note=f'where the new `{new}` would go', root=root,
                                          literals=literals, toks=toks))
    ranked = index.rank(terms, _statement.paths(text) + mpaths, n=len(index.docs) if only_rel else n + len(out))
    for _, doc, matched in ranked:
        if only_rel and doc.rel != only_rel or within and not doc.name.startswith(within + '.'):
            continue
        if (doc.rel, doc.name) not in seen and len(out) < n:
            seen.add((doc.rel, doc.name))
            out.append(_candidate(index, doc, matched, root=root, literals=literals, toks=toks))
    for i, c in enumerate(out, 1):
        c['id'] = f'C{i}'
    _set_handles(out)
    return out, index


# ---------------------------------------------------------------------------------------------------------- steps

def s0(root, state, args):
    items = [a for a in args if a and a.strip()]
    if not items or len(items[0].strip()) < 10:
        return 'The first call copies the issue statement. Nothing was searched yet.'
    statement = items[0]
    if '\n' not in statement.strip() and '\\n' in statement:
        # written with escaped line breaks (V10: the requirements then held 'nThis', 'nShould')
        statement = statement.replace('\\r\\n', '\n').replace('\\n', '\n')
    # any copy is accepted, even one line: copying long text exactly is what models do worst (B2), refusing it cost a
    # call in 5 of 10 V10 runs, and the whole statement is in the system instruction anyway
    extra = [t.strip() for a in items[1:] for t in re.split(r'[\n,;]', a) if t.strip()]
    text = _statement.clean(statement) or statement
    _state.save('statement', {'text': text, 'terms': extra})
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
              'changed code ("Kept and checked" means they passed); to see how a test uses some code, send the test '
              'file with two line numbers.')


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
    for rel in _repo.iter_py(root, tests=True, docs=True):
        if only and rel != only:
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
    docs = {(d.rel, d.name): d for d in index.docs}
    order = sorted(groups, key=lambda k: (_repo.is_doc_path(k[0]), k[0] not in near, -len(groups[k]), k[0],
                                          groups[k][0]))
    out = []
    for key in order:
        doc = docs.get(key)
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
    docs = {(d.rel, d.name): d for d in index.docs}
    literals = [x for x in dict.fromkeys(re.findall(r'`([^`\n]{4,60})`', text)) if len(x) >= 4]
    toks = {}
    for term in _statement.terms(text):
        for ident in re.findall(r'[A-Za-z_]\w*', term):
            for t in _rank.tokens(ident):
                if len(t) >= 4 and t not in _statement.STOP:
                    toks[t] = 1
    out = [_candidate(index, docs[k], [], root=root, literals=literals, toks=toks) for k in keys[:N_CANDIDATES]
           if k in docs]
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
            doc = next((d for d in _rank.Index(root).docs if d.sym is None and _rank.names_file(top.group(1), d.rel)),
                       None)
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
        if rel:
            found = [(r, s) for r, s in found if _rank.names_file(rel, r)] or found
        found.sort(key=lambda x: (_repo.is_doc_path(x[0]), x[0]))
        for r, s in found[:1]:
            out.append({'rel': r, 'name': s.name, 'start': s.start, 'end': s.end, 'line': ''})
        if not found and len(raw) >= 4 and not rel and not re.fullmatch(r'[A-Za-z_][\w.]*', raw):
            # a code fragment (not a bare name the repository does not define, such as a library's): the function or
            # class that contains it
            hit = _find_text(root, table, raw)
            if hit:
                out.append(hit)
    return out


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
    hits = [(rel, s) for rel, syms in table.syms.items() for s in syms if s.name.lower() == low]
    return hits[:1]


def _find_text(root, table, text):
    """The innermost function or class whose lines contain text literally (package code before docs and scripts)."""
    needle = re.sub(r'\s+', ' ', text.strip())
    for rel in sorted(table.syms, key=lambda r: (_repo.is_doc_path(r), r)):
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
        return TESTS_NOTE + '\n\n' + _candidates_view(state) + '\n\nChoose the code to change.'
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
    places = places[:MAX_PLACES]
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
        out.append(f'Places (the chosen code and the code related to it):\n{places_view}\nEach place is edited by the '
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
    if ROLE == 'editor' and state['step'] == 'D3' and state.get('editor_place'):
        # c2 (fastapi_14448): the editor of is_coroutine_callable edited Dependant._unwrapped_call, the helper its place
        # reads; choosing it replaced the plan (the planned siblings were dropped) and left the editor on a place id
        # that no longer existed (TypeError). An editor's edit of related code adds that code as a place and keeps
        # the plan; _after_edit then brings the editor back to its own place.
        _add_place(state, found[0], f'edited by the editor of {_name_of(state, state["editor_place"])}')
        return d3(root, state, args)
    head, _ = _choose_code(root, state, found[0])
    if state['step'] != 'D3':
        return None
    return head + '\n\n' + d3(root, state, args)


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
        hit = next((x for x in syms if q and x.name == q), None)
        if hit:
            return hit
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
                inner = own.kind == 'class' and not isinstance(d, ast.ClassDef)
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
    rels = sorted(_repo.iter_py(root, tests=True, docs=False),
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


def _library_def(root, dotted, depth=0):
    """(file, start, end, qualified name) of a library definition from its dotted path, following re-exports; None
    when it is not found or lives in the repository."""
    import importlib.util
    parts = dotted.split('.')
    for k in range(len(parts) - 1, 0, -1):
        module, attrs = '.'.join(parts[:k]), parts[k:]
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError, AttributeError):
            spec = None
        if spec is None or not spec.origin or not spec.origin.endswith('.py'):
            continue
        path = os.path.realpath(spec.origin)
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
                                    base = module if spec.submodule_search_locations else module.rsplit('.', 1)[0]
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


def d3(root, state, args):
    """The edit step (SOP-Agent: only the valid actions of the step). Accepted: an edit of a listed place (its whole
    new function or class, or a line range), ["skip"], ["back"] and a listed place's name (opens it). Questions
    are not answered: the window already holds the code the place uses and the code that calls it (Agentless: the
    model never asks for code). Anything else is refused and changes nothing; only failed edits count against a
    place."""
    if _args.plan(args) == 'back':
        _journal.back(state)
        return 'The edits made so far stay. ' + _candidates_view(state) + '\n\nChoose the code to change.'
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
        return TESTS_NOTE + '\n\n' + (_window(root, state, cur) if cur is not None else _finish_view(root, state))
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
                   "number>', '<last line number>', '<new lines>'], ['skip'] if the place needs no change, or the name "
                   "of related code to read it. Nothing was changed.")
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
    verdict, detail, new_failures = _tests.check(root, changed, renamed, cover={p['rel']: cover} if cover else None)
    notes = ''.join(f'\n  note: {x}' for x in r.repairs + r.warnings)
    shown = _changed_view(before, _repo.read_lines(root, p['rel']), p['rel'])
    if verdict == 'BROKEN':
        _remember_failed(state, p, r, new_failures)
        _tests.restore_verified(root)
        _refresh(root, state, p['rel'])
        n = len(new_failures)
        return _failed(root, state, i, f'Undone: {n} existing test{"s" if n > 1 else ""} that passed before the edit '
                       f'failed after it, so {p["handle"]} is back as it was before the edit.\n{detail}\n'
                       f'Send {p["handle"]} again with a change that keeps {"these tests" if n > 1 else "this test"} '
                       'passing.', 'BROKEN ' + ' '.join(new_failures[:3]))
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
    what = 'Kept and checked' if verdict == 'OK' else 'Kept, not checked'
    head = f'{what}: {p["handle"]} lines {r.start}-{r.end} changed; {detail}.{notes}\n{shown}'
    move = _journal.edit_done(state, i)
    if ROLE == 'editor' and state.get('editor_place') and state['plan'][i]['place'] != state['editor_place']:
        j = _journal.target(state, state['editor_place'])
        if state['plan'][j]['status'] == 'todo':
            # an edit of other code by this editor is kept; its own place is still to decide
            state['current'], state['step'] = j, 'D3'
            return head + f'\n\nBack to {_name_of(state, state["editor_place"])}:\n\n' + _window(root, state, j)
    if ROLE == 'editor':
        # the place stays open with the diff: run a04 (requests_7328) saw its own wrong deletion in this diff and
        # sent the right fix, which a closed editor refused 5 times (F1: told where, it corrects well)
        state['current'], state['step'], state['editor_review'] = i, 'D3', True
        return (head + f'\n\nIf this is the whole change {p["handle"]} needs, reply with one sentence for the '
                'coordinator, without a call. If it is wrong or incomplete, send the corrected edit of the code '
                'below (the code as it is now).\n\n' + _window(root, state, i))
    if move == 'next':
        return head + '\n\n' + _window(root, state, state['current'])
    return head + '\n\n' + _finish_view(root, state)


def _edit_key(args):
    """The identity of an edit: its items without spacing differences."""
    return '\x00'.join(re.sub(r'\s+', ' ', str(a)).strip() for a in args if a is not None and str(a).strip())


def _remember_failed(state, p, r, failures):
    """An edit the tests undid, kept with the tests it broke, so that the same edit sent again is answered at once."""
    key = state.get('edit_key')
    if key:
        failed = state.setdefault('failed_edits', {})
        failed[key] = [p['handle'], failures[:3]]
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
    message = re.sub(r'\nSend \S+ again with a change that keeps (these tests|this test) passing\.', '', message)
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
        return any(rel == q['rel'] and (name == q['name'] or name.startswith(q['name'] + '.')) for rel, name in changed)
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
        out.append('      ' + ('; '.join(facts) + '.' if facts else 'nothing here can be checked automatically.'))
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
        move = _journal.skip(state, state['current'])
        done = state['plan'][state['current']]['status'] == 'done'
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
    try:
        return _status.progress_line(root, now, left)
    except Exception as e:      # the recap must never cost the answer
        _state.record({'error': f'progress: {type(e).__name__}: {e}'})
        return ''


def _current_view(root, state):
    if state['step'] == 'D3' and state['current'] is not None:
        return _window(root, state, state['current'])
    if state['step'] == 'D4':
        return _finish_view(root, state)
    return _requirements_view(state) + '\n\n' + _candidates_view(state) + '\n\nChoose the code to change.'


def _coordinator_status(root, state):
    """The coordinator's answer while places are left: which, and that edit_place edits them."""
    left = [_name_of(state, e['place']) for e in state['plan'] if e['status'] == 'todo']
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
        if state['plan'][i]['status'] != 'done':
            state['plan'][i]['status'] = 'todo'
        state['current'], state['step'] = i, 'D3'
        return f'{hit["handle"]} is planned and is next.\n\n' + _coordinator_status(root, state)
    found = [c for c in _resolve(root, [name]) if not _repo.is_doc_path(c['rel'])]
    if not found:
        return None
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
        state.update(editor_done=False, editor_place=hit['id'], editor_review=False, editor_reads=[], seen={},
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
                state.update(editor_done=False, editor_place=hit['id'], editor_review=False, editor_reads=[], seen={},
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
        return ('This editor opens its place first: send the place name from the request, alone. The places: '
                + ', '.join(q['handle'] for q in places) + '.')
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
EDITOR_READS_UNTIL = 22000  # estimated history tokens after which no more code is shown to the editor
EDITOR_CLOSE_AT = 27000     # ... after which the place is closed (the next requests would not fit)
# the editor's thinking_budget (variants/agent_tool/sub_agents/sampling.yaml); local runs set the server's (c8: the
# local server's 512 against this 2,048 closed an editor at 18,000 real tokens)
# (the harness gives scripts a clean environment, so the local runner writes it to a file; Kaggle has none: 2,048)
def _thinking_budget():
    try:
        with open('/tmp/swe_editor_thinking') as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 2048


EDITOR_THINKING = _thinking_budget()


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
    return 1600 + state.get('editor_calls', 0) * (1450 + (EDITOR_THINKING - 512) // 2)


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
        if hit['handle'] == state.get('requested') and not state.get('requested_used'):
            new_editor = True       # the place the coordinator was told to hand over: its editor's first call
        else:
            # c5: an open editor sent open.py for the other planned places in turn (8 calls, a full window each, no
            # edit) and every switch reset its context estimate; another place is read like related code, and an
            # edit of it is kept while the editor's own place stays open
            mine_name = state.get('editor_name') or _name_of(state, state['editor_place'])
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
        state.update(editor_reads=[], editor_chars=0, editor_calls=0)
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
    if ROLE == 'editor':
        # a file's top level: an import is added there by line numbers (the editor's prompt says so)
        return _seen_mark(c['rel'], f'{label} ({c["rel"]}, lines {c["start"]}-{c["end"]}). To add an import, edit '
                          f"it by line numbers (['{label}', '<first line number>', '<last line number>', '<new "
                          f"lines>']); {mine_name} stays open.\n{body}")
    return _seen_mark(c['rel'], f'{label} ({c["rel"]}, lines {c["start"]}-{c["end"]}), read only: this editor '
                      f'changes {mine_name} alone; if the change belongs to {label}, reply with one sentence saying '
                      f'so, without a call.\n{body}')


OPENING = False     # the call came through open.py: an edit_place editor opens the place of its request


def main(argv, role=None, opening=False):
    global ROLE, OPENING
    if role:
        ROLE = role
    OPENING = opening
    root = _repo.repo_root()
    state = _state.load('journal') or _journal.new()
    _SEEN['state'] = state
    argv = list(argv)
    while len(argv) > 1 and argv[-1].strip().lower() in ('false', 'true', 'none'):
        argv.pop()              # bare booleans the model appends to a list (seen after a runaway) are not items
    argv = _args.split_single_quoted(argv)
    args = _args.unpack(argv) if state['step'] in ('D1', 'D2', 'D4') else _args.split_escaped(argv)
    if ROLE != 'editor' and state.get('editor_place'):
        # (also after an editor that closed itself: c11's closed editor kept its counts, and the next editor was told
        # 'near the context limit' on its first call)
        # the coordinator's call after an edit_place: that editor has replied, so it is closed (the script cannot
        # tell one edit_place invocation from the next: a07, where an editor still open made the next editor's
        # opening call read as a read of related code). A place it left undecided keeps its code (its sentence says
        # why); an accepted edit under review is kept
        i = _journal.plan_entry(state, state['editor_place'])
        if i is not None and state['plan'][i]['status'] == 'todo' and not state.get('editor_done'):
            state['plan'][i]['status'] = 'skipped'
        state['editor_review'], state['editor_done'] = False, True
        state.update(editor_place=None, editor_reads=[], seen={}, editor_chars=0, editor_calls=0, recent=[], repeats=0)
        if state['step'] == 'D3':
            _journal.advance(state)
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
    if ROLE == 'editor' and len([x for x in args if x and x.strip()]) == 1 and state.get('places'):
        # an editor's first call names its place, often the coordinator's last call word for word (run a04: it was
        # answered as a repeat, so the editor never got its place and its name printed empty)
        hit = _match(state['places'], _args.clean(args[0]), fuzzy=False)
        if hit is not None and (state.get('editor_done') or state.get('editor_place') != hit['id']):
            skip = True
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
    elif state['step'] != 'S0' and not skip and _journal.repeated(state, args):
        # a repeated call is answered differently each time and the third time the work moves on (structured
        # variation; a repeated failure in the context makes the model repeat it: Feedback That Backfires); repeats
        # never count as failed edits
        before = str(state.get('repeated_answer') or '')
        pargs = _place_args(state, args) if state['step'] in ('D3', 'D4') and state.get('places') else args
        is_edit = _args.edit(pargs) is not None or _whole_args([x for x in pargs if x and x.strip()]) is not None
        if is_edit and before.startswith(('Kept', 'OK', 'APPLIED')):
            out = 'This edit is already in the patch, so nothing was changed.\n\n' + _current_view(root, state)
        elif state['repeats'] >= 2:
            out = _move_on(root, state)
        elif is_edit and (state.get('failed_edits') or {}).get(_edit_key(args)):
            # a05: the undone edit sent again got "same call", and the model sent it a third time (RLEF: models resend
            # the same code; the answer says the same text fails again)
            handle, fails = state['failed_edits'][_edit_key(args)]
            out = (f'This exact edit of {handle} was made before and undone: these existing tests failed after it: '
                   f'{", ".join(fails) or "(see before)"}. It was not run again: the same text fails the same way. The '
                   'code is as it was before that edit.\n\n' + _current_view(root, state))
        else:
            out = ('This is the same call as one of the last three, so it was not run again'
                   + (f'; its answer began: {before[:160].rstrip(".")}' if before else '') + '.\n\n' + _current_view(root, state))
    else:
        try:
            items0 = [a for a in args if a and a.strip()]
            scoped = None
            if scoped is None and len(items0) == 1 and _args.clean(items0[0]).lower().strip('[]\'" ') in (
                    'status', 'state', 'next'):
                # the coordinator's call after each edit_place: where the work is (never a code name: 'status' chose
                # rich's Console.status in a dry run)
                scoped = (_coordinator_status(root, state) if ROLE == 'coordinator' and state['step'] == 'D3'
                          else _current_view(root, state))
            if scoped is None and ROLE == 'coordinator' and state['step'] in ('D3', 'D4') and len(items0) == 1 \
                    and _args.plan(args) != 'back':
                scoped = _coordinator_choice(root, state, items0[0])
            if scoped is None and ROLE == 'coordinator' and state['step'] == 'D3' and _args.plan(args) != 'back':
                # a04, a05, a07: the coordinator asked to see the code before delegating and got the same status
                # each time (F7); the answer says why it is not shown (P3: the rule with its reason)
                left = state['plan'][state['current']]['place'] if state.get('current') is not None else None
                scoped = ((f'This call was not run: the code of {_name_of(state, left)} is read by its editor. '
                           'edit_place shows the editor the numbered code, the code it uses and calls, and a test, and '
                           'the editor decides the change and makes it.\n\n' if left else '')
                          + _coordinator_status(root, state))
            state['edit_key'] = _edit_key(args) if len(items0) >= 2 else None
            seen_fail = (state.get('failed_edits') or {}).get(state['edit_key']) if state['edit_key'] else None
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
                handle, fails = seen_fail
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
        except Exception as e:      # never a traceback for the model: say what happened and the call to make
            _state.record({'error': f'{type(e).__name__}: {e}'})
            out = (f'The script hit an internal error ({type(e).__name__}: {str(e)[:200]}). Make the call below; '
                   'if it fails the same way, call submit_patch.')
        state['answer'] = out.strip().split('\n')[0][:300]
        _journal.remember(state, key, state['answer'])
        if range_key is not None and out.startswith('Lines ') and ' do not exist' not in out.split('\n')[0]:
            state.setdefault('ranges_shown', []).append(range_key)
            del state['ranges_shown'][:-6]
    if ROLE == 'editor':
        if not OPENING:
            state['reopen'] = 0         # reopenings count only in a row
        state['editor_chars'] = state.get('editor_chars', 0) + len(out) + sum(len(str(a)) for a in args)
        state['editor_calls'] = state.get('editor_calls', 0) + 1
    _state.save('journal', state)
    out = out.rstrip()
    if state['step'] != 'S0':
        out = _progress(root, state) + '\n\n' + out
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS - 2500] + '\n      ... (output cut) ...\n' + out[-2400:]
    print(out + '\n\n' + _journal.next_call(state, ROLE))


if __name__ == '__main__':
    main(sys.argv[1:])
