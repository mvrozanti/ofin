import logging
from contextlib import asynccontextmanager
from pathlib import Path

import urllib.parse

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth as auth_mod
from .auth import AuthState, probe
from .config import settings
from .db import engine, session
from .models import Base
from .parsers.categorize_engine import apply_rules_to_all
from .parsers.seed_rules import migrate_seed_rules, seed_default_rules
from .routes import api, budgets, pages, rules, wealth

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings().log_level.upper(), logging.INFO)),
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session() as s:
        n = await seed_default_rules(s)
        if n:
            log.info("seeded_rules", n=n)
        if await migrate_seed_rules(s):
            updated, skipped = await apply_rules_to_all(s)
            await s.commit()
            log.info("seed_migrated", updated=updated, skipped_overrides=skipped)
    log.info("ofin_started")
    yield
    await auth_mod.aclose()


app = FastAPI(title="ofin", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def auth_probe_mw(request: Request, call_next):
    if request.url.path in ("/healthz", "/readonly") or request.url.path.startswith("/static/"):
        request.state.auth = AuthState()
    else:
        cookie = request.headers.get("cookie", "")
        request.state.auth = await probe(cookie)
    return await call_next(request)


@app.middleware("http")
async def read_only_guard(request: Request, call_next):
    if settings().read_only and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return JSONResponse(
            {"error": "read_only", "detail": "this instance is public read-only; mutations disabled"},
            status_code=403,
        )
    return await call_next(request)


STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(rules.router)
app.include_router(budgets.router)
app.include_router(wealth.router)


@app.get("/readonly")
async def readonly_state() -> dict:
    return {"read_only": settings().read_only}


@app.get("/login")
async def login(request: Request, rd: str | None = None):
    cfg = settings()
    target = rd or str(request.headers.get("referer") or f"https://{cfg.forwarded_host}/")
    if not target.startswith("https://" + cfg.forwarded_host):
        target = f"https://{cfg.forwarded_host}/"
    url = f"{cfg.auth_portal}/?rd={urllib.parse.quote(target, safe='')}"
    return RedirectResponse(url, status_code=302)


@app.get("/logout")
async def logout(request: Request):
    cfg = settings()
    url = f"{cfg.auth_portal}/logout?rd=https%3A%2F%2F{cfg.forwarded_host}%2F"
    return RedirectResponse(url, status_code=302)


@app.get("/whoami")
async def whoami(request: Request) -> dict:
    a = request.state.auth
    return {"authed": a.authed, "user": a.user, "name": a.name, "email": a.email}


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
