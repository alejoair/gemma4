"""Shared helpers for the swe skill scripts: repository root, source-file walking and AST lookups."""
import ast
import os
import re
import subprocess

SKIP_DIRS = {'.git', '.hg', '.tox', '.nox', '.venv', 'venv', 'env', 'node_modules', 'build', 'dist', '__pycache__',
             '.mypy_cache', '.pytest_cache', '.ruff_cache', 'site-packages', '.eggs'}
DOC_DIRS = {'docs', 'doc', 'docs_src', 'examples', 'example', 'benchmarks', 'scripts'}
MAX_OUT = 4000


def repo_root():
    """The repository the agent works on: the sandbox working directory, else /workspace, else git top level."""
    for cand in (os.environ.get('SWE_REPO'), os.environ.get('PWD'), '/workspace'):
        if cand and os.path.isdir(os.path.join(cand, '.git')):
            return cand
    try:
        out = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get('PWD') or '/workspace'


def is_test_path(rel):
    parts = rel.replace('\\', '/').split('/')
    name = parts[-1]
    return any(p in ('tests', 'test', 'testing') for p in parts[:-1]) or name.startswith('test_') or name.endswith('_test.py') \
        or name == 'conftest.py'


def is_doc_path(rel):
    return rel.replace('\\', '/').split('/')[0] in DOC_DIRS


def iter_py(root, tests=False, docs=False):
    """Yield repo-relative paths of Python files; source files only unless tests/docs are requested."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.') and not d.endswith('.egg-info'))
        for fn in sorted(filenames):
            if not fn.endswith('.py'):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            if is_test_path(rel) and not tests:
                continue
            if is_doc_path(rel) and not docs:
                continue
            yield rel


def read_text(root, rel):
    try:
        with open(os.path.join(root, rel), encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def parse(text):
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def symbols(tree):
    """List of (qualified_name, kind, start_line, end_line) for every function and class, outermost first."""
    out = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                start = min([child.lineno] + [d.lineno for d in getattr(child, 'decorator_list', [])])
                end = getattr(child, 'end_lineno', None) or child.lineno
                kind = 'class' if isinstance(child, ast.ClassDef) else 'def'
                out.append((name, kind, start, end))
                visit(child, name + '.')

    if tree is not None:
        visit(tree, '')
    return out


def enclosing(syms, line):
    """Innermost symbol containing the line, or None for module level."""
    best = None
    for name, kind, start, end in syms:
        if start <= line <= end and (best is None or end - start <= best[3] - best[2]):
            best = (name, kind, start, end)
    return best


def find_symbol(syms, wanted):
    """Match 'Class.method', 'method' or a dotted suffix; exact qualified match first."""
    exact = [s for s in syms if s[0] == wanted]
    if exact:
        return exact
    tail = wanted.split('.')[-1]
    return [s for s in syms if s[0] == tail or s[0].endswith('.' + wanted) or s[0].split('.')[-1] == tail]


def clip(text, limit=MAX_OUT):
    if len(text) <= limit:
        return text
    return text[:limit] + '\n[... output clipped ...]'


WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
