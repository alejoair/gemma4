"""SWE-Gym (Pan et al., ICML 2025; docs/literature.md) as a second task set, in the competition's data layout.

SWE-Gym is not a Python package: it is 2,438 SWE-bench-style tasks from 11 Python repositories (Lite: 230) on Hugging
Face (SWE-Gym/SWE-Gym, SWE-Gym/SWE-Gym-Lite), plus training scripts. This tool turns its tasks into what our tools read
(tools/local_eval.py, tools/judge.py: --data <dir> with tasks.jsonl, snapshots/<task>.tgz, wheels/), so the agent can
be run and judged on repositories it was never tuned on (the competition's tasks come from private repositories).

    python tools/swegym.py tasks     --data <dir> [--split lite|full] [--repos moto,mypy]
    python tools/swegym.py snapshots --data <dir> [--tasks ids | --repos names] [--limit N]
    python tools/swegym.py env       --data <dir> --repo moto [--version 4.1] --envs <dir> [--base-python <python>]
    python tools/swegym.py ids       --data <dir> --repo moto [--version 4.1]

tasks: instance_id is '<repository name>_<number>' (getmoto__moto-5752 -> moto_5752), as the competition's
(fastapi_15661); the original id is kept as swe_gym_id. FAIL_TO_PASS and PASS_TO_PASS are fields of the harness's own
Task. Nothing of the agent reads them: judge.py computes FAIL_TO_PASS by running the reference, as for every task.

snapshots: the repository at base_commit (one commit, fetched by its SHA with depth 1: the competition's snapshots hold
the whole history, which no tool of ours reads), as <task>.tgz with .git, like the competition's.

env: the test dependencies of one repository and SWE-bench version in a venv of its own (<envs>/<repo>-<version>), for the subprocess sandbox (it adds the host's
site-packages to the sandbox: the tests import what the Python running local_eval/judge imports). The venv's own
site-packages come first, then the harness's venv (swegemma, ADK, pytest) by a .pth, so a repository's pinned versions
win. Run local_eval.py or judge.py with <envs>/<repo>-<version>/bin/python on that version's tasks (ids). The competition's sandbox is Python 3.13, so the env
is too: a task whose code or dependencies do not run on 3.13 is left out by the screen (local_eval.py --screen N
--screen-only, which keeps a task only when its reference passes and some test fails without it).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

DATASETS = {'lite': 'SWE-Gym/SWE-Gym-Lite', 'full': 'SWE-Gym/SWE-Gym'}
ROWS = 'https://datasets-server.huggingface.co/rows?dataset={ds}&config=default&split=train&offset={off}&length=100'
FIELDS = ('instance_id', 'repo', 'base_commit', 'problem_statement', 'patch', 'test_patch', 'hints_text', 'created_at',
          'FAIL_TO_PASS', 'PASS_TO_PASS', 'environment_setup_commit')

# What each repository's tests need besides the package's own dependencies (its test requirements at the commit are
# read too, when the file exists). Repositories that need a compiler or a large build (pandas, modin, bokeh) are not set
# up: their tasks stay in tasks.jsonl and the screen leaves them out. hydra's grammar is generated with a JVM at install.
TEST_REQUIREMENTS = {
    'moto': ['requirements-tests.txt', 'requirements-dev.txt'],
    'mypy': ['test-requirements.txt'],
    'dvc': [],
    'MONAI': ['requirements-dev.txt'],
    'pydantic': [],
    'dask': [],
    'conan': ['conans/requirements.txt', 'conans/requirements_server.txt', 'conans/requirements_dev.txt'],
    'hydra': ['requirements/dev.txt'],
}
EXTRAS = {
    'moto': '[all,server]',
    'dvc': '[tests]',
    'dask': '[complete,test]',
    'pydantic': '[email]',
}
# packages never installed into a repository's venv: the harness's venv already has them, and an old pin cannot build
# on 3.13 (numpy 1.x) or is several GB (torch). pytest is the repository's own: its plugins of that time need it (dvc's
# pytest-lazy-fixture fails at collection on pytest 9), and the harness does not import it
KEEP_FROM_BASE = {'torch', 'torchvision', 'numpy'}


def short_id(swe_id: str) -> str:
    """getmoto__moto-5752 -> moto_5752; Project-MONAI__MONAI-6034 -> MONAI_6034."""
    m = re.fullmatch(r'.+?__(.+)-(\d+)', swe_id)
    return f'{m.group(1)}_{m.group(2)}' if m else swe_id


def fetch_rows(dataset: str) -> list[dict]:
    rows, off = [], 0
    while True:
        url = ROWS.format(ds=urllib.parse.quote(dataset, safe=''), off=off)
        with urllib.request.urlopen(url, timeout=120) as r:
            page = json.load(r)
        got = [x['row'] for x in page.get('rows', [])]
        rows += got
        off += len(got)
        if not got or off >= page.get('num_rows_total', 0):
            return rows


def cmd_tasks(a) -> None:
    rows = fetch_rows(DATASETS[a.split])
    repos = {r for r in a.repos.split(',') if r}
    a.data.mkdir(parents=True, exist_ok=True)
    (a.data / 'snapshots').mkdir(exist_ok=True)
    (a.data / 'wheels').mkdir(exist_ok=True)  # the subprocess sandbox installs nothing from it
    n = 0
    with open(a.data / 'tasks.jsonl', 'w', encoding='utf-8') as f:
        for r in rows:
            if repos and r['repo'].split('/')[1] not in repos:
                continue
            t = {k: r[k] for k in FIELDS if r.get(k) is not None}
            for k in ('FAIL_TO_PASS', 'PASS_TO_PASS'):
                if isinstance(t.get(k), str):
                    t[k] = json.loads(t[k])
            t['instance_id'] = short_id(r['instance_id'])
            t['swe_gym_id'] = r['instance_id']
            t['version'] = r.get('version', '')
            f.write(json.dumps(t) + '\n')
            n += 1
    print(f'{n} tasks of {DATASETS[a.split]} -> {a.data / "tasks.jsonl"}')


def load(data: Path) -> list[dict]:
    return [json.loads(l) for l in open(data / 'tasks.jsonl', encoding='utf-8') if l.strip()]


def git(*args, cwd=None) -> None:
    subprocess.run(['git', *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def checkout(repo: str, commit: str, dest: Path) -> None:
    """The repository at commit in dest, as a one-commit shallow clone."""
    dest.mkdir(parents=True)
    git('init', '-q', cwd=dest)
    git('fetch', '-q', '--depth', '1', f'https://github.com/{repo}.git', commit, cwd=dest)
    git('checkout', '-q', commit, cwd=dest)


def cmd_snapshots(a) -> None:
    tasks = load(a.data)
    if a.tasks:
        want = set(a.tasks.split(','))
        tasks = [t for t in tasks if t['instance_id'] in want]
    if a.repos:
        repos = set(a.repos.split(','))
        tasks = [t for t in tasks if t['repo'].split('/')[1] in repos]
    tasks = tasks[:a.limit] if a.limit else tasks
    out = a.data / 'snapshots'
    out.mkdir(exist_ok=True)
    for i, t in enumerate(tasks, 1):
        tgz = out / f'{t["instance_id"]}.tgz'
        if tgz.exists():
            continue
        with tempfile.TemporaryDirectory(dir=a.data) as tmp:
            work = Path(tmp) / 'repo'
            try:
                checkout(t['repo'], t['base_commit'], work)
            except subprocess.CalledProcessError as e:
                print(f'{t["instance_id"]}: fetch failed: {e.stderr.decode(errors="replace")[:200]}')
                continue
            part = tgz.with_suffix('.part')
            with tarfile.open(part, 'w:gz') as tar:
                tar.add(work, arcname='.')
            part.rename(tgz)
        print(f'[{i}/{len(tasks)}] {tgz.name} {tgz.stat().st_size // 1024} KB', flush=True)


def _site_packages(python: str) -> str:
    return subprocess.run([python, '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'],
                          check=True, capture_output=True, text=True).stdout.strip()


# pytest plugins of the harness's venv that the harness's own pytest command does not use, while it disables anyio's
PLUGIN_GROUP = 'pytest11'


def _harness_view(base_python: str, venv: Path) -> Path:
    """The harness's site-packages as a directory of links, without the metadata that registers its pytest plugins
    (inline-snapshot, pytest-httpbin): pytest autoloads every registered plugin it can find, and the repository's own
    older pytest fails on plugins written for pytest 9 ('No module named _pytest._io.pprint'). The modules stay
    importable; anyio's metadata stays (its plugin is disabled by the harness's '-p no:anyio')."""
    base = Path(_site_packages(base_python))
    view = venv / 'harness_site'
    view.mkdir()
    for entry in base.iterdir():
        if entry.name.endswith('.dist-info') and not entry.name.startswith('anyio-'):
            ep = entry / 'entry_points.txt'
            if ep.exists() and f'[{PLUGIN_GROUP}]' in ep.read_text(encoding='utf-8', errors='replace'):
                continue
        (view / entry.name).symlink_to(entry)
    return view


def _install(py: str, targets: list[str], reqs: list[str], env: dict) -> subprocess.CompletedProcess:
    """uv pip install of targets and the test requirements; a test requirement uv names as unsatisfiable (a package
    gone from PyPI, as MONAI's types-pkg-resources, or one with no release for 3.13) is dropped and the rest retried."""
    reqs = list(reqs)
    while True:
        r = subprocess.run(['uv', 'pip', 'install', '-q', '-p', py, *targets, *reqs], capture_output=True, text=True,
                           env=env)
        err = ' '.join(r.stderr.split()).lower()
        bad = [q for q in reqs if r.returncode and re.search(
            r'(?:require|of|for|build) `?' + re.escape(_name(q)) + r'\b', err)]
        if not bad:
            return r
        print(f'  dropped the requirement(s) uv cannot satisfy: {", ".join(bad)}')
        reqs = [q for q in reqs if q not in bad]


def _name(req: str) -> str:
    return re.split(r'[<>=!~\[; ]', req, maxsplit=1)[0].lower()


def _freeze(python: str) -> str:
    return subprocess.run(['uv', 'pip', 'freeze', '-p', python], check=True, capture_output=True, text=True).stdout


def _requirements(path: Path, seen: set | None = None) -> list[str]:
    """The requirement lines of a requirements file and the files it includes (-r), without the ones the harness's
    venv provides, other options, or the package itself ('-e .')."""
    seen = set() if seen is None else seen
    if path in seen or not path.exists():
        return []
    seen.add(path)
    out = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.split('#', 1)[0].strip()
        m = re.match(r'-r\s*(\S+)$', line)
        if m:
            out += _requirements(path.parent / m.group(1), seen)
            continue
        if not line or line.startswith(('-', '.')):
            continue
        name = re.split(r'[<>=!~\[; ]', line, maxsplit=1)[0].lower()
        if name and name not in KEEP_FROM_BASE:
            out.append(line)
    return out


def _of(data: Path, repo: str, version: str = '') -> list[dict]:
    """The tasks of one repository, of one SWE-bench version when given (the newest version when version is 'newest')."""
    tasks = [t for t in load(data) if t['repo'].split('/')[1] == repo]
    if version == 'newest' and tasks:
        version = max(tasks, key=lambda t: t.get('created_at', ''))['version']
    return [t for t in tasks if not version or t.get('version') == version]


def cmd_ids(a) -> None:
    print(','.join(t['instance_id'] for t in _of(a.data, a.repo, a.version)))


def _install_package(a, version: str, py: str, src: Path, old: Path, reqs: list[str], env: dict):
    """The package with its own dependencies (not editable: the tests import the task's workspace, eval_env.py, but
    some code reads its installed version), then the test requirements. uv's cache hardlinks the files into each
    venv, so the versions of a repository share the disk."""
    extras = EXTRAS.get(a.repo, '')
    if old.exists():
        # the oldest commit's package first: a runtime dependency its code has and the newest dropped (moto 4.0's
        # older code imports pytz) stays, as the newest install below replaces only the package itself
        _install(py, [f'{old}{extras}'], [], env)
    r = _install(py, [f'{src}{extras}'], reqs, env)
    if r.returncode:
        # the checkout has no tags, so a version read from git is '0+untagged' and a pin on it fails (dask[complete]
        # needs distributed of dask's own version): the release of this version from PyPI brings the same
        # dependencies; the tests import the workspace's code anyway (eval_env.py)
        release = f'{a.repo.lower()}{extras}=={version}.*'
        print(f'{a.repo} {version}: the checkout did not install, trying the release {release}')
        r = _install(py, [release], reqs, env)
    if r.returncode and reqs:
        # no release installs on 3.13 either (mypy's own build): the test requirements alone, which list the runtime
        # dependencies too in these repositories
        print(f'{a.repo} {version}: installing its test requirements only')
        r = _install(py, [], reqs, env)
    return r


def cmd_env(a) -> None:
    tasks = _of(a.data, a.repo, a.version)
    if not tasks:
        sys.exit(f'no task of {a.repo} {a.version} in {a.data / "tasks.jsonl"}')
    version = tasks[0]['version']
    if a.repo not in TEST_REQUIREMENTS:
        sys.exit(f'{a.repo}: not set up by this tool (needs a compiler, a large build or a JVM)')
    base_python = a.base_python or sys.executable
    # one venv per repository and version, as SWE-bench's harness builds one image per version: the dependencies change
    # between versions (moto 4.0's tests import 'sure', 5.0's do not). The version's newest task gives the commit: its
    # dependencies are the closest to what Python 3.13 can install
    t = max(tasks, key=lambda t: t.get('created_at', ''))
    venv = a.envs / f'{a.repo}-{version}'
    if venv.exists():
        shutil.rmtree(venv)
    subprocess.run(['uv', 'venv', '-q', '-p', 'python3.13', str(venv)], check=True)
    py = str(venv / 'bin' / 'python')
    before = _freeze(base_python)
    # the test requirements of the version's oldest task too: a test helper can drop a dependency within a version
    # (moto 4.1's older tests import 'sure', its newest do not); the newest commit's line wins for a package in both
    oldest = min(tasks, key=lambda t: t.get('created_at', ''))
    with tempfile.TemporaryDirectory(dir=a.envs) as tmp:
        src = Path(tmp) / 'repo'
        checkout(t['repo'], t['base_commit'], src)
        reqs = {}
        for commit_src in ([src, Path(tmp) / 'old'] if oldest is not t else [src]):
            if commit_src != src:
                checkout(oldest['repo'], oldest['base_commit'], commit_src)
            for name in TEST_REQUIREMENTS[a.repo]:
                if (commit_src / name).exists():
                    for line in _requirements(commit_src / name):
                        reqs.setdefault(re.split(r'[<>=!~\[; ]', line, maxsplit=1)[0].lower(), line)
        reqs = list(reqs.values())
        # a Rust extension pinned by an older version (pydantic-core 2.18) predates PyO3's support of 3.13: build it
        # against the stable ABI, as PyO3 allows
        base_env = dict(os.environ, PYO3_USE_ABI3_FORWARD_COMPATIBILITY='1')
        # packages of today: the packages of the version's time (uv --exclude-newer) do not run on 3.13 (old build
        # backends, pyyaml 6.0's build, and at run time 'typing.TypeVar is not an acceptable base type'), tried 10-21
        r = _install_package(a, version, py, src, Path(tmp) / 'old', reqs, base_env)
        if r.returncode:
            print(r.stderr[-3000:])
            sys.exit(f'{a.repo} {version}: install failed')
    # drop what the harness's venv already provides, so its copy is the one imported
    dup = [l.split('==')[0] for l in _freeze(py).splitlines() if l.split('==')[0].lower() in KEEP_FROM_BASE]
    if dup:
        subprocess.run(['uv', 'pip', 'uninstall', '-q', '-p', py, *dup], check=True)
    # the harness's venv after this venv's own packages; written last: with it in place, uv sees the harness's
    # packages as this venv's and upgrades or uninstalls them there (it removed the harness's pytest once)
    # relative to site-packages, so the venv can be moved
    site = Path(_site_packages(py))
    view = os.path.relpath(_harness_view(base_python, venv), site)
    (site / 'zz_harness.pth').write_text(view + '\n', encoding='utf-8')
    if _freeze(base_python) != before:
        sys.exit(f"the harness's venv changed while {a.repo}'s was set up: check it with uv pip check -p {base_python}")
    print(f'{a.repo} {version}: {venv} (from {t["instance_id"]}, {t["base_commit"][:10]}); {len(tasks)} tasks: '
          f'{",".join(x["instance_id"] for x in tasks)}; run local_eval.py or judge.py with {py}')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('tasks')
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--split', choices=sorted(DATASETS), default='lite')
    p.add_argument('--repos', default='', help='repository names (moto,mypy); default all')
    p = sub.add_parser('snapshots')
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--tasks', default='')
    p.add_argument('--repos', default='')
    p.add_argument('--limit', type=int, default=0)
    p = sub.add_parser('env')
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--version', default='newest', help="a SWE-bench version of the repository (default: the newest)")
    p.add_argument('--envs', type=Path, required=True)
    p.add_argument('--base-python', default='', help="the harness's venv python (default: this one)")
    p = sub.add_parser('ids', help='the task ids of a repository (and version), for --tasks')
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--version', default='')
    a = ap.parse_args()
    {'tasks': cmd_tasks, 'snapshots': cmd_snapshots, 'env': cmd_env, 'ids': cmd_ids}[a.cmd](a)


if __name__ == '__main__':
    main()
