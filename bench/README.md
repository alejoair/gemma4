# Bench

Tools to check a submission before spending the one daily Kaggle submit.
All need Python >= 3.12 and the wheels `swegemma`, `adk_submission`, `adk_eval_core`
from the Kaggle dataset `metric/gemma-4-developer-agent-wheelhouse`
(plus `pytest`, `pytest-httpbin` for phase 2).

| Script | What it proves | Needs |
|---|---|---|
| `../tools/validate_submission.py <dir> [--zip out.zip]` | The competition's own validators and compiler accept the submission | nothing else |
| `smoke_test.py <dir>` | State passing between stages (`output_key`, `{var}`, `include_contents`), `submit_patch` only in the last stage. Pipeline `b_pipeline` only | nothing else |
| `smoke_test_c.py <dir>` | Same for `c_pipeline_v2`: three parallel locators, merge, repro gate | nothing else |
| `loc_bench.py <data_dir>` | Offline localization benchmark: how often a BM25 + identifier retriever over the code graph ranks a gold file first (129 dev tasks, no model) | tasks.jsonl and graphs/ |
| `e2e_gold.py <dir> <data_dir>` | Full harness run (sandbox, tools, patch extraction, phase 2) with a scripted LLM that applies the gold patch | one task's snapshot, graph, embeddings, `sandbox/setup.py` and `wheels/` from the competition data |

None of these measure agent quality. That needs the real model (vLLM, GPU) on the
129 development tasks; compare variants by the tasks each one solves, not by the
public score (one task = 0.017, noise is about 2 tasks).

The upload must be named exactly `submission.zip`, with `agent.yaml` at the zip root.

## Measuring quality (needs the GPU)

`kaggle_eval.py` runs one submission on a fixed, repo-stratified sample of the development tasks with the
real model (vLLM) inside a Kaggle notebook, and writes one CSV row per task. Run it with the same `--n` and
`--seed` for every variant, then `compare.py a.csv b.csv c.csv` prints solved counts and, per pair, the tasks
only one variant solved with an exact McNemar p-value. It has not been run yet.
