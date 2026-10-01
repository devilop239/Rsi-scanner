<div align="center">

# 📈 Nifty 50 Stochastic RSI Scanner

**An automated, production-grade service that monitors all 50 Nifty constituents daily,
computes Stochastic RSI, and delivers real-time Telegram alerts for oversold and overbought stocks.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.31-2CA5E0?logo=telegram&logoColor=white)](https://docs.aiogram.dev)
[![SQLAlchemy 2.x](https://img.shields.io/badge/SQLAlchemy-2.1-D71F00?logo=sqlalchemy&logoColor=white)](https://sqlalchemy.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## Table of Contents

- [What This Does](#what-this-does)
- [How It Works](#how-it-works)
- [Indicator Specification](#indicator-specification)
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Bot Commands Reference](#bot-commands-reference)
- [Alert Format](#alert-format)
- [Local Development Setup](#local-development-setup)
- [Environment Variables](#environment-variables)
- [Database](#database)
- [Deployment on Render](#deployment-on-render)
- [Telegram Bot Setup](#telegram-bot-setup)
- [Market Calendar & Holidays](#market-calendar--holidays)
- [Updating Nifty 50 Constituents](#updating-nifty-50-constituents)
- [Running Tests](#running-tests)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)

---

## What This Does

This service:

1. **Scans all 50 Nifty 50 stocks** once per trading day at 16:30 IST (after market close)
2. **Computes Stochastic RSI** on daily adjusted-close candles — matching TradingView's default indicator exactly
3. **Sends Telegram alerts** when a stock enters an extreme zone:
   - 🔴 **Overbought** — Stochastic RSI %K above **80**
   - 🟢 **Oversold** — Stochastic RSI %K below **20**
4. **Deduplicates alerts** — only notifies when a stock *enters* a zone; won't spam you for consecutive days in the same zone
5. **Skips weekends and NSE trading holidays** automatically
6. **Provides a Telegram bot** for on-demand scans, status checks, and subscription management

All thresholds, indicator lengths, and behaviors are configurable via environment variables — nothing is hardcoded.

---

## How It Works

```
16:30 IST (Mon–Fri)
       │
       ▼
Render Cron Job ──POST /internal/run-scan──▶ FastAPI App
                                                  │
                              ┌───────────────────┘
                              ▼
                    Fetch 200 days of adjusted
                    close prices from Yahoo Finance
                    for all 50 Nifty tickers (1 batch call)
                              │
                              ▼
                    Compute Stochastic RSI
                    (RSI-14 Wilder + Stoch-14 + K-3 + D-3)
                              │
                              ▼
                    Save candles + indicator values to DB
                              │
                              ▼
                    Check thresholds: K < 20 or K > 80?
                              │
                    ┌─────────┴─────────┐
                   Yes                  No
                    │                   │
                    ▼                   ▼
             Check dedup table      No alert sent
             (alerted recently?)
                    │
                    ▼
             Broadcast HTML alert
             to all subscribers via Telegram
                    │
                    ▼
             Log scan result to DB
             (duration, failures, signals found)
```

**On data source failure:** Retries up to 3× with exponential backoff. If more than 20% of tickers fail, the admin receives a separate Telegram notification.

---

## Indicator Specification

The implementation **exactly matches TradingView's default Stochastic RSI indicator** so you can visually cross-check values on any chart.

| Parameter | Default | What It Does |
|---|---|---|
| RSI Length | 14 | Wilder's EWM smoothing: `ewm(alpha=1/14, adjust=False)` |
| Stoch Length | 14 | Rolling window applied to the RSI series |
| %K Smooth | 3 | `SMA(stoch_raw, 3)` |
| %D Smooth | 3 | `SMA(%K, 3)` — the signal line |
| Signal Line | %K | Threshold comparison uses %K by default (switchable to %D) |
| Timeframe | Daily | Uses completed daily candles only |
| Price | Adj Close | Split- and dividend-adjusted to prevent corporate-action distortion |

**Math walkthrough:**

```
1.  RSI  = Wilder EWM RSI of Adj Close (length 14)
2.  raw  = 100 × (RSI − min(RSI, 14)) / (max(RSI, 14) − min(RSI, 14))
           → returns 0 when max == min (flat price guard)
3.  %K   = SMA(raw, 3)
4.  %D   = SMA(%K, 3)
5.  Signal when %K < 20 → OVERSOLD
            %K > 80 → OVERBOUGHT
```

> **Why Wilder, not plain SMA RSI?**  Plain SMA RSI gives different numbers. Wilder's EWM is what TradingView uses and what most traders look at. The test suite asserts they are statistically different.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   FastAPI App                        │
│  ┌──────────────┐  ┌────────────┐  ┌─────────────┐  │
│  │ Webhook      │  │ /health    │  │ /internal/  │  │
│  │ /telegram    │  │ /ready     │  │ run-scan    │  │
│  └──────┬───────┘  └────────────┘  └──────┬──────┘  │
│         │                                 │         │
│  ┌──────▼──────┐              ┌───────────▼──────┐  │
│  │ aiogram Bot │              │  Scan Service    │  │
│  │ Handlers    │              │  (Orchestrator)  │  │
│  └──────┬──────┘              └──────────┬───────┘  │
│         │                               │           │
│  ┌──────▼───────────────────────────────▼───────┐  │
│  │              SQLAlchemy Async ORM             │  │
│  │         PostgreSQL (prod) / SQLite (dev)      │  │
│  └──────────────────────────────────────────────┘  │
│                                                     │
│  ┌─────────────────┐    ┌────────────────────────┐  │
│  │ Domain Layer    │    │ Data Layer             │  │
│  │ indicators.py   │    │ MarketDataProvider     │  │
│  │ (pure math,     │    │ YFinanceProvider       │  │
│  │  no I/O)        │    │ FallbackProvider       │  │
│  └─────────────────┘    └────────────────────────┘  │
└─────────────────────────────────────────────────────┘
         ▲                          ▲
         │                          │
  Telegram API              Yahoo Finance API
```

**Layer responsibilities:**

| Layer | Location | Rule |
|---|---|---|
| `domain/` | `indicators.py` | Pure math — zero imports from other layers |
| `data/` | `base.py`, providers | Data fetching — never imports domain or services |
| `services/` | `scanner.py`, `alerter.py` | Business logic — imports domain + data |
| `bot/` | handlers, middleware | Telegram UI — imports services only |
| `api/` | routes, dependencies | HTTP interface — thin pass-through |
| `infra/` | config, db, logging | Infrastructure — imported by all layers |

---

## Project Structure

```
nifty-stoch-rsi/
│
├── src/
│   ├── domain/
│   │   └── indicators.py        ← StochRSI math (no I/O)
│   ├── data/
│   │   ├── base.py              ← MarketDataProvider Protocol
│   │   ├── yfinance_provider.py ← yfinance implementation
│   │   └── index_repo.py        ← Nifty 50 CSV parser
│   ├── services/
│   │   ├── scanner.py           ← Scan orchestrator
│   │   └── alerter.py           ← Alert deduplication logic
│   ├── bot/
│   │   ├── handlers/            ← aiogram command handlers
│   │   ├── middlewares/         ← Rate limiting, access control
│   │   ├── keyboards.py         ← Inline keyboard builders
│   │   └── setup.py             ← Webhook / polling setup
│   ├── api/
│   │   ├── routes.py            ← /health, /ready, /internal/run-scan
│   │   └── dependencies.py      ← FastAPI DI helpers
│   └── infra/
│       ├── config.py            ← Pydantic Settings (single source of truth)
│       ├── database.py          ← Async SQLAlchemy engine
│       ├── models.py            ← All ORM models
│       └── logging.py           ← Structured JSON logging
│
├── tests/
│   ├── conftest.py              ← In-memory SQLite fixtures
│   ├── test_domain/             ← Indicator unit tests
│   ├── test_data/               ← Provider tests (mocked network)
│   └── test_services/           ← Scanner + alerter tests
│
├── alembic/
│   ├── versions/
│   │   └── 0001_initial_schema.py
│   └── env.py                   ← Async-compatible Alembic setup
│
├── docs/
│   └── runbook.md               ← Holiday updates, secret rotation, etc.
│
├── .env.example                 ← All variables documented
├── pyproject.toml               ← Dependencies + tool config
├── alembic.ini
├── Dockerfile                   ← Multi-stage, non-root, slim
├── render.yaml                  ← Render Blueprint
└── .github/workflows/ci.yml     ← Lint → typecheck → test → Docker build
```

---

## Bot Commands Reference

All commands work in a private chat with the bot after starting it.

### General Commands (All Users)

| Command | Description |
|---|---|
| `/start` | Register and get a welcome message. Activates your subscription to daily alerts. |
| `/help` | Show all available commands with brief descriptions. |
| `/subscribe` | Subscribe to daily automated scan alerts. |
| `/unsubscribe` | Stop receiving daily alerts (you can re-subscribe anytime). |
| `/status` | Show when the last scan ran, how many stocks were processed, and data-source health. |

### On-Demand Scans (All Users)

| Command | Description |
|---|---|
| `/scan` | Trigger an immediate scan of all 50 stocks and show results (uses cached data if available for today). |
| `/oversold` | Show all stocks currently in the oversold zone (StochRSI %K < 20). |
| `/overbought` | Show all stocks currently in the overbought zone (StochRSI %K > 80). |
| `/rsi <SYMBOL>` | Show the full StochRSI breakdown for a specific symbol. Example: `/rsi RELIANCE` or `/rsi TCS` |

### Settings (All Users — Personal)

| Command | Description |
|---|---|
| `/settings` | Opens an inline keyboard to view and adjust your personal alert preferences. |

> **Note:** The `/settings` command uses Telegram's inline keyboard. Tap the buttons to toggle options — no typing needed.

### Admin Commands

Admin access requires your Telegram user ID to be in the `ADMIN_IDS` environment variable.

| Command | Description |
|---|---|
| `/scan force` | Force a fresh scan even if one already ran today (bypasses idempotency). |
| `/status full` | Extended status with DB row counts, provider health, and last 5 scan run details. |

---

## Alert Format

When stocks enter extreme zones, subscribers receive a formatted HTML message like this:

```
📊 Nifty 50 StochRSI Alert — 01 Oct 2024

🟢 OVERSOLD (StochRSI %K < 20)

• WIPRO.NS
  Close: ₹432.10  |  RSI: 28.4  |  %K: 11.2  |  %D: 13.8

• HCLTECH.NS
  Close: ₹1,256.70  |  RSI: 31.1  |  %K: 8.5  |  %D: 10.2

─────────────────────────────
🔴 OVERBOUGHT (StochRSI %K > 80)

• ADANIENT.NS
  Close: ₹2,890.00  |  RSI: 72.3  |  %K: 88.1  |  %D: 85.4

─────────────────────────────
⚠️ This alert is for informational purposes only and does not
constitute investment advice. Always do your own research.
```

**Alert rules:**
- Only sent when a stock **enters** an extreme zone (not repeated daily while in the zone)
- Re-alerted only after the stock **exits and re-enters** the zone, or after 7 days (configurable)
- Messages exceeding Telegram's 4096-character limit are automatically split
- Rate limiting on Telegram's end (`TelegramRetryAfter`) is handled gracefully

---

## Local Development Setup

### Prerequisites

- Python 3.11 or 3.12
- A Telegram bot token (see [Telegram Bot Setup](#telegram-bot-setup))

### Step-by-Step

```powershell
# 1. Clone / enter the project folder
cd "Nifity 50 project"

# 2. Create a virtual environment
python3 -m venv .venv
.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate        # Linux / macOS

# 3. Install all dependencies (editable + dev tools)
pip install -e ".[dev]"

# 4. Set up environment variables
copy .env.example .env
# Open .env and fill in BOT_TOKEN, ADMIN_IDS, CRON_SECRET at minimum

# 5. Run database migrations (creates dev.db SQLite file)
alembic upgrade head

# 6. Start the bot (long-polling mode for local dev)
python -m src.main
```

The bot will start polling Telegram for messages. Try sending `/start` in your bot chat.

### Trigger a Manual Scan (Local)

```powershell
# The scan endpoint requires the CRON_SECRET bearer token
$secret = "your_cron_secret_from_env"
Invoke-WebRequest -Uri "http://localhost:8000/internal/run-scan" `
  -Method POST `
  -Headers @{ Authorization = "Bearer $secret" }
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values. Below is a summary of the most important ones.

### Required

| Variable | Example | Description |
|---|---|---|
| `BOT_TOKEN` | `123456:ABC-xyz...` | Telegram bot token from @BotFather |
| `ADMIN_IDS` | `123456789` | Your Telegram user ID (comma-separated for multiple) |
| `CRON_SECRET` | `abc123...` | Bearer token protecting the scan trigger endpoint |

### Optional (with sensible defaults)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | SQLite `./dev.db` | Use `postgresql+asyncpg://...` in production |
| `WEBHOOK_BASE_URL` | *(empty = polling)* | Set to your Render URL in production |
| `WEBHOOK_SECRET` | *(empty)* | Security token for webhook validation |
| `STOCH_LOW` | `20` | Oversold threshold |
| `STOCH_HIGH` | `80` | Overbought threshold |
| `RSI_LENGTH` | `14` | RSI period |
| `STOCH_LENGTH` | `14` | Stochastic window |
| `SIGNAL_LINE` | `k` | Use `k` or `d` for threshold comparison |
| `ALERT_COOLDOWN_DAYS` | `7` | Days before re-alerting the same stock/zone |
| `ALERT_ON_EXIT` | `false` | Also notify when a stock *leaves* the extreme zone |
| `MIN_CANDLES` | `100` | Skip stocks with less than this many candles |
| `SCAN_CRON` | `30 16 * * 1-5` | Cron schedule (Mon–Fri 16:30 IST) |
| `ENVIRONMENT` | `development` | `development` or `production` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

> **Security:** Never commit `.env` to version control. The `.gitignore` excludes it.
>
> **Generate secrets:** `python -c "import secrets; print(secrets.token_hex(32))"`

---

## Database

### Schema Overview

```
symbols              daily_candles         indicator_values
───────              ─────────────         ────────────────
id (PK)              id (PK)               id (PK)
ticker (unique)      symbol_id (FK)        symbol_id (FK)
company_name         candle_date           value_date
isin                 open/high/low         rsi
is_active            close/adj_close       stoch_raw
added_at             volume                stoch_k / stoch_d
updated_at           fetched_at            computed_at

signals              alerts_sent           subscribers
───────              ───────────           ───────────
id (PK)              id (PK)               telegram_user_id (PK)
symbol_id (FK)       symbol_id (FK)        chat_id (unique)
signal_date          signal_type           username / first_name
signal_type          alert_date            role (ADMIN/USER)
stoch_k/d/rsi        sent_at               is_active
close                chat_ids_notified     subscribed_at

scan_runs
─────────
id (PK)
started_at / completed_at
trading_date (indexed)
status (RUNNING/SUCCESS/FAILED/SKIPPED)
items_processed / items_failed
signals_found / alerts_dispatched
error_message
```

### Migration Commands

```bash
# Apply all pending migrations (run this on first setup and after updates)
alembic upgrade head

# Check current migration version
alembic current

# See migration history
alembic history

# Roll back one migration
alembic downgrade -1

# Roll back all migrations (WARNING: deletes all data)
alembic downgrade base

# Auto-generate a new migration after changing models
alembic revision --autogenerate -m "describe your change"
```

### Data Retention

Old rows are automatically cleaned up based on these defaults (all configurable):

| Table | Retention |
|---|---|
| `daily_candles` | 730 days (2 years) |
| `indicator_values` | 365 days (1 year) |
| `scan_runs` | 90 days |

---

## Deployment on Render

### Prerequisites

1. A [Render](https://render.com) account
2. A GitHub repository with this code
3. A Telegram bot token

### One-Click Deploy (Blueprint)

The `render.yaml` in the root defines everything. In Render dashboard:

1. Click **New** → **Blueprint**
2. Connect your GitHub repository
3. Render will auto-create:
   - A **Web Service** (FastAPI app + aiogram webhook)
   - A **Cron Job** (daily scan trigger at 16:30 IST)
   - A **PostgreSQL database**

### Manual Setup

```
Render Dashboard → New Web Service
  Runtime:       Python
  Build Command: pip install -e ".[dev]"
  Start Command: python -m src.main
  Health Check:  /health
```

Set these environment variables in Render:

```
BOT_TOKEN          = <your token>
DATABASE_URL       = <auto-filled from Render Postgres>
WEBHOOK_BASE_URL   = https://your-app-name.onrender.com
WEBHOOK_SECRET     = <generate with secrets.token_hex(32)>
ADMIN_IDS          = <your Telegram user ID>
CRON_SECRET        = <generate with secrets.token_hex(32)>
ENVIRONMENT        = production
```

### Free Tier Behavior

> **Important:** Render free web services sleep after 15 minutes of inactivity. The Cron Job acts as a daily wake-up, but cold starts can take 20–30 seconds. This is acceptable for a daily scan — the cron will wait for the app to boot.

For always-on behavior, upgrade to Render's paid Starter plan ($7/month).

---

## Telegram Bot Setup

### Creating Your Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g. `Nifty StochRSI Scanner`)
4. Choose a username ending in `bot` (e.g. `nifty_stochrsi_bot`)
5. BotFather gives you a **token** — copy it to `BOT_TOKEN` in your `.env`

### Finding Your User ID

1. Search for **@userinfobot** on Telegram
2. Send `/start` — it replies with your user ID
3. Add this to `ADMIN_IDS` in your `.env`

### Setting the Webhook (Production)

The app sets and removes the webhook automatically on startup/shutdown. If you need to set it manually:

```bash
# Set webhook
curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -d "url=https://your-app.onrender.com/webhook/telegram" \
  -d "secret_token=<WEBHOOK_SECRET>"

# Verify webhook status
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"

# Remove webhook (to switch back to long-polling)
curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/deleteWebhook"
```

---

## Market Calendar & Holidays

NSE trading holidays are defined in `docs/holidays.json`. The scanner checks this file before running and skips non-trading days automatically.

### Updating the Holiday List (Annual Task)

1. Visit the [NSE official holidays page](https://www.nseindia.com/resources/exchange-communication-holidays)
2. Download the trading holiday list for the new year
3. Edit `docs/holidays.json`:

```json
{
  "market": "NSE",
  "timezone": "Asia/Kolkata",
  "holidays": [
    {"date": "2025-01-26", "description": "Republic Day"},
    {"date": "2025-03-14", "description": "Holi"},
    ...
  ]
}
```

4. Commit the file — the change takes effect immediately on next deployment

See [`docs/runbook.md`](docs/runbook.md) for the full procedure.

---

## Updating Nifty 50 Constituents

The index is rebalanced approximately twice a year (March and September). To update:

```bash
# Option 1: Re-fetch from NSE's official CSV (requires internet)
python -m src.data.index_repo --refresh

# Option 2: Manually update the bundled fallback list
# Edit: src/data/nifty50_fallback.py
# Then run:
alembic upgrade head   # not needed unless schema changed
python -m src.data.index_repo --sync-db
```

The `symbols` table in the database is the live source of truth. The CSV and fallback list are used only when the DB is empty or unreachable.

---

## Running Tests

```bash
# Run all tests with coverage report
pytest

# Run only domain/indicator tests (fast, no DB needed)
pytest tests/test_domain/ -v

# Run with verbose output and no coverage
pytest -v --no-cov

# Run excluding live/integration tests (no internet needed)
pytest -m "not live and not integration"

# Run a specific test
pytest tests/test_domain/test_indicators.py::TestComputeStochRSI::test_flat_prices_no_crash -v

# Check code style
ruff check src/ tests/

# Auto-fix style issues
ruff check --fix src/ tests/

# Type checking
mypy src/
```

**Coverage target:** 85%+ on `domain/` and `services/` layers.

---

## Troubleshooting

### Bot not responding to commands

1. Check your `BOT_TOKEN` is correct (no spaces, no extra characters)
2. In development: confirm long-polling is active — check the console for `Polling started`
3. In production: verify the webhook is set — use `getWebhookInfo` (see above)
4. Ensure the app is running: `curl https://your-app.onrender.com/health`

### `ModuleNotFoundError: No module named 'numpy'`

Your IDE or terminal is using the wrong Python. Activate the venv first:

```powershell
.venv\Scripts\Activate.ps1
```

Then confirm: `python -c "import numpy; print(numpy.__version__)"` — should print a version number.

### Database migration errors

```bash
# Check current state
alembic current

# If "None" (no migrations applied), run:
alembic upgrade head

# If schema is out of sync, try:
alembic stamp head    # marks current state as latest without running migrations
```

### Render app is sleeping / cold start too slow

- The free Cron Job pings the app at 16:30 IST — first request after sleep takes 20–30s
- The Cron Job has a 60-second timeout, which is enough for a cold start
- If scans are silently failing, check Render logs: Dashboard → Web Service → Logs

### Yahoo Finance returning empty data

- Yahoo Finance occasionally rate-limits or changes its API without notice
- The service retries 3× with exponential backoff automatically
- If all 50 tickers fail, the admin receives a Telegram notification
- Check yfinance GitHub issues for known outages

### Duplicate alerts being sent

- Check the `alerts_sent` table in the DB — it tracks all dispatched alerts
- The `ALERT_COOLDOWN_DAYS` setting (default 7) controls the cooldown period
- If the table is corrupt, you can clear it: `DELETE FROM alerts_sent;`

---

## Known Limitations

| Limitation | Detail |
|---|---|
| **Yahoo Finance reliability** | yfinance is an unofficial API. Occasional outages or data gaps are expected. A broker API (Fyers, Upstox) can be plugged in later without code changes. |
| **End-of-day only** | The scanner uses daily candles only. Intraday signals are not supported in this version. |
| **NSE holidays** | The holiday list in `docs/holidays.json` must be manually updated each year. |
| **Nifty 50 rebalancing** | New constituents added to the index will not be scanned until you refresh the symbol list. |
| **Render free tier** | Free services sleep. Cold starts add 20–30 seconds to the first request after inactivity. |
| **Single data source** | Currently only yfinance is implemented. The fallback provider is a stub — a second real provider has not been wired up yet. |

---

<div align="center">

Built with ❤️ for the Indian equity markets  
**Not investment advice. For educational and informational purposes only.**

</div>
