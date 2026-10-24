"""judge.py: the verdict of a patch on its task's hidden tests, independent of the environment's own failures.

The harness's verdict (swegemma verify_task) is "pytest exits 0 on the test files of the test patch, and the JUnit
report shows every test function the test patch adds or changes passed". In the dev environment some of those files
fail on the original code too (V21: requests' TLS/network tests, rich's pygments-dependent ANSI output), so a correct
patch was reported "not resolved" and a task could not be scored at all. The judge runs the same verification
(verify_task, same sandbox setup) several times, the way SWE-bench defines its tests:

  - with the reference patch, twice: the tests that pass in both runs are the ones a fix can be asked to pass;
  - with no patch, twice:            FAIL_TO_PASS = passing with the reference, not passing here; the rest that
                                     passes with the reference is PASS_TO_PASS;
  - a test whose outcome differs between the two runs of the same code is "flaky" and left out of both sets;
  - with the agent's patch:          resolved = it ran to the end (applied, pytest printed its result line), every
                                     FAIL_TO_PASS and PASS_TO_PASS test passed, and every test function the test patch
                                     adds or changes (the harness's own _extract_test_functions_from_patch, matched as
                                     the harness matches it) is present and passed, not skipped, except the functions
                                     that do not pass with the reference either. When the first agent run fails on
                                     tests, it is run once more and only the tests failing in both runs count.

Per-test outcomes come from the JUnit XML pytest writes in verify_task (install_capture() wraps the harness's
sandbox_exec to read it in the same sandbox right after pytest); the FAILED/ERROR lines of pytest's short test summary
are the fallback when no report exists. A patch that does not apply, a run without pytest's result line, and an empty
patch are "not resolved"; a reference run that does not finish, or where no test passes, makes the task "unscorable".

Two verdicts per patch: 'verdict' (judged against the reference, above) and 'official_feasible' (the reference patch
itself meets the harness's own criterion here, in both runs: when it does not, no patch can score officially in an
environment like this one, and 'verdict' is the only usable signal). When the reference fixes no test here
(FAIL_TO_PASS empty: the issue's tests pass on the original code too), the patch is still judged on the tests that
pass with the reference, and 'no_f2p' flags it.

The reference and no-patch runs are cached per task (JSON in the cache directory), keyed by the task's patch and test
patch and fingerprinted by the environment (python, key package versions in the sandbox, eval_env on or off, the
sandbox kind, the command timeout, this file's schema version); a mismatch recomputes them.

Used by tools/local_eval.py and tools/kaggle_eval.py after each task, and on its own for patches already made:

  python tools/judge.py --data <competition data dir> --patches <dir with patch_<task>.diff> [--tasks a,b]
      [--cache <dir>] [--work <dir>] [--no-eval-env] [--json <out.json>]
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import re
import shlex
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

SCHEMA = 2
KEY_PACKAGES = ('pytest', 'starlette', 'pydantic', 'pygments', 'rich', 'httpx', 'requests', 'urllib3', 'anyio',
                'httpcore', 'markdown-it-py', 'certifi', 'idna', 'swegemma')
# errors of verify_task that mean the tests did not run (the patch did not apply, the sandbox failed, ...)
INFRA_ERRORS = ('Failed to apply', 'Evaluation error', 'Missing test specification', 'Missing JUnit XML')
JUNIT_ARG = re.compile(r'--junitxml=(\S+)')
SUMMARY_HEADER = re.compile(r'^=+ short test summary info =+$')
RESULT_LINE = re.compile(r'^=*\s*(?:no tests ran|(?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|'
                         r'warnings?|rerun)(?:, )?)+)(?: in [\d.]+s.*)?\s*=*$')
FAILED_LINE = re.compile(r'^(?:FAILED|ERROR) (.+?)(?: - .*)?$')
PASSED = 'passed'


# ---------------------------------------------------------------------------------------------------- JUnit capture

_lock = threading.Lock()
_captures: list[dict] = []
# which run a capture belongs to: with several tasks at once (kaggle_eval --concurrency) the last report after a mark
# could be another task's, so each mark sets a tag in its context (asyncio tasks and to_thread copy it) and only the
# reports of that tag count
_tag: contextvars.ContextVar[int | None] = contextvars.ContextVar('judge_capture_tag', default=None)
_next_tag = [0]


def install_capture() -> None:
    """Wrap swegemma.harness.verification.sandbox_exec: after the pytest command (it has --junitxml=<path>), read the
    report in the same sandbox and record it with the command's exit code and stdout. Idempotent."""
    import swegemma.harness.verification as hv

    if getattr(hv, '_judge_capture_installed', False):
        return
    original = hv.sandbox_exec

    async def sandbox_exec(docker, sandbox_id, command, *args, **kwargs):
        res = await original(docker, sandbox_id, command, *args, **kwargs)
        m = JUNIT_ARG.search(command or '') if 'pytest' in (command or '') else None
        if m:
            xml = ''
            try:
                x = await original(docker, sandbox_id, f'cat {shlex.quote(m.group(1))} 2>/dev/null || true')
                xml = x.stdout or ''
            except Exception as e:  # noqa: BLE001
                xml = ''
                print(f'judge: could not read the JUnit report: {type(e).__name__}: {e}', flush=True)
            with _lock:
                _captures.append({'xml': xml, 'exit': res.exit_code, 'stdout': res.stdout or '', 'tag': _tag.get()})
        return res

    hv.sandbox_exec = sandbox_exec
    hv._judge_capture_installed = True


