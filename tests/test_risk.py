"""Risk rails.

These are the tests that matter most: everything else decides what to trade, this decides
how much can be lost. Each rail gets a test that fails loudly if it is ever loosened.
"""

import pytest

from helm import doctrine
from helm.models import Analysis, Candidate, Instrument, Position, now_ms
from helm.risk import AccountState, RiskEngine


def candidate(price=100.0, stop=96.0, target=112.0, score=70.0, regime="TRENDING_UP"):
    analysis = Analysis(symbol="BTCEUR", price=price, regime=regime,
                        indicators={"atr": 2.0, "swing_low": price * 0.96})
    return Candidate(symbol="BTCEUR", setup="TREND_PULLBACK", score=score, factors={},
                     price=price, stop=stop, target=target, regime=regime, analysis=analysis)


def position(symbol="ETHEUR", entry=100.0, stop=96.0, qty=1.0):
    return Position(id="p", symbol=symbol, venue="sim", qty=qty, entry_price=entry, stop=stop,
                    target=entry * 1.1, initial_stop=stop, initial_qty=qty, risk_amount=4.0,
                    opened_at=now_ms(), setup="TREND_PULLBACK", score=70, conviction=60)


def state(**kwargs):
    base = dict(equity=100.0, cash=100.0, peak_equity=100.0)
    base.update(kwargs)
    return AccountState(**base)


ENGINE = RiskEngine()


# -- sizing ------------------------------------------------------------------------------

def test_size_is_derived_from_risk_and_stop_distance():
    """The doctrine's worked example: 100 EUR, 1.5% risk, 4% stop -> 37.50 notional."""
    verdict = ENGINE.evaluate(candidate(), state(), now_ms())
    assert verdict.allowed
    assert verdict.sizing.quote_amount == pytest.approx(37.50, abs=0.01)
    assert verdict.sizing.risk_amount == pytest.approx(1.50, abs=0.01)


def test_tighter_stop_buys_a_bigger_position_for_the_same_risk():
    wide = ENGINE.evaluate(candidate(stop=94.0), state(), now_ms()).sizing
    tight = ENGINE.evaluate(candidate(stop=97.0), state(), now_ms()).sizing
    assert tight.quote_amount > wide.quote_amount
    # ...but never more risk, and never past the notional cap.
    assert tight.risk_amount <= doctrine.MAX_RISK_PER_TRADE * 100.0 + 1e-9


def test_notional_is_capped_as_a_fraction_of_equity():
    verdict = ENGINE.evaluate(candidate(stop=99.0), state(), now_ms())
    assert verdict.sizing.quote_amount <= 100.0 * doctrine.MAX_NOTIONAL_FRACTION + 1e-9


def test_risk_never_exceeds_the_doctrine_ceiling_at_any_multiplier():
    """No multiplier, however absurd, produces more risk than the doctrine permits.

    A very small multiplier may legitimately produce no trade at all: at 100 EUR a 0.25
    multiplier on a 4% stop sizes below the exchange minimum, and refusing is the correct
    answer rather than shaving the stop to make it fit.
    """
    for multiplier in (0.1, 0.5, 1.0, 2.0, 10.0, 1e6):
        verdict = ENGINE.evaluate(candidate(), state(), now_ms(), size_multiplier=multiplier)
        if verdict.sizing is None:
            continue
        assert verdict.sizing.risk_amount <= doctrine.MAX_RISK_PER_TRADE * 100.0 + 1e-9


def test_size_multiplier_can_only_shrink_a_position():
    full = ENGINE.evaluate(candidate(), state(), now_ms(), size_multiplier=1.0).sizing
    huge = ENGINE.evaluate(candidate(), state(), now_ms(), size_multiplier=99.0).sizing
    half = ENGINE.evaluate(candidate(), state(), now_ms(), size_multiplier=0.5).sizing
    assert huge.quote_amount == pytest.approx(full.quote_amount)
    assert half.quote_amount < full.quote_amount


def test_sizing_respects_available_cash():
    verdict = ENGINE.evaluate(candidate(), state(cash=20.0), now_ms())
    assert verdict.sizing.quote_amount <= 20.0


def test_rejects_when_size_would_fall_below_exchange_minimum():
    instrument = Instrument(symbol="BTCEUR", base="BTC", quote="EUR", tick_size=0.01,
                            step_size=0.00001, min_notional=50.0, min_qty=0.0)
    verdict = ENGINE.evaluate(candidate(), state(), now_ms(), instrument=instrument)
    assert not verdict.allowed
    assert any("minimum" in reason for reason in verdict.reasons)


# -- the stop is one-directional ----------------------------------------------------------

