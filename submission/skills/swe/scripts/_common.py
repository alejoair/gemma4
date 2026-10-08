"""Shared helpers for the swe skill scripts: repository root, source-file walking and AST lookups."""
import ast
import atexit
import hashlib
import json
import os
import re
import subprocess
import sys

SKIP_DIRS = {'.git', '.hg', '.tox', '.nox', '.venv', 'venv', 'env', 'node_modules', 'build', 'dist', '__pycache__',
             '.mypy_cache', '.pytest_cache', '.ruff_cache', 'site-packages', '.eggs'}
DOC_DIRS = {'docs', 'doc', 'docs_src', 'examples', 'example', 'benchmarks', 'scripts'}
MAX_OUT = 4000
CALL_LOG = '/tmp/swe_skill_calls.log'
# The single agent's skill ships assets/procedure.json: then the journal drives the work (see _journal.py).
PROCEDURE_ON = os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets', 'procedure.json'))


# With the procedure the JOURNAL line is the only NEXT: a script's own NEXT line becomes a TIP, and the ones written
# for the pipeline stages (report, plan, locator, fixer) are dropped.
_PIPELINE_WORDS = ('report', 'locator', 'fixer', 'apply_edit', 'your plan', 'your procedure')


def _procedure_text(s):
    lines = s.split('\n')
    out = []
    for line in lines:
        if line.startswith('NEXT:'):
            if any(w in line for w in _PIPELINE_WORDS):
                continue
            line = 'TIP:' + line[5:]
        out.append(line)
    return '\n'.join(out)


class _Tee:
    """Copy everything a script prints into CALL_LOG, so a run can be reviewed afterwards."""

    def __init__(self, stream):
        self.stream = stream
        self.buf = []
        try:
            self.fh = open(CALL_LOG, 'a')
            self.fh.write('\n===== ' + ' '.join(os.path.basename(a) if i == 0 else a for i, a in enumerate(sys.argv))[:300] + '\n')
        except OSError:
            self.fh = None

    def write(self, s):
        if PROCEDURE_ON:
            s = _procedure_text(s)
        self.buf.append(s)
        if self.fh:
            self.fh.write(s)
            self.fh.flush()
        return self.stream.write(s)

    def flush(self):
        self.stream.flush()


if not isinstance(sys.stdout, _Tee):
    sys.stdout = _Tee(sys.stdout)

# Models sometimes wrap each argument in literal quotes (["\"pkg/mod.py\"", "\"Cls\""]) or add a trailing comma;
# strip them so a path or symbol still resolves instead of sending the model into a retry loop.
def _clean(a):
    return a.strip().strip(',').strip().strip('"\'`').strip()


def _split_packed(argv):
    """Models sometimes pack every argument into one string that looks like a JSON list: ['a.py", "10-20'] or
    ['["a.py", "10-20"]']. Unpack it into separate arguments."""
    if len(argv) == 2 and ' | ' in argv[1] and '\n' not in argv[1]:
        return [argv[0]] + [a.strip() for a in argv[1].split(' | ')]  # "file | 10-20"
    if len(argv) != 2 or '", "' not in argv[1] and '","' not in argv[1]:
        return argv
    raw = argv[1].strip()
    for candidate in (raw, '[' + raw + ']', '["' + raw.strip('[]').strip('"') + '"]'):
        try:
            items = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(items, list) and len(items) > 1 and all(isinstance(i, str) for i in items):
            return [argv[0]] + items
    return argv


sys.argv = _split_packed(sys.argv)


# edit.py keeps its new text exactly as given (indentation matters); only its file and range are cleaned.
# try.py keeps its code exactly as given too.
_keep_from = {'edit.py': 4, 'try.py': 1}.get(os.path.basename(sys.argv[0]), len(sys.argv))
if os.path.basename(sys.argv[0]) == 'edit.py' and len(sys.argv) > 3 and not _clean(sys.argv[3]).isdigit():
    _keep_from = 3  # [file, "start-end", text]: the text starts at the third argument
