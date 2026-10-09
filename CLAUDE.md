# CLAUDE.md

## Competition: Google – The Gemma 4 Developer Agent Competition (Kaggle `gemma-4-developer-agent`)

Source: the competition pages on Kaggle (Overview, Evaluation, Rules, Data, "Model Selection, Budget, and Harness Rules") and `HARNESS_README.md` in the competition data. Refresh with `kaggle competitions pages list --content --format json gemma-4-developer-agent`.

**Goal.** Post-train Gemma 4 into an autonomous software-engineering agent that navigates a Python repository and drafts a fix for a real issue. The organizers' framing is fine-tuning / RL ("post-train"), but prompts, skills and multi-agent configs are equally part of a submission.

**Submission (`submission.zip`, root `agent.yaml`).** Declarative Google ADK Agent Config, with no Python entry points: `agent.yaml`, `prompts/`, `configs/` (sampling via `!include`), `sub_agents/`, optional `skills/<name>/SKILL.md` + `scripts/` (run in the task sandbox with `run_skill_script`) + resources, optional `adapters/<name>/` LoRA (`adapter_config.json` + `adapter_model.safetensors`), optional `eval_config.yaml` (per-task budgets).
- Base model: only `gemma-4-31b-it-qat-w4a16-ct`, for every agent and sub-agent.
- LoRA: each `LlmAgent` can use its own adapter (`adapter: <name>`). vLLM serves up to 8 adapters with rank ≤ 128.
- Limits: total unpacked size < 3 GiB (adapters included); allowed extensions are .yaml/.yml/.md/.txt/.py (skill scripts only)/.json/.safetensors; no `..` or symlinks; `!include` paths are relative to the including file; `skills:` paths are relative to the submission root.
- Generation: `max_output_tokens` 1–32768, `thinking_budget` 0–32768 (vLLM enforces it), `temperature`/`top_p`/`top_k`/`presence_penalty`/`frequency_penalty`/`seed` allowed. Context window: 32,768 tokens; the harness compacts history at 14,336 tokens.
- Harness tools: `run_command`, `submit_patch`, `get_status`, `read_file`, `edit_file`, `write_file`, `get_code_neighbors`, `search_similar_code`, `get_code_subgraph` (+ the skill tools when `skills:` is set). Graph node ids are fully qualified dotted paths, e.g. `fastapi.routing._prepare_response_content`, so short names return empty results.

**Scoring.** SWE-bench style PASS/FAIL. The patch is applied and the hidden validation tests (fail-to-pass + whole suite pass-to-pass) are run; the score is the % of tasks that pass. The test set has about 120 tasks, split evenly into public and private leaderboards, and is curated from **private repositories** (not the four public dev repos). A frontier model passes, or nearly passes, each test task.

**Time.** 12 h for all tasks, including sandbox setup and excluding validation, which is about 6 min per task. Our `eval_config.yaml` sets the per-task budget (currently 5 min, 45 tool calls, 70 turns, 120 s per command). Hardware: 4×L4 (96 GB), no internet.

**Data.** 129 public dev tasks (`tasks.jsonl` with `problem_statement`, `hints_text`, reference `patch`, `test_patch`) from fastapi, rich, requests and httpx, plus snapshots, code graphs, embeddings, offline wheels, the sandbox Dockerfile (Python 3.13) and a sample submission.

**Rules.** 1 submission per day (resets 00:00 UTC). Select up to 2 final submissions. Teams of up to 5. External data and models are allowed if publicly and reasonably accessible to everyone. Winners license the solution as Apache 2.0.

**Dates.** Started 2026-09-23. Optional paper track deadline 2026-11-12. Entry and team-merger deadline 2026-11-25. **Final submission deadline 2026-12-02** (23:59 UTC).

**Prizes.** $37k / $18k / $10k for places 1–3, plus a $35k paper track (PEFT/RL for SWE agents, code graphs/embeddings, benchmarks, graph reasoning).

## System design (the user's idea; keep it)

**The only design principle: make the task easier for the 31B model** (`gemma-4-31b-it-qat-w4a16-ct`). Every design choice is judged by this alone; there are no other design principles.

**Rules for Claude working on this repo:**
- Never remove or replace a piece of this design because it has few or no calls. Zero calls means it is not wired into the prompts or tools. Find out why and wire it.
- Record design decisions and literature here, not only in the scratchpad: the scratchpad and the conversation context are lost.
- Every evaluated version (Kaggle eval run V, submission S) gets a row in `VERSIONS.md`: date, commit, system, tools, generation, budget, result, tool-call counts, failures seen and what changed next. Update it as soon as a run finishes.

### Current state of the repository (2026-10-09)
The old scripts (`locate.py`, `show.py`, `edit.py`, `check.py`, `hints.py`, …) and the old agent configs (`single/`, `pipeline/`, `submission/`, `build.py`) were removed: they were built for a model that explores, and V4–V6 showed the model browsing instead of deciding. What they learned is in `docs/old_scripts_lessons.md`; their results are in `VERSIONS.md`.

