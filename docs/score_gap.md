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

## vf5's empty patches (analysis of the Kaggle traces, 10-20)

vf5 submitted 11 empty patches in 30 tasks; 8 of them were stopped at 5 minutes. Statement lengths matter: 6 of the 11
statements are 64-354 characters, mostly a PR title and "Fixes #N".

| Cause | Tasks | What the trace shows |
|---|---|---|
| Every specifier answered "no change" | fastapi_13207, rich_3934, fastapi_14419, fastapi_15661 | The specifier reads the place and judges that it already does what the requirement says: "it *already* handles `None` by returning `""`" (rich_3934), "this already does that" (fastapi_14419), "Correct" (fastapi_15661, which needs a new file). These statements give only a title, and the specifier has no failing output, so the code looks right. |
| The fix was specified but the time ran out before the edit | requests_7505, fastapi_14459, rich_3480, rich_3180 | Specify took 120-200 s, going back and forth between "missing" (the change is in other code) and "no change". The edits arrived at 270-308 s and were refused with "the time is up". In fastapi_14459, a `replace` was also refused twice because its old lines occur 2 times. In rich_3180, the reference's place `divide_line` got its sentence at 293 s. |
| Requirements took the whole budget | rich_4076 | The same malformed call (`file_path` "req.py`,skill_name:") was repeated 98 times: the ADK's error never reaches the script. |
| The editor's call was written inside its thinking | rich_4075 | The `replace` that removes the two lines (the right fix) is at the end of a thought, so no call was made. Review answered "Nothing to review" and repair answered "Nothing is left to change", although the patch was empty and the place was still "to edit". |
| The same place appears twice in the plan | rich_3772 | The nested `render_stack` was listed once as "to edit" and once as "no change". The editor and repair spent 130 s asking why. |

**vf5's two fixes for empty patches fired but changed nothing:**
- The warning "With this, no place of the fix changes: the patch stays empty" fired in 7 tasks. Every time, the model sent "no change" again. Its thought in fastapi_13207: "Let's try `no change` again to see if it moves to `get_schema_from_model_field`".
- Repair's retry on the main place fired in 3 tasks (fastapi_13207, fastapi_14372, rich_3772). None of them produced a diff from it.

**How much it matters:** S1 resolved only 1 of these 11 tasks (rich_4075). Its patches for the other 10 fail as well. Of S1's three tasks that vf5 does not resolve, two are tasks where vf5 wrote a wrong patch (rich_3718 and rich_3043). Removing the empty patches alone would recover about 1 task against S1.

## S14: 0.00 with the native thinking off (10-28)

S14 (vf22: thinking_budget 0, think.py) scored 0.00, against 10/30 on our Kaggle eval and S13's 0.10 with the thinking
on. No error was reported; the score took about 20 h. The likeliest cause is vLLM 0.19.1's gemma4 tool parser hang
(`_parse_gemma4_array`, a loop on the model's output): the thinking-off builds hit it once in about 30 tasks (vf22 and
vf24b in rich_4075, vf25 in rich_3934). Our eval's watchdog kills and restarts vLLM; the scorer has no such watchdog, so
from the hang on every task would time out, and with a resolve rate near 10% a hang among the first tasks leaves
nothing. Not proven: the scorer's logs are not visible, and it may also treat thinking_budget 0 differently. Either way
a thinking-off build is not sent again; vf29b (native thinking on, budget 256) keeps the thinking on, as S13 did.
