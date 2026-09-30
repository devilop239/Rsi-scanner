"""SQLAlchemy async database engine, session factory, and declarative base.

Supports both PostgreSQL (production) and SQLite (development/testing)
via a single DATABASE_URL environment variable.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


def build_engine(database_url: str) -> "AsyncEngine":  # type: ignore[name-defined]
    """Create an async engine from the given URL.

    PostgreSQL: uses asyncpg with a connection pool.
    SQLite: uses aiosqlite with check_same_thread disabled.
    """
    from sqlalchemy.ext.asyncio import create_async_engine as _create

    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    pool_kwargs: dict[str, object] = (
        {}
        if is_sqlite
        else {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_timeout": 30,
            "pool_recycle": 1800,
        }
    )

    return _create(
        database_url,
        echo=False,
        connect_args=connect_args,
        **pool_kwargs,  # type: ignore[arg-type]
    )


class DatabaseManager:
    """Manages the async engine and session factory lifecycle."""

    def __init__(self, database_url: str) -> None:
        self._engine = build_engine(database_url)
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )

    @property
    def engine(self) -> "AsyncEngine":  # type: ignore[name-defined]
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide a transactional async session context manager."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        """Dispose of the connection pool on shutdown."""
        await self._engine.dispose()


# Module-level instance — initialized in app lifespan.
_db_manager: DatabaseManager | None = None


def get_db_manager() -> DatabaseManager:
    """Return the module-level DatabaseManager. Must be initialized first."""
    if _db_manager is None:
        raise RuntimeError("DatabaseManager has not been initialized. Call init_db() first.")
    return _db_manager


def init_db(database_url: str) -> DatabaseManager:
    """Initialize the module-level DatabaseManager."""
    global _db_manager
    _db_manager = DatabaseManager(database_url)
    return _db_manager


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with get_db_manager().session() as session:
        yield session
