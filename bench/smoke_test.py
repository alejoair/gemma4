"""Offline smoke test for a pipeline submission, no GPU and no real model.

Compiles the submission with the competition's compiler, swaps the model for a
scripted fake LLM and stub tools, runs it through ADK's Runner and checks how
information moves between stages:

  * output_key text lands in session.state and is injected as {variable}
  * the optional {verdict?} is empty on the first loop round
  * include_contents: none hides earlier tool traffic
  * submit_patch is called exactly once, by the last stage
  * every stage sees {problem_description}

Usage: python bench/smoke_test.py submissions/b_pipeline
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from adk_submission import ModelRegistry, ToolRegistry, compile_submission
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from swegemma.config import build_submission_limits

MODEL = 'gemma-4-31b-it-qat-w4a16-ct'
PROBLEM = 'PROBLEM-MARKER: Foo.bar() raises KeyError instead of ValueError'

TRIAGE_REPORT = (
    'FILE: pkg/foo.py\nSYMBOL: Foo.bar\nLINES: 10-20\nCAUSE: wrong exception\n'
    'CHANGE: raise ValueError\nTEST: pytest tests/test_foo.py -q'
)

# stage marker in the system instruction -> list of scripted turns.
# A turn is a list of steps: ('call', tool, args) or ('text', str).
SCRIPT = {
    'TRIAGE stage': [[('call', 'read_file', {'filepath': 'pkg/foo.py'}), ('text', TRIAGE_REPORT)]],
    'FIX stage': [
        [('call', 'edit_file', {'filepath': 'pkg/foo.py', 'old_string': 'a', 'new_string': 'b'}),
         ('text', 'EDITED: pkg/foo.py - ValueError')],
        [('text', 'NOOP')],
        [('text', 'NOOP')],
    ],
    'CHECK stage': [
        [('call', 'run_command', {'command': 'pytest'}),
         ('text', 'VERDICT: FAIL\nEVIDENCE: still KeyError\nNEXT: also fix bar2')],
        [('call', 'run_command', {'command': 'pytest'}),
         ('text', 'VERDICT: PASS\nEVIDENCE: ok\nNEXT: none')],
        [('text', 'VERDICT: PASS (unchanged)')],
    ],
    'FINAL stage': [[('call', 'run_command', {'command': 'git status'}),
                     ('call', 'submit_patch', {}), ('text', 'Done: raise ValueError')]],
}

calls: list[tuple[str, str]] = []  # (stage, tool)
requests: list[tuple[str, str, str]] = []  # (stage, system_instruction, contents_text)
progress: dict[str, list[int]] = {}  # stage -> [turn_idx, step_idx]


def _system_text(req: LlmRequest) -> str:
    si = req.config.system_instruction if req.config else ''
    if isinstance(si, str):
        return si
    parts = getattr(si, 'parts', None) or []
    return '\n'.join(p.text or '' for p in parts)


def _contents_text(req: LlmRequest) -> str:
    out = []
    for c in req.contents or []:
        for p in c.parts or []:
            if p.text:
                out.append(p.text)
            if p.function_call:
                out.append(f'<call {p.function_call.name}>')
            if p.function_response:
                out.append(f'<resp {p.function_response.name}>')
    return '\n'.join(out)


class ScriptedLlm(BaseLlm):
    model: str = 'scripted'

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        system = _system_text(llm_request)
        stage = next((k for k in SCRIPT if k in system), None)
        assert stage, f'unknown stage, instruction starts: {system[:80]!r}'
        requests.append((stage, system, _contents_text(llm_request)))
        turns = SCRIPT[stage]
        turn_idx, step_idx = progress.setdefault(stage, [0, 0])
        turn = turns[min(turn_idx, len(turns) - 1)]
        kind, *rest = turn[step_idx]
        if kind == 'call':
            tool, args = rest
            progress[stage][1] += 1
            calls.append((stage, tool))
            part = types.Part(function_call=types.FunctionCall(name=tool, args=args))
        else:
            progress[stage] = [turn_idx + 1, 0]
            part = types.Part(text=rest[0])
        yield LlmResponse(content=types.Content(role='model', parts=[part]))


def _stub(name: str):
    def tool(*args, **kwargs) -> str:
        return '{"status": "ok"}'

    tool.__name__ = name
    return tool


async def run(path: Path) -> None:
    limits, constraints = build_submission_limits()
    models = ModelRegistry()
    models.register(MODEL, ScriptedLlm())
    tools = ToolRegistry()
    for n in ['run_command', 'submit_patch', 'get_status', 'read_file', 'edit_file',
              'write_file', 'get_code_neighbors', 'search_similar_code', 'get_code_subgraph']:
        tools.register(n, _stub(n))
    agent = compile_submission(path, tools, models, limits=limits, generation_constraints=constraints)

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name='smoke', user_id='u', state={'problem_description': PROBLEM}
    )
    runner = Runner(agent=agent, app_name='smoke', session_service=svc)
    msg = types.Content(role='user', parts=[types.Part(text=f'Fix this: {PROBLEM}')])
    async for _ in runner.run_async(user_id='u', session_id=session.id, new_message=msg):
        pass
    final = await svc.get_session(app_name='smoke', user_id='u', session_id=session.id)
    state = final.state

    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(('[ok]   ' if cond else '[FAIL] ') + label)
        if not cond:
            failures.append(label)

    check(state.get('locus') == TRIAGE_REPORT, 'triage final text saved in state["locus"]')
    check(str(state.get('verdict', '')).startswith('VERDICT: PASS'), 'last checker text saved in state["verdict"]')

    by_stage: dict[str, list[tuple[str, str]]] = {}
    for stage, system, contents in requests:
        by_stage.setdefault(stage, []).append((system, contents))

    check(all(PROBLEM in s for s, _ in sum(by_stage.values(), [])),
          '{problem_description} injected into every stage prompt')
    fixer = by_stage['FIX stage']
    check('FILE: pkg/foo.py' in fixer[0][0], '{locus} injected into the fixer prompt')
    check('VERDICT' not in fixer[0][0].split('Latest check result')[1][:60],
          '{verdict?} empty on the first fixer round')
    check(any('VERDICT: FAIL' in s for s, _ in fixer[1:]), 'fixer round 2 sees the failed verdict')
    check(all('<call read_file>' not in c for _, c in by_stage['FIX stage']),
          'include_contents none: triage tool calls are hidden from the fixer')
    check(all('<call' not in c for _, c in by_stage['FINAL stage'][:1]),
          'include_contents none: finalizer starts without earlier tool calls')

    submit_stages = [s for s, t in calls if t == 'submit_patch']
    check(submit_stages == ['FINAL stage'], f'submit_patch called once, by the last stage ({submit_stages})')
    order = [s for s, _ in calls]
    check(order[-1] == 'FINAL stage', 'last tool call belongs to the finalizer')
    rounds = len(fixer)
    print(f'       loop rounds executed: {rounds}; tool calls: {len(calls)}')

    if failures:
        sys.exit(f'{len(failures)} check(s) failed')
    print('SMOKE OK')


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1] if len(sys.argv) > 1 else 'submissions/b_pipeline').resolve()))
