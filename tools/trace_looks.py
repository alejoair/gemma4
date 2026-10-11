"""trace_looks.py <traces dir>: per task, what each step.py answer was (search, range, choice by name, kept, undone, ...) and the longest run of calls between effective decisions (kept, undone, skip)."""
import json,sys,glob,re,collections
tot=collections.Counter()
for f in sorted(glob.glob(sys.argv[1]+'/trace_*.json')):
    t=f.split('trace_')[1][:-5]; d=json.load(open(f)); c=collections.Counter(); seq=[]
    for s in d['steps']:
        if s.get('source')!='agent': continue
        o=s.get('observation',{}).get('content','') or ''
        try: o=json.loads(o).get('stdout',o) or o
        except Exception: pass
        o=str(o)
        for tc in s.get('tool_calls') or []:
            c['calls']+=1
            if tc['function_name']!='run_skill_script': c[tc['function_name']]+=1; continue
            if '"error"' in o[:30] or 'error_code' in o[:200]: c['malformed']+=1; seq.append('x'); continue
            body='\n'.join(o.split('\n')[3:6])
            k=('search' if 'Where the code holds' in body or 'Nothing in the code holds' in body else
               'range→list' if 'Code is shown through' in body else
               'range in place' if re.search(r'Lines \d+-\d+ are in ',body) else
               'test view' if 'are in tests' in body else
               'chose by name' if ' is chosen: ' in body else
               'kept' if body.startswith('Kept') or '\nKept' in body else
               'undone' if 'Undone:' in body else
               'not applied' if 'Not applied' in body else
               'not run' if 'Not run:' in body else
               'repeat' if 'same call' in body else
               'skip' if 'skipped' in body else
               'time up' if 'Time for edits is over' in body or 'Edits are closed' in body else 'other')
            c[k]+=1; seq.append(k)
    eff=[i for i,k in enumerate(seq) if k in('kept','undone','skip')]
    gaps=[b-a-1 for a,b in zip([-1]+eff,eff)] 
    print(t, dict(c), 'max calls between effective decisions:', max(gaps) if gaps else len(seq))
    tot+=c
print('TOTAL',dict(tot))
