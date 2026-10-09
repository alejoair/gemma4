# What each script must do, and the review of 2026-10-09

The agent has one entry point, `agent/skills/swe/scripts/step.py`; the other modules are internal. Each line below is
a requirement. The status is the result of reading the code (2026-10-09 review); the user's rule is that only a run
against the model, monitored through the gateway, counts as evidence that it works.

## step.py: one call per decision; the answer ends with the exact next call (NEXT line)

| Step | Must do | Status |
|---|---|---|
| S0 start | Take the statement copy and the search terms. A single short line is asked for once again. Clean the statement and keep it. Extract up to 6 requirements (fix/new/rename). Rank 10 candidates (BM25F; the owner class of a new entity first). | OK |
| D1 choose | Accept 1–3 candidate ids, `file::Name` or a code fragment. List the related places. Plan the chosen places, their copies in a same-named file and their async/sync twins. Show the place list and the first edit window. Save the verified state. | **Fixed**: when no chosen code was found the plan was empty and `planned[0]` raised |
| D3 window | Show the place's code (60 lines around the requirement words; a long class also lists its members). Show what the change must do: the requirements, the statement's behaviour sentences, its example, the shortest existing test that uses the code. List the accepted forms. | **Fixed**: the "(k of n)" counter was wrong after reopening a place. The accepted forms now list every form |
| D3 accepts | An edit (4 items, packed or not; `""` deletes). `skip`, `done`, `P<n>`, `C<n>` (adds a candidate), `back`. Up to 3 questions per place: a name (definitions and callers), a file (outline), `["P<n>", a, b]` (lines in full). Anything else is refused and counts as a failed attempt; 2 failed attempts leave the place. | **Fixed**: `done` did not exist (the model could not finish without skipping each place). After the questions ran out, the answer said "it needs 4 items". The line view hid long texts |
| D3 edit | Apply with repairs and a syntax guard. Run the existing related tests. BROKEN: undo to the last verified state. OK: save it and go to the next place. No edits after 340 s. | OK |
| D4 finish | Show the places changed or not and each requirement's word coverage. `R<n>` goes back to choose for that requirement; an edit reopens D3. | **Fixed**: coverage raised when `git diff` failed. Places the model skipped were labelled "its edits failed" |
| main | Detect repeats of the last 3 calls. In D3 a repeated question is answered again once; a repeated edit counts as a failure. In D1, after 2 repeats, the first candidate opens. Turn an internal error into one line plus the next call. Cap the answer at 8,000 characters. Always end with NEXT. | OK |

## Internal modules

| Module | Must do | Status |
|---|---|---|
| `_args` | Read the model's arguments tolerantly: wrappers, packed lists, `79\|code` numbers, packed edits, leaked call syntax, fences | **Fixed (serious)**: escaped `\n`/`\r\n` were turned into line breaks before the edit, which broke literals like `b"\r\n"` (httpx). Now `_edit` tries that only when the text does not compile as given |
| `_edit` | Replace lines a..b. Repairs: a boundary line repeated, re-indent, escaped quotes, escaped line breaks, a range longer than the text. Never write a file that does not compile. Warn on unknown names and removed definitions. Refuse test files. `""` deletes | OK. Limit: it cannot create a new file |
| `_tests` | Select the tests: the module's own `test_<mod>.py`, importers and rare names, at most 3 files, big files filtered. Run them in parallel with one 30 s deadline. Stub missing test-only modules. Count only new failures (checked on an original worktree). Give OK / BROKEN / NOT VERIFIED. Keep and restore the verified state. Find the test example for the window | OK. Limit seen in requests_7328: "OK" with 5 tests while the hidden tests failed, because the redirect tests do not name the changed function |
| `_statement` | Clean the statement. Extract terms, requirements, change type, behaviour sentences and the example | OK; a statement copied on one line is now split into requests |
| `_rank` | BM25F over names, code, prose and path | OK (gold in the top 10 for 76% of 128 tasks) |
| `_impact` | Related places: copies, twins, overrides and base versions, callers, exports. A sibling to model a new entity on | OK. Limit: covers 57 of the 403 other gold places |
| `_journal` | The state machine. Repeats, skip, failures. A done place stays done | OK |
| `_state` | State per repository and commit in /tmp | OK |
| `_repo` | Find the root (SWE_REPO, PWD, /workspace, git). Byte-exact reads and writes. The diff | OK. It depends on PWD being /workspace on Kaggle |
| `_code` | AST symbols, skeletons, numbered lines with long texts collapsed | OK (the explicit line view no longer collapses) |
