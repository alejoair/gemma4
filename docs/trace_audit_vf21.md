# Audit of the vf21 traces: inefficiencies (2026-10-27)

At the user's request ("lanza varios agentes sin contexto a analizar las trazas … que encuentren ineficiencias"), five
fresh agents with no context of the design discussion read the Kaggle traces of vf21 (the lazy-map build, Gemma, vf5's
30 tasks, 8/30 resolved) and of vf20 for comparison. Three took 10 tasks each step by step; one measured the time
budget; one measured what the model reads and writes. Their counting scripts are in the session scratchpad
(`ineff/{A,B,C,time,ctx}/`); this file keeps their findings, merged where they agree. Numbers are vf21's 30 tasks unless
a group of 10 is named. No change has been made from it yet.

## Where the 5 minutes go

| Component (30 tasks) | vf21 s | % | vf20 s | % |
|---|---:|---:|---:|---:|
| thinking | 5,391 | 75.3 | 5,093 | 74.6 |
| visible output (args, "done") | 984 | 13.7 | 852 | 12.5 |
| prompt, per-call overhead, hidden compaction | 264 | 3.7 | 180 | 2.6 |
| scripts | **149** | **2.1** | 482 | 7.1 |
| harness before the first call | 81 | 1.1 | 121 | 1.8 |
| in flight at the cut (lost) | 287 | 4.0 | 96 | 1.4 |

- vLLM decodes at 36 tok/s with one request at a time (681 of 762 log lines "Running: 1 reqs", KV cache at most 4.6%).
  A regression over 553 calls: t ≈ 27.5 ms per generated token + 0.06 ms per prompt token (prefix cache hit 76%) + 0.3 s.
- The lazy map cut script time by 333 s, but the model spent ~430 s more on thinking and output: the tool is no longer a
  lever; the model's tokens are.
- **About 40% of the time moves nothing**: 943 s of calls after the script answered "NEXT: no call", 776 s of rejected
  calls, ~1,000 s of `open`/"done"/finish turns.

## Findings, by cost (each with the tasks it cost)

1. **Thinking runs into the 1,024-token budget** (`sampling_1k.yaml`, `sampling_edit.yaml`): 122-129 of 553 calls,
   ~3,100-3,900 s (42-58% of model time); specify 74 of 147 calls (50%), 39% of its thoughts cut mid-sentence. After a
   capped thought the next call is malformed 8.5% of the time (1.2% otherwise; 11 of 16 malformed calls). ~19-25% of
   specify's and edit's thinking copies code from the window; in edit/check 65% of the code sent was written first,
   whole, in the thought. Trivial decisions ('no change', check's 'done') also reach the cap (~30 s each).
2. **Forced 'no change' on the main place before the others are specified** (`_stages.py:2154`, `p is
   _main_place(st)`): the requirement's text becomes that place's sentence, the specifier's part ends, its later real
   sentence is refused ("your part is finished"), and edit opens the forced place first (plan order) where its 'no
   change' is refused again. 11 tasks, ~760 s; involved in 6 of the 7 empty patches. Lost rich_3043 (the correct
   sentence for CONSOLE_HTML_FORMAT was ready at 204 s), rich_3772, fastapi_14419, fastapi_9753; fastapi_13207 resolved
   with 14 s left (lost in vf20 the same way) and left a harmful `=True` in the patch.
3. **The requirements stage contradicts its prompt**: each call replaces the saved list, and the answer invites
   resending ("sending the pairs again replaces them"); after STAGE_END (75 s) calls are refused with that same text;
   `_copied` drops the pair that copies the issue (the prompt asks for it); `_looks_like_requirements` needs ≥3 words, so
   a 2-word title ("empty live", "preserve newlines") is read as a name lookup and not saved. 10-12 tasks, ~620-745 s.
   Lost rich_3180 (203 s in requirements) and rich_4075 (the bug requirement dropped); fastapi_13537 resolved 1.3 s
   before the cut.
4. **The scripts' clock starts late**: `t0` is set at the first script call (`_stages.py:124`), 8-36 s after the task
   starts (median ~18-20 s). BUDGET_S (4:30) falls at 4:38-5:06 of the harness, stage ends shift the same way, "Time
   used" understates (B10). The three cut tasks of group A have the largest offsets; an edit was refused as "time is up"
   with ~4 s really left (rich_3180); check skipped review ("Nothing is left to change") in several tasks.
5. **One runaway generation**: `max_output_tokens: 4096` for edit lets one call run ~110 s; rich_3772 (~4,000 tokens,
   107 s) and fastapi_14419 (~3,100 tokens, 82-86 s) were cut mid-generation with empty patches. The longest completed
   edit call was 1,663 tokens.
