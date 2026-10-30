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

## The repository's layout found from its files, not assumed (10-18)

S8 (vf5) scored 0.06 on the public leaderboard although the eval judged 12/30; every dev task comes from fastapi,
rich, requests or httpx, and the hidden tasks from private repositories. The user asked to fix what assumed the shape
of those repositories. The audit found no repository name in the scripts' logic (only in comments citing traces), but
it did find layout assumptions:

- `src/` was the only import root besides the repository root, written in five places (`_tests._module_names`,
  `_tests._env`'s PYTHONPATH, `_tests._missing_modules`, `step.py`'s `module_of`, `_example._packages`). A package under
  `lib/`, `python/` or any other directory got wrong module names (the import check probed `lib.pkg.mod`), was not on
  the tests' PYTHONPATH, and was not recognised as the repository's package.
  Now `_repo.import_roots(root)` derives the roots from the files: a package's import root is the directory above its
  topmost `__init__.py`, ordered by how much code each holds, the repository root last. `_repo.module_name(root, rel)`
  names a file from the root that holds it.
- `DOC_DIRS` listed `docs_src`, fastapi's own directory name, and `_example._packages` listed it again with
  `tutorial`. Now a top-level directory is documentation, examples, benchmarks or scripts when the first word of its
  name is one of those conventions (`docs`, `docs_src`, `doc-examples`, `example_app`, `benchmarks`), `_repo.is_aux_name`.

Checks: on the dev repositories the roots and module names are the same as before (fastapi and rich: the root;
requests and httpx: `src`, then the root); a made-up `lib/` layout gives `lib` and `mypkg.sub.core`; replays of the
vf5d traces on 6 tasks (requests ×2, fastapi ×2, rich ×2) print NO PROBLEMS. No model run: the dev repositories do not
exercise the new cases, so only a task from a repository of another layout can show the effect.

| Checklist item | Effect | Why |
|---|---|---|
| B1-B15 (what the model reads and sends) | neutral | No text the model reads changes on the dev repositories. |
| What the scripts show about another layout | closer | Module names, the import check, the tests' imports and the repository's package are right for any import root. |

Why it is generic: the layout is read from each repository's own files (`__init__.py` chains); the remaining names
are conventions of Python projects (`tests`, `docs`, `examples`, `scripts`), none from one repository.

### Audit of the remaining assumptions (10-18)

A read-only audit of the scripts (findings marked "verified" were reproduced on made-up repositories). Ranked by the
harm on a private repository. Not fixed yet.

| # | Severity | Assumption | Failure on another repository |
|---|---|---|---|
| 1 | high | `_rank.Index` and `_impact.Table` are rebuilt on every call (17 builds in `_stages.py`, 10 in `step.py`), with no cache or size cap | fastapi: 1.2 s + 0.2 s per build; 273k lines: 7.9 s + 4.5 s. On 0.5-1M lines one locate opening costs 40-150 s, under `_state.lock`, so the workers wait in turn |
| 2 | high | `SKIP_DIRS` (`build`, `dist`, `env`, `venv`) applies at any depth (verified) | a real subpackage `pkg/build/` or `pkg/env/` is not indexed, cannot be found or edited |
| 3 | high | `is_test_path`: any `test/`, `tests/`, `testing/` directory or `test_*.py` file at any depth is a test (verified) | `pkg/testing/fixtures.py` or `pkg/test_utils.py` is left out of the index and its edit is refused ("test files are not edited"): empty patch when the fix is there |
| 4 | high/medium | namespace packages (no `__init__.py` in `src/acme/`) (verified) | wrong module names (`billing.api` for `acme.billing.api`), a false "import billing" shown to the model, and `src/acme` put on PYTHONPATH where its `http/` or `logging/` would shadow the stdlib |
| 5 | medium | the base worktree holds tracked files only | generated `_version.py` or compiled extensions are missing there: the base cannot import, every new failure is "unknown" |
| 6 | medium | a file with non-UTF-8 bytes (verified) | every edit of it is refused as not compiling (`ast.parse` of a str with surrogates) |
| 7 | medium | recursive AST walk (verified) | one generated file with a deep expression raises `RecursionError` in every index build: every call fails |
| 8 | medium | the repository's own package installed as a wheel is taken for a library | own code shown as "library, read only" (the installed copy), new names classed as library |
| 9 | medium | stop words drop parts of backticked names (verified) | `` `Cache.update` `` keeps only `Cache`: a target method named `update`, `add`, `get` ranks badly |
| 10 | medium | fixed timeouts that ignore `DEADLINE` (60 s import probes, 120 s worktree); `DEADLINE` set only in the fast build | a hanging import or slow worktree runs past the budget |
| 11 | medium | `RLIMIT_AS` 3 GiB on test runs | numpy/torch/OpenBLAS reserve address space: every run fails, everything is UNCHECKED |
| 12 | medium | vendored and generated code not filtered (`vendor/`, `third_party/`, `_vendor/`, `migrations/`, `*_pb2.py`) | pollutes the candidates and the "copies", and goes on PYTHONPATH |
| 13 | medium/low | `is_aux_name` by the first word (verified) | a real package `sample_app/` or `doc_builder/` in a flat layout is treated as documentation |
| 14 | medium/low | the call map runs the whole suite once, aborting on one collection error (`--maxfail=60`) | base build only; the map is empty and 150 s are lost |
| 15 | low/medium | `unittest.TestCase` classes not named `Test*` are not test classes | no example or template test in unittest-style repositories |
| 16 | low/medium | `_library_homes` imports up to 8 third-party libraries in the script's process, no timeout | a heavy or network-touching import slows or hangs the call |
| 17 | low | stand-in modules for missing test dependencies match everything; `inline_snapshot` named in the logic | a test passes vacuously |
| 18 | low | tests that rewrite tracked files leave the changes in the patch | golden files or snapshots end up in the patch |
| 19 | low | `_example._packages` sees only directories with `__init__.py` | a single-module project never matches the statement's example |

Checked and fine: no repository name in the logic or in the text shown to the model; the pytest plugin and
`_is_test_file` match the harness's `python_files`; exceptions are caught in the entry points (but faults 1 and 7 make
every call fail).

### Fixes of the audit (10-18)

Every item of the table but #3 was changed in the shared scripts (`_repo.py`, `_rank.py`, `_impact.py`, `_code.py`,
`_edit.py`, `_tests.py`, `_example.py`, `_statement.py`, `step.py`) and in `_stages.py`. Each fix was checked on a
made-up git repository that reproduces the failure (`scratchpad/gen/mkrepos.sh`, `gen/synth.py <scripts> <case>`), run
once with the scripts before the change (a copy of the vf5-era build) and once after, through the scripts' own
functions; on the dev repositories (requests_7205, fastapi_14361, rich_3905) the import roots, `iter_py` counts, the
repository's packages, the index and table sizes and the top 10 candidates of 3 queries each are the same before and
after; the 6 replays of the vf5d traces print NO PROBLEMS, and the first line of every replayed answer is the same as
with the build before the change but for the elapsed seconds.

