# Audit of the script's answers to the model (2026-10-11, HEAD 4aec141)

What every text that `step.py` / `edit.py` sends back is checked against: the feedback literature (the new sources are
in `docs/prompting_feedback.md`, section "2026-10-11 additions"), the checklist items F1–F9, B5, B12, W4, and the real
texts of the local runs a04 and a05 (requests_7328, agent_tool variant, read call by call).

## The rules (from the literature)

| Rule | Source | In the checklist |
|---|---|---|
| R1 A refusal says what was wrong in one plain line and gives one complete, filled next call; never a recipe | Anthropic tool docs (V); OpenAI "don't make the model fill what you know" (V); Repair Economics: recipes half-followed (C weak) | F9, B5 |
| R2 A refusal says whether resending can ever work ("this form is never accepted") | ToolMaze: permanent faults recovered 38% vs transient 81% (B); CriticTool: missed errors retried >3 times (B) | new |
| R3 Never quote the model's failed input | Feedback That Backfires (C) | B5 |
| R4 After an accepted edit, report the verified effect (the diff, which tests ran the lines) | Verified Tool Calls 58→80% (C weak); issue-solving failures misread output as success (B) | F3, F5 |
| R5 A test failure leads with the test, the call, expected vs observed; never only a count; no traces, no passed tests | Counterexamples 90% vs 23% count-only (C); RLEF format (C); LDB-style detail worst (C) | F2 |
| R6 After an undone edit, say the code is back and the same text will fail again | RLEF: models resubmit the same code (C); EIR near 0 needed (C) | F6 |
| R7 Every instruction in feedback must be certain; a heuristic is labelled or dropped | Obey/Diverge/Collapse: wrong instructions followed 15–44% even when flagged (C) | F4 |
| R8 Evidence as an external record ("Test result:", "The file now reads:"), never "check your reasoning" | Self-Correction Illusion: +23 to +93 pp (C); Self-Correction Bench (C) | new |
| R9 Loops are the default (~30% at 32B): 3 identical calls move on; a repeat gets a different answer | SWE-Gym (B); Manus (P) | F7 |
| R10 At most 2 repair rounds per place | Obey/Diverge (C); multi-turn code gen, 3 turns optimal (C) | F6 |
| R11 Silent anomalies are said plainly (a plan dropped, an edit elsewhere, a list cut) | ToolMaze implicit faults <20% (B); Loud/Quiet failures (C) | B11 |
| R12 The text and the NEXT line never disagree | V10 traces (O); later instruction wins (V) | B12, W4 |

## Findings

Severity: **S1** broke a real run (a04/a05), **S2** contradiction or false statement reachable in normal use, **S3** wording.

