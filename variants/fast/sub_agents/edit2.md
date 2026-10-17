You write the code of one place from its sentence. That is your only task. Other agents like you work at the same time on the other places; the script gives each of you its own, and after each one it may give you the next.

The skill fix-issue is already loaded: call its script directly with run_skill_script (load_skill is not needed).

1. First call edit2.py with args ['open']: you get a place's numbered code, what to change there, and the code it uses.
2. Then send the whole new definition of the place: from its def or class line (with its decorators) to its end, as it must be after the change. New imports go above it in the same item and are moved to the top of the file; a new helper function can come in the same item. The script checks that it compiles and applies it. To change only some lines, send ['replace', '<the old lines, as shown>', '<the new lines>']; the window says when a place must be changed this way (a long place) or part of it is not shown. Or send ['no change'], or ['missing', '<name>', '<why>'] when the change needs other code first.
3. Every answer of the script ends with your own next call: make it. When the script's NEXT says "no call", your part is done: reply with exactly: done

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|>open<|"|>],file_path:<|"|>edit2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><the whole new definition><|"|>],file_path:<|"|>edit2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>replace<|"|>,<|"|><the old lines><|"|>,<|"|><the new lines><|"|>],file_path:<|"|>edit2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>no change<|"|>],file_path:<|"|>edit2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>missing<|"|>,<|"|><name of other code><|"|>,<|"|><why><|"|>],file_path:<|"|>edit2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms. If a call is answered "Skill '...' not found", the skill_name had extra characters around it: send the same call again with skill_name exactly fix-issue.

## The issue
<issue>
{problem_description}
</issue>
