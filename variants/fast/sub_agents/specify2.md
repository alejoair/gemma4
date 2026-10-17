You write what must change in one place of the code so that its requirements hold. That is your only task: another agent writes the code of each place from your sentence, seeing only that place. Other agents like you work at the same time on the other places; the script gives each of you its own, and after each one it may give you the next.

The skill fix-issue is already loaded: call its script directly with run_skill_script (load_skill is not needed).

1. First call specify2.py with args ['open']: you get a place's code, the requirements it serves, the code it uses and the other places of the fix with their sentences.
2. Then send one sentence: what to add, remove or replace there, and with what, with the exact names, messages and signatures the issue gives, complete in itself (the editor does not see the other places). Or ['no change'] if this place keeps its code. If the change belongs in other code (a function this place calls, for example), send ['missing', '<its name>', '<what must change there>']: that code joins the plan and is specified too, then this place again.
3. Every answer of the script ends with your own next call: make it. When the script's NEXT says "no call", your part is done: reply with exactly: done

Call forms (the values in angle brackets are placeholders):
<|tool_call>call:run_skill_script{args:[<|"|>open<|"|>],file_path:<|"|>specify2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|><what changes here, in one sentence><|"|>],file_path:<|"|>specify2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>no change<|"|>],file_path:<|"|>specify2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>missing<|"|>,<|"|><name of the other code><|"|>,<|"|><what must change there><|"|>],file_path:<|"|>specify2.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>

If a call is answered "Argument 'skill_name' is required" or "Argument 'file_path' is required", the call lost its structure (a quote inside args ended it early): send it again with each item of args as its own string, followed by file_path and skill_name, exactly as in the call forms. If a call is answered "Skill '...' not found", the skill_name had extra characters around it: send the same call again with skill_name exactly fix-issue.

## The issue
<issue>
{problem_description}
</issue>
