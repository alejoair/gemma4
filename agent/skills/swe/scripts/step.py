"""The only script the model calls. Each call is the model's decision for the current step; the answer is what that
step produced, a fixed verdict when something was checked, and the exact next call (docs/design_single.md, v1).

    S0  args [statement, search terms...]   -> requirements and candidates C1..C10
    D1  args ["C2", "C5"]                    -> the related places P1..Pk and the first planned place, numbered, with
                                                what the change must do
    D3  args ["P1", first, last, new lines]  -> edit, syntax, existing tests; the next place, or the finish
        or ["skip"], ["back"], ["P<n>"],     -> nothing else is accepted in this step
           ["C<n>"] (adds that candidate)
    D4  submit_patch, or ["R2"]              -> back to D1 for a requirement that is not covered
"""
import os
import re
import sys

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
import _tests  # noqa: E402

N_CANDIDATES = 10
MAX_PLACES = 8
CODE_LINES = 120        # numbered lines shown for one place; a longer place shows its best-matching part
EDIT_LINES = 60         # the edit window: the place's head and the part the requirements are about
CLASS_OUTLINE_AT = 90   # a chosen class longer than this is shown as an outline of its members
MODULE = '<module>'
MAX_CHARS = 8000        # a cap on one answer (about 2,300 tokens) against context growth; the end with NEXT is kept


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
        return f'{p["id"]} {p["rel"]} :: (its imports) — {p["reason"]}'
    return f'{p["id"]} {p["rel"]} :: {p["name"]} (lines {p["start"]}-{p["end"]}) — {p["reason"]}'


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
    head = f'EDIT {p["id"]} (place {i + 1} of {len(state["plan"])} to edit): {p["rel"]} :: {p["name"]}'
    if p['reason'] != 'chosen':
        head += f' ({p["reason"]})'
    if p['name'] == '<exports>':
        n = len(_repo.read_lines(root, p['rel']))
        body = _code_view(root, p['rel'], 1, n, _focus(state, entry))
    else:
        body = _code_view(root, p['rel'], p['start'], p['end'], _focus(state, entry), limit=EDIT_LINES)
        members = _members(root, p)
        if members:
            body = members + '\n' + body
    parts = [head, body, _task_view(root, state, p)]
    if p['reason'] != 'chosen':
        parts.append(f'This place was added because it is related to the chosen code: make the same change here if '
                     f'it needs it, or send ["skip"] if it needs none.')
    parts.append('Send the line numbers of the lines to replace and the new lines with their full indentation ("DELETE" '
                 'as the new lines deletes them). To add lines, replace the line before them with that same line followed by the '
                 'new ones. Also accepted: ["skip"] (this place needs no change), ["done"] (all needed changes are '
                 'made), ["P<n>"] (open another listed place), ["C<n>"] (add a candidate), ["back"] (choose again), '
                 f'and up to {MAX_LOOKUPS} questions per place: ["<name>"] (where it is defined and called), '
                 '["<file>.py"] (its classes and functions), ["P<n>", "<first line>", "<last line>"] (those lines), '
                 '["search <text>"] (the lines that contain the text).')
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
    tail = f'. To see a member, send ["{p["id"]}", "<first line>", "<last line>"] (no new lines), e.g. ["{p["id"]}", ' \
           f'"{first.start}", "{first.end}"]' if first else ''
    return 'Members: ' + ', '.join(f'{x.name.split(".")[-1]} {x.start}-{x.end}' for x in members) + tail


def _task_view(root, state, p):
    """What the change must do, for the window of place p."""
    out = ['What the change must do:']
    for r in state['requirements']:
        out.append(f'  {r["id"]} {r["text"][:200]}')
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
        out.append(f'  {r["id"]} [{kind}] {r["text"]}')
    return '\n'.join(out)


def _candidates_view(state, title='Candidates (the code most related to the statement):'):
    out = [title]
    for c in state['candidates']:
        out.append(f'  {c["id"]} {c["line"]}')
    if not state['candidates']:
        out.append('  (none found: name the code to change as "<file>::<Name>")')
    return '\n'.join(out)


# ---------------------------------------------------------------------------------------------------------- state

