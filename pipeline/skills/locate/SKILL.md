---
name: locate
description: "Locate stage scripts: find the code a problem statement is about (locate.py, show.py, callers.py) and its requirements (hints.py). Every output ends with a JOURNAL line giving the next call."
---
Run each script with run_skill_script, skill_name "locate", file_path "scripts/<name>.py", and args as a list of strings. The JOURNAL line at the end of every output says the step and the exact next call.
