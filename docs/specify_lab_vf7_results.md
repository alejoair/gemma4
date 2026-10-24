# vf7 specify acceptance runs (step_lab, local 31B, budget 1024)

### vf7 acceptance runs (budget 1024)

Sample 1:

| case | turns | decisions | faults | no call | grade | note |
|---|---|---|---|---|---|---|
| fastapi_14419 | 6 | diagnosis + sentence (cache_key), diagnosis + sentence (get_dependant), no change with its line | 0 | 0 | partial | the diagnosis of cache_key is the reference's ("always includes security_scopes regardless of whether ... use scopes"); the sentences key on empty scopes and lift sub-dependency scopes to the parent; vf6: empty in both samples. 5 decision turns of about 1,100 tokens |
| rich_3180 | 5 | diagnosis + sentence (chop_cells), diagnosis + sentence (divide_line) | 0 | 0 | wrong | the diagnosis cites the reversed loop, but the sentence keeps "the reversed processing order"; divide_line gets an unrelated change. Not empty (vf6 s1 empty, vf6 s2 right after the warning) |
| fastapi_14361 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | thoughts not cut (403 and 835 tokens) |
| rich_3905 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | not cut (329 and 679 tokens) |
| fastapi_5624 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | the diagnosis names the reference cause ("leak into others at the same level"); a new list each pass of the loop (vf6: partial) |
| fastapi_11194 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | the diagnosis is the reference cause (the first field decides for all) |
| rich_3043 | 3 | diagnosis + sentence | 0 | 0 | correct | <html> before <head>; the diagnosis came with literal \n and was accepted |
| rich_3934 | 4 | no change (its evidence was the def line, accepted: fixed after), diagnosis + sentence | 0 | 0 | wrong | adds an empty line to the render; the reference skips the line in Live.stop. Statement: two words |
| rich_3772 | 3 | diagnosis + sentence | 0 | 0 | correct (mechanism) | a seen set passed into the recursive extract calls: the reference's _visited_exceptions (vf6: partial) |
| rich_4076 | 3 | diagnosis + sentence | 0 | 0 | partial | splitlines(keepends=True), as in vf6 |

Sample 2:

| case | turns | decisions | faults | no call | grade | note |
|---|---|---|---|---|---|---|
| fastapi_14419 | 7 | no change × 3 (the evidence was cache_key's own faulty return, then signature lines) | 1 (signature lines were accepted as the evidence: fixed with the AST) | 0 | wrong (empty) | it repeated a refused call 4 times; the refusal limit and the empty-patch warning let it through |
| rich_3180 | 4 | no change on chop_cells (evidence: its return line), diagnosis + sentence on divide_line | 0 | 0 | wrong | the divide_line change does not touch the fault |
| rich_3043 | 3 | diagnosis + sentence | 0 | 0 | correct | the whole template with <html> first |
| rich_3934 | 8 | diagnosis + sentence (Live.stop), missing LiveRender.__init__, diagnosis + sentence × 2 | 0 | 0 | correct (mechanism) | Live.stop skips the line when nothing was rendered (a rendered flag): the reference's last_render_height check. 8 turns |
| rich_3772 | 3 | diagnosis + sentence | 0 | 0 | partial | a seen set checked before the recursive call, not passed into it |
| rich_4076 | 3 | diagnosis + sentence | 0 | 0 | partial | splitlines(keepends=True) again |
| fastapi_14361 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | not cut (707, 958 tokens) |
| rich_3905 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | not cut (396, 682 tokens) |
| fastapi_5624 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | the diagnosis names the leak to siblings; a new list on each pass |
| fastapi_11194 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | each field's field_info |
| fastapi_14419 (rerun with the signature fix) | 6 | diagnosis + sentence × 2, no change with a body line | 0 | 0 | partial | as in sample 1 |
