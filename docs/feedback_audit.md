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
