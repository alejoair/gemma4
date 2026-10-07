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

## ADK config and harness behaviour (checked in the installed adk_submission 0.2.12, swegemma 0.2.7, google-adk 1.39.1)

- Agent classes: `LlmAgent`, `SequentialAgent`, `ParallelAgent`, `LoopAgent`. Tools are the 9 harness tools, the skill tools, or `agent_tool: {config_path, skip_summarization}`. Not in the schema: ADK `planner`, `code_executor`, `input_schema`, `output_schema`, `response_schema`.
- Callbacks validate but do nothing: the harness compiles without a callback registry.
- There is no `exit_loop` tool, so a `LoopAgent` always runs all `max_iterations`. The only early stop is `submit_patch` followed by a final text.
- If the root agent ends without `submit_patch`, the harness re-runs it with a "nudge" message (up to 3 times), and it takes `git diff` at the end anyway.
- `ParallelAgent` branches share one `/workspace`; separate candidate patches need separate git worktrees.
- AgentTool: the sub-agent starts with a fresh session (it sees only its `request`) and can use `skills:` (path relative to the submission root). Its `run_skill_script` calls count against the 45 tool calls (tested: the budget ran out at 6 with a limit of 6) and its model calls count as turns. With `skip_summarization: true` the calling agent's turn ends right after the tool returns, so keep it `false` when the caller must continue.

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
