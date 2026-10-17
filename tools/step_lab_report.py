"""step_lab_report.py <session dir> [<session dir> ...]: one row per model turn of step_lab sessions.

For each turn: the prompt size, the output tokens, whether the thought was cut at the budget, how much of the thought
is code copied from the window (fenced blocks), how often it restarts ('Wait', re-reading), the decision it sent and
how the script took it (saved, refused, sent on). The thought's last 300 characters show where it was when it was cut.
It reads the sessions' log.jsonl only.
"""
import json
import re
import sys
from pathlib import Path

CUT = 0.98          # an output at 98% of the budget plus the call is taken as cut


def kind(args):
    if args is None:
        return 'no call'
    if args == ['open']:
        return 'open'
    first = str(args[0]).strip().lower() if args else ''
    if first.startswith('no change'):
        return 'no change' + (' + line' if len(args) > 1 else '')
    if first == 'missing':
        return 'missing ' + str(args[1] if len(args) > 1 else '')[:40]
    return {1: 'sentence', 2: 'finding', 3: 'finding + sentence'}.get(len(args), f'{len(args)} items')


def answer_kind(stdout):
    s = stdout or ''
    for pat, k in (("names no line|names a line that is not|None of these lines", 'refused'),
                   ('already a place of the fix', 'refused (planned place)'),
                   ('no place of the fix changes', 'empty-patch warning'),
                   ('Saved for', 'saved'), ('Saved: these lines', 'finding saved'),
                   ('keeps its code', 'no change saved'), ('goes into the plan', 'added to the plan'),
                   ('time is up', 'time up'), ('Your part is', 'part done')):
        if re.search(pat, s):
            return k
    return s[:40].replace('\n', ' ')


def report(session):
    rows = []
    recs = [json.loads(l) for l in open(Path(session) / 'log.jsonl')]
    budget = json.loads((Path(session) / 'session.json').read_text())
    for i, r in enumerate(recs):
        if 'usage' not in r:
            continue
        calls = r.get('calls') or []
        try:
            args = json.loads(calls[0]['arguments']).get('args') if calls else None
        except (ValueError, KeyError):
            args = ['(malformed)']
        result = next((x.get('result') for x in recs[i + 1:i + 2] if 'result' in x), None) or {}
        t = r.get('thoughts') or ''
        code = sum(len(m) for m in re.findall(r'```.*?(?:```|$)', t, re.S))
        out = (r['usage'] or {}).get('completion_tokens', 0)
        rows.append({
            'turn': r['turn'], 'prompt': (r['usage'] or {}).get('prompt_tokens', 0), 'out': out,
            'cut': out >= 1024 * CUT, 'thought_chars': len(t), 'code_pct': round(100 * code / len(t)) if t else 0,
            'waits': len(re.findall(r'\bWait\b', t)), 'rereads': len(re.findall(r're-read|look again', t, re.I)),
            'decision': kind(args), 'answer': 'window' if args == ['open'] else answer_kind(result.get('stdout')),
            'tail': ' '.join(t[-300:].split()), 'args': json.dumps(args, ensure_ascii=False)[:400]})
    return rows


def main():
    for s in sys.argv[1:]:
        rows = report(s)
        print(f'\n## {Path(s).name}\n')
        print('| turn | prompt | out | cut | code % | Wait | decision | script |')
        print('|---|---|---|---|---|---|---|---|')
        for r in rows:
            print(f"| {r['turn']} | {r['prompt']} | {r['out']} | {'yes' if r['cut'] else ''} | {r['code_pct']} | "
                  f"{r['waits']} | {r['decision']} | {r['answer']} |")
        for r in rows:
            if r['decision'] != 'open':
                print(f"\n- turn {r['turn']}: {r['args']}\n  thought ends: …{r['tail']}")


if __name__ == '__main__':
    main()
