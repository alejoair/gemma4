"""hermetic.py: give a local copy of a task's repository the test setup of the harness's sandbox.

swegemma prepares every sandbox workspace (the agent's, and the one the hidden tests run in) with
setup_container_workspace: git exclude rules, setup_workspace_test_config (a root pytest.ini with the harness's
discovery rules, `-p no:anyio` and warning filters, and a marker line at the top of the root conftest.py), and a
baseline commit tagged _swegemma_baseline. A repository with its own pytest config (rich: tests/pytest.ini, fastapi:
pyproject.toml) collects and runs tests differently without that pytest.ini, so a dry run or a replay of the scripts
on a bare snapshot does not judge tests the way the sandbox does. hermetic(repo_dir) applies the same three steps.

The pytest.ini/conftest script is read from the installed harness (swegemma/harness/container_setup.py), so it
follows the harness version; a copy of swegemma 0.2.11's script is the fallback when the harness is not installed.

  python tools/hermetic.py <repo dir> [<repo name, e.g. Textualize/rich>] [--overwrite]
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

EXCLUDE = '__pycache__/\n*.pyc\n.pytest_cache/\n*.egg-info/\nbuild/\ndist/\n.coverage\n.adk_exec_*.py\n'
TAG = '_swegemma_baseline'
MARKER_INI = '[pytest]'
MARKER_CONFTEST = '# Hermetic test discovery hook for SWE-gemma'

# swegemma 0.2.11 setup_workspace_test_config's script (used only when the harness is not importable)
_FALLBACK_SCRIPT = r'''
import sys
from pathlib import Path

ws = Path(sys.argv[1]).resolve()
repo = sys.argv[2] if len(sys.argv) > 2 else ""
overwrite = sys.argv[3] == "1" if len(sys.argv) > 3 else False

ini_path = ws / "pytest.ini"
existing_ini = ""
if ini_path.exists():
    try:
        existing_ini = ini_path.read_text(errors="replace")
    except Exception:
        existing_ini = ""

if overwrite or ("# Hermetic test discovery" not in existing_ini and "[pytest]" not in existing_ini):
    content = """[pytest]
addopts = -p no:anyio
norecursedirs = .* build dist venv
python_classes = Test* *Test
python_files = test_*.py *_test.py
filterwarnings =
    ignore::DeprecationWarning
    ignore::UserWarning
"""
    try:
        ini_path.write_text(content)
    except Exception as e:
        sys.stderr.write(f"Warning: could not write {ini_path}: {e}\n")

conftest_path = ws / "conftest.py"
existing = ""
if conftest_path.exists():
    try:
        existing = conftest_path.read_text(errors="replace")
    except Exception:
        existing = ""

hook_header = "# Hermetic test discovery hook for SWE-gemma\n"
if overwrite or (
    "# Hermetic test discovery hook for SWE-gemma" not in existing
    and "# Standard test discovery hook for SWE-gemma" not in existing
):
    clean_existing = existing.replace(hook_header, "").strip()
    new_conftest = hook_header + ("\n" + clean_existing if clean_existing else "") + "\n"
    try:
        conftest_path.write_text(new_conftest)
    except Exception as e:
        sys.stderr.write(f"Warning: could not write {conftest_path}: {e}\n")
'''


def harness_script() -> tuple[str, str]:
    """(script, origin): setup_workspace_test_config's script from the installed harness, else the 0.2.11 copy."""
    try:
        spec = importlib.util.find_spec('swegemma')
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.submodule_search_locations:
        for loc in spec.submodule_search_locations:
            src = Path(loc) / 'harness' / 'container_setup.py'
            if src.exists():
                text = src.read_text(encoding='utf-8')
                body = text[text.find('def setup_workspace_test_config'):]
                m = re.search(r"script = r'''(.*?)'''", body, re.S)
                if m:
                    return m.group(1), str(src)
    return _FALLBACK_SCRIPT, 'built-in copy of swegemma 0.2.11'


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=check)


def is_hermetic(repo_dir) -> bool:
    """The baseline tag is HEAD and the harness's pytest.ini and conftest marker are committed."""
    repo = Path(repo_dir)
    head = _git(repo, 'rev-parse', 'HEAD', check=False).stdout.strip()
    tag = _git(repo, 'rev-parse', '-q', '--verify', f'refs/tags/{TAG}^{{commit}}', check=False).stdout.strip()
    if not head or head != tag:
        return False
    try:
        ini = (repo / 'pytest.ini').read_text(errors='replace')
        conf = (repo / 'conftest.py').read_text(errors='replace')
    except OSError:
        return False
    return MARKER_INI in ini and MARKER_CONFTEST in conf


def hermetic(repo_dir, repo: str = '', overwrite: bool = False, force: bool = False) -> bool:
    """Apply setup_git_exclude, setup_workspace_test_config and setup_baseline_commit to repo_dir, as the sandbox
    does (overwrite=False, as setup_container_workspace; verify_task rewrites pytest.ini with overwrite=True after
    the test patch). Whatever is uncommitted in repo_dir goes into the baseline commit, as in the sandbox: clean the
    tree first. Idempotent: returns False, doing nothing, when repo_dir is already hermetic (unless force)."""
    path = Path(repo_dir).resolve()
    if not (path / '.git').exists():
        raise ValueError(f'{path} is not a git repository')
    if not force and is_hermetic(path):
        return False
    exclude = path / '.git' / 'info' / 'exclude'
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text() if exclude.exists() else ''
    if EXCLUDE not in current:
        with open(exclude, 'a') as fh:
            fh.write(EXCLUDE)
    script, _ = harness_script()
    subprocess.run([sys.executable, '-S', '-c', script, str(path), repo, '1' if overwrite else '0'], check=True)
    _git(path, 'add', '-A')
    _git(path, '-c', 'user.email=agent@eval', '-c', 'user.name=Agent', 'commit', '-m', 'eval_baseline',
         '--allow-empty', '-q')
    _git(path, 'tag', '-f', TAG)
    return True


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(2)
    changed = hermetic(args[0], args[1] if len(args) > 1 else '', overwrite='--overwrite' in sys.argv,
                       force='--force' in sys.argv)
    print(f'{args[0]}: {"baseline committed" if changed else "already hermetic"} '
          f'(script: {harness_script()[1]})')


if __name__ == '__main__':
    main()