def _place(state, pid):
    for p in state['places']:
        if p['id'] == pid:
            return p
    return None


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
        line = f'{doc.rel} :: {MODULE} (lines {doc.start}-{doc.end}: imports and top-level code)'
    else:
        line = _code.skeleton(doc.rel, doc.sym)
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
        reqs.append({'id': f'R{i}', 'text': r, 'type': kind, 'names': names})
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
    ids, others = _args.ids(args, 'C')
    known = {c['id']: c for c in state['candidates']}
    chosen = [known[i] for i in ids if i in known][:3]
    unknown = [i for i in ids if i not in known]
    if others:
        chosen += _resolve(root, others)[:3 - len(chosen)]
    if not chosen:
        msg = f'{", ".join(unknown + others)} is not a candidate id nor a name found in the code. ' if (
            unknown or others) else 'No candidate was chosen. '
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
    # in its own window. Other related places are edited by id.
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
    out.append(f'Places (the chosen code and the code related to it):\n{places_view}\n'
               f'They are edited one at a time, starting with {planned[0]}. A related place outside the plan needs an '
               f'edit only when the change must be made there too: send ["P<n>"] to open it.')
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


def _listed_place(root, state, text):
    """The id of the listed place that text names: a place id, a candidate id or a code name of a listed place."""
    text = _args.clean(text).strip('[]"\' ')
    if re.fullmatch(r'(?i)P\d+', text):
        return text.upper() if _place(state, text.upper()) else None
    cand = None
    if re.fullmatch(r'(?i)C\d+', text):
        cand = next((c for c in state['candidates'] if c['id'] == text.upper()), None)
    elif re.fullmatch(r'[\w./-]*(::)?[\w.]+(\(\))?', text):
        rel, _, name = text.rpartition('::')
        name = name.strip('()')
        ids = [p['id'] for p in state['places'] if p['name'] != '<exports>'
               and (p['name'] == name or p['name'].endswith('.' + name)) and (not rel or _rank.names_file(rel, p['rel']))]
        if ids:                     # a name of several listed places: the open one first, else the first listed
            cur = state['plan'][state['current']]['place'] if state.get('current') is not None else None
            return cur if cur in ids else ids[0]
        found = _resolve(root, [text])
        cand = found[0] if found else None
    if cand is None:
        return None
    p = next((p for p in state['places'] if (p['rel'], p['name']) == (cand['rel'], cand['name'])), None)
    return p['id'] if p else None


MAX_LOOKUPS = 5    # questions per place in the edit step (asking never counts as a failed attempt at the place)


SHOW_LINES = 80
LIMIT = 'LIMIT'     # returned by a question asked after the MAX_LOOKUPS of the place were used


EDIT_BY_CALL = 12   # without an edit by this call, questions stop (SHERLOC final-turn prompt; notebooks: edit by call 12)


def _no_edit_yet(state):
    return not state['edited'] and state.get('calls', 0) >= EDIT_BY_CALL


def _limit_message(state, cur, asked=''):
    pid = state['plan'][cur]['place']
    if _no_edit_yet(state):
        return (f'NOT RUN: {state.get("calls", 0)} calls and no edit yet. Questions stop here: make your best edit of '
                f'{pid} now (a wrong edit can be corrected, reading more cannot score), or ["skip"] it if it needs no '
                f'change. Nothing was opened or changed.')
    asked = _args.clean(asked or '').strip('[]"\'\\ ')
    words = {w for w in re.findall(r'[A-Za-z_]\w+', asked) if w not in ('search', 'py', 'src')}
    match = [c for c in state['candidates'] if asked and (c['rel'] == asked or c['rel'].endswith('/' + asked)
                                                          or c['name'].split('.')[-1] in words or c['name'] in words)]
    hint = (' The code you ask about is a candidate: send ' + ' or '.join(f'["{c["id"]}"]' for c in match[:3])
            + ' to add it to the plan and see it.') if match else ''
    return (f'NOT RUN: {MAX_LOOKUPS} questions were already answered for {pid}. Edit it now, or ["skip"] it, or '
            f'["done"] if all needed changes are made.{hint} Nothing was opened or changed.')


