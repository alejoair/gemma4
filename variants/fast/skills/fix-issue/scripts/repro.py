"""(To the model: call this script with run_skill_script; reading its source does not help with the task.)

A reproduce worker of the fast build (docs/design_fast.md)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _stages  # noqa: E402

if __name__ == '__main__':
    _stages.main_fast('reproduce', 'repro', sys.argv[1:])
