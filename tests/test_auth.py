from ofin.auth import AuthState, _has_authelia_cookie


def test_auth_state_defaults():
    a = AuthState()
    assert a.authed is False
    assert a.user is None
    assert a.name is None
    assert a.email is None
    assert a.groups is None


def test_auth_state_authed():
    a = AuthState(authed=True, user="m", name="M Rozanti")
    assert a.authed is True
    assert a.user == "m"


def test_has_authelia_cookie_positive():
    assert _has_authelia_cookie("authelia_session=abcdef") is True


def test_has_authelia_cookie_dotted_variant():
    assert _has_authelia_cookie("authelia.session=abc") is True


def test_has_authelia_cookie_negative():
    assert _has_authelia_cookie("other=x; session=y") is False


def test_has_authelia_cookie_empty():
    assert _has_authelia_cookie("") is False
    assert _has_authelia_cookie(None) is False


def test_has_authelia_cookie_among_multiple():
    cookie = "lang=pt; theme=dark; authelia_session=xyz; foo=bar"
    assert _has_authelia_cookie(cookie) is True


def test_has_authelia_cookie_substring_not_a_prefix_match():
    cookie = "fake_authelia_session=x"
    assert _has_authelia_cookie(cookie) is False
