"""Candidate places for a statement: BM25F over the repository's functions and classes (docs/old_scripts_lessons.md,
Localization). Each function or class is a document of its own lines (a method's lines belong to the method, not to its
class); the name and signature, the code and the prose (comments, docstrings) are its fields."""
import ast
import difflib
import hashlib
import math
import os
import re
from collections import Counter

import _code
import _map
import _repo

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

K1, B = 1.2, 0.75
FIELDS = {'name': 3.0, 'code': 1.0, 'prose': 0.4, 'path': 1.0}  # path: the file's directories and name
DOC_PRIOR = 0.5      # docs_src/, scripts/, examples/: searched, but below the package
MODULE_PRIOR = 1.0   # the lines outside every function and class (0.3 lost 4 of 128 module-level hits, gained none)
CODE_WORDS = {'self', 'cls', 'none', 'true', 'fals', 'return', 'def', 'class', 'import', 'from'}  # in every function
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
PART = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+')


def stem(word):
    """A light suffix stripper, the same for the query and the code: redirects/redirected/redirecting -> redirect."""
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 4 and word.endswith('sses'):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        word = word[:-1]
    elif len(word) > 5 and word.endswith('ing'):
        word = word[:-3]
    elif len(word) > 4 and word.endswith('ed'):
        word = word[:-2]
    if len(word) > 4 and word.endswith('e'):
        word = word[:-1]
    return word


def parts(ident):
    return [p.lower() for p in PART.findall(ident)]


DIGIT_PART = re.compile(r'(?<![A-Za-z])[A-Za-z]{1,6}[0-9]+(?![0-9])')


def digit_parts(ident):
    """v1, http2, utf8 in pydantic_v1, is_http2, to_utf8: the parts split them into 'v' + '1', and both are too short
    to count (fastapi_14583: the term v1 matched nothing of annotation_is_pydantic_v1)."""
    return [m.lower() for seg in ident.split('_') for m in DIGIT_PART.findall(seg) if m.lower() == seg.lower()]


def tokens(ident):
    """The index tokens of one identifier: its stemmed parts (and v1, http2), and the whole name (lower case) when it
    has several."""
    out = _TOKENS.get(ident)
    if out is None:
        out = _TOKENS[ident] = _tokens(ident)
    return out


_TOKENS = {}


def _tokens(ident):
    ps = parts(ident)
    whole = ident.lower().strip('_')
    out = [stem(p) for p in ps if len(p) > 1] + [d for d in digit_parts(ident) if d != whole or len(ps) <= 1]
    if len(ps) > 1:
        out.append(ident.lower().strip('_'))
    return out


def names_file(path, rel):
    """Whether a path from the statement names this file: their last components agree, at least two of them unless
    one side is a bare file name (a traceback's /site-packages/pkg/mod.py names pkg/mod.py and src/pkg/mod.py)."""
    a, b = path.strip('/').split('/'), rel.split('/')
    need = min(2, len(a), len(b))
    return a[-need:] == b[-need:]


class Doc:
    __slots__ = ('rel', 'sym', 'start', 'end', 'prior', 'tf', 'length')

    def __init__(self, rel, sym, start, end, prior):
        self.rel, self.sym, self.start, self.end, self.prior = rel, sym, start, end, prior
        self.tf = {f: Counter() for f in FIELDS}
        self.length = dict.fromkeys(FIELDS, 0)

    def add(self, field, line):
        toks = [t for ident in IDENT.findall(line) for t in tokens(ident)]
        self.tf[field].update(toks)
        self.length[field] += len(toks)

    @property
    def name(self):
        return self.sym.name if self.sym else '<module>'


def _comment_lines(lines):
    return {i for i, line in enumerate(lines, 1) if line.lstrip().startswith('#')}


def _docstring_lines(tree):
    out = set()
    for node in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                      ast.ClassDef))] if tree else []:
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            out.update(range(body[0].lineno, body[0].end_lineno + 1))
    return out


def _prose_lines(tree, lines):
    """_code.prose_lines | _comment_lines | _docstring_lines in one walk of the tree."""
    out = _comment_lines(lines)
    if tree is None:
        return out
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and node.end_lineno > node.lineno:
                out.update(range(node.lineno, node.end_lineno + 1))
        elif isinstance(node, holders):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.update(range(body[0].lineno, body[0].end_lineno + 1))
    return out


