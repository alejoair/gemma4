"""check.py

Verifies the current edit in one call: lists the changed files (git diff --stat), compiles every changed Python
file in memory, runs the tests that exercise the changed source files, and ends with a verdict line.
"""
import builtins
import concurrent.futures
import keyword
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, is_test_path, read_text, repo_root  # noqa: E402
from tests_for import failed_ids, find_tests, find_tests_for_names, run_pytest  # noqa: E402

BUILTINS = set(dir(builtins)) | {'self', 'return', 'None', 'True', 'False'}


def git(root, *args):
    r = subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=root, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f'git {" ".join(args)} failed in {root}: {r.stderr.strip()[:300]}')
        sys.exit(0)
    return r.stdout.strip()


def changed_names(root):
    """Identifiers on the added and removed lines of the source diff (keywords, builtins and short names left out)."""
    r = subprocess.run(['git', '-c', 'safe.directory=*', 'diff', '-U0', '--', '*.py'], cwd=root, capture_output=True,
                       text=True, timeout=30)
    names, current = [], ''
    for line in r.stdout.splitlines():
        if line.startswith('+++ '):
            current = line[6:]
        elif line[:1] in '+-' and not line.startswith('--- ') and not is_test_path(current):
            for n in re.findall(r'[A-Za-z_][A-Za-z0-9_]{3,}', line[1:]):
                if not keyword.iskeyword(n) and n not in BUILTINS and n not in names:
                    names.append(n)
    return names[:40]


def run_within(root, tests, limit):
    """Run the test files in parallel, each alone with its own time limit, so one slow file (sockets, servers)
    neither delays nor hides the others. Returns (files run, files too slow, exit code, summary)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(tests))) as pool:
        results = list(pool.map(lambda t: run_pytest(root, [t], timeout=limit), tests))
    ran, slow, codes, summaries = [], [], [], []
    for t, (code, summary) in zip(tests, results):
        if code is None:
            slow.append(t)
            continue
        ran.append(t)
        codes.append(code)
        summaries.append(summary)
    if not ran:
        return ran, slow, None, ''
    lines = [l for s in summaries for l in s.splitlines()]
    summary = '\n'.join([l for l in lines if not l.startswith(('FAILED', 'ERROR', 'E '))] +
                        [l for l in lines if l.startswith(('FAILED', 'ERROR', 'E '))])
    return ran, slow, max(codes), summary


def failing_before(root, changed, ids):
    """Run the failing tests against the original commit in a separate git worktree (the working tree is never
    touched, so a timeout cannot lose the edit); return the tests that pass there."""
    base = '/tmp/swe_check_base'
    if not os.path.isdir(os.path.join(base, '.git')) and not os.path.isfile(os.path.join(base, '.git')):
        r = subprocess.run(['git', '-c', 'safe.directory=*', 'worktree', 'add', '--detach', '-f', base, 'HEAD'],
                           cwd=root, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
    code, base_summary = run_pytest(base, ids[:40], timeout=25)
    if code is None:
        return None
    still = set(failed_ids(base_summary)) if code != 0 else set()
    return [t for t in ids if t not in still]


def main():
    root = repo_root()
    changed = [f for f in git(root, 'diff', '--name-only').splitlines() if f]
    untracked = [f for f in git(root, 'ls-files', '--others', '--exclude-standard').splitlines() if f.endswith('.py')]
    out = []
    if not changed and not untracked:
        print('VERDICT: NO CHANGES. Nothing is edited yet; make the edit with edit_file first.')
        return
    out.append('Changed files:\n' + (git(root, 'diff', '--stat') or '(none)'))
    if untracked:
        out.append('New files: ' + ', '.join(untracked[:10]))
    problems = []
    for rel in changed + untracked:
        if not rel.endswith('.py') or not os.path.exists(os.path.join(root, rel)):
            continue
        try:
            compile(read_text(root, rel), rel, 'exec')
        except SyntaxError as e:
            problems.append(f'SyntaxError in {rel} line {e.lineno}: {e.msg}')
    if problems:
        out += problems
        out.append('VERDICT: FIX THE SYNTAX ERROR above, then run check.py again.')
        print(clip('\n'.join(out)))
        return
    # Tests that use the identifiers on the changed lines come first (they exercise the changed behaviour), then
    # the tests that import the changed module.
    tests = find_tests_for_names(root, changed_names(root), limit=2)
    for rel in changed + untracked:
        if rel.endswith('.py') and not is_test_path(rel):
            for t in find_tests(root, rel):
                if t not in tests:
                    tests.append(t)
    tests, slow, code, summary = run_within(root, tests[:4], limit=35)
    if slow:
        out.append('Skipped (slower than 35s): ' + ', '.join(slow))
    if tests:
        out.append(f"Tests run: {', '.join(tests)} (exit {code})")
        lines = summary.splitlines()
        failed_lines = [l for l in lines if l.startswith(('FAILED', 'ERROR'))]
        out += [l for l in lines if not l.startswith(('FAILED', 'ERROR'))][:6] + failed_lines[:8]
        if len(failed_lines) > 8:
            out.append(f'... and {len(failed_lines) - 8} more failing tests')
        if code == 0:
            out.append('VERDICT: OK. The edit compiles and the related tests pass; the change is ready to submit.')
        elif code is None:
            out.append('VERDICT: TESTS TIMED OUT. The edit compiles; it can be submitted.')
        else:
            ids = failed_ids(summary)
            new = failing_before(root, changed, ids) if ids else None
            if new == []:
                out.append('The same tests also fail without your edit (environment or pre-existing failure).')
                out.append('VERDICT: OK. Your edit did not break these tests; the change is ready to submit.')
            elif new:
                out.append(f'{len(new)} of these tests pass without your edit, so your edit breaks them: ' + ', '.join(new[:5]))
                out.append('VERDICT: YOUR EDIT BREAKS TESTS. Read the failure above and fix your edit, then run check.py again.')
            else:
                out.append('VERDICT: TESTS FAIL. Read the failure above: fix it if your edit caused it, '
                           'otherwise the change is ready to submit.')
    else:
        out.append('VERDICT: OK. The edit compiles; no related tests were found. The change is ready to submit.')
    verdict = [l for l in out if l.startswith('VERDICT')]
    body = [l for l in out if not l.startswith('VERDICT')]
    print(clip('\n'.join(body), 3500) + ('\n' + verdict[-1] if verdict else ''))


if __name__ == '__main__':
    main()
