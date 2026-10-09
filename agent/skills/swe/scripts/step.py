"""The only script the model calls. Each call is the model's decision for the current step; the answer is what that
step produced, a fixed verdict when something was checked, and the exact next call (docs/design_single.md, v1).

    S0  args [statement, search terms...]   -> requirements and candidates C1..C10
    D1  args ["C2", "C5"]                    -> the chosen code and the related places P1..Pk
    D2  args ["P1: what changes", ...]       -> the first planned place, numbered
    D3  args ["P1", first, last, new lines]  -> edit, syntax, existing tests; the next place, or the finish
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
EDIT_LINES = 50         # the edit window: the place's head and the part its plan line is about
CLASS_OUTLINE_AT = 90   # a chosen class longer than this is shown as an outline of its members
MODULE = '<module>'
MAX_CHARS = 12000       # a safety cap on one answer (about 3,500 tokens); the end with NEXT is always kept


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
    """The place of plan entry i, ready to edit."""
    entry = state['plan'][i]
    p = _place(state, entry['place'])
    _refresh(root, state, p['rel'])
    p = _place(state, entry['place'])
    done = sum(1 for e in state['plan'] if e['status'] != 'todo')
    head = f'EDIT {p["id"]} ({done + 1} of {len(state["plan"])} planned): {p["rel"]} :: {p["name"]}. Plan: ' \
           f'{entry["intent"]}'
    if p['name'] == '<exports>':
        n = len(_repo.read_lines(root, p['rel']))
        body = _code_view(root, p['rel'], 1, n, _focus(state, entry))
    else:
        body = _code_view(root, p['rel'], p['start'], p['end'], _focus(state, entry), limit=EDIT_LINES)
    hint = ('Send the line numbers of the lines to replace and the new lines with their full indentation. To add '
            'lines, replace the line before them with that same line followed by the new ones.')
    named = _named_lines(root, p, entry['intent'])
    if named:
        hint = 'The plan names code on ' + '; '.join(f'line {n} (`{frag}`)' for n, frag in named) + '.\n' + hint
    return f'{head}\n{body}\n{hint}'


def _named_lines(root, p, intent, limit=3):
    """[(line number, fragment)] for the code fragments in backticks of the plan line found in the place."""
    if p['name'] == '<exports>':
        start, lines = 1, _repo.read_lines(root, p['rel'])
    else:
        start, lines = p['start'], _repo.read_lines(root, p['rel'])[p['start'] - 1:p['end']]
    out = []
    for frag in re.findall(r'`([^`\n]{6,})`', intent):
        norm = re.sub(r'\s+', ' ', frag.strip())
        for i, line in enumerate(lines):
            if norm in re.sub(r'\s+', ' ', line):
                out.append((start + i, norm))
                break
    return out[:limit]


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
    return out


def d1(root, state, args):
    if _args.plan(args) == 'back':
        return _candidates_view(state) + '\n\nChoose the code to change.'
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
    limit = CODE_LINES if len(chosen) == 1 else CODE_LINES // 2
    for p in places:
        if p['reason'] != 'chosen':
            continue
        sym = table.get(p['rel'], p['name']) if p['name'] != MODULE else None
        if sym is not None and sym.kind == 'class' and sym.end - sym.start + 1 > CLASS_OUTLINE_AT:
            body = _outline(root, p['rel'], sym)
        else:
            body = _code_view(root, p['rel'], p['start'], p['end'], focus, limit=limit)
        out.append(f'{p["id"]} {p["rel"]} :: {p["name"]}\n{body}')
    new = [(r, n) for r in state['requirements'] if r['type'] == 'new' for n in r['names']]
    for r, name in new[:1]:
        owner = next((c for c in chosen if 'where the new' in c.get('line', '')), None)
        if owner is None:
            continue
        keywords = re.findall(r'(\w+)\s*=', r['text'])
        sib = _impact.sibling(table, owner['rel'], owner['name'], name.split('.')[-1], keywords)
        if sib is not None:
            out.append(f'An existing method of {owner["name"]} to model the new `{name.split(".")[-1]}` on:\n'
                       + _code_view(root, owner['rel'], sib.start, sib.end, focus, limit=60))
    places_view = '\n'.join('  ' + _place_line(p) for p in places)
    return ('\n\n'.join(out) + f'\n\nPlaces (the chosen code and the code related to it):\n{places_view}\n\n'
            'Plan the change: for each place that must change, say what changes there. Places that need no change '
            'are left out.')


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


def d3(root, state, args):
    if _args.plan(args) == 'back':
        _journal.back(state)
        return 'The edits made so far stay. ' + _candidates_view(state) + '\n\nChoose the code to change.'
    if _journal.time_is_up(state):
        state['step'] = 'D4'
        return 'TIME IS UP: no more edits are accepted. ' + _finish_view(root, state)
    e = _args.edit(args)
    colon = any(re.match(r'\s*[\["\'`「]*\s*P\d+\s*[:=]', a or '') for a in args)
    replan = _args.plan(args) if e is None and colon else []
    if replan and replan != 'back' and all(_place(state, p) for p, _ in replan):
        for pid, intent in replan:                  # a corrected plan line: the edit follows it
            state['plan'][_journal.target(state, pid)].update(intent=intent, status='todo')
        state['current'] = _journal.target(state, replan[0][0])
        return 'Plan updated. Nothing was changed in the code.\n\n' + _window(root, state, state['current'])
    if e is None:
        cur = state['current']
        again = _window(root, state, cur) if cur is not None else ''
        items = [x for x in args if x is not None]
        if len(items) < 4:
            why = f'it has {len(items)} items; it needs 4: the place id, two line numbers and the new lines.'
        else:
            why = (f'the second and third items must be line numbers like 79 and 82, not "{items[1][:40]}" and '
                   f'"{items[2][:40]}".')
        return f'The edit was not read: {why} Nothing was changed.\n' + again
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
                       f'If the plan line was wrong, send the corrected edit, or first the corrected plan line '
                       f'["{pid}: <what changes there>"].', 'BROKEN ' + ' '.join(new_failures[:3]))
    _tests.save_verified(root)
    what = 'OK' if verdict == 'OK' else 'APPLIED, NOT VERIFIED'
    head = f'{what}: {pid} lines {r.start}-{r.end} changed; {detail}.{notes}\n{shown}'
    move = _journal.edit_done(state, i)
    if move == 'next':
        return head + '\n\n' + _window(root, state, state['current'])
    return head + '\n\n' + _finish_view(root, state)


def _failed(root, state, i, message, error):
    move = _journal.edit_failed(state, i, error)
    if move == 'retry':
        return message + '\n\n' + _window(root, state, i)
    pid = state['plan'][i]['place']
    left = f'{pid} failed {state["plan"][i]["fails"]} times and keeps its last verified code.'
    if state['step'] == 'D3':
        return f'{message}\n{left}\n\n' + _window(root, state, state['current'])
    if state['step'] == 'D1':
        return f'{message}\n{left} Nothing was changed, so choose again.\n\n' + _candidates_view(state)
    return f'{message}\n{left}\n\n' + _finish_view(root, state)


def _coverage(state, root):
    """Which requirement shares names or words with an edited place (its name, its plan line, its changed lines)."""
    diff = _repo.worktree_diff(root)
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
        status = {'done': 'changed', 'skipped': 'NOT changed (its edits failed)', 'todo': 'not edited'}[e['status']]
        out.append(f'  {p["id"]} {p["rel"]} :: {p["name"]} — {status}. Plan: {e["intent"]}')
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
    return 'Nothing left to do in this step. ' + _finish_view(root, state)


STEPS = {'S0': s0, 'D1': d1, 'D2': d2, 'D3': d3, 'D4': d4}


def main(argv):
    root = _repo.repo_root()
    state = _state.load('journal') or _journal.new()
    args = _args.unpack(argv) if state['step'] in ('D1', 'D2', 'D4') else list(argv)
    _state.record({'step': state['step'], 'args': [a[:300] for a in args]})
    if state['step'] != 'S0' and _journal.repeated(state, args):
        now = _journal.next_call(state).replace('NEXT: ', '', 1)
        out = (f'STOP REPEATING: this call was already made and was not run again (its answer: '
               f'{state.get("answer", "")}).')
        if state['step'] == 'D3' and state['current'] is not None:
            # stuck on a place: a repeat counts as a failed edit, so the place is left after MAX_FAILS
            out = _failed(root, state, state['current'], out, 'repeated call')
        else:
            out += f' The call to make now is different: {now}' 
    else:
        out = STEPS[state['step']](root, state, args)
        state['answer'] = out.strip().split('\n')[0][:300]
    _state.save('journal', state)
    out = out.rstrip()
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS - 2500] + '\n      ... (output cut) ...\n' + out[-2400:]
    print(out + '\n\n' + _journal.next_call(state))


if __name__ == '__main__':
    main(sys.argv[1:])