sys.argv = [sys.argv[0]] + [_clean(a) if i < _keep_from else a for i, a in enumerate(sys.argv[1:], 1)]
sys.argv = [a for i, a in enumerate(sys.argv) if i == 0 or a or i >= _keep_from]

def _state_path(name):
    """Per-repository state file in /tmp, so state from one task's sandbox never leaks into another task."""
    tag = hashlib.sha1((os.environ.get('PWD') or '/workspace').encode()).hexdigest()[:10]
    return f'/tmp/swe_{name}_{tag}'


SEEN = _state_path('seen.txt')
GOOD = _state_path('good.patch')
READS_BEFORE_NUDGE = 12


def _status_note():
    """After a history compaction the model forgets that its fix is already done. When the working tree is exactly
    the state check.py approved, every script says so at the end of its output."""
    if os.path.basename(sys.argv[0]) == 'check.py':
        return
    try:
        root = repo_root()
        r = subprocess.run(['git', '-c', 'safe.directory=*', 'diff', '--binary'], cwd=root, capture_output=True,
                           text=True, timeout=20)
        good = open(GOOD).read() if os.path.exists(GOOD) else ''
    except Exception:  # noqa: BLE001
        return
    if r.returncode == 0 and not r.stdout.strip():
        # Reading without editing is how small models run out of time: after many reads, say so.
        try:
            with open(SEEN) as fh:
                reads = sum(1 for line in fh if line.split(' ', 1)[0] in ('show.py', 'locate.py', 'callers.py'))
        except OSError:
            reads = 0
        if reads >= READS_BEFORE_NUDGE:
            print(f'\nSTATUS: {reads} reading calls so far and nothing is edited yet; time is short. If your job is to '
                  'edit, make the edit now with edit.py from the code you have already seen. If your job is to report, '
                  'write the report now.')
        return
    if r.returncode == 0 and r.stdout and r.stdout == good:
        files = sorted(set(re.findall(r'^\+\+\+ b/(\S+)', good, re.M)))
        print(f'\nSTATUS: your current changes ({", ".join(files)}) already passed check.py. If the statement needs '
              'no other change, finish now as your instructions say.')


def _at_exit():
    if PROCEDURE_ON and _journal is not None and _journal.PROC:
        script = os.path.basename(sys.argv[0])
        text = ''.join(sys.stdout.buf) if isinstance(sys.stdout, _Tee) else ''
        event = _journal.classify(script, text)
        if _REFUSED:
            event['refused'] = True
        _journal.record(event)
        if script != 'journal.py':
            print('\n' + _journal.journal_line())
        return
    _status_note()


atexit.register(_at_exit)
_REFUSED = False
_journal = None
NO_REPEAT_GUARD = {'check.py', 'journal.py'}


def repeat_guard(next_step):
    """Stop a call identical to an earlier one: print a short reminder instead of the same output again."""
    script = os.path.basename(sys.argv[0])
    if script in NO_REPEAT_GUARD:
        return
    sig = script + ' ' + ' '.join(a.strip().lower() for a in sys.argv[1:])
    seen = []
    try:
        with open(SEEN) as fh:
            seen = fh.read().splitlines()
    except OSError:
        pass
    count = seen.count(sig)
    try:
        with open(SEEN, 'a') as fh:
            fh.write(sig + '\n')
    except OSError:
        pass
    if count and PROCEDURE_ON:
        if script == 'show.py' and count == 1:
            # Printed again once: a history compaction may have dropped the first output.
            print(f'NOTE: you already ran "{sig}"; the code is unchanged since then:')
            return
        print(f'REPEATED CALL: you already ran "{sig}" ({count + 1} times now). Its output is in the conversation '
              'above and has not changed.')
        sys.exit(0)
    if count == 1 or (count and script == 'show.py'):
        # A repeat may come from a later stage that never saw the output (the locator and the fixer share /tmp), and
        # show.py prints the numbered code the edits are made from, so print the output again instead of stopping.
        if count >= 2:
            print(f'STOP: this is call number {count + 1} of "{sig}". The code below is unchanged since your first call. '
                  'Do not call show.py on it again: use it now for your next step (write your report or plan, or '
                  'call edit.py / apply_edit). To see lines below the shown part, call show.py with [file, "start-end"].')
        else:
            print(f'NOTE: you already ran "{sig}"; same output as before:')
        if count >= 2:
            # After the code (never instead of it), built at exit so it includes the symbol this call shows: a
            # looping locator still gets a finished report.
            def tail():
                report = last_candidate_report(with_code=False)
                if report:
                    print(f'\nYou have viewed this {count + 1} times. If you are the locator, stop and write this '
                          f'report now:\n{report}\nIf you are the fixer, call edit.py now.')
            atexit.register(tail)
        return
    if count:
        print(f'REPEATED CALL: you already ran "{sig}" ({count + 1} times now). Its output is in the conversation '
              f'above and has not changed.')
        report = last_candidate_report() if script == 'locate.py' else ''
        if report:
            print('STOP calling scripts. If you are the locator, write this as your final message now:')
            print(report)
            print('If you are the fixer, call edit.py now with the line numbers of the code shown earlier.')
        else:
            print('NEXT: ' + next_step)
        sys.exit(0)


