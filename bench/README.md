# Bench

Tools to check the submission in `../submission` before spending the one daily Kaggle submit.
All need Python >= 3.12 and the wheels `swegemma`, `adk_submission`, `adk_eval_core` from the Kaggle
dataset `metric/gemma-4-developer-agent-wheelhouse` (plus `pytest`, `pytest-httpbin` for phase 2).

| Script | What it proves | Needs |
|---|---|---|
| `../tools/validate_submission.py submission [--zip out.zip]` | The competition's own validators and compiler accept the submission | nothing else |
| `loc_bench.py <data_dir>` | Offline localization benchmark: how often BM25 + identifiers over the code graph ranks a gold file first | tasks.jsonl and graphs/ |

None of these measure agent quality. `kaggle_eval.py` does: it runs the submission on a fixed,
repo-stratified sample of the development tasks with the real model (vLLM) in a Kaggle GPU notebook and
writes one CSV row per task. Re-run with the same `--n` and `--seed` after each change and compare with
`compare.py before.csv after.csv` (solved counts, tasks only one run solved, exact McNemar p-value).
Not run yet.

Upload rules: the file must be named exactly `submission.zip` with `agent.yaml` at the zip root.
