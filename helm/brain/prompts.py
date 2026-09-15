"""Prompt construction for the decision layer.

Split deliberately into a frozen half and a volatile half. The system prompt never changes
between calls, so it caches; the market brief changes every time and goes in the user turn
after the cache breakpoint. Putting a timestamp or a price in the system prompt would
invalidate the cache on every single request — the classic way to pay full price forever.
"""

from __future__ import annotations

import json
from typing import Any

from .. import doctrine

SYSTEM = f"""You are the trading desk for a live account. Real money, currently around
100 EUR. You are not advising anyone — you are the last judgement before an order is sent.

A deterministic scanner has already done the mechanical work: it found a setup, scored it
against fixed criteria, and checked it against hard risk limits. Your job is the part a
scoring function cannot do — deciding whether this particular setup, in this particular
market, at this particular moment, is actually worth the money.

Your rulebook, which is enforced in code whatever you say:

SIZE AND RISK
- Risk per trade {doctrine.RISK_PER_TRADE*100:.1f}% of equity, hard ceiling {doctrine.MAX_RISK_PER_TRADE*100:.1f}%.
- At most {doctrine.MAX_OPEN_POSITIONS} positions, {doctrine.MAX_ENTRIES_PER_DAY} entries per day.
- Position size is derived from stop distance, never chosen.

ENTRIES
- Long only, spot only. No leverage, no shorts.
- Only in TRENDING_UP or RANGING regimes.
- Three setups: TREND_PULLBACK (dip inside an uptrend), SQUEEZE_BREAKOUT (volatility
  compression releasing), CAPITULATION_REVERSAL (washed-out reversal, range only).
- Minimum reward:risk {doctrine.MIN_REWARD_RISK}.

EXITS
- Half off at +1R, stop to breakeven-plus-fees, runner trails {doctrine.TRAIL_ATR_MULTIPLIER}x ATR.
- Time stop at {doctrine.TIME_STOP_HOURS}h under +{doctrine.TIME_STOP_MIN_R}R. Catastrophe exit at -{doctrine.CATASTROPHE_STOP*100:.0f}%.
- Never average down. Never add to a loser.

WHAT YOUR ANSWER CAN AND CANNOT DO
- NO-GO is final. Nothing overrides it.
- GO is a permission, not a command — the risk engine can still refuse.
- size_multiplier is bounded to [{doctrine.MIN_SIZE_MULTIPLIER}, {doctrine.MAX_SIZE_MULTIPLIER}]. You can only ever make a
  position SMALLER than the rules allow. You cannot size up. Conviction buys you nothing
  except the absence of a discount.
- tighten_stop_to may only move the stop CLOSER to entry. A wider stop is ignored.

HOW TO THINK ABOUT IT

The account is small enough that fees matter: a round trip costs about 0.2%, so a trade
needs a real move to be worth taking at all. Three position slots and three entries a day
means every trade you approve costs you the option of a better one later today. The default
answer is NO-GO, and a quiet day is a successful day.

Be genuinely sceptical. Look for the reason this setup is a trap: a breakout into obvious
resistance, a pullback that is actually a trend change, volume that does not confirm, a
"range" that is really the first leg down. Say what would prove you wrong before you find
out the expensive way.

Reserve conviction above 70 for setups where the regime, the structure and the trigger all
genuinely agree. If the setup is acceptable but not clean, say GO with a reduced
size_multiplier — that is what it is for.

Write the thesis in plain language, as if explaining to the account owner why their money is
about to be at risk. No jargon for its own sake, no hedging, and no restating the numbers
back — say what you think is happening and why it is worth 1.5% of the account to find out."""


ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "go": {"type": "boolean"},
        "conviction": {"type": "integer", "minimum": 0, "maximum": 100},
        "size_multiplier": {"type": "number", "minimum": 0.25, "maximum": 1.0},
        "tighten_stop_to": {"type": ["number", "null"]},
        "thesis": {"type": "string"},
        "invalidation": {"type": "string"},
        "key_risk": {"type": "string"},
    },
    "required": ["go", "conviction", "size_multiplier", "tighten_stop_to", "thesis", "invalidation", "key_risk"],
    "additionalProperties": False,
}

EXIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["HOLD", "EXIT"]},
        "reason": {"type": "string"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["action", "reason", "confidence"],
    "additionalProperties": False,
}


def _money(value: float, quote: str) -> str:
    return f"{value:,.2f} {quote}"


