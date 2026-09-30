"""FastAPI routes — health checks, webhook, and internal scan trigger."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from src.api.dependencies import verify_cron_secret
from src.infra.config import settings
from src.infra.logging import get_logger

log = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness check — always returns 200 if the app is running."""
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    """Readiness check — verifies DB connectivity."""
    try:
        db = request.app.state.db
        async with db.session() as session:
            await session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        log.error("readiness_check_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Database not ready")


# ---------------------------------------------------------------------------
# Telegram webhook
# ---------------------------------------------------------------------------


@router.post(settings.webhook_path)
async def telegram_webhook(request: Request) -> Response:
    """Receive and process Telegram updates via webhook.

    Validates the X-Telegram-Bot-Api-Secret-Token header if WEBHOOK_SECRET is set.
    """
    # Validate secret token
    if settings.webhook_secret:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if token != settings.webhook_secret:
            log.warning("invalid_webhook_secret_token")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    body = await request.body()
    update = Update.model_validate_json(body)

    bot: Bot = request.app.state.bot
    dp: Dispatcher = request.app.state.dp
    await dp.feed_update(bot=bot, update=update)

    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Internal scan trigger (called by Render Cron Job)
# ---------------------------------------------------------------------------


class ScanResponse(BaseModel):
    status: str
    trading_date: str | None = None
    processed: int = 0
    failed: int = 0
    signals: int = 0
    alerts_dispatched: int = 0
    duration_ms: int = 0
    message: str = ""


@router.post(
    "/internal/run-scan",
    dependencies=[Depends(verify_cron_secret)],
    response_model=ScanResponse,
)
async def run_scan_endpoint(request: Request, force: bool = False) -> ScanResponse:
    """Trigger the Nifty 50 StochRSI scan.

    Protected by Bearer token (CRON_SECRET env var).
    Called by Render Cron Job at 16:30 IST on trading days.
    """
    from src.data.calendar import is_trading_day
    from src.data.yfinance_provider import YFinanceProvider
    from src.services.alerter import AlerterService
    from src.services.scanner import ScannerService

    if not is_trading_day() and not force:
        log.info("scan_skipped_non_trading_day")
        return ScanResponse(status="skipped", message="Not a trading day.")

    db = request.app.state.db
    bot: Bot = request.app.state.bot

    try:
        provider = YFinanceProvider()
        scanner = ScannerService(provider=provider, db=db)
        result = await scanner.run_scan(force=force)

        if result.was_skipped:
            return ScanResponse(
                status="already_ran",
                trading_date=str(result.trading_date),
                message="Scan already completed for today.",
            )

        alerts_dispatched = 0
        if result.signals:
            alerter = AlerterService(bot=bot, db=db)
            alerts_dispatched = await alerter.process_and_send(result)

        return ScanResponse(
            status="success",
            trading_date=str(result.trading_date),
            processed=result.processed,
            failed=result.failed,
            signals=len(result.signals),
            alerts_dispatched=alerts_dispatched,
            duration_ms=result.duration_ms,
        )

    except Exception as exc:
        log.error("scan_endpoint_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


