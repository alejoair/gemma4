"""locate.py <words from the problem statement>

Deterministic code search. Extracts identifiers, error messages and option names from the text, scores every
function and class of the package's source files (tests and docs excluded), and prints the best candidates
with their code, so the caller only has to pick one.
"""
import ast
import collections
import difflib
import math
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (WORD, clip, enclosing, find_definitions, find_symbol, graph_id, is_doc_path, is_symbol_name, iter_py, parse, read_text,  # noqa: E402
                     remember_candidate, repeat_guard, repo_root, symbols)

STOP = set('''a an and are as at be been but by can could did do does for from had has have how i if in into is it its
may might more most must no not of on or our should so some such than that the their them then there these they this
those to too use used uses using via was we were what when where which while who why will with would you your also
only just like make makes new now one two get set add see bug fix issue error errors problem pull request description
closes close fixes related example examples current currently instead expected actual behavior behaviour value values
return returns returned call called calls true false none self cls def class import python version please thanks'''.split())


def extract_terms(text):
    """Weighted search terms: code-looking tokens weigh more than plain words."""
    terms = collections.OrderedDict()

    def add(t, w):
        t = t.strip().strip('.,:;()[]{}"\'`')
        if len(t) < 3 or t.lower() in STOP:
            return
        terms[t] = max(terms.get(t, 0), w)

    for m in re.findall(r'`([^`]{2,80})`', text):
        for part in re.split(r'[\s(),=]+', m):
            if part:
                add(part.split('.')[-1], 5)
                if '.' in part:
                    add(part, 4)
    for tok in re.findall(r'[A-Za-z]+(?:-[A-Za-z]+)+', text):
        add(tok, 2)
        add(tok.replace('-', '_'), 3)
    for m in re.findall(r'"([^"]{6,80})"|\'([^\']{6,80})\'', text):
        phrase = m[0] or m[1]
        if ' ' in phrase:
            add(phrase, 4)
    for tok in re.findall(r'\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b', text):
        add(tok, 3)  # dotted names (pydantic.v1, Console.print) are searched whole, not only by their parts
    for tok in WORD.findall(text):
        if '_' in tok.strip('_') or re.search(r'[a-z][A-Z]', tok) or re.match(r'^[A-Z][a-z]+[A-Z]', tok):
            add(tok, 4)
        elif tok.isupper() and len(tok) > 3:
            add(tok, 3)
        elif tok[:1].isupper() and len(tok) >= 3:
            add(tok, 2)
        elif len(tok) >= 5:
            add(tok.lower(), 1)
    return terms


def add_close_identifiers(terms, sources, text=''):
    """For each code-like term that appears nowhere in the source (a plural, a wrong class name), add the closest
    identifiers that do exist, so a statement that says `Parameters.empty` still finds `param.empty` / `Parameter`."""
    # Same case rule as the scoring below: code-like terms (weight > 1) must match with their exact case.
    missing = [t for t, w in terms.items() if w >= 2 and ' ' not in t and not any(t in src for src in sources.values())]
    if not missing:
        return {}
    vocab = set()
    for src in sources.values():
        vocab.update(WORD.findall(src))
    vocab = sorted(v for v in vocab if len(v) >= 3)
    lower = {}
    for v in vocab:
        lower.setdefault(v.lower(), v)
    replaced = {}
    for head, attr in re.findall(r'\b([A-Za-z_]\w*)\.([A-Za-z_]\w+)\b', text):
        # `Missing.attr`: the attribute access is what the code really contains (param.empty for Parameters.empty).
        if head in missing and any('.' + attr in src for src in sources.values()):
            terms['.' + attr] = max(terms.get('.' + attr, 0), terms[head])
            replaced.setdefault(head, []).append('.' + attr)
    for t in missing:
        base = t.split('.')[-1]
        found = []
        for cand in (base[:-1] if base.endswith('s') else '', base.lower()):
            if cand and cand.lower() in lower:
                found.append(lower[cand.lower()])
        found += difflib.get_close_matches(base, vocab, n=3, cutoff=0.8)
        found = list(dict.fromkeys(f for f in found if f != t))[:3]
        if found:
            replaced[t] = replaced.get(t, []) + found
            for f in found:
                terms.setdefault(f, max(1, terms[t] - 1))
    return replaced


