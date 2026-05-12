# Deployment Guide

## Architecture

- Python engine: run as a long-lived process on a VPS or private server
- Supabase: managed PostgreSQL source of truth
- Next.js dashboard: deploy to Vercel or a private Node host
- Telegram bot: runs inside the Python engine process

## Backend Deployment

1. Provision a Linux VM or Windows server with Python 3.11+.
2. Copy the repository and create a virtual environment.
3. Install backend dependencies:

```bash
pip install -r requirements.txt
```

4. Create a `.env` file from `.env.example`.
5. Run the SQL in [supabase/schema.sql](/D:/Projects/Trading Engine/supabase/schema.sql:1).
6. Start the engine with a process manager.

### `systemd` example

```ini
[Unit]
Description=Private Trading Intelligence Engine
After=network.target

[Service]
WorkingDirectory=/opt/trading-intelligence
ExecStart=/opt/trading-intelligence/.venv/bin/python main.py
Restart=always
EnvironmentFile=/opt/trading-intelligence/.env

[Install]
WantedBy=multi-user.target
```

## Frontend Deployment

1. Change into `dashboard/`.
2. Install dependencies:

```bash
npm install
```

3. Create `dashboard/.env.local` from `dashboard/.env.example`.
4. Run locally:

```bash
npm run dev
```

5. Deploy with Vercel or another Node host using `npm run build` and `npm run start`.

## Recommended Production Posture

- Keep `EXECUTION_MODE=paper` until the process is consistently followed.
- Keep `BYBIT_ALLOW_ORDER_PLACEMENT=false` by default.
- Keep `BYBIT_REQUIRE_MANUAL_CONFIRMATION=true` for any future live rollout.
- Restrict dashboard access and never expose the Supabase service role key to the browser.
- Rotate Bybit, Supabase, and Telegram secrets if they are ever shared accidentally.

## Live Cutover Checklist

1. Verify paper-trading results over a meaningful sample of trades.
2. Confirm the daily loss limit and risk-per-trade settings still reflect current capital.
3. Confirm Bybit keys are spot-only and do not allow unnecessary privileges.
4. Test Telegram command access with `TELEGRAM_ALLOWED_USER_ID`.
5. Move to Bybit mainnet only after manual confirmation flow is reviewed end to end.
