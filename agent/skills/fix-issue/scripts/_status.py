"""What the patch holds now, read from git and the code, never from the plan (the plan forgets edits after a way back;
V10 audit #1): the changed files and functions with their line counts, the statement lines an edit changed, and the
facts a requirement can be checked by (a new name is defined, an old name is gone or kept as an alias)."""
import ast
import difflib
import re

import _code
import _repo

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
MAX_SHOWN = 4


def _changes(root):
    """{rel: [(new line or anchor, added, removed)]} from `git diff -U0`, plus untracked files (all lines added)."""
    out = {}
    diff = _repo.git(root, 'diff', '-U0', '--no-color', '--no-ext-diff') or ''
    rel = None
    for line in diff.splitlines():
        if line.startswith('+++ '):
            rel = line[6:] if line.startswith('+++ b/') else None
        elif rel and line.startswith('@@'):
            m = HUNK.match(line)
            if m:
                removed = int(m.group(2) or '1') if m.group(2) != '0' else 0
                start, added = int(m.group(3)), (int(m.group(4) or '1') if m.group(4) != '0' else 0)
                out.setdefault(rel, []).append((max(1, start), added, removed))
    new = _repo.git(root, 'ls-files', '--others', '--exclude-standard') or ''
    for rel in new.splitlines():
        parts = rel.split('/')
        if rel and rel.endswith('.py') and not any(p.startswith('.') or p == '__pycache__' for p in parts):
            n = len(_repo.read_lines(root, rel))
            out[rel] = [(1, n, 0)]
    return out


def summary(root):
    """[(rel, owner, added, removed)]: per changed file, the innermost function or class (or 'top level') that holds
    each change, with the counts of added and removed lines."""
    rows = {}
    for rel, hunks in _changes(root).items():
        try:
            lines = _repo.read_lines(root, rel)
            syms = _code.symbols(_code.parse(''.join(lines)))
        except OSError:
            continue
        owner = _code.owner_map(syms, len(lines))
        for start, added, removed in hunks:
            at = min(max(1, start), len(lines)) if lines else 1
            o = owner[at] if at < len(owner) else None
            name = o.name if o is not None else 'top level'
            a, r = rows.get((rel, name), (0, 0))
            rows[(rel, name)] = (a + added, r + removed)
    return [(rel, name, a, r) for (rel, name), (a, r) in rows.items()]


def progress_line(root, now='', left=()):
    """One line, the same in every answer: what the patch holds, what is open now, what is left."""
    rows = summary(root)
    if rows:
        shown = [f'{name} in {rel} (+{a} -{r})' for rel, name, a, r in rows[:MAX_SHOWN]]
        more = f' and {len(rows) - MAX_SHOWN} more' if len(rows) > MAX_SHOWN else ''
        held = 'The patch now changes ' + '; '.join(shown) + more + '.'
    else:
        held = 'The patch is empty so far.'
    if now:
        held += f' Open now: {now}.'
    if left:
        held += ' Still to look at: ' + ', '.join(left) + '.'
    return held


def changed_statements(before, after):
    """The line numbers (in after) of the statements an edit changed: the first line of every statement whose lines
    were changed, and for a changed def or class line the first statement of its body (a signature change shows when
    the body runs). Def and class lines themselves run at import, so they prove nothing."""
    touched = set()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        touched.update(range(j1 + 1, j2 + 1) if j2 > j1 else (j1, j1 + 1))
    tree = _code.parse(''.join(after))
    if tree is None:
        return set()
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        span = set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            head = set(range(min([node.lineno] + [d.lineno for d in node.decorator_list]), node.body[0].lineno))
            if head & touched and node.body:
                out.add(node.body[0].lineno)
            continue
        if isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)):
            span = {node.lineno}                       # a compound statement: its own line, not its body's
        if span & touched:
            out.add(node.lineno)
    return out


def defined(root, name):
    """Where code defines name (its last part): 'file :: Qual.name' for functions, classes and assignments at the top
    of a module or class, '' when nothing does."""
    short = name.split('.')[-1]
    for rel in _repo.iter_py(root, docs=False):
        text = _repo.read_text(root, rel)
        if short not in text:
            continue
        tree = _code.parse(text)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == short:
                return f'{rel} :: {node.name}'
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == short:
                        return f'{rel} :: {short} ='
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                for a in node.names:
                    if (a.asname or a.name.split('.')[-1]) == short and rel.endswith('__init__.py'):
                        return f'{rel} (imported there)'
    return ''


def requirement_facts(root, requirement, added_text):
    """Facts about one requirement that the code can show, as short sentences; [] when none can be checked."""
    facts = []
    if requirement['type'] == 'new':
        for name in dict.fromkeys(n.split('.')[-1] for n in requirement['names'][:4]):
            where = defined(root, name)
            facts.append(f'`{name.split(".")[-1]}` is defined in {where}' if where else
                         f'`{name.split(".")[-1]}` is not defined anywhere yet')
    for old in requirement.get('old', [])[:2]:
        where = defined(root, old)
        facts.append(f'the old name `{old}` is still defined in {where}' if where else
                     f'the old name `{old}` is gone')
    for lit in re.findall(r'`([^`\n]{3,60})`', requirement['text'])[:3]:
        if lit in added_text and not any(lit in f for f in facts):
            facts.append(f'`{lit}` appears in the added lines')
    return facts
