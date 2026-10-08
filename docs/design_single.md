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

## HTA step 6: plans

For each goal: the order of its parts, the conditions, and where to go back when something fails. Still not who does it.

| Plan | Order and conditions |
|---|---|
| **Plan 0 (fix the issue)** | 1 → 2 → 3 → 4 → 5. Then, by the result of 5: |
| | • 5.1 shows the change broke tests → **undo the change** and go back to 3.1 with the failure |
| | • 5.2 shows the requested behaviour is not met: if the place's code is unrelated to it → back to **2.3** with the next candidate; if related → back to **3.1** |
| | • 5.3 shows a requirement or a place not covered → 3 → 4 → 5 **for that requirement or place only** |
| | • everything passes → 6 |
| | • **time limit:** if there is no time left for another 4 + 5 cycle → keep the last state that passed 5.1 (or the original if none did) and go to 6 |
| **Plan 1** | 1.1 and 1.2 in any order (both read only the statement); then 1.3 |
| **Plan 2** | If 1.2 found names defined in the repository → start 2.2 from their definitions (shortcut); else 2.1 → 2.2 |
| | 2.3 on the best candidate; if not confirmed → the next one from 2.2; when those run out → the next file from 2.1 |
| | If a requirement asks for a new entity (a name that does not exist) → in 2.2 look for where entities of the same kind live (the owner place) |
| | For each confirmed place → 2.4 |
| **Plan 3** | For each requirement: 3.3 (what to keep) → 3.2 if new code is needed → 3.1 |
| **Plan 4** | For each place in the 2.4 list: 4.1 → 4.2 → 4.3; if 4.3 fails → back to 4.1 with the error |
| **Plan 5** | 5.1 → 5.2 → 5.3 |
| **Plan 6** | 6.1 → 6.2 |

Two points the earlier design lacked: "keep the last verified state" is explicit (never deliver something worse than
what was already verified), and the 2.4 place list drives plans 4 and 5 (every place is edited and every place's
coverage is checked; V6 made one edit and submitted).

## HTA step 7: stopping rule (P×C)

An operation is redescribed only when the probability of failing (P) times the cost of the failure (C) justifies it.
P comes from small samples (10 tasks): orders of magnitude, not exact rates.

| Op. | P (evidence) | C (if it fails) | Redescribe? |
|---|---|---|---|
| 1.1 Extract requirements | Medium: `hints.py` made up requirements ("__init__", "com") | Medium: misleads the coverage check | No (guarded by 5.3) |
| 1.2 Statement names | Low: mechanical extraction | Low | No |
| 1.3 Classify the change | No data: not done today | Medium | No |
| **2.1–2.2 Candidates** | **High:** the right function is not #1 in 14/20 queries, not in the top 5 in 11/20 (BM25, 10 tasks) | **High:** wrong place = lost task | **Yes** |
| **2.3 Confirm the place** | **High:** fastapi_14448 saw the right place and left it; 14583 and 3469 never reached the right file | High | **Yes** |
| **2.4 Related places** | **High:** 28% of the fixes change 4+ functions; V6 covered **0 of 5** multi-place tasks | High | **Yes** |
| **3.1 Decide the behaviour** | **High:** in **3 of 3** V6 tasks with the right place the change was wrong or incomplete | High | **Yes** |
| **3.2 New code: where and signature** | No data, but in 33% of the tasks | High: hidden tests import the exact names | **Yes** |
| 3.3 What to keep | Medium: requests_7328 broke the `max_redirects` check | High, but 5.1 catches it | No |
| 4.1 Write the change text | Medium: 12B duplicates, escapes | Medium: 4.3 and 5.1 catch it | No |
| 4.2 Apply it | Low: **14 of 15** edits applied in V6 | Low | No |
| 4.3 Validity (syntax) | Low: deterministic guard | Low | No |
| 5.1 Regression | Low: it works; V6's false OK is because it does not check the requested behaviour (5.2's job) | Medium | No |
| **5.2 Reproduction** | **Total: not done today** | High: patches that do not meet the request are delivered | **Yes** |
| **5.3 Coverage** | **High:** it counted "1/1 covered" for touching the function with a trivial change (fastapi_14851) | High | **Yes** |
| 6.1 Remove leftovers | Low | Low | No |
| 6.2 Submit | Low | Total (an exception loses the patch), but there is nothing to redescribe | No |

