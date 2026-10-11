You edit one place of the Python repository in /workspace, so that the issue below is fixed there: the request names the place. You work through the script edit.py of the skill fix-issue: call it with run_skill_script, skill_name fix-issue, file_path edit.py and args, a list of strings. Each answer ends with a NEXT line that gives the next call: make that call. You do not need load_skill.

First call: file_path open.py, args ['<the place name from the request>']: it opens the place and you get the goal it serves, its numbered code and the related code. Every later call uses file_path edit.py.
Then one of:
- ['<place name>', '<the whole new function or class>']: replaces the definition of that name, from its def or class line to its last line, with its decorators (a new name is added after the place);
- ['<place name>', '<first line number>', '<last line number>', '<new lines>']: replaces those lines, with their full indentation ('DELETE' deletes them; to add an import, the place is '<file> top level');
- ['skip'] if this place needs no change;
- ['<place name>', '<first line number>', '<last line number>'] to see other lines of the place.
- ['<name of other code>'] to read code your place uses or that uses it (read only).
The script checks the syntax and runs the existing tests that use the code: an edit that breaks them is undone, and you see which test failed and how; send a different change. After an accepted edit you see its diff and the code as it is now: if the change is complete, reply with one sentence for the coordinator (what changed; if more change belongs to other code, name it) and make no more calls; if it is wrong or incomplete, send the corrected edit. After ['skip'], reply with one sentence saying why nothing changed.

Call form (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|><place name><|"|>],file_path:<|"|>open.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><place name><|"|>,<|"|><the whole new function or class><|"|>],file_path:<|"|>edit.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

Use the exact names, messages and signatures the issue gives. When the issue says a change would be ideal but must wait (a breaking change, "for now", a later release), make only the immediate fix it asks for: the hidden tests check that fix, and the postponed change breaks them. Change the code, not the tests (test changes never count).

## The issue
<issue>
{problem_description}
</issue>
