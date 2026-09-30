"""Per-user rate limiting middleware for the Telegram bot.

Allows at most `rate_limit_messages` messages per `rate_limit_window_seconds`
using an in-memory sliding window. No external state (Redis) required.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from src.infra.config import settings
from src.infra.logging import get_logger

log = get_logger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """Sliding-window rate limiter per Telegram user."""

    def __init__(self) -> None:
        # user_id → deque of timestamps (seconds since epoch)
        self._windows: dict[int, deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[..., Any],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        # Admins bypass rate limiting
        user_id = event.from_user.id
        if user_id in settings.admin_ids:
            return await handler(event, data)

        now = time.monotonic()
        window = self._windows[user_id]
        cutoff = now - settings.rate_limit_window_seconds

        # Remove timestamps outside the window
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= settings.rate_limit_messages:
            log.info("rate_limit_exceeded", user_id=user_id)
            await event.answer(
                f"⏳ Too many requests. Please wait a moment and try again.\n"
                f"(Limit: {settings.rate_limit_messages} messages per "
                f"{settings.rate_limit_window_seconds}s)"
            )
            return None  # Swallow the event

        window.append(now)
        return await handler(event, data)
