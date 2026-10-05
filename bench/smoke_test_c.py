"""Offline smoke test for submissions/c_pipeline_v2 (locator committee + repro gate).

Same idea as smoke_test.py: scripted LLM, stub tools, real compiler and ADK Runner. Checks:
  * the three parallel locators all run and each writes its own output_key
  * merge sees all three reports; repro sees the merged locus
  * fixer and checker see the repro; the checker gate reads it
  * submit_patch is called once, by the last stage

Usage: python bench/smoke_test_c.py submissions/c_pipeline_v2
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import smoke_test as st  # noqa: E402
from adk_submission import ModelRegistry, ToolRegistry, compile_submission  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402
from swegemma.config import build_submission_limits  # noqa: E402

LOC_A = '1. pkg/foo.py :: Foo.bar - named in the statement'
LOC_B = '1. pkg/foo.py :: Foo.bar - error text matches\n2. pkg/util.py :: helper - same string'
LOC_C = '1. pkg/foo.py :: Foo.bar - test calls it\nTEST: pytest tests/test_foo.py -q'
LOCUS = ('FILE: pkg/foo.py\nSYMBOL: Foo.bar\nLINES: 10-20\nCAUSE: wrong exception\nCHANGE: raise ValueError\n'
         'ALT: pkg/util.py :: helper\nTEST: pytest tests/test_foo.py -q')
REPRO = 'STATUS: FAILS_BEFORE\nCMD: cd /workspace && python /tmp/repro.py\nEXPECTED: ValueError is raised'

SCRIPT = {
    'LOCATOR-SYMBOLS': [[('call', 'search_similar_code', {'query': 'Foo'}), ('text', LOC_A)]],
    'LOCATOR-TEXT': [[('call', 'run_command', {'command': 'grep -rn KeyError pkg | head'}), ('text', LOC_B)]],
    'LOCATOR-TESTS': [[('call', 'run_command', {'command': 'grep -rln bar tests | head'}), ('text', LOC_C)]],
    'MERGE stage': [[('call', 'read_file', {'filepath': 'pkg/foo.py'}), ('text', LOCUS)]],
    'REPRO stage': [[('call', 'run_command', {'command': "cat > /tmp/repro.py <<'EOF'\nprint('repro')\nEOF"}), ('text', REPRO)]],
    'FIX stage': [
        [('call', 'edit_file', {'filepath': 'pkg/foo.py', 'old_string': 'a', 'new_string': 'b'}),
         ('text', 'EDITED: pkg/foo.py - ValueError')],
        [('text', 'NOOP')],
        [('text', 'NOOP')],
    ],
    'CHECK stage': [
        [('call', 'run_command', {'command': 'python /tmp/repro.py'}),
         ('text', 'VERDICT: PASS\nEVIDENCE: repro ok\nNEXT: none')],
        [('text', 'VERDICT: PASS (unchanged)')],
        [('text', 'VERDICT: PASS (unchanged)')],
    ],
    'FINAL stage': [[('call', 'submit_patch', {}), ('text', 'Done')]],
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
    state = (await svc.get_session(app_name='smoke', user_id='u', session_id=session.id)).state

    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(('[ok]   ' if cond else '[FAIL] ') + label)
        if not cond:
            failures.append(label)

    by_stage: dict[str, list[str]] = {}
    for stage, system, _ in st.requests:
        by_stage.setdefault(stage, []).append(system)

    check(state.get('loc_a') == LOC_A and state.get('loc_b') == LOC_B and state.get('loc_c') == LOC_C,
          'each parallel locator wrote its own output_key (loc_a, loc_b, loc_c)')
    check(all(k in by_stage for k in ('LOCATOR-SYMBOLS', 'LOCATOR-TEXT', 'LOCATOR-TESTS')),
          'all three locators ran')
    merge = by_stage['MERGE stage'][0]
    check(LOC_A in merge and LOC_B in merge and LOC_C in merge, 'merge prompt contains all three locator reports')
    check(state.get('locus') == LOCUS, 'merge final text saved in state["locus"]')
    check('Foo.bar' in by_stage['REPRO stage'][0], 'repro prompt contains the merged locus')
    check(state.get('repro') == REPRO, 'repro final text saved in state["repro"]')
    check('FAILS_BEFORE' in by_stage['FIX stage'][0], 'fixer prompt contains the repro')
    check('FAILS_BEFORE' in by_stage['CHECK stage'][0], 'checker prompt contains the repro')
    check(str(state.get('verdict', '')).startswith('VERDICT: PASS'), 'verdict saved')
    submits = [s for s, t in st.calls if t == 'submit_patch']
    check(submits == ['FINAL stage'], f'submit_patch called once, by the last stage ({submits})')
    check(st.calls[-1][0] == 'FINAL stage', 'last tool call belongs to the finalizer')
    first_three = {s for s, _ in st.calls[:3]}
    print(f'       first three tool calls came from: {sorted(first_three)}')
    if failures:
        sys.exit(f'{len(failures)} check(s) failed')
    print('SMOKE OK')


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1] if len(sys.argv) > 1 else 'submissions/c_pipeline_v2').resolve()))
