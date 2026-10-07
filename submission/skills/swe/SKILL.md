---
name: swe
description: Deterministic helper scripts for fixing an issue in the Python repository at /workspace - locate code from the statement, show numbered source, edit a line range with a syntax guard, list callers, find and run related tests, verify an edit, keep a work journal, and get a fix checklist.
---
Run each script with run_skill_script, skill_name "swe", file_path "scripts/<name>.py", and args as a list of strings.

- scripts/locate.py  args: words from the problem statement -> best functions with code
- scripts/show.py    args: [file, symbol] or [file, "start-end"] or [file, a line of code] -> the code with line numbers
- scripts/edit.py    args: [file, start, end, new lines] -> replaces lines start..end (numbers from show.py); an edit that breaks the syntax is not applied
- scripts/callers.py args: [name] -> definition, callers and tests using it
- scripts/tests_for.py args: [file_or_symbol] or [file_or_symbol, "--run"] -> related tests and their result
- scripts/check.py   args: [] -> changed files, syntax check, related tests, VERDICT line; undoes an edit that breaks tests
- scripts/journal.py args: [phase, note...] or ["show"] -> work log and next step
- scripts/hints.py   args: words from the problem statement -> checklist of what such a fix touches

Each script ends with a NEXT or VERDICT line: do what it says.
