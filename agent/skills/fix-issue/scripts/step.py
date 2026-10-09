"""The only script the model calls. Each call is the model's decision for the current step; the answer is what that
step produced, a fixed verdict when something was checked, and the exact next call (docs/design_single.md, v1).

    S0  args [statement, search terms...]     -> requirements and candidates, each candidate shown by its name
    D1  args ["Session.send"] (1-3 names)    -> the related places, by name, and the first planned place, numbered,
                                                with the code it uses, its callers and what the change must do
    D3  args ["Session.send", whole new def]  -> edit (by name), syntax, existing tests; the next place, or the finish
        or ["Session.send", first, last, new lines]
        or ["skip"], ["back"], ["<place name>"] -> nothing else is accepted in this step (no questions)
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

N_CANDIDATES = 10
MAX_PLACES = 8
CODE_LINES = 120        # numbered lines shown for one place; a longer place shows its best-matching part
EDIT_LINES = 100        # the edit window: a place up to EDIT_LINES + 30 lines is shown whole, a longer one
                        # as its head and the part the requirements are about
CLASS_OUTLINE_AT = 90   # a chosen class longer than this is shown as an outline of its members
MODULE = '<module>'
MAX_CHARS = 10000       # a cap on one answer (about 2,900 tokens) against context growth; the end with NEXT is kept


# ---------------------------------------------------------------------------------------------------------- views

def _code_view(root, rel, start, end, focus_terms=None, limit=CODE_LINES):
    """Numbered lines start..end of rel, long strings collapsed. Over the limit: the head of the place (its def and
    first lines) and the part where the focus terms occur most."""
    lines = _repo.read_lines(root, rel)
    tree = _code.parse(''.join(lines))
    collapse = _code.long_strings(tree)
    start, end = max(1, start), min(len(lines), end)
    if end - start + 1 <= limit + 30:          # hiding a few lines saves little and hides code
        return _code.numbered(lines, start, end, collapse=collapse)
    head = min(end, start + 14)
    best, best_score = head + 1, -1
    toks = focus_terms or {}
    width = limit - (head - start + 1)
    if toks:
        score = [0] * (end + 2)
        for i in range(head + 1, end + 1):
            score[i] = sum(toks.get(t, 0) for w in re.findall(r'[A-Za-z_]\w*', lines[i - 1]) for t in _rank.tokens(w))
        for s in range(head + 1, max(head + 2, end - width + 2)):
            total = sum(score[s:s + width])
            if total > best_score:
                best, best_score = s, total
    a, b = best, min(end, best + width - 1)
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
        return x['rel']
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
    t = re.sub(r'\s+[(—-].*$', '', t) if not t.startswith('(') else t     # "Name (file, lines ..) — reason" copied
    t = re.sub(r'\(\)$|:$', '', t.strip())
    return t.strip().strip('`')


def _match(items, text):
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
    close = difflib.get_close_matches(t, list(by), n=1, cutoff=0.85)
    return by[close[0]] if close else None


def _pick(items, args):
    """(matched items, texts that name none of them) from the model's choice: one name per item, or several names in
    one item separated by commas or lines."""
    found, others = [], []
    for a in args:
        if not (a or '').strip():
            continue
        x = _match(items, a)
        if x is not None:
            found.append(x)
            continue
        parts = [q for q in re.split(r'[,;\n]+', a) if q.strip()]
        got = [_match(items, q) for q in parts] if len(parts) > 1 else [None]
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


def _window(root, state, i):
    """The place of plan entry i, ready to edit: its code, then what the change must do (the requirements, the
    statement's sentences about the behaviour, its example, an existing test that uses the code), then how to send
    the edit. The task is repeated in every window so that it is next to the decision even after the history is
    compacted."""
    entry = state['plan'][i]
    p = _place(state, entry['place'])
    _refresh(root, state, p['rel'])
    p = _place(state, entry['place'])
    head = f'EDIT {p["handle"]} ({p["rel"]}; place {i + 1} of {len(state["plan"])} to edit)'
    if p['reason'] != 'chosen':
        head += f' ({p["reason"]})'
    if p['name'] == '<exports>':
        n = len(_repo.read_lines(root, p['rel']))
        body = _code_view(root, p['rel'], 1, n, _focus(state, entry))
    else:
        body = _code_view(root, p['rel'], p['start'], p['end'], _focus(state, entry), limit=EDIT_LINES)
        if 'not shown) ...' in body:
            body += (f'\n{p["handle"]} is too long to show whole: change it with line numbers, or rewrite only a member '
                     'that is shown whole.')
        members = _members(root, p)
        if members:
            body = members + '\n' + body
    parts = [head, body]
    context = _context_view(root, p)
    if context:
        parts.append(context)
    parts.append(_task_view(root, state, p))
    if p['reason'] != 'chosen':
        parts.append('This place was added because it is related to the chosen code: make the same change here if '
                     'it needs it, or send ["skip"] if it needs none.')
    name = p['handle']
    parts.append(f'Answer with one of:\n'
                 f'  ["{name}", "<the whole new function or class>"]: it replaces the definition of the same name (a new '
                 f'name is added after {name}); write it whole, from its def or class line to its last line.\n'
                 f'  ["{name}", "<first line number>", "<last line number>", "<new lines>"]: replaces those lines, with '
                 f'their full indentation ("DELETE" deletes them).\n'
                 f'  ["skip"] if {name} needs no change; ["<name of another listed place>"] to open it; ["back"] to '
                 f'choose other code. Questions are not answered in this step.')
    return '\n'.join(parts)


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


USES = 8        # definitions the place calls or reads
USES_CHARS = 2500   # their total size
SHORT_MEMBER = 15   # a member of the place's own class up to this many lines is shown as code
CALLERS = 4     # code that calls the place, one line each


def _context_view(root, p):
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
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for al in n.names:
                imported[al.asname or al.name] = (n.module or '', al.name)
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
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in here or node.id in imported:
                used.append((node.lineno, node.id, 'name'))

    def module_of(rel):
        mod = rel[:-3].replace('/', '.')
        return mod[4:] if mod.startswith('src.') else mod

    def resolve(name, how):
        found = table.find(name)
        if how == 'self':
            return [(r, x) for r, x in found if r == p['rel'] and cls and x.name == f'{cls}.{name}']
        if how == 'name':
            if name in here:
                return [(r, x) for r, x in found if r == p['rel'] and x.name == name]
            mod, orig = imported[name]
            mod = mod.lstrip('.')
            return [(r, x) for r, x in table.find(orig) if x.name == orig
                    and (not mod or module_of(r).endswith(mod) or module_of(r).endswith(mod + '.__init__'))]
        defs = [(r, x) for r, x in found if x.name.split('.')[-1] == name]
        return defs if len(defs) <= 3 else []          # a common method name (get, copy of many classes) says little

    uses, seen, chars = [], set(), 0
    for name, how in dict.fromkeys((n, h) for _, n, h in sorted(used)):
        defs = [(r, x) for r, x in resolve(name, how)
                if not (r == p['rel'] and (x.name == own or x.name.startswith(own + '.')))]
        mine = how == 'self'
        if not defs:
            continue
        for r, x in defs[:2]:
            if (r, x.name) in seen:
                continue
            seen.add((r, x.name))
            if mine and x.end - x.start + 1 <= SHORT_MEMBER:
                # a short member of the same class (a property, a helper): its code, since its name says little
                text = _code.numbered(_repo.read_lines(root, r), x.start, x.end, collapse=[])
            else:
                line = _code.skeleton(r, x)
                text = '  ' + (line if len(line) <= 200 else line[:197] + '...')
            if chars + len(text) > USES_CHARS:
                break
            uses.append(text)
            chars += len(text)
        if len(uses) >= USES:
            break
    short = own.split('.')[-1]
    callers = [] if short.startswith('__') else [
        (r, c, n) for r, c, n in table.calls.get(short, []) if c and (r, c) != (p['rel'], own)
        and not _repo.is_doc_path(r)]
    callers.sort(key=lambda c: (c[0] != p['rel'], c[0], c[2]))
    calls = []
    for r, c, n in callers[:CALLERS]:
        text = _repo.read_lines(root, r)[n - 1].strip()
        calls.append(f'  {r}:{n} in {c}: {text[:120]}')
    out = []
    if uses:
        out.append(f'Code that {short} uses:\n' + '\n'.join(uses))
    if calls:
        more = f' ({len(callers)} calls, first {CALLERS})' if len(callers) > CALLERS else ''
        out.append(f'Code that calls {short}{more}:\n' + '\n'.join(calls))
    return '\n'.join(out)


def _task_view(root, state, p):
    """What the change must do, for the window of place p."""
    out = ['What the change must do:']
    for r in state['requirements']:
        out.append(f'  - {r["text"][:200]}')
    st = _state.load('statement', {})
    text = st.get('text', '')
    said = _statement.behaviour(text, skip=[r['text'] for r in state['requirements']])
    if said:
        out.append('  The statement says: ' + ' '.join(said))
    ex = _statement.example(text)
    if ex:
        out.append('  Its example:\n' + '\n'.join('    ' + l for l in ex.split('\n')))
    if p['name'] not in (MODULE, '<exports>'):
        cache = state.setdefault('tests_seen', {})
        key = f'{p["rel"]}::{p["name"]}'
        if key not in cache:
            found = _tests.example(root, p['rel'], p['name'])
            cache[key] = [found[0], found[1]] if found else None
        if cache[key]:
            rel, lines = cache[key]
            out.append(f'An existing test that uses {p["name"].split(".")[-1]} ({rel}), how it is used today:\n'
                       + '\n'.join('    ' + l for l in lines))
    return '\n'.join(out)


def _requirements_view(state):
    out = ['Requirements (from the statement):']
    for r in state['requirements']:
        kind = r['type'] if r['type'] != 'new' else 'new: ' + ', '.join(f'`{n}`' for n in r['names'])
        out.append(f'  - [{kind}] {r["text"]}')
    return '\n'.join(out)


def _candidates_view(state, title='Candidates (the code most related to the statement):'):
    out = [title]
    for c in state['candidates']:
        out.append(f'  {c["handle"]}  {c["line"]}')
    if not state['candidates']:
        out.append('  (none found: name the code to change as "<file>::<Name>")')
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


def _candidate(index, doc, matched, note=None):
    if doc.sym is None:
        line = f'(lines {doc.start}-{doc.end}: imports and top-level code)'
    else:
        doc_line = _code.first_doc_line(doc.sym)
        line = f'({doc.rel}, {doc.sym.kind}, lines {doc.sym.start}-{doc.sym.end}) {_code.signature(doc.sym)}' + (
            f' — {doc_line}' if doc_line else '')
    if len(line) > 220:
        line = line[:217] + '...'
    line += f'  [{note}]' if note else (f'  [matched: {", ".join(matched[:4])}]' if matched else '')
    return {'rel': doc.rel, 'name': doc.name, 'start': doc.start, 'end': doc.end, 'line': line}


def _rank_candidates(root, text, extra_items, requirements, n=N_CANDIDATES):
    index = _rank.Index(root)
    terms = _statement.terms(text)
    mt, mpaths = _statement.model_terms(extra_items)
    for k, v in mt.items():
        terms[k] = max(v, terms.get(k, 0))
    out, seen = [], set()
    for r in requirements:
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
                    out.append(_candidate(index, doc, [], note=f'where the new `{new}` would go'))
    for _, doc, matched in index.rank(terms, _statement.paths(text) + mpaths, n=n + len(out)):
        if (doc.rel, doc.name) not in seen and len(out) < n:
            seen.add((doc.rel, doc.name))
            out.append(_candidate(index, doc, matched))
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
    if '\n' not in statement.strip() and len(statement.strip()) < 160 and not state.get('asked_whole'):
        # the local model copied only the title in 3 of 6 runs, so the edit windows had nothing about the behaviour
        state['asked_whole'] = True
        return ('Only one line was copied. Copy the whole issue statement, every paragraph of the section "The issue '
                'statement" of your instructions, as the first item, then the search terms. If the statement really is '
                'one line, send the same call again. Nothing was searched yet.')
    extra = [t.strip() for a in items[1:] for t in re.split(r'[\n,;]', a) if t.strip()]
    text = _statement.clean(statement) or statement
    _state.save('statement', {'text': text, 'terms': extra})
    reqs = []
    index = _rank.Index(root)
    for i, r in enumerate(_statement.requirements(text) or [text[:300]], 1):
        kind, names = _statement.change_type(r, index.names.__contains__)
        old_names = [names[0].split('.')[-1]] if kind == 'rename' and names and \
            names[0].split('.')[-1] in index.names else []          # "A -> B", "rename A to B": A is the old name
        reqs.append({'id': f'R{i}', 'text': r, 'type': kind, 'names': names, 'old': old_names})
    cands, _ = _rank_candidates(root, text, extra, reqs)
    _journal.start(state, reqs, cands)
    return f'{_requirements_view(state)}\n\n{_candidates_view(state)}\n\nChoose the code to change.'


def _resolve(root, names):
    """Candidates for code the model named off the list: 'file::Name', 'Name' or 'pkg/file.py'."""
    table = _impact.Table(root, docs=True)
    out = []
    for raw in names:
        raw = _args.clean(raw)
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
                out.append({'rel': match[0], 'name': MODULE, 'start': 1, 'end': n, 'line': ''})
            continue
        found = table.find(name) if name else []
        if rel:
            found = [(r, s) for r, s in found if _rank.names_file(rel, r)] or found
        found.sort(key=lambda x: (_repo.is_doc_path(x[0]), x[0]))
        for r, s in found[:1]:
            out.append({'rel': r, 'name': s.name, 'start': s.start, 'end': s.end, 'line': ''})
        if not found and len(raw) >= 4 and not rel:
            hit = _find_text(root, table, raw)        # a code fragment: the function or class that contains it
            if hit:
                out.append(hit)
    return out


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
                    return {'rel': rel, 'name': MODULE, 'start': 1, 'end': len(lines), 'line': ''}
                return {'rel': rel, 'name': owner.name, 'start': owner.start, 'end': owner.end, 'line': ''}
    return None


def d1(root, state, args):
    if _args.plan(args) == 'back':
        return _candidates_view(state) + '\n\nChoose the code to change.'
    if len(args) == 1 and (_is_done(args[0]) or _is_skip(args[0])):
        return ('In this step code is chosen; nothing is open to skip or finish. ' + _candidates_view(state)
                + '\n\nChoose the code to change.')
    picked, others = _pick(state['candidates'], args)
    chosen = picked[:3]
    if others and len(chosen) < 3:
        chosen += _resolve(root, others)[:3 - len(chosen)]
    if not chosen:
        msg = ('That is not a name from the candidate list, and the code has no function or class of that name. '
               if others else 'No candidate was chosen. ')
        return msg + 'Nothing was opened.\n\n' + _candidates_view(state)
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
               or p['reason'].startswith('async/sync twin')
               or (p['reason'].startswith('same definition') and p['rel'].split('/')[-1] in bases)]
    if not planned:
        _journal.back(state)
        return ('The chosen code was not found in the parsed files (nothing was opened). Choose other candidates.\n\n'
                + _candidates_view(state))
    _journal.set_plan(state, [(pid, '') for pid in planned])
    _tests.save_verified(root)
    first = _place(state, planned[0])['handle']
    out.append(f'Places (the chosen code and the code related to it):\n{places_view}\n'
               f'They are edited one at a time, starting with {first}. A related place outside the plan needs an '
               f'edit only when the change must be made there too: send its name to open it.')
    return '\n\n'.join(out + [_window(root, state, state['current'])])


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
    for cand in (code, code.replace('\\n', '\n').replace('\\"', '"')):
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
                            f'to change other lines send their numbers: ["{p["handle"]}", "<first line number>", '
                            '"<last line number>", "<new lines>"]; nothing was changed', [], [])
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
        return _edit.Result(False, p['start'], p['end'], 'the new code is the same as the old one: nothing changes',
                            [], [])
    syms = _code.symbols(_code.parse(''.join(_repo.read_lines(root, rel))))
    spans = [t for t in (_target(syms, p, n) for n in names) if t is not None]
    start = min([t.start for t in spans] or [p['start']])
    end = max([t.end for t in spans] or [p['end']])
    return _edit.Result(True, start, end, None, repairs, warnings)


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
        return (f'The new lines are empty, which is unclear. To delete lines {raw[1]}-{raw[2]} of {name} send '
                f'["{name}", "{raw[1]}", "{raw[2]}", "DELETE"]. Nothing was changed.')
    items = [x for x in raw if x.strip()]
    single = _args.clean(items[0].replace('\\"', '"').replace("\\'", "'")) if len(items) == 1 else ''
    cur = state['current']
    if _is_skip(single) and cur is not None:
        pid = state['plan'][cur]['place']
        move = _journal.skip(state, cur)
        head = f'{_name_of(state, pid)} skipped: it keeps its code.'
        if move == 'next':
            return head + '\n\n' + _window(root, state, state['current'])
        if move == 'back':
            return head + ' Nothing was changed, so choose again.\n\n' + _candidates_view(state)
        return head + '\n\n' + _finish_view(root, state)
    listed = _listed_place(state, single) if single else None
    if listed:
        i = _journal.target(state, listed)               # a listed place: open it
        if state['plan'][i]['status'] == 'skipped':
            state['plan'][i]['status'] = 'todo'
        state['current'] = i
        return _window(root, state, i)
    whole = _whole_args(items)
    if whole:
        pid, text = whole
        p = _place(state, pid)
        if p is None:
            return ('The first item is not the name of a listed place. Nothing was changed.\nPlaces:\n'
                    + '\n'.join('  ' + _place_line(q) for q in state['places']))
        i = _journal.target(state, pid)
        before = _repo.read_lines(root, p['rel'])
        return _after_edit(root, state, i, p, _whole_edit(root, p, text), before)
    e = _args.edit(args)
    if e is None:
        if len(items) == 4 or (len(items) > 1 and re.fullmatch(r'(?i)\W*P\d+\W*', items[0] or '')):
            why = ('the second and third items must be line numbers, like 79 and 82.' if len(items) >= 4 else
                   f'it has {len(items)} items: send the place name and the whole new function or class, or the place '
                   'name, two line numbers and the new lines.')
            msg = f'The edit was not read: {why} Nothing was changed.'
        else:
            msg = ('NOT RUN: this step only edits; questions are not answered here (the code the place uses and its '
                   'callers are listed in the window). Accepted: the whole new function or class ["<place name>", '
                   '"<code>"], a line edit ["<place name>", "<first line number>", "<last line number>", "<new '
                   'lines>"], ["skip"] if the place needs no change, ["<name of another listed place>"] to open it, '
                   'or ["back"] to choose other code. Nothing was opened or changed.')
        if cur is None:
            return msg + '\nPlaces:\n' + '\n'.join('  ' + _place_line(q) for q in state['places'])
        return msg + '\n\n' + _window(root, state, cur)      # refused, but the place stays open
    pid, start, end, text = e
    p = _place(state, pid)
    if p is None:
        return ('The first item is not the name of a listed place. Nothing was changed.\nPlaces:\n'
                + '\n'.join('  ' + _place_line(q) for q in state['places']))
    i = _journal.target(state, pid)
    before = _repo.read_lines(root, p['rel'])
    return _after_edit(root, state, i, p, _edit.apply(root, p['rel'], start, end, text), before)


SHOW_CHANGED = 40   # numbered lines shown after an edit: the changed lines with 2 around them


def _changed_view(before, after):
    """The lines that an edit changed, numbered as they are now, with 2 lines around each change (a rewritten whole
    function is not shown again: only what differs)."""
    keep = set()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != 'equal':
            keep.update(range(max(1, j1 - 1), min(len(after), max(j2, j1 + 1) + 2) + 1))
    nums = sorted(keep)[:SHOW_CHANGED]
    out, prev = [], None
    for n in nums:
        if prev is not None and n > prev + 1:
            out.append('      ...')
        out.append(_code.numbered(after, n, n, collapse=[]))
        prev = n
    return '\n'.join(out)


def _after_edit(root, state, i, p, r, before):
    """The verdict of an edit r of plan entry i (place p; before: its file's lines before the edit): not applied,
    BROKEN (undone), or OK / not verified with the changed lines, then the next place or the finish."""
    pid = p['id']
    if not r.applied:
        return _failed(root, state, i, f'NOT APPLIED: {r.error}.', r.error)
    _refresh(root, state, p['rel'])
    new_lines = _repo.read_lines(root, p['rel'])[r.start - 1:r.end]
    changed = {}
    for e2 in state['plan']:
        q = _place(state, e2['place'])
        if e2['status'] == 'done' or e2['place'] == pid:
            changed.setdefault(q['rel'], []).append(q['name'].split('.')[-1])
    changed.setdefault(p['rel'], []).extend(l.strip() for l in new_lines)
    renamed = sorted({n for r_ in state['requirements'] for n in r_.get('old', [])})
    after = _repo.read_lines(root, p['rel'])
    cover = _status.changed_statements(before, after)
    verdict, detail, new_failures = _tests.check(root, changed, renamed, cover={p['rel']: cover} if cover else None)
    notes = ''.join(f'\n  note: {x}' for x in r.repairs + r.warnings)
    shown = _changed_view(before, _repo.read_lines(root, p['rel']))
    if verdict == 'BROKEN':
        _tests.restore_verified(root)
        _refresh(root, state, p['rel'])
        n = len(new_failures)
        return _failed(root, state, i, f'Undone: {n} existing test{"s" if n > 1 else ""} that passed before the edit '
                       f'failed after it, so {p["handle"]} is back as it was before the edit.\n{detail}\n'
                       f'Send {p["handle"]} again with a change that keeps {"these tests" if n > 1 else "this test"} '
                       'passing.', 'BROKEN ' + ' '.join(new_failures[:3]))
    _tests.save_verified(root)
    what = 'Kept and checked' if verdict == 'OK' else 'Kept, not checked'
    head = f'{what}: {p["handle"]} lines {r.start}-{r.end} changed; {detail}.{notes}\n{shown}'
    move = _journal.edit_done(state, i)
    if move == 'next':
        return head + '\n\n' + _window(root, state, state['current'])
    return head + '\n\n' + _finish_view(root, state)


def _failed(root, state, i, message, error):
    name = _name_of(state, state['plan'][i]['place'])  # before the plan may be emptied
    move = _journal.edit_failed(state, i, error)       # may empty the plan (back to choosing)
    if move == 'retry':
        return message + '\n\n' + _window(root, state, i)
    left = f'{name} failed {_journal.MAX_FAILS} times, so it keeps its last checked code.'
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
    left = [_place(state, e['place'])['handle'] for e in state['plan']
            if (_place(state, e['place'])['rel'], _place(state, e['place'])['name']) not in changed]
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
        head = f'The same call came three times, so {name} keeps its code and the work moves on.'
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
    return 'The same call came three times. Nothing else is done in this step: call submit_patch.'


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
        return _status.progress_line(root, now, left) + '\n' + _journal.budget_line(state)
    except Exception as e:      # the recap must never cost the answer
        _state.record({'error': f'progress: {type(e).__name__}: {e}'})
        return ''


def _current_view(root, state):
    if state['step'] == 'D3' and state['current'] is not None:
        return _window(root, state, state['current'])
    if state['step'] == 'D4':
        return _finish_view(root, state)
    return _requirements_view(state) + '\n\n' + _candidates_view(state) + '\n\nChoose the code to change.'


def main(argv):
    root = _repo.repo_root()
    state = _state.load('journal') or _journal.new()
    argv = list(argv)
    while len(argv) > 1 and argv[-1].strip().lower() in ('false', 'true', 'none'):
        argv.pop()              # bare booleans the model appends to a list (seen after a runaway) are not items
    args = _args.unpack(argv) if state['step'] in ('D1', 'D2', 'D4') else _args.split_escaped(argv)
    _state.record({'step': state['step'], 'args': [a[:300] for a in args]})
    state['calls'] = state.get('calls', 0) + 1
    key = _journal._key(state, args)          # the call as it is now, before the step changes the state
    skip = state['step'] == 'D3' and len(args) == 1 and _is_skip(args[0])     # one skip per place: not a repeat
    if state['step'] == 'D3' and len(args) == 1 and not skip and state['current'] is not None:
        # opening another listed place is a move, not a repeat
        target = _listed_place(state, args[0])
        skip = target is not None and target != state['plan'][state['current']]['place']
    resent = state['step'] != 'S0' and _is_statement_again(args)
    if state['step'] in ('D1', 'D2', 'D3', 'D4') and _journal.time_is_up(state):
        # time for edits is over: the call is not run, and the only next call is submit_patch (V10: "TIME IS UP"
        # while NEXT still offered edits, and the model lost its last turns)
        first = not state.get('time_up')
        state['time_up'], state['step'], state['current'] = True, 'D4', None
        out = (('Time for edits is over, so this call was not run. ' if first else
                'Edits are closed, so this call was not run. ') + _finish_view(root, state))
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
        else:
            out = ('This is the same call as just before, so it was not run again'
                   + (f'; its answer began: {before[:160].rstrip(".")}' if before else '') + '.\n\n' + _current_view(root, state))
    else:
        try:
            out = STEPS[state['step']](root, state, args)
        except Exception as e:      # never a traceback for the model: say what happened and the call to make
            _state.record({'error': f'{type(e).__name__}: {e}'})
            out = (f'The script hit an internal error ({type(e).__name__}: {str(e)[:200]}). Make the call below; '
                   'if it fails the same way, call submit_patch.')
        state['answer'] = out.strip().split('\n')[0][:300]
        _journal.remember(state, key, state['answer'])
    _state.save('journal', state)
    out = out.rstrip()
    if state['step'] != 'S0':
        out = _progress(root, state) + '\n\n' + out
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS - 2500] + '\n      ... (output cut) ...\n' + out[-2400:]
    print(out + '\n\n' + _journal.next_call(state))


if __name__ == '__main__':
    main(sys.argv[1:])
