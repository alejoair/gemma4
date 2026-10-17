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

## Session 2: rich_3180, the same setup

The state is the one Kaggle reached in vf3/vf4:
- R1: "CJK characters should not disappear when the fold location is within a double-width character";
- located at `divide_line` and `Text.wrap`.

The reference rewrites `chop_cells` (`rich/cells.py`) to walk the characters forwards instead of `reversed()`. In `divide_line` it only renames variables and adapts the call to the new signature.

| Turn | Gemma sent | Its thinking | The script answered |
|---|---|---|---|
| 1 | `open` | — | `divide_line` whole, with `words` and `loop_last`; `chop_cells` as a signature only |
| 2 | `missing chop_cells` "Ensure it does not split a double-width character…" | half of it copies `divide_line` verbatim; **cut mid-sentence** (580 tokens) | `chop_cells` whole |
| 3 | "replace the loop that processes characters in reverse with one that processes them in forward order … remove the 'reversed()' call" | copies both functions again; **cut** (603 tokens) | saved: **the reference's change**; back to `divide_line` |
| 4 | `no change` on `divide_line` | cut while working an example | (the answer was lost: the lab's output was piped to `head`, which killed it; the session was rebuilt from the script's view, and the tool now saves before it prints) |
| 5 | **no call**: 3,072 output tokens, 52 s | the thinking was cut at about 512 tokens, then **the model went on reasoning in its visible text**. It found the fault ("`Aሴ` has width 3. But width was 2! … because `chop_cells` was splitting from the end") and stopped at `max_output_tokens` with no call. | the agent ends; `Text.wrap` is never specified |

## What the two sessions show together

1. **Every decision turn hits the thinking budget**: 551-603 output tokens against 512, in 11 of 11 decision turns. Kaggle shows the same at 1024 (vf4: 49 of 77 long thoughts are at the cap).
   - When the thought is cut, Gemma either emits the default call, "no change" (5 of 6 in session 1),
   - or it keeps reasoning in its visible text until `max_output_tokens` and ends without a call (session 2, turn 5).
2. **Gemma spends much of the budget copying code it was just shown into its thought.** In session 2 about half of each thought is a verbatim copy of a function from the window.
3. **When the window shows the code that decides the behaviour, the decision is right even with a cut thought.** In session 2, turn 3, the `chop_cells` sentence is the reference's change. In session 1 the deciding code (the class fields and who sets them) was never shown, and Gemma asked for it three times through `missing`.

## Variant B: an instruction to think briefly (10-17)

The variant is `tools/lab_cases/specify/instr_short_think.md`, the specify1 instruction plus one paragraph:
- point at code by name and line instead of copying it;
- think in three short steps (which lines decide, what they do now, what they must do), then make the call;
- 'no change' only when the place already does what the requirement says; otherwise send the best sentence, since review and repair check afterwards.

The result is that it does not help:
- **fastapi_14419, turns 1-3:** less code copied, but the thought was still cut (551 and 580 tokens) and it re-read the issue from the start ("Wait … let's re-read carefully"). Turn 2 was "no change" on `solve_dependencies` (reasonable: the logic is in `cache_key`); turn 3 used `missing Dependant.computed_scope` to read.
- **rich_3180, turns 1-2:** the same as without it: `divide_line` copied into the thought, cut at 589 tokens, `missing chop_cells`.

The model's way of thinking does not change with an instruction; the budget is still exceeded every decision turn.

## Where the missing information comes from (the user's question)

In fastapi_14419 the missing facts are mainly a fault of **the specify view** built by `_stages.py`, and partly of locate:
- **The specify view:**
  - For a method that reads `self.<attribute>`, it shows the functions the method calls, but not the class's fields or where those attributes are assigned (here `Dependant(..., security_scopes=...)` in `get_dependant`).
  - It shows unrelated members instead.
  - Its name resolution does not know class attributes, hence the false "does not exist yet".
