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
