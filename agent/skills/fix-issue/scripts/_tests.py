"""Regression check of an edit (docs/old_scripts_lessons.md, Tests and verification): the existing tests that use the
changed names, run with time limits; a failing test counts against the edit only if it passes on the original code
(run in a separate git worktree). Also the last verified state: the content of the changed files when the tests last
passed, to go back to when an edit breaks them."""
import ast
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time

import _repo
import _state

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

FILE_TIMEOUT = 60      # seconds per test group (groups run at once share it; one after the other, each has its own)
BASE_TIMEOUT = 60      # the same groups on the original code
MAP_TIMEOUT = 150      # the once-per-task run of the whole suite on the original code that maps functions to tests
TEST_TIMEOUT = 15      # seconds per test, when pytest-timeout is installed (a hanging test then costs 15 s, not 60)
# The latest moment any test run of this script call may end (time.time()), or None. vf7 on Kaggle (rich_3772): an
# edit's checks ran the groups one after another, 60 s each, past the 5-minute stop; the harness cannot end a tool
# call, so the task took 604 s of the competition's 12 h. The caller sets it from the task's remaining time.
DEADLINE = None


def _cap(t):
    return min(t, DEADLINE) if DEADLINE else t


def _budget(seconds, floor=1.0):
    """A subprocess timeout of at most seconds that ends by DEADLINE (audit 10-18 #10: the 60 s import probes and the
    120 s worktree ignored it)."""
    return max(floor, min(seconds, DEADLINE - time.time())) if DEADLINE else seconds


MEM_LIMIT = 3 << 30    # address space of one pytest process (the sandbox has 4 GiB)
MAX_FILES = 3
MAX_IDS = 40           # tests of one file taken from the call map
BIG_FILE = 40          # a test file with more tests than this runs only the tests that use the changed names
PYTEST = ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--no-header', '-rfE', '--maxfail=60']   # E: setup errors count as failures
# the call map's run of the whole suite: one test file that cannot be collected must not stop it (audit 10-18 #14: the
# map came out empty and its 150 s were lost)
PYTEST_WHOLE = [f for f in PYTEST if not f.startswith('--maxfail')] + ['--continue-on-collection-errors']
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{2,}')
COMMON = {'self', 'cls', 'None', 'True', 'False', 'return', 'import', 'from', 'def', 'class', 'assert', 'raise', 'not',
          'and', 'for', 'while', 'with', 'pass', 'elif', 'else', 'try', 'except', 'finally', 'lambda', 'yield',
          'async', 'await', 'str', 'int', 'list', 'dict', 'len', 'isinstance', 'value', 'name', 'args', 'kwargs'}


def _module_names(root, rel):
    """Import names of a source file: pkg/sub/mod.py -> pkg.sub.mod, relative to its import root (src/, lib/ ...)."""
    return [_repo.module_name(root, rel)]


def _test_functions(tree):
    """[(node id suffix, first line, last line)] of the test functions, methods of Test classes included, and of
    unittest.TestCase classes whatever their name (pytest collects them; audit 10-18 #15), also through a base class
    of the same file."""
    out = []
    cases = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
                (b.attr if isinstance(b, ast.Attribute) else getattr(b, 'id', '')).endswith('TestCase') or
                getattr(b, 'id', None) in cases for b in node.bases):
            cases.add(node.name)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test'):
            out.append((node.name, node.lineno, node.end_lineno))
        elif isinstance(node, ast.ClassDef) and (node.name.startswith('Test') or node.name.endswith('Test')
                                                 or node.name in cases):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith('test'):
                    out.append((f'{node.name}::{item.name}', item.lineno, item.end_lineno))
    return out


def _is_test_file(rel):
    """A file pytest collects (test_*.py or *_test.py): tests/main.py or tests/testserver/server.py are helpers, not
    tests, and took the 3 places of the selection (audit)."""
    name = os.path.basename(rel)
    return rel.endswith('.py') and (name.startswith('test_') or name.endswith('_test.py'))


