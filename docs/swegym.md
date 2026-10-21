# SWE-Gym as a second task set (10-21)

The user's request: install SWE-Gym and add it as a dependency of this project.

**What SWE-Gym is.** SWE-Gym is not a Python package: nothing named swegym or swe-gym is on PyPI. Its GitHub repository (SWE-Gym/SWE-Gym) holds training and serving scripts for OpenHands and MoatlessTools. The usable part is the data on Hugging Face:
- `SWE-Gym/SWE-Gym`: 2,438 tasks;
- `SWE-Gym/SWE-Gym-Lite`: 230 tasks.

The tasks come from 11 repositories, in SWE-bench's format: `instance_id`, `repo`, `base_commit`, `problem_statement`, `patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, `version`. The paper and what applies to us are in `docs/literature.md` ("SWE-Gym").

**Why it is useful here.** The competition scores private repositories the agent has never seen, while our 129 dev tasks come from four repositories we have tuned against (fastapi, rich, requests, httpx). SWE-Gym gives tasks from other repositories, judged the same way as ours. A change that helps only on our four repositories shows up there (`docs/score_gap.md`).

## How it is wired: `tools/swegym.py`

It is a data dependency, kept in the competition's own layout, so `tools/local_eval.py` and `tools/judge.py` read it unchanged (`--data <dir>`):

```bash
python tools/swegym.py tasks     --data <dir> [--split lite|full] [--repos moto,mypy]
python tools/swegym.py snapshots --data <dir> [--tasks ids | --repos names] [--limit N]
python tools/swegym.py env       --data <dir> --repo moto [--version 4.1] --envs <dir>
python tools/swegym.py ids       --data <dir> --repo moto [--version 4.1]
```

**`tasks`** writes `<dir>/tasks.jsonl` from the Hugging Face datasets server.
- Ids follow the competition's style (`getmoto__moto-5752` becomes `moto_5752`). The original id is kept as `swe_gym_id`.
- `FAIL_TO_PASS` and `PASS_TO_PASS` are fields of the harness's own `Task`. No part of the agent reads them. `judge.py` computes FAIL_TO_PASS by running the reference, as it does for every task.

**`snapshots`** writes `<dir>/snapshots/<task>.tgz`.
- Each one is the repository at `base_commit`, fetched by its SHA with depth 1: 0.4-30 MB, about 2 s each.
- The competition's snapshots hold the whole history, which no tool of ours reads.

**`env`** builds one venv per repository and SWE-bench version (`<envs>/<repo>-<version>`), as SWE-bench builds one image per version. Dependencies change between versions: moto 4.0's older tests import `sure`, its newest do not.

What goes into the venv, in order:
1. The package at the version's oldest task. A runtime dependency the newest code dropped stays this way (moto 4.0's older code imports `pytz`).
2. The package at the newest task, with its extras.
3. The test requirements files of both commits, including the files they pull in with `-r`. A requirement uv names as unsatisfiable is dropped (MONAI's `types-pkg-resources`, gone from PyPI).

When an install fails, the venv falls back in turn:
- **The PyPI release of the version** (`dask[complete,test]==2024.2.*`). The checkout has no tags, so a version read from git is `0+untagged` and breaks pins.
- **The test requirements alone** (mypy does not build on 3.13).
- **Old Rust extensions** are built with `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1`.

**Packages of the version's date were tried and dropped.** With `uv --exclude-newer`:
- old build backends (setuptools before 66) and pyyaml 6.0 do not build on 3.13;
- with those exempted, an old package fails at run time ("typing.TypeVar is not an acceptable base type").

So the venvs take today's packages. That means a newer dependency can change what a test checks: botocore 1.36's S3 checksums against moto 4.0's multipart tests. The screen drops such tasks.

**How the harness is seen.**
- **pytest is the repository's own.** Its plugins of that time need it: dvc's pytest-lazy-fixture fails at collection on pytest 9.
- **numpy, torch and torchvision are the harness's.** Old numpy does not build on 3.13, and torch is several GB.
- **The harness's site-packages comes after the venv's own**, by a relative `.pth`, as a directory of links named `harness-site-packages`. The name matters: swegemma's subprocess sandbox (`_host_env.pth`) keeps only host paths that contain `site-packages`.
- **The view leaves out the metadata that registers the harness's pytest plugins.** Without it, the repository's older pytest is not handed plugins written for pytest 9.
- **The sandbox's venv also sees the system's site-packages** (`/usr/local/lib/python3.13/dist-packages`, with pytest-httpbin and inline-snapshot). pytest loads every plugin registered there.
  - A system plugin that does not import next to the venv's packages gets a no-op stand-in of the same entry-point name in the venv (httpbin with moto's Flask 2.1: "'Flask' object has no attribute 'json'").
  - pluggy keeps the first plugin of each name in `sys.path` order, so the stand-in keeps the system's plugin out.

