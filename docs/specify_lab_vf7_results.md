vf7 (diagnosis call, 'R1 does not hold' framing, no change names its line; budget 1024)

| case | turns | decisions | faults | no call | grade | note |
|---|---|---|---|---|---|---|
| fastapi_14419 | 6 | diagnosis + sentence (cache_key), diagnosis + sentence (get_dependant), no change with its line | 0 | 0 | partial | the diagnosis of cache_key is the reference's ("always includes security_scopes regardless of whether ... use scopes"); the sentences key on empty scopes and lift sub-dependency scopes to the parent; vf6: empty in both samples. 5 decision turns of about 1,100 tokens |
| rich_3180 | 5 | diagnosis + sentence (chop_cells), diagnosis + sentence (divide_line) | 0 | 0 | wrong | the diagnosis cites the reversed loop, but the sentence keeps "the reversed processing order"; divide_line gets an unrelated change. Not empty (vf6 s1 empty, vf6 s2 right after the warning) |
| fastapi_14361 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | thoughts not cut (403 and 835 tokens) |
| rich_3905 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | not cut (329 and 679 tokens) |
| fastapi_5624 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | the diagnosis names the reference cause ("leak into others at the same level"); a new list each pass of the loop (vf6: partial) |
| fastapi_11194 (control) | 3 | diagnosis + sentence | 0 | 0 | correct | the diagnosis is the reference cause (the first field decides for all) |
