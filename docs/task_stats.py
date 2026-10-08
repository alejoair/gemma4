import json,re,statistics as S,collections
T=[json.loads(l) for l in open(__import__('sys').argv[1] if len(__import__('sys').argv) > 1 else 'tasks.jsonl')]
def chunks(p):
    out=[]
    for c in re.split(r'^(?=--- (?:a/|/dev/null))',p,flags=re.M):
        m=re.search(r'^\+\+\+ b/(\S+)',c,re.M)
        if m: out.append((m.group(1),c,c.startswith('--- /dev/null')))
    return out
rows=[]
for t in T:
    ch=chunks(t['patch'])
    src=[(f,c,new) for f,c,new in ch if not re.search(r'(^|/)tests?/|test_',f)]
    code=[x for x in src if x[0].endswith('.py') and not x[0].startswith(('docs','docs_src/'))]
    docs=[x for x in src if x[0].startswith(('docs/','docs_src/')) or not x[0].endswith('.py')]
    add=sum(len([l for l in c.splitlines() if l.startswith('+') and not l.startswith('+++')]) for _,c,_ in code)
    rem=sum(len([l for l in c.splitlines() if l.startswith('-') and not l.startswith('---')]) for _,c,_ in code)
    funcs=set()
    for f,c,_ in code:
        for h in re.findall(r'^@@[^@]*@@\s*(?:async\s+)?(?:def|class)\s+(\w+)',c,re.M): funcs.add((f,h))
        for h in re.findall(r'^[-+]\s*(?:async\s+)?def\s+(\w+)',c,re.M): funcs.add((f,h))
    newdefs=sum(len(re.findall(r'^\+\s*(?:async\s+)?(?:def|class)\s+\w+',c,re.M)) for _,c,_ in code)
    names=collections.Counter(h for _,h in funcs)
    twins=any(v>1 for v in names.values())
    st=t['problem_statement']; words=len(re.sub(r'https?://\S+','',st).split())
    ticks=re.findall(r'`([^`\n]{2,60})`',st)
    gold_named=any(h in st for _,h in funcs if len(h)>3)
    rows.append(dict(id=t['instance_id'],repo=t['repo'],files=len(code),docs=len(docs),newfile=sum(1 for x in code if x[2]),
        lines=add+rem,funcs=len(funcs),newdefs=newdefs,twins=twins,words=words,ticks=len(ticks),
        trace='Traceback' in st,gold_named=gold_named,hints=bool(t['hints_text'].strip())))
def pct(k,f=lambda v:v): return f"{100*sum(1 for r in rows if f(r[k]))/len(rows):.0f}%"
def dist(k): v=sorted(r[k] for r in rows); return f"median {S.median(v)}, p75 {v[int(.75*len(v))]}, p90 {v[int(.9*len(v))]}, max {v[-1]}"
print('tasks',len(rows), collections.Counter(r['repo'] for r in rows))
print('code files changed:', dist('files'), '| 1 file:', pct('files',lambda v:v==1), '| 0 code files:', pct('files',lambda v:v==0))
print('functions touched:', dist('funcs'), '| 1 function:', pct('funcs',lambda v:v==1), '| >=4:', pct('funcs',lambda v:v>=4))
print('code lines +/-:', dist('lines'), '| <=10:', pct('lines',lambda v:v<=10), '| >50:', pct('lines',lambda v:v>50))
print('new def/class:', pct('newdefs',lambda v:v>0), '| new code file:', pct('newfile',lambda v:v>0), '| docs/non-py changed:', pct('docs',lambda v:v>0))
print('same function name changed in 2+ files (twins):', pct('twins',bool))
print('statement words:', dist('words'), '| <25 words:', pct('words',lambda v:v<25))
print('backticked names:', pct('ticks',lambda v:v>0), '| traceback:', pct('trace',bool), '| names a changed function:', pct('gold_named',bool), '| hints_text:', pct('hints',bool))
json.dump(rows,open('/tmp/task_stats.json','w'))
