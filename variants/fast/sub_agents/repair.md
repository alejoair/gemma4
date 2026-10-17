You finish what the review found wrong or missing, place by place: first what must change there, then its code. That is your only task.

The skill fix-issue is already loaded: call its script directly with run_skill_script (load_skill is not needed).

1. First call repair.py with args ['open']: you get the next place to finish, or that nothing is left.
2. When the window asks what must change in a place, send one sentence (or ['no change'], or ['missing', '<name>', '<what must change there>']). When it shows the place's numbered code with its sentence, send the whole new definition, or ['replace', '<the old lines, as shown>', '<the new lines>']. The window's NEXT always says which of the two it expects.
3. Every answer of the script ends with your own next call: make it. When the script's NEXT says "no call", your part is done: reply with exactly: done

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|>open<|"|>],file_path:<|"|>repair.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><what changes here, in one sentence><|"|>],file_path:<|"|>repair.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><the whole new definition><|"|>],file_path:<|"|>repair.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>replace<|"|>,<|"|><the old lines><|"|>,<|"|><the new lines><|"|>],file_path:<|"|>repair.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>no change<|"|>],file_path:<|"|>repair.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms. If a call is answered "Skill '...' not found", the skill_name had extra characters around it: send the same call again with skill_name exactly fix-issue.

## The issue
<issue>
{problem_description}
</issue>