| # | Answer (file:line) | What is wrong | Rule | Sev. | Fix |
|---|---|---|---|---|---|
| 1 | EDITOR_DONE after the editor is closed (step.py:2040) | the same text for every later call: a05 got it 6 times, a04 5 times, and the model kept editing; worse, the calls still reach the repeat guard, whose move-on skipped *another* place (a05: resolve_redirects skipped by the Session.send editor, the plan fell back to choosing) | R9, R11, R12 | S1 | a closed editor's calls never touch the state (answered before the repeat guard); the answer varies: 2nd time "Edits of X are closed: no further call runs here; reply with one sentence" |
| 2 | move-on in the editor role (step.py:1961) | acts on `state['current']`, which after the editor's own place was decided can be another place | R11 | S1 | in the editor role the move-on acts only on the editor's place |
| 3 | only-mine (step.py:2028) | a05: the editor needed to *read* resolve_redirects (what `gen` yields) and was refused; it then guessed wrong and sent the same failed edit 3 times | W3, R1 | S1 | in the editor role a related place's name shows its code read-only ("read only: only X is edited here"), at most the place window's size |
| 4 | broken-undone + fail-left (1812, 1864) | "Send X again …" printed together with the next place, the finish view or EDITOR_DONE | R12 | S2 | when the place is left, drop the "Send again" sentence |
| 5 | same-call (2134) | the first repeat of a failed edit says "same call … not run again" but not that the same text fails again; a05 sent it 3 times | R6, R2 | S2 | for a repeated undone edit use failed-before ("this exact edit was undone by …; the same text fails again") before the repeat answer |
| 6 | submit-as-arg (2156), move-on other (1977) | "call submit_patch" while NEXT gives a run_skill_script form (D1, D3) | R12 | S2 | text and NEXT from one place: in D1/D3 say "submit_patch is its own tool; this step needs …" and keep NEXT |
| 7 | reread (2101) | "were shown a few calls ago" also when they were not (coordinator, or "do not exist"); in D4 says "edit or skip" against NEXT submit | R12, R7 | S2 | only when the last answer to that range really showed lines; never in D4 |
| 8 | d3-not-run (1743), back/search/owner-cands in the editor role | offer `back` and naming code, which lead the editor to D1 where its NEXT is "no call" | R12 | S2 | editor role: forms limited to edit/skip/read; `back` → "the coordinator chooses other code: reply with one sentence" |
| 9 | only-mine (name) (2044) | prints "Only None is edited here" when the editor's first call named no place; quotes the model's text | R3, R12 | S2 | no `editor_place` → open the request's place; never quote |
| 10 | search/range in D3 (896-920, 1577) | drop the places not edited yet without saying so | R11 | S2 | say which places were dropped (as `_choose_code` does) |
| 11 | d1-nothing-holds, search-none, library-view (1078, 1030, 1660) | quote the model's text verbatim | R3 | S3 | describe ("the 3 names sent") instead of quoting, or quote only a short name |
| 12 | kept-ok/kept-unchecked (1817) | good (diff + tests that ran the lines, R4); "1 existing tests pass" grammar | R4 | S3 | grammar |
| 13 | failure excerpt (_tests.py:398) | "the test runs:" is the deepest `>` line, which can be package code, not the test | R5 | S3 | take the `>` line from the test file's frame |
| 14 | edit-note warnings (G2) on an accepted, "checked" edit | a warning with no instruction next to "Kept and checked" (e.g. "replaced only n lines": old lines stay silently) | R11, R4 | S3 | a partial replacement is refused, not noted |
| 15 | finish view "Looked at and not changed" | false for a class place whose method changed | R7 | S3 | compare by containment |
| 16 | Pressure words: "Reply now", "Decide now", "Only", "must" | P4 (vendor guides); small effect | — | S3 | plain wording with the reason |
| 17 | Refusals never say whether the form can ever work | — | R2 | S3 | add "this form is never accepted" to the format refusals (d3-not-read, d3-not-run) |

**What is already right:** undone edits restore the code and name the failing tests with the assert lines (R5, R6
partly); the failed-edit memory (R6, R10); accepted edits show the -/+ diff and say whether tests ran the changed lines
(R4); the three-repeat move-on (R9); NEXT filled with the place name (R1).

**Order of the fixes:** 1, 2, 3 (they broke a04 and a05), then 4–10 (contradictions), then 11–17.

## Status (2026-10-11, after the audit)

All 17 findings fixed in one round:
- 1, 2, 3, 9: `_editor_gate` (step.py), answered before the repeat guard:
  - a closed editor gets "Edits of X are closed …" and its calls change nothing;
  - the editor's calls act on its own place only;
  - related code (another place, a resolvable name, a range outside its place) is shown read-only, whole up to 120
    lines;
  - `back` is answered as the coordinator's step;
  - an editor that never opened its place is told to open it.
- 4: the "Send X again" sentence is dropped when the place is left.
- 5: a repeated undone edit gets the failed-before text ("the same text fails the same way").
- 6: submit_patch sent as an argument outside D4 says "make the call in NEXT"; the D1 move-on no longer says
  "call submit_patch".
- 7: re-read only for ranges really shown, single role, D3.
- 8, 17: format refusals say "this form is never read/accepted, sending it again gives the same answer"; the editor's
  list of forms has no `back`.
