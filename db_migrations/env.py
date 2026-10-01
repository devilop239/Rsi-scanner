"""Alembic environment configuration.

Supports async SQLAlchemy engines. DATABASE_URL is read from the application
settings so there is a single source of truth.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Load all models so Alembic can autogenerate migrations
# ---------------------------------------------------------------------------
from src.infra.database import Base  # noqa: F401
import src.infra.models  # noqa: F401  — side-effect: registers models with Base

# ---------------------------------------------------------------------------
# Alembic Config object (provides access to alembic.ini)
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Allow overriding DATABASE_URL from the environment / .env file.
# This is important so CI, tests, and production all use the correct URL
# without editing alembic.ini.
def get_url() -> str:
    """Read DATABASE_URL from environment, falling back to SQLite dev DB.

    Deliberately avoids importing src.infra.config (which pulls in
    pydantic-settings) so that alembic works even when run from the system
    Python or before the venv is fully set up.
    """
    import os
    from pathlib import Path

    # Manually parse .env so standalone `alembic upgrade head` picks up DATABASE_URL
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # In development mode, fall back to SQLite if the PostgreSQL host is unreachable
    env = os.environ.get("ENVIRONMENT", "development")
    if env == "development" and "asyncpg" in url:
        import socket
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
            host = parsed.hostname or ""
            port = parsed.port or 5432
            socket.getaddrinfo(host, port)
        except (socket.gaierror, OSError):
            url = "sqlite+aiosqlite:///./dev.db"

    return url

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection needed, emit SQL)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
