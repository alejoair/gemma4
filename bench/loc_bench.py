"""Offline localization benchmark: can the pre-built code graph point to the file to edit?

For each of the 129 development tasks, ranks the graph's nodes (functions, classes, methods)
against the problem statement and checks whether the top-k nodes fall in a file touched by the
gold patch. No model and no GPU: it measures how much of the localization problem a cheap,
deterministic retrieval step can solve before the LLM starts, which decides how much to spend
on LLM exploration.

Data: tasks.jsonl and graphs/<repo>_<base_commit>.json from the competition data.

Usage: python bench/loc_bench.py <data_dir> [--ks 1,3,5,10]
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

STOP = set('the and for that this with from are was not but you can when what should have has will into then than its '
           'use used using like also does doesn not any all one via get set add new instead'.split())
NON_PKG = ('tests.', 'test.', 'docs_src.', 'docs.', 'scripts.', 'tutorial', 'benchmarks.')


def split_ident(s: str) -> list[str]:
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', s)
    return [w for w in re.split(r'[^A-Za-z0-9]+', s.lower()) if len(w) > 2 and w not in STOP]


def tokens(text: str) -> list[str]:
    out = []
    for raw in re.findall(r'[A-Za-z_][A-Za-z0-9_\.]*', text):
        parts = split_ident(raw)
        out.extend(parts)
        if len(raw) > 4 and ('_' in raw or re.search(r'[a-z][A-Z]', raw)):
            out.append(raw.lower())  # keep the compound identifier too
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(1, len(docs))
        df = Counter(t for tf in self.tf for t in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.inv = defaultdict(list)
        for i, tf in enumerate(self.tf):
            for t in tf:
                self.inv[t].append(i)

    def scores(self, query: list[str]) -> dict[int, float]:
        sc: dict[int, float] = defaultdict(float)
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.inv[t]:
                f = self.tf[i][t]
                sc[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return sc


def gold_modules(patch: str) -> set[str]:
    mods = set()
    for f in re.findall(r'^\+\+\+ b/(\S+)', patch, re.M):
        if not f.endswith('.py') or re.search(r'(^|/)(tests?|docs?|docs_src|scripts)/', f):
            continue
        f = re.sub(r'^src/', '', f)[:-3].replace('/', '.')
        mods.add(f[:-9] if f.endswith('.__init__') else f)
    return mods


def in_gold(node_id: str, mods: set[str]) -> bool:
    return any(node_id == m or node_id.startswith(m + '.') for m in mods)


def explicit_idents(stmt: str) -> list[str]:
    ids = re.findall(r'`([A-Za-z_][\w\.]*)(?:\(\))?`', stmt)
    ids += re.findall(r'\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b', stmt)  # dotted names
    ids += re.findall(r'\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b', stmt)  # CamelCase
    ids += re.findall(r'\b([a-z]+_[a-z_0-9]+)\b', stmt)  # snake_case
    return list(dict.fromkeys(ids))


def ident_scores(ids: list[str], names: list[str]) -> dict[int, float]:
    sc: dict[int, float] = defaultdict(float)
    lowered = [n.lower() for n in names]
    for ident in ids:
        il = ident.lower()
        last = il.split('.')[-1]
        for i, n in enumerate(lowered):
            if n == il or n.endswith('.' + il):
                sc[i] += 3.0
            elif n.split('.')[-1] == last:
                sc[i] += 2.0
            elif len(last) > 4 and last in n:
                sc[i] += 0.5
    return sc


def rrf(rankings: list[list[int]], k: int = 60) -> list[int]:
    s: dict[int, float] = defaultdict(float)
    for r in rankings:
        for pos, i in enumerate(r):
            s[i] += 1 / (k + pos + 1)
    return sorted(s, key=s.get, reverse=True)


def top(sc: dict[int, float]) -> list[int]:
    return sorted(sc, key=sc.get, reverse=True)


def module_of(node_id: str) -> str:
    """Guess the module of a node id: keep segments until the first CamelCase one, else drop the last."""
    parts = node_id.split('.')
    for i, seg in enumerate(parts):
        if i > 0 and seg[:1].isupper():
            return '.'.join(parts[:i])
    return '.'.join(parts[:-1]) if len(parts) > 1 else node_id


def module_ranking(rank: list[int], names: list[str], depth: int = 15) -> list[str]:
    """Rank modules by the best-ranked nodes they contain (reciprocal-rank sum over the top nodes)."""
    s: dict[str, float] = defaultdict(float)
    for pos, i in enumerate(rank[:depth]):
        s[module_of(names[i])] += 1 / (pos + 1)
    return sorted(s, key=s.get, reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('data', type=Path)
    ap.add_argument('--ks', default='1,3,5,10')
    ap.add_argument('--keep-nonpkg', action='store_true', help='do not drop tests/docs nodes')
    args = ap.parse_args()
    ks = [int(x) for x in args.ks.split(',')]
    tasks = [json.loads(line) for line in (args.data / 'tasks.jsonl').read_text().splitlines()]

    methods = ['bm25', 'idents', 'hybrid']
    mod_hit = Counter()  # hybrid, module level
    rand_hit = Counter()
    hit = {m: Counter() for m in methods}  # any gold module in top-k
    full = {m: Counter() for m in methods}  # every gold module in top-k
    n_eval = 0
    per_repo = defaultdict(lambda: {m: Counter() for m in methods})
    repo_n = Counter()
    for t in tasks:
        mods = gold_modules(t['patch'])
        gp = args.data / 'graphs' / f"{t['repo'].split('/')[1].lower()}_{t['base_commit']}.json"
        if not mods or not gp.exists():
            continue
        g = json.loads(gp.read_text())
        nodes = [n for n in g['nodes'] if args.keep_nonpkg or not n['id'].startswith(NON_PKG)]
        names = [n['id'] for n in nodes]
        if not any(in_gold(n, mods) for n in names):
            continue  # gold file has no node (e.g. new file): not evaluable
        n_eval += 1
        repo_n[t['repo']] += 1
        bm = BM25([tokens(n['id'].replace('.', ' ') + ' ' + n['text'][:1500]) for n in nodes])
        q = tokens(t['problem_statement'])
        r_bm = top(bm.scores(q))
        r_id = top(ident_scores(explicit_idents(t['problem_statement']), names))
        rankings = {'bm25': r_bm, 'idents': r_id, 'hybrid': rrf([r_bm, r_id]) if r_id else r_bm}
        mr = module_ranking(rankings['hybrid'], names)
        pool = len({module_of(x) for x in names})
        gold_pool = len({module_of(x) for x in names if in_gold(x, mods)})
        for k in ks:
            if any(x in mods or any(x.startswith(g_ + '.') or g_.startswith(x + '.') for g_ in mods) for x in mr[:k]):
                mod_hit[k] += 1
            rand_hit[k] += 1 - math.comb(max(pool - gold_pool, 0), min(k, pool)) / math.comb(pool, min(k, pool)) if pool >= k else 1
        for m, r in rankings.items():
            for k in ks:
                head = [names[i] for i in r[:k]]
                if any(in_gold(x, mods) for x in head):
                    hit[m][k] += 1
                    per_repo[t['repo']][m][k] += 1
                if all(any(in_gold(x, {g_}) for x in head) for g_ in mods):
                    full[m][k] += 1

    print(f'evaluable tasks: {n_eval} / {len(tasks)}  ({dict(repo_n)})')
    print('\nP(at least one gold module among the top-k nodes)')
    print(f"{'method':8}" + ''.join(f'  @{k:<4}' for k in ks))
    for m in methods:
        print(f'{m:8}' + ''.join(f'  {100 * hit[m][k] / n_eval:4.0f}%' for k in ks))
    print('\nP(every gold module among the top-k nodes)')
    for m in methods:
        print(f'{m:8}' + ''.join(f'  {100 * full[m][k] / n_eval:4.0f}%' for k in ks))
    print('\nP(a gold FILE is among the top-k FILES), hybrid + module aggregation')
    print(f"{'method':8}" + ''.join(f'  @{k:<4}' for k in ks))
    print(f"{'files':8}" + ''.join(f'  {100 * mod_hit[k] / n_eval:4.0f}%' for k in ks))
    print(f"{'random':8}" + ''.join(f'  {100 * rand_hit[k] / n_eval:4.0f}%' for k in ks))
    print('\nper repo, hybrid (any gold module)')
    for repo, d in per_repo.items():
        print(f'{repo:18}' + ''.join(f'  @{k}: {100 * d["hybrid"][k] / repo_n[repo]:3.0f}%' for k in ks))


if __name__ == '__main__':
    main()