def capture_mark() -> tuple[int, int]:
    """A mark to pass to judge() (capture_mark=) for a verify_task run that starts after this call, in this context
    (the same asyncio task, or code it awaits or starts)."""
    with _lock:
        _next_tag[0] += 1
        tag = _next_tag[0]
        _tag.set(tag)
        return len(_captures), tag


def _captured_since(mark: tuple[int, int]) -> dict | None:
    start, tag = mark
    with _lock:
        new = [c for c in _captures[start:] if c.get('tag') == tag]
    return new[-1] if new else None


# ------------------------------------------------------------------------------------------------- parsing outcomes

def _file_for(dotted: str, test_files: list[str]) -> str | None:
    """The test file whose dotted path is `dotted`, or ends with it: pytest's ids are relative to its rootdir, which
    is a subdirectory when the repository has a config there (rich: tests/pytest.ini, so 'test_segment')."""
    for f in test_files:
        if f.endswith('.py'):
            d = f[:-3].replace('/', '.')
            if d == dotted or d.endswith('.' + dotted):
                return f
    return None


def nodeid(classname: str, name: str, test_files: list[str]) -> str:
    """A JUnit testcase as a node id relative to the workspace (as pytest's summary prints it). pytest writes
    classname = the rootdir-relative node id's path with '/' -> '.' and '.py' dropped, plus the classes, and name =
    the function with its parameters; a collection error has no classname and the module as name."""
    if not classname:
        return _file_for(name, test_files) or name.replace('.', '/') + '.py'
    parts = classname.split('.')
    for i in range(len(parts), 0, -1):
        f = _file_for('.'.join(parts[:i]), test_files)
        if f:
            return '::'.join([f] + parts[i:] + [name])
    # not a file of the test patch: the module ends at the last part that looks like a test module
    idx = max((i for i, p in enumerate(parts) if p.startswith('test') or p.endswith('_test')), default=len(parts) - 1)
    return '::'.join(['/'.join(parts[:idx + 1]) + '.py'] + parts[idx + 1:] + [name])


def parse_junit(xml: str) -> list[list[str]] | None:
    """[[classname, name, outcome], ...] with outcome passed / failed / error / skipped; None if not a report."""
    if not xml or not xml.strip():
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    if root.tag != 'testsuite' and root.find('.//testsuite') is None:
        return None
    cases = []
    for tc in root.iter('testcase'):
        if tc.find('failure') is not None:
            o = 'failed'
        elif tc.find('error') is not None:
            o = 'error'
        elif tc.find('skipped') is not None:
            o = 'skipped'
        else:
            o = PASSED
        cases.append([tc.attrib.get('classname', ''), tc.attrib.get('name', ''), o])
    return cases


def _summary_lines(stdout: str) -> list[str]:
    """The lines of pytest's short test summary section (not the captured output above it)."""
    lines = (stdout or '').splitlines()
    out, inside = [], False
    for ln in lines:
        s = ln.strip()
        if SUMMARY_HEADER.match(s):
            inside = True
            continue
        if inside:
            if RESULT_LINE.match(s) or (s.startswith('=') and s.endswith('=')):
                inside = False
                continue
            out.append(s)
    return out


def failures(test_output: str) -> set[str]:
    """Fallback: the ids (or files, for a collection error) the short test summary names as FAILED or ERROR."""
    return {m.group(1) for m in (FAILED_LINE.match(l) for l in _summary_lines(test_output)) if m}


