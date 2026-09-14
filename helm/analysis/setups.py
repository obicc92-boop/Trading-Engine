"""Setup detection — the three things I am willing to buy.

Each detector is deterministic and returns either `None` or a `Candidate` carrying a score
built from named factors. The factors matter as much as the score: a number I can't
decompose is a number I can't audit after a losing month.

Nothing here decides anything. Detection produces candidates; `risk.py` decides what is
permitted and the decision layer decides what is wise.
"""

from __future__ import annotations

from .. import doctrine
from ..models import Analysis, Candidate, Candles, Regime
from . import indicators as ind


def _score(factors: dict[str, float]) -> float:
    """Weighted sum of 0-1 factors against the doctrine's weights."""
    total = 0.0
    for name, weight in doctrine.SCORE_WEIGHTS.items():
        total += weight * max(0.0, min(1.0, factors.get(name, 0.0)))
    return round(total, 2)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def analyse(
    symbol: str,
    trigger: Candles,
    structure: Candles,
    regime: Regime,
    regime_metrics: dict[str, float],
    relative_strength: float = 0.5,
) -> Analysis:
    """Flatten everything the detectors need into one inspectable snapshot."""
    close = structure.close
    price = float(trigger.close[-1])

    ema21 = ind.ema(close, 21)
    ema50 = ind.ema(close, 50)
    rsi_series = ind.rsi(close, 14)
    atr_series = ind.atr(structure.high, structure.low, close, 14)
    upper, middle, lower, bandwidth = ind.bollinger(close, 20, 2.0)

    # Shifted by one bar on purpose: a channel that includes the current bar's own high can
    # essentially never be broken by the current bar's close, which would make the breakout
    # setup silently unreachable. The channel must describe where price *had* been.
    donchian_high = ind.shift(ind.rolling_max(structure.high, 20), 1)
    donchian_low = ind.shift(ind.rolling_min(structure.low, 20), 1)

    volume_ma = ind.sma(trigger.volume, 20)
    trigger_ema21 = ind.ema(trigger.close, 21)
    structure_volume_ma = ind.sma(structure.volume, 20)

    indicators = {
        "price": price,
        "ema21": ind.last(ema21, price),
        "ema50": ind.last(ema50, price),
        "rsi": ind.last(rsi_series, 50.0),
        "atr": ind.last(atr_series, 0.0),
        "atr_pct": ind.last(atr_series, 0.0) / price if price else 0.0,
        "bb_upper": ind.last(upper, price),
        "bb_lower": ind.last(lower, price),
        "bb_width": ind.last(bandwidth, 0.0),
        "bb_width_rank": ind.percentile_rank(bandwidth, 100),
        # A squeeze is identified by where volatility *was* just before the break, not
        # where it is during it — by the breakout bar, bandwidth is already expanding.
        "bb_squeeze_rank": ind.min_percentile_rank(bandwidth, 100, recent=6),
        "bb_expanding": float(ind.expanding(bandwidth, 3)),
        "donchian_high": ind.last(donchian_high, price),
        "donchian_low": ind.last(donchian_low, price),
        "volume": float(trigger.volume[-1]),
        "volume_ma": ind.last(volume_ma, float(trigger.volume[-1])),
        "volume_ratio": float(trigger.volume[-1]) / max(ind.last(volume_ma, 1.0), 1e-9),
        "structure_volume_ratio": float(structure.volume[-1])
        / max(ind.last(structure_volume_ma, 1.0), 1e-9),
        "trigger_ema21": ind.last(trigger_ema21, price),
        "swing_low": ind.swing_low(structure.low, 20),
        "swing_high": ind.swing_high(structure.high, 20),
        "structure_slope": ind.slope_pct(close, 20),
        "relative_strength": relative_strength,
        "adx_4h": regime_metrics.get("adx", 0.0),
        "atr_4h": regime_metrics.get("atr", 0.0),
        "slope_4h": regime_metrics.get("slope_pct", 0.0),
        "rsi_divergence": float(ind.bullish_divergence(close, rsi_series, 40)),
    }
    return Analysis(symbol=symbol, price=price, regime=regime, indicators=indicators)


