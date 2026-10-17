# A faster stages build: no coordinator, parallel workers (design, 10-16)

The user's request (10-16): "quiero que pienses un rediseño para hacer el sistema más rápido". The trigger is the
15-minute trial (V25-T15, `docs/design_stages.md`). With 15 minutes the same build resolves 10/30; with 5 minutes it
resolves 5/30. The 12 h budget allows at most about 6 minutes per task. The same work therefore has to take about
half the time.

## Where the time goes (V25-T15: 30 tasks, natural durations, mean 486 s of agent time per task)

| Kind of model call | Calls per task | Time per task | Share | What it is |
|---|---|---|---|---|
| Decisions | 10.1 | 284 s | 58% | The calls that decide something: requirements, a place, a sentence, an edit, a verdict |
| `open` | 6.9 | 76 s | 16% | Each agent's first call, which only fetches its view |
| Coordinator | 11.6 | 54 s | 11% | Routing: it copies the next call |
| Final replies | 6.9 | 52 s | 11% | Each agent's last message: the coordinator's next line, copied |
| Refused or unused | 0.7 | 20 s | 4% | |

More measurements from the same 30 tasks:
- **Tokens.** Output is about 12,200 tokens per task, of which about 85% is thinking. The prompt averages about 4,100
  tokens per call.
- **Where the wall time goes.** About 70% is generation at about 36 tokens/s. The scripts take 31 s per task (6%).
- **By agent:** specify 39%, edit 22%, coordinator 11%, review 10%, requirements 10%, locate 8%.
- **Calls that decide nothing.** 25 of the 36 model calls per task (coordinator, `open` and final replies) take 38%
  of the time.
