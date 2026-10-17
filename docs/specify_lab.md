# The specify step, step by step (10-17)

The user asked for a detailed analysis of one step, the specify step, tested live against the model, one call at a time. The tool is `tools/step_lab.py`:
- `start` prepares the state with the fixed calls of a case (`tools/lab_cases/`);
- each `next` makes one model call and runs its tool calls with the build's scripts;
- it prints the thinking, the call and the script's answer whole.

The request is the one the ADK sends: the agent's instruction, the ADK's appended text, the four skill tools, the agent's sampling and the "For context:" message.

## Session 1: fastapi_14419, local Gemma 31B (llama.cpp, reasoning budget 512), vf5 build

The state is the one Kaggle reached in vf3/vf4:
- R1: "A dependency that doesn't use scopes and doesn't have sub-dependencies with scopes is cached based on the function";
- located at `solve_dependencies` and `Dependant.cache_key`.

The reference changes `Dependant.cache_key`, adds a `Dependant._uses_scopes`, and renames the scopes fields set in `get_dependant` and `get_parameterless_sub_dependant`.

| Turn | Gemma sent | Its thinking | The script answered |
|---|---|---|---|
| 1 | `open` | follows the instruction | `solve_dependencies` whole (about 150 lines); `Dependant.cache_key` as a signature only; no fields of `Dependant` |
| 2 | `no change` on `solve_dependencies` | "I don't have its code yet, but it's listed as 'to specify' … `solve_dependencies` might not need any changes"; **cut mid-sentence** ("maybe it's because") | the next place, `cache_key` |
| 3 | `missing Dependant.computed_scope` "I need to see how computed_scope is implemented" | **`missing` used to read** (B16) | `computed_scope` joins the plan |
| 4 | `no change` | **cut mid-sentence** ("Currently, `Dependant` does not") | back to `cache_key` |
| 5 | `missing Dependant.security_scopes` "I need to know how security_scopes are handled" | reading again; it had found that `dependencies` must be looked at | **false fact:** "`Dependant.security_scopes` does not exist yet: it is new code". It is a dataclass field. |
| 6 | `missing Dependant.__init__` "to see how Dependant is initialized" | "confusing because the provided `cache_key` uses `self.security_scopes`" | refused: one step down only |
| 7 | `no change` | **cut mid-sentence** | back to `cache_key`; the same unrelated `is_gen_callable` and `is_async_gen_callable` blocks again |
| 8 | `no change` | **cut mid-sentence** | vf5's warning: "no place of the fix changes: the patch stays empty" |
| 9 | `no change` | **cut mid-sentence**, re-reading the issue from the start | "Your part is done": the empty patch of Kaggle, reproduced |

## What the session shows

1. **The thinking budget ends the reasoning before the decision, and the call after a cut thought is "no change".**
   - Every decision turn used 551-586 output tokens: the 512 budget plus the call.
   - 5 of the 6 decisions were "no change" after a thought cut mid-sentence, often just as it was about to state the fault.
   - Each turn re-reads the issue and the code from the start, because the ADK does not send earlier thoughts back. So the same budget runs out at the same point every turn, and vf5's warning changed nothing.
   - **The same happens on Kaggle at 1024 tokens.** In the vf4 log, 49 of the 77 long specify thoughts (over 1,500 characters) are 3,500-4,679 characters long, about 1,024 tokens: the cap of `sampling_1k.yaml`.
2. **The window lacks the facts the decision needs, and shows code that does not bear on it.**
   - Missing: the fields of the class (`security_scopes`, `dependencies`, `security_scopes_param_name`) and the code that sets them (`get_dependant`, `get_parameterless_sub_dependant`).
   - Shown in every window instead: `is_gen_callable` and `is_async_gen_callable` (P7). `computed_scope` is shown as a signature only, so the model asked for it.
3. **`missing` is used to read (B16):** 3 of 9 calls. Each one adds a place to the plan or is refused, and costs a turn.
4. **The script states a false fact (B11):** a class attribute is "new code".
5. **Each specifier defers to the other place it cannot see (turn 2).** With parallel specifiers, the one on the other place does the same.

## Candidate fixes, to be tested with the same session before any build

| Fix | Checklist |
|---|---|
| Raise the specify thinking budget, or make the decision need less reasoning | W7, B9 (cost: time) |
| Show the fields of the class and the code that sets the fields the place reads | W3; P7 if the unrelated members are dropped |
| A class attribute named with `missing` is answered with its definition and where it is set, not "new code" | B11 |
| A read form that changes nothing (the code of a named function or field), so that `missing` means a change | B16 |
