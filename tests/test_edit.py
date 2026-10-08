import _edit

SRC = '''import os


class Box:
    @property
    def area(self):
        if self.size:
            return self.size * self.size
        return 0

    def describe(self):
        return "box"
'''


def setup(repo, text=SRC, rel='pkg/box.py'):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_plain_replacement(repo):
    path = setup(repo)
    r = _edit.apply(str(repo), 'pkg/box.py', 9, 9, '        return -1\n')
    assert r.applied and (r.start, r.end) == (9, 9) and r.repairs == []
    assert path.read_text().splitlines()[8] == '        return -1'


def test_one_space_off_is_reindented(repo):
    path = setup(repo)
    r = _edit.apply(str(repo), 'pkg/box.py', 7, 8, '         if self.size > 0:\n             return self.size\n')
    assert r.applied and r.repairs == ['re-indented to the replaced line']
    assert path.read_text().splitlines()[6:8] == ['        if self.size > 0:', '            return self.size']


def test_escaped_quotes_are_unescaped(repo):
    path = setup(repo)
    r = _edit.apply(str(repo), 'pkg/box.py', 12, 12, '        return \\"a box\\"\n')
    assert r.applied and r.repairs == ['turned escaped quotes into quotes']
    assert path.read_text().splitlines()[11] == '        return "a box"'


def test_a_range_longer_than_the_text_replaces_only_its_lines(repo):
    path = setup(repo)
    r = _edit.apply(str(repo), 'pkg/box.py', 7, 8, '        if self.size is not None:\n')
    assert r.applied and 'replaced only 1 lines, as many as the new text has' in r.repairs
    assert path.read_text().splitlines()[6:8] == ['        if self.size is not None:',
                                                   '            return self.size * self.size']


def test_a_repeated_boundary_line_is_dropped(repo):
    path = setup(repo)
    r = _edit.apply(str(repo), 'pkg/box.py', 6, 6, '    @property\n    def area(self) -> int:\n')
    assert r.applied and r.repairs == ['dropped a first line that repeated the line before the range']
    assert path.read_text().count('@property') == 1


def test_an_edit_that_does_not_compile_is_not_applied(repo):
    path = setup(repo)
    r = _edit.apply(str(repo), 'pkg/box.py', 7, 7, '        if self.size\n')
    assert not r.applied and 'would not compile' in r.error
    assert path.read_text() == SRC


def test_no_op_test_files_and_bad_ranges_are_refused(repo):
    setup(repo)
    setup(repo, rel='tests/test_box.py')
    assert 'nothing changes' in _edit.apply(str(repo), 'pkg/box.py', 9, 9, '        return 0\n').error
    assert 'test files' in _edit.apply(str(repo), 'tests/test_box.py', 1, 1, 'x = 1\n').error
    assert 'outside the file' in _edit.apply(str(repo), 'pkg/box.py', 40, 41, 'x = 1\n').error


def test_warnings_for_unknown_names_and_removed_definitions(repo):
    setup(repo)
    r = _edit.apply(str(repo), 'pkg/box.py', 9, 9, '        return math.floor(0)\n')
    assert r.applied and r.warnings == ['nothing in the file defines or imports: math']
    r = _edit.apply(str(repo), 'pkg/box.py', 11, 12, '    pass\n')
    assert r.applied and r.warnings == ['the edit removed the definition of: describe']


def test_crlf_files_keep_their_line_ends(repo):
    path = setup(repo, text=SRC.replace('\n', '\r\n'))
    assert _edit.apply(str(repo), 'pkg/box.py', 9, 9, '        return -1\n').applied
    assert path.read_bytes().count(b'\r\n') == SRC.count('\n') and b'return -1\r\n' in path.read_bytes()
