import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import engine, session
from .models import Base
from .parsers.seed_rules import seed_default_rules
from .routes import api, pages, rules

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
    log.info("ofin_started")
    yield


app = FastAPI(title="ofin", version="0.2.0", lifespan=lifespan)

STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(rules.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
