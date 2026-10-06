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

MARKER = 'FIXER of a bug-fixing pipeline'
LOC = 'LOCATOR of a bug-fixing pipeline'
SCRIPT = {
    LOC: [[
        ('call', 'get_code_subgraph', {'nodes': ['Foo']}),
        ('call', 'read_file', {'filepath': 'pkg/foo.py'}),
        ('text', 'FILE: pkg/foo.py\nSYMBOL: Foo\nLINES: 1-5\nCAUSE: x\nCHANGE: y\nALSO: NONE'),
    ]],
    MARKER: [[
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

    loc = [x for _, x, _ in st.requests if LOC in x]
    fix = [x for _, x, _ in st.requests if MARKER in x]
    check(bool(loc) and bool(fix), 'both stages ran')
    check(all('{' not in x for x in loc + fix), 'prompts compile with no unresolved placeholders')
    check(all('FILE: pkg/foo.py' in x for x in fix), 'fixer receives the locator report')
    check(all(st.PROBLEM[:30] in x for x in loc + fix), 'both stages see the problem statement')
    check(all('get_status' in x and 'PYTHONPATH' in x for x in fix), 'fixer prompt covers budget and workspace path')
    tool_names = [t for _, t in st.calls]
    check(tool_names[:2] == ['get_code_subgraph', 'read_file'], f'locator used only graph + read ({tool_names})')
    check(tool_names[-1] == 'submit_patch' and tool_names.count('submit_patch') == 1, 'submit_patch called once, last')
    check([a.name for a in agent.sub_agents] == ['locator', 'fixer'], 'sequential locator -> fixer')
    if failures:
        sys.exit(f'{len(failures)} check(s) failed')
    print('SMOKE OK')


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1] if len(sys.argv) > 1 else 'submission').resolve()))
