"""Scripted fake LLM and stub tools shared by the offline tests (smoke_test.py, e2e_gold.py).

The fake model picks a scripted turn by looking for a marker in the agent's system instruction and
replays tool calls and final texts, so the real compiler, ADK runner and harness run without a GPU.
"""

from __future__ import annotations

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

MODEL = 'gemma-4-31b-it-qat-w4a16-ct'
PROBLEM = 'PROBLEM-MARKER: Foo.bar() raises KeyError instead of ValueError'

SCRIPT: dict = {}

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