def _show_lines(root, state, items):
    """["P<n>", first, last] (an edit without new lines): the lines first..last of that place, numbered, at most
    SHOW_LINES, so that the model can see the part of a long place it wants to edit. Counts as a lookup."""
    pid = _args.clean(items[0]).strip('[]"\' ').upper()
    p = _place(state, pid)
    try:
        a, b = int(_args.clean(items[1])), int(_args.clean(items[2]))
    except ValueError:
        return None
    if p is None or p['name'] == '<exports>':
        return None
    entry = state['plan'][state['current']]
    entry['lookups'] = entry.get('lookups', 0) + 1
    if entry['lookups'] > MAX_LOOKUPS or _no_edit_yet(state):
        return LIMIT
    if max(a, p['start']) > min(b, p['end']):
        return f'Lines {a}-{b} are outside {pid}, which has lines {p["start"]}-{p["end"]}. Nothing was changed.'
    a, b = max(a, p['start']), min(b, p['end'])
    b = min(b, a + SHOW_LINES - 1)
    lines = _repo.read_lines(root, p['rel'])
    return (f'Lines {a}-{b} of {pid} ({p["rel"]} :: {p["name"]}); nothing was changed. To replace lines, send the place '
            f'id, the first and last line numbers and the new lines.\n'
            + _code.numbered(lines, a, b, collapse=[]))      # asked for: long texts shown in full


def _lookup(root, state, text):
    """A bare code name (or "def name") that is not a listed place, sent in the edit step: the answer says where code
    of that name is defined, or that none exists, without opening it, so that the model need not search for it. At
    most MAX_LOOKUPS per place; later ones are refused like other calls."""
    text = _args.clean(text).strip('[]"\'\\ ')
    if re.fullmatch(r'[\w./-]+/[\w.-]+', text) and not text.endswith('.py') \
            and os.path.isfile(os.path.join(root, text + '.py')):
        text += '.py'                                   # a module path without .py
    if re.fullmatch(r'[\w./-]+\.py', text):
        return _file_outline(root, state, text)
    ls = re.fullmatch(r'(?:ls|dir)\s+(-\w+\s+)?([\w./-]+)', text)
    if ls or (re.fullmatch(r'[\w.-]+(/[\w.-]+)*/?', text) and os.path.isdir(os.path.join(root, text))):
        d = (ls.group(2) if ls else text).strip('/')
        if not os.path.isdir(os.path.join(root, d)):
            return f'No directory {d} in the repository (nothing was opened or changed).'
        names = sorted(n + ('/' if os.path.isdir(os.path.join(root, d, n)) else '') for n in os.listdir(os.path.join(root, d))
                       if not n.startswith('.') and n != '__pycache__')
        return f'{d}/ has: {", ".join(names[:80])} (nothing was opened or changed).'
    m = re.fullmatch(r'(?:(?:async\s+)?def\s+|class\s+)?([A-Za-z_][\w.]*)(?:\(.*\))?:?', text)
    if m and (re.fullmatch(r'(?i)[PCR]\d+', m.group(1)) or _is_skip(m.group(1)) or m.group(1).lower() == 'back'):
        return None
    if not m:
        search = re.fullmatch(r'(?is)(?:search|grep|find)\s*:?\s+(.+)', text)
        needle = (search.group(1) if search else text).strip().strip('"\'`')
        scope = re.fullmatch(r'(?s)(.+?)\s+in\s+([\w./-]+\.py)', needle)      # "search X in path/file.py"
        only = None
        if scope:
            needle, only = scope.group(1).strip().strip('"\'`'), scope.group(2)
        if '\n' in needle or len(needle) < 3 or len(needle) > 120:
            return None
        entry = state['plan'][state['current']]
        entry['lookups'] = entry.get('lookups', 0) + 1
        if entry['lookups'] > MAX_LOOKUPS or _no_edit_yet(state):
            return LIMIT
        return _search_text(root, needle, only)
    entry = state['plan'][state['current']]
    entry['lookups'] = entry.get('lookups', 0) + 1
    if entry['lookups'] > MAX_LOOKUPS or _no_edit_yet(state):
        return LIMIT
    name = m.group(1)
    table = _impact.Table(root, docs=False)
    found = table.find(name.split('.')[-1])
    if '.' in name:
        found = [(r, x) for r, x in found if x.name.endswith(name)] or found
    if not found:
        return (f'No function or class named `{name}` is defined in the repository. '
                + (_search_text(root, name) or ''))
    where = '; '.join(f'{r} :: {x.name} (lines {x.start}-{x.end})' for r, x in found[:6])
    callers = [(r, c, line) for r, c, line in table.calls.get(name.split('.')[-1], []) if c]
    if callers:
        where += '. Called in: ' + '; '.join(f'{r} :: {c} (line {line})' for r, c, line in callers[:6])
    listed = [p['id'] for p in state['places'] for r, x in found if (p['rel'], p['name']) == (r, x.name)]
    tail = f' It is listed as {", ".join(listed)}: send ["{listed[0]}"] to open it.' if listed else \
        ' It is not a listed place: to edit it, send ["back"] and choose it as "<file>::<Name>".'
    return f'`{name}` is defined in: {where}.{tail} Nothing was opened or changed.'


