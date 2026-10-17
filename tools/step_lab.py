"""step_lab.py: run one agent of the fast build live against a model, on a prepared state, and show everything.

The question it answers: what does the model see in this step, what does it decide, and what does the script answer?
It is a model run of one step (docs/design_fast.md, "A test bench per step"), not a replay: the agent under test is
driven by the model; the agents before it are driven by fixed calls from a case file, so every sample starts from
the same state.

    python tools/step_lab.py --case cases/specify/fastapi_14419.json --build <dir>/submission \
        --api-base http://127.0.0.1:8790/v1 --model claude-haiku-5-5 --samples 3 --out <dir>

A case file (JSON) names the task and the agents in order. An agent with "calls" is driven by those calls (each
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

--instruction <file> replaces the agent's instruction (the same {problem_description} placeholder) to test a new
wording on the same state. Each sample starts from the snapshot (git checkout + clean, the harness's test setup)
and an empty journal. Output: the whole transcript of each sample on stdout, and with --out a JSONL of every
request and answer plus report.md.
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

    def reset(self):
        subprocess.run(['git', 'checkout', '-q', '.'], cwd=self.repo)
        subprocess.run(['git', 'clean', '-qfd'], cwd=self.repo)
        hermetic(str(self.repo))
        self.state = tempfile.mkdtemp(prefix='lab_state_')

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


def show(title, body, width=110):
    print(f'\n--- {title} ' + '-' * max(0, width - len(title) - 5))
    print(body.rstrip())


def drive(agent, ws, scripts, statement, context, args, log, max_turns):
    """The model drives one agent until it answers without a tool call. Returns (final text, calls)."""
    messages = [{'role': 'system', 'content': agent.system(statement)},
                {'role': 'user', 'content': [{'type': 'text', 'text': 'For context:'},
                                             {'type': 'text', 'text': context}]}]
    made = []
    for turn in range(1, max_turns + 1):
        text, thoughts, calls, usage, secs = complete(args.api_base, args.key, args.model, messages,
                                                      agent.sampling)
        log({'agent': agent.name, 'turn': turn, 'request_messages': len(messages), 'thoughts': thoughts,
             'text': text, 'calls': calls, 'usage': usage, 'secs': round(secs, 1)})
        tok = usage.get('completion_tokens', '?')
        if thoughts:
            show(f'{agent.name} turn {turn}: thinking ({len(thoughts)} chars, {secs:.0f} s, {tok} tokens out)',
                 thoughts)
        if text:
            show(f'{agent.name} turn {turn}: text', text)
        if not calls:
            return text, made
        ids = [c['id'] or f'call_{turn}_{i}' for i, c in enumerate(calls)]
        messages.append({'role': 'assistant', 'tool_calls': [
            {'type': 'function', 'id': ids[i], 'function': {'name': c['name'], 'arguments': c['arguments']}}
            for i, c in enumerate(calls)]})
        for i, c in enumerate(calls):
            try:
                a = json.loads(c['arguments'] or '{}')
            except json.JSONDecodeError:
                a = None
            if c['name'] == 'run_skill_script' and isinstance(a, dict) and a.get('skill_name') == SKILL \
                    and a.get('file_path'):
                cargs = a.get('args') or []
                cargs = cargs if isinstance(cargs, list) else [json.dumps(cargs)]
                res = ws.run(scripts, a['file_path'], cargs)
                made.append([a['file_path'], cargs])
            elif c['name'] == 'run_skill_script' and isinstance(a, dict) and not a.get('file_path'):
                res = {'error': "Argument 'file_path' is required"}
            elif c['name'] == 'run_skill_script' and isinstance(a, dict):
                res = {'error': f"Skill '{a.get('skill_name')}' not found."}
            elif c['name'] == 'load_skill':
                res = {'skill_name': SKILL, 'instructions': (Path(scripts).parent / 'SKILL.md').read_text()}
            elif c['name'] == 'list_skills':
                res = {'skills': [{'name': SKILL}]}
            elif c['name'] == 'load_skill_resource':
                p = Path(scripts).parent / str((a or {}).get('file_path', ''))
                res = {'content': p.read_text()} if p.is_file() else {'error': 'Resource not found.'}
            else:
                res = {'error': f"Tool '{c['name']}' not found."}
            show(f'{agent.name} turn {turn}: call {c["name"]}', json.dumps(a, ensure_ascii=False, indent=1)
                 if a is not None else c['arguments'])
            show(f'{agent.name} turn {turn}: answer', res.get('stdout') or json.dumps(res, ensure_ascii=False)
                 + ('\n[stderr] ' + res['stderr'] if res.get('stderr') else ''))
            log({'agent': agent.name, 'turn': turn, 'call': c['name'], 'args': a, 'result': res})
            messages.append({'role': 'tool', 'tool_call_id': ids[i], 'content': json.dumps(res, ensure_ascii=False)})
    return '(max turns)', made


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--case', required=True)
    ap.add_argument('--build', required=True, help='the submission directory (agent.yaml, sub_agents/, skills/)')
    ap.add_argument('--data', default=os.environ.get('LAB_DATA', ''), help='holds tasks.jsonl and snapshots/')
    ap.add_argument('--repos', default=os.environ.get('LAB_REPOS', ''), help='where snapshots are unpacked')
    ap.add_argument('--api-base', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--key-file', default='', help="a file holding the endpoint's key (never the repository)")
    ap.add_argument('--live', default='', help='agents driven by the model even if the case gives calls')
    ap.add_argument('--instruction', action='append', default=[],
                    help='<agent>=<file>: a replacement instruction for that agent')
    ap.add_argument('--samples', type=int, default=1)
    ap.add_argument('--max-turns', type=int, default=12)
    ap.add_argument('--out', default='')
    args = ap.parse_args()
    args.key = Path(args.key_file).read_text().strip() if args.key_file else ''
    case = json.loads(Path(args.case).read_text())
    task = next(json.loads(l) for l in open(Path(args.data) / 'tasks.jsonl')
                if json.loads(l)['instance_id'] == case['task'])
    statement = task['problem_statement']
    build = Path(args.build).resolve()
    scripts = build / 'skills' / SKILL / 'scripts'
    live = {x for x in args.live.split(',') if x}
    repl = dict(x.split('=', 1) for x in args.instruction)
    ws = Workspace(args.data, case['task'], Path(args.repos).resolve() if args.repos
                   else tempfile.mkdtemp(prefix='lab_repos_'))
    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    report = [f'# step_lab: {case["task"]}, {args.model}, {args.samples} sample(s)', '']
    for n in range(1, args.samples + 1):
        print(f'\n{"=" * 110}\n== sample {n}/{args.samples}: {case["task"]}\n{"=" * 110}')
        ws.reset()
        logf = open(out / f'sample{n}.jsonl', 'w') if out else None

        def log(rec):
            if logf:
                logf.write(json.dumps(rec, ensure_ascii=False) + '\n')
                logf.flush()
        context = case.get('context', '[requirements] said: done')
        report.append(f'## Sample {n}')
        for spec in case['agents']:
            name = spec['agent']
            if spec.get('calls') and name not in live:
                for fp, a in spec['calls']:
                    res = ws.run(scripts, fp, a)
                    print(f'\n[fixed] {name}: {fp} {json.dumps(a, ensure_ascii=False)[:200]}')
                    print('   -> ' + (res.get('stdout') or json.dumps(res))[:600].replace('\n', '\n      '))
                    log({'agent': name, 'fixed': [fp, a], 'result': res})
                context = f'[{name}] said: {spec.get("says", "done")}'
                continue
            agent = Agent(build, name, repl.get(name))
            if n == 1:
                show(f'{name}: system instruction ({len(agent.system(statement))} chars)', agent.system(statement))
                show(f'{name}: sampling', json.dumps(agent.sampling))
                if out:
                    (out / f'system_{name}.md').write_text(agent.system(statement))
            final, made = drive(agent, ws, scripts, statement, context, args, log, args.max_turns)
            report.append(f'- **{name}**: ' + '; '.join(f'`{fp} {json.dumps(a, ensure_ascii=False)[:300]}`'
                                                        for fp, a in made) + f' → final: {final[:200]!r}')
            context = f'[{name}] said: {final}'
        diff = subprocess.run(['git', 'diff'], cwd=ws.repo, capture_output=True, text=True).stdout
        report.append(f'- diff: {len(diff)} chars')
        if logf:
            logf.close()
    print('\n' + '\n'.join(report))
    if out:
        (out / 'report.md').write_text('\n'.join(report) + '\n')


if __name__ == '__main__':
    main()
