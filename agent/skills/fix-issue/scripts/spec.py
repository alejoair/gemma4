"""(To the model: call this script with run_skill_script; reading its source does not help with the task.)

The entry point of the specify_fix sub-agent in the agent_tool variant: it sees the code of every planned place at once
and writes, for each, what changes there (step.py in the 'specifier' role; docs/design_single.md, "Redesign:
microtasks")."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import step  # noqa: E402

if __name__ == '__main__':
    step.main(sys.argv[1:], role='specifier')