- **Locate** chose `cache_key` but not the functions that set the field, which the reference changes too.
- **The model** asked for exactly the missing code three times. `missing` is its only way to ask, since it cannot read.

## Fix 1: the specify view (vf6, 10-17)

The changes are in `agent/skills/fix-issue/scripts/step.py`, used by the specifier and the editor:

1. **The attributes a method reads.** A new block, "Attributes it reads (where each is declared and where it is set)", lists each data attribute of its own class the place reads as `self.<name>`:
   - the line that declares it (a class-level field, or the first `self.<name> = …`);
   - up to 2 calls that build the class with `<name>=…`;
   - when that value is a parameter of the building function, the call that passes it, one step further;
   - then the class's other fields by name, one line.
2. **Short members of the class are shown as code to the specifier** up to 10 lines (6 before), so `computed_scope` is no longer a bare signature.
3. **Members of the class the place does not call** are shown only when they share at least 2 names with the place (1 before). `is_gen_callable` and `is_async_gen_callable` shared only `self.call`.
4. **`Class.attribute` and `Class.__init__` resolve to the class.** The first applies when the class declares or assigns the attribute; the second when the class has no `__init__` of its own (a dataclass). Before, `missing` answered both as new code.

### The same session with the fix (fastapi_14419, local 31B, budget 512)

| Turn | Gemma sent | Note |
|---|---|---|
| 1 | `open` | the `solve_dependencies` view now shows `Dependant.cache_key` as code (a short member it reads) |
| 2 | `no change` on `solve_dependencies` | "cache_key should probably be different depending on whether scopes are used": the change is placed in the right code |
| 3 | `missing Dependant.security_scopes_with_subdependencies` "Add a cached property that returns … all security scopes used by the dependant and its sub-dependencies recursively" | `missing` used for a change, not to read |
| 4 | "Add a @cached_property named security_scopes_with_subdependencies that returns a set containing self.security_scopes (if not None) and the security_scopes_with_subdependencies of all dependants in self.dependencies." | it checked the field: "Let me check if `Dependant` has `dependencies` … 'Dependant also declares: … dependencies' Yes, it does." |
| 5 | "Replace tuple(sorted(set(self.security_scopes or []))) with tuple(sorted(self.security_scopes_with_subdependencies))." | the part ends with two sentences |

| | vf5 | vf6 view |
|---|---|---|
| `missing` used to read | 3 of 9 calls | 0 of 4 |
| false facts from the script | 1 | 0 |
| "no change" | 6 of 6 decisions | 1 (`solve_dependencies`, consistent with its reasoning) |
| result | empty patch | two concrete sentences, 5 turns instead of 9 |

The thought is still cut every turn (551-598 tokens), but with the deciding facts in the window the decision comes before the cut.

The logic is not the reference's:
- the reference leaves the scopes out of the key when the dependency does not use them;
- Gemma's version adds the sub-dependencies' scopes.

Whether it passes the tests is not checked: the editor and the tests are the next steps.

### Checklist (`docs/llm_checklist.md`)

| Item | Effect | Why |
|---|---|---|
| W3 code in front of it | closer | The fields the place reads and the code that sets them are in the window; the session's decisions turned on them |
| B16 a form with side effects used to read | closer | Nothing is left to ask for through `missing` (0 of 4 calls against 3 of 9) |
| B11 trust in the script's facts | closer | A field is no longer "new code" |
| P7 unrelated context | closer | Members sharing one name are left out; the attribute block is about 1,000 characters at most |
| B7 long answers | farther, slightly | The window grows by the attribute block (up to 1,200 characters). Reason: the facts the decision turns on |
| B8 forms | neutral | No form added |
| B9 exploring | neutral | Same forms; fewer reasons to ask |

**Why it is generic:** it reads Python's own structure for any repository. The rules it uses:
- class-level fields and `self.<name>` assignments;
- keyword arguments of the calls that build the class, and one step to the parameter's caller;
- dataclass `__init__`.

