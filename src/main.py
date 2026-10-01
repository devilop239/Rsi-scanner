"""Application entry point.

Run locally:
    python src/main.py

Or as a module (after pip install -e .):
    python -m src.main
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

# Ensure project root is on sys.path when running `python src/main.py`
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import uvicorn
from fastapi import FastAPI

from src.infra.config import settings
from src.infra.database import init_db
from src.infra.logging import configure_logging, get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# App lifespan — startup and shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of all long-lived resources."""
    from src.bot.setup import create_bot, create_dispatcher, remove_webhook, setup_webhook

    # ── Startup ──────────────────────────────────────────────────────────────
    configure_logging(
        log_level=settings.log_level,
        json_logs=settings.is_production,
    )
    log.info("app_starting", environment=settings.environment)

    # Initialise database
    db = init_db(settings.database_url)
    app.state.db = db

    # Run Alembic migrations automatically on startup
    await _run_migrations()

    # Sync Nifty 50 constituents (falls back to bundled list if NSE is unreachable)
    try:
        from src.data.index_repo import sync_nifty_constituents
        async with db.session() as session:
            await sync_nifty_constituents(session)
    except Exception as exc:
        log.warning("constituent_sync_failed_on_startup", error=str(exc))

    # Create Telegram bot and dispatcher
    bot = create_bot()
    dp = create_dispatcher(db=db)

    # Inject bot into dispatcher workflow_data so handlers can use it
    dp["bot"] = bot

    app.state.bot = bot
    app.state.dp = dp

    if settings.is_webhook_mode:
        await setup_webhook(bot)
        log.info("running_in_webhook_mode", url=settings.full_webhook_url)
    else:
        # Long-polling for local development — runs in a background task
        polling_task = asyncio.create_task(
            dp.start_polling(bot, handle_signals=False)
        )
        log.info("running_in_polling_mode")

    log.info("app_ready", port=settings.port)

    yield  # ── Application is now running ──────────────────────────────────

    # ── Shutdown ─────────────────────────────────────────────────────────────
    log.info("app_shutting_down")

    if settings.is_webhook_mode:
        await remove_webhook(bot)
    else:
        # Cancel polling task
        try:
            polling_task.cancel()
            await asyncio.gather(polling_task, return_exceptions=True)
        except Exception:
            pass

    await bot.session.close()
    await db.close()
    log.info("app_stopped")


async def _run_migrations() -> None:
    """Apply pending Alembic migrations on startup using Alembic API."""
    import asyncio
    from alembic import command
    from alembic.config import Config

    try:
        alembic_cfg = Config("alembic.ini")
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        log.info("migrations_applied")
    except Exception as exc:
        log.error("migrations_exception", error=str(exc))


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    from src.api.routes import router

    app = FastAPI(
        title="Nifty 50 StochRSI Scanner",
        description="Automated scanner with Telegram alerts for Nifty 50 stocks.",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import os
    # Render automatically sets the PORT environment variable
    port = int(os.environ.get("PORT", 8000))
    
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,  # CRITICAL: Must be False on Render to avoid Out of Memory (512MB limit)
        log_level="info",
    )
