<div align="center">

# 📈 Nifty 50 Stochastic RSI Scanner

**An automated, production-grade service that monitors all 50 Nifty constituents daily,
computes Stochastic RSI, and delivers real-time Telegram alerts.**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.31-2CA5E0?logo=telegram&logoColor=white)](https://docs.aiogram.dev)

</div>

---

## ⚡ What This Does

1. **Daily Scans**: Monitors Nifty 50 stocks every trading day at 16:30 IST.
2. **Precision Tracking**: Computes Stochastic RSI exactly matching TradingView's default formula.
3. **Telegram Alerts**: Instantly notifies subscribers when a stock enters extreme zones:
   - 🔴 **Overbought**: StochRSI %K > 80
   - 🟢 **Oversold**: StochRSI %K < 20
4. **Smart Deduplication**: Prevents spam by only alerting on zone entry, not consecutive days.
5. **Interactive Bot**: Manage subscriptions, view live signals, and tweak personal thresholds via Telegram.

---

## 🚀 Quick Start (Local & VPS)

### 1. Prerequisites
- Python 3.10+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))

### 2. Setup

```bash
# Clone and enter directory
git clone https://github.com/devilop239/Rsi-scanner.git
cd Rsi-scanner

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # (Windows: .venv\Scripts\Activate.ps1)

# Install dependencies
pip install -e ".[dev]"

# Configure environments
cp .env.example .env
# Edit .env with your BOT_TOKEN, ADMIN_IDS, and CRON_SECRET

# Initialize the Database
alembic upgrade head
```

### 3. Running the Bot
```bash
# Start the FastAPI / Telegram Bot polling service
python -m src.main
```

> **Automated Scanning:** To run the daily scan, hit the internal endpoint (e.g., via a VPS Cron job or Render Cron):
> `curl -X POST -H "Authorization: Bearer YOUR_CRON_SECRET" http://localhost:8000/internal/run-scan`

---

## 🤖 Bot Commands

Interact with your bot on Telegram:

| Command | Action |
|---|---|
| `/start` | Register and activate daily alerts. |
| `/scan` | Force a manual scan of all 50 stocks instantly. |
| `/oversold` | List all stocks currently in the oversold zone. |
| `/overbought` | List all stocks currently in the overbought zone. |
| `/rsi <TICKER>` | Get the full StochRSI breakdown for a specific stock (e.g. `/rsi TCS`). |
| `/settings` | Tweak your custom Oversold/Overbought thresholds. |
| `/status` | View system health, last scan time, and subscriber count. |

---

## 📁 Project Structure

- **`src/`**: Core logic (FastAPI, Telegram Bot, Data fetchers, Indicators).
- **`db_migrations/`**: Alembic database migrations.
- **`tests/`**: Pytest suite for domain and service layers.
- **`docs/`**: Runbooks and NSE holiday configurations.
- **`PLAN.md`**: Roadmap and upcoming features.

<div align="center">
Built with ❤️ for the Indian equity markets. Not investment advice.
</div>
