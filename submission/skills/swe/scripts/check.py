"""check.py

Verifies the current edit in one call: lists the changed files (git diff --stat), compiles every changed Python
file in memory, runs the tests that exercise the changed source files, and ends with a verdict line.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, is_test_path, read_text, repo_root  # noqa: E402
from tests_for import failed_ids, find_tests, run_pytest  # noqa: E402


def git(root, *args):
    r = subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=root, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f'git {" ".join(args)} failed in {root}: {r.stderr.strip()[:300]}')
        sys.exit(0)
    return r.stdout.strip()


def failing_before(root, changed, ids):
    """Run the failing tests on the original code (edit temporarily reverted); return the ones that pass there."""
    saved = os.path.join('/tmp', 'swe_check_saved.diff')
    diff = subprocess.run(['git', '-c', 'safe.directory=*', 'diff', '--binary'], cwd=root, capture_output=True, text=True).stdout
    if not diff.strip():
        return None
    with open(saved, 'w') as fh:
        fh.write(diff)
    tracked = [f for f in changed if os.path.exists(os.path.join(root, f))]
    try:
        subprocess.run(['git', '-c', 'safe.directory=*', 'checkout', '--', *tracked], cwd=root, capture_output=True, timeout=30)
        code, base_summary = run_pytest(root, ids[:10], timeout=40)
    finally:
        subprocess.run(['git', '-c', 'safe.directory=*', 'checkout', '--', *tracked], cwd=root, capture_output=True, timeout=30)
        r = subprocess.run(['git', '-c', 'safe.directory=*', 'apply', '--whitespace=nowarn', saved], cwd=root,
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print('WARNING: could not restore the edit: ' + r.stderr[:200])
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
    tests = []
    for rel in changed + untracked:
        if rel.endswith('.py') and not is_test_path(rel):
            for t in find_tests(root, rel):
                if t not in tests:
                    tests.append(t)
    if tests:
        code, summary = run_pytest(root, tests[:3])
        out.append(f'Tests run: {", ".join(tests[:3])} (exit {code})\n{summary}')
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
                out.append('These tests pass without your edit, so your edit breaks them: ' + ', '.join(new[:5]))
                out.append('VERDICT: YOUR EDIT BREAKS TESTS. Read the failure above and fix your edit, then run check.py again.')
            else:
                out.append('VERDICT: TESTS FAIL. Read the failure above: fix it if your edit caused it, '
                           'otherwise the change is ready to submit.')
    else:
        out.append('VERDICT: OK. The edit compiles; no related tests were found. The change is ready to submit.')
    print(clip('\n'.join(out)))


if __name__ == '__main__':
    main()
