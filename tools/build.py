"""build.py <out dir> [validator]: the submission from agent/, without Python caches (a .pyc file makes the harness
reject it), as <out dir>/submission/ and <out dir>/submission.zip; then runs the competition's
validate_submission.py on the directory when its path is given."""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agent')
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache')


def build(out):
    dst = os.path.join(out, 'submission')
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(ROOT, dst, ignore=IGNORE)
    zpath = os.path.join(out, 'submission.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for d, _, files in os.walk(dst):
            for f in sorted(files):
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, dst))
    return dst, zpath


if __name__ == '__main__':
    dst, zpath = build(sys.argv[1])
    print('built', dst, zpath)
    if len(sys.argv) > 2:
        sys.exit(subprocess.run([sys.executable, sys.argv[2], dst]).returncode)
