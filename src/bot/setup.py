"""Bot setup — creates the Bot, Dispatcher, and wires everything together."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.handlers.commands import router as commands_router
from src.bot.middlewares.rate_limit import RateLimitMiddleware
from src.infra.config import settings
from src.infra.database import DatabaseManager
from src.infra.logging import get_logger

log = get_logger(__name__)


def create_bot() -> Bot:
    """Create an aiogram Bot instance with HTML as the default parse mode."""
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(db: DatabaseManager) -> Dispatcher:
    """Create and configure the aiogram Dispatcher.

    Registers:
      - Rate limiting middleware
      - All command handlers
      - db and bot as workflow_data (injected into handlers via DI)
    """
    dp = Dispatcher()

    # Middleware — applied to all incoming messages
    dp.message.middleware(RateLimitMiddleware())

    # Include routers
    dp.include_router(commands_router)

    # Inject DatabaseManager into all handlers via workflow_data
    dp["db"] = db

    return dp


async def setup_webhook(bot: Bot) -> None:
    """Register the webhook with Telegram in production mode."""
    await bot.set_webhook(
        url=settings.full_webhook_url,
        secret_token=settings.webhook_secret or None,
        drop_pending_updates=True,
    )
    log.info("webhook_set", url=settings.full_webhook_url)


async def remove_webhook(bot: Bot) -> None:
    """Remove the webhook on shutdown."""
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("webhook_removed")
