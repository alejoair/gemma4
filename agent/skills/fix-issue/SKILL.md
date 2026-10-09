---
name: fix-issue
description: Fixes the issue in the Python repository at /workspace, one decision per step. Its one script, scripts/step.py, takes the decision for the current step and answers with the next one.
---
These are the same steps as in your instructions; start with step 1.

Call run_skill_script with skill_name fix-issue, file_path scripts/step.py and args, a list of strings. Each answer ends with a NEXT line that gives the form of the next call: make that call.

1. Start. args: the issue statement (all of it, or its first paragraph if it is long), then search terms, one per item. You get the requirements and up to 10 candidates, each shown by its name.
2. Choose. args: the names of the 1 to 3 candidates whose code must change. You get the places to edit, by name, and the first one opens.
3. Edit, one place at a time. args: the place name and the whole new function or class (from its def or class line to its last line), or the place name, the first and last line numbers and the new lines. Or ['skip'], another listed place's name, or ['back'].
4. Finish. If a requirement still needs code, send ['back']; otherwise call submit_patch, then write one sentence about the change.

The script shows the code and runs the existing tests after every edit, so no other tool is needed. Reading the script's source does not help: call it.
