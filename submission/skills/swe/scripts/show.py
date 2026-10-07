"""show.py <file> <symbol>   |   show.py <file> <start>-<end>   |   show.py <file> "<a line or lines of code>"

Prints the exact source of a function, method or class (found with the AST), or of a line range, verbatim and
without line-number prefixes, so a piece of it can be copied as old_string for edit_file.
"""
import ast
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (SKIP_DIRS, clip, find_definitions, find_symbol, graph_id, is_symbol_name, parse, read_text,  # noqa: E402
                     remember_candidate, repeat_guard, repo_root, symbols)

MAX_LINES = 90


def best_match(lines, text):
    """(first, last, similarity) of the window of file lines closest to text, compared without indentation."""
    want = [l.strip() for l in text.replace('\\n', '\n').splitlines() if l.strip()]
    if not want:
        return None
    norm = [l.strip() for l in lines]
    n = len(want)
    best = None
    anchors = [i for i, l in enumerate(norm) if l and difflib.SequenceMatcher(None, l, want[0]).ratio() > 0.6] or range(len(norm))
    for i in anchors:
        window = norm[i:i + n]
        score = difflib.SequenceMatcher(None, '\n'.join(window), '\n'.join(want)).ratio()
        if best is None or score > best[2]:
            best = (i + 1, min(len(lines), i + n), score)
    return best if best and best[2] >= 0.5 else None


def collapse_strings(src, lines, start, end):
    """Lines start..end with the inside of long string literals (docstrings, Doc("...") texts) replaced by one
    marker line, so the code of a long, heavily documented function fits on screen."""
    hidden = {}
    texts = []
    for node in ast.walk(parse(src) or ast.Module(body=[], type_ignores=[])):
        # Only documentation text: docstrings and plain strings passed as arguments (Doc("""...""")). Never parts
        # of f-strings or assigned strings, which are code the fix may have to edit.
        if isinstance(node, ast.Expr):
            texts.append(node.value)
        elif isinstance(node, ast.Call):
            texts.extend(node.args)
    for node in texts:
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.end_lineno - node.lineno >= 3 \
                and start <= node.lineno and node.end_lineno <= end:
            hidden[node.lineno + 1] = node.end_lineno - 1
    out = []
    i = start
    while i <= end:
        if i in hidden:
            last = hidden[i]
            indent = lines[i - 1][:len(lines[i - 1]) - len(lines[i - 1].lstrip())]
            out.append((last, f'{indent}[... text lines {i}-{last} hidden ...]'))
            i = last + 1
            continue
        out.append((i, lines[i - 1]))
        i += 1
    return out


PLACEHOLDERS = {'start-end', '<start>-<end>', 'symbol', '<symbol>', 'file', '<file>', '.'}


def all_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
        for fn in filenames:
            yield os.path.relpath(os.path.join(dirpath, fn), root)


def resolve_file(root, given):
    """(path, extra symbol parts) for a file argument written as a path, a wrong path or a dotted module name."""
    p = given[2:] if given.startswith('./') else given
    p = os.path.relpath(p, root) if os.path.isabs(p) else p
    if os.path.isfile(os.path.join(root, p)):
        return p, []
    candidates = [p[:-3] + '/__init__.py' if p.endswith('.py') else p + '/__init__.py', 'src/' + p, 'lib/' + p]
    if '/' not in p and '.' in p and not p.endswith('.py'):
        parts = p.split('.')
        for k in range(len(parts), 0, -1):
            base = '/'.join(parts[:k])
            for c in (base + '.py', base + '/__init__.py', 'src/' + base + '.py', 'src/' + base + '/__init__.py'):
                if os.path.isfile(os.path.join(root, c)):
                    return c, parts[k:]
    for c in candidates:
        if os.path.isfile(os.path.join(root, c)):
            return c, []
    name = os.path.basename(p)
    same = sorted(f for f in all_files(root) if os.path.basename(f) == name)
    if len(same) == 1:
        return same[0], []
    return None, same[:5]


