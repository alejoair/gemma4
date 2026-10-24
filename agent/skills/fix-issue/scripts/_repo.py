"""The task repository: where it is, its Python files, exact reads and writes, and the diff the patch will contain."""
import codecs
import io
import os
import re
import subprocess
import sys
import tokenize

# Directories that are never repository code. ALWAYS_SKIP at any depth; SKIP_DIRS (a virtual environment, a build's
# output) only when the directory is not a package itself: audit 10-18 #2, a real subpackage pkg/build/ or pkg/env/
# was never indexed and could not be found or edited
ALWAYS_SKIP = {'.git', '.hg', 'node_modules', '__pycache__', 'site-packages', '.mypy_cache', '.pytest_cache',
               '.ruff_cache'}
SKIP_DIRS = {'.tox', '.nox', '.venv', 'venv', 'env', 'build', 'dist', '.eggs'}
# Copies of other projects kept inside the repository (audit 10-18 #12): not the repository's code, so not candidates,
# copies, callers or import roots. Generated protobuf modules (*_pb2.py) likewise. Migrations are code and stay.
VENDOR_DIRS = {'vendor', '_vendor', 'vendored', '_vendored', 'third_party', '_third_party', 'extern', 'external'}
GENERATED = re.compile(r'_pb2(_grpc)?\.py$')
# A single file larger than this is generated data, not code to read (audit 10-18 #1): left out of the index and the
# impact table, whose build time grows with it
MAX_BYTES = 1_000_000
MAX_LINES = 20_000
TEST_DIRS = {'tests', 'test', 'testing'}
# Documentation, examples, benchmarks and scripts beside the package, by the first word of the top-level directory's
# name (docs, docs_src, doc-examples, example_app, benchmarks ...): naming conventions of Python projects, not the names
# of one repository
AUX_WORDS = {'doc', 'docs', 'example', 'examples', 'sample', 'samples', 'demo', 'demos', 'tutorial', 'tutorials',
             'benchmark', 'benchmarks', 'bench', 'script', 'scripts'}
# Every write the scripts make to the repository bumps this (audit 10-18 #1): the index and the impact table kept for
# this process are made again after it
VERSION = [0]

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)


_ROOT = []


def repo_root():
    """The sandbox working directory (or /workspace), else the git top level of the current directory (found once per
    process: is_aux_name asks for it for every file)."""
    if _ROOT:
        return _ROOT[0]
    for cand in (os.environ.get('SWE_REPO'), os.environ.get('PWD'), '/workspace'):
        if cand and os.path.isdir(os.path.join(cand, '.git')):
            _ROOT.append(cand)
            return cand
    out = git(os.getcwd(), 'rev-parse', '--show-toplevel')
    _ROOT.append(out.strip() if out else (os.environ.get('PWD') or '/workspace'))
    return _ROOT[0]


def git(root, *args, input_text=None, timeout=30):
    """stdout of a git command, or None when it fails."""
    if args and args[0] in ('checkout', 'reset', 'restore', 'stash', 'clean', 'apply'):
        bump()                  # it may change the repository's files
    try:
        r = subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=root, capture_output=True, text=True,
                           input=input_text, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def bump():
    """The repository's files changed: what was computed from them in this process is made again."""
    VERSION[0] += 1


def is_test_path(rel):
    # (kept as it is, audit 10-18 #3: the harness itself drops these files from the patch, swegemma's
    # verification.py _is_protected_test_or_config_path: tests/, test/ and testing/ directories, test_*.py and
    # conftest.py, with the runner configs; an edit there could never reach the score)
    parts = rel.replace('\\', '/').split('/')
    name = parts[-1]
    return (any(p in TEST_DIRS for p in parts[:-1]) or name.startswith('test_') or name.endswith('_test.py')
            or name == 'conftest.py')


def _norm(name):
    return re.sub(r'[-_.]+', '_', name.strip().lower())


def is_aux_name(name, root=None):
    """Whether a directory name is documentation, examples, benchmarks or scripts (its first word). Never the
    repository's own package: one its packaging files declare, or the only package it has (audit 10-18 #13: a flat
    sample_app/ or doc_builder/ package was taken for documentation)."""
    if re.split(r'[_\-.]', name.lower(), maxsplit=1)[0] not in AUX_WORDS:
        return False
    try:
        return name not in _aux_exempt(root or repo_root())
    except Exception:           # noqa: BLE001  (a layout that cannot be read: the naming convention alone)
        return True


def is_doc_path(rel, root=None):
    parts = rel.replace('\\', '/').split('/')
    return len(parts) > 1 and is_aux_name(parts[0], root)


_EXEMPT = {}


