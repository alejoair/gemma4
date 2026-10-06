"""Offline smoke test for the single-agent submission (no GPU, no real model).

Compiles the submission with the competition's compiler, swaps the model for a scripted fake LLM and
stub tools, runs it through ADK's Runner and checks that the agent sees the problem statement in its
prompt, uses its tools and calls submit_patch last.

Usage: python bench/smoke_test.py submission
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scripted_llm as st  # noqa: E402
from adk_submission import ModelRegistry, ToolRegistry, compile_submission  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402
from swegemma.config import build_submission_limits  # noqa: E402

MARKER = 'expert autonomous software engineer'
SCRIPT = {
    MARKER: [[
        ('call', 'search_similar_code', {'query': 'Foo'}),
        ('call', 'read_file', {'filepath': 'pkg/foo.py'}),
        ('call', 'edit_file', {'filepath': 'pkg/foo.py', 'old_string': 'a', 'new_string': 'b'}),
        ('call', 'run_command', {'command': 'PYTHONPATH=/workspace:/workspace/src python -c "import pkg"'}),
        ('call', 'submit_patch', {}),
        ('text', 'Fixed: raise ValueError'),
    ]],
}


async def run(path: Path) -> None:
    st.SCRIPT.clear()
    st.SCRIPT.update(SCRIPT)
    limits, constraints = build_submission_limits()
    models = ModelRegistry()
    models.register(st.MODEL, st.ScriptedLlm())
    tools = ToolRegistry()
    for n in ['run_command', 'submit_patch', 'get_status', 'read_file', 'edit_file', 'write_file',
              'get_code_neighbors', 'search_similar_code', 'get_code_subgraph']:
        tools.register(n, st._stub(n))
    agent = compile_submission(path, tools, models, limits=limits, generation_constraints=constraints)

    svc = InMemorySessionService()
    session = await svc.create_session(app_name='smoke', user_id='u', state={'problem_description': st.PROBLEM})
    runner = Runner(agent=agent, app_name='smoke', session_service=svc)
    msg = types.Content(role='user', parts=[types.Part(text=f'Fix this: {st.PROBLEM}')])
    async for _ in runner.run_async(user_id='u', session_id=session.id, new_message=msg):
        pass

    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(('[ok]   ' if cond else '[FAIL] ') + label)
        if not cond:
            failures.append(label)

    systems = [system for _, system, _ in st.requests]
    check(bool(systems) and all(st.PROBLEM in s for s in systems), '{problem_description} injected into the prompt')
    check(all('PYTHONPATH' in s for s in systems), 'prompt warns about running against the workspace code')
    tool_names = [t for _, t in st.calls]
    check(tool_names[-1] == 'submit_patch' and tool_names.count('submit_patch') == 1,
          f'submit_patch called once, last ({tool_names})')
    check(len(agent.sub_agents) == 0, 'single agent, no sub-agents')
    if failures:
        sys.exit(f'{len(failures)} check(s) failed')
    print('SMOKE OK')


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1] if len(sys.argv) > 1 else 'submission').resolve()))