**Safety.** The `.pth` is written after the install. With it in place during the install, uv counted the harness's packages as the new venv's and upgraded or removed them there: on 10-21 it removed the harness's pytest, which was restored from the competition's wheel (9.1.1, the judge cache's fingerprint). `env` now stops if the harness's venv changed.

**Disk.** uv's cache hardlinks files, so the versions of a repository share them (moto 5.0 added 81 MB to moto 4.1's 293 MB). `uv cache clean` frees what no venv uses.

The subprocess sandbox imports what the Python that runs it imports. So a version's tasks are run with that version's venv's Python:

```bash
E=<envs>/moto-4.1/bin/python
$E tools/judge.py --data <dir> --patches <dir of patch_<task>.diff> --cache <dir>/judge_cache --work <dir>
$E tools/local_eval.py --submission <build> --data <dir> --tasks $(python tools/swegym.py ids --data <dir> --repo moto --version 4.1) --out <dir> ...
```

**Python 3.13 only.** The competition's sandbox is Python 3.13, so the venvs are too. The judge's screen keeps a task only when its reference passes the harness's whole-file check and some test fails without it.

## Which tasks can be scored here (Lite, 10-21)

Every repository of Lite was tried, version by version, except pandas, modin and bokeh, which need a compiler or a large build. Each task was judged with the empty patch (`tools/judge.py`): the reference twice, no patch twice.

**39 tasks can be scored here: moto 35, conan 4.**

| Repository | Lite tasks | Scorable | Why the rest are not |
|---|---|---|---|
| moto | 59 | 35 | 4.1: 19/25, 4.0: 7/12, 5.0: 6/9, 3.1: 3/7. The rest have a test in the file that fails with the reference too (Lambda and Batch need Docker, as rich's pygments tests need their version), or did not install (3.0) |
| conan | 12 | 4 | 2.1: 4/4. 2.0 does not install; 1.x: 0/4 |
| mypy | 40 | 0 | Its tests are data files (`test-data/unit/check-*.test`) run by `mypy/test/testcheck.py`. The harness runs the test patch's own files with pytest, so a `.test` file is never run |
| pydantic | 20 | 0 | pydantic-core 2.16-2.18 does not build on 3.13 (`ForwardRef._evaluate` in its build) |
| dvc | 36 | 0 | Its own test helpers use `pathlib`'s `_from_parts`, removed in 3.12 |
| dask | 14 | 0 | Every version fails on 3.13 with today's pandas and numpy (`inspect`: "descriptor '__call__' for 'type' objects doesn't apply to a 'property' object") |
| MONAI | 27 | 0 | Its development requirements fill several GB: the install brought the disk to 688 MB free, and it was stopped |
| hydra | 11 | 0 | 1.0 and 1.1 do not install. 1.2 and 1.3 bring an old isort whose entry point Python 3.13 refuses ("Invalid object reference") |
| pandas, modin, bokeh | 7 | 0 | Not set up |

The scorable tasks, by venv:
- conan-2.1: conan_13403, conan_14177, conan_14296, conan_15422
- moto-3.1: moto_4990, moto_5020, moto_5321
- moto-4.0: moto_5406, moto_5478, moto_5502, moto_5620, moto_5699, moto_5737, moto_5752
- moto-4.1: moto_5835, moto_5865, moto_5885, moto_5899, moto_6022, moto_6041, moto_6114, moto_6178, moto_6185, moto_6212, moto_6226, moto_6317, moto_6355, moto_6387, moto_6408, moto_6469, moto_6470, moto_6508, moto_6701
- moto-5.0: moto_7335, moto_7348, moto_7361, moto_7495, moto_7584, moto_7607

**End to end.** The fast build (S10's files) ran with Haiku on `moto_5865`, using the moto-4.1 venv's Python. It made 12 tool calls and 20 model calls in 168 s, and wrote a 573-character patch. The judge found it not resolved (the reference passes here). The whole path works: tasks, snapshot, venv, agent, harness, judge.

**The set is narrow:** 35 of the 39 tasks are moto. That is one large repository (AWS service mocks, very long files) against the private set's unknown mix. A change that helps here and on our four repositories is more likely generic than one that helps on our four alone.

**Next sources, if a wider set is wanted:**
- SWE-Gym full (2,438 tasks, the same 11 repositories, `--split full`): more moto and conan, and more versions of the others.
- Tasks from repositories whose current versions support 3.13.
