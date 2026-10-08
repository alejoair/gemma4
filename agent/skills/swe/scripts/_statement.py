"""The issue statement: the text without what asks for nothing (template comments, links, issue references), and the
search terms it contains with their weights (docs/old_scripts_lessons.md, Localization)."""
import builtins
import keyword
import re

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)

IDENT = r'[A-Za-z_][A-Za-z0-9_]*'
STOP = set('''a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing done down during each else even ever every few for from further get
gets got had has have having he her here hers him his how however i if in into is it its itself just let like make
makes made many may me might more most much must my need needs new no nor not now of off often on once one only or
other our ours out over own per please pr put rather really same see seem seems she should since so some such than
that the their theirs them then there these they this those though through thus to too under until up upon us use
used uses using very via want wants was way we well were what when where whether which while who whom whose why will
with within without would yet you your yours fix fixes fixed fixing issue issues bug bugs add added adds adding
support supports update updates updated change changes changed current currently instead already still able example
examples case cases thing things something anything everything work works working worked expect expected actual
actually problem problems happen happens happened right wrong true false none self cls def return import class pass
raise lambda yield async await print'''.split())
DROP_SECTIONS = re.compile(r'(?im)^#+\s*(ai disclaimer|checklist|ai transcript)\b.*?(?=^#+\s|\Z)', re.S)


def clean(text):
    """The statement without HTML comments, <details> blocks, template headings, links, issue references and
    @mentions, with repeated paragraphs (the title copied as the first body line) kept once."""
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'<!--.*?(-->|\Z)', '', text, flags=re.S)
    text = re.sub(r'<details>.*?(</details>|\Z)', '', text, flags=re.S | re.I)
    text = DROP_SECTIONS.sub('', text)
    text = re.sub(r'(?im)^#+\s*(pull request|description|summary|type of changes?)\s*$', '', text)
    text = re.sub(r'(?m)^\s*[-*]\s*\[[ xX]\].*$', '', text)          # template checklists
    text = re.sub(r'(?im)^\s*(discussion|ref|refs|related|see also)\s*:.*$', '', text)
    text = re.sub(r'(?i)\b(fix(es|ed)?|close[sd]?|resolve[sd]?|ref(s)?|addresses|see)\s*:?\s*'
                  r'(https?://\S+|#\d+|[\w.-]+/[\w.-]+#\d+)', '', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'(?<![\w.])#\d+\b', '', text)
    text = re.sub(r'(?<![\w.])@[\w-]+', '', text)
    paras, seen = [], set()
    for para in re.split(r'\n\s*\n', text):
        key = re.sub(r'\W+', ' ', para).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        paras.append(para.strip('\n'))
    return '\n\n'.join(paras).strip()


def _shape(word):
    """The weight an identifier gets for its shape alone."""
    if '_' in word.strip('_') and word.upper() == word and re.search('[A-Z]', word):
        return 3                                           # UPPER_CASE
    if '_' in word.strip('_') or re.search(r'[a-z][A-Z]', word) or re.fullmatch(r'[A-Z][a-z0-9]+[A-Z]\w*', word):
        return 4                                           # snake_case, camelCase, CapWords
    if word.isupper() and len(word) > 1:
        return 3
    return 0


def terms(text):
    """{term: weight} from a statement (already cleaned): code in backticks 5, function names in traceback frames 5,
    compound identifiers 4, dotted names and UPPER_CASE 3, the snake form of hyphenated words 3 and their parts 2,
    capitalised words 2, other words of 5+ letters 1. Stop words are dropped."""
    out = {}

    def add(term, weight):
        if term and term.lower() not in STOP and len(term) > 1 and not term.isdigit():
            out[term] = max(out.get(term, 0), weight)

    for m in re.finditer(r'File "([^"]+)", line \d+, in (' + IDENT + ')', text):
        add(m.group(2), 5)
    for m in re.finditer(r'`([^`\n]+)`', text):
        for ident in re.findall(IDENT, m.group(1)):
            add(ident, 5)
    for m in re.finditer(IDENT + r'(?:\.' + IDENT + r')+', text):
        for part in m.group(0).split('.'):
            add(part, max(3, _shape(part)))
    for m in re.finditer(r'\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b', text):
        word = m.group(0)
        add(word.replace('-', '_').lower(), 3)
        for part in word.split('-'):
            add(part, 2)
    for m in re.finditer(r'\b[A-Z][a-z0-9]+(?:[ ]+[A-Z][a-z0-9]+){1,3}\b', text):
        add('_'.join(m.group(0).lower().split()), 3)      # Server Sent Events -> server_sent_events
    sentence_start = True
    for m in re.finditer(r'[A-Za-z_][A-Za-z0-9_]*|[.!?:\n]', text):
        word = m.group(0)
        if word in '.!?:\n':
            sentence_start = True
            continue
        weight = _shape(word)
        if not weight and word[0].isupper() and not sentence_start:
            weight = 2
        if not weight and len(word) >= 5:
            weight = 1
        if weight:
            add(word, weight)
        sentence_start = False
    return out


