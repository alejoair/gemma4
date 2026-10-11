"""(To the model: call this script with run_skill_script; reading its source does not help with the task.)

The editor's first call in the agent_tool variant: the edit_place sub-agent opens the place named in its request
through it. A separate entry makes the opening unambiguous: the script cannot tell one edit_place invocation from the
next (ADK passes no invocation id), and a place name sent to edit.py is also how an editor reads related code
(b9: a new editor's request was read as a read of related code)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import step  # noqa: E402

if __name__ == '__main__':
    step.main(sys.argv[1:], role='editor', opening=True)