MAX_MATCHES = 50


def _search_text(root, text, only=None):
    """Lines of the repository's code (tests and docs included, hidden and cache files not) that contain text
    literally, as 'file:line: code', at most MAX_MATCHES; more matches ask for a narrower text (SWE-agent's summarized
    search)."""
    needle = text.strip()
    if len(needle) < 3:
        return None
    hits, files = [], set()
    rels = sorted(_repo.iter_py(root, tests=True, docs=True), key=lambda r: (_repo.is_doc_path(r), _repo.is_test_path(r), r))
    if only:
        rels = [r for r in rels if _rank.names_file(only, r)] or rels
    for rel in rels:                                    # package code first, then tests, then docs and examples
        for i, line in enumerate(_repo.read_text(root, rel).splitlines(), 1):
            if needle in line:
                hits.append(f'  {rel}:{i}: {line.strip()[:120]}')
                files.add(rel)
    if not hits:
        return f'No line of the repository contains `{needle}` (nothing was opened or changed).'
    if len(hits) > MAX_MATCHES:
        top = sorted(files, key=lambda r: -sum(h.startswith(f'  {r}:') for h in hits))[:10]
        return (f'{len(hits)} lines in {len(files)} files contain `{needle}`: too many to list; send a more specific '
                f'text. Files with most matches: {", ".join(top)} (nothing was opened or changed).')
    return f'{len(hits)} lines contain `{needle}` (nothing was opened or changed):\n' + '\n'.join(hits)


def _file_outline(root, state, path):
    """A file path sent in the edit step: the file's classes and functions with their lines (no code). Counts as a
    lookup."""
    entry = state['plan'][state['current']]
    entry['lookups'] = entry.get('lookups', 0) + 1
    if entry['lookups'] > MAX_LOOKUPS or _no_edit_yet(state):
        return LIMIT
    table = _impact.Table(root, docs=True)
    rel = next((r for r in table.syms if _rank.names_file(path, r)), None)
    if rel is None:
        return f'No file {path} in the repository (nothing was opened or changed).'
    syms = [x for x in table.syms[rel] if x.name.count('.') <= 1]
    lines = _repo.read_lines(root, rel)
    if len(lines) <= 40:                     # a small file is shown whole (an outline would say little)
        return f'{rel} ({len(lines)} lines; nothing was opened or changed):\n' + _code.numbered(lines, 1, len(lines))
    listed = {p['name']: p['id'] for p in state['places'] if p['rel'] == rel}
    out = [f'{rel} has (nothing was opened or changed; listed places can be opened with their id, other code is chosen '
           f'after ["back"] as "<file>::<Name>"):']
    for x in syms[:60]:
        inside = next((p for p in state['places'] if p['rel'] == rel and p['name'] != x.name
                       and p['start'] <= x.start and x.end <= p['end']), None)
        note = f' = {listed[x.name]}' if x.name in listed else (
            f' (inside {inside["id"]}: to see it send ["{inside["id"]}", "{x.start}", "{x.end}"])' if inside else '')
        out.append(f'  {x.name} ({x.kind}, lines {x.start}-{x.end}){note}')
    return '\n'.join(out)


