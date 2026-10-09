"""Regression check of an edit (docs/old_scripts_lessons.md, Tests and verification): the existing tests that use the
changed names, run with time limits; a failing test counts against the edit only if it passes on the original code
(run in a separate git worktree). Also the last verified state: the content of the changed files when the tests last
passed, to go back to when an edit breaks them."""
import ast
import math
import os
import re
import subprocess
import sys
import tempfile
import time

import _repo
import _state

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

FILE_TIMEOUT = 30      # seconds per test file; files run in parallel
BASE_TIMEOUT = 25      # seconds for the failing ids on the original code
TEST_TIMEOUT = 15      # seconds per test, when pytest-timeout is installed (a hanging test then costs 15 s, not 30)
MAX_FILES = 3
MAX_BASE_IDS = 10
BIG_FILE = 40          # a test file with more tests than this runs only the tests that use the changed names
PYTEST = ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--no-header', '-rf', '--maxfail=60']
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{2,}')
COMMON = {'self', 'cls', 'None', 'True', 'False', 'return', 'import', 'from', 'def', 'class', 'assert', 'raise', 'not',
          'and', 'for', 'while', 'with', 'pass', 'elif', 'else', 'try', 'except', 'finally', 'lambda', 'yield',
          'async', 'await', 'str', 'int', 'list', 'dict', 'len', 'isinstance', 'value', 'name', 'args', 'kwargs'}


def _module_names(rel):
    """Import names of a source file: pkg/sub/mod.py -> pkg.sub.mod (and without a leading src.)."""
    mod = rel[:-3].replace('/', '.')
    if mod.endswith('.__init__'):
        mod = mod[:-9]
    return [mod[4:]] if mod.startswith('src.') else [mod]


