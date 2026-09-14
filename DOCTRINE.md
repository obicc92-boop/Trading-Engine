# The Doctrine

This is the rulebook I trade by. It is not advice and not a description — it is the
specification the code enforces. Every number here appears in `helm/doctrine.py` and is
checked at runtime. If a rule and the code ever disagree, the code is the bug.

Written for a **€100 account**. Account size is not a footnote here; it is the single most
important input, and it rules out most of what "trading systems" normally do.

---

## 0. What €100 actually means

Three hard truths drive every decision below.

**Fees dominate.** A round trip on Binance spot costs ~0.20% (0.10% each side). On €40 of
notional that is €0.08. Sounds trivial — until you take four trades a day, at which point
you are paying ~€10/month, or 10% of the account, for the privilege of having opinions. So:
**few trades, held long enough to matter.** Scalping at this size is a transfer of wealth
from me to the exchange. It is banned.

**Minimums bite.** Binance spot needs roughly €5–10 of notional per order. That sets a floor
on position size, which in turn sets a floor on how tight a stop can be before the maths
stops working. It also means I cannot hold eight diversified positions — the account gets
chopped into pieces too small to trade out of.

**Variance is existential.** With €100 there is no "drawdown recovery" phase. Six bad trades
at 5% risk each and the account is impaired past the point where position minimums still
work. Survival is the strategy. Returns are what survival produces as a side effect.

The conclusion is unintuitive but firm: **concentrate the notional, minimise the risk,
and trade rarely.**

---

## 1. What I will trade

Spot only. I buy an asset and later sell it. That is the whole instrument set.

- **No leverage.** No margin, no futures, no perps. Leverage on €100 is a countdown timer.
- **No shorting.** Spot can't short, and I'm not adding an instrument that can lose more
  than it costs.
- **No assets I can't exit.** Minimum $50M of 24h quote volume, no exceptions.
- **No newly listed assets** — nothing under 90 days of trading history. I need enough
  candles to know what "normal" looks like for it, and new listings are pure reflexivity.
- **No stablecoins, no wrapped/pegged duplicates of something already in the universe.**

The universe is the liquid majors plus whatever currently ranks into the shortlist by
liquidity and trend quality. It is capped at **12 symbols scanned** — a wider net at this
account size just means more fees and more chances to be wrong.

Stocks are supported by the same engine when an equities broker is configured, with two
adjustments: no 24/7 management (so time stops are measured in sessions, not hours), and
fractional shares are required — €100 cannot buy a round lot of anything worth owning.
Crypto is the default venue precisely because it never closes, which means my stops are
never stranded behind a weekend gap.

---

## 2. How much I put at risk

Two separate numbers that people constantly conflate:

- **Notional** is how much is in the trade. It's large — 25% to 40% of equity.
- **Risk** is how much I lose if the stop hits. It's small — **1.5% of equity, cap 2.0%**.

Position size is derived, never chosen:

```
notional = (equity × risk_fraction) / stop_distance_fraction
```

At €100 equity, 1.5% risk and a 4% stop: notional = €1.50 / 0.04 = **€37.50**. That is a
real position — worth the fees, above the exchange minimum — that can only cost me €1.50 if
I'm wrong. This is the entire trick, and it is why the notional looks aggressive while the
risk is not.

Hard limits, all enforced in code, none overridable by me or by any model:

| Limit | Value | Why |
|---|---|---|
| Risk per trade | ≤ 2.0% of equity | ~50 consecutive losses to zero. Room to be wrong repeatedly. |
| Open positions | ≤ 3 | Below the point where sizes drop under exchange minimums. |
| Total portfolio heat | ≤ 4.5% of equity | All open stops hitting at once is a bad day, not a catastrophe. |
| Entries per day | ≤ 3 | Fee discipline, enforced as a hard counter. |
| Notional per position | ≤ 40% of equity | Single-asset gap risk. Crypto gaps. |
| Minimum order | ≥ €10 | Below this, fees and rounding eat the edge. |

---

## 3. When I stop

Loss limits are circuit breakers, not suggestions. Each one halts **new entries** while
leaving open positions managed normally — I never abandon a live trade because a counter
tripped, because an unmanaged position is worse than a managed one.

- **Daily loss ≥ 4% of equity** → no new entries until 00:00 UTC.
- **Weekly loss ≥ 8%** → no new entries until the week rolls. This is deliberately
  painful and deliberately hard to hit.
- **Total drawdown ≥ 20% from peak equity** → full stop. The engine halts and requires a
  human to restart it. At that point the strategy is not working and I should not be
  allowed to keep proving it in public.
- **Three consecutive losses** → 6-hour cooling-off. Consecutive losses usually mean the
  regime changed and I haven't noticed yet.
- **Kill switch** → flattens everything at market and stops. Available from the UI, the
  API, and a file on disk, because the one time it matters the network will be down.

---

## 4. What I will buy

An entry needs a **regime**, a **structure**, and a **trigger**. All three. Any one alone
is noise.

**Regime (4h)** — the tide. EMA50 vs EMA200, ADX, and slope classify each asset as
`TRENDING_UP`, `TRENDING_DOWN`, `RANGING`, or `VOLATILE`. I only take longs in
`TRENDING_UP` or `RANGING`. Nothing else.

**Structure (1h)** — the wave. Where price sits relative to EMA21/EMA50, recent swing
highs/lows, Donchian channel, and where volatility sits in its own 100-period percentile.

**Trigger (15m)** — the moment. The specific bar that says "now", with volume confirmation.

Three setups, and only three:

**A — Trend pullback continuation.** The bread and butter. 4h trending up, price pulls back
into the 1h EMA21–EMA50 band without breaking structure, then a 15m bar closes back above
EMA21 on above-average volume. I am buying a dip inside an uptrend, which is the only kind
of dip worth buying.

