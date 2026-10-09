# What an LLM does well and badly: the checklist for every design change (2026-10-09)

The only design principle is to make the task easier for the 31B. This list turns it into checks. **Every design change
(script output, prompt, procedure, generation config) is checked against every item below before it is run against the
model**: for each item, write whether the change moves us **closer**, **farther** or is **neutral**, and why. The table
goes into the change's section of `docs/design_single.md` (or the commit message for a small change), and a change
that moves us farther on any item needs a stated reason.

Sources: our traces (Kaggle V1–V10 with the 31B; local batches with the 12B), the audit of the V10 traces
(`docs/audit_v10.md`, cognitive walkthrough + Nielsen's heuristics), and a literature search
(`docs/llm_strengths.md`). Evidence strength: **C** controlled study or ablation, **B** benchmark, **V** vendor
guidance, **O** our own traces, **A** anecdote.

## What an LLM does well (use it)

| # | The model does well | Evidence | Check question |
|---|---|---|---|
| W1 | **Choosing from a short list of meaningful options** (a classification), when the options are names, not letters or numbers | Option-id token bias across 20 LLMs (Zheng et al., ICLR 2024) C; constrained choice helps classification (Let Me Speak Freely, arXiv 2408.02442) C; meaningful names reduce hallucination (Anthropic, Writing tools for agents) V | Is every choice the model makes a pick from a short list of names, shown with the evidence that makes the right one recognizable? |
| W2 | **Writing whole functions or blocks** in plain code | Aider: whole 16.4% vs diff 8.0% for Qwen2.5-Coder-32B, 99.6% vs 71.6% well-formed B; high-level hunks cut editing errors 30–50% (Aider) C; Cursor rewrites whole files A/V | Does the model write whole units of code, never diffs, offsets or counts? |
| W3 | **Reasoning correctly about code that is in front of it** | V10: once the right code was in the window it found the fix (requests s6 `hist[1:]`, rich_3006 s12 `==` → `is`, 14986 s20 "Found it!") O | Does the window show the exact lines that must change, with the code they use and the code that uses them, so that nothing must be looked up? |
| W4 | **Following a concrete, fully filled next call** | V10: NEXT lines and navigation hints were followed O; SOP-Agent, StateFlow C | Does every answer end with one unambiguous next call that agrees with everything else in the answer? |
| W5 | **Reading familiar formats**: `cat -n` style numbered code, pytest output, tracebacks, `git diff` | "Keep the format close to what the model has seen on the internet" (Anthropic, Building effective agents) V; V10 test excerpts read correctly O | Is every output in a format common on the internet (diff with -/+, pytest lines), not a private notation? |
| W6 | **Using the names and words of code and of the statement** | Names carry meaning; performance drops when they are anonymized (Wang & Luo 2023, arXiv 2307.12488; When Names Disappear, arXiv 2510.03178) C; V10: 55 of 118 edit-step calls named code O | Does the interface speak in the repository's and the statement's own names and words? |
| W7 | **Better calls after thinking** | Thinking improves tool selection and parameters (Google, Gemma 4 function calling) V; answer-before-reason JSON loses up to 42 points on reasoning (arXiv 2408.02442) C | Is there room to think before each decision (thinking budget, reasoning outside the args)? |

## What an LLM does badly (do not ask for it)

| # | The model does badly | Evidence | Check question |
|---|---|---|---|
| B1 | **Producing line numbers or counts** | "GPT is terrible at working with source code line numbers" (Aider) C; "notoriously bad at counting line numbers" (Cursor) A; V10: a range "663-662", packed numbers; local runs: wrong ranges O | Does the model have to write a line number, an offset or a count anywhere? |
| B2 | **Copying long text exactly** | Exact transcription of long strings fails and the error grows with length (arXiv 2601.03640) C; V10: "Only one line was copied" refused in 5/10 runs, retries brought literal `\n` into the requirements O | Does the model have to copy more than a name? Is an inexact copy accepted? |
| B3 | **Opaque ids** (C2, P1, R1, UUIDs): it must remember what they mean, and it merges an id with another item's description | Id merged with another element's text (arXiv 2312.06147) C; the 12B's `C11:file::Name` O; compaction removes the list that defines an id O | Does the model have to send or remember an id instead of a name? |
| B4 | **Exact tool-call syntax** | V10: 44% of calls had a mangled `skill_name` (`「swe」`) O; Live API-Bench B | Is every required string short, plain and natural? Is a wrong spelling repaired instead of refused (where the script can see it)? |
| B5 | **Repeating a failed call once it is in the context**; error answers that quote the failed call make it worse; "do not repeat" instructions do not help | P(repeat) 0.06 → 0.54, 83% from the failed call's wording; describing instead of quoting removes 76% (Feedback That Backfires, arXiv 2608.23651; models ≤1.7B) C; self-conditioning (arXiv 2509.09677) C; V10: the same broken call 34 times O | Does any answer quote the model's failed input back? Does every refusal describe the problem in one line and give a different, filled-in next call? |
| B6 | **Calling tools or scripts that do not exist**, more with thinking | Reasoning Trap (arXiv 2510.22977) C; scale does not help (arXiv 2609.19425) C; `show_file`, `grep.py`, `nonexistent.py` in V1, V5, V6 O | Is a call to a plausible but missing script or tool harmless, answered with the right call? |
| B7 | **Using the middle of a long context**; it never asks for the next page | Lost in the middle: 75.8% first, 53.8% middle, 63.2% last (Liu et al.) C; 0 requests for a second page (arXiv 2608.26130) B; compaction at 14,336 tokens O | Is the decision question and what the change must do at the start and at the end of the answer, and is nothing needed cut away? |
| B8 | **Following many rules or forms**: adherence falls as their number grows | IFScale (arXiv 2507.11538) B; V10: the edit step had about 12 forms O | How many forms and rules does this step accept? Did the change add any? |
| B9 | **Deciding when it can keep exploring**: it reads instead of editing | V4–V6 and the local batches (reads, questions, late or no edits) O; Overthinking (arXiv 2502.08235) C | Can the model postpone the decision by asking for more? Is what it would ask for already shown? |
| B10 | **Knowing its budget**: it cannot see time or calls | V10: 6/10 runs ended on a budget, 5 without submit_patch O | Does every answer show the time and calls used and the deadline? |
| B11 | **Doubting tool output**: it takes the system's verdicts and summaries as true | V10: patch summary built from the plan, not the diff (4/10); "OK" from unrelated tests; coverage by shared words O; plausible-but-wrong patches (arXiv 2503.15223) C | Is every verdict and summary computed from the real state (the diff, tests that run the changed lines), and does it say "not checked" when it was not? |
| B12 | **Resolving contradictions** between the prompt, the window text and the NEXT line | V10: "TIME IS UP" while NEXT offered edits; a rename requirement against "an edit that breaks tests is undone"; the same input meaning different things in different steps O | Do the prompt, the answer and the NEXT line say the same thing? Does each input form mean one thing in every step? |
| B13 | **Order sensitivity in lists**, worse for smaller models | Permutation self-consistency +157% for 7B, +12% for 70B (Tang et al., NAACL 2024) C | Is the best item first, and is the list short? |
| B14 | **Decoding far from the vendor's settings** (repetition with near-greedy decoding) | Gemma 4 card: temperature 1.0, top_p 0.95, top_k 64 "across all use cases" V; Qwen3 card: greedy decoding in thinking mode causes endless repetitions A | Do the generation settings follow the vendor's, or is a difference measured? |

## Check of the design at commit 8c95d1d

| # | Status | Gap (evidence) | Fix |
|---|---|---|---|
| W1 | partly | Names are done; candidate lines carry no evidence of why they match, and "matched" shows stems and stop-words (audit #2: rich_3006 skipped the right C1) | Under each candidate, the 1–2 source lines that hold the statement's terms; no stop-words; signatures cut at 80 characters |
| W2 | yes | Whole-definition edit done | — |
| W3 | partly | Long places show the wrong part (Doc(...) text, the body hidden; audit #8); imports and the class line cannot be reached | Collapse `Doc(...)` parameters; show the lines with statement terms; show the import block and the class line |
| W4 | partly | "TIME IS UP" with a NEXT that still offers edits (audit #5) | At time-up the only next call is submit_patch |
| W5 | no | After an edit only the new lines are shown, no removed lines (audit #4) | Show a -/+ diff |
| W6 | partly | Requirement extraction leaves fragments ("address , avoiding"), PR remarks, deferred ideas; `deprecat` tagged as rename (audit #6) | Clean the extraction; drop PR/test remarks and "ideally … later" sentences; no rename tag for deprecation |
| W7 | partly | `thinking_budget` 512 for writing whole functions | Measure 1,024 if time allows |
| B1 | partly | Line numbers are still asked for code outside functions | Anchor such edits by old text → new text, matched without regard to whitespace |
| B2 | no | Step 1 asks for the whole statement and refuses a one-line copy (5/10) | Accept any copy; turn literal `\n` into line breaks |
| B3 | yes | Names instead of ids (8c95d1d) | — |
| B4 | partly | Skill renamed to `fix-issue`; the ADK's own "Skill not found" answer cannot be changed | Measure in the next run |
| B5 | no | STOP REPEATING quotes the earlier answer; refusals quote the model's text ("X is not a listed candidate", `not "<item>"`); the repeat guard ignores the state (audit #3) | Describe, never quote; key repeats on (step, place, args); give the full earlier answer only for a repeated look at the same view |
| B6 | no | No answer for `show.py`, `grep.py`, `edit.py` … | Small stub scripts that answer with the right `step.py` call |
| B7 | partly | The window ends with the answer forms and NEXT, but what the change must do is in the middle; long answers are cut in the middle | Repeat a one-line "what to do" just before NEXT |
| B8 | yes | Edit step down to edit / skip / back / a place name | — |
| B9 | partly | No questions in the edit step; the choose step has no evidence lines, so the model wants to search (audit #2) | Evidence lines in the candidate list instead of a search |
| B10 | no | No time or call count shown (audit #5) | "time 4.6 of 7 min; edits until 5.7 min" in every NEXT |
| B11 | no | Patch summary from the plan, not the diff; OK from unrelated tests; coverage by words (audit #1, #4, #7) | Summary from `git diff`; NOT VERIFIED when no selected test runs the changed lines; coverage by facts or "cannot be checked" |
| B12 | partly | Time-up contradiction; rename vs test rule (the current code excuses tests that use the old name; to confirm) | As B10 and W4; say "N tests use the old name; the hidden tests replace them" |
| B13 | yes | Ranked, best first, at most 10 | — |
| B14 | no | Temperature 0.2, no top_k; Gemma's card says 1.0 / 0.95 / 64 | A/B run: vendor settings vs 0.2, measuring loops and malformed calls |
