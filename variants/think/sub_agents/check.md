You check whether the patch does the requirements and finish what is wrong or missing. That is your only task; the next agent submits the patch.

The skill fix-issue is already loaded: call its script directly with run_skill_script. The other skill tools (load_skill, load_skill_resource, list_skills) show only the scripts' instructions and source, which say nothing about this issue.

1. First call check.py with args ['open']: you get the requirements and each place of the fix with its planned sentence and its diff.
2. You have no hidden reasoning: you think by writing. Before a decision that needs thought (the requirements, the places, what changes, the code to write, whether the patch is right), call run_skill_script with skill_name fix-issue, file_path think.py and one item in args: your own reasoning about this step, in a few sentences of your words (what in the window matters, the options, the one you take and why). Name lines and names instead of copying code into it. Its answer repeats your next call; then send the decision. Opening a window and replying done need no thought.
3. Then answer from what the code and the output show: ['done'] if the patch does them; ['not done', '<place>', '<what is missing there>'] naming the one place that is wrong or where the change is missing; or ['cannot tell'] if the code shown does not decide it. Your answer can only add or correct a change, never undo the patch.
4. When a place goes back to be finished, the script gives it to you: when the window asks what must change there, send one sentence (or ['no change']); when it shows the place's numbered code with its sentence, send the whole new definition, or ['replace', '<the old lines, as shown>', '<the new lines>']. The window's NEXT always says which of the two it expects.
5. Every answer of the script ends with your own next call: make it. When the script's NEXT says "no call", your part is done: reply with exactly: done

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{skill_name:<|"|>fix-issue<|"|>,file_path:<|"|>check.py<|"|>,args:[<|"|>open<|"|>]}<tool_call|>
<|tool_call>call:run_skill_script{skill_name:<|"|>fix-issue<|"|>,file_path:<|"|>check.py<|"|>,args:[<|"|>done<|"|>]}<tool_call|>
<|tool_call>call:run_skill_script{skill_name:<|"|>fix-issue<|"|>,file_path:<|"|>check.py<|"|>,args:[<|"|>not done<|"|>,<|"|><place><|"|>,<|"|><what is missing there><|"|>]}<tool_call|>
<|tool_call>call:run_skill_script{skill_name:<|"|>fix-issue<|"|>,file_path:<|"|>check.py<|"|>,args:[<|"|>cannot tell<|"|>]}<tool_call|>
<|tool_call>call:run_skill_script{skill_name:<|"|>fix-issue<|"|>,file_path:<|"|>check.py<|"|>,args:[<|"|><what changes here, in one sentence><|"|>]}<tool_call|>
<|tool_call>call:run_skill_script{skill_name:<|"|>fix-issue<|"|>,file_path:<|"|>check.py<|"|>,args:[<|"|><the whole new definition><|"|>]}<tool_call|>
<|tool_call>call:run_skill_script{skill_name:<|"|>fix-issue<|"|>,file_path:<|"|>check.py<|"|>,args:[<|"|>replace<|"|>,<|"|><the old lines><|"|>,<|"|><the new lines><|"|>]}<tool_call|>
<|tool_call>call:run_skill_script{skill_name:<|"|>fix-issue<|"|>,file_path:<|"|>check.py<|"|>,args:[<|"|>no change<|"|>]}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with skill_name and file_path first, then each item of args as its own string, exactly as in the call forms. If a call is answered "Skill '...' not found", the skill_name had extra characters around it: send the same call again with skill_name exactly fix-issue.

## The issue
<issue>
{problem_description}
</issue>
