"""eval_env.py: make the subprocess sandbox of our local and Kaggle evaluations import the task's own code.

Our evaluations (tools/local_eval.py, tools/kaggle_eval.py, tools/judge.py) use swegemma's subprocess sandbox: it skips
the editable install and the sandbox setup script (install_test_dependencies returns at once for it), sets PYTHONPATH
to the workspace root only, and adds the host's site-packages to the sandbox venv. For a repository with a src/ layout
(requests: src/requests, httpx: src/httpx) the tests then import the copy installed on the host (a dependency of the
harness itself), never the workspace: every patch was invisible to the hidden tests (V21: the reference patch of
requests_7502 failed its own new test).

What the competition's scorer does is not known for certain. The documented public image (Dockerfile.public plus the
harness's verify_task) does NOT import a src/ package from the workspace either: install_editable_package
(`pip install -e /workspace`) runs first, then install_test_dependencies (_ensure_container_site_packages) unpacks the
wheels (requests, httpx, ... among them) straight into site-packages, so the wheel's copy shadows the editable
install, and the sandbox setup script's fast path only appends the workspace directories with a .pth file, after
site-packages (finding of the 2026-10-14 judge audit). Whether the private scorer's image differs is unknown. We import
the workspace's code anyway, because a patch must be visible to the tests for a verdict to mean anything; a patch to
a src/ package that passes here may therefore not pass in an image that behaves like the public one.

patch() does two things on every sandbox command:
  - PYTHONPATH: <workspace>/src (if it exists), then <workspace> (before site-packages: our deliberate deviation);
  - the official fast path (sandbox/setup.py execute_fast_path): a workspace_paths.pth in the sandbox venv's
    site-packages listing <workspace> and every non-hidden top-level directory of it, read after site-packages
    (after _host_env.pth, as the .pth files are read in name order), exactly where the official image puts them.
    Those directories are not put on PYTHONPATH: before the standard library, rich/json.py or fastapi/types.py
    would shadow the modules of the same name.
"""
from __future__ import annotations

import os
from pathlib import Path


def fast_path_dirs(work: Path) -> list[str]:
    """The directories the official fast path adds: the workspace and its non-hidden top-level directories."""
    paths = [str(work)]
    try:
        paths += [str(p) for p in sorted(work.iterdir()) if p.is_dir() and not p.name.startswith('.')]
    except OSError:
        pass
    return paths


def _host_package_dirs() -> list[str]:
    """The host's package directories, 'dist-packages' included. swegemma's _host_env.pth lists only paths that contain
    'site-packages'; on Kaggle's image (Debian's python) the host packages are in /usr/local/lib/python3.12/dist-packages
    and reach the sandbox only as system site packages, which Python adds after every .pth of the venv. The workspace
    directories of workspace_paths.pth then came first: V22 (10-15) imported fastapi/requests.py as `requests` (from
    langsmith's pytest plugin), so every reproduction test and every reference run of the fastapi tasks failed. Listing
    the host directories first keeps the official order: the workspace directories after the installed packages."""
    import sys
    return [p for p in sys.path if p and ('site-packages' in p or 'dist-packages' in p) and Path(p).is_dir()]


def task_python() -> str:
    """SWE_TASK_PYTHON: the Python of a venv that holds one task's dependencies (tools/swegym.py env --python), for
    tasks whose code or pinned dependencies do not run on the harness's Python. The sandbox's venv is then made from
    it, so the skill's scripts and the tests run on the task's Python while the harness stays on its own."""
    return os.environ.get('SWE_TASK_PYTHON', '')


def _task_site_packages() -> list[str]:
    import json
    import subprocess
    out = subprocess.run([task_python(), '-c', 'import json, site; print(json.dumps(site.getsitepackages()))'],
                         check=True, capture_output=True, text=True).stdout
    return [p for p in json.loads(out) if Path(p).is_dir()]


def _write_pth(work: Path) -> None:
    """workspace_paths.pth in the venv of the subprocess sandbox that owns this workspace (<root>/venv)."""
    host = _task_site_packages() if task_python() else _host_package_dirs()
    text = '\n'.join(dict.fromkeys(host + fast_path_dirs(work))) + '\n'
    for sp_dir in (work.parent / 'venv').glob('lib/python*/site-packages'):
        pth = sp_dir / 'workspace_paths.pth'
        try:
            if not pth.exists() or pth.read_text(encoding='utf-8') != text:
                pth.write_text(text, encoding='utf-8')
        except OSError:
            pass


def patch() -> None:
    import swegemma.sandbox.subprocess as sp

    if getattr(sp, '_src_layout_patched', False):
        return
    original = sp.build_sanitized_env

    def build_sanitized_env(*args, **kwargs):
        extra = dict(kwargs.get('extra_env') or {})
        work = kwargs.get('work_dir')
        if work is not None:
            work = Path(work)
            front = ([str(work / 'src')] if (work / 'src').is_dir() else []) + [str(work)]
            parts = front + [p for p in str(extra.get('PYTHONPATH', '')).split(':') if p]
            extra['PYTHONPATH'] = ':'.join(dict.fromkeys(parts))
            kwargs['extra_env'] = extra
            _write_pth(work)
        if os.environ.get('SWE_PAUSED_FILE'):
            # tools/step_gate.py: the scripts leave the time a step was held out of their clock
            extra['SWE_PAUSED_FILE'] = os.environ['SWE_PAUSED_FILE']
            kwargs['extra_env'] = extra
        return original(*args, **kwargs)

    sp.build_sanitized_env = build_sanitized_env
    if task_python():
        _patch_venv(sp)
    sp._src_layout_patched = True


def _patch_venv(sp) -> None:
    """The sandbox's venv made from SWE_TASK_PYTHON instead of the harness's Python: its _host_env.pth lists the task
    venv's site-packages (not the harness's: packages built for another Python do not import there). The sandbox runs
    every command with this venv's python3 first on PATH (skill scripts through the ADK's wrapper, the tests)."""
    import shutil
    import subprocess

    original = sp.SubprocessManager.start

    def start(self):
        sandbox_id = original(self)
        venv_dir = Path(self._sandboxes[sandbox_id]['venv'])
        shutil.rmtree(venv_dir, ignore_errors=True)
        subprocess.run([task_python(), '-m', 'venv', '--without-pip', str(venv_dir)], check=True)
        for sp_dir in venv_dir.glob('lib/python*/site-packages'):
            (sp_dir / '_host_env.pth').write_text('\n'.join(_task_site_packages()) + '\n', encoding='utf-8')
        return sandbox_id

    sp.SubprocessManager.start = start


def active() -> bool:
    """Whether patch() is in force in this process (part of the judge's environment fingerprint)."""
    try:
        import swegemma.sandbox.subprocess as sp
    except ImportError:
        return False
    return bool(getattr(sp, '_src_layout_patched', False))