def select(root, changed, use_map=True):
    """pytest targets for the changed code. changed: {rel: [changed line texts and symbol names]}. First the tests that
    call a changed function on the original code (the call map), grouped by file, the files with most such tests
    first. Without the map, test files score by being the changed module's own test file (test_<module>.py), then by
    importing a changed module, then by the rare names they share with the changed code (inverse document frequency);
    in a big file, or one that ran out of time before, only the tests that use those names are kept."""
    names = set()
    for items in changed.values():
        for item in items:
            names.update(w for w in IDENT.findall(item) if w not in COMMON)
    m = callmap(root) if use_map else None   # the stages build's review check: lexical selection only (no suite run)
    if m:
        by_file = {}
        for key, tids in m.items():
            krel, _, qual = key.partition('::')
            if krel in changed and qual.split('.')[-1] in names:
                for t in tids:
                    if _is_test_file(t.split('::')[0]):
                        by_file.setdefault(t.split('::')[0], set()).add(t)
        if by_file:
            ranked = sorted(by_file, key=lambda f: (-len(by_file[f]), f))[:MAX_FILES]
            return [sorted(by_file[f])[:MAX_IDS] for f in ranked]
    tests = [r for r in _repo.iter_py(root, tests=True) if _is_test_file(r)]
    if not tests:
        return []
    slow = set(_state.load('slow_tests', []))
    texts = {r: _repo.read_text(root, r) for r in tests}
    words = {r: set(IDENT.findall(t)) for r, t in texts.items()}
    modules = [m_ for rel in changed for m_ in _module_names(root, rel)]
    n = len(tests)
    idf = {w: math.log(1 + n / (1 + sum(w in ws for ws in words.values()))) for w in names}
    stems = {os.path.basename(rel)[:-3].lstrip('_') for rel in changed if rel.endswith('.py')} - {'__init__'}
    scores = []
    for r in tests:
        s = sum(idf[w] for w in names if w in words[r])
        base = os.path.basename(r)[:-3]
        if base in {f'test_{x}' for x in stems} | {f'{x}_test' for x in stems}:
            s += 20.0                      # the module's own test file (rich/repr.py -> tests/test_repr.py)
        if any(re.search(rf'\b{re.escape(m_)}\b', texts[r]) for m_ in modules):
            s += 6.0                       # imports the changed module
        if s > 0:
            scores.append((s, r))
    scores.sort(key=lambda x: (-x[0], x[1]))
    targets = []
    for _, r in scores[:MAX_FILES]:
        try:
            tree = ast.parse(texts[r])
        except (SyntaxError, ValueError):
            continue
        funcs = _test_functions(tree)
        if len(funcs) > BIG_FILE or r in slow:
            lines = texts[r].splitlines()
            keep = [f'{r}::{name}' for name, a, b in funcs
                    if names & set(IDENT.findall('\n'.join(lines[a - 1:b])))]
            if keep:
                targets.append(keep)
                continue
            if r in slow:
                continue
        targets.append([r])
    return targets


COMMON_IN_TESTS = 12     # a name used in more test files than this says little: only its own test file is searched


def example(root, rel, name, max_lines=6):
    """(test file, its lines) of the shortest existing test that uses the place's name (a call, an attribute or a
    decorator): the module's own test file first, then tests in files that also name the owner class. A name used in
    many test files is looked up only in the module's own test file. None when no test uses it, and for functions
    nested in functions (no test can call them)."""
    parts = name.split('.')
    short = parts[-1]
    if short.startswith('__') and len(parts) > 1:
        short, parts = parts[-2], parts[:-1]            # Class.__init__ -> Class
    if not re.fullmatch(r'\w{3,}', short) or short in COMMON:
        return None
    if len(parts) > 2 or (len(parts) == 2 and not parts[0][:1].isupper()):
        return None                                     # setup.openapi: a closure
    owner = parts[0] if len(parts) == 2 else None
    pattern = re.compile(rf'(?<![\w]){re.escape(short)}\b')
    stem = os.path.basename(rel)[:-3].lstrip('_')
    own = {f'test_{stem}.py', f'{stem}_test.py'}
    files = []
    for r in _repo.iter_py(root, tests=True):
        if not _repo.is_test_path(r) or os.path.basename(r) == 'conftest.py':
            continue
        text = _repo.read_text(root, r)
        if pattern.search(text):
            files.append((r, text))
    if len(files) > COMMON_IN_TESTS:
        files = [(r, t) for r, t in files if os.path.basename(r) in own]
    best = None
    for r, text in files:
        try:
            funcs = _test_functions(ast.parse(text))
        except (SyntaxError, ValueError):
            continue
        lines = text.splitlines()
        names_owner = owner is None or owner in text
        for fname, a, b in funcs:
            body = lines[a - 1:b]
            if not any(pattern.search(l) for l in body):
                continue
            key = (os.path.basename(r) not in own, not names_owner, max(b - a + 1, 4), r, a)
            if best is None or key < best[0]:
                best = (key, r, body)
    if best is None:
        return None
    _, r, body = best
    if len(body) > max_lines:
        body = body[:max_lines] + ['    ...']
    return r, body


