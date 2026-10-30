"""Places related to the chosen code (CodePlan-light change-impact analysis): copies of it in other files or in an async
twin class, overrides and base versions, direct callers, exports in __init__.py. And, for an entity the statement asks
for that does not exist yet, a sibling entity of the same kind to model it on."""
import ast
import difflib
import os
import re
from collections import namedtuple

import _map
import _repo

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

Place = namedtuple('Place', 'rel name start end reason')
MAX_CALLERS = 5
MAX_USES = 30      # a property read as x.<name> in more places than this has a common name: its readers are not shown
MAX_SAME_NAME = 3   # a plain name defined in more places than this (get, __init__) is not a copy
SAME_PATTERN = 0.7  # a sibling whose body matches this much of the chosen one's (tokens) repeats its logic
MAX_PATTERN = 3
PATTERN_LINES = 60  # longer siblings are not compared


def _twin_key(qualname):
    """The qualified name with async markers removed: AsyncClient.send and Client.send share it."""
    return re.sub('(?i)async', '', qualname).replace('_', '').lower()


class Table:
    """Every function and class of the repository's code (tests left out), the calls made inside each one, the
    base classes of each class and the names each __init__.py exports. Read lazily (_map): a question about a name
    reads only the files that hold it (git grep), each from its record; nothing is made for the whole repository."""

    def __new__(cls, root, docs=False):
        k = (os.path.abspath(root), bool(docs))
        self = _TABLES.get(k)
        if self is None:
            self = object.__new__(cls)
            self.root, self.docs = root, bool(docs)
            self.syms = _Syms(self)          # rel -> [Sym]
            self.calls = _ByName(self, 'calls')      # called name -> [(rel, caller qualname or None, line)]
            self.reads = _ByName(self, 'reads')      # attribute read as self.<name> -> [(rel, reader qualname, line)]
            self.uses = _ByName(self, 'uses')        # attribute read as <other expression>.<name> -> [(rel, reader, line)]
            self.exports = _Exports(self)            # name -> [rel of __init__.py]
            self.bases = _Bases(self)                # (rel, class qualname) -> [base names]
            _TABLES[k] = self
        return self

    def files(self):
        return _map.files(self.root, docs=self.docs)

    def facts(self, rel):
        return _map.facts(self.root, rel) if rel in self._fileset() else None

    def _fileset(self):
        k = _repo.VERSION[0]
        if getattr(self, '_set', (None,))[0] != k:
            self._set = (k, set(self.files()))
        return self._set[1]

    def where(self, name, kind='word'):
        """The table's files, in the table's order, whose text can hold name: as a whole identifier (word), defined
        there (def: 'def name' or 'class name'), called (call: 'name(', or name at the end of a line before its
        arguments) or read as an attribute (attr: '.name')."""
        if not isinstance(name, str) or not _map.IDENT.match(name):
            return []
        k = (os.path.abspath(self.root), _repo.VERSION[0], kind, name)
        if k not in _WHERE:
            self.prefetch([name], kind)
        hits = _WHERE[k]
        return [r for r in self.files() if r in hits]

    def prefetch(self, names, kind='def'):
        """where() for several names with one git grep (the window of a place looks up every name it uses)."""
        names = sorted({n for n in names if isinstance(n, str) and _map.IDENT.match(n)
                        and (os.path.abspath(self.root), _repo.VERSION[0], kind, n) not in _WHERE}, key=lambda n: (-len(n), n))
        if not names:
            return
        alt = '(?:' + '|'.join(names) + ')'
        body = {'word': f'{_map.BEFORE}{alt}{_map.AFTER}',
                'def': rf'(?:def|class)[ \t]+{alt}{_map.AFTER}',
                'call': rf'{_map.BEFORE}{alt}[ \t]*(?:\(|\\?$)',
                'attr': rf'\.[ \t]*{alt}{_map.AFTER}'}[kind]
        got = {n: set() for n in names}
        for rel, hits in _map.found(self.root, body).items():
            for m in hits:
                n = re.findall(r'[A-Za-z_]\w*', m)
                n = n[-1] if kind != 'call' else n[0] if n else None
                if n in got:
                    got[n].add(rel)
        for n in names:
            _WHERE[(os.path.abspath(self.root), _repo.VERSION[0], kind, n)] = got[n]

    def get(self, rel, name):
        for s in self.syms.get(rel, []):
            if s.name == name:
                return s
        return None

    def find(self, name):
        """(rel, Sym) of every definition whose qualified name is name, or ends with '.name'."""
        return [(rel, s) for rel in self.where(name.split('.')[-1], 'def') for s in self.syms.get(rel, [])
                if s.name == name or s.name.endswith('.' + name)]

    def subclasses(self, cls):
        short = cls.split('.')[-1]
        out = []
        for rel in self.where(short):
            f = self.facts(rel)
            out += [(rel, c) for c, bases in (f['bases'] if f else {}).items() if short in bases]
        return out

    def call_estimates(self, names):
        """{name: the call sites of name in the table's files}, counted from the text (git grep: 'name(' outside a def
        line), for an order by how much code calls each name, where the exact calls of every name would read the
        files of all of them."""
        names = [n for n in dict.fromkeys(names) if _map.IDENT.match(n or '')]
        out = dict.fromkeys(names, 0)
        if not names:
            return out
        alt = '|'.join(sorted(names, key=lambda n: (-len(n), n)))
        files = set(self.files())
        for rel, hits in _map.found(self.root, rf'(?<!def ){_map.BEFORE}(?:{alt})[ \t]*\(').items():
            if rel in files:
                for m in hits:
                    out[m.rstrip('( \t')] += 1
        return out

    def params(self, rel, name):
        """The parameter names of the function name of the file rel ([] for a class or unknown code)."""
        f = self.facts(rel)
        return f['params'].get(name, []) if f else []


