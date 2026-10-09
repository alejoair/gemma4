# Versions

One row per evaluated version. Update this file after every Kaggle run or submission (see CLAUDE.md).

- **V** = a version of the Kaggle notebook `alejoair7/gemma4-eval-single`. It runs our `bench/kaggle_eval.py` with the real 31B on 4×L4, on the same 10 dev tasks (`--n 10 --seed 0`): fastapi_14448, fastapi_14583, fastapi_14851, fastapi_14986, fastapi_15800, httpx_3672, requests_7328, rich_3006, rich_3469, rich_3521. The outputs are in `scratchpad/kout_single<N>`.
- **S** = a real competition submission (public LB score, about 60 tasks from private repos).
- **Q** = earlier runs of the notebook `alejoair7/gemma4-eval-quick` (pipelines before the journal, other task sets).

## Kaggle eval runs (10 dev tasks, real 31B)

| V | Date (UTC) | Commit (scripts) | System | Tools of the agent | Journal / gate | Generation | Budget | Resolved | Resolved tasks | Timeouts / empty patch | Tool calls (total) | Main failures seen | Changed for the next version |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V1 | 10-07 21:25 | 5f5223a | Single agent | 9 harness tools (incl. `run_command`, `read_file`, `edit_file`) + skill `swe` | No (journal.py exists, never called) | thinking 2048, out 8192, T 0.2 | 5 min, 45 calls | **0/10** | – | 8 / 5 | 127 scripts, 113 `run_command`, 39 `read_file`, 15 `edit_file` | Called the missing tool `show_file` → task lost (fastapi_14583); mangled `file_path` (`scripts/show.py`,skill_name:...) 18× | Prompt examples in Gemma's tool-call format |
| V2 | 10-07 23:43 | f15c518 | Single agent | same as V1 | No | same | same | **1/10** | rich_3006 | 6 / 3 | 94 scripts, 96 `run_command`, 65 `read_file` | The model works with `run_command` / `read_file`; `journal.py` and `hints.py` never called | Scripts: STATUS after 12 reads, locate.py turns grep commands into searches |
| V3 | 10-08 02:20 | edf9342 | Single agent | same as V1 | No | same | same | **3/10** | fastapi_14851 (empty patch, likely an artefact), rich_3006, rich_3521 | 6 / 2 | 99 scripts, 96 `run_command`, 67 `read_file` | Same: scripts used for about a third of the calls | **Submitted as S4.** Next: journal engine, take `run_command` away |
| V4 | 10-08 11:50 | 8622645 | Single agent driven by the journal (`_journal.py`, `procedure.json`) | `submit_patch` + 3 graph tools + skill `swe` (no `run_command` / `read_file` / `edit_file`) | Yes: JOURNAL line with NEXT, gate refuses out-of-phase calls, auto-check after edit | thinking 512, out 4096, T 0.2 | same | **1/10** | rich_3006 | 4 / 2 | 228 scripts (show 111, locate 41, try 26, edit 24, hints 13, journal 3), 0 `run_command` | (1) The 31B ignores NEXT: up to 16–17 show.py calls before hints.py (fastapi_14448, 14851). (2) 39 calls lost to a mangled `skill_name` (`「swe」`, `` `swe` ``, empty) → SKILL_NOT_FOUND. (3) 55 calls refused by the gate, and the model retries them. (4) 9/10 tasks reach the 14,336-token compaction threshold. (5) fastapi_14986: 2/2 requirements covered, then 5 more edits in the SUBMIT step | V5 = pipeline. Pending: NEXT compliance, skill_name mangling, context size |
| V5 | 10-08 13:05 (queued; ran 14:13–15:11) | 756d010 | Pipeline B: SequentialAgent locator → planner → editor → submitter, `include_contents: none`, one skill per stage | per stage (see `pipeline/sub_agents`) | Yes, stage-aware (before the strict order) | locator 256, planner 1024, editor 512, submitter 0 | same | **1/10** | rich_3006 (96 s) | 9 / 7 | 235 scripts (show 164, locate 32, hints 16, try 12, edit 7), 55 graph-tool calls; by stage: locator 192, planner 85, editor 18, submitter 1 | (1) Time: the editor was reached in only 3/10 tasks, the submitter in 1. The locator used 60–300 s (45 calls in fastapi_14448, 38 in rich_3469 with no report at all), the planner 30–220 s. 4 stages × ~5 s per call do not fit 5 min at this call count. (2) The locator read without limit before the strict order (show.py 164). (3) Locator prompts reached the compaction threshold (max 14.4k–16.0k tokens) | V6 = single agent with the strict order + BM25F. The pipeline needs per-stage call budgets (locator ≤ 6 calls) before another run |
| V6 | 10-08 15:13–16:09 | 1278f56 | Single agent + journal; strict order (out-of-step scripts NOT RUN), reads bounded by time, BM25F locate, hints ignore issue links | as V4 | Yes, strict | as V4 | same | **2/10** | rich_3006 (44 s, 5 tools), rich_3521 (12 tools) | 7 / 1 | 229 scripts (show 128, edit 31, locate 20, try 16, hints 10, check 6), 0 run_command | (1) BM25 put the reference function first in fastapi_14448 (`Dependant._unwrapped_call`, not found before) and fastapi_14986 (`FastAPI.setup`); requests_7328 still starts at the class `Response`. (2) 31 of 229 script calls (14%) had a mangled `skill_name`/`file_path` (`「swe」`, `「scripts/show.py」`); the ADK rejects them before any script runs. Rate by version: V1 31%, V2 21%, V3 10%, V4 17%, V6 14%. The `<|"|>` examples (V2) removed the args-packed-in-one-string form; `「swe」` (Gemma's string delimiter written as CJK brackets) exists since V1. Skill names must match `[a-z0-9_-]`, so no alias can catch it: fewer calls is the lever. (3) 50 NOT RUN refusals, retried. (4) show.py is still 128 calls: reading in EDIT is bounded only by time, and 9/10 tasks reach the compaction threshold. (5) The automatic check said OK in fastapi_14448/14583/14986/15800 and the hidden tests failed | Next: fewer calls (each one risks the mangling), budget reads per step by calls again but without the dead end (allow the show.py of a symbol a refused edit needs) |
| V8 | 10-09 16:39 (queued from 15:00) | bace21b | Single agent, step.py v1 + edit-step questions (text search, name lookups, line views), what-the-change-must-do in every window | `submit_patch` + 3 graph tools + skill `swe` | Yes, strict D3 with questions | thinking 512, out 4096, T 0.2 | 7 min, 30 calls, 240 s per command | **ERROR before the first task** | – | – | – | `kaggle_eval.py` imported `swegemma.models.discovery`, gone in the wheelhouse updated that day (swegemma 0.2.11, adk_submission 0.2.13) | kaggle_eval and the validator use `adk_submission.discovery`; V10 = current code (commit 82a7e10, graph tools no longer declared) |
| V10 | 10-09 16:53–17:47 | 82a7e10 | Single agent, step.py with the D3 questions (lookups, search, `python -c`), task view in every window, `{problem_description}` in the instruction | `submit_patch` + skill `swe` | Yes, strict D3 with questions | thinking 512, out 4096, T 0.2 | 7 min, 30 calls, 40 turns, 240 s per command | **1/10** | rich_3006 (122 s) | 2 time / 4 empty patch (4 hit 40 turns) | 312 tool-call turns, of which **138 (44%) were malformed** and never reached step.py (harness counted 167 calls) | (1) **Malformed `skill_name`** (`「swe」`, `「swe」<|"|>`, `` ``swe`` ``, or `skill_name` merged into `file_path`) in 9 of 10 tasks, highest rate since V1 (V6 14%). Since swegemma 0.2.11 the error comes back as a tool response, and the model then **repeats the same broken call**: fastapi_14448 34 times in a row, fastapi_15800 26 times, both lost to the 40-turn limit. The first malformed call came at call 4–8, usually on a D3 question. (2) Patches in 6 tasks; httpx_3672 and rich_3521 got BROKEN verdicts; requests_7328 edited and submitted (wrong fix). (3) The statement was copied with literal `\n` (fastapi_14448 S0 requirements show `nThis`) | V11 = HEAD 66423f9 (before the D3 redesign), V12 = the D3 redesign (prepared context, edit/skip/back only); the malformed-call loop needs its own change (fewer calls; temperature to be tested) |
| V11 | 10-09 17:56–18:45 | 66423f9 | As V10 plus the local batch 2 fixes: a repeated question gets its full answer again, failing tests that use a renamed old name are excused, NOT VERIFIED with 0 passing tests, `python -c` in the edit step, questions stop at call 12 without an edit | `submit_patch` + skill `swe` | Yes, D3 with questions | thinking 512, out 4096, T 0.2 | 7 min, 30 calls, 40 turns, 240 s per command | **1/10** | rich_3006 (161 s) | 2 time / 1 turns; **0 empty patches** (V10: 4) | 234 tool-call turns, **52 malformed (22%**; V10 44%); longest run of identical calls 3 (V10 36); harness counted 174 calls | (1) A patch in all 10 tasks. Near misses on the hidden tests: fastapi_14448 6 of 28 fail, fastapi_14851 2 of 9, rich_3469 2 of 7, fastapi_14986 4 of 7. (2) Missing features break test collection: httpx_3672 (`httpx.Stream` never added; the rename `complete` → `reset` and `keep_alive` were done), fastapi_14583 (a module the tests import was not created). (3) requests_7328 deleted `resp.history = hist[1:]` instead of moving it (`hist[:]` before the append), plus the usual httpbin errors. (4) The model used `python -c` 24 times and repeated questions 12 times. (5) Malformed `skill_name` still in 9 of 10 tasks (`「swe」`, `「swe」<|"|>`, empty), but no long loops | V12 = D3 redesign + `fix-issue` (running). Found after V11: tool answers reach the model as escaped JSON and names appear in four quote forms (`docs/prompt_audit.md`) |

## V4 per task (single agent + journal)

| Task | Resolved | Patch chars | End | Edits applied / not applied | Automatic check verdicts | NOT RUN (gate) | What went wrong |
|---|---|---|---|---|---|---|---|
| fastapi_14448 | 0 | 860 | timeout | 0 / 0 (1 cut by the timeout) | – | 7 | 16 show.py before hints.py; first edit at 300 s |
| fastapi_14583 | 0 | 0 | timeout | 2 / 0 | – | 8 | 12 calls with a mangled `skill_name`; reads files with try.py |
| fastapi_14851 | 0 | 0 | timeout | 0 / 0 | – | 14 | 17 show.py before hints.py, never edited |
| fastapi_14986 | 0 | 1661 | submitted | 6 / 2 | OK ×4 | 3 | 2/2 requirements covered, then kept editing in SUBMIT; check OK but hidden tests fail |
| fastapi_15800 | 0 | 1132 | submitted | 2 / 0 | OK ×2 | 5 | check OK, hidden tests fail (semantics) |
| httpx_3672 | 0 | 2408 | submitted | 5 / 2 | OK, NO CHANGES, OK, OK | 8 | check OK, hidden tests fail |
| requests_7328 | 0 | 895 | submitted | 1 / 0 | TESTS FAIL ×2 | 1 | submitted with failing tests |
| rich_3006 | **1** | 558 | submitted | 1 / 0 | OK | 1 | – (9 model calls, 49 s) |
| rich_3469 | 0 | 677 | submitted | 1 / 0 | OK | 4 | check OK, hidden tests fail |
| rich_3521 | 0 | 471 | timeout | 1 / 0 | OK | 4 | resolved in V3; edited but did not submit before the timeout |

Readings: (a) the automatic check said OK in 5 failed tasks, so the related tests do not exercise the new behaviour and OK is weak evidence; (b) the reading phase is where time is lost (NEXT ignored, gate refusals retried, mangled `skill_name`).

## V5 per task (pipeline)

| Task | Resolved | Patch chars | Stages reached (calls, seconds) |
|---|---|---|---|
| fastapi_14448 | 0 | 0 | locator 45 calls 20–238 s, planner 12 calls to the timeout |
| fastapi_14583 | 0 | 0 | locator 21 (11–161 s), planner 8 to the timeout |
| fastapi_14851 | 0 | 0 | locator 14 (13–84 s), planner 19 (97–272 s) |
| fastapi_14986 | 0 | 1416 | locator 6, planner 5, editor 8 (191–293 s), no submit |
| fastapi_15800 | 0 | 448 | locator 11, planner 6, editor 8 (222–293 s), no submit |
| httpx_3672 | 0 | 0 | locator 7, planner 11 (72–292 s) |
| requests_7328 | 0 | 0 | locator 22 (8–197 s), planner 5 |
| rich_3006 | **1** | 558 | locator 4, planner 6, editor 3, submitter 2: 96 s |
| rich_3469 | 0 | 0 | locator 38 calls over the whole 300 s |
| rich_3521 | 0 | 0 | locator 34 (10–265 s), planner 5 |

## V6 per task (single agent, strict order, BM25F)

| Task | Resolved | Patch | End | locate #1 | Wasted calls | Notes |
|---|---|---|---|---|---|---|
| fastapi_14448 | 0 | 974 | timeout | `Dependant._unwrapped_call` (correct) | 9 NOT RUN, 4 bad name | one edit, checked OK, hidden tests fail |
| fastapi_14583 | 0 | 390 | submitted | `PydanticSchemaGenerationError` | 6 NOT RUN | |
| fastapi_14851 | 0 | 0 | timeout | – | 12 bad name, 6 NOT RUN | 20+ show.py, never edited |
| fastapi_14986 | 0 | 1252 | timeout | `FastAPI.setup` (correct) | 7 NOT RUN | 4 edits checked OK, hidden tests fail |
| fastapi_15800 | 0 | 567 | timeout | `APIRouter` | 9 NOT RUN | |
| httpx_3672 | 0 | 907 | timeout | `HTTPParser.complete` (correct file) | 12 bad name, 5 NOT RUN | one edit broke tests and was rolled back |
| requests_7328 | 0 | 899 | submitted | `Response` (class; the fix is in `resolve_redirects`) | 0 | |
| rich_3006 | **1** | 558 | submitted | `auto_rich_repr` (correct) | 0 | 5 tools, 44 s |
| rich_3469 | 0 | 0 | timeout | `Text.align` | 4 NOT RUN | edit not applied |
| rich_3521 | **1** | 441 | submitted | – | 4 NOT RUN | 12 tools |

## Competition submissions

| S | Date (UTC) | Kaggle ref | System | Budget | Public LB | Notes |
|---|---|---|---|---|---|---|
| S1 | 10-05 15:31 | 56855789 | Sample agent baseline, no LoRA, out 8192 | 4 min, 40 calls | **0.12** | |
| S2 | 10-06 09:30 | 56877024 | Locator committee (3 parallel) + merge + fails-before repro + fix/check loop + finalizer | 3.5 min, 50 calls | **0.05** | |
| S3 | 10-07 04:46 | 56900403 | 4-stage sequential: grep locator → graph → fixer (edit + bash) → submitter, out 8192 | 4 min, 45 calls | **0.05** | Shown as ERROR at first; Kaggle lists it as COMPLETE 0.05 on 10-09 |
| S4 | 10-08 13:03 | 56954989 | **V3** exactly as evaluated (`kout_single3/eval_single`), validated with `validate_submission.py` | 5 min, 45 calls | pending | |
| S5 | 10-09 (queued) | — | **v1 step procedure** (commit bea4f0f): one agent, `swe/step.py` journal (S0 candidates → choose → plan → edit + tests → submit), thinking 512, out 4096. Never run on Kaggle with the 31B (GPU weekly quota exhausted); local 12B: rich_3006 resolved, requests_7328 not | 5 min, 30 calls, 40 turns | not yet submitted | 02:42 UTC refused: S4 still pending (one pending submission per team). `scratchpad/submit_s5_when_free.sh` submits it when S4 finishes, until 23:50 UTC |

## Earlier eval-quick runs (Q)

| Q | Date (UTC) | Tasks | Resolved |
|---|---|---|---|
| Q6–Q9 | 10-07 08:04–12:21 | 5 | 1/5, 0/5, 1/5, 1/5 (fastapi_9555) |
| Q10–Q16 | 10-07 12:54–20:56 | 12 | 1, 2, 2, 2, 2, 2, 1 of 12 (requests_6644, rich_3006) |
