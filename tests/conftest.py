"""Pytest configuration and shared fixtures.

Uses SQLite in-memory for fast, isolated database tests.
All network calls must be mocked (use pytest-mock or unittest.mock).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set test environment before any application imports
os.environ.setdefault("BOT_TOKEN", "0:test-token-for-testing-only")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("ADMIN_IDS", "12345")

from src.infra.database import Base  # noqa: E402
from src.infra.models import Symbol  # noqa: E402 — import to register models


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create a fresh in-memory SQLite engine per test function."""
    from sqlalchemy.ext.asyncio import create_async_engine as _create

    _engine = _create(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional test session that rolls back after each test."""
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def sample_symbol() -> dict[str, str]:
    """Return a minimal symbol dict for test fixtures."""
    return {"ticker": "RELIANCE.NS", "company_name": "Reliance Industries Ltd"}
