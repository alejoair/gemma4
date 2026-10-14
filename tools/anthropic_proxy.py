"""anthropic_proxy.py: an OpenAI-style chat-completions endpoint in front of an Anthropic-style Messages API.

The harness talks to models through LiteLLM's OpenAI client (`openai/<model>` at an `api_base`), as it does with the
local gateway (https://llm.rayflow.dev/v1). This proxy lets the same runs use a service that speaks the Anthropic
Messages API: it serves `POST /v1/chat/completions` and `GET /v1/models`, converts each request to `POST
<upstream>/v1/messages` and converts the answer back.

  ANTHROPIC_API_KEY=... python tools/anthropic_proxy.py --upstream https://api.anthropic.com --port 8790 \
      [--model <upstream model, overrides the request's>] [--auth x-api-key|bearer|auto] [--log <file.jsonl>]
  python tools/local_eval.py ... --api-base http://127.0.0.1:8790/v1 --model <name>

Converted: system messages (to `system`), text, assistant tool calls (`tool_use` blocks) and tool results
(`tool_result` blocks, consecutive ones merged into one user turn), function tools and tool_choice, max_tokens /
max_completion_tokens, temperature, top_p, top_k, stop, one tool call per turn unless --parallel
(`disable_parallel_tool_use`), and reasoning (`reasoning_effort` or `thinking`, answered as
`reasoning_content`, the way vLLM's reasoning parser does; `reasoning`/`reasoning_content` of earlier assistant turns
is not sent back, since Anthropic needs the signed thinking blocks it produced). `stream: true` is answered as one
server-sent-events chunk sequence built from the whole answer. Upstream errors keep their HTTP status (LiteLLM
retries 429 and 5xx).

Only the Python standard library is used. The key comes from ANTHROPIC_API_KEY or, when that is unset, from the
request's own `Authorization: Bearer` header (anything but 'EMPTY'). Never use the outputs to train the competition
model (CLAUDE.md).
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
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PARALLEL = False          # --parallel: let the upstream model make several tool calls in one turn
SAMPLING = ('temperature', 'top_p', 'top_k', 'stop_sequences')
EFFORT_BUDGET = {'minimal': 1024, 'low': 2048, 'medium': 4096, 'high': 16384}
STOP = {'end_turn': 'stop', 'stop_sequence': 'stop', 'max_tokens': 'length', 'tool_use': 'tool_calls',
        'pause_turn': 'stop', 'refusal': 'content_filter'}


def _text_of(content) -> str:
    """The text of an OpenAI message content: a string, or a list of parts (only text parts are kept)."""
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    return ''.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') in ('text', None))


def _blocks(content) -> list[dict]:
    """Anthropic content blocks from an OpenAI user/assistant content (text and images by URL or data URI)."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content else []
    out = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get('type') == 'text' and p.get('text'):
            out.append({'type': 'text', 'text': p['text']})
        elif p.get('type') == 'image_url':
            url = (p.get('image_url') or {}).get('url', '')
            if url.startswith('data:') and ';base64,' in url:
                media, data = url[5:].split(';base64,', 1)
                out.append({'type': 'image', 'source': {'type': 'base64', 'media_type': media, 'data': data}})
            elif url:
                out.append({'type': 'image', 'source': {'type': 'url', 'url': url}})
    return out


