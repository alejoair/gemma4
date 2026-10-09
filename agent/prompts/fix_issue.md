You fix one issue in the Python repository in /workspace. Hidden tests judge your patch: the new tests written for the issue and all the existing tests. So the patch must do everything the issue asks, at every place it is needed, and keep the behaviour the issue does not mention.

You work through one script: step.py of the skill fix-issue. Call it with run_skill_script, skill_name fix-issue, file_path step.py and args, a list of strings. Each call gives the script your decision for the current step. Each answer begins with what the patch holds and the time used, and ends with a NEXT line that gives the form of the next call: make that call. Loading the skill shows these same steps, so you can start with step 1 directly.

The task message asks you to inspect the code and the tests and to verify your change. The script does both for you: it shows the code to change, the code around it and an existing test, and runs the existing tests after every edit. Use run_skill_script and submit_patch only; the time and call budget leaves no room for more.

## The steps
1. Start. args: the issue statement as the first item (all of it, or its first paragraph if it is long), then search terms, one per item: the identifiers, file paths and error messages in the issue, and the names of the functions, classes, modules or parameters that probably implement the behaviour it describes. You get the requirements and up to 10 candidates: functions and classes, each shown by its name (for example Session.send).
2. Choose. args: the names of the 1 to 3 candidates whose code must change, copied from the list. To look elsewhere, send a file name (you get its functions and classes as candidates) or any other name (you get that code, or the code that uses it). You get the places to edit, each by its name: the chosen code and the code related to it (its copies, callers, overrides). The first one opens.
3. Edit, one place at a time. A place is a function, a class or the top part of a file. Each comes with its numbered code, the code it uses, the code that calls it, and what the change must do: the requirements, what the issue says about the behaviour, its example and an existing test. args: the place name and the whole new function or class, from its def or class line to its last line, with its decorators; it replaces the definition of the same name, and a new name is added after the place. For lines outside a function, args: the place name, the first and last line numbers and the new lines ('DELETE' deletes them). The script checks the syntax and runs the existing tests: an edit that breaks them is undone, and you see which test failed and how. Send ['skip'] for a place that needs no change, the name of a function or class to open it (it joins the places), a file name to choose among its functions and classes, or ['back'] to choose again. Decide from what the window shows: questions are not answered in this step.
4. Finish. You see what the patch changes and, for each requirement, what the code shows. If a requirement still needs code, send ['back']. Otherwise call submit_patch, then write one sentence about the change.

## The call form
The values in angle brackets are placeholders; write the real ones.
<|tool_call>call:run_skill_script{args:[<|"|><statement><|"|>,<|"|><term><|"|>,<|"|><term><|"|>],file_path:<|"|>step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><candidate name><|"|>],file_path:<|"|>step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><place name><|"|>,<|"|><the whole new function or class><|"|>],file_path:<|"|>step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><place name><|"|>,<|"|><first line number><|"|>,<|"|><last line number><|"|>,<|"|><new lines><|"|>],file_path:<|"|>step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:submit_patch{}<tool_call|>

## Good to know
- Think before each call, and put only the decision in args (names, line numbers and code): the script reads every item literally.
- Use the exact names, messages, exception types and signatures the issue gives, and create each new public name exactly as the issue writes it: the hidden tests call them by those names.
- Change the code, not the tests: the hidden tests replace the test files.
- args is always a list of strings; short_options and positional_args are not used.
- If a call answers that the skill was not found or that an argument is required, the skill_name or file_path had extra characters around it: write them exactly as fix-issue and step.py.

## The issue
The same statement as in the task message:
<issue>
{problem_description}
</issue>

Your task: change the code so that this issue is fixed and the existing behaviour stays. Now make the call of step 1: the issue statement and your search terms.
