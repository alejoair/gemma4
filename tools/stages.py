import ast, json, re, sys, glob, os, csv
S = sys.argv[1]
tasks = {json.loads(l)['instance_id']: json.loads(l) for l in open(f'{S}/comp/tasks.jsonl')}

def owners(repo, rel, lines):
    try:
        src = open(os.path.join(repo, rel)).read()
        tree = ast.parse(src)
    except Exception:
        return {f'{rel}::?'}
    spans = []
    def walk(node, prefix=''):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = prefix + ch.name
                start = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                spans.append((start, ch.end_lineno, q))
                walk(ch, q + '.')
    walk(tree)
    out = set()
    for n in lines:
        inner = [s for s in spans if s[0] <= n <= s[1]]
        out.add(min(inner, key=lambda s: s[1] - s[0])[2] if inner else '<top>')
    return out

def patch_funcs(repo, patch):
    res = set()
    for fpatch in re.split(r'(?m)^diff --git ', patch):
        m = re.search(r'(?m)^--- a/(\S+)', fpatch)
        if not m: continue
        rel = m.group(1)
        if '/tests/' in '/' + rel or rel.startswith('tests'): continue
        lines = []
        for h in re.finditer(r'(?m)^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@.*\n((?:[ +\-\\].*\n?)*)', fpatch):
            ln = int(h.group(1))
            for l in h.group(3).split('\n'):
                if l.startswith('-'): lines.append(ln); ln += 1
                elif l.startswith('+'): lines.append(max(ln - 1, 1))
                elif l.startswith(' '): ln += 1
        res |= {f'{rel}::{q}' for q in owners(repo, rel, lines)}
    return res

def norm(q):   # compare by the last two name parts
    return '.'.join(q.split('::')[-1].split('.')[-2:])

rows = []
for tdir in sorted(glob.glob(f'{S}/r_*/results/traces')):
    run = tdir.split('/r_')[1].split('/')[0]
    csvp = f'{S}/r_{run}/run.csv'
    res = {}
    if os.path.exists(csvp):
        for r in csv.DictReader(open(csvp)): res[r['task_id']] = r
    for tf in glob.glob(f'{tdir}/trace_*.json'):
        tid = tf.split('trace_')[1][:-5]
        if tid not in tasks or tid.startswith('requests'): pass
        repo = f'{S}/repos/{tid}'
        if not os.path.isdir(repo): continue
        ref = {norm(x) for x in patch_funcs(repo, tasks[tid]['patch'])}
        d = json.load(open(tf))
        texts = []
        for s in d['steps']:
            ob = s.get('observation')
            if isinstance(ob, dict) and ob.get('content'):
                c = ob['content']
                try:
                    c = json.loads(c).get('stdout', c)
                except Exception:
                    pass
                texts.append(str(c))
        blob = '\n'.join(texts)
        cand = set(norm(x) for x in re.findall(r'(?m)^\s{2}([\w.]+(?:::[\w.]+)?)\s{2}\(', blob))
        places = set(norm(x) for x in re.findall(r'(?m)^\s{2}([\w./]+(?:::[\w.]+)?(?: top level)?) \([^)]*\) — ', blob))
        pf = f'{S}/r_{run}/patch_{tid}.diff'
        agent = {norm(x) for x in patch_funcs(repo, open(pf).read())} if os.path.exists(pf) else set()
        r = res.get(tid, {})
        rows.append((run, tid, sorted(ref), bool(ref & cand), bool(ref & places), sorted(agent), bool(ref & agent) if agent else False, r.get('resolved', '?'), r.get('error', '')[:20]))
for row in rows:
    print(' | '.join(str(x) for x in row))
