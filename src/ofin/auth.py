from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import httpx
import structlog
from fastapi import Request

from .config import settings

log = structlog.get_logger()


@dataclass(slots=True)
class AuthState:
    authed: bool = False
    user: str | None = None
    name: str | None = None
    email: str | None = None
    groups: list[str] | None = None


_cache: dict[str, tuple[AuthState, float]] = {}
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _cache_key(cookie: str) -> str:
    return hashlib.sha256(cookie.encode("utf-8")).hexdigest()


_AUTH_COOKIE_PREFIXES = ("authelia_session", "authelia.session")


def _has_authelia_cookie(cookie_header: str) -> bool:
    if not cookie_header:
        return False
    parts = [p.strip().split("=", 1)[0] for p in cookie_header.split(";")]
    for p in parts:
        if any(p.startswith(prefix) for prefix in _AUTH_COOKIE_PREFIXES):
            return True
    return False


async def probe(cookie_header: str) -> AuthState:
    if not _has_authelia_cookie(cookie_header):
        return AuthState()

    cfg = settings()
    key = _cache_key(cookie_header)
    now = time.time()
    cached = _cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    state = AuthState()
    try:
        resp = await _http().get(
            cfg.authelia_url + cfg.authelia_verify_path,
            headers={
                "Cookie": cookie_header,
                "X-Forwarded-Method": "GET",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": cfg.forwarded_host,
                "X-Forwarded-Uri": "/",
                "Accept": "application/json",
            },
        )
        if resp.status_code == 200:
            state = AuthState(
                authed=True,
                user=resp.headers.get("Remote-User"),
                name=resp.headers.get("Remote-Name"),
                email=resp.headers.get("Remote-Email"),
                groups=[g for g in (resp.headers.get("Remote-Groups") or "").split(",") if g],
            )
    except httpx.HTTPError as e:
        log.warning("authelia_probe_failed", error=str(e))

    _cache[key] = (state, now + cfg.auth_cache_ttl)
    if len(_cache) > 4096:
        for k in list(_cache.keys())[:1024]:
            _cache.pop(k, None)
    return state


def state_for(request: Request) -> AuthState:
    return getattr(request.state, "auth", AuthState())
