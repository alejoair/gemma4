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

**st3** (Haiku, build 8b6196a, the same 13 tasks): 6/13.
- Resolved: rich_3006, rich_3052, rich_3521, rich_3894, fastapi_14851, and fastapi_14873 (the long-definition forms).
- On the usual 10 that is 3/10, against 4-5/10 for the previous design with the same model (h1, h2).
- Still failing:
  - **fastapi_14986.** "do not store `root_path` in servers" is written as "the servers do not contain root_path";
    the reproduction test and the specifier follow it against three existing tests. Since the next commit the re-specified
    place is told those tests show behaviour the issue keeps.
  - **requests_7328.** A `replace` block was counted twice; fixed after the round.
  - **fastapi_14448.** As before.
  - **httpx_3672.** 2,016 s, not resolved.
  - **fastapi_15800, fastapi_14583, rich_3469.** Statement-limited.
- Time per task: 96-327 s except requests (580 s) and httpx (2,016 s). That is still over Kaggle's ~6-minute average
  for those repositories.

**Per-edit test check off (2026-10-14, the user's request).** Stage 6 no longer runs the existing tests after each
edit (`EDIT_TESTS`, `SWE_EDIT_TESTS=1` turns it back on). In requests it cost about 70 s per edit, and the first edit of
a task built the call map with a whole-suite run (up to 150 s). An edit still has to compile, and the editor's
instruction no longer says tests are run. Stage 7 still runs the selected existing tests once on the whole patch.

| Item | Effect | Why |
|---|---|---|
| B10 knowing its budget | closer | edits answer at once; tasks fit the time better |
| F2 repairing from failing tests, B11 | farther | a regression is no longer seen at the edit that made it; only stage 7 sees it, once |

**Import check back on (2026-10-14, the user's request).** With the per-edit test run off, each edit is still checked
for importing. The edited module is imported in a subprocess; when that fails, it is imported once more with the code
just before the edit. The edit is undone only when the earlier code imported and the new code does not. No copy of the
original code is needed. It costs about 0.1 s (rich: 0.1 s with an error, 0.04 s without), and the answer says the
module cannot be imported, with the error.

| Item | Effect | Why |
|---|---|---|
| F1 where the error is | closer | a broken import is caught at the edit that made it, with the error line |
| B10 knowing its budget | neutral | about 0.1 s per edit |

**Stage 7 off too (2026-10-14, the user's request).** `_verify` no longer runs the existing tests on the whole patch,
no longer builds the whole-suite call map and no longer reruns the reproduction test (`VERIFY`, `SWE_VERIFY=1` turns it
on). The patch as it is becomes the last good state. The review sees "not run again". Its rule that a failing
reproduction test refuses a "done" applies only while stage 7 is on; otherwise it would refuse every "done". With both
checks off, the only tests that run in a task are the reproduction test's validation at stage 2 (one file, at most 40
s). A replay of rich_3006 now takes 18 s.

| Item | Effect | Why |
|---|---|---|
| B10 knowing its budget | closer | no whole-suite run in any task |
| B11, F3 doubting a verdict | farther | the review has no test output after the edits; only the model's reading of the diff |
| F2 repairing from failing tests | farther | no regression reaches the specifier |

**st4** (Haiku, build d2f7e51: per-edit tests and stage 7 off, import check on; the usual 10 tasks): 3/10.
- Resolved: httpx_3672 (558 s; 2,016 s and not resolved in st3), rich_3521, rich_3006.
- Time for the 10 tasks: 2,051 s, a mean of 205 s. The rounds with the test checks took about twice as long.
  requests_7328 took 208 s (580 s before). Only httpx is over the ~6-minute average.
- **fastapi_14851 was lost to a system fault.** The editor of `APIRouter.on_event` cannot see other places. It named
  `APIRouter.__init__` as missing four times after `__init__` had been edited, and each answer sent `__init__` back to
  be specified and edited again. Fixed after the round: a "missing" answer that names a place already edited in this
  fix gets that place's diff, and the editor goes on with its own change.
- Same score as st3 (3/10) with half the time. The failures left are fastapi_14986 (the requirement paraphrases the
  issue wrongly), requests_7328, fastapi_14448, and the statement-limited fastapi_15800, fastapi_14583 and rich_3469.

## Weak points found by four agents in the st4 traces (2026-10-14)

Four read-only analyses of the st4 traces. Agent A: fastapi_14986, requests_7328. Agent B: fastapi_14851,
fastapi_14448. Agent C: fastapi_15800, fastapi_14583, rich_3469. Agent D: the resolved runs, plus patterns across all
10. The model in the traces is Haiku. Items are grouped by kind; each has its evidence and a generic fix. Not fixed yet.

**Script faults (bugs)**
1. *Requirement sentences are cleaned like names.* `_args.clean` strips a closing backtick, so `code_names` finds
   nothing: no `[new: app.frontend]`, no new-member places, an empty reproduce view, text-only ranking (15800, 14583).
   Fix: never clean a sentence.
2. *Text before an elided `def name(...):` is dropped.* The helper `_escape_json_for_html` was lost, and three Swagger
   tests fail with NameError (14986). Fix: apply the leading definitions as new code, or refuse; never drop them.
3. *Whole-definition re-indent.* A def at column 0 with its body at the file's indentation ends up 4 spaces too deep,
   still compiles and is kept (requests_7328, httpx). Fix: indent from the body's own offset; flag a whitespace-only
   diff.
4. *The edit cap counts refused attempts.* Two stale `replace` answers used up `MAX_EDITS`, and the editor's own
   correct fix (`hist.copy()`) was refused (requests_7328). Fix: count only applied edits that change code.
5. *A diff against the previous edit,* not the original: a correct restore looked like 209 changed lines. Fix: show the
   place's cumulative diff against the original.
6. *A refused `replace` shows nothing.* Fix: show the place's current lines.
7. *Asks are counted when the router routes, not when an agent delivers.* Idle `next` calls use them up, and
   "failed" is set while the edit stays in the patch. The review then shows "the edit failed" and no diff (14851).
   Fix: count delivered decisions; set "edited" from the diff on disk; always show every place's current diff in
   review.
8. *A "not done" review is lost* when the round's ask keys are used up; the route goes to submit (14851). A re-edited
   requirement is never reviewed again (rich_3469). Fix: a "not done" starts a new round with fresh keys and reviews
   that requirement again.
9. *"The patch is submitted" is said before `submit_patch` is called.* In 14851 the coordinator refused to submit and
   wrote 17 text turns. Fix: keep NEXT=submit_patch until the call is seen.
10. *The `missing` answer loops.* Its ask counter is reset each time. An attribute became a "new definition" place in
    the wrong twin file (httpx). A place already edited was re-edited 4 times (14851; fixed). Fix: at most 1-2 missing
    answers per place; an attribute goes to its owner's `__init__` in the requester's file.
11. *Scope answers in the view's own format are dropped silently.* `'Name (path)'` matched nothing and the plan was
    saved without those places (httpx). Fix: read every format the views print; answer "not recognised" otherwise.
12. *A search turns into a choice* when the searched name resolves (14448). Fix: a search only lists matches.
13. *A class is split into members that match only the class's own name* (`APIRouter.handle` for "router"), over
    the scope's "none" (15800). Fix: split on the requirement's specific terms, or keep the class for a new member.

**Missing information (views)**
14. *Requirements are written blind.* req.py shows one definition per name and no library class. It shows no sign of
    the existing behaviour: `self.servers.insert(...)`, the `root_path_in_servers` docs, or test assertions. So "do
    not store" became "do not contain" (14986), and the title was repeated as a requirement (14851, 3521, 3469). Fix:
    for the statement's names, show every definition, where attributes are written, related parameter docs and
    existing test assertions, labelled "behaviour the tests expect now".
15. *Views leave out the code a place delegates to* (`super().__init__`, Starlette's `Router.__init__`), and list
    false "code it uses" (14851). Fix: show the overridden or super method as read-only context.
16. *Scope misses related code*: attribute readers and parameter pass-through (14851, "No other related code"). A
    reader is shown by one line, which cannot show an if/elif order (14448). Fix: add attribute and parameter
    readers; show the reader's whole branch chain.
17. *Locate does not show a detector's callers.* `annotation_is_pydantic_v1` was third; the reference warns at its
    callers (14583). Fix: for a predicate-like candidate, list its callers.
18. *Twins diverge.* The async parser got a property, the sync one an attribute (httpx). Fix: show the twin's sentence
    and diff, "the same change" by default.
19. *Review checks the requirement, not the sentence.* `hist[1:]` met the requirement literally against the sentence's
    `hist.copy()` (requests_7328). Fix: also ask whether the code does what the sentence says.

**Decisions with no way out**
20. *An empty or all-"no change" patch is submitted as finished,* at 2 minutes of 60 (15800). Fix: go back to locate
    once; "cannot tell" over unchanged places counts as not done.
21. *"no change" carries no reason*; the reasons reached only the coordinator, which decides nothing. Fix:
    `['no change', '<why>']`, shown to the next stage.
22. *Requirements without substance* (the title repeated) give the review nothing to check. A forced re-edit
    invented a change in core wrapping code (rich_3469). Fix: keep such a patch minimal; do not force an edit
    through review.

**Reproduce**
23. *A test that runs no repository code is kept* (14583: it only imports `pydantic.v1`). Fix: refuse 0 repository
    calls.
24. *Tests import packages that do not exist* (`mitmproxy`, `esmerald`); the view gives no import path or template
    when names are missing. Fix: always show the package's import path and a template test.
25. *With stage 7 off, the kept test is never run again,* yet the answer says it will be. Fix: rerun only that test
    at review (seconds), or skip stage 2 when verification is off.
26. *The concreteness gate is lexical* (14448: no backticks, so no test). Fix: also allow it when an existing test of
    the located code exists.

**Cost (Agent D: 840 model calls for 10 tasks; the router alone 368)**
27. Each sub-agent call costs 3-4 model calls (`open`, decision, final text, often `load_skill`); about 1 of them is a
    decision. Decisions are 35% of the time, router turns 28%, final texts 20%, `open` 13%, `load_skill` 4%.
28. *`load_skill`* came in 39 calls, 27% of sub-agent calls. Only the coordinator's prompt says it is not needed.
    Fix: say so in all 7 sub-agent prompts.
29. *The coordinator deliberates* on alarming sub-agent sentences: 51 text turns, 136 s. Fix: a fixed short reply for
    sub-agents, and a router answer that repeats unchanged.
30. *httpx_3672 was resolved by luck.* The hidden tests check only the parser rename, done at 212 s. The patch leaves
    `_pool.py`'s calls broken, and 208 s went into a `missing` loop.

## Local run g1 (the local 31B, fastapi_9555, step by step, 2026-10-14)

The user asked for one random task, monitored step by step instead of a batch of 10. fastapi_9555 ("Handle wrapped
dependencies": `functools.wraps` wrappers hide that a dependency is a coroutine or a generator) on the stages build at
1f9c3c8 (per-edit tests and stage 7 off, import check on). Not resolved: 1 of the 3 fail-to-pass tests passes, and both
generator cases fail. Time was 567 s over 42 model calls.

Step by step:

| Stage | What happened | Verdict |
|---|---|---|
| requirements | One requirement, quoting the statement's first sentence ("Use the unwrapped call in `solve_dependencies`...") | right for the statement; it names the place, not the behaviour |
| reproduce | An `async def` test. pytest has no async plugin in the sandbox (the harness's pytest.ini has `-p no:anyio`), so it failed before its body ran. The script **kept** it, saying it "runs 0 functions of the repository" | **system fault** (weak point 23) |
| locate | `solve_dependencies` was chosen from 8 candidates; `Dependant.is_gen_callable` and its siblings were on the list | follows R1, which names `solve_dependencies` |
| scope | Showed only the two callers of `solve_dependencies`; the model answered `none` | `_solve_generator` (the callee that reads `is_gen_callable`) is not related code |
| specify | One clear sentence: replace the three `use_sub_dependant.is_*` reads with `inspect.unwrap(call)` checks | good |
| edit | Two `replace` edits. The first turned `elif` into `if` and broke the cache branch; the model saw it in the diff and sent the whole 140-line definition. Its first 3 tries were malformed, so the harness asked again, about 2 min (4 × ~2,100 tokens); the 4th was applied | the model found its own mistake; long whole definitions cost malformed calls |
| review | "done" | right for R1 as written |

**Why it failed.** The patch makes `solve_dependencies` choose the generator branch for a wrapped generator. That
branch calls `_solve_generator`, which reads `dependant.is_gen_callable` again and still sees the wrapper. The
reference changes the three `Dependant.is_*_callable` properties themselves (models.py), so every reader sees the
unwrapped call.

**Fixes (built as build_g2; dry runs on the snapshot made hermetic):**
1. *Reproduce refuses a test that fails before it calls any function of the repository* (weak point 23), when the
   call map is available (Python 3.12+). An `async def` test that pytest cannot run gets its own refusal: write a
   plain `def` test that runs the async code with `asyncio.run(...)` or through the repository's test client.
   Checked: an async test is refused with that reason; `assert 1 == 2` is refused (0 calls); a TestClient test
   with a wrapped generator dependency is kept ("runs 16 functions").
2. *An edit that stops using code that other code still uses says so* (`_dropped_uses`). The attributes read as
   `<expr>.<attr>`, and the plain names called, inside the place before and after the edit are compared. For each
   name the edit stopped using, the readers elsewhere are listed when all of these hold:
   - it has exactly one definition in the repository;
   - it has at most 30 readers;
   - the readers are outside tests and docs.

   The editor is offered `['missing', '<that definition>', '<what must change in it>']`, which puts it into the plan.
   On g1's edit, the notice lists:
   - `Dependant.is_gen_callable` and `Dependant.is_async_gen_callable`, read by `_solve_generator` (utils.py:549,
     551), `get_dependant` (268) and `Dependant.computed_scope` (models.py:105);
   - nothing for `is_coroutine_callable` (only `solve_dependencies` reads it).

   It takes 0.3 s.

Checklist:

| Item | Fix 1 (reproduce) | Fix 2 (dropped uses) |
|---|---|---|
| W3 code in front of it | neutral | closer: the readers are shown by name and line, not left to be found |
| W4 a concrete NEXT | neutral | neutral: the NEXT names the definition in the `missing` form |
| W5 familiar formats | closer: pytest's own message in place of a kept test that never ran | neutral |
| B8 many forms | neutral | farther, slightly: a second choice after an accepted edit (`missing` or a sentence); reason: it is the existing `missing` form, filled in |
| B9 exploring | neutral | neutral: nothing to read, the names are given |
| B11 doubting tool output | closer: the script no longer reports a test that never reached the code as evidence | closer: "accepted" no longer hides that other readers keep the old behaviour |

Still open from g1:
- A requirement that names a place ("in `solve_dependencies`") makes locate pick the place, not the code that decides
  the behaviour.
- A 140-line whole definition costs malformed calls; the view asks for the whole definition first even when the place
  is long.

## Gated runs with Haiku (fastapi_9555, 2026-10-14)

The user asked for a real step-by-step test. Watching a running batch is not that: the model goes on while a fault is
being read.

`tools/step_gate.py` is an OpenAI-style pass-through in front of any upstream: the local gateway, or
`tools/anthropic_proxy.py`.
- Each model call goes upstream at once, but its answer is held until the step is released.
- Each step can be read before the agent acts on it:
  - the agent that speaks;
  - the script's answer it just received, whole;
  - its thinking and its call.
- `show`, `go [K]`, `until <agent>`.
- The time held is written to `paused_s`.
  - `tools/eval_env.py` passes `SWE_PAUSED_FILE` to the sandbox.
  - `_stages._elapsed` leaves the pauses out, so the "Time used" the model reads is not changed by the pauses.
- `local_eval.py --max-minutes/--llm-timeout` keep a held step from ending the task.
  - One visible side effect: the harness's task message then says "Time allowance: 600 minutes".

Processes started with `nohup` die when the session's turn ends; the gate, the proxy and the run must be started as
background tasks of the session (`scratchpad/grun.sh`).

**The runs** (fastapi_9555, the same task as g1). Each was stopped at the first system fault, fixed and restarted:

| Run | Stopped at | Fault | Fix |
|---|---|---|---|
| gh1 | step 1 | the processes died at the end of the turn (nohup) | background tasks |
| gh2 | step 3 | `requirements` called `load_skill` first (weak point 28: only the coordinator's prompt said it is not needed) | the 7 sub-agent prompts say the skill is already loaded; from gh3 on no sub-agent called it |
| gh3 | step 5 | R3's sentence lost its first backtick (`_args.clean`), the backticks no longer paired, and plain words ('decide', 'solves', 'calling') were tagged as new code; `inspect.unwrap`, `functools.cache` and `__wrapped__` were tagged new too | requirement sentences are only stripped of quotes that wrap them whole (`_sentence`); library names (an importable module outside the repository) and dunders are not new code (`_library`) |
| gh3 | step 20 | locate sent `['solve_dependencies', 'Dependant.is_coroutine_callable']` for R2 and the script kept only the first, silently, because the second was off R2's list (it had been chosen for R1) | a name off the list is looked up in the repository; one that is not found is named in the answer |
| gh4 | — | ran to the end | **resolved** (3 of 3 fail-to-pass tests), 78 model calls, 290 s of which 139 s held |

**gh4 step by step:**

| Stage | What happened |
|---|---|
| requirements | Two requirements; sentences intact; only `lru_cache_wrapper` tagged new |
| reproduce | A `TestClient` test with an `@cache` async dependency; kept, it runs 46 functions of the repository |
| locate R1 | `solve_dependencies`, `Dependant.is_coroutine_callable`, `Dependant.is_async_gen_callable`. Every candidate the reproduction test runs was marked "[the reproduction test runs it]", which pointed at the properties |
| locate R2 | `Dependant.is_coroutine_callable`, `solve_dependencies` (both kept since the fix) |
| scope | The script added `Dependant.is_gen_callable` (same pattern) and `Dependant.computed_scope` (reads a planned property); the model also named `is_gen_callable` |
| specify | `solve_dependencies`: no change, "the fix belongs in the Dependant properties"; one sentence each for the three properties (unwrap `self.call`, apply the same checks); `computed_scope`: no change |
| edit | Three whole definitions, all accepted. Two began with an unindented decorator line; the re-indent handled it |
| review | R1 done, R2 done |
| submit | `submit_patch`, 1,688 characters: the reference's change, without its `_unwrapped_call` helper |

What g1 (the local 31B, before the fixes) did differently:
- it kept an async reproduction test that never ran;
- the test therefore gave locate no evidence;
- locate chose `solve_dependencies` alone;
- the edit left `_solve_generator` reading the wrapped call.

**Still open (seen in gh4, not faults of the result):**
- Scope adds a reader of a planned property (`computed_scope`) under "the same change is needed there". It is a
  reader, not a copy. Cost: one specify cycle, 3 model calls, answered with "no change".
- A "no change" answer to specify is acknowledged as "Saved for X".
- Reproduce still says "It will be run again after the edits" while stage 7 is off (weak point 25).
- 78 model calls for 5 places. The coordinator's turns are 2 per sub-agent call (`route.py ['next']` and the call),
  and every sub-agent call costs `open`, a decision and a closing sentence (weak point 27).

Checklist (the three fixes):

| Item | load_skill line | `_sentence` / `_library` | locate keeps off-list names |
|---|---|---|---|
| W4 a concrete NEXT | closer: the first call is the script, as the forms show | neutral | neutral |
| W6 the issue's words | neutral | closer: sentences reach later stages as the issue and the model wrote them | neutral |
| B6 tools that do not exist | closer: no detour through load_skill | neutral | neutral |
| B8 many forms | neutral | neutral | neutral |
| B11 doubting tool output | neutral | closer: no false "[new: decide, solves]" tags | closer: the answer no longer hides a dropped choice |
| P3 positive rules | closer: stated as a fact ("already loaded"), with the call to make | neutral | neutral |

## Fixes after gh4 (2026-10-14)

1. **Scope:** a reader of a planned property (`reads <property>`) is no longer added as needing "the same change". It
   is listed as related code, with its line, for scope to decide (gh4: `Dependant.computed_scope`). Copies and
   same-pattern siblings are still added.
2. **Specify:** a "no change" answer is acknowledged as "<place> keeps its code: it is not edited". A sentence is
   acknowledged as "its editor writes the change from this sentence".
3. **Reproduce:** with stage 7 off, the kept test's answer no longer promises a rerun. It says the other agents see
   which code the test runs and how it fails; specify and review show its output.
4. **Locate:** each candidate that reads or calls other candidates says so: `[it uses X, Y from this list]`. In g1 the
   statement named `solve_dependencies`, which only reads the `Dependant.is_*_callable` properties where the decision
   is made. They were on the list, but nothing said that one uses the others.
5. **Edit, long places** (over 80 lines):
   - The view asks for `['replace', '<old lines>', '<new lines>']`, one call per group of lines, not the whole
     definition. g1 sent the 140-line `solve_dependencies` whole and the call came out malformed three times.
   - A `replace` whose new lines open with an import at column 0 moves the import to the top of the file (undone if
     the replace is not applied).
   - After an accepted edit, a correction is asked for as another `replace`.
6. **Chained steps:**
   - When an agent finishes its part (the script's answer ends with DONE), the script computes the coordinator's
     next call at once.
   - The agent's last answer says: "reply to the coordinator with exactly this line: call the tool X with request
     'Y'". The coordinator makes that call without asking route.py.
   - Guards:
     - While a line is pending, route.py answers that same line, not a new ask (a second ask would spend the
       target's tries).
     - A call from an agent that is not the next agent's `open` is refused with the line to return.
   - The coordinator's and the agents' prompts say how to treat the line.
   - Expected saving: one coordinator call per finished agent step (about 14 of gh4's 78).

Dry run on fastapi_9555 (the snapshot): the flow route → requirements → reproduce → locate → scope → specify → edit
ran through the scripts. Checked:
- each finished part ended with the chained line;
- route.py with a pending line returned it unchanged;
- a late call of the previous agent was refused;
- the long `solve_dependencies` got the replace view;
- a replace carrying `import functools` put the import at the top.

Checklist:

| Item | 1 scope | 2 ack | 3 repro text | 4 locate uses | 5 long edits | 6 chained steps |
|---|---|---|---|---|---|---|
| W1 choosing from named options | closer: the reader is an option with its line, not imposed | neutral | neutral | closer: the list shows how the options relate | neutral | neutral |
| W2 writing whole blocks | neutral | neutral | neutral | neutral | farther for long places; reason: B2, a 140-line copy is a long exact transcription that failed three times | neutral |
| W4 a concrete NEXT | neutral | neutral | neutral | neutral | closer: one form named first | closer: the next call is given whole, one step earlier |
| B2 copying long text | neutral | neutral | neutral | neutral | closer: only the changed lines are copied | slightly farther: the agent copies one short line; reason: it saves a call per step, and route.py stays as the fallback |
| B7 middle of long context | neutral | neutral | neutral | neutral | closer: shorter answers | closer: fewer coordinator turns in its history |
| B11 doubting tool output | closer: no false "same change is needed" | closer: the answer says what was saved | closer: no false promise | neutral | neutral | neutral |
| B12 contradictions | neutral | neutral | closer | neutral | closer: the correction form matches the edit form | neutral |

## Passing the coordinator's line on (2026-10-15)

From gk2 to gk11, Haiku's locate agent refused six times to return the chained line. Its reasons were "that isn't part
of my locate task", "my task covers only R1" and "choosing the next step is outside my task". Each refusal cost the
coordinator one route.py call. Locate's prompt says "one requirement… that is your only task", and the line often
names the same agent for another requirement ("call the tool locate with request 'R2'"). Every sub-agent's step 3 now
says that the line is the coordinator's next call, passed on word for word even when it names another requirement or
another agent, and that the agent does not make that call.

| Checklist item | Effect |
|---|---|
| W4 a concrete NEXT | closer: the step names what to do with the line |
| B12 contradictions | closer: "only task" and "pass this line on" are reconciled in the same sentence |
| P3 positive rules with their reason | closer: it says why (the coordinator's next call) |
| B8 many rules | neutral: no new form, one clause in an existing step |

## The open weak points of the gated runs, fixed (2026-10-15)

The list of system problems that were still open after gk2-gk12. All were checked by dry runs on the task snapshots;
the next gated runs test them against the models.

1. **Regressions (gk9).** Review runs the existing tests of the changed code once per patch state, on the patch and on
   the original code (`_tests.check` with `use_map=False`: the changed names and the changed lines choose the tests,
   with no whole-suite call map).
   - The view lists the tests that pass on the original code and fail with the patch, and says that a requirement
     contradicting them was read against the issue.
   - A first "done" is refused while they fail.
   - Dry run on fastapi_14986 with gk9's patch: in 6 s, three tests that expect `servers: [{'url': '/api/v1'}]` fail,
     and "done" is refused.
2. **A requirement or test that contradicts existing tests (gm8, gk9).** It is caught by the same check, once the
   patch follows it.
3. **Locate on a title-only statement (fastapi_14583).** After the candidates, the view adds the code that calls the
   first three usable candidates (`[calls X]`). Dunders, nested functions and names called in more than 8 places are
   left out. Dry run: `analyze_param`, `get_body_field` and `create_model_field` now appear, one step from
   `get_dependant`.
4. **Scope looked only upward (fastapi_14851, fastapi_14583).** The related code now includes:
   - the repository functions a chosen place calls (`self.<method>()` of its class, and plain names defined once);
   - the code that imports a module whose top-level code was chosen.

   Each comes with its linking line.
5. **Requirements without substance (gk11, gm8).** When there is more than one requirement, one whose "what must
   happen" repeats the issue's sentence (similarity over 0.9) is left out, and the answer says so.
6. **Line numbers in specify sentences (Gemma).** The first such sentence is refused, with the reason and an example of
   naming code by its text.
7. **Noise.**
   - "[it uses ...]" is not shown on classes or docs examples.
   - A dotted name in the requirements lookup matches its exact qualified name first.
   - A word-chosen test template needs two shared words.
8. **Cost (an accepted edit had no line for the coordinator).**
   - The accepted edit also returns the coordinator's next call. It is computed on a copy of the state.
   - It becomes real when the next agent opens; a correction from the editor is still accepted.
   - Dry run: "edit is done: analyze_param is changed. Next: call the tool review with request 'R1'."

| Checklist item | 1-2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|
| W1 choosing from named options | neutral | closer: the callers are options with their names | closer: callees are options with their lines | neutral | neutral | closer | neutral |
| W3 code in front of it | closer: the failing assertion is shown | closer | closer | neutral | neutral | neutral | neutral |
| W5 familiar formats | closer: pytest's own output | neutral | neutral | neutral | neutral | neutral | neutral |
| B1 line numbers | neutral | neutral | neutral | neutral | closer | neutral | neutral |
| B11 doubting tool output | closer: "done" is checked against real tests | neutral | neutral | closer: no empty requirement to approve | neutral | closer | neutral |
| B7 long context | slightly farther: the review view grows by the failing tests (about 700 characters); reason: the evidence is what decides | slightly farther (up to 4 lines) | slightly farther (up to 5 + 4 lines) | neutral | neutral | closer | closer: fewer coordinator turns |

## Gated runs gk13-gk13c (fastapi_15800, Haiku): new members (2026-10-15)

fastapi_15800 asks for two new methods ("Add support for `app.frontend("/", directory="dist")` and
`router.frontend(...)`"); the statement is the title only. Three runs, each stopped at its first system fault:

1. **gk13 (build 7795e45): locate had no place for new code.** The candidates were swagger and docs code; the class
   FastAPI, where the new method goes, was not on the list although the reproduction test failed with
   `'FastAPI' object has no attribute 'frontend'`; the searches for `frontend` found nothing and locate gave up. The
   base procedure had this (`_rank_candidates` puts the owner class of a new `var.name` first) but the stages build
   passed no requirement types to it. **Fix:** `_new_members` puts the new member at the top of locate's list, in the
   class it goes in: from the requirement's new names (`app.frontend` → the class `app` is made from, by
   `_rank.owner_class`) and from the reproduction test's AttributeError. A search for it says it is new code and points
   there. Locate stores it as a new place, and scope adds it once.
2. **gk13b (build d8e7d91 without the edit-after-edit fix).**
   - Locate chose both new places at once.
   - Specify, for APIRouter.frontend, wrote only "registers a static frontend". The view of a new member said nothing
     but "new code at the end of class APIRouter".
   - The second editor was shown the first place. The tentative call after an accepted edit became real only when an
     agent of another stage opened, never edit after edit.

   **Fixes:**
   - `_members_note`: the view of a new member lists its class's public methods and those it inherits from a library
     class, resolved through the file's imports (APIRouter inherits `mount` from Starlette's `Router`).
   - The tentative call becomes real whenever a new agent opens (`open` or no args), in any stage.
3. **gk13c (build d8e7d91).**
   - Both specify sentences named the means: `self.mount(path, StaticFiles(directory=directory, html=True))`.
   - Edit after edit worked.
   - The regression check ran at review (64 pass, 3 failing before).
   - The rerun reproduction test forced "not done" while it failed. The test's own fault was that it used a `dist` that
     does not exist, so the edit went to `check_dir=False`. The second review passed.
   - One new fault: locate for R2 replied to the coordinator with its NEXT line instead of choosing. Step 3 of the
     prompts said "reply with the line the script gives" without saying when. **Fix (all six sub-agent prompts, not run
     live yet):** reply only when the script's NEXT says "no call". A NEXT that names a script is the agent's own call.
     "Only if you cannot go on, reply with one sentence that says why."

   Result: not resolved (0 of 60 FAIL_TO_PASS, nothing broken). The reference is a whole feature (fallback modes,
   low-priority routes, `check_dir`, include prefixes) that the title does not describe.

Open weak points from these runs:
- A reproduction test can fail for its own reason after a correct fix (a missing directory). Review treats it as the
  requirement's verdict.
- Review does not compare twin places already edited (APIRouter.frontend kept checking the directory after
  FastAPI.frontend stopped).
- Scope has no related code for a new member (its callers do not exist yet).

| Checklist item | New members in locate | The class's methods for new code | Edit after edit | Prompt step 3 |
|---|---|---|---|---|
| W1 choosing from named options | closer: the place for new code is an option, by name | closer: methods to build on are options, by name | neutral | neutral |
| W3 code in front of it | neutral | closer: what the class can already do is shown | closer: the editor sees its own place | neutral |
| W4 following a concrete next call | closer: a search that finds nothing points to the option | neutral | closer: the call and the state agree | closer: a NEXT that names a script is the agent's own call |
| W6 names of code and statement | closer: `app.frontend` becomes `FastAPI.frontend` | closer | neutral | neutral |
| B11 doubting tool output | neutral | neutral | closer: the view no longer contradicts the request | neutral |
| B12 resolving contradictions | neutral | neutral | closer: request and window name the same place | closer: "reply with the line" no longer competes with the NEXT |
| B7 long context | slightly farther: one or two lines more | slightly farther: up to 2,500 characters for a new member only; reason: without it the sentence said nothing of how | neutral | slightly farther: two sentences more in each prompt |

## Gated runs gk14-gk14b (fastapi_12942, Haiku): the audit's build, live (2026-10-15)

The first live run after the checklist audit (`docs/prompt_audit_stages.md`, commit 4944d3d). fastapi_12942 asks for a
tagged discriminated union (`Annotated[Annotated[A, Tag] | Annotated[B, Tag], Discriminator(...)]`) to be read as a
body field, not a query parameter. The reference changes `field_annotation_is_complex` in `_compat.py`.

**The audit's changes, live:**
- The coordinator read "Time used: 0 min 0 s of 45 min" and the NEXT without double quotes, and followed every line.
  It also followed a line that came after an editor's extra sentence.
- Specify's line-number refusal turned "before the scalar check at line 465" into a sentence naming the line by its
  text.
- The new callers in locate brought `analyze_param` (`[calls Path]`), which decides body or query.

**Three changes from these runs:**
1. **gk14, scope: the deciding callee left out.** `_callees` took the first five calls in `ast.walk` order (breadth
   first, not by line). The condition `elif not field_annotation_is_scalar(...)`, where body or query is decided, was
   cut. Callees are now ordered: those the reproduction test runs, then those a condition calls, then by line.
   Checked live in gk14b: it is listed with its `elif` line.
2. **gk14b, edit: a regression of the audit.** The sentence restated before NEXT (P6) was cut at 400 characters with
   "...". Haiku read it as the script's output cut off ("I worked from the visible part"). It is now restated whole
   (a sentence is at most 1,500 characters).
3. **gk14b, specify: the context view missed the deciding call.** "Code that analyze_param uses" showed Body,
   ParamTypes and FieldInfo, chosen by words, but not `field_annotation_is_scalar`. The specify and edit views now add
   up to 3 functions the place calls in a condition or that the test runs, with their call line, when the context view
   left them out. Dry run on fastapi_12942: `field_annotation_is_scalar` first.

Result: **resolved** (judged: the FAIL_TO_PASS test passes, nothing broken, official_feasible). 40 model calls, 394 s.
The fix is in `analyze_param`, not the reference's place: it unwraps the Annotated members of the union before the
scalar check.

| Checklist item | Callee order | Sentence restated whole | Deciding calls in the views |
|---|---|---|---|
| W1 choosing from named options | closer: the deciding callee is an option | neutral | neutral |
| W3 code in front of it | closer | neutral | closer: the call where the branch is decided, with its line |
| B11 doubting tool output | neutral | closer: no "..." that reads as a cut output | neutral |
| B12 contradictions | neutral | closer: the two copies of the sentence agree | neutral |
| B13 order in lists | closer: the relevant callee first | neutral | closer |
| P6 the decision at both ends | neutral | closer: the whole decision before NEXT | neutral |
| B7 / P7 long context | neutral (the same five) | slightly farther: up to 1,100 characters more in a long sentence; reason: a cut copy misled | slightly farther: up to 3 lines of 2; reason: the deciding function was missing |

## Gated run gk15 (rich_3894, Haiku): no system fault (2026-10-15)

rich_3894 ("Handle unusual `__qualname__` in inspect"; the author's sentence gives the fix). Resolved, with no stop:
- locate listed the reference's place, `Inspect._get_signature`, first;
- the line-number refusal turned "line 103" into the line's text;
- the sibling-members heading and the whole restated sentence appeared as designed.

The reproduction test was not kept: the failing case needs a class with `__qualname__` in its `__slots__` behind a
decorator. Haiku's two tries were refused with their reasons (a TypeError at class creation; a test that passes on
the code). One fix: the reproduce view named `benchmarks` (asv's folder) as a package of the repository next to
`rich`. `_example._packages` now leaves out benchmarks, examples, tools and similar folders.

Checklist: P7 closer (one unneeded, misleading name less in the view), W6 closer (the package named is the one the
issue's users import); every other item neutral.

## The open problems of the table, fixed in the design (2026-10-15)

After the reading of `docs/runs_by_stage.md` and the root causes (proxies instead of checks, information that does
not travel between stages, contradictions between what is shown and what is asked):

1. **Localization without names (14583, 15800).**
   - The test plugin records each failure's traceback (`_tests._frames`, `path:line`).
   - Locate puts first the up to 3 repository functions the reproduction test's failure went through, innermost first:
     "[the reproduction test fails here: <line>]", then "[the failure passes through here: <line>]".
   - Dry run on rich_3894 with the reference test: `Text.append` (raises), `Text.assemble`, `Inspect._get_signature`
     (the reference's place).
2. **A requirement against the issue's intent (14986).** `requirements.md`:
   - a requirement says what changes;
   - the behaviour the issue does not mention stays as it is, so a change of how something is done does not become the
     removal of what it produces (the hidden tests also run the existing tests).
3. **A reproduction test that fails for its own reason (gk13c).** When the test still fails with the patch but with
   another error kind than on the original code, and not on an assertion:
   - review says so as a fact: either a part of the change is missing, or the test fails for a reason of its own;
   - it no longer forces "not done".
4. **Twins not compared (gk13c).** The review view shows the plan's places that share the reviewed places' short
   name, with their diff.
5. **Line numbers in specify (Gemma, gk14b, gk15).** The specify view shows the code without line numbers: it was the
   anchor that the sentences used and that the script then refused.
6. **Cost.**
   - A requirement whose own reproduction test passes on the patch, with the regression check clean, is marked done
     by the test, with no review agent (2-3 model calls less).
   - The regression check's cache used `hash()`, which changes with every process, so it never hit. It is now keyed
     by a SHA-1 of the patch, on disk (shared with the tentative route's copy), as is the test's rerun.

| Checklist item | 1 failing frames | 2 requirements | 3 changed failure | 4 twins | 5 no numbers | 6 cost |
|---|---|---|---|---|---|---|
| W1 named options | closer: the failure's functions are options | neutral | neutral | neutral | neutral | neutral |
| W3 code in front | closer | neutral | neutral | closer: the twin's diff | neutral | neutral |
| W5 familiar formats | closer: a traceback's frames | neutral | closer: the two errors | closer: a diff | neutral | neutral |
| B1 line numbers | neutral | neutral | neutral | neutral | closer: no number to anchor on | neutral |
| B11 doubting tool output | neutral | neutral | closer: the test's own failure is not a verdict | closer | neutral | neutral: the test decides only when it passes and nothing breaks |
| B12 contradictions | neutral | neutral | closer: no forced "not done" against the evidence | neutral | closer: nothing shown that is then refused | neutral |
| F4 challenges without facts | neutral | neutral | closer | neutral | neutral | neutral |
| P3 positive rules with reason | neutral | closer: what to keep, with the reason | neutral | neutral | neutral | neutral |
| B7 / P7 long context | slightly farther: up to 3 lines; reason: the only signal when the statement names nothing | slightly farther: one sentence | slightly farther: one line when it applies | slightly farther: up to 1,200 characters when there is a twin; reason: gk13c | closer: shorter lines | closer: fewer turns |

## Gated run gm11 (fastapi_12942, the 31B on Colab vLLM): stopped in round 2, seven fixes (2026-10-15)

The 31B located `analyze_param` (through the callers) and the regression check refused its first patch (a `while`
over the outer `Annotated` broke `test_multiple_annotations`). It did not reach a fix. The faults, in the order they
appeared, and their fixes:

1. **Library names.** The name lookup showed only `fastapi.openapi.models.Tag`, so the reproduction test defined its
   own `class Tag` and failed twice for that reason. The lookup now adds "also in the library: pydantic.Tag", taking
   the libraries from the modules the repository imports most. The reproduce view lists the issue's names that a
   library defines. The example's own classes (`Pie`) read "code of the issue's own example, new code ... or library
   code".
2. **A finished editor reopening its place advanced the route.** The editor opened its own place again after an
   accepted edit, and the tentative next call became real under it ("Nothing to edit now"). Every agent now opens
   with `['open', '<the request you received>']`, and an open of the place just finished does not advance.
3. **The specifier did not see the deciding code.** It wrote "field_annotation_is_scalar, though I don't have it, I
   can infer" and answered `no change`. Two changes:
   - specify shows the place up to 150 lines (the `Annotated` block had been hidden in "lines 371-454 not shown");
   - the deciding calls of 6 lines or fewer are shown whole.

   The answer cap rose from 9,000 to 12,000 characters.
4. **`no change` on a place the review sent back kept the breaking edit.** The view now says that its edit is in the
   patch and that `no change` undoes it. `no change` restores the file when no other edited place shares it.
5. **A repeated `replace` after an accepted edit read "not found".** It now reads "Already applied", with the
   coordinator's line.
6. **Line numbers leaked into specify** through the header ("lines 356-519") and the elision marker. Both are gone.
7. **An unused call's line lacked "<stage> is done"**, so the coordinator asked route.py first (3 calls more). The
   line is now the whole chained line.

| Checklist item | 1 library | 2 open with request | 3 deciding code | 4 no change undoes | 5 already applied | 6 numbers | 7 line |
|---|---|---|---|---|---|---|---|
| W3 code in front | neutral | neutral | closer | neutral | closer | neutral | neutral |
| W6 names | closer | closer | neutral | neutral | neutral | neutral | neutral |
| B1 line numbers | neutral | neutral | neutral | neutral | neutral | closer | neutral |
| B11 doubting tool output | closer: the lookup no longer hides the library class | neutral | neutral | closer | closer: "not found" was false | neutral | neutral |
| B12 contradictions | neutral | closer: the request and the state agree | neutral | closer: what `no change` means is said | closer | closer | closer |
| B9 exploring | neutral | neutral | closer: nothing to infer | neutral | neutral | neutral | neutral |
| B7 long context | slightly farther: one line per library name | neutral | farther: up to 12,000 characters; reason: the decision needs the code | neutral | neutral | neutral | neutral |
| W4 next call | neutral | neutral | neutral | neutral | closer | neutral | closer: one call less |

### gm11b (the 31B, fastapi_12942, stopped at specify): the specifier could not route a change

After the gm11 fixes the library names worked: the 31B imported pydantic's `Tag` and `Discriminator` and the
reproduction test was kept at the first try (422). Specify then wrote a `while` loop that unwraps the outer
`Annotated` in `analyze_param`; its thinking said "field_annotation_is_scalar is in _compat.py, not in analyze_param.
The request says to change analyze_param". It saw where the fault is and had no way to say so: editors and the review
had the `missing` form, the specifier did not. A design gap, not the model's.

**Change:** specify accepts `['missing', '<name of other code>', '<what must change there>']`. The named code joins the
plan before the place (with "Why it is in the plan: the specifier of X says: ..."), is specified by its own
specifier, and then the place is specified again, seeing the other place's sentence ("Why it is specified again"),
where `no change` is the usual answer. The script refuses the place's own name, a name it cannot find, and code
already in the plan (giving that place's sentence), so the form cannot loop. Checked directly on the snapshot
(`analyze_param` → `field_annotation_is_scalar` → `analyze_param` `no change`).

| Checklist item | Status | Why |
|---|---|---|
| W1 choosing from named options | closer | the specifier names code it sees in its view (the deciding calls) instead of being held to one place |
| W3 code in front | closer | the code where the change belongs gets its own view and specifier |
| B12 contradictions | closer | "change this place" against "the fault is in a callee" had no answer; now it has one |
| B9 exploring | neutral | the name must be code the script resolves; no reading form |
| B8 many forms | farther: 3 forms instead of 2. Reason: without it the 31B writes a workaround in the wrong place (gm11b); the form is the editors' own `missing`, same shape |
| W4 next call | neutral | the answer chains as every finished specify |
| F7 leaving a loop | closer | a repeated `missing` for code already in the plan is refused with its sentence |

### gm11c (the 31B, fastapi_12942, stopped at reproduce): a refusal without its cause; the open target

1. **The reproduction test defined its own `Tag`** although the view listed `pydantic.Tag` among the issue's library
   names. Pydantic rejected the union, and the refusal said only "it fails for a reason of its own" with fastapi's
   "Invalid args for response field"; the 31B changed the return annotation and sent the same fault again (two tries
   are all reproduce has). The script now checks, before running the test and without counting a try, whether the
   test defines a class or function with the short name of a library name of the issue, and says so: "The test
   defines its own Tag, but the issue's Tag is the library's pydantic.Tag: delete that definition and import it".
2. **The agents' `open` copied the whole issue** (reproduce sent the statement as the target; 8.7 s of output). The
   prompts' placeholder `<the request you received>` is now what each agent receives: `<the requirement id you
   received, such as R1>` (reproduce, locate, review), `the plan` (scope), `<the place name you received>` (specify,
   edit).

| Checklist item | 1 shadowed library name | 2 open target |
|---|---|---|
| F1 where the error is | closer: the refusal names the definition and the import | neutral |
| F4 challenges without new facts | closer: the second try now has a new fact | neutral |
| B11 doubting tool output | closer | neutral |
| W6 names | closer | closer: the placeholder says what the value is |
| B2 copying long text | neutral | closer: an id instead of the statement |
| B8 many forms | neutral | neutral |

### gm11d (the 31B, fastapi_12942, stopped after requirements): no reproduction for a concrete issue

The two requirements quoted prose ("FastAPI fails to identify it correctly as **body** payload.") and so were not
"concrete", and the route skipped reproduce, although the issue holds a whole example and both requirements named its
`Dessert`. The scripts do not see the statement, but they have the names the requirements agent sent first; a name
there that neither the repository nor a library defines comes from the issue's own example. A requirement that names
one is now concrete as well.

| Checklist item | Status | Why |
|---|---|---|
| B11 doubting tool output | closer | the review gets a failing test to rerun instead of judging prose |
| F2 failing test with expected vs actual | closer | there is a test to show |
| W3 code in front | closer | the issue's example becomes the test the later agents see |
| B9 exploring | neutral | no new form |

Generic (the user's rule of 10-15, checked for the four changes above): the specifier's `missing` takes any code the
script resolves; the shadow check applies to every library name the issue uses; the open targets describe what each
agent receives; "concrete" uses any name of the issue's own example. None names a task, a library or a class.

### gm11e (the 31B, fastapi_12942, stopped at the second specify): the `missing` form worked; the callee was hidden

The fixes of gm11c-gm11d worked: the reproduction test imported `pydantic.Tag` at the first try and was kept (422),
the agents opened with `R1` and the place name, and the specifier of `analyze_param`, seeing the call
`field_annotation_is_scalar`, sent `['missing', 'field_annotation_is_scalar', 'It needs to handle Annotated types,
possibly by unwrapping them ...']`. The script added it to the plan and the route specified it next. That specifier
saw the one-line wrapper but only the signature of `field_annotation_is_complex` (11 lines; the cap for a whole body
was 6), reasoned "if field_annotation_is_complex handles Union, it returns True", and answered `no change`. The
reference patch is three lines in `field_annotation_is_complex`.

**Change (generic):** the deciding callees' bodies are shown whole within a line budget instead of a fixed 6-line cap:
60 lines when the place is 20 lines or shorter (a wrapper delegates its decision), 24 otherwise; each body at most 30
lines; the smallest are chosen first so that more of them are whole, and they keep the call order. On the snapshot:
`field_annotation_is_scalar`'s view now holds `field_annotation_is_complex` whole; `analyze_param`'s holds
`is_uploadfile_or_nonable_uploadfile_annotation` and `field_annotation_is_scalar` whole (the 13-line
`is_uploadfile_sequence_annotation` keeps its signature).

| Checklist item | Status | Why |
|---|---|---|
| W3 code in front | closer | the code the place delegates its decision to is in the view |
| B9 exploring | closer | nothing to guess; the specifier has no reading form |
| B11 doubting tool output | neutral | |
| B7 long context | slightly farther: up to 60 lines more for a short place (whose own view is short); 24 for a long one. Reason: the decision needs that code |
| P7 unneeded context | neutral: only callees in a condition or run by the reproduction test |

Generic: it applies to every place and callee by size alone.

### gm11f (the 31B, fastapi_12942): resolved

The second task the competition's model resolves with this system (judged: 1/1 FAIL_TO_PASS, nothing broken,
official_feasible); the patch is the reference's three lines. 50 model calls, 25 tool calls, about 19 minutes of
wall time with the gate's pauses. What the fixes of gm11-gm11e did in this run:
- reproduce: the precheck caught a test that defined its own `Tag` and the 31B imported `pydantic.Tag` without losing
  a try; the test was kept (422);
- scope added `field_annotation_is_scalar`; its specifier saw `field_annotation_is_complex` whole (the line budget)
  and sent `['missing', 'field_annotation_is_complex', 'add a check for origin is Annotated and return
  field_annotation_is_complex(get_args(annotation)[0])']`;
- that place was specified with the reference's change; the two other places were re-specified as `no change`;
- review R2 `done`, R1 closed by the script (its reproduction passes, regressions clean), submit.

Faults left, fixed after the run (generic):
1. The first test imported `Tag` and `Discriminator` from `fastapi.openapi.models` (the repository's classes of the
   same names) and the refusal gave only "BaseModel.__init__() takes 1 positional argument". A refusal for "a reason
   of its own" now adds, for every name the issue uses from a library that the test imports from another module,
   "It imports Tag from fastapi.openapi.models; the issue's Tag is the library's pydantic.Tag".
2. One call had `skill_name` `「fix-issue」` ("Skill not found"); the 31B recovered by itself. Every prompt now says
   what that error means, next to the ADK's "Argument ... is required".
3. The judge cached reference runs that did not finish (a snapshot missing once), which left the task unscorable in
   every later run; such runs are no longer cached, and a cached one is recomputed.

| Checklist item | 1 import hint | 2 skill not found |
|---|---|---|
| F1 where the error is | closer | closer |
| F4 challenges without new facts | closer: the retry gets a fact | neutral |
| F8 harness errors | neutral | closer |
| B8 many rules | neutral | slightly farther: one sentence more per prompt; reason: V10 lost 44% of calls to this error |

Generic: the hint uses any library name of the issue; the prompt line covers any mangled skill name.

### gm12 (the 31B, rich_3052, stopped at step 37: the Colab tunnel died, HTTP 530)

Up to the tunnel's end the run was clean:
- four requirements quoted from the issue;
- a reproduction test kept at the first try (`TypeError: ... unexpected keyword argument 'case_sensitive'`);
- locate chose `PromptBase.ask`, plus `PromptBase.process_response` for the matching;
- scope added `PromptBase.check_choice`;
- the specifier of `ask` sent `['missing', 'PromptBase.__init__', 'Add case_sensitive: bool = True ... and set
  self.case_sensitive = case_sensitive.']`, the reference's change there.

The specifier of `PromptBase.__init__` then read its note, "the specifier of PromptBase.ask says: Add ...", as a
change meant for `ask` ("This is confusing. PromptBase.ask is likely a class method, so self.case_sensitive ... doesn't
make sense there"). It ended its turn with no call and no text; the coordinator's step 3 sends that back to route.py,
which asks again.

**Change (generic):** the note now says whom the change is for: "Why it is in the plan: the specifier of X named
PromptBase.__init__ for the change; what it says must change here: ...".

| Checklist item | Status | Why |
|---|---|---|
| B12 contradictions | closer | the note no longer reads as an instruction about another place |
| W6 names | closer | it names the place the change is for |
| B8 many rules | neutral | |

Generic: the wording holds for every place a specifier names.

### V22: the stages build on Kaggle with 5 minutes per task (10-15): 1/10

The first Kaggle eval of the stages build, after the time limit went back to 5 minutes (S6, with 60, got no score).
Only requests_7328 was resolved; the other nine stopped at 5 minutes with an empty patch.

| Task | Last agent at the stop | Model calls | Seconds per agent |
|---|---|---|---|
| fastapi_14448 | specify | 25 | requirements 47, locate 83, scope 5, specify 60 |
| fastapi_14583 | specify | 25 | requirements 15, reproduce 63, locate 57, scope 5, specify 68 |
| fastapi_14851 | reproduce | 9 | requirements 31, reproduce 225 (the environment fault below) |
| fastapi_14986 | specify | 27 | requirements 20, reproduce 57, locate 37, scope 14, specify 70 |
| fastapi_15800 | specify | 23 | requirements 37, reproduce 84, locate 39, scope 8 |
| httpx_3672 | specify | 35 | requirements 46, reproduce 30, locate 101, scope 4 |
| requests_7328 | review (resolved) | 30 | requirements 17, locate 11, scope 3, specify 125, edit 26, review 53 |
| rich_3006 | specify | 22 | requirements 26, reproduce 176, locate 22, scope 15, specify 12 |
| rich_3469 | specify | 19 | requirements 35, locate 33, scope 10, specify 117 |
| rich_3521 | review, no edit kept | 23 | requirements 13, locate 12, scope 4, specify 179 |

What it shows:
- **The budget is tokens, not steps.** vLLM on Kaggle's 4×L4 generated 28-38 tokens/s; every task used about 9,000
  output tokens (thinking included) in its 5 minutes. On Colab's RTX PRO 6000 the resolved runs needed 3 min 49 s
  (rich_3894) and 7 min (fastapi_12942) of model time at about twice that speed.
- **Specify and reproduce are the long agents** (specify up to 179 s, reproduce up to 225 s): both have the largest
  thinking budgets (specify 4096) and the largest views.
- **An environment fault of our own Kaggle eval** cost the five fastapi tasks: swegemma's `_host_env.pth` lists only
  paths with "site-packages", Kaggle's Python keeps its packages in `dist-packages`, which the sandbox venv adds after
  its own .pth files, so `workspace_paths.pth` put `fastapi/` before them and langsmith's pytest plugin imported
  `fastapi/requests.py` as `requests`. Every reproduction test failed to collect and the judge's reference runs did not
  finish (unscorable). `tools/eval_env.py` now lists the host package directories first; the official order (the
  workspace after the installed packages) is kept. Not a fault of the submission.

Not decided yet (the user's): how the procedure meets 5 minutes. A time-aware route (skip the optional reproduce and
review when little time is left, one locate for all requirements) and smaller thinking budgets for the agents are the
two levers this run measures.

#### Where V22's time went (all 238 model calls of the 10 tasks)

Method: each trace step's `elapsed_s` and token counts (`results_v22/traces`), and vLLM's own log. A call's wall time is
the time since the previous event: the previous tool's run, the prompt's prefill and the generation. Generation is
estimated at vLLM's measured 36.6 tokens/s (median while one request runs; p10 27.8, p90 37.9).

- **Generation is 91% of the time** (2,533 of 2,777 s). Prefill is negligible (2-4k prompt tokens per call, a prefix
  cache hit rate of 79%). The scripts' own work (tests, the reproduction runs, the index) is inside the other 9%.
- **About 80% of the generated tokens are thinking** (about 74k of 93k; estimated from the length of the thought text).
- **vLLM ran one request at a time with 2% of its KV cache in use**: decoding is memory-bound, so concurrent requests
  would cost almost nothing in speed. The procedure is strictly sequential.

By kind of call:

| Kind | Calls | Output tokens | Wall time | Tokens per call |
|---|---|---|---|---|
| An agent's decision (the useful work) | 60 | 47,124 | 1,412 s (51%) | 785 |
| An agent's final reply, passing the coordinator's line on | 52 | 17,040 | 533 s (19%) | 328 |
| Refused, an ADK error, or a call after the part was done | 9 | 11,258 | 319 s (11%) | 1,251 |
| An agent's `open` | 42 | 10,140 | 293 s (11%) | 241 |
| The coordinator | 75 | 7,162 | 221 s (8%) | 95 |

By agent: specify 708 s (30 calls), reproduce 670 s (22), locate 425 s (53), requirements 393 s (31), scope 269 s (20),
the coordinator 221 s (75), review 61 s, edit 31 s.

Findings:
1. **The ceremony of an agent call costs about 21 s**: its `open` (241 tokens), its final reply (328 tokens, almost all
   thinking about copying one line) and two coordinator calls (95 each). It is 38% of all the time (1,047 s): about
   100 s per task, a third of the 5 minutes. Locate is called once per requirement (53 calls).
2. **Thinking fills the budget on every hard decision**: each specify decision produced 2,084-2,181 tokens (the
   2,048 budget plus the call) and took 57-60 s. The time of a decision is set by its agent's thinking budget, not by
   the decision.
3. **A thinking budget that ends mid-thought can run away**: in fastapi_14851 reproduce's thinking was cut by the
   budget, the model went on reasoning in the visible text, wrote the call as text in the middle of it and kept going
   to `max_output_tokens` (8,192 tokens, 225 s, no call parsed). The visible output has no cap of its own below 8,192.
4. **Reproduce is the most expensive optional stage**: 670 s over the 7 tasks that ran it (up to 225 s in one task).
5. The fastest path that resolved (requests_7328: no reproduction, locate 11 s, scope 3 s, specify 125 s, edit 26 s,
   review 53 s) still took more than 4 minutes.

Levers, each generic, with what V22 says it would save (to be measured with the model, not assumed):

| Lever | Saving in V22's terms | Risk for the 31B |
|---|---|---|
| Fewer agent calls per task: one locate call for all requirements; stages merged where one view serves both (requirements with locate, specify with edit for a single place) | about 21 s per agent call removed | a larger view per decision (checklist B7, B8) |
| Smaller thinking budgets where the decision is bounded (specify 2048, the agents' 1024-2048) | about 28 s per specify decision for a halved budget | a worse sentence; measure resolve rate |
| `max_output_tokens` of each agent near its largest legitimate answer (a whole definition or a test: about 2,000 tokens) instead of 8,192 | up to 160 s in a runaway call | a long whole-definition edit cut |
| A time-aware route: no reproduction, and review only when time is left | up to 225 s per task | the review's check and the reproduction evidence are lost under pressure |
| Concurrent calls (ParallelAgent for independent places or requirements) | the duration of the longest branch instead of the sum | ParallelAgent branches share /workspace; whether the scorer runs tasks concurrently is unknown |

### The time fixes after V22 (10-16, the user's: "haz todos esos arreglos y prueba")

1. **One agent call per stage, not per target.** When the route's next call is the same stage again, the agent goes
   on in the same call ("Your part goes on with R2, in the same way:" and that view). This applies to locate and
   review (up to 6 targets) and to specify (up to 2 places, so that two views of up to 12,000 characters stay under
   the harness's compaction). Each target saved removes an open, a final reply and two coordinator calls (about 21 s
   in V22). The prompts of the three agents say the script may show the next target. Edit stays one place per call
   (its tentative-route logic and the editor's fresh context).
2. **A time-aware route.** Reproduce runs only when at least 6 minutes are left (V22: 30-225 s, and the rest needs the
   remainder); review agent calls only when at least 75 s are left (otherwise the script's own checks stand). Under
   the 5-minute limit reproduce is therefore off; with a larger budget it comes back by itself.
3. **No scope call when there is nothing to choose.** If the located places have no related code, the plan is made
   without the agent.
4. **Thinking budgets and output caps per agent.** V22's hard decisions always filled the budget: the time of a
   decision is the budget. Specify, reproduce and requirements now have thinking 1024 and at most 3,072 output tokens
   (they had 2048 and 8,192); edit 1024 and 4,096 (a whole definition needs the room); locate, scope and review 512 and
   2,048 (they had 1024 and 4,096). The cap bounds a runaway like V22's 8,192-token call (225 s).
5. **The final reply says there is nothing to decide** ("There is nothing to decide: copy it."): V22's final replies
   thought 328 tokens on average to copy one line.

Not done: concurrent agent calls. The scripts take a lock on the task's state, so two calls at once are refused by
design (one decision at a time on one state), and the coordinator's calls are sequential.

Dry run on fastapi_12942: the route skipped reproduce under 4 min 30 s, locate went from R1 to R2 in one agent call,
and specify went from `analyze_param` (`missing`) to `field_annotation_is_scalar` in the same call, then returned to the
coordinator after two places.

| Checklist item | 1 one call per stage | 2 time-aware route | 3 no empty scope | 4 budgets and caps | 5 nothing to decide |
|---|---|---|---|---|---|
| W4 next call | neutral: the view ends with the agent's own NEXT as before | neutral | closer: a call less | neutral | closer |
| W7 room to think | neutral | neutral | neutral | farther: half the thinking for every agent. Reason: V22 showed the budget, not the decision, sets the time, and nothing was submitted in 9 of 10 tasks | neutral |
| B7 long context | slightly farther: two specify views in one context. Reason: 21 s per place; bounded at 2 | neutral | neutral | neutral | neutral |
| B10 knowing the budget | neutral | closer: the route spends time where it is left | neutral | neutral | neutral |
| B12 contradictions | closer: the prompts say the next target may come | neutral | neutral | neutral | neutral |
| F2 failing test | neutral | farther: no reproduction test under 5 minutes. Reason: it cost up to 225 s of the 300 | neutral | neutral | neutral |
| F7 leaving a loop | neutral | neutral | neutral | closer: a runaway stops at 2,048-4,096 tokens | neutral |

Generic: every rule is a count of targets, a time left or a token budget; none names a task or a repository.

### V23 (10-16): the time fixes on Kaggle, 3/10

requests_7328, fastapi_14851 and rich_3006 resolved (judged); 6 of 10 tasks had a patch (V22: 1). The fastapi tasks
were scorable again after the eval_env fix (fastapi_14583 still unscorable).

| Kind of call | V22 wall time | V23 wall time | V22 tokens/call | V23 tokens/call |
|---|---|---|---|---|
| Decisions | 1,412 s (51%) | 1,822 s (69%) | 785 | 663 |
| Final replies | 533 s (19%) | 235 s (9%) | 328 | 114 |
| Opens | 293 s (11%) | 227 s (9%) | 241 | 185 |
| Coordinator | 221 s (8%) | 276 s (10%) | 95 | 118 |
| Refused, errors, unused | 319 s (11%) | 74 s (3%) | 1,251 | 642 |

By agent, specify took 47% of the time (77 calls). Three causes, each fixed generically after the run:
1. **Copies of the same code** (httpx_3672: the same methods in `src/httpx/` and `src/ahttpx/`, six places specified
   and edited one by one). A place whose original code is the same as another place of the plan with the same
   qualified name takes that place's sentence without a specify call, and that place's accepted edit is made in it
   too (with the imports the edit added), when it still has the shared original code and the file still parses.
2. **A refused `missing`** (rich_3521): the specifier of `split_cells` named `_split_cells`, which another specifier
   had left `no change`; the script refused it as "already a place of the fix" three times (about 90 s). A place that
   is finished (no change, edited or failed) named by `missing` is now specified again with the note.
3. **A chain of `missing`** (fastapi_14986): `get_openapi` named `OpenAPI`, which named `Server`; both ended `no
   change`, and the sentence for `get_openapi` came at 298 s. Code that was itself named by a specifier names no
   further code.
Also: specify handles up to 3 places in one agent call (the ceremony is now about 12 s a call).

Dry run on httpx_3672: the `ahttpx` copy of `HTTPParser.__repr__` took the sentence of the `httpx` one and got its
edit, with no agent call for either.

| Checklist item | 1 copies | 2 re-open by missing | 3 no chain | specify 3 |
|---|---|---|---|---|
| B9 exploring | neutral | neutral | closer: no browsing through `missing` | neutral |
| B12 contradictions | neutral | closer: a finished place can be named, as the prompt says | closer: the refusal says why | neutral |
| W4 next call | closer: fewer calls | closer: no refused calls | closer | closer |
| B7 long context | neutral | neutral | neutral | slightly farther: up to 3 views; reason: 12 s a call, and V23's views were shorter |
| B11 doubting tool output | slightly farther: a copied edit is checked only by parsing and the later review. Reason: the copy is the same code with the same sentence | neutral | neutral | neutral |

Generic: a copy is decided by identical original code and qualified name; the other two rules hold for any place.

### Can the per-task limit be longer? (10-16, the user's question)

**The rule (the competition's Evaluation page, read 10-16):** "Your agent has a limit of 12 hours to submit patches for
all tasks, inclusive of sandbox setup time, but excluding time for patch validation." The test set has "about 120
tasks" (Data page). The harness runs one task at a time by default (`swegemma` `EvalConfig.concurrency = 1`; the
official `inference.py` is not public, so sequential is the assumption). S6, with 60 minutes per task, finished
without a score.

**Measured on Kaggle's 4×L4 (V23):**
- fixed cost of the run: vLLM ready after 442 s, wheels 66 s, about 8.5 minutes;
- per task: the agent stops at the limit plus up to 16 s (the running model call finishes: durations 302-316 s for a
  300-s limit); our subprocess sandbox's own setup and teardown are 3-20 s (the official Docker container's setup is
  not known, and counts);
- 8 of 10 tasks used the whole limit (mean 289 s of 300).

**The limit that fits** (12 h, every task using the whole limit, 15 s of overrun):

| Tasks | Setup per task | No margin | 5% margin |
|---|---|---|---|
| 120 | 9 s (ours) | 5.5 min | 5.2 min |
| 120 | 30 s | 5.2 min | 4.9 min |
| 120 | 60 s | 4.7 min | 4.4 min |
| 125 | 9 s | 5.3 min | 5.0 min |
| 125 | 30 s | 4.9 min | 4.7 min |
| 130 | 30 s | 4.7 min | 4.5 min |

**Conclusion:** 5 minutes is already at the edge. The most it could grow is about 5.25 minutes, and only if the
official setup is as short as ours and there are no more than 120 tasks; with 30 s of setup or 125 tasks, 5 minutes is
already over a 5% margin. Lengthening it risks the whole score (S6) for at most 5% more time per task. The time must
come from the procedure (V22 → V23: decisions from 51% to 69% of the time), not from the limit.

### V25: the recommended block of the six agents' proposals (10-16, the user's: "me voy con tus recomendaciones")

From `docs/time_proposals/` (where six independent agents agreed):
1. **The coordinator thinks less**: thinking 256 and at most 1,024 output tokens (it had 512 and 4,096). V23's
   coordinator calls had a median of 89 tokens; the long ones (300-430) were doubts about a line.
2. **A literal sentence is applied by the script**: a sentence that is one replacement ("replace `A` with `B`", 6 of
   V23's 15 sentences) is made by the script through the editor's own checks (whole new definition, syntax, tests,
   undo), when A occurs once in the place; the editor agent is called only when that fails. The specify answer says
   so ("the script makes this replacement").
3. **`missing` naming the place itself is its sentence** (rich_3521 in V23 named its own place with the change).
4. **No scope agent.** The plan is the located places and their identical copies (other "same pattern" or twin places
   are no longer added: in V22-V23 they were never edited); each specifier's view lists the related code (callers,
   overrides, copies, with the linking line, without line numbers) and the specifier names what must change too with
   `missing`. The agent is gone from `agent.yaml`, the coordinator's prompt and SKILL.md.
5. **The main place first**: the place chosen first for the first requirement is specified and edited (or applied
   literally) before the other places are specified, so that a patch exists early (V22-V23: the first diff came at
   about 240 s).
6. `tools/kaggle_eval.py --bench-concurrency` measures vLLM with 1, 2 and 4 requests at once before the tasks (the
   check the parallel-specify proposal needs).

Dry run on fastapi_12942: locate went straight to specify (no scope), the specify view listed the six related functions
without line numbers, and a literal sentence for `analyze_param` was applied by the script and routed to review with no
editor call.

| Checklist item | 1 coordinator 256 | 2 literal by script | 3 missing on self | 4 no scope | 5 main first |
|---|---|---|---|---|---|
| W4 next call | neutral | closer: a call less | closer: no refusal | closer: an agent less | neutral |
| W7 room to think | slightly farther: reason: routing needs no deliberation; the long calls were doubts | neutral | neutral | neutral | neutral |
| W3 code in front | neutral | neutral | neutral | closer: the related code is in the specifier's view, next to the place's code | neutral |
| B8 many forms | neutral | neutral | neutral | neutral: missing already existed | neutral |
| B11 doubting tool output | neutral | neutral: the same checks as an editor's edit | neutral | neutral | neutral |
| B12 contradictions | closer: the answer says who makes the change | neutral | closer | neutral | neutral |
| F5 what is done | neutral | neutral | neutral | neutral | closer: a patch exists earlier |

Generic: every rule depends on the plan's structure, the form of a sentence or the time; none names a task.

### V24 (10-16): 2/10

fastapi_14851 and rich_3006 resolved; 7 of 10 tasks had a patch (V23: 6), 6 stopped at 5 minutes (V23: 7). requests_7328,
resolved in V22 and V23, was stopped with a wrong patch. Specify fell from 77 calls (47%) to 64 (42%); refused or unused
calls from 4 to 2. Decisions stayed at 69% of the time; the coordinator rose to 12% (79 calls, 137 tokens each). With
10 tasks at temperature 1.0 a difference of one task is noise: V23 and V24 are not distinguishable by resolve count.

### V25 (10-16): 4/10, submitted as S7

requests_7328, fastapi_14851, httpx_3672 and rich_3006 resolved; 9 of 10 tasks had a patch and only 2 stopped at 5
minutes (V24: 7 with a patch, 6 stopped). Decisions 67% of the time; specify 44%; the coordinator 12% (90 calls).

**vLLM concurrency on Kaggle's 4×L4** (`--bench-concurrency`, 300 tokens per request, no thinking):

| Requests at once | Total tokens/s | Per request |
|---|---|---|
| 1 | 39.8 | 39.8 |
| 2 | 76.4 | 38.2 |
| 4 | 143.3 | 35.8 |

Four requests at once cost each only 10% of its speed: the parallel-specify proposal of `docs/time_proposals` (B1, D5,
E1) would cut the specify stage of a plan with several places by up to about 3.5 times. It needs one state file per
slot (the scripts lock one state) and ParallelAgent as an agent_tool; not built yet.

### Screening tasks that can be scored (10-16, the user's: "Lanza otras 30 diferentes, pero asegúrate de que puedan ser puntuadas")

V25-30 lost 8 of its 30 tasks to the environment (fastapi test files importing inline_snapshot and dirty_equals,
absent from the Kaggle image). Whether a task can be scored depends on the environment, so it is checked where the run
happens, before the agent runs: `judge.screen` judges the empty patch of each candidate (the reference twice, the
original code twice, cached for the later judgement) and keeps a task only when the reference ran, it passes the
harness's own whole-file check (`official_feasible`) and at least one test fails without it (FAIL_TO_PASS). Candidates
come in a deterministic order that keeps each repository's share at every prefix (`judge.screen_order`, seed 1), so
the first 30 that pass are a stratified sample and Kaggle and the local run screen the same order.
`kaggle_eval.py --screen 30 --exclude <ids>` and `local_eval.py --screen 30 --exclude <ids> --fetch-snapshots
--screen-only` (the snapshot of a task not chosen is deleted again: the local disk holds about 7 GB).

A fault found while writing it: with several tasks at once (`--concurrency 4`, V25-30) the judge took the last JUnit
report captured after its mark, which could be another task's run. Captures now carry a tag set per context
(`contextvars`: each asyncio task and each `to_thread` call keeps its own), and a mark is `(index, tag)`. V25-30's
judgements may have used a wrong report for some tasks; its numbers stand as recorded, with that caveat.

This changes only the evaluation tools, nothing the model reads: no checklist table.

### hk30: the V25 build with Haiku on 30 screened tasks (10-16), why the unresolved ones failed

Haiku through tools/anthropic_proxy.py, the V25 build (= S7), 30 tasks chosen by `judge.screen` (all scorable here,
none of V25-30's). Batches 1-2: 9/20 resolved, every task under 5 min (52-256 s). Not Kaggle evidence. The 11
unresolved tasks of batches 1-2 were analysed by four independent agents from per-task dossiers (statement, reference,
agent patch, failing tests, every call and answer); their reports are in `docs/analysis_hk30/`.

| Task | First wrong stage | Info in the statement | The system contributed |
|---|---|---|---|
| rich_4076 | locate | no (title only) | no |
| fastapi_15661 | — | no (title only; tests pin a new script's names) | no |
| fastapi_15280 | — | no (the reference is a joke exception) | no |
| rich_4075 | requirements, then specify "no change" | partly | yes: an empty requirement accepted; a parameter not tied to its function |
| fastapi_9753 | locate/plan | yes | yes: siblings reading the same helper shown as "does not call them"; review shows only callers |
| fastapi_13713 | edit | partly | yes: the signature hidden ("parameters, lines 65-822, not shown"), "Import them" for what must be a parameter, `missing` accepted for a non-name (loop) |
| rich_3934 | requirements (title only), then locate | no | yes: reproduce off with 187 s unused; review's "not done" sends a "no change" place back to specify, not to locate |
| fastapi_14953 | locate | partly | yes: library producers not candidates; review cut the evidence ('bina...') and framed it as "behaviour the issue keeps" |
| rich_3043 | locate (the script) | partly | yes: a module-level constant is never a place; refused edits count toward MAX_EDITS (`p['edits'] += 1` before the apply) |
| fastapi_14372 | locate | yes | yes: R1 names two classes, one planned; the subclass not related code; review "cannot tell" goes to submit |
| fastapi_14361 | locate (the script) | yes | yes: a text fragment resolves silently to the first function holding it; review cannot send back to locate; its message says "goes back" and the route submits |

Patterns (generic, by count):
- Locate is the first wrong stage in 7 of 11; in 3 of them the script decided (module-level code not placeable, text
  fragment resolved to the first hit, library producer not a candidate).
- No route back from review to locate: a "not done" on a place specify keeps as "no change" loops or submits (3 tasks).
- Weak checks after the edit: "Kept, not checked" and review's "cannot tell" go straight to submit (4 tasks).
- Title-only statements: an empty requirement is accepted and reproduce is off at 5 min (`REPRO_LEFT_S = 360`), although
  Haiku used 52-256 s (3 tasks); 3 tasks are not solvable from the statement at all.
- Script faults to fix: module-level assignments as places; refused sends not counted as edits; a text search lists every
  function holding the text; edit windows never hide a signature's parameters; "declare it as a parameter" instead of
  "import it" when the undefined name is the def's own; `missing` only for code names; review evidence not cut
  mid-value; test selection by bare name (`main`) counted as evidence.

#### The hk30 proposals through the checklist (10-16, the user's: "pasa esa propuesta por el checklist")

Closer (+), farther (−), neutral (=) per item of `docs/llm_checklist.md`; only the items each change moves are listed.

| # | Change | Items | Why generic | Verdict |
|---|---|---|---|---|
| 1a | Module-level assignments (constants, templates) are places: chosen by name, shown whole, edited as a whole `NAME = ...` | W1 + (a found name can be chosen), W2 + (whole unit), W3 + (the text that must change is shown), B1 + (no line range for module code), B8 = (same forms) | every repository has module-level constants and templates | do |
| 1b | A refused send does not count toward MAX_EDITS | F6 + ("refusals stay off the failure count"), F7 + | about the counter, not about the code | do |
| 1c | A text fragment gives the list of every function and class holding it, with the matching line, ranked by the requirement's words, instead of the first hit silently | W1 +, B11 + (no hidden decision), B13 + (best first), B9 = (the list is the answer, not a new read form), B8 = | any text in any repository | do |
| 1d | Edit windows never hide parameters: long default/doc text inside a signature is folded to `...`, every parameter line stays | W3 +, B16 + (no guessing the hidden part), B1 + (no line guessing), B7 − (a longer window; bounded by folding the docs text) | long signatures exist in any library | do |
| 1e | The undefined-name check states the fact (not defined in the def, not imported in the file) and gives both ways: a parameter of the def or an import | F9 + (fact first), F1 + (where), B12 + ("import it" contradicted a parameter the sentence asks for) | any undefined name | do |
| 1f | `missing` takes only a code name (or a new file path); anything else gets the place's code again with the forms | B6 + (a misused form answered with the right call), F7 + (the loop ends), B5 = (described, not quoted) | about the form | do |
| 1g | Review evidence keeps whole values (expected vs actual never cut mid-value) and is labelled as a fact: "passed before, fails now", without "behaviour the issue keeps" | B11 +, F2 + (expected vs actual), F4 + (no framing that pushes a reconsideration), B12 + | any test output | do |
| 1h | Tests count as evidence for a place only when the call map shows they call it, not by a shared bare name | B11 +, F3 + (no false OK) | about test selection | do |
| 2a | Review's "not done" on a place that specify kept as "no change" sends the requirement back to locate, without the places already tried, once per requirement and only with time left | W4 + (route agrees with the message: today it says "goes back" and submits), B12 +, B10 = (gated by time left), B9 − (one more search; bounded to once, time-aware) | about the route | do |
| 2b | Review's "cannot tell" with time left runs a check (the reproduction test if one exists) before submit | F3 +, B11 +, B10 − (time: Gemma on Kaggle has little to spare) | about the route | measure on Gemma first |
| 3a | When a requirement names several definitions of the repository and only some are planned, locate's answer lists the ones left out to choose from | W1 +, W6 + (the statement's own names), B11 +, B8 = (same choice form), P7 − (more names shown; limited to those the requirement names) | names taken from the requirement | do |
| 3b | Subclasses that re-declare the class (decorators, overrides) and siblings that read the same helper are related code; the label says what they share ("reads the same _helper") instead of "does not call them" | W3 +, B12 + (the old label invited a local fix), P7 − (more related code; only same-helper readers and direct subclasses) | structure of any code base | do |
| 4a | The requirements step refuses a requirement with no situation or no expected result (described, not quoted) and ties every name to its owner ("end: a parameter of Console.print") | W6 +, W3 +, F9 + (fact), B5 = (described), B8 = | any statement | do |
| 4b | Reproduce runs when time is left at the point it would start, not only with 6 min left (today never at 5 min) | F2 +, F3 +, B11 +, W5 + (pytest output), B10 − (time: V22 reproduce took 30-225 s with Gemma), B9 − | any task | measure on Gemma first |
| 4c | A requirement about something that is not code (packaging, Python versions) can be marked "not code" in locate | B8 − (one more form; reason: two wasted steps in rich_4075), W1 + | any statement | defer: one task, and a new form |
| 4d | Library code the repository re-exports becomes a candidate | W1 +, P7 − (distractors from large libraries), B13 − (longer list) | any library | defer: the distractor risk is not measured |

Moving farther on an item needs a reason: 1d (B7), 2a (B9), 3a/3b (P7) are bounded as written; 2b and 4b move farther on
B10, the item that cost the most with Gemma on Kaggle (V22), so they wait for a Gemma measurement; 4c and 4d are
deferred.

#### V26: the "free" hk30 changes (10-16, the user's: "implementa lo que salga gratis")

"Free" = no checklist item farther (the table above): 1a, 1b, 1c, 1e, 1f, 1g, 1h and the owner half of 4a. Not built:
1d (B7 −), 2a (B9 −), 3a/3b (P7 −), 2b/4b (B10 −: measure on Gemma first), 4c/4d (deferred); the refusal half of 4a
(an empty requirement refused) is not free either: no deterministic test tells an empty requirement from a short
one, and a wrong refusal costs a call (F4).

- 1a `step.assignment`/`assignment_span`: `_resolve` finds a top-level assignment (`NAME = ...`, `NAME: T = ...`)
  when no function or class has the name, in the file named first; `_refresh` follows it; the place view shows its
  string whole (not collapsed); the edit takes the whole new assignment (one statement assigning that name) or the
  replace form.
- 1b refused sends (nothing applied) are counted apart (`refused`, at most 6), not as edits (at most 4).
- 1c locate: a text fragment (not a name) is searched and every function holding it listed; `class Depends` / `def f`
  is the name.
- 1e the undefined-name check gives both ways: a parameter of the place, or an import.
- 1f edit's `missing` must name code (a definition, a new member of an existing owner, or a .py path); otherwise the
  form is described and the replace / whole forms offered.
- 1g review: failing existing tests are facts; "a test that checks a value the issue changes fails as expected; a test
  of behaviour the issue keeps must keep passing"; a second `done` is accepted as before.
- 1h the review's regression check measures whether the passing tests run the changed lines ("but none of them runs
  the changed lines").
- 4a requirements: a name that is a parameter is shown with the functions that take it, the most called first.

Checked: replays of rich_3043, rich_4075 and fastapi_14361 (hk30 traces) on the V26 build: no traceback or internal
error; in rich_3043 locate now chooses CONSOLE_HTML_FORMAT (`rich/_export_format.py`, lines 1-18) at the first call
that named it. Model run: the 30 hk30 tasks with Haiku on V26 (row hk26 in VERSIONS.md).

### Which tasks are slow, and the 15-minute time trial (10-16, the user's question)

The 12 h are for all tasks together; `max_time_minutes` is a cap per task that we set, so a task that ends early leaves
time for others only if the cap is raised. Data: the Gemma Kaggle runs of the stages build (V23, V24, V25, V25-30,
V25-30s: 90 task runs; `scratchpad/slow.py`).

- 60 of 90 ended at the cap (≥ 295 s), 30 under it. Resolved: 14 of the 30 under the cap, 8 of the 60 at the cap
  (the harness takes the diff at the stop). The 22 resolved took 154-335 s (median about 265 s); 8 of them at the cap.
- **Not the vague statements.** Title-only statements (fewer than 25 words after the title, PR-template text removed)
  reach the cap less often: 54% (39 runs) against 76% (51 runs). Longer bodies give more requirements.
- **The number of requirements.** 1 requirement: 56% at the cap, 18/62 resolved; 2: 90% at the cap, 3/20; 3 or more:
  6 of 7 at the cap, 1/7 resolved. Each requirement adds a locate and a review, and its places a specify and an edit.
- **The size of the fix.** Reference patches of up to 20 changed lines: about half at the cap, 18/46 resolved; over 20
  lines: 82% at the cap, 4/44 resolved.
- Per agent, the capped runs spent more in every stage, most in specify (120 s against 81 s on average).
- Haiku on the same 30 tasks (V25-30s): median 94 s where Gemma was capped, 82 s where it was not; Haiku resolved 8 of
  the 20 Gemma capped.

**The trial (V25-T15):** the V25 build unchanged except `max_time_minutes: 15`, `BUDGET_S` 14 min 30 s and
reproduce kept off (`REPRO_LEFT_S` out of reach, so the procedure is the one of the 5-minute runs), on the 30 tasks of
V25-30s (5/30 at 5 min), 4 at once on Kaggle. It measures each task's natural duration and what more time resolves;
from it, for a cap C, the 12 h total Σ min(C, tᵢ) + setup over about 120 tasks and the tasks resolved by time C.

**How the 90 runs ended, and what the cut ones resolved** (10-16, the user's question "de las tareas que cortamos
cuántas se resolvieron?"; the submit_patch call's time in each trace):

| Ending | Runs | Resolved | Empty patch |
|---|---|---|---|
| The harness cut at 5 min (no submit_patch) | 44 | 1 | 34 |
| Our route submitted at its 4:30 stop | 16 | 5 | 0 |
| Submitted before 4:30 | 30 | 16 | 1 |

In the 44 harness cuts a sub-agent was working at 4:30 (specify 24, edit 12, other 8): the 4:30 stop is checked only
when the coordinator calls `route.py`, so a batched sub-agent call runs past it. 36 of the 44 had no edit kept: the
time went to requirements, locate and specify, and nothing was left to submit.

**V26 with Haiku (hk26):** 15/30, as V25 (hk30), with three tasks exchanged. The three gained are the three the
changes were for, and each used its change: rich_3043 chose CONSOLE_HTML_FORMAT and edited it as a whole assignment
(62 s), fastapi_14361 chose from the text-search list, rich_4075 saw `end` as a parameter of `print` / `Console.print`.
The three lost (fastapi_14419, rich_3718, rich_3180) went another way before any new text was shown (other places
located or specified; rich_3718 saw none of the new texts): sampling at temperature 1.0, not the changes. In
fastapi_14419 and rich_3180 the review then saw the new 1g wording with failing tests and answered done / cannot tell;
the judge finds no existing test broken by the final patch in either, so the failing hidden tests are the wrong fix
itself.

#### The time trial's result (V25-T15, 10-16)

10/30 resolved with 15 minutes, against 5/30 with 5 (V25-30s, the same tasks and build). Natural durations: mean
519 s, median 488 s, 154-907 s; 4 tasks hit 15 minutes; empty patches 5 (V25-30s: 18). The resolved tasks finished
at 154-516 s; none after 8 min 36 s.

| Cap | Resolved by then | Mean time per task | 120 tasks, 9 s setup | 120 tasks, 30 s setup |
|---|---|---|---|---|
| 5 min | 5 | 298 s | 10.4 h | 11.1 h |
| 6 min | 7 | 341 s | 11.8 h | 12.5 h |
| 7 min | 8 | 380 s | 13.1 h | 13.8 h |
| 8 min | 9 | 414 s | 14.3 h | 15.0 h |
| 10 min | 10 | 461 s | 15.8 h | 16.5 h |

("Resolved by then": resolved in the trial and finished by the cap plus 15 s; a run with that cap would stop the route
earlier, so this is an estimate. Totals add the fixed 8.5 min. Durations were measured 4 at once, about 10% slower
per request than one at a time.)

Reading: more time resolves more (the extra 5 come at 5.1-8.6 min), but the 12 h hold only up to about 6 minutes,
and only with a short official setup per task. The rest of the gain has to come from making the tasks faster: the
same resolved tasks in less time.
