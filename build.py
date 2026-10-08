"""build.py <out dir>: assemble the submissions that run in the harness into <out dir>/single and <out dir>/pipeline.

The skill scripts live once, in submission/skills/swe/scripts; every skill of single/ and pipeline/ gets a copy of
them next to its own SKILL.md and assets/procedure.json (which says the stage and the scripts it may run). Python
caches are left out: a .pyc file makes the harness reject the submission.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(ROOT, 'submission', 'skills', 'swe', 'scripts')
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', 'scripts')


def build(name, out):
    dst = os.path.join(out, name)
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, name), dst, ignore=IGNORE)
    skills = os.path.join(dst, 'skills')
    for skill in sorted(os.listdir(skills)) if os.path.isdir(skills) else []:
        shutil.copytree(SCRIPTS, os.path.join(skills, skill, 'scripts'), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return dst


if __name__ == '__main__':
    out = sys.argv[1]
    for name in ('single', 'pipeline'):
        print('built', build(name, out))
