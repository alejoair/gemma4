"""Not a script of this skill: the only one is scripts/step.py. This file answers with the call to make now."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _stub  # noqa: E402

_stub.answer('scripts/grep.py')
