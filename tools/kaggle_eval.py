"""Run one submission on a fixed sample of the development tasks with the real model, logging everything.

Meant for a Kaggle notebook with the competition GPUs (4 x L4). Mirrors the official "Getting Started"
notebook (wheelhouse install, vLLM server with the competition model, swegemma Evaluator) and adds heavy
logging so a run can be validated afterwards from /kaggle/working:

  eval_log.txt        everything printed (also visible in the notebook log)
  run_nN.csv          one row per task
  stages_<task>.json  per-stage summary (llm calls, tool calls, tokens, max prompt tokens, time window)
  patch_<task>.diff   the patch the agent produced
  pip_freeze.txt      the full `pip freeze` of the python the test sandbox runs (what the hidden tests import)
  judge_cache/        the judge's reference and no-patch runs per task (tools/judge.py)
  results_<name>/     the harness' own logs/ and traces/
  vllm_server.log     vLLM log
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import csv
import glob
import importlib
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

WORK = Path('/kaggle/working')
WHEELHOUSE_DIR = Path('/kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse')
DATA_DIR = Path('/kaggle/input/competitions/gemma-4-developer-agent')
MODEL_PATH = Path('/kaggle/input/models/google/gemma-4/other/gemma-4-31b-it-qat-w4a16-ct/2')
MODEL = 'gemma-4-31b-it-qat-w4a16-ct'
COMPACTION_THRESHOLD = 14336

ENV = {
    'LITELLM_LOCAL_MODEL_COST_MAP': 'True', 'TRANSFORMERS_NO_TF': '1', 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn',
    'VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS': '1', 'VLLM_ENGINE_READY_TIMEOUT_S': '1200',
    'VLLM_NO_USAGE_STATS': '1', 'OTEL_SDK_DISABLED': 'true', 'PYTORCH_CUDA_ALLOC_CONF': 'expandable_segments:True',
}


class Tee:
    """Duplicate a stream into the log file."""

    def __init__(self, stream, fh):
        self.stream, self.fh = stream, fh

    def write(self, s):
        self.stream.write(s)
        try:
            self.fh.write(s)
            self.fh.flush()
        except Exception:  # noqa: BLE001
            pass
        return len(s)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.stream, name)


def banner(title: str) -> None:
    print('\n' + '=' * 100 + f'\n== {title}\n' + '=' * 100, flush=True)


def sh(cmd: list[str] | str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f'(failed: {type(e).__name__}: {e})'


def install_wheels() -> None:
    banner('install wheelhouse')
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
    print(f'{len(wheels)} wheels', flush=True)
    t0 = time.time()
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--no-deps', '--force-reinstall', *wheels], check=True)
    importlib.invalidate_caches()
    print(f'wheels installed in {time.time() - t0:.0f}s', flush=True)


def check_gpus(min_total_mib: int = 28000) -> None:
    banner('GPUs')
    print(sh('nvidia-smi'), flush=True)
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, check=True).stdout.strip().splitlines()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f'NO GPU FOUND ({e}). Enable a GPU accelerator in the notebook settings.')
    mems = [int(line.split(',')[1]) for line in out]
    if sum(mems) < min_total_mib:
        raise SystemExit(f'NOT ENOUGH GPU MEMORY: {sum(mems)} MiB in total, need about {min_total_mib}.')


def log_environment(submission: Path) -> None:
    banner('environment')
    print('python', sys.version.replace('\n', ' '))
    print(sh([sys.executable, '-m', 'pip', 'list', '--format=freeze'], 120).replace('\n', ' | ')[:3000])
    print('\n-- submission tree (this is exactly what runs):')
    for p in sorted(submission.rglob('*')):
        if p.is_file():
            print(f'  {p.relative_to(submission)}  ({p.stat().st_size} bytes)')
    for p in sorted(submission.rglob('*')):
        if p.is_file() and p.suffix in ('.yaml', '.md'):
            print(f'\n---------- {p.relative_to(submission)} ----------')
            print(p.read_text(encoding='utf-8'))


KEY_PACKAGES_NOTE = 'versions the hidden tests import in the test sandbox'


def log_sandbox_packages(evaluator, judge) -> None:
    """The full `pip freeze` of the python the test sandbox runs, to WORK/pip_freeze.txt, and the key versions."""
    banner('test sandbox packages')
    try:
        r = judge.sandbox_run(evaluator, 'python3 -m pip freeze --all 2>&1; python3 -VV', timeout=300)
        (WORK / 'pip_freeze.txt').write_text(r.stdout or '', encoding='utf-8')
        print(f'pip_freeze.txt: {len((r.stdout or "").splitlines())} lines (exit {r.exit_code})')
    except Exception as e:  # noqa: BLE001
        print(f'(pip freeze in the sandbox failed: {type(e).__name__}: {e})')
    try:
        info = judge.sandbox_packages(evaluator)
        print(f'{KEY_PACKAGES_NOTE}: python {info.get("python")}')
        for k, v in (info.get('packages') or {}).items():
            print(f'  {k}=={v}')
    except Exception as e:  # noqa: BLE001
        print(f'(sandbox package probe failed: {type(e).__name__}: {e})')
    sys.stdout.flush()


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


def _lit(x):
    """Trace fields may be dicts or their string form."""
    if isinstance(x, (dict, list)):
        return x
    if isinstance(x, str):
        try:
            import ast

            return ast.literal_eval(x)
        except Exception:  # noqa: BLE001
            return None
    return None


def summarize_trace(task_id: str, results_dir: Path) -> dict:
    """Per-stage view of the harness trace: who spoke, which tools, tokens, time window."""
    path = results_dir / 'traces' / f'trace_{task_id}.json'
    if not path.exists():
        print(f'(no trace at {path})')
        return {}
    d = json.loads(path.read_text(encoding='utf-8'))
    stages: OrderedDict[str, dict] = OrderedDict()
    timeline = []
    for s in d.get('steps', []):
        extra = _lit(s.get('extra')) or {}
        author = extra.get('author') or ('harness' if s.get('source') in ('user', 'system') else 'agent')
        st = stages.setdefault(author, dict(llm_calls=0, tool_calls=Counter(), prompt_tokens=0, completion_tokens=0,
                                            max_prompt_tokens=0, first_s=None, last_s=None, texts=0))
        el = extra.get('elapsed_s')
        if el is not None:
            st['first_s'] = el if st['first_s'] is None else min(st['first_s'], el)
            st['last_s'] = el if st['last_s'] is None else max(st['last_s'], el)
        m = _lit(s.get('metrics')) or {}
        if m:
            st['llm_calls'] += 1
            st['prompt_tokens'] += m.get('prompt_tokens', 0) or 0
            st['completion_tokens'] += m.get('completion_tokens', 0) or 0
            st['max_prompt_tokens'] = max(st['max_prompt_tokens'], m.get('prompt_tokens', 0) or 0)
        for tc in _lit(s.get('tool_calls')) or []:
            st['tool_calls'][tc.get('function_name', '?')] += 1
            args = json.dumps(tc.get('arguments', {}), ensure_ascii=False)
            timeline.append(f"  [{(tc.get('extra') or {}).get('elapsed_s', el) or 0:7.1f}s] {author:10s} {tc.get('function_name', '?')} {args[:160]}")
        msg = s.get('message')
        if s.get('source') == 'agent' and isinstance(msg, str) and msg.strip() and not s.get('tool_calls'):
            st['texts'] += 1
            timeline.append(f"  [{el or 0:7.1f}s] {author:10s} TEXT {msg.strip()[:200]!r}")
    print(f'\n-- per-stage summary for {task_id}')
    out = {}
    for name, st in stages.items():
        tcs = dict(st['tool_calls'])
        flag = '  <-- over compaction threshold!' if st['max_prompt_tokens'] >= COMPACTION_THRESHOLD else ''
        window = f"{st['first_s']:.0f}s-{st['last_s']:.0f}s" if st['first_s'] is not None else '-'
        print(f"  {name:10s} llm_calls={st['llm_calls']:3d} tools={tcs} prompt_tok={st['prompt_tokens']} "
              f"completion_tok={st['completion_tokens']} max_prompt={st['max_prompt_tokens']} window={window}{flag}")
        out[name] = dict(st, tool_calls=tcs)
    print('\n-- timeline (tool calls and text replies)')
    print('\n'.join(timeline[:150]))
    (WORK / f'stages_{task_id}.json').write_text(json.dumps(out, indent=2), encoding='utf-8')
    return out


def vllm_new_lines(server, offset: int, grep: tuple[str, ...] = ('throughput', 'error', 'Error', 'Traceback', 'WARNING', 'warn')) -> int:
    try:
        text = Path(server.log_path).read_text(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        return offset
    new = text[offset:].splitlines()
    sel = [ln for ln in new if any(g in ln for g in grep)]
    print(f'-- vLLM log: {len(new)} new lines, {len(sel)} relevant (showing last 25)')
    for ln in sel[-25:]:
        print('   ' + ln[:260])
    return len(text)


def start_watchdog(server, probe_every: int = 30, misses: int = 3):
    """vLLM froze in V13 (its log stopped mid-request, no error, every later task got no answer). Every probe_every s
    this probes /health; after `misses` failed probes in a row it sends SIGABRT to the server (started with
    PYTHONFAULTHANDLER=1, so every thread's stack goes to the vLLM log), then restarts it so the remaining tasks run."""
    import signal
    import threading
    import urllib.request

    def probe() -> bool:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.config.port}/health', timeout=10) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def loop():
        failed = 0
        while True:
            time.sleep(probe_every)
            if probe():
                failed = 0
                continue
            failed += 1
            print(f'WATCHDOG: vLLM /health failed ({failed}/{misses})', flush=True)
            if failed < misses:
                continue
            pid = server.process.pid if server.process else None
            print(f'WATCHDOG: vLLM is not answering; SIGABRT to pid {pid} for a stack dump, then a restart', flush=True)
            try:
                for child in sh(f'pgrep -P {pid}').split():
                    os.kill(int(child), signal.SIGABRT)
                os.kill(pid, signal.SIGABRT)
            except Exception as e:  # noqa: BLE001
                print('WATCHDOG: kill failed:', e, flush=True)
            time.sleep(20)
            try:
                print(sh(f'tail -n 120 {server.log_path}', timeout=30), flush=True)
                shutil.copy(server.log_path, WORK / f'vllm_server_hang_{int(time.time())}.log')
                server.stop()
            except Exception as e:  # noqa: BLE001
                print('WATCHDOG: stop failed:', e, flush=True)
            try:
                server.start()
                print('WATCHDOG: vLLM restarted', flush=True)
            except Exception as e:  # noqa: BLE001
                print('WATCHDOG: restart failed:', e, flush=True)
            failed = 0

    threading.Thread(target=loop, daemon=True).start()