def _doc_rows(text, tree, syms, rel):
    """The documents of one file as a record (_map): [(the function or class as (name, kind, start, def_line, end), or
    None for the top-level code; first line, last line, {field: {token: count}}, {field: length})]. [] for a file too
    big to be hand-written code (_repo.read_code)."""
    if text is None:
        return []
    lines = text.splitlines()
    owner = _code.owner_map(syms, len(lines))
    prose = _prose_lines(tree, lines)
    docs = {}
    path_words = rel[:-3].replace('/', ' ') if rel.endswith('.py') else rel.replace('/', ' ')
    module_lines = [i for i in range(1, len(lines) + 1) if owner[i] is None and lines[i - 1].strip()]
    if module_lines:
        docs[None] = Doc(rel, None, module_lines[0], module_lines[-1], 1.0)
        docs[None].add('path', path_words)
    for sym in syms:
        doc = docs[sym.name] = Doc(rel, sym, sym.start, sym.end, 1.0)
        doc.add('path', path_words)
        doc.add('name', sym.name.replace('.', ' '))
        body = sym.node.body[0].lineno if sym.node.body else sym.def_line
        for i in range(sym.def_line, max(sym.def_line, body - 1) + 1):
            if i <= len(lines) and owner[i] is sym:
                doc.add('name', lines[i - 1])
    for i, line in enumerate(lines, 1):
        sym = owner[i]
        doc = docs.get(sym.name if sym else None)
        if doc is None or (sym is not None and sym.def_line <= i < (sym.node.body[0].lineno if sym.node.body else i)):
            continue
        doc.add('prose' if i in prose else 'code', line)
    return [((d.sym.name, d.sym.kind, d.sym.start, d.sym.def_line, d.sym.end) if d.sym else None, d.start, d.end,
             {f: dict(c) for f, c in d.tf.items()}, d.length) for d in docs.values()]


SELECT = 40         # files whose documents are scored for one ranking: those that hold the query's words most
SAMPLE = 24         # files whose documents give the average field lengths (spread over the repository by their path)
DEF_LINE = r'^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]'


