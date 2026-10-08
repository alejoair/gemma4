"""Candidate places for a statement: BM25F over the repository's functions and classes (docs/old_scripts_lessons.md,
Localization). Each function or class is a document of its own lines (a method's lines belong to the method, not to its
class); the name and signature, the code and the prose (comments, docstrings) are its fields."""
import ast
import difflib
import math
import re
from collections import Counter

import _code
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


def tokens(ident):
    """The index tokens of one identifier: its stemmed parts, and the whole name (lower case) when it has several."""
    ps = parts(ident)
    out = [stem(p) for p in ps if len(p) > 1]
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
        for ident in IDENT.findall(line):
            toks = tokens(ident)
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


def index_file(root, rel, names=None):
    """The documents of one file. names, when given, receives every identifier of the file."""
    text = _repo.read_text(root, rel)
    if names is not None:
        names.update(IDENT.findall(text))
    lines = text.splitlines()
    tree = _code.parse(text)
    syms = _code.symbols(tree)
    owner = _code.owner_map(syms, len(lines))
    prose = _code.prose_lines(tree) | _comment_lines(lines) | _docstring_lines(tree)
    prior = DOC_PRIOR if _repo.is_doc_path(rel) else 1.0
    docs = {}
    path_words = rel[:-3].replace('/', ' ') if rel.endswith('.py') else rel.replace('/', ' ')
    module_lines = [i for i in range(1, len(lines) + 1) if owner[i] is None and lines[i - 1].strip()]
    if module_lines:
        docs[None] = Doc(rel, None, module_lines[0], module_lines[-1], prior * MODULE_PRIOR)
        docs[None].add('path', path_words)
    for sym in syms:
        doc = docs[sym.name] = Doc(rel, sym, sym.start, sym.end, prior)
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
    return list(docs.values())


class Index:
    def __init__(self, root):
        self.root = root
        self.names = set()      # every identifier in the code: what 'defined' means for a new entity
        self.files = _repo.iter_py(root)
        self.docs = [d for rel in self.files for d in index_file(root, rel, self.names)]
        self.df = Counter()
        for d in self.docs:
            self.df.update(set().union(*(d.tf[f].keys() for f in FIELDS)))
        n = max(1, len(self.docs))
        self.avg = {f: max(1.0, sum(d.length[f] for d in self.docs) / n) for f in FIELDS}
        self.n = n

    def idf(self, tok):
        df = self.df.get(tok, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def query(self, terms):
        """{token: weight} from {term: weight}. A compound name gives its whole token at full weight and its parts at
        half; a compound name that exists nowhere is replaced by the closest real names (a plural, a typo)."""
        q = {}

        def put(tok, w):
            if tok not in CODE_WORDS:
                q[tok] = max(q.get(tok, 0), w)

        wholes = None
        for term, w in terms.items():
            for ident in IDENT.findall(term):
                ps = parts(ident)
                if len(ps) <= 1:
                    for p in ps:
                        if len(p) > 1:
                            put(stem(p), w)
                    continue
                whole = ident.lower().strip('_')
                if whole in self.df:
                    put(whole, w)
                else:
                    if wholes is None:
                        wholes = [t for t in self.df if '_' in t or len(t) > 8]
                    for close in difflib.get_close_matches(whole, wholes, n=2, cutoff=0.8):
                        put(close, w * 0.8)
                for p in ps:
                    if len(p) > 1:
                        put(stem(p), w * 0.5)
        return q

    def score(self, doc, q):
        total, matched = 0.0, []
        for tok, w in q.items():
            tf = sum(FIELDS[f] * doc.tf[f][tok] / (1 - B + B * doc.length[f] / self.avg[f])
                     for f in FIELDS if doc.tf[f][tok])
            if tf:
                s = w * self.idf(tok) * tf / (K1 + tf)
                total += s
                matched.append((s, tok))
        return total * doc.prior, [t for _, t in sorted(matched, reverse=True)]

    def rank(self, terms, paths=(), n=10):
        """The n best documents as (score, doc, matched tokens). Files the statement names count double."""
        q = self.query(terms)
        named = [p for p in paths if p]
        out = []
        for doc in self.docs:
            s, matched = self.score(doc, q)
            if s <= 0:
                continue
            if any(names_file(p, doc.rel) for p in named):
                s *= 2
            out.append((s, doc, matched))
        out.sort(key=lambda x: (-x[0], x[1].rel, x[1].start))
        return out[:n]


def owner_class(index, var):
    """The class an object named var belongs to, for a new entity asked as var.new_name: a class named like var, else
    the class var is most often made from in the code and examples (app = FastAPI() -> FastAPI). (rel, qualname) or
    None."""
    classes = {}
    for d in index.docs:
        if d.sym is not None and d.sym.kind == 'class' and not _repo.is_doc_path(d.rel):
            classes.setdefault(d.sym.name.split('.')[-1], (d.rel, d.sym.name))
    for name, place in classes.items():
        if name.lower() == var.lower():
            return place
    made = Counter()
    pattern = re.compile(rf'^\s*{re.escape(var)}\s*=\s*([A-Z]\w*)\(', re.M)
    for rel in index.files:
        for cls in pattern.findall(_repo.read_text(index.root, rel)):
            if cls in classes:
                made[cls] += 1
    return classes[made.most_common(1)[0][0]] if made else None
