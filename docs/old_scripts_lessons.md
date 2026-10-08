# Lessons from the old scripts (removed)

The old skill `swe` (`submission/skills/swe/scripts/`: `_common.py`, `_journal.py`, `locate.py`, `show.py`, `edit.py`,
`check.py`, `tests_for.py`, `hints.py`, `callers.py`, `try.py`, `journal.py`; about 3,000 lines) was removed on
2026-10-08. It was built as a toolbox for a model that explores, and the redesign (`docs/design_single.md`) does not
reuse its code. This file keeps what it learned. Every item was found in a real run or a replay of real model calls.
The old code is in git history (last commit with it: the one before its removal) if a detail is needed.

## Harness and sandbox

- `run_skill_script` copies the skill's `scripts/`, `assets/` and `references/` into a temporary directory that is
  deleted when the script ends, **before** the interpreter's exit handlers run. Work done at exit can no longer read the
  skill's files or start threads: finish everything before returning.
- A `.pyc` file in the submission makes the harness reject it: never ship `__pycache__`.
- The repository is the sandbox working directory (`PWD`), else `/workspace`, else `git rev-parse --show-toplevel`.
  Run git with `-c safe.directory=*`.
- The harness drops hidden runner files (`.adk_exec_*.py`) in `/workspace`: skip hidden files when listing code and
  when building the diff.
- State per task: files in `/tmp` named with a hash of the repository path (`/tmp/swe_<name>_<sha1(PWD)[:10]>`), so
  one task's state never leaks into another.
- The submitted patch includes new untracked files: the diff to verify is `git diff` plus the untracked, non-hidden,
  non-`.pyc` files.
- Script output was clipped at about 4,000 characters, keeping the closing instruction lines (NEXT, VERDICT) intact.

## Malformed arguments seen from the models

- Each argument wrapped in literal quotes or backticks, or with a trailing comma: strip `"`, `'`, `` ` `` and `,`
  from the ends of path and symbol arguments (never from code text).
- All arguments packed into one string that looks like a JSON list (`'a.py", "10-20'`, `'["a.py", "10-20"]'`), or
  joined with ` | `: unpack them.
- Code text with literal `\n` escapes instead of line breaks (sometimes mixed with a stray real break after a
  backslash): unescape `\n`, `\t`, `\"` only when most line breaks are escaped.
- Code text with leaked tool-call syntax at its end (`file_path:`, `skill_name:`, `<|"|>`, `<tool_call|>`): cut the
  text before it.
- Code text copied with the viewer's line-number prefixes (`  79| `): strip them when every line has one.
- Escaped quotes `\"` in code that should be `"`.
- Placeholders copied literally (`start-end`, `<file>`, `symbol`).
- Paths written as dotted module names, or wrong paths: resolve them to a real file.

## Editing

- Line-range replacement worked: 14 of 15 edits applied in V6.
- Repairs worth keeping, tried in order until the file compiles: the text as given; the block re-indented to the
  indentation of the line it replaces (the usual slip is one extra space from the `79| ` prefix); escaped quotes turned
  into quotes; both.
- A range longer than the text (lines 79–80 replaced by one line drops the body of an `if`): when the edit does not
  compile, also try replacing only as many lines as the text has.
- The 12B also sent text that repeated the line before the range (a duplicated `@cached_property` in fastapi_14448):
  compare the text's first and last lines with the lines just outside the range.
- An edit that breaks the syntax is not applied; show the error, the would-be code and the original.
- Keep CRLF line ends and non-UTF-8 bytes (`errors='surrogateescape'`, `newline=''`).
- Refuse edits to test files: the hidden tests replace them.
- Deleting a definition is almost always a range that is too wide, unless the statement names it.
- Warn about names on the new lines that nothing defines (missing import or typo).
- Detect a no-op edit (new text equal to the old) and say so.
- An edit right before the time limit can leave an unverified broken patch: stop accepting edits when there is no time
  left to verify them (the old limit was 255 s of a 300 s budget, because a check could take about 40 s).

