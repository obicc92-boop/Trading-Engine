# Helm

[![CI](https://github.com/obicc92-boop/Trading-Engine/actions/workflows/ci.yml/badge.svg)](https://github.com/obicc92-boop/Trading-Engine/actions/workflows/ci.yml)

An autonomous trading desk for a small real-money account. It scans the market, analyses
what it finds, decides whether anything is worth buying, sizes the position from risk rather
than conviction, and manages the exit. It reports what it is doing in plain language while
it does it.

Built for **€100**. That constraint drives every design decision here, and most of what a
larger account would do is wrong at this size.

```
scan  →  analyse  →  decide  →  size  →  execute  →  manage  →  exit
```

**[The Doctrine](DOCTRINE.md) is the specification.** It states the rules — the universe,
the risk limits, the entries, the exits, the circuit breakers — in prose, and every number
in it appears in `helm/doctrine.py` and is enforced at runtime. Read that first. This file
only explains how to run the thing.

---

## Read this before you run it live

This software places real orders with real money and **you can lose all of it**. Not as a
disclaimer — as the actual expected range of outcomes for any trading system, this one
included.

What is genuinely true about it:

- Risk per trade is capped at 2% of equity and enforced in three independent places.
- Position size is derived from stop distance. Nothing can choose a size directly.
- The decision layer can only ever make a position *smaller* or a stop *tighter*.
- Every loss limit halts new entries automatically. A 20% drawdown halts everything.
- Every decision, including every refusal, is recorded with its reasoning.

What is **not** true about it:

- It has no record of making money. A backtest of the rules alone — no decision layer —
  over April to October 2026 on BTC, ETH, SOL and XRP lost 10.2% while simply holding the
  same four coins made 11%. Before fees its 140 trades came out about even (−€0.53); the
  €9.63 paid in fees is nearly all of the loss. See [Backtesting](#backtesting).
  The synthetic market in this repo is a rendering aid, not a simulation of anything.
- Stops are not guarantees. In a fast market a stop becomes a market order and fills where
  it fills.
- If the process dies or the exchange goes down, open positions are unmanaged until it
  comes back. It reconciles against the exchange on recovery, but it cannot manage what it
  cannot reach.
- The setups are historically sound patterns, not predictions. Regimes change faster than
  any rulebook adapts, and a long run of losses inside the rules is a normal outcome.

**Run it in `dry` mode for at least a week before going live.** Dry mode runs the identical
code path against real market data with simulated fills — same sizing, same rails, same
exits. The only substituted component is the one that moves money. If you would not have
been happy with that week, you have learned something for free.

---

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # then edit it
python main.py
```

The dashboard is at `http://localhost:8000`.

With no configuration at all it starts in dry mode, pulls real Binance market data, and
buys nothing — because with no `ANTHROPIC_API_KEY` there is no decision layer, and a
candidate with no verdict is declined. That is the intended default: a fresh clone watches
the market and does nothing.

### Check the environment before trusting it

```bash
python main.py doctor
```

Run this **on the machine that will actually trade**. It verifies venue reachability, that
your credentials work, that the balance exists, that candles are flowing and that the
decision layer responds. It never places an order. Fix everything it calls a problem before
going near live mode.

### What to look for during the dry run

A dry run is only useful if you know what you are grading. After a few days, open the
dashboard and check these, in order — the first three are about the machine, the last three
about the strategy.

**Is it healthy?**
- The **Live** tab's narration should show a steady cycle. Long silences mean it is stuck.
- No red "last cycle failed" banner.
- Open positions should show a stop that moves *up* over time and never down.

**Is it behaving?**
- Entries per day never exceeds 3, open positions never exceeds 3.
- On the **Trades** tab, no single loss should be much worse than ~1R. A −2R trade means a
  stop did not hold and is worth investigating.
- Holds measured in hours to days, not minutes. Minutes means something is cutting trades
  early.

**Is it any good?** This is the part that needs patience and the part the dry run mostly
cannot answer yet.
- Ten trades tell you almost nothing. Thirty start to mean something. The doctrine is
  selective by design, so a few days may produce only a handful.
- Read the **Decisions** tab, especially the refusals. The theses are the real output: if
  the reasoning is sound but the trades lose, the edge is missing. If the reasoning is
  sloppy, the problem is upstream of the market.
- Expectancy in R matters more than win rate. A 40% win rate with +0.4R expectancy is a
  good system; a 70% win rate with −0.1R expectancy is a slow bleed.

If the dry week would have annoyed you, do not fund it. That is the entire point of running
one.

### Soak testing

```bash
python tools/soak.py --days 30
```

Runs the equivalent of a month of continuous operation in a couple of minutes against
generated prices, then reports engine health and rule compliance. It exists to catch
crashes, leaks, state loss across restarts and rail violations — **not** to say anything
about performance. The prices are fake and its P&L is meaningless; it is a robustness test,
not a backtest.

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
python tools/backtest.py --days 180
```

Replays real Binance history through the real engine on a simulated clock — the same
scanner, rails, sizing, exit ladder, fees and slippage — and reports trades, win rate,
expectancy in R, drawdown, and buy-and-hold for comparison. Where the soak asks whether the
engine survives, this asks whether the rules make money.

Two things are substituted. The market serves recorded candles, and only bars that had
closed by the simulated time, so nothing from the future can reach a decision. The decision
layer approves everything the rails allow, at full size: the result is the rules with no
judgement on top, and the live system takes a subset of those trades.

The first run needs a network connection and caches what it downloads in `data/backtest/`;
later runs over the same window are offline. One window is one sample. Tune a rule on one
period and check it on another, or the backtest will only tell you how well the rules
memorised the past.

---

## Going live

Five things, in this order.

**1. Fund the account and get keys.** On Binance, create an API key with **spot trading
enabled and withdrawals disabled**, restricted to your server's IP. The engine has no code
path that can withdraw, but a key that *cannot* withdraw stays safe even if the whole box
is compromised.

**2. Match the quote currency to how the account is funded.** `HELM_QUOTE=EUR` trades
`BTCEUR` and friends. If you funded with USDT, set `HELM_QUOTE=USDT`. Getting this wrong
means the scanner finds no tradeable pairs — `doctor` will tell you.

**3. Add the decision layer.** Set `ANTHROPIC_API_KEY`. Without it nothing is ever bought.

**4. Guard the control API.** Set `HELM_UI_TOKEN` to a long random string:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The guard fails open. With no token set, `POST /api/control/flatten` sells everything and
`/api/control/pause` stops the engine, for anyone who can reach the port — no credential
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

- **Flatten all** in the dashboard — sells everything at market, immediately.
- `POST /api/control/flatten` — the same thing without a browser.
- `touch data/KILL` — the engine flattens and stops on its next cycle. No API, no UI, no
  network. This is the one that works when nothing else does.

**Pause** is different from all three: it stops new entries while continuing to manage open
positions. That is almost always what you want, because an abandoned position is worse than
a managed one.

---

## The dashboard

Four tabs.

**Live** — equity and return, the risk meters (portfolio heat, drawdown, entries used
today), a running plain-language narration of what the engine is doing, open positions drawn
to scale between their stop and target, the equity curve, and every market it scanned
including the ones it passed over.

**Trades** — closed trades only, with win rate, expectancy in R, and profit factor. Open
positions are deliberately excluded: counting unrealised gains as performance is the most
common way a trading record flatters itself.

**Decisions** — every setup that reached the desk, taken or not, with the thesis, what would
invalidate it, and the factor scores behind it. Most entries here are refusals. That is the
more informative half of the record and the reason the log exists.

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
  config.py              operational settings from the environment
  engine.py              the cycle: reconcile → manage → scan → decide → execute
  risk.py                position sizing and the hard limits. Has veto power over everything.
  positions.py           the exit ladder. Pure functions, heavily tested.
  store.py               SQLite persistence
  bus.py                 engine thread → browser event bridge
  api.py                 REST + WebSocket + static UI
  doctor.py              preflight checks
  analysis/
    indicators.py        EMA, RSI, ATR, ADX, Bollinger, Donchian — pure numpy
    regime.py            4h regime classification (the veto)
    setups.py            the three setups and their scoring
    scanner.py           universe selection and ranking
  brain/
    claude.py            the decision layer; fails closed
    prompts.py           the system prompt and the market brief
  venues/
    binance.py           spot crypto: market data and signed execution
    alpaca.py            US equities, fractional
    simulated.py         dry-run broker + synthetic market
ui/                      dashboard: one HTML file, one CSS file, one JS file, no build step
tests/                   164 tests
```

Two structural choices worth knowing about.

**Rules are code, not configuration.** Nothing in `.env` can change a risk limit. Amending
the doctrine means editing `helm/doctrine.py`, which means a diff and a commit. The point of
writing rules down is that they stop being negotiable at exactly the moment they become
inconvenient.

**The exchange is the source of truth.** Every cycle reconciles open positions against the
broker's actual balances. If they disagree, the engine is wrong and updates itself. A
position sold by hand, a partial fill, a dust rounding — all of it gets corrected rather
than traded against.

---

## Tests

```bash
python -m pytest tests/ -q
```

164 tests covering indicator correctness, the sizing arithmetic, every risk rail, the full
exit ladder, and end-to-end trade lifecycles through the real engine against a simulated
broker. The risk and exit tests are the ones that matter — they fail loudly if a limit is
ever loosened.

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

**What it sends.** Entries, exits, halts, circuit breakers, order rejections, and one daily
summary. It does *not* forward routine narration — the engine produces a line or two every
cycle, and pushing those would train you to ignore the channel inside a day. Only
entry/win/loss/warning/error levels reach your phone.

The daily summary arrives even when nothing happened, and that is deliberate: at this
account size most days are correctly uneventful, and a system that only speaks when it
trades is indistinguishable from one that has quietly died.

**What you can send back.**

| Command | |
|---|---|
| `/status` | equity, risk, entries used, what it is doing |
| `/positions` | open trades with entry, stop, target, R |
| `/today` | trades closed today |
| `/pause` · `/resume` | stop new entries, keep managing what is open |
| `/flatten` | sell everything at market — asks for `/confirm` |
| `/kill` | flatten and stop the engine — asks for `/confirm` |

Two design choices worth stating.

There is **no `/confirm_entry`**. The old build had Telegram approve each live trade; this
one is autonomous by design, and a human gate on every entry would just be the old system
with extra steps. The bot can stop this system — it cannot aim it.

`/flatten` and `/kill` **require a second message** within two minutes. They spend real
money, and a fat-thumbed tap on a phone should not be able to close three positions.

Commands are locked to one Telegram user id. A leaked token is not great, but its worst
case is an unwanted flatten — lost opportunity, not lost capital, since nothing in the bot
can open a position, raise a limit, or withdraw. Set `TELEGRAM_COMMANDS=false` for alerts
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

Railway's container filesystem is wiped on every deploy. This database holds the stop,
target and thesis for every open position — the exchange holds the coins, but nothing else
knows what they are for. Without a volume, a redeploy while a trade is open leaves real
money sitting on the exchange with **no stop and nothing managing it**, which is the most
dangerous state this system has.

The engine detects the aftermath and alerts loudly — on startup it compares the exchange's
balances against its own records and reports anything it has no plan for — but detection is
a backstop, not a fix. `python main.py doctor` warns if `HELM_DB` looks container-local.

**One instance.** Two engines on one account will fight: both see the same balance, both
size against it, both manage the same positions. Never scale this past one replica.

**Set `HELM_UI_TOKEN`.** Railway gives the service a public `*.up.railway.app` domain, and
the control API fails open: with no token, anyone who has the URL can `POST
/api/control/flatten` and sell the account. Live mode refuses to start without one, but set
it for the dry run too — the dry run is what you will leave running and forget about.

`/health` stays unauthenticated on purpose, so Railway's healthcheck keeps working.

### Migrating from the previous deployment

The engine is a different program, so most of the old configuration no longer exists.

**Delete all of these** — they are gone, not renamed:

```
BYBIT_*              SUPABASE_*           PAPER_*              LIVE_*
EXECUTION_MODE       AI_COACH_*           MARKET_DATA_PROVIDER
ACCOUNT_BALANCE      RISK_PER_TRADE       DAILY_LOSS_LIMIT     MAX_OPEN_TRADES
RISK_REWARD_RATIO    STOP_ATR_MULTIPLIER  TRAIL_ATR_MULTIPLIER PARTIAL_PROFIT_RR
SIGNAL_CONFIDENCE_THRESHOLD              MAX_VOLATILITY_PCT    MIN_ORDER_NOTIONAL
SWING_LOOKBACK       BREAKOUT_LOOKBACK    VOLUME_CONFIRMATION_RATIO
SYMBOL_TOP_N         SYMBOL_MIN_STRENGTH_SCORE                 POLL_INTERVAL_SECONDS
```

That list is the migration in miniature. Every risk and strategy parameter it contains now
lives in `helm/doctrine.py` as a constant, unreachable from the environment. It was
possible to widen a stop or raise the risk per trade by editing a variable in a hosting
dashboard; now it takes a commit. A rule you can change at 2am from your phone, while
losing, is not a rule.

**Keep and rename:**

| Old | New | Note |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `TELEGRAM_BOT_TOKEN` | same bot, new commands |
| `TELEGRAM_CHAT_ID` | `TELEGRAM_CHAT_ID` | unchanged |
| `TELEGRAM_ALLOWED_USER_ID` | `TELEGRAM_ALLOWED_USER_ID` | now defaults to the chat id |
| `ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` | unchanged |
| `ANTHROPIC_MODEL` | `HELM_MODEL` | |
| `TRADED_SYMBOLS` | `HELM_SYMBOLS` | optional; empty means rank by liquidity |
| `LOG_LEVEL` | `LOG_LEVEL` | unchanged |

**Add:** `HELM_MODE`, `HELM_VENUE`, `HELM_QUOTE`, `HELM_STARTING_EQUITY`, `HELM_DB`,
`HELM_UI_TOKEN`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`, and — only when you actually mean
it — `I_UNDERSTAND_REAL_MONEY`.

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
