You decide which code related to the chosen places must change too, so that the requirements hold everywhere they apply. That is your only task.

The skill fix-issue is already loaded: call its script directly with run_skill_script (load_skill is not needed).

1. First call scope.py with args ['open']: you get the chosen places, the requirements, and the related code (callers, readers, overrides, code of the same name), each with the line that links it.
2. Then send the names of the related code that must change too, one per item, or ['none']. Code that only uses the chosen places and keeps working unchanged does not need to change.
3. Reply to the coordinator without a call: with the line the script gives for it, or else one sentence.

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|>open<|"|>],file_path:<|"|>scope.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><name><|"|>,<|"|><name><|"|>],file_path:<|"|>scope.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>none<|"|>],file_path:<|"|>scope.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms.

## The issue
<issue>
{problem_description}
</issue>
