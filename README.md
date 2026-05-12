# Private Trading Intelligence Platform

Private trading assistant for disciplined crypto decision support on Bybit spot markets. The platform is designed for personal use, capital protection, paper-trading rehearsal, risk-aware execution review, and performance learning.

## Operating Principles

- Protect capital before chasing opportunity
- Keep risk fixed and explicit
- Prefer high-quality signals over high signal volume
- Treat SELL conditions in spot mode as exit or de-risk signals, not short entries
- Keep live order placement disabled until paper-trading behavior is trusted

## Current Scope

- Binance public spot market data for signal candles
- Bybit V5 spot integration for optional future live execution
- Testnet-first operation
- Paper trading mode with simulated fills, spot fees, slippage, and PnL tracking
- Automatic paper entries once a setup clears the risk filters
- Optional dual-lane execution where paper and live can run side by side from the same signal
- Mainnet read-only posture for live balance verification before real-money rollout
- Manual-confirmed live entries through Telegram
- Exchange-backed live exit handling once a live position exists
- Tiny-size rollout caps for first live trades
- Supabase-backed signals, trades, journal entries, and performance logs
- Telegram bot commands and alerting
- Optional Anthropic-powered AI coach for read-only trade guidance
- Next.js private dashboard for monitoring and review

## Project Structure

```text
.
|-- .env.example
|-- DEPLOYMENT.md
|-- README.md
|-- requirements.txt
|-- main.py
|-- dashboard/
|   |-- .env.example
|   |-- app/
|   |-- components/
|   |-- lib/
|   `-- package.json
|-- engine/
|   |-- main.py
|   |-- models.py
|   |-- data/
|   |-- indicators/
|   |-- risk/
|   |-- services/
|   |-- strategies/
|   `-- utils/
`-- supabase/
    `-- schema.sql
```

## Backend Modules

- `engine/data/market_data.py`: fetches and normalizes Binance public OHLCV by default, with a Bybit fallback option
- `engine/services/bybit_service.py`: Bybit V5 REST client for tickers, wallet balance, spot order placement, and order status lookup
- `engine/indicators/technical.py`: EMA20, MA50, MA200, RSI(14), ATR, ADX, breakout levels, and volume context
- `engine/strategies/trend_rsi.py`: higher-timeframe trend filter, breakout vs pullback entries, regime detection, and symbol-strength ranking
- `engine/risk/manager.py`: confidence gating, volatility filtering, higher-timeframe alignment, improved stop placement, and adaptive target planning
- `engine/services/paper_trading_service.py`: simulated entries/exits, partial profit locking, adaptive trailing, portfolio snapshotting, and journal helpers
- `engine/services/supabase_service.py`: storage layer for signals, trades, journal entries, and performance logs
- `engine/services/ai_coach_service.py`: Anthropic-backed, read-only coaching service for conservative trade guidance
- `engine/services/telegram_service.py`: alerting plus `/signal`, `/live_pending`, `/live_confirm`, `/live_cancel`, `/performance`, `/status`, `/journal`, and `/coach`
- `engine/main.py`: orchestration loop

## Dashboard Pages

- `Dashboard`: chart, active signals, portfolio metrics, and trade history
- `Signals`: recent model output with approved and rejected setups
- `Trade Journal`: process notes and journaling feed
- `Performance`: equity curve and trade ledger
- `Paper vs Live`: side-by-side comparison of grouped paper/live sibling trades
- `Telegram Alerts`: command reference and alert policy
- `Settings`: execution posture and safety controls

## Supabase Tables

Run [supabase/schema.sql](supabase/schema.sql) to create:

- `signals`
- `trades`
- `journal_entries`
- `performance_logs`

If you already ran the schema earlier, rerun it now so the `trades` table picks up the `metadata` column and the expanded live-trade status constraint.

