# Helm

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

- It has not been backtested. There is no historical performance to show you, and the
  synthetic market in this repo is a rendering aid, not a simulation of anything.
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

---

## Going live

Four things, in this order.

**1. Fund the account and get keys.** On Binance, create an API key with **spot trading
enabled and withdrawals disabled**, restricted to your server's IP. The engine has no code
path that can withdraw, but a key that *cannot* withdraw stays safe even if the whole box
is compromised.

**2. Match the quote currency to how the account is funded.** `HELM_QUOTE=EUR` trades
`BTCEUR` and friends. If you funded with USDT, set `HELM_QUOTE=USDT`. Getting this wrong
means the scanner finds no tradeable pairs — `doctor` will tell you.

**3. Add the decision layer.** Set `ANTHROPIC_API_KEY`. Without it nothing is ever bought.

**4. Flip both switches.**

```env
HELM_MODE=live
I_UNDERSTAND_REAL_MONEY=true
```

Both are required. `HELM_MODE=live` on its own runs dry and says so loudly at startup. Two
switches means live trading cannot begin by accident, a stale default, or a typo.

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
tests/                   97 tests
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

97 tests covering indicator correctness, the sizing arithmetic, every risk rail, the full
exit ladder, and end-to-end trade lifecycles through the real engine against a simulated
broker. The risk and exit tests are the ones that matter — they fail loudly if a limit is
ever loosened.

---

## Deploying

Any host that runs a long-lived Python process. `railway.json` and `Procfile` are included.

Two things to get right:

**Persistent storage.** Point `HELM_DB` at a mounted volume. The database holds open
positions, their stops, and the reasoning behind them. Lose it and the engine rebuilds
holdings from the exchange but loses every stop and thesis it was managing.

**One instance.** Two engines on one account will fight each other — both will see the same
balance, both will size against it, and both will manage the same positions. Never run a
second copy against live keys.