def _env(root, extra=()):
    env = dict(os.environ)
    for k in list(env):
        if k.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
            # the sandbox has no proxy; a proxy in the environment made 8 of requests' tests wait 5 s each (audit)
            del env[k]
    paths = list(extra) + _repo.pythonpath_roots(root)    # the repository's own import roots, the root last
    env['PYTHONPATH'] = os.pathsep.join(paths + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    # numeric libraries start one thread per core, each reserving address space: under RLIMIT_AS a test process that
    # imports numpy or torch failed before any test ran (audit 10-18 #11)
    for k in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        env[k] = '1'
    return env


PLUGIN = r'''"""Per-test outcomes, written as each test ends (a run killed at its deadline keeps what finished), and with
sys.monitoring (Python 3.12+) the wanted lines each test ran and the functions each test called (the call map)."""
import json, os, sys
_fh = open(os.environ["SWE_OUTCOMES"], "a", buffering=1) if os.environ.get("SWE_OUTCOMES") else None
_want = {os.path.realpath(k): set(v) for k, v in json.loads(os.environ.get("SWE_COVER_FILES", "{}")).items()}
_root = os.path.realpath(os.environ.get("SWE_CALLMAP_ROOT", "")) if os.environ.get("SWE_CALLMAP_ROOT") else ""
_lines, _calls, _real = set(), set(), {}

_prefix = [""]

def pytest_configure(config):
    # node ids are relative to pytest's rootdir (rich's is tests/): written relative to the working directory, so
    # that they can be passed back to pytest from there
    rel = os.path.relpath(str(config.rootpath), os.getcwd())
    _prefix[0] = "" if rel == "." else rel.replace(os.sep, "/") + "/"

def _w(rec):
    if _fh is not None:
        if "id" in rec and _prefix[0] and not rec["id"].startswith("<"):
            rec["id"] = _prefix[0] + rec["id"]
        _fh.write(json.dumps(rec) + "\n")

def _real_of(code):
    f = _real.get(code.co_filename)
    if f is None:
        f = _real[code.co_filename] = os.path.realpath(code.co_filename)
    return f

_mon = getattr(sys, "monitoring", None)
_tool = None
if _mon is not None and (_want or _root):
    _tool = next((i for i in (3, 4, 1) if _mon.get_tool(i) is None), None)
if _tool is not None:
    _mon.use_tool_id(_tool, "swe-tests")
    events = 0
    if _want:
        def _line(code, n):
            f = _real_of(code)
            if n in _want.get(f, ()):
                _lines.add((f, n))
            return _mon.DISABLE
        _mon.register_callback(_tool, _mon.events.LINE, _line)
        events |= _mon.events.LINE
    if _root:
        def _start(code, offset):
            f = _real_of(code)
            if f.startswith(_root):
                _calls.add((f, code.co_qualname))
            return _mon.DISABLE
        _mon.register_callback(_tool, _mon.events.PY_START, _start)
        events |= _mon.events.PY_START
    _mon.set_events(_tool, events)

def _short(report):
    try:
        text = str(report.longrepr)
    except Exception:
        return "", ""
    lines = text.splitlines()
    marked = next((l[1:].strip() for l in lines if l.startswith(">")), "")
    errs = [l[1:].strip() for l in lines if l.startswith("E ")]
    if not errs:
        errs = [l.strip() for l in lines[-4:] if l.strip()]
    return marked[:300], "\n".join(e[:300] for e in errs[:8])

def _frames(report):
    """The failure's traceback entries as 'path:line', outermost first (gk13-gk15: locate needs where the failure
    happened in the repository's code, not only the test's own marked line)."""
    out = []
    try:
        for e in report.longrepr.reprtraceback.reprentries:
            loc = getattr(e, "reprfileloc", None)
            if loc is not None:
                out.append(f"{loc.path}:{loc.lineno}")
    except Exception:
        pass
    return out[-12:]

def pytest_collectreport(report):
    if report.failed:
        marked, msg = _short(report)
        _w({"id": report.nodeid or "<collection>", "o": "error", "msg": msg, "marked": marked})

def pytest_runtest_logstart(nodeid, location):
    _lines.clear()
    _calls.clear()
    sys._swe_stub_eq = False           # set by a stand-in module's object when it compares equal to something
    if _tool is not None:
        _mon.restart_events()          # every line and function counts again for this test

def pytest_runtest_logreport(report):
    if report.when == "call" or report.outcome != "passed":
        if report.outcome == "failed":
            o = "failed" if report.when == "call" else "error"
        else:
            o = report.outcome
        if o == "passed" and report.when == "call" and getattr(sys, "_swe_stub_eq", False):
            o = "skipped"                  # it passed because a stand-in equals anything: not a pass
        rec = {"id": report.nodeid, "o": o}
        if o in ("failed", "error"):
            rec["marked"], rec["msg"] = _short(report)
            rec["frames"] = _frames(report)
        _w(rec)
    if report.when == "teardown":
        if _lines or _calls:
            _w({"id": report.nodeid, "lines": sorted(_lines), "calls": sorted(_calls)})
'''

RANK = {'passed': 0, 'skipped': 1, 'failed': 2, 'error': 3}
# what a process killed by the address-space limit prints (MemoryError, a thread or an allocation that fails)
NO_MEMORY = re.compile(r"\bMemoryError\b|Cannot allocate memory|can't start new thread|pthread_create|std::bad_alloc|"
                       r"(?i:out of memory)|OpenBLAS[^\n]*(?:memory|thread)|failed to (?:map|allocate)")
PORT_CLASH = re.compile(r'Address already in use|EADDRINUSE|\[Errno 98\]|\[Errno 48\]')
NO_MODULE = re.compile(r"No module named '([\w.]+)'")


def _plugin_dir():
    """A directory holding the plugin, outside the skill's files (they are deleted when the script ends)."""
    d = os.path.join(_state.directory(), 'plugin')
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, 'swe_tests_plugin.py')
    if not os.path.exists(path) or open(path).read() != PLUGIN:
        with open(path, 'w') as fh:
            fh.write(PLUGIN)
    return d


def _stub_dir(modules):
    """A directory of stand-in modules for test-only packages the environment lacks, one directory per set of
    modules (a stand-in never outlives the run that needs it: audit, a stub written once shadowed later runs). Every
    attribute is a stand-in object; called with one plain argument it returns that argument, so that a wrapper such as
    snapshot(value) or Is(value) compares the value itself: 290 of fastapi's 468 test files assert
    `== snapshot({...})`, and a stand-in equal to anything made them pass whatever the code did (fastapi_14986, V13b).
    A stand-in still equal to anything marks the test it ran in (sys._swe_stub_eq), and the plugin does not count that
    test as passed (audit 10-18 #17: the rule was inline_snapshot's alone)."""
    key = '_'.join(sorted(modules))
    d = os.path.join(_state.directory(), 'stubs', re.sub(r'\W', '_', key)[:80])
    os.makedirs(d, exist_ok=True)
    body = ('import sys as _sys\n'
            'def _used():\n    _sys._swe_stub_eq = True\n'
            'class _Any:\n    def __init__(self, *a, **k): pass\n'
            '    def __call__(self, *a, **k): return a[0] if len(a) == 1 and not k else _Any()\n'
            '    def __eq__(self, other):\n        _used()\n        return True\n'
            '    def __ne__(self, other):\n        _used()\n        return False\n'
            '    def __getattr__(self, name): return _Any()\n    def __repr__(self): return "<any>"\n'
            '    def __or__(self, other): return self\n    __ror__ = __or__\n    def __iter__(self): return iter(())\n'
            '    __hash__ = object.__hash__\n'
            'def __getattr__(name): return _Any()\n')
    for m in modules:
        with open(os.path.join(d, m + '.py'), 'w') as fh:
            fh.write(body)
    return d