def main():
    text = ' '.join(sys.argv[1:]).strip()
    if not text:
        print('usage: locate.py <words from the problem statement>')
        return
    args = sys.argv[1:]
    if len(args) == 2 and args[0].endswith('.py') and is_symbol_name(args[1]) and \
            find_symbol(symbols(parse(read_text(repo_root(), args[0]))), args[1], strict=True):
        # [file, symbol] is a show.py request: hand it over so the right symbol is shown and remembered.
        show = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'show.py')
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable, show] + args)
    repeat_guard('pick the best candidate from the earlier output and write the report, or run show.py <file> <symbol>.')
    root = repo_root()
    shell = re.match(r'\s*(find|grep|ls|cat|rg|git)\s', text)
    if shell:
        # A shell command given to locate.py: search for the quoted words it was looking for instead.
        words = grep_patterns(text)
        print(f'locate.py is not a shell: "{shell.group(1)}" commands run with the run_command tool. '
              + (f'Searching the code for {", ".join(words)} instead.' if words else ''))
        if not words:
            print('NEXT: call locate.py with the names or words to find, for example ["split_cells", "Segment"].')
            return
        text = ' '.join(words)
        sys.argv[1:] = words
    m = re.fullmatch(r'(?:async\s+)?(?:class|def)\s+([A-Za-z_][\w.]*)\W*', text)
    if m:
        text = m.group(1)
        sys.argv[1:] = [text]
    if len(sys.argv) == 2 and is_symbol_name(text):
        defs = find_definitions(root, text)
        if defs:
            rel, (name, kind, start, end) = defs[0]
            lines = read_text(root, rel).splitlines()
            remember_candidate(rel, name, start, end, lines[start - 1:end])
            out = [f'Definition of {text}: {rel} :: {name} ({kind}) lines {start}-{end}  graph id: {graph_id(rel, name)}']
            if len(defs) > 1:
                out.append('Other definitions: ' + ', '.join(f'{r} :: {s[0]}' for r, s in defs[1:]))
                if any(s[0] == name and r != rel for r, s in defs[1:]):
                    out.append('The same code is in several files (for example sync and async versions): a fix '
                               'there must change every copy.')
            out += ['----- code -----'] + [f'{n:>5}|{l}' for n, l in enumerate(lines[start - 1:min(end, start + 59)], start)]
            if end > start + 59:
                out.append(f'----- {end - start - 59} more lines: show.py {rel} {start + 60} {end} -----')
            out.append('NEXT: if this function implements the behaviour, write the report from this code; '
                       'otherwise run callers.py ' + text.split('.')[-1] + ' to find the caller that prepares its input.')
            print(clip('\n'.join(out)))
            return
    scoped = [a for a in sys.argv[1:] if a.endswith('.py') and os.path.isfile(os.path.join(root, a))]
    if scoped:
        # [file, words...]: search only inside that file and list every matching line with its number.
        words = [a for a in sys.argv[1:] if a not in scoped]
        find_in_file(root, scoped[0], words)
        return
    terms = extract_terms(text)
    if not terms:
        print('No searchable words found. Pass function names, class names or error text from the problem statement.')
        print('NEXT: run locate.py again with the identifiers, option names or error message of the statement.')
        return
    ranked, scores, hits, hit_lines, info, file_scores, replaced, strong, sources = rank(root, text, terms)
    if not scores:
        print('No matches in source files for: ' + ', '.join(list(terms)[:12]))
        print('NEXT: run locate.py again with other names from the statement (an error message, option or class name).')
        return
    out = ['Search terms: ' + ', '.join(list(terms)[:15])]
    if replaced:
        out.append('Not in the code, searched the closest names instead: ' +
                   '; '.join(f'{t} -> {", ".join(v)}' for t, v in replaced.items()))
    out.append('')
    for n, key in enumerate(ranked[:5], 1):
        rel, name = key
        kind, start, end = info[key]
        out.append(f'#{n} {rel} :: {name} ({kind}, lines {start}-{end}) graph id: {graph_id(rel, name)} '
                   f'score={scores[key]:.1f} matched={sorted(hits[key])}')
        for i, l in hit_lines[key][:3]:
            out.append(f'     {i}: {l}')
    best_rel, best_name = ranked[0]
    kind, start, end = info[ranked[0]]
    src_lines = read_text(root, best_rel).splitlines()
    body = src_lines[start - 1:min(end, start + 39)]
    if best_name != '<module>':
        remember_candidate(best_rel, best_name, start, end, src_lines[start - 1:end], weak=True)
    save_ranking(ranked, scores, hits, info)
    out += ['', f'Code of #1 ({best_rel} lines {start}-{min(end, start + 39)}):']
    out += [f'{n:>5}|{l}' for n, l in enumerate(body, start)]
    if end > start + 39:
        out.append(f'    ... ({end - start - 39} more lines; use show.py {best_rel} {best_name})')
    out += ['', 'NEXT: pick the candidate whose code implements the behaviour in the statement. '
            'To see another candidate in full run show.py <file> <symbol>. '
            f'Most matching files: {", ".join(f for f, _ in file_scores.most_common(3))}']
    if strong:
        missing = [t for t in strong if not any(t in hits[k] for k in ranked[:5])]
        if missing:
            out.append('Terms not found in the top candidates: ' + ', '.join(missing[:8]))
    new_names = [t for t, w in terms.items() if w >= 3 and ' ' not in t and re.fullmatch(r'[A-Za-z_][\w.]*', t)
                 and not any(re.search(r'(?<![\w])' + re.escape(t.split('.')[-1]) + r'\b', src) for src in sources.values())]
    if new_names:
        out.append('Not anywhere in the code: ' + ', '.join(new_names[:6]) + '. If the statement asks for them, they '
                   'are new names the fix must create (or a rename of existing code); check the spelling too.')
    if best_name != '<module>':
        twins = [r for r, sym in find_definitions(root, best_name, limit=4) if r != best_rel and sym[0] == best_name]
        if twins:
            out.append(f'{best_name} of #1 is also defined in {", ".join(twins)}: the same code in several files '
                       '(for example sync and async versions); a fix there must change every copy.')
    imports = importing_files(sources, [t for t in terms if '.' in t.strip('.')])
    if imports:
        # Before the closing NEXT lines, so clipping a long output keeps both.
        nxt = next(i for i, l in enumerate(out) if l.startswith('NEXT'))
        out.insert(nxt, 'Files that import ' + '; '.join(f'{t}: {", ".join(fs)}' for t, fs in imports.items()))
    print(clip('\n'.join(out)))


