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
| **S0 Start** | `[statement, search terms]`: the statement copied, plus the identifiers, file paths, error messages and behaviour words it contains | Splits requirements (R1..Rn); extracts names and looks up their definitions; classifies each requirement (HTA 1.3): fix of existing code, new entity (a name the statement asks for that does not exist yet), rename or deprecation; BM25F over functions and classes with the statement **and** the model's search terms (query reformulation); for a new entity, adds its **owner place** to the candidates (where entities of the same kind live, for example the class that `app.frontend` belongs to) | Requirements with their type; top 10 candidates C1..C10, one or two lines each (file :: symbol, lines, signature, first docstring line, matched terms), owner places marked |
| **D1 Choose** | `["C2", "C5"]` (1–3 ids), or a name or path not on the list | Shows the chosen code (numbered, long strings collapsed); impact analysis: same-named definitions in other files, direct callers, overrides and subclasses, exports in `__init__`; for a new entity, shows a **sibling entity** of the same kind as a model (signature and body) and the exact name the statement uses | The chosen code; related places P1..Pk with their reason; for a new entity, the sibling and the exact name |
| **D2 Plan** | `["P1: <what changes>", "P3: <what changes>"]`, or `["back"]` | Stores the plan; maps requirements to places | The first planned place's window (±15 numbered lines) with its plan line and the statement's exact names |
| **D3 Edit** (once per planned place) | `["P1", start, end, new lines]` | Applies (with the known repairs), checks syntax (reverts on failure), runs the related existing tests, compares against the failures already present before the change, stores the last verified state | A fixed verdict line, then: on failure the error and the updated window; on success the next place's window; after the last place the coverage map |
| **D4 Finish** | `submit_patch`, or `["R2"]` for a requirement the coverage map shows as not covered | Shows the coverage map (requirement ↔ edited place); on `["R2"]` goes back to D1 with that requirement's candidates; before `submit_patch`, removes leftovers and keeps the last verified state | On `["R2"]`: the candidates of R2 |

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

### HTA coverage of v1

| HTA operation | In v1 |
|---|---|
| 1.1 Requirements · 1.2 Names · 1.3 Change type | S0 (`_statement.py`) |
| 2.1–2.2 Candidates | S0 (`_rank.py`); new entities get their owner place (plan 2) |
| 2.3 Confirm the place | D1 chooses, D2 confirms or goes `back` |
| 2.4 Related places | D1 (`_impact.py`) |
| 3.1 New behaviour | D2 ("what changes" per place) |
| 3.2 New code: where and signature | D1 shows the sibling entity and the exact name; D2 plans it |
| 3.3 What to keep | Indirect: the regression tests of D3 |
| 4.1 Write · 4.2 Apply · 4.3 Syntax | D3 (`_edit.py`) |
| 5.1 Regression | D3 (`_tests.py`) |
| 5.2 Reproduction | Out of v1 on purpose (not cheap, not measured) |
| 5.3 Coverage | D4: coverage map; an uncovered requirement goes back to D1 (plan 0) |
| 6.1 Leftovers · 6.2 Submit | D4 |
| Plan 0 time limit → last verified state | `_journal.py` |
| Plan 2 next candidate | `back` in D2; another id in D1 |

### Scripts

The model calls only `step.py`. The other files are internal modules: their names start with `_`, and called directly
they do nothing and point to `step.py`. All in the new skill's `scripts/`.

| File | Used by | Responsibility | Unit test (written before the code) |
|---|---|---|---|
| `step.py` | The model (the only entry point) | Reads the current step from the journal, reads `args` as that step's decision, calls the modules, ends with a fixed verdict and the exact next call | Whole flow S0 → D4 dry, with hand-written decisions, on the local tasks |
| `_journal.py` | `step.py` | State machine (S0, D1, D2, D3 per place, D4): transitions, going back (`back`, a place that fails twice, an uncovered requirement), stuck detector (same `args`, same error 3 times), time limits | Every transition and recovery with simulated events |
| `_args.py` | `step.py` | Tolerant parsing of `args`: extra quotes or backticks, everything packed in one string, escaped `\n`, leaked call syntax, line-number prefixes | One case per malformed form in `docs/old_scripts_lessons.md` |
| `_state.py` | All | State files in `/tmp`, one set per repository (statement, requirements, candidates, places, plan, last verified state, events) | Two repositories never share state |
| `_repo.py` | All | Repository root; `.py` files (with `docs_src/` and `scripts/`, without hidden files and caches); read and write keeping CRLF and non-UTF-8 bytes; `git diff` plus new files | A CRLF file with invalid bytes is read and written back identical |
| `_statement.py` | S0 | Requirements (bullets and sentences, without links and "Fixes #N"); code names and where they are defined; change type per requirement | Expected requirements, names and types on the local statements |
| `_rank.py` | S0 | BM25F over functions and classes with the statement plus the model's terms; owner places for new entities; top 10 | **Recall@10 of the reference function over the 129 tasks**, target ≥ 80% |
| `_code.py` | D1, D2, D3 | AST: symbols, skeleton (signature + first docstring line), numbered code, ±15-line windows, long strings collapsed | Exact format and line numbers on test files |
| `_impact.py` | D1 | Related places: same-named definitions in other files, direct callers, overrides and subclasses, exports in `__init__`; sibling entity for a new one | httpx_3672's sync/async copies; the callers of a known case; a sibling for fastapi_15800's `frontend` |
| `_edit.py` | D3 | Line-range replacement with the known repairs (indentation, escaped quotes, range longer than the text, duplicated boundary line) and a syntax guard that reverts | Each repair and each revert |
| `_tests.py` | D3 | Picks tests by the changed names; runs them with time limits and stubs; compares with the failures present before the change; prints a fixed verdict; stores or restores the last verified state | An edit that breaks a test (reverted), a neutral edit (OK), a failure already present before the change (not counted) |

Outside the skill (our tools, not shipped): `tools/build.py` (builds the submission without `__pycache__` and
validates it), `tools/bench_locate.py` (the 129-task localization benchmark, the test of `_rank.py`), `tests/` (the
unit tests). Agent config: `agent.yaml` (`submit_patch`, the skill and the three graph tools), the short prompt,
`configs/` and `eval_config.yaml`.

## Implementation of v1 and its verification (2026-10-09)

All the scripts of the table above exist in `agent/skills/swe/scripts/`; `agent/` is the submission (`tools/build.py`
builds and validates it: VALID). Unit tests: `tests/`, 85 passing (`venv/bin/python -m pytest -q tests`).

**Localization benchmark** (`tools/bench_locate.py`, the test of `_rank.py`; 129 dev tasks, 128 with a gold place in
an existing file; a hit is any gold function, class or module level in the top k). The model's search terms were
simulated with the local 12B (one call per statement, thinking off, the S0 instruction), `scratchpad/terms_12b.json`.

| Query | hit@1 | hit@3 | hit@5 | hit@10 |
|---|---|---|---|---|
| Statement only | 40 | 69 | 78 | 85 (66%) |
| Statement + 12B terms | 48 | 76 | 83 | 92 (72%) |
| Statement + 12B terms, module level at full weight | 49 | 79 | 88 | **97 (76%)** |

- Kept: module level at full weight (0.3 lost 4 module-level hits and gained none; function-level hits 91 vs 90).
- Field weights, b and k1 variations moved hit@10 by at most ±2 tasks: the old values stay.
- The target of 80% is not met with the 12B's terms. Most misses have statements that name nothing locatable
  ("empty live", "fix superfluous space", a checklist only); the test tasks are curated so that a frontier model
  solves them, so their statements should be more informative. The 31B's terms are still to be measured.
- Impact analysis (`--impact`): from the best gold place, the other gold places it relates are 57 of 403 (54 tasks
  with several places). Most multi-place reference patches change places with no call or copy relation (new
  features touching many files); impact analysis covers copies, twins and overrides (httpx_3672) but not those.

**Dry runs.** S0 → D4 by hand on requests_7328 (37 s for the tests, of which 35 s a test file that hangs; such files
are now left out after their first timeout). S0 + D1 on the 14 local repositories: gold place among the candidates in
10 of 14; answers of 2–6k characters.

**Local 12B, first run (rich_3006).** S0, D1 and D2 worked in 3 calls (about 10 s). The edit failed 6 times: the 12B
read the placeholder `"<first line>"` as the text of the line and sent `"79|    if ..."`; the repeat answer did not
say what was wrong. Fixed: the placeholders say "line number", a numbered-line text is read as its number, and a
malformed edit or a repeat says exactly what was wrong (an instance of "skill-induced failures": an example value
taken literally).

**Local 12B, second run (rich_3006).** 6 calls, 22 s: S0, C1, plan, one edit, submit. The plan and the edit were
wrong (`is not param.empty` instead of `is param.empty`), and the check said OK: the test selection had picked
`test_inspect.py`, `test_columns.py` and `test_color.py` by shared words (`param`, `default`, `empty`) and not
`tests/test_repr.py`. Fixed: the changed module's own test file (`test_<module>.py`) comes first, then the files that
import the changed module, then the rare shared names. With that, the same edit is BROKEN (2 tests of
`tests/test_repr.py`) and undone.

**Local 12B, third run (rich_3006).** The wrong edit was now BROKEN and undone (5 calls, 14 s). The 12B then wanted
to correct its plan line (`is not` → `is`), had no form for it in the edit step, reasoned inside the tool-call
argument and ran into the 4,096-token limit (80 s): the cut call ends the task in the harness. Fixed: in the edit
step a corrected plan line `["P1: <what changes there>"]` updates the plan and shows the place again, and the BROKEN
answer says so. The runaway itself is the known 12B escape loop; watch for it with the 31B.

**Local 12B, fourth run (rich_3006).** BROKEN → corrected plan line (`is`) → "Plan updated" worked, but then the
12B sent the same plan line three more times. Fixed: a repeat is answered "STOP REPEATING" followed by the call to
make now, and the edit window says on which line the code named in the plan is (`line 79 (param.default ==
param.empty)`), so the next call is concrete.

**Local 12B, fifth run (rich_3006): RESOLVED** (harness and local re-verification). 6 calls (S0, C1, plan, one
edit, submit, final text), about 20 s of model time; the patch is the reference patch. The plan said `is` this time
(sampling at temperature 0.2), so the BROKEN path was not exercised in this run.