def _add_named(root, state, items):
    """Candidate ids and "<file>::<Name>" items sent together in the edit step: each joins the places and the plan
    (choosing more code is part of the procedure). Returns the place ids, or None."""
    out, first = [], None
    for raw in items[:4]:
        x = _args.clean(raw)
        if re.fullmatch(r'(?i)C\d+', x):
            got = _add_candidate(state, x)
            if got:
                out.append(got)
                first = first if first is not None else state['current']
            continue
        rel, _, name = x.rpartition('::')
        table = _impact.Table(root, docs=True)
        defs = [(r, d) for r, d in table.find(name.strip('()')) if not rel or _rank.names_file(rel, r)]
        defs.sort(key=lambda t: (_repo.is_doc_path(t[0]), t[0]))
        if not defs or len(state['places']) >= MAX_PLACES + 4:
            continue                    # only a real definition opens a place (no text-fragment fallback)
        r0, d0 = defs[0]
        c = {'rel': r0, 'name': d0.name, 'start': d0.start, 'end': d0.end}
        old = next((p for p in state['places'] if (p['rel'], p['name']) == (c['rel'], c['name'])), None)
        pid = old['id'] if old else f'P{len(state["places"]) + 1}'
        if not old:
            state['places'].append({'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end'],
                                    'reason': 'chosen', 'id': pid})
        i = _journal.target(state, pid)
        if state['plan'][i]['status'] == 'skipped':
            state['plan'][i]['status'] = 'todo'
        out.append(pid)
        first = first if first is not None else i
    if not out:
        return None
    state['current'] = first
    return ', '.join(out)


def _add_candidate(state, text):
    """A candidate id that is not a listed place, sent in the edit step: the candidate joins the places and the plan
    (choosing more of the system's own list is part of the procedure) and is opened. Returns the new place id."""
    ids = re.findall(r'C\d+', _args.clean(text).upper())
    if not ids or not re.fullmatch(r'[\sC\d,;"\'\[\]]+', _args.clean(text).upper()):
        return None
    added = []
    for cid in ids[:3]:
        c = next((c for c in state['candidates'] if c['id'] == cid), None)
        if c is None or len(state['places']) >= MAX_PLACES + 4:
            continue
        old = next((p for p in state['places'] if (p['rel'], p['name']) == (c['rel'], c['name'])), None)
        pid = old['id'] if old else f'P{len(state["places"]) + 1}'
        if not old:
            state['places'].append({'rel': c['rel'], 'name': c['name'], 'start': c['start'], 'end': c['end'],
                                    'reason': 'chosen', 'id': pid})
        i = _journal.target(state, pid)
        if state['plan'][i]['status'] == 'skipped':
            state['plan'][i]['status'] = 'todo'
        added.append((pid, i))
    if not added:
        return None
    state['current'] = added[0][1]
    return ', '.join(pid for pid, _ in added)