SUB = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+')
K1, B = 1.2, 0.75          # BM25 constants (term saturation, length normalisation)
NAME_W, PROSE_W = 3.0, 0.4  # BM25F field weights: the symbol's name, comments and long strings (docstrings, Doc texts)  # BM25F field weights: the symbol's name, comments and long strings (docstrings, Doc texts)


def stem(word):
    """A light stemmer: plural and verb endings, so 'wraps', 'wrapped' and 'wrapper' meet 'wrap'."""
    w = word.lower()
    for suf in ('ing', 'ers', 'ies', 'ed', 'er', 'es', 's'):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not w.endswith(('ss', 'us', 'is')):
            w = w[:-len(suf)] + ('y' if suf == 'ies' else '')
            break
    if len(w) > 4 and w[-1] == w[-2] and w[-1] not in 'aeiouls':
        w = w[:-1]  # wrapp -> wrap
    return w


def subtokens(line):
    """Stemmed words of a line of code: identifiers split at '_' and case changes (_unwrapped_call -> unwrap, call;
    getHTTPHeader -> get, http, header), plus 'unX' -> 'X' (unwrap also meets wrap)."""
    out = []
    for ident in WORD.findall(line):
        for part in SUB.findall(ident):
            if len(part) < 3:
                continue
            st = stem(part)
            out.append(st)
            if st.startswith('un') and len(st) >= 6:
                out.append(st[2:])
    return out


def prose_lines(tree):
    """Line numbers inside string constants that span several lines (docstrings, Doc("...") texts)."""
    out = set()
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and getattr(node, 'end_lineno', None) \
                and node.end_lineno > node.lineno:
            out.update(range(node.lineno, node.end_lineno + 1))
    return out


def owner_map(syms, n_lines):
    """For each line, the innermost function or class that contains it (None at module level)."""
    owner = [None] * (n_lines + 2)
    for sym in sorted(syms, key=lambda x: -(x[3] - x[2])):
        for i in range(sym[2], min(sym[3], n_lines) + 1):
            owner[i] = sym
    return owner


