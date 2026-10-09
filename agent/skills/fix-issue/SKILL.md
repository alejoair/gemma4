---
name: fix-issue
description: Fixes an issue in the Python repository at /workspace by a fixed procedure. The only script is scripts/step.py; each call gives it the decision for the current step, and its answer ends with the exact next call.
---
Run scripts/step.py with run_skill_script, skill_name "fix-issue", file_path "scripts/step.py", and args as a list of strings.
Its answer always ends with a NEXT line: make that call.
