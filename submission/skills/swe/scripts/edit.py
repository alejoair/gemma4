"""edit.py <file> <start> <end> <new text>   |   edit.py <file> "<start>-<end>" <new text>   |   edit.py <file> <old text> <new text>

Replaces lines start..end (inclusive, the numbers show.py prints) with the new text in one step, then checks the
file. An edit that breaks the syntax is undone, and the output shows the error, what the edit would have looked
like and the original lines. A good edit shows the updated lines with their numbers. To insert lines, replace one
line with itself plus the new lines; to delete lines, pass an empty new text.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, is_test_path, read_text, repo_root  # noqa: E402
from show import resolve_file  # noqa: E402

CONTEXT = 4
NUMBERED = re.compile(r'^\s*\d+\s*[|:]')


def numbered(lines, first):
    return [f'{i:>5}|{line}' for i, line in enumerate(lines, first)]


def unescape(item):
    """One argument with literal \\n, \\t and \\" escapes (and no real line break) turned into the text they mean."""
    item = item.replace('⏎', '\n')
    if '\n' not in item and '\\n' in item:
        item = item.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
    return item


def clean_text(text):
    """The new text as file lines: real newlines (or escaped \\n when the model sent none), and without the line
    number prefixes of show.py when every line carries one."""
    text = text.replace('⏎', '\n')
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


def indent_of(line):
    return len(line) - len(line.lstrip(' '))


def reindent(block, target):
    """The block shifted so that its first non-empty line starts with target spaces."""
    first = next((l for l in block if l.strip()), None)
    if first is None:
        return block
    delta = target - indent_of(first)
    if delta < 0 and any(l.strip() and indent_of(l) < -delta for l in block):
        return block
    return [(' ' * delta + l if delta > 0 else l[-delta:]) if l.strip() else l for l in block]


def compiles(content, name):
    try:
        compile(content, name, 'exec')
        return True
    except SyntaxError:
        return False


def find_block(lines, old):
    """(start, end) of the only place where the old lines appear, compared without leading/trailing spaces."""
    want = [l.strip() for l in old if l.strip()]
    if not want:
        return None, None
    norm = [l.strip() for l in lines]
    hits = []
    for i in range(len(norm) - len(want) + 1):
        if norm[i] == want[0] and norm[i:i + len(want)] == want:
            hits.append(i)
    if len(hits) != 1:
        return None, None
    return hits[0] + 1, hits[0] + len(want)


def usage(msg):
    print(msg)
    print('usage: edit.py [file, start, end, new text] with the line numbers that show.py prints.')
    print('NEXT: call show.py [file, symbol] to see the numbered lines, then call edit.py with that range.')


def create(root, rel, args):
    """A file that does not exist yet: create it with the text (a range given before the text is ignored)."""
    top = rel.replace('\\', '/').split('/')[0]
    if os.path.isabs(rel) or '..' in rel.split('/') or not (os.path.isdir(os.path.join(root, top)) or '/' not in rel):
        usage(f'File not found: {rel}.')
        return
    _, _, rest = parse_range(args)
    rest = [r for r in rest if r != '']
    if not rest:
        usage(f'{rel} does not exist. To create it, call edit.py [{rel!r}, its full text].')
        return
    lines = clean_text('\n'.join(unescape(r) for r in rest))
    content = '\n'.join(lines) + '\n'
    if rel.endswith('.py') and not compiles(content, rel):
        try:
            compile(content, rel, 'exec')
        except SyntaxError as e:
            print(f'FILE NOT CREATED: its text causes "SyntaxError: {e.msg}" at line {e.lineno}.')
            print(f'NEXT: call edit.py [{rel!r}, its full text] again with corrected text.')
            return
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(content)
    out = [f'CREATED {rel} with {len(lines)} line(s):'] + numbered(lines[:40], 1)
    out.append('NEXT: run check.py.')
    print(clip('\n'.join(out), 5000))


