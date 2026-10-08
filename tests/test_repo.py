import _repo
from conftest import commit_all


def test_root_is_the_task_repository(repo):
    assert _repo.repo_root() == str(repo)


def test_python_files_skip_hidden_caches_and_tests_but_keep_docs(repo):
    for rel in ['pkg/a.py', 'pkg/__pycache__/a.py', '.hidden.py', '.adk_exec_1.py', 'tests/test_a.py',
                'pkg/test_b.py', 'docs_src/tutorial.py', 'scripts/release.py', 'build/x.py', 'pkg/x.egg-info/y.py',
                'notes.txt']:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x = 1\n')
    files = list(_repo.iter_py(str(repo)))
    assert files == ['docs_src/tutorial.py', 'pkg/a.py', 'scripts/release.py']
    assert list(_repo.iter_py(str(repo), docs=False)) == ['pkg/a.py']
    assert 'tests/test_a.py' in list(_repo.iter_py(str(repo), tests=True))


def test_test_and_doc_paths():
    assert _repo.is_test_path('tests/helpers.py')
    assert _repo.is_test_path('pkg/test_x.py')
    assert _repo.is_test_path('pkg/x_test.py')
    assert _repo.is_test_path('conftest.py')
    assert _repo.is_doc_path('docs_src/a.py')
    assert not _repo.is_doc_path('pkg/docs.py')


def test_crlf_and_invalid_bytes_survive_a_read_write(repo):
    raw = b'a = 1\r\nb = "\xff\xfe"\r\nc = 3\r\n'
    (repo / 'm.py').write_bytes(raw)
    lines = _repo.read_lines(str(repo), 'm.py')
    assert lines[1].endswith('\r\n')
    _repo.write_lines(str(repo), 'm.py', lines)
    assert (repo / 'm.py').read_bytes() == raw


def test_replacing_a_line_keeps_the_file_line_ending(repo):
    (repo / 'm.py').write_bytes(b'a = 1\r\nb = 2\r\n')
    lines = _repo.read_lines(str(repo), 'm.py')
    assert _repo.newline_of(lines) == '\r\n'
    assert _repo.newline_of(['x\n']) == '\n'


def test_diff_includes_new_files_but_not_hidden_or_pyc(repo):
    (repo / 'a.py').write_text('a = 1\n')
    commit_all(repo)
    (repo / 'a.py').write_text('a = 2\n')
    (repo / 'new.py').write_text('n = 1\n')
    (repo / '.adk_exec_1.py').write_text('h = 1\n')
    (repo / 'c.pyc').write_bytes(b'\x00')
    diff = _repo.worktree_diff(str(repo))
    assert '-a = 1' in diff and '+a = 2' in diff
    assert '+++ b/new.py' in diff and '+n = 1' in diff
    assert '.adk_exec' not in diff and 'c.pyc' not in diff


def test_diff_is_empty_on_a_clean_tree(repo):
    assert _repo.worktree_diff(str(repo)) == ''