_TABLES = {}
_WHERE = {}       # (root, version, kind, name) -> the files that can hold it (one process)


class _Syms:
    """rel -> [Sym] of the table's files; a file's symbols are read when asked for."""

    def __init__(self, table):
        self.t = table

    def get(self, rel, default=None):
        f = self.t.facts(rel)
        return _map.lazy_syms(self.t.root, rel, f['syms']) if f else default

    def __getitem__(self, rel):
        out = self.get(rel)
        if out is None:
            raise KeyError(rel)
        return out

    def __contains__(self, rel):
        return rel in self.t._fileset()

    def __iter__(self):
        return iter(self.t.files())

    def __len__(self):
        return len(self.t.files())

    def keys(self):
        return list(self.t.files())

    def items(self):
        """Every file's symbols: reads the whole repository (the callers ask by name instead)."""
        for rel in self.t.files():
            syms = self.get(rel)
            if syms is not None:
                yield rel, syms


class _ByName:
    """name -> [(rel, the function or class it is in, line)] of the calls, self.<name> reads or other <x>.<name>
    reads in the table's files."""

    def __init__(self, table, kind):
        self.t, self.kind = table, kind

    def get(self, name, default=None):
        return list(self.iter(name)) or default

    def iter(self, name, order=None):
        """The entries one file at a time, read only as far as the caller goes: in the table's order, or with the
        files sorted by order(rel) and each file's entries by line (the order of a sort on (order(rel), line))."""
        rels = self.t.where(name, 'call' if self.kind == 'calls' else 'attr')
        for rel in sorted(rels, key=order) if order else rels:
            got = self.in_file(rel, name)
            yield from sorted(got, key=lambda e: e[2]) if order else got

    def in_file(self, rel, name):
        f = self.t.facts(rel)
        return [(rel, o, line) for n, o, line in (f[self.kind] if f else ()) if n == name]

    def count(self, name, cap):
        """How many entries name has, counted up to cap + 1 (enough to tell 'more than cap')."""
        n = 0
        for _ in self.iter(name):
            n += 1
            if n > cap:
                break
        return n


class _Exports:
    def __init__(self, table):
        self.t = table

    def get(self, name, default=None):
        out = []
        for rel in self.t.where(name):
            f = self.t.facts(rel) if rel.endswith('__init__.py') else None
            out += [rel for n in (f['exports'] if f else ()) if n == name]
        return out or default


class _Bases:
    def __init__(self, table):
        self.t = table

    def get(self, key, default=None):
        rel, cls = key
        f = self.t.facts(rel)
        return f['bases'].get(cls, default) if f else default


def _body_tokens(sym):
    try:
        text = ast.unparse(sym.node)
    except (RecursionError, MemoryError):
        return []
    return re.findall(r'\w+|\S', text.split('\n', 1)[1] if '\n' in text else text)


