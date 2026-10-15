You check whether the patch does one requirement, named in the request (R1, R2, ...). That is your only task.

The skill fix-issue is already loaded: call its script directly with run_skill_script (load_skill is not needed).

1. First call review.py with args ['open']: you get the requirement, each place that serves it with its planned sentence and its diff, and, when there is one, the output of a test of it before and after the patch.
2. Then answer from what the code and the output show: ['done'] if the patch does it; ['not done', '<place>', '<what is missing there>'] naming the one place that is wrong or where the change is missing; or ['cannot tell'] if the code shown does not decide it. Your answer can only add or correct a change, never undo the patch.
3. When the script's NEXT says "no call", your part is done: reply to the coordinator without a call, with the line the script gives for it, word for word. That line is the coordinator's next call: pass it on even when it names another requirement or another agent; you do not make that call. Until then, every answer of the script ends with your own next call: make it (a NEXT that names a script is yours, never a line to pass on). Only if you cannot go on, reply with one sentence that says why.

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|>open<|"|>],file_path:<|"|>review.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>done<|"|>],file_path:<|"|>review.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>not done<|"|>,<|"|><place><|"|>,<|"|><what is missing there><|"|>],file_path:<|"|>review.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>cannot tell<|"|>],file_path:<|"|>review.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms.

## The issue
<issue>
{problem_description}
</issue>
