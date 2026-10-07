"""callers.py <name>

Lists where a function, method or class is defined and every place that calls or references it in the package
source (tests listed separately), each with its enclosing function, so you can see whether the fix belongs in
the function itself or in a caller that prepares its input.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import clip, enclosing, graph_id, iter_py, parse, read_text, repeat_guard, repo_root, symbols  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print('usage: callers.py <function_or_class_name>')
        print('NEXT: call callers.py with the name of the function or class the statement is about.')
        return
    repeat_guard('use the callers listed earlier: view one with show.py <file> <symbol> or write your report.')
    name = sys.argv[1].split('.')[-1].strip('()')
    root = repo_root()
    define = re.compile(r'^\s*(async\s+def|def|class)\s+' + re.escape(name) + r'\b')
    use = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'\b')
    defs, src_uses, test_uses = [], collections.OrderedDict(), collections.OrderedDict()
    for rel in iter_py(root, tests=True, docs=True):
        text = read_text(root, rel)
        if name not in text:
            continue
        syms = symbols(parse(text))
        for i, line in enumerate(text.splitlines(), 1):
            if define.match(line):
                enc = enclosing(syms, i)
                defs.append(f'{rel}:{i}: {line.strip()[:110]}' + (f'  graph id: {graph_id(rel, enc[0])}' if enc else ''))
            elif use.search(line) and not line.strip().startswith(('#', 'import ', 'from ')):
                enc = enclosing(syms, i)
                where = enc[0] if enc else '<module>'
                bucket = test_uses if ('test' in rel.split('/')[0] or '/test' in rel or os.path.basename(rel).startswith('test_')) else src_uses
                bucket.setdefault((rel, where), []).append(f'{i}: {line.strip()[:100]}')
    out = [f'Definitions of {name}:'] + (['  ' + d for d in defs] or ['  none found'])
    out.append(f'Callers / references in source ({len(src_uses)} places):')
    for (rel, where), ls in list(src_uses.items())[:15]:
        out.append(f'  {rel} :: {where}  graph id: {graph_id(rel, where)}')
        out += ['      ' + l for l in ls[:2]]
    out.append(f'Tests that use it ({len(test_uses)} places): ' +
               ', '.join(sorted({rel for rel, _ in test_uses}))[:500])
    out.append('NEXT: if a caller builds the wrong input, the fix belongs in that caller; '
               'view it with show.py <file> <symbol>.')
    print(clip('\n'.join(out)))


if __name__ == '__main__':
    main()
