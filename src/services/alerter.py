"""Alerter Service — deduplication logic and Telegram message dispatch.

Responsibilities:
  - Check if an alert was already sent for this (symbol, zone) within cooldown
  - Format oversold/overbought signals into an HTML Telegram message
  - Split messages that exceed Telegram's 4096-character limit
  - Handle TelegramRetryAfter and blocked/deactivated users gracefully
  - Record every dispatched alert in alerts_sent for deduplication
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.config import settings
from src.infra.database import DatabaseManager
from src.infra.logging import get_logger
from src.infra.models import AlertSent, Signal, SignalType, Subscriber
from src.services.scanner import ScanResult, SignalRecord

log = get_logger(__name__)

_TELEGRAM_MAX_CHARS = 4096


class AlerterService:
    """Handles deduplication and Telegram broadcast of scan signals."""

    def __init__(self, bot: Bot, db: DatabaseManager) -> None:
        self._bot = bot
        self._db = db

    async def process_and_send(self, scan_result: ScanResult) -> int:
        """Filter signals through dedup, format, and broadcast to subscribers.

        Returns:
            Number of distinct alert messages dispatched.
        """
        if not scan_result.signals:
            log.info("no_signals_to_alert", trading_date=str(scan_result.trading_date))
            return 0

        # Separate into oversold / overbought
        oversold = [s for s in scan_result.signals if s.signal_type == "OVERSOLD"]
        overbought = [s for s in scan_result.signals if s.signal_type == "OVERBOUGHT"]

        # Dedup check: filter out signals already alerted within cooldown
        async with self._db.session() as session:
            oversold = [s for s in oversold if await self._should_alert(session, s)]
            overbought = [s for s in overbought if await self._should_alert(session, s)]

        if not oversold and not overbought:
            log.info("all_signals_deduplicated", trading_date=str(scan_result.trading_date))
            return 0

        # Format message(s)
        messages = self._format_messages(
            oversold, overbought, scan_result.trading_date
        )

        # Get active subscribers
        async with self._db.session() as session:
            result = await session.execute(
                select(Subscriber).where(Subscriber.is_active == True)  # noqa: E712
            )
            subscribers = list(result.scalars().all())

        if not subscribers:
            log.warning("no_active_subscribers")
            return 0

        dispatched = 0
        all_new_signals = oversold + overbought

        for message in messages:
            sent_count = await self._broadcast(message, subscribers)
            dispatched += 1 if sent_count > 0 else 0

        # Record alerts as sent (for deduplication)
        async with self._db.session() as session:
            for sig in all_new_signals:
                await self._record_alert_sent(session, sig, len(subscribers))

        log.info(
            "alerts_dispatched",
            messages=dispatched,
            subscribers=len(subscribers),
            oversold=len(oversold),
            overbought=len(overbought),
        )
        return dispatched

    async def _should_alert(self, session: AsyncSession, sig: SignalRecord) -> bool:
        """Return True if this signal has not been alerted within the cooldown period."""
        from src.infra.models import Symbol

        # Get symbol_id
        sym_result = await session.execute(
            select(Symbol.id).where(Symbol.ticker == sig.ticker)
        )
        symbol_id = sym_result.scalar_one_or_none()
        if symbol_id is None:
            return True  # Unknown symbol — allow alert

        cutoff = sig.trading_date - timedelta(days=settings.alert_cooldown_days)
        existing = await session.execute(
            select(AlertSent).where(
                AlertSent.symbol_id == symbol_id,
                AlertSent.signal_type == SignalType(sig.signal_type),
                AlertSent.alert_date >= cutoff,
            )
        )
        return existing.scalar_one_or_none() is None

    async def _record_alert_sent(
        self,
        session: AsyncSession,
        sig: SignalRecord,
        subscriber_count: int,
    ) -> None:
        from src.infra.models import Symbol

        sym_result = await session.execute(
            select(Symbol.id).where(Symbol.ticker == sig.ticker)
        )
        symbol_id = sym_result.scalar_one_or_none()
        if symbol_id is None:
            return

        session.add(AlertSent(
            symbol_id=symbol_id,
            signal_type=SignalType(sig.signal_type),
            alert_date=sig.trading_date,
            sent_at=datetime.now(timezone.utc),
            chat_ids_notified=subscriber_count,
        ))

    def _format_messages(
        self,
        oversold: list[SignalRecord],
        overbought: list[SignalRecord],
        trading_date: date,
    ) -> list[str]:
        """Build one or more HTML messages, splitting if > 4096 chars."""
        lines: list[str] = []
        date_str = trading_date.strftime("%d %b %Y")
        lines.append(f"📊 <b>Nifty 50 StochRSI Alert — {date_str}</b>\n")

        if oversold:
            lines.append(f"🟢 <b>OVERSOLD</b>  (StochRSI %K &lt; {settings.stoch_low})\n")
            for s in oversold:
                lines.append(
                    f"• <code>{s.ticker.replace('.NS', '')}</code>  {s.company_name}\n"
                    f"  Close: ₹{s.close:,.2f}  |  RSI: {s.rsi:.1f}  |"
                    f"  %K: {s.stoch_k:.1f}  |  %D: {s.stoch_d:.1f}\n"
                )

        if overbought:
            if oversold:
                lines.append("─" * 30 + "\n")
            lines.append(f"🔴 <b>OVERBOUGHT</b>  (StochRSI %K &gt; {settings.stoch_high})\n")
            for s in overbought:
                lines.append(
                    f"• <code>{s.ticker.replace('.NS', '')}</code>  {s.company_name}\n"
                    f"  Close: ₹{s.close:,.2f}  |  RSI: {s.rsi:.1f}  |"
                    f"  %K: {s.stoch_k:.1f}  |  %D: {s.stoch_d:.1f}\n"
                )

        disclaimer = (
            "\n─" * 15 + "\n"
            "⚠️ <i>For informational purposes only. Not investment advice. "
            "Always do your own research.</i>"
        )
        lines.append(disclaimer)

        # Combine and split if needed
        full = "".join(lines)
        return self._split_message(full)

    def _split_message(self, text: str) -> list[str]:
        """Split a long message at newline boundaries under 4096 chars."""
        if len(text) <= _TELEGRAM_MAX_CHARS:
            return [text]

        parts: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in text.split("\n"):
            line_with_nl = line + "\n"
            if current_len + len(line_with_nl) > _TELEGRAM_MAX_CHARS - 10:
                parts.append("".join(current))
                current = [line_with_nl]
                current_len = len(line_with_nl)
            else:
                current.append(line_with_nl)
                current_len += len(line_with_nl)

        if current:
            parts.append("".join(current))

        return parts

    async def _broadcast(self, message: str, subscribers: list[Subscriber]) -> int:
        """Send a message to all active subscribers. Returns count successfully sent."""
        import asyncio

        sent = 0
        for sub in subscribers:
            try:
                await self._bot.send_message(
                    chat_id=sub.chat_id,
                    text=message,
                    parse_mode="HTML",
                )
                sent += 1
            except TelegramRetryAfter as exc:
                log.warning("telegram_rate_limited", retry_after=exc.retry_after)
                await asyncio.sleep(exc.retry_after + 1)
                try:
                    await self._bot.send_message(
                        chat_id=sub.chat_id, text=message, parse_mode="HTML"
                    )
                    sent += 1
                except Exception:
                    pass
            except TelegramForbiddenError:
                # User blocked the bot — mark as inactive
                log.info("user_blocked_bot", chat_id=sub.chat_id)
                async with self._db.session() as session:
                    sub_obj = await session.get(Subscriber, sub.telegram_user_id)
                    if sub_obj:
                        sub_obj.is_active = False
            except Exception as exc:
                log.error("telegram_send_error", chat_id=sub.chat_id, error=str(exc))

        return sent
