# Why the internal eval and the leaderboard disagree (investigation, 10-19)

| Build | Internal eval (Kaggle, our judge) | Public leaderboard |
|---|---|---|
| S1, the official sample agent (no LoRA, out 8192, 4 min, 40 calls) | **14/30** (version 35, 10-19) | 0.12 |
| S4 = V3 (native tools plus scripts, 5 min) | 3/10 (old 10-task set, old judge) | 0.13 |
| S7 = V25 (stages build) | 5/30, 8/30 on V25-30 | 0.06 |
| S8 = vf5 (fast build) | 12/30 | 0.06 |
| S9 = vf11 | not measured (vf10, its neighbour, 7/30) | 0.05 |

The internal eval ranks vf5 far above S1 and S4; the leaderboard ranks it below both. Every dev task comes from
fastapi, rich, requests or httpx (67, 48, 13 and 1 of 129); the hidden tasks come from private repositories.

## Checked and ruled out (or weakened)

- **Repository names or layouts in the scripts.** No repository name in the logic; the src/-only import root and
  `docs_src` were fixed (docs/design_fast.md). The audit's 19 findings were fixed in vf10/vf11.
- **Concurrent tasks in the scorer.** Unknown how many tasks the scorer runs at once, but vLLM on the 4×L4 loses only
  about 10% per request at 4 requests at once (design_stages.md, `--bench-concurrency`). A factor of six is not
  explained by that unless the scorer runs many more tasks at once.
- **CPU of the sandbox (2 vCPUs per container).** Replays of the scripts take the same time pinned to two CPUs
  (fastapi_14361: 24.6 s on four, 17.8 s on two; rich_3905: 15.3 s and 12.9 s). Script time is about 1 s per call.
- **Where the scripts run.** In both the scorer and our eval, `run_skill_script` runs in the task sandbox
  (`AdkSandboxCodeExecutor` on `context.sandbox`); the scripts import only the standard library and their own modules,
  so the scorer's `python:3.13-slim` image has everything they need.

## What remains

1. **Our eval set is not the hidden set.** Our agent has been tuned for weeks on the same three repositories; the
   leaderboard measures repositories it has never seen. Two quick facts point that way: the sample agent, with
   native tools and no tuning, scores 0.12 there; the public notebooks that score 0.17-0.18 are that baseline with a
   better prompt and 8 minutes.
2. **The scorer's per-task speed or setup.** Our agent uses most of its 5 minutes even in our eval (vf5 median 253 s,
   10 of 30 at the cap); a slowdown of about 1.4 times would leave only the fastest 1-2 of its 12 resolved tasks inside
   the cap (their agent times: 161-328 s). The scorer counts sandbox setup in its 12 hours; its per-task speed is not
   observable.

## Next steps

- Version 35 of the eval notebook runs S1 (the sample agent as submitted) on the same 30 tasks with the same judge. If
  S1 scores near vf5 there, our eval does not tell the builds apart and the gap is the task set; if it scores far below
  vf5, our agent's advantage does not carry to the hidden repositories.
- A held-out set from repositories the scripts have never seen (public SWE-bench-style tasks) is the only eval that can
  predict the leaderboard for a design tuned on fastapi, rich and requests.

## Result of the calibration (10-19)

S1, the official sample agent as submitted (native tools, 4 minutes, 40 calls, no tuning), resolved **14 of the same 30
tasks** with the same judge: more than vf5 (12), vf5d (11) and vf10 (7). It leaves 4 empty patches against 8-11 for the
fast builds. So:

- **The internal eval orders the builds as the leaderboard does** (S1 above vf5 in both), but compresses the gap: 14 vs
  12 here, 0.12 vs 0.06 there. The hidden set is much harder for every build (S1: 47% here, about 12% there).
- **Our step design has never beaten the untuned baseline on our own tasks.** The internal numbers looked like progress
  (vf3 7/30, vf4 9/30, vf5 12/30) because no baseline was measured on the same tasks with the same judge; the baseline
  was already at 14/30.
- From now on every internal eval should run S1 next to the build on the same tasks: a build is better only when it
  beats S1 there.
