---
name: fix-issue
description: The scripts that fix the issue in the Python repository at /workspace. Each agent calls the one script its instructions name, with run_skill_script; there is nothing to load first.
---
Your instructions name the script you call and the form of each call; follow them, not this file. Every answer of a script ends with a NEXT line that gives the next call.

- The coordinator calls step.py (start, choose, status, finish).
- specify_fix calls spec.py (the plan, then one sentence per place).
- edit_place calls open.py once, then edit.py (one place, from its sentence).

Reading the scripts' source does not help: call the script your instructions name.
