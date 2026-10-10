# Audit of every text the model reads (2026-10-09)

Everything below was read from a real request (the first call of the local run of 2026-10-09 13:03, request
`69a756e4` on the gateway; the Kaggle traces have the same parts) and from the installed ADK (google-adk 1.36.1,
`tools/skill_toolset.py`) and harness (swegemma 0.2.11). Each problem names the checklist item it breaks
(`docs/llm_checklist.md`). "Ours" means we control the text; "ADK" and "harness" mean we cannot change it and can only
answer it from our own text.

## What the model reads, in order

| # | Part | Who writes it | Where it comes from |
|---|---|---|---|
| 1 | Our system instruction (about 1,100 tokens + the statement) | ours | `agent/prompts/swe.md`, with `{problem_description}` |
| 2 | "You are an agent. Your internal name is "swe_step_agent". The description about you is "One agent that fixes the issue by making one bounded decision per step of the deterministic fix-issue/scripts/step.py procedure."" | ADK, from our `agent.yaml` | `name` and `description` in `agent/agent.yaml` |
| 3 | The skills instruction: "You MUST use the skill tools … If a skill seems relevant … you MUST use the `load_skill` tool … follow them exactly … Use `load_skill_resource` to view script content first if needed … scripts … can be run via bash" | ADK | `_DEFAULT_SKILL_SYSTEM_INSTRUCTION`; not configurable from `agent.yaml` |
| 4 | The task message (user turn): "You are evaluating a software engineering task", the statement again, the budget (7 min, 30 calls, 40 turns), execution rules (240 s per command, "Command output limit: 5000 characters", offline), and six instructions: "Inspect existing codebase conventions and test files before making edits", "Verify your implementation using targeted tests or inline assertions before submitting", "Call submit_patch only after your implementation is complete and verified", "As your final action … return a text-only response", plus the workspace tree | harness | swegemma; not configurable |
| 5 | Tool declarations: `submit_patch` ("Capture the current working tree modifications as the agent's submission … can submit multiple times; only the final submission is evaluated"), `list_skills`, `load_skill`, `load_skill_resource`, `run_skill_script` ("Executes a script from a skill's scripts/ directory"; `skill_name`, `file_path` with the example `'scripts/setup.py'`, `args` as an object **or** a list, `short_options`, `positional_args`) | ADK / harness | not configurable |
| 6 | `SKILL.md` (only if the model calls `load_skill`) | ours | `agent/skills/fix-issue/SKILL.md`: two lines |
| 7 | Every answer of `step.py` | ours | the script (checked item by item in `docs/llm_checklist.md`) |

The 5,000-character output limit in part 4 applies to `run_command` and the graph tools, not to skill scripts (the
local run received 7,017-character answers whole); it only misleads the model about our answers' length.

## Problems

