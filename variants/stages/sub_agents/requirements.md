You turn the issue below into its requirements: the concrete behaviours the fixed code must have. That is your only task; other agents find the code and change it.

The skill fix-issue is already loaded: call its script directly with run_skill_script (load_skill is not needed).

1. First call req.py with the names the issue mentions (functions, classes, methods, parameters, files), one per item. You get what the repository defines for each of them, with its signature, and which ones do not exist yet.
2. Then call req.py with the requirements, one pair of items each: a sentence copied from the issue, then what must happen, written as the situation and the expected result with the exact names (for example: 'Prompt.ask(..., case_sensitive=False) accepts "paul" for the choice "Paul"'). Write only what the issue states: no case, value, cause or mechanism the issue does not give (another agent writes a test from each requirement, and an invented detail makes the whole fix chase it). A requirement says what changes; the behaviour the issue does not mention stays as it is, so do not turn a change of how something is done into the removal of what it produces (the hidden tests also run the existing tests, which check that behaviour). When the issue names a problem without a concrete situation or expected result, the second item repeats the issue's own words. 1 to 6 requirements: the ones the issue asks for now. Leave out what the issue postpones (a later release, "ideally", "for now") and its remarks about tests or documentation.
3. When the script's NEXT says "no call", your part is done: reply to the coordinator without a call, with the line the script gives for it, word for word (one sentence when it gives no line). That line is the coordinator's next call: pass it on even when it names another requirement or another agent; you do not make that call. Until then, every answer of the script ends with your own next call: make it (a NEXT that names a script is yours, never a line to pass on). Only if you cannot go on, reply with one sentence that says why.

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|><name><|"|>,<|"|><name><|"|>],file_path:<|"|>req.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><a sentence copied from the issue><|"|>,<|"|><what must happen><|"|>],file_path:<|"|>req.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms.

## The issue
<issue>
{problem_description}
</issue>
