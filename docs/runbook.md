# Operations Runbook — Nifty 50 Stochastic RSI Scanner

This document outlines routine maintenance, operational procedures, and troubleshooting steps for maintaining the Nifty 50 Stochastic RSI Scanner service.

---

## 1. Annual NSE Holiday Calendar Update

The NSE trading calendar is maintained in `docs/holidays.json`. The scanner checks this file before executing daily Post-Market scans to skip execution on official exchange holidays.

### Update Procedure (Every December)

1. Obtain the official NSE Holiday Calendar for the upcoming year from the official website ([NSE India](https://www.nseindia.com/resources/exchange-communication-holidays)).
2. Edit `docs/holidays.json` to add the new year's dates under the `"holidays"` array.
3. Validate JSON formatting:
   ```bash
   python -m json.tool docs/holidays.json
   ```
4. Commit and push the changes:
   ```bash
   git add docs/holidays.json
   git commit -m "ops: update NSE holiday calendar for 2027"
   git push origin main
   ```
5. On Render, redeployment triggers automatically upon push.

---

## 2. Nifty 50 Constituent Index Refresh

The system syncs the official constituent list automatically from `NIFTY50_CSV_URL` during scanner execution. If NSE updates symbol tickers or additions/deletions occur:

- Standard sync runs automatically without manual intervention.
- To force an immediate index resync, execute the admin command via Telegram:
  `/scan`
  or trigger the internal API endpoint:
  ```bash
  curl -X POST https://your-app-name.onrender.com/internal/run-scan \
    -H "Authorization: Bearer YOUR_CRON_SECRET"
  ```

---

## 3. Database Backup & Maintenance

### SQLite (Local Development)
- Database path: `./dev.db`
- Backup: Copy `dev.db` to a backup location before running schema migrations.

### PostgreSQL (Render Production)
- Render automatically provides managed PostgreSQL backups.
- To take a manual snapshot:
  1. Go to Render Dashboard -> Databases -> your-db-instance.
  2. Click **Backups** -> **Create Manual Backup**.
- Schema migrations run using Alembic:
  ```bash
  alembic upgrade head
  ```

---

## 4. Telegram Bot Secret Rotation

If `BOT_TOKEN` is leaked or compromised:

1. Open Telegram and talk to [@BotFather](https://t.me/BotFather).
2. Select your bot and click **Revoke Token** -> **New Token**.
3. Update environment variable `BOT_TOKEN` in Render Environment Settings.
4. Restart the Render Web Service.
5. If using webhook mode, the service re-registers the webhook automatically upon startup.

---

## 5. Troubleshooting Common Issues

### Issue A: "No alerts sent post-market"
1. **Check if today is an NSE Holiday or Weekend**:
   - Weekends (Sat/Sun) and dates listed in `docs/holidays.json` are skipped by design.
2. **Check Cron Execution**:
   - Inspect Render Cron Job logs to verify HTTP 200 response from `/internal/run-scan`.
3. **Verify Market Data Availability**:
   - YFinance data for NSE post-market closes around 16:15–16:30 IST. If run too early (e.g. before 16:15 IST), close prices may not be populated yet.

### Issue B: Telegram `429 Too Many Requests`
- The system includes a built-in rate limiter (`RATE_LIMIT_MESSAGES` per `RATE_LIMIT_WINDOW_SECONDS`) and exponential backoff retry.
- Check logs for retry attempts; subscriber broadcasts automatically delay to comply with Telegram API limits.

### Issue C: `pydantic-settings` Module Error
- `pydantic-settings` must remain pinned to `==2.6.1` in `requirements.txt` to avoid upstream CLI module breakage in 2.7.x.

---

## 6. Monitoring Health Endpoints

- Liveness check: `GET /health` (Returns `{"status": "ok", "environment": "production"}`)
- Readiness check: `GET /ready` (Validates database connection readiness)