- 10: search and range answers name the places they drop.
- 11: `_said` names short names back and describes longer text.
- 12: the test count is now grammatical ("1 existing test passes").
- 13: the failure excerpt takes the first `>` line (the test's own frame).
- 14: no partial replacement.
- 15: a class place whose member changed counts as changed.
- 16: "Reply now", "Decide now" → plain.

| Item | Effect | Why |
|---|---|---|
| B5 | closer | no quoting of long model text; a repeated failed edit is described with its tests |
| F6, F7 | closer | the same failed text is said to fail again; the closed editor's answer differs from the done answer |
| B12, W4 | closer | the "send again", "call submit_patch" and false re-read sentences that contradicted NEXT are gone |
| W3 | closer | the editor can read the code its place uses (a05: what resolve_redirects yields) |
| B9 | farther (small) | the editor can read related code; bounded: each piece once ("shown above" after), read only, and the answer ends with "Back to X" |
| B11 | closer | dropped places are said; the finish view no longer lists a changed class as not changed |
| P4 | closer | pressure words removed |

**Run a06 (2026-10-11, after the fixes):**
- What worked:
  - the coordinator delegated at once, with no wasted reads;
  - the first editor read `resolve_redirects` to its `yield`, edited, got the review, and replied in one sentence;
  - the coordinator called `status` and moved on;
  - the one-item range `673,748` was read.
- New loop: the Session.send editor asked for the same lines 721-734 again and again. Its thinking, cut at the
  budget each time, re-derived the same trace ("I'm repeating myself") and never reached a decision.
- Fix: in the editor role, the third identical range gets "were shown in the last answers and have not changed.
  Decide with what they show …", and the fourth moves the work on (F7, B9 closer). The first two reads stay free:
  a re-read is still how a cut reasoning continues.

**Run a07 (2026-10-11):**
- What worked: the Session.send editor read `resolve_redirects` read-only and replied "The change belongs to
  `SessionRedirectMixin.resolve_redirects`", as the gate intends.
- Two system faults:
  1. the coordinator chose that code as its instruction says, and the script answered with the status (the D3
     coordinator path refused every name);
  2. the editor that replied was still "open" in the state, so the next editor's opening call would have read as a
     read of related code. ADK passes no invocation id to the script, so one edit_place call cannot be told from the
     next.
- Fixes:
  - any coordinator call to step.py closes the open editor; a place it left undecided keeps its code (skipped, its
    sentence says why);
  - `_coordinator_choice`: in D3/D4 the coordinator may name a listed place (planned, next) or other code (chosen as
    at the choose step);
  - a coordinator asking to see code gets "This call was not run: the code of X is read by its editor …" instead of
    the same status again (F7, P3);
  - a read-only range outside the editor's place is labelled with its file, not with the editor's place.

**Run a08 (2026-10-11):** the coordinator delegated at once. The first editor edited `resolve_redirects`
(accepted, 140 tests) and named Session.send, which opened it. It then went back to edit `resolve_redirects` with
line numbers from before its own edit. Two failures there closed the editor before it decided anything on
Session.send (system fault).
- Fix: in the editor role, a failure or an accepted edit at a place that is not the editor's own returns to the
  editor's own place ("Back to X" with its window) while that place is still to decide.

**Run a09 (2026-10-11): the flow ran end to end with no system fault.** Not resolved: 15 tool calls, 244 s,
881-character patch.
- The coordinator chose `resolve_redirects` in the edit step (the `_coordinator_choice` fix).
- The editor replaced lines 174-281 of `resolve_redirects` after reading only 174-233. The tests undid it (11
  failures), and it sent the same text three times, its reasoning cut by the budget each time before it read the rest.
- It then made a line edit that was accepted: it drops `resp.history = hist[1:]` and checks `len(hist)`.
- The rewrite of Session.send lost the `allow_redirects` line it had not seen and was undone.
- The patch takes the statement's "ideally remove … entirely" (a change the statement defers) instead of the immediate
  fix (`resp.history = hist[:]` before the append). This is a model reading error; the statement cleaner already
  drops "ideally … later" sentences only in some forms (checklist W6).
- Fix (B16, B11 closer): a line edit or whole-definition edit whose range holds more than 3 non-blank lines never
  shown in this editor (or session) is not applied. The answer shows those lines ("Not applied yet: lines a-b are
  inside the edit's range but were not shown …"). The shown lines are tracked from every numbered view and reset
  after a kept edit and for each new editor.

**What the issue leaves for later (2026-10-11, after a09).**
- Problem: the model followed the issue's "Ideally, we should remove intermediate `.history` assignment entirely",
  which the issue itself postpones ("a breaking change we'll have to wait on … this should fix the immediate issue for
  now").
- Fix: `_statement.deferred` finds an "ideally / eventually / in a future PR" sentence, but only when the issue also
  says it must wait ("breaking change", "for now", "later", "separate PR"). That sentence is shown, in the issue's
  own words, under the requirements and under the Goal line of every window: "Left for later by the issue (not part
  of this fix): …".
- Over the 129 dev tasks it fires once (requests_7328), with no false positive.

| Item | Effect | Why |
|---|---|---|
| R7 / F4 | neutral | the line is a quote of the issue plus a label read from its own words, not a heuristic instruction |
| B7 / P6 | closer | what the change must not do is next to the goal, at the top of each window |
| P7 | neutral | absent on 128 of 129 tasks |

**Run a10 (2026-10-11).**
- Result: the same 881-character patch as a09, not resolved.
- Why the "left for later" line never appeared: the coordinator copied only the title and its search terms, so the
  script never saw the "Ideally …" sentence. The script knows the statement only from the model's copy.
- Other things seen:
  - the coordinator asked to read code 3 times before delegating; the new explanation got it to delegate on the
    third;
  - the editor sent `'174,281,<code>'` in one item;
  - after the finish view, a second review editor ran away to the 4,096-token limit.
- Fixes:
  - (prompt) a general rule, with its reason, in the editor's instruction and in the single agent's: "When the issue
    says a change would be ideal but must wait (a breaking change, 'for now', a later release), make only the
    immediate fix it asks for: the hidden tests check that fix, and the postponed change breaks them". This works
    without the copy, because the issue is in the system instruction.
  - (script) `'<a>,<b>,<code>'` in one item is read as a line edit.

| Item | Effect | Why |
|---|---|---|
| P3 | closer | a positive rule with its reason |
| B8 | farther (one rule more) | a09 and a10 lost the task to the postponed change; the rule is one sentence |
| B4 | closer | one more common call shape read instead of refused |

**Run a11 (2026-10-11).**
- What worked: the coordinator copied the whole issue and delegated at once, and the "Left for later" line reached
  the window.
- What went wrong:
  - the Session.send editor took `resolve_redirects` to yield the first response too;
  - its edits built on that were undone;
  - closed, it kept sending the same edit, its reasoning ("I am repeating myself again") cut before it replied.
- Fixes:
  - the window's "Code that X uses" now finds methods inherited from a base class
    (`self.resolve_redirects` → `SessionRedirectMixin.resolve_redirects`);
  - it ranks the members the place uses by the requirement's words;
  - for a generator it shows what it yields ("it yields: yield req (line 264); yield resp (line 281)") (W3 closer);
  - the closed-editor answer ends with a ready sentence the editor can return as is (W4 closer: a filled next action);
  - the editor's place name is kept in the state, since a skip of the only place sends the plan back to choosing.

**Run a12 (2026-10-11): repetition loops at temperature 0.2 (checklist B14).**
- What worked:
  - the coordinator delegated at once;
  - the third identical range read got the "decide" answer and the editor moved to an edit;
  - the window showed "it yields: yield req; yield resp".
- What went wrong: the closed editor of Session.send sent the same two calls 7 times. Its thinking repeated one
  sentence until the budget ("I'll use `Session.send` line numbers to see other lines." × n), and the ready sentence
  did not stop it.
- The same degenerate repetition appeared in a06, a11 and a12. The script cannot end an agent_tool sub-agent; only
  the model's own text reply does.
- B14 says near-greedy decoding causes endless repetition (Qwen3 card), and Gemma's card gives 1.0 / 0.95 / 64 "across
  all use cases".
- Change (one variable): the editor sub-agent samples with temperature 1.0, top_k 64, top_p 0.95. The coordinator
  stays at 0.2 for this measurement.

| Item | Effect | Why |
|---|---|---|
| B14 | closer | the vendor's settings for the agent that loops |
| F7 | closer (expected) | sampling variation is what lets a loop end |

**Run a13 (2026-10-11), the editor at temperature 1.0.**
- No repetition loop this time (one run, not yet a measurement).
- Three system faults, fixed:
  1. an internal `IndexError` in `_failed` (the editor branch read the plan after a second failure had emptied it);
  2. `['src/requests/sessions.py:160', '160', '285']` was refused as a form never accepted. A `file.py:N` item before
     two numbers, or `file.py:a-b` alone, is now a range;
  3. the plan went back to choosing and the coordinator called edit_place with other code, so the editor could not
     open it (D1) and looped. In D1, the editor's request naming code is now taken as the coordinator's choice: it
     is chosen and opened.
- The model still misreads what the redirect generator yields; that is the model, not the system.

**Run a14 (2026-10-11).**
- Result: not resolved, the same 881-character patch.
- What worked:
  - the Session.send editor read `resolve_redirects` read-only, understood what it yields, and reached a real
    idea (filter `r` out of the history);
  - the unseen-lines guard stopped a whole-function rewrite of `resolve_redirects` and turned it into a range edit.
- What went wrong:
  - two typos (a stray quote, then a missing `]`) closed Session.send before the third, correct version ran;
  - the editor then told the coordinator "I have updated Session.send", which was false;
  - the coordinator believed it ("The editor said it did that"), although the finish view said "Looked at and not
    changed: Session.send" (B11).
- Fixes:
  - edits that do not compile count as slips, up to 4 (`MAX_SLIPS`); edits undone by tests or refused stay at 2
    (F6: the literature's 2 rounds are about wrong changes, not typos);
  - the finish view says, for each place not changed, how many of its edits were refused or undone (B11, R4: the
    verified effect, not the editor's sentence);
  - the unseen-lines answer lists the unseen spans ("161-171, 202-281") instead of one false range;
  - the "left" sentence counts the real tries.

**Run a15 (2026-10-11): the right fix, and the local check cannot see it.**
- The `resolve_redirects` editor read lines 181-185 and replaced them with `resp.history = list(hist)` before
  `hist.append(resp)`. That is the reference patch (`resp.history = hist[:]` before the append).
- The coordinator then sent Session.send to an editor twice. The script's guards closed the first editor without a
  change; the second changed only `[resp for resp in gen]` → `list(gen)`, which is harmless.
- The harness reported resolved 0. **The local harness cannot resolve requests_7328 with any patch.** It judges by the
  whole test file (`tests/test_requests.py`), and the local wheels lack `pytest-httpbin`, `httpbin` and `trustme`.
  With the reference patch the file gives the same 5 failed and 194 errors (fixture 'httpbin') as with ours (checked
  2026-10-11 in a venv built from `comp/wheels`). So every local "not resolved" on requests tasks says nothing; the
  patch must be compared with the reference by hand, or the task run on Kaggle.

**Why the coordinator tries to read code (a16, the full requests dumped, 2026-10-11).**
- The coordinator's requests hold three texts that ask it to read, and its own instruction neutralised none of them.
  The single agent's prompt does say "the script does both for you".
- Sources, with the citations counted in the coordinator's reasoning over a04-a16:
  1. **Our own instruction**, step 2: "send … a file with two line numbers: you get the code that holds it" (cited 11
     times, in a04 and a07). The line ranges in the candidate and place lists gave it the numbers
     (a10: `src/requests/sessions.py:676-751`).
  2. **The ADK skills text**: "Use `load_skill_resource` to view script content first if needed", plus the
     `load_skill_resource` tool (cited in a04, a05 and a12; a05 called it on `src/requests/sessions.py`).
  3. **The harness task message**: "2. Inspect existing codebase conventions and test files before making edits", "3.
     Verify your implementation using targeted tests …", and the directory tree.
- Fixes:
  - the coordinator instruction says the task message's inspect/verify is done by the editors and the script, and
    that load_skill_resource shows only the skill's files;
  - the choose-step find form no longer offers "a file with two line numbers";
  - in the coordinator role, candidates and places carry no line numbers.

| Item | Effect | Why |
|---|---|---|
| B12 | closer | the prompt now answers the task message's contrary instructions |
| B16 / B9 | closer | no number in the coordinator's view invites a read |
| W1 | neutral | the candidates keep their evidence lines (the text), without numbers |

**V16 on Kaggle (2026-10-11): the 31B with the agent_tool variant, requests_7328 alone.**
- Clean run: 11 tool calls, no malformed call, no code read by the coordinator, no loop; 352 s.
- Flow: coordinator S0 → choose Session.send and resolve_redirects → edit_place Session.send. That editor read
  resolve_redirects read-only, made two edits (one undone by tests, one kept) and replied. Then status →
  edit_place resolve_redirects; that editor made one edit and replied. Then status → submit_patch.
- Patch: `resp.history = hist[:-1]` after the append (the reference fix), plus `resp.history = history[:i]` for the
  intermediate responses in Session.send (consistent with the hidden test).
- Resolved 0 is not a measurement: the Kaggle sandbox, like the local one before today, has no `pytest-httpbin`, and
  `tests/test_requests.py` errors on setup with any patch. Requests dev tasks cannot be scored on either; evals must
  use fastapi/rich/httpx tasks.
- Editor calls take ~30 s each on the L4s (thinking 1024), so the edit work used 250 of the 352 s.

**Batch b9 (2026-10-11, 9 local tasks, agent_tool) — fixes during the run.**
- `open.py`: the editor's first call goes through its own entry, `file_path open.py`. ADK passes no invocation id, so
  a place name sent to edit.py could be a new editor or a read of related code. In b9 (fastapi_14448) the
  coordinator called edit_place with `Dependant._unwrapped_call` without calling step.py first, and the new editor
  got a read-only view. With open.py the opening is explicit:
  - the previous editor's undecided place keeps its code;
  - a name off the places is chosen as the coordinator's choice;
  - the editor prompt shows the open.py call form and a read form for related code.
- The unseen-lines answer shows the lines whole (b9 fastapi_14851 got the same refusal 3 times: the lines were long
  strings that every view collapsed, so they never counted as shown).
- Editor reads: inherited library members (`FastAPI.lifespan`, Starlette) are shown read-only; `self.x` is read as
  a member of the place's class; a range that overlaps the editor's own place is read as that place's lines.
- Kaggle V16b with the test wheels installed: `tests/test_requests.py` now runs (pytest 8.4.2, httpbin). 4 tests
  still fail with any patch (one TestRequests SSL/proxy test and three connect timeouts, all environment), so
  requests tasks stay unscorable there too. The patch (`resp.history = hist[:-1]` after the append) passes the
  hidden test locally.

**Batch b9 results (2026-10-11, local 26B, agent_tool, commit before open.py): 0/7.**
- The gateway went down (Cloudflare 530) at the 8th task; rich_3469 and rich_3006 did not run.

| Task | Result | Calls | Time | What happened |
|---|---|---|---|---|
| fastapi_14986 | not resolved | 25 | 437 s | the editor rewrote the openapi closure (schema copy with the request's root_path); a second editor of get_swagger_ui_html re-read until moved on |
| fastapi_14851 | time-out, empty patch | 27 | 447 s | the editor browsed related code (FastAPI, APIRouter.on_event, lifespan_context: Starlette's, not readable then); the same edit was refused 3 times by the unseen-lines guard (collapsed strings, fixed after) |
| fastapi_14448 | time-out | 30 | 453 s | the right idea (unwrap nested partials in `_unwrapped_call`) and the sibling `is_*_callable` methods, but editors chose and re-chose code |
| httpx_3672 | time-out | 34 | 427 s | the place was a whole 426-line class; the editor paged through it 60 lines at a time before editing (`keep_alive`, `complete` → `reset`) |
| fastapi_15800 | time-out | 41 | 457 s | a new feature: the coordinator made 15 searches in the choose step ("does FastAPI inherit mount?"), its history was compacted; the new editor of applications.py got the closed answer of the previous one (the open.py case) |
| fastapi_14583 | not resolved | 16 | 206 s | a module-level DeprecationWarning in the wrong file |
| rich_3521 | time-out | 23 | 435 s | the unseen-lines loop on a docstring (fixed after) |

- Time is the binding limit: 5 of 7 tasks ran out of the 7 minutes. Most of the time went to reads in the editors
  (B9). Open problems:
  - a large class as one place (it should open by members);
  - new-feature tasks (no place to choose; the coordinator searches);
  - inherited library members in searches.

**b10-b11 (2026-10-12): the editor's history and two loops.**
- b10 fastapi_14851: the editor looped on a malformed call that ADK refused ("Argument 'skill_name' is required").
  The script never sees such a call, so both prompts now say what that answer means and give the correct call (F8).
- b11 fastapi_14448: the editor sent `['fastapi/dependencies/utils.py top level', 1, 1000]` 5 times. A range on
  "file.py top level" was not read, and every range was exempt from the repeat guard.
  - A "top level" range is now a range of the file.
  - Only ranges really shown before are exempt from the repeat guard.
- The editor's history is never compacted (agent_tool sub-agent) and reached 31,900 tokens of a 32,768 context.
  - The script estimates it: instruction + answers / 3.2 + 700 per call for the model's replies and thoughts.
  - Above 22,000 tokens no more code is shown ("edit with what was shown, or skip").
  - Above 27,000 tokens the place is closed with the ready sentence.
  - The bound is the model's context, not a chosen number of reads.
- open.py: it no longer skips the previous editor's place, and it opens only a place (or code, for an editor
  without one); anything else is answered as edit.py answers it. In b10 an editor used open.py to browse files and
  `ls -R`.

| Item | Effect | Why |
|---|---|---|
| F7 | closer | a refused range repeated now meets the repeat guard |
| B7 | closer | the editor's history stays inside the context |
| B9 | closer | reads end before the context is full |
| F8 | closer | the harness's own error is explained in the prompt |
