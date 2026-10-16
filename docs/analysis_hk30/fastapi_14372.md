# fastapi_14372: not resolved (66.9 s, 11 calls)

## Reference vs agent
- Reference: `@dataclass(frozen=True)` on both `params.Depends` and its subclass `params.Security`, and
  `analyze_param` uses `dataclasses.replace` instead of mutating the copy.
- Agent: `@dataclass(unsafe_hash=True)` on `Depends` only. `Security` keeps a plain `@dataclass` (eq=True), which sets
  `Security.__hash__ = None` again, so `hash(Security(dep))` raises "TypeError: unhashable". The agent's approach would
  have passed had `Security` got the same decorator (unsafe_hash keeps `analyze_param`'s mutation legal).

## First wrong stage: locate
The requirement was right and named both classes: "R1: Depends(...) and Security(...) return objects that can be
hashed". req.py had shown both classes ("Depends: class Depends (fastapi/params.py)", "Security: class
Security(Depends) (fastapi/params.py)"), and locate's list opened with "fastapi/params.py::Security (fastapi/params.py,
class) class Security(Depends)". The model sent only `['class Depends']` and the stage ended: "locate is done: R1 is
decided at: Depends." Security was never in the plan.

## Was the information in the statement?
Yes. The title names both `Depends()` and `Security()` and asks for hashability; equal-field instances hashing equal
follows from dataclass semantics.

## Did the script's answers contribute?
Yes, as missing safety nets around a model slip:
- locate accepted one place for a requirement that names two repository classes, with no note that `Security`, named in
  R1 and defined in the repository, is not chosen.
- The plan's related code for `Depends` listed only its caller in `param_functions.py`, not the subclass
  `Security(Depends)`, although a subclass re-applies the class decorator and redefines dunder methods.
- Review saw "Other code near these places: Depends." (again not Security), answered "cannot tell", and "cannot tell"
  routes straight to submit_patch. No reproduction ran (skipped for time) and the edit was "Kept, not checked".

## Generic fix
1. Locate (or the plan) checks coverage of the requirement's own names: every repository definition the requirement
   names (req.py already resolves them) that is neither located nor in the plan is listed when locate ends ("R1 also
   names X (file), not chosen: add it or say why not").
2. Related code of a located class includes its subclasses in the repository (they inherit or re-derive its behaviour).
3. Review shows the requirement's named definitions not touched by the patch; "cannot tell" with such a gap goes back
   to specify for that name instead of to submit.
