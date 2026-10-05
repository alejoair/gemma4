"""Compare result CSVs from kaggle_eval.py on the same tasks (paired).

Prints tasks solved per variant, mean agent time, and for every pair the tasks only one of them
solved plus an exact McNemar p-value. With 20-30 tasks only large differences are detectable:
read the discordant counts, not the totals.

Usage: python bench/compare.py a.csv b.csv [c.csv ...]
"""

from __future__ import annotations

import csv
import itertools
import math
import sys
from pathlib import Path


def load(path: str) -> dict[str, dict]:
    with open(path, newline='') as fh:
        return {r['task_id']: r for r in csv.DictReader(fh)}


def mcnemar_exact(only_a: int, only_b: int) -> float:
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def main(paths: list[str]) -> None:
    runs = {Path(p).stem: load(p) for p in paths}
    common = set.intersection(*(set(r) for r in runs.values()))
    print(f'tasks in common: {len(common)}')
    for name, r in runs.items():
        rows = [r[t] for t in common]
        solved = sum(int(x['resolved']) for x in rows)
        dur = sum(float(x['duration_s'] or 0) for x in rows) / max(1, len(rows))
        empty = sum(1 for x in rows if int(x['patch_chars'] or 0) == 0)
        errs = sum(1 for x in rows if x['error'])
        print(f'{name:14} solved {solved:3}/{len(rows)}  mean time {dur:6.1f}s  empty patches {empty:3}  errors {errs:3}')
    for a, b in itertools.combinations(runs, 2):
        only_a = sorted(t for t in common if int(runs[a][t]['resolved']) and not int(runs[b][t]['resolved']))
        only_b = sorted(t for t in common if int(runs[b][t]['resolved']) and not int(runs[a][t]['resolved']))
        print(f'\n{a} vs {b}: only {a}: {len(only_a)}, only {b}: {len(only_b)}, McNemar exact p = {mcnemar_exact(len(only_a), len(only_b)):.3f}')
        if only_a:
            print(f'  only {a}:', ', '.join(only_a))
        if only_b:
            print(f'  only {b}:', ', '.join(only_b))


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1:])