def same_pattern(table, rel, sym):
    """The functions next to sym (the same class, or the file's top level) whose body repeats its logic: [(rel, Sym)].
    fastapi_14448 changes is_gen_callable, is_async_gen_callable and is_coroutine_callable the same way (bodies 0.93
    and 0.73 alike); the agent chose one and changed only that one, because nothing put the others in the plan."""
    if sym.kind != 'def' or sym.name.split('.')[-1].startswith('__') or sym.end - sym.start >= PATTERN_LINES:
        return []
    prefix = sym.name.rsplit('.', 1)[0] + '.' if '.' in sym.name else ''
    mine = _body_tokens(sym)
    scored = []
    for s in table.syms.get(rel, []):
        short = s.name[len(prefix):]
        if (s is sym or s.kind != 'def' or not s.name.startswith(prefix) or '.' in short or short.startswith('__')
                or s.end - s.start >= PATTERN_LINES):
            continue
        ratio = difflib.SequenceMatcher(None, mine, _body_tokens(s), autojunk=False).ratio()
        if ratio >= SAME_PATTERN:
            scored.append((-ratio, s.start, s))
    return [(rel, s) for _, _, s in sorted(scored)[:MAX_PATTERN]]


def _is_property(sym):
    return any((isinstance(d, ast.Name) and d.id in ('property', 'cached_property')) or
               (isinstance(d, ast.Attribute) and d.attr in ('property', 'cached_property'))
               for d in getattr(sym.node, 'decorator_list', []))


def related(table, chosen):
    """The chosen places first, then the places related to them, each once: [Place]. chosen: [(rel, qualname)]."""
    out, seen = [], set()

    def put(rel, sym, reason):
        if sym is not None and (rel, sym.name) not in seen:
            seen.add((rel, sym.name))
            out.append(Place(rel, sym.name, sym.start, sym.end, reason))

    for rel, name in chosen:
        put(rel, table.get(rel, name), 'chosen')
    outside = {}     # (rel, reader) -> [chosen properties it reads, first line, first property]
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
        for r, s in same_pattern(table, rel, sym):
            put(r, s, f'same pattern as {short}')
        if '.' in name and _is_property(sym):
            # c13: Dependant._unwrapped_call is a cached_property: read, never called, so its readers (the three
            # is_*_callable, which the reference changes too) were not related places
            cls = name.rsplit('.', 1)[0]
            readers = [(r, o) for r, o, _ in table.reads.in_file(rel, short) if o.startswith(cls + '.')
                       and o != name]
            for r, o in list(dict.fromkeys(readers))[:MAX_CALLERS]:
                put(r, table.get(r, o), f'reads {short}')
            # its readers outside the class (dependant.is_gen_callable in utils.py): fastapi_14448's reference also
            # swaps two branches of _solve_generator, which reads two of the changed properties, and no run planned or
            # even showed it (the analysis of s1: with that swap added, its patch passes). Shown, not planned; the code
            # that reads more of the chosen properties first. Common attribute names (read in many places) are skipped.
        plain = [(r, s) for r, s in same if not short.startswith('__')]
        if len(plain) <= MAX_SAME_NAME:
            for r, s in plain:
                put(r, s, f'also named {short}')
        # calls of a dunder method are not calls of this one (super().__init__ of other classes); examples in docs
        # are callers only of documentation code
        # (read in the order they are shown, only as far as MAX_CALLERS of them: the files of a common name are not
        # all read)
        callers = () if short.startswith('__') else (
            c for c in table.calls.iter(short, order=lambda r: (r != rel, r.split('/')[:-1] != rel.split('/')[:-1], r))
            if c[1] and (c[0], c[1]) != (rel, name) and (_repo.is_doc_path(rel) or not _repo.is_doc_path(c[0])))
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
    for p in list(out):
        sym = table.get(p.rel, p.name)
        if p.reason == 'chosen' or p.reason.startswith(('same pattern', 'reads ', 'async/sync twin')):
            if sym is None or '.' not in p.name or not _is_property(sym):
                continue
            cls, short = p.name.rsplit('.', 1)
            found = []
            for u in table.uses.iter(short):
                found.append(u)
                if len(found) > MAX_USES:
                    break
            for r, o, line in found if len(found) <= MAX_USES else []:
                if not _repo.is_doc_path(r) and not o.startswith(cls + '.') and (r, o) not in seen:
                    u = outside.setdefault((r, o), [set(), line, short])
                    u[0].add(short)

    def density(k):
        # the planned properties it reads per line: a short reader is mostly about them (fastapi_14448: the 9-line
        # _solve_generator before the 138-line solve_dependencies)
        sym = table.get(*k)
        return len(outside[k][0]) / (1 + (sym.end - sym.start + 1) / 10) if sym is not None else 0

    order = sorted(outside, key=lambda k: (-density(k), k[0] != chosen[0][0], k[0], outside[k][1]))
    for r, o in order[:MAX_CALLERS]:
        put(r, table.get(r, o), f'uses {outside[(r, o)][2]} (line {outside[(r, o)][1]})')
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
