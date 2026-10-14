"""(To the model: call this script with run_skill_script; reading its source does not help with the task.)

The locate stage of the stages build (docs/design_stages.md)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _stages  # noqa: E402

if __name__ == '__main__':
    _stages.main('locate', sys.argv[1:])
