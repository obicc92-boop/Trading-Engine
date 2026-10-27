"""The exit ladder.

`plan` is pure, so every rung can be tested in isolation and in combination. Exits decide
the outcome, so this is where the coverage goes.
"""

import pytest

from helm import doctrine
from helm.models import Analysis, Position
from helm.positions import book_swept, plan, realise, update_highest_close, write_off

HOUR = 3_600_000


def make(**kwargs) -> Position:
    base = dict(id="p", symbol="BTCEUR", venue="sim", qty=1.0, entry_price=100.0, stop=96.0,
                target=112.0, initial_stop=96.0, initial_qty=1.0, risk_amount=4.0,
                opened_at=0, setup="TREND_PULLBACK", score=70, conviction=60)
    base.update(kwargs)
    return Position(**base)


def analysis(regime="TRENDING_UP", atr=2.0) -> Analysis:
    return Analysis(symbol="BTCEUR", price=100.0, regime=regime,
                    indicators={"atr": atr, "ema21": 99.0})


# -- urgent rungs ------------------------------------------------------------------------

def test_catastrophe_guard_fires_below_the_hard_floor():
    price = 100.0 * (1 - doctrine.CATASTROPHE_STOP) - 0.5
    result = plan(make(), price, analysis(), ts=HOUR)
    assert result.exit.kind == "CATASTROPHE"
    assert result.exit.fraction == 1.0


def test_catastrophe_guard_ignores_the_minimum_hold():
    """Being 8% underwater one minute in is not a whipsaw, it is a mistake."""
    result = plan(make(), 91.0, analysis(), ts=60_000)
    assert result.exit.kind == "CATASTROPHE"


def test_stop_fires_at_the_stop():
    assert plan(make(), 95.99, analysis(), ts=HOUR).exit.kind == "STOP"


def test_no_exit_above_the_stop():
    assert plan(make(), 98.0, analysis(), ts=HOUR).exit is None


def test_target_closes_the_remainder():
    assert plan(make(), 112.5, analysis(), ts=HOUR).exit.kind == "TARGET"


# -- scale out and breakeven -------------------------------------------------------------

def test_scales_out_half_at_one_r():
    result = plan(make(), 104.0, analysis(), ts=HOUR)
    assert result.exit.kind == "SCALE_OUT"
    assert result.exit.fraction == doctrine.SCALE_OUT_FRACTION


def test_scale_out_moves_the_stop_above_entry():
    result = plan(make(), 104.0, analysis(), ts=HOUR)
    assert result.stop_update.stop > 100.0        # breakeven *plus fees*, not breakeven


def test_does_not_scale_out_twice():
    result = plan(make(scaled_out=True, qty=0.5, stop=100.25), 106.0, analysis(), ts=HOUR)
    assert result.exit is None or result.exit.kind != "SCALE_OUT"


def test_r_multiple_is_measured_against_the_original_stop():
    """Once the stop trails, measuring R against it would report +1R forever."""
    position = make(scaled_out=True, stop=108.0)
    assert position.r_multiple(104.0) == pytest.approx(1.0)


# -- trailing ----------------------------------------------------------------------------

def test_trail_ratchets_up_with_the_high():
    # Target set far away so this exercises the trail rung rather than the target rung,
    # which sits above it in the ladder and would fire first.
    position = make(scaled_out=True, qty=0.5, stop=100.25, breakeven_set=True,
                    highest_close=120.0, target=300.0)
    result = plan(position, 118.0, analysis(), ts=HOUR)
    assert result.stop_update.stop == pytest.approx(120.0 - doctrine.TRAIL_ATR_MULTIPLIER * 2.0)


def test_trail_never_moves_down():
    position = make(scaled_out=True, qty=0.5, stop=114.0, breakeven_set=True,
                    highest_close=120.0, target=300.0)
    result = plan(position, 118.0, analysis(), ts=HOUR)
    assert result.stop_update is None       # 114 is already tighter than the chandelier


