"""hints.py <problem statement text>

Rule-based SWE checklist. Reads the statement and prints the places a fix of that kind usually has to touch and
the searches that find them, so the model follows a concrete plan instead of improvising one.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402,F401  (tees output to the call log)

RULES = [
    (r'\b(error|exception|raise[sd]?|traceback|warning)\b',
     'The statement is about an error or warning: find where it is raised or emitted '
     '(locate.py with the exact message text or the exception class) and fix the condition that triggers it.'),
    (r'\bdeprecat',
     'Deprecation: emit warnings.warn(..., DeprecationWarning, stacklevel=2) where the old usage enters, keep the old '
     'behaviour working, and look for an existing deprecation helper in the package (locate.py deprecat).'),
    (r'\b(default|defaults)\b',
     'Default value: find the constant or the keyword default in the function signature; check every function that '
     'forwards the same parameter so the defaults stay consistent.'),
    (r'\b(parameter|argument|option|flag|kwarg|keyword)\b',
     'New or changed parameter: update the function signature, the place that uses the value, the docstring, and '
     'every wrapper or public API function that forwards it (callers.py <function>).'),
    (r'\b(type hint|typing|annotation|mypy|pyright|overload)\b',
     'Typing change: edit the annotations (and any overloads or stub files .pyi); runtime behaviour usually stays.'),
    (r'\b(regression|used to work|no longer|broke|broken since)\b',
     'Regression: the fix is usually a small condition that a recent change made too strict or too loose; compare '
     'the branch conditions in the located function.'),
    (r'\b(url|path|uri|slash|separator)\b',
     'URL/path handling: check where the string is normalised (split, join, strip, quote) and also the caller that '
     'builds the value before passing it on (callers.py).'),
    (r'\b(env|environment variable|environ|[A-Z][A-Z0-9]*_[A-Z0-9_]+)\b',
     'Environment variable: find the os.environ / getenv reads (locate.py environ) and keep the precedence between '
     'explicit arguments and variables. For a new or boolean-like variable handle each value explicitly: "0" turns '
     'the feature off, "1" turns it on, an empty string counts as unset, and any other value keeps the default '
     'behaviour; check how related variables (for example NO_COLOR, FORCE_COLOR) treat empty values too.'),
    (r'\b(async|await|asyncio|anyio|sync)\b',
     'Sync/async: if the package has both sync and async versions of the code, apply the same change to both files.'),
    (r'\b(docs?|documentation|readme|typo)\b',
     'Documentation: the change may be in .md/.rst files or docstrings; search them too (grep without --include).'),
    (r'\b(performance|slow|faster|memory|cache)\b',
     'Performance: keep the behaviour identical and change only the hot loop or add caching at the located place.'),
    (r'\b(script|release|workflow|ci|github action)\b',
     'Tooling task: the change likely lives in scripts/, .github/workflows/ or pyproject.toml rather than the package.'),
]


def main():
    text = ' '.join(sys.argv[1:])
    if not text.strip():
        print('usage: hints.py <problem statement text>')
        return
    _common.repeat_guard('follow the checklist printed earlier: run locate.py with the names from the statement.')
    low = text.lower()
    found = [msg for pat, msg in RULES if re.search(pat, low)]
    names = re.findall(r'`([^`]{2,60})`', text)
    print('Checklist for this statement:')
    for i, msg in enumerate(found[:6], 1):
        print(f'{i}. {msg}')
    if not found:
        found = ['Find the function that produces the behaviour described, change it minimally, keep its signature.']
        print(f'1. {found[0]}')
    print(f'{len(found[:6]) + 1}. Keep exact names, messages and exception types written in the statement.')
    if names:
        print('Names written in the statement: ' + ', '.join(dict.fromkeys(names))[:400])
    print('NEXT: run locate.py with those names.')


if __name__ == '__main__':
    main()
