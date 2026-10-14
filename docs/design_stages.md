# Redesign: one responsibility per agent (2026-10-14, the user's)

## Why

A check of the agent_tool variant (docs/llm_checklist.md, "Check of the whole system at commit f43293c") found the
mechanics sound and the work misallocated:
- the two hard decisions are each taken in one step: localization by the coordinator (thinking 512), the whole fix
  by `specify_fix`, in the longest view of the system (about 27,000 characters), without running anything;
- the editor, which should be mechanical, carries about ten responsibilities, line numbers among them, and about 7
  answer forms;
- three agents can decide "more code is needed";
- nothing checks that the issue is fixed.

Results follow the model's strength (12B/26B about 1/10, Haiku 4-5/10, Sonnet 6/10).

The user's principles for the redesign:
1. **An agent has, as far as possible, a single responsibility.** That removes many failure modes, all the more with
   small models.
2. **The system must work whatever the model.**
3. **No stage may depend on a valid reproduction test.** A test written by the model is often impossible or wrong.
   Every stage works with what always exists: the statement, the code and the existing tests. The issue's example and
   a reproduction test are extra evidence. They improve a stage when they exist and never gate it.

The thread through the stages is therefore **the list of requirements**, which exists for every task, not a test.

## The stages

| # | Stage | Who | Single responsibility | Always receives | Extra evidence if it exists |
|---|---|---|---|---|---|
| 0 | Prepare | script | — | the repository | — |
| 1 | Requirements | agent `requirements` | turn the statement into concrete behaviours (situation → expected result, exact names) | the statement; the signatures of the names it holds | the issue's example |
| 2 | Reproduce (optional) | agent `reproduce` | write one test of one requirement | a requirement, the signatures, a template test | — (a test that does not fail for the right reason is dropped; the flow goes on unchanged) |
| 3 | Locate | agent `locate` | choose where each requirement's behaviour is decided | candidates per requirement (words + call graph) | the test's trace |
| 4 | Scope | agent `scope` | decide which other places must change too | the script's related places, each with the line that links it | the functions the test runs |
| 5 | Specify | agent `specify`, one per place | say what changes at that place | the place, the requirements it serves, the other places' names and sentences | the test's failure at that place |
| 6 | Edit | agent `edit`, one per place | write the whole new definition | the place, its sentence, the definitions it uses | — |
| 7 | Verify | script | — | the existing tests (regressions) | the reproduction test, the example |
| 8 | Review | agent `review`, one per requirement | say whether the diff does that requirement, and if not which place is wrong or missing | the requirement, the diff of its places | the test's or the example's output |
| 9 | Submit | script | — | the last good state, the budget | — |

The coordinator has one responsibility: make the call the NEXT line names. It decides nothing about the issue.

## What each stage needs and delivers (checked backwards, from the end)

**9 Submit (script).**
- Needs:
  - a clean patch on disk (no reproduction test, no files the tests wrote);
  - a closing condition: every requirement reviewed as done with no regressions, or the review rounds used up, or
    the budget used up;
  - the last good state with its score (requirements done, regressions), saved at every verification;
  - the time and calls used;
  - the coordinator to follow the NEXT line with `submit_patch`.
- Delivers: `submit_patch` and a record of the requirements done and not done.

**8 Review (one agent per requirement).**
- Needs, always:
  - one concrete requirement (stage 1);
  - the diff of the places that serve it, with the lines around it (the requirement → place map of stages 3-4);
  - those places' sentences (stage 5);
  - the names of the plan's places and of their callers and callees, to name a missing one;
  - stage 7's regressions;
  - the round counter.
- Needs, if it exists: the reproduction test's or the example's output before and now. When present, it decides,
  not the model's opinion.
- Delivers one of:
  - "done";
  - "not done: place X (planned or new), missing Y" (one sentence);
  - "cannot tell", which counts as done so that doubt never loops (F4).

  It can only add or correct, never undo. "Not done" goes back to stage 5 for X. At most 2 rounds (F6).

**7 Verify (script).**
- Needs, always:
  - the whole patch applied: every place edited, "no change" or marked failed;
  - the existing tests' baseline and call map (stage 0);
  - the harness's environment;
  - the budget.
- Needs, if it exists: the reproduction test (stage 2) or the example (stage 0) with its output on the original code.
- Delivers:
  - regressions, with their E lines, each assigned by the call map to the place the test calls (no model) and sent
    to stage 5 for that place;
  - the test's or example's output before and now, per requirement;
  - the last good state, updated when better;
  - the time used.