def test_a_wider_stop_override_is_ignored():
    verdict = ENGINE.evaluate(candidate(stop=96.0), state(), now_ms(), stop_override=90.0)
    assert verdict.sizing.stop == 96.0


def test_a_tighter_stop_override_is_accepted():
    verdict = ENGINE.evaluate(candidate(stop=96.0), state(), now_ms(), stop_override=98.0)
    assert verdict.sizing.stop == 98.0


def test_a_stop_above_entry_is_rejected_outright():
    verdict = ENGINE.evaluate(candidate(stop=101.0), state(), now_ms())
    assert not verdict.allowed


# -- gates -------------------------------------------------------------------------------

def test_blocks_at_max_open_positions():
    positions = [position(symbol=f"X{i}EUR") for i in range(doctrine.MAX_OPEN_POSITIONS)]
    assert ENGINE.gate_entries(state(positions=positions), now_ms())


def test_blocks_at_daily_entry_cap():
    blocked = ENGINE.gate_entries(state(entries_today=doctrine.MAX_ENTRIES_PER_DAY), now_ms())
    assert any("daily cap" in reason for reason in blocked)


def test_blocks_after_the_daily_loss_limit():
    loss = -(doctrine.DAILY_LOSS_HALT * 100.0) - 0.01
    blocked = ENGINE.gate_entries(state(realized_today=loss), now_ms())
    assert any("today" in reason for reason in blocked)


def test_blocks_after_the_weekly_loss_limit():
    loss = -(doctrine.WEEKLY_LOSS_HALT * 100.0) - 0.01
    blocked = ENGINE.gate_entries(state(realized_week=loss), now_ms())
    assert any("this week" in reason for reason in blocked)


def test_blocks_at_the_drawdown_hard_stop():
    equity = 100.0 * (1 - doctrine.MAX_DRAWDOWN_HALT) - 1
    blocked = ENGINE.gate_entries(state(equity=equity, peak_equity=100.0), now_ms())
    assert any("drawdown" in reason for reason in blocked)


def test_blocks_during_the_consecutive_loss_cooldown():
    blocked = ENGINE.gate_entries(
        state(consecutive_losses=doctrine.CONSECUTIVE_LOSS_LIMIT,
              loss_cooldown_until=now_ms() + 3_600_000),
        now_ms(),
    )
    assert any("row" in reason for reason in blocked)


def test_portfolio_heat_ceiling_blocks_further_risk():
    # Two positions risking 3.00 each is 6.00 of heat against a 4.50 ceiling, and stays
    # under the position-count cap so this test isolates the heat rail.
    hot = [position(symbol=f"X{i}EUR", entry=100.0, stop=97.0, qty=1.0) for i in range(2)]
    blocked = ENGINE.gate_entries(state(positions=hot), now_ms())
    assert any("heat" in reason for reason in blocked)


def test_open_risk_falls_to_zero_once_the_stop_is_above_entry():
    breakeven = position(entry=100.0, stop=101.0, qty=1.0)
    assert state(positions=[breakeven]).open_risk == 0.0


# -- per-candidate refusals ---------------------------------------------------------------

def test_never_adds_to_an_existing_position():
    verdict = ENGINE.evaluate(candidate(), state(positions=[position(symbol="BTCEUR")]), now_ms())
    assert not verdict.allowed
    assert any("already holding" in reason for reason in verdict.reasons)


def test_respects_the_symbol_cooldown_after_a_stop_out():
    cooldowns = {"BTCEUR": now_ms() + 3_600_000}
    verdict = ENGINE.evaluate(candidate(), state(cooldowns=cooldowns), now_ms())
    assert not verdict.allowed
    assert any("cooldown" in reason for reason in verdict.reasons)


def test_rejects_a_low_score():
    verdict = ENGINE.evaluate(candidate(score=doctrine.MIN_SETUP_SCORE - 1), state(), now_ms())
    assert not verdict.allowed


def test_rejects_an_untradeable_regime():
    verdict = ENGINE.evaluate(candidate(regime="TRENDING_DOWN"), state(), now_ms())
    assert not verdict.allowed


def test_rejects_insufficient_reward_for_the_risk():
    poor = candidate(price=100.0, stop=96.0, target=102.0)   # 0.5R of room
    verdict = ENGINE.evaluate(poor, state(), now_ms())
    assert not verdict.allowed
    assert any("reward:risk" in reason for reason in verdict.reasons)


def test_every_refusal_explains_itself():
    verdict = ENGINE.evaluate(candidate(score=10), state(entries_today=99), now_ms())
    assert not verdict.allowed
    assert all(isinstance(reason, str) and reason.strip() for reason in verdict.reasons)
