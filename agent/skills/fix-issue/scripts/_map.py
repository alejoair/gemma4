"""The repository map, made only as far as a question needs it (docs/design_fast.md, "A lazy repository map"). Which
files hold a name comes from git grep; what one file gives (its functions and classes, the calls and attribute reads in
them, its search documents) is made from that file alone and kept on disk under the file's path and content, so a call
reads only the files its question names, and a later call of the task reuses what an earlier one made. S7-S12: every
call rebuilt the search index and the impact table from all the repository's files, 130 s of script time in one task on
moto (1,500 files) on our machine, more in the scorer's sandbox, against 5 minutes for the whole task."""
import ast
import hashlib
import marshal
import os
import re
import subprocess
import sys

import _code
import _repo

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
BEFORE, AFTER = r'(?<![A-Za-z0-9_])', r'(?![A-Za-z0-9_])'     # a whole identifier (git grep -w)
GREP_TIMEOUT = 60


def _key(root):
    return os.path.abspath(root)


# ------------------------------------------------------------------------------------------------- files and grep

_FILES = {}


def files(root, tests=False, docs=True):
    """_repo.iter_py, walked once per process until the scripts write to the repository."""
    k = (_key(root), _repo.VERSION[0])
    if k not in _FILES:
        _FILES[k] = {}
        _FILES[k][(True, True)] = _repo.iter_py(root, tests=True, docs=True)
    got = _FILES[k]
    if (tests, docs) not in got:
        got[(tests, docs)] = [r for r in got[(True, True)] if (tests or not _repo.is_test_path(r))
                              and (docs or not _repo.is_doc_path(r, root))]
    return list(got[(tests, docs)])


_VISIBLE = {}


def _hidden(root):
    """The repository's .py files git grep does not search (ignored by .gitignore, or no git at all): read here."""
    k = (_key(root), _repo.VERSION[0])
    if k not in _VISIBLE:
        out = _repo.git(root, 'ls-files', '-z', '-co', '--exclude-standard', '--', '*.py')
        seen = set(out.split('\0')) if out is not None else None
        _VISIBLE[k] = [r for r in files(root, tests=True) if seen is None or r not in seen], seen is not None
    return _VISIBLE[k]


_PCRE = []