def _test_functions(tree):
    """[(node id suffix, first line, last line)] of the test functions, methods of Test classes included."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test'):
            out.append((node.name, node.lineno, node.end_lineno))
        elif isinstance(node, ast.ClassDef) and node.name.startswith('Test'):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith('test'):
                    out.append((f'{node.name}::{item.name}', item.lineno, item.end_lineno))
    return out


def select(root, changed):
    """pytest targets for the changed code. changed: {rel: [changed line texts and symbol names]}. Test files score
    first by being the changed module's own test file (test_<module>.py), then by importing a changed module, then by
    the rare names they share with the changed code (inverse document frequency); in a big file only the tests that
    use those names are kept."""
    tests = [r for r in _repo.iter_py(root, tests=True) if _repo.is_test_path(r) and os.path.basename(r) != 'conftest.py']
    if not tests:
        return []
    texts = {r: _repo.read_text(root, r) for r in tests}
    words = {r: set(IDENT.findall(t)) for r, t in texts.items()}
    names = set()
    for items in changed.values():
        for item in items:
            names.update(w for w in IDENT.findall(item) if w not in COMMON)
    modules = [m for rel in changed for m in _module_names(rel)]
    n = len(tests)
    idf = {w: math.log(1 + n / (1 + sum(w in ws for ws in words.values()))) for w in names}
    stems = {os.path.basename(rel)[:-3].lstrip('_') for rel in changed if rel.endswith('.py')} - {'__init__'}
    scores = []
    for r in tests:
        s = sum(idf[w] for w in names if w in words[r])
        base = os.path.basename(r)[:-3]
        if base in {f'test_{x}' for x in stems} | {f'{x}_test' for x in stems}:
            s += 20.0                      # the module's own test file (rich/repr.py -> tests/test_repr.py)
        if any(re.search(rf'\b{re.escape(m)}\b', texts[r]) for m in modules):
            s += 6.0                       # imports the changed module
        if s > 0:
            scores.append((s, r))
    scores.sort(key=lambda x: (-x[0], x[1]))
    targets = []
    for _, r in scores[:MAX_FILES]:
        try:
            tree = ast.parse(texts[r])
        except (SyntaxError, ValueError):
            continue
        funcs = _test_functions(tree)
        if len(funcs) > BIG_FILE:
            lines = texts[r].splitlines()
            keep = [f'{r}::{name}' for name, a, b in funcs
                    if names & set(IDENT.findall('\n'.join(lines[a - 1:b])))]
            if keep:
                targets.append(keep)
                continue
        targets.append([r])
    return targets


COMMON_IN_TESTS = 12     # a name used in more test files than this says little: only its own test file is searched


def example(root, rel, name, max_lines=14):
    """(test file, its lines) of the shortest existing test that uses the place's name (a call, an attribute or a
    decorator): the module's own test file first, then tests in files that also name the owner class. A name used in
    many test files is looked up only in the module's own test file. None when no test uses it, and for functions
    nested in functions (no test can call them)."""
    parts = name.split('.')
    short = parts[-1]
    if short.startswith('__') and len(parts) > 1:
        short, parts = parts[-2], parts[:-1]            # Class.__init__ -> Class
    if not re.fullmatch(r'\w{3,}', short) or short in COMMON:
        return None
    if len(parts) > 2 or (len(parts) == 2 and not parts[0][:1].isupper()):
        return None                                     # setup.openapi: a closure
    owner = parts[0] if len(parts) == 2 else None
    pattern = re.compile(rf'(?<![\w]){re.escape(short)}\b')
    stem = os.path.basename(rel)[:-3].lstrip('_')
    own = {f'test_{stem}.py', f'{stem}_test.py'}
    files = []
    for r in _repo.iter_py(root, tests=True):
        if not _repo.is_test_path(r) or os.path.basename(r) == 'conftest.py':
            continue
        text = _repo.read_text(root, r)
        if pattern.search(text):
            files.append((r, text))
    if len(files) > COMMON_IN_TESTS:
        files = [(r, t) for r, t in files if os.path.basename(r) in own]
    best = None
    for r, text in files:
        try:
            funcs = _test_functions(ast.parse(text))
        except (SyntaxError, ValueError):
            continue
        lines = text.splitlines()
        names_owner = owner is None or owner in text
        for fname, a, b in funcs:
            body = lines[a - 1:b]
            if not any(pattern.search(l) for l in body):
                continue
            key = (os.path.basename(r) not in own, not names_owner, max(b - a + 1, 4), r, a)
            if best is None or key < best[0]:
                best = (key, r, body)
    if best is None:
        return None
    _, r, body = best
    if len(body) > max_lines:
        body = body[:max_lines] + ['    ...']
    return r, body


def _env(root, extra=()):
    env = dict(os.environ)
    paths = list(extra) + [root] + ([os.path.join(root, 'src')] if os.path.isdir(os.path.join(root, 'src')) else [])
    env['PYTHONPATH'] = os.pathsep.join(paths + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def _stub_dir(modules):
    """A directory of stand-in modules for test-only packages the environment lacks: every attribute is an object
    equal to anything."""
    d = os.path.join(_state.directory(), 'stubs')
    os.makedirs(d, exist_ok=True)
    body = ('class _Any:\n    def __init__(self, *a, **k): pass\n    def __call__(self, *a, **k): return _Any()\n'
            '    def __eq__(self, other): return True\n    def __ne__(self, other): return False\n'
            '    def __getattr__(self, name): return _Any()\n    def __repr__(self): return "<any>"\n'
            '    __hash__ = object.__hash__\n'
            'def __getattr__(name): return _Any()\n')
    for m in modules:
        with open(os.path.join(d, m + '.py'), 'w') as fh:
            fh.write(body)
    return d


def _parse(out):
    failed = set()
    for line in out.splitlines():
        m = re.match(r'(FAILED|ERROR) (\S+)', line)
        if m:
            failed.add(m.group(2))
    passed = sum(int(x) for x in re.findall(r'(\d+) passed', out))
    return failed, passed


def run(root, targets, timeout=None, cwd=None, stubs=()):
    """Runs each target group in its own pytest process, in parallel. Returns {'ran', 'failed', 'passed', 'output',
    'timeouts', 'missing'}: ran is False when pytest itself could not run."""
    timeout = timeout or FILE_TIMEOUT
    extra = [_stub_dir(stubs)] if stubs else []
    flags = PYTEST + ([f'--timeout={TEST_TIMEOUT}'] if _has_pytest_timeout() else [])
    procs = []
    for group in targets:
        out = tempfile.TemporaryFile('w+')
        p = subprocess.Popen([sys.executable] + flags + group, cwd=cwd or root, env=_env(cwd or root, extra),
                             stdout=out, stderr=subprocess.STDOUT, text=True)
        procs.append((group, p, out))
    result = {'ran': False, 'failed': set(), 'passed': 0, 'output': '', 'timeouts': [], 'missing': set()}
    deadline = time.time() + timeout          # one deadline for the parallel runs, not one per run
    for group, p, out in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            result['timeouts'].append(group[0].split('::')[0])
        out.seek(0)
        text = out.read()
        out.close()
        if 'No module named pytest' in text or 'pytest is disabled' in text:
            continue
        result['ran'] = True
        for m in re.findall(r"ModuleNotFoundError: No module named '([\w.]+)'", text):
            top = m.split('.')[0]
            if not os.path.exists(os.path.join(root, top)) and not os.path.exists(os.path.join(root, 'src', top)):
                result['missing'].add(top)
        failed, passed = _parse(text)
        result['failed'] |= failed
        result['passed'] += passed
        result['output'] += text
    return result


def _has_pytest_timeout():
    try:
        import importlib.util
        return importlib.util.find_spec('pytest_timeout') is not None
    except (ImportError, ValueError):
        return False


def _base_worktree(root):
    """A git worktree of the original commit, made once per task, outside the repository."""
    path = os.path.join(_state.directory(), 'original')
    if not os.path.isdir(os.path.join(path, '.git')) and not os.path.isfile(os.path.join(path, '.git')):
        _repo.git(root, 'worktree', 'add', '--detach', '-f', path, 'HEAD', timeout=120)
    return path if os.path.exists(path) else None


def failing_before(root, ids, stubs=()):
    """The ids (first MAX_BASE_IDS) that also fail on the original code. Results are kept, so each id runs on the
    original once per task."""
    known = _state.load('original_results', {})
    ids = sorted(ids)[:MAX_BASE_IDS]
    todo = [i for i in ids if i not in known]
    base = _base_worktree(root) if todo else None
    if base:
        r = run(base, [todo], timeout=BASE_TIMEOUT, cwd=base, stubs=stubs)
        if r['ran']:
            for i in todo:
                known[i] = i in r['failed'] or any(f.startswith(i) for f in r['failed'])
            _state.save('original_results', known)
    return {i for i in ids if known.get(i)}


def _test_uses(root, test_id, names):
    """Whether the test function test_id ('file::name' or 'file::Class::name') uses one of names."""
    parts = test_id.split('::')
    try:
        tree = ast.parse(_repo.read_text(root, parts[0]))
    except (SyntaxError, ValueError):
        return False
    lines = _repo.read_text(root, parts[0]).splitlines()
    want = parts[-1].split('[')[0]
    for fname, a, b in _test_functions(tree):
        if fname.split('::')[-1] == want:
            body = '\n'.join(lines[a - 1:b])
            return any(re.search(rf'\b{re.escape(n)}\b', body) for n in names)
    return False


def check(root, changed, renamed=()):
    """The verdict on the current code: ('OK' | 'BROKEN' | 'NOT VERIFIED', detail, new failures). A new failure of a
    test that uses a name the statement renames does not count: the hidden tests replace such tests."""
    slow = _state.load('slow_tests', [])
    targets = [g for g in select(root, changed) if g[0].split('::')[0] not in slow]
    if not targets:
        return 'NOT VERIFIED', 'no existing test uses the changed code' + (
            f' (left out because they ran out of time before: {", ".join(slow)})' if slow else ''), []
    r = run(root, targets)
    if r['timeouts']:
        _state.save('slow_tests', sorted(set(slow) | set(r['timeouts'])))
    stubs = ()
    if r['missing']:
        stubs = tuple(sorted(r['missing']))
        r = run(root, targets, stubs=stubs)
    if not r['ran']:
        return 'NOT VERIFIED', 'pytest could not run', []
    files = sorted({g[0].split('::')[0] for g in targets} - set(r['timeouts']))
    late = f'; out of time, not counted: {", ".join(r["timeouts"])}' if r['timeouts'] else ''
    if not r['failed']:
        if r['passed'] == 0:
            return 'NOT VERIFIED', 'the selected tests did not finish (' + ', '.join(r['timeouts'] or files) + ')', []
        return 'OK', f'{r["passed"]} existing tests pass ({", ".join(files)}){late}', []
    before = failing_before(root, r['failed'], stubs)
    new = sorted(f for f in r['failed'] if f not in before and not any(f.startswith(b) for b in before))
    old_name = [f for f in new if renamed and _test_uses(root, f, renamed)]
    new = [f for f in new if f not in old_name]
    if old_name:
        late += (f'; {len(old_name)} failing tests use the renamed {", ".join(f"`{n}`" for n in renamed)} and do not '
                 f'count (the hidden tests replace them)')
    if not new:
        if r['passed'] == 0:      # nothing passes: the tests say nothing about the change (SWE-ABS: not verified)
            return 'NOT VERIFIED', (f'no selected test passes, before or after the change ({len(r["failed"])} failed '
                                    f'already before it){late}'), []
        already = len(r['failed']) - len(old_name)
        return 'OK', (f'{r["passed"]} existing tests pass' + (f'; {already} failures were already there before the '
                                                              f'change' if already else '') + late), []
    return 'BROKEN', _failure_excerpt(r['output'], new), new


def _failure_excerpt(output, ids, limit=1500):
    """The short failure lines of the new failures, then the end of the first traceback."""
    lines = [l for l in output.splitlines() if l.startswith(('FAILED', 'ERROR')) and any(i in l for i in ids)]
    m = re.search(r'(?ms)^_{3,} .*?(?=^_{3,} |^=+ short test summary)', output)
    tb = m.group(0).strip().splitlines()[-25:] if m else []
    text = '\n'.join(lines[:8] + [''] + tb)
    return text[-limit:]


# The last verified state: the content of every changed file when the tests last passed (None: the file did not
# exist). Going back restores those contents and undoes everything else.

def changed_files(root):
    out = _repo.git(root, 'status', '--porcelain', '--untracked-files=all') or ''
    rels = []
    for line in out.splitlines():
        rel = line[3:].split(' -> ')[-1].strip().strip('"')
        if rel.endswith('.pyc') or os.path.basename(rel).startswith('.'):
            continue
        rels.append(rel)
    return rels


def save_verified(root):
    snap = {}
    for rel in changed_files(root):
        try:
            snap[rel] = ''.join(_repo.read_lines(root, rel))
        except OSError:
            snap[rel] = None
    _state.save('verified', snap)


def restore_verified(root):
    """Puts every changed file back to the last verified state (or the original when there is none)."""
    snap = _state.load('verified', {})
    for rel in set(changed_files(root)) | set(snap):
        content = snap.get(rel)
        path = os.path.join(root, rel)
        if content is not None:
            _repo.write_lines(root, rel, content.splitlines(keepends=True))
        elif rel in snap or _repo.git(root, 'ls-files', '--error-unmatch', rel) is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            _repo.git(root, 'checkout', 'HEAD', '--', rel)
