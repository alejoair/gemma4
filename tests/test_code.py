import _code

SRC = '''import os


def top(a, b=1, *args, **kw) -> int:
    """Add things.

    More text.
    """
    return a + b


class Box:
    """A box."""

    size = 3

    @property
    def area(self):
        return self.size * self.size

    def describe(self):
        text = """line 1
line 2
line 3
line 4
line 5
line 6
line 7
"""
        return text
'''


def test_symbols_with_qualified_names_and_decorators_in_the_range():
    syms = {s.name: s for s in _code.symbols(_code.parse(SRC))}
    assert set(syms) == {'top', 'Box', 'Box.area', 'Box.describe'}
    assert syms['Box.area'].start == 17 and syms['Box.area'].def_line == 18   # starts at @property
    assert syms['Box.area'].kind == 'def' and syms['Box'].kind == 'class'
    assert syms['top'].end == 9


def test_innermost_owner_of_each_line():
    syms = _code.symbols(_code.parse(SRC))
    owner = _code.owner_map(syms, len(SRC.splitlines()))
    assert owner[19].name == 'Box.area'
    assert owner[15].name == 'Box'
    assert owner[1] is None


def test_skeleton_is_one_line_with_signature_and_first_docstring_line():
    syms = {s.name: s for s in _code.symbols(_code.parse(SRC))}
    line = _code.skeleton('pkg/m.py', syms['top'])
    assert line == 'pkg/m.py :: top (def, lines 4-9) def top(a, b=1, *args, **kw) -> int — Add things.'


def test_numbered_code_has_no_space_after_the_bar_and_collapses_long_strings():
    lines = SRC.splitlines(keepends=True)
    out = _code.numbered(lines, 21, 30, prose=_code.prose_lines(_code.parse(SRC)))
    assert '  21|    def describe(self):' in out
    assert 'line 4' not in out                       # the long string is collapsed
    assert '(6 lines of text)' in out
    assert '  30|        return text' in out


def test_short_strings_are_not_collapsed():
    lines = SRC.splitlines(keepends=True)
    out = _code.numbered(lines, 4, 9, prose=_code.prose_lines(_code.parse(SRC)))
    assert 'More text.' in out


def test_window_is_clipped_to_the_file():
    lines = SRC.splitlines(keepends=True)
    assert _code.window(lines, 2, 2, radius=15) == (1, 17)
    assert _code.window(lines, 25, 25, radius=15) == (10, 30)
    assert _code.window(lines, 19, 20, radius=2) == (17, 22)


def test_find_by_qualified_name_or_last_part():
    syms = _code.symbols(_code.parse(SRC))
    assert [s.name for s in _code.find(syms, 'Box.area')] == ['Box.area']
    assert [s.name for s in _code.find(syms, 'area')] == ['Box.area']
    assert _code.find(syms, 'Other.area') == []


def test_parse_failure_gives_no_symbols():
    assert _code.symbols(_code.parse('def broken(:\n')) == []
