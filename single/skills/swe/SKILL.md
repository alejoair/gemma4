---
name: swe
description: Deterministic scripts that fix an issue in the Python repository at /workspace by a fixed procedure - locate the code, read the checklist, show the numbered code, edit it with a syntax guard, check it with the related tests, submit. Every script ends with a JOURNAL line that gives the next call.
---
Run each script with run_skill_script, skill_name "swe", file_path "scripts/<name>.py", and args as a list of strings.

Procedure (the JOURNAL line at the end of every script output says which step you are in and the exact next call):
1. LOCATE: scripts/locate.py, args = names and error text of the statement
2. UNDERSTAND: scripts/hints.py, args = the sentences or bullets of the statement that ask for something (it lists the requirements and where each name is defined, or that it is new); then scripts/show.py, args = [file, symbol]
3. EDIT: scripts/edit.py, args = [file, start, end, new lines] with the line numbers show.py printed, or [new file, its full text] to create a file; cover every requirement
4. VERIFY: check.py runs automatically after every applied edit (its VERDICT ends the edit.py output); scripts/check.py, args = [] tests again
5. SUBMIT: the submit_patch tool

Other scripts: scripts/callers.py [name] (who calls a function), scripts/try.py [lines of Python] (run a snippet outside the repository, at most twice), scripts/journal.py [] (the procedure, what is done and the next call).