def to_anthropic(req: dict, model_override: str | None = None) -> dict:
    """The Anthropic Messages request for an OpenAI chat-completions request."""
    system, msgs = [], []

    def push(role, blocks):
        if not blocks:
            return
        if msgs and msgs[-1]['role'] == role:          # Anthropic wants user/assistant turns to alternate
            msgs[-1]['content'].extend(blocks)
        else:
            msgs.append({'role': role, 'content': list(blocks)})

    for m in req.get('messages') or []:
        role = m.get('role')
        if role in ('system', 'developer'):
            t = _text_of(m.get('content'))
            if t:
                system.append(t)
        elif role == 'user':
            push('user', _blocks(m.get('content')))
        elif role == 'assistant':
            blocks = _blocks(m.get('content'))
            for tc in m.get('tool_calls') or []:
                fn = tc.get('function') or {}
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except json.JSONDecodeError:
                    args = {'_raw': fn.get('arguments')}
                blocks.append({'type': 'tool_use', 'id': tc.get('id') or f'toolu_{uuid.uuid4().hex[:24]}',
                               'name': fn.get('name', ''), 'input': args if isinstance(args, dict) else {'value': args}})
            push('assistant', blocks)
        elif role == 'tool':
            push('user', [{'type': 'tool_result', 'tool_use_id': m.get('tool_call_id', ''),
                           'content': _text_of(m.get('content')) or '(empty)'}])
    if msgs and msgs[0]['role'] != 'user':
        msgs.insert(0, {'role': 'user', 'content': [{'type': 'text', 'text': '(start)'}]})

    out = {'model': model_override or req.get('model'), 'messages': msgs,
           'max_tokens': int(req.get('max_completion_tokens') or req.get('max_tokens') or 4096)}
    if system:
        out['system'] = '\n\n'.join(system)
    for k in ('temperature', 'top_p', 'top_k'):
        if req.get(k) is not None:
            out[k] = int(req[k]) if k == 'top_k' else float(req[k])     # (the ADK sends top_k as 64.0)
    stop = req.get('stop')
    if stop:
        out['stop_sequences'] = [stop] if isinstance(stop, str) else list(stop)
    tools = [t['function'] for t in req.get('tools') or [] if t.get('type') == 'function' and t.get('function')]
    if tools:
        out['tools'] = [{'name': f['name'], 'description': f.get('description', ''),
                         'input_schema': f.get('parameters') or {'type': 'object', 'properties': {}}} for f in tools]
        tc = req.get('tool_choice')
        if tc == 'required':
            out['tool_choice'] = {'type': 'any'}
        elif tc == 'none':
            out['tool_choice'] = {'type': 'none'}
        elif isinstance(tc, dict) and (tc.get('function') or {}).get('name'):
            out['tool_choice'] = {'type': 'tool', 'name': tc['function']['name']}
    budget = None
    th = req.get('thinking')
    if isinstance(th, dict) and th.get('type') == 'enabled':
        budget = int(th.get('budget_tokens') or 4096)
    elif req.get('reasoning_effort') in EFFORT_BUDGET:
        budget = EFFORT_BUDGET[req['reasoning_effort']]
    if budget:
        budget = max(1024, budget)
        out['thinking'] = {'type': 'enabled', 'budget_tokens': budget}
        out['max_tokens'] = max(out['max_tokens'], budget + 1024)
        for k in ('temperature', 'top_p', 'top_k'):          # not allowed with thinking
            out.pop(k, None)
        if out.get('tool_choice', {}).get('type') in ('any', 'tool'):
            out['tool_choice'] = {'type': 'auto'}
    if tools and not PARALLEL and req.get('parallel_tool_calls') is not True:
        # one tool call per turn, as the competition model makes them (V20-V21: no turn with two calls). Parallel
        # calls ran several editors on the same journal and files at once (s1, requests_7328: an edit the tests
        # undid stayed in the patch).
        choice = out.get('tool_choice') or {'type': 'auto'}
        if choice.get('type') != 'none':
            out['tool_choice'] = {**choice, 'disable_parallel_tool_use': True}
    return out


