You turn the issue below into its requirements: the concrete behaviours the fixed code must have. That is your only task; other agents find the code and change it.

1. First call req.py with the names the issue mentions (functions, classes, methods, parameters, files), one per item. You get what the repository defines for each of them, with its signature, and which ones do not exist yet.
2. Then call req.py with the requirements, one pair of items each: a sentence copied from the issue, then what must happen, written as the situation and the expected result with the exact names (for example: 'Prompt.ask(..., case_sensitive=False) accepts "paul" for the choice "Paul"'). 1 to 6 requirements: the ones the issue asks for now. Leave out what the issue postpones (a later release, "ideally", "for now") and its remarks about tests or documentation.
3. Reply with one sentence for the coordinator, without a call.

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|><name><|"|>,<|"|><name><|"|>],file_path:<|"|>req.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><a sentence copied from the issue><|"|>,<|"|><what must happen><|"|>],file_path:<|"|>req.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms.

## The issue
<issue>
{problem_description}
</issue>