**Local 12B, requests_7328, first run.** The 12B copied only the title as the statement, chose C1 and C2
(`Session.send`, `resolve_redirects`) and planned both: the plan for `resolve_redirects` named the right cause (the
response is put in its own history). It started with `Session.send`, sent an edit identical to the old lines, then
repeated the same plan line despite "STOP REPEATING". Fixed (stuck detector): in the edit step a repeated call counts
as a failed edit of the current place, so after two failures the place keeps its last verified code and the next
planned place comes. Prompt tokens reached 11.8k after 7 calls (two chosen functions shown in full).

**Local runs and thinking.** The local runs so far had thinking off (`no_think.flag` in the stream proxy, meant to
approximate the 512-token budget). The harness sends `enable_thinking: true` and `thinking_token_budget: 512` for our
`thinking_config`, so on Kaggle the 31B thinks. Without thinking the 12B reasoned inside the tool-call argument and
ran into the 4,096-token limit twice (rich_3006 and requests_7328, both in a plan line): an artifact of the local
setup. From now on local runs keep thinking on (llama-server's global budget is 3,072 tokens). The prompt also says
that the reasoning goes before the call and the args hold only the decision.

**Local 12B, requests_7328 with thinking on: not resolved (time).** With llama-server's global 3,072-token reasoning
budget each call took 60–75 s and the prompt grew by about 4.4k tokens per call (16k after 5 calls, then
compaction); the 5.5 min ran out after 6 calls with an empty patch. The 12B planned only `Session.send` with a vague
line ("change how history is handled") and re-sent the plan line. On Kaggle the budget is 512 tokens, so neither the
time nor the growth is representative; a faithful local loop needs llama-server restarted with
`--reasoning-budget 512` (the GPU machine's session is inactive, so this is for the user).

Local 12B tally of v1 so far: rich_3006 resolved (6 calls); requests_7328 not resolved (wrong place first; the right
cause was in its plan for `resolve_redirects` in the first attempt).

**Pre-submit review (2026-10-09).** Stress run on the 14 local repositories (full flow plus malformed calls in every
step: empty args, unknown ids, placeholders, repeats, back, requirement ids, a harmless edit with tests). Found and
fixed:
- `「C1」, "C2"` (the 31B's known malformation) was not read as ids: the model would have stayed in the choose step.
- A plan with the literal placeholder `<what changes there>` was accepted; now refused.
- A repeat that used up the only planned place raised an exception (the plan was read after being emptied).
- Parallel test files waited one after another, each with its own limit (57 s seen); now one shared deadline. Limits
  30 s per check and 25 s on the original code, whose results are kept per test id; `--timeout=15` per test when
  pytest-timeout is installed.
- A masked pytest ("pytest is disabled", a harness option) counts as not verified.
- Any internal error is answered in one line with the next call, never with a traceback.
- `eval_config` back to 5 min (5.5 min plus sandbox setup risks the 12 h limit over about 120 tasks); edits stop at
  220 s.

**Local 12B, rich_3521 (thinking on), first run.** S0 and C1,C2 fine; in the plan step the 12B re-sent `C1,C2` (back
to choose, harmless) and then copied a place line (`"P2 rich/segment.py :: Segment._split_cells (lines 109-159) —
chosen"`) three times. Fixed: a place id without the colon plans that place (no plan line), and a repeat in the plan
step plans the chosen places and opens the first one for editing (the stuck detector moves the work on instead of
waiting for a decision the model does not produce).

**Design change (2026-10-09): no separate plan step.** In the local 12B runs the free-text plan (D2) was where 4 of 5
problem runs failed: two runaways that copied code or reasoning into the plan argument (task lost), a copied place line
repeated three times, a vague plan on the wrong place repeated. Under the only principle (make the task easier for the
model), the plan decision is removed: after choosing, the chosen places are the plan, the same answer is the first edit
window, and related places (copies, callers, overrides) stay listed and are edited by id when needed. One call and one
free-text decision fewer; the impact list (CodePlan-light) is kept. A plan line sent in the edit step is still
accepted. Steps are now S0 start → D1 choose (= open for editing) → D3 edit per place → D4 finish.

**Local 12B, rich_3521, third run (no plan step).** S0, C1+C2 and the edit window worked; the 12B then sent names
(`Segment.split_cells`) and candidate ids in the edit step: it did not know what to change. The statement is only
"Segment.split_cells fix" plus an external link, and the reference rewrites the algorithm of `_split_cells`; the 31B
solved it in V6, the 12B has nothing to infer it from. Fix: candidate ids in the edit step choose again. Idea for
later: show the existing test of the chosen function next to its code (the expected behaviour), for statements that
say nothing.

**Local 12B, httpx_3672 (no plan step).** S0 and C1,C2,C3 fine; in the edit step the 12B sent `HTTPParser.complete`
(a code name: it wanted to open that code), then called `get_code_neighbors` twice and chose C6,C7. Same pattern as
rich_3521 (`Segment.split_cells`). Fixed: a single code name that exists in the repository opens that code in the edit
step, as in the choose step.

**Local 12B, httpx_3672, second run.** With code names accepted in the edit step, the 12B opened whole files
(`src/httpx/_parsers.py`, `_server.py`, `_parsers.py` again): browsing, with the prompt at 16.9k tokens after 6 calls.
Fixed: a bare file path is not a place (answered with the edit window); the repeat detector also catches a call equal
to any of the last three (ping-pong A-B-A, as in OpenHands' stuck detector).

**Local 12B, httpx_3672, third run: resolved by the local re-verification** (fail-to-pass tests pass, no new
failures; the harness said 0/1 because of this container's environment, as in earlier runs). 13 calls in 5 min (time
limit): it chose twice, a file path was refused, then it added `HTTPParser.reset` (OK, 32 existing tests pass). Seen:
the 12B packed an edit into one string (`"P1,378,399,    def complete..."`), refused twice before it sent the four
items. Fixed: an edit packed as `P1,start,end,text` or `P1 | start | end | text` is read.

**Local 12B, fastapi_14986: not resolved.** The candidates held the right code (C2 `FastAPI.setup.openapi`, C3
`FastAPI.__init__`); the 12B chose C9, then C2, sent an edit equal to the old lines, then a broken one (undone), went
back, and ended repeating `"self.servers.append"` ten times in the choose step (it wanted to find code by its text).
Fixed: a code fragment that is not a function or class name opens the function that contains it; two repeats in the
choose step open the first candidate. rich_3469 ("fix superfluous space") was stopped early: nothing in the statement
locates the code (the reference function is not among the candidates).

Tally of the local 12B loop with thinking on (2026-10-09): rich_3006 resolved earlier; httpx_3672 resolved by the
local re-verification; rich_3521, rich_3469 (statements without information), requests_7328, fastapi_14986 not
resolved. Every run found a protocol problem, each fixed with a unit test (102 tests).

**Local 12B, fastapi_14986, second run (testing the fixes).** S0 and C2 fine; the 12B then sent `"P2"` alone in the
edit step (it wanted the related place `FastAPI.openapi`), answered as a malformed edit. Fixed: a place id alone opens
that place for editing.

**Local 12B, fastapi_14986, third run: not resolved, no procedure errors.** 12 calls in 5 min (time limit, 20–30 s per
call). The new tolerances worked as designed: code names opened their functions (`server_urls` → `FastAPI.setup`,
`FastAPI.openapi`, `get_swagger_ui_html`), a file path was refused, repeats were stopped. The 12B used them to look
around and made one edit late (`url: {json.dumps(openapi_url)}` in `get_swagger_ui_html`, a part of the reference fix);
5 of 7 fail-to-pass tests still fail. The remaining gap is the model's decision, not the protocol.

### Local 12B batch of the 10 local tasks (2026-10-09, commits c32d90b → b43892c)

Run with the local 12B (reasoning budget 3072, the server's global value; Kaggle uses 512) and the 7-minute budget,
monitored call by call through the gateway. The scripts were fixed during the batch; each task loads the scripts at
its start, so the later tasks ran later fixes.

| Task | Result | Calls | What happened |
|---|---|---|---|
| fastapi_14448 | not resolved (hidden tests error) | 14 | Chose `Dependant`, edited three of its methods. Its packed edit `["P1,78,85", code]` was not read at first (fixed). Time ran out |
| fastapi_14583 | no patch | 14 | Asked for `pydantic.v1` six times in a row (reasoning at the budget each time). No text search existed then (added) |
| fastapi_14851 | lost (harness exception) | 10 | A reply ran away (`false,false,…`) and was cut at 4,096 tokens; the invalid JSON made the harness raise and lose the task. Before that, the model sent the statement again after compaction and was refused (now answered with the current step) |
| fastapi_14986 | not resolved (2 tests fail) | 15 | Good flow: removed the `root_path` insertion, escaped two Swagger UI values, submitted. The changes were incomplete |
| fastapi_15800 | no patch | 12 | A large new feature (`frontend`); read the classes in parts and ran out of time |
| httpx_3672 | no patch | 15 | `"C6,C7"` read as a text search (fixed). After compaction it sent whole argument lists as one string with escaped quotes (fixed) |
| requests_7328 | no patch | 12 | **Had the right fix** (`resp.history = hist[:-1]`) and sent it 8 times as one string with plain quotes, which was not read (fixed) |
| rich_3006 | **resolved** | 10 | 149 s |
| rich_3469 | no patch | 15 | Sent `["C2", "rich/text.py::pad_left", …]` in the edit step, refused (now they join the plan) |
| rich_3521 | no patch | 10 | Asked for lines and the test file; reasoning at the budget on most calls |

System faults found and fixed during the batch:
- packed edits with the code apart;
- no text search;
- a question after the place id;
- the statement sent again after compaction;
- a repeated applied edit counted as a failure;
- questions in the finish step;
- small files not shown;
- several candidate ids at once;
- the question-limit answer without the candidate to add;
- argument lists sent as one string, with escaped or plain quotes;
- trailing bare booleans.

Model faults:
- most calls hit the 3,072-token reasoning budget (60–70 s each), so a task got only 10–15 calls;
- the same call repeated;
- one runaway reply.

The local budget is not Kaggle's (512); a faithful local loop needs `--reasoning-budget 512` on the server.

### Local 12B batch 2 of the 10 local tasks (2026-10-09, v9 = b792504, fixed during the run up to ce369b5)

Result: 1/10 (rich_3006, 124 s), as in batch 1. Patches were produced in fastapi_14448, fastapi_14583 and httpx_3672,
besides rich_3006; batch 1 produced fewer.

**System faults found and fixed during the batch:**
- empty new lines were ambiguous (view or delete); they are now answered with both options, and `DELETE` deletes;
- names, class names and `C11:file::Name` labels did not join the plan;
- a module path without `.py` got no outline;
- "OK" was given with 0 passing tests; it is now NOT VERIFIED;
- no file-scoped search;
- `Router` opened a function that only contains the word;
- an edit that a repair turned back into the original lines was reported as OK, and the task ended with an empty patch;
- asking questions made the model leave the right place;
- no rule against reading without editing: questions now stop at call 12 without an edit;
- repeated questions after compaction pointed to an answer no longer in the history;
- search text lost its brackets;
- **a required rename (`complete` → `reset`) was undone as BROKEN** because existing tests use the old name; such failures no longer count;
- no way to run code: `python -c` was added.

**Model faults:**
- most calls hit the 3,072-token local reasoning budget;
- the same question repeated;
- `async def` written into the sync class.

The harness on Kaggle was updated the same day (swegemma 0.2.11). The local runs still use 0.2.7.

### Local 12B batch 4 (2026-10-09 evening, v13 = the checklist fixes, fixed during the run up to the commit after cf27011)

Run with the local 12B (reasoning budget 3072, Kaggle's is 512), monitored call by call. Stopped at 20:08 UTC when the
gateway went down (Cloudflare 502, then 530); httpx_3672 and the four tasks after it did not run.

| Task | Result | What happened |
|---|---|---|
| fastapi_14448 | no patch | The model moved between file names looking for "the function that analyses callables" and never chose `Dependant._unwrapped_call` (listed first when it sent `fastapi/dependencies/models.py` in a dry run) |
| fastapi_14583 | no patch | It asked for `fastapi/_compat/v1.py` in a loop: the change is in its top-level code, which a file's candidate list could not reach then (fixed) |
| fastapi_14851 | patch, 9 tests fail | Edited `FastAPI.on_event` by line range; the re-implementation the issue asks for is larger |
| fastapi_14986 | patch, 3 of 7 tests pass | Escaped the Swagger UI values with `json.dumps` by line edits and called submit_patch itself |
| fastapi_15800 | patch, not resolved | — |

System faults found and fixed during the batch:
- the window did not list properties the place reads on other objects (`dependant.is_gen_callable`);
- a file path at the choose step opened the whole module as one place;
- a bare name the repository does not define (`is_async_callable`, Starlette's) fell back to a text search that opened
  a module through its import line;
- the edit step refused names of code the window had just shown (`Dependant`), and file names;
- a name the repository uses but does not define gave nothing (now: the code that uses it, as candidates);
- a file's candidates did not include its top-level code;
- signatures in the "uses" list carried whole `Doc(...)` texts.

Model faults: navigating by file names instead of choosing; reasoning at the 3,072-token budget (55–70 s per call);
copying placeholders (`'<first line number>'`) into a call; once, a repository file path in the tool's `file_path`
parameter (the ADK's parameter name invites it).

### Why the patches of the near misses stay incomplete (V13b traces, 2026-10-09)

Method: each V13b patch was applied with the task's test patch on the local snapshot and the new tests were run
(`scratchpad/near_run.py`; the reference patch passes them, except rich's `test_inline_code`, which fails with the
local Pygments too); then every call of the trace was read with the script's full answer (`scratchpad/trace_step.py`).

| Task | Failing new tests | Model patch vs reference | Cause |
|---|---|---|---|
| fastapi_14986 | 4 of 7: root_path tests (2), Swagger escaping (2) | deleted the root_path logic; `json.dumps` on two values | **The statement was lost at step 1**: the model sent only the title, so the requirements and every window's "What the change must do" held only "Refactor logic to handle OpenAPI and Swagger UI escaping data". The two concrete sentences ("do not store `root_path` in servers", "Escape Swagger UI configs", "JSON inside of HTML") never reached a window. It read "do not store" as "do not add" and deleted the behaviour; the deletion was "Kept, not checked" (see the test faults below). Submitted at 3.6 of 7 min |
| fastapi_14851 | 9 of 9 (`Router.__init__() got an unexpected keyword argument 'on_startup'`) | +2 lines in `APIRouter` vs a 6.8k-character re-implementation | **The window of `FastAPI.__init__` (940 lines) hid the code to change**: lines 964-999, the `routing.APIRouter(..., on_startup=..., on_shutdown=..., lifespan=...)` call. The window spent its 98 lines on `self.x = x` and on Doc texts that mention "FastAPI" (the focus score counts prose, and collapsed text counts as raw lines). The model said "Since I can't see the code of `FastAPI.__init__` …" and sent `DELETE` over line ranges 5 times **to see them**; one (APIRouter.__init__ 691-929) was saved only by the tests |
| fastapi_14448 | empty patch | — | **No way to read lines**: the model wanted the imports of `fastapi/routing.py` (where `is_async_callable` comes from) and sent `['fastapi/routing.py', 1, 50, 'DELETE']` 4 times; `fastapi/routing.py::fastapi/routing.py top level` was refused. Its one real edit was undone with **an empty failure excerpt** (only test names), so it concluded "I didn't actually apply the change" |
| rich_3469 | `test_table_with_empty_cells` | edited `rich/align.py`; the fix is one condition in `rich/markdown.py` | **Statement without content**: "fix superfluous space / Fixes <url>". Nothing names Markdown; the description is in the linked issue, which is not available |
| rich_3521 | 4 of 8 new `test_split_cells_mixed` cases | right function, a different bug | **Statement without content**: "Segment.split_cells fix / Fixes <url>". The model guessed the wide-character branch; the bug is the first position estimate overshooting the cut. The hidden test is the existing `test_split_cells_mixed` with more strings and positions 0..n |

Faults of the procedure (all systematic, not of one task):
1. **Step 1 keeps only what the model copies.** A one-line copy (the title) leaves the requirements, the windows and the
   coverage map without the statement's content. The model copied only the title in 14986 and 14851.
2. **There is no form to read lines**, and "questions are not answered" in the edit step. The model needs the imports,
   the hidden part of a long function or a library definition, and it uses `DELETE` over line ranges as a viewer
   (9 calls in 14851 and 14448). A deletion of 240 lines was applied and only the tests undid it.
3. **A long function's window counts collapsed text as code lines and scores prose.** Doc texts win the focus window;
   the call that holds the statement's words (`lifespan=lifespan`) stays hidden.
4. **The test check is blind to removed behaviour.** Test selection uses the new lines and the place's name; a
   deletion has no new lines, so the root_path tests were never selected. And 290 of fastapi's 468 test files assert
   `== snapshot(...)` from `inline_snapshot`, which the sandbox lacks: our stub makes every such assertion true, so most
   fastapi regressions pass as "Kept and checked".
5. **The failure excerpt is empty for long test ids.** pytest writes `_ <long id> _` with one underscore when the id is
   wider than the line; the excerpt's pattern needs three.

Also found: the hidden test of fastapi_14583 (`tests/test_compat_params_v1.py`) imports `inline_snapshot`, so it fails
at collection in the eval sandbox whatever the patch (the earlier note "a module is never created" was wrong).

Proposed fixes (to be checked against `docs/llm_checklist.md` before they are made):
1. Step 1: ask for the whole statement (first 40 lines if longer); when the copy is one line, the answer says that only
   the title was read and asks for the rest once, with the exact form.
2. A read form in every step: `['<file>', '<first line>', '<last line>']` (no new lines) shows those lines, read-only;
   a library name shows its definition from site-packages. A `DELETE` over more than 30 lines is shown first and applied
   only when sent again.
3. Window: count displayed lines (a collapsed text is one line) and give prose lines no score.
4. Tests: select also by the removed lines' names; the `inline_snapshot` stub returns its argument (`snapshot(x) == x`).
5. Failure excerpt: match headers with any number of underscores.
6. Statements with no content (a title and a link): no fix in the procedure; the existing test closest to the place is
   the only evidence and is already shown.

### The near-miss fixes (2026-10-10) and their check against `docs/llm_checklist.md`

Made (each verified by a dry run on the task where the fault was seen):
1. **Step 1, the whole statement**: the prompt and `SKILL.md` ask for every line of it (the first 40 if longer; "the
   title alone is not enough"). A one-line copy is still accepted, and the answer says once that only one line was
   read and how to send the rest; a long text at the choose step after that rebuilds the requirements and candidates
   (fastapi_14986: 1 requirement from the title → 3, with "do not store `root_path` in servers" and "Escape Swagger UI
   configs").
2. **A read form in every step**: `['<file or name>', '<first line>', '<last line>']` shows up to 80 lines, read
   only, and names the functions or classes that hold them; at the choose step a range with a 4th item (the model's
   `['fastapi/routing.py', 1, 50, 'DELETE']`) is a read too, and so is a file path with a range in the edit step. A
   name the repository imports from a library shows the library's definition, read only, found through the import
   that binds it in the open place's file first (`is_async_callable` → `starlette._utils`, the implementation after
   its `@overload` stubs; `routing.Router` → `starlette.routing.Router`). `file::file top level` names the file's
   top-level code. A `DELETE` of more than 30 lines is shown first and made when the same call comes again (the
   confirmation is not counted as a repeat).
3. **Window of a long place**: sizes count shown lines (a collapsed text is one line) and text lines score nothing
   (fastapi_14851: `FastAPI.__init__`'s body, lines 857-999, is now shown whole with the `routing.APIRouter(...)`
   call).
4. **Tests**: the removed lines' names select tests too, and the `inline_snapshot` stand-in returns its argument
   (fastapi_14986: the root_path deletion is now undone, 3 tests failing with `Right contains 1 more item:
   {'servers': [{'url': '/api/v1'}]}`).
5. **Failure excerpt**: test headers with one underscore (long parametrized ids) are found, and `_ _ _` frame
   separators no longer end a test's section.

Check (closer / farther / neutral):

| Item | Verdict | Why |
|---|---|---|
| W1 | neutral | Choosing is unchanged; the library view adds the users of the name as candidates, as before |
| W2 | neutral | Edits unchanged |
| W3 | **closer** | The code to change is shown (long window), and the code the model asks for can be read (imports, library code) |
| W4 | neutral | NEXT unchanged; the large-deletion answer gives the exact repeat call |
| W5 | neutral | Reads use the same numbered format |
| W6 | **closer** | The requirements carry the statement's own words instead of the title |
| W7 | neutral | — |
| B1 | farther (small) | The read form takes two line numbers. Reason: the model already sent them (9 calls), and a wrong range costs nothing since a read changes nothing; ranges come from numbered code it has seen |
| B2 | **farther** | Step 1 asks for a longer copy. Reason: the requirements are read only from that copy (the sandbox has no copy of the statement), and a one-line copy left every window with the title alone (14986). Mitigated: any copy is still accepted, the rest is asked once, never refused |
| B3 | neutral | — |
| B4 | neutral | No new tool or string to spell |
| B5 | neutral | Reads quote nothing of the call |
| B6 | neutral | — |
| B7 | neutral | Answers stay under the cap; a read is at most 80 lines |
| B8 | **farther** | One more accepted form (read) in every step, and a confirmation for large deletions. Reason: B16; the form is the one the model wrote on its own, and it is described once in the prompt, the windows and the refusal text |
| B9 | farther (small) | A read lets the model postpone the decision. Reason: the reads it needs (imports, a hidden function body, library code) are bounded at 80 lines and do not replace the window; the edit budget still closes at 5.7 min |
| B10 | neutral | — |
| B11 | **closer** | "Kept and checked" now means the tests can fail: snapshot assertions compare values, and deletions select their tests |
| B12 | **closer** | "Questions are not answered" no longer contradicts the model's need to read: reading has its own form |
| B13 | neutral | — |
| B14 | neutral | — |
| B15 | neutral | — |
| B16 (new) | **closer** | A read form with no side effect in every step, and large deletions shown first |
| P1 | neutral | — |
| P2 | neutral | Layouts unchanged; one line added to the window's answer forms |
| P3 | neutral | — |
| P4 | neutral | No pressure words |
| P5 | **closer** | The prompt no longer says "its first paragraph" (which the model read as the title) next to "the requirements are read from it" |
| P6 | neutral | — |
| P7 | neutral | Library code is shown only when asked for |
| P8 | neutral | — |
| P9 | neutral | The read example in the prompt is a call form with placeholders |
| P10 | neutral | — |
| F1 | **closer** | Failures of parametrized tests show their marked line and `E` lines (14448 got only names) |
| F2 | **closer** | Same: expected vs actual instead of nothing |
| F3 | **closer** | Fewer false OKs (snapshot stand-in, deletions) |
| F4 | neutral | The deletion confirmation states a fact (the line count and the lines), it does not ask to reconsider |
| F5–F9 | neutral | — |

Not fixed: statements without content (rich_3469, rich_3521: a title and a link); the evidence is in the linked issue,
which the sandbox does not have.

### V14 on Kaggle (2026-10-10): what the near-miss fixes did

1/10 real (rich_3006); fastapi_14851 counts as resolved with an empty patch because its hidden tests pass on the
unchanged code in the eval sandbox (as in V3). Row in `VERSIONS.md`.

- **Worked**: the whole statement reached step 1 in 7 of 10 tasks (14986's three paragraphs became its requirements);
  the deletion check undid 14986's root_path DELETE with the selected tests; malformed calls stayed low (6%).
- **Did not work**: the one-line note fired in 3 tasks and the model never sent the rest (15800 sent 100 of 1,236
  characters).
- **Made things worse: the read form brought back browsing** (B9, the V4–V6 failure the D3 redesign removed). 51 reads
  in 10 tasks; 14986 read `fastapi/applications.py` in 16 slices and timed out with an empty patch; 14851 read 12
  slices and opened 14 names without an edit; 3 empty patches against 1 in V13b. The check table had marked B9 "farther
  (small)": the trace shows it is not small. The read form needs a bound that the model sees and the script enforces.

### Finding instead of reading (2026-10-10, after V14): what the 51 reads were for, and the fix

**What the reads were** (every V14 call read with the model's own words, `tools/trace_calls.py`): 50 range reads in
6 tasks (14986: 16, 14851: 12, rich_3521: 10, 15800: 6, 14448, httpx_3672, rich_3469: 2 each). They were not reading:
they were **a search done by hand**, and the model said so:
- fastapi_14986: "I need to find where `root_path_in_servers` is defined" → 16 slices of `fastapi/applications.py`,
  then "Found it! at line 663", then 2 more for its default; timed out with an empty patch.
- fastapi_14851: "I'll search for `super().__init__` … but I can't. I have to read" → 12 slices of `FastAPI.__init__`
  and `APIRouter.__init__`; also "where is `add_event_handler` defined" (Starlette's `Router`, a library base class).
- rich_3521: "I'll search for `test_split_cells` in tests/test_segment.py" → 8 slices of the test file.
- rich_3469 (a statement with no content): "Let me search for `pad(1)` in the whole repository. No, I can't."; 20 names
  opened one after the other.

**Why a cap is not the fix.** A cap on reads moves the browsing to names (rich_3469) and is arbitrary. The cause is
that a read was the only answer that decides nothing: it showed code, left the same decision open and cost nothing,
so it could be repeated without end (B9). And the need behind it, search, had no form.

**The design.** Code outside the window is reached by finding it, and its code is shown only once it is chosen:
1. **Search** (step.py `_search`, `_search_view`): a name the repository does not define, a text, or
   `['search', '<text>']` / `'search <text> in <file>'` gives the functions and classes whose lines hold it, each with
   its matching lines numbered (grep's answer, W5), the files of the current work first; the tests' matching lines as
   read-only lines; a library name also shows the library's definition. The answer is a candidate list: choosing is
   the next call (W1).
2. **A line range** (`_range_request`): inside the open place, or inside a listed place, it opens that place with those
   lines in view (the window is the place's code: W3); elsewhere it gives the functions and classes at those lines,
   to choose from; in a test file it shows the test functions that hold them, whole and read only, and the current
   step again (tests are evidence, never places).
3. **Naming code** means the same in every step (B12): a listed place opens; other code is chosen, and it and its
   related code replace the places not edited yet (`_choose_code`; the edits made stay, the dropped places are named).
   An edit that names code which is not a place chooses it and applies the edit (`_edit_of_other`; V14 sent 6 edits of
   code that was no longer a place).
4. **The window carries what the reads looked for**: the imports the place uses (not only "lines 1-43"), and the
   library code it calls, including a method that only a library base class defines (`_library_uses`; 14851's
   `self.add_event_handler` → `starlette.routing.Router.add_event_handler`, shown as code).
5. **Every look is logged** (`look` events in the state log: search, range, range in a place, test, name, file, edit of
   other code), so each trace shows which needs the window still misses (the permanent loop: a need found in a trace
   goes into the window, not into a new form).

No count limits anything: a search costs the places not edited yet, and the time to edits stays the only budget.
Accepted risk, to be measured: a range inside a long open place pages through it (the place under decision, W3).

Dry runs on fastapi_14986, fastapi_14851 and rich_3469, and a replay of all their V14 calls through the new script:
no script error; 14986's search for `root_path_in_servers` answers with lines 663, 678, 886 and 1110 in one call;
14851's `search super().__init__ in fastapi/routing.py` answers `APIRouter.__init__` line 906; `APIRouter.on_event`'s
window shows Starlette's `add_event_handler` code; rich_3469's `pad(1)` lists `Panel._title` and `Panel._subtitle`.

Check against `docs/llm_checklist.md`:

| Item | Verdict | Why |
|---|---|---|
| W1 | **closer** | Every look ends in a choice from a short list of names with the lines that make the right one recognizable |
| W2 | neutral | Edits unchanged |
| W3 | **closer** | The window shows the imports the place uses and the library code it calls; ranges in a place show those lines in it |
| W4 | **closer** | After a look the NEXT is always the choose call; after opening, the edit call |
| W5 | **closer** | Search answers are grep's numbered lines |
| W6 | **closer** | The model finds code with its own words (a parameter name, `super().__init__`, `pad(1)`) |
| W7 | neutral | — |
| B1 | **closer** | Line numbers are no longer needed to see code (a range still works, never required) |
| B2 | neutral | — |
| B3 | neutral | — |
| B4 | neutral | `search` is a word the model already wrote; no new tool or argument |
| B5 | neutral | No answer quotes the call; a search answer names the text searched, as a fact |
| B6 | neutral | — |
| B7 | **farther (small)** | Windows grow by the used imports and library entries. Reason: they replace reads of 80 lines each; library entries are one line unless the definition is 15 lines or fewer, at most 4 |
| B8 | neutral | The read form is gone, the search form is new; the edit step accepts the same kinds of answer (edit, skip, back, a name) |
| B9 | **closer** | No answer shows code that no decision asked for; every look replaces the plan left or opens a place |
| B10 | neutral | — |
| B11 | **closer** | The dropped places are named in the answer; nothing is lost silently |
| B12 | **closer** | A name means "choose this code" in every step; a range always means "the code that holds these lines" |
| B13 | **closer** | Search results put the files of the current work first |
| B14 | neutral | — |
| B15 | neutral | — |
| B16 | **closer** | An edit of code that is not a place is applied to it (chosen first); a range never edits |
| P1 | neutral | Library code and tests are labelled read only |
| P2 | **farther** | The window gains blocks and the look answers change. Reason: the reads had no other cure; measured in one run against V14 |
| P3 | neutral | — |
| P4 | neutral | — |
| P5 | **closer** | "Questions are not answered in this step" next to a read form is gone; the prompt, SKILL.md, windows and refusals give the same forms |
| P6 | neutral | — |
| P7 | **farther (small)** | Library entries may be noise. Reason: only code the place calls, not the signature's annotations nor the standard library |
| P8 | **closer** | The answer says which places are no longer planned |
| P9 | neutral | — |
| P10 | neutral | — |
| F1–F9 | neutral | Edit feedback unchanged |

### V15 on Kaggle (2026-10-10): what finding instead of reading did

1/10 real (rich_3006). fastapi_14851 counts as resolved with a 663-character patch (APIRouter keeps `on_startup` and
`on_shutdown` itself instead of passing them to Starlette's Router), but its hidden tests passed with an empty patch in
V14, so it is not evidence. Row in `VERSIONS.md`. Per-answer counts: `tools/trace_looks.py`.

- **Worked**: no range read in any task (V14: 50); 9 of 10 patches non-empty (V14: 7); 5 searches, each answered
  with a list; malformed calls 7%.
- **Did not work: the browsing moved to names**, the risk the checklist table marked under B9 ("closer (partial)").
  27 choices by name; fastapi_14851 chose code 15 times in a row (APIRouter.__init__ 3 times, APIRouter.on_event 4,
  `_merge_lifespan_context` 3) and made its one kept edit at 4.5 min; rich_3469 (a statement with no content) 7. A
  choice replaced the places not edited yet, but that cost nothing the model valued, so it was not a decision: the
  same code was chosen again a few calls later, which also says the windows are lost to compaction or not read.
- **Script faults found and fixed**: (1) an edit naming `fastapi/_compat/v1.py top level` was applied to the place
  `fastapi/_compat/may_v1.py top level` through a near-spelling match: near spellings are now tried only for text that
  is no path and names no code of its own; (2) a search for `" "` lost its quotes, became empty and matched 39,000
  lines: a search keeps the model's text when cleaning leaves fewer than 2 letters, and an empty search is refused;
  (3) `lifespan` chose an example in `docs_src`: a bare name defined only in docs is found, not chosen; (4)
  `APIRouter.add_event_handler`, inherited from Starlette's Router, was refused: it is now shown as library code,
  read only, with the current step again.
- **The run itself took 68 minutes**; Kaggle kept the kernel in "running" for about 8.8 hours before and after it.

Open: re-choosing code already seen. The next step is to find out from the traces why the model chooses the same
code again (the window compacted away, or a window that does not answer what it looks for) before any change.

### Local run L1 (2026-10-11): rich_3006 with the 26B A4B, monitored call by call

`tools/local_eval.py` (new): the competition harness (swegemma 0.2.11, subprocess sandbox, the same compaction and
budgets as `tools/kaggle_eval.py`) with the model served by the gateway (`gemma-26b`, reasoning budget 512, MTP on).

Result: **resolved** (the right edit, `==` → `is`, at 28 s; submit_patch at 56 s), but the agent went on for 27 more
calls until the 30-call budget. Every call was read; the faults of the system:
1. **The model did not know its edit was made.** It opened the place again after submitting, saw `is` in the code and
   wrote "I haven't actually applied the fix yet" and "the code already has `is` … maybe the issue was already fixed";
   20 calls of doubt and one more edit followed. The window showed the code as it is now, with nothing saying what the
   patch changed there (the progress line says only "+1 -1"). → A place the patch changes shows that change, as git
   diff lines, before its numbered code (`_patch_here`).
2. **It wanted to see or add tests** ("Also added the sample code as a test" in the statement): `tests/` and
   `tests/test_repr.py` were answered "Nothing left to do in this step", 6 calls. → A test directory or test file
   without line numbers is answered with the facts: test files are not changed (the hidden tests replace them), every
   edit was checked with the existing tests that use the changed code, and a test file with two line numbers shows a
   test (`TESTS_NOTE`).
3. **A search inside a file**, `['rich/repr.py', 'param.empty']` (6 calls), was taken as the file name alone. → `[file,
   text]` is the text searched in that file.
4. It called step.py after submit_patch (the harness does not end the task there; only the last submission counts).
   Mostly a consequence of 1; not changed.

Check against `docs/llm_checklist.md`:

| Item | Verdict | Why |
|---|---|---|
| W3 | **closer** | The window shows what the patch already changed in the place |
| W5 | **closer** | The change is shown as git diff lines |
| W6 | **closer** | `[file, text]` is read as the model meant it |
| B4 | **closer** | One more natural spelling of a search is understood instead of refused |
| B7 | farther (small) | A changed place's window grows by its diff lines. Reason: without them the model edited again |
| B9 | **closer** | 'tests/' gets a fact and the current step, not a dead end that invites more tries |
| B11 | **closer** | What is done is stated from git, not left to the model's memory |
| B12 | **closer** | "Nothing left to do" no longer answers a request the model can rephrase forever |
| F5 | **closer** | Knowing what is done: the place's own diff |
| F9 | neutral | The tests note is facts plus one form |
| others | neutral | No new form; the edit and choose forms are unchanged |

L2 (same task, after the fixes above): resolved, submit_patch after 11 calls (L1: 30); after reopening the place the
model wrote "the change is already in the buffer" (the diff note worked). It still went on after submitting, set on
adding a test ("Also added the sample code as a test" in the statement), and searches for text that nothing holds
(`inspect.Parameter.empty`) were answered "Not run: this step edits the open place" or "not a name from the candidate
list", which read as wrong forms. Fixes: the tests note states the decisive fact (the harness removes every change to
a test file from the patch, so a test written here never counts); a name or text nothing holds is answered "Nothing in
the repository holds X (library and standard-library code is not searched)", in every step; several texts at the
choose step are searched in turn. Checklist: F9 closer (a fact instead of advice), B12 closer (no answer that reads as
a wrong form), B5 farther (small): the answer names the text searched; reason: without it the answer does not say what
was not found, and it is a fact about the repository, not a quote of a failed form.

L3 (same task): the tests note never reached the model at the finish step: `d4` passed a test path to `d3` and then
overwrote its answer with "Nothing left to do in this step"; the model asked for `tests`, `tests/test_repr.py`,
`test_repr`, `tests/test_pretty.py` and looped on the last until the budget. And the cause of the test-writing was the
script's own requirement: the copied statement's sentence "Also added the sample code as a test." (what the PR author
did) was a requirement shown in every window. Fixes: `d4` keeps `d3`'s answer when nothing is opened; the statement
cleaning drops sentences where the author says they added or wrote tests (in the 129 dev statements this removes only
such remarks: "I added a test case which reproduced …", "Adds some additional tests …"; a sentence that adds behaviour
and also updates tests, fastapi_14479, is kept), and "with the suggested change". Checklist: B12 closer (no
requirement asks for what can never count), P7 closer (no window repeats a remark that bears on no decision), F9
neutral.

L4 (same task): **resolved in 48 s, 4 calls reached the script, and the agent stopped after submit_patch** (L1: 204 s,
30 calls, budget-stopped). The model read the tests note and submitted. Left: the requirement still held "Also added
the sample code as a test." (5 words between "added" and "test"; the filter allowed 4): now up to 6, which in the 129
dev statements still removes only the authors' remarks about tests. Also seen: 2 calls whose args mixed JSON and the
answer forms' single quotes (`"auto.do_replace.auto_rich_repr', '        def ..."`) and were rejected by the ADK
before reaching the script ("Argument 'skill_name' is required"): a B4/B15 cost of the single-quoted forms, to watch.

L5 (same task): the requirement is clean now; resolved, submit_patch at 45 s. Two more needs seen in every local run:
(1) **args packed with the forms' single quotes** (`"Name', '<code>"`, one string): split into the intended items
(`_args.split_single_quoted`; a line edit `"Name', '79', '79', '<lines>"` too); (2) **code to run** (a reproduction
starting with imports, `python -c …`, `pytest …`, `ls`): answered with the facts (no shell or Python command here; the
existing tests that use the changed code ran after the edit; the hidden tests with the issue's example run after
submit_patch) and the current step, instead of an echo of the code ("Nothing in the repository holds import inspect
…") or "Nothing left to do". Checklist: B4 closer (a natural mis-quoting is repaired), B5 closer (no failed input is
quoted back), B12 closer, F9 neutral (facts), W3 neutral. Open, for the design: the model wants to run the issue's
example after an edit (all five runs); the script could run the statement's example itself after an edit and report
it (F2: expected vs actual) — to be weighed against the D3 redesign's removal of `python -c` (it was used to explore).

### Windows that fit the history (2026-10-11): the compaction was erasing every window

Local batch, task 1 (requests_7328, the 26B): the model opened `SessionRedirectMixin.resolve_redirects` 8 times in a
row and never edited it. The gateway log shows why: prompts of 12,074 → 15,995 tokens, then a compaction summary, then
10,777 → 14,003 → 17,862 and another summary, every 2-3 calls. A window was about 10,000 characters (3,500 tokens: the
code 5,785 characters, imports 876, used code 1,026, the task 526, a test 872, the forms 1,126); the harness keeps the
last 5 events after compacting, so the kept history alone was near the 14,336-token threshold and each new window
triggered the next compaction, which removed it. The 31B in V15 was in the same state: **mean prompt 10-11k tokens
and maximum 15.5-17k in 9 of 10 tasks; the one resolved task (rich_3006) peaked at 8k and never compacted.** The
"browsing by names" of V15 (14851 chose the same code 3-4 times) is the same effect: the window it had chosen was gone.

Fix: every window fits about 6,000 characters (resolve_redirects: 10,211 → 6,061): the code of a long place is 45
lines around what the requirements name (a place up to 75 lines whole; a range inside the place shows other lines,
`_range_request`), the imports it uses are one line of names and modules, 4 used definitions in 900 characters (2 of
them library code), 2 callers, the statement's example and the existing test 8 lines each, the answer forms on one
line; the answer cap is 7,000 characters.

| Item | Verdict | Why |
|---|---|---|
| B7 | **closer** | The history stays under the compaction threshold, so the window the model is deciding on stays in its context |
| P7 | **closer** | Less of each window is context that bears on no decision (30 import lines, 8 definitions) |
| P8 / F5 | **closer** | Compaction summaries, which lose the window and the state, happen less often |
| W3 | **farther** | Less of a long place and of its context is shown at once. Reason: a window that the compaction removes shows nothing; the rest of the place is one range away |
| B9 | farther (small) | Ranges inside a long place may be used more. Reason as W3; they stay inside the place under decision |
| W4 | neutral | The forms are the same, on one line; NEXT unchanged |
| others | neutral | — |

Second cut (same day, after the first rerun of requests_7328 still compacted every 3-4 calls: each window added about
2,200 tokens, of which about 550 are the model's own kept thoughts; `include_thoughts: false` would turn thinking off
in the ADK bridge, so the thoughts stay): the window is about 5,000 characters (resolve_redirects 10,211 → 5,015): the
code whole up to 60 lines, else 40 around the requirements' names; used code and callers one line each with
location and a short signature (short own members up to 6 lines as code); imported names in one line; the
statement's behaviour sentences cut at 350 characters and its example dropped (the statement is in the system
instruction, never compacted); the existing test 6 lines; the forms line says only what NEXT does not. Checklist as
above (B7, P7 closer; W3 farther, same reason); W4 neutral: NEXT still gives the edit forms in full.

### The decision window (2026-10-11): a critical look at what each window costs

Measured with the model's own tokenizer (llama-server `/tokenize`), as the ADK sends it (the answer JSON-encoded):
the 5,000-character window of `resolve_redirects` was **1,500 tokens**: progress, head and code 806 (the line numbers
alone 187 of the code's 596: a padded `" 184|"` is about 5 tokens), used code 142, callers 82, imports 66, the task
91, a test 126, the forms line 79 and NEXT 78. A turn also keeps the model's own thoughts (about 512 tokens with
`include_thoughts: true`, which must stay: false turns thinking off). With 3,200 tokens of system instruction and task
message, about 1,000 of summary and the last 5 events kept after each compaction, a turn of about 2,100 tokens meant a
compaction every 3-4 calls.

What a window is for: one decision, what to write at this place. Three kinds of content were mixed in it: decision
material (the code, the goal), navigation affordances (used code, callers, imports, forms) and status (progress, the
patch's change). The principle now: **what does not change goes in the system instruction, which is never compacted;
each answer carries only what this decision needs, measured in tokens.**
- Head: the place, and **one goal line**: the requirement that shares the most words with the place (the issue in
  full is in the system instruction, above every compaction).
- Code: whole up to 52 lines, else 32 around the requirements' names (a range inside the place shows the rest);
  numbers without padding (`184|`), which saves about 40 tokens per window.
- Related code: up to 3 used definitions **that share the issue's words** (none that share none) and 1 caller, one
  line each.
- A test that uses it: only for a chosen place and only the first time it is shown.
- Dropped from every window: the requirement list and the statement's sentences (system instruction), the imports
  line (adding an import is `<file> top level`, said once in the system instruction), the forms line (NEXT and the
  system instruction), the "added because it is related" sentence (now part of the head). The budget line is shorter.

Result: `resolve_redirects` **884 tokens** (from about 3,500 this morning), the choose step's answer with the places
list and the first window 1,189.

| Item | Verdict | Why |
|---|---|---|
| B7 | **closer** | Turns of about 1,500 tokens instead of 4,000: the window stays in the history |
| P6 | neutral | The goal is the second line; NEXT is the last; the full task is in the system instruction |
| P7 | **closer** | Only context that shares the issue's words is shown |
| P8 / F5 | **closer** | Fewer compactions, so less state is lost |
| W1 | neutral | — |
| W3 | **farther** | Less code and context at once. Reason: a window that is compacted away shows nothing; the rest is one in-place range away |
| W4 | neutral | NEXT unchanged |
| W5 | neutral | `184|code` is still the numbered format |
| W6 | neutral | The goal is the issue's own sentence |
| B1 | neutral | Line numbers still shown on every code line |
| B8 | neutral | No form added or removed |
| B12 | **closer** | The prompt's description of the window matches the window |
| others | neutral | — |

First run with the decision window (requests_7328): prompts grew 1,400-1,500 tokens per call instead of 2,200, but the
model read `Session.send` (76 lines, 32 shown) in 6 ranges, and each range answer repeated the whole window. A range
inside the open place now answers only those lines (at most 60, inside the place) and NEXT (`_place_lines`).
Checklist: B7 closer (no repeated window), W3 closer (the lines asked for, at once), B9 neutral (the reads stay inside
the place under decision; still counted in the `look` log).

Same run: the repeat guard took a range read three times (`['Session.send', 732, 737]`) as a loop and moved on,
which marks the open place skipped; Session.send and then resolve_redirects were skipped undecided and the plan went
back to choosing. A range read is now never a repeat (its answer is short, and the lines may have been compacted
away). Checklist: F7 neutral (loops of edits and choices are still moved on), B12 closer (a read never changes the
plan).

Same run, end: the model sent the same failing edit of Session.send 5 times, alternating with re-choosing it, so the
repeat guard (last 3 calls) never saw a repeat; each try ran the tests again (about 20 s) and was undone by the same 3
failures. An edit the tests undid is now remembered (place, the tests it broke); the same edit sent again is answered
at once with those facts and the current step, without running it (F6 closer: no third try of the same thing; F9
neutral: facts; B5 farther (small): the answer names the failing tests again; reason: they are the fact that makes a
different change necessary). The run ended with an empty patch: the model localized the change in Session.send while
the fix is in resolve_redirects (the place it had lost to the repeat-guard skip, fixed above).

Question raised by the user: would ADK sub-agents avoid the compaction? Recorded options: `agent_tool` (a sub-agent in
a fresh session; only its final text reaches the parent; its calls count against the 30-call budget) as an editor per
place would start every window with a clean context, and the procedure's state already lives in step.py's journal, not
in the history; a `SequentialAgent` does not help (each stage accumulates its own history; V5 used one and failed for
browsing, not for the architecture). Decision: first measure the decision window; if tasks still compact, try an
`agent_tool` editor per place locally.

Budget (2026-10-11, the user's question): `max_tool_calls` 30 and `max_turns` 40 were ours (a safety net,
design v1); the harness default is 100 calls and the competition's real limit is time (12 h for about 120 tasks, about
6 min each). With a faster model the call cap ended runs before the time did (L1: 30 calls at 3.2 min). Now 60 calls
and 80 turns, 7 min unchanged, so time decides. (Also checked: the competition's compaction uses
`compaction_interval 5`, ours 15; it counts user invocations, one per task, so the token threshold of 14,336 is the
trigger in both.)

Then (the user's point): a call cap only ends a run before its time; time alone is the competition's limit. Now
`max_tool_calls` 200 and `max_turns` 300 (not binding), and the budget line shows only the time (B10: the binding
budget; a call count against a cap that never binds is noise). Also: `<file> top level` (how the system instruction
says to add an import) was answered "Nothing in the repository holds …" in the edit step (the name pattern refused the
space), and choosing it replaced the plan: the top level of a file the plan works in now joins the plan.

fastapi_14448 (local batch, task 4): at the finish step the model sent args `['submit_patch']` to the script 20 times:
the D4 NEXT read "NEXT: none: call submit_patch now" while every other NEXT reads "call run_skill_script … args", and
the script answered each repeat with "call submit_patch", which the model took as what it was doing. The D4 and
time-up NEXT lines now say "call the tool submit_patch, with no arguments (it is its own tool, not an argument of
run_skill_script)", and args naming submit_patch are answered with that fact (never counted as a repeat). Checklist:
W4 closer (an unambiguous next call), B12 closer, F7 closer (the loop ends with the right call). Also: code in a file
the plan works in joins the plan instead of replacing it (the model chose four `Dependant` members in turn, each
dropping the others), and the budget line is gone from the answers (the user's decision: the script closes edits at
5.7 min and the harness keeps the diff, so the model does not manage time; B10 is now met by the script, not by the
model). `tools/local_eval.py` can dump every request as sent (`SWE_DUMP_REQUESTS`): the fixed part of each request is
about 4,075 tokens (our prompt ~1,500, ADK's skill text ~450, the task message ~1,470 with a 700-token directory tree,
tool definitions 646), 28% of the compaction threshold.

### The agent_tool variant (2026-10-11): a coordinator and an editor per place

`variants/agent_tool/` (built with `tools/build.py <dir> --variant agent_tool`, copied over `agent/`): the root agent
`issue_fixer` (prompt `prompts/coordinator.md`, step.py in the `coordinator` role from `_role.py`) starts, chooses and
then calls the tool `edit_place` (an ADK AgentTool, `sub_agents/edit_place.yaml`) once per place, and `['status']`
after each; `edit_place` runs in a fresh session (ADK: a new Runner and session; the parent's state, including
`problem_description`, is copied in, so its instruction carries the issue) and works through `edit.py` (step.py in
the `editor` role): it opens its place, edits, skips or reads lines of it, and when the place is decided the script
tells it to reply with one sentence and make no more calls. Only that sentence enters the coordinator's history, so
neither history comes near the compaction threshold. Read in ADK's `tools/agent_tool.py`: the child has no
compaction (a looping editor could reach the 32k context), its tool calls count against the same budget, plugins are
inherited.

First runs (26B, local):
- rich_3006: resolved in 87 s; the editor started from 1,568 prompt tokens and made the edit in one call.
- requests_7328, read call by call: (1) one editor call hit the 4,096-token output limit while thinking (the rare
  local budget fault) and its half reasoning became its reply; (2) the coordinator did not understand that the
  editor decides the change ("how does it know what to edit it to? maybe I should provide the edit in the
  request?"): its prompt now says that the editor, which has the issue too, decides and makes the change, and the
  coordinator only names the place; (3) the second editor read the same 15 lines of Session.send 5 times, and its
  reasoning advanced at every call: **with a 512-token thinking budget the model uses repeated reads to go on
  thinking**; on the fourth read it found the self-reference (no redirect: `r0.history = [r0]`). In the variant the
  editor's thoughts never reach the coordinator, so the editor can think more without filling any history: its
  `thinking_budget` is now 1,024 (the coordinator keeps 512). Re-reads are not answered "decide now" in the editor.

Checklist: B7 closer (no history reaches the threshold), W7 closer (room to think where the decision is made), P7
closer (each editor sees its place only), B4 and B8 farther (one more tool and one more script name; reason: the only
way to give a clean context from a declarative config), W4 neutral (both roles end every answer with their own NEXT).

requests_7328 with the variant, end: the editor (after 14 thinking re-reads) kept an edit of Session.send, went on by
itself to resolve_redirects and removed `resp.history = hist[1:]` (the reference replaces it with
`resp.history = hist[:]` before the append; removing it breaks `len(resp.history) >= max_redirects`): not resolved,
but at the right place. The coordinator then wrote a `python3 -c "…"` call that ran to the 4,096-token output limit;
the cut JSON made the harness end the task ("Sandbox execution error: Expecting value") with the patch kept. The
tools each agent can call are fixed by its definition (no shell in either); the `python3 -c` text was args to
run_skill_script, which the script answers with the facts (RUN_NOTE), but the reply was cut before it arrived. So no
prompt sentence about it (the user's point: tool limits belong to the agent definition, not the prompt).

### Run a04 (2026-10-11): requests_7328 with the agent_tool variant, read text by text

Commit 02581ca, local 26B A4B, reasoning budget 1,024. Result: not resolved, 41 tool calls, 50 model calls, 430 s;
the patch only deletes `hist.append(resp)` and `resp.history = hist[1:]` (the reference keeps the append and assigns
`resp.history = hist[:]` before it). All 50 requests were read in full from `/_monitor/conversation`.

**What the model did well (keep it):**
- Coordinator S0 → D1 in 2 calls: it copied the statement, chose `SessionRedirectMixin.resolve_redirects` from the 10
  names, with the evidence lines `184: resp.history = hist[1:]` (W1, W6).
- Editor, first window (1,458 → 2,665 tokens of prompt): it read the numbered code and traced the loop by hand
  (R1, R2, R3 and `hist[1:]`), the right lines (W3, W5).
- After seeing its own diff it found its error at once: "Wait, I just deleted `hist.append(resp)` … `hist` will
  always be empty" and sent `hist.append(resp); resp.history = hist[:-1]`, which is equivalent to the reference fix
  (F1: the -/+ diff shows where the error is).
- With a failing test it read the names and changed the edit (F2).
- Coordinator replies were short and followed NEXT when NEXT was a single call (W4).

**What went wrong, and whose fault it is:**

| # | Text the model saw / wrote | Cause | Checklist |
|---|---|---|---|
| 1 | Editor thinking cut mid-trace ("So `R3.history` is `[R2`") and then the call `183,184,DELETE` | the thinking budget ended in the middle of the simulation; the call came from the half-done reasoning | W7, P5 |
| 2 | "Your work on SessionRedirectMixin.resolve_redirects is finished. Reply now" right after the first accepted edit; the correct fix sent next was refused 5 times with the same text | **system**: the editor closes on the first accepted edit; it cannot correct what it sees in its own diff | F1, F6, B12 |
| 3 | "Your work on  is finished … what changed at ," (empty name) | **system bug**: the name of the editor's place is empty after the edit | B12 |
| 4 | Coordinator: "I need to see the code … I'll try `src/requests/sessions.py:160-281`" (2 wasted calls) | the coordinator prompt still describes the "look elsewhere" forms of the single agent; it wants to understand before delegating | B9, B12 |
| 5 | Session.send editor sent `SessionRedirectMixin.resolve_redirects` and the script reopened that other place | **system**: `_editor_scope` treats any one-item place name as "a new editor opens its place" | B12, P8 |
| 6 | `['Session.send','722,735']` answered "The edit was not read: it has 2 items" (5 times) | **system**: a range in one item with a comma is not read as a range; and the open place was the wrong one (#5) | B4, B1 |
| 7 | "The same call came three times, so SessionRedirectMixin.resolve_redirects keeps its code" while "Open now: Session.send" | **system**: contradictory state lines | B12 |
| 8 | Thinking degenerated into "I'll try to use `Session.send` with the first line number and the last line number." repeated tens of times, then `finish_reason=length` at 4,096 tokens | loop after 10 identical answers that contradicted its request | B5, F7, B14 |
| 9 | Second Session.send editor: a correct-looking edit was undone by 4 tests, then "Time for edits is over" at 340 s | the time had gone in #2–#8 | B10 (not shown to the model, by design) |
| 10 | Coordinator summary "The issue was fixed by moving the fix…" accepted; then submit_patch | it believes the editor's sentence (the status shows the real diff, but it did not doubt it) | B11 |

**Conclusions.** The model's reasoning about the code was right twice (it found the bug and the equivalent fix); the
task was lost by the interface: an editor that closes before the model can correct its edit (#2), the wrong place
reopened (#5), a range form not read (#6) and contradictory status lines (#3, #7). Those produced the loop (#8) and ate
the time (#9). Fixes to make: keep the place open after an accepted edit, with the diff, until the model replies or
skips; a one-item name of another place in the editor role gets "Only X is edited here"; read `a,b` / `a-b` in one
item as a range; never print an empty name; the coordinator prompt without the choose-step forms.

**Fixes after a04 (2026-10-11)** and their check against `docs/llm_checklist.md`:
1. Editor role: an accepted edit keeps the place open, with its diff and the code as it is now; the editor replies
   when the change is complete or sends a corrected edit. The coordinator's next call moves the work on.
2. Editor role: another place's name, while the editor's own place is undecided, answers "Only X is edited here"
   with the own window; it no longer reopens the other place.
3. A range in one item (`722,735`, `722-735`, `722:735`, `722..735`, `722 to 735`) is read as two line numbers.
4. The editor's first call (often the coordinator's last call word for word) is never answered as a repeat, so the
   editor always gets its place; an empty place name prints "this place".
5. Coordinator prompt: it does not see or write the places' code; the find forms are only for choosing.
6. (The user's) **No time limit inside the script**: `EDIT_STOP` (340 s) and the "Time for edits is over" answer were
   removed; in a04 it refused the second editor's edits at 350 s. The harness's own 7 min budget stays.

| Item | Effect | Why |
|---|---|---|
| F1 | closer | the diff shows where the error is and the model may act on it (a04: it found its own deletion) |
| F6 | neutral | a correction is a new edit; the failed-edit memory and the three-repeat move-on still apply |
| B12 | closer | no "finished" next to "send an edit", no other place opened, no empty names |
| B4, B1 | closer | a common range spelling is accepted instead of refused |
| W4 | farther | NEXT after an accepted edit offers two outcomes (reply or correct): the reason is F1, the correction is the case a04 lost |
| B9 | farther (small) | the editor can keep editing an accepted place; bounded by the repeat guard and the harness's time |
| B10 | neutral | the model never saw the time; the harness still ends the run at 7 min |
| P5 | closer | the coordinator prompt no longer offers find forms in the edit step |

## Redesign: microtasks with the context each role needs (2026-10-12, the user's)

**Why.**
- In V18-V19 the editors spent 88-95% of each task's time and 60-75% of their calls reading.
- 289 reads in three tasks went to the other planned places, the callers, the code the place uses, and ranges of long
  places (`docs/feedback_audit.md`, "What the editor reads").
- That reading was understanding the fix, not writing it. The only resolved task (rich_3521, V18) had a change that
  fit in one function: 3 reads, 6 minutes.
- The user's rule: if the files a fix needs do not fit, the task is not specific enough; the tasks must be smaller,
  with less context.

**Roles and their context.**

| Role | Agent | Gets | Does not get | Size |
|---|---|---|---|---|
| Find | coordinator (`step.py`) | the statement; names, signatures and matching lines | code bodies | ~2k tokens |
| Choose | coordinator | requirements, candidates with evidence | code bodies | ~2k |
| **Specify** | **`specify_fix`** (new agent_tool, fresh context, thinking 4096), `spec.py` | the statement (system instruction); the numbered code of every planned place, with the lines that call it and the signatures it uses, all at once | editing, tests, search history | ~3-8k |
| Edit one microtask | `edit_place`, `open.py`/`edit.py` | its place's code (the part the specification names, at most ~90 lines), the specification sentence, the requirement lines, the signatures it uses | other places' code, reads of any kind, the whole statement's reasoning | ~1-2.5k |
| Review | script | the diff and the failing test with its line and error | — | ~0.5-1k |
| Propagate | script | after a rename or removal: each use of the old name, with the function that holds it | — | ~0.3k per use |
| Finish | coordinator | the requirement coverage map | — | ~1k |

**Rules.**
- **The specifier sees the whole plan at once** and writes one sentence per place: what changes there, or
  'no change'.
  - It can add code the plan lacks by naming it; the script adds it as a place and shows its code.
  - A place it marks 'no change' is not handed to an editor.
- **The editor receives one specified change.** It cannot read other code.
  - A name, file, range or search gets one fixed answer: the change is specified; edit with what is shown, or reply
    with one sentence naming the code that is missing.
  - The one exception is a range inside its own place when the place is longer than the window.
  - Edits of other code by name stay accepted (they join the plan).
- **The coordinator no longer explains the fix.** Its steps are: start, choose, specify (`specify_fix`), edit each
  specified place (`edit_place`), finish.

**Checklist.**

| Item | Effect | Why |
|---|---|---|
| W1 choosing from a short list | neutral | find and choose unchanged |
| W2 whole functions | neutral | edit forms unchanged |
| W3 code in front of it | closer | the specifier gets all the plan's code at once; each editor gets the code of its change |
| W4 a fully filled next call | neutral | every answer still ends with NEXT |
| W7 better calls after thinking | closer | the step that needs understanding gets a 4096 thinking budget |
| B7 middle of a long context | closer | editor windows shrink to ~1-2.5k tokens; the specifier's view is one answer of ≤ 8k |
| B8 many forms | closer | the editor loses every read form |
| B9 reading instead of editing | closer | no reading in the editor; the reading is one prepared view for the specifier |
| P3 open-ended prohibitions | farther | "no reads" is a prohibition; stated with its reason (the change is specified) and a way out (reply naming the missing code) |
| new risk: free text | farther | the specification is free text, the step that failed with the local 12B (4 of 5 runs, 10-09); accepted because the 31B now writes one sentence per listed place, with all the code in view, in a fresh context with a 4096 thinking budget; measured in the next Kaggle eval |

**First local runs of the microtasks (2026-10-13, fastapi_14448, local 26B with thinking 512), m1-m10.**
- System faults found and fixed, one per run:
  - before the specification, the coordinator's searches, files and multiple names were refused with the old "its
    editor reads it" text; they are now answered as at the choose step;
  - a repeated coordinator call showed it an editor's window, and the repeat move-on skipped an unspecified place;
  - code the coordinator named after the specification replaced the plan; it now joins it, keeping the sentences;
  - the specifier's parsing: a name alone, names copied from the view, annotated names, file paths (read as the class
    FastAPI), one-word sentences;
  - an all-"no change" specification sent the plan back to choosing; it is not accepted now;
  - an editor whose edit was kept and reached for other places now gets the reply to send;
  - a new editor opened without a status call is told apart when the previous place is done;
  - a third 'the plan' gets a reminder, not the whole view.
- m10, the first run with no system fault, took 5.7 minutes:
  - the specifier wrote the code of the four `Dependant` methods;
  - the editors applied it;
  - the patch passes 24 of 28 hidden tests, the same as V18's 31B run, which took 45 minutes.
- The four failures are cases no sentence covered: class instances with a wrapped `__call__`, and a generator with an
  async wrapper.

### Local runs p1-p2 (2026-10-13, gemma-26b, agent_tool variant): requests_7502, drawn at random

The task: "Fix `_encode_files` detection for `__getattr__`-based file wrappers" (one line); the reference replaces
`isinstance(fp, _SupportsRead)` with `... or hasattr(fp, "read")` in `RequestEncodingMixin._encode_files`.

- **p1** (stopped after 38 model calls). Choosing took 2 calls, the specification 6, the edit 2: the right change at
  3:30 min. Then the coordinator did not submit: after "nothing here can be checked automatically" it tried `pytest`,
  `ls tests` and called `specify_fix` again, twice; each new specifier got the whole view of code already fixed, read
  `prepare_body` and `_encode_files` again and again (one reply cut at 8192 tokens) and proposed a change of
  `prepare_body` that the reference does not make. Faults of the system, fixed:
  1. `['_SupportsRead']` from the specifier was answered "No place and sentence were read": it is an import alias
     (`from ._types import SupportsRead as _SupportsRead`). `_resolve` now follows import aliases
     (`_import_alias`), and a name that nothing defines is answered "Nothing in the repository defines X".
  2. `specify_fix` called again with nothing new (the specification done, its places edited, no pair in the call) is
     answered with the sentences and their state and a sentence to give back; a call with pairs is still read (in
     V20 a second specifier added a needed place).
  3. The finish view says the hidden tests, with the issue's own test, run after `submit_patch` and that tests are
     not written here.
- **p2** (all fixes): submitted after 12 tool calls in 1.6 min, the patch equal to the reference's code. The local
  verdict was "not resolved" (exit code 1); run by hand with the test patch: `test_post_named_tempfile` passes with
  the patch and fails without it; the whole suite has 12 failures with and 13 without the patch, the 12 being
  network tests (connect timeouts, proxies) that fail on the original too, as the container has no network. So the
  task is solved; the local judge cannot pass requests tasks. Two smaller faults, fixed:
  1. `['src/requests/models.py', '<sentence>']` from the specifier was answered "Not in the repository": now its
     functions and classes are listed and the sentence is asked for one of them.
  2. "no fact of the code to show for it here" under each requirement made the coordinator want to write a test;
     the line is gone when there is no fact.

| Item | Effect | Why |
|---|---|---|
| B11 doubting tool output | closer | no "cannot be checked" line that reads as "unverified"; the finish says how the patch is judged |
| B9 deciding when to stop exploring | closer | a second specify_fix with nothing new ends at once instead of a full view |
| W6 names of code | closer | an import alias resolves to its definition |
| B12 contradictions | closer | "not in the repository" for a file that is in it is gone |

### Local runs p3-p4 (2026-10-13, gemma-26b, agent_tool): rich_3296, drawn at random

The task: "fix(syntax): include padding with background color override" (a PR text with images); the reference changes
the `Padding` style in `Syntax.__rich_console__` from `self._theme.get_background_style()` to `self._get_base_style()`
(which adds the `background_color` override).

- **p3** (stopped after 12 specifier calls, no sentence written): the specifier's view had two faults.
  1. `self.padding` (an attribute that `Syntax.__init__` assigns) was shown as `Table.padding`, a property of another
     class: the `self` lookup fell back to any class's member of that name. A `self` attribute that the place's own
     class assigns is now an instance attribute and resolves to nothing.
  2. The fix needs `Syntax._get_base_style`, a member of the place's class that the place does not call; the view
     showed only what the place uses. Up to 2 members of the place's class that use what the place uses (the same
     calls and attributes; `_get_base_style` calls `self._theme.get_background_style()` like the place) are now shown,
     the issue's words breaking ties; scoring by the issue's words alone picked `from_path` and `highlight` (large
     bodies match many words).
  The specifier read `__rich_console__` again and again; two replies were cut at 8192 tokens, and four model calls in
  a row had the same prompt size (5,550): the harness retried replies that did not enter the history (to be looked at).
- **p4** (both fixes): the specification in 2 calls ("use `self._get_base_style()`"), the edit at 59 s, equal to the
  reference. At the finish the coordinator again tried to write a test (`reproduce_issue.py`, `tests/test_syntax.py`,
  refused). The run then hung waiting on the model: the tunnel to the local server went down (Cloudflare 1033), so
  the run was stopped and its patch taken from the sandbox. By hand: `test_background_color_override_includes_padding`
  fails without the patch and passes with it; the whole suite has 15 failures without and 14 with it, the 14 being
  the same on the original code (environment). So the task is solved.

| Item | Effect | Why |
|---|---|---|
| W3 reasoning about code in front of it | closer | the view holds the member that the fix needs |
| B11 doubting tool output | closer | no definition of another class shown as the place's own attribute |
| B7 the middle of a long context | neutral | at most 2 more short entries |

Open after p1-p4: at the finish the coordinator still wants to write or run a test (p2, p4), each attempt refused at
once; the step is cheap but the wish comes back. The local judge cannot score requests tasks (no network) and the
rich suite has 14 environment failures: a local "not resolved" must be checked by hand against the test patch.

### Kaggle V21 (2026-10-13, the 31B, 6 tasks): 1/6 by the harness, 3/6 correct by hand

- rich_3521 resolved. requests_7502 and rich_3296: correct patches (checked with the test patch: the new test passes,
  nothing that passed on the original fails), but the harness's verdict needs the whole test file to pass, and these
  files fail in this environment on the original code too (TLS/network tests; pygments-dependent ANSI output). A
  local or Kaggle "not resolved" is checked by hand before it counts as a failure of the agent. `tools/kaggle_eval.py`
  now keeps the whole hidden-test output (`tests_<task>.txt`) and prints its FAILED/ERROR lines (it was cut at 700
  characters).
- fastapi_14986: the specifier wrote "remove lines 1108-1112" (the root_path handling) instead of moving it to a
  per-request copy of the schema. Three existing tests failed on their asserts; the answer offered "send the same edit
  again: it is then kept", and the editor did, twice (also in `get_swagger_ui_html`). **The 10-13 assert rule was
  wrong as written**: a failing assert of an existing test is usually right. Now the rule applies only when the new
  value each test rejects appears in the issue's text (as in "returns '3 B'"); otherwise the edit is undone and no
  resend is offered.
- fastapi_14448: the same 4 hidden failures as local m10 (an async `__call__` of a class instance behind a wrapper).
- httpx_3672: 60-min stop. The fix spans interdependent places (parser rename and keep_alive, connection loop, server
  close); each place is verified alone, so half a fix left servers open and the next tests failed with
  `OSError: [Errno 98] Address already in use`; correct edits were undone again and again. Open, proposed: when the
  specification covers several places, verify them together after the last one (or treat a failure that is an
  environment error such as EADDRINUSE as "not checked" instead of "broken").

| Item | Effect | Why |
|---|---|---|
| B11 doubting tool output | closer | the escape from a test verdict needs the issue's own value; existing tests are trusted otherwise |
| B5 repeating a failed call | closer | resending an undone edit is no longer offered unless the issue writes the value |

### Evaluating the hidden tests locally and on Kaggle (2026-10-13, the user's request)

Two faults made the dev evaluation wrong, locally and on Kaggle:
1. **The sandbox imported the installed package, not the task's code.** swegemma's subprocess sandbox (used by
   `local_eval.py` and `kaggle_eval.py`) skips the editable install, sets PYTHONPATH to the workspace root and adds the
   host's site-packages. For a `src/` layout (requests, httpx) the tests imported the host's copy (a dependency of the
   harness itself), so no patch could change their result: the reference patch of requests_7502 failed its own new test,
   and V21's httpx_3672 failed collection on the installed httpx. `tools/eval_env.py` puts `<workspace>/src` first.
   (The skill's own test runs, `_tests._env`, already put `src/` on the path.)
2. **The harness's verdict needs the whole test files to pass**, including tests this environment fails on the
   original code (requests' TLS/network tests, rich's pygments-dependent output). `tools/judge.py` compares with the
   reference patch and the original code in the same sandbox (SWE-bench's FAIL_TO_PASS / PASS_TO_PASS) and caches the
   two baseline runs per task.

Judged again, V21's six patches: 4/6 resolved (rich_3521, requests_7502, rich_3296, httpx_3672); fastapi_14448 fails 4
of its 8 FAIL_TO_PASS tests; fastapi_14986 fails 3 and breaks a PASS_TO_PASS test.

### Port clashes in the skill's test runs (2026-10-14)

`_tests.run` started one pytest process per test file at once. httpx's test files start servers on one fixed port, so
the processes failed each other with `OSError: [Errno 98] Address already in use`, while the run on the original code
(one process) passed: every edit of httpx_3672 counted as breaking 7-12 tests and was undone (V20, V21 with the 31B,
and a local run with another model through the proxy). Now a run whose output shows a port clash runs again one file
after the other (each file with its own 30 s deadline) and the task keeps running serially. Checked on httpx_3672's
snapshot: test_client, test_pool, test_network went from clashing to 20 passed, 0 failed (about 55 s a check).

### The tests audits and the rewrite of the skill's test runner (2026-10-14)

Four agents audited the tests (the user's request: the system is evaluated by tests, so the tests must be right): the
skill's in-edit runner (`_tests.py`), the evaluation judge, a check with known answers (each task's reference patch and
an empty patch), and the 129 tasks' hidden tests. Findings on the runner that gave wrong verdicts, all reproduced:
- an edit that broke importing the package was "Kept, not checked" (conftest failed before any test ran);
- a typo'd import was stubbed away and passed; a stub written once shadowed every later run, and stubs replaced
  installed packages (a stub for email_validator broke pydantic and every fastapi test file);
- the original code ran only the first 10 failing ids, in one batch, alone: failures beyond 10, order- or
  load-dependent tests, a base run cut by its deadline and one id with a space (`test_p[a b]`, cut at the space,
  aborting the batch) all gave false BROKEN; a prefix match hid a test file that the edit made uncollectable;
- the lexical selection found no test for fastapi_14448's places, which 115-598 tests run;
- coverage counted lines run by tests that then failed; files written by tests entered the patch;
- one edit of a rename that spans files (httpx_3672) was BROKEN in every order; parallel test files clashed on a port.
The runner now reads per-test outcomes from a pytest plugin (streamed, ids relative to the working directory),
runs the original code on the same groups (cached per groups and stubs), finds stand-ins on the original code only and
only for modules the tests import, checks that the changed modules import, confirms a new failure alone and on the code
just before the edit, selects tests from a once-per-task call map (which tests call which function), keeps an edit whose
failures are AttributeError/ImportError/NameError on a name it renames while other planned places are still to edit,
runs clashing test files one after the other, removes files tests write, strips proxy variables and limits memory.
Check with the reference patches (12 tasks): no reference edit is BROKEN any more (before: fastapi_15800 in forward
order, false timeouts in requests, the httpx rename); fastapi_14448's models.py edit is now checked by 91 tests that run
its lines (before: no test selected). The judge side (JUnit outcomes, required tests, doubled baselines, official
feasibility, fingerprinted cache, failed applies) is described in CLAUDE.md ("How a patch is judged").

| Item | Effect | Why |
|---|---|---|
| B11 doubting tool output | closer | the verdicts the model reads are now right on known answers; false BROKEN and false "Kept and checked" removed |
| W5 familiar formats | neutral | the answers keep pytest's marked line and E lines |
| B5 repeating a failed call | closer | correct edits are no longer undone, so the model is not pushed to resend them |

### Proxy runs with a stronger model, round h1 (2026-10-14, 10 dev tasks, the rewritten test runner)

Judged 4/10 (harness 3/10, official_feasible 8/10): rich_3521, rich_3006, fastapi_14851 and **httpx_3672 (first time
resolved by any run; 31 min, the port-clash fix and the pending rename kept its edits)**. Not resolved: fastapi_14986
(escaped the HTML with html.escape where the tests want JSON escaping), requests_7328 (a wrong specification that the
existing tests rightly rejected), fastapi_14448 (the same 4 wrapped-async tests), fastapi_15800 (a large feature: 60
new tests), fastapi_14583 and rich_3469 (wrong place: vague statements). No internal error, no repeated call; the import
check caught an edit that used `os` without importing it in fastapi_15800, but its answer said "2 existing tests
failed" (fixed: it now says the module does not import and how to add the import). The coordinator's attempts to read
code (9 in httpx_3672) are refused at once. The failures left are of the model's reasoning, not of the tests.
