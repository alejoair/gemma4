# fastapi_14361: not resolved, empty patch (96.4 s, 18 calls)

## Reference vs agent
- Reference: in `fastapi/_compat/v2.py::_remap_definitions_and_field_mappings`, skip a field mapping whose schema has
  no "$ref" (`if model not in model_name_map or "$ref" not in schema: continue`) before `schema["$ref"].split(...)`.
- Agent: empty patch. The only place in the plan was `get_schema_from_model_field`, which already guards
  `if "$ref" not in json_schema`; specify answered "no change" three times; review said "not done" three times; the
  round limit then routed to submit_patch.

## First wrong stage: locate (a script route)
The BM25 list missed the function (it held `contextmanager_in_threadpool`, `UploadFile`, "fastapi/_compat/v2.py top
level (lines 1-405 ...)"). The model then asked to search: "I'll search for that text directly" with args `['$ref']`.
The script answered "R1 is decided at: get_schema_from_model_field." and closed the stage. Cause in code:
`locate` -> `step._resolve` treats a non-identifier fragment of 4+ chars as code and `_find_text` returns the FIRST
function containing it literally (file order), which in v2.py is `get_schema_from_model_field`, above
`_remap_definitions_and_field_mappings`. A bare word ("release" in fastapi_15661) gets a hit list instead; "$ref" did
not. The model never saw the other holders of "$ref".

## Was the information in the statement?
Yes, enough: 'Avoid accessing non-existing "$ref" key for Pydantic v2 compat remapping' gives the literal, the module
area (v2 compat) and the function ("remapping" ~ `_remap_definitions_and_field_mappings`); the fix is a missing-key
guard. The snapshot test needs no specific names beyond that.

## Did the script's answers contribute?
Yes, decisively:
- The text search silently became a single choice (first literal hit) and ended locate.
- After review "not done" on a place whose specifier said "no change", the loop went back to specify on the same place
  with the same view; specify cannot search or name unseen code, so it repeated "no change". Review can only name a
  place, not send the requirement back to locate.
- The last review message said "goes back to be specified and edited" while NEXT routed to submit_patch with an empty
  patch (contradictory text).
- Minor: BM25 did not match "remapping" to the identifier part "remap".

## Generic fix
1. A text sent to locate that is not a name lists every function/class holding it (as the word search does) and is
   chosen only when exactly one holds it.
2. When review says "not done" on a place the specifier left "no change" (or twice on the same place), route that
   requirement back to locate with the review's reason and the other holders of the requirement's literals.
3. Never route to submit with an empty patch while a requirement is "not done" and time remains; make the message
   match the route.