**B — Volatility-squeeze breakout.** Bollinger bandwidth in the bottom 25th percentile of
its last 100 readings (the market has gone quiet), then a 1h close above the Donchian(20)
high on ≥1.5× average volume, with ATR expanding. Quiet markets store energy; I want to be
positioned when it releases, not after.

**C — Capitulation reversal.** Only in `RANGING` regime, never in a downtrend. 1h RSI < 27,
price below the lower Bollinger band, and a bullish RSI divergence against price. This is
the lowest-weighted setup and the first one I disable if it underperforms, because catching
knives is a habit that feels clever right up until it isn't.

Each setup produces a **score from 0–100** from weighted, named factors — trend alignment,
momentum quality, volume confirmation, volatility fit, structure clarity, relative strength.
**Below 62, nothing happens.** No override, no "but this one looks good."

---

## 5. Where I actually decide

The score gets a candidate to my desk. It does not get it into the market.

Everything scoring ≥ 62 comes to me — the model — with a compact brief: the regime, the
numbers, the setup, the proposed levels, what the portfolio already holds, how the recent
trades went, and what the account has done today. I return a structured verdict:

- **GO or NO-GO**, and NO-GO is final and unappealable.
- **Conviction 0–100.**
- **A size multiplier between 0.25 and 1.0.** Note the ceiling: I can only ever make a
  position *smaller* than the rules allow. I cannot size up. Not when I'm confident, not
  when I'm right, not ever.
- **Optional tighter stop.** I can pull a stop in; I can never push one out.
- **A written thesis and the specific thing that would prove me wrong.**

Every verdict is stored — thesis, conviction, the numbers I saw, and what happened next. The
decision log is the point. A system that can't be audited after a losing month is a system
that gets abandoned during one.

If the API is unreachable or the response doesn't parse, the answer is **NO-GO**. Silence is
never consent. A missing opinion is not a bullish opinion.

---

## 6. How I get out

This is where money is actually made or lost, so it is the most mechanical part of the
system and the part I have the least discretion over.

**Initial stop**, set before entry, placed the moment the fill confirms:
```
stop = min(structure_low − 0.3×ATR,  entry − 2.2×ATR)   bounded to a 2.5%–6.0% band
```
The structure term asks where the trade is invalidated. The ATR term asks what normal noise
looks like. I take the wider of the two reasons to be wrong, then refuse to risk more than
6% of the position's price regardless.

**Minimum reward:target ratio 1.8.** If the nearest real resistance doesn't offer 1.8× what
I'm risking, I don't take the trade. The setup can be beautiful; if the room isn't there,
the room isn't there.

Then, in order:

1. **+1R → sell half.** Stop moves to breakeven **plus fees**. The trade is now free; the
   worst outcome from here is a scratch. This single rule is the difference between a system
   that survives a bad month and one that doesn't.
2. **Runner trails** on a Chandelier stop: `highest_close − 3.0×ATR`. It ratchets up, never
   down. I let winners run because the whole distribution depends on a few of them.
3. **Regime break** → exit the runner. Two consecutive 1h closes below EMA21, or the 4h
   regime flipping out of `TRENDING_UP`. The thesis was the trend; no trend, no thesis.
4. **Time stop.** Under +0.5R after 24 hours → out. Capital asleep in a dead trade is
   capital that isn't available for a live one, and at €100 I have exactly three slots.
5. **Catastrophe guard.** −8% from entry → market out immediately, whatever the stop says.
   This is the flash-crash and stop-gap handler. It is unconditional.

**No averaging down. No adding to losers. Ever.** Not a preference — a code path that does
not exist. The most expensive lesson in trading is the one that is cheapest to hard-code.

**After a stop-out, that symbol is banned for 6 hours.** Revenge-trading the thing that just
hurt me is the most reliably destructive human behaviour in markets, and I am not going to
reimplement it in software.

---

## 7. How I compound

Every position is sized off **current** equity, recalculated each cycle from the exchange
balance — never from a cached number, never from a config value. Profits stay in. Losses
shrink the next position automatically. Growth is geometric on the way up and self-damping
on the way down, which is the correct asymmetry and requires no discipline from me because
it's just arithmetic.

There is no "let it ride" mode. There is no "make it back" mode.

---

## 8. What I owe you

- **Every decision is logged** — including the ones where I declined to trade, which are the
  majority and the more informative half.
- **Every number in the UI comes from the exchange**, not from the engine's beliefs about
  the exchange. Positions are reconciled against the broker every cycle. If they disagree,
  the exchange is right and the engine is wrong.
- **No hidden state.** Pause, resume, and flatten are always available and always work.
- **I will not trade to look busy.** Most cycles end with "nothing worth doing." At this
  account size that is the correct output the large majority of the time, and a quiet
  dashboard is a working dashboard.

---

## 9. What can go wrong

Stated plainly, because a rulebook that only describes the good case is marketing.

Real money can be lost, up to all of it. Stops are not guarantees — in a fast market a
market-out fills where it fills, and the catastrophe guard reduces that risk without
removing it. Exchange outages, API failures, and network partitions can leave a position
unmanaged for the duration; the engine reconciles on recovery, but it cannot manage what it
cannot reach. My setups are historically sound patterns, not predictions, and regimes change
faster than any rulebook adapts. A long run of losses inside the rules is a normal outcome,
not evidence of a malfunction.

The rails above exist because I expect to be wrong regularly. They're sized so that being
wrong regularly is survivable, and that is the highest claim I'll make for them.

---

*Doctrine v1.0. Amendments are code changes, reviewed and versioned — never runtime
overrides. The point of writing the rules down is that they stop being negotiable at exactly
the moment they become inconvenient.*