def _installed(top):
    """Whether a top-level module can be imported here (an installed package is never replaced by a stand-in)."""
    try:
        import importlib.util
        return importlib.util.find_spec(top) is not None
    except (ImportError, ValueError):
        return False


def _test_imports(root):
    """Top-level modules that the test files and conftests import: the only ones a stand-in may replace (a stand-in
    for email_validator, which pydantic imports, made pydantic fail on its missing metadata and every fastapi test file
    with it)."""
    found = set()
    for rel in _repo.iter_py(root, tests=True):
        if not _repo.is_test_path(rel):
            continue
        for m in re.findall(r'(?m)^\s*(?:from|import)\s+([A-Za-z_]\w*)', _repo.read_text(root, rel)):
            found.add(m)
    return found


def _missing_modules(root, text):
    """Top-level modules the output says are missing, that the tests import, and that are neither in the repository
    nor installed."""
    out = set()
    imported = None
    for m in NO_MODULE.findall(text):
        top = m.split('.')[0]
        if '.' in m and _installed(top):
            continue                     # a submodule of an installed package: the code's own error
        if any(os.path.exists(os.path.join(base, top)) or os.path.exists(os.path.join(base, top + '.py'))
               for base in _repo.import_roots(root)) or _installed(top):
            continue
        if imported is None:
            imported = _test_imports(root)
        if top in imported:
            out.add(top)
    return out


def _untracked(cwd):
    out = _repo.git(cwd, 'ls-files', '--others', '--exclude-standard') or ''
    return {l.strip() for l in out.splitlines() if l.strip()}


