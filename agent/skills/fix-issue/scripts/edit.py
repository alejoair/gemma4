"""(To the model: call this script with run_skill_script; reading its source does not help with the task.)

The editor's entry point in the agent_tool variant: the edit_place sub-agent edits one place through it and ends with
one sentence (step.py in the 'editor' role; see step.py and variants/agent_tool)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import step  # noqa: E402

if __name__ == '__main__':
    step.main(sys.argv[1:], role='editor')
