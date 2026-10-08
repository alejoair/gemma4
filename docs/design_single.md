# Single agent: design by Hierarchical Task Analysis

Method: Hierarchical Task Analysis (HTA, Stanton 2006, *Hierarchical task analysis: Developments, applications and
extensions*, Applied Ergonomics 37), then function allocation (Fitts; Parasuraman, Sheridan & Wickens 2000, levels of
automation), then the workflow-vs-agent patterns (Anthropic, *Building Effective Agents*, 2024) and V-model
verification. The scripts are designed after the allocation, in the detailed design; the existing scripts are only
material to reuse when they meet a specification.

Why: the earlier single agent (V4, V6) was built from the existing tools with an ad-hoc procedure on top. The journal
fixed the order, but inside each step the model still chose what to read, so it browsed (V6: 128 `show.py` calls,
first edit after 150–250 s). See `VERSIONS.md`.

## HTA step 1: purpose

Decide, for each operation needed to fix an issue in a Python repository, whether a deterministic script or the 31B
model performs it, with which exact input and output, and how much time and how many calls it gets. The goal is the
largest share of resolved tasks under the real constraints:
- 12 h for about 120 tasks (about 6 min per task on average, sandbox setup included);
- the fixed model `gemma-4-31b-it-qat-w4a16-ct` on 4×L4;
- a 32k-token context;
- a declarative ADK config;
- no internet.

The result feeds the function-allocation table, the specification of each script and each model call, and the values
of `eval_config.yaml` (time, calls and turns per task). Those values are design outputs, not inputs: 5 min and 45 calls
were our own choice.

## HTA step 2: system boundary

Inside (what we design):
- the ADK config: agents, prompts, generation settings, `eval_config.yaml`;
- the scripts of the skill `swe` and their state files in `/tmp`;
- each model call: what it receives and what it returns.

Outside (fixed environment):
- the harness: sandbox setup, the task message, tool execution, history compaction at 14,336 tokens, taking the patch;
- vLLM and the model, including the `gemma4` tool-call parser (where `「swe」` comes from);
- the ADK runtime: its skill tools and the system instruction it adds;
- validation: the hidden fail-to-pass tests and the whole suite as pass-to-pass.

Interfaces:

| Into the system | Detail |
|---|---|
| Issue statement | In the task message and the session state (`problem_description`); `hints` exists but `hints_text` is empty in all 129 dev tasks |
| Repository | Snapshot in `/workspace`, git, Python 3.13 with its dependencies and existing tests, no network |
| Harness tools | The 9 harness tools plus the skill tools; calling a tool the agent lacks ends the task |
| Graph and embeddings | Only through the 3 graph tools (node to node, no text query) |

| Out of the system | Detail |
|---|---|
| Patch | What `submit_patch` receives, else the final `git diff`; kept on a timeout, lost on an exception |

Out of scope here: training (LoRA), unless the allocation shows the base model cannot do an operation; the multi-agent
pipeline (the HTA can be reused for it later).

Key assumption: the evaluation tasks come from private repositories, so no operation may rely on knowing fastapi,
rich, requests or httpx.

## HTA step 3: information about the task

### A. The reference fixes (129 dev tasks, source code only; `docs/task_stats.py tasks.jsonl`)

| Measure | Result |
|---|---|
| Code files changed | 1 file in 69% (median 1, p90 3) |
| Functions changed | 1 function in 44%; 4 or more in 28% (p90 12) |
| Lines changed | ≤10 in 48%; median 12; >50 in 22% |
| Creates a new function or class | 33% |
| Changes docs or non-.py files | 12% |
| Same function name changed in 2+ files (sync/async twins) | 6% |

By repository: fastapi 67, rich 48, requests 13, httpx 1.

### B. The statements

| Measure | Result |
|---|---|
| Length | median 57 words; 29% under 25 words |
| Code names in backticks | 57% |
| Traceback | 1% |
| Names the function to change | 23% |
| `hints_text` not empty | 0% |

### C. Our traces (V1–V6)

- The 31B localizes well when the statement names code or BM25 finds it.
- It loses its time browsing with `show.py` when the procedure lets it choose what to read.
- It makes one small edit even when the fix needs several.
- The check on existing tests passes incomplete patches.

### D. Literature

Agentless: hierarchical localization (file → function → lines) with fixed prompts over inputs the system builds
(repository tree, file skeletons, function code), repair as search/replace edits on ±10-line windows with several
samples, and validation with regression and reproduction tests plus majority voting. The model never navigates.

### What this implies

1. Two kinds of task: half are small single-place fixes (≤10 lines, one function), a quarter are large changes over 4+
   functions, often with new code. The decomposition must cover both.
2. Localization is the critical operation: the statement names the function to change in only 23% of the tasks and
   almost never has a traceback.
3. Creating new code is common (33%): "edit existing lines" does not cover every fix.
4. There are no hints to rely on.

## HTA step 4: goals and sub-goals

What has to be achieved, not who does it (that is the function allocation). Each operation has the input it needs and
the output it produces: those become the interfaces between scripts and model calls.

**0. Fix the issue** (input: statement and repository; output: patch)

| Sub-goal | Operation | Input | Output |
|---|---|---|---|
| **1. Understand the request** | 1.1 Extract the requested behaviours (requirements) | Statement | Requirement list |
| | 1.2 Identify the named entities: functions, classes, options, messages, errors | Statement | Names and texts |
| | 1.3 Classify the change: bug fix, new feature, deprecation or rename, other | Requirements | Change type |
| **2. Locate the code** | 2.1 Find candidate files | Requirements, names, repository | Ranked files |
| | 2.2 Find candidate functions or classes in those files | Files, requirements | Ranked functions |
| | 2.3 Confirm the place by reading its code against the requirement | Candidates' code, requirement | Confirmed place(s) |
| | 2.4 Find related places that must change too: sync/async copies, overloads, exports, callers | Confirmed place, repository | Complete place list |
| **3. Decide the change** | 3.1 Per requirement and place: decide the new behaviour | Requirement, place's code | Change description |
| | 3.2 If new code is needed: decide where it goes and its exact signature | Requirement, statement names, module structure | Location and signature |
| | 3.3 Decide which existing behaviour must be kept | Place's code, existing tests | Constraints |
| **4. Make the change** | 4.1 Write the new code for each place | Change description, place's code | Change text |
| | 4.2 Apply it to the file | Change text, file | Modified file |
| | 4.3 Check it is valid: syntax, defined names, imports | Modified file | Valid or error |
| **5. Verify** | 5.1 Run the related existing tests (regression) | Changed files, repository tests | Pass or fail, with detail |
| | 5.2 Check the requested behaviour (reproduction) | Requirement, changed code | Met or not |
| | 5.3 Check every requirement and every place is covered | Requirements, place list, diff | Coverage |
| **6. Deliver** | 6.1 Remove leftovers: scratch files, prints | Diff | Clean diff |
| | 6.2 Submit the patch | Diff | Patch |

HTA step 5 (few sub-goals per level): 6 sub-goals with 2–4 operations each, within 3–10.

Link to the step-3 data: 1.3 and 3.2 exist because 33% of the fixes create new code; 2.4 because 28% change 4+
functions and 6% repeat the change in copies; 5.2 and 5.3 because the regression check passed incomplete patches in
V6; 2.1–2.3 are hierarchical (as in Agentless) because the statement names the function in only 23% of the tasks.
