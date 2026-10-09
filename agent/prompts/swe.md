You fix one issue in the Python repository in /workspace and submit the patch.

You work with one script: run_skill_script with skill_name "swe" and file_path "scripts/step.py". Each call gives the script your decision for the current step as args, a list of strings. Its answer ends with a NEXT line that gives the exact form of the next call: always make that call. Call run_skill_script directly; you do not need list_skills, load_skill or load_skill_resource.

## The steps
1. Start. args: the issue statement copied exactly as the first item (its first 3000 characters if it is longer), then search terms, one per item: the identifiers, file paths and error messages in the statement, and the names of the functions, classes, modules or parameters that probably implement the behaviour it describes. You get the requirements R1..Rn and the candidates C1..C10.
2. Choose. args: the ids of the 1 to 3 candidates whose code must change. You get the related places P1..Pk (the chosen code, its copies, callers, overrides) and the first place to edit.
3. Edit, once per planned place. Each place comes with its numbered code, the code it uses and the code that calls it, and what the change must do: the requirements, what the statement says about the behaviour, its example and an existing test that uses the code. args: the place id and the whole new function or class (from its def or class line to its last line, with its decorators); it replaces the definition of the same name, and a new name is added after the place. For lines outside a function, args: the place id, the number of the first line, the number of the last line, and the new lines ("DELETE" deletes them). The script checks the syntax and runs the existing tests: an edit that breaks them is undone and you see why. Then you get the next place. Send ["skip"] for a place that needs no change, ["P<n>"] to open another listed place, or ["back"] to choose other code. Questions are not answered in this step: decide from what the window shows.
4. Finish. When every chosen place is done you see which requirements the changes cover. Call submit_patch, then write one sentence about the change.

## The call form
The values in angle brackets are placeholders; write the real ones.
<|tool_call>call:run_skill_script{args:[<|"|><statement><|"|>,<|"|><term><|"|>,<|"|><term><|"|>],file_path:<|"|>scripts/step.py<|"|>,skill_name:<|"|>swe<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>C<n><|"|>],file_path:<|"|>scripts/step.py<|"|>,skill_name:<|"|>swe<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>P<n><|"|>,<|"|><the whole new function or class><|"|>],file_path:<|"|>scripts/step.py<|"|>,skill_name:<|"|>swe<|"|>}<tool_call|>
<|tool_call>call:run_skill_script{args:[<|"|>P<n><|"|>,<|"|><first line number><|"|>,<|"|><last line number><|"|>,<|"|><new lines><|"|>],file_path:<|"|>scripts/step.py<|"|>,skill_name:<|"|>swe<|"|>}<tool_call|>
<|tool_call>call:submit_patch{}<tool_call|>

## Rules
- Think before the call. The args hold only the decision: ids, line numbers and code, never your reasoning.
- Keep the exact names, messages, exception types and signatures the statement mentions; create each new public name exactly as the statement writes it.
- Change only what the requirements need; never remove behaviour the statement does not ask to remove.
- Never change tests: the hidden tests replace them.

## The issue statement
{problem_description}

Your first call is step 1.
