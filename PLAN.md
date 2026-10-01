# 📅 Project Plan: Nifty 50 StochRSI Scanner

## 🎯 Current Status
The project is currently **Production-Ready (v1.0)**. 
All core components are fully implemented, resilient, and tested. The bot is actively capable of running on any VPS or PaaS (like Render) and seamlessly delivering Telegram alerts.

## 🏗️ Architecture & Features (Implemented)
- **Data Pipeline**: Time-aware, timezone-adjusted daily market scanning that accurately respects the NSE market close (15:30 IST) and gracefully handles API rate limits using exponential backoff (`tenacity`).
- **Idempotent DB Migrations**: Custom namespace isolation for Alembic (`db_migrations`) preventing environment path shadowing.
- **Python 3.10+ Compatibility**: Gracefully downgraded standard library dependencies (like `logging` level mapping) and strictly pinned ORM libraries (SQLAlchemy `2.0.36`) to work perfectly on standard Linux VPS distros (Ubuntu 22.04 LTS).
- **Premium Reporting**: Generated responsive, beautifully styled HTML reports dynamically sent via Telegram with fallback chunking for character limits.
- **Dynamic Thresholds**: Personalized settings allowing individual subscribers to tune their Oversold/Overbought targets.

## 🧹 Housekeeping / Clean-Up Needed
- [ ] Remove temporary script files (`scratch_test_yf.py`, `test_yf2.py`).
- [ ] Ensure SQLite `dev.db` is strictly ignored in version control to prevent VPS conflicts.
- [ ] Remove unused standard libraries or broken dependency references.

## 🚀 Future Roadmap & Enhancements

### Phase 2 (Data Expansion)
- **Multi-Provider Strategy**: Implement a secondary provider (e.g. Fyers, Upstox, or NSE India API directly) to fallback if `yfinance` permanently blocks requests.
- **Intraday Scanning**: Upgrade the database schema to support 15-minute or 1-hour candles to capture intraday swing opportunities.

### Phase 3 (Feature Additions)
- **Additional Indicators**: Implement MACD crossover or Bollinger Bands alongside StochRSI.
- **Portfolio Tracking**: Allow users to maintain a custom watchlist beyond the Nifty 50 constituents.
- **User Analytics**: Provide a weekly summary of successful setups vs. false positives.

### Phase 4 (Performance & Scaling)
- **Redis Caching**: Offload database hits for frequently accessed data (like holiday calendars).
- **Asynchronous Webhooks**: Scale the Telegram dispatcher by decoupling the bot webhook receiver from the heavy database query execution.