def _aux_exempt(root):
    """The directory names that look like documentation by their first word but are the repository's own package:
    declared in pyproject.toml, setup.cfg or setup.py, or the only packages there are (at the top level or one level
    down, as in src/)."""
    if root in _EXEMPT:
        return _EXEMPT[root]
    _EXEMPT[root] = set()            # (no recursion through the calls below)
    declared = declared_packages(root)
    aux, own = set(), False

    def package(p):
        return os.path.isfile(os.path.join(p, '__init__.py'))

    for top in _listdir(root):
        p = os.path.join(root, top)
        if not os.path.isdir(p) or _skip_dir(root, top) or top in TEST_DIRS:
            continue
        first = re.split(r'[_\-.]', top.lower(), maxsplit=1)[0] in AUX_WORDS
        if first:
            if package(p) or _norm(top) in declared:
                aux.add(top)
            continue
        if package(p):
            own = True
            continue
        for sub in _listdir(p):              # a src/-style directory: its packages
            q = os.path.join(p, sub)
            if os.path.isdir(q) and not _skip_dir(p, sub) and sub not in TEST_DIRS and package(q):
                if re.split(r'[_\-.]', sub.lower(), maxsplit=1)[0] in AUX_WORDS:
                    aux.add(sub)
                else:
                    own = True
    out = {a for a in aux if _norm(a) in declared or not own}
    out |= {n for n in declared if re.split(r'[_\-.]', n, maxsplit=1)[0] in AUX_WORDS}
    _EXEMPT[root] = out
    return out


