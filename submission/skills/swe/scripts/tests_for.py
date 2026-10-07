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


def run_pytest(root, files, timeout=60, extra_env=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=code_path(root), **(extra_env or {}))
    cmd = [sys.executable, '-m', 'pytest', '--maxfail=60', '-q', '-p', 'no:cacheprovider', '--no-header', '-rf'] + files
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, f'pytest timed out after {timeout}s'
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
            if '::' in tid and tid not in ids:
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
