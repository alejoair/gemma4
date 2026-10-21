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

**`env`** builds one venv per repository and SWE-bench version (`<envs>/<repo>-<version>`), as SWE-bench builds one image per version.
- Dependencies change between versions. For example, moto 4.1's older tests import `sure` and its newest ones do not.
- The venv gets the package (from the version's newest task, with its extras) and the test requirements files of the version's newest and oldest tasks.
- Its own site-packages come first. The harness's venv (swegemma, ADK, pytest, numpy, torch) follows through a `.pth`, written after the install.
  - With the `.pth` in place during the install, uv counted the harness's packages as the new venv's and upgraded or removed them there: it removed the harness's pytest once.
  - The command now checks that the harness's venv is unchanged after the install.
- uv's cache hardlinks files, so the versions of a repository share disk (moto 5.0 added 81 MB to moto 4.1's 293 MB).

The subprocess sandbox imports what the Python that runs it imports. So a version's tasks are run with its venv's Python:

```bash
E=<envs>/moto-4.1/bin/python
$E tools/judge.py --data <dir> --patches <dir of patch_<task>.diff> --cache <dir>/judge_cache --work <dir>
$E tools/local_eval.py --submission <build> --data <dir> --tasks $(python tools/swegym.py ids --data <dir> --repo moto --version 4.1) --out <dir> ...
```

**Python 3.13 only.** The competition's sandbox is Python 3.13, so the venvs are too. A task whose code or pinned dependencies do not run on 3.13 is left out by the judge's screen: it is kept only when its reference passes the harness's whole-file check and some test fails without it.

**Repositories not set up:**
- pandas, modin, bokeh: they need a compiler or a large build.
- hydra: its grammar is generated with a JVM.

Their tasks stay in `tasks.jsonl` and the screen leaves them out.

## Which tasks can be scored here
