"""Shared fixtures: the skill's scripts are importable, and each test gets its own git repository and state dir."""
import os
import subprocess
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), '..', 'agent', 'skills', 'swe', 'scripts')
sys.path.insert(0, os.path.abspath(SCRIPTS))


def git(root, *args):
    subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty git repository with one commit; the scripts see it as the task repository."""
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'base')
    monkeypatch.setenv('SWE_REPO', str(root))
    monkeypatch.setenv('SWE_STATE_ROOT', str(tmp_path / 'state'))
    return root


def commit_all(root):
    git(root, 'add', '-A')
    git(root, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', 'files')
