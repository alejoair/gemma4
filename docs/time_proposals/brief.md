# Brief: making an LLM agent system faster (essentials only)

## The task and the hard constraints
- A Kaggle competition: an agent built on **Gemma 4 31B** (instruction-tuned, quantized W4A16) must fix a real issue in a
  Python repository; the patch is scored by hidden tests (SWE-bench style). About 120 test tasks.
- **Time: 5 minutes of agent time per task** (fixed in a config file; the whole run has 12 h for about 120 tasks
  including sandbox setup; a longer per-task limit does not fit). When the limit is reached the agent is stopped and the
  current `git diff` is taken as the patch.
- Hardware: 4×NVIDIA L4, vLLM 0.19 serving the model with tensor parallel 4. Measured: **generation about 36 tokens/s
  for one request**; prompt processing is fast (prefix cache hit rate about 79%); vLLM serves **one request at a time**
  (KV cache 2% used). Context window 32,768 tokens; the harness compacts the conversation history at 14,336 tokens.
- The submission is a **declarative Google ADK agent config** (YAML), no Python entry point of our own. Allowed:
  `LlmAgent`, `SequentialAgent`, `ParallelAgent`, `LoopAgent`, sub-agents called as tools (`agent_tool`: each call starts a
  fresh session and must end with a text reply, which becomes the tool result for the caller), system instructions,
  "skills" (Python scripts that run inside the task's sandbox via a `run_skill_script` tool), and LoRA adapters (rank ≤
  128, up to 8, one base model for all agents). Per agent generation config: temperature, top_p, top_k, penalties,
  seed, stop sequences, `max_output_tokens`, `thinking_budget` (both ≤ 32,768), `include_thoughts`. Not available:
  callbacks with logic, planners, code executors, output schemas, forcing tool calls (`tool_choice=required`), vLLM
  server flags.
- The model always "thinks" (a reasoning channel) before answering; thinking tokens are generated tokens and cost time.
  A thinking budget cuts the thinking at N tokens.
- Training data rule: no outputs of other commercial LLMs may be used to train the model (a LoRA must be trained on
  other data).
- Design rules of the team (keep them): a **deterministic procedure in scripts drives the model, one bounded decision
  per step**; every change must be **generic** (hold for any repository and issue, never a rule for one task); the
  model must not be sent back to free exploration with shell tools.

## The current system (how a task runs)
A coordinator agent routes the work through sub-agents, each called as a tool, each with one responsibility. A script
(a state machine over a JSON state file in the sandbox) decides the next stage; the coordinator only passes the
script's "next call" line on.

Stages, in order: **requirements** (the model quotes the issue's sentences and states what must happen) → **reproduce**
(the model writes a failing test; currently skipped under 5 min) → **locate** (the script ranks candidate
functions/classes with BM25 and shows them; the model chooses 1–3 per requirement) → **scope** (the script lists related
code; the model chooses which must change too) → **specify** (per place: the model writes one sentence of what changes
there, seeing that place's code and the code it calls) → **edit** (per place: the model sends the whole new definition
or a replace of lines; the script applies it, checks syntax and runs related existing tests, undoes it if they break)
→ **review** (per requirement: the model says done / not done; often skipped by the script when its own checks pass) →
submit.

Each sub-agent call: the agent first calls its script with `open` (gets the view), makes its decision(s) by calling the
script, then replies with one text line the script gives (the coordinator's next call). Locate, review and specify
handle several targets in one agent call; edit handles one place per call. Views are 2,000–12,000 characters.

Thinking budgets now: coordinator 512 (temperature 0.2); requirements, specify, reproduce 1,024 (output cap 3,072);
edit 1,024 (cap 4,096); locate, scope, review 512 (cap 2,048); agents at temperature 1.0, top_k 64, top_p 0.95.

## Measured (latest Kaggle run, 10 tasks, 5 min each)
Result: 3 of 10 resolved; 7 of 10 stopped at 5 minutes (6 of 10 had a non-empty patch).
- 270 model calls, 86,000 generated tokens (about 85% thinking), 2,635 s of model calls; generation is about 90% of
  the time; the scripts' own work (tests, ranking) is under 10%.

| Kind of call | Calls | Wall time | Tokens/call |
|---|---|---|---|
| A sub-agent's decision | 91 | 1,822 s (69%) | 663 |
| Coordinator (routing) | 80 | 276 s (10%) | 118 |
| A sub-agent's final reply (copies one line) | 54 | 235 s (9%) | 114 |
| A sub-agent's `open` | 41 | 227 s (9%) | 185 |
| Refused / error / unused | 4 | 74 s (3%) | 642 |

| Agent | Calls | Wall time |
|---|---|---|
| specify | 77 | 1,240 s (47%) |
| requirements | 30 | 339 s (13%) |
| coordinator | 80 | 276 s (10%) |
| locate | 35 | 275 s (10%) |
| edit | 26 | 229 s (9%) |
| scope | 17 | 210 s (8%) |
| review | 5 | 67 s (3%) |

A hard decision (specify) usually spends its whole thinking budget: its time is set by the budget (about 30 s at
1,024 tokens). Plans often have 2–6 places; copies of the same code in two packages, and places added on request of a
specifier, multiply the specify and edit calls.
