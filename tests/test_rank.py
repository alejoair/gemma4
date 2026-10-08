import _rank
import _statement


def write(repo, files):
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


SESSIONS = '''"""Sessions."""
import os

MAX_REDIRECTS = 30


class SessionRedirectMixin:
    """Redirect handling."""

    def get_redirect_target(self, resp):
        return resp.headers.get("location")

    def resolve_redirects(self, resp, req):
        """Yield the responses of each redirect."""
        hist = []
        while True:
            hist.append(resp)
            resp.history = hist[1:]
            yield resp


class Session(SessionRedirectMixin):
    def send(self, request):
        return self.resolve_redirects(None, request)
'''

MODELS = '''class Response:
    """The response. Its history holds the redirect responses."""

    __attrs__ = ["history", "headers", "status_code"]

    def __init__(self):
        self.history = []
        self.headers = {}
'''


def names(ranked):
    return [f'{d.rel}::{d.name}' for _, d, _ in ranked]


def test_tokens_split_identifiers_and_keep_compound_names_whole():
    assert _rank.tokens('resolve_redirects') == ['resolv', 'redirect', 'resolve_redirects']
    assert _rank.tokens('HTTPAdapter') == ['http', 'adapter', 'httpadapter']
    assert _rank.tokens('history') == ['history']


def test_stem_joins_inflections():
    assert len({_rank.stem(w) for w in ['redirect', 'redirects', 'redirected', 'redirecting']}) == 1
    assert _rank.stem('parse') == _rank.stem('parsing') == _rank.stem('parses')


def test_a_method_is_a_document_of_its_own_lines(repo):
    write(repo, {'pkg/sessions.py': SESSIONS})
    docs = {d.name: d for d in _rank.index_file(str(repo), 'pkg/sessions.py')}
    assert set(docs) == {'<module>', 'SessionRedirectMixin', 'SessionRedirectMixin.get_redirect_target',
                         'SessionRedirectMixin.resolve_redirects', 'Session', 'Session.send'}
    assert docs['SessionRedirectMixin.resolve_redirects'].tf['code']['hist'] == 3
    assert docs['SessionRedirectMixin'].tf['code']['hist'] == 0       # the method's lines are not the class's
    assert docs['SessionRedirectMixin.resolve_redirects'].tf['name']['resolve_redirects'] == 2  # name + def line
    assert docs['SessionRedirectMixin.resolve_redirects'].tf['prose']['respons'] == 1
    assert docs['<module>'].tf['code']['max_redirects'] == 1


def test_the_named_method_outranks_the_class_with_the_attribute(repo):
    write(repo, {'pkg/sessions.py': SESSIONS, 'pkg/models.py': MODELS})
    text = 'Response.history is missing the first response after `resolve_redirects`'
    ranked = _rank.Index(str(repo)).rank(_statement.terms(text))
    assert names(ranked)[0] == 'pkg/sessions.py::SessionRedirectMixin.resolve_redirects'
    assert 'pkg/models.py::Response' in names(ranked)


def test_a_plural_or_misspelt_name_finds_the_real_one(repo):
    write(repo, {'pkg/sessions.py': SESSIONS})
    ranked = _rank.Index(str(repo)).rank({'get_redirect_targets': 5})
    assert names(ranked)[0] == 'pkg/sessions.py::SessionRedirectMixin.get_redirect_target'


def test_docs_examples_rank_below_the_package(repo):
    code = 'def read_items():\n    return items_reader()\n'
    write(repo, {'pkg/items.py': code, 'docs_src/items.py': code})
    assert names(_rank.Index(str(repo)).rank({'read_items': 5}))[:2] == ['pkg/items.py::read_items',
                                                                         'docs_src/items.py::read_items']


def test_a_file_the_statement_names_counts_double(repo):
    code = 'def read_items():\n    return items_reader()\n'
    write(repo, {'pkg/a.py': code, 'pkg/b.py': code})
    assert names(_rank.Index(str(repo)).rank({'read_items': 5}, paths=['pkg/b.py']))[0] == 'pkg/b.py::read_items'
    assert names(_rank.Index(str(repo)).rank({'read_items': 5}, paths=['b.py']))[0] == 'pkg/b.py::read_items'


def test_nothing_matching_gives_no_candidates(repo):
    write(repo, {'pkg/sessions.py': SESSIONS})
    assert _rank.Index(str(repo)).rank({'zebra': 1}) == []


def test_owner_class_of_a_new_entity(repo):
    write(repo, {'pkg/app.py': 'class FastAPI:\n    def get(self, path):\n        pass\n',
                 'pkg/routing.py': 'class APIRouter:\n    pass\n',
                 'docs_src/tutorial.py': 'from pkg.app import FastAPI\n\napp = FastAPI()\n',
                 'docs_src/other.py': 'app = FastAPI()\nrouter = APIRouter()\n'})
    index = _rank.Index(str(repo))
    assert _rank.owner_class(index, 'app') == ('pkg/app.py', 'FastAPI')
    assert _rank.owner_class(index, 'router') == ('pkg/routing.py', 'APIRouter')
    assert _rank.owner_class(index, 'fastapi') == ('pkg/app.py', 'FastAPI')
    assert _rank.owner_class(index, 'client') is None
    assert 'FastAPI' in index.names and 'get' in index.names