def d3(root, state, args):
    """The edit step (SOP-Agent: only the valid actions of the step). Accepted: an edit of a listed place, skip, done,
    back, a listed place, candidates or names to add to the plan, and up to MAX_LOOKUPS questions per place. Anything
    else is refused and changes nothing. Only failed edits (not applied, tests broken, repeated) count against a
    place; asking or a refused form never makes the model leave the place it is working on."""
    word = _args.plan(args)
    if word == 'back':
        _journal.back(state)
        return 'The edits made so far stay. ' + _candidates_view(state) + '\n\nChoose the code to change.'
    if _journal.time_is_up(state):
        state['step'] = 'D4'
        return 'TIME IS UP: no more edits are accepted. ' + _finish_view(root, state)
    raw = [x for x in args if x is not None]
    if len(raw) == 4 and not raw[3].strip() and re.fullmatch(r'(?i)\W*P\d+\W*', raw[0] or ''):
        pid = _args.clean(raw[0]).upper()
        return (f'The new lines are empty, which is unclear. To see lines {raw[1]}-{raw[2]} of {pid} send ["{pid}", '
                f'"{raw[1]}", "{raw[2]}"]; to delete them send ["{pid}", "{raw[1]}", "{raw[2]}", "DELETE"]. Nothing was '
                'changed.')
    items = [x for x in raw if x.strip()]
    if len(items) == 2 and re.fullmatch(r'(?i)\W*P\d+\W*', items[0]) and '\n' not in items[1].strip() \
            and not re.match(r'\s*\d', items[1]):
        items = [items[1]]          # ["P2", "search x"]: a question asked about the open place
    single = _args.clean(items[0].replace('\\"', '"').replace("\\'", "'")) if len(items) == 1 else ''
    cur = state['current']
    if _is_skip(single) and cur is not None:
        pid = state['plan'][cur]['place']
        move = _journal.skip(state, cur)
        head = f'{pid} skipped: it keeps its code.'
        if move == 'next':
            return head + '\n\n' + _window(root, state, state['current'])
        if move == 'back':
            return head + ' Nothing was changed, so choose again.\n\n' + _candidates_view(state)
        return head + '\n\n' + _finish_view(root, state)
    if _is_done(single):
        if not state['edited']:
            return ('Nothing was changed yet, so there is nothing to finish: edit a place, or ["back"] to choose other '
                    'code.\n\n' + (_window(root, state, cur) if cur is not None else _candidates_view(state)))
        for e in state['plan']:
            if e['status'] == 'todo':
                e['status'] = 'skipped'
        state['step'], state['current'] = 'D4', None
        return _finish_view(root, state)
    listed = _listed_place(root, state, single) if single else None
    if listed and cur is not None and listed == state['plan'][cur]['place'] and not re.fullmatch(r'(?i)P\d+', single):
        listed = None               # the open place named again: answer where the name is defined and called
    if listed:
        i = _journal.target(state, listed)               # a listed place, by id, candidate id or name: open it
        if state['plan'][i]['status'] == 'skipped':
            state['plan'][i]['status'] = 'todo'
        state['current'] = i
        return _window(root, state, i)
    packed = re.fullmatch(r'\s*(P\d+)\s*[,|\s]\s*(\d+)\s*[,|\s-]\s*(\d+)\s*', _args.clean(single), re.I) if single else None
    view = list(packed.groups()) if packed else items
    shown = _show_lines(root, state, view) if len(view) == 3 and cur is not None else None
    if shown == LIMIT:
        return _limit_message(state, cur, single) + '\n\n' + _window(root, state, cur)
    if shown:
        return shown
    added = _add_candidate(state, single) if single else None
    names = [re.sub(r'(?i)^C\d+\s*:\s*(?=[\w./-]+::)', '', _args.clean(x)) for x in items]
    if not added and len(names) > 1 and all(
            re.fullmatch(r'(?i)C\d+|([\w./-]+::)?[A-Za-z_][\w.]*(\(\))?', x) and not re.fullmatch(r'(?i)P\d+', x)
            for x in names):
        added = _add_named(root, state, names)
    elif not added and len(names) == 1 and (
            (names[0] != _args.clean(items[0]) and '::' in names[0])        # "C11:file::Name"
            or re.fullmatch(r'([\w./-]+::)?[A-Z]\w*(\.[A-Za-z_]\w*)?(\(\))?', names[0])):   # Class or Class.method
        added = _add_named(root, state, names)
    if added:
        return f'{added} in the plan now.\n\n' + _window(root, state, state['current'])
    lookup = _lookup(root, state, single) if single and cur is not None else None
    if lookup == LIMIT:
        return _limit_message(state, cur, single) + '\n\n' + _window(root, state, cur)
    if lookup:
        return lookup + '\n\n' + _window(root, state, cur)
    e = _args.edit(args)
    if e is None:
        if len(items) == 4 or (len(items) > 1 and re.fullmatch(r'(?i)\W*P\d+\W*', items[0] or '')):
            why = (f'the second and third items must be line numbers like 79 and 82, not "{items[1][:40]}" and '
                   f'"{items[2][:40]}".' if len(items) >= 4 else
                   f'it has {len(items)} items; it needs 4: the place id, two line numbers and the new lines.')
            msg = f'The edit was not read: {why} Nothing was changed.'
            return msg + ('\n' + _window(root, state, cur) if cur is not None else '')
        else:
            msg = ('NOT RUN: this step only edits the listed places. Accepted: an edit ["P<n>", "<first line number>", '
                   '"<last line number>", "<new lines>"], ["P<n>"] to open a listed place, ["C<n>"] to add a candidate, '
                   '["skip"] if the place needs no change, or ["back"] to choose again. Nothing was opened or changed.'
                   '\nPlaces:\n'
                   + '\n'.join('  ' + _place_line(q) for q in state['places']))
        if cur is None:
            return msg
        return msg + '\n\n' + _window(root, state, cur)      # refused, but the place stays open
    pid, start, end, text = e
    p = _place(state, pid)
    if p is None:
        return (f'{pid} is not a listed place. Nothing was changed.\nPlaces:\n'
                + '\n'.join('  ' + _place_line(q) for q in state['places']))
    i = _journal.target(state, pid)
    r = _edit.apply(root, p['rel'], start, end, text)
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
    verdict, detail, new_failures = _tests.check(root, changed)
    notes = ''.join(f'\n  note: {x}' for x in r.repairs + r.warnings)
    shown = _code.numbered(_repo.read_lines(root, p['rel']), max(1, r.start - 2), r.end + 2)
    if verdict == 'BROKEN':
        _tests.restore_verified(root)
        _refresh(root, state, p['rel'])
        return _failed(root, state, i, f'BROKEN: the edit made existing tests fail, so it was undone.\n{detail}\n'
                       f'Read the failing test: send a corrected edit of {pid} that keeps it passing.',
                       'BROKEN ' + ' '.join(new_failures[:3]))
    _tests.save_verified(root)
    what = 'OK' if verdict == 'OK' else 'APPLIED, NOT VERIFIED'
    head = f'{what}: {pid} lines {r.start}-{r.end} changed; {detail}.{notes}\n{shown}'
    move = _journal.edit_done(state, i)
    if move == 'next':
        return head + '\n\n' + _window(root, state, state['current'])
    return head + '\n\n' + _finish_view(root, state)


