# ADK config and harness behaviour

Moved from CLAUDE.md on 2026-10-09. The current harness is swegemma 0.2.11 / adk_submission 0.2.13 (Kaggle wheelhouse of 2026-10-09; the local venv has the same wheels). The first section lists what changed from 0.2.7; the rest was checked in 0.2.7 and holds unless that section says otherwise.

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

