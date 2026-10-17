"""Kaggle script entry: finds the mounted repo dataset, installs the test-only wheels the requests tasks need
(pytest-httpbin, httpbin, trustme: not in the Kaggle image nor in the competition wheelhouse, so tests/test_requests.py
errored on setup with any patch, V15-V16), and runs the evaluation.

The Kaggle dataset (scratchpad/ds3) holds this repository's tools/kaggle_eval.py as bench/kaggle_eval.py, the builds
as v<N>/ and the wheels as extra_wheels/ (with inline-snapshot, dirty-equals, executing and asttokens since 10-16). Set
SUBMISSION (the build folder) and TASKS (comma-separated) per run, or leave TASKS empty to choose SCREEN tasks this
environment can score (judge.screen: the reference passes the harness's check and some test fails without it) from
every task not in EXCLUDE. EXCLUDE below is the 30-task set of V25-30 (pick_tasks n=30 seed 0); tasks run 4 at a time."""
import glob
import os
import subprocess
import sys

SUBMISSION = 'vf4'
TASKS = 'fastapi_9753,rich_4075,fastapi_13207,rich_4076,fastapi_14419,fastapi_14953,rich_3043,fastapi_11194,fastapi_13537,rich_3905,fastapi_14459,fastapi_14361,rich_2943,fastapi_14372,rich_3718,fastapi_15661,requests_7505,rich_3934,fastapi_15280,rich_3180,fastapi_13713,rich_3480,fastapi_12942,rich_3278,fastapi_14258,requests_7205,fastapi_14297,rich_3772,fastapi_5624,rich_3942'
EXCLUDE = 'fastapi_13786,fastapi_14099,fastapi_14301,fastapi_14303,fastapi_14349,fastapi_14485,fastapi_14492,fastapi_14583,fastapi_14605,fastapi_14851,fastapi_14962,fastapi_14986,fastapi_15763,fastapi_15800,fastapi_9425,fastapi_9555,httpx_3672,requests_6592,requests_7433,requests_7502,rich_3006,rich_3469,rich_3472,rich_3506,rich_3521,rich_3535,rich_3675,rich_3777,rich_3782,rich_3882'
SCREEN = 30
# tasks at once: 1 as the official harness is assumed to run them; the fast build runs its own workers at once
CONCURRENCY = 1

hits = glob.glob('/kaggle/input/**/bench/kaggle_eval.py', recursive=True)
assert hits, 'bench/kaggle_eval.py not found under /kaggle/input'
root = os.path.dirname(os.path.dirname(hits[0]))
print('repo root:', root, flush=True)

wheels = os.path.join(root, 'extra_wheels')
if os.path.isdir(wheels):
    pip = [sys.executable, '-m', 'pip', 'install', '-q', '--no-index', '--find-links', wheels,
           'pytest-httpbin==2.1.0', 'httpbin~=0.10.0', 'trustme']
    rc = subprocess.call(pip)
    if rc != 0:     # a distro-installed package (blinker) cannot be uninstalled: install over it
        rc = subprocess.call(pip + ['--ignore-installed', 'blinker'])
    print('test wheels install exit code:', rc, flush=True)
    # V25-30: fastapi's test files import inline_snapshot and dirty_equals, which the Kaggle image lacks: their tests
    # did not collect and 8 of 16 fastapi tasks were unscorable; installed without touching the image's pytest or rich
    rc2 = subprocess.call([sys.executable, '-m', 'pip', 'install', '-q', '--no-index', '--find-links', wheels, '--no-deps',
                           'inline-snapshot', 'dirty-equals', 'executing', 'asttokens'])
    print('fastapi test wheels install exit code:', rc2, flush=True)
    subprocess.call([sys.executable, '-c', 'import pytest_httpbin, httpbin, trustme, pytest; '
                     'print("test deps ok, pytest", pytest.__version__)'])

cmd = [sys.executable, os.path.join(root, 'bench', 'kaggle_eval.py'), '--submission', os.path.join(root, SUBMISSION),
       '--out', f'/kaggle/working/run_{SUBMISSION}.csv', '--concurrency', str(CONCURRENCY)]
cmd += ['--tasks', TASKS] if TASKS else ['--screen', str(SCREEN), '--exclude', EXCLUDE]
print(' '.join(cmd), flush=True)
sys.exit(subprocess.call(cmd))
