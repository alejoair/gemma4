"""step_lab.py: run one agent of the fast build live against a model, on a prepared state, and show everything.

The question it answers: what does the model see in this step, what does it decide, and what does the script answer?
It is a model run of one step (docs/design_fast.md, "A test bench per step"), not a replay: the agent under test is
driven by the model; the agents before it are driven by fixed calls from a case file, so every sample starts from
the same state.

    python tools/step_lab.py start <session dir> --case tools/lab_cases/specify/fastapi_14419.json \
        --build <dir>/submission --data <comp dir> --repos <dir> --api-base <url>/v1 --model <name>
    python tools/step_lab.py next <session dir>      # one model call, its tool calls run, then it stops

It goes step by step (the user's, 10-17): 'start' resets the snapshot, runs the fixed agents and stops before the
first model call; each 'next' makes exactly one model call, runs the tool calls it makes, prints the thinking, the
calls and the script's answers whole, and stops, so each answer is read before the next call.

A case file (JSON) names the task and the agents in order. "context" on an agent the model drives sets the
message it gets after "For context:" (an agent after a ParallelAgent stage sees the last sequential agent's, as in
the ADK requests logged on 10-17: specify sees "[requirements] said: done"). An agent with "calls" is driven by those calls (each
[script, [args...]]); an agent without calls, or named in --live, is driven by the model:

    {"task": "fastapi_14419",
     "agents": [
       {"agent": "requirements", "calls": [["req.py", ["<quote from the issue>", "<requirement>"]]], "says": "done"},
       {"agent": "locate1", "calls": [["locate1.py", ["open"]], ["locate1.py", ["solve_dependencies"]]]},
       {"agent": "specify1"}]}

The request is the one the ADK sends (checked against the requests the Anthropic proxy logged on 10-17): the
agent's instruction from the build with {problem_description} filled, the ADK's own text after it, the four skill
tools, the "For context:" user message, and the sampling of the agent's yaml (temperature, top_p, top_k, output
tokens, thinking budget). A tool result is the ADK's dict (skill_name, file_path, stdout, stderr, status). Earlier
thoughts are not sent back, as the ADK does not send them.

--instruction <agent>=<file> replaces the agent's instruction (the same {problem_description} placeholder) to test
a new wording on the same state. A session starts from the snapshot (git checkout + clean, the harness's test
setup) and an empty journal; <session dir> keeps session.json (the conversation so far), log.jsonl (every model
answer and script answer) and the system instruction of each agent the model drives.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermetic import hermetic  # noqa: E402

SKILL = 'fix-issue'
# the text the ADK appends to every agent's instruction when the agent has skills (google-adk 2.7, the
# Anthropic proxy's request log of 10-17); {name} and {description} are the agent's own
ADK_TAIL = '''

You are an agent. Your internal name is "{name}". The description about you is "{description}".

You can use specialized 'skills' to help you with complex tasks. You MUST use the skill tools to interact with these skills.

Skills are folders of instructions and resources that extend your capabilities for specialized tasks. Each skill folder contains:
- **SKILL.md** (required): The main instruction file with skill metadata and detailed markdown instructions.
- **references/** (Optional): Additional documentation or examples for skill usage.
- **assets/** (Optional): Templates, scripts or other resources used by the skill.
- **scripts/** (Optional): Executable scripts that can be run via bash.

This is very important:

1. If a skill seems relevant to the current user query, you MUST use the `load_skill` tool with `skill_name="<SKILL_NAME>"` to read its full instructions before proceeding.
2. Once you have read the instructions, follow them exactly as documented before replying to the user. For example, If the instruction lists multiple steps, please make sure you complete all of them in order.
3. The `load_skill_resource` tool is for viewing files within a skill's directory (e.g., `references/*`, `assets/*`, `scripts/*`). Do NOT use other tools to access these files.
4. Use `run_skill_script` to run scripts from a skill's `scripts/` directory. Use `load_skill_resource` to view script content first if needed.
'''
TOOLS = [
    {'type': 'function', 'function': {
        'name': 'list_skills', 'description': 'Lists all available skills with their names and descriptions.',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {
        'name': 'load_skill', 'description': 'Loads the SKILL.md instructions for a given skill.',
        'parameters': {'type': 'object', 'properties': {
            'skill_name': {'type': 'string', 'description': 'The name of the skill to load.'}},
            'required': ['skill_name']}}},
    {'type': 'function', 'function': {
        'name': 'load_skill_resource',
        'description': 'Loads a resource file (from references/, assets/, or scripts/) from within a skill.',
        'parameters': {'type': 'object', 'properties': {
            'skill_name': {'type': 'string', 'description': 'The name of the skill.'},
            'file_path': {'type': 'string', 'description': "The relative path to the resource (e.g., "
                          "'references/my_doc.md', 'assets/template.txt', or 'scripts/setup.sh')."}},
            'required': ['skill_name', 'file_path']}}},
    {'type': 'function', 'function': {
        'name': 'run_skill_script', 'description': "Executes a script from a skill's scripts/ directory.",
        'parameters': {'type': 'object', 'properties': {
            'skill_name': {'type': 'string', 'description': 'The name of the skill.'},
            'file_path': {'type': 'string',
                          'description': "The relative path to the script (e.g., 'scripts/setup.py')."},
            'args': {'anyOf': [{'type': 'object'}, {'type': 'array', 'items': {'type': 'string'}}],
                     'description': 'Arguments for the script.'}},
            'required': ['skill_name', 'file_path']}}},
]


def yaml_values(path):
    """The flat key: value pairs of a small yaml (the build's agent and sampling files)."""
    out = {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r'\s*(\w+):\s*(.*?)\s*$', line)
        if m and m.group(2) and not m.group(2).startswith('#'):
            out[m.group(1)] = m.group(2)
    return out


class Agent:
    def __init__(self, build, name, instruction=None):
        sub = Path(build) / 'sub_agents'
        y = yaml_values(sub / f'{name}.yaml')
        self.name = name
        self.description = y.get('description', '')
        inc = re.match(r'!include\s+(\S+)', y.get('instruction', ''))
        self.instruction = Path(instruction).read_text() if instruction else (sub / inc.group(1)).read_text()
        s = yaml_values(sub / re.match(r'!include\s+(\S+)', y['generate_content_config']).group(1))
        self.sampling = {
            'max_completion_tokens': int(s.get('max_output_tokens', 8192)),
            'temperature': float(s.get('temperature', 1.0)),
            'top_p': float(s.get('top_p', 0.95)),
            'top_k': float(s.get('top_k', 64)),
        }
        budget = int(s.get('thinking_budget', 0))
        self.sampling['chat_template_kwargs'] = {'enable_thinking': budget > 0}
        if budget > 0:
            self.sampling['thinking_token_budget'] = budget

    def system(self, statement):
        return (self.instruction.replace('{problem_description}', statement)
                + ADK_TAIL.format(name=self.name, description=self.description))


class Workspace:
    """The task's snapshot as a git repository, reset before each sample, and the journal directory."""

    def __init__(self, data, task, root):
        self.repo = Path(root) / task
        if not (self.repo / '.git').exists():
            tgz = Path(data) / 'snapshots' / f'{task}.tgz'
            if not tgz.exists():
                subprocess.run(['kaggle', 'competitions', 'download', 'gemma-4-developer-agent', '-f',
                                f'snapshots/{task}.tgz', '-p', str(tgz.parent), '-q'], check=True)
            self.repo.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tgz) as t:
                t.extractall(self.repo, filter='data')
        self.state = None

    def reset(self, state):
        subprocess.run(['git', 'checkout', '-q', '.'], cwd=self.repo)
        subprocess.run(['git', 'clean', '-qfd'], cwd=self.repo)
        hermetic(str(self.repo))
        self.state = state

    def run(self, scripts, file_path, args):
        """The ADK's result dict for one run_skill_script call."""
        script = Path(scripts) / os.path.basename(file_path or '')
        if not script.exists():
            return {'error': f"Script '{file_path}' not found in skill '{SKILL}'."}
        env = dict(os.environ, SWE_REPO=str(self.repo), SWE_STATE_ROOT=self.state, PWD=str(self.repo),
                   PYTHONDONTWRITEBYTECODE='1')
        try:
            r = subprocess.run([sys.executable, str(script)] + [str(a) for a in args], cwd=self.repo, env=env,
                               capture_output=True, text=True, timeout=600)
            out, err = r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            out, err = e.stdout or '', 'Timed out after 600s'
        status = 'error' if err and not out else 'warning' if err else 'success'
        return {'skill_name': SKILL, 'file_path': file_path, 'stdout': out, 'stderr': err, 'status': status}


