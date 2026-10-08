"""check.py

Verifies the current edit in one call: lists the changed files (git diff --stat), compiles every changed Python
file in memory, runs the tests that exercise the changed source files, and ends with a verdict line.
"""
import builtins
import concurrent.futures
import keyword
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import GOOD, _state_path, clip, is_test_path, read_text, repo_root, worktree_diff  # noqa: E402
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
        if code == 5:  # pytest collected no tests in this file
            continue
        ran.append(t)
        codes.append(code)
        summaries.append(summary)
    if not ran:
        return ran, slow, None, ''
    lines = [l for s in summaries for l in s.splitlines()]
    summary = '\n'.join([l for l in lines if not l.startswith(('FAILED', 'ERROR', 'E '))] +
                        [l for l in lines if l.startswith(('FAILED', 'ERROR', 'E '))])
    return ran, slow, (max(codes) if any(codes) else 0), summary


def save_good(root):
    """Remember the current diff as the last state that passed check.py."""
    diff = worktree_diff(root)
    if diff is not None:
        with open(GOOD, 'w') as fh:
            fh.write(diff)


def rollback(root, changed):
    """Undo a failing edit: put the changed files back to the last state that passed check.py (or to the original
    commit when none passed yet), so a broken edit never ends up in the submitted patch."""
    tracked = [f for f in changed if f]
    if not tracked:
        return ''
    subprocess.run(['git', '-c', 'safe.directory=*', 'checkout', '--', *tracked], cwd=root, capture_output=True,
                   timeout=30)
    restored = 'the original code'
    good = open(GOOD).read() if os.path.exists(GOOD) else ''
    # Only the git part of the saved state can be applied; new files listed after it were never removed.
    good = re.split(r'^\+\+\+ b/\S+ \(new file\)$', good, maxsplit=1, flags=re.M)[0]
    if good.strip():
        r = subprocess.run(['git', '-c', 'safe.directory=*', 'apply', '--whitespace=nowarn', '-'], cwd=root,
                           input=good, capture_output=True, text=True, timeout=30)
        restored = 'the last version that passed check.py' if r.returncode == 0 else 'the original code'
    return f'Your edit was undone: {", ".join(tracked)} is back to {restored}.'


def removal_warning(root):
    """A warning when the edit mostly deletes code or removes definitions, which usually drops existing behaviour."""
    r = subprocess.run(['git', '-c', 'safe.directory=*', 'diff', '-U0', '--', '*.py'], cwd=root, capture_output=True,
                       text=True, timeout=30)
    added, removed, current = [], [], ''
    for line in r.stdout.splitlines():
        if line.startswith('+++ '):
            current = line[6:]
        elif is_test_path(current) or line.startswith('--- '):
            continue
        elif line.startswith('+') and line[1:].strip():
            added.append(line[1:])
        elif line.startswith('-') and line[1:].strip():
            removed.append(line[1:])
    defined = set(re.findall(r'^\s*(?:async\s+)?(?:def|class)\s+(\w+)', '\n'.join(added), re.M))
    gone = [n for n in re.findall(r'^\s*(?:async\s+)?(?:def|class)\s+(\w+)', '\n'.join(removed), re.M) if n not in defined]
    if gone:
        return 'WARNING: your edit removes the definitions ' + ', '.join(gone[:5]) + '.'
    if len(removed) >= 5 and 3 * len(added) <= len(removed):
        return (f'WARNING: your edit removes {len(removed)} lines and adds only {len(added)}; '
                'it may delete existing behaviour instead of changing it.')
    return ''


def failing_before(root, changed, ids):
    """Run the failing tests against the original commit in a separate git worktree (the working tree is never
    touched, so a timeout cannot lose the edit); return the tests that pass there."""
    # One clean copy per repository (state path keyed by the workspace), checked to be at this repository's HEAD,
    # so tasks that share /tmp never compare against another task's code.
    base = _state_path('base')
    head = subprocess.run(['git', '-c', 'safe.directory=*', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
                          text=True, timeout=30).stdout.strip()
    there = subprocess.run(['git', '-c', 'safe.directory=*', 'rev-parse', 'HEAD'], cwd=base, capture_output=True,
                           text=True, timeout=30).stdout.strip() if os.path.isdir(base) else ''
    if not head or there != head or not os.path.exists(os.path.join(base, '.git')):
        shutil.rmtree(base, ignore_errors=True)
        subprocess.run(['git', '-c', 'safe.directory=*', 'worktree', 'prune'], cwd=root, capture_output=True, timeout=30)
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
    # Hidden files are the harness' own runners (.adk_exec_*.py), not edits.
    untracked = [f for f in git(root, 'ls-files', '--others', '--exclude-standard').splitlines()
                 if f.endswith('.py') and not os.path.basename(f).startswith('.')]
    out = []
    if not changed and not untracked:
        print('VERDICT: NO CHANGES. Nothing is edited yet; make the edit with edit.py first.')
        return
    out.append('Changed files:\n' + (git(root, 'diff', '--stat') or '(none)'))
    if untracked:
        out.append('New files: ' + ', '.join(untracked[:10]) + '. New files end up in the patch: if you created any '
                   'of them only to try something, delete it (use try.py for experiments).')
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
        out.append(rollback(root, changed))
        out.append('VERDICT: FIX THE SYNTAX ERROR above: call show.py again and redo the edit, then run check.py again.')
        print(clip('\n'.join(out)))
        return
    warning = removal_warning(root)
    if warning:
        out.append(warning)
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
                out.append(rollback(root, changed))
                out.append('VERDICT: YOUR EDIT BREAKS TESTS. Read the failure above, call show.py again and make a '
                           'corrected edit that keeps the existing behaviour, then run check.py again.')
            else:
                out.append('VERDICT: TESTS FAIL. Read the failure above: fix it if your edit caused it, '
                           'otherwise the change is ready to submit.')
    else:
        out.append('VERDICT: OK. The edit compiles; no related tests were found. The change is ready to submit.')
    verdict = [l for l in out if l.startswith('VERDICT')]
    body = [l for l in out if not l.startswith('VERDICT')]
    if verdict and verdict[-1].startswith('VERDICT: OK'):
        save_good(root)
        verdict[-1] += ' If the statement needs no other change, finish now as your instructions say.'
        if warning:
            verdict[-1] += (' But first read the WARNING above: if the statement does not ask to remove that code, '
                            'change it so the existing behaviour is kept.')
    print(clip('\n'.join(body), 3500) + ('\n' + verdict[-1] if verdict else ''))


if __name__ == '__main__':
    main()
