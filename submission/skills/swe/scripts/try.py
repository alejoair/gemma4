"""try.py <python code>

Runs a snippet of Python (several lines are fine) in a temporary directory outside the repository, with the
repository's code importable, and prints its output. Use it to check how something behaves; it never creates files
in the repository, so nothing it does ends up in the patch.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, repo_root  # noqa: E402
from tests_for import code_path  # noqa: E402


def main():
    code = '\n'.join(sys.argv[1:])
    if not code.strip():
        print('usage: try.py <python code>')
        print('NEXT: call try.py with the lines of Python to run, for example ["import json", "print(json.dumps(1))"].')
        return
    code = code.replace('⏎', '\n')
    # A shell command (python3 -c '...') instead of Python code: run the code inside it.
    m = re.match(r'\s*python[0-9.]*\s+-c\s+(.+)$', code, re.S)
    if m:
        try:
            parts = shlex.split(m.group(1))
            code = parts[0] if parts else code
        except ValueError:
            pass
    if '\n' not in code and '\\n' in code:
        code = code.replace('\\n', '\n').replace('\\t', '\t')
    root = repo_root()
    tmp = tempfile.mkdtemp(prefix='swe_try_')
    try:
        path = os.path.join(tmp, 'snippet.py')
        with open(path, 'w') as fh:
            fh.write(code + '\n')
        env = dict(os.environ, PYTHONPATH=code_path(root), PYTHONDONTWRITEBYTECODE='1')
        try:
            r = subprocess.run([sys.executable, path], cwd=tmp, capture_output=True, text=True, timeout=30, env=env)
        except subprocess.TimeoutExpired:
            print('The snippet ran for more than 30 s and was stopped.')
            print('NEXT: try a smaller snippet, or go on with your fix.')
            return
        out = [f'exit code {r.returncode}']
        if r.stdout.strip():
            out += ['--- stdout ---', r.stdout.rstrip()]
        if r.stderr.strip():
            out += ['--- stderr ---', r.stderr.rstrip()[-2000:]]
        if not r.stdout.strip() and not r.stderr.strip():
            out.append('The snippet ran and printed nothing (add print() calls to see values).')
        out.append('NEXT: use what you learned for your edit; the snippet left no files in the repository.')
        print(clip('\n'.join(out), 4000))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