def _failed(root, state, i, message, error):
    pid = state['plan'][i]['place']
    move = _journal.edit_failed(state, i, error)       # may empty the plan (back to choosing)
    if move == 'retry':
        return message + '\n\n' + _window(root, state, i)
    left = f'{pid} failed {_journal.MAX_FAILS} times and keeps its last verified code.'
    if state['step'] == 'D3':
        return f'{message}\n{left}\n\n' + _window(root, state, state['current'])
    if state['step'] == 'D1':
        return f'{message}\n{left} Nothing was changed, so choose again.\n\n' + _candidates_view(state)
    return f'{message}\n{left}\n\n' + _finish_view(root, state)


def _coverage(state, root):
    """Which requirement shares names or words with an edited place (its name, its plan line, its changed lines)."""
    diff = _repo.worktree_diff(root) or ''
    added = ' '.join(l[1:] for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))
    edited_text = ' '.join(f'{e["intent"]} {_place(state, e["place"])["name"]}' for e in state['plan']
                           if e['status'] == 'done') + ' ' + added
    have = {t for w in re.findall(r'[A-Za-z_]\w*', edited_text) for t in _rank.tokens(w)}
    out = []
    for r in state['requirements']:
        terms = _statement.terms(r['text'])
        hits = sorted({term for term, w in terms.items() for ident in re.findall(r'[A-Za-z_]\w*', term)
                       if w >= 2 and any(t in have for t in _rank.tokens(ident))})
        out.append((r, hits))
    return out


def _finish_view(root, state):
    out = ['The planned places are done. The patch now holds:']
    for e in state['plan']:
        p = _place(state, e['place'])
        status = {'done': 'changed', 'skipped': 'NOT changed', 'todo': 'not edited'}[e['status']]
        out.append(f'  {p["id"]} {p["rel"]} :: {p["name"]} — {status}')
    out.append('Requirements:')
    for r, hits in _coverage(state, root):
        mark = f'the changes share its words {", ".join(hits[:4])}' if hits else \
            'NOTHING changed shares its words: is it covered?'
        out.append(f'  {r["id"]} {r["text"][:120]}\n      -> {mark}')
    return '\n'.join(out)