| # | Problem | Part | Breaks | Fix |
|---|---|---|---|---|
| 1 | **Our text contradicts the ADK's**: we say "you do not need list_skills, load_skill or load_skill_resource"; the ADK says "you MUST use the `load_skill` tool … follow them exactly" and "use `load_skill_resource` to view script content first". The model reconciles them with its 512 thinking tokens; once (local run, 10-08) it read `step.py`'s source through `load_skill_resource` (a 28.8k-token prompt) | 1 vs 3 | P5, B12 | Stop contradicting it: make loading harmless and useful. `SKILL.md` holds the same steps as our prompt, so "load it and follow it" agrees with us; our prompt says "loading the skill shows these same steps; you can start with step 1 directly". Keep the scripts' source unhelpful to read: a short module docstring at the top of `step.py` that says "do not read this file: call it" |
| 2 | **The harness's six instructions contradict the procedure** and come after ours (the later instruction tends to win): "inspect … test files before making edits", "verify your implementation using targeted tests or inline assertions", "call submit_patch only after … verified" | 4 vs 1 | P5, B12, B9 | Our prompt answers each one explicitly: "The task message asks you to inspect the code and tests and to verify your change: the script does both. It shows the code, the code around it and an existing test, and runs the existing tests after every edit. Do not look for other ways to read files or run tests." Plus the reason (the budget) |
| 3 | **The agent's name and description use our jargon**: "swe_step_agent" (the old skill name), "bounded decision", "deterministic … procedure" | 2 | W6, B3 | `name: issue_fixer`; `description: "Fixes the issue by calling the fix-issue script once per step and making the decision that step asks for."` |
| 4 | **The statement has no boundary** and appears twice (our system text and the task message). Its own headings, emoji, `@mentions`, URLs and code fences run into our sections | 1, 4 | P1, P7 | Put ours inside `<issue>` … `</issue>` with one line saying it is the same statement as in the task message. Keep it in the system text (it is never compacted) |
| 5 | **The prompt's rules are prohibitions without a goal**: "Change only what the requirements need; never remove behaviour …", "Never change tests". Our main failure is patches that change too little (V6: 4–14 lines where the reference has 34–654) | 1 | P3 | "The patch is judged by hidden tests: the new tests of the issue and all the existing tests. So make every change the requirements need, at every place they need it, and keep the behaviour the statement does not mention. Test files are replaced by the hidden ones, so changes to them are lost." |
| 6 | **No one tells the model who judges the result**, so the reasons behind the rules are missing | 1 | P3, F9 | As in #5, in the first lines |
| 7 | **Step 1 asks for a long exact copy** ("the issue statement copied exactly as the first item … first 3000 characters"), and the script refuses a one-line copy | 1, 7 | B2 | Ask for "the statement's first paragraph, or all of it"; the script accepts any copy and takes the rest from what it has |
| 8 | **The call-form examples write the raw control tokens** (`<|tool_call>`, `<|"|>`, `<tool_call|>`) in the system text. The tokenizer probably reads them as the real special tokens (Hugging Face tokenizers match special tokens in text by default), so the system turn contains what look like real tool calls, with placeholders such as `<statement>` inside string delimiters | 1 | P2, P9, B4 | Measure, one change at a time (P2): (a) as now; (b) the same examples written as plain JSON-like text (`run_skill_script(skill_name="fix-issue", file_path="scripts/step.py", args=["…"])`). The 「swe」 rate and malformed calls decide |
| 9 | **The model is not told what the ADK's errors mean.** "Skill '…' not found" and "Argument 'skill_name' is required" never reach our script; in V10 the model answered them by repeating the same call up to 34 times | 3, 5 | F8, B5 | One sentence, without showing the malformed form: "If a call answers that the skill was not found or that an argument is required, the skill_name or file_path was written with extra characters around it: write them exactly as fix-issue and scripts/step.py" |
| 10 | **The budget is only in the task message**, which may be compacted, and the model cannot count time | 4 | B10, F5 | The script states the time used and the deadline in every answer (plan item 3); the prompt says so |
| 11 | **`run_skill_script` offers more forms than we accept**: `args` as an object, `short_options`, `positional_args`; the `file_path` example is `scripts/setup.py` | 5 | B8, P9 | Our prompt says "args is always a list of strings; do not use short_options or positional_args". The script reads an object or positional args too, as a repair (it already gets them as argv) |
| 12 | **"Place", "window", "listed place", "planned place" are our words**; the model's words are function, class and code | 1, 7 | W6 | Say "the function or class to edit" where "place" is used, or define "place" once in the prompt as "a function, class or file part that may need a change" |
| 13 | **`SKILL.md` is two lines**; if the model loads it (as the ADK asks), it learns nothing and spends a call | 6 | P5, B12 | `SKILL.md` = the steps and call forms of the prompt (one source for both) |
| 14 | **The final text**: the harness asks for "a text-only response reporting your completion"; our prompt asks for "one sentence about the change" after submit_patch. They agree; keep | 1, 4 | — | — |
| 15 | **No goal line at the end of the system text**: it ends with the statement and "Your first call is step 1.", then the ADK and harness text follow | 1 | P6 | End our text with a two-line summary: what to do now (step 1) and what the whole task is |

## Names the model reads

| Name | Where | Verdict |
|---|---|---|
| `fix-issue` | skill name | descriptive (renamed from `swe`) |
| `swe_step_agent` | agent name, read in part 2 | rename (`issue_fixer`) |
| `scripts/step.py` | file path in every call | acceptable: one script, said to take "the decision for the current step". A more descriptive name (`scripts/next_step.py`) is possible but changes a string the model has to write in every call; measure before changing (P2) |
| `prompts/swe.md`, `swe_state_*` | not read by the model | rename for consistency only (`prompts/fix_issue.md`) |

