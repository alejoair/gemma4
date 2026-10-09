import _args


# Paths, ids and names: the wrappers models add around each argument.
def test_clean_strips_quotes_backticks_commas_and_cjk_brackets():
    assert _args.clean('"C2",') == 'C2'
    assert _args.clean(" 'C2' ") == 'C2'
    assert _args.clean('`pkg/mod.py`') == 'pkg/mod.py'
    assert _args.clean('「C2」') == 'C2'
    assert _args.clean('『P1』') == 'P1'


def test_unpack_a_list_packed_into_one_string():
    assert _args.unpack(['["C2", "C5"]']) == ['C2', 'C5']
    assert _args.unpack(['C2", "C5']) == ['C2', 'C5']
    assert _args.unpack(['C2 | C5']) == ['C2', 'C5']
    assert _args.unpack(['C2', 'C5']) == ['C2', 'C5']


def test_unpack_leaves_code_with_commas_alone():
    code = 'x = f("a", "b")'
    assert _args.unpack(['P1', '3', '4', code]) == ['P1', '3', '4', code]


def test_ids_in_every_form():
    assert _args.ids(['C2', 'C5'], 'C') == (['C2', 'C5'], [])
    assert _args.ids(['C2, C5'], 'C') == (['C2', 'C5'], [])
    assert _args.ids(['c2'], 'C') == (['C2'], [])
    assert _args.ids(['2', '5'], 'C') == (['C2', 'C5'], [])
    assert _args.ids(['["C2", "C5"]'], 'C') == (['C2', 'C5'], [])
    assert _args.ids(['C2', 'pkg/mod.py::Cls.meth'], 'C') == (['C2'], ['pkg/mod.py::Cls.meth'])


# Code text: kept exactly, except for the damage seen in real calls.
def test_code_with_escaped_newlines_is_unescaped():
    assert _args.code('    if x:\\n        return 1\\n') == '    if x:\n        return 1\n'


def test_code_with_real_newlines_and_a_literal_backslash_n_in_a_string_is_kept():
    text = '    s = "a\\nb"\n    return s\n'
    assert _args.code(text) == text


def test_code_loses_leaked_call_syntax():
    leaked = '    return 1\n<|"|>],file_path:<|"|>scripts/step.py<|"|>'
    assert _args.code(leaked) == '    return 1\n'
    assert _args.code('    return 1\n<tool_call|>') == '    return 1\n'


def test_code_loses_line_number_prefixes_only_when_every_line_has_one():
    assert _args.code('  79|    a = 1\n  80|    b = 2\n') == '    a = 1\n    b = 2\n'
    mixed = '    a = 1\n  80|    b = 2\n'
    assert _args.code(mixed) == mixed


def test_code_loses_markdown_fences():
    assert _args.code('```python\n    a = 1\n```') == '    a = 1\n'


def test_edit_in_every_form():
    assert _args.edit(['P1', '79', '80', '    a = 1']) == ('P1', 79, 80, '    a = 1\n')
    assert _args.edit(['P1', '79-80', '    a = 1']) == ('P1', 79, 80, '    a = 1\n')
    assert _args.edit(['"P1"', '"79"', '"80"', '    a = 1\n']) == ('P1', 79, 80, '    a = 1\n')
    assert _args.edit(['p1', '79', '79', '    a = 1\n', '    b = 2\n']) == ('P1', 79, 79, '    a = 1\n    b = 2\n')


def test_edit_with_a_placeholder_or_missing_numbers_is_rejected():
    assert _args.edit(['P1', 'start', 'end', 'x']) is None
    assert _args.edit(['P1', 'x = 1']) is None
    assert _args.edit(['<place>', '3', '4', 'x']) is None


def test_plan_in_every_form():
    assert _args.plan(['P1: raise ValueError', 'P3: same in the async copy']) == \
        [('P1', 'raise ValueError'), ('P3', 'same in the async copy')]
    assert _args.plan(['P1: a\nP3: b']) == [('P1', 'a'), ('P3', 'b')]
    assert _args.plan(['P1', 'a', 'P3', 'b']) == [('P1', 'a'), ('P3', 'b')]
    assert _args.plan(['back']) == 'back'
    assert _args.plan(['"back"']) == 'back'
    assert _args.plan(['something without places']) == []


def test_placeholders_are_recognised():
    for p in ['<file>', '<start>', 'start-end', 'file', 'symbol', '...', '']:
        assert _args.is_placeholder(p)
    assert not _args.is_placeholder('P1')


def test_a_one_item_list_in_a_string_is_unpacked():
    assert _args.unpack(['["P1: the history must change"]']) == ['P1: the history must change']
    assert _args.plan(['["P1: the history must change"]']) == [('P1', 'the history must change')]


def test_edit_with_numbered_lines_instead_of_numbers():
    args = ['P1', '79|        if a == b:', '82|            yield a', '        if a is b:\\n            yield b']
    assert _args.edit(args) == ('P1', 79, 82, '        if a is b:\n            yield b\n')


def test_ids_in_cjk_brackets_and_lists():
    assert _args.ids(['「C1」, "C2"'], 'C') == (['C1', 'C2'], [])
    assert _args.ids(['[C1, C3]'], 'C') == (['C1', 'C3'], [])