def _limit_memory():
    """A pytest process may not take the whole sandbox (4 GiB): a test that hangs or leaks dies alone (rich_3772 and
    rich_3480 run out of memory on the original code)."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
    except (ImportError, ValueError, OSError):
        pass


def _tracked_changes(cwd):
    """{rel: bytes} of the tracked files that differ from the commit now (None: deleted)."""
    out = {}
    for rel in (_repo.git(cwd, 'diff', '--name-only', 'HEAD') or '').splitlines():
        try:
            with open(os.path.join(cwd, rel), 'rb') as fh:
                out[rel] = fh.read()
        except OSError:
            out[rel] = None
    return out


def _restore_tracked(cwd, before):
    """Puts back the tracked files a test run changed (audit 10-18 #18: a test that rewrites its golden files or
    snapshots left them changed, in the patch): those changed during the run to the commit, those already changed
    before it to their content then."""
    now = _tracked_changes(cwd)
    for rel, data in now.items():
        if rel not in before:
            _repo.git(cwd, 'checkout', '--', rel)
        elif data != before[rel] and before[rel] is not None:
            _repo.bump()
            with open(os.path.join(cwd, rel), 'wb') as fh:
                fh.write(before[rel])


def _run_groups(cwd, targets, timeout, extra, flags, env_extra, serial, limit=True):
    """One pytest process per target group, all at once or (serial) one after the other; per group: outcomes
    {id: o}, messages {id: (marked line, E lines)}, hits {id: (lines, calls)}, timed out, exit code, output."""
    before = _untracked(cwd)
    tracked = _tracked_changes(cwd)
    results, procs = [], []
    deadline = _cap(time.time() + timeout)
    for k, group in enumerate(targets):
        if DEADLINE and time.time() >= DEADLINE:
            break                       # no time left: the remaining groups are not started
        fd, outcomes = tempfile.mkstemp(prefix='swe_outcomes_', suffix='.jsonl')
        os.close(fd)
        out = tempfile.TemporaryFile('w+')
        env = _env(cwd, extra)
        env.update(env_extra, SWE_OUTCOMES=outcomes)
        p = subprocess.Popen([sys.executable] + flags + list(group), cwd=cwd, env=env, stdout=out,
                             stderr=subprocess.STDOUT, text=True, preexec_fn=_limit_memory if limit else None)
        procs.append((group, p, out, outcomes))
        if serial:
            deadline = _cap(time.time() + timeout)
            try:
                p.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                pass
    for group, p, out, outcomes in procs:
        timed_out = False
        try:
            p.wait(timeout=max(0.1, deadline - time.time()) if not serial else 0.1)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            timed_out = True
        out.seek(0)
        text = out.read()
        out.close()
        res = {'group': list(group), 'outcomes': {}, 'msgs': {}, 'frames': {}, 'hits': {}, 'timeout': timed_out,
               'exit': p.returncode, 'output': text}
        try:
            with open(outcomes) as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if 'o' in rec:
                        old = res['outcomes'].get(rec['id'])
                        if old is None or RANK[rec['o']] > RANK[old]:
                            res['outcomes'][rec['id']] = rec['o']
                        if rec['o'] in ('failed', 'error'):
                            res['msgs'].setdefault(rec['id'], (rec.get('marked', ''), rec.get('msg', '')))
                            res['frames'].setdefault(rec['id'], rec.get('frames') or [])
                    else:
                        res['hits'][rec['id']] = (rec.get('lines', []), rec.get('calls', []))
        except OSError:
            pass
        finally:
            try:
                os.remove(outcomes)
            except OSError:
                pass
        results.append(res)
    for rel in _untracked(cwd) - before:
        # files a test writes into the working directory (fastapi's templates test copies static/ and templates/
        # there): they would enter the patch (audit)
        try:
            os.remove(os.path.join(cwd, rel))
        except OSError:
            pass
    _restore_tracked(cwd, tracked)
    return results


def run(root, targets, timeout=None, cwd=None, stubs=(), cover=None, callmap=False, whole=False):
    """Runs the target groups with the outcome plugin. Returns {'ran', 'outcomes', 'msgs', 'covered', 'calls',
    'timeouts' (groups killed at the deadline), 'missing' (modules the output says are missing), 'output', 'broken_run'
    (groups that ran no test and ended with an error, e.g. a conftest that cannot import)}."""
    timeout = timeout or FILE_TIMEOUT
    cwd = cwd or root
    extra = [_plugin_dir()] + ([_stub_dir(stubs)] if stubs else [])
    flags = (PYTEST_WHOLE if whole else PYTEST) + ['-p', 'swe_tests_plugin'] + (
        [f'--timeout={TEST_TIMEOUT}'] if _has_pytest_timeout() else [])
    limit = not _state.load('no_mem_limit', False)
    env_extra = {}
    if cover:
        env_extra['SWE_COVER_FILES'] = json.dumps({os.path.join(cwd, rel): sorted(lines) for rel, lines in cover.items()})
    if callmap:
        env_extra['SWE_CALLMAP_ROOT'] = cwd
    serial = bool(_state.load('serial_tests', False)) and len(targets) > 1
    res = _run_groups(cwd, targets, timeout, extra, flags, env_extra, serial, limit)
    if not serial and len(targets) > 1 and any(PORT_CLASH.search(g['output']) for g in res):
        # httpx_3672 (V20, V21, a local run): the test files start servers on one fixed port, so the processes
        # failed each other with 'Address already in use'; the groups run again one after the other, and so for the
        # rest of the task
        _state.save('serial_tests', True)
        res = _run_groups(cwd, targets, timeout, extra, flags, env_extra, True, limit)
    out = {'ran': False, 'outcomes': {}, 'msgs': {}, 'frames': {}, 'covered': None, 'calls': {}, 'timeouts': [],
           'missing': set(), 'output': '', 'broken_run': []}
    base = os.path.realpath(cwd)
    for g in res:
        if 'No module named pytest' in g['output'] or 'pytest is disabled' in g['output']:
            continue
        out['ran'] = True
        out['output'] += g['output']
        out['missing'] |= _missing_modules(cwd, g['output'])
        out['outcomes'].update(g['outcomes'])
        out['msgs'].update(g['msgs'])
        out['frames'].update(g.get('frames', {}))
        if g['timeout']:
            out['timeouts'].append(g['group'])
        elif not g['outcomes'] and g['exit'] not in (0, 5):
            out['broken_run'].append(g['group'])
        for tid, (lines, calls) in g['hits'].items():
            if cover and lines and g['outcomes'].get(tid) == 'passed':
                # only tests that passed count: a test that ran the line and then failed anyway checks nothing
                out['covered'] = out['covered'] or set()
                out['covered'] |= {(os.path.relpath(f, base), n) for f, n in lines}
            for f, qual in calls:
                out['calls'].setdefault(f'{os.path.relpath(f, base)}::{qual}', []).append(tid)
    if cover and out['covered'] is None and getattr(sys, 'monitoring', None) is not None and out['outcomes']:
        out['covered'] = set()
    if cover and getattr(sys, 'monitoring', None) is None:
        out['covered'] = None
    return out


def _has_pytest_timeout():
    try:
        import importlib.util
        return importlib.util.find_spec('pytest_timeout') is not None
    except (ImportError, ValueError):
        return False


COPY_FILE = 64 << 20    # an untracked file larger than this is not copied into the original code's worktree
COPY_TOTAL = 256 << 20


def _base_worktree(root):
    """A git worktree of the original commit, made once per task, outside the repository."""
    path = os.path.join(_state.directory(), 'original')
    if not os.path.isdir(os.path.join(path, '.git')) and not os.path.isfile(os.path.join(path, '.git')):
        _repo.git(root, 'worktree', 'add', '--detach', '-f', path, 'HEAD', timeout=_budget(120, 5))
        if os.path.exists(path):
            try:
                _copy_untracked(root, path)
            except Exception as e:          # noqa: BLE001  (the worktree is still the original code's)
                _state.record({'error': f'copy into the original worktree: {type(e).__name__}: {e}'})
    return path if os.path.exists(path) else None


def _copy_untracked(root, path):
    """Copies into the original code's worktree the files git does not track that live in the repository's packages:
    a generated _version.py, compiled extensions, data (audit 10-18 #5: without them the original code could not be
    imported, and every new failure was 'unknown'). Ignored files, and untracked ones that were there before the work
    started (never the agent's new files); size-capped."""
    dirs = {os.path.dirname(rel) for rel in _repo.iter_py(root, docs=False) if _repo.in_package(root, rel)}

    def inside(rel):
        parts = rel.rstrip('/').split('/')
        return (rel and not any(p.startswith('.') or p == '__pycache__' for p in parts) and not rel.endswith('.pyc')
                and any('/'.join(parts[:k]) in dirs for k in range(1, len(parts))))

    # (--directory: an ignored directory is one line, so a virtual environment inside the repository is not listed
    # file by file)
    listed = (_repo.git(root, 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory',
                        timeout=_budget(30, 2)) or '').splitlines()
    files = []
    for rel in listed:
        if not inside(rel):
            continue
        if not rel.endswith('/'):
            files.append(rel)
            continue
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, rel)):
            dirnames[:] = [d for d in dirnames if not d.startswith('.') and d != '__pycache__']
            files += [os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, '/') for f in filenames]
    before = _state.load('preexisting', {}) or {}
    files += [r for r in before if r not in files and os.path.exists(os.path.join(root, r))
              and _repo.git(root, 'ls-files', '--error-unmatch', r) is None]
    total = 0
    for rel in files:
        if not inside(rel):
            continue
        src, dst = os.path.join(root, rel), os.path.join(path, rel)
        try:
            size = os.path.getsize(src)
            if size > COPY_FILE or total + size > COPY_TOTAL or os.path.exists(dst) or not os.path.isfile(src):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            import shutil
            shutil.copy2(src, dst)
            total += size
        except OSError:
            continue


