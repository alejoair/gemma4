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
| V6 | 10-08 15:13 | 1278f56 | Single agent + journal; strict order (out-of-step scripts NOT RUN), reads bounded by time, BM25F locate, hints ignore issue links | as V4 | Yes, strict | as V4 | same | running | | | | | |

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

## Competition submissions

| S | Date (UTC) | Kaggle ref | System | Budget | Public LB | Notes |
|---|---|---|---|---|---|---|
| S1 | 10-05 15:31 | 56855789 | Sample agent baseline, no LoRA, out 8192 | 4 min, 40 calls | **0.12** | |
| S2 | 10-06 09:30 | 56877024 | Locator committee (3 parallel) + merge + fails-before repro + fix/check loop + finalizer | 3.5 min, 50 calls | **0.05** | |
| S3 | 10-07 04:46 | 56900403 | 4-stage sequential: grep locator → graph → fixer (edit + bash) → submitter, out 8192 | 4 min, 45 calls | **ERROR** | Cause unknown |
| S4 | 10-08 13:03 | 56954989 | **V3** exactly as evaluated (`kout_single3/eval_single`), validated with `validate_submission.py` | 5 min, 45 calls | pending | |

## Earlier eval-quick runs (Q)

| Q | Date (UTC) | Tasks | Resolved |
|---|---|---|---|
| Q6–Q9 | 10-07 08:04–12:21 | 5 | 1/5, 0/5, 1/5, 1/5 (fastapi_9555) |
| Q10–Q16 | 10-07 12:54–20:56 | 12 | 1, 2, 2, 2, 2, 2, 1 of 12 (requests_6644, rich_3006) |
