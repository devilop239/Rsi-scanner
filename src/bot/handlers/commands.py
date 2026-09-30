"""Telegram bot command handlers — all bot commands in one module.

Uses aiogram 3.x patterns: Router, filters, CallbackData factories.
No aiogram 2.x patterns.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select, desc

from src.data.calendar import is_trading_day, last_trading_day
from src.infra.config import settings
from src.infra.database import DatabaseManager
from src.infra.logging import get_logger
from src.infra.models import IndicatorValue, ScanRun, ScanStatus, Signal, SignalType, Subscriber, Symbol, UserRole

log = get_logger(__name__)

router = Router(name="commands")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_or_create_subscriber(
    session: "AsyncSession",  # type: ignore[name-defined]
    message: Message,
    *,
    activate: bool = True,
) -> Subscriber:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401
    user = message.from_user
    assert user is not None

    existing = await session.get(Subscriber, user.id)
    if existing is None:
        role = UserRole.ADMIN if user.id in settings.admin_ids else UserRole.USER
        existing = Subscriber(
            telegram_user_id=user.id,
            chat_id=message.chat.id,
            username=user.username,
            first_name=user.first_name,
            role=role,
            is_active=activate,
        )
        session.add(existing)
    else:
        existing.last_seen_at = datetime.now(timezone.utc)
        if activate:
            existing.is_active = True
    return existing


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------


@router.message(Command("start"))
async def cmd_start(message: Message, db: DatabaseManager) -> None:
    assert message.from_user is not None
    async with db.session() as session:
        sub = await _get_or_create_subscriber(session, message, activate=True)

    name = message.from_user.first_name or "Trader"
    await message.answer(
        f"👋 Welcome, <b>{name}</b>!\n\n"
        "I scan all <b>Nifty 50</b> stocks daily and alert you when "
        "Stochastic RSI enters extreme zones.\n\n"
        "🟢 <b>Oversold</b> — %K below 20\n"
        "🔴 <b>Overbought</b> — %K above 80\n\n"
        "You're now subscribed to daily alerts. Use /help to see all commands.\n\n"
        "<i>⚠️ Not investment advice.</i>",
        parse_mode="HTML",
    )
    log.info("user_started", user_id=message.from_user.id)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📋 <b>Available Commands</b>\n\n"
        "📊 <b>Scans & Alerts</b>\n"
        "  /scan — Run a scan now (uses cached data if already ran today)\n"
        "  /oversold — Show currently oversold stocks\n"
        "  /overbought — Show currently overbought stocks\n"
        "  /rsi &lt;SYMBOL&gt; — Get StochRSI for a stock (e.g. /rsi RELIANCE)\n\n"
        "⚙️ <b>Subscription</b>\n"
        "  /subscribe — Enable daily alerts\n"
        "  /unsubscribe — Disable daily alerts\n"
        "  /settings — View current indicator settings\n\n"
        "ℹ️ <b>Info</b>\n"
        "  /status — Last scan time and data-source health\n"
        "  /help — Show this message\n\n"
        "<i>⚠️ For informational purposes only. Not investment advice.</i>"
    )
    await message.answer(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /subscribe / /unsubscribe
# ---------------------------------------------------------------------------


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, db: DatabaseManager) -> None:
    async with db.session() as session:
        sub = await _get_or_create_subscriber(session, message, activate=True)

    await message.answer(
        "✅ <b>Subscribed!</b> You'll receive daily StochRSI alerts after market close (16:30 IST).\n\n"
        "Use /unsubscribe to stop.",
        parse_mode="HTML",
    )


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, db: DatabaseManager) -> None:
    assert message.from_user is not None
    async with db.session() as session:
        sub = await session.get(Subscriber, message.from_user.id)
        if sub:
            sub.is_active = False

    await message.answer(
        "🔕 <b>Unsubscribed.</b> You won't receive automated alerts anymore.\n\n"
        "Use /subscribe to re-enable.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /scan
# ---------------------------------------------------------------------------


@router.message(Command("scan"))
async def cmd_scan(message: Message, db: DatabaseManager, bot: Bot) -> None:
    assert message.from_user is not None

    # Import here to avoid circular at module level
    from src.data.yfinance_provider import YFinanceProvider
    from src.services.alerter import AlerterService
    from src.services.scanner import ScannerService

    processing_msg = await message.answer("🔍 Running scan… this may take 30–60 seconds.")

    try:
        provider = YFinanceProvider()
        scanner = ScannerService(provider=provider, db=db)
        result = await scanner.run_scan(force=True)

        if result.was_skipped:
            await processing_msg.edit_text(
                "ℹ️ Scan already completed for today. Use the results below.\n"
                "Send /oversold or /overbought to see current signals."
            )
            return

        text = (
            f"✅ <b>Scan Complete</b> — {result.trading_date.strftime('%d %b %Y')}\n\n"
            f"📈 Processed: {result.processed} stocks\n"
            f"❌ Failed: {result.failed} stocks\n"
            f"⚠️ Skipped (insufficient data): {result.skipped} stocks\n"
            f"🚨 Signals found: {len(result.signals)}\n"
            f"⏱ Duration: {result.duration_ms / 1000:.1f}s"
        )

        if result.signals:
            alerter = AlerterService(bot=bot, db=db)
            dispatched = await alerter.process_and_send(result)
            text += f"\n📤 Alerts sent: {dispatched}"

        await processing_msg.edit_text(text, parse_mode="HTML")

    except Exception as exc:
        log.error("scan_command_failed", error=str(exc))
        await processing_msg.edit_text(
            f"❌ Scan failed: <code>{exc}</code>", parse_mode="HTML"
        )


# ---------------------------------------------------------------------------
# /oversold / /overbought
# ---------------------------------------------------------------------------


async def _show_zone_signals(message: Message, db: DatabaseManager, signal_type: SignalType) -> None:
    trading_date = last_trading_day()
    emoji = "🟢" if signal_type == SignalType.OVERSOLD else "🔴"
    zone_name = "OVERSOLD" if signal_type == SignalType.OVERSOLD else "OVERBOUGHT"
    threshold = f"%K < {settings.stoch_low}" if signal_type == SignalType.OVERSOLD else f"%K > {settings.stoch_high}"

    async with db.session() as session:
        result = await session.execute(
            select(Signal, Symbol)
            .join(Symbol, Signal.symbol_id == Symbol.id)
            .where(
                Signal.signal_date == trading_date,
                Signal.signal_type == signal_type,
            )
            .order_by(Signal.stoch_k)
        )
        rows = result.all()

    if not rows:
        await message.answer(
            f"{emoji} No <b>{zone_name}</b> signals found for {trading_date.strftime('%d %b %Y')}.\n\n"
            "Run /scan to refresh data.",
            parse_mode="HTML",
        )
        return

    lines = [f"{emoji} <b>{zone_name}</b> ({threshold}) — {trading_date.strftime('%d %b %Y')}\n"]
    for signal, symbol in rows:
        ticker = symbol.ticker.replace(".NS", "")
        lines.append(
            f"\n• <code>{ticker}</code>  {symbol.company_name}\n"
            f"  Close: ₹{signal.close:,.2f}  |  RSI: {signal.rsi:.1f}  |"
            f"  %K: {signal.stoch_k:.1f}  |  %D: {signal.stoch_d:.1f}"
        )

    lines.append("\n\n<i>⚠️ Not investment advice.</i>")
    await message.answer("".join(lines), parse_mode="HTML")


@router.message(Command("oversold"))
async def cmd_oversold(message: Message, db: DatabaseManager) -> None:
    await _show_zone_signals(message, db, SignalType.OVERSOLD)


@router.message(Command("overbought"))
async def cmd_overbought(message: Message, db: DatabaseManager) -> None:
    await _show_zone_signals(message, db, SignalType.OVERBOUGHT)


# ---------------------------------------------------------------------------
# /rsi <SYMBOL>
# ---------------------------------------------------------------------------


@router.message(Command("rsi"))
async def cmd_rsi(message: Message, command: CommandObject, db: DatabaseManager) -> None:
    if not command.args:
        await message.answer("Usage: /rsi <SYMBOL>  e.g. /rsi RELIANCE")
        return

    raw = command.args.strip().upper()
    ticker = raw if raw.endswith(".NS") else f"{raw}.NS"
    trading_date = last_trading_day()

    async with db.session() as session:
        sym_result = await session.execute(
            select(Symbol).where(Symbol.ticker == ticker)
        )
        symbol = sym_result.scalar_one_or_none()

        if symbol is None:
            await message.answer(
                f"❌ Symbol <code>{raw}</code> not found in Nifty 50. "
                "Check the ticker and try again.",
                parse_mode="HTML",
            )
            return

        iv_result = await session.execute(
            select(IndicatorValue)
            .where(
                IndicatorValue.symbol_id == symbol.id,
                IndicatorValue.value_date == trading_date,
            )
        )
        iv = iv_result.scalar_one_or_none()

    if iv is None:
        await message.answer(
            f"ℹ️ No data for <code>{raw}</code> on {trading_date.strftime('%d %b %Y')}. "
            "Run /scan to fetch today's data.",
            parse_mode="HTML",
        )
        return

    # Determine zone
    zone = ""
    if iv.stoch_k is not None:
        if iv.stoch_k < settings.stoch_low:
            zone = "  🟢 <b>OVERSOLD</b>"
        elif iv.stoch_k > settings.stoch_high:
            zone = "  🔴 <b>OVERBOUGHT</b>"

    await message.answer(
        f"📊 <b>{raw}</b> — {symbol.company_name}\n"
        f"<i>{trading_date.strftime('%d %b %Y')}</i>{zone}\n\n"
        f"RSI(14):  <code>{iv.rsi:.2f}</code>\n"
        f"Stoch Raw:  <code>{iv.stoch_raw:.2f if iv.stoch_raw else 'N/A'}</code>\n"
        f"%K:  <code>{iv.stoch_k:.2f if iv.stoch_k else 'N/A'}</code>\n"
        f"%D:  <code>{iv.stoch_d:.2f if iv.stoch_d else 'N/A'}</code>\n\n"
        f"Thresholds: OS &lt; {settings.stoch_low} | OB &gt; {settings.stoch_high}\n"
        "<i>⚠️ Not investment advice.</i>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /settings
# ---------------------------------------------------------------------------


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    await message.answer(
        "⚙️ <b>Current Indicator Settings</b>\n\n"
        f"RSI Length:  <code>{settings.rsi_length}</code>\n"
        f"Stoch Length:  <code>{settings.stoch_length}</code>\n"
        f"%K Smooth:  <code>{settings.stoch_k_smooth}</code>\n"
        f"%D Smooth:  <code>{settings.stoch_d_smooth}</code>\n"
        f"Signal Line:  <code>%{settings.signal_line.upper()}</code>\n\n"
        f"🟢 Oversold threshold:  <code>&lt; {settings.stoch_low}</code>\n"
        f"🔴 Overbought threshold:  <code>&gt; {settings.stoch_high}</code>\n\n"
        f"Alert cooldown:  <code>{settings.alert_cooldown_days} days</code>\n"
        f"Alert on exit:  <code>{'Yes' if settings.alert_on_exit else 'No'}</code>\n\n"
        "<i>To change settings, update the environment variables and redeploy.</i>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


@router.message(Command("status"))
async def cmd_status(message: Message, db: DatabaseManager) -> None:
    assert message.from_user is not None

    async with db.session() as session:
        # Last scan run
        result = await session.execute(
            select(ScanRun)
            .order_by(desc(ScanRun.started_at))
            .limit(1)
        )
        last_run = result.scalar_one_or_none()

        # Subscriber count
        sub_result = await session.execute(
            select(Subscriber).where(Subscriber.is_active == True)  # noqa: E712
        )
        sub_count = len(sub_result.scalars().all())

    today_is_trading = is_trading_day()
    trading_day_status = "✅ Yes" if today_is_trading else "🔕 No (holiday/weekend)"

    if last_run:
        duration = f"{last_run.duration_ms / 1000:.1f}s" if last_run.duration_ms else "N/A"
        status_emoji = {"SUCCESS": "✅", "FAILED": "❌", "RUNNING": "⏳", "SKIPPED": "⏭"}.get(
            last_run.status.value, "❓"
        )
        last_run_text = (
            f"{status_emoji} <b>{last_run.status.value}</b>\n"
            f"  Date: {last_run.trading_date}\n"
            f"  Processed: {last_run.items_processed} | Failed: {last_run.items_failed}\n"
            f"  Signals: {last_run.signals_found} | Duration: {duration}"
        )
    else:
        last_run_text = "No scans have run yet."

    await message.answer(
        f"📡 <b>System Status</b>\n\n"
        f"Today is trading day: {trading_day_status}\n\n"
        f"<b>Last Scan Run:</b>\n{last_run_text}\n\n"
        f"Active subscribers: <code>{sub_count}</code>\n"
        f"Environment: <code>{settings.environment}</code>\n"
        f"Mode: <code>{'Webhook' if settings.is_webhook_mode else 'Long-Polling'}</code>",
        parse_mode="HTML",
    )
