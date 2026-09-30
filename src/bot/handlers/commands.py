"""Telegram bot command handlers — all bot commands in one module.

Uses aiogram 3.x patterns: Router, filters, CallbackData factories.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, desc

from src.data.calendar import is_trading_day, last_trading_day
from src.infra.config import settings
from src.infra.database import DatabaseManager
from src.infra.logging import get_logger
from src.infra.models import IndicatorValue, ScanRun, ScanStatus, Signal, SignalType, Subscriber, Symbol, UserRole

log = get_logger(__name__)

router = Router(name="commands")


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Run Market Scan", callback_data="action_scan"),
            ],
            [
                InlineKeyboardButton(text="🟢 Oversold", callback_data="action_oversold"),
                InlineKeyboardButton(text="🔴 Overbought", callback_data="action_overbought"),
            ],
            [
                InlineKeyboardButton(text="📡 System Status", callback_data="action_status"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="action_settings"),
            ]
        ]
    )


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
        f"🌟 <b>Welcome to Nifty 50 Scanner, {name}!</b> 🌟\n\n"
        "I provide professional market scanning for <b>Nifty 50</b> stocks, delivering high-probability "
        "Stochastic RSI setups directly to your inbox.\n\n"
        "⚡ <b>ZONES:</b>\n"
        "🟢 <b>Oversold:</b> %K &lt; 20 (Potential Reversal)\n"
        "🔴 <b>Overbought:</b> %K &gt; 80 (Overextended)\n\n"
        "<i>⚠️ Data is delayed by 15 minutes.</i>\n\n"
        "Select an action below or use /help to see all commands.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )
    log.info("user_started", user_id=message.from_user.id)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📋 <b>COMMAND CENTER</b>\n\n"
        "<b>Market Scans</b>\n"
        "• /scan — Force a market scan now\n"
        "• /oversold — List oversold stocks\n"
        "• /overbought — List overbought stocks\n"
        "• /rsi &lt;SYMBOL&gt; — Get indicator values for a stock\n\n"
        "<b>Alerts & Settings</b>\n"
        "• /subscribe — Enable daily updates\n"
        "• /unsubscribe — Disable daily updates\n"
        "• /set_low &lt;val&gt; — Custom oversold limit (e.g. 20)\n"
        "• /set_high &lt;val&gt; — Custom overbought limit (e.g. 80)\n"
        "• /settings — View scanner configuration\n\n"
        "<b>System</b>\n"
        "• /status — Check server health & last sync\n\n"
        "<i>Use the interactive menu below to navigate:</i>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# /subscribe / /unsubscribe
# ---------------------------------------------------------------------------


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, db: DatabaseManager) -> None:
    async with db.session() as session:
        sub = await _get_or_create_subscriber(session, message, activate=True)

    await message.answer(
        "✅ <b>Alerts Activated!</b>\n\n"
        "You are now subscribed to automated market updates. "
        "I will notify you immediately when StochRSI extremes are detected.",
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
        "🔕 <b>Alerts Disabled.</b>\n\n"
        "You have been unsubscribed from automated market updates. "
        "Use /subscribe to resume.",
        parse_mode="HTML",
    )


@router.message(Command("set_low"))
async def cmd_set_low(message: Message, command: CommandObject, db: DatabaseManager) -> None:
    assert message.from_user is not None
    if not command.args:
        await message.answer("Usage: <code>/set_low &lt;number&gt;</code>\nExample: <code>/set_low 20</code>", parse_mode="HTML")
        return
    try:
        val = float(command.args)
    except ValueError:
        await message.answer("❌ Invalid number.", parse_mode="HTML")
        return

    async with db.session() as session:
        sub = await _get_or_create_subscriber(session, message)
        sub.custom_stoch_low = val
        await session.commit()

    await message.answer(f"✅ <b>Custom Oversold Threshold Set to {val}</b>\n\nYou will now receive alerts when StochRSI %K &lt; {val}.", parse_mode="HTML", reply_markup=main_menu_keyboard())


@router.message(Command("set_high"))
async def cmd_set_high(message: Message, command: CommandObject, db: DatabaseManager) -> None:
    assert message.from_user is not None
    if not command.args:
        await message.answer("Usage: <code>/set_high &lt;number&gt;</code>\nExample: <code>/set_high 80</code>", parse_mode="HTML")
        return
    try:
        val = float(command.args)
    except ValueError:
        await message.answer("❌ Invalid number.", parse_mode="HTML")
        return

    async with db.session() as session:
        sub = await _get_or_create_subscriber(session, message)
        sub.custom_stoch_high = val
        await session.commit()

    await message.answer(f"✅ <b>Custom Overbought Threshold Set to {val}</b>\n\nYou will now receive alerts when StochRSI %K &gt; {val}.", parse_mode="HTML", reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# Core Scan & Data Logic
# ---------------------------------------------------------------------------

async def _trigger_scan(db: DatabaseManager, bot: Bot) -> tuple[str, InlineKeyboardMarkup | None]:
    # Import here to avoid circular at module level
    from src.data.yfinance_provider import YFinanceProvider
    from src.services.alerter import AlerterService
    from src.services.scanner import ScannerService

    try:
        provider = YFinanceProvider()
        scanner = ScannerService(provider=provider, db=db)
        result = await scanner.run_scan(force=True)

        if result.was_skipped:
            return (
                "ℹ️ <b>Scan Complete.</b> Market data is already up-to-date.\n"
                "Use the menu below to view the latest signals.",
                main_menu_keyboard()
            )

        text = (
            f"✅ <b>MARKET SCAN SUCCESS</b> — {result.trading_date.strftime('%d %b %Y')}\n\n"
            f"📈 Processed: <b>{result.processed}</b> stocks\n"
            f"🚨 Signals: <b>{len(result.signals)}</b> found (Global thresholds)\n"
            f"⏱ Duration: <b>{result.duration_ms / 1000:.1f}s</b>"
        )

        alerter = AlerterService(bot=bot, db=db)
        dispatched = await alerter.process_and_send(result)
        text += f"\n📤 Dispatched to subscribers: {dispatched}"

        return text, main_menu_keyboard()

    except Exception as exc:
        log.error("scan_command_failed", error=str(exc))
        return f"❌ <b>Scan Failed</b>\n\n<code>{exc}</code>", None


async def _get_zone_text(db: DatabaseManager, user_id: int, signal_type: SignalType) -> str:
    from src.infra.models import DailyCandle
    trading_date = last_trading_day()
    emoji = "🟢" if signal_type == SignalType.OVERSOLD else "🔴"
    zone_name = "ᴏ ᴠ ᴇ ʀ ѕ ᴏ ʟ ᴅ  ᴢ ᴏ ɴ ᴇ" if signal_type == SignalType.OVERSOLD else "ᴏ ᴠ ᴇ ʀ ʙ ᴏ ᴜ ɢ ʜ ᴛ  ᴢ ᴏ ɴ ᴇ"
    
    async with db.session() as session:
        sub = await session.get(Subscriber, user_id)
        if sub and signal_type == SignalType.OVERSOLD:
            threshold_val = sub.custom_stoch_low if sub.custom_stoch_low is not None else settings.stoch_low
        elif sub and signal_type == SignalType.OVERBOUGHT:
            threshold_val = sub.custom_stoch_high if sub.custom_stoch_high is not None else settings.stoch_high
        else:
            threshold_val = settings.stoch_low if signal_type == SignalType.OVERSOLD else settings.stoch_high

    threshold = f"StochRSI &lt; {threshold_val}" if signal_type == SignalType.OVERSOLD else f"StochRSI &gt; {threshold_val}"

    async with db.session() as session:
        query = (
            select(IndicatorValue, Symbol, DailyCandle.close)
            .join(Symbol, IndicatorValue.symbol_id == Symbol.id)
            .outerjoin(DailyCandle, (DailyCandle.symbol_id == Symbol.id) & (DailyCandle.candle_date == trading_date))
            .where(IndicatorValue.value_date == trading_date)
        )
        if signal_type == SignalType.OVERSOLD:
            query = query.where(IndicatorValue.stoch_k < threshold_val).order_by(IndicatorValue.stoch_k)
        else:
            query = query.where(IndicatorValue.stoch_k > threshold_val).order_by(desc(IndicatorValue.stoch_k))

        result = await session.execute(query)
        rows = result.all()

    if not rows:
        raw_zone = "OVERSOLD" if signal_type == SignalType.OVERSOLD else "OVERBOUGHT"
        return (
            f"{emoji} <b>NO {raw_zone} SIGNALS</b>\n\n"
            f"No stocks are currently registering as {raw_zone.lower()} "
            f"({threshold}) for {trading_date.strftime('%d %b %Y')}."
        )

    lines = [f"{emoji} <b>{zone_name}</b> ({threshold})\n<i>{trading_date.strftime('%d %b %Y')}</i>\n"]
    for iv, symbol, close_price in rows:
        if iv.stoch_k is None:
            continue
        ticker = symbol.ticker.replace(".NS", "")
        c_price = close_price if close_price is not None else 0.0
        lines.append(
            f"\n• <code>{ticker}</code> — <b>{symbol.company_name[:25]}</b>\n"
            f"  ├ ᴘ ʀ ɪ ᴄ ᴇ : ₹{c_price:,.2f}\n"
            f"  ├ ʀ ѕ ɪ : {iv.rsi:.1f}\n"
            f"  └ ѕ ᴛ ᴏ ᴄ ʜ : <b>{iv.stoch_k:.1f}</b>"
        )

    lines.append("\n\n<i>⚠️ ᴛ ʀ ᴀ ᴅ ᴇ  ѕ ᴇ ᴛ ᴜ ᴘ ѕ  ᴅ ᴇ ᴛ ᴇ ᴄ ᴛ ᴇ ᴅ  ᴀ ᴜ ᴛ ᴏ ᴍ ᴀ ᴛ ɪ ᴄ ᴀ ʟ ʟ ʏ. ᴅ ᴏ  ʏ ᴏ ᴜ ʀ  ᴏ ᴡ ɴ  ʀ ᴇ ѕ ᴇ ᴀ ʀ ᴄ ʜ.</i>")
    return "".join(lines)


# ---------------------------------------------------------------------------
# Commands /oversold /overbought /scan
# ---------------------------------------------------------------------------


@router.message(Command("scan"))
async def cmd_scan(message: Message, db: DatabaseManager, bot: Bot) -> None:
    processing_msg = await message.answer("🔍 <i>Initializing market scan...</i>", parse_mode="HTML")
    text, markup = await _trigger_scan(db, bot)
    await processing_msg.edit_text(text, parse_mode="HTML", reply_markup=markup)


@router.message(Command("oversold"))
async def cmd_oversold(message: Message, db: DatabaseManager) -> None:
    assert message.from_user is not None
    text = await _get_zone_text(db, message.from_user.id, SignalType.OVERSOLD)
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard())


@router.message(Command("overbought"))
async def cmd_overbought(message: Message, db: DatabaseManager) -> None:
    assert message.from_user is not None
    text = await _get_zone_text(db, message.from_user.id, SignalType.OVERBOUGHT)
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# /rsi <SYMBOL>
# ---------------------------------------------------------------------------


@router.message(Command("rsi"))
async def cmd_rsi(message: Message, command: CommandObject, db: DatabaseManager) -> None:
    if not command.args:
        await message.answer("Usage: /rsi &lt;SYMBOL&gt;\nExample: <code>/rsi RELIANCE</code>", parse_mode="HTML")
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
                f"❌ Symbol <code>{raw}</code> not found in Nifty 50 index.",
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
            f"ℹ️ <b>No Data</b>\nNo indicators computed for <code>{raw}</code> yet.\n"
            "Try running a /scan first.",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
        return

    # Determine zone
    zone = ""
    if iv.stoch_k is not None:
        if iv.stoch_k < settings.stoch_low:
            zone = "  🟢 <b>[OVERSOLD]</b>"
        elif iv.stoch_k > settings.stoch_high:
            zone = "  🔴 <b>[OVERBOUGHT]</b>"

    await message.answer(
        f"📊 <b>{raw}</b> — {symbol.company_name}\n"
        f"<i>{trading_date.strftime('%d %b %Y')}</i>{zone}\n\n"
        f"• <b>RSI(14):</b>  <code>{iv.rsi:.2f}</code>\n"
        f"• <b>Stoch %K:</b>  <code>{iv.stoch_k:.2f if iv.stoch_k else 'N/A'}</code>\n"
        f"• <b>Stoch %D:</b>  <code>{iv.stoch_d:.2f if iv.stoch_d else 'N/A'}</code>\n\n"
        "<i>⚠️ Not investment advice.</i>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )


# ---------------------------------------------------------------------------
# /settings & /status
# ---------------------------------------------------------------------------


@router.message(Command("settings"))
async def cmd_settings(message: Message, db: DatabaseManager) -> None:
    assert message.from_user is not None
    async with db.session() as session:
        sub = await session.get(Subscriber, message.from_user.id)
    
    low_thresh = sub.custom_stoch_low if sub and sub.custom_stoch_low is not None else settings.stoch_low
    high_thresh = sub.custom_stoch_high if sub and sub.custom_stoch_high is not None else settings.stoch_high

    await message.answer(
        "⚙️ <b>SCANNER CONFIGURATION</b>\n\n"
        f"• <b>RSI Length:</b>  <code>{settings.rsi_length}</code>\n"
        f"• <b>Stoch Length:</b>  <code>{settings.stoch_length}</code>\n"
        f"• <b>%K Smooth:</b>  <code>{settings.stoch_k_smooth}</code>\n"
        f"• <b>%D Smooth:</b>  <code>{settings.stoch_d_smooth}</code>\n\n"
        f"🟢 <b>Oversold Threshold:</b>  <code>&lt; {low_thresh}</code>\n"
        f"🔴 <b>Overbought Threshold:</b>  <code>&gt; {high_thresh}</code>\n\n"
        f"• <b>Alert Cooldown:</b>  <code>{settings.alert_cooldown_days} days</code>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )


@router.message(Command("status"))
async def cmd_status(message: Message, db: DatabaseManager) -> None:
    assert message.from_user is not None

    async with db.session() as session:
        result = await session.execute(
            select(ScanRun)
            .order_by(desc(ScanRun.started_at))
            .limit(1)
        )
        last_run = result.scalar_one_or_none()
        
        sub_result = await session.execute(
            select(Subscriber).where(Subscriber.is_active == True)  # noqa: E712
        )
        sub_count = len(sub_result.scalars().all())

    today_is_trading = is_trading_day()
    trading_day_status = "ᴏɴʟɪɴᴇ" if today_is_trading else "ᴏꜰꜰʟɪɴᴇ (Weekend/Holiday)"

    if last_run:
        duration = f"{last_run.duration_ms / 1000:.1f}s" if last_run.duration_ms else "N/A"
        status_emoji = {"SUCCESS": "✅", "FAILED": "❌", "RUNNING": "⏳", "SKIPPED": "⏭"}.get(
            last_run.status.value, "❓"
        )
        last_run_text = (
            f"{status_emoji} <b>{last_run.status.value}</b>\n"
            f"Date: <i>{last_run.trading_date}</i>\n"
            f"Processed: <b>{last_run.items_processed}</b> | Failed: <b>{last_run.items_failed}</b>\n"
            f"Duration: <b>{duration}</b>"
        )
    else:
        last_run_text = "No scans have run yet."

    await message.answer(
        f"📡 <b>SYSTEM DIAGNOSTICS</b>\n\n"
        f"Market Status: <b>{trading_day_status}</b>\n\n"
        f"<b>Last Automated Scan:</b>\n{last_run_text}\n\n"
        f"👥 Active Subscribers: <code>{sub_count}</code>\n"
        f"⚙️ Environment: <code>{settings.environment.upper()}</code>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )


from aiogram.exceptions import TelegramBadRequest

# ---------------------------------------------------------------------------
# Callback Query Handlers (Inline Buttons)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "action_scan")
async def cb_scan(callback: CallbackQuery, db: DatabaseManager, bot: Bot) -> None:
    try:
        await callback.message.edit_text("🔍 <i>Initializing market scan...</i>", parse_mode="HTML")
        text, markup = await _trigger_scan(db, bot)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        await callback.answer("✅ Market scan executed successfully.")
    except TelegramBadRequest:
        await callback.answer("ℹ️ The scan interface is already displayed.")
    except Exception as e:
        log.error("cb_scan_error", error=str(e))
        await callback.answer("❌ An error occurred while executing the scan.", show_alert=True)


@router.callback_query(F.data == "action_oversold")
async def cb_oversold(callback: CallbackQuery, db: DatabaseManager) -> None:
    try:
        assert callback.from_user is not None
        text = await _get_zone_text(db, callback.from_user.id, SignalType.OVERSOLD)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard())
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ You are already viewing the Oversold zone.", show_alert=False)
    except Exception as e:
        log.error("cb_oversold_error", error=str(e))
        await callback.answer("❌ Error retrieving Oversold data.", show_alert=True)


@router.callback_query(F.data == "action_overbought")
async def cb_overbought(callback: CallbackQuery, db: DatabaseManager) -> None:
    try:
        assert callback.from_user is not None
        text = await _get_zone_text(db, callback.from_user.id, SignalType.OVERBOUGHT)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard())
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ You are already viewing the Overbought zone.", show_alert=False)
    except Exception as e:
        log.error("cb_overbought_error", error=str(e))
        await callback.answer("❌ Error retrieving Overbought data.", show_alert=True)


@router.callback_query(F.data == "action_settings")
async def cb_settings(callback: CallbackQuery, db: DatabaseManager) -> None:
    assert callback.from_user is not None
    async with db.session() as session:
        sub = await session.get(Subscriber, callback.from_user.id)
    
    low_thresh = sub.custom_stoch_low if sub and sub.custom_stoch_low is not None else settings.stoch_low
    high_thresh = sub.custom_stoch_high if sub and sub.custom_stoch_high is not None else settings.stoch_high

    text = (
        "⚙️ <b>SCANNER CONFIGURATION</b>\n\n"
        f"• <b>RSI Length:</b>  <code>{settings.rsi_length}</code>\n"
        f"• <b>Stoch Length:</b>  <code>{settings.stoch_length}</code>\n"
        f"• <b>%K Smooth:</b>  <code>{settings.stoch_k_smooth}</code>\n"
        f"• <b>%D Smooth:</b>  <code>{settings.stoch_d_smooth}</code>\n\n"
        f"🟢 <b>Oversold Threshold:</b>  <code>&lt; {low_thresh}</code>\n"
        f"🔴 <b>Overbought Threshold:</b>  <code>&gt; {high_thresh}</code>\n\n"
        f"• <b>Alert Cooldown:</b>  <code>{settings.alert_cooldown_days} days</code>"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard())
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ You are already viewing the settings.", show_alert=False)
    except Exception as e:
        log.error("cb_settings_error", error=str(e))
        await callback.answer("❌ Error loading settings.", show_alert=True)


@router.callback_query(F.data == "action_status")
async def cb_status(callback: CallbackQuery, db: DatabaseManager) -> None:
    try:
        async with db.session() as session:
            result = await session.execute(
                select(ScanRun)
                .order_by(desc(ScanRun.started_at))
                .limit(1)
            )
            last_run = result.scalar_one_or_none()
            
            sub_result = await session.execute(
                select(Subscriber).where(Subscriber.is_active == True)  # noqa: E712
            )
            sub_count = len(sub_result.scalars().all())

        today_is_trading = is_trading_day()
        trading_day_status = "ᴏɴʟɪɴᴇ" if today_is_trading else "ᴏꜰꜰʟɪɴᴇ (Weekend/Holiday)"

        if last_run:
            duration = f"{last_run.duration_ms / 1000:.1f}s" if last_run.duration_ms else "N/A"
            status_emoji = {"SUCCESS": "✅", "FAILED": "❌", "RUNNING": "⏳", "SKIPPED": "⏭"}.get(
                last_run.status.value, "❓"
            )
            last_run_text = (
                f"{status_emoji} <b>{last_run.status.value}</b>\n"
                f"Date: <i>{last_run.trading_date}</i>\n"
                f"Processed: <b>{last_run.items_processed}</b> | Failed: <b>{last_run.items_failed}</b>\n"
                f"Duration: <b>{duration}</b>"
            )
        else:
            last_run_text = "No scans have run yet."

        text = (
            f"📡 <b>SYSTEM DIAGNOSTICS</b>\n\n"
            f"Market Status: <b>{trading_day_status}</b>\n\n"
            f"<b>Last Automated Scan:</b>\n{last_run_text}\n\n"
            f"👥 Active Subscribers: <code>{sub_count}</code>\n"
            f"⚙️ Environment: <code>{settings.environment.upper()}</code>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard())
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ System diagnostics are already up to date.", show_alert=False)
    except Exception as e:
        log.error("cb_status_error", error=str(e))
        await callback.answer("❌ Error retrieving system diagnostics.", show_alert=True)
