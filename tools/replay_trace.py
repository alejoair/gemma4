"""replay_trace.py <scripts dir> <snapshot repo> <trace.json> [<trace.json> ...]

Replays every run_skill_script call of harness traces (Kaggle `trace_<task>.json`, the steps of every agent in order)
on a clean copy of the task's snapshot, with the scripts of a build, and reports each answer that is a fault of the
script: a traceback, an 'internal error', an empty answer, or an answer without a NEXT line. It is the regression check
after a change of the scripts (review: fixes were tested only by new model runs; this replays the calls that made each
fault). A replay is not a model run: the calls after the first changed answer may no longer fit the state, so it
catches crashes, not whether a change helps.

Each trace starts from the snapshot as it was (git checkout + clean) and from an empty journal. Before the first
call the snapshot gets the sandbox's test setup (tools/hermetic.py: the harness's pytest.ini, conftest marker and
baseline commit, once per snapshot), so the scripts collect and run tests as they do in the harness's sandbox. Exit
status 1 when a call had a fault.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermetic import hermetic  # noqa: E402

LIMIT = int(os.environ.get('REPLAY_TIMEOUT', '240'))     # eval_config.yaml timeout_seconds


def calls(trace):
    """(file_path, args) of every run_skill_script call of the trace, in order."""
    d = json.load(open(trace))
    for s in d.get('steps', []):
        for tc in s.get('tool_calls') or []:
            if tc.get('function_name') != 'run_skill_script':
                continue
            a = tc.get('arguments') or {}
            args = a.get('args') if isinstance(a, dict) else None
            if isinstance(args, list):
                yield a.get('file_path') or 'step.py', [str(x) for x in args]


def replay(scripts, repo, trace):
    subprocess.run(['git', 'checkout', '-q', '.'], cwd=repo)
    subprocess.run(['git', 'clean', '-qfd'], cwd=repo)
    if hermetic(repo):
        print(f'(hermetic: the sandbox test setup was committed in {repo})')
    faults = 0
    with tempfile.TemporaryDirectory() as state:
        # (no __pycache__ in the build: the competition's validator rejects .pyc files)
        env = dict(os.environ, SWE_REPO=repo, SWE_STATE_ROOT=state, PWD=repo, PYTHONDONTWRITEBYTECODE='1')
        for n, (file_path, args) in enumerate(calls(trace), 1):
            script = os.path.join(scripts, os.path.basename(file_path))
            if not os.path.exists(script):
                print(f'#{n} {file_path}: not in the build (the harness answers: script not found)')
                continue
            t0 = time.time()
            try:
                r = subprocess.run([sys.executable, script] + args, cwd=repo, env=env, capture_output=True,
                                   text=True, timeout=LIMIT)
            except subprocess.TimeoutExpired:
                faults += 1
                print(f'#{n} FAULT {file_path} {json.dumps(args)[:100]} => over {LIMIT} s (the harness stops a '
                      'command at its timeout_seconds)')
                continue
            took = time.time() - t0
            out = r.stdout.strip()
            last = out.splitlines()[-1] if out else ''
            bad = (r.returncode != 0 or 'Traceback' in r.stderr or 'internal error' in out.lower() or not out
                   or not last.startswith('NEXT'))
            faults += bad
            first = next((l for l in out.splitlines() if l.strip()), '')
            print(f'#{n} {"FAULT " if bad else ""}{file_path} {json.dumps(args)[:100]} ({took:.0f} s) => {first[:140]}')
            if bad:
                print('   stderr:', r.stderr[-600:].replace('\n', '\n   '))
                print('   end of answer:', out[-300:].replace('\n', '\n   '))
    return faults


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    scripts, repo = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    total = sum(replay(scripts, repo, t) for t in sys.argv[3:])
    print('NO PROBLEMS' if not total else f'{total} FAULTS')
    sys.exit(1 if total else 0)


if __name__ == '__main__':
    main()
