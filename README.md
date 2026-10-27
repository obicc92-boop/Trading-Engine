# Helm

[![CI](https://github.com/obicc92-boop/Trading-Engine/actions/workflows/ci.yml/badge.svg)](https://github.com/obicc92-boop/Trading-Engine/actions/workflows/ci.yml)

A trading engine for a small real-money account. It holds BTC, ETH, SOL and XRP while each
one's daily close is above its 200-day average, and holds euros instead when it is not. It
checks once a day, just after the daily candle closes at 00:00 UTC, and reports what it is
doing in plain language while it does it.

Built for **€100**. That constraint drove the design: at this size fees decide the result,
so the rule trades rarely.

```
daily close  →  above or below the average  →  sell what broke  →  buy what turned on
```

**[The Doctrine](DOCTRINE.md) is the specification.** It states the rule — the coins, the
average, the sizing, the halt — in prose, says what to expect including how bad it can get,
and every number in it appears in `helm/doctrine.py`. Read that first. This file only
explains how to run the thing.

Version 1 traded short-term setups on hourly charts with a language model approving each
entry. Its own backtest ended it: −10.2% over six months while holding the same coins made
+11%, and the model's picks were no better than chance. The doctrine's section 0 has the
detail; the code is in the git history.

---

## Read this before you run it live

This software places real orders with real money and **you can lose all of it**. Not as a
disclaimer — as the actual expected range of outcomes for any trading system, this one
included.

What is genuinely true about it:

- The rule is mechanical and tested: the engine itself, replaying daily Binance history,
  reproduces the research behind it (see [Backtesting](#backtesting)).
- Purchases are a quarter of the account each, never added to. Nothing can choose a size.
- Sales are never blocked — not by a pause, not by the halt.
- A 70% drop from peak equity halts purchases until a human clears it.
- Every daily decision, including the days nothing happened, is recorded with the close and
  the average it was compared with.

What is **not** true about it:

- It does not make money often. In testing only one trade in four or five was profitable;
  the result came from a few trends held for months. Weeks of small losses are normal.
- It does not protect you from a crash inside a day. It looks once a day, so a coin can fall
  20–30% before the next close says sell. The worst drop in eight years of testing was 63%.
- Past results are not a forecast. Crypto has a short history and only a few full cycles.
  The synthetic market in this repo is a rendering aid, not a simulation of anything.
- If the process dies or the exchange goes down, the decision waits until it is back. It
  reconciles against the exchange on recovery and acts on the latest close.

**Run it in `dry` mode first.** Dry mode runs the identical code path against real market
data with simulated fills — same rule, same sizing, same books. The only substituted
component is the one that moves money. With trades this rare, a dry run proves the engine
behaves, not that the rule makes money; the backtest is the evidence for that.

---

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # then edit it
python main.py
```

The dashboard is at `http://localhost:8000`.

With no configuration at all it starts in dry mode on real Binance market data. On its first
cycle it reads the latest daily close and buys, with simulated money, every coin that closed
above its average — the rule does not wait for a coin to cross; it holds whatever is above.

### Check the environment before trusting it

```bash
python main.py doctor
```

Run this **on the machine that will actually trade**. It verifies venue reachability, that
your credentials work, that the balance exists, and that each coin has the 200 closed days
the rule needs — printing where each one stands against its average. It never places an
order. Fix everything it calls a problem before going near live mode.

### What to look for during the dry run

The rule acts once a day, so a dry run is mostly about the machine. After a few days:

**Is it healthy?**
- The **Live** tab shows a cycle every minute, and "Next decision" counts down to 00:00 UTC.
- A "Read the … close" line in the narration every day, and one Telegram message a day.
- No red "last cycle failed" banner.

**Is it following the rule?**
- On the **Decisions** tab, every coin has a row for every day. Each "bought" is a coin that
  closed above its average and was not held; each "sold" one that closed below.
- Nothing bought or sold except in the first minutes after midnight UTC (or on first start).
- Each purchase is about a quarter of the account at the time.

**Is it any good?** A week cannot say. The rule trades about three times a month and most
trades lose; the result comes from the occasional trend held for months. The backtest is the
evidence, and the doctrine's section 5 says what to expect.

### Soak testing

```bash
python tools/soak.py --days 30
```

Runs the equivalent of a month of continuous operation in a few seconds against generated
prices, restarting the engine from disk along the way, then reports engine health and rule
compliance: one decision per coin per day, trades only in the direction the close said,
purchases only just after a close and never above a quarter of the account, and the books
matching the cash to the cent. It is a robustness test, **not** a backtest — the prices are
fake and its P&L is meaningless.

Synthetic prices are a pure function of time, so an unpinned run walks a different price
path every time. That is useful — each run explores a market shape the last one did not —
but it means a failure has to be replayable. Every run prints its `--start-at`, and a
failing one prints the full command to reproduce it:

```bash
python tools/soak.py --days 30 --start-at 1757000000000
```

CI pins that value so a red build always means a real regression rather than an unlucky
path; a nightly job runs unpinned to keep exploring new ones.

### Backtesting

```bash
python tools/backtest.py                              # the four coins, all their history
python tools/backtest.py --start 2026-04-30           # from a date
python tools/backtest.py --symbols BTCUSDT,ETHUSDT    # an experiment: further back, other coins
```

Replays real daily Binance history through the real engine on a simulated clock — the same
rule, sizing, sales-before-purchases, halt, books, fees and slippage — one decision a minute
after each midnight, filled at that day's opening price. It reports the result, the worst
drop, how much of the time it was invested, trades and fees, and buy-and-hold alongside,
year by year. The market only ever shows days that had closed, so nothing from the future
can reach a decision.

On the doctrine's four coins from December 2021, when SOL had enough history, to October
2026 it made **+158% with a 43% worst drop**, against +17% and 74% for holding — the same as
the research script the doctrine was written from, which is the point of running it.
`--set NAME=VALUE` and `--symbols` run experiments, and the report labels them as such.

The first run needs a network connection and caches what it downloads in `data/backtest/`;
later runs are offline. One window is one sample.

---

## Going live

Five things, in this order.

**1. Get keys.** On Binance, create an API key with **spot trading
enabled and withdrawals disabled**, restricted to your server's IP. The engine has no code
path that can withdraw, but a key that *cannot* withdraw stays safe even if the whole box
is compromised.

**2. Fund it in euros.** The doctrine trades the four coins against EUR. `doctor` checks
that the venue prices all four.

**3. Use a fresh database.** A version-1 database is refused at startup — its positions
carried stops and targets this version does not have. Point `HELM_DB` at a new file.

**4. Guard the control API.** Set `HELM_UI_TOKEN` to a long random string:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The guard fails open. With no token set, `POST /api/control/flatten` sells everything and
`/api/control/pause` stops purchases, for anyone who can reach the port — no credential
required. That is a deliberate convenience when it is bound to a laptop, and an open
liquidate button the moment the service has a domain, which on Railway it does by default.

**5. Flip both switches.**

```env
HELM_MODE=live
I_UNDERSTAND_REAL_MONEY=true
```

All three of these are required for live trading — the two switches and the token. Any one
missing and the engine runs dry and says at startup exactly which. Live trading cannot begin
by accident, a stale default, or a typo, and it cannot begin with the controls wide open.

### Stopping it

Three ways, in descending order of how much has to be working:

- **Flatten all** in the dashboard — sells everything at market, immediately, and pauses
  purchases so the rule does not buy straight back in at the next close.
- `POST /api/control/flatten` — the same thing without a browser.
- `touch data/KILL` — the engine flattens and stops on its next cycle. No API, no UI, no
  network. This is the one that works when nothing else does.

**Pause** is different from all three: it stops purchases while sales carry on. A coin held
against a broken trend is exactly what the rule exists to prevent.

---

## The dashboard

Four tabs.

**Live** — equity and return; how much is invested, the drawdown against the 70% halt and
the time to the next decision; the four coins, each against its 200-day average at the last
close and now; the coins held, drawn to scale against the average they must stay above; a
running plain-language narration; and the equity curve.

**Trades** — closed trades only, with how many won, the average return, fees and days held.
Open positions are deliberately excluded: counting unrealised gains as performance is the
most common way a trading record flatters itself, and with a trend rule most of the gain
sits in a few positions that are open for months.

**Decisions** — every coin at every daily close: the close, the average, the distance
between them and what the rule did about it, including the days it did nothing.

**Doctrine** — the rulebook, rendered from `DOCTRINE.md`.

Set `HELM_UI_TOKEN` before exposing any of this to the internet. The control endpoints can
sell your positions.

---

## How it is put together

```
main.py                  entry point; also `python main.py doctor`
DOCTRINE.md              the rules, in prose — the specification
helm/
  doctrine.py            the rules, as constants. Single source of truth.
  strategy.py            the rule: closes in, verdicts and purchase sizes out. Pure functions.
  engine.py              the cycle: reconcile → record → (once a day) sell → buy → report
  risk.py                account state and the reasons purchases may be blocked
  positions.py           what each sale books: fees, lot steps, dust. Pure functions.
  config.py              operational settings from the environment
  store.py               SQLite persistence
  bus.py                 engine thread → browser event bridge
  api.py                 REST + WebSocket + static UI
  doctor.py              preflight checks
  notify/telegram.py     alerts, the daily message, and remote control
  venues/
    binance.py           spot crypto: market data and signed execution
    simulated.py         dry-run broker + synthetic market
ui/                      dashboard: one HTML file, one CSS file, one JS file, no build step
tools/                   soak.py (robustness) and backtest.py (the rule on real history)
tests/                   117 tests
```

Two structural choices worth knowing about.

**Rules are code, not configuration.** Nothing in `.env` can change the coins, the average,
the sizing or the halt. Amending the doctrine means editing `helm/doctrine.py`, which means a
diff and a commit. The point of writing rules down is that they stop being negotiable at
exactly the moment they become inconvenient.

**The exchange is the source of truth.** Every cycle reconciles open positions against the
broker's actual balances. If they disagree, the engine is wrong and updates itself. A
position sold by hand, a partial fill, a dust rounding — all of it gets corrected rather
than traded against.

---

## Tests

```bash
python -m pytest tests/ -q
```

117 tests covering the rule's arithmetic, the engine's daily decision against closes
set on purpose — what it buys, what it sells, when it waits, what pause and the halt block
and what they never block — the books through lot steps and dust, restarts, the Telegram
command surface, and the backtest's guarantee that no future price reaches a decision.

These run in CI on every push and pull request, alongside a 30-day soak. Both must be green
before anything merges — see [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## Notifications (Telegram)

The dashboard is for when you are looking at it. Telegram is for when you are not — which,
for a system that trades around the clock, is most of the time.

Set two variables and it starts talking:

```env
TELEGRAM_BOT_TOKEN=...     # from @BotFather
TELEGRAM_CHAT_ID=...       # your numeric id, from @userinfobot
```

**What it sends.** Every purchase and sale, halts, failed orders, and one message a day just
after the close: each coin against its average, what was done, and the account. It does
*not* forward routine narration; only purchase/sale/warning/error levels reach your phone.

The daily message arrives even when nothing happened, and that is deliberate: with this rule
most days are correctly uneventful, and a system that only speaks when it trades is
indistinguishable from one that has quietly died.

**What you can send back.** The menu Telegram shows is registered by the engine at start,
so it always matches what it answers.

| Command | |
|---|---|
| `/status` | equity, invested, drawdown, the four coins against their averages |
| `/positions` | coins held, with their results so far and the average they must stay above |
| `/trades` | the last closed trades |
| `/pause` · `/resume` | stop purchases; sales carry on |
| `/flatten` | sell everything at market and pause — asks for `/confirm` |
| `/kill` | flatten and stop the engine — asks for `/confirm` |

Two design choices worth stating.

There is **no command to buy**. The bot can stop this system — it cannot aim it.

`/flatten` and `/kill` **require a second message** within two minutes. They sell real
coins, and a fat-thumbed tap on a phone should not be able to empty the account.

Commands are locked to one Telegram user id. A leaked token is not great, but its worst
case is an unwanted flatten — lost opportunity, not lost capital, since nothing in the bot
can buy, raise a limit, or withdraw. Set `TELEGRAM_COMMANDS=false` for alerts
only.

---

## Deploying

Any host that runs a long-lived Python process. `railway.json` and `Procfile` are included.

### Railway

One service, `python main.py`, which runs the engine *and* serves the dashboard on `$PORT`.

**Mount a volume. This is not optional.**

Create a volume in the Railway service settings, mount it at `/data`, and set:

```env
HELM_DB=/data/helm.db
```

Railway's container filesystem is wiped on every deploy. This database is the only record of
which coins the rule bought — the exchange holds the coins, but nothing else knows they are
the rule's to sell. Without a volume, a redeploy while holding leaves real money on the
exchange with **nothing managing it**: the rule will not sell what it has no record of, and
will not buy more of it either.

The engine detects the aftermath and alerts loudly — every cycle it compares the exchange's
balances against its own records and reports anything it has no record of — but detection
is a backstop, not a fix. `python main.py doctor` warns if `HELM_DB` looks container-local.

**One instance.** Two engines on one account will fight: both see the same balance, both
size against it, both sell the same coins. Never scale this past one replica.

**Set `HELM_UI_TOKEN`.** Railway gives the service a public `*.up.railway.app` domain, and
the control API fails open: with no token, anyone who has the URL can `POST
/api/control/flatten` and sell the account. Live mode refuses to start without one, but set
it for the dry run too — the dry run is what you will leave running and forget about.

`/health` stays unauthenticated on purpose, so Railway's healthcheck keeps working.

### Upgrading from version 1

Give version 2 a **new database file** (`HELM_DB`); it refuses a version-1 one. These
settings no longer exist and can be deleted: `ANTHROPIC_API_KEY`, `HELM_MODEL`,
`HELM_MODEL_EFFORT`, `HELM_SYMBOLS`, `HELM_QUOTE` and `HELM_SCAN_EVERY`. The coins and the
quote currency are doctrine now, and there is no model to call.

### Migrating from the original deployment

The pre-Helm build ran on Bybit with Supabase and Vercel. **Delete all of these** — they are
gone, not renamed:

```
BYBIT_*              SUPABASE_*           PAPER_*              LIVE_*
EXECUTION_MODE       AI_COACH_*           MARKET_DATA_PROVIDER
ACCOUNT_BALANCE      RISK_PER_TRADE       DAILY_LOSS_LIMIT     MAX_OPEN_TRADES
RISK_REWARD_RATIO    STOP_ATR_MULTIPLIER  TRAIL_ATR_MULTIPLIER PARTIAL_PROFIT_RR
SIGNAL_CONFIDENCE_THRESHOLD              MAX_VOLATILITY_PCT    MIN_ORDER_NOTIONAL
SWING_LOOKBACK       BREAKOUT_LOOKBACK    VOLUME_CONFIRMATION_RATIO
SYMBOL_TOP_N         SYMBOL_MIN_STRENGTH_SCORE                 POLL_INTERVAL_SECONDS
TRADED_SYMBOLS       ANTHROPIC_*
```

Every strategy parameter that list contains is now a constant in `helm/doctrine.py`,
unreachable from the environment. A rule you can change at 2am from your phone, while
losing, is not a rule. Keep `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`TELEGRAM_ALLOWED_USER_ID` (now defaulting to the chat id) and `LOG_LEVEL`. Add `HELM_MODE`,
`HELM_VENUE`, `HELM_STARTING_EQUITY`, `HELM_DB`, `HELM_UI_TOKEN`, `BINANCE_API_KEY`,
`BINANCE_API_SECRET`, and — only when you actually mean it — `I_UNDERSTAND_REAL_MONEY`.

The old Supabase project can be deleted once you no longer want the history. Nothing reads
it; the engine owns its own SQLite database now.

### Vercel is no longer needed

The old dashboard was a separate Next.js app that read from Supabase, so it had to be hosted
somewhere. This one is three static files served by the same process that trades, reading
state over REST and a WebSocket from memory. There is no second tier left to deploy.

You *can* still put the UI on Vercel — serve `ui/` as a static site pointed at the Railway
API — but it buys nothing and costs a cross-origin WebSocket, a CORS policy, and a second
place for the two halves to drift out of sync. Railway already gives the service a domain.
Set `HELM_UI_TOKEN` and use that.

Delete the Vercel project when you are ready. Nothing in this repo references it.
