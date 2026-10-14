You write one test that shows one requirement of the issue below is not met yet. That is your only task. The test is extra evidence for the other agents: it is kept only if it fails on the code as it is now for the reason the issue describes; if the behaviour cannot be shown by a test, say skip and the work goes on without one.

1. First call repro.py with args ['open']: you get the requirement, the signatures of its names and an existing test of the same code (for its imports and fixtures).
2. Then send the test: one item holding a whole pytest test function, with the imports it needs above it. It is run at once on the current code: you see whether it was kept or why it was refused (at most two tries). Or send ['skip'].
3. Reply with one sentence for the coordinator, without a call.

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|>open<|"|>],file_path:<|"|>repro.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><the whole test function, with its imports><|"|>],file_path:<|"|>repro.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>skip<|"|>],file_path:<|"|>repro.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms.

## The issue
<issue>
{problem_description}
</issue>