def complete(api_base, key, model, messages, sampling, timeout=900):
    """One streamed chat completion: (text, thoughts, tool calls, usage, seconds)."""
    body = dict(sampling, model=model, messages=messages, tools=TOOLS, stream=True,
                stream_options={'include_usage': True})
    req = urllib.request.Request(api_base.rstrip('/') + '/chat/completions', data=json.dumps(body).encode(),
                                 method='POST', headers={'content-type': 'application/json',
                                                         'authorization': f'Bearer {key or "EMPTY"}',
                                                         'user-agent': 'swe-monitor'})
    text, thoughts, calls, usage = [], [], {}, {}
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            ev = json.loads(data)
            if ev.get('error'):
                raise RuntimeError(json.dumps(ev['error'])[:500])
            usage = ev.get('usage') or usage
            for ch in ev.get('choices') or []:
                d = ch.get('delta') or {}
                if d.get('content'):
                    text.append(d['content'])
                for k in ('reasoning_content', 'reasoning'):
                    if d.get(k):
                        thoughts.append(d[k])
                for tc in d.get('tool_calls') or []:
                    c = calls.setdefault(tc.get('index', 0), {'id': '', 'name': '', 'arguments': ''})
                    c['id'] = tc.get('id') or c['id']
                    f = tc.get('function') or {}
                    c['name'] += f.get('name') or ''
                    c['arguments'] += f.get('arguments') or ''
    return ''.join(text), ''.join(thoughts), [calls[i] for i in sorted(calls)], usage, time.time() - t0


