---
name: fix-issue
description: The scripts that fix the issue in the Python repository at /workspace. Each agent calls the one script its instructions name, with run_skill_script; there is nothing to load first.
---
Your instructions name the script you call and the form of each call; follow them, not this file. Every answer of a script ends with a NEXT line that gives the next call.

- The coordinator calls route.py.
- requirements calls req.py, reproduce calls repro.py, locate calls locate.py, scope calls scope.py, specify calls specify.py, edit calls edit.py, review calls review.py.

Reading the scripts' source does not help: call the script your instructions name.
