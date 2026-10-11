You fix one issue in the Python repository in /workspace. Hidden tests judge your patch: the new tests written for the issue and all the existing tests. So the patch must do everything the issue asks, at every place it is needed, and keep the behaviour the issue does not mention.

You work through the script step.py of the skill fix-issue (call it with run_skill_script, skill_name fix-issue, file_path step.py and args, a list of strings) and the tool edit_place. Each answer of the script ends with a NEXT line that gives the next call: make that call. You do not need load_skill.

1. Start. args: the whole issue statement as the first item (every line of it), then search terms, one per item: the identifiers, file paths and error messages in the issue, and the names of the code that probably implements the behaviour it describes. You get the requirements and candidates: functions and classes, each by its name.
2. Choose. args: the names of the 1 to 3 candidates whose code must change. To look elsewhere, send a name or a text to find, a file name, or a file with two line numbers: you get the code that holds it, to choose from. You get the places to edit: the chosen code and the code related to it.
3. Edit. For each place, call the tool edit_place with request '<place name>' (the name only): an editor, which has the issue too, reads that place, decides the change and makes it itself (the script checks the syntax and runs the existing tests, and undoes an edit that breaks them), then replies with one sentence. You do not write code and do not tell it what to change. Then call step.py with args ['status']: you get the places left and the next call. If an editor says the change belongs to other code, choose that code (step 2 form) and edit it.
4. Finish. You see what the patch changes and, for each requirement, what the code shows. If a requirement still needs code, send ['back'] to step.py. Otherwise call the tool submit_patch with no arguments, then write one sentence about the change.

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|><candidate name><|"|>],file_path:<|"|>step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:edit_place{request:<|"|><place name><|"|>}<tool_call|>
<|tool_call>call:submit_patch{}<tool_call|>

Use the exact names, messages and signatures the issue gives: the hidden tests call them by those names. Change the code, not the tests.

## The issue
<issue>
{problem_description}
</issue>

Now make the call of step 1: the issue statement and your search terms.
