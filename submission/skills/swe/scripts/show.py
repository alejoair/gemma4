"""show.py <file> <symbol>   |   show.py <file> <start_line> <end_line>

Prints the exact source of a function, method or class (found with the AST), or of a line range, verbatim and
without line-number prefixes, so a piece of it can be copied as old_string for edit_file.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, find_definitions, find_symbol, parse, read_text, repeat_guard, repo_root, symbols  # noqa: E402

MAX_LINES = 90


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
        print('usage: show.py <file> <symbol>  or  show.py <file> <start_line> <end_line>')
        return
    rel = (args[0][2:] if args[0].startswith('./') else args[0]) if not os.path.isabs(args[0]) else os.path.relpath(args[0], root)
    src = read_text(root, rel)
    if not src:
        print(f'File not found or empty: {rel}. Use locate.py to find the right path.')
        return
    lines = src.splitlines()
    if len(args) >= 3 and args[1].isdigit() and args[2].isdigit():
        start, end = max(1, int(args[1])), min(len(lines), int(args[2]))
        label = f'{rel} lines {start}-{end}'
    elif args[1].isdigit():
        start, end = max(1, int(args[1]) - 15), min(len(lines), int(args[1]) + 25)
        label = f'{rel} lines {start}-{end}'
    else:
        syms = symbols(parse(src))
        found = find_symbol(syms, args[1])
        if not found:
            names = [s[0] for s in syms if args[1].split('.')[-1].lower() in s[0].lower()][:10]
            print(f'Symbol {args[1]} not found in {rel}.' + (f' Similar: {", ".join(names)}' if names else
                  ' Top-level symbols: ' + ', '.join(s[0] for s in syms if '.' not in s[0])[:600]))
            return
        name, kind, start, end = found[0]
        label = f'{rel} :: {name} ({kind}) lines {start}-{end}'
        if len(found) > 1:
            label += '  [also: ' + ', '.join(f'{n} {s}-{e}' for n, _, s, e in found[1:4]) + ']'
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
