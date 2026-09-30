import asyncio
from src.infra.logging import configure_logging
from src.data.yfinance_provider import YFinanceProvider
from src.services.scanner import ScannerService
from src.services.alerter import AlerterService
from src.infra.database import DatabaseManager

async def test_run():
    configure_logging(log_level="DEBUG", json_logs=False)
    db = DatabaseManager("sqlite+aiosqlite:///./dev.db")
    provider = YFinanceProvider()
    scanner = ScannerService(provider=provider, db=db)
    from src.infra.config import settings
    from aiogram import Bot
    bot = Bot(token=settings.bot_token)
    
    alerter = AlerterService(bot=bot, db=db)
    
    try:
        res = await scanner.run_scan(force=True)
        print("Scan result:", res)
        if res.signals:
            dispatched = await alerter.process_and_send(res)
            print("Alerts dispatched:", dispatched)
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(test_run())
