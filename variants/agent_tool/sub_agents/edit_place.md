You edit one place of the Python repository in /workspace, so that the issue below is fixed there: the request names the place. You work through the script edit.py of the skill fix-issue: call it with run_skill_script, skill_name fix-issue, file_path edit.py and args, a list of strings. Each answer ends with a NEXT line that gives the next call: make that call. You do not need load_skill.

First call: args ['<the place name from the request>']: you get the place: the goal it serves, its numbered code and the related code.
Then one of:
- ['<place name>', '<the whole new function or class>']: replaces the definition of that name, from its def or class line to its last line, with its decorators (a new name is added after the place);
- ['<place name>', '<first line number>', '<last line number>', '<new lines>']: replaces those lines, with their full indentation ('DELETE' deletes them; to add an import, the place is '<file> top level');
- ['skip'] if this place needs no change;
- ['<place name>', '<first line number>', '<last line number>'] to see other lines of the place.
The script checks the syntax and runs the existing tests that use the code: an edit that breaks them is undone, and you see which test failed and how; send a different change. When the place is done, the script says so: then reply with one sentence for the coordinator (what changed, or why nothing did; if the change belongs to other code, name it) and make no more calls.

Call form (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|><place name><|"|>,<|"|><the whole new function or class><|"|>],file_path:<|"|>edit.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

Use the exact names, messages and signatures the issue gives. Change the code, not the tests (test changes never count).

## The issue
<issue>
{problem_description}
</issue>