OUT = []      # what a step prints, held until the session is saved (a closed pipe must not lose a step)


def show(title, body, width=110):
    OUT.append(f'\n--- {title} ' + '-' * max(0, width - len(title) - 5) + '\n' + body.rstrip())


def tool_result(ws, scripts, c):
    """(parsed arguments, the ADK's answer) for one tool call of the model."""
    try:
        a = json.loads(c['arguments'] or '{}')
    except json.JSONDecodeError:
        a = None
    if c['name'] == 'run_skill_script' and isinstance(a, dict) and a.get('skill_name') == SKILL \
            and a.get('file_path'):
        cargs = a.get('args') or []
        return a, ws.run(scripts, a['file_path'], cargs if isinstance(cargs, list) else [json.dumps(cargs)])
    if c['name'] == 'run_skill_script' and isinstance(a, dict) and not a.get('file_path'):
        return a, {'error': "Argument 'file_path' is required"}
    if c['name'] == 'run_skill_script' and isinstance(a, dict):
        return a, {'error': f"Skill '{a.get('skill_name')}' not found."}
    if c['name'] == 'load_skill':
        return a, {'skill_name': SKILL, 'instructions': (Path(scripts).parent / 'SKILL.md').read_text()}
    if c['name'] == 'list_skills':
        return a, {'skills': [{'name': SKILL}]}
    if c['name'] == 'load_skill_resource':
        p = Path(scripts).parent / str((a or {}).get('file_path', ''))
        return a, ({'content': p.read_text()} if p.is_file() else {'error': 'Resource not found.'})
    return a, {'error': f"Tool '{c['name']}' not found."}


