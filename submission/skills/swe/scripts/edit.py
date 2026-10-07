"""edit.py <file> <start> <end> <new text>   |   edit.py <file> "<start>-<end>" <new text>

Replaces lines start..end (inclusive, the numbers show.py prints) with the new text in one step, then checks the
file. An edit that breaks the syntax is undone, and the output shows the error, what the edit would have looked
like and the original lines. A good edit shows the updated lines with their numbers. To insert lines, replace one
line with itself plus the new lines; to delete lines, pass an empty new text.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, read_text, repo_root  # noqa: E402
from show import resolve_file  # noqa: E402

CONTEXT = 4
NUMBERED = re.compile(r'^\s*\d+\s*[|:] ?')


def numbered(lines, first):
    return [f'{i:>5}| {line}' for i, line in enumerate(lines, first)]


def clean_text(text):
    """The new text as file lines: real newlines (or escaped \\n when the model sent none), and without the line
    number prefixes of show.py when every line carries one."""
    if '\n' not in text and '\\n' in text:
        text = text.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines = lines[:-1]
    if lines and all(NUMBERED.match(l) for l in lines if l.strip()):
        lines = [NUMBERED.sub('', l, count=1) for l in lines]
    return lines


def parse_range(args):
    """(start, end, rest of args) from [start, end, text...] or ["start-end", text...]."""
    nums = re.findall(r'\d+', args[0]) if args and re.fullmatch(r'[\d\s,:\-]+', args[0]) else []
    if nums:
        # "79-82", "79:82", "79, 82" or a list of the lines "79,80,81,82": the first and last number.
        nums = [int(n) for n in nums]
        if len(nums) == 1 and len(args) >= 2 and args[1].strip().isdigit():
            return nums[0], int(args[1]), args[2:]
        return min(nums), max(nums), args[1:]
    if len(args) >= 2 and args[0].strip().isdigit() and args[1].strip().isdigit():
        return int(args[0]), int(args[1]), args[2:]
    if args and args[0].strip().isdigit():
        return int(args[0]), int(args[0]), args[1:]
    return None, None, args


def usage(msg):
    print(msg)
    print('usage: edit.py [file, start, end, new text] with the line numbers that show.py prints.')
    print('NEXT: call show.py [file, symbol] to see the numbered lines, then call edit.py with that range.')


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        usage('Missing arguments.')
        return
    root = repo_root()
    rel, _ = resolve_file(root, args[0])
    if rel is None:
        usage(f'File not found: {args[0]}.')
        return
    start, end, rest = parse_range(args[1:])
    if start is None:
        usage(f'Line range not understood: {args[1:3]}.')
        return
    text = '\n'.join(rest)
    if '[... text lines' in text:
        usage('The new text contains a "[... text lines ...]" marker from show.py; those lines were hidden, not code.')
        return
    path = os.path.join(root, rel)
    with open(os.path.join(root, rel), encoding='utf-8', errors='surrogateescape', newline='') as fh:
        original = fh.read()
    eol = '\r\n' if '\r\n' in original else '\n'
    lines = original.split(eol)
    trailing = original.endswith(eol)
    if trailing:
        lines = lines[:-1]
    if not (1 <= start <= end <= len(lines)):
        usage(f'Lines {start}-{end} are outside {rel}, which has {len(lines)} lines.')
        return
    new = clean_text(text)
    updated = lines[:start - 1] + new + lines[end:]
    content = eol.join(updated) + (eol if trailing else '')
    with open(path, 'w', encoding='utf-8', errors='surrogateescape', newline='') as fh:
        fh.write(content)
    shown_from = max(1, start - CONTEXT)
    new_end = start + len(new) - 1
    if rel.endswith('.py'):
        try:
            compile(content, rel, 'exec')
        except SyntaxError as e:
            with open(path, 'w', encoding='utf-8', errors='surrogateescape', newline='') as fh:
                fh.write(original)
            bad = e.lineno or start
            lo, hi = max(1, min(bad, start) - CONTEXT), min(len(updated), max(bad, new_end) + CONTEXT)
            out = [f'EDIT NOT APPLIED: it causes "SyntaxError: {e.msg}" at line {bad}. The file is unchanged.',
                   'Your edit would have looked like this:'] + numbered(updated[lo - 1:hi], lo)
            out += [f'Original lines {shown_from}-{min(len(lines), end + CONTEXT)}:'] + \
                numbered(lines[shown_from - 1:end + CONTEXT], shown_from)
            out.append(f'NEXT: call edit.py again for lines {start}-{end} with corrected text (check indentation, '
                       'brackets and quotes).')
            print(clip('\n'.join(out), 5000))
            return
    hi = min(len(updated), new_end + CONTEXT)
    out = [f'EDITED {rel}: lines {start}-{end} replaced by {len(new)} line(s). Updated code:']
    out += numbered(updated[shown_from - 1:hi], shown_from)
    out.append('NEXT: if more lines must change, call edit.py again (line numbers below this edit have shifted '
               f'by {len(new) - (end - start + 1)}); otherwise run check.py.')
    print(clip('\n'.join(out), 5000))


if __name__ == '__main__':
    main()