def rank(root, text, terms, sources=None):
    """BM25F over every function and class of the source (each one a document made of its own lines; docs and
    scripts at half weight): (ranked keys, scores, hits, hit_lines, info, file_scores, replaced, strong, sources).
    Plain words of the statement match stemmed sub-words of the code; code-like terms (weight > 1) match exactly.
    BM25 normalises by length and saturates repeated words, so a long function that repeats common words of the
    statement does not outrank the short one that uses its rare names."""
    if sources is None:
        sources = {rel: read_text(root, rel) for rel in iter_py(root, docs=True)}
    replaced = add_close_identifiers(terms, sources, text)
    strong = [t for t, w in terms.items() if w >= 3]
    plain = {t: stem(t) for t, w in terms.items() if w == 1 and ' ' not in t}
    docs = {}  # key -> {'len': weighted length, 'tf': Counter(term -> weighted count), 'lines': [(i, text)]}
    info = {}
    for rel, src in sources.items():
        if not src:
            continue
        exact = [t for t in terms if t not in plain and t in src]
        if not exact and not plain:
            continue
        tree = parse(src)
        syms = symbols(tree)
        prose = prose_lines(tree)
        lines = src.splitlines()
        owner = owner_map(syms, len(lines))
        file_weight = 0.5 if is_doc_path(rel) else 1.0
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(('import ', 'from ')):
                continue
            sym = owner[i]
            key = (rel, sym[0] if sym else '<module>')
            if key not in docs:
                docs[key] = {'len': 0.0, 'tf': collections.Counter(), 'lines': [], 'w': file_weight}
                info[key] = (sym[1], sym[2], sym[3]) if sym else ('module', i, i)
            d = docs[key]
            is_def = bool(sym) and i == sym[2]
            fw = NAME_W if is_def else PROSE_W if (i in prose or stripped.startswith('#')) else 1.0
            words = subtokens(line)
            d['len'] += len(words) * (1.0 if fw == NAME_W else fw)
            matched = False
            if plain:
                bag = collections.Counter(words)
                for t, st in plain.items():
                    if bag.get(st):
                        d['tf'][t] += bag[st] * fw
                        matched = True
            for t in exact:
                if t in line:
                    d['tf'][t] += fw
                    matched = True
            if matched and len(d['lines']) < 4:
                d['lines'].append((i, stripped[:120]))
    n = max(len(docs), 1)
    avg = sum(d['len'] for d in docs.values()) / n or 1.0
    df = collections.Counter(t for d in docs.values() for t in d['tf'])
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}
    scores = collections.Counter()
    hits = collections.defaultdict(set)
    hit_lines = collections.defaultdict(list)
    file_scores = collections.Counter()
    for key, d in docs.items():
        if not d['tf']:
            continue
        norm = K1 * (1 - B + B * d['len'] / avg)
        sc = sum(terms[t] * idf[t] * tf * (K1 + 1) / (tf + norm) for t, tf in d['tf'].items())
        sc *= d['w'] * (0.3 if key[1] == '<module>' else 1.0)
        scores[key] = sc
        hits[key] = set(d['tf'])
        hit_lines[key] = d['lines']
        file_scores[key[0]] += sc
    ranked = sorted(scores, key=lambda k: (-scores[k], k))
    return ranked, scores, hits, hit_lines, info, file_scores, replaced, strong, sources


def save_ranking(ranked, scores, hits, info):
    """The top candidates with their scores, for the journal: when #2 scores close to #1, both are shown before
    the code to change is chosen."""
    import json
    from _common import _state_path
    top = []
    for rel, name in ranked[:3]:
        if name == '<module>':
            continue
        kind, start, end = info[(rel, name)]
        top.append({'file': rel, 'symbol': name, 'start': start, 'end': end,
                    'score': round(scores[(rel, name)], 2)})
    try:
        with open(_state_path('ranking.json'), 'w') as fh:
            json.dump(top, fh)
    except OSError:
        pass


def grep_patterns(command):
    """The search patterns of grep/rg inside a shell command (the first non-option argument after each grep)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    found = []
    for i, tok in enumerate(tokens):
        if tok in ('grep', 'rg', 'egrep'):
            for nxt in tokens[i + 1:]:
                if not nxt.startswith('-'):
                    if nxt not in found:
                        found.append(nxt)
                    break
    return found


def find_in_file(root, rel, words):
    lines = read_text(root, rel).splitlines()
    syms = symbols(parse('\n'.join(lines)))
    words = [w for w in words if w.strip()]
    if not words:
        print(f'No words given to search in {rel}.')
        print(f'NEXT: call locate.py with [{rel!r}, word, ...] or show.py [{rel!r}, symbol].')
        return
    hits = []
    for i, line in enumerate(lines, 1):
        if any(w.lower() in line.lower() for w in words):
            enc = enclosing(syms, i)
            hits.append(f'{i:>5}|{line.rstrip()}    [{enc[0] if enc else "<module>"}]')
    if not hits:
        print(f'None of {words} appears in {rel}.')
        print('NEXT: run locate.py with the words alone to search the whole repository.')
        return
    out = [f'Lines of {rel} that contain {", ".join(words)} ({len(hits)} lines; [enclosing function]):'] + hits[:40]
    if len(hits) > 40:
        out.append(f'... and {len(hits) - 40} more lines; use more specific words.')
    out.append(f'NEXT: run show.py [{rel!r}, symbol] or [{rel!r}, "start-end"] around the line you need.')
    print(clip('\n'.join(out)))


def importing_files(sources, modules):
    """For dotted module-like terms, the source files whose import lines mention them (import lines are not scored)."""
    found = {}
    for t in modules:
        pat = re.compile(r'^\s*(?:from\s+' + re.escape(t) + r'\b|import\s+' + re.escape(t) + r'\b)', re.M)
        hits = sorted((rel for rel, src in sources.items() if pat.search(src)), key=lambda r: (is_doc_path(r), r))
        if hits:
            found[t] = hits[:6]
    return found


if __name__ == '__main__':
    main()