class Index:
    """BM25F over the repository's functions and classes, read lazily (_map; audit 10-26: it was built from every file
    in every call, 17 s per call on moto). A ranking scores the documents of the SELECT files that hold the query's
    words most (git grep); the statistics BM25 needs of the whole repository are estimated from git grep counts: a
    word's document frequency from the lines that hold it in each file (at most that file's functions and classes),
    the number of documents from the def and class lines, the average field lengths from a fixed sample of files."""

    def __new__(cls, root):
        k = os.path.abspath(root)
        self = _INDEXES.get(k)
        if self is None or self._version != _repo.VERSION[0]:
            self = object.__new__(cls)
            self.root, self._version = root, _repo.VERSION[0]
            self._counts = {}        # token -> {rel: lines or matches that hold it}
            self._idf = {}
            self._avg = None
            self._defs = None
            self.names = _Names(self)       # every identifier in the code: what 'defined' means for a new entity
            _INDEXES[k] = self
        return self

    @property
    def files(self):
        return _map.files(self.root)

    def _fileset(self):
        if getattr(self, '_set', None) is None:
            self._set = set(self.files)
            self._list = sorted(self._set)
        return self._set

    def docs_in(self, rel):
        """The documents of one file of the index ([] for a file outside it)."""
        if rel not in self._fileset():
            return []
        rows = _map.record(self.root, rel, 'docs', _doc_rows) or []
        k = (rel, id(rows))
        hit = _DOCS.get(k)
        if hit is None or hit[0] is not rows:
            prior = DOC_PRIOR if _repo.is_doc_path(rel) else 1.0
            docs = []
            for sym, start, end, tf, length in rows:
                d = Doc.__new__(Doc)
                d.rel, d.start, d.end, d.length = rel, start, end, length
                d.sym = _map.LazySym(*sym, (self.root, rel)) if sym else None
                d.prior = prior * (MODULE_PRIOR if sym is None else 1.0)
                d.tf = {f: Counter(tf.get(f, {})) for f in FIELDS}
                docs.append(d)
            hit = _DOCS[k] = (rows, docs)
        return hit[1]

    def doc(self, rel, name):
        """The document of the function or class name of the file rel (None or '<module>': its top-level code)."""
        name = name or '<module>'
        return next((d for d in self.docs_in(rel) if d.name == name), None)

    # ------------------------------------------------------------------ statistics

    def _def_counts(self):
        if self._defs is None:
            self._defs = _map.count(self.root, DEF_LINE)
        return self._defs

    @property
    def n(self):
        if getattr(self, '_n', None) is None:
            defs = self._def_counts()
            self._n = max(1, sum(defs.get(r, 0) + 1 for r in self._fileset()))
        return self._n

    @property
    def avg(self):
        if self._avg is None:
            sample = sorted(self.files, key=lambda r: hashlib.md5(r.encode()).hexdigest())[:SAMPLE]
            docs = [d for r in sample for d in self.docs_in(r)]
            n = max(1, len(docs))
            self._avg = {f: max(1.0, sum(d.length[f] for d in docs) / n) for f in FIELDS}
        return self._avg

    def _count(self, toks, wholes=()):
        """Fills _counts for the tokens not counted yet: one git grep for all of them. A token is matched where an
        identifier part starts (snake_case, CamelCase, UPPER); a whole name (wholes) as an identifier of its own."""
        toks = [t for t in dict.fromkeys(toks) if t and t not in self._counts]
        if not toks:
            return
        wholes = set(wholes)
        alts, needles = [], {}
        for t in toks:
            if t in wholes:
                alts.append((len(t) + 2, f'(?i:{_map.BEFORE}_*{re.escape(t)}(?!_*[A-Za-z0-9]))'))
                continue
            nd = t[:-1] if t.endswith('y') and len(t) > 3 else t      # bodies -> body: the needle is 'bod'
            needles[t] = nd
            for v in dict.fromkeys((f'(?<![A-Za-z]){re.escape(nd)}', re.escape(nd[0].upper() + nd[1:]),
                                    f'(?<![A-Z]){re.escape(nd.upper())}')):
                alts.append((len(nd), v))
        pattern = '|'.join(v for _, v in sorted(alts, key=lambda x: -x[0]))
        for t in toks:
            self._counts[t] = {}
        for rel, hits in _map.found(self.root, pattern).items():
            for m, c in Counter(hits).items():
                low = m.lower()
                for t in toks:
                    if (low.strip('_') == t) if t in wholes else low.startswith(needles[t]):
                        self._counts[t][rel] = self._counts[t].get(rel, 0) + c

    def _df(self, tok):
        if tok not in self._counts:
            self._count([tok])
        defs = self._def_counts()
        files = self._fileset()
        return sum(min(c, defs.get(r, 0) + 1) for r, c in self._counts[tok].items() if r in files)

    def idf(self, tok):
        if tok not in self._idf:
            df = self._df(tok)
            self._idf[tok] = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
        return self._idf[tok]

    # ------------------------------------------------------------------ ranking

    def query(self, terms):
        """{token: weight} from {term: weight}. A compound name gives its whole token at full weight and its parts at
        half; a compound name that exists nowhere is replaced by the closest real names (a plural, a typo) among the
        documents that share its words."""
        q, missing = {}, []

        def put(tok, w):
            if tok not in CODE_WORDS:
                q[tok] = max(q.get(tok, 0), w)

        idents = [(ident, w) for term, w in terms.items() for ident in IDENT.findall(term)]
        wholes = {ident.lower().strip('_') for ident, _ in idents if len(parts(ident)) > 1}
        toks = [stem(p) for ident, _ in idents for p in parts(ident) if len(p) > 1] + [
            d for ident, _ in idents for d in digit_parts(ident)]
        self._count(toks + sorted(wholes), wholes)
        files = self._fileset()
        for ident, w in idents:
            ps = parts(ident)
            for d in digit_parts(ident):
                put(d, w if d == ident.lower() else w * 0.5)
            if len(ps) <= 1:
                for p in ps:
                    if len(p) > 1:
                        put(stem(p), w)
                continue
            whole = ident.lower().strip('_')
            if any(r in files for r in self._counts.get(whole, ())):
                put(whole, w)
            else:
                missing.append((whole, w))
            for p in ps:
                if len(p) > 1:
                    put(stem(p), w * 0.5)
        if missing:
            near = sorted({t for r in self._select(q) for d in self.docs_in(r) for f in FIELDS for t in d.tf[f]
                           if '_' in t or len(t) > 8})
            for whole, w in missing:
                for close in difflib.get_close_matches(whole, near, n=2, cutoff=0.8):
                    put(close, w * 0.8)
            self._count([t for t in q if t not in self._counts])
        return q

    def _select(self, q, named=(), guessed=()):
        """The SELECT files that hold the query's tokens most (a file the statement names counts double, one the
        model guessed a quarter more, as their documents do in rank), sorted."""
        scores = {}
        idf = {t: self.idf(t) for t in q}
        for t, w in q.items():
            for rel, c in self._counts.get(t, {}).items():
                scores[rel] = scores.get(rel, 0) + w * idf[t] * c / (K1 + c)
        self._fileset()
        for rel in self._list:
            for t in _path_tokens(rel) & q.keys():
                scores[rel] = scores.get(rel, 0) + q[t] * idf[t] * FIELDS['path'] / (K1 + 1)
        files = self._fileset()
        out = {}
        for rel, s in scores.items():
            if rel not in files or s <= 0:
                continue
            s *= DOC_PRIOR if _repo.is_doc_path(rel) else 1.0
            if any(names_file(p, rel) for p in named):
                s *= 2
            elif any(names_file(p, rel) for p in guessed):
                s *= 1.25
            out[rel] = s
        return sorted(sorted(out, key=lambda r: (-out[r], r))[:SELECT])

    def score(self, doc, q):
        total, matched = 0.0, []
        avg = self.avg
        for tok, w in q.items():
            tf = sum(FIELDS[f] * doc.tf[f][tok] / (1 - B + B * doc.length[f] / avg[f])
                     for f in FIELDS if doc.tf[f][tok])
            if tf:
                s = w * self.idf(tok) * tf / (K1 + tf)
                total += s
                matched.append((s, tok))
        return total * doc.prior, [t for _, t in sorted(matched, reverse=True)]

    def rank(self, terms, paths=(), n=10, guessed=()):
        """The n best documents as (score, doc, matched tokens), all of them when n is None. Files the statement names
        count double; files the model guessed (its search terms) count a quarter more: h1 (fastapi_14583) guessed
        temp_pydantic_v1_params.py, and 8 of the 10 candidates came from that file."""
        q = self.query(terms)
        named = [p for p in paths if p]
        guessed = [p for p in guessed if p and p not in named]
        out = []
        for rel in self._select(q, named, guessed):
            for doc in self.docs_in(rel):
                s, matched = self.score(doc, q)
                if s <= 0:
                    continue
                if any(names_file(p, doc.rel) for p in named):
                    s *= 2
                elif any(names_file(p, doc.rel) for p in guessed):
                    s *= 1.25
                out.append((s, doc, matched))
        out.sort(key=lambda x: (-x[0], x[1].rel, x[1].start))
        return out if n is None else out[:n]