def shift_clock(state, seconds):
    """Moves the task's start time (t0 of each journal under state) later by seconds: the time between two lab steps
    (the reader's) is not the agent's, and the scripts' 5-minute budget counts only model and script time."""
    for f in Path(state).glob('*/stages.json'):
        d = json.loads(f.read_text())
        if isinstance(d, dict) and 't0' in d:
            d['t0'] += seconds
            f.write_text(json.dumps(d))


class Session:
    """A step-by-step run kept in <dir>/session.json: each 'next' makes one model call and stops."""

    def __init__(self, path):
        self.path = Path(path)
        self.s = json.loads((self.path / 'session.json').read_text())
        self.case = self.s['case']
        task = next(json.loads(l) for l in open(Path(self.s['data']) / 'tasks.jsonl')
                    if json.loads(l)['instance_id'] == self.case['task'])
        self.statement = task['problem_statement']
        self.scripts = Path(self.s['build']) / 'skills' / SKILL / 'scripts'
        self.ws = Workspace(self.s['data'], self.case['task'], self.s['repos'])
        self.ws.state = self.s['state']

    def save(self):
        self.s['idle_from'] = time.time()
        (self.path / 'session.json').write_text(json.dumps(self.s, ensure_ascii=False, indent=1))

    def log(self, rec):
        with open(self.path / 'log.jsonl', 'a') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    def agent(self):
        spec = self.case['agents'][self.s['index']]
        return Agent(self.s['build'], spec['agent'], self.s['instructions'].get(spec['agent']))

    def advance(self):
        """Runs the fixed agents from the current one on, up to the next agent the model drives."""
        agents = self.case['agents']
        while self.s['index'] < len(agents):
            spec = agents[self.s['index']]
            if spec.get('calls') and spec['agent'] not in self.s['live']:
                for fp, a in spec['calls']:
                    res = self.ws.run(self.scripts, fp, a)
                    OUT.append(f'\n[fixed] {spec["agent"]}: {fp} {json.dumps(a, ensure_ascii=False)[:300]}\n   -> '
                               + (res.get('stdout') or json.dumps(res))[:800].replace('\n', '\n      '))
                    self.log({'agent': spec['agent'], 'fixed': [fp, a], 'result': res})
                self.s['context'] = f'[{spec["agent"]}] said: {spec.get("says", "done")}'
                self.s['index'] += 1
                continue
            agent = self.agent()
            system = agent.system(self.statement)
            self.s['messages'] = [{'role': 'system', 'content': system},
                                  {'role': 'user', 'content': [{'type': 'text', 'text': 'For context:'},
                                                               {'type': 'text', 'text': spec.get('context')
                                                                or self.s['context']}]}]
            self.s['turn'] = 0
            (self.path / f'system_{agent.name}.md').write_text(system)
            show(f'{agent.name}: driven by the model; its system instruction is in system_{agent.name}.md '
                 f'({len(system)} chars); first message', self.s['messages'][1]['content'][1]['text'])
            show(f'{agent.name}: sampling', json.dumps(agent.sampling))
            return
        diff = subprocess.run(['git', 'diff'], cwd=self.ws.repo, capture_output=True, text=True).stdout
        show('every agent of the case is done; the diff', diff or '(empty)')
        self.s['messages'] = None

    def next(self):
        if self.s['messages'] is None:
            OUT.append('The case is finished: start a new session.')
            return
        if self.s.get('idle_from'):
            shift_clock(self.s['state'], time.time() - self.s['idle_from'])
        agent = self.agent()
        self.s['turn'] += 1
        turn = self.s['turn']
        msgs = self.s['messages']
        text, thoughts, calls, usage, secs = complete(self.s['api_base'], self.s['key'], self.s['model'], msgs,
                                                      agent.sampling)
        self.log({'agent': agent.name, 'turn': turn, 'thoughts': thoughts, 'text': text, 'calls': calls,
                  'usage': usage, 'secs': round(secs, 1)})
        head = f'{agent.name} turn {turn} ({secs:.0f} s, {usage.get("completion_tokens", "?")} tokens out)'
        show(f'{head}: thinking ({len(thoughts)} chars)', thoughts or '(none returned)')
        if text:
            show(f'{head}: text', text)
        if not calls:
            self.s['context'] = f'[{agent.name}] said: {text}'
            self.s['index'] += 1
            OUT.append(f'\n[{agent.name} ended its turn without a call]')
            self.advance()
            return
        ids = [c['id'] or f'call_{turn}_{i}' for i, c in enumerate(calls)]
        msgs.append({'role': 'assistant', 'tool_calls': [
            {'type': 'function', 'id': ids[i], 'function': {'name': c['name'], 'arguments': c['arguments']}}
            for i, c in enumerate(calls)]})
        for i, c in enumerate(calls):
            a, res = tool_result(self.ws, self.scripts, c)
            show(f'{head}: call {c["name"]}', json.dumps(a, ensure_ascii=False, indent=1)
                 if a is not None else c['arguments'])
            show(f'{head}: the script answers', res.get('stdout') or json.dumps(res, ensure_ascii=False)
                 + ('\n[stderr] ' + res['stderr'] if res.get('stderr') else ''))
            self.log({'agent': agent.name, 'turn': turn, 'call': c['name'], 'args': a, 'result': res})
            msgs.append({'role': 'tool', 'tool_call_id': ids[i], 'content': json.dumps(res, ensure_ascii=False)})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    st = sub.add_parser('start', help='reset the snapshot, run the fixed agents, stop before the first model call')
    st.add_argument('session', help='a new directory for this run')
    st.add_argument('--case', required=True)
    st.add_argument('--build', required=True, help='the submission directory (agent.yaml, sub_agents/, skills/)')
    st.add_argument('--data', required=True, help='holds tasks.jsonl and snapshots/')
    st.add_argument('--repos', required=True, help='where snapshots are unpacked')
    st.add_argument('--api-base', required=True)
    st.add_argument('--model', required=True)
    st.add_argument('--key-file', default='', help="a file holding the endpoint's key (never the repository)")
    st.add_argument('--live', default='', help='agents driven by the model even if the case gives calls')
    st.add_argument('--instruction', action='append', default=[],
                    help='<agent>=<file>: a replacement instruction for that agent')
    nx = sub.add_parser('next', help='one model call of the current agent, its tool calls run, then stop')
    nx.add_argument('session')
    args = ap.parse_args()
    if args.cmd == 'start':
        path = Path(args.session)
        path.mkdir(parents=True, exist_ok=False)
        (path / 'state').mkdir()
        s = {'case': json.loads(Path(args.case).read_text()), 'build': str(Path(args.build).resolve()),
             'data': str(Path(args.data).resolve()), 'repos': str(Path(args.repos).resolve()),
             'state': str((path / 'state').resolve()), 'api_base': args.api_base, 'model': args.model,
             'key': Path(args.key_file).read_text().strip() if args.key_file else '',
             'live': [x for x in args.live.split(',') if x],
             'instructions': {k: str(Path(v).resolve()) for k, v in (x.split('=', 1) for x in args.instruction)},
             'index': 0, 'context': '[requirements] said: done', 'messages': None, 'turn': 0}
        (path / 'session.json').write_text(json.dumps(s))
        os.chmod(path / 'session.json', 0o600)        # it may hold the endpoint's key
        ses = Session(path)
        ses.ws.reset(ses.s['state'])
        ses.advance()
        ses.save()
    else:
        ses = Session(args.session)
        try:
            ses.next()
        finally:
            ses.save()
    print('\n'.join(OUT))


if __name__ == '__main__':
    main()
