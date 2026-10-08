---
name: edit
description: "Edit stage scripts: show code with line numbers (show.py), replace lines with a syntax guard (edit.py; check.py runs automatically after an applied edit), test again (check.py). Every output ends with a JOURNAL line giving the next call."
---
Run each script with run_skill_script, skill_name "edit", file_path "scripts/<name>.py", and args as a list of strings. The JOURNAL line at the end of every output says the step and the exact next call.
