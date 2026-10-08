"""Localization benchmark, the unit test of _rank.py: for each dev task, are the functions or classes its reference
patch changes among the top-k candidates the statement alone gives?

    python tools/bench_locate.py <tasks.jsonl> <repos dir> [-k 10] [--show] [--only id,id]

<repos dir>/<instance_id>/ holds the task's original Python files (tools/fetch_snapshots.sh). The gold places are the
innermost function or class around each changed line of the original file (for added lines, around the line before
them); module-level changes are '<module>' of their file; new files cannot be found and are counted apart. Test files
are ignored. Target (docs/design_single.md): a gold place in the top 10 for at least 80% of the tasks."""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agent', 'skills', 'swe', 'scripts'))
import _code  # noqa: E402
import _impact  # noqa: E402
import _rank  # noqa: E402
import _repo  # noqa: E402
import _statement  # noqa: E402


def changed_lines(patch, root):
    """{rel: set of original line numbers changed (an indented insertion: the last non-blank line before it; a new
    top-level statement: a negative number, module level)} and the set of new files. The
    patches have no 'diff --git' lines and new files appear as '--- a/x' with '@@ -0,0': files are split on '--- '."""
    out, new = {}, set()
    rel, old, last = None, 0, 0
    lines = patch.split('\n')
    for i, line in enumerate(lines):
        m = re.match(r'--- (?:a/)?(\S+)', line)
        if m and i + 1 < len(lines) and lines[i + 1].startswith('+++ '):
            target = re.match(r'\+\+\+ (?:b/)?(\S+)', lines[i + 1]).group(1)
            rel = target if m.group(1) == '/dev/null' else m.group(1)
            if not rel.endswith('.py') or _repo.is_test_path(rel):
                rel = None
            elif not os.path.isfile(os.path.join(root, rel)):
                new.add(rel)
                rel = None
            continue
        if rel is None or line.startswith('+++ '):
            continue
        h = re.match(r'@@ -(\d+)', line)
        if h:
            old = int(h.group(1))
            last = max(1, old - 1)
            continue
        if line.startswith('-'):
            out.setdefault(rel, set()).add(old)
            last = old if line[1:].strip() else last
            old += 1
        elif line.startswith('+') and line[1:].strip():
            # an added line belongs where the code before it is, unless it starts a new top-level statement
            out.setdefault(rel, set()).add(last if line[1:2] in (' ', '\t') else -old)
        elif line.startswith(' '):
            last = old if line[1:].strip() else last
            old += 1
    return out, new


def gold_places(root, patch):
    changed, new = changed_lines(patch, root)
    gold = set()
    for rel, nums in changed.items():
        text = _repo.read_text(root, rel)
        syms = _code.symbols(_code.parse(text))
        owner = _code.owner_map(syms, len(text.splitlines()))
        for n in nums:
            sym = owner[n] if 0 < n < len(owner) else None   # negative: a new top-level statement
            gold.add((rel, sym.name if sym else '<module>'))
    return gold, new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tasks')
    ap.add_argument('repos')
    ap.add_argument('-k', type=int, default=10)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--only', default='')
    ap.add_argument('--impact', action='store_true',
                    help='also: from each gold place, how many of the other gold places _impact relates to it')
    a = ap.parse_args()
    only = set(filter(None, a.only.split(',')))
    tasks = [json.loads(l) for l in open(a.tasks)]
    rows, impact, t0 = [], [], time.time()
    for task in tasks:
        tid = task['instance_id']
        root = os.path.join(a.repos, tid)
        if (only and tid not in only) or not os.path.isdir(root):
            continue
        gold, new = gold_places(root, task['patch'])
        text = _statement.clean(task['problem_statement'])
        t = time.time()
        ranked = _rank.Index(root).rank(_statement.terms(text), _statement.paths(text), n=a.k)
        took = time.time() - t
        names = [(d.rel, d.name) for _, d, _ in ranked]
        pos = [names.index(g) + 1 for g in gold if g in names]
        files = {g[0] for g in gold}
        if a.impact:
            real = sorted(g for g in gold if g[1] != '<module>')
            if len(real) > 1:
                table = _impact.Table(root, docs=True)
                found = max(len({(p.rel, p.name) for p in _impact.related(table, [g])} & set(real)) - 1 for g in real)
                impact.append((tid, found, len(real) - 1))
        rows.append({'id': tid, 'gold': len(gold), 'new': len(new), 'first': min(pos) if pos else None,
                     'all': bool(gold) and len(pos) == len(gold), 'file': any(n[0] in files for n in names),
                     'secs': took})
        if a.show:
            print(f'== {tid}  first={rows[-1]["first"]}  gold={sorted(gold)}  new={sorted(new)}  {took:.1f}s')
            for i, (s, d, m) in enumerate(ranked, 1):
                mark = '*' if (d.rel, d.name) in gold else ' '
                print(f'  {mark}{i:>2} {s:6.2f} {d.rel} :: {d.name}  [{", ".join(m[:4])}]')
    scored = [r for r in rows if r['gold']]
    n = max(1, len(scored))

    def at(k):
        return sum(1 for r in scored if r['first'] and r['first'] <= k)
    print(f'tasks {len(rows)} (with gold places {len(scored)}, only new files {len(rows) - len(scored)})')
    for k in (1, 3, 5, a.k):
        print(f'  hit@{k}: {at(k)}/{n} = {100 * at(k) / n:.0f}%')
    print(f'  all gold places in top {a.k}: {sum(r["all"] for r in scored)}/{n}')
    print(f'  a gold file in top {a.k}: {sum(r["file"] for r in scored)}/{n}')
    print(f'  seconds per task: mean {sum(r["secs"] for r in rows) / max(1, len(rows)):.2f}, '
          f'max {max((r["secs"] for r in rows), default=0):.2f} (total {time.time() - t0:.0f}s)')
    if impact:
        print(f'  impact: tasks with several gold places {len(impact)}; other gold places related to the best one: '
              f'{sum(f for _, f, _ in impact)}/{sum(n for _, _, n in impact)}; tasks fully covered '
              f'{sum(f == n for _, f, n in impact)}')
        print('   ', ' '.join(f'{t}:{f}/{n}' for t, f, n in impact))
    misses = [r['id'] for r in scored if not r['first'] or r['first'] > a.k]
    print('  misses:', ' '.join(misses))


if __name__ == '__main__':
    main()