def to_openai(resp: dict, model: str) -> dict:
    """The OpenAI chat-completions answer for an Anthropic Messages answer."""
    text, thinking, calls = [], [], []
    for b in resp.get('content') or []:
        if b.get('type') == 'text':
            text.append(b.get('text', ''))
        elif b.get('type') == 'thinking':
            thinking.append(b.get('thinking', ''))
        elif b.get('type') == 'tool_use':
            calls.append({'id': b.get('id'), 'type': 'function',
                          'function': {'name': b.get('name'), 'arguments': json.dumps(b.get('input') or {})}})
    msg = {'role': 'assistant', 'content': ''.join(text) or None}
    if thinking:
        msg['reasoning_content'] = '\n'.join(thinking)
    if calls:
        msg['tool_calls'] = calls
    u = resp.get('usage') or {}
    prompt = (u.get('input_tokens') or 0) + (u.get('cache_read_input_tokens') or 0) + \
        (u.get('cache_creation_input_tokens') or 0)
    return {'id': resp.get('id') or f'chatcmpl-{uuid.uuid4().hex}', 'object': 'chat.completion',
            'created': int(time.time()), 'model': model,
            'choices': [{'index': 0, 'message': msg,
                         'finish_reason': 'tool_calls' if calls else STOP.get(resp.get('stop_reason'), 'stop')}],
            'usage': {'prompt_tokens': prompt, 'completion_tokens': u.get('output_tokens') or 0,
                      'total_tokens': prompt + (u.get('output_tokens') or 0),
                      'prompt_tokens_details': {'cached_tokens': u.get('cache_read_input_tokens') or 0}}}


def sse_chunks(answer: dict):
    """The answer as chat.completion.chunk events (one delta for the content, one per tool call, then the finish)."""
    base = {'id': answer['id'], 'object': 'chat.completion.chunk', 'created': answer['created'],
            'model': answer['model']}
    msg, choice = answer['choices'][0]['message'], answer['choices'][0]
    delta = {'role': 'assistant'}
    if msg.get('reasoning_content'):
        delta['reasoning_content'] = msg['reasoning_content']
    if msg.get('content'):
        delta['content'] = msg['content']
    yield dict(base, choices=[{'index': 0, 'delta': delta, 'finish_reason': None}])
    for i, tc in enumerate(msg.get('tool_calls') or []):
        yield dict(base, choices=[{'index': 0, 'delta': {'tool_calls': [dict(tc, index=i)]}, 'finish_reason': None}])
    yield dict(base, choices=[{'index': 0, 'delta': {}, 'finish_reason': choice['finish_reason']}],
               usage=answer['usage'])


class _Body:
    """An HTTPError body already read (to raise it again with its text)."""

    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text.encode()

    def close(self):
        pass


def adaptive(body: dict) -> dict:
    """The request with adaptive thinking: the token budget becomes an effort (low, medium, high)."""
    b = dict(body)
    budget = (b.get('thinking') or {}).get('budget_tokens') or 4096
    b['thinking'] = {'type': 'adaptive'}
    b['output_config'] = {'effort': 'low' if budget <= 2048 else 'medium' if budget <= 8192 else 'high'}
    return b