No name, library or task is special-cased.

## Acceptance test of the specify step (agreed with the user, 10-17)

**The step is tested alone.** Each case starts with the reference's places located, so a failure is the step's own. The requirements quote the statement and add nothing it does not say. `feasible_from_statement` marks the cases whose change can be inferred from it.

**The cases** are in `tools/lab_cases/specify/`:
- 6 that ended with an empty patch in vf4: fastapi_14419, rich_3180, rich_3043, rich_3934, rich_3772, rich_4076. rich_4076 replaces rich_3942, whose statement ("Update to markdown styles") cannot give the reference's colours.
- 4 controls that vf4 resolved: fastapi_14361, rich_3905, fastapi_5624, fastapi_11194.

**The protocol:** 2 samples per case, run step by step with `tools/step_lab.py`.

**The step is accepted when:**
- there are 0 system faults;
- at most 1 turn in 10 ends without a call;
- at most 1 run in 12 ends with every place "no change" when the location is right;
- at least 2 in 3 runs give a sentence describing the reference's change, wholly or in part;
- the controls do not regress;
- the median time per place is 60 s or less at Kaggle's speed.

**The final check** is a Kaggle eval of the 30 tasks.

### Round 1 (vf6 build: the view fix; local 31B, reasoning budget 512; 10 cases × 1 sample)

| Case | Turns | Decisions | Faults | No call | Grade | Note |
|---|---|---|---|---|---|---|
| rich_4076 | 2 | 1 sentence | 0 | 0 | partial | `splitlines(keepends=True)`; the reference splits after each `\n` and strips it in `decode_line` |
| rich_3772 | 2 | 1 sentence | 0 | 0 | partial | a visited set for exception groups; the reference passes it into the recursive calls |
| rich_3934 | 3 | no change + 1 sentence | 0 | 0 | wrong | adds a blank line in `LiveRender`; the reference skips the line in `Live.stop`. The statement is two words. |
| rich_3043 | 2 | 1 sentence | 0 | 0 | correct | moves `<html>` before `<head>` |
| fastapi_14419 | 5 | 4 × no change | 0 | 0 | **wrong (empty)** | every thought cut at 551 tokens; it judged the key already right and never linked the parent's scopes passed down by `get_dependant` |
| rich_3180 | 4 | 2 × no change, then no call | 0 | 1 | **wrong (empty)** | turn 2 was cut while finding the bug (`Result: ["CB", "A`) and sent no change; turn 4 reasoned in its text to 3,072 tokens (44 s) |
| fastapi_14361 (control) | 2 | 1 sentence | 0 | 0 | correct | skip a schema without `"$ref"` |
| rich_3905 (control) | 2 | 1 sentence | 0 | 0 | correct | `if not self.disable`; it checked the field in the new "also declares" line |
| fastapi_5624 (control) | 2 | 1 sentence | 0 | 0 | partial | right fault (a shared list mutated); copies it once instead of on each pass of the loop |
| fastapi_11194 (control) | 2 | 1 sentence | 0 | 0 | correct | each field's `field_info` instead of the first one's |

The first four cases ran with an inflated clock (1-3.5 minutes used at the start), because the states were prepared about 20 minutes before. The lab now pauses the task clock between steps (`shift_clock`).

**Against the criterion after round 1:**

| Criterion | Result | Status |
|---|---|---|
| System faults | 0 | pass |
| Turns without a call | 1 of 26 | pass |
| Empty plan with the location right | 2 of 6 (fastapi_14419, rich_3180) | **fail** |
| Correct or partial | 7 of 10 | pass |
| Controls | 3 correct, 1 partial | to watch |

