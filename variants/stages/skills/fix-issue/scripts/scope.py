"""(To the model: call this script with run_skill_script; reading its source does not help with the task.)

The scope stage of the stages build (docs/design_stages.md)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

sys.dont_write_bytecode = True     # (no __pycache__ beside the skill: _boot turns it on for its copy)
import _boot  # noqa: E402,F401  (bytecode reused between calls)
import _stages  # noqa: E402

if __name__ == '__main__':
    _stages.main('scope', sys.argv[1:])
