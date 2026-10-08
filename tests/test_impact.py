import _impact

SYNC = '''class Parser:
    def complete(self):
        return self.reset_state()

    def reset_state(self):
        pass


def handle(parser):
    parser.complete()
'''

ASYNC = '''class Parser:
    async def complete(self):
        return self.reset_state()

    def reset_state(self):
        pass
'''

CLIENT = '''class BaseClient:
    def send(self, request):
        raise NotImplementedError


class Client(BaseClient):
    def send(self, request):
        return request


class AsyncClient(BaseClient):
    async def send(self, request):
        return request


def get(url):
    return Client().send(url)
'''

INIT = 'from ._client import Client, AsyncClient\n'


def write(repo, files):
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def places(table, chosen):
    return {(p.rel, p.name): p.reason for p in _impact.related(table, chosen)}


def test_the_same_definition_in_a_copied_package_is_related(repo):
    write(repo, {'src/httpx/_parsers.py': SYNC, 'src/ahttpx/_parsers.py': ASYNC})
    got = places(_impact.Table(str(repo)), [('src/httpx/_parsers.py', 'Parser.complete')])
    assert list(got)[0] == ('src/httpx/_parsers.py', 'Parser.complete')
    assert got[('src/ahttpx/_parsers.py', 'Parser.complete')] == 'same definition in src/ahttpx/_parsers.py'
    assert got[('src/httpx/_parsers.py', 'handle')].startswith('calls complete')


def test_async_twin_overrides_base_version_and_exports(repo):
    write(repo, {'pkg/_client.py': CLIENT, 'pkg/__init__.py': INIT})
    got = places(_impact.Table(str(repo)), [('pkg/_client.py', 'Client.send')])
    assert got[('pkg/_client.py', 'AsyncClient.send')] == 'async/sync twin of Client.send'
    assert got[('pkg/_client.py', 'BaseClient.send')] == 'version in base class BaseClient'
    assert got[('pkg/_client.py', 'get')] == 'calls send (line 17)'
    assert ('pkg/__init__.py', '<exports>') not in got           # send is not exported
    got = places(_impact.Table(str(repo)), [('pkg/_client.py', 'Client')])
    assert got[('pkg/__init__.py', '<exports>')] == 'exports Client'


def test_a_base_method_lists_its_overrides(repo):
    write(repo, {'pkg/_client.py': CLIENT})
    got = places(_impact.Table(str(repo)), [('pkg/_client.py', 'BaseClient.send')])
    assert got[('pkg/_client.py', 'Client.send')] == 'override in subclass Client'
    assert got[('pkg/_client.py', 'AsyncClient.send')] in ('override in subclass AsyncClient',
                                                           'async/sync twin of BaseClient.send')


def test_common_dunder_names_are_not_copies(repo):
    many = ''.join(f'class C{i}:\n    def __init__(self):\n        pass\n\n\n' for i in range(5))
    write(repo, {'pkg/m.py': many})
    got = places(_impact.Table(str(repo)), [('pkg/m.py', 'C0.__init__')])
    assert list(got) == [('pkg/m.py', 'C0.__init__')]


def test_sibling_prefers_matching_keyword_parameters_then_the_name(repo):
    app = '''class App:
    def mount(self, path, app, name=None):
        pass

    def static_files(self, path, directory):
        pass

    def front(self, x):
        pass

    def _private(self, directory):
        pass
'''
    write(repo, {'pkg/app.py': app})
    table = _impact.Table(str(repo))
    assert _impact.sibling(table, 'pkg/app.py', 'App', 'frontend', keywords=['directory']).name == 'App.static_files'
    assert _impact.sibling(table, 'pkg/app.py', 'App', 'frontend').name == 'App.front'
    assert _impact.sibling(table, 'pkg/app.py', 'Missing', 'frontend') is None