v1 of the new single agent (`docs/design_single.md`, "Design v1") is implemented: `agent/` is the submission (one `LlmAgent`, skill `fix-issue` (named `swe` until 2026-10-09) whose only entry point is `scripts/step.py`; internal modules `_*.py`), `tools/build.py` builds and validates it, `tools/bench_locate.py` is the 129-task localization benchmark (snapshots' Python files via `tools/fetch_snapshots.sh`). Measurements and the local 12B runs are at the end of `docs/design_single.md`. Decision 2026-10-09: the free-text plan step was removed (the local model failed there in 4 of 5 problem runs); choosing opens the chosen places for editing. Decision 2026-10-09 (later): every edit window repeats what the change must do (requirements, the statement's sentences about the behaviour, its example, an existing test that uses the code), the statement is also in the system instruction (`{problem_description}`, never compacted), the edit step accepts an edit, `skip`, `done`, `back`, a place id, candidates or names to add, and up to 5 questions per place (name, file, lines, text search); anything else is refused, and only failed edits count against a place (asking never makes it leave the place), copies in a file of the same name and async/sync twins join the plan, and one answer is capped at 8,000 characters. The user removed the `tests/` folder (unit tests) on 2026-10-09. Decision 2026-10-09 (evening), after V10 and the two local batches: the edit step had grown to about 12 accepted forms (questions by name, file, lines, text search, `python -c`, candidates and names joining the plan, done) and 16 of the 24 fixes of the local batches were new forms or rules to stop the questions, with no change in the result (1/10, 1/10). D3 was redesigned: an edit is the place id and the whole new function or class (replaced by name; a new name is added after the place), the line form stays for code outside functions; the window shows places up to 130 lines whole, the code the place calls and the code that calls it; only an edit, `skip`, `back` and `P<n>` are accepted (Agentless: the model never asks for code). The skill was renamed from `swe` to `fix-issue` at the user's request (V10: 44% of the calls had a mangled `skill_name` such as `「swe」`, and the model repeated the broken call up to 34 times). V11 = before, V12 = after. Decision 2026-10-09 (later, the user's): code is named by its name, not by an id. Candidates and places are shown and chosen by their qualified name (`Session.send`, or `file::Name` when two share it; a file path for top-level code); a whole new definition alone is matched to the place of the same name; the finish step's `["back"]` chooses code for the requirements nothing covers (no `R<n>`). Ids are still read when sent. Evidence: V10 55 of 118 edit-step calls named code, the 12B merged ids with names (`C11:file::Name`), compaction removes the list that gives an id its meaning; literature: names carry meaning for code models (Wang & Luo 2023, arXiv 2307.12488; When Names Disappear, arXiv 2510.03178), models merge an id with another element's description (arXiv 2312.06147), LocAgent / Moatless / Agentless / AutoCodeRover all address code by name. No controlled study of ids vs names in agent actions was found. Local runs need thinking on (the harness sends `enable_thinking: true` with our `thinking_budget`); a faithful local loop needs llama-server with `--reasoning-budget 512`.

### Public notebooks (2026-10-09)
The public notebooks that score 0.17–0.18 are one plain `LlmAgent` with the native tools, a prompt with three rules (exact strings from the issue; edit by call 12, best guess allowed; 1–2 targeted checks then submit), thinking 4096, 8192 output tokens, and eval 240 s / 28 calls / 8 min / 80 turns. `timeout_seconds` is also the hidden-test timeout (checked in our local harness). Details in `docs/public_notebooks.md`.

### What the 31B does badly, and what the literature does about it (2026-10-08)

Measured in the Kaggle traces of V1–V6 (31B only; nothing from the local 12B, nothing assumed). "Applicable" is about
our setting: declarative ADK, no own code in the agent, no internet.

**A. Seen in our runs**

