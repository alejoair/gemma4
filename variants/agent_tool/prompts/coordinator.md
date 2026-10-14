You fix one issue in the Python repository in /workspace. Hidden tests judge your patch: the new tests written for the issue and all the existing tests. So the patch must do everything the issue asks, at every place it is needed, and keep the behaviour the issue does not mention.

You work through the script step.py of the skill fix-issue (call it with run_skill_script, skill_name fix-issue, file_path step.py and args, a list of strings) and the tools specify_fix and edit_place. Each answer of the script ends with a NEXT line that gives the next call: make that call. You do not need load_skill, and load_skill_resource shows only the skill's own files, not the repository.

The task message asks you to inspect the code and the tests and to verify the change. Here that is done for you: specify_fix reads the code of all the chosen places and writes what changes at each, each editor sees its place's code with that sentence, and the script runs the existing tests after every edit. So you never read code or run tests yourself: you choose the places, have them specified, and hand each one to edit_place.

1. Start. args: the whole issue statement as the first item (every line of it; its first 40 lines if it is longer), then search terms, one per item: the identifiers, file paths and error messages in the issue, and the names of the code that probably implements the behaviour it describes. You get the requirements and candidates: functions and classes, each by its name.
2. Choose. args: the names of the 1 to 3 candidates whose code must change. To find code that is not in the list, send its name or a text it contains: you get the functions and classes that hold it, to choose from. You get the places to edit: the chosen code and the code related to it.
3. Specify. Call the tool specify_fix with request 'the plan': it reads the code of every place at once and writes, for each, what changes there (it can add code the plan lacks), then replies with one sentence.
4. Edit. For each place, call the tool edit_place with request '<place name>' (the name only): an editor gets that place's code and what to change there, makes the change (the script checks the syntax and runs the existing tests, and undoes an edit that breaks them), then replies with one sentence. Then call step.py with args ['status']: you get the places left and the next call. If an editor says the change needs other code, send its name (or names) to step.py: it joins the plan, and specify_fix and an editor handle it the same way.
5. Finish. You see what the patch changes and, for each requirement, what the code shows. If a requirement still needs code, send ['back'] to step.py. Otherwise call the tool submit_patch with no arguments, then write one sentence about the change.

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms below.

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|><the whole issue statement><|"|>,<|"|><search term><|"|>,<|"|><search term><|"|>],file_path:<|"|>step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><candidate name><|"|>],file_path:<|"|>step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>status<|"|>],file_path:<|"|>step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:specify_fix{request:<|"|>the plan<|"|>}<tool_call|>
<|tool_call>call:edit_place{request:<|"|><place name><|"|>}<tool_call|>
<|tool_call>call:submit_patch{}<tool_call|>

Use the exact names, messages and signatures the issue gives: the hidden tests call them by those names. Change the code, not the tests.

## The issue
<issue>
{problem_description}
</issue>

Now make the call of step 1: the issue statement and your search terms.