**6 Edit (one agent per place).**
- Needs:
  - a function-level place (the script splits a large class into the member that changes);
  - its sentence;
  - the definitions it uses;
  - one answer form, the whole definition: the script places it, adds a new name at the end of its class or file, and
    moves its imports to the top;
  - the per-edit check (syntax, import, the place's existing tests against the baseline);
  - the right order: new code before its users (the script reorders after stage 5).
- Delivers, each with a per-edit verdict:
  - the definition applied;
  - "no change";
  - "cannot be done here: X is missing", which the script adds to the plan and sends to stage 5.

  After 2 undone edits the place is marked failed.

**5 Specify (one agent per place).**
- Needs, always:
  - the place's code, focused on the lines its requirements touch;
  - the requirements it serves;
  - the other places' names and the sentences already written, specified callees first;
  - the signatures it uses;
  - the statement (system instruction);
  - on a round back: stage 8's "missing Y" or stage 7's regression, the place's current diff and its previous
    sentence.
- Needs, if it exists: the lines of this place the test runs, and its failure.
- Delivers: one sentence or "no change"; new code it names is added to the plan by the script, which reorders.

**4 Scope.**
- Needs, always:
  - the located places with their requirement (stage 3);
  - the script's related places, each with the line that links it: copies, async/sync twins, same pattern,
    readers, callers, overrides, exports;
  - the requirements (a rename means all its uses);
  - the new names the issue asks for, with their target class or file.
- Needs, if it exists: the functions the test runs.
- Delivers:
  - the plan: function-level places, each tagged with its requirements;
  - its order: callees first, new code first.

  The script adds by itself the relatives always planned: copies, twins, same pattern.

**3 Locate.**
- Needs, always:
  - the requirements;
  - candidates per requirement, ranked by the script from the requirement's text, the statement and the call graph;
  - a form to find code off the list by name or text.
- Needs, if it exists: the test's trace.
- Delivers: per requirement 1-3 places, or "covered by R1's place" (the requirement → place map).

**2 Reproduce (optional).**
- Needs:
  - one requirement the script picks (the one with concrete names or an example);
  - the signatures (stage 0);
  - a template test of the same module (stage 0, from the call map);
  - the example;
  - the script's validation: collected, and failing on the original code for the right reason (an assert or an error
    in repository code, not the test's own import or a fixture);
  - on a retry, the previous attempt and why it was refused; at most 2 attempts.
- Delivers: a valid test tied to its requirement, with its original output and the lines it runs. Or nothing: the
  flow is the same.

**1 Requirements (option B, the user's choice).** Two calls of one agent, one responsibility:
1. The agent sends the names it sees in the statement. The script answers with their signatures, which of them
   exist, the sentences it marks as deferred ("later", "a future release") and the example if there is one.
2. The agent sends 1-6 requirements. Each has the situation and the expected result, the exact names (marked new or
   existing), and **the statement's sentence it comes from**. The script checks that the sentence is in the statement
   (fuzzy match) and that each name exists or is marked new. That is the one cheap defence against an invented
   requirement, which would spoil every later stage. The quoted sentences also give the script the statement's text.

The model is never asked to copy the statement (B2).

**0 Prepare (script).**
- Needs: the repository, the harness's environment, the start time.
- Delivers:
  - the code index;
  - the impact table;
  - the existing tests' call map and baseline;
  - template tests per module;
  - the start time.

  What depends on the statement (signatures, example, deferred sentences) is computed at stage 1's first call, from
  the names the agent sends.

## Handoff gaps found and closed

| Handoff | Gap | Closed by |
|---|---|---|
| 0 → 1 | The script never receives the statement, so it cannot resolve its names or find its example | Option B: names sent at stage 1's first call; requirements quote the statement |
| 3/4 → 5, 6 | A class place is too large for one editor | The script splits it into the members that change (`_big_class_members`) |
| 5 → 6 | A helper named by a sentence must exist before its users are edited | The script reorders the plan after stage 5 |
| 6 → 7 | "X is missing" from an editor had no consumer | The script adds X and sends it to stage 5; only stage 4 (before editing) and stage 8 (after) decide scope |
| 8 → 5 | A new place from the review skipped the related places | The script adds its always-planned relatives |
| 2 retry | A retry could write the same wrong test | Stage 2 gets the previous attempt and the reason |

## Checklist

| Item | Effect | Why |
|---|---|---|
| W1 choosing from a short list | closer | locate and scope choose from short ranked lists per requirement |
| W2 whole functions | closer | the editor's only form is a whole definition |
| W3 reasoning about code in front of it | closer | each agent sees one place or one requirement with its direct context |
| W4 a filled next call | closer | the coordinator only routes; the script names each next call |
| B1 line numbers | closer | no line-number form left |
| B2 copying long text | closer | no copy of the statement; requirements quote one sentence each |
| B7 the middle of a long context | closer | no 27,000-character view; every view is one place or one requirement |
| B8 many forms | closer | one or two forms per agent |
| B10 knowing its budget | neutral | the script tracks it; showing it to each agent is still to do |
| B11, F3 doubting a verdict | closer | review per requirement; a test or example output, when present, outranks the model's opinion |
| F1 where the error is | closer | review names the requirement and the place; regressions are assigned to places by the call map |
| F4 resisting a challenge | farther (bounded) | a review is a question about done work; it can only add or correct, "cannot tell" counts as done, at most 2 rounds |
| F6 a third retry | closer | every loop is bounded at 2 |
| P7 unneeded context | closer | places left out of the plan are shown only to scope, as one linking line each |

## Costs and risks

- **More model calls.** Each is short, but Kaggle's budget is about 6 minutes per task, so the script's checks must be
  cheap (call map once, regressions only on the places' tests).
- **A wrong requirement spoils everything after it.** The quoted sentence is checked; the review compares the diff
  with the requirement, not with the sentence alone.
- **The review can be wrong (F3, F4).** Its power is limited to adding or correcting one named place, twice at most.
- ADK: every agent is an `agent_tool` sub-agent of the coordinator (fresh context per call, as today). That is 7
  sub-agents: requirements, reproduce, locate, scope, specify, edit, review. One `LlmAgent` each, plus the coordinator:
  8 agents, the most LoRA adapters a submission may carry.

## Implementation (2026-10-14)

`variants/stages/` (`tools/build.py <dir> --variant stages`):
- `skills/fix-issue/scripts/_stages.py` holds the state machine (one JSON document, `_state` 'stages') and one
  handler per stage. It reuses step.py's ranking, search, views and whole-definition edits, and `_tests.py`'s checks.
- One entry script per agent: `route.py`, `req.py`, `repro.py`, `locate.py`, `scope.py`, `specify.py`, `edit.py`,
  `review.py`.
- `agent.yaml` declares the router and 7 `agent_tool` sub-agents (`sub_agents/*.yaml`, thinking 1024 or 2048, the
  vendor's sampling).
- `eval_config.yaml` raises the turn and call budgets (many short calls).

Every agent's first call is `['open']`. The answer gives its view, and the second call is its one decision. The
router's only call is `route.py ['next']`, answered with the tool and its request.

What the first runs (Haiku through the proxy) changed:
- **st1** (rich_3006, rich_3052): both resolved.
  - An editor's "missing" answer named two methods in one string. Now several names are split.
  - The missing code goes into the plan before the place that needs it. rich_3052's `check_choice` read
    `self.case_sensitive` before `__init__` set it.
  - A name defined several times in a row (`typing.overload` variants) is one place spanning them all.
  - A reproduction test that never finishes (a prompt reading its stream forever) is refused.
- **st2** (11 tasks): resolved fastapi_14851 and rich_3894. With st1 that makes 4 of 13 (2 of the usual 10), below
  the previous design with the same model (4-5 of 10). Causes, all generic, and their fixes:
  - **A long definition could not be edited.** fastapi_14873: `APIRouter.__init__` has 217 lines of parameters
    hidden in the window, and the editor answered "missing" 16 times. New forms, without line numbers:
    - `def name(...):` with a new body keeps the decorators, parameters and docstring;
    - `['replace', '<old lines>', '<new lines>']` finds the old lines by their text in the place.
  - **Edits undone by existing tests never reached the specifier.** fastapi_14986 and requests_7328 had sentences
    the existing tests refuted; the specifier never saw those tests. A place undone twice now goes back to the
    specifier once, with the failing tests.
  - **The review overruled a failing test.** fastapi_14448 was reviewed "done" while its reproduction test still
    failed. Now a "done" is refused once while the requirement's test fails.
  - **The 300-turn budget stopped httpx_3672** with time left. The variant's budget is 1200 turns and 800 calls.
  - **Invented requirements.** rich_3521's statement is only "Segment.split_cells fix". The requirements agent
    invented a case of combining marks; the reproduction test and the fix chased it. fastapi_14986's "do not
    store root_path in servers" became "do not include it". The requirements agent now writes only what the issue
    states, and repeats the issue's words when it gives no concrete situation. A reproduction test is written only
    for a requirement whose issue sentence is concrete (code, a value, an example).

| Item | Effect | Why |
|---|---|---|
| B1 line numbers | closer | long definitions are edited by elided signature or by old text, never by numbers |
| F2 repairing from failing tests | closer | the tests that undid a place reach its specifier |
| B11, F3 doubting a verdict | closer | a failing reproduction test outranks a "done" review |
| B8 many forms | farther (small) | the editor has two more forms; they are offered in the window only when part of the place is hidden |
