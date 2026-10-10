# Public notebooks that score 0.15–0.18 (reviewed 2026-10-09)

Pulled with the kaggle CLI on 2026-10-09; read as data, not run. The public leaderboard has 58 tasks and shown
scores are truncated: 0.05 = 3 tasks, 0.12 = 7, 0.17 = 10, 0.18 = 11, 0.24 = 14. Identical resubmissions move by
about ±2 tasks.

| Notebook | Score | Source of the score |
|---|---|---|
| verracodeguacas/gemma-4-agent-budget-fit-single-agent (v5) | 0.18 | lucifer19/agentic-counterexample-lab table; honghanhhh text |
| lavinwins/... calibrated-sin (v1), same files | 0.17 | same table |
| matterhorn3838/gemma-4-superagent | about 0.17 | flexonafft notebook: "The source reportedly scored 0.17" |
| matterhorn3838/gemma-gemini-the-ultimate-duo (v3), same files | 0.15 | same table |
| yiyu0716/g4-pack-yiyu-v19-fixed-trace-t7p5 | between 0.15 and 0.17 | inferred from the score sort only |
| honghanhhh/gemma-4-baseline-lb-0-24 | not 0.24 | 0.24 is the leaderboard leader it quotes; "It is not a measured score" |

What all scored ones share (except yiyu):
- **Structure.** One `LlmAgent` with the native tools `run_command`, `read_file`, `edit_file`, `write_file`, `get_status` and `submit_patch`. No skills, scripts, sub-agents or LoRA.
- **Graph tools.** Registered but forbidden in the prompt, or not registered.
- **Generation.** `temperature 0.2`, `top_p 0.95`, `max_output_tokens 8192`, `thinking_budget 4096`, `include_thoughts true`. `include_thoughts: false` turns thinking off in the bridge, and honghanhhh reports that solved fewer tasks.
- **eval_config.** `timeout_seconds 240`, `max_tool_calls 28`, `max_time_minutes 8`, `max_turns 80`. Their measurements: only about half of the fixes the model eventually finds are in the file at 4 minutes, and runs with no source edit by call 12 fail 87% of the time. A scored run with these caps finished within 12 hours.
- **Prompt.** Copy the exact names and messages of the issue. Locate with `git grep -n ... | head -20` plus `read_file` of about 40–120 lines. Edit by call 12, with a best guess allowed. Run at most 1–2 targeted checks (`pytest file -k`, `python -c`). Never end with an empty patch; then `git diff` and `submit_patch`.
- **No pipelines.** honghanhhh: "A read-only analyzer beside a coder solved fewer development tasks than the coder alone".

yiyu (the only one with skills) works differently:
- A `code_analyzer` sub-agent is reached through `transfer_to_agent` and has `output_key: analysis_plan`.
- `run_skill_script` is called once, to install `/tmp/g4edit.py`. After that the model uses it through `run_command`, with SEARCH/REPLACE blocks, a syntax check, refusal of test files, and a trace of a reproduction.
- `temperature 1.0`; thinking off.

**Checked in our local harness:** `timeout_seconds` is also the timeout of the hidden-test run (`swegemma/harness/verification.py` runs pytest with `timeout=config.harness.command_timeout_seconds`, which `config.py` sets from `timeout_seconds`, and `-o timeout=0`). With 120 s, a correct patch fails when the test files take longer. We moved to 240 s.

**Contrast with our results.**
- Our S1 (the sample agent) scored 0.12. The scored notebooks are that baseline plus budget and prompt tuning.
- Our pipelines (S2 4-stage, committee) scored 0.05.
- The current design forces every action through `run_skill_script` arguments, the call form where 10–31% of our script calls were malformed in V1–V6.