## Tests and verification

- Choose tests by the identifiers on the changed lines, rare identifiers first (inverse document frequency), then the
  test files that import the changed module. Run only the test functions that use those names when a test file is big.
- Run each test file alone with its own time limit (about 35 s), in parallel, so one slow file (sockets, servers)
  neither delays nor hides the others.
- pytest flags: `-q -p no:cacheprovider --no-header -rf --maxfail=60` (no cache written into the repository).
- `PYTHONPATH` must point to the code (also for `src/` layouts).
- Some repositories' tests import test-only modules the environment lacks (`inline_snapshot`, `dirty_equals`): retry
  with a stub module whose names are objects equal to any value. Only for modules the repository itself does not
  provide.
- A failing test is the edit's fault only if it passes on the original: run the failing ids (first 10, 40 s limit)
  in a separate `git worktree` of the original commit, never touching the working tree.
- Collection errors count as failures.
- "No module named pytest" means the tests could not run: not verified, not a failure.
- Roll back a breaking edit to the last verified state (or the original), applying only the git part of that state.
- A check that passes the existing tests says nothing about the requested behaviour: in V6 it said OK in 4 tasks
  whose hidden tests failed.
- Snippets must run in a temporary directory outside the repository with the repository importable, so nothing they
  write ends up in the patch. Unwrap `python3 -c "..."` when the model sends a shell command.

## Localization

- BM25F at function level (k1 1.2, b 0.75; each function or class a document of its own lines; identifiers split at
  `_` and case changes and lightly stemmed; the def line weighs 3, comments and long strings 0.4; `docs_src/` and
  `scripts/` at half weight; module level at 0.3) beat the old per-line count: top-1 4 → 6, top-3 8 → 9 of 20 title
  and full-statement queries on the 10 local tasks. Variants with b 0.5/0.3, name weight 2/1.5 or classes at 0.5 were
  worse. Known loss: requests_7328 (the class `Response` outranks `resolve_redirects`).
- Search terms from the statement, by weight: backticked code 5 (dotted names also whole, 4); `snake_case`,
  `camelCase` and `CapWords` 4; dotted names 3; `UPPER_CASE` 3; hyphenated words 2 and their `snake` form 3;
  capitalised words 2; plain words of 5+ letters 1; a stop-word list removes the rest.
- A code-like term that appears nowhere (a plural, a wrong class name) is replaced by the closest real identifiers
  (singular form, case-insensitive match, `difflib` ratio ≥ 0.8); `Missing.attr` falls back to `.attr`.
- 16 of the 129 dev tasks are fixed in `docs_src/` or `scripts/`: search them too.
- The statement's own links and issue references ("Fixes #2875", URLs) ask for nothing.
- A statement attribute (`.history`) is usually changed where it is assigned: list the assignments.
- Definitions of a name in several files are usually sync/async twins that change together.
- Skip directories: `.git`, virtualenvs, `node_modules`, `build`, `dist`, caches, `site-packages`, `*.egg-info`.
- Long documented functions (FastAPI's `Doc("...")` texts): collapse long string literals when showing code; show
  an outline instead of the whole body for classes over about 90 lines.

## Procedure (the journal)

- The model does not follow a suggested next step: the 31B made 16–17 reads before the requirement step (V4).
  Out-of-order calls must be refused, and a refused call must never run on a retry.
- Refusals are retried by the model unless the answer says plainly that the call will never run in this step.
- `journal.py` called again with nothing done in between is a loop.
- A fixed read count refused the right function after the procedure itself had imposed reads of wrong candidates
  (fastapi_14448), leaving no way to edit it: a gate must never leave the model without a valid move.
- A time-based switch to SUBMIT refused an improving edit while a requirement was still open (V6, fastapi_14448).
- Requirement coverage counted from the changed symbols is easily fooled: a trivial edit of the named function
  "covered" it (fastapi_14851).
- A second candidate was shown when it scored at least 60% of the first.