def _key(targets, stubs):
    return json.dumps([sorted(map(list, targets)), sorted(stubs)])


def base_run(root, targets):
    """(outcomes on the original code for exactly these target groups, stubs, complete). The modules to stub are
    found here only, on the original code, until no new one appears (at most 3 rounds): a module missing only after an
    edit is the edit's fault, not a test-only dependency (audit: a typo'd import was stubbed and passed). Cached per
    groups and stubs; a run that timed out is not cached and its unfinished tests stay unknown."""
    base = _base_worktree(root)
    if base is None:
        return None, tuple(_state.load('stubs', [])), False
    cache = _state.load('base_runs', {})
    stubs = set(_state.load('stubs', []))
    for _ in range(3):
        key = _key(targets, stubs)
        if key in cache:
            return cache[key], tuple(sorted(stubs)), True
        r = run(base, targets, timeout=BASE_TIMEOUT, cwd=base, stubs=tuple(sorted(stubs)))
        if _lift_memory_limit(r):
            r = run(base, targets, timeout=BASE_TIMEOUT, cwd=base, stubs=tuple(sorted(stubs)))
        if not r['ran']:
            return None, tuple(sorted(stubs)), False
        new = r['missing'] - stubs
        if new:
            stubs |= new
            _state.save('stubs', sorted(stubs))
            continue
        complete = not r['timeouts']
        if complete:
            cache[key] = r['outcomes']
            _state.save('base_runs', cache)
        return r['outcomes'], tuple(sorted(stubs)), complete
    return None, tuple(sorted(stubs)), False


def _lift_memory_limit(r):
    """True (once per task) when a run on the original code ran out of memory under RLIMIT_AS: the runs go on without
    the limit, the original code's and the edited code's alike (audit 10-18 #11: numpy, torch or OpenBLAS reserve more
    address space than the limit, and every run failed)."""
    if _state.load('no_mem_limit', False) or not NO_MEMORY.search(r.get('output') or ''):
        return False
    _state.save('no_mem_limit', True)
    _state.record({'tests': 'no memory limit from now on'})
    return True


def _test_uses(root, test_id, names):
    """Whether the test function test_id ('file::name', 'file::Class::name', or a whole file 'file') uses one of
    names (a file-level id: the file names one, e.g. it imports the old name)."""
    parts = test_id.split('::')
    try:
        text = _repo.read_text(root, parts[0])
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError):
        return False
    if len(parts) == 1:
        return any(re.search(rf'\b{re.escape(n)}\b', text) for n in names)
    lines = text.splitlines()
    want = parts[-1].split('[')[0]
    for fname, a, b in _test_functions(tree):
        if fname.split('::')[-1] == want:
            body = '\n'.join(lines[a - 1:b])
            return any(re.search(rf'\b{re.escape(n)}\b', body) for n in names)
    return False


def import_check(root, rels):
    """[(module, error)] of the changed modules that cannot be imported now but can on the original code (audit: an
    edit that broke importing requests was 'Kept, not checked' because conftest failed before any test ran)."""
    mods = [m for rel in rels if rel.endswith('.py') and not _repo.is_test_path(rel) and not _repo.is_doc_path(rel)
            for m in _module_names(root, rel)]
    if not mods:
        return []
    code = ('import importlib, json, sys, traceback\nres = {}\n'
            'for m in sys.argv[1:]:\n'
            '    try:\n        importlib.import_module(m)\n'
            '    except BaseException as e:\n'
            '        res[m] = traceback.format_exception_only(type(e), e)[-1].strip()\n'
            'print("SWEIMPORT" + json.dumps(res))\n')

    def probe(cwd):
        try:
            p = subprocess.run([sys.executable, '-c', code] + mods, cwd=cwd, env=_env(cwd), capture_output=True,
                               text=True, timeout=_budget(60), preexec_fn=_limit_memory)
        except (subprocess.TimeoutExpired, OSError):
            return None
        m = re.search(r'SWEIMPORT(\{.*\})', p.stdout)
        return json.loads(m.group(1)) if m else None

    now = probe(root)
    if not now:
        return []
    base = _base_worktree(root)
    before = probe(base) if base else {}
    if before is None:
        return []
    return [(m, err) for m, err in now.items() if m not in before]


