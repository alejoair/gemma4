"""tests_for.py <file_or_symbol> [--run]

Finds the test files that exercise a source file or symbol. With --run it runs them with pytest (stop at the
first failure, short output) and prints a short summary, without writing caches into the repository.
"""
import collections
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, is_test_path, iter_py, read_text, repo_root  # noqa: E402


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
        if not is_test_path(rel) or os.path.basename(rel) == 'conftest.py':
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


def run_pytest(root, files, timeout=100):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    cmd = [sys.executable, '-m', 'pytest', '-x', '-q', '-p', 'no:cacheprovider', '--no-header', '-rf'] + files
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, f'pytest timed out after {timeout}s'
    text = (r.stdout + '\n' + r.stderr).strip().splitlines()
    summary = [l for l in text if re.search(r'\b(passed|failed|error|errors|no tests ran)\b', l)][-1:]
    failures = [l for l in text if l.startswith(('FAILED', 'ERROR', 'E   '))][:12]
    return r.returncode, '\n'.join(summary + failures) or '\n'.join(text[-15:])


def main():
    args = [a for a in sys.argv[1:] if a != '--run']
    if not args:
        print('usage: tests_for.py <file_or_symbol> [--run]')
        return
    root = repo_root()
    files = find_tests(root, args[0])
    if not files:
        print(f'No test files reference {args[0]}.')
        return
    out = ['Test files for ' + args[0] + ':'] + ['  ' + f for f in files]
    if '--run' in sys.argv:
        code, summary = run_pytest(root, files[:2])
        out += [f'pytest on {", ".join(files[:2])} (exit {code}):', summary]
    else:
        out.append('NEXT: run tests_for.py ' + args[0] + ' --run after your edit to check them.')
    print(clip('\n'.join(out)))


if __name__ == '__main__':
    main()
