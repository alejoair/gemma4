"""build.py <out dir> [validator] [--variant <name>]: the submission from agent/, without Python caches (a .pyc file
makes the harness reject it), as <out dir>/submission/ and <out dir>/submission.zip; then runs the competition's
validate_submission.py on the directory when its path is given. A variant (variants/<name>/) is copied over agent/:
its files replace or add to the base ones."""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agent')
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache')


def build(out, variant=None):
    dst = os.path.join(out, 'submission')
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(ROOT, dst, ignore=IGNORE)
    if variant:
        shutil.copytree(os.path.join(ROOT, '..', 'variants', variant), dst, ignore=IGNORE, dirs_exist_ok=True)
    zpath = os.path.join(out, 'submission.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for d, _, files in os.walk(dst):
            for f in sorted(files):
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, dst))
    return dst, zpath


if __name__ == '__main__':
    argv = sys.argv[1:]
    variant = None
    if '--variant' in argv:
        k = argv.index('--variant')
        variant = argv[k + 1]
        del argv[k:k + 2]
    dst, zpath = build(argv[0], variant)
    print('built', dst, zpath, f'(variant {variant})' if variant else '')
    if len(argv) > 1:
        sys.exit(subprocess.run([sys.executable, argv[1], dst]).returncode)
