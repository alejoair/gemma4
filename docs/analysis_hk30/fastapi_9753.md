# fastapi_9753: partial() dependables (not resolved, 94 s, 13 calls). A near miss.

## 1. Reference vs agent
- Reference: `Dependant._unwrapped_call` (the shared helper) also unwraps `functools.partial` (`unwrapped.func`). All
  three predicates that read it then see through partials: `is_coroutine_callable`, `is_gen_callable` and
  `is_async_gen_callable`.
- Agent: unwraps partials only inside `is_coroutine_callable`. This fixes 10 of the 12 parametrizations. The two
  partial-of-generator-callable-class cases still fail (`/partial-callable-gen-dependency`,
  `/partial-async-callable-gen-dependency`), because `is_gen_callable`/`is_async_gen_callable` still read the raw partial.

## 2. First wrong stage: locate/plan (one place, where the decision is shared by three); specify could have recovered
- locate: no candidate fit. The model searched by name: `locate.py ["is_coroutine_callable"]`. That was a good move,
  but it chose the consumer, not the shared helper.
- plan: only `Dependant.is_coroutine_callable`. Its near-copies (`is_gen_callable`, `is_async_gen_callable`, the same
  shape: predicate on `self._unwrapped_call`, then on `__call__`) were not added as places.
- specify: the window showed `_unwrapped_call` under "Code that is_coroutine_callable uses", and the two siblings under
  "Other members of its class that use the same code (is_coroutine_callable does not call them)". The model wrote a
  sentence that unwraps the partial locally instead of `["missing", "Dependant._unwrapped_call", ...]`.
- review: "done" for both requirements. Review's "other code near" listed only callers (`solve_dependencies`,
  `get_request_handler`), not the siblings.

## 3. Was the information in the statement?
Yes. "makes partial() work consistently in all combinations" (async, callable classes, generators by implication).
An engineer who sees three predicates reading one `_unwrapped_call` would fix the helper.

## 4. Did the system contribute?
Yes.
- The plan adds identical copies, but not near-copies (siblings with the same structure that read the same helper).
- The specify view framed the siblings as "does not call them" and related code as "keeps its code unless you name it",
  which invites a local fix.
- The review view showed callers, not those siblings.
- No test ran after the edit ("Kept, not checked"), so no failing generator case surfaced.

## 5. Generic fix
- When the place's sentence changes how it treats a value from a helper it reads (`self._x`, a call), and other members
  read that same helper in the same pattern, specify asks one bounded question: "change the helper `X` (also read by A,
  B) instead? ['missing', 'X', ...] or keep".
- Also: plan near-copies, meaning same-class members with the same statement shape over the same attribute, as
  related places.
- Review lists those siblings under "code near".