def result_line(stdout: str) -> str:
    """pytest's final result line ('3 failed, 10 passed in 1.2s', 'no tests ran in 0.01s'), or ''."""
    for ln in reversed((stdout or '').splitlines()):
        s = ln.strip()
        if s and RESULT_LINE.match(s) and re.search(r'\d+ (?:passed|failed|errors?|skipped)|no tests ran', s):
            return s
    return ''


def _run_record(result, cap: dict | None, secs: float) -> dict:
    """What the judge keeps of one verify_task run."""
    error = getattr(result, 'error', None) or getattr(result, 'error_message', None) or ''
    stdout = cap['stdout'] if cap else (result.test_output or '')
    cases = parse_junit(cap['xml']) if cap else None
    line = result_line(stdout)
    ran = bool(cap) and (cases is not None or bool(line)) and not str(error).startswith(INFRA_ERRORS)
    rec = {'exit': result.test_exit_code, 'harness_resolved': bool(result.resolved), 'error': str(error)[:500],
           'ran': ran, 'result_line': line, 'source': 'xml' if cases is not None else ('stdout' if ran else None),
           'secs': round(secs, 1)}
    if cases is not None:
        rec['cases'] = cases
    elif ran:
        rec['failures'] = sorted(failures(stdout))
    return rec


def outcomes(rec: dict, test_files: list[str], universe: set[str] = frozenset()) -> dict[str, str]:
    """node id -> outcome for one run. A test file with a collection error gives 'error' to its tests; in a run
    without a report, every test of the universe not named as failed is taken as passed."""
    out: dict[str, str] = {}
    rank = {PASSED: 0, 'skipped': 1, 'failed': 2, 'error': 2}
    if rec.get('cases') is not None:
        for cls, name, o in rec['cases']:
            nid = nodeid(cls, name, test_files)
            if nid not in out or rank[o] > rank[out[nid]]:
                out[nid] = o
        return out
    if rec.get('ran'):
        failed = set(rec.get('failures') or [])
        for t in universe:
            out[t] = 'failed' if t in failed or t.split('::', 1)[0] in failed else PASSED
        for f in failed:
            out.setdefault(f, 'failed')
    return out


def outcome(oc: dict[str, str], test: str) -> str:
    """A test's outcome in a run: its own, the error of its file's collection, or 'missing'."""
    if test in oc:
        return oc[test]
    f = test.split('::', 1)[0]
    if f != test and oc.get(f) in ('error', 'failed'):
        return 'error'
    return 'missing'


# -------------------------------------------------------------------------- the harness's requirement, per function

def required_functions(task) -> list[str]:
    from swegemma.harness.verification import _extract_test_functions_from_patch

    nodes = list(getattr(task, 'FAIL_TO_PASS', ()) or ()) + list(getattr(task, 'PASS_TO_PASS', ()) or ())
    return nodes or _extract_test_functions_from_patch(task.test_patch or '')


def _names(rec: dict, test_files: list[str], universe: set[str] = frozenset()) -> tuple[set[str], set[str]]:
    """The harness's candidate names of the passed and of the not passed testcases (as in _validate_junit_xml)."""
    passed, other = set(), set()
    if rec.get('cases') is not None:
        for cls, name, o in rec['cases']:
            cand = {name}
            if '[' in name:
                cand.add(name.split('[', 1)[0])
            if cls:
                cand |= {f'{cls}::{name}', f'{cls}.{name}', f'{cls.replace(".", "/")}.py::{name}'}
            (passed if o == PASSED else other).update(cand)
    else:
        for nid, o in outcomes(rec, test_files, universe).items():
            last = nid.rsplit('::', 1)[-1]
            (passed if o == PASSED else other).update({nid, last, last.split('[', 1)[0]})
    return passed, other


def requirement_ok(rec: dict, test_files: list[str], req: str, universe: set[str] = frozenset()) -> bool:
    """The harness's check for one required node: some matching testcase passed and none failed or was skipped."""
    from swegemma.harness.verification import _node_matches_requirement

    if not rec.get('ran'):
        return False
    passed, other = _names(rec, test_files, universe)
    short = req.split('::')[-1]
    base = short.split('[', 1)[0]
    has_pass = any(_node_matches_requirement(p, req, short, base) for p in passed)
    has_other = any(_node_matches_requirement(p, req, short, base) for p in other)
    return has_pass and not has_other