**The two empty plans have the same cause.** The thought is cut at the budget just as the reasoning reaches the fault, and the call after a cut thought is "no change". Of the 16 decisions of round 1, 15 had their thought cut at the cap; 8 of those 15 were "no change" or no call. The only one that finished below the cap (rich_3905, 492 tokens) was right.

## Fix 2: the decision split in two (vf7, 10-17; tested with the reasoning budget at 1024, Kaggle's)

Why each decision needed so much reasoning (21 decision turns of rounds 1-2):
- 43% of the thought's characters were code copied from the window;
- "Wait" came 1.6 times per turn and a re-read 0.9 times;
- the model ran code by hand, character by character;
- earlier thoughts are not sent back, so each turn starts from nothing.

The model fills whatever budget it gets: at 1024 it traced longer examples and was cut again (1,063 tokens).

**The three changes** (`_stages.py` `specify`, the specify instruction in `tools/make_fast.py`):
1. **The decision is split in two calls.**
   - First `['<the lines that make it fail>', '<what they do now>']`. The lines must be in the place, whitespace aside; this is refused at most twice.
   - Then the sentence, after an answer that repeats that finding.
   - The finding stays in the conversation as the call's own text, and is shown again if the place is reopened.
2. **The framing.** The window says that the requirements do not hold in the current code (that is why the issue exists) and asks for what breaks them. Before, the model spent its budget showing the code already worked ("That is already handled", 4 times in one session).
3. **'no change' names the line that already does what the requirement says**, found in the place. Without it, it is refused at most twice. The NEXT line shows that form.

New places and places whose edit is in the patch keep the old single call.

| Case | Turns | Grade | vf6 (same budget) |
|---|---|---|---|
| fastapi_14419 | 6 | partial: the diagnosis of `cache_key` is the reference's ("always includes security_scopes … regardless of whether the dependency or its sub-dependencies use scopes"); the sentences key on empty scopes and lift scopes to the parent | empty (round 1) and empty (round 2, unfinished) |
| rich_3180 | 5 | wrong: the diagnosis cites the reversed loop, but the sentence keeps "the reversed processing order" | empty / correct after the empty-patch warning |
| fastapi_14361 (control) | 3 | correct | correct |
| rich_3905 (control) | 3 | correct | correct |
| fastapi_5624 (control) | 3 | **correct**: the diagnosis names the reference cause ("leak into others at the same level") | partial |
| fastapi_11194 (control) | 3 | correct | correct |

**Result:**
- 0 empty plans (vf6: 3 of 4 runs of these two cases);
- 0 system faults and 0 turns without a call;
- the controls went from 3 correct and 1 partial to 4 correct.
- **The cost is one more call per place.** Easy places do not reach the cap (329-835 tokens a call). Hard ones still are cut in every call (about 1,100 tokens), but the cut call now carries a finding or a sentence, not "no change".

Decision tokens and time at Kaggle's speed (about 35 tokens/s):

| Case | Decision turns | Tokens | Time |
|---|---|---|---|
| fastapi_14419 | 5 | 5,646 | about 161 s |
| rich_3180 | 4 | 4,804 | about 137 s |
| controls | 2 each | 1,008-2,416 | 29-69 s |

The hard cases are too slow for the 5-minute budget with specify alone taking 2-3 minutes. That is the open problem.

What still fails is understanding, not the process: in rich_3180 the model found the right lines and then kept the fault in its own sentence.

### Checklist (`docs/llm_checklist.md`)

| Item | Effect | Why |
|---|---|---|
| B9 deciding / exploring | closer | Each call asks for one concrete thing; "no change" is no longer free |
| W7 thinking before the call | closer | The finding written in one call is read before the next; the thought no longer has to hold both steps |
| P3 positive rule with its reason | closer | "R1 does not hold in the current code: that is why the issue exists" |
| B11 trust in output | closer | A "no change" needs a line that is in the place (checked) |
| B2 copying text | farther | The model copies the failing lines. Reason: they anchor the finding; any one matching line is accepted, whitespace aside |
| B8 forms | farther, one form | The finding call. Reason: 15 of 16 decisions cut at the budget, 8 of them "no change" or no call |
| B10 budget / time | farther | One more call per place: about 30 s at Kaggle's speed for an easy place |

