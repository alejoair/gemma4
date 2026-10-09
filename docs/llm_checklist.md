# What an LLM does well and badly: the checklist for every design change (2026-10-09)

The only design principle is to make the task easier for the 31B. This list turns it into checks. **Every design change
(script output, prompt, procedure, generation config) is checked against every item below before it is run against the
model**: for each item, write whether the change moves us **closer**, **farther** or is **neutral**, and why. The table
goes into the change's section of `docs/design_single.md` (or the commit message for a small change), and a change
that moves us farther on any item needs a stated reason.

Sources: our traces (Kaggle V1–V10 with the 31B; local batches with the 12B), the audit of the V10 traces
(`docs/audit_v10.md`, cognitive walkthrough + Nielsen's heuristics), and a literature search
(`docs/llm_strengths.md`), and two prompting searches: how to present information (`docs/prompting_input.md`, items P) and how to give
feedback (`docs/prompting_feedback.md`, items F). Evidence strength: **C** controlled study or ablation, **B** benchmark, **V** vendor
guidance, **O** our own traces, **A** anecdote, **P** practitioner report.

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
| B15 | **Reading and writing text that is escaped**: every tool answer reaches the model as JSON, so line breaks read as `\n` and quotes as `\"`; the model then writes escaped code and mangles the quotes of its calls | ADK serializes tool results with json.dumps; a V10 window had 98 `\n` and 62 `\"` O; code in JSON is edited worse (Aider) C; V10: 44% mangled `skill_name` O | Does the answer add double quotes of its own (lists, names, NEXT)? Is a name written in one quote form everywhere? |

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
| B15 | no | NEXT lines and answer forms use double quotes (escaped to `\"` for the model); names appear in four quote forms (`docs/prompt_audit.md`) | Single quotes in lists, names written bare; A/B of the prompt's raw call examples |

## How to present information (prompting)

| # | The model does well / badly | Evidence | Check question |
|---|---|---|---|
| P1 | **Badly: telling quoted data from instructions** when the issue or test text is pasted without a boundary | Anthropic, OpenAI, Gemini delimiter guidance V; formatting alone moves accuracy up to 76 points (Sclar et al., arXiv 2310.11324) C | Is every piece of quoted data (statement, example, test, code) inside a fixed label or tag that the instruction text never uses? |
| P2 | **Badly: absorbing layout changes**; accuracy moves with layout, more in smaller models | up to 40% on GPT-3.5 (arXiv 2411.10541) C; Sclar C | Does the change alter the layout of an answer the model already handles? Is one layout change measured at a time? |
| P3 | **Well: positive rules with their reason; badly: open-ended prohibitions** ("only", "never"), which make it overreact or do too little | Anthropic, Gemini 3 guide V; negated prompts C | Is every rule what to do, with the reason in the same sentence? Could a "never / only" rule make the model change too little? |
| P4 | **Badly: emphasis and pressure words** (capitals, MUST, NOTHING) | OpenAI, Anthropic, Gemini V; "Be THOROUGH" caused repeated searches (Cursor) A | Does any answer use capitals or pressure words where plain words and a reason would do? |
| P5 | **Badly: spending a small thinking budget on contradictions** | GPT-5 guide V; the later instruction wins (GPT-4.1 guide) V | With 512 thinking tokens, can the model act without reconciling two statements? |
| P6 | **Well: using a goal stated at both ends** of a long answer | Liu et al. C; GPT-4.1, Anthropic, Gemini V | Is the decision of this step in the first line and again just before NEXT? |
| P7 | **Badly: ignoring relevant-looking but unneeded context** | Chroma context rot, 18 models B | Does every block of the answer bear on this step's decision? |
| P8 | **Well: keeping track when progress is recited** | Manus todo list P; instruction drift within 8 rounds (arXiv 2402.10962) C; recap +16 to +17.5 points (Laban et al., arXiv 2505.06120) C | Does each answer say what is done, what is open now and what remains? |
| P9 | **Well: copying call-form examples; badly: copying content examples** | few-shot degrades reasoning models (DeepSeek-R1) C; few-shot calls 11 → 75% for a small model (LangChain) B; Agent Skills Can Be Harmful C | Is every example a call form or a filled NEXT, never a sample fix? |
| P10 | **Badly: uniform repeated observations** lead it to repeat its last decision | Manus P; self-conditioning C | Do consecutive windows differ visibly in their place-specific part? |

## How to give feedback

| # | The model does badly / needs | Evidence | Check question |
|---|---|---|---|
| F1 | **Finding where its error is**: it corrects well once told where | location given: +18 to +44 points (Tyen et al.) C; SWE-agent linter +3.0 C | Does every negative verdict say where: the offending line's text, the test name, the requirement, never a bare file line number? |
| F2 | **Repairing from prose or tracebacks**; it repairs best from a failing test with expected vs actual | Self-Debugging: unit test with expected/actual 88.8 vs plain 80.9, traces add little C; FeedbackEval: tests 61.0, prose 50.5 B | Does a test failure lead with the test name and expected vs actual, say the tests passed before and the code is back, without traceback frames? |
| F3 | **Doubting a verdict**: a false OK is worse than none | Reflexion 16.3% false-positive tests C; Olausson: model feedback wrong in 32 of 80 C | Is "OK" given only when a test ran the changed lines, and "kept, not checked" otherwise? |
| F4 | **Resisting a challenge**: "are you sure?" questions make it undo correct work | Huang et al.: 75.8 → 38.1 C; FlipFlop: 46% flips, −17% C | Does any answer ask it to reconsider without a new fact? Is every heuristic labelled as one? |
| F5 | **Knowing what is done** after compaction | recap +16/+17.5 points C; Manus P; Anthropic progress file V | Does every answer start with one line, from `git diff`, of what is kept, current and left? Is success one plain word, in the same place? |
| F6 | **Profiting from a third retry** of the same thing | 2 rounds give 76–95% of the gain (arXiv 2604.10508) C; Olausson C | After 2 failed edits does the work move on, with fresh code and no trace of the failed text? Do refusals and repeats stay off the failure count? |
| F7 | **Leaving a loop on its own**, worse with near-greedy decoding | Manus structured variation P; Laban (temperature 0) C | Does a repeated call get a different answer and a different filled NEXT? Does the third repeat move the work on in every step? |
| F8 | **Recovering from harness errors we cannot change** | missing tool: 39.9% recovery (Loud Failures, Quiet Failures) C; V10: 34 repeats O | Does the prompt say what each harness error means and the exact correct call, without showing the malformed form (B5)? |
| F9 | **Using advice**: facts help, instructions do not | falsification feedback +15 vs instructions +3 n.s. C; FeedbackEval prose worst B | Is every verdict a fact (test, value, line), with at most one line of instruction? |

## Check of the design at commit 8c95d1d: prompting items

| # | Status | Gap | Fix |
|---|---|---|---|
| P1 | no | `swe.md` ends with the raw statement, which can have its own headings and fences | `<issue>…</issue>` around it; fixed labels for quoted text in the windows |
| P2 | — | process item | Change one layout at a time; keep the old one for comparison |
| P3 | partly | "Change only what the requirements need; never remove behaviour…" (our main failure is changing too little) | "Make every change the requirements need, at every place they need it; keep the behaviour the statement does not mention, because the hidden tests also run the existing tests" |
| P4 | no | STOP REPEATING, NOT RUN, NOTHING, TIME IS UP, NOT changed | Plain case with the reason; at most one status word in capitals |
| P5 | no | "too long to show whole: change it with line numbers" against the prompt's whole definitions; TIME IS UP against NEXT | Remove both contradictions |
| P6 | partly | The answer forms are last; no goal line at the top | The step's decision in the first line and before NEXT |
| P7 | unknown | The used/calling code lists may hold distractors (not yet run) | Measure in the next run; keep only definitions sharing names with the place or the requirements |
| P8 | no | No progress line | As F5 |
| P9 | yes | Examples are call forms only | — |
| P10 | unknown | — | Watch in the next run |
| F1 | partly | A compile error gives a file line number | Show the offending line of the new code |
| F2 | no | BROKEN ends with traceback lines | Test name, expected vs actual (pytest `E` lines), "passed before, the code is back" |
| F3 | no | "OK" from tests that may not run the changed lines | "kept, not checked" when no selected test runs them |
| F4 | no | "NOTHING changed shares its words: is it covered?" from a word match | A labelled fact, or nothing |
| F5 | no | No progress line | One line from `git diff` at the top of every answer |
| F6 | partly | MAX_FAILS 2; a repeated edit counts as a failure | Keep 2; fresh code after leaving a place |
| F7 | no | The same repeat answer every time; only D1 and D2 move on at the third repeat | A different answer and NEXT each time; move on at the third repeat in every step |
| F8 | no | The prompt says nothing about "skill not found" or "argument required" errors | One sentence: such an error means the call's names were written with extra marks; the exact call to make |
| F9 | partly | Verdicts mix facts and advice | Facts first, one line of instruction |