def main():
    # Drop shell flags and placeholders copied from the usage text; split "file symbol" given as one argument.
    args = [a for a in sys.argv[1:] if not a.startswith('-') and a.strip() not in PLACEHOLDERS]
    if len(args) == 1 and ' ' in args[0].strip() and args[0].split()[0].endswith('.py'):
        args = args[0].split(None, 1)
    repeat_guard('use the code printed earlier: copy old_string from it, or write your report.')
    root = repo_root()
    if not args:
        print('usage: show.py <file> <symbol>  |  show.py <file> <start>-<end>  |  show.py <file> "<a line of code>"')
        print('NEXT: call show.py with a file path and a function name, for example ["pkg/module.py", "Class.method"].')
        return
    looks_like_file = '/' in args[0] or re.search(r'\.(py|toml|cfg|ini|txt|md|rst|ya?ml|json)$', args[0])
    if len(args) == 1 and not looks_like_file:
        resolved, extra = resolve_file(root, args[0]) if '.' in args[0] else (None, [])
        if resolved and extra:
            args = [resolved, '.'.join(extra)]
        else:
            defs = find_definitions(root, args[0])
            if not defs:
                print(f'No definition of {args[0]} found in the source files.')
                print('NEXT: run locate.py with this name and words from the statement to find where it lives.')
                return
            args = [defs[0][0], args[0]]
    rel, extra = resolve_file(root, args[0])
    if rel is None:
        if extra:
            print(f'File not found: {args[0]}. Files with that name: {", ".join(extra)}')
            print(f'NEXT: call show.py with one of these paths, for example ["{extra[0]}"{", " + json.dumps(args[1]) if len(args) > 1 else ""}].')
        else:
            print(f'File not found: {args[0]}.')
            print('NEXT: run locate.py with the function or class name to find the right file.')
        return
    if extra and len(args) == 1:
        args = [rel, '.'.join(extra)]
    src = read_text(root, rel)
    lines = src.splitlines()
    syms = symbols(parse(src)) if rel.endswith('.py') else []
    if len(args) == 1:
        if syms:
            print(f'Symbols in {rel} (name kind lines):')
            print(clip('\n'.join(f'  {n} {k} {s}-{e}' for n, k, s, e in syms)))
            print(f'NEXT: run show.py {rel} <symbol> with one of the names above.')
            return
        args = [rel, f'1-{min(len(lines), MAX_LINES)}']
    args[0] = rel
    spec = ' '.join(args[1:])
    rng = re.fullmatch(r'\s*(\d+)\s*(?:[-:,]|\s)\s*(\d+)\s*', spec)
    if rng:
        start, end = max(1, int(rng.group(1))), min(len(lines), int(rng.group(2)))
        label = f'{rel} lines {start}-{end}'
    elif args[1].isdigit():
        start, end = max(1, int(args[1]) - 15), min(len(lines), int(args[1]) + 25)
        label = f'{rel} lines {start}-{end}'
    else:
        found = find_symbol(syms, args[1]) if is_symbol_name(args[1]) else []
        if not found and is_symbol_name(args[1]):
            # The symbol lives in another file: show it from there instead of failing.
            defs = [d for d in find_definitions(root, args[1]) if d[0] != rel]
            if defs:
                print(f'{args[1]} is not defined in {rel}; it is defined in {defs[0][0]}:')
                rel = defs[0][0]
                src = read_text(root, rel)
                lines = src.splitlines()
                syms = symbols(parse(src))
                found = find_symbol(syms, args[1])
        if found:
            name, kind, start, end = found[0]
            remember_candidate(rel, name, start, end, lines[start - 1:end])
            label = f'{rel} :: {name} ({kind}) lines {start}-{end}  graph id: {graph_id(rel, name)}'
            if len(found) > 1:
                label += '  [also: ' + ', '.join(f'{n} {s}-{e}' for n, _, s, e in found[1:4]) + ']'
        else:
            # Not a symbol: treat the argument as a piece of code (for example an old_string that edit_file did not
            # find) and show the lines of the file that match it best, exactly as they are.
            hit = best_match(lines, spec)
            if hit is None:
                names = [s[0] for s in syms if args[1].split('.')[-1].lower() in s[0].lower()][:10]
                top = ', '.join(s[0] for s in syms if '.' not in s[0])[:600]
                print(f'Symbol or text {args[1]!r} not found in {rel}.' + (f' Similar symbols: {", ".join(names)}' if names
                      else (f' Top-level symbols: {top}' if top else f' The file has {len(lines)} lines.')))
                print(f'NEXT: call show.py {rel} with one of those names, or with a line range such as 1-{min(len(lines), MAX_LINES)}.'
                      if syms or lines else 'NEXT: run locate.py with the name to find the right file.')
                return
            first, last, score = hit
            start, end = max(1, first - 3), min(len(lines), last + 3)
            label = f'{rel} lines {start}-{end} (best match for the given text at lines {first}-{last}, similarity {score:.2f})'
    if end - start + 1 > MAX_LINES and not rng and not args[1].isdigit():
        body = collapse_strings(src, lines, start, end)
        if len(body) < end - start + 1:
            out = [label, '----- code (verbatim; long text strings hidden as [...]) -----'] + [t for _, t in body[:MAX_LINES]]
            if len(body) > MAX_LINES:
                nxt = body[MAX_LINES - 1][0] + 1
                out.append(f'----- {end - nxt + 1} more lines: show.py {rel} {nxt}-{end} -----')
            else:
                out.append('----- end -----')
            out.append('NEXT: copy 3-6 consecutive lines (not a [...] line) exactly as old_string for edit_file.')
            print(clip('\n'.join(out), 6000))
            return
    shown_end = min(end, start + MAX_LINES - 1)
    out = [label, '----- code (verbatim) -----'] + lines[start - 1:shown_end]
    if shown_end < end:
        out.append(f'----- {end - shown_end} more lines: show.py {rel} {shown_end + 1} {end} -----')
    else:
        out.append('----- end -----')
    out.append('NEXT: copy 3-6 consecutive lines from the code above exactly as old_string for edit_file.')
    print(clip('\n'.join(out), 6000))


if __name__ == '__main__':
    main()