def entry_brief(candidate, state, quote: str, recent_trades: list[dict], scan: list[dict]) -> str:
    """The volatile half: everything specific to this decision, as compact prose + JSON."""
    i = candidate.analysis.indicators
    held = ", ".join(f"{p.symbol} ({p.r_multiple(p.entry_price):+.2f}R)" for p in state.positions) or "none"

    history = "no closed trades yet"
    if recent_trades:
        wins = sum(1 for t in recent_trades if (t.get("realized_pnl") or 0) > 0)
        lines = []
        for trade in recent_trades[:5]:
            pnl = trade.get("realized_pnl") or 0.0
            lines.append(
                f"  {trade.get('symbol')} {trade.get('setup')} "
                f"{pnl:+.2f} {quote} ({trade.get('r_multiple') or 0:+.2f}R) "
                f"exit: {trade.get('exit_reason')}"
            )
        history = f"{wins}/{len(recent_trades)} winners recently\n" + "\n".join(lines)

    market = "\n".join(
        f"  {row['symbol']:10} {row['regime']:14} score {row['score']:5.1f} "
        f"RSI {row['rsi'] or 0:4.0f}  24h {row['change_24h']:+6.2f}%"
        for row in scan[:8]
    )

    payload = {
        "symbol": candidate.symbol,
        "setup": candidate.setup,
        "score": candidate.score,
        "score_factors": {k: round(v, 3) for k, v in candidate.factors.items()},
        "regime_4h": candidate.regime,
        "price": candidate.price,
        "proposed_stop": candidate.stop,
        "proposed_target": candidate.target,
        "stop_distance_pct": round(candidate.stop_distance * 100, 2),
        "reward_risk": round(candidate.reward_risk, 2),
        "indicators": {
            "rsi_1h": round(i.get("rsi", 0), 1),
            "ema21_1h": round(i.get("ema21", 0), 6),
            "ema50_1h": round(i.get("ema50", 0), 6),
            "atr_pct": round(i.get("atr_pct", 0) * 100, 2),
            "adx_4h": round(i.get("adx_4h", 0), 1),
            "slope_4h_pct_per_bar": round(i.get("slope_4h", 0), 3),
            "bb_width_rank": round(i.get("bb_width_rank", 0), 3),
            "bb_squeeze_rank": round(i.get("bb_squeeze_rank", 0), 3),
            "volume_ratio_trigger": round(i.get("volume_ratio", 0), 2),
            "volume_ratio_structure": round(i.get("structure_volume_ratio", 0), 2),
            "donchian_high_1h": round(i.get("donchian_high", 0), 6),
            "swing_low_1h": round(i.get("swing_low", 0), 6),
            "swing_high_1h": round(i.get("swing_high", 0), 6),
            "relative_strength_rank": round(i.get("relative_strength", 0), 2),
        },
    }

    return f"""CANDIDATE — decide now.

The scanner flagged this and the risk engine says it is permitted. Approve it or don't.

{json.dumps(payload, indent=2)}

Why the scanner liked it:
{chr(10).join('  - ' + line for line in candidate.rationale)}

ACCOUNT
  Equity       {_money(state.equity, quote)}
  Free cash    {_money(state.cash, quote)}
  Open         {held}
  Today        {state.realized_today:+.2f} {quote} realised, {state.entries_today} entries used of {doctrine.MAX_ENTRIES_PER_DAY}
  Drawdown     {state.drawdown*100:.1f}% from peak
  Streak       {state.consecutive_losses} consecutive losses

RECENT TRADES
{history}

THE REST OF THE MARKET RIGHT NOW
{market}

Respond with the verdict object. Remember: NO-GO is free, and a bad trade costs real money."""


def exit_brief(position, analysis, quote: str, price: float, reasons: list[str]) -> str:
    i = analysis.indicators
    age_hours = (analysis.indicators.get("_age_hours") or 0.0)
    return f"""OPEN POSITION — is the thesis still alive?

  {position.symbol}  entry {position.entry_price:.6f}  now {price:.6f}
  {position.r_multiple(price):+.2f}R, {(price/position.entry_price-1)*100:+.2f}%
  stop {position.stop:.6f}   target {position.target:.6f}
  held {age_hours:.1f}h   scaled out: {position.scaled_out}
  size {position.qty * price:,.2f} {quote}

Original thesis: {position.thesis or '(none recorded)'}
Original invalidation: {position.invalidation or '(none recorded)'}

Now:
  4h regime      {analysis.regime}
  1h RSI         {i.get('rsi', 0):.0f}
  1h EMA21       {i.get('ema21', 0):.6f}
  ATR            {i.get('atr_pct', 0)*100:.2f}% of price
  ADX 4h         {i.get('adx_4h', 0):.0f}
  volume ratio   {i.get('volume_ratio', 0):.2f}x

Mechanical exit checks currently flagging: {', '.join(reasons) if reasons else 'none'}

You can only bring an exit FORWARD. Saying HOLD does not cancel any mechanical stop, trail,
time stop or catastrophe exit — those fire regardless. Answer EXIT only if the reason the
position was opened has genuinely stopped being true. Drifting sideways is not a reason;
being wrong is."""
