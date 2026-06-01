from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings


_engine = create_async_engine(settings().database_url, pool_pre_ping=True, future=True)
_Session = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


def engine():
    return _engine


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    async with _Session() as s:
        yield s


async def session_dep() -> AsyncIterator[AsyncSession]:
    async with _Session() as s:
        yield s
