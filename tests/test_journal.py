import _journal


def planned(*pids):
    s = _journal.new(now=1000)
    _journal.start(s, [{'id': 'R1'}], [{'id': 'C1'}])
    _journal.choose(s, [{'id': p} for p in ('P1', 'P2', 'P3')])
    _journal.set_plan(s, [(p, 'change') for p in pids])
    return s


def test_happy_path_s0_to_d4():
    s = _journal.new(now=1000)
    assert s['step'] == 'S0' and 'the issue statement' in _journal.next_call(s)
    _journal.start(s, [{'id': 'R1'}], [{'id': 'C1'}])
    assert s['step'] == 'D1' and '"C<n>"' in _journal.next_call(s)
    _journal.choose(s, [{'id': 'P1'}, {'id': 'P2'}])
    assert s['step'] == 'D2' and '"back"' in _journal.next_call(s)
    _journal.set_plan(s, [('P1', 'a'), ('P2', 'b')])
    assert s['step'] == 'D3' and '["P1", "<first line number>"' in _journal.next_call(s)
    assert _journal.edit_done(s, 0) == 'next' and '["P2"' in _journal.next_call(s)
    assert _journal.edit_done(s, 1) == 'finish' and s['step'] == 'D4'
    assert 'submit_patch' in _journal.next_call(s) and s['edited'] == ['P1', 'P2']


def test_a_place_that_fails_twice_is_skipped_and_the_next_comes():
    s = planned('P1', 'P2')
    assert _journal.edit_failed(s, 0, 'BROKEN test_a') == 'retry' and s['current'] == 0
    assert _journal.edit_failed(s, 0, 'BROKEN test_b') == 'skipped'
    assert s['plan'][0]['status'] == 'skipped' and s['current'] == 1 and s['step'] == 'D3'


def test_when_nothing_could_be_edited_it_goes_back_to_choose():
    s = planned('P1')
    _journal.edit_failed(s, 0, 'x')
    _journal.edit_failed(s, 0, 'y')
    assert s['step'] == 'D1' and s['places'] == [] and s['candidates'] == [{'id': 'C1'}]


def test_back_from_the_plan_and_an_uncovered_requirement():
    s = planned('P1')
    _journal.back(s)
    assert s['step'] == 'D1' and s['candidates'] == [{'id': 'C1'}]
    s = planned('P1')
    _journal.edit_done(s, 0)
    _journal.requirement_back(s, [{'id': 'C1', 'for': 'R2'}])
    assert s['step'] == 'D1' and s['candidates'] == [{'id': 'C1', 'for': 'R2'}] and s['edited'] == ['P1']


def test_an_edit_of_a_listed_place_outside_the_plan_joins_the_plan():
    s = planned('P1')
    i = _journal.target(s, 'P3')
    assert i == 1 and s['plan'][1]['place'] == 'P3'
    assert _journal.target(s, 'P1') == 0


def test_the_same_args_twice_in_a_step_are_a_repeat():
    s = _journal.new(now=1000)
    assert not _journal.repeated(s, ['C1'])
    assert _journal.repeated(s, [' C1 '])
    assert not _journal.repeated(s, ['C2'])
    s['step'] = 'D2'
    assert not _journal.repeated(s, ['C2'])


def test_time_limit_for_edits():
    s = _journal.new(now=1000)
    assert not _journal.time_is_up(s, now=1000 + _journal.EDIT_STOP - 1)
    assert _journal.time_is_up(s, now=1000 + _journal.EDIT_STOP + 1)
