"""The whole flow S0 -> D4 through step.py, with hand-written decisions, on a small repository."""
import os
import subprocess
import sys

from conftest import SCRIPTS, commit_all

STEP = os.path.join(SCRIPTS, 'step.py')

SESSIONS = '''class SessionRedirectMixin:
    def get_redirect_target(self, resp):
        return resp.headers.get("location")

    def resolve_redirects(self, resp, req):
        """Yield the responses of each redirect."""
        hist = []
        url = self.get_redirect_target(resp)
        while url:
            hist.append(resp)
            resp.history = hist[1:]
            url = None
        return hist


class Session(SessionRedirectMixin):
    def send(self, request):
        return self.resolve_redirects(request, request)
'''

TEST = '''from pkg.sessions import Session


class R:
    headers = {"location": "x"}


def test_resolve_redirects_returns_history():
    assert len(Session().resolve_redirects(R(), None)) == 1
'''

STATEMENT = ('Fix redirect history\n\n`resolve_redirects` puts the original response in `Response.history`; '
             'the history of a redirect must not include the response itself.')


def call(repo, *args):
    env = dict(os.environ, SWE_REPO=str(repo))
    r = subprocess.run([sys.executable, STEP, *args], cwd=repo, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout


def setup(repo):
    for rel, text in {'pkg/__init__.py': '', 'pkg/sessions.py': SESSIONS, 'tests/test_sessions.py': TEST}.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    commit_all(repo)


def test_whole_flow(repo):
    setup(repo)
    out = call(repo, STATEMENT, 'resolve_redirects', 'history')
    assert 'R1 [fix] Fix redirect history' in out
    assert 'C1 pkg/sessions.py :: SessionRedirectMixin.resolve_redirects' in out
    assert out.rstrip().splitlines()[-1].startswith('NEXT: call run_skill_script')

    out = call(repo, '「C1」')
    assert '  11|            resp.history = hist[1:]' in out
    assert 'P2 pkg/sessions.py :: Session.send' in out and 'calls resolve_redirects' in out
    assert 'Edit the chosen code now, starting with P1' in out and '["P1", "<first line number>"' in out

    out = call(repo, 'P1', '11', '11', '            resp.history = hist[:-1]')
    assert out.startswith('OK: P1 lines 11-11 changed; 1 existing tests pass')
    assert 'The planned places are done' in out and 'submit_patch' in out.splitlines()[-1]
    assert 'resp.history = hist[:-1]' in (repo / 'pkg/sessions.py').read_text()


def test_wrong_forms_and_repeats_are_answered_with_the_expected_call(repo):
    setup(repo)
    out = call(repo, 'short')
    assert 'copies the issue statement' in out and 'NEXT: call' in out
    call(repo, STATEMENT)
    out = call(repo, 'C99')
    assert 'C99 is not a candidate id' in out and 'Nothing was opened' in out
    call(repo, 'C1')
    out = call(repo, 'P9', '1', '1', 'x = 1')
    assert 'P9 is not a listed place' in out
    out = call(repo, 'P9', '1', '1', 'x = 1')
    assert out.startswith('STOP REPEATING')
    out = call(repo, 'back')
    assert 'Choose the code to change' in out and '"C<n>"' in out.splitlines()[-1]


def test_an_edit_that_breaks_a_test_is_undone_and_retried(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    call(repo, 'P1: change the history')
    out = call(repo, 'P1', '13', '13', '        return []')
    assert out.startswith('BROKEN: the edit made existing tests fail, so it was undone.')
    assert 'test_resolve_redirects_returns_history' in out
    assert '        return hist' in (repo / 'pkg/sessions.py').read_text()
    assert out.startswith('BROKEN') and 'EDIT P1' in out


def test_a_malformed_edit_changes_nothing(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    call(repo, 'P1: change the history')
    out = call(repo, 'P1', 'start', 'end', 'x')
    assert out.startswith('The edit was not read') and 'EDIT P1' in out
    out = call(repo, 'P1', '11', '11', '            resp.history = hist[1:')
    assert out.startswith('NOT APPLIED: the file would not compile')
    assert (repo / 'pkg/sessions.py').read_text() == SESSIONS


def test_a_corrected_plan_line_in_the_edit_step_updates_the_plan(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    call(repo, 'P1: change the history')
    out = call(repo, 'P1: keep the history without the response itself')
    assert out.startswith('Plan updated') and 'Plan: keep the history without the response itself' in out
    assert (repo / 'pkg/sessions.py').read_text() == SESSIONS


def test_the_window_points_to_code_the_plan_names(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    out = call(repo, 'P1: change `resp.history = hist[1:]` to `resp.history = hist[:-1]`')
    assert 'The plan names code on line 11 (`resp.history = hist[1:]`).' in out


def test_repeats_in_the_edit_step_count_as_failures_and_the_place_is_left(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    call(repo, 'P1: a', 'P2: b')
    call(repo, 'P1: a better plan')
    out = call(repo, 'P1: a better plan')
    assert out.startswith('STOP REPEATING') and 'EDIT P1' in out
    out = call(repo, 'P1: a better plan')
    assert 'P1 failed 2 times' in out and 'EDIT P2' in out


def test_a_placeholder_plan_line_is_ignored(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, '「C1」, "C2"')
    out = call(repo, 'P1: <what changes there>')
    assert not out.startswith('Plan updated')


def test_repeats_that_exhaust_the_only_place_go_back_to_choosing(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    call(repo, 'P1: a')
    call(repo, 'P1', '11', '11', '            resp.history = hist[1:')     # does not compile: failure 1
    out = call(repo, 'P1', '11', '11', '            resp.history = hist[1:')
    assert out.startswith('STOP REPEATING') and 'choose again' in out and '"C<n>"' in out.splitlines()[-1]


def test_choosing_opens_the_first_place_for_editing(repo):
    setup(repo)
    call(repo, STATEMENT)
    out = call(repo, 'C1')
    assert 'Edit the chosen code now, starting with P1' in out
    out = call(repo, 'P1', '11', '11', '            resp.history = hist[:-1]')
    assert out.startswith('OK: P1 lines 11-11 changed')


def test_candidate_ids_in_the_edit_step_choose_again(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    out = call(repo, 'C1,C2')
    assert 'Edit the chosen code now' in out


def test_a_code_name_in_the_edit_step_opens_that_code(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    out = call(repo, 'Session.send')
    assert 'P1 pkg/sessions.py :: Session.send' in out and 'Edit the chosen code now' in out


def test_a_bare_file_path_in_the_edit_step_does_not_open_the_file(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    out = call(repo, 'pkg/sessions.py')
    assert out.startswith('A file is not a place to edit') and 'EDIT P1' in out


def test_a_code_fragment_opens_the_function_containing_it(repo):
    setup(repo)
    call(repo, STATEMENT)
    out = call(repo, 'resp.history = hist[1:]')
    assert 'P1 pkg/sessions.py :: SessionRedirectMixin.resolve_redirects' in out


def test_repeats_in_the_choose_step_open_the_first_candidate(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'no_such_thing_here')
    call(repo, 'no_such_thing_here')
    out = call(repo, 'no_such_thing_here')
    assert 'The first candidate is opened now' in out and 'Edit the chosen code now' in out


def test_a_place_id_alone_opens_that_place(repo):
    setup(repo)
    call(repo, STATEMENT)
    call(repo, 'C1')
    out = call(repo, 'P2')
    assert out.startswith('EDIT P2') and 'Session.send' in out and '["P2", "<first line number>"' in out