# -------------------------------------------------------------------------------------------- running verify_task

def test_files_of(task) -> list[str]:
    from swegemma.models import extract_test_files_from_patch

    return list(task.test_files) if getattr(task, 'test_files', None) else extract_test_files_from_patch(
        task.test_patch)


async def _verify_async(evaluator, task, patch: str):
    from swegemma.deduplication import resolve_task_snapshot_paths
    from swegemma.harness.verification import verify_task

    snap, base, ppath = resolve_task_snapshot_paths(evaluator.config.snapshots_dir, task.instance_id, task.repo)
    return await verify_task(evaluator.docker, evaluator.config, task, snap, base_snapshot_path=base,
                             patch_path=ppath, agent_patch=patch, start_time=0.0)


def run_verify(evaluator, task, patch: str) -> dict:
    """One verify_task run of the patch, as a judge record (with the JUnit report's per-test outcomes)."""
    install_capture()
    mark = capture_mark()
    t0 = time.time()
    r = asyncio.run(_verify_async(evaluator, task, patch))
    return _run_record(r, _captured_since(mark), time.time() - t0)


# ------------------------------------------------------------------------------------- environment and cache key

_ENV_CACHE: dict = {}
_PROBE = ('import importlib.metadata as m, json, sys\n'
          'v = {}\n'
          'for p in sys.argv[1:]:\n'
          '    try: v[p] = m.version(p)\n'
          '    except Exception: v[p] = None\n'
          'print("JUDGE_PROBE " + json.dumps({"python": sys.version.split()[0], "packages": v}))\n')


def sandbox_run(evaluator, command: str, timeout: int = 300):
    """Run a command in a fresh sandbox of the evaluator (empty workspace) and stop it."""
    from swegemma.sandbox import sandbox_exec, sandbox_start, sandbox_stop

    async def go():
        sid = await sandbox_start(evaluator.docker)
        try:
            return await sandbox_exec(evaluator.docker, sid, command, timeout=timeout)
        finally:
            await sandbox_stop(evaluator.docker, sid)

    return asyncio.run(go())


def sandbox_packages(evaluator) -> dict:
    """Python version and versions of the key packages importable by the sandbox's python (probed once)."""
    key = id(evaluator)
    if key not in _ENV_CACHE:
        info = None
        try:
            r = sandbox_run(evaluator, f'python3 -c {shlex.quote(_PROBE)} {" ".join(KEY_PACKAGES)}', timeout=120)
            line = next((l for l in (r.stdout or '').splitlines() if l.startswith('JUDGE_PROBE ')), '')
            info = json.loads(line[len('JUDGE_PROBE '):]) if line else None
        except Exception as e:  # noqa: BLE001
            print(f'judge: sandbox probe failed ({type(e).__name__}: {e}); using this process', flush=True)
        if info is None:
            import importlib.metadata as md
            import sys
            v = {}
            for p in KEY_PACKAGES:
                try:
                    v[p] = md.version(p)
                except Exception:  # noqa: BLE001
                    v[p] = None
            info = {'python': sys.version.split()[0], 'packages': v, 'probe': 'host'}
        _ENV_CACHE[key] = info
    return _ENV_CACHE[key]


def fingerprint(evaluator) -> dict:
    try:
        import eval_env
        on = eval_env.active()
    except ImportError:
        import swegemma.sandbox.subprocess as sp
        on = bool(getattr(sp, '_src_layout_patched', False))
    cfg = evaluator.config
    return {'schema': SCHEMA, 'env': sandbox_packages(evaluator), 'eval_env': on,
            'sandbox': str(getattr(cfg, 'sandbox', '')),
            'timeout': getattr(getattr(cfg, 'harness', None), 'command_timeout_seconds', None) or cfg.timeout_seconds}


def task_key(task) -> str:
    h = hashlib.sha256()
    h.update((task.instance_id + '\0' + (task.patch or '') + '\0' + (task.test_patch or '')).encode('utf-8'))
    return h.hexdigest()