def detect(
    symbol: str,
    trigger: Candles,
    structure: Candles,
    analysis: Analysis,
) -> Candidate | None:
    """Run every detector and return the highest-scoring candidate, if any qualifies."""
    regime = analysis.regime
    if regime not in doctrine.TRADEABLE_REGIMES:
        return None

    found = [
        _trend_pullback(symbol, trigger, structure, analysis),
        _squeeze_breakout(symbol, trigger, structure, analysis),
        _capitulation_reversal(symbol, trigger, structure, analysis),
    ]
    candidates = [c for c in found if c is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.score)


def _stop_and_target(analysis: Analysis, structure_low: float) -> tuple[float, float]:
    """Stop from the doctrine's two-reason rule; target from the nearest real resistance.

    The stop takes the *wider* of "structure says I'm wrong" and "this is just noise", then
    clamps into the 2.5%-6% band that keeps position sizing sane at this account size.
    """
    price = analysis.price
    atr_value = analysis.indicators.get("atr", 0.0) or price * 0.01

    structural = structure_low - doctrine.STRUCTURE_STOP_ATR_BUFFER * atr_value
    volatility = price - doctrine.STOP_ATR_MULTIPLIER * atr_value
    stop = min(structural, volatility)

    distance = (price - stop) / price if price else 0.0
    distance = max(doctrine.MIN_STOP_DISTANCE, min(doctrine.MAX_STOP_DISTANCE, distance))
    stop = price * (1 - distance)

    risk = price - stop
    # Prefer a real level to a made-up multiple: the recent swing high is where sellers
    # actually were. Fall back to the doctrine's minimum R:R when there's no level above.
    swing_high = analysis.indicators.get("swing_high", 0.0)
    resistance_target = swing_high * 0.998 if swing_high > price * 1.01 else 0.0
    minimum_target = price + risk * doctrine.MIN_REWARD_RISK
    target = max(resistance_target, minimum_target)
    return stop, target


def _trend_pullback(symbol, trigger, structure, analysis) -> Candidate | None:
    """Setup A — buying a dip inside an established uptrend."""
    if analysis.regime != "TRENDING_UP":
        return None
    i = analysis.indicators
    price, ema21, ema50 = i["price"], i["ema21"], i["ema50"]
    if ema21 <= 0 or ema50 <= 0:
        return None

    # The pullback must reach the value band but not break it decisively.
    band_top = ema21 * 1.015
    band_bottom = ema50 * 0.985
    if not (band_bottom <= price <= band_top):
        return None
    # The trigger bar has to actually reclaim: without this we're buying a falling knife
    # and calling it a pullback.
    if price < i["trigger_ema21"]:
        return None
    if i["rsi"] < 38 or i["rsi"] > 68:
        return None

    depth = (ema21 - price) / ema21 if ema21 else 0.0
    factors = {
        "trend_alignment": _clamp01(i["slope_4h"] / 0.15),
        "momentum_quality": _clamp01((i["rsi"] - 38) / 22),
        "volume_confirmation": _clamp01((i["volume_ratio"] - 0.8) / 0.7),
        # A shallow pullback is a strong trend; a deep one is a failing one.
        "volatility_fit": _clamp01(1.0 - abs(depth - 0.01) / 0.05),
        "structure_clarity": _clamp01(i["structure_slope"] / 0.20),
        "relative_strength": i["relative_strength"],
    }
    score = _score(factors)
    if score < doctrine.MIN_SETUP_SCORE:
        return None

    stop, target = _stop_and_target(analysis, i["swing_low"])
    return Candidate(
        symbol=symbol, setup="TREND_PULLBACK", score=score, factors=factors, price=price,
        stop=stop, target=target, regime=analysis.regime, analysis=analysis,
        rationale=[
            f"4h regime is TRENDING_UP with ADX {i['adx_4h']:.0f}",
            f"price pulled into the 1h EMA21/EMA50 band ({depth*100:.1f}% below EMA21)",
            f"trigger bar reclaimed its EMA21 on {i['volume_ratio']:.2f}x average volume",
            f"RSI {i['rsi']:.0f} — cooled off without breaking down",
        ],
    )


