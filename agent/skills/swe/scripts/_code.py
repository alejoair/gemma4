"""Python source through the AST: functions and classes with their line ranges, one-line skeletons, numbered code
('  79|code', no space after the bar so copied indentation stays exact) with long strings collapsed, and windows."""
import ast
from collections import namedtuple

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

# start: first line including decorators (an edit of a decorated function must see them); def_line: the def/class.
Sym = namedtuple('Sym', 'name kind start def_line end node')
COLLAPSE_AT = 6  # a string literal longer than this many lines is shown as one marker line


def parse(text):
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def symbols(tree):
    """Every function and class, with its qualified name (Class.method), outermost first."""
    out = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                kind = 'class' if isinstance(child, ast.ClassDef) else 'def'
                out.append(Sym(name, kind, start, child.lineno, child.end_lineno, child))
                walk(child, name + '.')
            else:
                walk(child, prefix)

    if tree is not None:
        walk(tree, '')
    return out


def owner_map(syms, n_lines):
    """For each line number (1-based), the innermost function or class containing it, or None."""
    owner = [None] * (n_lines + 2)
    for sym in sorted(syms, key=lambda s: -(s.end - s.start)):
        for i in range(sym.start, min(sym.end, n_lines) + 1):
            owner[i] = sym
    return owner


def prose_lines(tree):
    """Line numbers inside string literals that span several lines (docstrings, Doc("...") texts)."""
    out = set()
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.end_lineno > node.lineno:
            out.update(range(node.lineno, node.end_lineno + 1))
    return out


def long_strings(tree):
    """(first, last) line ranges of string literals longer than COLLAPSE_AT lines."""
    if tree is None:
        return []
    return sorted((n.lineno, n.end_lineno) for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and n.end_lineno - n.lineno + 1 > COLLAPSE_AT)


def signature(sym):
    node = sym.node
    if sym.kind == 'class':
        bases = ', '.join(ast.unparse(b) for b in node.bases)
        return f'class {node.name}({bases})' if bases else f'class {node.name}'
    prefix = 'async def' if isinstance(node, ast.AsyncFunctionDef) else 'def'
    ret = f' -> {ast.unparse(node.returns)}' if node.returns is not None else ''
    return f'{prefix} {node.name}({ast.unparse(node.args)}){ret}'


def first_doc_line(sym):
    doc = ast.get_docstring(sym.node)
    return doc.strip().splitlines()[0].strip() if doc and doc.strip() else ''


def skeleton(rel, sym):
    """One line: file :: Qual.name (kind, lines a-b) signature — first docstring line."""
    doc = first_doc_line(sym)
    return f'{rel} :: {sym.name} ({sym.kind}, lines {sym.start}-{sym.end}) {signature(sym)}' + (f' — {doc}' if doc else '')


def numbered(lines, start, end, prose=None, collapse=None):
    """Lines start..end as '  79|code'. Long string literals (collapse: their (first, last) ranges) are shown as one
    marker line; when collapse is None it is computed from the given prose lines' contiguous runs."""
    start, end = max(1, start), min(len(lines), end)
    if collapse is None:
        collapse = _runs(prose or set())
    hidden = {}
    for a, b in collapse:
        if b - a + 1 > COLLAPSE_AT:
            for i in range(a + 1, b):
                hidden[i] = (a, b)
    out, i = [], start
    while i <= end:
        if i in hidden:
            a, b = hidden[i]
            last = min(b - 1, end)
            out.append(f'{"":>5} ... ({b - a - 1} lines of text) ...')
            i = last + 1
            continue
        out.append(f'{i:>4}|{lines[i - 1].rstrip(chr(10)).rstrip(chr(13))}')
        i += 1
    return '\n'.join(out)


def _runs(nums):
    runs, cur = [], None
    for n in sorted(nums):
        if cur and n == cur[1] + 1:
            cur[1] = n
        else:
            cur = [n, n]
            runs.append(cur)
    return [tuple(r) for r in runs]


def window(lines, start, end, radius=15):
    """The (first, last) lines of a window around start..end, clipped to the file."""
    return max(1, start - radius), min(len(lines), end + radius)


def find(syms, wanted):
    """Symbols matching a qualified name exactly, else by its last part when the name has no dot."""
    exact = [s for s in syms if s.name == wanted]
    if exact or '.' in wanted:
        return exact
    return [s for s in syms if s.name.split('.')[-1] == wanted]