def baseline(evaluator, task, cache: Path | None) -> dict:
    """The reference and no-patch runs (two each), from the cache when its key and fingerprint match."""
    fp = fingerprint(evaluator)
    key = task_key(task)
    path = cache / f'{task.instance_id}.json' if cache else None
    if path is not None and path.exists():
        try:
            d = json.loads(path.read_text(encoding='utf-8'))
            if d.get('key') == key and d.get('fingerprint') == fp and all(r.get('ran') for r in d['ref']):
                return d
            print(f'judge: cache of {task.instance_id} is stale (key or environment changed, or a run did not finish); recomputing', flush=True)
        except (ValueError, OSError):
            pass
    d = {'key': key, 'fingerprint': fp,
         'ref': [run_verify(evaluator, task, task.patch) for _ in range(2)],
         'orig': [run_verify(evaluator, task, '') for _ in range(2)]}
    if path is not None and all(r.get('ran') for r in d['ref']):
        # a run that did not finish (a missing snapshot, a sandbox fault) is not cached: it would leave the task
        # unscorable on every later judgement (gm11c-gm11f: a snapshot missing once)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(d, indent=1), encoding='utf-8')
    return d


# ------------------------------------------------------------------------------------------------------- verdict

def _problems(rec: dict, f2p, p2p, reqs, files, universe) -> dict:
    oc = outcomes(rec, files, universe)
    return {'f2p_failing': [t for t in f2p if outcome(oc, t) != PASSED],
            'p2p_broken': [t for t in p2p if outcome(oc, t) != PASSED],
            'required_failing': [r for r in reqs if not requirement_ok(rec, files, r, universe)]}


def judge(evaluator, task, agent_patch: str, harness_result=None, cache: Path | None = None,
          capture_mark: tuple[int, int] | None = None) -> dict:
    """{'verdict': 'resolved' | 'not resolved' | 'unscorable', 'official_feasible': bool, 'f2p': [...],
    'f2p_failing': [...], 'p2p_broken': [...], 'required_failing': [...], 'flaky': [...], 'env_failures': [...],
    'no_f2p': bool, 'why': str, ...}.

    harness_result (the TaskResult of the run) is used as the first agent run when given; pass capture_mark (from
    capture_mark() taken before evaluate_task, with install_capture() in force) so its JUnit report is used too."""
    install_capture()
    files = test_files_of(task)
    base = baseline(evaluator, task, cache)
    refs, origs = base['ref'], base['orig']
    out: dict = {'official_feasible': all(r['harness_resolved'] for r in refs),
                 'ref_exit': [r['exit'] for r in refs], 'orig_exit': [r['exit'] for r in origs],
                 'f2p': [], 'env_failures': [], 'flaky': [], 'no_f2p': False}

    # the universe of tests: every node id any baseline run reported
    universe: set[str] = set()
    for r in refs + origs:
        universe |= set(outcomes(r, files))
    ref_oc = [outcomes(r, files, universe) for r in refs]
    orig_oc = [outcomes(r, files, universe) for r in origs]
    flaky = sorted(t for t in universe
                   if len({outcome(o, t) for o in ref_oc}) > 1 or len({outcome(o, t) for o in orig_oc}) > 1)
    fl = set(flaky)
    passing_ref = sorted(t for t in universe if t not in fl and all(outcome(o, t) == PASSED for o in ref_oc))
    f2p = [t for t in passing_ref if all(outcome(o, t) != PASSED for o in orig_oc)]
    p2p = [t for t in passing_ref if t not in set(f2p)]
    reqs_all = required_functions(task)
    req_ref = {q: [requirement_ok(r, files, q, universe) for r in refs] for q in reqs_all}
    reqs = [q for q, oks in req_ref.items() if all(oks)]
    out.update(f2p=f2p, flaky=flaky, n_p2p=len(p2p),
               env_failures=sorted(t for t in universe if t not in fl
                                   and any(outcome(o, t) in ('failed', 'error') for o in ref_oc)),
               skipped_with_ref=len([t for t in universe if t not in fl and outcome(ref_oc[0], t) == 'skipped']),
               required=reqs_all, required_not_passing_with_ref=[q for q, oks in req_ref.items() if not all(oks)])

    if not all(r['ran'] for r in refs):
        out['verdict'] = 'unscorable'
        out['why'] = 'the reference run did not finish: ' + '; '.join(r['error'][:150] for r in refs if not r['ran'])
        return out
    if not passing_ref:
        out['verdict'] = 'unscorable'
        out['why'] = 'no test passes with the reference in this environment'
        return out
    if not f2p:
        # fastapi_14851: the issue's tests already pass on the original code here (the change prepares for a later
        # Starlette), so nothing turns from failing to passing; a patch is still judged by the tests that pass with
        # the reference, as the harness would
        out['no_f2p'] = True
        out['why'] = 'no FAIL_TO_PASS test in this environment: judged on the tests that pass with the reference'

    if not (agent_patch or '').strip():
        out.update(verdict='not resolved', agent_exit=None, f2p_failing=list(f2p), p2p_broken=[],
                   required_failing=list(reqs), why=(out.get('why', '') + '; ' if out.get('why') else '')
                   + 'empty patch')
        return out

    if harness_result is not None:
        cap = _captured_since(capture_mark) if capture_mark is not None else None
        first = _run_record(harness_result, cap, getattr(harness_result, 'duration_seconds', 0.0) or 0.0)
        if cap is None:
            # no report of the harness's own run: its stdout is the only source (the ran flag needs the result line)
            first['ran'] = bool(first['result_line']) and not first['error'].startswith(INFRA_ERRORS)
            first['source'] = 'stdout' if first['ran'] else None
            if first['ran']:
                first['failures'] = sorted(failures(harness_result.test_output or ''))
    else:
        first = run_verify(evaluator, task, agent_patch)
    out['agent_exit'] = first['exit']
    out['agent_runs'] = 1
    if not first['ran']:
        out.update(verdict='not resolved', f2p_failing=list(f2p), p2p_broken=[], required_failing=list(reqs))
        out['why'] = (f'the tests did not run to the end (exit {first["exit"]}'
                      f'{": " + first["error"][:200] if first["error"] else ", no pytest result line"})')
        return out
    probs = _problems(first, f2p, p2p, reqs, files, universe)
    if any(probs.values()):
        # a test may fail once by chance (timeouts, ports): only what fails in both runs counts
        second = run_verify(evaluator, task, agent_patch)
        out['agent_runs'] = 2
        if second['ran']:
            p2 = _problems(second, f2p, p2p, reqs, files, universe)
            probs = {k: [t for t in v if t in set(p2[k])] for k, v in probs.items()}
    out.update(probs)
    out['verdict'] = 'not resolved' if any(probs.values()) else 'resolved'
    return out


