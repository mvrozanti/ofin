from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlencode

import pytest


class FakeAuth:
    def __init__(self, authed: bool = False, user: str | None = None, name: str | None = None):
        self.authed = authed
        self.user = user
        self.name = name
        self.email = None
        self.groups = None


class FakeRequest:
    """Minimal Starlette-style stand-in for filters.Filter.from_request."""

    def __init__(self, params: dict | None = None, authed: bool = False, user: str | None = None):
        params = params or {}
        self.query_params = _QueryParams(params)
        self.state = SimpleNamespace(auth=FakeAuth(authed=authed, user=user))
        self.url = SimpleNamespace(query=urlencode([(k, v) for k, v in params.items() if v is not None]))
        self.headers = {}


class _QueryParams:
    def __init__(self, params: dict):
        self._items = [(k, str(v)) for k, v in params.items() if v is not None]

    def get(self, key, default=None):
        for k, v in self._items:
            if k == key:
                return v
        return default

    def __iter__(self):
        return iter(self._items)

    def multi_items(self):
        return list(self._items)


@pytest.fixture
def anon_request():
    return FakeRequest()


@pytest.fixture
def authed_request():
    return FakeRequest(authed=True, user="m")