def bench_concurrency(port=8000, levels=(1, 2, 4), max_tokens=300):
    """Aggregate generation speed with 1, 2 and 4 requests at once (docs/time_proposals: parallel agents pay off only
    if vLLM batches them on the 4xL4). Each request asks for max_tokens tokens without thinking."""
    import json as _json
    import threading
    import urllib.request

    base = f'http://127.0.0.1:{port}/v1'
    try:
        model = _json.loads(urllib.request.urlopen(base + '/models', timeout=30).read())['data'][0]['id']
    except Exception as e:                        # never stop the evaluation for the benchmark
        print(f'bench: no model list ({e})')
        return

    def one(i, out):
        body = {'model': model, 'max_tokens': max_tokens, 'temperature': 0.0, 'ignore_eos': True,
                'chat_template_kwargs': {'enable_thinking': False},
                'messages': [{'role': 'user', 'content': f'Write a long story about a lighthouse keeper number {i}.'}]}
        req = urllib.request.Request(base + '/chat/completions', data=_json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            r = _json.loads(urllib.request.urlopen(req, timeout=600).read())
            out.append(r['usage']['completion_tokens'])
        except Exception as e:
            print(f'bench: request failed ({e})')

    one(0, [])                                    # warm-up
    for k in levels:
        threads = []
        res = []
        t0 = time.time()
        for i in range(k):
            th = threading.Thread(target=one, args=(i, res))
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        dt = time.time() - t0
        tok = sum(res)
        print(f'BENCH concurrency={k}: {tok} tokens in {dt:.1f}s = {tok / dt:.1f} tok/s aggregate, '
              f'{tok / dt / max(1, k):.1f} tok/s per request', flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--submission', type=Path, required=True)
    ap.add_argument('--n', type=int, default=20, help='number of development tasks')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--tasks', default='', help='comma-separated instance ids (overrides --n/--seed)')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--skip-install', action='store_true')
    ap.add_argument('--concurrency', type=int, default=1, help='tasks run at once')
    ap.add_argument('--bench-concurrency', action='store_true', help='measure vLLM with 1, 2, 4 requests at once first')
    ap.add_argument('--exclude', default='', help='comma-separated instance ids never chosen (with --screen)')
    ap.add_argument('--screen', type=int, default=0,
                    help='choose this many tasks that can be scored here (judge.screen), from every task not excluded')
    ap.add_argument('--screen-seed', type=int, default=1)
    ap.add_argument('--screen-workers', type=int, default=6)
    args = ap.parse_args()

    logfh = open(WORK / 'eval_log.txt', 'a', encoding='utf-8')
    sys.stdout, sys.stderr = Tee(sys.__stdout__, logfh), Tee(sys.__stderr__, logfh)
    t_start = time.time()
    banner(f'START {time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}  n={args.n} seed={args.seed}')

    check_gpus()
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
    sys.path.insert(0, str(Path(__file__).resolve().parent))     # judge.py and eval_env.py ship next to this file
    import eval_env
    import judge
    eval_env.patch()            # the tests import the task's src/ code (see eval_env.py for what is known of the scorer)
    judge.install_capture()     # per-test outcomes from the JUnit report of every verify_task run
    try:
        from swegemma.models.discovery import validate_single_declared_model
    except ImportError:          # swegemma >= 0.2.11 moved model discovery to adk_submission
        from adk_submission.discovery import discover_declared_models

        def validate_single_declared_model(submission_dir):
            found = discover_declared_models(submission_dir)
            assert len(found) == 1, f'expected one declared model, found {sorted(found)}'
            return next(iter(found))

    litellm.drop_params = True
    agent_dir = WORK / ('eval_' + args.submission.name)
    if agent_dir.exists():
        shutil.rmtree(agent_dir)
    shutil.copytree(args.submission, agent_dir)
    log_environment(agent_dir)

    banner('submission validation')
    declared = validate_single_declared_model(agent_dir)
    print('declared model:', declared)
    adapters = discover_adapters(str(agent_dir), adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS)
    print('adapters:', adapters)

    banner('start vLLM')
    gpus = torch.cuda.device_count() if torch.cuda.is_available() else 1
    tp = 4 if gpus >= 4 else (2 if gpus >= 2 else 1)
    print(f'gpus={gpus} tensor_parallel={tp}')
    server = VllmServer(
        VllmConfig(
            model=str(MODEL_PATH), port=8000, host='127.0.0.1', tool_call_parser='gemma4', reasoning_parser='gemma4',
            max_model_len=32768, dtype='bfloat16' if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else 'auto',
            gpu_memory_utilization=0.90, enable_auto_tool_choice=True, enable_lora=True, max_loras=8, max_lora_rank=128,
            tensor_parallel_size=tp, startup_timeout=60 * 20,
        ),
        adapter_manifest=adapters,
    )
    t0 = time.time()
    os.environ['PYTHONFAULTHANDLER'] = '1'      # the server inherits it: SIGABRT then dumps every thread's stack
    server.start()
    start_watchdog(server)
    print(f'vLLM ready after {time.time() - t0:.0f}s; log file: {server.log_path}')
    print(sh(f'tail -n 25 {server.log_path}'))
    if args.bench_concurrency:
        bench_concurrency()
    models = server.create_model_registry(aliases=[declared, MODEL], model_prefix='openai/', api_key='EMPTY')
    vllm_offset = len(Path(server.log_path).read_text(encoding='utf-8', errors='replace'))

    cfg_y = yaml.safe_load((agent_dir / 'eval_config.yaml').read_text(encoding='utf-8'))
    ev = cfg_y.get('evaluation', cfg_y)
    limits, constraints = build_submission_limits()
    tasks_path = DATA_DIR / 'tasks.jsonl'
    all_tasks = load_tasks(tasks_path)
    if args.tasks:
        wanted = [x.strip() for x in args.tasks.split(',') if x.strip()]
        tasks = [t for t in all_tasks if t.instance_id in wanted]
        assert len(tasks) == len(wanted), f'missing tasks: {set(wanted) - {t.instance_id for t in tasks}}'
    else:
        tasks = pick_tasks(all_tasks, args.n, args.seed)
    print('tasks:', [t.instance_id for t in tasks])
    results_dir = WORK / ('results_' + args.submission.name)
    config = EvalConfig(
        tasks_path=tasks_path, snapshots_dir=DATA_DIR / 'snapshots', results_dir=results_dir,
        submission_dir=agent_dir, models=models, sandbox='subprocess',
        timeout_seconds=int(ev.get('timeout_seconds', 300)), max_time_minutes=float(ev.get('max_time_minutes', 60.0)),
        max_tool_calls=int(ev.get('max_tool_calls', 100)),
        max_turns=int(ev['max_turns']) if ev.get('max_turns') is not None else None,
        limits=limits, generation_constraints=constraints, adapter_manifest=adapters,
        context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
        events_compaction_config=EventsCompactionConfig(compaction_interval=15, overlap_size=2, token_threshold=COMPACTION_THRESHOLD, event_retention_size=5),
        graph_dir=str(DATA_DIR / 'graphs'), embeddings_dir=str(DATA_DIR / 'embeddings'), wheels_dir=DATA_DIR / 'wheels', verbose=True,
    )
    evaluator = Evaluator(config)
    log_sandbox_packages(evaluator, judge)
    judge_cache = WORK / 'judge_cache'
    if args.screen:
        # only tasks this environment can score: the reference runs and passes the harness's whole-file check and some
        # test fails without it (V25-30: 8 of 30 had no score here). The runs are cached for the judge.
        banner(f'SCREEN {args.screen} tasks')
        excluded = {x.strip() for x in args.exclude.split(',') if x.strip()}
        cands = judge.screen_order([t for t in all_tasks if t.instance_id not in excluded], args.screen_seed)
        tasks, srows = judge.screen(evaluator, cands, args.screen, judge_cache, workers=args.screen_workers)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out.parent / 'screen.csv', 'w', newline='') as sf:
            sw = csv.DictWriter(sf, list(srows[0]) if srows else ['task_id'])
            sw.writeheader()
            sw.writerows(srows)
        print(f'screened {len(srows)} candidates in {(time.time() - t_start) / 60:.1f} min; tasks:',
              ','.join(t.instance_id for t in tasks), flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = ['task_id', 'repo', 'resolved', 'judged', 'official_feasible', 'f2p', 'f2p_failing', 'p2p_broken',
              'required_failing', 'flaky', 'judge_note', 'test_exit_code', 'patch_chars', 'tool_calls', 'llm_calls',
              'duration_s', 'error']
    rows = []
    with open(args.out, 'w', newline='') as fh:
        wr = csv.DictWriter(fh, fields)
        wr.writeheader()
        t0 = time.time()
        lock = threading.Lock()
        state = {'vllm_offset': vllm_offset}

        def run_one(i, task, r=None, err=None, mark=None):
            """Everything after the agent's run: the row, the logs, the judge (r is the harness's result)."""
            try:
                if err is not None:
                    raise err
                row = dict(task_id=task.instance_id, repo=task.repo, resolved=int(bool(r.resolved)), test_exit_code=r.test_exit_code,
                           patch_chars=len(r.agent_patch or ''), tool_calls=r.tool_calls, llm_calls=r.total_llm_calls,
                           duration_s=round(r.duration_seconds, 1), error=(r.error_message or '')[:300])
                banner(f'RESULT {task.instance_id}')
                for k, v in vars(r).items():
                    if k != 'agent_patch':
                        print(f'  {k}: {str(v)[:700]}')
                (WORK / f'patch_{task.instance_id}.diff').write_text(r.agent_patch or '', encoding='utf-8')
                # V21: the hidden-test output was cut at 700 characters, so the failing tests could not be named; the
                # whole output is kept, and its FAILED/ERROR lines printed (a 'not resolved' may be the environment's)
                out = str(getattr(r, 'test_output', '') or '')
                (WORK / f'tests_{task.instance_id}.txt').write_text(out, encoding='utf-8')
                for ln in [l for l in out.splitlines() if l.startswith(('FAILED', 'ERROR'))][:30]:
                    print('  hidden test: ' + ln[:200])
                print('\n-- patch (first 2500 chars):\n' + (r.agent_patch or '(empty)')[:2500])
                try:
                    # the harness's verdict needs every test of the test files to pass, also those this environment
                    # fails on the original code: the judge compares with the reference patch (judge.py)
                    v = judge.judge(evaluator, task, r.agent_patch or '', harness_result=r, cache=judge_cache,
                                    capture_mark=mark)
                    row.update(judged=v['verdict'], official_feasible=int(bool(v['official_feasible'])),
                               f2p=len(v['f2p']), f2p_failing=' '.join(v.get('f2p_failing', [])),
                               p2p_broken=' '.join(v.get('p2p_broken', [])),
                               required_failing=' '.join(v.get('required_failing', [])),
                               flaky=len(v.get('flaky', [])), judge_note=v.get('why', '')[:300])
                    print(f'  JUDGED: {judge.short(v)}')
                    print(f'  f2p={v["f2p"]}')
                except Exception as e:  # noqa: BLE001
                    row.update(judged=f'judge error: {type(e).__name__}: {e}'[:200])
            except Exception as e:  # noqa: BLE001 - a crash is a data point too
                import traceback

                traceback.print_exc()
                row = dict(task_id=task.instance_id, repo=task.repo, resolved=0, test_exit_code='', patch_chars=0,
                           tool_calls=0, llm_calls=0, duration_s=0, error=f'{type(e).__name__}: {e}'[:300])
            try:
                summarize_trace(task.instance_id, results_dir)
            except Exception as e:  # noqa: BLE001
                print(f'(trace summary failed: {type(e).__name__}: {e})')
            with lock:
                state['vllm_offset'] = vllm_new_lines(server, state['vllm_offset'])
                rows.append(row)
                wr.writerow(row)
                fh.flush()
            print(f"\n[{i}/{len(tasks)}] {row['task_id']} resolved={row['resolved']} patch={row['patch_chars']} "
                  f"tools={row['tool_calls']} llm={row['llm_calls']} {row['duration_s']}s error={row['error'][:120]!r} "
                  f"(elapsed {(time.time() - t0) / 60:.1f} min)", flush=True)

        # several tasks at once (--concurrency): vLLM on the 4xL4 gives 3.6x the throughput with 4 requests (V25's
        # bench), each about 10% slower than alone; the harness's own CLI runs tasks concurrently the same way
        # in one event loop, as the harness's CLI does it; what follows the agent (judge, logs) runs in a thread
        async def run_all():
            sem = asyncio.Semaphore(max(1, args.concurrency))

            async def worker(i, task):
                async with sem:
                    banner(f'TASK {i}/{len(tasks)} {task.instance_id} ({task.repo})  elapsed '
                           f'{(time.time() - t0) / 60:.1f} min')
                    mark = judge.capture_mark()
                    try:
                        r, err = await evaluator.evaluate_task(task=task, task_index=i, total_tasks=len(tasks)), None
                    except Exception as e:  # noqa: BLE001
                        r, err = None, e
                    # the judge runs verify_task with asyncio.run, which cannot run inside this loop: always in a
                    # thread (vf1, one task at a time: every judgement failed with 'asyncio.run() cannot be called
                    # from a running event loop')
                    await asyncio.to_thread(run_one, i, task, r, err, mark)

            await asyncio.gather(*(worker(i, task) for i, task in enumerate(tasks, 1)))

        asyncio.run(run_all())

    banner('SUMMARY')
    solved = sum(r['resolved'] for r in rows)
    print(f'resolved {solved}/{len(rows)}; empty patches {sum(1 for r in rows if not r["patch_chars"])}; '
          f'errors {sum(1 for r in rows if r["error"])}; total {(time.time() - t_start) / 60:.1f} min')
    judged = [r.get('judged', '') for r in rows]
    print(f'JUDGED (against the reference patch, tools/judge.py): {judged.count("resolved")} resolved of '
          f'{sum(1 for j in judged if j in ("resolved", "not resolved"))} scorable here; '
          f'{judged.count("unscorable")} unscorable')
    print(f'OFFICIAL_FEASIBLE (the reference patch passes the harness\'s own check here): '
          f'{sum(1 for r in rows if r.get("official_feasible") == 1)}/{len(rows)} tasks; a task where it does not '
          f'cannot score officially in this environment')
    for r in rows:
        print('  ', r)
    shutil.copy(server.log_path, WORK / 'vllm_server.log')
    try:
        server.stop()
    except Exception as e:  # noqa: BLE001
        print('server.stop failed:', e)
    print('DONE')


if __name__ == '__main__':
    main()
