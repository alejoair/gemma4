"""locate.py <words from the problem statement>

Deterministic code search. Extracts identifiers, error messages and option names from the text, scores every
function and class of the package's source files (tests and docs excluded), and prints the best candidates
with their code, so the caller only has to pick one.
"""
import collections
import difflib
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (WORD, clip, enclosing, find_definitions, graph_id, is_doc_path, is_symbol_name, iter_py, parse, read_text,  # noqa: E402
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
    if len(args) == 2 and args[0].endswith('.py') and is_symbol_name(args[1]):
        # [file, symbol] is a show.py request: hand it over so the right symbol is shown and remembered.
        show = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'show.py')
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable, show] + args)
    repeat_guard('pick the best candidate from the earlier output and write the report, or run show.py <file> <symbol>.')
    root = repo_root()
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
            out += ['----- code -----'] + [f'{n:>5}| {l}' for n, l in enumerate(lines[start - 1:min(end, start + 59)], start)]
            if end > start + 59:
                out.append(f'----- {end - start - 59} more lines: show.py {rel} {start + 60} {end} -----')
            out.append('NEXT: if this function implements the behaviour, write the report from this code; '
                       'otherwise run callers.py ' + text.split('.')[-1] + ' to find the caller that prepares its input.')
            print(clip('\n'.join(out)))
            return
    terms = extract_terms(text)
    if not terms:
        print('No searchable words found. Pass function names, class names or error text from the problem statement.')
        print('NEXT: run locate.py again with the identifiers, option names or error message of the statement.')
        return
    # Docs examples (docs_src/) and repository scripts (scripts/) are searched too, at half weight: some issues are
    # fixed there (tutorial code, release scripts).
    sources = {rel: read_text(root, rel) for rel in iter_py(root, docs=True)}
    replaced = add_close_identifiers(terms, sources, text)
    strong = [t for t, w in terms.items() if w >= 3]
    files = {}
    df = collections.Counter()
    for rel, src in sources.items():
        if not src:
            continue
        low = src.lower()
        # Same case rule as the scoring below: plain words ignore case, code-like terms must match exactly.
        present = [t for t in terms if (t.lower() in low if terms[t] == 1 else t in src)]
        if present:
            files[rel] = (src, present)
            df.update(present)
    n_files = max(len(files), 1)
    idf = {t: math.log(1 + n_files / df[t]) for t in df}
    scores = collections.Counter()
    hits = collections.defaultdict(set)
    hit_lines = collections.defaultdict(list)
    info = {}
    file_scores = collections.Counter()
    for rel, (src, present) in files.items():
        syms = symbols(parse(src))
        lines = src.splitlines()
        file_weight = 0.5 if is_doc_path(rel) else 1.0
        for t in present:
            w = terms[t] * idf[t]
            pat = re.compile(re.escape(t), re.IGNORECASE if terms[t] == 1 else 0)
            for i, line in enumerate(lines, 1):
                if not pat.search(line):
                    continue
                stripped = line.strip()
                if stripped.startswith(('import ', 'from ')):
                    continue
                lw = w * file_weight * (0.4 if stripped.startswith('#') else 1.0)
                enc = enclosing(syms, i)
                if enc is None:
                    key = (rel, '<module>')
                    info.setdefault(key, ('module', i, i))
                    lw *= 0.3
                else:
                    key = (rel, enc[0])
                    info.setdefault(key, (enc[1], enc[2], enc[3]))
                if re.match(r'\s*(async\s+def|def|class)\s+' + re.escape(t) + r'\b', line):
                    lw += 3 * idf[t]
                scores[key] += lw
                hits[key].add(t)
                file_scores[rel] += lw
                if len(hit_lines[key]) < 4:
                    hit_lines[key].append((i, stripped[:120]))
    if not scores:
        print('No matches in source files for: ' + ', '.join(list(terms)[:12]))
        print('NEXT: run locate.py again with other names from the statement (an error message, option or class name).')
        return
    ranked = sorted(scores, key=lambda k: (-(scores[k] * (1 + len(hits[k]))), k))
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
    out += ['', f'Code of #1 ({best_rel} lines {start}-{min(end, start + 39)}):']
    out += [f'{n:>5}| {l}' for n, l in enumerate(body, start)]
    if end > start + 39:
        out.append(f'    ... ({end - start - 39} more lines; use show.py {best_rel} {best_name})')
    out += ['', 'NEXT: pick the candidate whose code implements the behaviour in the statement. '
            'To see another candidate in full run show.py <file> <symbol>. '
            f'Most matching files: {", ".join(f for f, _ in file_scores.most_common(3))}']
    if strong:
        missing = [t for t in strong if not any(t in hits[k] for k in ranked[:5])]
        if missing:
            out.append('Terms not found in the top candidates: ' + ', '.join(missing[:8]))
    print(clip('\n'.join(out)))


if __name__ == '__main__':
    main()
