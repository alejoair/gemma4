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

**Time.** 12 h for all tasks, including sandbox setup and excluding validation, which is about 6 min per task. Our `eval_config.yaml` sets the per-task budget (currently 4 min, 45 tool calls, 70 turns, 120 s per command). Hardware: 4×L4 (96 GB), no internet.

**Data.** 129 public dev tasks (`tasks.jsonl` with `problem_statement`, `hints_text`, reference `patch`, `test_patch`) from fastapi, rich, requests and httpx, plus snapshots, code graphs, embeddings, offline wheels, the sandbox Dockerfile (Python 3.13) and a sample submission.

**Rules.** 1 submission per day (resets 00:00 UTC). Select up to 2 final submissions. Teams of up to 5. External data and models are allowed if publicly and reasonably accessible to everyone. Winners license the solution as Apache 2.0.

**Dates.** Started 2026-09-23. Optional paper track deadline 2026-11-12. Entry and team-merger deadline 2026-11-25. **Final submission deadline 2026-12-02** (23:59 UTC).

**Prizes.** $37k / $18k / $10k for places 1–3, plus a $35k paper track (PEFT/RL for SWE agents, code graphs/embeddings, benchmarks, graph reasoning).

## Monitoring local LLM runs

Monitor every run against the local LLM (LM Studio behind https://llm.rayflow.dev) with `curl` calls to the gateway monitoring API, step by step while the run is in progress. Do not rely on background monitors or wait for the run to finish.

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

Map a request to its pipeline stage by `tool_names`: contains `read_file` = locator, `get_code_subgraph` = grapher, `edit_file` = fixer, `submit_patch` = submitter (the skill tools `run_skill_script`, `load_skill`, ... appear in the locator and fixer).
