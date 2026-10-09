import _state
import _tests
from conftest import commit_all

CALC = '''def add(a, b):
    return a + b


def scale(x):
    return x * 2
'''

TEST_CALC = '''from pkg.calc import add, scale


def test_add():
    assert add(1, 2) == 3


def test_scale():
    assert scale(2) == 4
'''

TEST_OTHER = '''def test_other():
    assert True
'''


def setup(repo, extra=None):
    files = {'pkg/__init__.py': '', 'pkg/calc.py': CALC, 'tests/test_calc.py': TEST_CALC,
             'tests/test_other.py': TEST_OTHER}
    files.update(extra or {})
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    commit_all(repo)


def edit(repo, old, new):
    path = repo / 'pkg/calc.py'
    path.write_text(path.read_text().replace(old, new))


def test_select_picks_the_test_file_that_uses_the_changed_names(repo):
    setup(repo)
    assert _tests.select(str(repo), {'pkg/calc.py': ['scale', '    return x * 3']}) == [['tests/test_calc.py']]


def test_a_neutral_edit_is_ok(repo):
    setup(repo)
    edit(repo, 'return x * 2', 'return 2 * x')
    verdict, detail, new = _tests.check(str(repo), {'pkg/calc.py': ['scale', '    return 2 * x']})
    assert verdict == 'OK' and '2 existing tests pass' in detail and new == []


def test_an_edit_that_breaks_a_test_is_broken(repo):
    setup(repo)
    edit(repo, 'return x * 2', 'return x * 3')
    verdict, detail, new = _tests.check(str(repo), {'pkg/calc.py': ['scale', '    return x * 3']})
    assert verdict == 'BROKEN' and new == ['tests/test_calc.py::test_scale']
    assert 'test_scale' in detail


def test_a_failure_already_there_before_the_change_does_not_count(repo):
    setup(repo, {'tests/test_calc.py': TEST_CALC + '\n\ndef test_add_broken():\n    assert add(1, 1) == 3\n'})
    edit(repo, 'return x * 2', 'return 2 * x')
    verdict, detail, new = _tests.check(str(repo), {'pkg/calc.py': ['add', 'scale']})
    assert verdict == 'OK' and 'already there before the change' in detail and new == []


def test_no_related_test_is_not_verified(repo):
    setup(repo)
    verdict, _, _ = _tests.check(str(repo), {'pkg/unrelated.py': ['zzz_unused']})
    assert verdict == 'NOT VERIFIED'


def test_missing_test_only_module_is_stubbed(repo):
    setup(repo, {'tests/test_snap.py': 'from inline_snapshot_x import snapshot\nfrom pkg.calc import scale\n\n\n'
                                       'def test_snap():\n    assert scale(2) == snapshot(4)\n'})
    verdict, detail, _ = _tests.check(str(repo), {'pkg/calc.py': ['scale', 'snapshot']})
    assert verdict == 'OK', detail


def test_going_back_restores_the_last_verified_state(repo):
    setup(repo)
    edit(repo, 'return x * 2', 'return 2 * x')
    _tests.save_verified(str(repo))
    edit(repo, 'return 2 * x', 'return x * 3')
    (repo / 'pkg/new.py').write_text('n = 1\n')
    _tests.restore_verified(str(repo))
    assert 'return 2 * x' in (repo / 'pkg/calc.py').read_text()
    assert not (repo / 'pkg/new.py').exists()
    _state.save('verified', {})
    _tests.restore_verified(str(repo))
    assert (repo / 'pkg/calc.py').read_text() == CALC


def test_a_test_file_that_runs_out_of_time_is_not_counted_and_left_out_later(repo, monkeypatch):
    setup(repo, {'tests/test_slow.py': 'import time\nfrom pkg.calc import scale\n\n\n'
                                       'def test_slow():\n    time.sleep(30)\n    assert scale(1) == 2\n'})
    monkeypatch.setattr(_tests, 'FILE_TIMEOUT', 3)
    verdict, detail, _ = _tests.check(str(repo), {'pkg/calc.py': ['scale']})
    assert verdict == 'OK' and 'out of time, not counted: tests/test_slow.py' in detail
    assert _state.load('slow_tests') == ['tests/test_slow.py']
    verdict, detail, _ = _tests.check(str(repo), {'pkg/calc.py': ['scale']})
    assert verdict == 'OK' and 'out of time' not in detail


def test_the_module_own_test_file_comes_first(repo):
    setup(repo, {'pkg/repr.py': 'def auto(cls):\n    return cls\n',
                 'tests/test_repr.py': 'import pkg.repr\n\n\ndef test_auto():\n    assert pkg.repr.auto(1) == 1\n',
                 'tests/test_inspect.py': 'def test_param_default_empty():\n    param = default = empty = 1\n'
                                          '    assert param == default == empty\n'})
    targets = _tests.select(str(repo), {'pkg/repr.py': ['auto_rich_repr', 'if param.default is param.empty:']})
    assert targets[0] == ['tests/test_repr.py']


def test_parallel_runs_share_one_deadline(repo, monkeypatch):
    import time
    slow = 'import time\nfrom pkg.calc import scale\n\n\ndef test_slow():\n    time.sleep(30)\n'
    setup(repo, {'tests/test_slow1.py': slow, 'tests/test_slow2.py': slow})
    monkeypatch.setattr(_tests, 'FILE_TIMEOUT', 3)
    t = time.time()
    r = _tests.run(str(repo), [['tests/test_slow1.py'], ['tests/test_slow2.py']])
    assert time.time() - t < 6 and sorted(r['timeouts']) == ['tests/test_slow1.py', 'tests/test_slow2.py']


def test_a_masked_pytest_is_not_verified(repo, monkeypatch):
    setup(repo)
    shim = repo / 'shim'
    shim.mkdir()
    (shim / 'pytest.py').write_text('import sys\nsys.stderr.write("pytest is disabled in this environment\\n")\n'
                                    'sys.exit(1)\n')
    (shim / '__main__.py').write_text('')
    monkeypatch.setenv('PYTHONPATH', str(shim))
    verdict, detail, _ = _tests.check(str(repo), {'pkg/calc.py': ['scale']})
    assert verdict == 'NOT VERIFIED' and 'could not run' in detail
