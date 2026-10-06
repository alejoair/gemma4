"""End-to-end harness test with a scripted LLM that applies the task's gold patch.

Proves a submission works inside the real swegemma harness (sandbox, tools,
submit_patch inside nested agents, patch extraction, Phase 2 verification)
without a GPU or a real model. It does NOT measure agent quality.

Data dir must hold: snapshots/<id>.tgz, graphs/, embeddings/, sandbox/setup.py,
wheels/, and a tasks.jsonl with the task(s) to run.

Usage: python bench/e2e_gold.py submission /path/to/data_dir [instance_id]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml
from adk_submission import ModelRegistry
from swegemma.config import EvalConfig, build_submission_limits
from swegemma.evaluate import Evaluator
from swegemma.models import load_tasks

sys.path.insert(0, str(Path(__file__).parent))
import scripted_llm as st  # noqa: E402

MODEL = st.MODEL


def apply_cmd(patch: str) -> str:
    return "git apply --whitespace=nowarn - <<'GOLD_PATCH_EOF'\n" + patch + "\nGOLD_PATCH_EOF\ngit diff --stat"


def build_script(patch: str) -> dict:
    """Locator answers at once; fixer: apply the gold patch with a shell command, then submit."""
    import smoke_test as sc

    return {sc.LOC: [[('text', 'FILE: x\nSYMBOL: x\nLINES: 1-2\nCAUSE: x\nCHANGE: x\nALSO: NONE')]],
            sc.MARKER: [[('call', 'run_command', {'command': apply_cmd(patch)}),
                         ('call', 'submit_patch', {}), ('text', 'Done')]]}


async def main(sub: Path, data: Path, instance_id: str | None) -> None:
    tasks = load_tasks(data / 'tasks.jsonl')
    task = next(t for t in tasks if instance_id in (None, t.instance_id))
    script = build_script(task.patch)
    st.SCRIPT.clear()
    st.SCRIPT.update(script)

    cfg_yaml = yaml.safe_load((sub / 'eval_config.yaml').read_text())['evaluation']
    limits, constraints = build_submission_limits()
    models = ModelRegistry()
    models.register(MODEL, st.ScriptedLlm())
    cfg = EvalConfig(
        tasks_path=data / 'tasks.jsonl',
        snapshots_dir=data / 'snapshots',
        results_dir=data / 'results',
        submission_dir=sub,
        models=models,
        sandbox='subprocess',
        timeout_seconds=int(cfg_yaml['timeout_seconds']),
        max_time_minutes=float(cfg_yaml['max_time_minutes']),
        max_tool_calls=int(cfg_yaml['max_tool_calls']),
        max_turns=int(cfg_yaml['max_turns']),
        limits=limits,
        generation_constraints=constraints,
        graph_dir=str(data / 'graphs'),
        embeddings_dir=str(data / 'embeddings'),
        wheels_dir=data / 'wheels',
        verbose=False,
    )
    result = await Evaluator(cfg).evaluate_task(task=task, task_index=1, total_tasks=1)
    print(f'task={task.instance_id} resolved={result.resolved} exit={result.test_exit_code} '
          f'patch_chars={len(result.agent_patch or "")} tool_calls={result.tool_calls} '
          f'duration={result.duration_seconds:.1f}s')
    if __import__('os').environ.get('DEBUG_RESULT'):
        for k, v in vars(result).items():
            if k != 'agent_patch':
                print(f'  {k}: {str(v)[:1200]}')
    print('stage order of tool calls:', [s for s, _ in st.calls])
    ok = bool(result.resolved) and bool(result.agent_patch)
    print('E2E OK' if ok else 'E2E FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(),
                     sys.argv[3] if len(sys.argv) > 3 else None))
