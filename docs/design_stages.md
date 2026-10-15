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