def _git_grep(root, mode, pattern):
    """git grep's output over the .py files (tracked and untracked), as text; '' when nothing matches; None when git
    cannot run it (no repository, no git, a git without Perl regexes)."""
    if _PCRE and not _PCRE[0]:
        return None
    try:
        r = subprocess.run(['git', '-c', 'safe.directory=*', 'grep', '-I', '--untracked', '-z', mode, '-P', pattern,
                            '--', '*.py'], cwd=root, capture_output=True, timeout=GREP_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode not in (0, 1) or (r.returncode == 1 and r.stderr.strip()):
        if b'perl' in r.stderr.lower() or b'pcre' in r.stderr.lower():
            _PCRE[:] = [False]
        return None
    _PCRE[:] = [True]
    return r.stdout.decode('utf-8', errors='replace')


_FOUND = {}


def found(root, pattern):
    """{rel: [each match of pattern in the file]} for every .py file of the repository (tests and docs too) that holds
    it: git grep -o with a Perl regex, else the files read here with the same regex."""
    k = (_key(root), _repo.VERSION[0], pattern)
    if k in _FOUND:
        return _FOUND[k]
    out = {}
    hidden, has_git = _hidden(root)
    text = _git_grep(root, '-o', pattern) if has_git else None
    if text is None:
        hidden = files(root, tests=True)
    else:
        known = set(files(root, tests=True))
        for line in text.split('\n'):
            rel, sep, match = line.partition('\0')
            if sep and rel in known:
                out.setdefault(rel, []).append(match)
    if hidden:
        try:
            rx = re.compile(pattern, re.M)
        except re.error:
            hidden = []
        for rel in hidden:
            hits = [m.group(0) for m in rx.finditer(_repo.read_text(root, rel))]
            if hits:
                out[rel] = hits
    _FOUND[k] = out
    return out


def having(root, text, word=True, ignore_case=False):
    """The .py files (tests and docs too) that hold text: as a whole identifier when word, else anywhere."""
    return having_any(root, [text], word=word, ignore_case=ignore_case)


_HAVING = {}


def having_any(root, texts, word=True, ignore_case=False):
    """The .py files (tests and docs too) that hold any of the texts: a set of repository-relative paths."""
    texts = sorted({t for t in texts if t and '\n' not in t}, key=lambda t: (-len(t), t))
    if not texts:
        return set()
    k = (_key(root), _repo.VERSION[0], tuple(texts), word, ignore_case)
    if k not in _HAVING:
        alt = '|'.join(re.escape(t) for t in texts)
        pattern = (BEFORE + f'(?:{alt})' + AFTER) if word else f'(?:{alt})'
        _HAVING[k] = set(found(root, ('(?i)' if ignore_case else '') + pattern))
    return _HAVING[k]


def count(root, pattern):
    """{rel: the number of matches of pattern} over the .py files (tests and docs too)."""
    return {rel: len(hits) for rel, hits in found(root, pattern).items()}


# ------------------------------------------------------------------------------------------------- one file's records

def _format():
    """What the records depend on: the source of the modules that make them (a change of the scripts makes them
    again)."""
    if not _FORMAT:
        h = hashlib.sha1()
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ('_map.py', '_code.py', '_rank.py', '_impact.py', '_repo.py'):
            try:
                with open(os.path.join(here, name), 'rb') as fh:
                    h.update(fh.read())
            except OSError:
                pass
        h.update(sys.version.encode())
        _FORMAT.append(h.hexdigest()[:12])
    return _FORMAT[0]


_FORMAT = []
_DIRS = {}


def cache_dir(root):
    """Where the records of this repository are kept: beside the scripts' state (one directory per repository path;
    a record is found by its file's path and content, so a reused path never sees a stale one)."""
    k = _key(root)
    if k not in _DIRS:
        base = os.environ.get('SWE_STATE_ROOT', '/tmp')
        d = os.path.join(base, 'swe_map_' + hashlib.sha1(k.encode()).hexdigest()[:10])
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            d = None
        _DIRS[k] = d
    return _DIRS[k]


def _text(data):
    """_repo.read_code from the file's bytes: None for a file too big to be hand-written code."""
    if len(data) > _repo.MAX_BYTES:
        return None
    text = data.decode(_repo._encoding(data), errors='replace')
    return None if text.count('\n') > _repo.MAX_LINES else text


_RECORDS = {}
_PARSED = {}


def _stamp(root, rel):
    try:
        st = os.stat(os.path.join(root, rel))
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_ino


def record(root, rel, kind, make):
    """make(text, tree, syms, rel) for one file (text None for a file too big to be code), once per process while the
    file stays the same, and kept on disk under the file's path and content for the later calls."""
    stamp = _stamp(root, rel)
    k = (_key(root), rel, kind)
    hit = _RECORDS.get(k)
    if hit is not None and hit[0] == stamp and stamp is not None:
        return hit[1]
    try:
        with open(os.path.join(root, rel), 'rb') as fh:
            data = fh.read()
    except OSError:
        return None
    name = hashlib.sha1(f'{_format()}\0{kind}\0{rel}\0'.encode() + data).hexdigest()
    d = cache_dir(root)
    path = os.path.join(d, name) if d else None
    rec = None
    if path:
        try:
            with open(path, 'rb') as fh:
                rec = marshal.loads(fh.read())
        except (OSError, EOFError, ValueError, TypeError):
            rec = None
    if rec is None:
        text = _text(data)
        tree, syms = _parse(root, rel, text, stamp)
        try:
            rec = make(text, tree, syms, rel)
        except (RecursionError, MemoryError):
            rec = None              # a generated file too deep for the AST helpers: left out (audit 10-18 #7)
        if rec is None:
            rec = False             # (kept as such: None is a record not made yet)
        if path:
            try:
                tmp = f'{path}.{os.getpid()}.tmp'
                with open(tmp, 'wb') as fh:
                    fh.write(marshal.dumps(rec, 4))
                os.replace(tmp, path)
            except (OSError, ValueError):
                pass
    _RECORDS[k] = (stamp, rec)
    return rec


def _parse(root, rel, text, stamp):
    k = (_key(root), rel)
    hit = _PARSED.get(k)
    if hit is not None and hit[0] == stamp and stamp is not None:
        return hit[1], hit[2]
    tree = _code.parse(text) if text is not None else None
    try:
        syms = _code.symbols(tree)
    except (RecursionError, MemoryError):
        syms = []
    _PARSED[k] = (stamp, tree, syms)
    return tree, syms


def parsed(root, rel):
    """(tree, syms) of one file, parsed once per process while it stays the same; (None, []) when it cannot be."""
    stamp = _stamp(root, rel)
    hit = _PARSED.get((_key(root), rel))
    if hit is not None and hit[0] == stamp and stamp is not None:
        return hit[1], hit[2]
    try:
        with open(os.path.join(root, rel), 'rb') as fh:
            data = fh.read()
    except OSError:
        return None, []
    return _parse(root, rel, _text(data), stamp)


class LazySym(_code.Sym):
    """A function or class read from a file's record: its AST node is parsed from that file only when it is used."""
    __slots__ = ()

    @property
    def node(self):
        root, rel = tuple.__getitem__(self, 5)
        _, syms = parsed(root, rel)
        for s in syms:
            if s.name == self.name and s.def_line == self.def_line:
                return s.node
        return None


def lazy_syms(root, rel, rows):
    """LazySym of the rows (name, kind, start, def_line, end) of a record, the same objects for the same record."""
    k = (_key(root), rel, id(rows))
    hit = _LAZY.get(k)
    if hit is None or hit[0] is not rows:
        hit = (rows, [LazySym(n, kind, a, d, b, (root, rel)) for n, kind, a, d, b in rows])
        _LAZY[k] = hit
    return hit[1]


_LAZY = {}


# ------------------------------------------------------------------------------------------------- the facts of a file

def _facts(text, tree, syms, rel):
    """What the impact table needs of one file: its functions and classes, the base classes of each class, the calls
    and attribute reads in it with the function or class they are in, the names an __init__.py imports, and the
    parameters of each function."""
    if tree is None:
        return None
    n = max([s.end for s in syms] + [getattr(tree.body[-1], 'end_lineno', 1) if tree.body else 1])
    owner = _code.owner_map(syms, n)
    rows = [(s.name, s.kind, s.start, s.def_line, s.end) for s in syms]
    bases, params = {}, {}
    for s in syms:
        if s.kind == 'class':
            bases[s.name] = [ast.unparse(b).split('.')[-1].split('[')[0] for b in s.node.bases]
        else:
            a = s.node.args
            params[s.name] = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs] + [
                x.arg for x in (a.vararg, a.kwarg) if x]
    calls, reads, uses, exports = [], [], [], []
    init = rel.endswith('__init__.py')
    for node in ast.walk(tree):         # (one walk: each list keeps the order of the walk)
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name:
                o = owner[node.lineno] if node.lineno < len(owner) else None
                calls.append((name, o.name if o else None, node.lineno))
        elif isinstance(node, ast.Attribute):
            if isinstance(node.ctx, ast.Load):
                o = owner[node.lineno] if node.lineno < len(owner) else None
                if o is not None:
                    own = isinstance(node.value, ast.Name) and node.value.id == 'self'
                    (reads if own else uses).append((node.attr, o.name, node.lineno))
        elif init and isinstance(node, ast.ImportFrom):
            exports += [a.asname or a.name for a in node.names]
    return {'syms': rows, 'bases': bases, 'params': params, 'calls': calls, 'reads': reads, 'uses': uses,
            'exports': exports}


def facts(root, rel):
    """The facts of one file (_facts), or None when it does not parse."""
    return record(root, rel, 'facts', _facts) or None


# ------------------------------------------------------------------------------------------------- imports

IMPORT_LINE = r'^[ \t]*(?:from[ \t]+[A-Za-z_][\w.]*[ \t]+import\b|import[ \t]+[A-Za-z_][\w. \t,]*)'


def import_lines(root):
    """{rel: [the import lines of the file, from their first word]} over the .py files (tests and docs too)."""
    return found(root, IMPORT_LINE)