| Failure (evidence) | Techniques in the literature | Applicable here? |
|---|---|---|
| **Malformed calls** (`「swe」`, `「scripts/show.py」`, extra quotes): 31% of script calls in V1, 21% V2, 10% V3, 17% V4, 14% V6. **Calls to tools or scripts that do not exist**: V1 `show_file` (task lost), V5 `grep.py`, V6 `nonexistent.py` | Grammar-constrained decoding (XGrammar-2: removes format errors; a 3B beat a 70B on BFCL). Malformed-call repair layer (SHERLOC). Fewer, simpler tools (SWE-agent ACI). Function-calling fine-tuning (Gorilla, xLAM) | vLLM enforces the schema only with `tool_choice="required"` or a named function, not with `"auto"`, and the ADK cannot set it (`generate_content_config` forbids extra fields; no `tool_config`). Repair: only for the arguments that reach our scripts. LoRA: yes |
| **With `run_command` / `read_file` it uses them instead of the scripts**: V2 96 + 65, V3 96 + 67 calls | Fewer, simpler tools (ACI); only the valid actions per step (SOP-Agent) | Yes |
| **Does not follow the suggested next step**: V4, 16–17 `show.py` before the requirement step. **Retries refused calls, repeats identical ones**: 55 refusals in V4, 50 in V6, 34 identical repeats in the V5 locator, the same refused edit 3× in V6 fastapi_14448 | Stuck detector: same action and observation 4+ times, same action and error 3+ times, ping-pong (OpenHands). Loop detection and intervention (SHERLOC). Only the valid actions per step (SOP-Agent, StateFlow). Early stop: most successes come within about 25 rounds; failures take 3.5× more steps (Liu et al.) | Yes, in the scripts |
| **Reads a lot, edits late or never** ("analysis paralysis"): V6 128 `show.py`; first edit after 148–248 s in 5 of 8 failed tasks, never in 2 | Native function calling and selective RL; picking the lower-overthinking of 2 low-effort samples: 27.3% vs 21.0% for one low-effort run, still below high effort (29.1%) at 43% lower cost; in the paper "analysis paralysis" means too much internal planning, not reading too many files (Cuadron et al.). Fixed steps with prepared inputs (Agentless). Thinking budget | Fixed steps and thinking budget: yes. RL: no |
| **Leaves the right place after seeing it** ("blind strategy switching"): V6 fastapi_14448 saw `Dependant._unwrapped_call` at 19 s, then read 14 other things | Fixed hierarchical localization file → function → lines (Agentless). Signals to keep or abandon a path (asked for by the trajectory study). Verifiers / value models (SWE-Gym, SWE-Search) | Fixed localization and journal rules: yes. Verifier: with LoRA. MCTS: not with the ADK |
| **Small change where several places must change** ("incomplete repair"): V6 patches of 4–14 lines where the reference has 34–654, in 5 tasks | Dependency and change-impact analysis plus an edit plan, one LLM call per location (CodePlan, FSE 2024: 5 of 6 multi-file repositories valid vs 0 for the baselines). Code graph for related places (LocAgent) | Yes: static analysis in the scripts |
| **Right place, wrong change**: V6 requests_7328 deleted `resp.history = hist[1:]` instead of changing it to `hist[:]`; fastapi_14986 incomplete (8 of 34 lines). In the literature fix implementation is the dominant bottleneck, and about 65% of causes are flawed reasoning (Liu et al.) | Several patch samples plus test selection: 40 patches, regression and reproduction filter, majority vote (Agentless). Test-time compute scaling with test-based voting (CodeMonkeys). Trained verifier choosing among patches (SWE-Gym) | Costly within about 6 min; `ParallelAgent` with seeds maybe; verifier with LoRA |
| **Context grows to the compaction threshold**: 9 of 10 tasks reach ≥14.3k prompt tokens in V4 and V6 | Collapse old observations (SWE-agent). Minimal context per subtask (Agentless, Beyond Generalist). Context management took one model from 6.4% to 58.4% (harness design study). Truncating can make degradation worse (How Fast Do Agents Rot?) | Yes |

**B. Reported in the literature, not yet seen in our runs (watch for them)**

| Failure | Techniques | Applicable here? |
|---|---|---|
| **Writing reproduction tests**: 3.6% useful tests with direct prompting, 16–19% with agents, 44–49% at best (SWT-bench) | AssertFlip: the LLM writes a test that **passes** on the buggy code, then its asserts are inverted (43.6% on SWT-bench Verified). Execution feedback (e-Otter++). Many samples, keep the most frequent normalized test (Agentless) | Yes: a script can do the inversion. Measure before relying on it |
| **Misreading reproduction or test output**; insufficient verification (Liu et al. C1) | Deterministic reading: the test prints a fixed marker ("Issue reproduced") and a script reads it (Agentless) | Yes |
| **Superficial matching**: led by statement keywords, cited code or stack traces to the wrong place (Liu et al. A2; 51% of the failures of a pipeline like Agentless are in localization). Our requests_7328 (`Response`) | Query reformulation: the LLM extracts identifiers, paths, error messages and traces, BM25 runs on them, an agent reranks (+35% first-file accuracy over BM25 alone). Query transformation plus reranking: over 78% top-1 on SWE-bench Lite with open models (BLAgent). Trained reranker (SweRank) | Yes (reformulation is a bounded decision for the model); reranker with LoRA |
| **Skill-induced failures**: task-implementation faults (68.8% of the functional failures, with a frontier model), which the authors often describe as defaults and examples taken as requirements, optional checklists made mandatory, context bloat from long skill bodies | Separate mandatory requirements from examples and defaults; keep always-loaded instructions short; load examples and checklists only when needed; scale verification to the uncertainty (Agent Skills Can Be Harmful) | Yes: prompts and SKILL.md |
| **Premature disengagement and rogue actions** (Cuadron et al.) | Native function calling, one action per turn | Yes |
| **Plausible but incorrect patches** that pass the tests: 7.8% pass validation but fail the developer suite, 29.6% behave differently from the reference (ICSE 2026); 1 in 5 "solved" patches is wrong (SWE-ABS). Our V6 false "check OK" is the same | Adversarial test strengthening (SWE-ABS); compare behaviour with the expected one | Partly: the reproduction snippet does this |
| **Early errors cascade** (AgentErrorBench) | Root-cause localization with corrective feedback: earliest-critical-error detection and re-rollout with feedback raised ALFWorld success (e.g. 21 → 55% for GPT-4o-mini) (AgentDebug; not evaluated on software tasks, and the "4B debugger beat GPT-4.1" claim is not in arXiv v1). Backtracking (SWE-Search) | The journal can detect the failed step and go back to it; tree search: no |

