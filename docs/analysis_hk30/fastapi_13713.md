# fastapi_13713: add an `external_docs` OpenAPI parameter to `FastAPI`

Result: not resolved (255 s, 53 calls). Hidden test `test_application.py::test_openapi_schema` still fails.

## 1. Reference vs agent
- Reference: adds `openapi_external_docs` to `FastAPI.__init__` (stored on `self`), passes it from `FastAPI.openapi()`
  to `get_openapi(external_docs=...)`, which writes `output["externalDocs"]`. Three places.
- Agent: only `get_openapi` got the `external_docs` parameter and the `externalDocs` key (correct there). `FastAPI.__init__`
  is unchanged (every edit failed or was undone) and `FastAPI.openapi` was never planned.

## 2. First wrong stage: edit (system-induced), with a plan gap behind it
- The edit window for `FastAPI.__init__` elides the whole signature: `64|    def __init__(` /
  `... (the parameters, lines 65-822, not shown) ...`. The editor must add a parameter it cannot see. It guessed anchors
  (`separate_input_output_schemas: bool = True,`, `**extra: Any,\n    ) -> None:`, `servers: Optional[...] = None,`); all
  "Not applied: the old lines were not found" because each real parameter is `name: Annotated[..., Doc("""...""")] = ...`.
- The body-only edit `self.external_docs = external_docs` is undone each time: "the edit uses names that this file
  neither defines nor imports: external_docs. Import them". The advice (import) is wrong for a missing parameter.
- The editor's correct escape, `['missing', 'FastAPI.__init__ parameter list (lines 65-822)', ...]`, was accepted as a new
  "place" ("goes into the plan before FastAPI.__init__") but the next window is identical: the hidden lines never appear.
  Loop repeated 4 times (specify -> edit -> undone -> review not done) until the route submitted.
- Plan gap: `FastAPI.openapi` (the caller that forwards app attributes to `get_openapi`) was never in the plan; even a
  successful `__init__` edit would not have put `externalDocs` in the output. Locate R2 chose `get_openapi, OpenAPI`.

## 3. Information in the statement: partly
The behaviour (root-level `externalDocs`) is derivable. The parameter name is not: the statement says "adds an
`external_docs` parameter to the `__init__` method", the test patch builds `FastAPI(openapi_external_docs=...)`
(tests/main.py). An engineer following the statement literally fails at import; only the repo's `openapi_*` naming
convention (`openapi_tags`, `openapi_url`) hints at the real name. A perfect run of this system would still fail.

## 4. Did the system contribute? Yes
- Hidden signature with no way to reveal it (the window collapses Doc strings in the body to "... (24 lines of text) ...",
  but drops the parameter list entirely).
- The undefined-name check undoes a correct half edit and suggests an import, not "add a parameter".
- `missing` with a non-code name is accepted as a plan place that changes nothing (silent no-op route, repeated).
- The plan did not add the function that passes a stored attribute on to the changed callee.

## 5. Generic fix proposals
1. Never elide a signature in an edit window: show every parameter line, collapsing only long string literals / Doc
   bodies inside it ("... (n lines of text) ...") the way body docstrings already are. Holds for any long signature.
2. When the undefined-name check finds a name that is a parameter in the specifier's sentence (or any name not
   defined anywhere), say "declare it as a parameter of <def>" instead of "import it".
3. `missing` must resolve to a real code name; otherwise answer that it is not code and re-show the requested lines
   (or reject), never add a pseudo-place that loops.
4. Plan: when a place gains a parameter/attribute and another planned place reads it, add the callers that connect them
   (here: the function calling the changed callee with the app's attributes).
5. Requirements: when the statement names a new parameter, show the existing sibling parameter names of the same
   signature (naming convention) next to the name. Does not fully fix this task (statement name differs from test).