## Control tokens and escaping (checked 2026-10-09 with the Gemma 4 tokenizer and chat template)

Checked with `google/gemma-4-31B-it`'s `tokenizer.json`, `tokenizer_config.json` and `chat_template.jinja`
(transformers 5.18), rendering the real first request of 2026-10-09 the way vLLM does (`apply_chat_template`, then
`encode` without added special tokens; `scratchpad/tok_check.py`).

1. **The examples in our prompt become real control tokens.** `<|tool_call>` (id 48), `<tool_call|>` (49) and `<|"|>`
   (52) are registered special tokens, and the tokenizer matches them in plain text. The system turn holds 5
   `<|tool_call>`, 5 `<tool_call|>` and 62 `<|"|>`: about 36 of these come from our five examples, the rest from the
   tool declarations the template adds. So the system turn contains five real-looking tool calls whose string values
   are placeholders (`<statement>`, `<candidate name>`). They match exactly the form in which the template renders the
   model's own calls in the history (`<|tool_call>call:run_skill_script{args:[<|"|>Session.send<|"|>],file_path:<|"|>
   scripts/step.py<|"|>,skill_name:<|"|>fix-issue<|"|>}<tool_call|>`).
2. **Every answer of our script reaches the model as escaped JSON.** The ADK's LiteLLM adapter serializes the tool's
   result dict with `json.dumps` (`google/adk/models/lite_llm.py`, `_safe_json_serialize`); the template wraps the
   string as `<|tool_response>response:run_skill_script{value:<|"|>{"skill_name": …, "stdout": "…"}<|"|>}`. So the
   model reads our code windows with `\n` for every line break and `\"` for every double quote, all on one line. A V10
   edit window (requests_7328) had 98 escaped line breaks and 62 escaped quotes, and its NEXT line read
   `skill_name \"swe\", file_path \"scripts/step.py\" and args [\"P1\", …]`. Code in JSON is edited worse than
   code in plain text (Aider, `docs/llm_strengths.md` 1.7).
3. **The model sees four quote forms around the same names**: `"fix-issue"` in our system prose, `\"fix-issue\"` in
   every NEXT line, backticks in the ADK's text, and `<|"|>fix-issue<|"|>` in its own calls. Its malformed calls
   replace the `<|"|>` token with another quote-like character (`「swe」`, a backtick, `\"`), and a malformed call
   in the history is rendered back as `skill_name:<|"|>「swe」<|"|>`, which the model then copies. That the quote
   variety causes the mangling is a hypothesis; `「swe」` existed in V1, before the examples were added (V2), and the
   rate fell after V2 (31% → 21%), then rose to 44% in V10.
4. **The literal `\n` and `\"` in the model's edits**, which `_args.py` and `_edit.py` repair, are what it reads: it
   copies the escaped form of the code in the window.

What follows (each one measured, one at a time where possible, P2):
- **Quote hygiene in the script's answers** (cannot remove the JSON escaping, can remove our own quotes): write lists
  with single quotes (`['skip']`, `['Session.send', '<the whole new function or class>']`; json.dumps does not escape
  them), write constant names bare (`skill_name fix-issue, file_path scripts/step.py`), and use no double quotes in our
  own sentences. Code lines keep theirs. In our system prose, also write the names bare.
- **The prompt's raw call examples**: keep them (they are the exact format and V2 lowered the malformed rate) or replace
  them with a plain description; decide by an A/B run on Kaggle, measuring the malformed-call rate (the 12B does not
  show `「swe」`, so only the 31B can measure it).
- The edits' unescaping repairs stay: the escaping cannot be removed.

## Order of the changes

The prompt changes go into plan item 4 (`CLAUDE.md`, "Next"): #1, #2, #3, #4, #5–#6, #7, #9, #11, #12, #13, #15
together, since they are wording; #8 (raw control tokens) as its own A/B measurement; #10 with the budget line in the
script.
