# fastapi_15661: not resolved (81.8 s, 16 calls)

## Reference vs agent
- Reference: adds a new file `scripts/prepare_release.py` (216 lines): `parse_version`, `bump_version`,
  `update_version_file`, `update_release_notes`, `get_release_notes_body`, constants `RELEASE_NOTES_HEADER`,
  `LATEST_CHANGES_HEADER`, and a typer `app` with commands `prepare`, `current-version`, `release-notes`, env vars
  `PREPARE_RELEASE_*` and exact error texts ("must be greater", "already contain", "Could not find", "is empty").
- Agent: adds an optional `sys.argv[1]` version filter to `main` in the existing `scripts/add_latest_release_date.py`.
  The hidden test module fails at import (`from scripts.prepare_release import ...`), so all 16 tests error.

## First wrong stage: none fixable (statement), visible first at requirements/locate
The statement is the title "Automate release preparation" plus an empty PR template ("Write the description of your
PR here"). The requirement could only restate it: "R1: Release preparation is automated ... the steps to prepare a
release run automatically instead of by hand." Locate then searched "release" and picked the only release script:
"locate is done: R1 is decided at: main."

## Was the information in the statement?
No. The hidden tests pin a new module path, function names, signatures, a typer CLI with three commands, env var names
and error messages. None of this is derivable from the title.

## Did the script's answers contribute?
Not to the outcome (unsolvable). Two system flaws are visible and matter elsewhere:
- Review evidence was misleading: "Existing tests of the changed code: 31 existing tests pass
  (docs_src/app_testing/.../test_main.py, tests/benchmarks/..., tests/test_tutorial/test_bigger_applications/test_main.py)".
  None of these exercise `scripts/add_latest_release_date.py::main`; this looks like a bare-name match on `main`.
  The reviewer then said "done" with this as support.
- The flow has no way to say "this needs a new module" when the requirement names no code: locate only offers new
  members for names the requirement already flags as new (`[new: ...]`), so the model edited the nearest existing
  script.
- Specify first answered "no change" (no code in the plan fits); review then forced an invented change.

## Generic fix
None for the outcome: the task is not solvable from the statement.
Secondary, generic: (1) the test map of changed code must key on file plus qualified name, never a bare name, and the
review must say "no existing test covers this code" when none does; (2) when the requirement names no repository code
and the locate candidates share no term with the requirement beyond generic words, let locate choose a new file
(`<path>` new) instead of forcing an existing place.
