"""step_gate.py: run an agent one model call at a time.

A pass-through OpenAI-style endpoint (/v1/chat/completions) in front of any upstream: the local gateway
(https://llm.rayflow.dev/v1) or tools/anthropic_proxy.py (http://127.0.0.1:8790/v1). Every call goes to the upstream at
once, but its answer is held until the step is released, so each step can be read whole before the agent acts on it:
the agent that speaks, what it just received (the script's answer to its last call) and what it decided (its thinking
and its call). The user's request (2026-10-14): watching a running batch is not step by step.

  python tools/step_gate.py serve --upstream <url>/v1 --dir <state dir> [--port 8791]
  python tools/step_gate.py show  --dir <state dir> [N]        # step N (default: the one waiting), in full
  python tools/step_gate.py go    --dir <state dir> [K]        # release the next K steps (default 1)
  python tools/step_gate.py until --dir <state dir> <agent>    # release steps until <agent> speaks (then hold)
  python tools/step_gate.py status --dir <state dir>

The time a step waits to be released is added to <dir>/paused_s; tools/eval_env.py passes SWE_PAUSED_FILE to the
sandbox so that the scripts' clock leaves the pauses out (the model sees the time it would have seen without them).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGENTS = [  # the first words of each agent's instruction (variants/stages)
    ('You route the fix', 'coordinator'), ('You turn the issue', 'requirements'), ('You write one test', 'reproduce'),
    ('You find where in the code', 'locate'), ('You decide which code related', 'scope'),
    ('You write what must change', 'specify'), ('You write the code of one place', 'edit'),
    ('You check whether the patch', 'review'),
]
LOCK = threading.Lock()


def agent_of(messages):
    system = next((m.get('content') for m in messages if m.get('role') == 'system'), '') or ''
    if isinstance(system, list):
        system = ' '.join(p.get('text', '') for p in system if isinstance(p, dict))
    for prefix, name in AGENTS:
        if prefix in system[:400]:
            return name
    return system[:40].replace('\n', ' ') or '?'


def _read_int(path, default=0):
    try:
        return int(float(Path(path).read_text().strip() or default))
    except (OSError, ValueError):
        return default


def _sse(answer):
    """The answer as the chunks of a streamed one (when the client asked for a stream)."""
    msg = answer['choices'][0]['message']
    delta = {'role': 'assistant', 'content': msg.get('content') or ''}
    if msg.get('reasoning_content'):
        delta['reasoning_content'] = msg['reasoning_content']
    if msg.get('tool_calls'):
        delta['tool_calls'] = [{**tc, 'index': i} for i, tc in enumerate(msg['tool_calls'])]
    base = {'id': answer.get('id', 'gate'), 'object': 'chat.completion.chunk', 'created': int(time.time()),
            'model': answer.get('model', '')}
    chunks = [{**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
              {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': answer['choices'][0].get(
                  'finish_reason', 'stop')}], 'usage': answer.get('usage')}]
    return ''.join(f'data: {json.dumps(c)}\n\n' for c in chunks) + 'data: [DONE]\n\n'


def _assemble(lines):
    """A non-streamed chat completion from the SSE lines of a streamed one (content, reasoning, tool calls, usage)."""
    msg = {'role': 'assistant', 'content': '', 'reasoning_content': ''}
    calls, finish, usage, meta = {}, None, None, {}
    error = None
    for raw in lines:
        line = raw.decode('utf-8', 'replace').strip()
        if not line.startswith('data:') or line[5:].strip() == '[DONE]':
            continue
        try:
            chunk = json.loads(line[5:].strip())
        except ValueError:
            continue
        if chunk.get('error'):
            # gm10: vLLM answered 200 and sent its 400 inside the stream (thinking_token_budget without
            # --reasoning-config); it was assembled into an empty answer, and the agent was nudged twice
            error = chunk['error']
            continue
        meta = meta or {k: chunk.get(k) for k in ('id', 'model', 'created')}
        usage = chunk.get('usage') or usage
        for ch in chunk.get('choices') or []:
            d = ch.get('delta') or {}
            msg['content'] += d.get('content') or ''
            msg['reasoning_content'] += d.get('reasoning_content') or d.get('reasoning') or ''
            for tc in d.get('tool_calls') or []:
                c = calls.setdefault(tc.get('index', 0), {'id': '', 'type': 'function',
                                                          'function': {'name': '', 'arguments': ''}})
                c['id'] = tc.get('id') or c['id']
                f = tc.get('function') or {}
                c['function']['name'] += f.get('name') or ''
                c['function']['arguments'] += f.get('arguments') or ''
            finish = ch.get('finish_reason') or finish
    if error and not (msg['content'] or calls):
        return {'error': error}
    if calls:
        msg['tool_calls'] = [calls[k] for k in sorted(calls)]
    if not msg['reasoning_content']:
        msg.pop('reasoning_content')
    return {**meta, 'object': 'chat.completion', 'usage': usage,
            'choices': [{'index': 0, 'message': msg, 'finish_reason': finish or 'stop'}]}


class Gate(BaseHTTPRequestHandler):
    upstream = ''
    dir = Path('.')
    api_key = ''              # the upstream's key, sent instead of the client's (a Colab vLLM behind a tunnel)
    stream_upstream = False   # ask the upstream for a stream and assemble it: a Cloudflare quick tunnel ends a
                              # response whose first byte takes over 100 s (a long non-streamed generation)

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header('content-type', ctype)
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _forward(self, method, path, data=None):
        auth = f'Bearer {self.api_key}' if self.api_key else self.headers.get('authorization', 'Bearer EMPTY')
        req = urllib.request.Request(self.upstream.rstrip('/') + path, data=data, method=method, headers={
            'content-type': 'application/json', 'authorization': auth, 'user-agent': 'swe-monitor'})
        try:
            with urllib.request.urlopen(req, timeout=3600) as r:
                if self.stream_upstream and method == 'POST':
                    ans = _assemble(r)
                    if 'error' in ans:
                        return int(ans['error'].get('code') or 502), json.dumps(ans).encode()
                    return r.status, json.dumps(ans).encode()
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            # gm3: a connection reset by the gateway crashed the handler and lost the step; the client gets a 502 and
            # retries, and the step is recorded
            return 502, json.dumps({'error': {'message': f'gate: upstream unreachable: {e}', 'code': 502}}).encode()

    def do_GET(self):
        code, body = self._forward('GET', self.path.replace('/v1', '', 1) if self.path.startswith('/v1') else self.path)
        self._send(code, body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))) or b'{}')
        stream = bool(req.get('stream'))
        req['stream'] = bool(self.stream_upstream)
        req.pop('stream_options', None)
        if self.stream_upstream:
            req['stream_options'] = {'include_usage': True}
        with LOCK:
            n = _read_int(self.dir / 'count') + 1
            (self.dir / 'count').write_text(str(n))
        t0 = time.time()
        code, body = self._forward('POST', '/chat/completions', json.dumps(req).encode())
        secs = round(time.time() - t0, 2)
        try:
            answer = json.loads(body)
        except ValueError:
            answer = {'raw': body.decode('utf-8', 'replace')}
        step = {'n': n, 'agent': agent_of(req.get('messages', [])), 'code': code, 'secs': secs, 't': t0,
                'request': req, 'answer': answer}
        (self.dir / f'{n:04d}.json').write_text(json.dumps(step, indent=1, default=str))
        # held until released: `go` raises <dir>/allow; `until <agent>` releases while another agent speaks
        held = time.time()
        while _read_int(self.dir / 'allow') < n:
            target = (self.dir / 'until').read_text().strip() if (self.dir / 'until').exists() else ''
            if target and step['agent'] != target:
                break
            if target and step['agent'] == target:
                (self.dir / 'until').unlink(missing_ok=True)
            time.sleep(0.3)
        waited = time.time() - held
        with LOCK:
            total = float((self.dir / 'paused_s').read_text() or 0) if (self.dir / 'paused_s').exists() else 0.0
            (self.dir / 'paused_s').write_text(f'{total + waited:.1f}')
            if _read_int(self.dir / 'allow') < n:
                (self.dir / 'allow').write_text(str(n))
        if code != 200 or not stream or 'choices' not in answer:
            return self._send(code, body)
        self._send(200, _sse(answer), 'text/event-stream')


def _text(c):
    if isinstance(c, list):
        return '\n'.join(p.get('text', '') for p in c if isinstance(p, dict))
    if isinstance(c, str):
        try:
            d = json.loads(c)
            if isinstance(d, dict) and 'stdout' in d:
                return d['stdout'] + (('\n[stderr] ' + d['stderr']) if d.get('stderr') else '')
            if isinstance(d, dict) and 'result' in d:
                return str(d['result'])
        except ValueError:
            pass
    return str(c or '')


def show(d, n=None):
    files = sorted(d.glob('[0-9]*.json'))
    if not files:
        print('no step yet')
        return
    step = json.loads((d / f'{n:04d}.json').read_text()) if n else json.loads(files[-1].read_text())
    msgs = step['request'].get('messages', [])
    last_ai = max((i for i, m in enumerate(msgs) if m.get('role') == 'assistant'), default=-1)
    new = msgs[last_ai + 1:] if last_ai >= 0 else [m for m in msgs if m.get('role') != 'system']
    print(f"=== step {step['n']} · {step['agent']} · {len(msgs)} messages · {step['secs']} s · HTTP {step['code']}")
    for m in new:
        print(f"--- it received ({m.get('role')}{', ' + m.get('name') if m.get('name') else ''}):")
        print(_text(m.get('content')))
    a = step['answer']
    if 'choices' not in a:
        print('--- upstream error:', json.dumps(a)[:2000])
        return
    msg = a['choices'][0]['message']
    if msg.get('reasoning_content'):
        print('--- its thinking:')
        print(msg['reasoning_content'])
    if msg.get('content'):
        print('--- its text:')
        print(msg['content'])
    for tc in msg.get('tool_calls') or []:
        f = tc.get('function', {})
        print(f"--- its call: {f.get('name')}")
        try:
            args = json.loads(f.get('arguments') or '{}')
            for k, v in args.items():
                if k == 'args' and isinstance(v, list):
                    for i, item in enumerate(v):
                        print(f'  args[{i}]:\n{item}' if '\n' in str(item) else f'  args[{i}]: {item}')
                else:
                    print(f'  {k}: {v}')
        except ValueError:
            print('  (arguments are not JSON) ' + str(f.get('arguments')))
    print(f"--- finish: {a['choices'][0].get('finish_reason')} · usage {a.get('usage')}")
    held = _read_int(d / 'allow') < step['n']
    print('=== HELD: waiting for go' if held else '=== released')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['serve', 'show', 'go', 'until', 'status'])
    ap.add_argument('arg', nargs='?')
    ap.add_argument('--dir', type=Path, required=True)
    ap.add_argument('--upstream', default='http://127.0.0.1:8790/v1')
    ap.add_argument('--port', type=int, default=8791)
    ap.add_argument('--api-key-file', default='', help="a file holding the upstream's key (never the repository)")
    ap.add_argument('--stream-upstream', action='store_true', help='stream from the upstream and assemble (tunnels)')
    a = ap.parse_args()
    a.dir.mkdir(parents=True, exist_ok=True)
    if a.cmd == 'serve':
        Gate.upstream, Gate.dir = a.upstream, a.dir
        Gate.stream_upstream = a.stream_upstream
        if a.api_key_file:
            Gate.api_key = Path(a.api_key_file).read_text().strip()
        for f in ('count', 'allow'):
            (a.dir / f).write_text('0')
        (a.dir / 'paused_s').write_text('0')
        print(f'gate on http://127.0.0.1:{a.port}/v1 -> {a.upstream}, steps in {a.dir}', flush=True)
        ThreadingHTTPServer(('127.0.0.1', a.port), Gate).serve_forever()
    elif a.cmd == 'show':
        show(a.dir, int(a.arg) if a.arg else None)
    elif a.cmd == 'go':
        k = int(a.arg or 1)
        (a.dir / 'allow').write_text(str(_read_int(a.dir / 'allow') + k))
        print('allow', _read_int(a.dir / 'allow'))
    elif a.cmd == 'until':
        (a.dir / 'until').write_text(a.arg or '')
        print('releasing until', a.arg, 'speaks')
    else:
        print(f"steps {_read_int(a.dir / 'count')}, released {_read_int(a.dir / 'allow')}, "
              f"paused {(a.dir / 'paused_s').read_text() if (a.dir / 'paused_s').exists() else 0} s")


if __name__ == '__main__':
    sys.exit(main())
