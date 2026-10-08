"""hints.py <the sentences or bullets of the problem statement that ask for something>

Turns the statement into a numbered list of requirements. For every name a requirement mentions it says where it
is defined (and every twin definition, such as the sync and async versions of the same code) or that it does not
exist yet, so it is a new name the fix must create. The list is saved for the journal, which shows which
requirements the edits already cover. Then a rule-based checklist of what a fix of that kind usually touches.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402,F401  (tees output to the call log)

REQS = _common._state_path('requirements.json')
DEFS = []
CODE_NAME = re.compile(r'`([^`]{2,60})`|\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+|[A-Za-z]\w*_\w+|_\w+|[a-z]+[A-Z]\w*|[A-Z][a-z0-9]+[A-Z]\w*)\b')


def split_items(args):
    """The requirement texts: each bullet or numbered line of a multi-line item, else each sentence."""
    items = []
    for a in args:
        lines = [l.strip() for l in a.replace('\\n', '\n').splitlines() if l.strip()]
        bullets = [re.sub(r'^([-*\u2022]|\d+[.)])\s+', '', l) for l in lines if re.match(r'^([-*\u2022]|\d+[.)])\s+', l)]
        if bullets:
            items += bullets
            continue
        for l in lines:
            items += [x.strip() for x in re.split(r'(?<=[.!?])\s+(?=[A-Z`])', l) if len(x.strip()) > 3]
    return list(dict.fromkeys(items))[:10]


def names_in(text):
    out = []
    for m in CODE_NAME.finditer(text):
        name = (m.group(1) or m.group(2)).strip().strip('()').lstrip('.')
        name = re.sub(r'\(.*$', '', name)
        if re.fullmatch(r'[A-Za-z_][\w.]*', name) and len(name) > 2 and not name.endswith('.py') and name not in out:
            out.append(name)
    return out[:6]


def facts_for(root, name, sources):
    """Where the name is defined (all twins), where it is used, or that it is new."""
    defs = []
    for d in _common.find_definitions(root, name, limit=4):
        if (d[1][0] == name or d[1][0].endswith('.' + name) or '.' not in name) and d not in defs:
            defs.append(d)
    if defs:
        DEFS.extend({'file': rel, 'symbol': q, 'start': s, 'end': e} for rel, (q, _, s, e) in defs)
        places = [f'{rel} :: {q} lines {s}-{e}' for rel, (q, _, s, e) in defs]
        twins = len({q for _, (q, _, _, _) in defs}) < len(defs)
        return (f'{name}: defined in ' + ' and in '.join(places) +
                (' (the same code in several files: change every one of them)' if twins else ''), [d[0] for d in defs])
    if '.' in name:
        # Owner.new_member: the member is new, but the owner exists; the place to add it is the owner.
        owner = name.rsplit('.', 1)[0]
        odefs = [d for d in _common.find_definitions(root, owner, limit=4) if d[1][0] == owner.split('.')[-1] or
                 d[1][0].endswith('.' + owner.split('.')[-1]) or d[1][0] == owner]
        odefs = list(dict.fromkeys(odefs))
        if odefs and not any(re.search(r'\b' + re.escape(name.split('.')[-1]) + r'\b', _common.read_text(root, r))
                             for r, _ in odefs):
            DEFS.extend({'file': rel, 'symbol': q, 'start': s, 'end': e} for rel, (q, _, s, e) in odefs)
            return (f'{name}: {name.split(".")[-1]} is NOT in {owner} yet: add it to ' + ' and to '.join(
                f'{rel} :: {q} lines {s}-{e}' for rel, (q, _, s, e) in odefs), [d[0] for d in odefs])
    tail = name.split('.')[-1]
    pat = re.compile(r'(?<![\w.])' + re.escape(tail) + r'\b' if '.' not in name else re.escape(name))
    for rel, src in sorted(sources.items(), key=lambda kv: _common.is_doc_path(kv[0])):
        m = pat.search(src)
        if m:
            line = src.count('\n', 0, m.start()) + 1
            return f'{name}: used in {rel} line {line} (not a function or class)', [rel]
    return (f'{name}: NOT in the code yet: a new name the statement introduces, so the fix must create it '
            '(or rename the existing code to it); check the spelling too', [])


IMPERATIVE = re.compile(r"(^|[:;,]\s*)(add|fix|escape|remove|support|change|allow|make|use|return|raise|handle|rename|"
                        r"deprecate|refactor|avoid|prevent|ensure|always|never|close|don'?t|do not|keep|update|set|"
                        r"validate|convert|accept|reject|include|exclude|show|hide|read|write|emit|warn)\b", re.I)


def likely_place(root, text, sources):
    """(file, symbol, start, end) of the best locate.py candidate for a sentence, or None."""
    try:
        import locate
        terms = locate.extract_terms(text)
        if not terms:
            return None
        ranked, scores, hits, _, info, _, _, _, _ = locate.rank(root, text, terms, dict(sources))
    except Exception:  # noqa: BLE001
        return None
    for rel, name in ranked[:3]:
        if name != '<module>' and not _common.is_doc_path(rel):
            kind, start, end = info[(rel, name)]
            return rel, name, start, end
    return None


def requirements(args):
    items = split_items(args)
    if len(items) < 2 and not any(names_in(i) for i in items):
        return []
    root = _common.repo_root()
    sources = {rel: _common.read_text(root, rel) for rel in _common.iter_py(root, docs=True)}
    reqs = []
    for n, item in enumerate(items, 1):
        names = names_in(item)
        facts, places, tracked = [], [], []
        creates = re.search(r'\b(add|adds|added|create|new|introduce|rename|support)\b|->', item, re.I) and \
            not re.search(r"\b(don'?t|do not|never|no longer|remove|stop)\b", item, re.I)
        DEFS.clear()
        for name in names:
            fact, files = facts_for(root, name, sources)
            facts.append(fact)
            places += [f for f in files if f not in places]
            # The journal checks a name on the edits only when it is code-like (backticks, a dot or an underscore;
            # not a prose word such as OpenAPI) and exists, or when the requirement creates it.
            code_like = f'`{name}' in item or '`.' + name.split('.')[-1] in item or '.' in name or '_' in name
            if code_like and (files or creates):
                tracked.append(name.split('.')[-1])
        if not tracked and IMPERATIVE.search(item):
            # An instruction that names no code: its most likely place, by the same search as locate.py, so the
            # journal can tell whether an edit covers it.
            m = IMPERATIVE.search(item)
            clause = re.split(r'[:;.]\s', item[m.start():] + ' ', maxsplit=1)[0]
            place = likely_place(root, clause, sources)
            if place:
                rel, qual, start, end = place
                facts.append(f'most likely place (search on this sentence): {rel} :: {qual} lines {start}-{end}')
                DEFS.append({'file': rel, 'symbol': qual, 'start': start, 'end': end})
                places.append(rel)
                tracked.append(qual.split('.')[-1])
        reqs.append({'id': n, 'text': item[:200], 'names': tracked, 'places': places, 'facts': facts,
                     'defs': list(DEFS)})
    return reqs

RULES = [
    (r'\b(error|exception|raise[sd]?|traceback|warning)\b',
     'The statement is about an error or warning: find where it is raised or emitted '
     '(locate.py with the exact message text or the exception class) and fix the condition that triggers it.'),
    (r'\bdeprecat',
     'Deprecation: emit warnings.warn(..., DeprecationWarning, stacklevel=2) where the old usage enters, keep the old '
     'behaviour working, and look for an existing deprecation helper in the package (locate.py deprecat).'),
    (r'\b(default|defaults)\b',
     'Default value: find the constant or the keyword default in the function signature; check every function that '
     'forwards the same parameter so the defaults stay consistent.'),
    (r'\b(parameter|argument|option|flag|kwarg|keyword)\b',
     'New or changed parameter: update the function signature, the place that uses the value, the docstring, and '
     'every wrapper or public API function that forwards it (callers.py <function>).'),
    (r'\b(type hint|typing|annotation|mypy|pyright|overload)\b',
     'Typing change: edit the annotations (and any overloads or stub files .pyi); runtime behaviour usually stays.'),
    (r'\b(regression|used to work|no longer|broke|broken since)\b',
     'Regression: the fix is usually a small condition that a recent change made too strict or too loose; compare '
     'the branch conditions in the located function.'),
    (r'\b(url|path|uri|slash|separator)\b',
     'URL/path handling: check where the string is normalised (split, join, strip, quote) and also the caller that '
     'builds the value before passing it on (callers.py).'),
    (r'\b(env|environment variable|environ|[A-Z][A-Z0-9]*_[A-Z0-9_]+)\b',
     'Environment variable: find the os.environ / getenv reads (locate.py environ) and keep the precedence between '
     'explicit arguments and variables. For a new or boolean-like variable handle each value explicitly: "0" turns '
     'the feature off, "1" turns it on, an empty string counts as unset, and any other value keeps the default '
     'behaviour; check how related variables (for example NO_COLOR, FORCE_COLOR) treat empty values too.'),
    (r'\b(async|await|asyncio|anyio|sync)\b',
     'Sync/async: if the package has both sync and async versions of the code, apply the same change to both files.'),
    (r'\b(docs?|documentation|readme|typo)\b',
     'Documentation: the change may be in .md/.rst files or docstrings; search them too (grep without --include).'),
    (r'\b(performance|slow|faster|memory|cache)\b',
     'Performance: keep the behaviour identical and change only the hot loop or add caching at the located place.'),
    (r'\b(script|release|workflow|ci|github action)\b',
     'Tooling task: the change likely lives in scripts/, .github/workflows/ or pyproject.toml rather than the package.'),
]


def main():
    text = ' '.join(sys.argv[1:])
    if not text.strip():
        print('usage: hints.py <problem statement text>')
        print('NEXT: call hints.py with the sentences or bullets of the statement that ask for something, one per item.')
        return
    _common.repeat_guard('follow the checklist printed earlier and continue your procedure.')
    reqs = requirements(sys.argv[1:])
    if reqs:
        print(f'Requirements of the statement ({len(reqs)}):')
        for r in reqs:
            print(f'{r["id"]}. {r["text"]}')
            for fact in r['facts']:
                print(f'   - {fact}')
        try:
            with open(REQS, 'w') as fh:
                json.dump(reqs, fh)
        except OSError:
            pass
        print()
    low = text.lower()
    # Lower-case words match the lowered text; patterns with capitals (ENV_VAR names) match the original text.
    found = [msg for pat, msg in RULES if re.search(pat, low) or re.search(pat, text)]
    names = re.findall(r'`([^`]{2,60})`', text)
    print('Checklist for this statement:')
    for i, msg in enumerate(found[:6], 1):
        print(f'{i}. {msg}')
    if not found:
        found = ['Find the function that produces the behaviour described, change it minimally, keep its signature.']
        print(f'1. {found[0]}')
    print(f'{len(found[:6]) + 1}. Keep exact names, messages and exception types written in the statement.')
    if names:
        print('Names written in the statement: ' + ', '.join(dict.fromkeys(names))[:400])
    print('NEXT: continue your procedure and cover every item of this checklist that applies to the statement.')


if __name__ == '__main__':
    main()
