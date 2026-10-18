"""The issue's own example, run: a code block of the statement that imports the repository's package is run on the
original code (once, at the start) and again after the edits, and both outputs are shown (checklist F2, W5: the model
repairs well from a traceback or output it can read; "Kept, not checked" said nothing about the new behaviour).
10 of the 129 dev statements have such a block."""
import ast
import os
import re
import subprocess
import sys

import _repo
import _state
import _tests

TIMEOUT = 20
MAX_OUT = 900


# Modules at the top of a repository that run its tooling, not its code (build hooks, task runners)
TOOL_MODULES = {'setup', 'conftest', 'noxfile', 'fabfile', 'tasks', 'dodo', 'pavement', 'manage', 'versioneer',
                'hatch_build', 'pdm_build', 'build', 'make', 'runtests', 'run_tests', 'bootstrap', 'release'}


def _packages(root):
    """The repository's top-level importable names, from each of its import roots (src/, lib/ ... layouts): its
    packages (namespace packages too) and its top-level modules (audit 10-18 #19: a single-module project never matched
    the statement's example). A module at the repository root beside a package is tooling (setup.py, pdm_build.py)
    unless the packaging files declare it."""
    declared = _repo.declared_packages(root)
    packages, modules = set(), {}
    for rel in _repo.iter_py(root, docs=False):
        mod = _repo.module_name(root, rel)
        head = mod.split('.')[0]
        # gk15 (rich_3894): 'benchmarks' (asv's) was named as the repository's package next to rich
        if not head or head.startswith(('.', '_')) or head in _repo.TEST_DIRS or _repo.is_aux_name(head, root) or \
                head in ('tools', 'tasks', 'site'):
            continue
        if _repo.in_package(root, rel):
            packages.add(head)
        elif '.' not in mod:
            modules[head] = '/' in rel             # src/name.py, not name.py at the repository root
    return packages | {m for m, below in modules.items() if m not in packages and m not in TOOL_MODULES and (
        below or not packages or _repo._norm(m) in declared)}


def find(root, text):
    """The first code block of the statement that parses as Python and imports the repository's package, or ''.
    Interpreter prompts (>>> and ...) are removed; a block without an import of the package is a fragment."""
    pkgs = _packages(root)
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    for block in re.findall(r'```[\w+-]*[ \t]*\n(.*?)```', text, flags=re.S):
        lines = [re.sub(r'^(>>>|\.\.\.) ?', '', l) for l in block.splitlines()
                 if not l.lstrip().startswith('$')]
        if any(l.startswith(('>>>', '...')) for l in block.splitlines()):
            lines = [re.sub(r'^(>>>|\.\.\.) ?', '', l) for l in block.splitlines() if l.startswith(('>>>', '...'))]
        code = '\n'.join(lines).strip('\n')
        try:
            ast.parse(code)
        except (SyntaxError, ValueError):
            continue
        if any(re.search(rf'^\s*(import|from)\s+{re.escape(p)}\b', code, re.M) for p in pkgs):
            return code
    return ''


def run(root, code):
    """The example's output (stdout and stderr, the end kept) and whether it raised. It runs from the state
    directory, never the repository (files it writes would enter the patch), and not as __main__ (a server
    started under `if __name__ == '__main__'` would never end)."""
    path = _state.path('example.py')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(code + '\n')
    limit = _tests._budget(TIMEOUT)
    try:
        p = subprocess.run([sys.executable, '-c', 'import runpy, sys; runpy.run_path(sys.argv[1], run_name="example")',
                            path], cwd=_state.directory(), env=_tests._env(root), capture_output=True, text=True,
                           timeout=limit, stdin=subprocess.DEVNULL)
        out, failed = (p.stdout + p.stderr).strip(), p.returncode != 0
    except subprocess.TimeoutExpired:
        out, failed = f'(stopped after {limit:.0f} s)', True
    except OSError as e:
        out, failed = f'(could not run: {e})', True
    out = out.replace(_state.directory() + os.sep, '').replace(root + os.sep, '')
    if 'Traceback (most recent call last):' in out:
        # the runner's own frames (runpy) say nothing about the issue: the traceback starts at the example
        head, _, tb = out.partition('Traceback (most recent call last):')
        k = tb.find('  File "example.py"')
        out = head + 'Traceback (most recent call last):\n' + (tb[k:] if k >= 0 else tb.lstrip('\n'))
    if len(out) > MAX_OUT:
        out = '...\n' + out[-MAX_OUT:]
    return out or '(no output)', failed


def start(root, text):
    """At the start (original code): finds the example and records its output."""
    code = find(root, text)
    if not code:
        _state.save('example', {})
        return
    out, failed = run(root, code)
    _state.save('example', {'code': code, 'before': out, 'failed_before': failed})


def view(root):
    """The example run now, beside its output on the original code; '' when the issue has none."""
    ex = _state.load('example', {}) or {}
    if not ex.get('code'):
        return ''
    out, failed = run(root, ex['code'])
    same = out == ex['before']
    lines = ex['code'].splitlines()
    shown = '\n'.join(lines[:12] + (['...'] if len(lines) > 12 else []))
    return (f"The issue's example, run with the patch (it imports the package as the issue does):\n{shown}\n"
            f"On the original code it {'failed' if ex['failed_before'] else 'printed'}:\n{ex['before']}\n"
            f"With the patch it {'fails' if failed else 'prints'}"
            + (' the same: the patch does not change what the example shows yet.' if same else f':\n{out}'))
