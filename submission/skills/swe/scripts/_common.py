"""Shared helpers for the swe skill scripts: repository root, source-file walking and AST lookups."""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys

SKIP_DIRS = {'.git', '.hg', '.tox', '.nox', '.venv', 'venv', 'env', 'node_modules', 'build', 'dist', '__pycache__',
             '.mypy_cache', '.pytest_cache', '.ruff_cache', 'site-packages', '.eggs'}
DOC_DIRS = {'docs', 'doc', 'docs_src', 'examples', 'example', 'benchmarks', 'scripts'}
MAX_OUT = 4000
CALL_LOG = '/tmp/swe_skill_calls.log'


class _Tee:
    """Copy everything a script prints into CALL_LOG, so a run can be reviewed afterwards."""

    def __init__(self, stream):
        self.stream = stream
        try:
            self.fh = open(CALL_LOG, 'a')
            self.fh.write('\n===== ' + ' '.join(os.path.basename(a) if i == 0 else a for i, a in enumerate(sys.argv))[:300] + '\n')
        except OSError:
            self.fh = None

    def write(self, s):
        if self.fh:
            self.fh.write(s)
            self.fh.flush()
        return self.stream.write(s)

    def flush(self):
        self.stream.flush()


if not isinstance(sys.stdout, _Tee):
    sys.stdout = _Tee(sys.stdout)

# Models sometimes wrap each argument in literal quotes (["\"pkg/mod.py\"", "\"Cls\""]) or add a trailing comma;
# strip them so a path or symbol still resolves instead of sending the model into a retry loop.
sys.argv = [sys.argv[0]] + [a.strip().strip(',').strip().strip('"\'`').strip() for a in sys.argv[1:]]
sys.argv = [a for i, a in enumerate(sys.argv) if i == 0 or a]

def _state_path(name):
    """Per-repository state file in /tmp, so state from one task's sandbox never leaks into another task."""
    tag = hashlib.sha1((os.environ.get('PWD') or '/workspace').encode()).hexdigest()[:10]
    return f'/tmp/swe_{name}_{tag}'


SEEN = _state_path('seen.txt')
NO_REPEAT_GUARD = {'check.py', 'journal.py'}


def repeat_guard(next_step):
    """Stop a call identical to an earlier one: print a short reminder instead of the same output again."""
    script = os.path.basename(sys.argv[0])
    if script in NO_REPEAT_GUARD:
        return
    sig = script + ' ' + ' '.join(a.strip().lower() for a in sys.argv[1:])
    seen = []
    try:
        with open(SEEN) as fh:
            seen = fh.read().splitlines()
    except OSError:
        pass
    count = seen.count(sig)
    try:
        with open(SEEN, 'a') as fh:
            fh.write(sig + '\n')
    except OSError:
        pass
    if count == 1:
        # The first repeat may come from a later stage that never saw the output (the locator and the fixer share
        # /tmp), so print the output again and only stop the third identical call.
        print(f'NOTE: you already ran "{sig}"; same output as before:')
        return
    if count:
        print(f'REPEATED CALL: you already ran "{sig}" ({count + 1} times now). Its output is in the conversation '
              f'above and has not changed.')
        report = last_candidate_report()
        if report:
            print('STOP calling scripts. If you are the locator, write this as your final message now:')
            print(report)
            print('If you are the fixer, call edit_file now with 3-6 lines copied from the code shown earlier.')
        else:
            print('NEXT: ' + next_step)
        sys.exit(0)


CANDIDATE = _state_path('candidate.json')


def remember_candidate(rel, name, start, end, code_lines):
    """Store the best location found so far, so a looping model can be handed a finished report."""
    try:
        with open(CANDIDATE, 'w') as fh:
            json.dump({'file': rel, 'symbol': name, 'graph_id': graph_id(rel, name), 'start': start, 'end': end,
                       'code': code_lines[:25]}, fh)
    except OSError:
        pass


def last_candidate_report():
    try:
        with open(CANDIDATE) as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return ''
    return '\n'.join([f"FILE: {c['file']}", f"SYMBOL: {c['symbol']}", f"GRAPH_ID: {c['graph_id']}",
                      f"LINES: {c['start']}-{c['end']}", 'CODE:'] + c['code'] + ['ALSO: NONE'])


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


def find_definitions(root, name, limit=5):
    """(file, (qualified_name, kind, start, end)) for every definition matching name in source files."""
    tail = name.split('.')[-1]
    found = []
    for rel in iter_py(root):
        text = read_text(root, rel)
        if tail not in text:
            continue
        for sym in find_symbol(symbols(parse(text)), name):
            found.append((rel, sym))
            if len(found) >= limit:
                return found
    return found


def is_symbol_name(text):
    return bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*', text.strip()))


def graph_id(rel, qualname):
    """Fully qualified id used by the competition code graph, e.g. src/pkg/mod.py + Cls.meth -> pkg.mod.Cls.meth."""
    parts = rel.replace('\\', '/')[:-3].split('/') if rel.endswith('.py') else rel.split('/')
    if parts and parts[0] in ('src', 'lib'):
        parts = parts[1:]
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    module = '.'.join(p for p in parts if p)
    if not qualname or qualname == '<module>':
        return module
    return f'{module}.{qualname}' if module else qualname