6. **Hidden compaction in specify**: three specify sessions passed 14,336 prompt tokens (14,407-14,615); 13-26 s of
   summary generation outside the trace each; rich_3942 then lost its thread (two refused `open`). Cause: `CHAIN_CHARS`
   counts script answers only, while with `include_thoughts: true` thoughts and own calls are 29% of specify's prompt.
7. **`replace` that does not apply**: `\"` sent where the file has `"` (fastapi_9753, twice, then the cut: empty
   patch), `\\\\` for `\\` (rich_3278), "occurs 2 times" although the model sent the `NNN|` numbers that tell them apart
   (fastapi_13207, fastapi_5624), an old line without its indentation (fastapi_14297). ~190 s.
8. **Edits accepted unchecked, corrections refused**: rich_3278's regex corrupted by doubled backslashes (the `NN|`
   margin is glued to a regex that uses `|`) broke 6 passing tests; fastapi_5624's edit was `pass` plus "# Wait…"
   comments and its correction was refused ("your part is finished"). Both lost.
9. **`missing` depth rule refused the reference's fix twice** in fastapi_12942 ("names no further code", "already named
   other code once"): lost.
10. **Check reviews blind**: 'done' in 8 unresolved tasks; a false 'not done' on a correct patch (rich_3905) because the
    window says "the numbered code below is the code now" but shows a diff with 1 line of context and no `@@`; no test
    evidence at all (EDIT_TESTS/VERIFY off). 571 s, no result changed in 30 tasks.
11. **Ceremony**: `open` 598 s, "done" 366 s (16 of them > 5 s of doubt caused by the previous answer listing other
    agents' pending work), finish 40 s: ~33 s per task. check opened 7-9 times only to read "Nothing is left to change".
12. **Window sections the model does not use** (specify): "Attributes it reads" used in 2 of 17 windows, "Other members"
    1 of 14, "calls in a condition or that the reproduction test runs" 6 of 26 (this build has no reproduce stage),
    "Related code" 2 of 17, and 73% of its entries repeat another section. The progress line in 119 answers is read in
    2 thoughts; "(from the issue: …)" 173 times; the requirements agent gets the statement twice and the directory tree.
13. **Smaller**: locate answers each requirement separately with the same places (~45 s); ambiguous names resolved
    silently to the wrong definition and a phantom new place created from a path component (fastapi_14297); a new
    parameter not passed by its caller although the caller was shown (fastapi_13713, `external_docs`).
14. **Not measured**: the GPU served one request at a time; independent places specified/edited in parallel would use
    it (the build removed ParallelAgent after S10; see design_fast.md).

## Which findings cost a result

| Task | Findings |
|---|---|
| rich_3180 | 3, 4 |
| rich_4075 | 3, 2 |
| rich_3043 | 2, 1 |
| rich_3772 | 2, 5 |
| fastapi_14419 | 2, 5, 6 |
| fastapi_9753 | 2, 7 |
| rich_3942 | 6, 2, 4 |
| rich_3278 | 7, 8 |
| fastapi_5624 | 8 (localization also wrong) |
| fastapi_12942 | 9, 6 |
| requests_7505 | 3, 4 (the F2P places never specified) |
| fastapi_13207, fastapi_13537 | resolved with 14 s and 1.3 s left |

Several of the others were lost to localization or to statements that do not say what the tests expect
(fastapi_15661, 15280, 14258, 14953, rich_4076, 3934, 3718).

## Generic fixes the agents proposed (to be checked against llm_checklist.md and measured with the model)

1. Requirements: accumulate pairs (a pair with the same quote replaces only itself), never invite resending, a frozen
   stage says so, accept the copied title, any even number of items is a submission.
2. Forced 'no change': force only when the last open place gets 'no change' and no place has a sentence; refusing 'no
   change' returns the place to the specifier without ending its part; edit places with a real sentence first.
3. Clock: anchor `t0` to the task start (subtract the first-call delay, ~35 s at most) and keep a reserve for editing.
4. Edit output cap ~2,048 tokens; specify/check thinking budgets measured (512 for check, specify both ways), and an
   instruction not to copy the window's code into the thought.
5. Count thoughts and own calls in CHAIN_CHARS (or one less place per specify batch).
6. `replace`: strip `NNN|`, undo one level of `\"`/`\\` escaping, use line numbers for duplicates, re-indent to the
   found line, show the closest line on failure; a margin that cannot be read as code (`12: ` instead of `12|`).
7. Allow one correction of the last item before a part closes; refuse an edit that leaves only `pass`/comments.
8. Let `missing` go one more step down the call table (bounded by the stage end, not by depth).
9. Check: show the place as it now is, numbered, and one bounded run of the existing tests that import the changed
   module.
10. Condense the unused window sections (keep the piece, one line per name), drop repeated text.