- **The GPU is mostly idle.** Kaggle's vLLM on 4×L4 gives 39.8 tok/s to one request, 76 tok/s total to 2 and 143 tok/s
  total to 4 (V25's bench). Within a task the requests are strictly one after another, although places,
  requirements and the reproduction are independent.

## The design

The scripts' logic and views stay as they are. What changes is who calls them and how many at once.

1. **No coordinator.** The root becomes a `SequentialAgent` of stage agents, and each stage agent has
   `include_contents: none`. ADK then gives each agent only its instruction, the previous agent's final message and
   its own calls (`google/adk/flows/llm_flows/contents.py`, `_get_current_turn_contents`), which keeps today's fresh
   context per stage without a router.
   - Saves the coordinator's 11.6 calls per task.
   - Final replies become a plain `done`: nothing to copy, and no "There is nothing to decide: copy it" line (checklist
     B2, B12).
2. **Parallel workers inside a stage.** A `ParallelAgent` runs K identical workers, and the branches do not see each
   other's events.
   - Each worker has its own script file (`specify1.py`, `specify2.py`, …: the slot comes from the file name, so no
     id is sent, B3).
   - Each worker claims the next open item from the script's queue under a file lock, and goes on to the next one
     with the existing chain answer ("Your part goes on with …"), which is work stealing.
   - A worker with nothing to do is told so at `open` and replies `done`.
   - Writes to state and source files take a lock (`fcntl`).
   - K is set by the measured item counts. Per task, the median is specify 2.5 places, locate 1 requirement, edit 1
     place, review 1 requirement; the maxima are 5, 3, 12 and 3. Hence specify 3, locate 2, edit 2, review 1.
3. **Reproduce comes back, in parallel with specify.** It was off at 5 minutes because it cost 30-225 s in series.
   Next to specify, the longest stage at about 190 s per task, it adds a failing test for review almost without wall
   time (checklist F2, F3, B11).
4. **One unrolled repair pass.** The fixed order replaces the dynamic route:

   requirements → locate ×2 → [specify ×3 ‖ reproduce] → edit ×2 → review → repair → finish

   - `repair` is one agent that specifies and edits, in one context, the places review sent back (today
     `MAX_ROUNDS` 2). With nothing sent back it ends at `open`.
   - `finish` has only `submit_patch` and thinking 0. The root must end with `submit_patch`: otherwise the harness
     nudges it, which would run the whole pipeline again.
5. **The budget is checked in every answer**, not only in the coordinator's `route.py`. Over the budget, every
   script answer says the time is up and gives the `done` reply, so the pipeline runs through to `finish` and submits.
   Today 44 of 90 runs were cut by the harness while a sub-agent was still working (`docs/design_stages.md`, "How the
   90 runs ended").

## Expected effect (an estimate, not evidence)

Per task in V25-T15:
- remove the coordinator's time;
- divide the time of locate, specify, edit and review by min(K, items), with K=3;
- keep requirements as it is.

| K | Mean time per task |
|---|---|
| 1 (no coordinator only) | 432 s |
| 2 | 299 s |
| 3 | 267 s |

Against 486 s today, that is about 40-45% less. Two things make the estimate optimistic:
- concurrent requests run about 10% slower each;
- idle workers' `open` and `done` calls share the GPU.

In V25-T15 the resolved tasks finished at 154-516 s. About 60% of that is 92-310 s, which is mostly inside 5 minutes.

## Checklist (`docs/llm_checklist.md`)

| Item | Effect | Why |
|---|---|---|
| B2 copying long text | closer | No coordinator lines to copy; final replies are `done` |
| B12 contradictions | closer | The coordinator / `route.py` / returned-line wording, the source of several fixes (gk2, gk12), is gone |
| W4 concrete next call | closer | Each agent's NEXT is its own; no line relayed through another agent |
| B10 budget | closer | Every answer checks it; time-up ends each stage and `finish` submits |
| F2, F3, B11 checks | closer | The reproduction test returns at no wall cost and review reruns it |
| B7 long context | closer | No coordinator history; each stage's context is its own turn |
| B3 ids | neutral | The worker slot is in the script's file name, never sent |
| B8 forms per agent | neutral, `repair` farther | Every stage keeps its forms; `repair` has specify's and edit's forms in one agent (reason: one idle-cheap agent instead of two idle stages) |
| P8 progress | neutral | Progress line kept in every `open` |
| B9 exploring | neutral | Same forms |
| W1, W3, W6 | neutral | Views unchanged |

**Why it is generic:** it changes how the stages are scheduled, not what any of them decides. The queue, locks and
worker slots hold for any repository and any issue.

## Risks and how they are checked first

- **ParallelAgent branches share `/workspace`.** Edits to two places of one file need a lock around read, change and
  write. Concurrent pytest runs need their own temp dirs (`_tests` already uses per-call dirs: to check). The
  reproduce worker writes only its own test file.
- **The fixed order loses the route's adaptivity.**
  - A `missing` answer stays inside the stage's queue: the new place is claimed by a free worker.
  - A second review round is gone. One repair pass in place of two `MAX_ROUNDS` loses little: V23-V25 rarely used
    the second round within 5 minutes.
- **Idle workers.** Each costs 2 short calls in parallel, which is throughput, not wall time.
- **Feasibility check before building everything:**
  1. a two-stage `SequentialAgent` with `include_contents: none` and a 2-worker `ParallelAgent`, run through the
     harness with Haiku: each worker sees only its own calls, and the claims do not collide;
  2. then the full pipeline on the hk30 tasks with Haiku, for system faults;
  3. then a Kaggle eval with Gemma at 5 minutes on the V25-30s tasks (V25: 5/30), 4 tasks at once as before.

## Built and tested with Haiku (10-17)

`tools/make_fast.py` writes `variants/fast/`, and `tools/build.py <out> --variant stages,fast` stacks it on the stages
build. `_stages.main_fast` holds the workers' claims, their chains and the budget check.

- **Feasibility (hf1, rich_3905):** resolved in 74 s.
  - Each worker's prompt was about 3,000 tokens: its instruction and its own calls only.
  - The three specifiers and reproduce opened in the same second; idle workers replied `done`.
  - Review, repair and finish ran in order.
  - One fault was found and fixed: the requirements agent's first call already holds the names, and it was answered
    with the first question instead.
- **The 30 hk30 tasks (hf):** 18/30 against 15/30 for V25 and V26.
  - Mean 80 s per task against 128 s for V26.
  - No internal error, no refused or unused call.
  - Not Kaggle evidence: Haiku is fast enough that time was never the limit.
- **Next:** the Kaggle eval with Gemma at 5 minutes on the V25-30s tasks (V25: 5/30), one task at a time as the
  official harness is assumed to run them (the fast build's own workers run at once).

## Can a global clock replace the per-task limit? (10-17, the user's question)

The user asked whether one time budget for all tasks could replace a fixed per-task limit: a large limit such as
15 minutes, with tasks that end early leaving time to the long ones, and no harm if the last tasks are not reached.

**What the competition says** (Evaluation page): "Your agent has a limit of 12 hours to submit patches for all tasks,
inclusive of sandbox setup time, but excluding time for patch validation. You may, but are not required, to set
per-task time limits in your submission's `eval_config.yaml`." Nothing is said about a run that passes 12 h. Stage 1
(`scripts/inference.py`, not public) writes `submission.parquet` with one prediction per task, and Stage 2 scores it.
S6, with 60 minutes per task, ended COMPLETE with no score at all, not a partial one. That is the only evidence: going
over the 12 h loses the whole submission, so "some tasks not reached" is not safe.

**Whether the agent can know the global time:**
- **Session state.** The harness gives the agent only `problem_description` and `hints`; it says nothing of the run's
  start, the task's index or the number of tasks.
- **Fresh containers.** In `swegemma/sandbox/docker.py`, `reuse_containers` defaults to `False`: each task gets a fresh
  container, and the sandbox is gVisor (runsc) without network.
- **Pooled containers are wiped.** When containers are pooled, `stop()` kills every process and empties `/workspace`,
  `/tmp`, `/var/tmp` and `/root` between tasks.
- **So a script sees only its own task's clock.** Keeping state elsewhere in the container to get around this
  isolation would work against the harness's design and could fail silently: it is not done.

**What follows:**
- The only control is the fixed per-task limit (`max_time_minutes`) and our own stop inside it (`BUDGET_S`).
- A task that ends early does leave time to the others, but only for a limit chosen in advance. The limit has to satisfy
  120 × (mean of min(duration, limit) + setup) + about 8.5 min ≤ 12 h, with a margin, because the official setup per
  task and the number of tasks are not known exactly.
- With the stages build, the trial V25-T15 gave a mean of 341 s per task at 6 minutes and 380 s at 7, which is the most
  that fits.
- The fast build ends tasks sooner. To choose its limit, the same kind of trial is needed: the fast build at 15 minutes,
  for its natural durations, then the largest limit whose total fits.

## vf1 on Kaggle with Gemma (10-17): 4/30, and why

vf1 is the fast build at 5 minutes, one task at a time, on the V25-30s tasks. The harness resolved 4/30, against 5/30
for V25. There were 17 empty patches and 16 stops at 5 minutes. Median stage spans: requirements 30 s, locate 21 s, the
specify stage 117 s, edit 39 s. 8 of the 30 tasks never reached edit.

1. **The reproduce branch held the specify stage open.** A ParallelAgent ends when all its branches end. In rich_3905
   the specifiers were done at 119 s; the reproduce agent's test was kept on its first try, and it sent it 8 more times
   until the time was up, answered only "No reproduction test is needed now". Reproduce is off in the fast build
   (`tools/make_fast.py`, `REPRODUCE = False`) until it can end on its own.
2. **Agents did not stop when told to reply done.** 161 calls came after that answer: reproduce 61, specify2 41,
   requirements 23. The idle specifiers' thoughts show why: the progress line listed places "to specify", while the
   answer said every place was specified or taken. Gemma opened again to find the place it believed it was missing
   (checklist B12). Two fixes:
   - an idle worker is told which agent holds each item, with no plan line;
   - a worker that already finished is told what it finished.

   These are in vf3.

The Haiku runs showed neither fault: Haiku stops when told to. Only the model run of the competition's model showed
them.

## vf3 on Kaggle (10-17): 7/30

7/30 judged, against 5/30 for V25 on the same tasks. 7 tasks stopped at 5 minutes (V25: 18), the mean was 243 s, and 27
of 30 reached edit. Calls after "reply done" fell from 161 to 80. 54 of those came from one fault, in the requirements
stage:
- Gemma called `req.py` with a shell command (`grep -r "netrc" src/requests`).
- The single item was saved as requirement R1, because one item was read as a requirement without its quote.
- Every later attempt to send the real requirements was answered "Your part is already finished".

Fixed in vf4:
- a single item, or an item that is a command, is not a requirement: the answer gives the pair form;
- the requirements agent may replace its requirements while no requirement is located yet.

## vf5 (10-17): the prompt and answer audit

Every text the model reads was audited against the checklist and the vf3 traces. The findings and fixes, agent by
agent, are in `docs/prompt_audit_fast.md`. The fixes add no stage and no agent.

| Item | Effect | Why |
|---|---|---|
| B12 contradictions | closer | "Your part is finished" now says the call was not applied and why stopping is safe. The final undone answer no longer reads as a contradiction. A long place's NEXT is the same in every answer. |
| P8 progress | closer | Every "done" answer recaps what this agent finished and names the next stage. |
| B11 trust in output | closer | `missing` for a planned place states that fact. "Nothing holds that" is limited to the files searched. The empty patch is warned about and retried. |
| W3 code in front of it | closer | A long signature keeps the hidden lines with the sentence's terms. A module answer lists its constants. |
| B5 quoting failed input | closer | The locate answer counts unmatched names instead of quoting them. |
| B2 / W4 | closer | Several groups of a long place go in one call, so a place is not closed half changed. |
| B8 forms | farther, one form | `replace` takes several pairs. Reason: one call per group closed the place after the first group (2 traces lost half the change); the form is the same one, repeated. |
| B9 exploring | farther, slightly | A locate worker may replace its last choice, and the requirements agent its requirements, before the next stage starts. Reason: a first wrong choice was final (4 of 8 traces); the window closes when the next stage starts. |
| B1, B3, W1 | neutral | No numbers, ids or lists changed. |

**Why it is generic:** every fix changes what an answer says or which form it accepts. Each one keys on the state of
the procedure (a finished part, a planned place, an empty diff, the files searched), never on a repository, a
library or a task.

## vf4 on Kaggle (10-17): 9/30

vf4 resolved 9/30 (judged; vf3 7/30).
- **Gains:** it resolved every task vf3 resolved, plus fastapi_14361 and rich_2943.
- **Time stops:** 4 tasks stopped at 5 minutes (vf3: 7).
- **Empty patches:** 10 (vf3: 14).
- **Mean time:** 255 s per task.

The requirements fix removed the loop of vf3: there are 28 log lines with "already finished", against 80 calls after "reply done" in vf3.

The kernel's outputs were replaced by the next version before they were downloaded. Only the notebook log is kept (scratchpad `kout_vf4`); it holds the cut-down trace of every task, which is what follows comes from.

**Why the 21 tasks were not resolved:**

| Cause | Tasks | Detail |
|---|---|---|
| The specifiers wrote "no change" on every place: empty patch | 10 | In 6 of them the place located is the reference's place or one next to it: fastapi_14419 (`solve_dependencies`, `Dependant.cache_key`), rich_3043 (`rich/_export_format.py`), rich_3180 (`divide_line` in `rich/_wrap.py`), rich_3934 (`Live`), rich_3942 (`rich/default_styles.py`), rich_3772 (`Traceback`). In the other 4 the place was wrong or missing: rich_4076, rich_4075 (partly right), fastapi_14459 (partly right), and fastapi_15661, which needs a new file. |
| A patch that fails the hidden tests | 7 | fastapi_15280, requests_7505, requests_7205, fastapi_13207, rich_3718, fastapi_14953, rich_3480 |
| Stopped at 5 minutes with a partial patch | 4 | fastapi_14258 and fastapi_14372 in repair; fastapi_13713 and rich_3278 in edit |

How a "no change" empties the patch:
- Each specifier sees one place. When two places share a requirement, each one judged that the change belongs to the other (fastapi_14419: both sent "no change" in the same second).
- In rich_3043 the specifier thought, after its "no change": "Wait, if I say no change, then the fix is not applied". Its next call was answered "Your part is already finished".
- Review and repair then had nothing to do, and the empty patch was submitted with 1.5-2.5 minutes left.

vf5 already holds the two fixes for this:
- the specifier whose "no change" would leave every place unchanged is warned once that the patch would be empty;
- repair retries the main place once when the diff is empty and time is left.

vf5's Kaggle run measures whether they work.

Stage spans (medians over the 30 tasks):
- requirements end at about 45 s;
- locate takes about 15-20 s;
- specify about 40 s;
- edit 5-35 s (2 calls when nothing is left to edit).

Calls after a part was finished are few: 28 log lines, at most 5 per task.
