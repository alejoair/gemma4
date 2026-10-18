"""The task repository: where it is, its Python files, exact reads and writes, and the diff the patch will contain."""
import os
import re
import subprocess

SKIP_DIRS = {'.git', '.hg', '.tox', '.nox', '.venv', 'venv', 'env', 'node_modules', 'build', 'dist', '__pycache__',
             '.mypy_cache', '.pytest_cache', '.ruff_cache', 'site-packages', '.eggs'}
TEST_DIRS = {'tests', 'test', 'testing'}
# Documentation, examples, benchmarks and scripts beside the package, by the first word of the top-level directory's
# name (docs, docs_src, doc-examples, example_app, benchmarks ...): naming conventions of Python projects, not the names
# of one repository
AUX_WORDS = {'doc', 'docs', 'example', 'examples', 'sample', 'samples', 'demo', 'demos', 'tutorial', 'tutorials',
             'benchmark', 'benchmarks', 'bench', 'script', 'scripts'}

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)


def repo_root():
    """The sandbox working directory (or /workspace), else the git top level of the current directory."""
    for cand in (os.environ.get('SWE_REPO'), os.environ.get('PWD'), '/workspace'):
        if cand and os.path.isdir(os.path.join(cand, '.git')):
            return cand
    out = git(os.getcwd(), 'rev-parse', '--show-toplevel')
    return out.strip() if out else (os.environ.get('PWD') or '/workspace')


def git(root, *args, input_text=None, timeout=30):
    """stdout of a git command, or None when it fails."""
    try:
        r = subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=root, capture_output=True, text=True,
                           input=input_text, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def is_test_path(rel):
    parts = rel.replace('\\', '/').split('/')
    name = parts[-1]
    return (any(p in TEST_DIRS for p in parts[:-1]) or name.startswith('test_') or name.endswith('_test.py')
            or name == 'conftest.py')


def is_aux_name(name):
    """Whether a top-level directory name is documentation, examples, benchmarks or scripts (its first word)."""
    return re.split(r'[_\-.]', name.lower(), maxsplit=1)[0] in AUX_WORDS


def is_doc_path(rel):
    parts = rel.replace('\\', '/').split('/')
    return len(parts) > 1 and is_aux_name(parts[0])


_ROOTS = {}


def import_roots(root):
    """The directories the repository's own packages are imported from, most code first, the repository root last.
    A package's import root is the directory above its topmost __init__.py, so a src/, lib/ or python/ layout is found
    from the files themselves; documentation, examples and tests are not import roots."""
    if root in _ROOTS:
        return _ROOTS[root]
    count = {}
    for rel in iter_py(root, docs=False):
        parts = rel.split('/')
        top = None
        for i in range(len(parts) - 1, 0, -1):        # the outermost directory of an unbroken chain of packages
            if os.path.isfile(os.path.join(root, *parts[:i], '__init__.py')):
                top = i - 1
            else:
                break
        if top:                                       # 0: a package at the repository root, which comes last anyway
            base = '/'.join(parts[:top])
            count[base] = count.get(base, 0) + 1
    _ROOTS[root] = [os.path.join(root, b) for b in sorted(count, key=lambda b: (-count[b], b))] + [root]
    return _ROOTS[root]


def module_name(root, rel):
    """The import name of a source file (pkg/sub/mod.py -> pkg.sub.mod), relative to the import root that holds it."""
    path = os.path.join(root, rel)
    for base in import_roots(root):
        if base != root and path.startswith(base + os.sep):
            rel = os.path.relpath(path, base).replace(os.sep, '/')
            break
    mod = rel[:-3].replace('/', '.')
    return mod[:-9] if mod.endswith('.__init__') else mod


def iter_py(root, tests=False, docs=True):
    """Repository-relative paths of the Python files, sorted. Hidden files are never repository code (the harness
    drops .adk_exec_*.py runners in /workspace)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith('.') and not d.endswith('.egg-info')]
        for fn in filenames:
            if not fn.endswith('.py') or fn.startswith('.'):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, '/')
            if (is_test_path(rel) and not tests) or (is_doc_path(rel) and not docs):
                continue
            out.append(rel)
    return sorted(out)


def read_text(root, rel):
    """The file as text for analysis (undecodable bytes replaced); '' when it cannot be read."""
    try:
        with open(os.path.join(root, rel), encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def read_lines(root, rel):
    """The file's lines with their own line ends, decoded so that writing them back gives the same bytes (CRLF and
    invalid UTF-8 survive)."""
    with open(os.path.join(root, rel), 'rb') as fh:
        data = fh.read()
    return data.decode('utf-8', errors='surrogateescape').splitlines(keepends=True)


def write_lines(root, rel, lines):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(''.join(lines).encode('utf-8', errors='surrogateescape'))


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
