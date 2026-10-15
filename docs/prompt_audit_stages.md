# Audit of every text the model reads in the stages build (2026-10-15)

The user's request (10-15): read every prompt and every piece of feedback the model receives, and apply the checklist
(`docs/llm_checklist.md`). The build audited is `variants/stages` (the design run since 10-14). The base build and the
agent_tool variant are not covered here; `docs/prompt_audit.md` (10-09) covers the base.

## What was read

| Part | Who writes it | Source read |
|---|---|---|
| Coordinator system instruction | ours | `variants/stages/prompts/coordinator.md` |
| The seven agents' instructions | ours | `variants/stages/sub_agents/*.md` |
| Agent names and descriptions (the coordinator's tool list) | ours | `agent.yaml`, `sub_agents/*.yaml` |
| `SKILL.md` | ours | `variants/stages/skills/fix-issue/SKILL.md` |
| "You are an agent. Your internal name is ..." and the skills instruction ("You MUST use the `load_skill` tool ...") | ADK | real requests of gk13c (`gate_k13c/0001.json`, `0003.json`) |
| The task message (budget, the six instructions, the tree) | harness | the same requests (coordinator only; an agent's user turn is just its request, such as `R1`) |
| Tool declarations (`submit_patch`, the seven agents, `run_skill_script` and the other skill tools) | ADK / harness / ours | the same requests |
| Every answer of the scripts | ours | 189 distinct answer kinds from the gated runs gh1-gh4, gk2-gk13c and gm3-gm9 (`scratchpad/gate_*`), and the code of `_stages.py` and of the base `step.py` helpers it calls (`_context_view`, `_patch_here`, `_code_view`) for answers no run has produced |

## Check, item by item

Status after the changes of this audit. "Changed" names what this audit changed.

| # | Status | What the texts do now | Gap left / changed |
|---|---|---|---|
| W1 choosing from named options | partly | Locate: candidates by qualified name with evidence; scope: options with their linking line; review: done / not done / cannot tell | The candidates' `[matches: ...]` shows stems (`fast, type`); a list helps only when the right name is on it |
| W2 whole units of code | partly | The editor's whole-definition form; `replace` by old text | A long place (over 80 lines) is changed by `replace` |
| W3 code in front of it | partly | Each view shows the place, the code it uses, its callers, and for a new member its class's methods | Changed: the sibling members of the class were listed under "Code that X uses" although X does not call them (gk12: `include_router`, `add_api_route` for `on_event`). They now have their own heading (`step._context_view`) |
| W4 a concrete next call | partly | Every answer ends with NEXT; agents return the coordinator's next call as a filled line | NEXT keeps placeholders (`'<name>'`, `'<the test>'`); filling them with a candidate would bias the choice (B13) |
| W5 familiar formats | yes | Numbered code, -/+ diffs, pytest E lines | — |
| W6 names of code and statement | yes | Code by qualified name; requirements quote the issue | — |
| W7 room to think | partly | Thinking 512 (coordinator), 1024-2048 (agents) | Not measured |
| B1 line numbers | partly | Specify refuses a sentence with line numbers; `replace` finds the old lines by text | Changed: the prompts and answers asked for the old lines "without their numbers"; the script already removes them, so that rule was dropped ("the old lines, as shown") |
| B2 copying long text | partly | Requirements quote a sentence (any copy accepted); `replace` copies old lines | — |
| B3 opaque ids | partly | Requirements are R1, R2 (always shown with their text) | The coordinator passes R1 on without its text; it never needs its meaning |
| B4 exact call syntax | partly | One script per agent, call forms in every prompt | The ADK's own errors are answered in the prompts (F8) |
| B5 quoting the failed input | partly | Refusals describe the problem | Changed: locate's empty search quoted the model's text back ("Nothing in the repository holds 'def frontend'"); it now says "that name or text" |
| B6 missing tools or scripts | yes | A finished agent's stray call is answered with the line to pass on | — |
| B7 the middle of a long context | partly | Views up to 9,000 characters (edit, specify) | Changed: P6 below |
| B8 many rules or forms | partly | Edit: 4 forms in NEXT (whole definition, replace, no change, missing) | Changed: `edit.md` step 2 explained the elided `def <name>(...):` form and the long-place rule to every editor; the window says them when they apply, so the prompt now only points to the window |
| B9 exploring instead of deciding | yes | Only locate can search; no read forms elsewhere | — |
| B10 knowing the budget | partly | The coordinator sees the time used | Changed: "Time used: 3 min 2 s of 45 min." (the limit was missing). The agents never see time; they cannot act on it |
| B11 doubting tool output | partly | "Kept, not checked" when no test ran; review reruns the reproduction test and the existing tests | Changed: W3's mislabelled siblings. Left: a reproduction test that fails for its own reason (gk13c: a missing `dist`) reads as the requirement's verdict |
| B12 contradictions | partly | Prompts and NEXT lines agree on who makes the call | Changed: (1) the harness's "inspect the code and tests ... verify before submit_patch" against the coordinator's "you never read code": the coordinator prompt now says the agents do both. (2) Step 3 said "reply with the line the script gives" while some finished answers give no line: "(one sentence when it gives no line)". (3) `edit.md` step 3 still had the old wording that gk13c showed to be misread; it now matches the other six |
| B13 order in lists | yes | Best first, short lists; a new member first | — |
| B14 vendor settings | partly | Agents at the vendor's settings | The coordinator runs at temperature 0.2, never measured against 1.0 |
| B15 escaped text | partly | The harness sends answers as JSON | Changed: our own double quotes in route's NEXT (`a line \"call the tool ...\"`) and around every requirement's quote (`(the issue: \"...\")`) were removed |
| B16 side-effect forms to get information | yes | No editor form changes code to read it | — |
| P1 quoted data in fixed labels | yes | `<issue>` in every prompt; "(from the issue: ...)", "What to change here:", "The reproduction test, on the original code:" | — |
| P2 layout changes | — | process item | This audit changes wording, two headings and two lines; the layout of each view is kept |
| P3 positive rules with their reason | partly | Most rules give their reason | Changed: reproduce now says what the test must do (create what it uses, so it fails only for the requirement) instead of leaving gk13c's failure mode unstated |
| P4 pressure words | partly | Ours have none. The ADK's skills text has "MUST", "Do NOT", "This is very important" | Out of our control |
| P5 contradictions in a small thinking budget | partly | See B12 | The ADK's "you MUST use load_skill" against ours "load_skill is not needed" stays (harmless: SKILL.md says which script to call) |
| P6 the decision at both ends | partly | Locate, scope, review: question first and last | Changed: the edit view put "What to change here" in the middle of up to 9,000 characters and ended with the forms; the instruction before NEXT now restates the sentence (up to 400 characters). Specify's last line now names the place and its requirements |
| P7 unneeded context | partly | Views are per place | Changed: the siblings' own heading (W3). "Other code near these places" in review is names only |
| P8 progress recited | partly | The coordinator's route line recites the patch and the plan; specify lists the other places with their sentences | Agents do not see the plan's state beyond their place |
| P9 call forms, not content examples | partly | Call forms in prompts; requirements has one content example (`Prompt.ask(..., case_sensitive=False)`) for the form of a requirement | The reproduce view's existing test is a content example by design (imports, fixtures) |
| P10 uniform observations | yes | Each view is place-specific | — |
| F1 where the error is | partly | Refusals name the test, the line, the requirement | Changed: review's "The answer was not read." now says which first items are read |
| F2 failing test with expected vs actual | partly | The reproduction test's E lines before and after | — |
| F3 no false OK | yes | "Kept, not checked"; review's done refused while tests fail | — |
| F4 challenges without new facts | partly | Refusals of "done" cite the failing test (a fact) | A test that fails for its own reason (B11) is presented as a fact about the patch |
| F5 what is done | partly | Coordinator: patch, plan states, time | Agents: their place and its diff only |
| F6 third retries | yes | MAX_FAILS 2, MAX_ASKS 2, two review rounds | — |
| F7 leaving a loop | partly | A repeated ask moves on after 2 | — |
| F8 harness errors | yes | Every prompt says what "Argument ... is required" means and the call to send | — |
| F9 facts first | partly | Verdicts lead with the test or the diff | Undone-edit answers end with one line of instruction |

## Changes made (one commit)

| # | Where | Change | Items |
|---|---|---|---|
| 1 | `prompts/coordinator.md` | The harness's "inspect and verify before submit_patch" is answered: the agents do both | B12, P5 |
| 2 | the six `sub_agents/*.md` other than edit | Step 3: "(one sentence when it gives no line)" | B12 |
| 3 | `sub_agents/edit.md` | Step 2: the replace form only, and "the window says when ..." instead of the elided-def rule; step 3 as the other six | B8, B12 |
| 4 | `sub_agents/reproduce.md` | The test creates what it uses, so it fails only for the requirement | P3, B11 |
| 5 | `_stages.py` route | NEXT without our double quotes | B15 |
| 6 | `_stages.py` `_req_line` | "(from the issue: ...)" without quotes | B15 |
| 7 | `_stages.py` `_progress` | "of 45 min" | B10 |
| 8 | `_stages.py` specify | The last line names the place and its requirements | P6 |
| 9 | `_stages.py` edit | The sentence again before NEXT, in both the whole-definition and the long-place views | P6, B7 |
| 10 | `_stages.py` replace texts | "as shown" (the script removes line numbers itself) | B1, B8 |
| 11 | `_stages.py` review | An unread answer says which first items are read | F1 |
| 12 | `_stages.py` locate | An empty search is described, not quoted | B5 |
| 13 | `agent/.../step.py` `_context_view` | Sibling members under "Other members of its class that use the same code (X does not call them)" | W3, B11, B12, P7 |

Checked by a dry run on fastapi_14851 (the edit, specify and review views). Not yet run against a model.

## Open after this audit

- A reproduction test that fails for its own reason after a correct fix (gk13c) still reads as the requirement's
  verdict; the prompt change (4) lowers the chance, the script does not tell the two apart.
- Review does not compare twin places already edited (gk13c: `APIRouter.frontend` kept the directory check).
- The coordinator's temperature 0.2 (B14) is unmeasured.
- The ADK's skills instruction (pressure words, "MUST use load_skill") cannot be changed from the config.
