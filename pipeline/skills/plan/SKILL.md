---
name: plan
description: "Plan stage scripts: read code with line numbers (show.py) and run a snippet (try.py) to plan one change per place. Every output ends with a JOURNAL line giving the next call."
---
Run each script with run_skill_script, skill_name "plan", file_path "scripts/<name>.py", and args as a list of strings. The JOURNAL line at the end of every output says the step and the exact next call.