Redescriptions of the high-P×C operations:

- **2.2 →** 2.2.1 rank the repository's functions · 2.2.2 present the candidates compactly (file, signature, first
  docstring line) · 2.2.3 choose the candidate(s) for the requirement
- **2.3 →** 2.3.1 present the chosen candidate's full code · 2.3.2 judge: does this code do what the requirement talks
  about? (yes/no and which lines)
- **2.4 →** 2.4.1 same-named definitions in other files (copies) · 2.4.2 callers and overrides · 2.4.3 where entities of
  the same kind are registered or exported (for new names) · 2.4.4 choose which must change
- **3.1 →** 3.1.1 state what the code does now in the requirement's case · 3.1.2 state what it must do · 3.1.3 the
  difference as a concrete line-level change
- **3.2 →** 3.2.1 find a sibling entity of the same kind as a model · 3.2.2 fix the exact name and signature from the
  statement
- **5.2 →** 5.2.1 write a snippet that exercises the requirement's case · 5.2.2 run it on the original (must show the
  problem) · 5.2.3 run it on the change (must show the fix)
- **5.3 →** 5.3.1 per requirement: which edit covers it · 5.3.2 per place from 2.4: edited, or discarded with a reason

Side finding: in V6 the journal refused 14 edits (NOT RUN), the time cut in SUBMIT seen in fastapi_14448. The redesign
fixes this, not the HTA.

## Function allocation

Model: Parasuraman, Sheridan & Wickens (2000), four stages (information acquisition, information analysis, decision
and action selection, action implementation), each with a level of automation (LOA) on Sheridan's 1–10 scale. Here the
"human operator" is the LLM and the "automation" is the scripts: LOA 10 = the script does it without asking; 7 = the
script does it and informs the model; 4 = the script proposes, the model decides; 1 = the model does it.

Criterion (Fitts, adapted to an LLM): to the script go search, counting, comparing, executing, exact recall,
bookkeeping and anything repeatable; to the LLM go understanding intent in natural language, judging what code does,
and generating new code. Our own constraint: a script runs only when the model calls it, so every script operation
with no decision in between is chained into the same call.

| Op. | Stage | Who | LOA | Why |
|---|---|---|---|---|
| 1.1 Requirements | Analysis | Script splits sentences and bullets; the LLM restates them when choosing candidates | 4 | Splitting is mechanical; knowing what asks for something needs understanding |
| 1.2 Names | Acquisition | Script | 10 | Exact extraction and definition lookup |
| 1.3 Change type | Analysis | Script (does the name exist? "add/deprecate/rename"?), the LLM may correct it | 7 | Cheap heuristic on objective data |
| 2.1 Files | Acquisition | Script (BM25) | 10 | Search |
| 2.2.1–2.2.2 Rank and present | Acquisition | Script | 10 | Search and format |
| **2.2.3 Choose candidates** | **Decision** | **LLM** on the script's list | 4 | Judgement among options; the model does not search, it chooses |
| 2.3.1 Show the chosen code | Acquisition | Script, in the same call that receives the choice | 10 | No decision in between |
| **2.3.2 Confirm the place** | **Decision** | **LLM** | 4 | Judge whether the code does what the requirement talks about |
| 2.4.1–2.4.3 Copies, callers, exports | Acquisition | Script, in the same call | 10 | Exact search |
| **2.4.4 Which places to change** | **Decision** | **LLM** on the script's list | 4 | Judgement |
| **3.1 New behaviour** | **Decision** | **LLM** | 1 | Understanding and reasoning about code |
| 3.2.1 Sibling entity as a model | Acquisition | Script | 10 | Search by kind |
| **3.2.2 Name and signature** | Decision | Script extracts the statement's exact names; **the LLM decides** | 4 | Hidden tests use those names |
| 3.3 What to keep | Analysis | Script lists the tests that touch the place | 7 | Objective data |
| **4.1 Write the change** | **Implementation** | **LLM** | 1 | Code generation |
| 4.2 Apply | Implementation | Script | 10 | Mechanical, with repairs |
| 4.3 Check syntax | Analysis | Script, reverts on failure | 10 | Deterministic |
| 5.1 Regression | Analysis | Script, automatic after 4.2 | 10 | Running tests |
| **5.2.1 Reproduction snippet** | **Implementation** | **LLM** | 1 | Code generation from the requirement |
| 5.2.2–5.2.3 Run it on the original and on the change | Analysis | Script, both runs in one call | 10 | Run and compare |
| 5.3.1 Requirement ↔ edit | Analysis | Script proposes the map; **the LLM confirms** | 4 | Semantic but anchored to data |
| 5.3.2 Place ↔ edit | Analysis | Script | 10 | Diff against the place list |
| 6.1 Remove leftovers | Implementation | Script | 10 | Mechanical |
| 6.2 Submit | Implementation | The LLM calls `submit_patch` when the script says so | 7 | The tool belongs to the harness |
| **Plans (which step is next)** | Decision | **Script (the journal)** | **10** | The model never decides the path |