def test_trail_never_lands_above_the_current_price():
    position = make(scaled_out=True, qty=0.5, stop=100.25, breakeven_set=True,
                    highest_close=200.0, target=300.0)
    result = plan(position, 101.0, analysis(), ts=HOUR)
    assert result.stop_update is None


def test_trail_floors_at_breakeven():
    position = make(scaled_out=True, qty=0.5, stop=100.25, breakeven_set=True,
                    highest_close=101.0, target=300.0)
    result = plan(position, 100.8, analysis(), ts=HOUR)
    if result.stop_update:
        assert result.stop_update.stop >= 100.0


def test_highest_close_only_rises():
    position = make(highest_close=130.0)
    update_highest_close(position, None, 105.0)
    assert position.highest_close == 130.0


# -- discretionary rungs -----------------------------------------------------------------

def test_regime_break_exits_the_runner_after_enough_closes():
    position = make(regime_break_closes=doctrine.REGIME_BREAK_CLOSES,
                    scaled_out=True, qty=0.5, target=300.0)
    assert plan(position, 101.0, analysis(), ts=HOUR).exit.kind == "REGIME_BREAK"


def test_regime_break_does_not_cut_a_position_that_has_not_scaled_out_yet():
    """Before +1R the initial stop is the only thing allowed to close the trade.

    A pullback entry buys into the EMA21 band, so this trigger sits right on top of the
    entry price. Applying it to a fresh position cuts every trade on noise before it has
    any chance to work.
    """
    position = make(regime_break_closes=doctrine.REGIME_BREAK_CLOSES + 5)
    assert plan(position, 101.0, analysis(), ts=HOUR).exit is None


def test_regime_flip_to_downtrend_exits_any_position_runner_or_not():
    """A confirmed 4h downtrend is decisive enough to override the runner-only scoping."""
    for position in (make(), make(scaled_out=True, qty=0.5, target=300.0)):
        result = plan(position, 101.0, analysis(regime="TRENDING_DOWN"), ts=HOUR)
        assert result.exit.kind == "REGIME_BREAK"


def test_time_stop_frees_a_slot_from_dead_money():
    result = plan(make(), 100.5, analysis(), ts=(doctrine.TIME_STOP_HOURS + 1) * HOUR)
    assert result.exit.kind == "TIME_STOP"


def test_time_stop_spares_a_position_that_is_working():
    price = 100.0 + 4.0 * (doctrine.TIME_STOP_MIN_R + 0.3)
    result = plan(make(), price, analysis(), ts=(doctrine.TIME_STOP_HOURS + 1) * HOUR)
    assert result.exit is None or result.exit.kind != "TIME_STOP"


def test_minimum_hold_defers_discretionary_exits():
    position = make(regime_break_closes=doctrine.REGIME_BREAK_CLOSES,
                    scaled_out=True, qty=0.5, target=300.0)
    assert plan(position, 101.0, analysis(), ts=60_000).exit is None


# -- ordering ----------------------------------------------------------------------------

def test_catastrophe_outranks_every_other_rung():
    position = make(regime_break_closes=5, scaled_out=True)
    result = plan(position, 85.0, analysis(), ts=100 * HOUR)
    assert result.exit.kind == "CATASTROPHE"


def test_stop_outranks_the_time_stop():
    result = plan(make(), 95.0, analysis(), ts=100 * HOUR)
    assert result.exit.kind == "STOP"


def test_closed_position_produces_no_plan():
    assert plan(make(qty=0.0), 100.0, analysis(), ts=HOUR).exit is None


# -- accounting --------------------------------------------------------------------------

def test_realise_nets_fees_off_the_result():
    position = make(entry_fee=0.10)
    net = realise(position, 1.0, 110.0, 0.11)
    assert net == pytest.approx(10.0 - 0.11 - 0.10)
    assert position.qty == 0.0