def callmap(root):
    """{'rel::qualname': [test ids]}: which tests call each function of the repository, from one run of the test
    suite on the original code, once per task, with a deadline (a run cut short keeps the tests that finished).
    fastapi_14448's places are called by 115-598 tests each, none of which the lexical selection found (audit)."""
    cached = _state.load('callmap', None)
    if cached is not None:
        return cached
    base = _base_worktree(root)
    tests = sorted({r.split('/')[0] for r in _repo.iter_py(base or root, tests=True) if _is_test_file(r)})
    m = {}
    if base and tests:
        stubs = set(_state.load('stubs', []))
        deadline = time.time() + MAP_TIMEOUT
        for _ in range(2):
            # a first round that stops on a missing test-only module (inline_snapshot in fastapi) runs again with
            # its stand-in, within the same deadline
            r = run(base, [tests], timeout=max(10, deadline - time.time()), cwd=base, stubs=tuple(sorted(stubs)),
                    callmap=True, whole=True)
            if _lift_memory_limit(r) and time.time() < deadline - 10:
                r = run(base, [tests], timeout=max(10, deadline - time.time()), cwd=base, stubs=tuple(sorted(stubs)),
                        callmap=True, whole=True)
            new = r['missing'] - stubs
            m = {k: sorted(set(v)) for k, v in r['calls'].items()}
            if not new or time.time() > deadline - 10:
                break
            stubs |= new
            _state.save('stubs', sorted(stubs))
    _state.save('callmap', m)
    return m


def _rerun(root, ids, stubs):
    """The ids that fail when run again on their own (one process)."""
    if not ids:
        return set()
    r = run(root, [list(ids)], stubs=stubs)
    return {i for i in ids if r['outcomes'].get(i) in ('failed', 'error')}


def check(root, changed, renamed=(), cover=None, undo=None, use_map=True):
    """The verdict on the current code: ('OK' | 'UNCHECKED' | 'BROKEN', detail, new failures). A failure counts against
    the edit only when the same test, in the same groups, passes on the original code, fails again when run alone,
    and (undo = (rel, lines before the edit)) passes on the code just before this edit. OK needs passing tests that run
    the changed statements (cover: {rel: lines}; a false OK is worse than none: Reflexion's 16.3% false positives,
    Olausson). A new failure of a test that uses a name the statement renames does not count: the hidden tests
    replace such tests."""
    if DEADLINE and time.time() > DEADLINE - 3:
        return 'UNCHECKED', 'not checked: the time is nearly up', []
    broken_imports = import_check(root, list(changed))
    if broken_imports:
        detail = '\n'.join(f'  importing {m} fails: {err}' for m, err in broken_imports[:3])
        return 'BROKEN', 'the changed code cannot be imported any more:\n' + detail, [m for m, _ in broken_imports]
    targets = select(root, changed, use_map)
    if not targets:
        return 'UNCHECKED', 'no existing test uses the changed code (it imports without errors)', []
    stubs = tuple(_state.load('stubs', []))
    limited = not _state.load('no_mem_limit', False)
    r = run(root, targets, stubs=stubs, cover=cover)
    if not r['ran']:
        return 'UNCHECKED', 'pytest could not run', []
    failing = {i for i, o in r['outcomes'].items() if o in ('failed', 'error')}
    if r['missing'] - set(stubs) or failing or r['broken_run'] or not r['outcomes']:
        base, stubs2, _ = base_run(root, targets)
        if set(stubs2) != set(stubs) or (limited and _state.load('no_mem_limit', False)):
            r = run(root, targets, stubs=stubs2, cover=cover)
            failing = {i for i, o in r['outcomes'].items() if o in ('failed', 'error')}
            stubs = stubs2
        missing = r['missing'] - set(stubs)
        if missing:
            return 'BROKEN', (f'the changed code imports {", ".join(sorted(missing))}, which is not installed here '
                              '(the tests cannot import it)'), sorted(missing)
    else:
        base = {}
    files = sorted({g[0].split('::')[0] for g in targets})
    late = ''
    if r['timeouts']:
        late = '; out of time, the unfinished tests not counted: ' + ', '.join(g[0].split('::')[0] for g in r['timeouts'])
        slow = _state.load('slow_tests', [])
        _state.save('slow_tests', sorted(set(slow) | {g[0].split('::')[0] for g in r['timeouts']}))
    if r['broken_run'] and base:
        ran_before = [g for g in r['broken_run'] if any(i.split('::')[0] == g[0].split('::')[0] for i in base)]
        if ran_before:
            text = '\n'.join(l for l in r['output'].splitlines() if l.startswith('E ') or 'Error' in l)[-1200:]
            return 'BROKEN', ('the tests cannot start any more (their conftest or imports fail), though they ran on '
                              f'the original code:\n{text}'), [g[0] for g in ran_before]
    passed = sum(1 for o in r['outcomes'].values() if o == 'passed')
    if not failing:
        if passed == 0:
            return 'UNCHECKED', 'the selected tests did not finish (' + ', '.join(files) + ')' + late, []
        return _ran_it(r, cover, f'{passed} existing test{"s pass" if passed != 1 else " passes"} '
                                 f'({", ".join(files)}){late}')
    base = base or {}

    def was_ok(i):
        if base.get(i) == 'passed':
            return True
        file = i.split('::')[0]
        if i == file:              # a file that cannot be collected now: it was fine if its tests ran before
            return base.get(i) != 'error' and any(b.startswith(file + '::') for b in base)
        return False

    new = sorted(i for i in failing if was_ok(i))
    unknown = sorted(i for i in failing if i not in base and not was_ok(i))
    accepted = set(_state.load('accepted_failures', []))
    new = [i for i in new if i not in accepted]
    old_name = [i for i in new if renamed and _test_uses(root, i, renamed)]
    new = [i for i in new if i not in old_name]
    if old_name:
        late += (f'; {len(old_name)} failing tests use the renamed {", ".join(f"`{n}`" for n in renamed)} and do not '
                 f'count (the hidden tests replace them)')
    if new:
        again = _rerun(root, new, stubs)
        flaky = [i for i in new if i not in again]
        new = [i for i in new if i in again]
        if flaky:
            late += f'; {len(flaky)} failing tests passed when run again (not counted)'
    if new and undo is not None:
        rel, lines_before = undo
        now = _repo.read_lines(root, rel)
        try:
            _repo.write_lines(root, rel, lines_before)
            earlier = _rerun(root, new, stubs)
        finally:
            _repo.write_lines(root, rel, now)
        if earlier:
            new = [i for i in new if i not in earlier]
            late += (f'; {len(earlier)} failing tests already failed before this edit, after the earlier edits '
                     f'({", ".join(sorted(earlier)[:3])})')
    if not new:
        if passed == 0:      # nothing passes: the tests say nothing about the change (SWE-ABS: not verified)
            return 'UNCHECKED', (f'no selected test passes ({len(failing)} failed for other reasons){late}'), []
        already = len(failing)
        return _ran_it(r, cover, f'{passed} existing tests pass' + (
            f'; {already} failures were already there or are not this edit\'s' if already else '') + late +
            (f'; {len(unknown)} failing tests could not be compared with the original code' if unknown else ''))
    return 'BROKEN', _failure_excerpt(r, new), new


