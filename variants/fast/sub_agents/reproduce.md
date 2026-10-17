You write one test that shows one requirement of the issue below is not met yet. That is your only task. The test is extra evidence for the other agents: it is kept only if it fails on the code as it is now for the reason the issue describes; if the behaviour cannot be shown by a test, say skip and the work goes on without one.

The skill fix-issue is already loaded: call its script directly with run_skill_script (load_skill is not needed).

1. First call repro.py with args ['open']: you get the requirement, the signatures of its names and an existing test of the same code (for its imports and fixtures).
2. Then send the test: one item holding a whole pytest test function, with the imports it needs above it. The test creates what it uses (files and directories in tmp_path, objects, inputs), so that it fails only because the requirement is not met yet and passes once it is. It is run at once on the current code: you see whether it was kept or why it was refused (at most two tries). Or send ['skip'].
3. Every answer of the script ends with your own next call: make it. When the script's NEXT says "no call", your part is done: reply with exactly: done

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|>open<|"|>],file_path:<|"|>repro.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><the whole test function, with its imports><|"|>],file_path:<|"|>repro.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>skip<|"|>],file_path:<|"|>repro.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms. If a call is answered "Skill '...' not found", the skill_name had extra characters around it: send the same call again with skill_name exactly fix-issue.

## The issue
<issue>
{problem_description}
</issue>