def test_entry_fee_is_split_across_partial_exits_and_never_double_counted():
    """Two half-exits must charge the entry fee exactly once in total."""
    position = make(entry_fee=0.10)
    first = realise(position, 0.5, 110.0, 0.05)
    second = realise(position, 0.5, 120.0, 0.06)
    gross = (110.0 - 100.0) * 0.5 + (120.0 - 100.0) * 0.5
    assert first + second == pytest.approx(gross - 0.05 - 0.06 - 0.10)


def test_a_winning_round_trip_nets_positive():
    position = make(entry_fee=0.04)
    realise(position, 0.5, 104.0, 0.02)
    realise(position, 0.5, 112.0, 0.02)
    assert position.realized_pnl > 0


def test_write_off_makes_the_booked_result_equal_the_cash():
    """A final exit floored to the lot step leaves a sliver unsold. The books must charge
    for it, or the realised result drifts above the cash by its cost on every trade."""
    position = make(entry_fee=0.10)
    paid = 100.0 * 1.0 + 0.10                         # what the buy took out of the account
    realise(position, 0.99, 110.0, 0.11)              # only 0.99 could be sold
    write_off(position)

    received = 0.99 * 110.0 - 0.11
    assert position.realized_pnl == pytest.approx(received - paid)
    assert position.qty == 0.0


def test_write_off_does_nothing_when_everything_sold():
    position = make(entry_fee=0.10)
    realise(position, 1.0, 110.0, 0.11)
    before = position.realized_pnl
    assert write_off(position) == 0.0
    assert position.realized_pnl == before


def test_swept_dust_is_booked_as_pure_proceeds():
    """Its cost was written off by the trade that left it, so all it fetches is result."""
    position = make()
    net = book_swept(position, 0.01, 110.0, 0.001)
    assert net == pytest.approx(1.1 - 0.001)
    assert position.realized_pnl == pytest.approx(net)
    assert position.qty == 1.0                        # the position's own coins are untouched


# -- regime-break counting is per bar, not per cycle --------------------------------------

def _bar(open_time: float, close: float):
    import numpy as np

    from helm.models import Candles

    return Candles(
        symbol="BTCEUR", timeframe="1h",
        open_time=np.array([open_time - 3_600_000.0, open_time]),
        open=np.array([close, close]), high=np.array([close, close]),
        low=np.array([close, close]), close=np.array([close, close]),
        volume=np.array([1.0, 1.0]),
    )


def test_regime_break_counts_each_bar_once_however_often_it_is_checked():
    """The management loop runs every 60s against an hourly series.

    Counting per call rather than per bar reaches the two-close threshold in two minutes
    and stops out every position almost immediately — the single most damaging bug this
    file guards against.
    """
    from helm.positions import count_regime_break

    position = make()
    for _ in range(60):        # an hour of 60-second cycles, all inside one bar
        count_regime_break(position, _bar(1_000_000.0, 90.0), 100.0)
    assert position.regime_break_closes == 1


def test_regime_break_advances_on_a_genuinely_new_bar():
    from helm.positions import count_regime_break

    position = make()
    count_regime_break(position, _bar(1_000_000.0, 90.0), 100.0)
    count_regime_break(position, _bar(4_600_000.0, 90.0), 100.0)
    assert position.regime_break_closes == doctrine.REGIME_BREAK_CLOSES


def test_regime_break_resets_when_price_closes_back_above():
    from helm.positions import count_regime_break

    position = make()
    count_regime_break(position, _bar(1_000_000.0, 90.0), 100.0)
    count_regime_break(position, _bar(4_600_000.0, 110.0), 100.0)
    assert position.regime_break_closes == 0


def test_a_stale_bar_never_decrements_progress():
    """Out-of-order or repeated candle data must not un-count a real break."""
    from helm.positions import count_regime_break

    position = make()
    count_regime_break(position, _bar(4_600_000.0, 90.0), 100.0)
    count_regime_break(position, _bar(1_000_000.0, 110.0), 100.0)   # older bar arrives late
    assert position.regime_break_closes == 1