CANDIDATE = _state_path('candidate.json')
VIEWED = _state_path('viewed.txt')


def remember_candidate(rel, name, start, end, code_lines, weak=False):
    """Store the best location found so far, so a looping model can be handed a finished report. A weak candidate
    (the top hit of a free-text search) never replaces a symbol the model chose to view."""
    if weak and os.path.exists(CANDIDATE):
        return
    try:
        with open(CANDIDATE, 'w') as fh:
            json.dump({'file': rel, 'symbol': name, 'graph_id': graph_id(rel, name), 'start': start, 'end': end,
                       'code': code_lines[:25]}, fh)
        if not weak:
            with open(VIEWED, 'a') as fh:
                fh.write(f'{rel} :: {name}\n')
    except OSError:
        pass


def last_candidate_report(with_code=True):
    try:
        with open(CANDIDATE) as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return ''
    try:
        with open(VIEWED) as fh:
            viewed = list(dict.fromkeys(fh.read().splitlines()))
    except OSError:
        viewed = []
    # Other places already viewed (often a second file the statement also asks to change) go into ALSO.
    also = [v for v in viewed if v != f"{c['file']} :: {c['symbol']}"][-3:]
    return '\n'.join([f"FILE: {c['file']}", f"SYMBOL: {c['symbol']}", f"GRAPH_ID: {c['graph_id']}",
                      f"LINES: {c['start']}-{c['end']}"] + (['CODE:'] + c['code'] if with_code else
                                                            ['CODE: the first lines of the code shown above']) +
                     ['ALSO: ' + ('; '.join(also) or 'NONE')])


def repo_root():
    """The repository the agent works on: the sandbox working directory, else /workspace, else git top level."""
    for cand in (os.environ.get('SWE_REPO'), os.environ.get('PWD'), '/workspace'):
        if cand and os.path.isdir(os.path.join(cand, '.git')):
            return cand
    try:
        out = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get('PWD') or '/workspace'


def is_test_path(rel):
    parts = rel.replace('\\', '/').split('/')
    name = parts[-1]
    return any(p in ('tests', 'test', 'testing') for p in parts[:-1]) or name.startswith('test_') or name.endswith('_test.py') \
        or name == 'conftest.py'


def is_doc_path(rel):
    return rel.replace('\\', '/').split('/')[0] in DOC_DIRS


