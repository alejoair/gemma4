"""show.py <file> <symbol>   |   show.py <file> <start>-<end>   |   show.py <file> "<a line or lines of code>"

Prints the exact source of a function, method or class (found with the AST), or of a line range, verbatim and
without line-number prefixes, so a piece of it can be copied as old_string for edit_file.
"""
import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (clip, find_definitions, find_symbol, graph_id, is_symbol_name, parse, read_text,  # noqa: E402
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


def main():
    args = sys.argv[1:]
    repeat_guard('use the code printed earlier: copy old_string from it, or write your report.')
    root = repo_root()
    if len(args) == 1:
        defs = find_definitions(root, args[0])
        if not defs:
            print(f'usage: show.py <file> <symbol>. No definition of {args[0]} found; run locate.py first.')
            return
        args = [defs[0][0], args[0]]
    if len(args) < 2:
        print('usage: show.py <file> <symbol>  |  show.py <file> <start>-<end>  |  show.py <file> "<a line of code>"')
        return
    rel = (args[0][2:] if args[0].startswith('./') else args[0]) if not os.path.isabs(args[0]) else os.path.relpath(args[0], root)
    src = read_text(root, rel)
    if not src:
        print(f'File not found or empty: {rel}. Use locate.py to find the right path.')
        return
    lines = src.splitlines()
    spec = ' '.join(args[1:])
    rng = re.fullmatch(r'\s*(\d+)\s*(?:[-:,]|\s)\s*(\d+)\s*', spec)
    if rng:
        start, end = max(1, int(rng.group(1))), min(len(lines), int(rng.group(2)))
        label = f'{rel} lines {start}-{end}'
    elif args[1].isdigit():
        start, end = max(1, int(args[1]) - 15), min(len(lines), int(args[1]) + 25)
        label = f'{rel} lines {start}-{end}'
    else:
        syms = symbols(parse(src))
        found = find_symbol(syms, args[1]) if is_symbol_name(args[1]) else []
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
                print(f'Symbol or text {args[1]!r} not found in {rel}.' + (f' Similar symbols: {", ".join(names)}' if names else
                      ' Top-level symbols: ' + ', '.join(s[0] for s in syms if '.' not in s[0])[:600]))
                return
            first, last, score = hit
            start, end = max(1, first - 3), min(len(lines), last + 3)
            label = f'{rel} lines {start}-{end} (best match for the given text at lines {first}-{last}, similarity {score:.2f})'
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
