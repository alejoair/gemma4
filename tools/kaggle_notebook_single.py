"""Kaggle script entry: finds the mounted repo dataset, installs the test-only wheels the requests tasks need
(pytest-httpbin, httpbin, trustme: not in the Kaggle image nor in the competition wheelhouse, so tests/test_requests.py
errored on setup with any patch, V15-V16), and runs the evaluation."""
import glob
import os
import subprocess
import sys

SUBMISSION = 'v16'
TASKS = 'requests_7328'

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
    subprocess.call([sys.executable, '-c', 'import pytest_httpbin, httpbin, trustme, pytest; '
                     'print("test deps ok, pytest", pytest.__version__)'])

cmd = [sys.executable, os.path.join(root, 'bench', 'kaggle_eval.py'), '--submission', os.path.join(root, SUBMISSION),
       '--tasks', TASKS, '--out', f'/kaggle/working/run_{SUBMISSION}.csv']
print(' '.join(cmd), flush=True)
sys.exit(subprocess.call(cmd))
