"""Places related to the chosen code (CodePlan-light change-impact analysis): copies of it in other files or in an async
twin class, overrides and base versions, direct callers, exports in __init__.py. And, for an entity the statement asks
for that does not exist yet, a sibling entity of the same kind to model it on."""
import ast
import difflib
import re
from collections import namedtuple

import _code
import _repo

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

Place = namedtuple('Place', 'rel name start end reason')
MAX_CALLERS = 5
MAX_SAME_NAME = 3   # a plain name defined in more places than this (get, __init__) is not a copy


def _twin_key(qualname):
    """The qualified name with async markers removed: AsyncClient.send and Client.send share it."""
    return re.sub('(?i)async', '', qualname).replace('_', '').lower()


class Table:
    """Every function and class of the repository's code (tests left out), the calls made inside each one, the
    base classes of each class and the names each __init__.py exports."""

    def __init__(self, root, docs=False):
        self.root = root
        self.syms = {}       # rel -> [Sym]
        self.calls = {}      # called name -> [(rel, caller qualname or None, line)]
        self.bases = {}      # (rel, class qualname) -> [base names]
        self.exports = {}    # name -> [rel of __init__.py]
        for rel in _repo.iter_py(root, docs=docs):
            tree = _code.parse(_repo.read_text(root, rel))
            if tree is None:
                continue
            syms = _code.symbols(tree)
            self.syms[rel] = syms
            n = max([s.end for s in syms] + [getattr(tree.body[-1], 'end_lineno', 1) if tree.body else 1])
            owner = _code.owner_map(syms, n)
            for s in syms:
                if s.kind == 'class':
                    self.bases[(rel, s.name)] = [ast.unparse(b).split('.')[-1].split('[')[0] for b in s.node.bases]
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                    if name:
                        o = owner[node.lineno] if node.lineno < len(owner) else None
                        self.calls.setdefault(name, []).append((rel, o.name if o else None, node.lineno))
            if rel.endswith('__init__.py'):
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for a in node.names:
                            self.exports.setdefault(a.asname or a.name, []).append(rel)

    def get(self, rel, name):
        for s in self.syms.get(rel, []):
            if s.name == name:
                return s
        return None

    def find(self, name):
        """(rel, Sym) of every definition whose qualified name is name, or ends with '.name'."""
        return [(rel, s) for rel, syms in self.syms.items() for s in syms
                if s.name == name or s.name.endswith('.' + name)]

    def subclasses(self, cls):
        short = cls.split('.')[-1]
        return [(rel, c) for (rel, c), bases in self.bases.items() if short in bases]


def related(table, chosen):
    """The chosen places first, then the places related to them, each once: [Place]. chosen: [(rel, qualname)]."""
    out, seen = [], set()

    def put(rel, sym, reason):
        if sym is not None and (rel, sym.name) not in seen:
            seen.add((rel, sym.name))
            out.append(Place(rel, sym.name, sym.start, sym.end, reason))

    for rel, name in chosen:
        put(rel, table.get(rel, name), 'chosen')
    for rel, name in chosen:
        sym = table.get(rel, name)
        if sym is None:
            continue
        short = name.split('.')[-1]
        same = [(r, s) for r, s in table.find(short) if (r, s.name) != (rel, name) and s.kind == sym.kind]
        base = rel.split('/')[-1]
        copies = [(r, s) for r, s in same if s.name == name]
        for r, s in copies:
            # a copy: the same qualified name in a file of the same name (src/httpx and src/ahttpx), or a rare name
            if r.split('/')[-1] == base or len(copies) <= MAX_SAME_NAME:
                put(r, s, f'same definition in {r}')
        for r, s in same:
            if s.name != name and _twin_key(s.name) == _twin_key(name):
                put(r, s, f'async/sync twin of {name}')
        if '.' in name:
            cls, method = name.rsplit('.', 1)
            for r, sub in table.subclasses(cls):
                put(r, table.get(r, f'{sub}.{method}'), f'override in subclass {sub}')
            for base in table.bases.get((rel, cls), []):
                for r, s in table.find(f'{base}.{method}'):
                    put(r, s, f'version in base class {base}')
        plain = [(r, s) for r, s in same if not short.startswith('__')]
        if len(plain) <= MAX_SAME_NAME:
            for r, s in plain:
                put(r, s, f'also named {short}')
        # calls of a dunder method are not calls of this one (super().__init__ of other classes); examples in docs
        # are callers only of documentation code
        callers = [] if short.startswith('__') else [
            c for c in table.calls.get(short, []) if c[1] and (c[0], c[1]) != (rel, name)
            and (_repo.is_doc_path(rel) or not _repo.is_doc_path(c[0]))]
        callers.sort(key=lambda c: (c[0] != rel, c[0].split('/')[:-1] != rel.split('/')[:-1], c[0], c[2]))
        n = 0
        for r, caller, line in callers:
            if n >= MAX_CALLERS:
                break
            if (r, caller) not in seen:
                put(r, table.get(r, caller), f'calls {short} (line {line})')
                n += 1
        for init in table.exports.get(short, []):
            if (init, '<exports>') not in seen:
                seen.add((init, '<exports>'))
                out.append(Place(init, '<exports>', 0, 0, f'exports {short}'))
    return out


def sibling(table, rel, owner, new_name, keywords=()):
    """The existing entity in the owner (a class qualname, or None for the file's top level) most like the new one: the
    most parameters named like the statement's keyword arguments, then the closest name. None when the owner is
    empty."""
    syms = table.syms.get(rel, [])
    prefix = owner + '.' if owner else ''
    pool = [s for s in syms if s.name.startswith(prefix) and '.' not in s.name[len(prefix):] and s.kind == 'def'
            and not s.name.split('.')[-1].startswith('_')]
    if not pool:
        return None

    def score(s):
        params = {a.arg for a in s.node.args.args + s.node.args.kwonlyargs}
        return (len(params & set(keywords)),
                difflib.SequenceMatcher(None, new_name, s.name.split('.')[-1]).ratio())
    return max(pool, key=score)