## Backend Setup

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env`.
4. Fill in your Bybit, Supabase, and Telegram values.
5. Run the SQL schema in Supabase.
6. Start the engine:

```bash
python main.py
```

Paper-mode note:
- Qualified BUY setups open automatically once they pass the strategy and risk checks.
- Paper results now include configurable spot-fee and adverse-slippage simulation so review numbers are closer to live behavior.
- Paper trade management now supports higher-timeframe filtering, setup tagging, partial-profit lock-ins, and adaptive trailing exits.
- The AI coach remains read-only and can be queried with `/coach` when you want extra explanation.

Dual-lane note:
- Set `PAPER_TRADING_ENABLED=true` and `LIVE_TRADING_ENABLED=true` if you want the same qualifying signal to create a paper trade and a live sibling from one engine cycle.
- The backend links sibling trades with a shared `trade_group_id` inside `trades.metadata`.
- The dashboard stays paper-first by default, but `/positions` and `/performance` now let you filter between the `paper` and `live` lanes, and `/compare` shows grouped paper-versus-live drift.

Live-rollout note:
- Start with `EXECUTION_MODE=live`, `BYBIT_USE_TESTNET=false`, and `BYBIT_ALLOW_ORDER_PLACEMENT=false` for mainnet read-only validation.
- Once read-only checks are stable, enable order placement with manual confirmation still on.
- Live entries are clamped by `LIVE_TINY_SIZE_SCALE`, `LIVE_MAX_ORDER_NOTIONAL`, and `LIVE_MAX_DAILY_ENTRIES`.
- Confirm real-money entries from Telegram with `/live_confirm <id>` and cancel them with `/live_cancel <id>`.

## Frontend Setup

1. Change into `dashboard/`.
2. Install dependencies:

```bash
npm install
```

3. Copy `dashboard/.env.example` to `dashboard/.env.local`.
4. Start the dashboard:

```bash
npm run dev
```

## Backend Environment Example

```env
BYBIT_API_KEY=
BYBIT_API_SECRET=
MARKET_DATA_PROVIDER=binance
BINANCE_BASE_URL=https://data-api.binance.vision
BYBIT_USE_TESTNET=true
BYBIT_BASE_URL=https://api-testnet.bybit.com
BYBIT_ACCOUNT_TYPE=UNIFIED
BYBIT_RECV_WINDOW=5000
BYBIT_ALLOW_ORDER_PLACEMENT=false
BYBIT_REQUIRE_MANUAL_CONFIRMATION=true
LIVE_CONFIRMATION_TTL_MINUTES=15
LIVE_TINY_SIZE_SCALE=0.10
LIVE_MAX_ORDER_NOTIONAL=25
LIVE_MAX_DAILY_ENTRIES=1
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_service_key
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-20250514
AI_COACH_ENABLED=true
AI_COACH_MAX_TOKENS=700
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
TELEGRAM_ALLOWED_USER_ID=
TELEGRAM_POLLING_ENABLED=true
TRADED_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
MARKET_INTERVALS=1m,5m,15m,1h
HIGHER_TIMEFRAME_INTERVAL=1h
EXECUTION_MODE=paper
PAPER_TRADING_ENABLED=true
LIVE_TRADING_ENABLED=false
PAPER_STARTING_BALANCE=10000
PAPER_FEE_RATE=0.001
PAPER_SLIPPAGE_BPS=5
ACCOUNT_BALANCE=10000
RISK_PER_TRADE=0.01
DAILY_LOSS_LIMIT=0.03
SWING_LOOKBACK=20
RISK_REWARD_RATIO=2.0
BREAKOUT_LOOKBACK=20
VOLUME_CONFIRMATION_RATIO=1.10
SYMBOL_TOP_N=2
SYMBOL_MIN_STRENGTH_SCORE=60
STOP_ATR_MULTIPLIER=1.35
TRAIL_ATR_MULTIPLIER=1.75
PARTIAL_PROFIT_RR=1.0
SIGNAL_CONFIDENCE_THRESHOLD=70
MAX_VOLATILITY_PCT=0.03
MAX_OPEN_TRADES=3
MIN_ORDER_NOTIONAL=10
POLL_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
LOG_FILE_PATH=logs/trading_engine.log
REQUEST_TIMEOUT_SECONDS=15
MARKET_DATA_LIMIT=500
```

## Dashboard Environment Example

```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_public_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
NEXT_PUBLIC_DEFAULT_CHART_SYMBOL=BYBIT:BTCUSDT
```

## Safety Notes

- Spot only, no leverage, no futures in this version
- Real orders remain disabled by default
- The engine supports paper trading before any live rollout
- Paper entries open automatically once the rules approve them
- Paper metrics include configurable fee and slippage drag so small-account expectations stay more realistic
- Higher-timeframe, setup-type, and symbol-ranking filters are intentionally selective; fewer trades is a feature, not a bug
- Dual-lane mode lets you keep the paper ledger running while testing live fills in parallel
- Live rollout defaults stay intentionally tiny and manually confirmed
- The AI coach is read-only and should not be treated as permission to ignore the risk manager
- Telegram commands can be restricted to one user with `TELEGRAM_ALLOWED_USER_ID`
- The dashboard uses server-side Supabase access to avoid exposing sensitive write credentials client-side

## Operational Note

As of May 8, 2026, Bybit's official V5 integration guide states that IP addresses located in the United States may be restricted and can return `403 Forbidden`. The engine uses Binance public market data by default so paper-mode signal generation does not depend on Bybit market-data access from the host. Verify legal and technical access before enabling any Bybit live execution flow.

## Validation Status

- Python backend compiles successfully with `python -m compileall engine main.py`
- Frontend dependencies are installed in `dashboard/node_modules`, and `npm run build` completes successfully in this workspace