def iter_py(root, tests=False, docs=False):
    """Yield repo-relative paths of Python files; source files only unless tests/docs are requested."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.') and not d.endswith('.egg-info'))
        for fn in sorted(filenames):
            if not fn.endswith('.py') or fn.startswith('.'):
                # Hidden files are not repository code (the harness drops .adk_exec_*.py runners in /workspace).
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            if is_test_path(rel) and not tests:
                continue
            if is_doc_path(rel) and not docs:
                continue
            yield rel


def read_text(root, rel):
    try:
        with open(os.path.join(root, rel), encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def parse(text):
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def symbols(tree):
    """List of (qualified_name, kind, start_line, end_line) for every function and class, outermost first."""
    out = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                start = min([child.lineno] + [d.lineno for d in getattr(child, 'decorator_list', [])])
                end = getattr(child, 'end_lineno', None) or child.lineno
                kind = 'class' if isinstance(child, ast.ClassDef) else 'def'
                out.append((name, kind, start, end))
                visit(child, name + '.')

    if tree is not None:
        visit(tree, '')
    return out


def enclosing(syms, line):
    """Innermost symbol containing the line, or None for module level."""
    best = None
    for name, kind, start, end in syms:
        if start <= line <= end and (best is None or end - start <= best[3] - best[2]):
            best = (name, kind, start, end)
    return best


def find_symbol(syms, wanted, strict=False):
    """Match 'Class.method', 'method' or a dotted suffix: exact qualified matches first, then dotted suffixes. Only
    when nothing matches the whole name, and not in strict mode, fall back to the last part ('X.setup' -> any
    'setup'), because a qualified name must not silently resolve to a method of another class."""
    exact = [s for s in syms if s[0] == wanted]
    if exact:
        return exact
    suffix = [s for s in syms if s[0].endswith('.' + wanted)]
    if suffix or strict:
        return suffix
    tail = wanted.split('.')[-1]
    return [s for s in syms if s[0] == tail or s[0].split('.')[-1] == tail]


def clip(text, limit=MAX_OUT):
    """Cut long output, but keep the closing instruction lines (NEXT, VERDICT, "more lines" hints, missing terms)
    that tell the model what to do next."""
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    tail = []
    while lines and (lines[-1].startswith(('NEXT', 'VERDICT', '-----', 'Terms not found', 'Most matching', 'Files that import')) or not lines[-1].strip()):
        tail.insert(0, lines.pop())
    head = '\n'.join(lines)
    room = max(500, limit - sum(len(t) + 1 for t in tail) - 30)
    cut = head[:room].rsplit('\n', 1)[0]
    return '\n'.join([cut, '[... output clipped ...]'] + tail)


WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def find_definitions(root, name, limit=5):
    """(file, (qualified_name, kind, start, end)) for every definition matching name: whole-name matches in any
    file (source first, then docs and scripts) before matches of the last part only."""
    tail = name.split('.')[-1]
    strict, loose = [], []
    for rel in list(iter_py(root)) + [r for r in iter_py(root, docs=True) if is_doc_path(r)]:
        text = read_text(root, rel)
        if tail not in text:
            continue
        syms = symbols(parse(text))
        strict += [(rel, sym) for sym in find_symbol(syms, name, strict=True)]
        if len(strict) >= limit:
            break
        if '.' in name:
            loose += [(rel, sym) for sym in find_symbol(syms, tail, strict=True)]
    return (strict + loose)[:limit]


def is_symbol_name(text):
    return bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*', text.strip()))


def graph_id(rel, qualname):
    """Fully qualified id used by the competition code graph, e.g. src/pkg/mod.py + Cls.meth -> pkg.mod.Cls.meth."""
    parts = rel.replace('\\', '/')[:-3].split('/') if rel.endswith('.py') else rel.split('/')
    if parts and parts[0] in ('src', 'lib'):
        parts = parts[1:]
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    module = '.'.join(p for p in parts if p)
    if not qualname or qualname == '<module>':
        return module
    return f'{module}.{qualname}' if module else qualname


if PROCEDURE_ON:
    try:
        import _journal  # noqa: E402  (imported last: it uses the helpers above)
    except Exception:  # noqa: BLE001
        _journal = None
    _script = os.path.basename(sys.argv[0])
    if _journal is not None and _journal.PROC and _script.endswith('.py') and not _script.startswith('_'):
        _reason = _journal.gate(_script)
        if _reason:
            _REFUSED = True
            print(f'NOT RUN: {_reason}.')
            sys.exit(0)
