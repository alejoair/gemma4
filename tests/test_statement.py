import _statement

TEMPLATE = '''🐛 Fix redirects losing history

🐛 Fix redirects losing history

## Pull Request

<!--
Please start with a GitHub Discussion.
-->

Discussion: <!-- Link to the GitHub Discussion -->

## Description

`Session.resolve_redirects` drops the first response. Fixes #2875, see https://github.com/psf/requests/issues/1.

@someone FYI.

## AI Disclaimer

Codex wrote part of it.

<details>
<summary>AI transcript</summary>
long transcript
</details>'''


def test_clean_keeps_the_request_and_drops_template_links_and_references():
    text = _statement.clean(TEMPLATE)
    assert text.count('Fix redirects losing history') == 1
    assert '`Session.resolve_redirects` drops the first response.' in text
    for gone in ['GitHub Discussion', 'https://', '#2875', '@someone', 'Codex', 'transcript', 'Pull Request']:
        assert gone not in text


def test_terms_weigh_code_more_than_words():
    t = _statement.terms('The `resolve_redirects` method of Session.history and MAX_REDIRECTS: '
                         'Content-Type headers are wrong when redirected')
    assert t['resolve_redirects'] == 5
    assert t['history'] == 3 and t['Session'] >= 3
    assert t['MAX_REDIRECTS'] == 3
    assert t['content_type'] == 3 and t['Content'] == 2
    assert t['headers'] == 1 and t['redirected'] == 1
    assert 'the' not in t and 'are' not in t and 'when' not in t


def test_traceback_function_names_are_strong_terms():
    tb = 'Traceback (most recent call last):\n  File "/x/requests/sessions.py", line 3, in send_request\n'
    assert _statement.terms(tb)['send_request'] == 5
    assert _statement.paths(tb) == ['x/requests/sessions.py']


def test_capitalised_word_at_sentence_start_is_not_a_name():
    t = _statement.terms('Rendering fails. Tables with Markup break')
    assert t['Rendering'] == 1 and t['Markup'] == 2


def test_runs_of_capitalised_words_give_their_snake_form():
    assert _statement.terms('Add support for Server Sent Events')['server_sent_events'] == 3


def test_model_terms_names_phrases_and_paths():
    terms, paths = _statement.model_terms(['`resolve_redirects`', 'Session.send', 'history', 'Server Sent Event',
                                           'requests/sessions.py', 'TooManyRedirects()'])
    assert terms['resolve_redirects'] == 5 and terms['TooManyRedirects'] == 5
    assert terms['Session'] == 5 and terms['send'] == 3 and terms['history'] == 3
    assert terms['server_sent_event'] == 3 and terms['Server'] == 2
    assert terms['sessions'] == 3 and paths == ['requests/sessions.py']


def test_requirements_are_title_bullets_and_sentences_naming_code():
    text = _statement.clean('''Server connection handling.

* Add `HTTPParser.keep_alive`.
* `HTTPParser.complete` -> `.reset`
- [x] Bug fix
- [ ] New feature

Some context without code. Then `Session.send()` overwrites it.

```python
client.get("/")
```''')
    assert _statement.requirements(text) == ['Server connection handling.', 'Add `HTTPParser.keep_alive`.',
                                             '`HTTPParser.complete` -> `.reset`',
                                             'Then `Session.send()` overwrites it.']


def test_change_type_new_rename_fix():
    defined = {'complete', 'send', 'convert_underscores'}.__contains__
    assert _statement.change_type('Add `HTTPParser.keep_alive`.', defined) == ('new', ['HTTPParser.keep_alive'])
    assert _statement.change_type('`HTTPParser.complete` -> `.reset`', defined)[0] == 'rename'
    assert _statement.change_type('Fix `Session.send()` and `KeyboardException`', defined)[0] == 'fix'
    assert _statement.change_type('Add support for `app.frontend("/", directory="dist")`', defined) == \
        ('new', ['app.frontend'])
    assert _statement.code_names('Run `mypy -p rich --strict` and `x == True`') == ['x']
