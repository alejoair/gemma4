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
- **Every change to the system must be generic** (the user's, 10-15): it must hold for any repository and any issue, never a rule for one task, library or name seen in a trace (the trace is only the evidence). Each change's checklist table says why it is generic.
- Never remove a piece of the design because it has few or no calls: find out why it is not used and wire it.
- Record design decisions and literature in the repository (this file and `docs/`), never only in the scratchpad.
- Every evaluated version (Kaggle eval run V, submission S) gets a row in `VERSIONS.md` as soon as it finishes.
- **Model runs use only the competition's model on the Colab vLLM** (the user's, 2026-10-15): `tools/colab_vllm.ipynb`
  on a Colab Pro G4 (RTX PRO 6000 Blackwell, 96 GB), vLLM 0.19.1 with the harness's own options (gemma4 parsers,
  `--reasoning-config` with `<|channel>`/`<channel|>`), reached through `tools/step_gate.py --stream-upstream
  --api-key-file <scratchpad key>`. Not the local llama.cpp server, not Haiku. The URL and key are given by the user
  each session; the key stays in the scratchpad.
- No Kaggle competition submission unless the user asks.
- Never use Claude's outputs to train Gemma.
- No model identifiers in commits, PRs or code. Commit and push after each round of changes
  (branch `claude/practical-wright-jnki3c`, PR alejoair/gemma4#3).

## The agent now (2026-10-13)

Two builds share the scripts. `agent/` is the base: one `LlmAgent` (`issue_fixer`) with the tool `submit_patch` and the
skill `fix-issue`, whose entry point is `skills/fix-issue/scripts/step.py` (`_*.py` are its modules) and whose system
instruction is `prompts/fix_issue.md` (it ends with the statement, `{problem_description}`). **The version evaluated since
V17 is the variant `variants/agent_tool/`** (`tools/build.py <dir> --variant agent_tool` overlays it on `agent/`; its
`REMOVE.txt` drops the base prompt): the coordinator `issue_fixer` (`prompts/coordinator.md`, step.py with
`_role.ROLE = 'coordinator'`) chooses the places and calls two `agent_tool` sub-agents, `specify_fix` (`spec.py`: one
sentence per place) and `edit_place` (`open.py`, then `edit.py`: one place, its sentence, its code). `spec.py`,
`open.py` and `edit.py` exist only in the variant. `tools/build.py` builds and validates the zip.

The table below is the base procedure; in the variant the coordinator does S0 and D1, `specify_fix` writes the
sentences, each `edit_place` does D3 for its own place, and the coordinator submits (D4). Since 10-13 a place can be a
new file (a path that does not exist yet, named by the specifier; it is created first, `['<path>', '<whole file>']`),
and an edit undone twice by the same existing tests, each failing only on its own assertion, is kept with a warning
(the test checks the old behaviour the issue changes).

**The procedure** (a state machine in `_journal.py`; each call is the model's decision for the current step, and every
answer ends with the exact NEXT call):

| Step | The model sends | The script answers |
|---|---|---|
| S0 Start | the statement copied, then search terms | requirements and up to 10 candidates (BM25F over functions and classes, with the model's terms), each shown by name |
| D1 Choose | 1–3 candidate names (or `file::Name` for code off the list) | the related places (copies, async/sync twins, callers, overrides, exports) by name, and the first edit window |
| D3 Edit, per planned place | `["<place name>", "<whole new function or class>"]`, or `["<place name>", first, last, new lines]`, or `skip`, `back`, or another place's name | applies the edit (with repairs), checks the syntax, runs the related existing tests, undoes it if they break; the next place or the finish |
| D4 Finish | `submit_patch`, or `["back"]` for the requirements nothing covers, or an edit again | the patch summary and a coverage map |

The edit window shows the place's numbered code (whole up to 62 lines in the base, 120 for an editor, longer in
parts), the code it calls or reads (short members of
its own class as code) and the code that calls it, then what the change must do (requirements, the statement's
sentences about the behaviour, its example, an existing test that uses the code), then the answer forms and NEXT.
Questions are not answered in the edit step. Code is named by its qualified name; old ids (C2, P1) are still read.

**Budget and generation:** `eval_config.yaml` 600 s per command / **5 min per task since 10-15** (the user's: S6, with 60 min, finished without a score, most likely past the 12 h; 7 min until 10-12, 60 min from 10-12 to 10-15); the stages route submits at 4 min 30 s (`BUDGET_S`); tool calls 200 and turns 300, not binding (since
10-11, the user's: time is the competition's only real limit, 12 h for about 120 tasks); no limit of our own
inside the script (the 340 s edit stop was removed on 10-11 at the user's request). Sampling per agent: the coordinator
`configs/sampling.yaml` (temperature 0.2, top_p 0.95, max_output_tokens 4096, thinking 512); the agent_tool sub-agents
use the model's own defaults (temperature 1.0, top_k 64, top_p 0.95, checklist B14): `specify_fix` thinking 4096 /
output 8192, `edit_place` thinking 2048 / output 8192; include_thoughts true everywhere. Answers are capped at 7,000
characters (the specifier's view at about 27,000).

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
- 10-10: **finding instead of reading** (after V14: 50 range reads were a search done by hand, "I can't grep"): no
  read form; a name, text or range gives the functions and classes that hold it with the matching lines, to choose
  from; naming code chooses it (it replaces the places not edited yet); an edit of code that is not a place chooses
  it; windows show the imports the place uses and the library code it calls. Reasons and checklist table in
  `docs/design_single.md` ("Finding instead of reading").
- 10-12 (the user's): **microtasks** (agent_tool variant). A `specify_fix` sub-agent (fresh context, thinking 4096,
  `spec.py`) sees the code of all the plan's places at once and writes one sentence per place. Each `edit_place`
  editor gets its place, that sentence and the definitions it uses, and no reading. Reason: V18-V19 editors spent
  88-95% of the time reading to understand the fix. Design, context per role and checklist in `docs/design_single.md`
  ("Redesign: microtasks").
- 10-14 (the user's): **one responsibility per agent** (`docs/design_stages.md`): stages 0-9 driven by a list of
  requirements quoted from the statement (never by a reproduction test, which is optional evidence), agents
  requirements / reproduce / locate / scope / specify / edit / review, the coordinator only routes. Designed, not built
  yet.
- 10-26 (the user's): **a lazy repository map** (`_map.py`, `docs/design_fast.md`): every call rebuilt the index and
  the impact table from all files (moto_5865: 130 s of script time per task; django 96 s). Now git grep names the files
  a question needs, each file's record is kept on disk by its content, BM25's statistics are estimated, and the
  scripts' bytecode is reused (`_boot.py`): moto 8 s, django 11 s, the impact table's answers identical.

## Results so far

All rows are in `VERSIONS.md`. Kaggle evals (the 31B on the same 10 dev tasks): V2 1/10, V3 3/10, V4 1/10, V6 2/10,
V8 harness error, V10 1/10 (rich_3006 in almost every run). Local 12B batches with step.py: 1/10 and 1/10.
Competition submissions: S1 0.12 (sample agent), S2 0.05, S3 0.05, **S4 (= V3) 0.13** (scored 10-10), S5 (step
procedure v1, commit bea4f0f, 5-min budget) **0.03** (checked 10-13). S6 (microtasks, 60 min) finished without a
score. **S7 (= V25, the stages build, 5 min) 0.06** (checked 10-16; V25 judged 4/10 on the Kaggle eval). **S8 (= vf5, the fast build, 5 min) 0.06** (checked 10-18; vf5 judged 12/30 on the Kaggle eval). **S9 (= vf11, vf5's specify plus the generic-repository fixes) 0.05** (checked 10-19); **S13 (= vf21, the lazy repository map) 0.10** (checked 10-27). **S14 (= vf22, native thinking off with think.py, plus the audit fixes) 0.00** (checked 10-28; likely the vLLM tool-parser hang that thinking-off builds hit, with no watchdog in the scorer); the gap between the internal eval and the leaderboard is in `docs/score_gap.md`.

Latest (2026-10-09): V11 (before the D3 redesign) 1/10, 22% malformed calls; V12 (D3 redesign) 1/10, 35%; V13 invalid
(vLLM froze mid-run); V13b (all checklist fixes, names, new prompt, `file_path step.py`) 1/10, **4% malformed calls**,
submit_patch in all 10 tasks. `tools/kaggle_eval.py` (the Kaggle eval script) now has a vLLM /health watchdog.

## Open gaps

The gaps of the current design are listed in `docs/llm_checklist.md` (its "Check of the design" tables) and
`docs/prompt_audit.md`.

## Repository map

- `agent/`: the submission (above).
- `tools/colab_vllm.ipynb` (10-15): the competition's model and vLLM (0.19.1, the gemma4 parsers) on a Colab Pro GPU,
  served through a Cloudflare quick tunnel with a key; `tools/step_gate.py --stream-upstream --api-key-file <file>`
  reaches it (a quick tunnel cuts a response whose first byte takes over 100 s). The tunnel's domain must be in the
  environment's allowed domains. To restart vLLM there, kill its engine too (`pkill -9 -f 'VLLM::EngineCore'`, then
  wait for the VRAM to drop): `proc.kill()` leaves the engine holding 91 GB and the new server fails (10-15). Stopping a
  cell can kill the tunnel or vLLM started from that kernel; the repository's notebook starts both in new sessions.
- `tools/swegym.py` (10-21, the user's): SWE-Gym's tasks (Hugging Face, Lite 230 / full 2,438, 11 repositories) as a
  second task set in the competition's data layout (tasks.jsonl, snapshots/), with one venv per repository and version
  for the subprocess sandbox; run local_eval/judge with that venv's Python. 39 Lite tasks score here (moto 35,
  conan 4); the list and why the other repositories do not: `docs/swegym.md`.
- `tools/build.py` (build + validate), `tools/bench_locate.py` (129-task localization benchmark; snapshots via
  `tools/fetch_snapshots.sh`).
- `VERSIONS.md`: every eval run and submission.
- `docs/llm_checklist.md`: the checklist; `docs/llm_strengths.md`, `docs/prompting_input.md`,
  `docs/prompting_feedback.md`: the literature behind it; `docs/audit_v10.md`: the critical audit of the V10 traces. `docs/prompt_audit.md`: every text the model reads
  (ours, the ADK's, the harness's) and its problems. `docs/prompt_audit_stages.md`: the same for the stages build, with the
  checklist applied item by item (10-15).
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
3. Replay the real calls of the latest traces on clean snapshots: `tools/replay_trace.py <build>/submission/skills/fix-issue/scripts
   scratchpad/repos/<task> <trace_<task>.json>` must print NO PROBLEMS, and its answers are diffed with the run before
   the change (the old `replay_step.py` was lost with the scratchpad on 10-12; see `docs/feedback_audit.md`, "Why the
   step-by-step local runs did not find these faults"). Local runs rotate over tasks of different shapes.
4. Build (`tools/build.py <dir>`) and validate (`scratchpad/validate_submission.py <dir>/submission`).
5. Run with the local 12B: `run_local12.py <submission dir> e10 <out dir>` (`e10/tasks.jsonl` holds the tasks), with
   the proxy `stream_proxy.py` on 127.0.0.1:8765.
6. Monitor it while it runs: every 30–45 s read each new call (`scratchpad/monb.py <since> <from>`, or the curl calls
   below). Stop at the first system problem, fix it and start again. Never wait for the end, never use a background
   monitor.
7. Analyse each finished task (result, calls, wasted calls and their cause, the fix) and record it in
   `docs/design_single.md`.
8. Kaggle eval: copy the build to `scratchpad/ds3/v<N>` and `tools/{kaggle_eval,judge,eval_env,hermetic,journals}.py` to `scratchpad/ds3/bench/`, `kaggle datasets version -p ds3 -r zip`, wait for "ready",
   set `SUBMISSION` in `scratchpad/kn_single*/run.py`, `kaggle kernels push`. Outputs: `kaggle kernels output`.
   Traces: `scratchpad/trace_brief.py`, `trace_stats.py`.

Acceptance before a Kaggle submission: the replays print no problems, the local model runs the 10 tasks without system
faults, then a Kaggle eval of the 10 tasks. If a launch is asked for earlier, say which criterion is not met.

**How a patch is judged (since 2026-10-14, local and Kaggle alike):** `tools/judge.py` runs the harness's own
verification in the same sandbox setup: the reference patch twice, no patch twice (a test whose outcome differs between
two runs of the same code is flaky and left out), then the agent's patch (repeated once when it fails something).
Outcomes come from the JUnit XML. FAIL_TO_PASS = failing without a patch, passing with the reference; `verdict` =
resolved when no test fails that passes with the reference and every test function the test patch adds passes (the
harness's own required-node rule); `official_feasible` = the reference itself passes the harness's whole-file check
here (rich's pygments-dependent tests and requests' network tests make it false for some tasks: no patch can score
there in an environment like ours). An empty patch, a patch that does not apply or a run that did not finish is not
resolved. The cache is keyed by the task, patch and test patch, with an environment fingerprint. `local_eval.py` and
`kaggle_eval.py` add `judged` and `official_feasible` to every row; `tools/eval_env.py` makes the subprocess sandbox
import the task's own code (src/ first, then the workspace; the official fast path's directories by a .pth after
site-packages); `tools/hermetic.py` gives a local repository the harness's pytest.ini, conftest and baseline commit
(replays use it). The documented public image shadows src/ packages with installed wheels; whether the private scorer
does is unknown. For patches already made: `tools/judge.py --data <comp dir> --patches <dir of patch_<task>.diff>
--cache <dir>`. The Kaggle dataset's `bench/` holds `kaggle_eval.py`, `judge.py`, `eval_env.py` and `hermetic.py`.

**The skill's own test checks (`_tests.py`, rewritten 2026-10-14 after four audits, docs/design_single.md):** per-test
outcomes from a pytest plugin; the original code run on the same groups (cached); stand-in modules decided on the
original code only; an import check of the changed modules; a new failure must fail again alone and pass just before the
edit; tests chosen from a once-per-task call map (which tests call which function), with the lexical score as fallback.

Local runs are not Kaggle runs: since 2026-10-14 the local model is the same 31B, but as a llama.cpp GGUF (QAT Q4_0, not
Kaggle's w4a16 on vLLM) with a 17,664-token context, MTP and n-gram speculation (the 26B A4B from 10-11, the 12B before),
and llama.cpp applies `min_p 0.05` by default
(vLLM: 0). From 2026-10-10 23:21 UTC the server ran with Kaggle's reasoning budget (512); the peer reports it was later
raised to 1024 at the 'editor' experiment's request, and it runs with 1024 now (10-14; the user decides 512 or 1024). The model's
`generation_config.json` sampling defaults (see below); before that its budget was 3072.

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

On 2026-10-14 drive F: of the PC was damaged after unexpected restarts: F:\llm-lab (LoRA adapters, data, the 24 GB
training base, the 12B copy) is not accessible until the user repairs it (chkdsk as administrator); the server was
rebuilt in C:\llm-lab-c with the 26B GGUF. A Claude Code session on the user's Windows machine (`session_01K7Fs6ULXEUTHQZQXS2wArN`, "Programa winget para máquina
virtual con CLI", via Remote Control) runs the local infrastructure:
- **From 2026-10-14 (the user's decision) the local server serves the 31B as `gemma-31b`**, with a 17,664-token
  context and a q8_0 KV cache (not Kaggle's 32,768). Options: `llama-server -m gemma-4-31B_q4_0-it.gguf -ngl 99
  --parallel 1 -c 17664 --alias gemma-31b --jinja --reasoning-budget 512 --temp 1.0 --top-k 64 --top-p 0.95 -fa on
  -ctk q8_0 -ctv q8_0 --spec-type draft-mtp,ngram-mod --spec-draft-model mtp-gemma-4-31B-it-Q4_0.gguf
  --spec-draft-n-max 3`.
  Measured by the peer:
  - at temperature 1.0, 79-94 tok/s overall (code about 80, text about 54, edits about 148), with 51-55% of the MTP
    draft accepted;
  - at temperature 0.2, 88.5 tok/s;
  - without speculation, about 32 tok/s;
  - the prompt runs at 1,012 tok/s (15,031 tokens in 15.5 s);
  - 19.8 of 20.5 GB of VRAM; one request at a time.

  Send `--model gemma-31b`: llama-server does not
  check the field, so a run that sends `gemma-26b` would get the 31B silently. The reasoning budget is global, 512 since
  10-14 (Kaggle's coordinator's); the stages build's sub-agents ask for 1024-2048, which the server ignores.
- `https://llm.rayflow.dev`: llama.cpp `llama-server`. **From 2026-10-11 to 10-14 the model was `gemma-26b`** (Gemma 4 26B A4B
  QAT Q4_0, MoE with 3.8B active parameters; the 12B, `gemma-12b`, until then). llama-server does not check the
  `model` field: send `gemma-26b` so results are not mixed up. Options: `-c 32768 -ngl 99 --parallel 1 --jinja
  --reasoning-budget 1024 --temp 1.0 --top-k 64 --top-p 0.95 -fa on` (1024 since a later request; Kaggle's coordinator uses 512; the model's
  defaults for unset parameters) plus MTP speculative decoding (`--spec-type draft-mtp`, unsloth's
  `mtp-gemma-4-26B-A4B-it.gguf`, `--spec-draft-n-max 3`). Measured by the peer: 16.3 GB VRAM; generation 126 tok/s
  without MTP, 142-173 with it; prompt 3,800 tok/s without MTP, 3,455 with it (the 12B: 54-65 and 2,700). With MTP
  the outputs differ from those without it, even at temperature 0: compare runs only within one configuration. The
  12B configs are kept on the gateway (`llama-12b.json`, `watchdog-12b.json`). The reasoning budget is global:
  thinking can be turned off per request but a per-request budget is ignored. The peer can restart the server with
  other options or models.
- **The 31B fits, barely (peer's test, 2026-10-14).** The official GGUF `google/gemma-4-31B-it-qat-q4_0-gguf` (17.65 GB)
  runs on the RTX 3090 (about 20.4 GB usable): 19.1-19.6 GB in use, nothing else may use VRAM. Speed: 31-32 tok/s
  generating, 930-1,100 tok/s on the prompt (the 26B: 126, or 173 with MTP). Largest context that held, filled to
  85-90%: 12,288 tokens with an f16 KV cache, 17,664 with q8_0, and 38,912 with q4_0 (only q4_0 reaches Kaggle's
  32,768; its effect on tool calling has not been measured). With its MTP draft (unsloth's 267 MB, `--spec-draft-n-max 3`), KV q8_0 and
  17,664 context (the same context as without it), at temperature 0.2:
  - generation is 66.6 tok/s on code (67-68% accepted) and 58.7 on text (52-54%), about twice the speed without MTP;
  - the prompt runs at 1,055 tok/s, the same as without MTP.

  Files are in C:\llm-lab-c\models. Since 10-14 the server serves the 31B (above).
- The gateway and its monitoring API (`/_monitor/*`); it can route a model name to base + LoRA (`routes.json`).
- The fine-tuning stack (`F:\llm-lab`: llama.cpp b11476 with CUDA, Unsloth/PEFT/TRL venv, LoRA → GGUF).

Work with it: ask it whenever something depends on the local machine (is the model served, why requests fail, option
changes, GPU windows, gateway changes); do not guess from silence. Send with the claude-code-remote `send_message`
tool (`SendMessage` cannot reach it). It warns before it unloads the model; tell it when a run is in progress. Never
ask it to do what either session was denied; take that to the user. It was inactive on 2026-10-09.

## Anthropic-style APIs through the same harness (2026-10-13)

`tools/anthropic_proxy.py` serves an OpenAI-style `/v1/chat/completions` in front of an Anthropic Messages API, so
`tools/local_eval.py --api-base http://127.0.0.1:8790/v1 --model <upstream model>` runs the submission against it
the way it runs against the local gateway. Start it with the key in the environment (never in the repository):
`ANTHROPIC_API_KEY=$(cat <scratchpad>/anthropic.key) python tools/anthropic_proxy.py --port 8790 --log <file>`. It
converts system text, tools and tool results, tool_choice, sampling, stop sequences, reasoning and streaming; it adapts
to what the upstream model refuses (a sampling parameter is dropped, a fixed thinking budget becomes adaptive
thinking) and casts the ADK's `top_k` (64.0) to an integer. Checked on 10-13: rich_3521 resolved and judged resolved
through it (coordinator, specifier, editor, submit in 42 s). **Its outputs are never used to train Gemma** (the rule
above); runs through it test the system, they are not Kaggle evidence.

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
