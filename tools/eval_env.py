"""eval_env.py: make the subprocess sandbox of our local and Kaggle evaluations import the task's own code.

In the competition the sandbox is a docker container where the repository is installed in editable mode, so the tests
import the workspace's code. Our evaluations (tools/local_eval.py, tools/kaggle_eval.py, tools/judge.py) use swegemma's
subprocess sandbox instead: it skips the editable install, sets PYTHONPATH to the workspace root only, and adds the
host's site-packages to the sandbox venv. For a repository with a src/ layout (requests: src/requests, httpx:
src/httpx) the tests then import the copy installed on the host (a dependency of the harness itself), never the
workspace: every patch was invisible to the hidden tests (V21: the reference patch of requests_7502 failed its own
new test). patch() puts <workspace>/src before the workspace root, as an editable install would.
"""
from __future__ import annotations

from pathlib import Path


def patch() -> None:
    import swegemma.sandbox.subprocess as sp

    if getattr(sp, '_src_layout_patched', False):
        return
    original = sp.build_sanitized_env

    def build_sanitized_env(*args, **kwargs):
        extra = dict(kwargs.get('extra_env') or {})
        work = kwargs.get('work_dir')
        if work is not None and (Path(work) / 'src').is_dir():
            parts = [str(Path(work) / 'src')] + [p for p in str(extra.get('PYTHONPATH', '')).split(':') if p]
            extra['PYTHONPATH'] = ':'.join(dict.fromkeys(parts))
            kwargs['extra_env'] = extra
        return original(*args, **kwargs)

    sp.build_sanitized_env = build_sanitized_env
    sp._src_layout_patched = True
