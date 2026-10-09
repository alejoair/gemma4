# CLAUDE.md

Rewritten from scratch on 2026-10-09 with the state of the project. Details moved to `docs/` (see the map below).

## The competition (Kaggle `gemma-4-developer-agent`)

Source: the competition pages and `HARNESS_README.md` in the competition data
(`kaggle competitions pages list --content --format json gemma-4-developer-agent`).

- **Goal.** Turn Gemma 4 into an agent that fixes a real issue in a Python repository. Prompts, skills, multi-agent
  configs and LoRA adapters are all part of a submission.
- **Submission** (`submission.zip`, root `agent.yaml`): a declarative Google ADK Agent Config, no Python entry points.
  `agent.yaml`, `prompts/`, `configs/` (`!include`), `sub_agents/`, `skills/<name>/SKILL.md` + `scripts/` (run in the
  task sandbox with `run_skill_script`), optional `adapters/<name>/` LoRA, optional `eval_config.yaml`.
  - Base model: only `gemma-4-31b-it-qat-w4a16-ct`, for every agent. LoRA per `LlmAgent` (up to 8, rank ≤ 128).
  - Limits: < 3 GiB unpacked; extensions .yaml/.yml/.md/.txt/.py (skill scripts only)/.json/.safetensors; no `..`, no
    symlinks, no `__pycache__`.
  - Generation: `max_output_tokens` and `thinking_budget` ≤ 32768; temperature, top_p, top_k, penalties, seed.
    Context 32,768 tokens; the harness compacts the history at 14,336 tokens.
- **Scoring.** SWE-bench style: the patch must pass the hidden fail-to-pass and pass-to-pass tests. About 120 test
  tasks from **private repositories**, half public and half private leaderboard.
- **Time.** 12 h for all tasks on 4×L4, about 6 min per task; no internet. `eval_config.yaml` sets the per-task budget;
  its `timeout_seconds` is also the hidden-test timeout.
- **Data.** 129 dev tasks from fastapi, rich, requests and httpx (statement, hints, reference patch, test patch),
  snapshots, code graphs, embeddings, offline wheels, the sandbox Dockerfile (Python 3.13).
- **Rules.** 1 submission per day (00:00 UTC), 2 final selections, teams up to 5, public external data and models
  allowed, winners license under Apache 2.0.
- **Dates.** Final submission deadline **2026-12-02 23:59 UTC**; entry/merger 2026-11-25; paper track 2026-11-12.
- **Prizes.** $37k / $18k / $10k, plus a $35k paper track.

## The user's rules (always)

- **Answer the user in Spanish only**, without mixing in English.
- **The only design principle: make the task easier for the 31B.** Every choice is judged by this alone.
- **Keep the design**: a deterministic procedure (`scripts/step.py`) that the model drives through `run_skill_script`,
  one bounded decision per step. Never propose going back to a plain prompt with the harness's native tools.
- **Check every design change against `docs/llm_checklist.md`** (what an LLM does well and badly, with evidence): for
  each item, closer / farther / neutral and why; record the table with the change. Moving farther on an item needs a
  stated reason. Add items when a trace or a paper shows a new strength or weakness.
- **The only valid test is a run against the model, monitored and analysed step by step** through the gateway API
  (local 12B) or the Kaggle traces (31B). Dry runs and replays catch script errors; they are not evidence that a change
  works. (The user removed the unit tests on 2026-10-09.)