def _listdir(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


_DECLARED = {}


def declared_packages(root):
    """The top-level package and module names the repository's packaging files declare (normalised: lower case, '_'
    for '-' and '.'): pyproject.toml ([project] name, setuptools packages and py-modules, hatch, poetry, flit, maturin),
    setup.cfg and setup.py. Empty when there are none or they cannot be read."""
    if root in _DECLARED:
        return _DECLARED[root]
    names = set()

    def add(n, whole=False):
        if isinstance(n, dict):
            n = n.get('include') or n.get('name')
        if not isinstance(n, str) or not n.strip():
            return
        n = n.strip().replace('\\', '/').rstrip('/').split('/')[-1]
        if not whole:
            n = re.sub(r'\.py$', '', n).split('.')[0].rstrip('*')
        if n and n != '*':
            names.add(_norm(n))

    try:
        import tomllib
        with open(os.path.join(root, 'pyproject.toml'), 'rb') as fh:
            data = tomllib.load(fh)
    except Exception:           # noqa: BLE001  (no file, no tomllib, or invalid TOML)
        data = {}
    try:
        tool = data.get('tool', {}) if isinstance(data.get('tool'), dict) else {}
        add((data.get('project') or {}).get('name'), whole=True)
        st = tool.get('setuptools') or {}
        pk = st.get('packages')
        for x in pk if isinstance(pk, list) else (pk or {}).get('find', {}).get('include', []) if isinstance(pk, dict) \
                else []:
            add(x)
        for x in st.get('py-modules') or []:
            add(x)
        hatch = (((tool.get('hatch') or {}).get('build') or {}).get('targets') or {}).get('wheel') or {}
        for x in (hatch.get('packages') or []) + list((((tool.get('hatch') or {}).get('build') or {}).get('packages'))
                                                       or []):
            add(x)
        poetry = tool.get('poetry') or {}
        add(poetry.get('name'), whole=True)
        for x in poetry.get('packages') or []:
            add(x)
        add(((tool.get('flit') or {}).get('module') or {}).get('name'))
        add((tool.get('maturin') or {}).get('module-name'))
    except Exception:           # noqa: BLE001
        pass
    try:
        import configparser
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(os.path.join(root, 'setup.cfg'), encoding='utf-8')
        if cfg.has_section('metadata'):
            add(cfg.get('metadata', 'name', fallback=''), whole=True)
        if cfg.has_section('options'):
            for key in ('packages', 'py_modules'):
                for x in re.split(r'[\s,]+', cfg.get('options', key, fallback='')):
                    if x and not x.startswith(('find', 'attr')):
                        add(x)
        if cfg.has_section('options.packages.find'):
            for x in re.split(r'[\s,]+', cfg.get('options.packages.find', 'include', fallback='')):
                add(x)
    except Exception:           # noqa: BLE001
        pass
    try:
        with open(os.path.join(root, 'setup.py'), encoding='utf-8', errors='replace') as fh:
            text = fh.read(200_000)
        m = re.search(r'''\bname\s*=\s*['"]([\w.\-]+)['"]''', text)
        if m:
            add(m.group(1), whole=True)
        for key in ('packages', 'py_modules'):
            for lst in re.findall(rf'''\b{key}\s*=\s*\[([^\]]*)\]''', text):
                for x in re.findall(r'''['"]([\w./\-]+)['"]''', lst):
                    add(x)
    except Exception:           # noqa: BLE001
        pass
    _DECLARED[root] = names
    return names


_ROOTS = {}
_BASES = {}
_IMPORTED = {}


def _imported(root):
    """Every dotted module name the repository's files import, and its prefixes (acme.billing.api gives acme and
    acme.billing)."""
    if root in _IMPORTED:
        return _IMPORTED[root]
    out = set()
    for rel in iter_py(root, tests=True):
        text = read_text(root, rel)
        found = re.findall(r'(?m)^[ \t]*from[ \t]+([A-Za-z_][\w.]*)[ \t]+import\b', text)
        for group in re.findall(r'(?m)^[ \t]*import[ \t]+([A-Za-z_][\w. \t,]*)', text):
            found += [x.split()[0] for x in group.split(',') if x.strip()]
        for name in found:
            parts = name.split('.')
            out.update('.'.join(parts[:k]) for k in range(1, len(parts) + 1))
    _IMPORTED[root] = out
    return out


def import_roots(root):
    """The directories the repository's own packages are imported from, most code first, the repository root last.
    A package's import root is the directory above its topmost __init__.py, so a src/, lib/ or python/ layout is found
    from the files themselves; documentation, examples and tests are not import roots. A directory without __init__.py
    above the package is climbed when the repository's own imports name it (a PEP 420 namespace package: src/acme/
    billing/ imported as acme.billing has the import root src/, not src/acme/; audit 10-18 #4)."""
    if root in _ROOTS:
        return _ROOTS[root]
    count, bases = {}, {}
    for rel in iter_py(root, docs=False):
        parts = rel.split('/')
        top = None
        for i in range(len(parts) - 1, 0, -1):        # the outermost directory of an unbroken chain of packages
            if os.path.isfile(os.path.join(root, *parts[:i], '__init__.py')):
                top = i - 1
            else:
                break
        package = top is not None
        while top:
            # parts[top] is the topmost package; parts[top - 1] a directory without __init__.py
            if '.'.join(parts[top - 1:top + 1]) not in _imported(root):
                break
            top -= 1
        if top is None and len(parts) > 1 and parts[-1] != '__init__.py':
            # a module outside any package (src/mod.py of a single-module project): its directory is an import
            # root when the repository imports the module by its own name
            stem = parts[-1][:-3]
            if stem in _imported(root) and '.'.join(parts[:-1] + [stem]) not in _imported(root):
                top = len(parts) - 1
        if top:                                       # 0: a package at the repository root, which comes last anyway
            base = '/'.join(parts[:top])
            count[base] = count.get(base, 0) + 1
        if top is not None:
            bases[rel] = (os.path.join(root, '/'.join(parts[:top])) if top else root, package)
    _ROOTS[root] = [os.path.join(root, b) for b in sorted(count, key=lambda b: (-count[b], b))] + [root]
    _BASES[root] = bases
    return _ROOTS[root]


def module_name(root, rel):
    """The import name of a source file (pkg/sub/mod.py -> pkg.sub.mod), relative to the import root that holds it."""
    path = os.path.join(root, rel)
    roots = import_roots(root)
    base = (_BASES.get(root, {}).get(rel) or (None,))[0]
    if base is None:
        base = next((b for b in roots if b != root and path.startswith(b + os.sep)), None)
    if base is not None and base != root:
        rel = os.path.relpath(path, base).replace(os.sep, '/')
    mod = rel[:-3].replace('/', '.')
    return mod[:-9] if mod.endswith('.__init__') else mod


def pythonpath_roots(root):
    """The import roots to put on the tests' PYTHONPATH: the repository root always, another root only when none of
    its packages or modules has a standard library module's name (audit 10-18 #4: src/acme/ on the path made its http/
    and logging/ shadow the standard library's for every test)."""
    out = []
    for base in import_roots(root):
        if base != root:
            names = {re.sub(r'\.py$', '', n) for n in _listdir(base) if n.endswith('.py')} | {
                n for n in _listdir(base) if os.path.isfile(os.path.join(base, n, '__init__.py'))}
            if names & set(sys.stdlib_module_names):
                continue
        out.append(base)
    return out


def in_package(root, rel):
    """Whether the file is inside a package (a chain of __init__.py, or a namespace package the repository imports),
    not a module on its own."""
    import_roots(root)
    return (_BASES.get(root, {}).get(rel) or (None, False))[1]


_OWN = {}


def own_names(root):
    """The top-level import names of the repository's own code (packages, namespace packages and modules), from its
    files: a library of the same name installed in the environment is still the repository's code (audit 10-18 #8)."""
    if root not in _OWN:
        names = set()
        for rel in iter_py(root, docs=False):
            mod = module_name(root, rel)
            if in_package(root, rel) or '.' not in mod:      # (not ci/check.py: no import reaches it by that name)
                names.add(mod.split('.')[0])
        _OWN[root] = names - {''}
    return _OWN[root]


def _skip_dir(dirpath, d):
    if d in ALWAYS_SKIP or d.startswith('.') or d.endswith('.egg-info') or d in VENDOR_DIRS:
        return True
    return d in SKIP_DIRS and not os.path.isfile(os.path.join(dirpath, d, '__init__.py'))


def iter_py(root, tests=False, docs=True):
    """Repository-relative paths of the Python files, sorted. Hidden files are never repository code (the harness
    drops .adk_exec_*.py runners in /workspace); nor are environments, build outputs, vendored copies of other
    projects or generated protobuf modules."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip_dir(dirpath, d)]
        for fn in filenames:
            if not fn.endswith('.py') or fn.startswith('.') or GENERATED.search(fn):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, '/')
            if (is_test_path(rel) and not tests) or (is_doc_path(rel, root) and not docs):
                continue
            out.append(rel)
    return sorted(out)


_MEMO = {}


def memo(root, key, files, build):
    """build(files()) once per process for this key (audit 10-18 #1: the index and the impact table were made up to 17
    times in one call). Made again after any write of the scripts (VERSION) or when a file appeared, went or changed
    size or time (a test or another process wrote it)."""
    rels = files()
    stamp = [VERSION[0]]
    for rel in rels:
        try:
            st = os.stat(os.path.join(root, rel))
            stamp.append((rel, st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append((rel, None))
    k = (os.path.abspath(root),) + tuple(key)
    hit = _MEMO.get(k)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    obj = build(rels)
    _MEMO[k] = (stamp, obj)
    return obj


def _encoding(data):
    """The encoding a Python source declares (PEP 263 coding cookie or BOM), as a codec name; utf-8 by default."""
    try:
        enc, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        enc = codecs.lookup(enc).name
    except (SyntaxError, LookupError, UnicodeError, ValueError):
        return 'utf-8'
    return 'utf-8' if enc in ('utf-8', 'utf-8-sig') else enc


def encode_source(text):
    """The bytes of a source text in the encoding its own coding cookie declares (utf-8 when none), undecodable bytes
    kept (surrogateescape). ValueError when a character cannot be written in that encoding."""
    head = ''.join(text.splitlines(keepends=True)[:2]).encode('utf-8', errors='surrogateescape')
    enc = _encoding(head)
    try:
        return text.encode(enc, errors='surrogateescape')
    except UnicodeEncodeError as e:
        raise ValueError(f'the file is written in {enc} (its coding line), which cannot hold the character '
                         f'{text[e.start:e.end]!r}') from None


def read_text(root, rel):
    """The file as text for analysis (undecodable bytes replaced); '' when it cannot be read."""
    try:
        with open(os.path.join(root, rel), 'rb') as fh:
            data = fh.read()
    except OSError:
        return ''
    return data.decode(_encoding(data), errors='replace')


def read_code(root, rel):
    """The file's text for the index and the impact table, or None when it is too big to be hand-written code."""
    try:
        if os.path.getsize(os.path.join(root, rel)) > MAX_BYTES:
            return None
    except OSError:
        return None
    text = read_text(root, rel)
    return None if text.count('\n') > MAX_LINES else text


def read_lines(root, rel):
    """The file's lines with their own line ends, decoded so that writing them back gives the same bytes (CRLF, a
    coding cookie's encoding and invalid bytes survive: audit 10-18 #6)."""
    with open(os.path.join(root, rel), 'rb') as fh:
        data = fh.read()
    return data.decode(_encoding(data), errors='surrogateescape').splitlines(keepends=True)


def write_lines(root, rel, lines):
    path = os.path.join(root, rel)
    data = encode_source(''.join(lines))
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    bump()
    with open(path, 'wb') as fh:
        fh.write(data)


def newline_of(lines):
    """The line end the file uses ('\\r\\n' or '\\n')."""
    for line in lines:
        if line.endswith('\r\n'):
            return '\r\n'
        if line.endswith('\n'):
            return '\n'
    return '\n'


def worktree_diff(root):
    """The change the submitted patch will contain: git diff of the tracked files plus every new file (not hidden,
    not a cache) as added lines. None when git fails."""
    diff = git(root, 'diff', '--binary')
    new = git(root, 'ls-files', '--others', '--exclude-standard')
    if diff is None or new is None:
        return None
    for rel in sorted(new.splitlines()):
        parts = rel.split('/')
        if not rel or any(p.startswith('.') or p == '__pycache__' for p in parts) or rel.endswith('.pyc'):
            continue
        text = read_text(root, rel)
        diff += f'+++ b/{rel} (new file)\n' + ''.join('+' + line + '\n' for line in text.splitlines())
    return diff
