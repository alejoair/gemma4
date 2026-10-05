# Bench

Tools to check a submission before spending the one daily Kaggle submit.
All need Python >= 3.12 and the wheels `swegemma`, `adk_submission`, `adk_eval_core`
from the Kaggle dataset `metric/gemma-4-developer-agent-wheelhouse`
(plus `pytest`, `pytest-httpbin` for phase 2).

| Script | What it proves | Needs |
|---|---|---|
| `../tools/validate_submission.py <dir> [--zip out.zip]` | The competition's own validators and compiler accept the submission | nothing else |
| `smoke_test.py <dir>` | State passing between stages (`output_key`, `{var}`, `include_contents`), `submit_patch` only in the last stage. Pipeline `b_pipeline` only | nothing else |
| `e2e_gold.py <dir> <data_dir>` | Full harness run (sandbox, tools, patch extraction, phase 2) with a scripted LLM that applies the gold patch | one task's snapshot, graph, embeddings, `sandbox/setup.py` and `wheels/` from the competition data |

None of these measure agent quality. That needs the real model (vLLM, GPU) on the
129 development tasks; compare variants by the tasks each one solves, not by the
public score (one task = 0.017, noise is about 2 tasks).

The upload must be named exactly `submission.zip`, with `agent.yaml` at the zip root.