def model_terms(items):
    """({term: weight}, paths) from the search terms the model wrote: its names count like code in backticks (5) or,
    when they are plain words, 3; a phrase gives its words (at least 2) and their snake form (3); file paths are
    paths, and their file name a term."""
    out, found = {}, []

    def add(term, weight):
        if term and term.lower() not in STOP and len(term) > 1 and not term.isdigit():
            out[term] = max(out.get(term, 0), weight)

    for item in items:
        item = item.strip().strip('`\'"「」')
        if not item:
            continue
        if re.fullmatch(r'[\w./-]+\.py', item) or ('/' in item and ' ' not in item):
            found.append(item.lstrip('./'))
            add(re.sub(r'\.py$', '', item.rstrip('/').split('/')[-1]), 3)
            continue
        words = re.findall(IDENT, item)
        if re.fullmatch(IDENT + r'(\.' + IDENT + r')*(\(\))?', item):      # a name, dotted or called
            for w in words:
                add(w, 5 if _shape(w) or w[0].isupper() else 3)
            continue
        for w in words:
            add(w, max(2, _shape(w)))
        if 2 <= len(words) <= 4 and re.fullmatch(r'[A-Za-z0-9 -]+', item):
            add('_'.join(w.lower() for w in words), 3)
    return out, list(dict.fromkeys(found))


def paths(text):
    """Repository paths the statement names (module files and traceback frames)."""
    found = re.findall(r'[\w./-]*\w\.py\b', text)
    return list(dict.fromkeys(p.lstrip('./') for p in found))


MAX_REQUIREMENTS = 6
BUILTIN_NAMES = set(dir(builtins)) | set(keyword.kwlist) | {'self', 'cls'}
NEW_WORDS = re.compile(r'(?i)\b(add|adds|added|adding|new|introduce[sd]?|support for|implement[sd]?|allow[s]?)\b')
RENAME_WORDS = re.compile(r'(?i)\b(renamed?|renaming|deprecat\w*)|->|→')


def requirements(text):
    """The requests of a cleaned statement, at most MAX_REQUIREMENTS: the title, every bullet item, and the body
    sentences that name code (in backticks or in a code shape). Code blocks are left out (they show, they do not
    ask)."""
    text = re.sub(r'```.*?(```|\Z)', '', text, flags=re.S)
    lines = [l.strip() for l in text.split('\n')]
    out = []

    def put(s):
        s = re.sub(r'\s+', ' ', s).strip(' -*•').strip()
        s = re.sub(r'^[^\w`"\'(]+', '', s)          # leading emoji or markers
        if len(s) > 3 and s.lower() not in (o.lower() for o in out):
            out.append(s if len(s) <= 300 else s[:297] + '...')

    if lines:
        put(lines[0])
    body = []
    for line in lines[1:]:
        if re.match(r'^([-*•]|\d+[.)])\s+', line) and not re.match(r'^[-*]\s*\[[ xX]\]', line):
            put(re.sub(r'^([-*•]|\d+[.)])\s+', '', line))
        elif line and not line.startswith(('#', '|', '>')):
            body.append(line)
    for sentence in re.split(r'(?<=[.!?])\s+', ' '.join(body)):
        if re.search(r'`[^`]+`', sentence) or any(_shape(w) >= 4 for w in re.findall(IDENT, sentence)):
            put(sentence)
    return out[:MAX_REQUIREMENTS]


def code_names(text):
    """Names the text shows as code: in backticks (the last part of a dotted or called name), or in a compound shape."""
    out = []
    for m in re.finditer(r'`([^`\n]+)`', text):
        span = m.group(1)
        if re.search(r'(^|\s)--?\w', span):                          # a shell command, not code
            continue
        span = re.sub(r'"[^"]*"|\'[^\']*\'', ' ', span)                # string literals
        span = re.sub(IDENT + r'\s*=(?!=)', ' ', span)                  # keyword arguments: parameters, not entities
        for name in re.findall(IDENT + r'(?:\.' + IDENT + r')*', span):
            out.append(name)
    for w in re.findall(IDENT, re.sub(r'`[^`\n]+`', ' ', text)):
        if _shape(w) >= 4:
            out.append(w)
    return [n for n in dict.fromkeys(out) if n not in BUILTIN_NAMES]


def change_type(requirement, defined):
    """('new', [names]) when the requirement asks for names the code does not define yet, ('rename', names) for a
    rename or deprecation, else ('fix', names). defined(name) tells whether the repository defines a name."""
    names = code_names(requirement)
    if RENAME_WORDS.search(requirement):
        return 'rename', names
    missing = [n for n in names if not defined(n.split('.')[-1])]
    if missing and NEW_WORDS.search(requirement):
        return 'new', missing
    return 'fix', names
