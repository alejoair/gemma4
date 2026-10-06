"""Run one submission on a fixed sample of the 129 development tasks, with the real model.

Meant for a Kaggle notebook with the competition's GPU (4 x L4 is ideal, 1 x GPU works for a smoke run).
It mirrors the official "Getting Started" notebook: wheelhouse install, vLLM server with the
competition model, then swegemma's Evaluator (phase 1 agent + phase 2 verification) per task.

Setup in the notebook
  1. Add inputs: the competition, the dataset metric/gemma-4-developer-agent-wheelhouse, the Gemma 4
     model, and a dataset holding this repo's `submission/` and `bench/` folders.
  2. Run:  python bench/kaggle_eval.py --submission <path>/submission --n 20 --out /kaggle/working/run1.csv
     Re-run after each change with the same --n and --seed so the task sample is paired.
  3. Compare two runs:  python bench/compare.py before.csv after.csv

Not tested here: it needs a GPU and vLLM, which the authoring environment does not have.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import csv
import glob
import importlib
import os
import random
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

WHEELHOUSE_DIR = Path('/kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse')
DATA_DIR = Path('/kaggle/input/competitions/gemma-4-developer-agent')
MODEL_PATH = Path('/kaggle/input/models/google/gemma-4/other/gemma-4-31b-it-qat-w4a16-ct/2')
MODEL = 'gemma-4-31b-it-qat-w4a16-ct'

ENV = {
    'LITELLM_LOCAL_MODEL_COST_MAP': 'True', 'TRANSFORMERS_NO_TF': '1', 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn',
    'VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS': '1', 'VLLM_ENGINE_READY_TIMEOUT_S': '1200',
    'VLLM_NO_USAGE_STATS': '1', 'OTEL_SDK_DISABLED': 'true', 'PYTORCH_CUDA_ALLOC_CONF': 'expandable_segments:True',
}


def install_wheels() -> None:
    os.environ.update(ENV)
    for pat in ('/usr/local/lib/python*/dist-packages/*cutlass*.pth', '/usr/local/lib/python*/site-packages/*cutlass*.pth'):
        for pth in glob.glob(pat):
            try:
                os.unlink(pth)
            except OSError:
                pass
    tmp = Path('/tmp/wheelhouse')
    tmp.mkdir(parents=True, exist_ok=True)
    for w in WHEELHOUSE_DIR.glob('*.whl'):
        if 'cutlass' in w.name.lower():
            continue
        name = w.name.replace('cu128', '+cu128') if ('cu128' in w.name and '+' not in w.name) else w.name
        if not (tmp / name).exists():
            os.symlink(w, tmp / name)
    wheels = sorted(str(w) for w in tmp.glob('*.whl'))
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--no-deps', '--force-reinstall', *wheels], check=True)
    importlib.invalidate_caches()


def pick_tasks(tasks, n: int, seed: int):
    """Stratified by repo, deterministic: identical for every variant given the same n and seed."""
    by_repo = defaultdict(list)
    for t in sorted(tasks, key=lambda t: t.instance_id):
        by_repo[t.repo].append(t)
    rng = random.Random(seed)
    total = sum(len(v) for v in by_repo.values())
    chosen = []
    for repo, ts in sorted(by_repo.items()):
        k = max(1, round(n * len(ts) / total))
        chosen += rng.sample(ts, min(k, len(ts)))
    return sorted(chosen, key=lambda t: t.instance_id)[:n] if len(chosen) > n else sorted(chosen, key=lambda t: t.instance_id)


def run_sync(fn, *args, **kwargs):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(fn(*args, **kwargs))).result()
    return asyncio.run(fn(*args, **kwargs))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--submission', type=Path, required=True)
    ap.add_argument('--n', type=int, default=20, help='number of development tasks')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--skip-install', action='store_true')
    args = ap.parse_args()

    if not args.skip_install:
        install_wheels()
    os.environ.update(ENV)

    import litellm
    import torch
    import yaml
    from adk_submission import VllmConfig, VllmServer, discover_adapters
    from google.adk.agents.context_cache_config import ContextCacheConfig
    from google.adk.apps._configs import EventsCompactionConfig
    from swegemma.config import ALLOWED_ADAPTER_EXTENSIONS, EvalConfig, build_submission_limits
    from swegemma.evaluate import Evaluator
    from swegemma.models import load_tasks
    from swegemma.models.discovery import validate_single_declared_model

    litellm.drop_params = True
    work = Path('/kaggle/working')
    agent_dir = work / ('eval_' + args.submission.name)
    if agent_dir.exists():
        shutil.rmtree(agent_dir)
    shutil.copytree(args.submission, agent_dir)

    declared = validate_single_declared_model(agent_dir)
    adapters = discover_adapters(str(agent_dir), adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS)
    gpus = torch.cuda.device_count() if torch.cuda.is_available() else 1
    tp = 4 if gpus >= 4 else (2 if gpus >= 2 else 1)
    server = VllmServer(
        VllmConfig(
            model=str(MODEL_PATH), port=8000, host='127.0.0.1', tool_call_parser='gemma4', reasoning_parser='gemma4',
            max_model_len=32768, dtype='bfloat16' if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else 'auto',
            gpu_memory_utilization=0.90, enable_auto_tool_choice=True, enable_lora=True, max_loras=8, max_lora_rank=128,
            tensor_parallel_size=tp, startup_timeout=60 * 20,
        ),
        adapter_manifest=adapters,
    )
    server.start()
    models = server.create_model_registry(aliases=[declared, MODEL], model_prefix='openai/', api_key='EMPTY')

    cfg_y = yaml.safe_load((agent_dir / 'eval_config.yaml').read_text(encoding='utf-8'))
    ev = cfg_y.get('evaluation', cfg_y)
    limits, constraints = build_submission_limits()
    tasks_path = DATA_DIR / 'tasks.jsonl'
    tasks = pick_tasks(load_tasks(tasks_path), args.n, args.seed)
    config = EvalConfig(
        tasks_path=tasks_path, snapshots_dir=DATA_DIR / 'snapshots', results_dir=work / ('results_' + args.submission.name),
        submission_dir=agent_dir, models=models, sandbox='subprocess',
        timeout_seconds=int(ev.get('timeout_seconds', 300)), max_time_minutes=float(ev.get('max_time_minutes', 60.0)),
        max_tool_calls=int(ev.get('max_tool_calls', 100)),
        max_turns=int(ev['max_turns']) if ev.get('max_turns') is not None else None,
        limits=limits, generation_constraints=constraints, adapter_manifest=adapters,
        context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
        events_compaction_config=EventsCompactionConfig(compaction_interval=15, overlap_size=2, token_threshold=14336, event_retention_size=5),
        graph_dir=str(DATA_DIR / 'graphs'), embeddings_dir=str(DATA_DIR / 'embeddings'), wheels_dir=DATA_DIR / 'wheels', verbose=False,
    )
    evaluator = Evaluator(config)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = ['task_id', 'repo', 'resolved', 'test_exit_code', 'patch_chars', 'tool_calls', 'llm_calls', 'duration_s', 'error']
    with open(args.out, 'w', newline='') as fh:
        wr = csv.DictWriter(fh, fields)
        wr.writeheader()
        t0 = time.time()
        for i, task in enumerate(tasks, 1):
            try:
                r = run_sync(evaluator.evaluate_task, task=task, task_index=i, total_tasks=len(tasks))
                row = dict(task_id=task.instance_id, repo=task.repo, resolved=int(bool(r.resolved)), test_exit_code=r.test_exit_code,
                           patch_chars=len(r.agent_patch or ''), tool_calls=r.tool_calls, llm_calls=r.total_llm_calls,
                           duration_s=round(r.duration_seconds, 1), error=(r.error_message or '')[:200])
            except Exception as e:  # keep going: a crash is a data point too
                row = dict(task_id=task.instance_id, repo=task.repo, resolved=0, test_exit_code='', patch_chars=0,
                           tool_calls=0, llm_calls=0, duration_s=0, error=f'{type(e).__name__}: {e}'[:200])
            wr.writerow(row)
            fh.flush()
            print(f"[{i}/{len(tasks)}] {row['task_id']} resolved={row['resolved']} patch={row['patch_chars']} "
                  f"tools={row['tool_calls']} {row['duration_s']}s  (elapsed {(time.time() - t0) / 60:.1f} min)", flush=True)
    server.stop() if hasattr(server, 'stop') else None


if __name__ == '__main__':
    main()