class Proxy(BaseHTTPRequestHandler):
    upstream = 'https://api.anthropic.com'
    model = None
    auth = 'auto'
    adaptive = False            # set when the upstream model refuses a fixed thinking budget
    dropped = set()             # sampling parameters the upstream model refused
    log = None
    lock = threading.Lock()
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):     # one line per request on stderr
        sys.stderr.write('%s %s\n' % (time.strftime('%H:%M:%S'), fmt % args))

    def _send(self, code, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            name = self.model or 'anthropic'
            return self._send(200, {'object': 'list', 'data': [{'id': name, 'object': 'model', 'owned_by': 'proxy'}]})
        return self._send(404, {'error': {'message': 'not found', 'type': 'invalid_request_error'}})

    def _key(self):
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not key:
            h = self.headers.get('authorization', '')
            key = h[7:] if h.lower().startswith('bearer ') else ''
        return '' if key == 'EMPTY' else key

    def _call(self, body: dict, key: str, mode: str):
        headers = {'content-type': 'application/json', 'anthropic-version': '2023-06-01'}
        if mode == 'bearer':
            headers['authorization'] = f'Bearer {key}'
        else:
            headers['x-api-key'] = key
        r = urllib.request.Request(self.upstream.rstrip('/') + '/v1/messages', data=json.dumps(body).encode(),
                                   headers=headers, method='POST')
        with urllib.request.urlopen(r, timeout=600) as resp:
            return json.loads(resp.read())

    def _adapting(self, body: dict, key: str, mode: str, tries: int = 5):
        """(answer, body sent): the call, adapted to what the upstream model refuses, at most `tries` times. A refused
        sampling parameter ('`temperature` is deprecated for this model') is dropped from this and later requests; a
        refused thinking budget becomes adaptive thinking with an effort."""
        for _ in range(tries):
            try:
                return self._call(body, key, mode), body
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors='replace')
                refused = [k for k in SAMPLING if k in body and f'`{k}`' in detail]
                if e.code == 400 and refused:
                    Proxy.dropped.update(refused)
                    body = {k: v for k, v in body.items() if k not in refused}
                elif e.code == 400 and 'thinking' in body and 'adaptive' in detail and \
                        body['thinking'].get('type') != 'adaptive':
                    Proxy.adaptive = True
                    body = adaptive(body)
                else:
                    raise urllib.error.HTTPError(e.url, e.code, e.msg, e.hdrs, _Body(detail)) from None
        return self._call(body, key, mode), body

    def do_POST(self):
        if not self.path.rstrip('/').endswith('/chat/completions'):
            return self._send(404, {'error': {'message': f'unsupported path {self.path}', 'type': 'invalid_request_error'}})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        except json.JSONDecodeError as e:
            return self._send(400, {'error': {'message': f'bad JSON: {e}', 'type': 'invalid_request_error'}})
        body = to_anthropic(req, self.model)
        key = self._key()
        t0 = time.time()
        modes = ['x-api-key', 'bearer'] if self.auth == 'auto' else [self.auth]
        resp, err = None, None
        if 'thinking' in body and Proxy.adaptive:
            body = adaptive(body)
        body = {k: v for k, v in body.items() if k not in Proxy.dropped}
        for mode in modes:
            try:
                resp, body = self._adapting(body, key, mode)
                if self.auth == 'auto':
                    Proxy.auth = mode          # remember the mode that worked
                break
            except urllib.error.HTTPError as e:
                err = (e.code, e.fp.text if isinstance(e.fp, _Body) else e.read().decode(errors='replace'))
                if e.code not in (401, 403):
                    break
            except Exception as e:  # noqa: BLE001 - network errors become a 502 the client retries
                err = (502, f'{type(e).__name__}: {e}')
                break
        if self.log:
            with self.lock, open(self.log, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps({'t': t0, 'secs': round(time.time() - t0, 2), 'request': req, 'upstream': body,
                                     'response': resp, 'error': err}) + '\n')
        if resp is None:
            code, detail = err
            try:
                msg = json.loads(detail).get('error', {}).get('message', detail)
            except (json.JSONDecodeError, AttributeError):
                msg = detail
            return self._send(code, {'error': {'message': msg, 'type': 'upstream_error', 'code': code}})
        answer = to_openai(resp, req.get('model') or body['model'])
        if not req.get('stream'):
            return self._send(200, answer)
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.send_header('cache-control', 'no-cache')
        self.send_header('connection', 'close')
        self.end_headers()
        for chunk in sse_chunks(answer):
            self.wfile.write(f'data: {json.dumps(chunk)}\n\n'.encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--upstream', default='https://api.anthropic.com')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8790)
    ap.add_argument('--model', default=None, help='the upstream model for every request (default: the request\'s)')
    ap.add_argument('--auth', choices=['auto', 'x-api-key', 'bearer'], default='auto')
    ap.add_argument('--log', default=None, help='append every request and answer as JSON lines')
    ap.add_argument('--parallel', action='store_true', help='allow several tool calls in one turn (default: one)')
    args = ap.parse_args()
    global PARALLEL
    PARALLEL = args.parallel
    Proxy.upstream, Proxy.model, Proxy.auth, Proxy.log = args.upstream, args.model, args.auth, args.log
    srv = ThreadingHTTPServer((args.host, args.port), Proxy)
    print(f'OpenAI-style endpoint http://{args.host}:{args.port}/v1 -> {args.upstream}/v1/messages', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