Result: the LLM has **5 decision points** per task, each with its input prepared by a script:
- **D1** choose candidates (2.2.3);
- **D2** confirm the place and choose the related places (2.3.2 + 2.4.4);
- **D3** write the reproduction snippet (5.2.1);
- **D4** write the change for each place (3.1 + 3.2.2 + 4.1), retried when 4.3, 5.1 or 5.2 fail;
- **D5** confirm coverage and submit (5.3.1 + 6.2).

Everything else is deterministic and chained inside those calls. Against the earlier design: "what to read" (2.1–2.3)
was LOA 1 (the model decided); now reading is LOA 10 and choosing is LOA 4.

## Patterns (workflow vs agent)

Measured in the V6 traces (Kaggle, 31B): one model turn (from a tool result to the next call) takes a median of 5.5 s
(p90 17 s); a normal script 0.2 s (p90 0.3 s); `edit.py` with its automatic check 0.2 s (p90 8.6 s). The cost is in the
model calls, not in the scripts or the tests.

Patterns from *Building Effective Agents*:

| Pattern | Where | How |
|---|---|---|
| **Prompt chaining with gates** | The whole flow D1 → D2 → D3 → D4 → D5 | The model's output is the next script's argument. The journal is the gate: it does not advance when, for example, D1 chose a candidate that is not on the list or the change was not applied |
| **Routing** | By change type (1.3) and number of places (2.4) | The script decides what the model receives: a bug fix gets the current code; a new entity gets the sibling entity and the statement's exact names; a multi-place change goes through one D4 per place |
| **Parallelization: sectioning** | Changes in several places | One D4 per place, each with that place's window, in sequence within the same agent |
| **Evaluator-optimizer** | D4 | The evaluator is deterministic: syntax (4.3), regression (5.1), reproduction (5.2). On failure the model rewrites with the error output. At most 2 retries per place |
| Parallelization: voting (several samples, the tests choose) | **Deferred** | Agentless uses it, but it needs independent samples: inside one agent each sample sees the previous one. It needs a `ParallelAgent` with seeds and separate worktrees. Decided after measuring v1 |
| Orchestrator-workers / autonomous agent | **Not used** | The subtasks are known in advance: this is a workflow, not an agent |

Budget, derived from the design rather than fixed beforehand:
- Calls per task: 5 decisions, +1 D4 per extra place (p90 3 places), +up to 2 retries per place, +about 14% mangled
  calls. Typical about 8–12; reasonable worst case about 20.
