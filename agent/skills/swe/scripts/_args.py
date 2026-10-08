"""Tolerant reading of the model's arguments. Every form handled here was seen in real calls (see
docs/old_scripts_lessons.md): wrappers around each argument, lists packed into one string, escaped line breaks, leaked
tool-call syntax and copied line-number prefixes."""
import json
import re

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

WRAPPERS = '"\'`「」『』'
PLACEHOLDERS = {'', '...', 'file', 'symbol', 'start', 'end', 'start-end', 'place', 'id', 'text', 'new lines'}
LEAK = re.compile(r'<\|"\|>|<tool_call\|>|<\|tool_call>|^\s*\]?\s*,?\s*(file_path|skill_name|args)\s*:', re.M)
NUMBERED = re.compile(r'^\s*\d+\|')  # the viewer prints '  79|code' (no space: copied indentation stays exact)


def clean(a):
    """A path, id or name without the quotes, backticks, CJK brackets and trailing commas models add."""
    a = a.strip()
    prev = None
    while a != prev:
        prev = a
        a = a.strip().rstrip(',').strip().strip(WRAPPERS).strip()
    return a


def is_placeholder(a):
    a = clean(a).lower()
    return a in PLACEHOLDERS or (a.startswith('<') and a.endswith('>'))


def unpack(args):
    """Arguments the model packed into one string (a JSON-like list, or items joined by ' | ') as separate items.
    Several arguments, or one that is code, are returned as they are."""
    args = [a for a in args if a is not None]
    if len(args) != 1 or '\n' in args[0].strip():
        return args
    raw = args[0].strip()
    if ' | ' in raw:
        return [clean(x) for x in raw.split(' | ')]
    for cand in (raw, '[' + raw + ']', '["' + raw.strip('[]').strip('"') + '"]'):
        try:
            items = json.loads(cand)
        except ValueError:
            continue
        if isinstance(items, list) and items and all(isinstance(i, str) for i in items) and (
                len(items) > 1 or raw.startswith('[')):
            return items
    return args


def ids(args, prefix):
    """(ids, others) from the model's choice: 'C2', 'c2', '2', 'C2, C5' and packed lists become prefix ids; anything
    else (a name or a path off the list) is returned in others."""
    found, others = [], []
    for a in unpack(args):
        for part in re.split(r'[,\s]+', a.strip()) if re.fullmatch(r'[\w\s,"\'`]+', a.strip()) else [a]:
            p = clean(part)
            if not p:
                continue
            m = re.fullmatch(rf'(?i){prefix}?(\d+)', p)
            if m:
                found.append(f'{prefix}{int(m.group(1))}')
            else:
                others.append(p)
    return list(dict.fromkeys(found)), others


def code(text):
    """Code text as the file must receive it: escaped line breaks unescaped when they are the only line breaks,
    leaked call syntax and markdown fences removed, line-number prefixes removed when every line has one. Ends with
    a line break."""
    m = LEAK.search(text)
    if m:
        text = text[:m.start()]
    if '\\n' in text and text.count('\n') <= text.count('\\n') // 4:
        text = text.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\t', '\t')
    lines = text.split('\n')
    if lines and lines[0].strip().startswith('```'):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().startswith('```'):
        lines = lines[:-1]
    body = [l for l in lines if l.strip()]
    if body and all(NUMBERED.match(l) for l in body):
        lines = [NUMBERED.sub('', l, count=1) for l in lines]
    return '\n'.join(lines) + '\n' if lines else ''


def edit(args):
    """(place, start, end, text) from [place, start, end, text...] or [place, 'start-end', text...]; None when the
    numbers are missing or are placeholders."""
    args = [a for a in args if a is not None]
    if len(args) < 3 or is_placeholder(args[0]):
        return None
    place = clean(args[0]).upper()
    m = re.fullmatch(r'(\d+)\s*[-:,]\s*(\d+)', clean(args[1]))
    if m:
        start, end, rest = int(m.group(1)), int(m.group(2)), args[2:]
    elif len(args) >= 4 and clean(args[1]).isdigit() and clean(args[2]).isdigit():
        start, end, rest = int(clean(args[1])), int(clean(args[2])), args[3:]
    else:
        return None
    pieces = [code(r) for r in rest]
    return place, min(start, end), max(start, end), ''.join(pieces)


def plan(args):
    """[(place, intent)] from 'P1: intent' items (one per argument or one per line) or alternating place / intent
    arguments; 'back' to go back to the candidates."""
    args = unpack(args)
    if len(args) == 1 and clean(args[0]).lower() == 'back':
        return 'back'
    out = []
    lines = [l for a in args for l in a.split('\n') if l.strip()]
    for line in lines:
        m = re.match(r'\s*["\'`「]?\s*(P\d+)\s*[:=\-]\s*(.+)', line, re.I)
        if m:
            out.append((m.group(1).upper(), clean(m.group(2))))
    if out:
        return out
    items = [clean(a) for a in args]
    for i in range(0, len(items) - 1, 2):
        if re.fullmatch(r'(?i)P\d+', items[i]):
            out.append((items[i].upper(), items[i + 1]))
    return out
