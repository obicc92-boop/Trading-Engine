# The Doctrine

This is the rulebook I trade by. It is not advice and not a description — it is the
specification the code enforces. Every number here appears in `helm/doctrine.py` and is
checked at runtime. If a rule and the code ever disagree, the code is the bug.

Written for a **€100 account**, fully invested by the rule below. That is a choice the owner
made knowing what it costs in a crash, and section 5 says exactly what that is.

---

## 0. Why this is version 2

Version 1 traded short-term setups on hourly charts, several times a week, with a language
model approving each entry. It was tested before it touched money, and the tests ended it:

- **Six months of real Binance history (April–October 2026): −10.2%,** while simply holding
  the same four coins made +11%. Before fees its 140 trades came out about even; the €9.63
  in fees was nearly the whole loss. At this account size, trading often is a way of paying
  the exchange.
- **The model did not pick winners.** Shown 74 of those trades with the coin, the date and
  the prices hidden, the trades it approved did slightly worse than the ones it declined.
  Its conviction had no relationship to the outcome.

What did work, tested before the results were seen and then on history it had never been
looked at against, was the oldest rule there is.

---

## 1. What I trade

Spot only. I buy a coin and later sell it.

- **Four coins, against the euro: BTC, ETH, SOL and XRP.** The most liquid EUR pairs on
  Binance, and the ones the rule was tested on. The list is fixed. Adding or removing a coin
  is a doctrine change, tested first.
- **No leverage, no shorting, no stablecoins.** Unchanged from version 1, for the same
  reasons: leverage on €100 is a countdown timer, and spot cannot short.

---

## 2. The rule

**I hold a coin while its daily close is above its 200-day simple moving average. When it
closes below, I sell it and hold euros instead.**

That is the entire strategy. There is no second signal, no score, no setup, no opinion.

- **When:** once a day. The daily candle closes at 00:00 UTC; the engine reads it and acts in
  the first minutes of the new day. A close above or below the average during the day means
  nothing until the day is over.
- **Only closed days count.** The average and the decision use completed daily candles,
  never the one still forming.
- **Nothing in between.** No stop-loss inside the day, no profit target, no trailing stop.
  The exit is the trend breaking, and it is checked once a day. This is how it was tested;
  a stop placed inside daily noise would sell on ordinary swings and buy back higher.

---

## 3. How much I put in

- **When a coin closes above its average and I do not hold it, I buy a quarter of the
  account's current value in it** — or all the remaining cash, if there is less than that.
  That includes the first close after the engine starts: whatever is above its average then
  is bought at once, as it was in testing.
- **When a coin closes below its average, I sell all of it.** Sales go first, so a sale on
  the same day pays for a purchase.
- **Nothing in between.** I never add to a coin I hold, never sell part of one, and never
  rebalance. Every trade costs fees, and fees are what killed version 1. A coin already in
  the account that I have no record of buying counts as held: buying more would be adding.
- **Minimum order €10.** If a quarter of the account is less than that, I stay in cash for
  that coin. Below about €40 of equity the rule can no longer buy anything; that is the
  account telling me it is finished, not a problem to work around.

With all four coins above their averages, the account is fully invested. With none, it is
all cash. Most of the time it is somewhere between.

---

## 4. What I do not do

- **No language model in the decision.** The rule is mechanical, and the test in section 0
  is why. A model may still describe what happened; it may not decide.
- **No discretion.** Not when a coin looks cheap, not after a bad week, not because
  something is in the news.
- **No averaging down, no adding to winners, no partial exits.**
- **No trading to look busy.** Most days end with "nothing to do", and whole weeks can pass
  without a trade. That is the strategy working.

---

## 5. What to expect

Stated plainly, because the owner chose to put the whole account in.

**Tested results**, from the engine itself replaying daily Binance history
(`tools/backtest.py`), with fees and slippage, against simply buying and holding the same
coins. The second and third rows use other coins, a third or a half of the account each,
to reach further back than the four EUR pairs go:

| Period | The rule | Worst drop | Holding | Holding's worst drop |
|---|---|---|---|---|
| BTC, ETH, SOL, XRP — Dec 2021 to Oct 2026 | +158% | 43% | +17% | 74% |
| BTC, ETH, XRP — Jul 2020 to Oct 2026 | +764% | 61% | +759% | 75% |
| BTC, ETH (USDT) — Mar 2018 to Oct 2026 | +954% | 63% | +381% | 81% |
| BTC, ETH, SOL, XRP — Apr to Oct 2026 | +10% | 13% | +11% | 29% |

What that means in practice:

- **About three orders a month** across all four coins, buys and sales counted separately.
  Weeks with none.
- **Most trades lose.** In testing only one in five to one in four closed at a profit. A
  typical loser is a coin that crossed its average and fell back within a week or two, sold
  about 5% down. The result comes from a handful of trends held for months: in every period
  tested, the best five trades made all of the profit, or more. Weeks of small losses are
  the rule working, not failing.
- **The account can fall by more than half.** The worst drop in testing was 63%. With €100
  in, that is €37 left at the bottom.
- **It earns its keep in crashes, not in booms.** In a strong rising year it lags simply
  holding, because it sells on dips that turn out to be nothing and buys back higher. In a
  falling year it loses a fraction of what holding loses, because it is mostly in cash.
- **The size of the gains is not reliable.** Small changes in how the rule is implemented
  moved one period's result between +80% and +159%. What held up in every version tested is
  the shape: roughly keeping up with holding, with much smaller crashes.

Crypto has a short history and only a few full cycles. This is evidence, not proof.

---

## 6. When I stop

- **Kill switch** — sells everything at market and stops. Available from the dashboard, the
  API, Telegram, and a file on disk, because the one time it matters the network will be
  down. Unchanged.
- **Flatten** — sells everything at market and pauses, so the rule does not buy straight
  back in at the next close.
- **Pause** — stops new purchases. Sales still happen when a trend breaks, because a coin
  held against a broken trend is exactly what the rule exists to prevent.
- **Hard halt at 70% below peak equity.** No new purchases until a human restarts it. That
  is beyond anything seen in eight years of testing, so reaching it means either the market
  is doing something the rule has never met or the engine is broken — and either way, a
  human should look before it buys again. Sales still happen. Clearing it is that human
  accepting the account as it stands, so the peak is measured afresh from that day; left
  where it was, the next close would halt again at once.

The daily and weekly loss limits, the losing-streak cooldown and the per-coin cooldowns of
version 1 are gone. They were written for frequent trading; here they would block the rule
from re-entering a trend it is supposed to follow.

---

## 7. What I owe you

- **Every decision is logged** — the close, the average, the distance between them, and
  what I did about it — including the days I did nothing.
- **The exchange is the source of truth.** Holdings are reconciled against the account
  every cycle. If they disagree, the exchange is right.
- **Every number in the UI comes from the exchange**, not from the engine's beliefs about it.
- **One message a day**, whether or not anything happened, so a quiet engine is
  distinguishable from a dead one.

---

## 8. What can go wrong

- **A crash between checks.** The rule looks once a day. A coin can fall 20%, 30% or more in
  a day before the next close tells me to sell. Nothing here protects against that, by design.
- **Whipsaws.** In a sideways market a coin can cross its average back and forth, buying and
  selling at a small loss each time. The tests include this; it is the main cost of the rule.
- **The rule stops working.** It has worked across the history tested. Markets change.
- **Outages.** If the exchange or this machine is down at 00:00 UTC, the decision waits until
  it is back. The engine acts on the latest closed day when it returns, not on a stale one.

Real money can be lost, up to most of it. The owner has chosen to accept a fall of more than
half in exchange for not riding the full depth of a crypto crash. That is the trade this
rulebook makes, and the highest claim I will make for it.

---

*Doctrine v2.0. Amendments are code changes, reviewed and versioned — never runtime
overrides. The point of writing the rules down is that they stop being negotiable at exactly
the moment they become inconvenient.*
