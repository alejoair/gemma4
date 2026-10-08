import _state


def test_save_and_load(repo):
    assert _state.load('plan', default=[]) == []
    _state.save('plan', [{'place': 'P1'}])
    assert _state.load('plan') == [{'place': 'P1'}]


def test_events_are_appended_in_order(repo):
    _state.record({'step': 'S0'})
    _state.record({'step': 'D1'})
    assert [e['step'] for e in _state.events()] == ['S0', 'D1']
    assert all('t' in e for e in _state.events())


def test_two_repositories_never_share_state(repo, tmp_path, monkeypatch):
    _state.save('plan', ['first'])
    other = tmp_path / 'other'
    (other / '.git').mkdir(parents=True)
    monkeypatch.setenv('SWE_REPO', str(other))
    assert _state.load('plan', default=None) is None


def test_a_corrupt_file_reads_as_the_default(repo):
    _state.save('plan', [1])
    with open(_state.path('plan.json'), 'w') as fh:
        fh.write('{not json')
    assert _state.load('plan', default='d') == 'd'
