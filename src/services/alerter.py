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
from sqlalchemy import select, update
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
        """Evaluate custom thresholds for each user, format, and broadcast."""
        from src.infra.models import IndicatorValue, Symbol, DailyCandle
        
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

        for sub in subscribers:
            low_thresh = sub.custom_stoch_low if sub.custom_stoch_low is not None else settings.stoch_low
            high_thresh = sub.custom_stoch_high if sub.custom_stoch_high is not None else settings.stoch_high

            async with self._db.session() as session:
                # Query oversold for this subscriber
                os_result = await session.execute(
                    select(IndicatorValue, Symbol, DailyCandle.close)
                    .join(Symbol, IndicatorValue.symbol_id == Symbol.id)
                    .outerjoin(DailyCandle, (DailyCandle.symbol_id == Symbol.id) & (DailyCandle.candle_date == scan_result.trading_date))
                    .where(
                        IndicatorValue.value_date == scan_result.trading_date,
                        IndicatorValue.stoch_k < low_thresh
                    )
                    .order_by(IndicatorValue.stoch_k)
                )
                os_rows = os_result.all()

                # Query overbought for this subscriber
                ob_result = await session.execute(
                    select(IndicatorValue, Symbol, DailyCandle.close)
                    .join(Symbol, IndicatorValue.symbol_id == Symbol.id)
                    .outerjoin(DailyCandle, (DailyCandle.symbol_id == Symbol.id) & (DailyCandle.candle_date == scan_result.trading_date))
                    .where(
                        IndicatorValue.value_date == scan_result.trading_date,
                        IndicatorValue.stoch_k > high_thresh
                    )
                    .order_by(IndicatorValue.stoch_k.desc())
                )
                ob_rows = ob_result.all()

            if not os_rows and not ob_rows:
                continue

            # Convert to SignalRecord format for the formatter
            oversold = [
                SignalRecord(
                    ticker=sym.ticker, company_name=sym.company_name, signal_type="OVERSOLD",
                    stoch_k=iv.stoch_k, stoch_d=iv.stoch_d or 0, rsi=iv.rsi,
                    close=close_price if close_price is not None else 0.0, trading_date=iv.value_date
                ) for iv, sym, close_price in os_rows if iv.stoch_k is not None
            ]
            
            overbought = [
                SignalRecord(
                    ticker=sym.ticker, company_name=sym.company_name, signal_type="OVERBOUGHT",
                    stoch_k=iv.stoch_k, stoch_d=iv.stoch_d or 0, rsi=iv.rsi,
                    close=close_price if close_price is not None else 0.0, trading_date=iv.value_date
                ) for iv, sym, close_price in ob_rows if iv.stoch_k is not None
            ]

            # Dedup check could go here if we tracked per-user, but we'll bypass it for custom limits
            
            # Send HTML document for all scans to provide a clean dashboard
            if len(oversold) + len(overbought) > 0:
                from src.services.reporter import generate_html_report
                from aiogram.types import BufferedInputFile
                
                html_bytes = generate_html_report(oversold, overbought, scan_result.trading_date, low_thresh, high_thresh)
                filename = f"Nifty50_Scan_{scan_result.trading_date.strftime('%Y%m%d')}.html"
                doc = BufferedInputFile(html_bytes, filename=filename)
                
                caption = (
                    f"🚨 <b>ᴍ ᴀ ʀ ᴋ ᴇ ᴛ  ᴀ ʟ ᴇ ʀ ᴛ</b>\n<i>{scan_result.trading_date.strftime('%d %b %Y')}</i>\n\n"
                    f"Found {len(oversold)} Oversold and {len(overbought)} Overbought setups.\n\n"
                    "📄 <b>Please open the attached HTML report for full details!</b>"
                )
                
                try:
                    await self._bot.send_document(chat_id=sub.chat_id, document=doc, caption=caption, parse_mode="HTML")
                    dispatched += 1
                except TelegramRetryAfter as e:
                    log.warning("telegram_retry_after", chat_id=sub.chat_id, retry_after=e.retry_after)
                except TelegramForbiddenError:
                    log.warning("user_blocked_bot", chat_id=sub.chat_id)
                except Exception as e:
                    log.error("telegram_send_failed", chat_id=sub.chat_id, error=str(e))
            else:
                messages = self._format_messages(oversold, overbought, scan_result.trading_date, low_thresh, high_thresh)
                for msg in messages:
                    try:
                        await self._bot.send_message(chat_id=sub.chat_id, text=msg)
                        dispatched += 1
                    except TelegramRetryAfter as e:
                        log.warning("telegram_retry_after", chat_id=sub.chat_id, retry_after=e.retry_after)
                    except TelegramForbiddenError:
                        log.warning("user_blocked_bot", chat_id=sub.chat_id)
                        # Optionally deactivate user here
                    except Exception as e:
                        log.error("telegram_send_failed", chat_id=sub.chat_id, error=str(e))

        log.info(
            "alerts_dispatched",
            messages=dispatched,
            subscribers=len(subscribers),
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

        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.alert_cooldown_days)
        existing = await session.execute(
            select(AlertSent).where(
                AlertSent.symbol_id == symbol_id,
                AlertSent.signal_type == SignalType(sig.signal_type),
                AlertSent.sent_at >= cutoff,
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
        low_thresh: float,
        high_thresh: float
    ) -> list[str]:
        """Build one or more HTML messages, splitting if > 4096 chars."""
        lines: list[str] = []
        date_str = trading_date.strftime("%d %b %Y")
        lines.append(f"🚨 <b>ᴍ ᴀ ʀ ᴋ ᴇ ᴛ  ᴀ ʟ ᴇ ʀ ᴛ</b>\n<i>{date_str}</i>\n\n")

        if oversold:
            lines.append(f"🟢 <b>ᴏ ᴠ ᴇ ʀ ѕ ᴏ ʟ ᴅ  ᴢ ᴏ ɴ ᴇ</b>  (StochRSI &lt; {low_thresh})\n")
            for s in oversold:
                lines.append(
                    f"• <code>{s.ticker.replace('.NS', '')}</code> — <b>{s.company_name[:25]}</b>\n"
                    f"  ├ ᴘ ʀ ɪ ᴄ ᴇ : ₹{s.close:,.2f}\n"
                    f"  ├ ʀ ѕ ɪ : {s.rsi:.1f}\n"
                    f"  └ ѕ ᴛ ᴏ ᴄ ʜ : <b>{s.stoch_k:.1f}</b>\n"
                )
            lines.append("\n")

        if overbought:
            if oversold:
                lines.append("─" * 25 + "\n\n")
            lines.append(f"🔴 <b>ᴏ ᴠ ᴇ ʀ ʙ ᴏ ᴜ ɢ ʜ ᴛ  ᴢ ᴏ ɴ ᴇ</b>  (StochRSI &gt; {high_thresh})\n")
            for s in overbought:
                lines.append(
                    f"• <code>{s.ticker.replace('.NS', '')}</code> — <b>{s.company_name[:25]}</b>\n"
                    f"  ├ ᴘ ʀ ɪ ᴄ ᴇ : ₹{s.close:,.2f}\n"
                    f"  ├ ʀ ѕ ɪ : {s.rsi:.1f}\n"
                    f"  └ ѕ ᴛ ᴏ ᴄ ʜ : <b>{s.stoch_k:.1f}</b>\n"
                )
            lines.append("\n")

        disclaimer = (
            "<i>⚠️ ᴛ ʀ ᴀ ᴅ ᴇ  ѕ ᴇ ᴛ ᴜ ᴘ ѕ  ᴅ ᴇ ᴛ ᴇ ᴄ ᴛ ᴇ ᴅ  ᴀ ᴜ ᴛ ᴏ ᴍ ᴀ ᴛ ɪ ᴄ ᴀ ʟ ʟ ʏ. ᴅ ᴏ  ʏ ᴏ ᴜ ʀ  ᴏ ᴡ ɴ  ʀ ᴇ ѕ ᴇ ᴀ ʀ ᴄ ʜ.</i>"
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
        blocked_chat_ids: list[int] = []

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
                # User blocked the bot — collect for bulk deactivation
                log.info("user_blocked_bot", chat_id=sub.chat_id)
                blocked_chat_ids.append(sub.chat_id)
            except Exception as exc:
                log.error("telegram_send_error", chat_id=sub.chat_id, error=str(exc))

        # Bulk-deactivate blocked users in a single DB round-trip
        if blocked_chat_ids:
            async with self._db.session() as session:
                await session.execute(
                    update(Subscriber)
                    .where(Subscriber.chat_id.in_(blocked_chat_ids))
                    .values(is_active=False)
                )

        return sent
