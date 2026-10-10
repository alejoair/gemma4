# Literature: what the 31B does badly, and the papers behind the design

Moved from CLAUDE.md on 2026-10-09. The checklist that every design change is checked against is `docs/llm_checklist.md`; the newer literature reports are `docs/llm_strengths.md`, `docs/prompting_input.md` and `docs/prompting_feedback.md`; per-script techniques are in `docs/scripts_spec.md`.

## What the 31B does badly, and what the literature does about it (2026-10-08)

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


## Literature behind the design
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
