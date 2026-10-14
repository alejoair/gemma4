"""judge.py: the verdict of a patch on its task's hidden tests, independent of the environment's own failures.

The harness's verdict (swegemma verify_task) is "the test files of the test patch all pass". In the dev environment
some of those files fail on the original code too (V21: requests' TLS/network tests, rich's pygments-dependent ANSI
output), so a correct patch was reported "not resolved" and a task could not be scored at all (2 of 6 in V21). The
judge runs the same verification three times in the same sandbox setup, the way SWE-bench defines its tests:

  - with the reference patch:  the tests that pass there are the ones a fix can be asked to pass;
  - with no patch:             FAIL_TO_PASS = failing here and passing with the reference; the rest is PASS_TO_PASS;
  - with the agent's patch:    resolved = no test fails that passes with the reference.

A task whose reference run does not finish here is "unscorable", reported as such instead of as a failure. When the
reference fixes no test here (FAIL_TO_PASS empty: the issue's tests pass on the original code too), the patch is still
judged on the tests that pass with the reference, with a note. The reference and no-patch runs are cached per
task (JSON in the cache directory), so the local runs and the Kaggle runs pay for them once per environment.

Used by tools/local_eval.py and tools/kaggle_eval.py after each task, and on its own for patches already made:

  python tools/judge.py --data <competition data dir> --patches <dir with patch_<task>.diff> [--tasks a,b]
      [--cache <dir>] [--work <dir>]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

FAILED_LINE = re.compile(r'^(?:FAILED|ERROR) (\S+?)(?: - .*)?$')


def failures(test_output: str) -> set[str]:
    """The test ids (or files, for a collection error) pytest's short summary names as FAILED or ERROR."""
    return {m.group(1) for m in (FAILED_LINE.match(l.strip()) for l in (test_output or '').splitlines()) if m}


def finished(exit_code) -> bool:
    """pytest ran the tests to the end: 0 all passed, 1 some failed (2 interrupted, 5 none collected, -1 setup)."""
    return exit_code in (0, 1)


async def _verify(evaluator, task, patch: str):
    from swegemma.deduplication import resolve_task_snapshot_paths
    from swegemma.harness.verification import verify_task

    snap, base, ppath = resolve_task_snapshot_paths(evaluator.config.snapshots_dir, task.instance_id, task.repo)
    r = await verify_task(evaluator.docker, evaluator.config, task, snap, base_snapshot_path=base, patch_path=ppath,
                          agent_patch=patch, start_time=0.0)
    return {'exit': r.test_exit_code, 'failed': sorted(failures(r.test_output)), 'output': r.test_output or ''}


def _baseline(evaluator, task, cache: Path | None):
    """The reference and no-patch runs of the task, from the cache when present."""
    path = cache / f'{task.instance_id}.json' if cache else None
    if path is not None and path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    ref = asyncio.run(_verify(evaluator, task, task.patch))
    orig = asyncio.run(_verify(evaluator, task, ''))
    base = {'ref': {k: ref[k] for k in ('exit', 'failed')}, 'orig': {k: orig[k] for k in ('exit', 'failed')}}
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(base, indent=1), encoding='utf-8')
    return base