_INDEXES = {}
_DOCS = {}
_PATHS = {}


def _path_tokens(rel):
    if rel not in _PATHS:
        words = rel[:-3].replace('/', ' ') if rel.endswith('.py') else rel.replace('/', ' ')
        _PATHS[rel] = {t for ident in IDENT.findall(words) for t in tokens(ident)}
    return _PATHS[rel]


class _Names:
    """Whether an identifier appears in the index's files (git grep: no file is read)."""

    def __init__(self, index):
        self.index = index

    def __contains__(self, name):
        if not isinstance(name, str) or not _map.IDENT.match(name):
            return False
        return bool(_map.having(self.index.root, name) & self.index._fileset())


def owner_class(index, var):
    """The class an object named var belongs to, for a new entity asked as var.new_name: a class named like var, else
    the class var is most often made from in the code and examples (app = FastAPI() -> FastAPI). (rel, qualname) or
    None."""
    if not re.fullmatch(r'[A-Za-z_]\w*', var or ''):
        return None
    root = index.root
    code = [r for r in index.files if not _repo.is_doc_path(r)]

    def first_class(short, ignore_case=False):
        hits = _map.having(root, short, ignore_case=ignore_case)
        for rel in code:
            if rel not in hits:
                continue
            f = _map.facts(root, rel)
            for name, kind, *_ in (f['syms'] if f else ()):
                last = name.split('.')[-1]
                if kind == 'class' and (last.lower() == short.lower() if ignore_case else last == short):
                    return rel, name
        return None

    place = first_class(var, ignore_case=True)
    if place:
        return place
    made = Counter()
    pattern = re.compile(rf'^\s*{re.escape(var)}\s*=\s*([A-Z]\w*)\(')
    found = _map.found(root, rf'^[ \t]*{re.escape(var)}[ \t]*=[ \t]*[A-Z]\w*\(')
    known = {}
    for rel in index.files:
        for m in found.get(rel, ()):
            cls = pattern.match(m)
            cls = cls.group(1) if cls else None
            if cls and cls not in known:
                known[cls] = first_class(cls)
            if cls and known[cls]:
                made[cls] += 1
    return known[made.most_common(1)[0][0]] if made else None
