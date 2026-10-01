"""Telegram bot command handlers — all bot commands in one module.

Uses aiogram 3.x patterns: Router, FSM States, CallbackData, ButtonStyle.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, Router, F
from aiogram.enums import ButtonStyle
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, desc

from src.data.calendar import is_trading_day, last_trading_day
from src.infra.config import settings
from src.infra.database import DatabaseManager
from src.infra.logging import get_logger
from src.infra.models import IndicatorValue, ScanRun, SignalType, Subscriber, Symbol, UserRole

log = get_logger(__name__)
router = Router(name="commands")


# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------

class SettingsForm(StatesGroup):
    awaiting_low = State()
    awaiting_high = State()


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔹 ʀᴜɴ ᴍᴀʀᴋᴇᴛ sᴄᴀɴ", callback_data="action_scan", style=ButtonStyle.PRIMARY),
            ],
            [
                InlineKeyboardButton(text="🟢 ᴏᴠᴇʀsᴏʟᴅ ᴢᴏɴᴇ", callback_data="action_oversold", style=ButtonStyle.SUCCESS),
                InlineKeyboardButton(text="🔴 ᴏᴠᴇʀʙᴏᴜɢʜᴛ ᴢᴏɴᴇ", callback_data="action_overbought", style=ButtonStyle.DANGER),
            ],
            [
                InlineKeyboardButton(text="📡 sʏsᴛᴇᴍ sᴛᴀᴛᴜs", callback_data="action_status", style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="⚙️ sᴇᴛᴛɪɴɢs", callback_data="action_settings", style=ButtonStyle.PRIMARY),
            ],
        ]
    )


def settings_keyboard(is_subscribed: bool) -> InlineKeyboardMarkup:
    sub_text = "🔔 sᴜʙsᴄʀɪʙᴇ ᴀʟᴇʀᴛs" if not is_subscribed else "🔕 ᴜɴsᴜʙsᴄʀɪʙᴇ"
    sub_style = ButtonStyle.SUCCESS if not is_subscribed else ButtonStyle.DANGER
    sub_data = "settings_subscribe" if not is_subscribed else "settings_unsubscribe"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 sᴇᴛ ᴏᴠᴇʀsᴏʟᴅ ʟɪᴍɪᴛ", callback_data="settings_set_low", style=ButtonStyle.SUCCESS),
            ],
            [
                InlineKeyboardButton(text="🔴 sᴇᴛ ᴏᴠᴇʀʙᴏᴜɢʜᴛ ʟɪᴍɪᴛ", callback_data="settings_set_high", style=ButtonStyle.DANGER),
            ],
            [
                InlineKeyboardButton(text=sub_text, callback_data=sub_data, style=sub_style),
            ],
            [
                InlineKeyboardButton(text="🔄 ʀᴇsᴇᴛ ᴛᴏ ᴅᴇꜰᴀᴜʟᴛs", callback_data="settings_reset", style=ButtonStyle.PRIMARY),
            ],
            [
                InlineKeyboardButton(text="◀️ ʙᴀᴄᴋ", callback_data="action_back", style=ButtonStyle.PRIMARY),
            ],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="action_settings", style=ButtonStyle.PRIMARY)],
        ]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_create_subscriber(
    session,
    message: Message,
    *,
    activate: bool = True,
) -> Subscriber:
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


def _build_settings_text(sub: Subscriber | None, low_thresh: float, high_thresh: float) -> str:
    is_active = sub.is_active if sub else False
    sub_status = "🔔 <b>ᴀᴄᴛɪᴠᴇ</b>" if is_active else "🔕 <b>ɪɴᴀᴄᴛɪᴠᴇ</b>"
    return (
        "⚙️ <b>sᴄᴀɴɴᴇʀ ᴄᴏɴꜰɪɢᴜʀᴀᴛɪᴏɴ</b>\n\n"
        "<blockquote>Your personalized scanner configuration and threshold settings.</blockquote>\n\n"
        f"• <b>ʀsɪ ʟᴇɴɢᴛʜ:</b>  <code>{settings.rsi_length}</code>\n"
        f"• <b>sᴛᴏᴄʜ ʟᴇɴɢᴛʜ:</b>  <code>{settings.stoch_length}</code>\n"
        f"• <b>%ᴋ sᴍᴏᴏᴛʜ:</b>  <code>{settings.stoch_k_smooth}</code>\n"
        f"• <b>%ᴅ sᴍᴏᴏᴛʜ:</b>  <code>{settings.stoch_d_smooth}</code>\n\n"
        f"🟢 <b>ᴏᴠᴇʀsᴏʟᴅ ʟɪᴍɪᴛ:</b>  <code>&lt; {low_thresh}</code>\n"
        f"🔴 <b>ᴏᴠᴇʀʙᴏᴜɢʜᴛ ʟɪᴍɪᴛ:</b>  <code>&gt; {high_thresh}</code>\n\n"
        f"• <b>ᴀʟᴇʀᴛ ᴄᴏᴏʟᴅᴏᴡɴ:</b>  <code>{settings.alert_cooldown_days} days</code>\n"
        f"• <b>ᴅᴀɪʟʏ ᴀʟᴇʀᴛs:</b>  {sub_status}"
    )


async def _load_settings_data(db: DatabaseManager, user_id: int):
    async with db.session() as session:
        sub = await session.get(Subscriber, user_id)
    low_thresh = sub.custom_stoch_low if sub and sub.custom_stoch_low is not None else settings.stoch_low
    high_thresh = sub.custom_stoch_high if sub and sub.custom_stoch_high is not None else settings.stoch_high
    return sub, low_thresh, high_thresh


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, db: DatabaseManager, state: FSMContext) -> None:
    assert message.from_user is not None
    await state.clear()
    async with db.session() as session:
        await _get_or_create_subscriber(session, message, activate=True)

    name = message.from_user.first_name or "Trader"
    await message.answer(
        build_main_menu_text(name),
        reply_markup=main_menu_keyboard()
    )
    log.info("user_started", user_id=message.from_user.id)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (
        "📋 <b>ᴄᴏᴍᴍᴀɴᴅ ᴄᴇɴᴛᴇʀ</b>\n\n"
        "<blockquote>Use the interactive menu below to navigate or type a command directly.</blockquote>\n\n"
        "<b>🔍 ᴍᴀʀᴋᴇᴛ sᴄᴀɴs</b>\n"
        "• /scan — Force a market scan now\n"
        "• /oversold — List oversold stocks\n"
        "• /overbought — List overbought stocks\n"
        "• /rsi &lt;SYMBOL&gt; — Get StochRSI breakdown for a stock\n\n"
        "<b>⚙️ ᴀʟᴇʀᴛs &amp; sᴇᴛᴛɪɴɢs</b>\n"
        "• /subscribe — Enable daily alerts\n"
        "• /unsubscribe — Disable daily alerts\n"
        "• /settings — Open settings panel (manage thresholds &amp; subscription)\n\n"
        "<b>📡 sʏsᴛᴇᴍ</b>\n"
        "• /status — Check server health &amp; last scan info"
    )
    await message.answer(text, reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# /subscribe / /unsubscribe  (commands still work for direct use)
# ---------------------------------------------------------------------------

@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    async with db.session() as session:
        await _get_or_create_subscriber(session, message, activate=True)
    await message.answer(
        "✅ <b>ᴀʟᴇʀᴛs ᴀᴄᴛɪᴠᴀᴛᴇᴅ!</b>\n\n"
        "<blockquote>You are now subscribed to automated market updates. I will notify you when StochRSI extremes are detected.</blockquote>",
    )


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    assert message.from_user is not None
    async with db.session() as session:
        sub = await session.get(Subscriber, message.from_user.id)
        if sub:
            sub.is_active = False
    await message.answer(
        "🔕 <b>ᴀʟᴇʀᴛs ᴅɪsᴀʙʟᴇᴅ.</b>\n\n"
        "<blockquote>You have been unsubscribed from automated market updates. Use /subscribe or open Settings to re-enable.</blockquote>",
    )


# ---------------------------------------------------------------------------
# /scan, /oversold, /overbought
# ---------------------------------------------------------------------------

async def _trigger_scan(db: DatabaseManager, bot: Bot) -> tuple[str, InlineKeyboardMarkup | None]:
    from src.data.yfinance_provider import YFinanceProvider
    from src.services.alerter import AlerterService
    from src.services.scanner import ScannerService

    try:
        provider = YFinanceProvider()
        scanner = ScannerService(provider=provider, db=db)
        result = await scanner.run_scan(force=True)

        if result.was_skipped:
            return (
                "ℹ️ <b>sᴄᴀɴ ᴄᴏᴍᴘʟᴇᴛᴇ</b>\n\n"
                "<blockquote>Market data is already up-to-date for today. Use the menu below to view the latest signals.</blockquote>",
                main_menu_keyboard()
            )

        text = (
            f"✅ <b>ᴍᴀʀᴋᴇᴛ sᴄᴀɴ sᴜᴄᴄᴇss</b> — {result.trading_date.strftime('%d %b %Y')}\n\n"
            "<blockquote>Scan completed successfully. Alerts have been dispatched to all active subscribers.</blockquote>\n\n"
            f"📈 Processed: <b>{result.processed}</b> stocks\n"
            f"🚨 Signals: <b>{len(result.signals)}</b> found\n"
            f"⏱ Duration: <b>{result.duration_ms / 1000:.1f}s</b>"
        )

        alerter = AlerterService(bot=bot, db=db)
        dispatched = await alerter.process_and_send(result)
        text += f"\n📤 Dispatched: <b>{dispatched}</b>"
        return text, main_menu_keyboard()

    except Exception as exc:
        log.error("scan_command_failed", error=str(exc))
        return f"❌ <b>sᴄᴀɴ ꜰᴀɪʟᴇᴅ</b>\n\n<blockquote><code>{exc}</code></blockquote>", None


async def _get_zone_text(db: DatabaseManager, user_id: int, signal_type: SignalType) -> str:
    from src.infra.models import DailyCandle
    trading_date = last_trading_day()
    emoji = "🟢" if signal_type == SignalType.OVERSOLD else "🔴"
    zone_name = "ᴏᴠᴇʀsᴏʟᴅ ᴢᴏɴᴇ" if signal_type == SignalType.OVERSOLD else "ᴏᴠᴇʀʙᴏᴜɢʜᴛ ᴢᴏɴᴇ"

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
            f"{emoji} <b>ɴᴏ {raw_zone} sɪɢɴᴀʟs</b>\n\n"
            f"<blockquote>No stocks are currently in the {raw_zone.lower()} zone ({threshold}) for {trading_date.strftime('%d %b %Y')}.</blockquote>"
        )

    lines = [f"{emoji} <b>{zone_name}</b>  ({threshold})\n<i>{trading_date.strftime('%d %b %Y')}</i>\n"]
    for iv, symbol, close_price in rows:
        if iv.stoch_k is None:
            continue
        ticker = symbol.ticker.replace(".NS", "")
        c_price = close_price if close_price is not None else 0.0
        lines.append(
            f"\n• <code>{ticker}</code> — <b>{symbol.company_name[:25]}</b>\n"
            f"  ├ ᴘʀɪᴄᴇ : ₹{c_price:,.2f}\n"
            f"  ├ ʀsɪ   : {iv.rsi:.1f}\n"
            f"  └ sᴛᴏᴄʜ : <b>{iv.stoch_k:.1f}</b>"
        )

    lines.append("\n\n<i>⚠️ ᴛʀᴀᴅᴇ sᴇᴛᴜᴘs ᴅᴇᴛᴇᴄᴛᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ. ᴅᴏ ʏᴏᴜʀ ᴏᴡɴ ʀᴇsᴇᴀʀᴄʜ.</i>")
    return "".join(lines)


@router.message(Command("scan"))
async def cmd_scan(message: Message, db: DatabaseManager, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    processing_msg = await message.answer("🔍 <i>Initializing market scan...</i>")
    text, markup = await _trigger_scan(db, bot)
    await processing_msg.edit_text(text, reply_markup=markup)


@router.message(Command("oversold"))
async def cmd_oversold(message: Message, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    assert message.from_user is not None
    text = await _get_zone_text(db, message.from_user.id, SignalType.OVERSOLD)
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(Command("overbought"))
async def cmd_overbought(message: Message, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    assert message.from_user is not None
    text = await _get_zone_text(db, message.from_user.id, SignalType.OVERBOUGHT)
    await message.answer(text, reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# /rsi <SYMBOL>
# ---------------------------------------------------------------------------

@router.message(Command("rsi"))
async def cmd_rsi(message: Message, command: CommandObject, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    if not command.args:
        await message.answer(
            "📊 <b>ʀsɪ ʟᴏᴏᴋᴜᴘ</b>\n\n"
            "<blockquote>Usage: /rsi &lt;SYMBOL&gt;\nExample: <code>/rsi RELIANCE</code></blockquote>",
        )
        return

    raw = command.args.strip().upper()
    ticker = raw if raw.endswith(".NS") else f"{raw}.NS"
    trading_date = last_trading_day()

    async with db.session() as session:
        sym_result = await session.execute(select(Symbol).where(Symbol.ticker == ticker))
        symbol = sym_result.scalar_one_or_none()

        if symbol is None:
            await message.answer(
                f"❌ <b>sʏᴍʙᴏʟ ɴᴏᴛ ꜰᴏᴜɴᴅ</b>\n\n"
                f"<blockquote><code>{raw}</code> is not in the Nifty 50 index. Check the symbol and try again.</blockquote>",
                reply_markup=main_menu_keyboard()
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
            f"ℹ️ <b>ɴᴏ ᴅᴀᴛᴀ ꜰᴏᴜɴᴅ</b>\n\n"
            f"<blockquote>No indicators computed for <code>{raw}</code> on {trading_date.strftime('%d %b %Y')} yet.\nTry running a /scan first.</blockquote>",
            reply_markup=main_menu_keyboard()
        )
        return

    # Determine zone
    zone = ""
    if iv.stoch_k is not None:
        if iv.stoch_k < settings.stoch_low:
            zone = "\n🟢 <b>[ᴏᴠᴇʀsᴏʟᴅ]</b>"
        elif iv.stoch_k > settings.stoch_high:
            zone = "\n🔴 <b>[ᴏᴠᴇʀʙᴏᴜɢʜᴛ]</b>"

    stoch_k_str = f"{iv.stoch_k:.2f}" if iv.stoch_k is not None else "N/A"
    stoch_d_str = f"{iv.stoch_d:.2f}" if iv.stoch_d is not None else "N/A"

    await message.answer(
        f"📊 <b>{raw}</b> — {symbol.company_name}\n"
        f"<i>{trading_date.strftime('%d %b %Y')}</i>{zone}\n\n"
        f"• <b>ʀsɪ(14):</b>   <code>{iv.rsi:.2f}</code>\n"
        f"• <b>sᴛᴏᴄʜ %ᴋ:</b>  <code>{stoch_k_str}</code>\n"
        f"• <b>sᴛᴏᴄʜ %ᴅ:</b>  <code>{stoch_d_str}</code>\n\n"
        "<i>⚠️ Not investment advice.</i>",
        reply_markup=main_menu_keyboard()
    )


# ---------------------------------------------------------------------------
# /settings
# ---------------------------------------------------------------------------

@router.message(Command("settings"))
async def cmd_settings(message: Message, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    assert message.from_user is not None
    sub, low_thresh, high_thresh = await _load_settings_data(db, message.from_user.id)
    await message.answer(
        _build_settings_text(sub, low_thresh, high_thresh),
        reply_markup=settings_keyboard(bool(sub and sub.is_active))
    )


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

@router.message(Command("status"))
async def cmd_status(message: Message, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    async with db.session() as session:
        result = await session.execute(select(ScanRun).order_by(desc(ScanRun.started_at)).limit(1))
        last_run = result.scalar_one_or_none()
        sub_result = await session.execute(select(Subscriber).where(Subscriber.is_active == True))  # noqa: E712
        sub_count = len(sub_result.scalars().all())

    trading_day_status = "🟢 ᴏɴʟɪɴᴇ" if is_trading_day() else "🔴 ᴏꜰꜰʟɪɴᴇ (Weekend/Holiday)"

    if last_run:
        duration = f"{last_run.duration_ms / 1000:.1f}s" if last_run.duration_ms else "N/A"
        status_emoji = {"SUCCESS": "✅", "FAILED": "❌", "RUNNING": "⏳", "SKIPPED": "⏭"}.get(last_run.status.value, "❓")
        last_run_text = (
            f"{status_emoji} <b>{last_run.status.value}</b>\n"
            f"Date: <i>{last_run.trading_date}</i>\n"
            f"Processed: <b>{last_run.items_processed}</b> | Failed: <b>{last_run.items_failed}</b>\n"
            f"Duration: <b>{duration}</b>"
        )
    else:
        last_run_text = "No scans have run yet."

    await message.answer(
        "📡 <b>sʏsᴛᴇᴍ ᴅɪᴀɢɴᴏsᴛɪᴄs</b>\n\n"
        "<blockquote>Real-time metrics and health status of the scanner service.</blockquote>\n\n"
        f"Market Status: <b>{trading_day_status}</b>\n\n"
        "<b>ʟᴀsᴛ ᴀᴜᴛᴏᴍᴀᴛᴇᴅ sᴄᴀɴ:</b>\n"
        f"<blockquote>{last_run_text}</blockquote>\n"
        f"👥 Active Subscribers: <code>{sub_count}</code>\n"
        f"⚙️ Environment: <code>{settings.environment.upper()}</code>",
        reply_markup=main_menu_keyboard()
    )


# ---------------------------------------------------------------------------
# Callback Query Handlers — Main Menu
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "action_scan")
async def cb_scan(callback: CallbackQuery, db: DatabaseManager, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text("🔍 <i>Initializing market scan...</i>")
        text, markup = await _trigger_scan(db, bot)
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ Already displaying.")
    except Exception as e:
        log.error("cb_scan_error", error=str(e))
        await callback.answer("❌ Scan failed.", show_alert=True)


@router.callback_query(F.data == "action_oversold")
async def cb_oversold(callback: CallbackQuery, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    try:
        assert callback.from_user is not None
        text = await _get_zone_text(db, callback.from_user.id, SignalType.OVERSOLD)
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ Already viewing Oversold zone.")
    except Exception as e:
        log.error("cb_oversold_error", error=str(e))
        await callback.answer("❌ Error retrieving data.", show_alert=True)


@router.callback_query(F.data == "action_overbought")
async def cb_overbought(callback: CallbackQuery, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    try:
        assert callback.from_user is not None
        text = await _get_zone_text(db, callback.from_user.id, SignalType.OVERBOUGHT)
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ Already viewing Overbought zone.")
    except Exception as e:
        log.error("cb_overbought_error", error=str(e))
        await callback.answer("❌ Error retrieving data.", show_alert=True)


@router.callback_query(F.data == "action_status")
async def cb_status(callback: CallbackQuery, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    try:
        async with db.session() as session:
            result = await session.execute(select(ScanRun).order_by(desc(ScanRun.started_at)).limit(1))
            last_run = result.scalar_one_or_none()
            sub_result = await session.execute(select(Subscriber).where(Subscriber.is_active == True))  # noqa: E712
            sub_count = len(sub_result.scalars().all())

        trading_day_status = "🟢 ᴏɴʟɪɴᴇ" if is_trading_day() else "🔴 ᴏꜰꜰʟɪɴᴇ (Weekend/Holiday)"

        if last_run:
            duration = f"{last_run.duration_ms / 1000:.1f}s" if last_run.duration_ms else "N/A"
            status_emoji = {"SUCCESS": "✅", "FAILED": "❌", "RUNNING": "⏳", "SKIPPED": "⏭"}.get(last_run.status.value, "❓")
            last_run_text = (
                f"{status_emoji} <b>{last_run.status.value}</b>\n"
                f"Date: <i>{last_run.trading_date}</i>\n"
                f"Processed: <b>{last_run.items_processed}</b> | Failed: <b>{last_run.items_failed}</b>\n"
                f"Duration: <b>{duration}</b>"
            )
        else:
            last_run_text = "No scans have run yet."

        text = (
            "📡 <b>sʏsᴛᴇᴍ ᴅɪᴀɢɴᴏsᴛɪᴄs</b>\n\n"
            "<blockquote>Real-time metrics and health status of the scanner service.</blockquote>\n\n"
            f"Market Status: <b>{trading_day_status}</b>\n\n"
            "<b>ʟᴀsᴛ ᴀᴜᴛᴏᴍᴀᴛᴇᴅ sᴄᴀɴ:</b>\n"
            f"<blockquote>{last_run_text}</blockquote>\n"
            f"👥 Active Subscribers: <code>{sub_count}</code>\n"
            f"⚙️ Environment: <code>{settings.environment.upper()}</code>"
        )
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ Already up to date.")
    except Exception as e:
        log.error("cb_status_error", error=str(e))
        await callback.answer("❌ Error retrieving diagnostics.", show_alert=True)


def build_main_menu_text(name: str = "Trader") -> str:
    return (
        f"🌟 <b>ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ɴɪꜰᴛʏ 50 sᴄᴀɴɴᴇʀ, {name}!</b> 🌟\n\n"
        "<blockquote>I provide professional market scanning for Nifty 50 stocks, delivering high-probability Stochastic RSI setups directly to your inbox.</blockquote>\n\n"
        "<b>⚡ ᴢᴏɴᴇs:</b>\n"
        "🟢 <b>ᴏᴠᴇʀsᴏʟᴅ:</b> %K &lt; 20 (Potential Reversal)\n"
        "🔴 <b>ᴏᴠᴇʀʙᴏᴜɢʜᴛ:</b> %K &gt; 80 (Overextended)\n\n"
        "<i>⚠️ Data is for informational purposes only — not investment advice.</i>\n\n"
        "Select an action below or use /help to see all commands."
    )


@router.callback_query(F.data == "action_back")
async def cb_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        name = callback.from_user.first_name if callback.from_user else "Trader"
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                build_main_menu_text(name),
                reply_markup=main_menu_keyboard()
            )
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer()
    except Exception as e:
        log.error("cb_back_error", error=str(e))
        await callback.answer("❌ Navigation error.", show_alert=True)


# ---------------------------------------------------------------------------
# Callback Query Handlers — Settings Menu
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "action_settings")
async def cb_settings(callback: CallbackQuery, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    try:
        assert callback.from_user is not None
        sub, low_thresh, high_thresh = await _load_settings_data(db, callback.from_user.id)
        await callback.message.edit_text(
            _build_settings_text(sub, low_thresh, high_thresh),
            reply_markup=settings_keyboard(bool(sub and sub.is_active))
        )
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("ℹ️ Already viewing settings.")
    except Exception as e:
        log.error("cb_settings_error", error=str(e))
        await callback.answer("❌ Error loading settings.", show_alert=True)


@router.callback_query(F.data == "settings_subscribe")
async def cb_settings_subscribe(callback: CallbackQuery, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    assert callback.from_user is not None
    async with db.session() as session:
        sub = await session.get(Subscriber, callback.from_user.id)
        if sub:
            sub.is_active = True
        else:
            sub = Subscriber(
                telegram_user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                username=callback.from_user.username,
                first_name=callback.from_user.first_name,
                role=UserRole.ADMIN if callback.from_user.id in settings.admin_ids else UserRole.USER,
                is_active=True,
            )
            session.add(sub)

    sub, low_thresh, high_thresh = await _load_settings_data(db, callback.from_user.id)
    try:
        await callback.message.edit_text(
            _build_settings_text(sub, low_thresh, high_thresh),
            reply_markup=settings_keyboard(True)
        )
        await callback.answer("✅ Subscribed to daily alerts!")
    except TelegramBadRequest:
        await callback.answer("✅ Subscribed!")


@router.callback_query(F.data == "settings_unsubscribe")
async def cb_settings_unsubscribe(callback: CallbackQuery, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    assert callback.from_user is not None
    async with db.session() as session:
        sub = await session.get(Subscriber, callback.from_user.id)
        if sub:
            sub.is_active = False

    sub, low_thresh, high_thresh = await _load_settings_data(db, callback.from_user.id)
    try:
        await callback.message.edit_text(
            _build_settings_text(sub, low_thresh, high_thresh),
            reply_markup=settings_keyboard(False)
        )
        await callback.answer("🔕 Unsubscribed from daily alerts.")
    except TelegramBadRequest:
        await callback.answer("🔕 Unsubscribed.")


@router.callback_query(F.data == "settings_reset")
async def cb_settings_reset(callback: CallbackQuery, db: DatabaseManager, state: FSMContext) -> None:
    await state.clear()
    assert callback.from_user is not None
    async with db.session() as session:
        sub = await session.get(Subscriber, callback.from_user.id)
        if sub:
            sub.custom_stoch_low = None
            sub.custom_stoch_high = None

    sub, low_thresh, high_thresh = await _load_settings_data(db, callback.from_user.id)
    try:
        await callback.message.edit_text(
            _build_settings_text(sub, low_thresh, high_thresh),
            reply_markup=settings_keyboard(bool(sub and sub.is_active))
        )
        await callback.answer("🔄 Thresholds reset to defaults.")
    except TelegramBadRequest:
        await callback.answer("🔄 Reset to defaults.")


@router.callback_query(F.data == "settings_set_low")
async def cb_settings_set_low(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsForm.awaiting_low)
    try:
        await callback.message.edit_text(
            "🟢 <b>sᴇᴛ ᴏᴠᴇʀsᴏʟᴅ ʟɪᴍɪᴛ</b>\n\n"
            "<blockquote>Enter a number between 1 and 49.\nDefault is <code>20</code>.\n\nExample: <code>15</code></blockquote>",
            reply_markup=back_keyboard()
        )
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer()


@router.callback_query(F.data == "settings_set_high")
async def cb_settings_set_high(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsForm.awaiting_high)
    try:
        await callback.message.edit_text(
            "🔴 <b>sᴇᴛ ᴏᴠᴇʀʙᴏᴜɢʜᴛ ʟɪᴍɪᴛ</b>\n\n"
            "<blockquote>Enter a number between 51 and 99.\nDefault is <code>80</code>.\n\nExample: <code>85</code></blockquote>",
            reply_markup=back_keyboard()
        )
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer()


# ---------------------------------------------------------------------------
# FSM — Awaiting threshold input from user
# ---------------------------------------------------------------------------

@router.message(SettingsForm.awaiting_low)
async def fsm_set_low(message: Message, state: FSMContext, db: DatabaseManager) -> None:
    assert message.from_user is not None
    raw = message.text.strip() if message.text else ""
    try:
        val = float(raw)
        if not (1 <= val <= 49):
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ <b>ɪɴᴠᴀʟɪᴅ ᴠᴀʟᴜᴇ</b>\n\n"
            "<blockquote>Please enter a number between 1 and 49.\nExample: <code>15</code></blockquote>",
            reply_markup=back_keyboard()
        )
        return

    async with db.session() as session:
        sub = await session.get(Subscriber, message.from_user.id)
        if sub:
            sub.custom_stoch_low = val

    await state.clear()
    sub, low_thresh, high_thresh = await _load_settings_data(db, message.from_user.id)
    await message.answer(
        f"✅ <b>ᴏᴠᴇʀsᴏʟᴅ ʟɪᴍɪᴛ ᴜᴘᴅᴀᴛᴇᴅ ᴛᴏ {val}</b>\n\n"
        "<blockquote>You will now receive alerts when StochRSI %K falls below this value.</blockquote>\n\n"
        + _build_settings_text(sub, low_thresh, high_thresh),
        reply_markup=settings_keyboard(bool(sub and sub.is_active))
    )


@router.message(SettingsForm.awaiting_high)
async def fsm_set_high(message: Message, state: FSMContext, db: DatabaseManager) -> None:
    assert message.from_user is not None
    raw = message.text.strip() if message.text else ""
    try:
        val = float(raw)
        if not (51 <= val <= 99):
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ <b>ɪɴᴠᴀʟɪᴅ ᴠᴀʟᴜᴇ</b>\n\n"
            "<blockquote>Please enter a number between 51 and 99.\nExample: <code>85</code></blockquote>",
            reply_markup=back_keyboard()
        )
        return

    async with db.session() as session:
        sub = await session.get(Subscriber, message.from_user.id)
        if sub:
            sub.custom_stoch_high = val

    await state.clear()
    sub, low_thresh, high_thresh = await _load_settings_data(db, message.from_user.id)
    await message.answer(
        f"✅ <b>ᴏᴠᴇʀʙᴏᴜɢʜᴛ ʟɪᴍɪᴛ ᴜᴘᴅᴀᴛᴇᴅ ᴛᴏ {val}</b>\n\n"
        "<blockquote>You will now receive alerts when StochRSI %K rises above this value.</blockquote>\n\n"
        + _build_settings_text(sub, low_thresh, high_thresh),
        reply_markup=settings_keyboard(bool(sub and sub.is_active))
    )