def judge(evaluator, task, agent_patch: str, harness_result=None, cache: Path | None = None) -> dict:
    """{'verdict': 'resolved' | 'not resolved' | 'unscorable', 'f2p': [...], 'f2p_failing': [...],
    'p2p_broken': [...], 'env_failures': [...], ...}. harness_result (the TaskResult of the run) saves the agent's
    verification run when given."""
    base = _baseline(evaluator, task, cache)
    ref_failed, orig_failed = set(base['ref']['failed']), set(base['orig']['failed'])
    if harness_result is not None:
        agent = {'exit': harness_result.test_exit_code, 'failed': failures(harness_result.test_output)}
    else:
        a = asyncio.run(_verify(evaluator, task, agent_patch))
        agent = {'exit': a['exit'], 'failed': set(a['failed'])}
    agent_failed = set(agent['failed'])
    # a collection error names the file: the no-patch run of a new test file fails as 'tests/x.py' (whole file)
    f2p = sorted(orig_failed - ref_failed)
    out = {'f2p': f2p, 'env_failures': sorted(ref_failed), 'ref_exit': base['ref']['exit'],
           'orig_exit': base['orig']['exit'], 'agent_exit': agent['exit']}
    if not finished(base['ref']['exit']):
        out['verdict'] = 'unscorable'
        out['why'] = 'the reference run did not finish'
        return out
    if not f2p:
        # fastapi_14851: the issue's tests already pass on the original code here (the change prepares for a later
        # Starlette), so nothing turns from failing to passing; a patch is still judged by the tests that pass with
        # the reference, as the harness would (it broke two of them)
        out['why'] = 'no FAIL_TO_PASS test in this environment: judged on the tests that pass with the reference'
    broken = sorted(agent_failed - ref_failed)
    out['f2p_failing'] = [t for t in f2p if t in agent_failed or any(t.startswith(f + '::') or t == f
                                                                     for f in agent_failed)]
    out['p2p_broken'] = [t for t in broken if t not in f2p]
    out['verdict'] = 'resolved' if finished(agent['exit']) and not broken else 'not resolved'
    if not finished(agent['exit']) and not out.get('why'):
        out['why'] = f'the tests did not run to the end (pytest exit {agent["exit"]})'
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', type=Path, required=True, help='tasks.jsonl, snapshots/, wheels/')
    ap.add_argument('--patches', type=Path, required=True, help='a directory of patch_<task>.diff')
    ap.add_argument('--tasks', default='', help='comma-separated ids (default: every patch in --patches)')
    ap.add_argument('--cache', type=Path, default=None, help='reference/no-patch results per task')
    ap.add_argument('--work', type=Path, default=Path('judge_work'))
    args = ap.parse_args()

    import os
    import sys
    os.environ.setdefault('OTEL_SDK_DISABLED', 'true')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_env
    eval_env.patch()
    from adk_submission.registry import ModelRegistry
    from swegemma.config import EvalConfig, build_submission_limits
    from swegemma.evaluate import Evaluator
    from swegemma.models import load_tasks

    ids = [x.strip() for x in args.tasks.split(',') if x.strip()] or sorted(
        p.stem[len('patch_'):] for p in args.patches.glob('patch_*.diff'))
    tasks = [t for t in load_tasks(args.data / 'tasks.jsonl') if t.instance_id in ids]
    limits, constraints = build_submission_limits()
    args.work.mkdir(parents=True, exist_ok=True)
    (args.work / 'agent').mkdir(exist_ok=True)
    (args.work / 'agent' / 'agent.yaml').write_text('name: judge\n', encoding='utf-8')
    config = EvalConfig(tasks_path=args.data / 'tasks.jsonl', snapshots_dir=args.data / 'snapshots',
                        results_dir=args.work / 'results', submission_dir=args.work / 'agent', models=ModelRegistry(),
                        sandbox='subprocess', timeout_seconds=600, limits=limits, generation_constraints=constraints,
                        wheels_dir=args.data / 'wheels')
    evaluator = Evaluator(config)
    rows = []
    for t in tasks:
        patch = (args.patches / f'patch_{t.instance_id}.diff').read_text(encoding='utf-8')
        v = judge(evaluator, t, patch, cache=args.cache)
        rows.append((t.instance_id, v))
        print(f'{t.instance_id}: {v["verdict"]}  f2p={len(v["f2p"])} failing={v.get("f2p_failing", [])} '
              f'p2p_broken={v.get("p2p_broken", [])} env_failures={len(v["env_failures"])} {v.get("why", "")}',
              flush=True)
    n = sum(v['verdict'] == 'resolved' for _, v in rows)
    k = sum(v['verdict'] != 'unscorable' for _, v in rows)
    print(f'JUDGED: {n}/{k} resolved ({len(rows) - k} unscorable here)')


if __name__ == '__main__':
    main()