def short(v: dict) -> str:
    """One line for logs."""
    return (f'{v["verdict"]} official_feasible={v["official_feasible"]} f2p={len(v["f2p"])} '
            f'f2p_failing={v.get("f2p_failing", [])[:5]} p2p_broken={v.get("p2p_broken", [])[:5]} '
            f'required_failing={v.get("required_failing", [])[:5]} flaky={len(v.get("flaky", []))} '
            f'env_failures={len(v.get("env_failures", []))} runs={v.get("agent_runs", 0)} {v.get("why", "")}')


# ------------------------------------------------------------------------------------------------- screening

def screen_order(tasks, seed: int = 1) -> list:
    """Every task in a deterministic order in which each repository keeps its share at every prefix (each
    repository's tasks shuffled with the seed, then a weighted round robin): the first n that pass screen() are a
    stratified sample."""
    import random

    by: dict[str, list] = {}
    for t in sorted(tasks, key=lambda t: t.instance_id):
        by.setdefault(t.repo, []).append(t)
    rng = random.Random(seed)
    for repo in sorted(by):
        rng.shuffle(by[repo])
    total = sum(len(v) for v in by.values())
    taken = {r: 0 for r in by}
    out = []
    while len(out) < total:
        r = min((r for r in sorted(by) if taken[r] < len(by[r])), key=lambda r: (taken[r] + 1) / len(by[r]) * total)
        out.append(by[r][taken[r]])
        taken[r] += 1
    return out


def unscorable_reason(v: dict) -> str:
    """'' when the judgement of the empty patch shows the task can be scored in this environment, else why not: the
    reference runs and passes the harness's own whole-file check, and some test fails without it (FAIL_TO_PASS)."""
    if v['verdict'] == 'unscorable':
        return v.get('why', 'unscorable')
    if not v['f2p']:
        return 'no FAIL_TO_PASS test here'
    if not v['official_feasible']:
        return "the reference patch does not pass the harness's whole-file check here"
    return ''


