"""Run the submission on development tasks with the local model behind the gateway (llm.rayflow.dev).

The same harness as tools/kaggle_eval.py (swegemma Evaluator, subprocess sandbox, the same compaction and budgets),
with the model served by the gateway instead of a vLLM server started here.

  python tools/local_eval.py --submission <dir> --data <competition data dir> --tasks rich_3006 --out <dir>
      [--model gemma-26b] [--api-base https://llm.rayflow.dev/v1]

<data> holds tasks.jsonl, snapshots/<task>.tgz and wheels/ (tools/fetch_snapshots.sh, kaggle competitions download).
Each task's patch goes to <out>/patch_<task>.diff, the harness logs and traces to <out>/results/.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import shutil
import time
from pathlib import Path

COMPACTION_THRESHOLD = 14336
DECLARED = 'gemma-4-31b-it-qat-w4a16-ct'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--submission', type=Path, required=True)
    ap.add_argument('--data', type=Path, required=True)
    ap.add_argument('--tasks', required=True, help='comma-separated instance ids')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--model', default='gemma-26b', help='the model name the gateway serves')
    ap.add_argument('--api-base', default='https://llm.rayflow.dev/v1')
    args = ap.parse_args()

    os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
    os.environ.setdefault('OTEL_SDK_DISABLED', 'true')
    import litellm
    import yaml
    from adk_submission.registry import ModelRegistry
    from adk_submission.resolvers.generation import install_litellm_reasoning_patch
    from google.adk.agents.context_cache_config import ContextCacheConfig
    from google.adk.apps._configs import EventsCompactionConfig
    from google.adk.models.lite_llm import LiteLlm
    from swegemma.config import EvalConfig, build_submission_limits
    from swegemma.evaluate import Evaluator
    from swegemma.models import load_tasks

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_env
    import judge
    eval_env.patch()            # the tests import the task's src/ code (see eval_env.py for what is known of the scorer)
    judge.install_capture()     # per-test outcomes from the JUnit report of every verify_task run
    litellm.drop_params = True
    install_litellm_reasoning_patch()
    dump = os.environ.get('SWE_DUMP_REQUESTS')
    if dump:
        # every request as sent to the model (messages and tools), one JSON line each: what the model really reads
        import json
        from litellm.integrations.custom_logger import CustomLogger

        class _Dump(CustomLogger):
            def log_pre_api_call(self, model, messages, kwargs):
                extra = kwargs.get('optional_params') or {}
                with open(dump, 'a', encoding='utf-8') as fh:
                    fh.write(json.dumps({'messages': messages, 'tools': extra.get('tools')}, default=str) + '\n')

        litellm.callbacks = [_Dump()]
    args.out.mkdir(parents=True, exist_ok=True)
    agent_dir = args.out / 'agent'
    if agent_dir.exists():
        shutil.rmtree(agent_dir)
    shutil.copytree(args.submission, agent_dir)

    models = ModelRegistry()
    for alias in (DECLARED, args.model):
        models.register(alias, LiteLlm(model=f'openai/{args.model}', api_base=args.api_base, api_key='EMPTY'))

    cfg_y = yaml.safe_load((agent_dir / 'eval_config.yaml').read_text(encoding='utf-8'))
    ev = cfg_y.get('evaluation', cfg_y)
    limits, constraints = build_submission_limits()
    tasks_path = args.data / 'tasks.jsonl'
    wanted = [x.strip() for x in args.tasks.split(',') if x.strip()]
    tasks = [t for t in load_tasks(tasks_path) if t.instance_id in wanted]
    assert len(tasks) == len(wanted), f'missing tasks: {set(wanted) - {t.instance_id for t in tasks}}'
    results_dir = args.out / 'results'
    config = EvalConfig(
        tasks_path=tasks_path, snapshots_dir=args.data / 'snapshots', results_dir=results_dir,
        submission_dir=agent_dir, models=models, sandbox='subprocess',
        timeout_seconds=int(ev.get('timeout_seconds', 300)), max_time_minutes=float(ev.get('max_time_minutes', 60.0)),
        max_tool_calls=int(ev.get('max_tool_calls', 100)),
        max_turns=int(ev['max_turns']) if ev.get('max_turns') is not None else None,
        limits=limits, generation_constraints=constraints,
        context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
        events_compaction_config=EventsCompactionConfig(compaction_interval=15, overlap_size=2,
                                                        token_threshold=COMPACTION_THRESHOLD, event_retention_size=5),
        wheels_dir=args.data / 'wheels', verbose=True,
    )
    evaluator = Evaluator(config)
    fields = ['task_id', 'resolved', 'judged', 'official_feasible', 'f2p', 'f2p_failing', 'p2p_broken',
              'required_failing', 'flaky', 'judge_note', 'test_exit_code', 'patch_chars', 'tool_calls', 'llm_calls',
              'duration_s', 'error']
    rows = []
    judge_cache = args.data / 'judge_cache'
    with open(args.out / 'run.csv', 'w', newline='') as fh:
        wr = csv.DictWriter(fh, fields)
        wr.writeheader()
        for i, task in enumerate(tasks, 1):
            print(f'== TASK {task.instance_id} {time.strftime("%H:%M:%S", time.gmtime())} UTC', flush=True)
            mark = judge.capture_mark()
            r = asyncio.run(evaluator.evaluate_task(task=task, task_index=i, total_tasks=len(tasks)))
            row = dict(task_id=task.instance_id, resolved=int(bool(r.resolved)), test_exit_code=r.test_exit_code,
                       patch_chars=len(r.agent_patch or ''), tool_calls=r.tool_calls, llm_calls=r.total_llm_calls,
                       duration_s=round(r.duration_seconds, 1), error=(r.error_message or '')[:300])
            (args.out / f'patch_{task.instance_id}.diff').write_text(r.agent_patch or '', encoding='utf-8')
            (args.out / f'tests_{task.instance_id}.txt').write_text(r.test_output or '', encoding='utf-8')
            try:
                # the harness's verdict needs every test of the test files to pass, also those the environment fails
                # on the original code: the judge compares with the reference patch (tools/judge.py)
                v = judge.judge(evaluator, task, r.agent_patch or '', harness_result=r, cache=judge_cache,
                                capture_mark=mark)
                row.update(judged=v['verdict'], official_feasible=int(bool(v['official_feasible'])),
                           f2p=len(v['f2p']), f2p_failing=' '.join(v.get('f2p_failing', [])),
                           p2p_broken=' '.join(v.get('p2p_broken', [])),
                           required_failing=' '.join(v.get('required_failing', [])), flaky=len(v.get('flaky', [])),
                           judge_note=v.get('why', '')[:300])
                print(f'== JUDGED {task.instance_id}: {judge.short(v)}', flush=True)
            except Exception as e:  # noqa: BLE001
                row.update(judged=f'judge error: {type(e).__name__}: {e}'[:200])
            rows.append(row)
            wr.writerow(row)
            fh.flush()
            print(f'== RESULT {row}', flush=True)
    judged = [r.get('judged', '') for r in rows]
    print(f'== SUMMARY harness resolved {sum(r["resolved"] for r in rows)}/{len(rows)}; '
          f'JUDGED (against the reference) {judged.count("resolved")} resolved of '
          f'{sum(1 for j in judged if j in ("resolved", "not resolved"))} scorable, {judged.count("unscorable")} '
          f'unscorable; OFFICIAL_FEASIBLE (the reference passes the harness here) '
          f'{sum(1 for r in rows if r.get("official_feasible") == 1)}/{len(rows)}', flush=True)


if __name__ == '__main__':
    main()
