"""Puts a stable copy of the scripts first on the import path, so that Python compiles them once per task instead of
once per call. The ADK writes the skill's files to a new temporary directory for every run_skill_script call, so the
11,000 lines of the scripts were compiled again in every call (0.28 s on our machine, 0.06 s from bytecode; more in the
scorer's sandbox; audit 10-26). The copy is named by the scripts' content: another build never shares it."""
import hashlib
import os
import shutil
import sys

if __name__ == '__main__':
    print('This is an internal module. Call scripts/step.py.')
    raise SystemExit(0)


def _stable():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        h = hashlib.sha1()
        for name in sorted(n for n in os.listdir(here) if n.endswith('.py')):
            with open(os.path.join(here, name), 'rb') as fh:
                h.update(name.encode() + b'\0' + fh.read() + b'\0')
        dst = os.path.join(os.environ.get('SWE_STATE_ROOT', '/tmp'), 'swe_code_' + h.hexdigest()[:12])
        if not os.path.isdir(dst):
            tmp = f'{dst}.{os.getpid()}.tmp'
            shutil.copytree(here, tmp, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            try:
                os.rename(tmp, dst)
            except OSError:                 # another call made it first
                shutil.rmtree(tmp, ignore_errors=True)
    except OSError:
        return                              # (the scripts run from where they are)
    sys.path.insert(0, dst)
    sys.dont_write_bytecode = False


_stable()
