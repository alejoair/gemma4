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