- Never remove a piece of the design because it has few or no calls: find out why it is not used and wire it.
- Record design decisions and literature in the repository (this file and `docs/`), never only in the scratchpad.
- Every evaluated version (Kaggle eval run V, submission S) gets a row in `VERSIONS.md` as soon as it finishes.
- No Kaggle competition submission unless the user asks (S5 is queued at the user's request; see below).
- Never use Claude's outputs to train Gemma.
- No model identifiers in commits, PRs or code. Commit and push after each round of changes
  (branch `claude/practical-wright-jnki3c`, PR alejoair/gemma4#3).

## The agent now (2026-10-09)

`agent/` is the submission: one `LlmAgent` (`swe_step_agent`) with the tool `submit_patch` and the skill `fix-issue`
(named `swe` until 2026-10-09), whose only entry point is `skills/fix-issue/scripts/step.py`; `_*.py` are its modules.
`prompts/swe.md` is the system instruction (it ends with the statement, `{problem_description}`). `tools/build.py`
builds and validates the zip.

**The procedure** (a state machine in `_journal.py`; each call is the model's decision for the current step, and every
answer ends with the exact NEXT call):

| Step | The model sends | The script answers |
|---|---|---|
| S0 Start | the statement copied, then search terms | requirements and up to 10 candidates (BM25F over functions and classes, with the model's terms), each shown by name |
| D1 Choose | 1–3 candidate names (or `file::Name` for code off the list) | the related places (copies, async/sync twins, callers, overrides, exports) by name, and the first edit window |
| D3 Edit, per planned place | `["<place name>", "<whole new function or class>"]`, or `["<place name>", first, last, new lines]`, or `skip`, `back`, or another place's name | applies the edit (with repairs), checks the syntax, runs the related existing tests, undoes it if they break; the next place or the finish |
| D4 Finish | `submit_patch`, or `["back"]` for the requirements nothing covers, or an edit again | the patch summary and a coverage map |

The edit window shows the place's numbered code (whole up to 130 lines), the code it calls or reads (short members of
its own class as code) and the code that calls it, then what the change must do (requirements, the statement's
sentences about the behaviour, its example, an existing test that uses the code), then the answer forms and NEXT.
Questions are not answered in the edit step. Code is named by its qualified name; old ids (C2, P1) are still read.

**Budget and generation:** `eval_config.yaml` 240 s per command / 30 tool calls / 7 min / 40 turns; edits stop at
340 s (`_journal.EDIT_STOP`). `configs/sampling.yaml`: temperature 0.2, top_p 0.95, max_output_tokens 4096,
thinking_budget 512, include_thoughts true. Answers are capped at 10,000 characters.

**Decisions (newest last; the reasons are in `docs/design_single.md` and the commit messages):**
- 10-08: the old exploring scripts (`locate.py`, `show.py`, `edit.py`, `check.py`, `hints.py`) and the pipeline
  configs were removed: V4–V6 showed the model browsing instead of deciding. Lessons in `docs/old_scripts_lessons.md`.
- 10-09: no free-text plan step (the local model failed there in 4 of 5 runs); choosing plans the chosen places, their
  copies in a file of the same name and their async/sync twins.
- 10-09: every window repeats what the change must do; the statement is in the system instruction (never compacted).
- 10-09: harness 0.2.11: the agent declares only `submit_patch` (the task message lists only declared tools).
- 10-09 evening: the edit step had grown to about 12 accepted forms (questions, searches, `python -c`, candidates
  joining the plan) and 16 of the 24 fixes of two local batches were new forms or rules to stop the questions, with
  no change in the result (1/10 both times). **D3 redesign**: whole-definition edits by name, prepared context in the
  window, only edit / skip / back / a place name.
- 10-09: skill renamed `swe` → `fix-issue` (V10: 44% of calls had a mangled `skill_name` such as `「swe」`).
- 10-09 (the user's): **names instead of ids** (C<n>, P<n>, R<n>): V10 named code in 55 of 118 edit-step calls, the 12B
  merged ids with names, compaction removes the list that gives an id its meaning; code-localization tools in the
  literature all address code by name.
- 10-09 (the user's): **the LLM checklist** (`docs/llm_checklist.md`) and the rule above. Its check of the current
  design found 7 items not met (see "Next").

## Results so far

All rows are in `VERSIONS.md`. Kaggle evals (the 31B on the same 10 dev tasks): V2 1/10, V3 3/10, V4 1/10, V6 2/10,
V8 harness error, V10 1/10 (rich_3006 in almost every run). Local 12B batches with step.py: 1/10 and 1/10.
Competition submissions: S1 0.12 (sample agent), S2 0.05, S3 0.05, S4 (= V3) pending since 10-08, S5 (step procedure
v1, commit bea4f0f) queued: the hourly routine "Kaggle S4/S5 hourly check" submits it when S4 is scored and no
submission was made that UTC day.

**Running on 2026-10-09:** Kaggle notebooks `alejoair7/gemma4-eval-single` (V11 = commit 66423f9, before the D3
redesign) and `alejoair7/gemma4-eval-single-b` (V12 = commit bc3f2a3, D3 redesign + `fix-issue`, still with ids).
Compare them per task when both finish.

## Next (the plan agreed on 2026-10-09)

The checklist now has 7 W, 14 B, 10 P and 9 F items (`docs/llm_checklist.md`); its check of commit 8c95d1d lists the
gaps. The fixes, grouped, in this order:
1. **Truthful status** (B11, F3, F4, F5, P8): a progress line from `git diff` at the top of every answer; the finish
   summary from the diff; "OK" only when a selected test runs the changed lines, else "kept, not checked"; coverage as
   labelled facts, never "is it covered?".
2. **Feedback** (F1, F2, F7, F9, B5, P4): test failures as test name + expected vs actual, "passed before, the code is
   back"; compile errors with the offending line's text; refusals that describe instead of quoting; a repeat answer
   that differs each time and moves on at the third repeat in every step; no capitals or pressure words.
3. **Budget** (B10, W4, P5): time used and the edit deadline in every answer; at time-up only submit_patch.
4. **Prompt** (P1, P3, P5, P6, F8, B2): the statement inside `<issue>…</issue>`; positive rules with their reason and
   who judges the patch (the hidden tests); no contradictions; the step's decision at both ends of each answer; what
   the harness's skill errors mean; any copy of the statement accepted.
5. **A -/+ diff after an edit** (W5).
6. **Stub scripts** for invented names (`show.py`, `grep.py`, …) (B6).
7. **Window content** (W1, W3, W6, P7): evidence lines under each candidate; the right part of long places; cleaner
   requirement extraction; context lists without distractors.
Then the 10 local tasks with the 12B, monitored step by step, then a Kaggle eval. Separately, an A/B run of Gemma's
recommended sampling (temperature 1.0, top_p 0.95, top_k 64) against 0.2.

## Repository map

- `agent/`: the submission (above).
- `tools/build.py` (build + validate), `tools/bench_locate.py` (129-task localization benchmark; snapshots via
  `tools/fetch_snapshots.sh`).
- `VERSIONS.md`: every eval run and submission.
- `docs/llm_checklist.md`: the checklist; `docs/llm_strengths.md`, `docs/prompting_input.md`,
  `docs/prompting_feedback.md`: the literature behind it; `docs/audit_v10.md`: the critical audit of the V10 traces.
- `docs/design_single.md`: the design method (HTA → function allocation → workflow vs agent → detailed design), design
  v1, its verification and the local batches. `docs/scripts_spec.md`: what each script must do, with the literature
  per script. `docs/literature.md`: the 31B's failures in V1–V6 with the techniques from the literature, and the papers
  behind the design. `docs/adk_harness.md`: ADK config and harness behaviour, the ADK features and which technique
  each implements. `docs/public_notebooks.md`: the public notebooks (0.17–0.18). `docs/old_scripts_lessons.md`.

## How a change is tested

1. Change the scripts or the prompt, and check the change against `docs/llm_checklist.md`.
2. Dry run: `scratchpad/dry_d3.sh <task> reset`, then `scratchpad/dry_d3.sh <task> <args…>` runs one `step.py` call on
   `scratchpad/repos/<task>` through `harness_like.py` (which deletes the skill's files when the script ends, as ADK
   does: nothing may run at exit).
3. Replay the real calls: `venv/bin/python replay_step.py <skill dir> step_calls_v10.json` (and `step_calls.json`)
   must print NO PROBLEMS.
4. Build (`tools/build.py <dir>`) and validate (`scratchpad/validate_submission.py <dir>/submission`).
5. Run with the local 12B: `run_local12.py <submission dir> e10 <out dir>` (`e10/tasks.jsonl` holds the tasks), with
   the proxy `stream_proxy.py` on 127.0.0.1:8765.
6. Monitor it while it runs: every 30–45 s read each new call (`scratchpad/monb.py <since> <from>`, or the curl calls
   below). Stop at the first system problem, fix it and start again. Never wait for the end, never use a background
   monitor.
7. Analyse each finished task (result, calls, wasted calls and their cause, the fix) and record it in
   `docs/design_single.md`.
8. Kaggle eval: copy the build to `scratchpad/ds3/v<N>`, `kaggle datasets version -p ds3 -r zip`, wait for "ready",
   set `SUBMISSION` in `scratchpad/kn_single*/run.py`, `kaggle kernels push`. Outputs: `kaggle kernels output`.
   Traces: `scratchpad/trace_brief.py`, `trace_stats.py`.

Acceptance before a Kaggle submission: the replays print no problems, the local model runs the 10 tasks without system
faults, then a Kaggle eval of the 10 tasks. If a launch is asked for earlier, say which criterion is not met.

Local runs are not Kaggle runs: llama-server's reasoning budget is 3072 tokens (Kaggle: 512) and a 12B call can take
60 s. A faithful local loop needs `--reasoning-budget 512` on the server (ask the peer session).

## Harness essentials (swegemma 0.2.11; details in `docs/adk_harness.md`)

- A call to an undeclared tool or a tool exception becomes an error response (`ToolErrorPlugin`); the task goes on.
  The ADK's own errors (`Skill '「swe」' not found`, `Argument 'skill_name' is required`) never reach our script.
- The task message lists only the declared tools. With `skills:`, the ADK adds the skill tools and an instruction to
  `load_skill` first; our prompt says it is not needed.
- If the agent ends without `submit_patch`, the harness nudges it (up to 3 times) and takes the `git diff` anyway; an
  exception or a budget stop keeps the diff. Changes to test and runner-config files are dropped from the patch.
- `include_thoughts: false` strips thoughts from the history; we use `true`.
- `LoopAgent` has no early exit; `ParallelAgent` branches share `/workspace`; an `agent_tool` sub-agent starts with a
  fresh session and its calls count against the budget. Callbacks validate but do nothing; no `planner`,
  `code_executor` or `output_schema`. vLLM cannot be forced to `tool_choice="required"` from the config.
- Kaggle runs vLLM with `--tool-call-parser gemma4 --reasoning-parser gemma4`; the model's `generation_config.json`
  defaults (temperature 1.0, top_k 64, top_p 0.95) apply to whatever we do not set.

## The local model and the peer session

A Claude Code session on the user's Windows machine (`session_01K7Fs6ULXEUTHQZQXS2wArN`, "Programa winget para máquina
virtual con CLI", via Remote Control) runs the local infrastructure:
- `https://llm.rayflow.dev`: llama.cpp `llama-server` with `gemma-12b` (google/gemma-4-12b-qat Q4_0 GGUF, context
  32768, parallel 1, RTX 3090), global `--reasoning-budget 3072`; thinking can be turned off per request but a
  per-request budget is ignored. It can restart the server with other options or models.
- The gateway and its monitoring API (`/_monitor/*`); it can route a model name to base + LoRA (`routes.json`).
- The fine-tuning stack (`F:\llm-lab`: llama.cpp b11476 with CUDA, Unsloth/PEFT/TRL venv, LoRA → GGUF).

Work with it: ask it whenever something depends on the local machine (is the model served, why requests fail, option
changes, GPU windows, gateway changes); do not guess from silence. Send with the claude-code-remote `send_message`
tool (`SendMessage` cannot reach it). It warns before it unloads the model; tell it when a run is in progress. Never
ask it to do what either session was denied; take that to the user. It was inactive on 2026-10-09.

## Monitoring local runs

Add `-A swe-monitor` (Cloudflare blocks default user agents):

```bash
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/report?minutes=5"                    # model, GPU, in flight
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/log?since=<t>"                       # one line per request
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/conversation?since=<t>&n=50"         # inputs and replies
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/conversation?since=<t>&brief=1"      # texts cut to 300
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/conversation?since=<t>&only=failed"  # length cuts, errors
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/request/<id>"
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/events?since=<t>"                    # restarts, disconnects
```

The scratchpad (`/tmp/claude-0/-home-user-gemma4/<session>/scratchpad`) holds the venv with the Kaggle wheels, the
repos, the Kaggle dataset folder `ds3/`, the notebook folders `kn_single*/` and the helper scripts named above. It is
lost when the container is recycled; anything that matters goes into the repository.