def d4(root, state, args):
    ids, others = _args.ids(args, 'R')
    if ids and not others:
        rid = ids[0]
        req = next((r for r in state['requirements'] if r['id'] == rid), None)
        if req is None:
            return f'{rid} is not a requirement.\n' + _finish_view(root, state)
        st = _state.load('statement', {})
        cands, _ = _rank_candidates(root, req['text'], st.get('terms', []), [req])
        _journal.requirement_back(state, cands)
        return (f'Candidates for {rid} ({req["text"][:120]}). The edits made so far stay.\n'
                + _candidates_view(state, title='Candidates:') + '\n\nChoose the code to change.')
    if _args.edit(args) is not None:
        state['step'] = 'D3'
        out = d3(root, state, args)
        if state['step'] == 'D3' and state['current'] is None:
            state['step'] = 'D4'
        return out
    items = [x for x in args if x is not None and x.strip()]
    if len(items) == 1 and state.get('finish_lookups', 0) < MAX_LOOKUPS:
        text = _args.clean(items[0]).strip('[]"\'\\ ')
        if re.fullmatch(r'[\w./-]+\.py', text):
            answer = None
        else:
            name = re.fullmatch(r'(?:(?:async\s+)?def\s+|class\s+)?([A-Za-z_][\w.]*)(?:\(.*\))?:?', text)
            search = re.fullmatch(r'(?is)(?:search|grep|find)\s*:?\s+(.+)', text)
            if name:
                table = _impact.Table(root, docs=False)
                found = table.find(name.group(1).split('.')[-1])
                answer = ('`{}` is defined in: {}.'.format(name.group(1), '; '.join(
                    f'{r} :: {x.name} (lines {x.start}-{x.end})' for r, x in found[:6])) if found
                          else _search_text(root, name.group(1)))
            else:
                answer = _search_text(root, (search.group(1) if search else text).strip())
        if answer:
            state['finish_lookups'] = state.get('finish_lookups', 0) + 1
            return answer + '\n\n' + _finish_view(root, state)
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
    skip = state['step'] == 'D3' and len(args) == 1 and _is_skip(args[0])     # one skip per place: not a repeat
    if state['step'] == 'D3' and len(args) == 1 and not skip and state['current'] is not None:
        # opening another listed place is a move, not a repeat
        target = _listed_place(root, state, args[0])
        skip = target is not None and target != state['plan'][state['current']]['place']
    resent = state['step'] != 'S0' and _is_statement_again(args)
    if resent:
        # after the harness compacts the history the model may start again: say where the work is
        out = ('The start was already made: the requirements and candidates are known and the work is in a later '
               'step. Continue from here (nothing was changed).\n\n' + _current_view(root, state))
    elif state['step'] != 'S0' and not skip and _journal.repeated(state, args):
        now = _journal.next_call(state).replace('NEXT: ', '', 1)
        out = (f'STOP REPEATING: this call was already made and was not run again (its answer was: '
               f'{state.get("repeated_answer") or state.get("answer", "")}).')
        if state['step'] == 'D3' and state['current'] is not None and _args.edit(args) is None:
            # a repeated question (not an edit): its answer again (after compaction it is no longer above); asking
            # never leaves the place
            full = state.get('full_answers', {}).get(repr([a.strip() for a in args]))
            out = ('This was asked before; the same answer again:\n' + full) if full else \
                out + '\n\n' + _window(root, state, state['current'])
        elif _args.edit(args) is not None and str(state.get('repeated_answer', '')).startswith(('OK', 'APPLIED')):
            # the same edit again after it was applied: nothing to undo or count
            out = ('This edit was already applied (its answer was: ' + state['repeated_answer'] + '). Nothing was '
                   'changed.\n\n' + _current_view(root, state))
        elif state['step'] == 'D3' and state['current'] is not None:
            # stuck on a place: a repeat counts as a failed edit, so the place is left after MAX_FAILS
            out = _failed(root, state, state['current'], out, 'repeated call')
        elif state['step'] == 'D2' and state['places']:
            # stuck on the plan: the chosen places are planned, so the work moves on to editing
            chosen = [(p['id'], '') for p in state['places'] if p['reason'] == 'chosen'] or [(state['places'][0]['id'], '')]
            _journal.set_plan(state, chosen)
            _tests.save_verified(root)
            out += (' The chosen places are planned now, so edit them.\n\n'
                    + _window(root, state, state['current']))
        elif state['step'] == 'D1' and state['candidates'] and state['repeats'] >= 2:
            # stuck on choosing: the first candidate is opened, so the work moves on (another can be chosen later)
            out += ' The first candidate is opened now.\n\n' + d1(root, state, [state['candidates'][0]['id']])
        else:
            out += f' The call to make now is different: {now}' 
    else:
        try:
            out = STEPS[state['step']](root, state, args)
        except Exception as e:      # never a traceback for the model: say what happened and the call to make
            _state.record({'error': f'{type(e).__name__}: {e}'})
            out = (f'The script hit an internal error ({type(e).__name__}: {str(e)[:200]}). Make the call below; '
                   'if it fails the same way, call submit_patch.')
        state['answer'] = out.strip().split('\n')[0][:300]
        _journal.remember(state, args, state['answer'])
        if state['step'] == 'D3' and _args.edit(args) is None and len(out) < 6000:
            full = state.setdefault('full_answers', {})       # questions' answers, to give again after compaction
            full[repr([a.strip() for a in args])] = out.strip()
            for k in list(full)[:-4]:
                del full[k]
    _state.save('journal', state)
    out = out.rstrip()
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS - 2500] + '\n      ... (output cut) ...\n' + out[-2400:]
    print(out + '\n\n' + _journal.next_call(state))


if __name__ == '__main__':
    main(sys.argv[1:])