| # | What changed | Before → after on the made-up repository |
|---|---|---|
| 1 | `_rank.Index(root)` and `_impact.Table(root, docs)` are kept per process (`_repo.memo`, keyed by root and arguments) and made again when `_repo.VERSION` changed (every `write_lines`, every git checkout/reset/stash/clean/apply, a test run's restore) or a file appeared, went, or changed size or time. A file over 1 MB or 20,000 lines is left out of both (`_repo.read_code`). | a 30,000-line generated module: indexed (0.54 s) → left out (0.00 s). Dev repositories: a second build 1.0-1.4 s → 0.013 s (fastapi), 0.002 s (rich); per call at most 6 builds → 3; build time over a whole replayed trace 14.5 → 10.0 s (fastapi_14361), 11.7 → 7.4 s (rich_3905), 1.8 → 1.2 s (requests_7205); a locate opening 1 index + 3 tables → 1 + 2 (the tables with and without documentation are different files), 2.8 → 2.1 s on fastapi |
| 2 | `SKIP_DIRS` (`build`, `dist`, `env`, `venv`, `.tox` ...) skip a directory only when it has no `__init__.py`; `.git`, `node_modules`, `site-packages`, caches always | `pkg/build/core.py` not indexed, `make_wheel` not found → indexed and found; `build/lib/` still skipped |
| 3 | not changed: the harness drops the same files from the patch (`verification.py`, `_is_protected_test_or_config_path`: `tests/`, `test/`, `testing/` directories, `test_*.py`, `conftest.py` and the runner configs `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`, `.pth`), so an edit there could never reach the score; refusing it says so early. A comment at `is_test_path` records it | — |
| 4 | `import_roots` climbs from a package through directories without `__init__.py` while the repository's own imports name the dotted path (`from acme.billing.api import x` → `acme.billing`); a module outside any package that the repository imports by its bare name makes its directory a root (`src/mymod.py`). `_tests._env` puts on PYTHONPATH only the roots none of whose packages or modules has a standard-library name (`sys.stdlib_module_names`), and the repository root always (`_repo.pythonpath_roots`) | `src/acme/billing/api.py`: module `billing.api`, PYTHONPATH `src/acme` → `acme.billing.api`, PYTHONPATH `src`; an unimported `src/acme` holding `http/`: on PYTHONPATH → left off |
| 5 | when the original code's worktree is made, the files git does not track that live inside the repository's package directories are copied into it: ignored ones (`git ls-files --others --ignored --exclude-standard --directory`, an ignored directory walked only when it is inside a package) and untracked ones recorded before the work started (never the agent's new files); at most 64 MB per file, 256 MB in all | ignored `pkg/_version.py`: `import pkg` in the worktree failed (`No module named 'pkg._version'`) → imports |
| 6 | `_repo.read_text`/`read_lines` decode with the file's PEP 263 coding cookie (or BOM), `write_lines` encodes with it (`_repo.encode_source`); `_edit._compiles` parses those bytes, so the parser honours the cookie; `_code.parse` parses a text holding surrogates as its bytes and drops a BOM. A character the file's encoding cannot hold refuses the edit with that reason | edit of a latin-1 file: refused (`surrogates not allowed`), no symbols → applied, the `\xe9` byte kept, `greet` found |
| 7 | `_code.symbols` walks iteratively and only through statements (where a definition can be); `_code.parse` returns None on RecursionError and MemoryError; the index and the table skip a file whose helpers raise them; `_impact._body_tokens` and `_edit._compiles` catch them | expressions of 3,000 and 30,000 terms: every index and table build raised RecursionError → both build (the 30,000-term file, which Python itself cannot compile, is indexed as module text and has no symbols) |
| 8 | `_repo.own_names(root)`: the top-level names of the repository's packages, namespace packages and modules. `step._library_def` and `_stages._library` return "not library" for them before any lookup; `_stages._library_modules` and `_lib_base` use them | a copy of `ownlib` on sys.path: `ownlib.core.func` shown as library code from the copy, `_library` True → None, False |
| 9 | `_statement.terms` and `model_terms`: code in backticks, traceback frames, the parts of a dotted name and the model's own names drop only Python keywords, `self` and `cls` (`CODE_STOP`), not the prose stop words | `` `Cache.update` loses entries ``: terms `Cache` only, top 2 `Cache`, `Store.cache_size` → `Cache` and `update`, top `Cache.update` |
| 10 | `_tests.DEADLINE` is set in `step.main` (journal start + `SWE_TASK_BUDGET`, 300 s, minus 10 s) and `_stages.main` (as `main_fast`); `_tests._budget(s)` caps every relative timeout: the import probes (`_tests.import_check`, `_stages._probe_imports`), the worktree (120 s), the example run, the library probes | an import that sleeps 12 s with 3 s left: 12.0 s → 3.0 s |
| 11 | `_tests._env` sets `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS` to 1. When a run on the original code (base run or call map) prints MemoryError or a thread or allocation failure, `no_mem_limit` is saved and the runs go on without RLIMIT_AS, the original code's and the edited code's alike (the check runs the edited code again when the flag appears during it) | a test that fails under the limit: original outcome failed → passed, `no_mem_limit` True |
| 12 | `iter_py` skips `vendor`, `_vendor`, `vendored`, `_vendored`, `third_party`, `_third_party`, `extern`, `external` directories at any depth and `*_pb2.py`, `*_pb2_grpc.py`; so do the index, the table, the candidates and the import roots. Migrations stay | index files 4 → 1 (`pkg/core.py`); import roots `vendor`, root → root; the top candidates for "session get" were the two vendored copies |
| 13 | `is_aux_name(name, root)`: never true for a name the packaging files declare (`_repo.declared_packages`: `[project] name`, setuptools `packages`/`packages.find.include`/`py-modules`, hatch wheel `packages`, poetry `name`/`packages`, flit `module`, maturin `module-name`; setup.cfg `name`/`packages`/`py_modules`/`packages.find`; setup.py `name=`/`packages=[...]`/`py_modules=[...]`; tomllib, configparser and regexes, each in try/except), nor for the aux-looking packages when the repository has no other package at the top level or one level down | flat `sample_app/` with `name = "sample-app"`, and `doc_builder/` with no packaging file: aux, no code indexed outside docs → package; `docs/` still documentation |
| 14 | the call map's whole-suite run uses `--continue-on-collection-errors` and no `--maxfail` (`PYTEST_WHOLE`) | one test file with an import error: map empty → `pkg/core.py::f` mapped to its test |
| 15 | `_test_functions` takes the classes whose bases end in `TestCase`, also through a base class of the same file, whatever their name | `class AddChecks(BaseCase)`: no test, no example → `AddChecks::test_add`, shown as the example test |
| 16 | `_library_homes` reads the public names of the 8 most imported libraries once per task in a subprocess with a time limit (`_library_names`, cached in `_state`); `_lib_base` and the inherited-method list run in a subprocess too (cached per base); `step._library_def` finds a submodule's file in the package's directories without importing it (`_module_source`; the top-level module still through the import system's finders, which import nothing) | a library whose import sleeps 15 s, 3 s left: 15.0 s → 2.9 s (the probe is cut, the name is not shown) |
| 17 | the `inline_snapshot` special case is gone. Generic rule: a stand-in object called with one plain argument returns it (so `snapshot(value)`, `Is(value)` compare the value itself), and a stand-in that compares equal to something marks the test (`sys._swe_stub_eq`); the plugin records such a pass as `skipped`, which no check counts as a pass | `== stub.snapshot()` and `== stub.snapshot(3)` with value 2: passed, passed → skipped, failed |
| 18 | `_run_groups` records the tracked files that differ from the commit before a run and, after it, checks out the ones the run changed and rewrites the ones it changed further | a test that rewrites `tests/golden.txt`: left changed (in the patch) → restored |
| 19 | `_example._packages` derives the names from the files: packages (namespace ones too) and top-level modules; a module at the repository root beside a package counts only when the packaging files declare it (not `setup.py`, `pdm_build.py`; build-tool names are never packages) | `mymod.py` and `src/mymod.py` projects: no package, the example not found → `mymod`, found |

Not done: a cache across processes. Measured on the dev repositories, loading a pickled index or table takes 0.35-0.61 s
against 0.57-1.08 s to build it (the AST nodes dominate, 2.7-7.5 MB), so a disk cache would save little and add a
staleness risk; the per-process memo removes the repeated builds, which were most of the cost.

Risks: the namespace climb trusts the repository's own import lines (a test that imports `src.acme.billing` keeps
`src/acme` out of it); a test that relies on rewriting a tracked file and reading it back in a later run sees it
restored; a vendored directory the issue is about is no longer found; a stand-in that is called with one argument and
used as a decorator now returns the argument.

| Checklist item | Effect | Why |
|---|---|---|
| W3 (reasoning about code in front of it) | closer | On other layouts the code shown is the repository's: its own package instead of an installed copy (#8), subpackages named `build`/`env` (#2), modules of namespace packages by their import names (#4), files with a coding cookie (#6) |
| W6 (the names of code and statement) | closer | A method named `update`, `get` or `add` in backticks or a dotted name is now a search term (#9) |
| P7 (relevant-looking but unneeded context) | closer | Vendored copies and generated protobuf modules no longer fill the candidates and the related places (#12) |
| F2, F3, B11 (repairing from test output; a false OK; trusting verdicts) | closer | A test that passes only through a stand-in is not a pass (#17); the original code can be imported (#5) and runs without the memory limit when it needs to (#11), so new failures are compared, not "unknown"; tests no longer leave their rewritten files in the patch (#18) |
| B10 (knowing its budget) | closer | Test runs, import probes and library probes end by the task's deadline in every entry point (#10, #16): a call no longer runs past the time the answers show |
| B1-B9, B12-B16, W1, W2, W4, W5, W7, P1-P6, P8-P10, F1, F4, F5 | neutral | No answer form, NEXT line or instruction changed. The texts the model sees that can differ: the candidate lists (#9, #12), module names and "The repository is the package ..." (#4, #13, #19), library views and "also in the library" (#8, #16), two new edit refusals ("the file is nested too deeply for Python to parse it"; "the file is written in <encoding> (its coding line), which cannot hold the character ...", #6, #7), test verdicts (#11, #17, #18), and the example's "(stopped after N s)" with the capped time (#10). On the dev repositories the candidates are the same except where a statement puts a stop word in code (#9) |

Why it is generic: every rule reads the repository's own files (`__init__.py` chains, its import lines, its packaging
files, its coding cookies, its `.gitignore` through git) or follows Python and pytest conventions (PEP 263, PEP 420,
`sys.stdlib_module_names`, unittest's `TestCase`, pytest's collection options, the conventional names of vendored and
generated code, the build tools' own module names); none names a repository, a library or a file seen in a trace.

## Decisions left to the model (audit, 10-20)

This is the inventory of the points where the model's answer decides the outcome and the script accepts it without checking it. Line numbers refer to `_stages.py` at head; vf5 behaves the same. Counts are over vf5's 30 Kaggle traces. The user's rule for these points is that a decision that matters is forced by the script (refused, defaulted or taken over), not persuaded by prompt text.

| # | Free decision | Script check today | vf5 evidence | How the script can force it |
|---|---|---|---|---|
| 1 | Specifier "no change" | One warning, then accepted (2002-2011). The warning counts an unspecified place (None) as a change, so with parallel specifiers it rarely fires. | 45 sent, 26 accepted, in 17 tasks. Accepted at a place the reference changes in fastapi_13207, rich_3480, requests_7505 and fastapi_14419. | Refuse it on the main place, or when no other place has a sentence. Default: the requirement text as the sentence. |
| 2 | Leaving a claimed place without finishing it | The claim never expires; repair skips the place. | rich_4075: the reference place was opened at 219 s and never sent, and the patch was empty. requests_7505: a place stayed "to specify". | Release the claims of earlier stages; repair takes every unspecified or unedited place. |
| 3 | Review "done" | Accepted. The refusal for broken existing tests is off in fast (`and not FAST`, 2816). | 15 "done" and 0 "not done". 4 wrong patches were accepted with 63-152 s left. In 2 of them (fastapi_14953, rich_3718) the view showed failing tests. | The script turns a BROKEN verdict into "not done" for the owning place, then repair. |
| 4 | Editor or repair "no change" / "skip" | Accepted unconditionally (2206). | It overrode the script's own empty-patch retry in fastapi_13207 and rich_3772. | Refuse it when the place has a sentence and the patch is empty. |
| 5 | When a stage ends | No per-stage deadline, only BUDGET_S. A ParallelAgent waits for its slowest worker. | rich_4076: requirements ran until the cut. fastapi_14419: requirements took 123 s. rich_3480: edit started at 262 s. 9 of 30 tasks were cut; 8 of them have an empty patch. | Script deadlines per stage with the existing fallbacks (R1, `_fallback_locate`, the requirement-text sentence). After a deadline, every call to that stage is answered "time is up". |
| 6 | A sentence sent after "no change" | Refused as "your part is finished". | rich_3480: the right sentence for `Text.append_text` was refused at 205 s. | Accept it until that place's edit starts. |
| 7 | A single requirement copied from the title | Accepted when it is the only one (591). | 6 tasks (2 resolved). | Refuse it once. |
| 8 | "missing" chains | A place already "no change" can be named again (1928). | rich_3180 and fastapi_14419 lost time this way. | One "missing" per place. |

Caveat: in fastapi_15661, rich_3934 and rich_3772 the located place was wrong. Forcing a change there gives a non-empty patch that is probably still wrong.

### The forcing fixes (10-20, the user's: "a decision that matters is forced, not asked for")

All of these are in `_stages.py`. They apply only to the fast build.

1. **Specifier "no change".** A "no change" is replaced by a sentence the script writes when two things hold:
   - no other place carries a change (a sentence or an applied edit);
   - the place is the main place (the first one located), or no other place is still unspecified.

   The script's sentence is what a specifier said must change there, or else what the place's requirements say must happen (`_default_sentence`). The specifier is told so and is not asked again. vf5's one-time warning is removed: it fired 7 times and was answered "no change" 7 times.
2. **Claims of earlier stages are released** at every claim (`_settle`), because those workers have ended.
   - A requirement nobody located gets the top candidate.
   - At the edit stage, a place nobody specified gets the default sentence (`_unspecified_default`).
   - A place left "to edit" is taken by repair.
3. **Review "done" with broken existing tests.** When the script's own test run says BROKEN, the review's "done" becomes "not done".
   - The edited places of that requirement go to repair with the failures.
   - Repair's editor keeps an edit whose failing test checks what the issue changes.
   - This applies while review rounds and time are left.
4. **The editor's "no change" with an empty patch.** When the place has a sentence and the patch is empty, the editor's "no change" or "skip" is refused once, then the place is marked failed. Repair's empty-patch retry then takes it.
5. **Stage ends (`STAGE_END`)**: requirements at 75 s, locate at 110 s, specify at 180 s, edit at 235 s.
   - After its end, a worker finishes the item it holds and takes no other.
   - "missing" is not added.
   - Requirements are not replaced.
   - What is left gets the defaults of item 2.
6. **Reversal of a "no change".** A specifier whose last place ended as "no change" can still send a sentence or "missing" for it until the edit stage starts. The call is applied, not refused as "your part is finished".
7. **Copied requirements.** Requirements whose "what must happen" all repeat the issue's sentence are refused once.
8. **One "missing" per place.** A second "missing" from the same place is refused, and so is a "missing" sent after the stage's end.
9. **No duplicate places.** A member of a split class that is already in the plan is not added twice (rich_3772).

What the script cannot force:
- A call the model writes inside its thinking (rich_4075). Item 2 only lets repair take the place afterwards.
- A malformed call that the ADK rejects before our script runs (rich_4076: the same call 98 times).

Checklist (`docs/llm_checklist.md`):

| Item | Effect | Why |
|---|---|---|
| B9 deciding when to stop | closer | The script decides when a stage ends and whether an empty patch is acceptable. The model no longer decides either. |
| B10 budget | closer | Stage ends are enforced by the script, not left to a model that cannot see the time. |
| B11 trusting output | closer | "done" against the script's own failing tests is overridden by the test result. |
| F3 doubting a verdict | closer | The test verdict replaces the model's verdict where the two disagree. |
| F4 resisting a challenge | closer | The "no change" warning (a challenge, answered the same way 7 of 7 times) is gone. The script states what it did instead. |
| F9 facts over advice | neutral | The answers state what the script did (the sentence it saved, the failures), not advice. |
| B12 contradictions | closer | "Your part is finished" no longer refuses an answer that the next stage would need. |
| B5/F7 repeated calls | closer | The second "missing" is refused with its reason, and the chain ends. |
| W3 reasoning on code in front of it | farther, with a reason | A place the model judged correct can now be edited from the requirement text. The reason: in vf5 that judgement was wrong at the reference's places (fastapi_13207, rich_3480, requests_7505, fastapi_14419), and an empty patch never scores. |

Why it is generic: every rule reads only the procedure's own state:
- the plan, its sentences and statuses;
- whether the diff is empty;
- the script's test verdict, compared against the original code;
- the elapsed time.

None of the rules names a repository, a library or a task. The traces are only the evidence.

### First run of the forcing fixes: Haiku, vf5's 30 tasks (h12, 10-20)

This run used Haiku through `tools/anthropic_proxy.py`, at the user's request. It tests the system; it is not evidence for Gemma. Result: 12/30 judged resolved, no empty patch, no internal error, no task cut at 5 minutes, median 85 s.

How often each rule fired, in tasks:

| Rule | Tasks |
|---|---|
| Forced sentence | 10 |
| Editor "no change" refused | 5 |
| Review "done" overridden by the test run | 4 |
| Copied requirement refused | 1 |
| Stage end | 0 (Haiku is fast) |

- **Formerly empty patches.** Two of vf5's empty-patch tasks were resolved: requests_7505 and fastapi_14459. The other 7 now carry a patch.
  - In 4 of those, the patch is in the reference's file and function: fastapi_13207, fastapi_14419, rich_3180, rich_3480.
  - In 3, it is in another file, because locate chose wrong: fastapi_15661, rich_4075, rich_4076.
- **Review overridden by the test run** (fastapi_14953, rich_3043, rich_3718, fastapi_14419). In fastapi_14953 and rich_3043 the failing tests were snapshots of the value the issue changes. Repair kept the edit, after 15-20 s of trying to name the test files with "missing". Nothing was lost.
- **What is left: the content of the change.** Of the 18 tasks not resolved:
  - 14 changed the reference's file, with a fix that is incomplete or different. In fastapi_13207 the change landed in `get_schema_from_model_field` and was missing in `get_definitions`. In rich_3180 the change was made in `_wrap.py` and was missing in `cells.py`.
  - 4 changed another file.

  The bottleneck moved from "no change" to what the change is, and to finding the second place.
- **Faults found and fixed** (5d04efb):
  - An edit refusal offered "skip" where "skip" is refused.
  - The recap said "its edits were undone" for a place where no edit was sent.
  - A requirement id was repeated ("R1, R1").

#### h12: the stage where each unresolved task first went wrong

The judgement compares each task's journal with the reference patch. The trace of each task is in the scratchpad (`h12_b*/results/traces`), and the per-task dossier is `h12dossier.txt`.

| Task | Stage | What happened |
|---|---|---|
| fastapi_15280 | statement | The title only ("Add support for `@app.vibe()`"). The reference is an LLM-prompt route that the statement does not describe. |
| rich_3942 | statement | The title only ("Update to markdown styles"). Nothing says which styles. |
| fastapi_14953 | locate | Only `UploadFile.__get_pydantic_json_schema__` was chosen. The reference changes the bytes schema in `fastapi/_compat/v2.py`. |
| fastapi_15661 | locate | The reference creates a new file (`scripts/prepare_release.py`); locate chose the existing `main` of another script. |
| fastapi_9753 | locate | R1 was never located. R2 went to `get_typed_signature`; the reference changes `Dependant` in `dependencies/models.py`. |
| rich_3180 | locate | `divide_line` was chosen. The reference changes `words` in `_wrap.py` and `set_cell_size` in `cells.py`. |
| rich_3480 | locate | `Renderables.append` and `Lines.append` were chosen. The editor's "missing" reached `Text.append` (the reference's place), but `append_text` was not changed. |
| rich_3772 | locate | `_render_stack` and `render_locals` were chosen. The reference changes `extract`, `safe_str` and `get_locals`. |
| rich_3934 | locate | `Live.refresh` and `LiveRender.set_renderable` were chosen; the reference changes `Live.__init__`, `stop` and `live_render.py`. The statement is the title only. |
| rich_4075 | locate | The module-level `rich.print` was chosen (twice) instead of `Console.print` in `console.py`. |
| rich_4076 | locate | `Syntax.highlight` and `Text.split` were chosen; the reference changes `ansi.py` `decode`. The statement is the title only. |
| fastapi_13207 | specify | The right places. `get_definitions` was kept as "no change", and the sentence for `get_schema_from_model_field` was wrong ("remove the override_mode logic"). |
| fastapi_14258 | specify | The right place and the right check. The sentence raised `FastAPIError`; the reference uses `assert`, as the lines next to it do. |
| fastapi_14419 | specify | The right places (`get_dependant`, `solve_dependencies`). The sentence invented a `uses_security_scopes` attribute; the reference's change is different. |
| rich_3043 | specify | The right place (`CONSOLE_HTML_FORMAT`). The fix comes from issue #3021, which is not in the statement. |
| rich_3718 | specify | The right places. Both changed, and an existing test broke. The reference "reverts the change in 7a38204", which the statement does not show. |
| fastapi_14372 | specify (script fault) | The specifier's `['missing', 'Depends (fastapi/params.py:765) class Depends', …]` and the editor's "class Depends in fastapi/params.py" were matched to the planned function `Depends` (param_functions.py). The `params.Depends` class the reference changes was never planned. |
| fastapi_13713 | edit | The editor of `FastAPI.__init__` (a signature of about 750 lines with `**extra`) failed 6 times. Each time it was told "arguments cannot follow var-keyword" or "old lines not found". |

**Totals:**

| Stage | Tasks |
|---|---|
| locate | 9 |
| specify | 5 (+1 script fault) |
| statement without detail | 2 |
| edit | 1 |

Review on these 18 tasks:

- It accepted a wrong patch ("done" or "cannot tell") in 10.
- It said "done" in 4 more, and the script's test run overrode it.
- It did not run in 3, because the patch was still empty when review started.
- It said "not done" in 1 (fastapi_13713).

### Fixes from h12's stage table (10-21)

These address the faults that the script can remove deterministically. Each is generic: it reads only code structure, the file the model names, or Python's grammar, never a repository or a task.

1. **"missing" keeps the file it names** (`_missing_names`). A `.py` path written with a name, as in "class Depends in fastapi/params.py" or "Depends (fastapi/params.py:765)", now makes `path::Name`. That name matches a planned place only in that file and resolves there. Before, only `Depends` was kept, and it matched the planned function `Depends` of another file (fastapi_14372).

   | Item | Effect | Why |
   |---|---|---|
   | W6 names and words | closer | All the model wrote is read, not only the last word |
   | B12 contradictions | closer | The model named params.py; the script changed another file |
   | B5 repeated calls | closer | The model asked twice because it never got what it named |

2. **Parameters written after `**kwargs` are moved before it** (`_edit._params_before_var_kw`). This repair applies only when the file does not compile with "arguments cannot follow var-keyword argument" and the misplaced lines are inside the replaced range. In fastapi_13713, a parameter added at the end of `FastAPI.__init__` was refused 6 times.

   | Item | Effect | Why |
   |---|---|---|
   | W2 writing code | closer | The model writes the parameter; the script places it where Python allows it |
   | F1/F7 locating the error, leaving loops | closer | No repeated refusal with the same cause |
   | B8 number of forms | neutral | No new form |

3. **Locate: one step down** (`_with_callees`, `_forwards_to`). After the candidates and their callers, the list gets up to 3 more entries:
   - the method a wrapper hands its call on to under its own name (`rich.print` → `Console.print`), when at most 3 classes define that method;
   - the functions the first 3 candidates call that share words with the requirement.

   Each entry says why it is there (`[the call of X]`, `[called by X]`). It mirrors `_with_callers`, one step up, and was measured on the dev tasks before it was kept (`bench_callees.py` in the scratchpad: the statement as the requirement, `step._rank_candidates` plus `_with_callers`, against the reference's places).

   Results on 128 tasks with gold places:
   - a gold place on the list: 92 → 93 tasks;
   - more gold places on the list in 7 tasks: fastapi_14986, rich_4070, httpx_3672, fastapi_15800, fastapi_15745, fastapi_14616 and rich_3782;
   - none lost: the list only grows;
   - mean list length: 9.7 → 10.8.

   On rich_4075's chosen `print`, the list now holds `Console.print`, the reference's place.

   | Item | Effect | Why |
   |---|---|---|
   | W1 choosing from a short list | closer | The right option reaches the list |
   | P7 unneeded context | farther, with a reason | Up to 3 more lines, each with the link that put it there; in h12 the reference's place was missing from the list in 9 of 18 unresolved tasks |
   | B9 exploring | closer | Fewer searches by hand (rich_4075 searched 4 times) |
   | B13 order | neutral | The new entries come after the ranked ones, marked |

### h13: the three fixes with Haiku (10-21)

Run: vf13 (`build_f19`) on vf5's 30 tasks, with Haiku. Result: 15/30 judged resolved (h12: 12/30), with no task lost.

**Gained over h12:**
- **fastapi_14372.** The "missing" file fix put the class `params.Depends` into the plan. That class is the reference's place.
- **rich_3718 and rich_3772.**

**Two empty patches** (fastapi_15661, rich_3942). Both came from the same fault of the forcing. The editor's refused "no change" was counted on the place, so repair's first "no change" was not refused, and the place was marked failed.

Fixed in db4d48f: the count is now kept per stage. Also fixed there: a "missing" that names its own place is answered as such, not as "goes into the plan".

Rerun of the two tasks with the fix (h13r, `build_f20`): both end with a patch, and neither is resolved. Both statements are title only.

### h13, step by step: where each unresolved task went wrong, and the unnecessary calls (10-21)

**Script faults found in h13, all fixed:**
- The editor's refused "no change" was counted per place (fastapi_15661, rich_3942); fixed in db4d48f.
- A "missing" that named its own place was answered "goes into the plan"; fixed in db4d48f.
- The `**kwargs` repair handled only a one-line `**extra`. FastAPI's `**extra: Annotated[...]` spans 8 lines, so fastapi_13713 was refused again. The repair now moves the lines the edit inserted to just before the `**` line. Replayed on h13's call #15 of fastapi_13713, the edit is kept.

**The stage of each unresolved task (15):**

| Stage | Tasks |
|---|---|
| locate | 7: fastapi_14953, fastapi_15661 (a new file), fastapi_9753, rich_3180, rich_3480, rich_3934, rich_4076 |
| specify | 5: fastapi_13207, fastapi_14258, fastapi_14419, rich_3043, rich_4075 (the right place was reached through "missing"; Console.print was then given "no change") |
| statement, title only | 2: fastapi_15280, rich_3942 |
| edit (a script fault) | 1: fastapi_13713 |

**Every model turn of the 30 traces** was classified by `waste.py` (scratchpad): 885 turns, about 30 per task.

| Kind | Turns | Per task | Necessary? |
|---|---|---|---|
| Useful calls | 436 | 14.5 | yes |
| "done" replies that end an agent | 294 | 10 | Yes by design: each of the 10 agents ends with a reply without a call |
| Idle workers: an "open" answered "nothing left for you" | 98 | 3.3 | **No.** Each costs 2 turns (open and done), about 196 turns or 22%. By stage: locate 32 (locate2 has no work when there is one requirement), specify 31, repair 22, edit 9, review 4 |
| Refused calls | 25 | 0.8 | **No.** By stage: edit 19, repair 4, specify 2. 7 were refused "no change" (the forcing, by design), 5 sent the same code as before, 6 had old lines not found or did not compile, 4 sent "missing" without a code name |
| Text without a call in the middle of work | 32 | 1.1 | **No.** Editors stopping with an explanation ("the signature lines are hidden", "I can't write…"); their place goes to repair |
| The finish agent's sentence after `submit_patch` | 30 | 1 | yes (it ends the agent) |
| The requirements name lookup | 28 | 0.9 | By design (step 1 of req) |
| Searches in locate | 5 | 0.2 | yes (looking off the list) |
| The same call sent again | 2 | — | **No** (6 more are review "done" for R1 then R2, which is not a repeat) |

The biggest waste is the idle workers: the stages have a fixed number of workers whatever the number of items. In these 30 tasks, locate2 had nothing to do in 32 of 60 worker runs (one requirement), and specify in 31 of 90.

#### h13: the 25 refused calls, one by one

Each call was checked against the script's own journal (`events.jsonl`), not the ADK trace. The trace pairs answers with the wrong worker when workers run in parallel.

| Cause | Calls | Tasks | Whose fault |
|---|---|---|---|
| **The class-place edit put a module-level helper inside the class.** The editor of the place `SupportsRead` (a class) sent the class and, after it at column 0, `def is_supports_read(...)`. `step._apply_definitions` adds a new function sent with a class place as a member (`inner = own.kind == 'class'`), even when the class itself was sent beside it. The import `from ._types import is_supports_read` then failed, and the edits that used it were undone. Each later send of the module-level function was re-indented into the class and answered "the same as the old one". | 4 (plus 3 undone edits) | requests_7505 | **script** |
| The forcing refused "no change" with an empty patch | 7 | fastapi_15661, rich_3942, rich_3772, rich_4076, fastapi_14419, rich_3480, rich_3934 | By design. In 5 of them the place's sentence is the script's default, the requirement text, which for a title-only issue says nothing concrete ("Live handles an empty live display as the issue describes: empty live") |
| "missing" used to explain "I cannot write this" | 4 | fastapi_15661, rich_3942, rich_3772, rich_3934 | **Design.** All came after the refusal above, on a default sentence. The editor has no form to say that the sentence names no change, so it repurposes "missing" (B16) |
| The old code sent back unchanged, a "no change" in disguise | 2 | rich_3480, rich_3934 | **Design.** The same cause, after the refusal |
| Replace with old lines that the window does not show | 2 | fastapi_13713 | **script.** For a long place the view hides the 750-line signature ("lines 65-820 not shown"). The sentence asks for a parameter beside `openapi_tags`, so the editor guessed lines it never saw (`openapi_tags: Optional[...]`; the file has `Annotated[...]`) |
| Replace with part of a line | 1 | rich_3278 | **script.** The editor sent a fragment of line 13 of the regex, `(?:\x1b([(@-Z\\-_]|`; only whole lines are matched |
| A sentence sent where code was expected | 2 | rich_3772 (repair), requests_7505 | Partly script. Repair both specifies and edits, and its edit view was answered with a sentence |
| `**kwargs` repair missing | 2 | fastapi_13713 | **script**, fixed in 639085a |
| "missing" naming a new helper (`_is_supports_read`) that does not exist yet | 1 | requests_7505 | **script.** "missing" accepts existing code or `Class.new_member`, but not a new module-level function `path.py::new_name` |

**Script faults to fix** (all generic):
1. A function sent together with its class stays at module level. Only a function sent alone with a class place is a member.
2. The long-place view shows the lines that hold the names in the sentence (`openapi_tags`, `self.openapi_tags`), instead of hiding them.
3. Replace accepts old text that is a unique part of a line.
4. "missing" accepts `path.py::new_name` for a new module-level function.
5. In repair, a sentence sent to an edit view becomes the place's new sentence, and the edit view follows.

**Not a script fault:** 13 of the 25 calls are the model unable to write a change from a sentence that names none (title-only issues). The forcing turns those into an attempt and a repair. It cannot create the missing information.

### Fixes from h13's refusals, the journal, and one worker for locate and edit (10-21)

1. **A function sent with its class stays at module level** (`step._whole_edit`). A def sent beside the class of a class place is a sibling. Only a def sent alone with a class place is a member. Checked on requests_7505's snapshot: `is_supports_read` lands after `SupportsRead`, at column 0.
2. **Long places show what the sentence names** (`step._code_view(names=)`, `_sentence_names`).
   - In a long signature, the parameters whose names the sentence gives come first, and the `**kwargs` parameter is shown whole with the lines after it.
   - In the body, lines that hold those names score high.
   - FastAPI.__init__ now shows `openapi_tags: Annotated[`, the 8 lines of `**extra`, and `self.openapi_tags = openapi_tags`.
3. **Replace accepts a part of one line** (`_replace_text`), when that text occurs once in the place.
4. **"missing" for a new function names its file.** A bare new name (`_is_supports_read`) whose file is written in what must change becomes `path.py::name`, new code of that file.
5. **Repair: a sentence sent to its edit view becomes the place's sentence**, and the edit view follows (`_prose`).

**The journal.** `_state.record` keeps every call's args and its whole answer, as printed. `tools/journals.py` finds a task's journal by its first calls. `local_eval.py` and `kaggle_eval.py` copy it to `journal_<task>.jsonl`. The Kaggle dataset's `bench/` must hold `journals.py`.

**One worker for locate and edit** (`tools/make_fast.py`, `WORKERS`). Specify keeps three. One editor chains through every place (`BATCH['edit'] = 6`, at most 12 places).

| Item | Effect | Why |
|---|---|---|
| W2 writing code | closer | Fixes 1 and 3: what the model wrote is applied where it meant it |
| W3 code in front of it | closer | Fix 2: the lines the change needs are in the window |
| B2 copying long text | closer | Fix 3: a part of a line is enough |
| B12 contradictions | closer | Fixes 4 and 5: a new function's file and repair's two roles no longer contradict the answer |
| B16 repurposing a form | neutral | "missing" is still the only way to say "the sentence names no change"; no new form |
| B10 budget | farther, with a reason | One worker in locate and edit costs about 10 s with Gemma (vf5: +8 s and +2 s). It removes 41 of h13's 98 idle workers and the lost answers in those stages |
| P10 uniform observations | neutral | — |

**Why it is generic:** the rules read Python syntax (a sibling def, the `**kwargs` parameter, a line part), the names the sentence itself writes, and the file the model names. None of them names a repository or a task.

### h14: the refusal fixes, one worker for locate and edit, the journal (10-21)

Run: vf14 (`build_f23`) on vf5's 30 tasks, with Haiku. Result: 13/30 judged resolved (h13: 15/30). This analysis reads the journals (`journal_<task>.jsonl`), not the traces.

- **Gained:** fastapi_14419 and rich_3043.
- **Lost:**
  - fastapi_14372, rich_3718 and fastapi_14459. The places and the plan were right, but the change the model wrote differed from h13's. For example, in fastapi_14372 `params.Depends` was planned (the "missing" file fix worked) and given `@dataclass(unsafe_hash=True)`, which the hidden tests reject.
  - requests_7505. Two script faults cost it:
    1. Review's run of the existing tests took 180 s, and 117 s in requests_7205. Repair then started with 36 s left. **Fix:** the run is limited to `REG_LIMIT_S = 60`.
    2. Repair sent the implementation of `_encode_params` alone. The edit replaced the first `@overload` stub's symbol, and the place's four stubs were deleted. **Fix:** `_target` takes the last definition of a name (the implementation). Without `@overload` in the new code, only the implementation and its own decorators are replaced. Checked with repair's real call on the snapshot: the stubs stay.
  - Rerun of the two requests tasks with both fixes (h14r, `build_f24`): both resolved, in 104 and 108 s.

**Calls, from the journals** (h13 counts were from traces, which miss 20% of the answers):

| Kind | h13 | h14 |
|---|---|---|
| Idle workers | 98 | 60 (specify 35, repair 22, review 3; locate and edit 0) |
| Refused calls | 25 | 14 |

The 14 refused calls in h14:

| Cause | Calls |
|---|---|
| The forcing's "no change" refusals, by design | 6 |
| "missing" naming test files to update (repair, after the test run's "not done"), which are not edited | 2 |
| "missing" used to say "I cannot" | 1 |
| The same code sent back by repair to keep an edit whose failing test checks what the issue changes | 2 |
| Old lines not found | 1 |
| Not a definition (a placeholder line, a setup.py shim) | 2 |

**Open:** in repair, a definition sent back unchanged could be read as "keeps its code". No rule for it yet.

### Fewer model turns (10-21, after h14: 26 turns per task, median)

1. **Requirements in one call.** The prompt asks for the pairs at once. The answer that saves them shows what the repository defines for the names in them (`_name_lines`), so the agent can send corrected pairs. The separate names lookup still works.
2. **One agent, `check`, reviews, repairs and submits.** It replaces review, repair and finish. `main_fast` gives the check worker the open review item first, then repair's places (`claim`), and ends with "call the tool submit_patch" (`CHECK_DONE`). The agent has the tool `submit_patch` beside the skill.
3. **One review call for every open requirement.** The claim target is `ALL`, and the verdict is stored for each requirement (`store`).
4. **Repair: code sent back unchanged keeps the place as it is**, instead of a refusal.
5. **Two specifiers** (h14: the third was idle in 23 of 30 tasks).

**Where the tests run** (checked at the user's request, from h14's journals and the code):
- In h14 only the review ran the existing tests, in 27 of 30 tasks. The other 3 had an empty patch at review.
- Per-edit tests are off (`EDIT_TESTS`). All 36 edits were answered "Kept, not checked".
- Reproduce and `VERIFY` are off in the fast build.
- So repair's edits, which come after the review, were never checked. requests_7505 lost its `@overload` stubs there.
- **Now:** repair's edits run the existing tests of the changed code. The run is limited to `REG_LIMIT_S`, and an edit that breaks a test passing just before it is undone (`_after_edit`).

| Item | Effect | Why |
|---|---|---|
| B8 number of forms | **farther, with a reason** | `check` has review's 3 answers plus repair's 4. The agents removed cost 2 turns each in 22 of 30 tasks (repair) and 2 in every task (finish). The script's NEXT names which form it expects at each step, as repair already did |
| P8 progress recited | closer | One agent sees its review and the repair it causes in one context |
| B10 budget | closer | About 5 fewer model turns per task |
| F3 doubting a verdict | closer | Repair's edits are checked by the tests, not trusted |
| W1 choosing | neutral | The same verdicts |
| P7 unneeded context | farther, with a reason | The review view holds every open requirement; with 1-2 requirements per task (h14: 1.4 calls) the view grows little |

**Why it is generic:** the changes are in the procedure's own agents, forms and test runs. None of them names a repository or an issue.

### No test runs in the fast build (10-21, the user's: time has been the main problem)

The fast build runs no tests, and no answer depends on a test run. Every test runner in the stages build is now gated by `FAST`:
- `_regression_check` (the review's check of the existing tests) returns nothing.
- `_rerun_repro` and `_verify` return at once.
- `_after_edit` runs tests only when `EDIT_TESTS` is set and the build is not the fast one. The repair-edit tests of 10-21 are removed.
- The rule that overrode the review's "done" with the script's BROKEN verdict (forcing fix 3 of 10-20) is removed, because it depended on that run.
- The check agent's prompt no longer promises test output.

The reproduce stage was already off (`REPRODUCE = False` in `tools/make_fast.py`).

**Checked on two tasks** (requests_7505 and rich_3180, every call from requirements to check). A `sitecustomize` spy logged every subprocess the scripts start. There was no pytest. What runs is:
- git (status, diff, show);
- the library-name probe, once per task.

These are not tests. The import probe of a changed module (`python -c`, one per edit, a few seconds) was removed as well, at the user's request (10-21). An edit is still checked, without running it, for names the file neither defines nor imports (`_undefined`, read from the code). On requests_7505's whole replay the spy saw one `python -c` in the task: the library probe.

| Item | Effect | Why |
|---|---|---|
| B10 budget | closer | h14: the review's test run took 117-180 s in 2 of 30 tasks, and 60 s per run with the limit |
| F3 doubting a verdict | farther, with a reason | No test verdict checks the model's "done". Time is the binding limit (the user's), and in h14 the test verdict decided only 4 tasks, without a gain |
| B11 trusting output | neutral | The answers say "Kept, not checked: the existing tests are not run", which is true |

**Why it is generic:** no repository's tests run, so no repository's test speed matters.

### Each agent's context, compaction and feedback (10-21, at the user's request)

**What an agent sees** (`include_contents: none`, ADK `contents._get_current_turn_contents`):
- its instruction: the stage's prompt, with the issue in `<issue>` tags. It is a system instruction, so it is never compacted;
- the event that started its turn: for the first agent, the harness's task message; for the others, the previous agent's last reply, "done";
- its own calls and answers.

**Compaction.** It is official and session-wide (HARNESS_README 7.2: `compaction_interval 5`, `token_threshold 14,336`, `event_retention_size 5`).
- When a model response reports a prompt of 14,336 tokens or more, ADK summarizes the older events with an extra model call. It keeps the last 5 events and puts the summary in place of the rest.
- An agent then keeps its instruction (with the issue), its last two calls and answers, and the summary.
- Every script answer restates what the agent needs: the place, its sentence, the requirements and the exact NEXT call. So the task is still in front of the agent after a compaction. What compaction costs is time: one more model call on a long prompt.
- With Gemma (vf5 and vf10, from `stages_*.json`), 4 of 593 agent runs crossed 14,336 tokens (requirements 1, specify 3). The medians per agent were 1,874-5,362 tokens.
- One editor now goes through every place, so its prompt grows by one window per place. **Fix:** a worker whose answers in this task already add up to `CHAIN_CHARS` (32,000 characters, about 9,000 tokens) takes no further item. What is left goes to the next agent (check repairs it).
- The eval tools used `compaction_interval 15`. **Fix:** 5, as the official harness. The interval counts invocations, and a task is one invocation (more only after a nudge), so it rarely applies.

**What feedback an agent gets on every call:**
- the progress line: the patch's files, the plan with each place's state, and the time used of 4 min 30 s;
- the window;
- the decision it must make;
- the exact NEXT call.

Refusals state the fact and the way on (F9), without "are you sure" (F4). **Fix:** chained windows ("Your part goes on with X") now carry the progress line too. Before, only the first window had it (P8, B10).

**Prompting practices** (`docs/llm_checklist.md`):

| Item | Status | Note |
|---|---|---|
| P1 quoted data | met | The issue is in `<issue>` tags |
| P3 positive rules with reasons | mostly met | — |
| P4 pressure words | met | No capitals, no MUST or NEVER in any prompt |
| P6 the goal at both ends | met | The issue is in the instruction, and each window ends with the decision and NEXT |
| P9 call forms copied | met | Native Gemma tool-call examples with placeholders |
| B8 number of forms | gap | Instructions are 2,000-3,100 characters. `check` lists 8 call forms |
| B8 rules in one paragraph | gap | The requirements step is one paragraph of about 1,500 characters with several rules |
| W7 thinking | — | requirements, specify, edit and check think up to 1,024 tokens; locate 512; finish 64 |

### The same call three times ends the worker's part (10-21, from SWE-Gym)

SWE-Gym (Pan et al., ICML 2025; `docs/literature.md`) counts an agent as "stuck in a loop" when it repeats the same action three turns in a row, and its trajectories that loop rarely end resolved. Our scripts refuse a wrong call and say the way on, but a worker that sends the same refused call again gets the same refusal each time until its time is gone.

**Fix (`_stages.main_fast`):** the script keeps, per worker, the last call (its args and the item it holds) and how many times in a row it came. The third identical call on the same item is not run: the worker's item is released ("stopped after the same call three times"), and the answer says its part ends there and the next agent goes on with what is left (`FAST_DONE`). Calls that open or move on (`''`, `open`, `start`, `next`) are not counted.

| Item | Effect | Why |
|---|---|---|
| F7 repetition | closer | A repeated call no longer costs turns; the item goes on with the next agent (check repairs it) |
| B5 loops | closer | The loop is cut by the script, not by asking the model to stop |
| F9 feedback states the fact and the way on | neutral | The answer says what happened and what follows |
| P6 the goal at both ends | neutral | The answer ends with the finish line as every other final answer |

Generic: it counts the worker's own identical calls on the same item; nothing depends on the repository, the issue or a name. Checked by replaying the 30 h17 traces with `build_f29` (no problems; no h17 worker repeated a call three times, so the rule did not fire there).

### One specifier, no ParallelAgent (10-22, after S10 failed on Kaggle)

S10 failed on Kaggle with "Your notebook hit an unhandled error while rerunning your code" (the submission's `error_description`). That is an exception escaping the scorer's stage 1, not its time limit.

**Every stage's code was read for a way into the harness's process; none was found.** The modules that changed since S9 are `_stages.py`, `step.py` and `_edit.py`; the other 11 are byte-identical to S9's, which ran.
- The scripts run inside the sandbox, through the ADK's wrapper, in a thread (`asyncio.to_thread`).
- `main_fast` catches every `Exception` and answers "internal error".
- Files the scripts write are filtered by `changed_files` and `worktree_diff`, which skip hidden files. So the ADK's `/workspace/.adk_exec_*.py` runner never enters the patch.
- git is read-only (`show`, `diff`, `status`, `rev-parse`), apart from `checkout -- <file>` to undo an edit.

**The one path found** is in the harness (`swegemma/harness/agent_runner.py`). When the time is up after an event, it `break`s out of `async for ... runner.run_async(...)` and leaves the ADK's generator open. Inside a `ParallelAgent`, the branch tasks keep running. An error there can surface as a `BaseExceptionGroup`, which the harness's `except Exception` does not catch.

S9 had three parallel stages and ran, so this is not proven to be S10's cause. It is the only path found, and removing it costs one specifier. With `WORKERS = {'locate': 1, 'specify': 1, 'edit': 1}`, the build has no `ParallelAgent`.

| Item | Effect | Why |
|---|---|---|
| F7 repetition, B10 time | neutral | The specify stage keeps its end (`STAGE_END`) and the chain limit; one worker takes the places one after another |
| P8 the goal at each step | neutral | The specifier's prompt says the places come one at a time (`ALONE`), as locate's and edit's do |
| B5 no idle agents | closer | No second specifier waiting with nothing to do (h14: idle in many tasks) |

Generic: it removes a harness-level mechanism; nothing depends on a repository or an issue.

### A lazy repository map (10-26, the user's: "only what is needed is loaded, and the time does not grow with the repository's size")

**The fault.** Every script call is a new process (the ADK writes the skill to a new temporary directory and runs it), and `_repo.memo` kept the search index (`_rank.Index`) and the impact table (`_impact.Table`) only for one process. So every call that ranked candidates or looked a name up parsed and indexed every file of the repository again. On our dev repositories (fastapi, rich, requests: 100-1,100 files) that was 1-2 s per call and was never seen. On a large repository it is most of the task: replaying the Haiku journal of moto_5865 (1,500 files) took **130 s of script time** (req 19 s, locate 26 s, specify 27 s), and a django task 96 s, on our machine. The scorer's sandbox (2 vCPU, Docker, possibly gVisor) is slower, and its budget is 5 minutes for the whole task, model time included. The competition's ~120 tasks come from private repositories of unknown size: this is a likely reason for the ~0.06 on the leaderboard against 7-16 of 30 in our own evals (S7-S12).

**The design** (`agent/skills/fix-issue/scripts/_map.py`):
- **git decides which files are read.** `git grep -P` (tracked and untracked files; the ones git does not see are read directly; without git or Perl regexes, every file is read with the same regex) answers "which files can hold this name": as a word, as a definition (`def name`, `class name`), as a call (`name(`), as an attribute (`.name`). The window of a place looks up all its names in one grep (`Table.prefetch`).
- **One file's record, kept on disk.** What the table needs of a file (its functions and classes, base classes, parameters, the calls and attribute reads in each function, an `__init__.py`'s exports) and what the index needs (its BM25 documents) are made from that file alone, in one AST walk, and stored with `marshal` under `SWE_STATE_ROOT/swe_map_<repo>/`, keyed by the file's path and content and by the source of the modules that make them. A later call reuses them; an edited file gets a new record.
- **The same interfaces.** `Table.syms/calls/reads/uses/exports/bases/find/subclasses` and `Index.names/rank/query/score` keep their signatures; `Index.docs` (all documents) became `Index.doc(rel, name)` and `Index.docs_in(rel)`. Callers that read every file (the search for a text, the module that holds an assignment, an import alias, the test that uses a name, the files that import a module, the third-party modules the code imports) read only the files git names. Callers that need the first few callers or users read files in the order they are shown and stop there.
- **Ranking without the whole corpus.** BM25 needs statistics of all documents. They are estimated from git grep counts: a token's document frequency from the lines that hold it in each file (at most that file's functions and classes + 1), the number of documents from the `def`/`class` lines, the average field lengths from a fixed sample of 24 files. Only the 40 files that hold the query's words most (`SELECT`) are indexed and scored.
- **Bytecode reused** (`_boot.py`): the scripts are copied once per task to `SWE_STATE_ROOT/swe_code_<content hash>` and imported from there, so Python compiles their 11,000 lines once per task, not once per call (0.28 s → 0.06 s per call here).

**Validation.**

| Check | Before | After |
|---|---|---|
| Impact table and `Index.names`/`owner_class`: the same answers for ~150 sampled names per repository (find, calls, reads, uses, exports, subclasses, bases, related) | — | **0 differences** in 2,416 (rich), 2,354 (fastapi) and 2,500 (moto) answers |
| `tools/bench_locate.py`, 129 dev tasks: hit@1 / @3 / @5 / @10 | 31% / 55% / 65% / 70% | 34% / 55% / 64% / 71%; the same first rank in 100 tasks, the gold place enters the top 10 in 4 and leaves it in 3 |
| bench_locate seconds per task | ~60 (whole index) | 1.0 |
| Replay of moto_5865's Haiku journal (12 calls, cold state) | 130.5 s | 8.2 s |
| Replay of a django task (2,900 files, 10 calls) | 95.9 s | 11.5 s |
| Replay of the 30 h17 journals (fastapi, rich, requests) | 5-23 s per task | 1.8-7.6 s per task, no traceback and no internal error; answers differ from the first candidate list on (the order of some candidates) |

| Item | Effect | Why |
|---|---|---|
| B10 time | closer | Script time no longer grows with the repository: the model's 5 minutes go to the model. A task on a large repository lost 1.5-2 of its 5 minutes to the scripts before the first decision |
| W1 short list with evidence | neutral | The same candidates in most tasks; the localization benchmark is equal or better |
| W3 code in front of it | neutral | The windows, related places and callers are the same (the table's answers are identical) |
| B11 tool output true | neutral | Rankings use estimated statistics; the impact table, which the views and checks rely on, is exact |

Generic: it changes how the repository is read, for any repository and any issue; nothing depends on a library, a task or a name.

**With Gemma on Kaggle (vf21, 10-26):** 8/30 judged resolved on vf5's 30 tasks (vf20, the build before: 7/30), median 264 s (253 s), 10 tasks cut at 5 min (10), no internal error. These dev repositories are small, so their time is the model's and did not move; the gain is on large repositories, which only SWE-Gym's moto (and the private test set, of unknown size) has here. Submitted as S13.

### Fixes from the vf21 trace audit (10-27, the user's: "haz los otros arreglos que son bugs")

From `docs/trace_audit_vf21.md` (five fresh agents on the vf21 traces). Each fix is a script rule that contradicted its
own prompt or refused a right answer:

| Stage | Bug | Fix |
|---|---|---|
| requirements | each call replaced the saved list and the answer invited resending; after STAGE_END the refusal repeated "sending the pairs again replaces them"; `_copied` dropped the pair that copies the issue (rich_4075 lost the bug); a two-word title copied as a pair was read as a name lookup (rich_4076, rich_3934) | pairs accumulate (the same sentence from the issue replaces its old pair); the prompt asks for all pairs in one call; a frozen stage says the requirements are final; copied pairs are kept, after the concrete ones; a pair whose sentence has 2+ words is a requirement |
| specify | 'no change' on the main place was forced at its first answer, before the other places were specified, and the specifier's part ended (11 tasks, 6 of the 7 empty patches) | forced only when the last open place gets 'no change' and no place carries a change; the change then goes to the main place |
| specify | a specifier could not correct its last saved sentence ("your part is finished": fastapi_9753, fastapi_14459) | one correction of the last sentence until the place is edited |
| specify | 'missing' from code a specifier had named was refused, and it was the reference's fix (fastapi_12942) | one more step down; the chain stops at the second |
| edit | the editor opened the place carrying only the requirement's text first (rich_3043, rich_3772) | places with a specifier's sentence are edited first; a place with only the requirement's text may keep its code while another still has a real sentence |
| edit | `replace` refused `\"`/`\\\\` copies of the window and duplicates the model had numbered (fastapi_9753 empty patch, fastapi_13207, fastapi_5624); a copy with numbers on some lines kept them (rich_3278's corrupted regex) | one level of escaping undone when the lines are not found; the occurrence at the copied line number when there are several (the refusal lists their lines); numbers stripped when most lines carry them |
| edit | an edit putting `pass`/comments where statements were was kept (fastapi_5624); a correction of the last edit was refused | such an edit is refused; the last edit may be corrected once |
| edit | `max_output_tokens` 4096 let one call run 82-110 s and the task end with an empty patch (rich_3772, fastapi_14419) | 2560 (the longest completed edit call was 1,663 tokens) |
| check | its window called a diff joined without its `@@` lines "the numbered code below is the code now" (a false 'not done' on a correct patch, rich_3905) | the diff with its hunk headers, called a git diff |
| all | the clock started at the first script call, 8-36 s into the task: BUDGET_S and STAGE_END came late, "time used" read low | the clock starts 20 s before the first call (`FIRST_CALL_S`) |
| all | `CHAIN_CHARS` counted only the script's answers; three specify sessions were compacted (13-26 s each, outside the trace) | each call also counts its arguments and an estimate of its thought (2,000 characters; none without native thinking) |

Checked: 30 replays of the h17 journals, no traceback and no internal error; requirement accumulation and the two-word
pair checked by hand on moto. Not yet run with the model.

| Item | Effect | Why |
|---|---|---|
| B12 contradictions | closer | the requirements answers and refusals say what the script does; the check window says what it shows |
| B5 refusals | closer | right answers no longer refused (a corrected sentence or edit, a numbered or escaped copy, one more 'missing' step) |
| B10 time | closer | the clock is nearer the harness's; no single call can take a third of the task |
| F1 forcing | neutral | the empty-patch rule still forces a change, now only when nothing else carries one |
| B8 forms | neutral | no new form; the requirements form shows two pairs in one call |

Generic: every fix is about the procedure's own rules and messages; none names a repository, library or task.

### Experiment: native thinking off, a think script (10-27, the user's)

The user's idea: "desactiva el thinking, y crea un script de think y instrucciones para que el modelo use esa tool …
así solucionamos ese problema al tener más control sobre los pensamientos". The audit found the native thoughts in
42-58% of the model's time, cut at their 1,024-token budget mid-sentence (39% of specify's), followed by malformed
calls 8.5% of the time (1.2% otherwise), and copying the window's code.

`tools/make_fast.py --think` writes `variants/think/` (build `--variant stages,think`): the same agents with
`thinking_budget: 0` and `include_thoughts: false` (the harness's `apply_thinking_config_to_model` then sends
`chat_template_kwargs.enable_thinking: false` to vLLM), a step in every prompt (call `think.py` with the reasoning in a
few sentences before a decision that needs thought, naming lines instead of copying code), and `think.py`
(`_stages.main_think`): it changes nothing, keeps the thought in the journal, counts it in the agent's context, and
answers "Noted. Now send the decision it leads to." with the agent's last NEXT line. `_mode.py` sets the hidden-thought
estimate to 0.

| Item | Effect | Why |
|---|---|---|
| W7 think before calls | farther, to measure | thinking is no longer automatic: the model must choose to call think.py; a decision without it has no reasoning |
| B10 time | closer, to measure | a thought is as long as the model writes it, with no budget to run into; the answer asks for few sentences |
| B4 call syntax | closer, to measure | no call follows a thought cut mid-sentence (8.5% malformed after a capped thought) |
| B8 forms | farther | one more call form in every agent |
| P5 visible state | closer | the reasoning is in the trace as calls, so the next turns and the audit read it |

Generic: it changes how every agent reasons, for any task. The only valid test is the model: a Kaggle eval of this
build on vf5's 30 tasks against vf21 (and against the fixes alone).

**vf22 (Gemma on Kaggle, 10-27), with the audit fixes:** 10/30 judged resolved (vf21 8/30), median 83 s per task (264
s), no task cut at 5 min (10), 4 empty patches (7). But the think script was mostly not used to think: of 350 think.py
calls, 252 sent the call form's placeholder itself ('<your reasoning, in a few sentences>') and about 50 more another
placeholder or the script's name; about 40 were real thoughts. So the model decided almost every step without any
reasoning, and the gain in time came from the thinking being off. Malformed calls rose (36 against 16, mostly the
skill name written 「fix-issue」). vf23 (the fixes with the native thinking) tells which change gave the result.

**vf23 (the audit fixes alone, with the native thinking, 10-27):** 8/30 judged resolved (vf21 8/30), median 237 s,
9 cut at 5 min, 3 empty patches (vf21 7), 8 malformed calls (15). The fixes removed empty patches and malformed calls
but not unresolved tasks; vf22's gain (10/30, median 83 s, no cut) came from turning the native thinking off.

**Think, second version (10-27, the user's: "haz los ajustes al think").** The call form with its placeholder is gone
from every prompt; the step describes the call in words (skill_name, file_path think.py, one item: your own reasoning
in a few sentences). think.py answers an item that holds no reasoning (a placeholder in angle brackets, the form's own
words, a script name, fewer than 4 words) with "Nothing was thought: that is a placeholder…", once, and after that only
"Send your decision now", so a refusal cannot loop (B5). Checklist: W7 closer (the thought must be written), B5 neutral
(one explanation, then the decision), B8 closer (one form less).

**vf24b (think v2, 10-27):** 11/30 judged resolved (vf22 10/30), median 96 s, 2 cut, 5 empty patches. The placeholder
is gone, but think.py is still mostly called without reasoning: 247 of 312 calls put the call's own values in args
('think.py' 138 times, 'fix-issue' 88); 65 real thoughts (median 537 characters). vLLM hung in rich_4075 in both vf24
runs, inside its gemma4 tool parser, on the model's answer to the specify window of `Node`: a reproducible trigger in
the thinking-off build, which the scorer's vLLM would meet too.

**Think, third version (10-27, the user's: "arregla que think.py reciba el razonamiento en args").** vf24b's
traces show where the thought went: the good ones came as ['think.py', '<reasoning>'] and the empty ones as ['think.py']
or ['fix-issue'] (args read as a command line that starts with the script's name). think.py now drops a leading script
or skill name from args; the prompts show the think call again, with the reasoning as a sentence frame to complete
('R<n> needs ...; the code shown does ... at ...; so I choose ..., because ...'), which think.py does not take as a
thought when it comes back uncompleted; and its one explanation says where the thought goes. Checklist: W7 closer,
B12 closer (the form, the step and the answer say the same), B8 neutral (the think form is back, one form).

### Call forms with skill_name and file_path first (10-27, the user's: "arregla las llamadas mal formadas «fix-issue»")

Every malformed call of vf21, vf22 and vf23 (13, 35 and 8 in 30 tasks) broke `skill_name` or `file_path`
('「fix-issue」' 21 times in vf22, '``', '「think.py」'): the two short values the prompts' call forms put after the long
`args` list, which holds code and quotes. The ADK rejects such a call before any script runs ("Skill '…' not found"),
so only the prompt can prevent it. The forms (tools/make_fast.py `FORM`) now give `skill_name` and `file_path` first and
`args` last, the order the scripts' NEXT lines already use ("with skill_name fix-issue, file_path X and args […]"),
and the recovery note says the same. A skill name with other characters cannot be added as an alias: ADK only accepts
kebab-case names that match the skill's directory.

| Item | Effect | Why |
|---|---|---|
| B4 call syntax | closer, to measure | the short exact values are written before the long args, not after them |
| B12 contradictions | closer | the prompt's forms and the NEXT lines give the values in the same order |

Generic: it changes the call format of every agent; nothing depends on a task.


Checked with Haiku through `tools/anthropic_proxy.py` on 10 dev tasks (10-27, the user's: "prueba con haiku, a ver si
mejoran las malformadas"), the old forms (vf24's build) against the new ones plus think v3 (vf26's build):

| Build | Resolved (judge) | Median s | Calls | Malformed | think.py with no reasoning |
|---|---|---|---|---|---|
| old forms | 9/10 | 60 | 178 | 0 | 0 of 63 |
| new forms + think v3 | 8/10 | 56 | 169 | 0 | 0 of 52 |

Haiku makes no malformed call with either form, so this run shows no regression but cannot measure the fix: the
'「fix-issue」' values are the 31B's own failure, and only a run of the 31B (Colab vLLM or Kaggle) measures them. The one
difference, rich_3180, is the edit's content (the new `chop_cells` walks forward but still returns the chunks
reversed), not the call form; requests_7205 fails in both on an environment test (`test_unzipped_paths_unchanged`).

### A lower temperature for the calls (10-28, the user's: "arregla lo de las llamadas malformadas")

Malformed calls per run_skill_script call in the Kaggle traces (a call whose skill_name is not exactly fix-issue or
whose file_path is not a script name):

| Run | Thinking | Calls | Malformed | Rate | After a malformed call (same agent) |
|---|---|---|---|---|---|
| vf20 | on | 378 | 10 | 2.6% | 0/10 |
| vf21 | on | 384 | 16 | 4.2% | 0/15 |
| vf23 | on | 374 | 8 | 2.1% | 1/8 |
| vf22 | off (think.py) | 736 | 36 | 4.9% | 2/34 |
| vf24b | off (think.py) | 662 | 46 | 6.9% | 8/46 |

In vf24b, 31 of the 46 wrote `「fix-issue」` and 35 came from specify1, mostly think.py calls whose args held a
script or skill name. The value is right and only its delimiter is wrong: the model sampled 「 (or a backtick, or
`":"`) where the `<|"|>` token goes. All agents sampled at temperature 1.0 (the vendor's setting, checklist B14), where a
token with a few percent of the probability is drawn a few percent of the time, once per string value, three string
values per call. Once in the history, the slip is copied more often (8/46 after a malformed call against 38/469 after
a good one). The ADK refuses such a call before any script runs, so no script can repair it, and an alias cannot be
declared (ADK accepts only the kebab-case name of the skill's directory).

Change: the think build (`tools/make_fast.py --think`) samples every agent at temperature 0.5, keeping top_k 64 and
top_p 0.95. Dividing the logits by 0.5 squares the probability ratios: a token with a tenth of the delimiter's
probability gets about a hundredth. Thinking is off in this build, so B14's reason for the vendor setting (greedy
decoding loops in thinking) applies less, and the same-call-three-times stop already ends a looping part. vf27 (vf26 plus
this) measures it: malformed rate per call, loops (the same call repeated), resolved.

| Item | Effect | Why |
|---|---|---|
| B4 call syntax | closer | the delimiter slips are sampling noise, which the lower temperature cuts |
| B14 vendor decoding | farther, with a reason | measured: 5-7% malformed calls at 1.0 with thinking off; loops are watched in vf27 |
| B12 contradictions | neutral | no text changes |

Generic: a sampling setting for every agent; it does not depend on a task, repository or name.

### vf25: the reordered forms work; thoughts cut at max_output_tokens (10-28)

vf25 (think v2 plus skill_name and file_path first) resolved 11/30, like vf24b. Malformed calls fell from 46 of 662
(6.9%) to 8 of 450 (1.8%), so the call-form reorder did what it was meant to do. vf27 adds a temperature of 0.5 on top.

The traces also show a second kind of lost call, which the malformed count did not include: 14 calls written as text
in vf25 (vf24b 12, vf22 7). Each was a think.py call whose reasoning ran 1,500-2,500 words, copying code and tracing it
("Wait, ..."), until max_output_tokens cut it before the call closed. vLLM then returns the text as a plain answer, the
ADK takes it as the agent's final answer, and the part ends with its decision never sent. The accepted thoughts had a
median of 90 words (75% under 140). Change: the think step in every prompt gives a limit and its cost ("At most 150
words: a longer thought is cut off before the call ends, so the call is lost and your part ends with nothing done"),
and think.py's answer to a thought over 1,000 characters gives its word count and the same reason. Raising
max_output_tokens would not help: the long thoughts are loops, and every 1,000 tokens costs about 25 s.

| Item | Effect | Why |
|---|---|---|
| B4 call syntax | closer | a call that cannot close is a malformed call; the limit keeps it short enough to close |
| A-length (long outputs drift) | closer | a bound with its reason, in the prompt and in the feedback |
| B12 contradictions | neutral | the prompt and think.py's answer give the same limit |

Generic: a length limit for every agent's thoughts; nothing depends on a task.

### vf27: the lower temperature failed; reverted, and a stop for empty thoughts (10-28)

vf27 (think v3 + every agent at temperature 0.5) resolved 9/30 (vf25 11/30). The temperature did the opposite of what
was expected:

| | vf25 (1.0) | vf27 (0.5) |
|---|---|---|
| Malformed calls | 8 of 450 (1.8%) | 28 of 583 (4.8%) |
| '「fix-issue」' | 4 | 1 |
| Repeated identical calls | 12 | 86 |
| think.py without reasoning | 22 | 127 |
| Calls written as text (thought cut) | 14 | 9 |
| Empty patches | 5 | 1 |

The delimiter slips were not low-probability samples that a lower temperature removes: at 0.5 the model still closed
strings with a backtick (`file_path:<|"|>think.py`,skill_name:`) and lost the call's structure, at a higher rate. The
loops of B14 appeared instead: specify1 sent `['think.py']` 68 times in a row in fastapi_5624, each answered "Send your
decision now." vf27 also changed think v2 to v3, so the two are not separated; the loop and the repeats are the
temperature's known failure. Changes (vf28):

- Temperature 1.0 again (the vendor's setting).
- Three think.py calls in a row with no thought end the worker's part, as three identical calls already do (SWE-Gym's
  "stuck in a loop"); a real thought or any decision resets the count. Replayed on a fastapi_12942 trace with an
  inserted loop: the third empty call ends the part, no faults.
- The 150-word limit on a thought (above) stays.

| Item | Effect | Why |
|---|---|---|
| B14 vendor decoding | closer | back to the vendor's 1.0 after the measured loops |
| B5 loops of refusals | closer | an empty-thought loop ends after three calls instead of running out the time |
| B4 call syntax | neutral | the 1.8% of vf25 is the reference again |

Generic: a sampling setting and a loop stop for every agent; nothing depends on a task.

### Short native thinking (10-28, the user's: "cómo arreglamos lo de thinking", then "prueba en kaggle")

The two ways of thinking measured so far each failed in their own way:

| | Native, budget 512-1024 (vf21, vf23) | Off, with think.py (vf22, vf24b, vf25, vf27) |
|---|---|---|
| Resolved | 8/30 | 9-11/30 |
| Median per task | 237-264 s | 83-139 s |
| Cut at 5 min | 9-10 | 0-2 |
| Its failure | 42-58% of the time in thoughts that ran into the budget | thoughts sent as a call: empty (`['think.py']`), cut at max_output_tokens before the call closed (14 in vf25), loops (vf27) |

The native budget is a safe limit: vLLM ends the thought and the model still writes its call. think.py has no limit
but max_output_tokens, which loses the call. So variants/short (build: `tools/build.py <out> --variant
stages,fast,short`) is the fast build (native thinking, no think.py, the reordered call forms) with every agent's
thinking budget at 256 tokens (finish keeps 64): at most about 7 s of thought per turn instead of about 30 s. vf29
measures it on 10 of vf5's tasks (the Kaggle GPU quota left before 10-31 does not cover 30), against vf25's result
on the same tasks.

| Item | Effect | Why |
|---|---|---|
| B4 call syntax | closer | no reasoning inside a call's args; the call forms stay reordered |
| B14 vendor decoding | neutral | temperature 1.0, top_k 64, top_p 0.95 |
| Thinking budget (the vf21 audit) | closer | a hard cap that cannot lose the call, a quarter of the old budget |

Generic: a sampling setting for every agent; nothing depends on a task.

vf29 (10 tasks, 10-28): 4/10 resolved against vf25's 5/10 on the same tasks (gained fastapi_14361, lost rich_3043 and
rich_3772), median 155 s against 194 s, 1 empty patch against 3. The failures that think.py brought are gone: no call
written as text (vf25: 6 on these tasks), no think.py calls to lose, malformed 3 of 129 (2.3%, vf25 1.8%). Native
thoughts stayed short (median 521 characters, max 1,223). Ten tasks cannot tell 4 from 5; the result says the short
native budget is as good as think.py at least, with fewer ways to fail, and needs the 30 tasks when the quota allows
(10-31).

vf29b (the same build on vf25's 30 tasks, 10-28): 10/30 against vf25's 11/30 (gained fastapi_14361, lost fastapi_5624
and rich_3772), median 128 s against 139 s, 1 empty patch against 5, malformed calls 0.9% against 1.8%, calls written
as text 2 against 14, 341 calls against 450. The resolved count is within one task, and every failure that think.py
brought is gone or smaller, at fewer calls. Decision: the short native budget (variants/short) replaces think.py as
the build to evaluate; think.py (variants/think) stays in the repository as the measured alternative.