def _squeeze_breakout(symbol, trigger, structure, analysis) -> Candidate | None:
    """Setup B — quiet market, then a break. Buying the release of stored energy."""
    i = analysis.indicators
    price = i["price"]
    if i["bb_squeeze_rank"] > 0.25:        # volatility was never compressed
        return None
    if i["bb_expanding"] < 1.0:            # compression has not released yet
        return None
    if i["donchian_high"] <= 0 or price <= i["donchian_high"]:
        return None
    # Volume is judged on the same timeframe as the break itself. Measuring a 1h breakout
    # against 15m volume compares two different things and passes far too easily.
    if i["structure_volume_ratio"] < 1.5:
        return None

    breakout_size = (price / i["donchian_high"] - 1) if i["donchian_high"] else 0.0
    factors = {
        "trend_alignment": _clamp01(0.5 + i["slope_4h"] / 0.2),
        "momentum_quality": _clamp01((i["rsi"] - 50) / 20),
        "volume_confirmation": _clamp01((i["structure_volume_ratio"] - 1.5) / 1.5),
        "volatility_fit": _clamp01(1.0 - i["bb_squeeze_rank"] / 0.25),
        # A modest break has room left; a 5% gap above the channel is chasing.
        "structure_clarity": _clamp01(1.0 - breakout_size / 0.03),
        "relative_strength": i["relative_strength"],
    }
    score = _score(factors)
    if score < doctrine.MIN_SETUP_SCORE:
        return None

    stop, target = _stop_and_target(analysis, max(i["donchian_high"] * 0.995, i["swing_low"]))
    return Candidate(
        symbol=symbol, setup="SQUEEZE_BREAKOUT", score=score, factors=factors, price=price,
        stop=stop, target=target, regime=analysis.regime, analysis=analysis,
        rationale=[
            f"volatility was compressed to the {i['bb_squeeze_rank']*100:.0f}th percentile before this move",
            f"1h close broke the prior 20-bar high by {breakout_size*100:.2f}%",
            f"volume {i['structure_volume_ratio']:.2f}x average confirms the break",
            f"regime {analysis.regime}, ADX {i['adx_4h']:.0f}",
        ],
    )


def _capitulation_reversal(symbol, trigger, structure, analysis) -> Candidate | None:
    """Setup C — the knife-catcher, allowed only in a range and only with divergence."""
    if analysis.regime != "RANGING":
        return None
    i = analysis.indicators
    price = i["price"]
    if i["rsi"] > 27:
        return None
    if price > i["bb_lower"]:
        return None
    if i["rsi_divergence"] < 1.0:      # no divergence, no trade
        return None

    stretch = (i["bb_lower"] / price - 1) if price else 0.0
    factors = {
        "trend_alignment": 0.5,        # by construction there is no trend to align with
        "momentum_quality": _clamp01((27 - i["rsi"]) / 12),
        "volume_confirmation": _clamp01((i["volume_ratio"] - 1.0) / 1.0),
        "volatility_fit": _clamp01(stretch / 0.02),
        "structure_clarity": 0.6,
        "relative_strength": i["relative_strength"],
    }
    score = _score(factors)
    if score < doctrine.MIN_SETUP_SCORE:
        return None

    stop, target = _stop_and_target(analysis, min(i["swing_low"], price * 0.99))
    return Candidate(
        symbol=symbol, setup="CAPITULATION_REVERSAL", score=score, factors=factors, price=price,
        stop=stop, target=target, regime=analysis.regime, analysis=analysis,
        rationale=[
            f"RSI {i['rsi']:.0f} — washed out",
            f"price {stretch*100:.2f}% below the lower Bollinger band",
            "bullish RSI divergence against price",
            "range regime — no downtrend to fight",
        ],
    )