**Why it is generic:** every issue describes behaviour the current code does not have. The checks compare text with the place's own lines. Nothing depends on a repository or a task.

## Acceptance test with vf7 (10 cases × 2 samples, budget 1024)

The full tables are in `docs/specify_lab_vf7_results.md`.

Two fixes were made during the runs:
- **rich_3934, sample 1:** 'no change' was accepted with the evidence `def stop(self) -> None:`.
- **fastapi_14419, sample 2:** it was accepted with the parameter lines of `get_dependant`'s signature.

Now only lines of the body count, without the signature or the docstring (found with the AST). fastapi_14419 was run once more with the fix.

**Against the criterion:**

| Criterion | Result | Status |
|---|---|---|
| System faults | 1, signature lines accepted as evidence (2 occurrences); fixed, and the rerun was clean | fixed |
| Turns ending without a call in the middle of the work | 0 | pass |
| All "no change" with the location right | 1 of 12 runs (fastapi_14419 sample 2, through the signature loophole); vf6: 3 of 4 runs of the two hard cases | pass (at the limit) |
| Correct or partial | 15 of 20 (16 of 20 with the rerun in place of the loophole run) | pass |
| Controls | 8 of 8 correct (vf6: 3 correct and 1 partial in sample 1) | pass |
| Median decision time per place at 35 tokens/s | 55 s | pass |
| Whole step within about 90 s | single-place cases 29-69 s; two- and three-place cases 95-189 s | **fail for several places** |

The fast build has 3 specify workers in parallel. Its places are split between them, so the wall time of a 3-place case is closer to one worker's share.

**Still wrong:**
- rich_3180, both samples: the diagnosis names the reversed loop, but the sentence keeps it. This is understanding, not the process.
- rich_3934, sample 1: a two-word statement.

**Verdict:** the step meets the agreed criterion except the whole-step time when one specifier holds two or three places. The next check is the Kaggle eval of the whole build.

## Where specify's time goes (g10: vf7 with the local 31B on 10 tasks, 10-17)

All 112 specify calls of the run were counted from the gate's records (`step_gate.py --serial`).

| Kind of call | Count | Output tokens |
|---|---|---|
| open | 30 (3 per task, one per worker; 2 of the 3 usually find nothing left) | 42-290 |
| finding | 12 | about 1,100 each |
| sentence | 11 | about 1,100 each |
| no change | 9 (5 of them in fastapi_15661, 3 after a refusal) | about 1,070 each |
| missing | 3 | about 1,100 each |
| text with no call | 27 (most are the final "done"; 4 are reasoning spilled into the text, 467-1,026 tokens) | |

- **Decision calls:** 62, median 1,002 output tokens; 29 were cut at the 1,024 budget.
- **A place costs two full-budget calls** (the finding, then the sentence), about 2,200 tokens: about 63 s at Kaggle's speed (35 tokens/s), against one call in vf5.
- **The refusals of 'no change' added calls in one task** (fastapi_15661: 5 'no change' calls).
- **Total specify output per task:** 935-7,762 tokens, upstream time 100-558 s.
- **Locally the 3 workers run one after another**, so that time is their sum. On Kaggle they run together.
- **For comparison, vf5 on Kaggle** (one decision call per place): specify span median 66 s, maximum 204 s.

**What it costs:** every decision call fills the thinking budget, easy or hard. The split doubled the number of decision calls per place.

**The fix to test:** keep the finding before the decision, but in **one call**: `['<the lines that make it fail>', '<what they do now>', '<what they must do instead>']`. The written finding still comes first, and is still kept in the conversation. A 'no change' without a body line is refused once, not twice.