def undefined_names(content, first, last):
    """Names read on lines first..last that nothing in the file defines (no import, assignment, def, class or
    parameter) and that are not builtins: usually a missing import or a typo."""
    import ast
    import builtins
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    bound = set(dir(builtins)) | {'__file__', '__name__', '__doc__', '__class__'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    if any(isinstance(n, ast.ImportFrom) and any(a.name == '*' for a in n.names) for n in ast.walk(tree)):
        return []
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and first <= node.lineno <= last \
                and node.id not in bound and node.id not in missing:
            missing.append(node.id)
    return missing


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        usage('Missing arguments.')
        return
    root = repo_root()
    given = args[0][2:] if args[0].startswith('./') else args[0]
    if is_test_path(given):
        print(f'NOT DONE: {given} is a test file. The hidden tests are already written; change only the code.')
        return
    rel, same = resolve_file(root, args[0])
    if rel is None and not same:
        create(root, given, args[1:])
        return
    if rel is None:
        usage(f'File not found: {args[0]}. Files with that name: {", ".join(same)}.')
        return
    if is_test_path(rel):
        print(f'NOT DONE: {rel} is a test file. The hidden tests are already written; change only the code.')
        return
    if len(args) < 3:
        usage(f'Missing arguments: {rel} exists, so give the lines to replace and the new text.')
        return
    start, end, rest = parse_range(args[1:])
    path = os.path.join(root, rel)
    with open(path, encoding='utf-8', errors='surrogateescape', newline='') as fh:
        original = fh.read()
    eol = '\r\n' if '\r\n' in original else '\n'
    lines = original.split(eol)
    trailing = original.endswith(eol)
    if trailing:
        lines = lines[:-1]
    if start is None and len(args) >= 3:
        # [file, old text, new text]: find the old lines (compared without indentation) and replace them.
        start, end = find_block(lines, clean_text(args[1]))
        rest = args[2:]
        if start is None:
            usage('The old text was not found in ' + rel + ' (or it matches more than one place).')
            return
    if start is None:
        usage(f'Line range not understood: {args[1:3]}.')
        return
    # Empty items (["file", "10-12", "", "text"]) carry nothing; each remaining item is unescaped on its own.
    rest = [r for r in rest if r != ''] or ['']
    text = '\n'.join(unescape(r) for r in rest)
    if '[... text lines' in text:
        usage('The new text contains a "[... text lines ...]" marker from show.py; those lines were hidden, not code.')
        return
    if not (1 <= start <= end <= len(lines)):
        usage(f'Lines {start}-{end} are outside {rel}, which has {len(lines)} lines.')
        return
    new = clean_text(text)
    note = ''
    updated = lines[:start - 1] + new + lines[end:]
    content = eol.join(updated) + (eol if trailing else '')
    if rel.endswith('.py') and not compiles(content, rel):
        # The most common slip is the block's indentation (one space too many from the "  79| " prefix): shift the
        # block so its first line has the indentation of the line it replaces, and keep it only if that compiles.
        shifted = reindent(new, indent_of(lines[start - 1]))
        candidate = eol.join(lines[:start - 1] + shifted + lines[end:]) + (eol if trailing else '')
        if shifted != new and compiles(candidate, rel):
            note = (f' (indentation adjusted: the first line now starts with {indent_of(lines[start - 1])} spaces, '
                    'like the line it replaces)')
            new, content = shifted, candidate
            updated = lines[:start - 1] + new + lines[end:]
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
    defs = re.compile(r'^\s*(?:async\s+)?(?:def|class)\s+(\w+)')
    kept = {m.group(1) for m in map(defs.match, new) if m}
    gone = [m.group(1) for m in map(defs.match, lines[start - 1:end]) if m and m.group(1) not in kept]
    out = [f'EDITED {rel}: lines {start}-{end} replaced by {len(new)} line(s){note}. Updated code:']
    missing = undefined_names(content, start, new_end) if rel.endswith('.py') else []
    if missing:
        out.insert(0, f'WARNING: {", ".join(missing)} on your new lines is not defined or imported anywhere in {rel} '
                      '(a missing import or a typo): fix it with another edit.py call.')
    if gone:
        out.insert(0, f'WARNING: this edit deleted the definition of {", ".join(gone)} (it was inside lines {start}-{end} '
                      'and is not in your new text). If the statement does not ask to remove it, put it back: call '
                      f'show.py on {rel} and re-add it with edit.py.')
    out += numbered(updated[shown_from - 1:hi], shown_from)
    out.append('NEXT: if more lines must change, call edit.py again (line numbers below this edit have shifted '
               f'by {len(new) - (end - start + 1)}); otherwise run check.py.')
    print(clip('\n'.join(out), 5000))


if __name__ == '__main__':
    main()
