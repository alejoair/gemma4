"""trace_calls.py <trace.json> [-f]: every call of a harness trace with the end of what the model said before it and the start of the answer."""
import json,sys,re
d=json.load(open(sys.argv[1]))
full='-f' in sys.argv
for s in d['steps']:
    if s.get('source')!='agent': continue
    msg=(s.get('message') or '').replace('\n',' ')
    for tc in s.get('tool_calls') or []:
        a=tc.get('arguments',{})
        args=a.get('args') if isinstance(a,dict) else a
        print(f"#{s['step_id']} {tc.get('function_name')} {json.dumps(args)[:200]}")
        print('   SAYS:', msg[-350:])
    o=s.get('observation',{}).get('content','')
    try: o=json.loads(o).get('stdout',o)
    except Exception: pass
    print('   ->', str(o)[:(3000 if full else 250)].replace('\n',' | '))
