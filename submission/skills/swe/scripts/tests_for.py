"""tests_for.py <file_or_symbol> [--run]

Finds the test files that exercise a source file or symbol. With --run it runs them with pytest (stop at the
first failure, short output) and prints a short summary, without writing caches into the repository.
"""
import collections
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, is_test_path, iter_py, read_text, repeat_guard, repo_root  # noqa: E402


def is_test_file(rel):
    """A file pytest collects by default: test_*.py or *_test.py (not helpers such as tests/testserver/server.py)."""
    name = os.path.basename(rel)
    return name.endswith('.py') and (name.startswith('test_') or name.endswith('_test.py'))


def find_tests(root, target):
    """Rank test files by references to the target module path or symbol name."""
    keys = []
    if target.endswith('.py') or '/' in target:
        mod = target[:-3] if target.endswith('.py') else target
        parts = [p for p in mod.replace('\\', '/').split('/') if p not in ('src', 'lib')]
        dotted = '.'.join(parts)
        keys += [dotted, parts[-1]]
        if parts[-1] == '__init__' and len(parts) > 1:
            keys += ['.'.join(parts[:-1]), parts[-2]]
    else:
        keys.append(target.split('.')[-1])
    keys = [k for k in keys if k and k not in ('__init__',)]
    scores = collections.Counter()
    for rel in iter_py(root, tests=True, docs=False):
        if not is_test_file(rel):
            continue
        text = read_text(root, rel)
        base = os.path.basename(rel)[:-3]
        for k in keys:
            short = k.split('.')[-1]
            if re.search(r'\b' + re.escape(k) + r'\b', text):
                scores[rel] += 3 if '.' in k else 1
            if short and short.lstrip('_') in base:
                scores[rel] += 4
    return [r for r, _ in scores.most_common(5)]


def find_tests_for_names(root, names, limit=3):
    """Rank test files by the identifiers an edit touched, rare identifiers first (log inverse document frequency),
    so the tests that exercise the changed behaviour run even when they never name the changed module."""
    texts = {}
    for rel in iter_py(root, tests=True, docs=False):
        if is_test_file(rel):
            texts[rel] = read_text(root, rel)
    if not texts or not names:
        return []
    df = {n: sum(1 for t in texts.values() if re.search(r'\b' + re.escape(n) + r'\b', t)) for n in names}
    weights = {n: math.log(1 + len(texts) / df[n]) for n in names if 0 < df[n] <= max(3, len(texts) // 4)}
    scores = collections.Counter()
    for rel, text in texts.items():
        for n, w in weights.items():
            if re.search(r'\b' + re.escape(n) + r'\b', text):
                scores[rel] += w
    return [r for r, _ in scores.most_common(limit)]


def code_path(root):
    """PYTHONPATH that makes the tests import the code in root (also for src/ layouts)."""
    paths = [root] + [os.path.join(root, d) for d in ('src', 'lib') if os.path.isdir(os.path.join(root, d))]
    return os.pathsep.join(paths + [os.environ.get('PYTHONPATH', '')]).rstrip(os.pathsep)


STUB = '''"""Stub of a test-only dependency missing from this environment (written by the swe skill): every name is
an object equal to any value, so the tests run and exercise the code; comparisons with it are not checked."""


class _Any:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Any()

    def __getattr__(self, name):
        return _Any()

    def __getitem__(self, key):
        return _Any()

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __hash__(self):
        return 0

    def __iter__(self):
        return iter(())


def __getattr__(name):
    return _Any()
'''


def stub_dir(root, text):
    """A directory with stubs for the test-only modules pytest could not import (not the repository's own
    packages), or None when nothing is missing."""
    from _common import _state_path
    missing = sorted(set(re.findall(r"No module named '([A-Za-z_]\w*)", text)))
    own = {d for d in os.listdir(root)} | ({d for d in os.listdir(os.path.join(root, 'src'))}
                                           if os.path.isdir(os.path.join(root, 'src')) else set())
    missing = [m for m in missing if m not in own and m + '.py' not in own]
    # Only modules that the repository's tests import and its package code does not: a stub never replaces a
    # runtime dependency, nor a module an installed library needs.
    pat = {m: re.compile(r'^\s*(?:import|from)\s+' + re.escape(m) + r'\b', re.M) for m in missing}
    in_tests = set()
    for rel in iter_py(root, tests=True):
        text = read_text(root, rel)
        hit = {m for m in missing if pat[m].search(text)}
        if is_test_path(rel):
            in_tests |= hit
        else:
            missing = [m for m in missing if m not in hit]
    missing = [m for m in missing if m in in_tests]
    if not missing:
        return None, []
    d = _state_path('stubs')
    os.makedirs(d, exist_ok=True)
    for m in missing:
        with open(os.path.join(d, m + '.py'), 'w') as fh:
            fh.write(STUB)
    return d, missing


def run_pytest(root, files, timeout=60, extra_env=None, _stubs=None):
    path = code_path(root)
    if _stubs:
        path = path + os.pathsep + _stubs
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', **(extra_env or {}))
    env['PYTHONPATH'] = path
    cmd = [sys.executable, '-m', 'pytest', '--maxfail=60', '-q', '-p', 'no:cacheprovider', '--no-header', '-rf'] + files
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, f'pytest timed out after {timeout}s'
    if _stubs is None and 'No module named' in r.stdout + r.stderr:
        d, missing = stub_dir(root, r.stdout + r.stderr)
        if d:
            code, summary = run_pytest(root, files, timeout, extra_env, _stubs=d)
            return code, (f'(test-only module {", ".join(missing)} is missing here: replaced by a stub that equals any '
                          'value, so comparisons with it are not checked)\n' + summary)
    text = (r.stdout + '\n' + r.stderr).strip().splitlines()
    summary = [l for l in text if re.search(r'\b(passed|failed|error|errors|no tests ran)\b', l)][-1:]
    failures = [l for l in text if l.startswith(('FAILED', 'ERROR'))][:40] + [l for l in text if l.startswith('E   ')][:4]
    return r.returncode, '\n'.join(summary + failures) or '\n'.join(text[-15:])


def failed_ids(summary):
    """Test node ids from the FAILED/ERROR lines of a pytest summary."""
    ids = []
    for line in summary.splitlines():
        if line.startswith(('FAILED ', 'ERROR ')):
            tid = line.split(' ', 1)[1].split(' - ')[0].strip()
            # "path::test" for a failing test, "path.py" for a file that fails to import (collection error).
            if ('::' in tid or tid.endswith('.py')) and tid not in ids:
                ids.append(tid)
    return ids


def main():
    args = [a for a in sys.argv[1:] if a != '--run']
    if not args:
        print('usage: tests_for.py <file_or_symbol> [--run]')
        return
    repeat_guard('use the earlier result.')
    root = repo_root()
    files = find_tests(root, args[0])
    if not files:
        print(f'No test files reference {args[0]}.')
        print('NEXT: run check.py after your edit; it also finds tests by the names on the changed lines.')
        return
    out = ['Test files for ' + args[0] + ':'] + ['  ' + f for f in files]
    if '--run' in sys.argv:
        code, summary = run_pytest(root, files[:2])
        out += [f'pytest on {", ".join(files[:2])} (exit {code}):', summary,
                'NEXT: if these tests fail because of your edit, fix it; check.py tells which failures are new.']
    else:
        out.append('NEXT: run tests_for.py ' + args[0] + ' --run after your edit to check them.')
    print(clip('\n'.join(out)))


if __name__ == '__main__':
    main()
