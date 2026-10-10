"""Line-range replacement with the repairs that real edits needed (docs/old_scripts_lessons.md, Editing): a boundary line
repeated from just outside the range, the block indented one space off, escaped quotes, a range longer than the text.
An edit that does not compile after every repair is not applied."""
import ast
import builtins
import re
from collections import namedtuple

import _repo

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

# applied: the file was written. start, end: the lines the new text occupies now. error: why it was not applied.
Result = namedtuple('Result', 'applied start end error repairs warnings')


def _indent(line):
    return len(line) - len(line.lstrip(' \t'))


def _meaningful(line):
    return len(re.sub(r'[\s()\[\]{},:]', '', line)) > 3


def _reindent(new, first_old):
    """The block shifted so that its first non-blank line has the indentation of the line it replaces."""
    body = [l for l in new if l.strip()]
    if not body or not first_old.strip():
        return new
    delta = _indent(first_old) - _indent(body[0])
    if delta == 0:
        return new
    out = []
    for l in new:
        if not l.strip():
            out.append(l)
        elif delta > 0:
            out.append(' ' * delta + l)
        else:
            cut = min(-delta, _indent(l))
            out.append(l[cut:])
    return out


def _unescape(new):
    return [l.replace('\\"', '"').replace("\\'", "'") for l in new]


def _compiles(lines):
    try:
        ast.parse(''.join(lines))
        return None
    except SyntaxError as e:
        # the text of the offending line, not its number: the model finds an error once told where it is in its own
        # words (Tyen et al.: +18 to +44 points), and a file line number means nothing to it
        text = ''.join(lines).splitlines()
        bad = text[e.lineno - 1].strip() if e.lineno and 0 < e.lineno <= len(text) else ''
        return f'{e.msg} in the line `{bad[:120]}`' if bad else e.msg
    except ValueError as e:
        return str(e)


def _bound_names(tree):
    names = set(dir(builtins)) | {'__file__', '__name__', '__doc__', '__spec__', '__path__'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split('.')[0] for a in node.names)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
        elif hasattr(ast, 'MatchAs') and isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
    return names


def unknown_names(lines, start, end):
    """Names read on lines start..end that nothing in the file defines or imports (a missing import or a typo)."""
    try:
        tree = ast.parse(''.join(lines))
    except (SyntaxError, ValueError):
        return []
    bound = _bound_names(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and start <= node.lineno <= end \
                and node.id not in bound and node.id not in out:
            out.append(node.id)
    return out


def _removed_defs(old, new):
    names = re.findall(r'^\s*(?:async\s+)?(?:def|class)\s+(\w+)', ''.join(old), re.M)
    text = ''.join(new)
    return [n for n in names if not re.search(rf'\b(def|class)\s+{re.escape(n)}\b', text)]


def apply(root, rel, start, end, text):
    """Replaces lines start..end (1-based, inclusive) of rel with text, trying the repairs in order until the file
    compiles. Returns a Result; the file is written only when applied."""
    if _repo.is_test_path(rel):
        return Result(False, start, end, 'test files are not edited: the hidden tests replace them', [], [])
    try:
        lines = _repo.read_lines(root, rel)
    except OSError:
        return Result(False, start, end, f'{rel} does not exist', [], [])
    if not (1 <= start <= end <= len(lines)):
        return Result(False, start, end, f'lines {start}-{end} are outside the file (it has {len(lines)} lines)', [],
                      [])
    nl = _repo.newline_of(lines)
    new = [l + nl for l in text.replace('\r\n', '\n').split('\n')]
    if text.endswith('\n') or text.endswith('\r\n'):
        new = new[:-1]
    if not text.strip() or text.strip() == 'DELETE':
        new = []                # "DELETE" (or empty new lines) deletes the range
    repairs = []
    if new and start > 1 and new[0].strip() == lines[start - 2].strip() and _meaningful(new[0]) \
            and lines[start - 1].strip() != new[0].strip():
        new = new[1:]
        repairs.append('dropped a first line that repeated the line before the range')
    if new and end < len(lines) and new[-1].strip() == lines[end].strip() and _meaningful(new[-1]) \
            and lines[end - 1].strip() != new[-1].strip():
        new = new[:-1]
        repairs.append('dropped a last line that repeated the line after the range')
    old = lines[start - 1:end]
    if [l.rstrip('\r\n') for l in new] == [l.rstrip('\r\n') for l in old]:
        return Result(False, start, end, 'the new lines are the same as the old ones: nothing changes', repairs, [])

    bases = [(new, [])]
    if any('\\n' in l for l in new):
        split = [x + nl for l in new for x in l.rstrip('\r\n').replace('\\r\\n', '\\n').split('\\n')]
        bases.append((split, ['turned the escaped line breaks \\n into line breaks']))
    variants = []
    for base, why in bases:
        variants.append((base, why))
        reind = _reindent(base, old[0])
        if reind != base:
            variants.append((reind, why + ['re-indented to the replaced line']))
        unesc = _unescape(base)
        if unesc != base:
            variants.append((unesc, why + ['turned escaped quotes into quotes']))
            both = _reindent(unesc, old[0])
            if both != unesc:
                variants.append((both, why + ['turned escaped quotes into quotes', 're-indented to the replaced line']))
    tries = [(v, r, end) for v, r in variants]
    if 0 < len(new) < end - start + 1:
        tries += [(v, r + [f'replaced only {len(v)} lines, as many as the new text has'], start + len(v) - 1)
                  for v, r in variants]
    first_error = None
    same_as_old = False
    for cand, extra, stop in tries:
        if [l.rstrip('\r\n') for l in cand] == [l.rstrip('\r\n') for l in lines[start - 1:stop]]:
            same_as_old = True          # a repair turned the text into the lines already there: not a change
            continue
        result = lines[:start - 1] + cand + lines[stop:]
        err = _compiles(result)
        if err is None:
            _repo.write_lines(root, rel, result)
            new_end = start + len(cand) - 1
            warnings = []
            unknown = unknown_names(result, start, max(start, new_end))
            if unknown:
                warnings.append('nothing in the file defines or imports: ' + ', '.join(unknown))
            removed = _removed_defs(lines[start - 1:stop], cand)
            if removed:
                warnings.append('the edit removed the definition of: ' + ', '.join(removed))
            return Result(True, start, new_end, None, repairs + extra, warnings)
        first_error = first_error or err
    if same_as_old and first_error is None:
        return Result(False, start, end, 'the new lines are the same as the old ones once indented: nothing changes',
                      repairs, [])
    if same_as_old:
        return Result(False, start, end, f'the new lines are the old ones once indented, or the file would not compile '
                                         f'({first_error}); nothing was changed', repairs, [])
    return Result(False, start, end, f'the file would not compile ({first_error}); nothing was changed', repairs, [])