- Time: about 10 calls × 5.5 s ≈ 1 min typical; 20 × 17 s ≈ 5.7 min worst case. The real limit is about 6 min per task
  on average, sandbox setup included.
- Proposed `eval_config.yaml`: about 5.5 min per task and a cap of about 30 calls as a safety net, leaving room for
  voting when it is tried. To be confirmed in verification.

Against V6: a typical task there used 20–40 calls, most of them to read; here the model makes about 10, all of them
decisions.

## Detailed design

One step with no decision comes first (S0): the scripts cannot read the statement (the ADK session state does not reach
them), so the first call only copies the statement into the first script.

| Step | Script | What the script does (deterministic) | What the model sees | What the model returns (next script's args) | Gate / recovery |
|---|---|---|---|---|---|
| **S0** | `start.py [statement]` | 1.1 splits requirements (R1..Rn) · 1.2 extracts names and looks up definitions · 1.3 classifies (bug / new / rename) · 2.1–2.2 BM25, top 10 candidates as skeletons · for new names, the owner place | Numbered requirements; candidates C1..C10, one or two lines each (file :: symbol, lines, signature, first docstring line, matched terms) | **D1:** `pick.py ["C2","C5"]` (1–3 ids) | Unknown id → reprint the list; empty statement → ask again |
| **D1** | `pick.py [ids]` | 2.3.1 full numbered code of the chosen ones · 2.4.1–2.4.3 same-named copies in other files, direct callers, exports · 3.2.1 sibling entity for new code · 3.3 tests touching the place | The chosen code; related places P1..Pk with their reason ("same name in `_async.py`", "caller", "exported in `__init__`") | **D2:** `plan.py ["P1: <what changes>", "P3: <what changes>"]`, or `pick.py` with other ids when none fits | Invalid id → reprint; going back to `pick.py` is allowed (plan 2: next candidate) |
| **D2** | `plan.py [plan]` | Stores the plan (places + intent) and crosses it with the requirements | Per requirement: the planned place and the signatures involved | **D3:** `repro.py [snippet]` that exercises the requirement | A requirement without a place → visible warning (does not block) |
| **D3** | `repro.py [snippet]` | 5.2.2 runs the snippet outside the repository against the original code and stores the result | The output on the original, and the first place's window (±15 numbered lines) with its plan line and the exact names | **D4:** `change.py ["P1", start, end, new lines]` | If the snippet does **not** show the problem on the original → one retry, then go on without reproduction (5.2 marked "not verified") |
| **D4** (per place) | `change.py [place, start, end, text]` | 4.2 applies (with `edit.py`'s repairs) · 4.3 syntax, revert on failure · 5.1 regression · 5.2.3 runs the stored snippet on the change · on pass, stores it as the **last verified state** | On failure: the error and the updated window. On pass: the next place's window. After the last place: the coverage map | `change.py` again (retry or next place) | Up to 2 retries per place; then revert to the last verified state and go to the next place |
| **D5** | (output of the last `change.py`) | 5.3.2 which planned places were edited · 6.1 removes leftovers | Coverage R ↔ edit ↔ place, and whether the reproduction passed | `submit_patch`, or `pick.py` when a requirement is not covered | Time: when another cycle does not fit → revert to the last verified state, NEXT = `submit_patch` |

Rules common to every script:
- Every output ends with **one NEXT line** naming the exact call with an example of its arguments. The journal
  (`_journal.py`, rewritten with these states) decides the transition; the model never chooses it.
- A script called outside its step answers NOT RUN with the NEXT. The repeat guard and the argument cleaning of
  `_common.py` stay.
- State in `/tmp`: `statement.txt`, `reqs.json`, `candidates.json`, `places.json`, `plan.json`, `repro.py` +
  `repro_orig.json`, `good.patch` (last verified state), `events.jsonl`.
- No free `show.py`: the model never asks for code, it receives it. The whole context per task stays under about 10k
  tokens, far from the compaction threshold (14k).

No reuse: every script is written new for this design. The old scripts (`submission/skills/swe/scripts/`) were built
as tools for a model that explores, and that assumption runs through their code. They were removed (see
`docs/old_scripts_lessons.md`). What they taught is kept as requirements, not as code:

| Learned constraint (from the old scripts and the runs) | Becomes |
|---|---|
| `run_skill_script` copies the skill into a temporary directory that is deleted when the script ends, before the exit handlers run; no threads at exit | Every script finishes its work (state, checks) before returning |
| A `.pyc` file in the package makes the harness reject the submission | The build leaves out `__pycache__` |
| State must not leak between tasks | State files in `/tmp` keyed by the repository path |
| The model packs arguments into one string, adds quotes or backticks, writes `\n` escapes | Argument parsing tolerant to these forms, with unit tests for each |
| Files with CRLF line ends and non-UTF-8 bytes | Read and write preserving both |
| Some repositories' tests import modules the package does not (`inline_snapshot`, `dirty_equals`) | The test runner provides stubs for them |
| A test that already fails before the change is not the change's fault | Regression compares against the original's failures |
| Slow test files | Run only the tests that use the changed names, with a time limit |
| BM25F at function level beats the per-line count (top-1 4 → 6 of 20) | The ranking method of `start.py` (rewritten) |
| Reproduction snippets must run outside the repository so they do not end up in the patch | `repro.py` runs in a temporary directory with the repository importable |

The new scripts go in a new skill directory, so the old ones cannot be called by mistake.

Unit tests, defined before writing the code (on the 14 local repositories):
- `start.py`: recall@10 of the reference function, against the reference-patch benchmark; target ≥ 80% of the tasks.
- `pick.py`: lists the copies in httpx_3672 (sync/async) and the callers in a known case.
- `repro.py`: a snippet that fails on rich_3006's original is reported as "reproduces".
- `change.py`: apply, revert on syntax, revert on regression, and store `good.patch` only when everything passes.
- Dry run of the whole flow with `harness_like.py`: S0 → D5 on rich_3006 with hand-written decisions.

## Design decisions (2026-10-08)

1. **The reproduction snippet comes before the change (D3 before D4)**, as in Agentless: the snippet is first checked to
   show the problem on the original code. If it does not after one retry, the flow goes on without reproduction and
   5.2 is marked "not verified". Cost: one call.
2. **Edits by line range, not search/replace.** Line ranges on numbered windows applied 14 of 15 edits in V6;
   search/replace needs the old text copied exactly, which small models get wrong.
3. **No free `show.py`.** The model never asks for code. So that the procedure never leaves the model without a valid
   move (the lesson of fastapi_14448), `pick.py` also accepts a name or a path that is not in the candidate list; the
   script resolves it deterministically and treats it like a chosen candidate.

## Design v1: cheap techniques only (2026-10-08)

The only design principle: make the task easier for the 31B. This version uses only the cheap techniques of the
failure table in CLAUDE.md: no several-sample voting, no verifiers, no LoRA, no RL, no tree search, and no
reproduction snippet (LLMs write useful reproduction tests in 3.6–49% of cases; it must be measured before the design
depends on it). It replaces the "Detailed design" table above where they differ.

Checked and discarded: `tool_choice="required"` (vLLM would then constrain the call format). The ADK's
`generate_content_config` forbids extra fields (`extra="forbid"`; no `tool_config`), so it cannot be set.

### One script for every step

The model calls the same script every time: `run_skill_script(skill_name="swe", file_path="scripts/step.py",
args=[...])`. The journal inside `step.py` knows the current step and reads `args` as that step's decision; every
output ends with the exact `args` the next call needs. A wrong script or a call out of order cannot happen, and
`file_path` is always the same text.

| Failure it removes | Technique | Source |
|---|---|---|
| Calls to scripts that do not exist; out-of-order calls and their retried refusals | Fewer, simpler tools; only the valid action of the step | SWE-agent ACI; SOP-Agent |

### The steps

| Step | The model's call (`args`) | What `step.py` does (deterministic) | What the model sees next |
|---|---|---|---|
| **S0 Start** | `[statement, search terms]`: the statement copied, plus the identifiers, file paths, error messages and behaviour words it contains | Splits requirements (R1..Rn); extracts names and looks up their definitions; BM25F over functions and classes with the statement **and** the model's search terms (query reformulation) | Requirements; top 10 candidates C1..C10, one or two lines each (file :: symbol, lines, signature, first docstring line, matched terms) |
| **D1 Choose** | `["C2", "C5"]` (1–3 ids), or a name or path not on the list | Shows the chosen code (numbered, long strings collapsed); impact analysis: same-named definitions in other files, direct callers, overrides and subclasses, exports in `__init__` | The chosen code; related places P1..Pk with their reason |
| **D2 Plan** | `["P1: <what changes>", "P3: <what changes>"]`, or `["back"]` | Stores the plan; maps requirements to places | The first planned place's window (±15 numbered lines) with its plan line and the statement's exact names |
| **D3 Edit** (once per planned place) | `["P1", start, end, new lines]` | Applies (with the known repairs), checks syntax (reverts on failure), runs the related existing tests, compares against the failures already present before the change, stores the last verified state | A fixed verdict line, then: on failure the error and the updated window; on success the next place's window; after the last place the coverage map |
| **D4 Finish** | `submit_patch` | Before saying so, removes leftovers and keeps the last verified state | — |

### Cheap techniques, by failure

| Failure (from CLAUDE.md) | Technique in v1 | Source |
|---|---|---|
| Reads a lot, edits late | Fixed steps with prepared inputs; the model never asks for code | Agentless |
| Leaves the right place | Fixed hierarchical localization (candidates → chosen code → places → lines); going back is an explicit choice (`back`, or another id) | Agentless |
| Superficial matching | Query reformulation in S0: the model writes the search terms in the same call that copies the statement (no extra call) | Reformulate, Retrieve, Localize |
| Incomplete repair across places | Impact analysis lists the related places; one D3 per planned place | CodePlan (light), LocAgent |
| Loops and repeated calls | Stuck detector in the journal: the same `args` twice in a step is answered with the expected form and not run again; the same error 3 times on one place skips it and keeps the last verified state | OpenHands stuck detector, SHERLOC |
| Misreading test output | The script decides from the tests and prints one verdict; the journal, not the model, chooses the next step | Agentless (deterministic reading) |
| Early errors cascade | Explicit way back: `back` in D2, or a place that fails twice returns to D1 with the remaining candidates | AgentDebug (idea, without the trained debugger) |
| Context growth | Small outputs: skeleton lines, ±15-line windows, long strings collapsed; target under about 8k tokens per task | Agentless, Beyond Generalist |
| Skill-induced failures | Short prompt; mandatory form separated from examples; examples use visibly fake values (`<file>`, `<start>`), never real-looking ones | Agent Skills Can Be Harmful |
| Malformed calls | Same `file_path` always; tolerant parsing of `args` (quotes, packed strings, escapes); thinking budget kept modest; `temperature: 0` to be measured as the only cheap lever left for `「swe」` | ACI, SHERLOC |
| Premature or chained actions | One call per turn, native function calling | Overthinking (Cuadron et al.) |

### Calls and time

S0 + D1 + D2 + one D3 per place + submit: 5 calls for one place, 7 for three places; with up to 2 retries per place
and about 14% malformed calls, about 8–12 calls typical. At a median of 5.5 s per model turn that is about 1 minute.
`eval_config.yaml`: about 5.5 min and 30 calls as a safety net.

### Tools of the agent

`submit_patch`, the skill `swe` (only `step.py` in `scripts/`), and the three graph tools: the harness's task message
advertises them, and a call to a tool the agent lacks ends the task. The prompt says they are not needed.

### Left out of v1 (not cheap or not measured)

Reproduction snippet (AssertFlip variant first, measured on the 10 local tasks against the reference patches);
several patch samples with test voting; trained verifier or reranker (LoRA); tree search.