def screen(evaluator, candidates: list, n: int, cache: Path | None, workers: int = 4, prepare=None, discard=None,
           log=print) -> tuple[list, list[dict]]:
    """The first n candidates (in order) that can be scored here, by judging the empty patch: the reference and
    no-patch runs it makes are cached, so judging the agent's patch later reuses them. prepare(task) runs before a
    candidate's runs (a snapshot download), discard(task) after a candidate not chosen."""
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            if prepare is not None:
                prepare(t)
            v = judge(evaluator, t, '', cache=cache)
            return unscorable_reason(v), v
        except Exception as e:  # noqa: BLE001
            return f'screen error: {type(e).__name__}: {e}'[:300], None

    chosen, rows, i = [], [], 0
    with ThreadPoolExecutor(max(1, workers)) as ex:
        while len(chosen) < n and i < len(candidates):
            wave = candidates[i:i + max(1, workers)]
            i += len(wave)
            for t, (why, v) in zip(wave, ex.map(one, wave)):
                ok = not why and len(chosen) < n
                rows.append({'task_id': t.instance_id, 'repo': t.repo, 'chosen': int(ok), 'why': why,
                             'f2p': len(v['f2p']) if v else '', 'official_feasible': int(bool(v and v['official_feasible']))})
                if ok:
                    chosen.append(t)
                elif discard is not None:
                    discard(t)
                log(f'SCREEN {t.instance_id}: {"chosen" if ok else "left out"} ({why or "scorable"}) '
                    f'[{len(chosen)}/{n} after {i} candidates]', flush=True)
    return chosen, rows


def make_evaluator(data: Path, work: Path, timeout: int = 600):
    """An Evaluator with the subprocess sandbox, for judging outside an agent run."""
    from adk_submission.registry import ModelRegistry
    from swegemma.config import EvalConfig, build_submission_limits
    from swegemma.evaluate import Evaluator

    limits, constraints = build_submission_limits()
    work.mkdir(parents=True, exist_ok=True)
    (work / 'agent').mkdir(exist_ok=True)
    (work / 'agent' / 'agent.yaml').write_text('name: judge\n', encoding='utf-8')
    config = EvalConfig(tasks_path=data / 'tasks.jsonl', snapshots_dir=data / 'snapshots',
                        results_dir=work / 'results', submission_dir=work / 'agent', models=ModelRegistry(),
                        sandbox='subprocess', timeout_seconds=timeout, limits=limits,
                        generation_constraints=constraints, wheels_dir=data / 'wheels')
    return Evaluator(config)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', type=Path, required=True, help='tasks.jsonl, snapshots/, wheels/')
    ap.add_argument('--patches', type=Path, required=True, help='a directory of patch_<task>.diff')
    ap.add_argument('--tasks', default='', help='comma-separated ids (default: every patch in --patches)')
    ap.add_argument('--cache', type=Path, default=None, help='reference/no-patch results per task')
    ap.add_argument('--work', type=Path, default=Path('judge_work'))
    ap.add_argument('--timeout', type=int, default=600, help='the per-command timeout (eval_config timeout_seconds)')
    ap.add_argument('--no-eval-env', action='store_true', help='do not import the workspace src/ (eval_env.py)')
    ap.add_argument('--json', type=Path, default=None, help='write every verdict here')
    args = ap.parse_args()

    import os
    import sys
    os.environ.setdefault('OTEL_SDK_DISABLED', 'true')
    os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if not args.no_eval_env:
        import eval_env
        eval_env.patch()
    install_capture()
    from swegemma.models import load_tasks

    ids = [x.strip() for x in args.tasks.split(',') if x.strip()] or sorted(
        p.stem[len('patch_'):] for p in args.patches.glob('patch_*.diff'))
    tasks = [t for t in load_tasks(args.data / 'tasks.jsonl') if t.instance_id in ids]
    evaluator = make_evaluator(args.data, args.work, args.timeout)
    rows = []
    for t in tasks:
        patch = (args.patches / f'patch_{t.instance_id}.diff').read_text(encoding='utf-8')
        v = judge(evaluator, t, patch, cache=args.cache)
        rows.append((t.instance_id, v))
        print(f'{t.instance_id}: {short(v)}', flush=True)
    if args.json:
        args.json.write_text(json.dumps(dict(rows), indent=1), encoding='utf-8')
    n = sum(v['verdict'] == 'resolved' for _, v in rows)
    k = sum(v['verdict'] != 'unscorable' for _, v in rows)
    feas = sum(bool(v['official_feasible']) for _, v in rows)
    print(f'JUDGED: {n}/{k} resolved ({len(rows) - k} unscorable here); '
          f'OFFICIAL_FEASIBLE: {feas}/{len(rows)} tasks whose reference passes the harness here')


if __name__ == '__main__':
    main()
