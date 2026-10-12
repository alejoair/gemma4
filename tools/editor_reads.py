"""Classifies every read call of the edit_place editors in a Kaggle trace: what the editor asked for, relative to its
own place, and what it got back."""
import json, re, sys, ast, os, collections
sys.path.insert(0, '/home/user/gemma4/agent/skills/fix-issue/scripts')
import _code
S = sys.argv.pop(1) if len(sys.argv) > 2 and not sys.argv[1].endswith('.json') else '.'   # the scratchpad with repos/<task>

def syms_of(root):
    out = {}
    for dp, _, fs in os.walk(root):
        if '/.git' in dp or '/tests' in dp or '/docs' in dp: continue
        for f in fs:
            if f.endswith('.py'):
                rel = os.path.relpath(os.path.join(dp, f), root)
                src = open(os.path.join(dp, f), errors='replace').read()
                t = _code.parse(src)
                if t is None: continue
                lines = src.split('\n')
                for s in _code.symbols(t):
                    out.setdefault(s.name.split('.')[-1], []).append((rel, s.name, '\n'.join(lines[s.start-1:s.end])))
    return out

def short(n): return re.sub(r'.*::', '', n).strip().split('.')[-1].strip('()')

for tf in sys.argv[1:]:
    task = re.search(r'trace_(.+)\.json', tf).group(1)
    root = f'{S}/repos/{task}'
    table = syms_of(root)
    d = json.load(open(tf))
    own, plan = None, []
    cats = collections.Counter(); got = collections.Counter(); examples = collections.defaultdict(list)
    for s in d['steps']:
        author = json.loads(json.dumps(s.get('extra') or {})).get('author') if isinstance(s.get('extra'), dict) else None
        for tc in s.get('tool_calls') or []:
            if tc.get('function_name') == 'edit_place':
                own = (tc.get('arguments') or {}).get('request')
            if tc.get('function_name') != 'run_skill_script': continue
            a = tc.get('arguments') or {}
            fp, args = a.get('file_path', ''), a.get('args')
            if 'step.py' in fp or not isinstance(args, list): continue
            items = [str(x) for x in args if str(x).strip()]
            obs = s.get('observation', {}).get('content', '') if isinstance(s.get('observation'), dict) else ''
            try: out = json.loads(obs).get('stdout', obs)
            except Exception: out = str(obs)
            m = re.search(r'Open now: ([^.\n]+(?:\.[A-Za-z_][\w]*)*)\. Still to look at: ([^\n]+?)\.\n', out)
            if m: plan = [x.strip() for x in m.group(2).split(',')]
            m2 = re.search(r'Open now: (\S+?)\.(?:\s|$)', out)
            is_edit = (len(items) == 2 and re.search(r'^\s*(@|(async\s+)?def |class )', items[1], re.M)) or len(items) == 4
            if is_edit or (len(items) == 1 and items[0].strip("[]' ").lower() == 'skip'): continue
            if 'open.py' in fp and own and items and short(items[0]) == short(own) and len(items) == 1 and cats['_first_'+own] == 0:
                cats['_first_'+own] += 1; continue      # the editor's opening call
            target = items[0] if items else ''
            if len(items) == 3:
                kind = 'range in own place' if own and short(target) == short(own) else 'range elsewhere'
            elif re.fullmatch(r'[\w./-]+\.py(\s+top level)?', target.strip()) or target.strip() in ('top level',):
                kind = 'a file / top level'
            elif own and short(target) == short(own):
                kind = 'own place again'
            elif any(short(target) == short(p) for p in plan):
                kind = 'another planned place'
            else:
                t = short(target)
                own_src = ''.join(src for rel, n, src in table.get(short(own or ''), []) if own and (n == own.split('::')[-1] or n.endswith(short(own))))
                defs = table.get(t, [])
                if not defs: kind = 'not in the repo (library/invented)'
                elif re.search(r'\b' + re.escape(t) + r'\b', own_src): kind = 'code the place uses'
                elif any(re.search(r'\b' + re.escape(short(own or 'zzz')) + r'\b', src) for _, _, src in defs): kind = 'code that uses the place'
                else: kind = 'other related code'
            cats[kind] += 1
            ans = ('code shown' if re.search(r'(?m)^\s*\d+\|', out) else 'refused: no more code' if 'No more code' in out
                   else 'refused: shown above / open already' if ('shown above' in out or 'open already' in out)
                   else 'nothing found' if 'Nothing in' in out else 'list of names' if 'related to the issue' in out else 'other')
            got[ans] += 1
            if len(examples[kind]) < 4: examples[kind].append(f'{own} -> {items[:3] if len(items)==3 else target[:60]}')
    total = sum(v for k, v in cats.items() if not k.startswith('_first_'))
    print(f'\n=== {task}: {total} reads')
    for k, v in cats.most_common():
        if k.startswith('_first_'): continue
        print(f'  {v:3d}  {k:38s} e.g. {"; ".join(examples[k][:3])}')
    print('  answers:', dict(got))
