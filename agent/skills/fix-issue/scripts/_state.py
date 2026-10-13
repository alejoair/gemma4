"""State kept between the model's calls: JSON files and an event log in /tmp, one directory per repository, so state
from one task never leaks into another."""
import hashlib
import json
import os
import time

try:
    import fcntl
except ImportError:      # (not on Windows; the sandbox is Linux)
    fcntl = None

import _repo

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)


_HEADS = {}


def directory():
    """One directory per repository path and original commit: a reused sandbox path never sees an earlier task. A rerun
    of the same task gets a new directory too: the harness commits a fresh baseline (_swegemma_baseline, its own
    timestamp) before each run, so HEAD differs (review: a stale journal of the same task)."""
    root = _repo.repo_root()
    base = os.environ.get('SWE_STATE_ROOT', '/tmp')
    if root not in _HEADS:
        _HEADS[root] = (_repo.git(root, 'rev-parse', 'HEAD') or '').strip()
    tag = hashlib.sha1((os.path.abspath(root) + _HEADS[root]).encode()).hexdigest()[:10]
    d = os.path.join(base, f'swe_state_{tag}')
    os.makedirs(d, exist_ok=True)
    return d


def path(name):
    return os.path.join(directory(), name)


def load(name, default=None):
    try:
        with open(path(name + '.json'), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save(name, value):
    tmp = path(name + '.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(value, fh)
    os.replace(tmp, path(name + '.json'))


def record(event):
    """Append one event (a dict) to the log, with its time."""
    event = dict(event, t=time.time())
    with open(path('events.jsonl'), 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(event) + '\n')


def events():
    try:
        with open(path('events.jsonl'), encoding='utf-8') as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError):
        return []


_LOCK = []


def lock():
    """Holds the state directory for this call (released when the process ends). Two agents calling at once (ADK may
    run parallel tool calls) would otherwise read, change and write the journal over each other (review)."""
    if fcntl is None or _LOCK:
        return
    fh = open(path('lock'), 'w')
    deadline = time.time() + 120
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() > deadline:
                break           # never block the task for good: go on unlocked
            time.sleep(0.2)
    _LOCK.append(fh)
