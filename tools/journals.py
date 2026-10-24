"""The skill's own journal of a task, found and copied next to the task's trace.

The harness's trace (ATIF) keeps one answer when parallel workers call the script at the same moment: in h13 (docs/
design_fast.md, "the 25 refused calls") 122 of 600 tool answers were missing, and the answers left were read as the
other worker's. The skill writes every call and its whole answer to <state root>/swe_state_<tag>/events.jsonl
(_state.record); the eval tools copy that file to <out>/journal_<task>.jsonl after each task.

A journal is matched to a task by its calls: the run_skill_script calls of the trace (file_path, args) and the
journal's calls must agree on the first ones. Only journals written since the task started are read, so tasks run
at once (--concurrency) do not mix.

    python tools/journals.py <trace.json> <out.jsonl> [--since <epoch seconds>] [--root /tmp]
"""
import argparse
import glob
import json
import os
import shutil

FIRST = 4          # calls compared


def trace_calls(trace_path):
    """(file name, args) of the trace's run_skill_script calls, in order."""
    try:
        steps = json.load(open(trace_path, encoding='utf-8')).get('steps', [])
    except (OSError, ValueError):
        return []
    out = []
    for s in steps:
        for tc in s.get('tool_calls') or []:
            a = tc.get('arguments') or {}
            if tc.get('function_name') == 'run_skill_script' and isinstance(a, dict) and isinstance(a.get('args'), list):
                out.append((os.path.basename(str(a.get('file_path') or '')), [str(x)[:300] for x in a['args']]))
    return out


def journal_calls(path):
    out = []
    try:
        for line in open(path, encoding='utf-8'):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if 'stage' in e and isinstance(e.get('args'), list):
                out.append([str(x)[:300] for x in e['args']])
    except OSError:
        pass
    return out


def find(trace_path, since=0.0, root='/tmp'):
    """The journal of the task whose trace this is, or None."""
    want = [args for _, args in trace_calls(trace_path)][:FIRST]
    if not want:
        return None
    best = None
    for path in glob.glob(os.path.join(root, 'swe_state_*', 'events.jsonl')):
        try:
            if os.path.getmtime(path) < since:
                continue
        except OSError:
            continue
        got = journal_calls(path)
        # parallel workers' calls can reach the journal in another order than the trace lists them
        n = sum(1 for a in want if a in got[:FIRST * 3])
        if n == len(want) and (best is None or n > best[0]):
            best = (n, path)
    return best[1] if best else None


def copy(trace_path, dest, since=0.0, root='/tmp'):
    """Copies the task's journal to dest; returns the source path or None."""
    src = find(trace_path, since, root)
    if src:
        shutil.copyfile(src, dest)
    return src


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('trace')
    ap.add_argument('dest')
    ap.add_argument('--since', type=float, default=0.0)
    ap.add_argument('--root', default='/tmp')
    a = ap.parse_args()
    print(copy(a.trace, a.dest, a.since, a.root) or 'no journal found')