def _ran_it(r, cover, detail):
    """OK when the passing tests ran a changed statement; UNCHECKED when they ran none; OK with a note when it could not
    be measured (no sys.monitoring)."""
    if not cover:
        return 'OK', detail, []
    if r['covered'] is None:
        return 'OK', detail + ' (whether they run the changed lines could not be measured)', []
    if r['covered']:
        return 'OK', detail + '; they run the changed lines', []
    return 'UNCHECKED', re.sub(r'\bpass(es)?\b', lambda m: m.group(0) + ' but none of them runs the changed lines',
                               detail, count=1), []


def _failure_excerpt(r, ids, limit=1500):
    """Per new failing test (at most 3): its name and file, the line pytest marks with '>' and its 'E' lines (what was
    expected and what came). A failing test with expected vs actual is the feedback models repair best from; traceback
    frames add little (Self-Debugging: 88.8 vs 89.5 with traces; FeedbackEval). A file that cannot be collected shows
    its import error."""
    out = []
    for test_id in ids[:3]:
        parts = test_id.split('::')
        marked, msg = r['msgs'].get(test_id, ('', ''))
        out.append(f'  {parts[-1]} ({parts[0]}):' if len(parts) > 1 else f'  {parts[0]} (cannot be collected):')
        if marked:
            out.append(f'    the test runs: {marked[:200]}')
        out.extend(f'    {e[:200]}' for e in (msg or '').splitlines()[:6])
    more = f'\n  and {len(ids) - 3} more failing tests' if len(ids) > 3 else ''
    return ('\n'.join(out) + more)[:limit]


# The last verified state: the content of every changed file when the tests last passed (None: the file did not
# exist). Going back restores those contents and undoes everything else.

def changed_files(root):
    out = _repo.git(root, 'status', '--porcelain', '--untracked-files=all') or ''
    rels = []
    for line in out.splitlines():
        rel = line[3:].split(' -> ')[-1].strip().strip('"')
        if rel.endswith('.pyc') or os.path.basename(rel).startswith('.'):
            continue
        rels.append(rel)
    return rels


def save_verified(root):
    snap = {}
    for rel in changed_files(root):
        try:
            snap[rel] = ''.join(_repo.read_lines(root, rel))
        except OSError:
            snap[rel] = None
    _state.save('verified', snap)


def record_preexisting(root):
    """The files already changed or untracked when the work starts (the sandbox may hold some): an undo puts them
    back as they were instead of deleting them (review: restore_verified removed any untracked file)."""
    if _state.load('preexisting', None) is not None:
        return
    snap = {}
    for rel in changed_files(root):
        try:
            snap[rel] = ''.join(_repo.read_lines(root, rel))
        except OSError:
            snap[rel] = None
    _state.save('preexisting', snap)


def restore_verified(root):
    """Puts every changed file back to the last verified state (or the original when there is none)."""
    snap = _state.load('verified', {})
    before = _state.load('preexisting', {}) or {}
    for rel in set(changed_files(root)) | set(snap):
        content = snap.get(rel)
        if content is None and rel not in snap and before.get(rel) is not None:
            content = before[rel]                     # a file that was there before the work started
        path = os.path.join(root, rel)
        if content is not None:
            _repo.write_lines(root, rel, content.splitlines(keepends=True))
        elif rel in snap or _repo.git(root, 'ls-files', '--error-unmatch', rel) is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            _repo.git(root, 'checkout', 'HEAD', '--', rel)