Main conclusions: fixing is harder for the model than localizing, especially across several places; writing a
reproduction test is hard for LLMs, so it must be measured before the design depends on it. New techniques that fit:
query reformulation before BM25, CodePlan-style impact analysis, AssertFlip for reproduction, script-side reading of
outputs (`tool_choice="required"` was checked: the ADK cannot set it).

Sources: [Liu et al. 2025](https://arxiv.org/pdf/2509.13941) · [trajectory study](https://arxiv.org/html/2511.00197) ·
[Overthinking](https://arxiv.org/pdf/2502.08235) · [SWE-smith](https://arxiv.org/pdf/2504.21798) ·
[SWE-Gym](https://arxiv.org/pdf/2412.21139) · [SWT-Bench](https://arxiv.org/pdf/2406.12952) ·
[AssertFlip](https://arxiv.org/html/2507.17542v2) · [e-Otter++](https://arxiv.org/html/2508.06365v1) ·
[Are Solved Issues Really Solved](https://arxiv.org/html/2503.15223v2) · [SWE-Bench+](https://arxiv.org/pdf/2410.06992) ·
[SWE-ABS](https://www.alphaxiv.org/abs/2603.00520) · [Agent Skills Can Be Harmful](https://arxiv.org/html/2608.11888v1) ·
[Live API-Bench / BFCL errors](https://arxiv.org/pdf/2506.11266) · [Tool-use failures synthesis](https://arxiv.org/pdf/2607.05775) ·
[XGrammar-2](https://arxiv.org/pdf/2601.04426) · [Repair, Not Improvement](https://arxiv.org/pdf/2608.13959) ·
[vLLM tool calling](https://docs.vllm.ai/en/v0.19.1/features/tool_calling/) ·
[OpenHands stuck detector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector) ·
[CodePlan](https://www.microsoft.com/en-us/research/publication/codeplan-repository-level-coding-using-llms-and-planning-2/) ·
[CodeMonkeys](https://arxiv.org/pdf/2501.14723) · [AgentDebug](https://www.alphaxiv.org/abs/2509.25370v1) ·
[Reformulate, Retrieve, Localize](https://arxiv.org/pdf/2512.07022) · [BRaIn](https://arxiv.org/html/2501.10542v1) ·
[BLAgent](https://arxiv.org/abs/2605.17965) (arXiv 2510.04468 is IQLoc, a different paper) · [Lost in the Middle](https://preview.aclanthology.org/setup/2024.tacl-1.9) ·
[How Fast Do Agents Rot?](https://arxiv.org/pdf/2609.01660) · [Harness design study](https://arxiv.org/pdf/2609.20804)

### Design methodology
Use established methods, not an ad-hoc list: Hierarchical Task Analysis (Stanton 2006) → function allocation (Parasuraman, Sheridan & Wickens 2000, levels of automation) → workflow-vs-agent patterns (Anthropic, *Building Effective Agents*, 2024) → detailed design → V-model verification. The work so far is in `docs/design_single.md`.

**Acceptance before Kaggle:** (unit tests: the `tests/` folder was removed by the user on 2026-10-09) the replay of real calls shows no problems, and the local model passes the iterative loop below on the 10 local tasks; then a Kaggle run with 10 tasks. If a launch is requested before that, say plainly which criterion is not met.

### Iterative test loop (how every change to the scripts or prompts is tested)
The user's rule (2026-10-09): the only valid way to test a change is a run against the model, monitored and analysed step by step through the gateway API (`/_monitor/*`). Dry runs and unit tests are not evidence.

1. **Fix.** Change the scripts or prompts.
2. **Dry test.** Run the affected scripts by hand on a repo in `scratchpad/repos/` with `PWD=<repo>`.
   - Run them through `scratchpad/harness_like.py <skill dir> <script> args…` too. It runs a script the way ADK's `run_skill_script` does: the skill's files are in a temporary directory that is deleted when the script ends, before the exit handlers run. Anything a script does at exit can no longer read its own files or start threads.
3. **Replay.** `venv/bin/python replay.py <scripts dir> repos real_calls.json` must print NO PROBLEMS (pipeline mode).
4. **Package and validate.** Build the submission without `__pycache__` (a `.pyc` file makes the harness reject it), then run `validate_submission.py` on it.
5. **Run one task with the local 12B** (`one_task.sh <task>`).
6. **Monitor it step by step while it runs.** Every 30–45 s, curl `/_monitor/conversation?since=<start>` and read each new call: its arguments, its result and the JOURNAL line. Never wait for the end of the run with a loop and never use a background monitor.
7. **Stop at the first problem.** When a script error, a loop or a wrong JOURNAL decision shows up, stop the run, fix it and relaunch from step 1. Do not let the run go on to the timeout.
8. **Analyse each finished task before the next one.** Record the result, the calls used, the wasted calls and their cause, and the fix.
9. **Commit and push** after each round of fixes.

Local timing is not Kaggle timing. llama-server has a global reasoning budget of 3072 tokens, so a 12B call can take up to 60 s; on Kaggle the agent sets `thinking_budget: 512`.

### Literature behind the design
- **SWE-agent / ACI** (NeurIPS 2024, [arXiv 2405.15793](https://arxiv.org/pdf/2405.15793)).
  - Tools designed for the model, not raw shell.
  - Ablations: no editor −7.7, no linting −3.0, whole-file view instead of a ~100-line window −5.3, iterative search −6.0. A failed edit drops recovery from 90.5% to 57.2%.
  - Collapse old observations.
- **SOP-Agent** ([arXiv 2501.09316](https://arxiv.org/html/2501.09316v1)): the closest to the journal.
  - The procedure is a decision graph. At each step a navigator gives the current step and only the valid actions.
  - Tools are restricted to those allowed in the current step.
- **StateFlow** ([arXiv 2403.11322](https://arxiv.org/html/2403.11322v1)): the task as a state machine, with one instruction per state and transitions by rules or by the LLM. +13% / +28% over ReAct at 3–5× less cost.
- **Blueprint First, Model Second** ([arXiv 2508.02721](https://papers.cool/arxiv/2508.02721)): a deterministic engine runs the procedure. The LLM only does bounded subtasks and never decides the path. v2: TravelPlanner 35.6% vs 18.0%, and with the same rules in the baselines' prompt 37.2 vs 24.5 (enforcement, not knowledge); the +10.1 pts on tau-bench is from v1 only.
- **Moatless Tools** ([SWE-Search appendix](https://arxiv.org/pdf/2410.20285)): a fixed state machine search → identify → plan → edit. 24.3% on Lite with GPT-4o (v0.0.2); the about $0.14 per task is the cost of the adapted variant (25.7%), so do not combine the two figures. Loosening the transitions gave only +1.4% and added loops.
- **CodeR** ([arXiv 2406.01304](https://arxiv.org/html/2406.01304v1)): plans as a JSON task graph, to avoid non-progressing loops and information lost between agents.
- **Agentless** ([arXiv 2407.01489](https://arxiv.org/html/2407.01489v2)): fixed phases.
  - Hierarchical localization: file → class/function → lines.
  - Then repair, then validation with regression and reproduction tests.
  - 32% on Lite.
- **AutoCodeRover** ([arXiv 2404.05427](https://arxiv.org/html/2404.05427v3)): AST search APIs (`search_class`, `search_method`, `search_code`), spectrum-based fault localization, and patch retries checked by tests.
- **Lingma SWE-GPT / SWESynInfer** ([arXiv 2411.00622](https://arxiv.org/html/2411.00622v1)): a process workflow (repo understanding → fault localization → patch) for small open models. The 7B model solves about 18% of Verified.
- **SHERLOC** ([arXiv 2606.24820](https://arxiv.org/pdf/2606.24820)): a localization framework with a self-recovery layer: loop detection, implicit malformed-call recovery, response-length re-prompts, first-and-recent context truncation, and a final-turn synthesis prompt. Removing the final-turn prompt cost the most (−5.0 localization F1). The about +6 pts is the resolve-rate gain from feeding its localization to repair agents, not from the robustness layer.
- **An Empirical Study of Harness Design for Coding Agents** ([arXiv 2609.20804](https://arxiv.org/pdf/2609.20804)): planning (a list of steps) is an accuracy scaffold for weaker models and only a cost saver for strong ones. Context management moved one model from 6.4% to 58.4%.
- **Beyond Generalist LLMs** ([arXiv 2607.14456](https://arxiv.org/abs/2607.14456)): BPMN business workflows turned into agents (not software engineering): 1.27 vs 3.19 tool-call errors per run (2.5×); the 95% fewer tokens is for generating the agent, not running it. Weak evidence for us.
- **Small Language Models are the Future of Agentic AI** ([arXiv 2506.02153](https://arxiv.org/pdf/2506.02153)): small models suffice for narrow subtasks.
- **LocAgent** ([arXiv 2503.09089](https://arxiv.org/pdf/2503.09089)) and **SweRank** ([arXiv 2505.07849](https://arxiv.org/pdf/2505.07849)): graph-guided and ranking-based localization, where small fine-tuned models compete with large ones.
- **SoRFT** ([arXiv 2502.20127](https://arxiv.org/pdf/2502.20127)): fine-tuning per subtask (localization, editing).

Most numbers come from abstracts and summaries; check them in the PDFs before citing them in the paper. On 2026-10-09 about 40 of the papers were read in full (method and results); the techniques, numbers and gaps per script are in `docs/scripts_spec.md`, and the claims above that the PDFs contradicted were corrected.

## ADK config and harness behaviour (checked in the installed adk_submission 0.2.12, swegemma 0.2.7, google-adk 1.39.1)

**Harness update on 2026-10-09 (Kaggle wheelhouse: swegemma 0.2.11, adk_submission 0.2.13).** The local venv (`scratchpad/venv`) was updated to the same wheels on 2026-10-09: swegemma 0.2.11, adk_submission 0.2.13, google-adk 1.36.1, google-genai 2.11.0, adk-eval-core 0.1.0 (installed with `--no-deps`; pip reports google-adk 1.36.1 wants starlette<1, which only matters for `adk web`). It changes some of the points below:
- A `ToolErrorPlugin` turns calls to undeclared tools, and tool exceptions, into error responses; they no longer end the task.
- The task message lists only the declared tools. Our agent therefore declares only `submit_patch`; the skill tools come with `skills:`.
- An exception during the agent run keeps the unsubmitted `git diff`.
- Patches drop changes to test and runner-config files.
- Skill tool arguments given as one-item lists are coerced to strings.
- `include_thoughts: false` now strips thoughts from the history; we use `true`.
- The workspace tree in the prompt is pruned (no docs or tests, depth 3).
- `swegemma.models.discovery` is gone; use `adk_submission.discovery.discover_declared_models`.

The bullets below describe 0.2.7 where they differ.

- Agent classes: `LlmAgent`, `SequentialAgent`, `ParallelAgent`, `LoopAgent`. Tools are the 9 harness tools, the skill tools, or `agent_tool: {config_path, skip_summarization}`. Not in the schema: ADK `planner`, `code_executor`, `input_schema`, `output_schema`, `response_schema`.
- Callbacks validate but do nothing: the harness compiles without a callback registry.
- There is no `exit_loop` tool, so a `LoopAgent` always runs all `max_iterations`. The only early stop is `submit_patch` followed by a final text.
- If the root agent ends without `submit_patch`, the harness re-runs it with a "nudge" message (up to 3 times), and it takes `git diff` at the end anyway. A timeout or budget stop keeps that diff.
- An exception during the agent run loses everything, even an earlier `submit_patch`: `agent_patch` stays `''` (`swegemma/harness/agent_runner.py`, the outer `except`). Two causes have been seen:
  - a call to a tool the agent lacks;
  - a reply cut at `max_output_tokens`, whose tool-call JSON is unterminated. This happened with the local 12B's runaway escape loops.
  Avoid both: list every tool the harness advertises, and keep `max_output_tokens` modest.
- `ParallelAgent` branches share one `/workspace`; separate candidate patches need separate git worktrees.
- AgentTool: the sub-agent starts with a fresh session (it sees only its `request`) and can use `skills:` (path relative to the submission root). Its `run_skill_script` calls count against the 45 tool calls (tested: the budget ran out at 6 with a limit of 6) and its model calls count as turns. With `skip_summarization: true` the calling agent's turn ends right after the tool returns, so keep it `false` when the caller must continue.
- Calling a tool the agent does not have raises `ValueError: Tool '<name>' not found` and ends the task. Seen in Kaggle single v1, fastapi_14583 (`show_file`). This cannot be made recoverable from the config.
- When `skills:` is set, the ADK appends a system instruction: "if a skill is relevant you MUST `load_skill` it and follow its instructions exactly, completing all steps in order". It also lists the skills. The skill tools are `list_skills`, `search_skills`, `load_skill`, `load_skill_resource` and `run_skill_script`.

### ADK features available to a submission

Sources: `adk_submission/schema.py`, `limits.py`, `resolvers/`, `swegemma/config.py`, `google/adk/tools/skill_toolset.py`. Status: **tested** = used in our runs; **available** = accepted by the schema but not tried; **no-op** = validates but does nothing.

| # | Feature | What it does | Status |
|---|---|---|---|
| 1 | `SequentialAgent` | Runs agents in a fixed order | tested (pipeline) |
| 2 | `ParallelAgent` | Runs branches at once; they share `/workspace` | tested |
| 3 | `LoopAgent` + `max_iterations` | Repeats sub-agents and always runs every iteration (no `exit_loop`) | tested |
| 4 | `LlmAgent.sub_agents` + `description` | The LLM transfers control to another agent (`transfer_to_agent`); `disallow_transfer_to_parent` and `disallow_transfer_to_peers` limit it | available |
| 5 | `agent_tool` | An agent used as a tool, in a fresh session; its calls count against the budget | tested (apply_edit) |
| 6 | `tools:` per agent | Each agent sees only the tools listed for it | tested |
| 7 | `skills:` per agent (several skill dirs) | Each agent can get its own set of scripts | tested (one skill `swe`) |
| 8 | `run_skill_script` | Runs our deterministic scripts in the sandbox; they keep state in files between calls | tested |
| 9 | `load_skill` / `SKILL.md` | The ADK tells the model to load the skill and follow its steps in order | available (we skip it on purpose) |
| 10 | `load_skill_resource` | Reads the skill's `references/` and `assets/` (for example a procedure per phase) | available |
| 11 | `instruction` with `{key}` and `{key?}` | Puts session state into the prompt (valid identifiers only) | tested (planner) |
| 12 | `output_key` | Stores the agent's final text in the state for later agents | tested |
| 13 | `include_contents: none` | The agent sees no earlier history, only its instruction and the state | tested |
| 14 | Generation config per agent | `temperature`, `top_p`, `top_k`, `seed`, penalties, `max_output_tokens` ≤ 32768, `thinking_budget` ≤ 32768 | tested |
| 15 | `stop_sequences` | Stops generation at a given text | available |
| 16 | `response_mime_type` (for example `application/json`) | Asks for structured output | available; unknown whether vLLM honours it |
| 17 | `adapter` per agent | A different LoRA per agent or stage (up to 8, rank ≤ 128) | available |
| 18 | `eval_config.yaml` | Per-task budget: time, tool calls, turns, command timeout | tested |
| 19 | Harness graph and embedding tools | `get_code_neighbors`, `get_code_subgraph`, `search_similar_code` | tested |
| 20 | Harness nudge + final `git diff` | Re-runs the root agent without a submit; takes the diff anyway | tested |
| — | Callbacks | Validate but do nothing | no-op |
| — | `planner`, `code_executor`, `output_schema`, Python tools | Not in the schema | not available |

Schema limits: up to 500 agents, nesting depth 50, `max_loop_iterations` 500, 1,000 skills, 50 MiB per skill directory.

### How each feature implements a technique from the literature

| Feature | Technique | Paper | Use here |
|---|---|---|---|
| 8: stateful scripts (the journal) | State machine with rule-based transitions | StateFlow, Blueprint First | The journal reads the real call log, diff and verdict and decides the next step; the LLM does not choose the path |
| 8: scripts that refuse out-of-phase calls | Only the valid actions of the current step | SOP-Agent | A script called in the wrong phase does not run and answers "the journal says: now do X" |
| 1 + 6 + 7: stages with different tools and skills | Restrict tools per step | SOP-Agent, Moatless | Each stage gets only the scripts of its phase: the locator cannot edit, the editor cannot search |
| 1: fixed stages | Phased pipeline with hierarchical localization | Agentless, Moatless, Lingma SWESynInfer | locate (file → function → lines) → edit → check → submit, in a fixed order |
| 12 + 11: state between stages | Shared plan or task graph; no information lost between agents | CodeR | Each stage writes its result (`output_key`); the next one gets it in its prompt |
| 16: JSON output | JSON plans | CodeR | The planner emits the plan as JSON that the scripts can read |
| 13: `include_contents: none` | Minimal context per subtask | Beyond Generalist, Harness Design | Each stage sees only what it needs, not the whole history |
| 8: script output format | ACI: ~100-line viewer, linted editor, summarized search | SWE-agent | Done in show.py, edit.py and locate.py |
| 8: AST search | `search_class` / `search_method` APIs | AutoCodeRover | locate.py and show.py by symbol |
| 8: call repair and loop detection | Tool robustness layer | SHERLOC | Argument cleaning and repeat_guard (done) |
| 8: check.py | Validation by regression tests | Agentless, AutoCodeRover | Related tests, with rollback when tests break |
| 19: graph and embeddings | Graph-guided localization, embedding ranking | LocAgent, SweRank | The grapher stage and `search_similar_code` |
| 9 + 10: SKILL.md and `references/` | A written procedure followed step by step | SOP-Agent | The SOP of each phase in `SKILL.md` or `references/`; the ADK already tells the model to follow it in order |
| 2: parallel branches + worktrees made by a script | Sample several patches and select one | Agentless, SWE-Search (partly) | N editors in separate worktrees; a script picks the patch that passes check |
| 14: `seed` and `temperature` per agent | Candidate diversity | Agentless | Different seeds in the parallel branches |
| 3: loop | Edit → verify iterations | AutoCodeRover (retries) | Fixed rounds of edit + check; a round with nothing to do must end quickly |
| 4: agent transfer | A coordinator that delegates to specialists | CodeR (manager), StateFlow SF_Agent | A coordinator transfers to the specialist of the phase (untested in the harness) |
| 17: LoRA per agent | Process-centric or per-subtask training | Lingma SWE-GPT, SoRFT | One adapter for locating, another for editing |

**Not possible with this ADK:**
- Dynamic tool filtering inside one agent (it needs callbacks). Use stages (1, 6, 7) or out-of-phase refusals in the scripts instead.
- Early loop exit.
- True tree search with backtracking (SWE-Search / MCTS); at most, parallel branches and a selection.
- Making a call to a missing tool recoverable.

**Combination that implements the journal idea:** stateful journal (8), scripts that refuse out-of-phase calls (8), stages with one skill per phase (1, 6, 7), and the procedure written in SKILL.md (9).

## The local model / GPU session (peer Claude Code session)

Another Claude Code session runs on the user's own Windows machine and manages the local LLM infrastructure: session id `session_01K7Fs6ULXEUTHQZQXS2wArN` (title "Programa winget para máquina virtual con CLI"). It is connected through Remote Control.

What it does and can do:
- Runs the model server behind `https://llm.rayflow.dev`: since 2026-10-07 only llama.cpp `llama-server` (LM Studio is no longer used). Model `gemma-12b` = the google/gemma-4-12b-qat Q4_0 GGUF, context 32768, parallel 1, all on an RTX 3090 with 20 GB (8.9 GB VRAM). Global `--reasoning-budget 3072`; reasoning can be turned off per request with `chat_template_kwargs.enable_thinking=false` or `reasoning_effort="none"`, but a per-request budget is ignored. It can restart the server with other options or models.
- Runs the gateway in front of the model server and its monitoring API (`/_monitor/*`). It built and changes those endpoints on request (full request content, tool names, tails of cut responses, `?since=`, `X-Run-Tag`, llama.cpp `timings` per request). The gateway can route a public model name to the base model + a LoRA adapter (`routes.json`), for per-stage adapters.
- Prepares the fine-tuning stack on that machine: llama.cpp b11476 with CUDA in `F:\llm-lab\llama-bin` (`llama-server` with `--jinja`, multi-LoRA selection per request, `--reasoning-budget`), the training environment in `F:\llm-lab\venv-train` (PyTorch + Unsloth/PEFT/TRL), the base model download, and LoRA → GGUF conversion.
- Its own permission classifier may refuse some changes. Never ask it to do something its session or this session was denied; take that to the user.

How to work with it:
- Ask it proactively whenever something depends on the local machine: whether the model is served, why requests fail or stall, model or option changes, GPU windows (it stops the model server for training tests), and gateway or monitoring changes. Do not guess the machine's state from silence.
- Send to it with the `send_message` tool of the claude-code-remote MCP server (`session_id` above). `SendMessage` from this cloud session cannot deliver.
- It answers with cross-session messages that arrive in this conversation, and it warns before it unloads the model or restarts the gateway. Before a local run, check that no such window is announced; when a run is in progress, tell it so that it does not take the GPU.

## Monitoring local LLM runs

Monitor every run against the local LLM (llama-server behind https://llm.rayflow.dev) with `curl` calls to the gateway monitoring API, step by step while the run is in progress. Do not rely on background monitors or wait for the run to finish.

Useful calls (add `-A swe-monitor`, because Cloudflare blocks some default user agents):

```bash
# Model state, GPU, requests in flight, last minutes summary
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/report?minutes=5"

# Request log since a Unix time: tool_names (stage), finish_reason, tokens, duration, tool_calls_made
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/log?since=<t>"

# Full content of each call: new input messages (prompt or tool results) and the whole reply
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/conversation?since=<t>&n=50"
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/conversation?since=<t>&brief=1"      # texts cut to 300 chars
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/conversation?since=<t>&only=failed"  # length cuts / errors
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/request/<id>"

# Gateway events (restarts, client disconnects)
curl -s -A swe-monitor "https://llm.rayflow.dev/_monitor/events?since=<t>"
```

Map a request to its pipeline stage by `tool_names`: skill tools + `search_similar_code` = locator, only `get_code_subgraph` = grapher, only skill tools = planner, only `apply_edit` = fixer, `edit_file` + skill tools = apply_edit (the editor agent the fixer calls as a tool), `submit_patch` = submitter.
