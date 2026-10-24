# fastapi_14953 — not resolved (65.6 s, 12 calls, patch = 1 of the reference's 2 code changes)

## 1. Reference vs agent
- Reference: (a) `UploadFile.__get_pydantic_json_schema__` returns `contentMediaType: application/octet-stream`;
  (b) `fastapi/_compat/v2.py` subclasses pydantic's `GenerateJsonSchema` and overrides `bytes_schema` so every plain
  `bytes` field gets `contentMediaType` (plus `contentEncoding: base64` when `ser_json_bytes`/`val_json_bytes` is
  base64); (c) adds a docs tutorial `docs_src/json_base64_bytes/tutorial001_py310.py` that new tests import.
- Agent: only (a). All 16 `test_file/*bytes*` schema tests and the `test_request_files` openapi snapshots still emit
  `format: binary` for `bytes` fields, because that text comes from the library's generator, not from repo code.

## 2. First wrong stage: locate
R1 was right ("The JSON Schema generated for a bytes field contains contentMediaType ..."). Locate's candidates held
only repo code matching the words; the producer of the `bytes` schema is library code (pydantic
`GenerateJsonSchema.bytes_schema`), reached in the repo only through the re-export in `fastapi/_compat/v2.py`, which
was not on the list. The model chose the single literal hit: `R1 is decided at: UploadFile.__get_pydantic_json_schema__.`
The requirements probe had already said `bytes: not a function or class; the code uses this name`.

## 3. Information in the statement: partly
"Fix JSON Schema for bytes" plus "Pydantic v2 implements ... except for this, I'm implementing it there too" lets a
careful engineer infer that `bytes` fields (not only UploadFile) need an override of the library's schema generator.
Not derivable: `contentEncoding: "base64"` under base64 bytes config, and the new tutorial module
`docs_src/json_base64_bytes/tutorial001_py310.py` whose 4 tests (`test_get_data`, `test_post_data`,
`test_post_data_in_out`, `test_openapi_schema`) are required test functions. Full resolution is therefore not
reachable from the statement; the 16+12 schema tests were.

## 4. Did the system contribute? Yes
- Locate shows only repo definitions; it never says "the old value is produced by library code X that the repo
  imports/re-exports here", so the real decision point could not be chosen.
- Review had the decisive evidence and cut/misframed it: the broken snapshot's actual output showed
  `'file': {'type': 'string', 'format': 'bina...` (a `bytes` field still emitting the old value), truncated, and
  followed by "These tests show behaviour the issue keeps" although the issue explicitly changes exactly that value.
- The review gate refused the first `done` ("So it is not done yet") but accepted the identical second `done`.
- Edit: "Kept, not checked: the existing tests are not run after each edit" (verification off under time).
The model's own error: answering `done` twice without asking where the remaining `format: binary` comes from.

## 5. Generic fix
When the statement names an old literal value to replace (`X instead of Y`), after the edits search the outputs of the
broken/related tests for Y; if Y still appears, review must say so explicitly ("the old value Y still appears in the
output of test T at <path in the diff>") instead of "behaviour the issue keeps", and route back to locate with that
evidence. Complementary: in locate, when a requirement concerns a built-in/library type the repo does not define,
list repo places that import, subclass or re-export the library class that produces it (e.g. by scanning
`from <lib> import <Class>` lines whose class has a method named after the type).
Do not accept a repeated `done` when the refusal reason (broken tests) is unchanged.
