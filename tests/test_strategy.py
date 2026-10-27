"""The rule, as pure functions: doctrine sections 2 and 3.

Everything here is arithmetic on numbers handed in, so it can be pinned exactly: which
coins are on, which are sold, how much each purchase spends, and what is never bought.
"""

import numpy as np
import pytest

from helm import doctrine, strategy
from helm.models import Candles
from helm.strategy import Signal

DAY = 86_400_000


def daily(closes, symbol="BTCEUR"):
    closes = np.array(closes, dtype=float)
    times = np.arange(closes.size, dtype=float) * DAY
    return Candles(symbol=symbol, timeframe="1d", open_time=times, open=closes, high=closes,
                   low=closes, close=closes, volume=np.ones_like(closes))


def signal(symbol, on=True, close=110.0):
    return Signal(symbol=symbol, day=0, close=close, average=close / 1.1 if on else close / 0.9)


# -- reading the close -------------------------------------------------------------------

def test_no_verdict_without_enough_history():
    assert strategy.read(daily([100.0] * (doctrine.SMA_DAYS - 1))) is None
    assert strategy.read(daily([100.0] * doctrine.SMA_DAYS)) is not None


def test_the_average_is_of_the_last_200_closes_including_the_day_judged():
    closes = [1_000.0] * 50 + list(range(1, doctrine.SMA_DAYS + 1))
    verdict = strategy.read(daily(closes))
    assert verdict.close == doctrine.SMA_DAYS
    assert verdict.average == pytest.approx(np.mean(range(1, doctrine.SMA_DAYS + 1)))


def test_on_only_strictly_above_the_average():
    assert strategy.read(daily([100.0] * 199 + [110.0])).on
    assert not strategy.read(daily([100.0] * 199 + [90.0])).on
    assert not strategy.read(daily([100.0] * 200)).on, "a close on the average is not above it"


def test_the_verdict_is_dated_by_the_day_it_rests_on():
    candles = daily([100.0] * 250)
    assert strategy.read(candles).day == int(candles.open_time[-1])


def test_distance_is_measured_from_the_average():
    verdict = Signal("BTCEUR", 0, close=105.0, average=100.0)
    assert verdict.distance == pytest.approx(0.05)


# -- selling -----------------------------------------------------------------------------

def test_sells_held_coins_whose_trend_broke_and_nothing_else():
    signals = {"BTCEUR": signal("BTCEUR", on=False), "ETHEUR": signal("ETHEUR", on=True),
               "SOLEUR": signal("SOLEUR", on=False)}
    assert strategy.exits(signals, held={"BTCEUR", "ETHEUR"}) == ["BTCEUR"]


def test_a_held_coin_with_no_verdict_is_left_alone():
    assert strategy.exits({}, held={"BTCEUR"}) == []


# -- buying ------------------------------------------------------------------------------

def test_buys_a_quarter_of_equity_in_each_coin_that_is_on():
    signals = {symbol: signal(symbol) for symbol in doctrine.SYMBOLS}
    plan = strategy.entries(signals, held=set(), equity=100.0, cash=100.0)
    assert [entry.symbol for entry in plan] == list(doctrine.SYMBOLS)
    assert [entry.amount for entry in plan] == [25.0] * 4


def test_quarter_is_of_equity_not_of_cash():
    """Half invested, half cash: a new coin still gets a quarter of the whole account."""
    plan = strategy.entries({"SOLEUR": signal("SOLEUR")}, held=set(), equity=200.0, cash=100.0)
    assert plan[0].amount == 50.0


def test_the_last_coin_gets_whatever_cash_is_left():
    signals = {symbol: signal(symbol) for symbol in doctrine.SYMBOLS}
    plan = strategy.entries(signals, held=set(), equity=120.0, cash=70.0)
    assert [entry.amount for entry in plan if not entry.skipped] == [30.0, 30.0, 10.0]


def test_skips_below_the_minimum_order_and_says_why():
    signals = {symbol: signal(symbol) for symbol in doctrine.SYMBOLS}
    plan = strategy.entries(signals, held=set(), equity=100.0, cash=55.0)
    assert [entry.amount for entry in plan] == [25.0, 25.0, 0.0, 0.0]
    assert "minimum" in plan[2].skipped


def test_never_buys_more_of_a_coin_already_held():
    plan = strategy.entries({"BTCEUR": signal("BTCEUR")}, held={"BTCEUR"}, equity=100.0, cash=100.0)
    assert plan == []


def test_never_buys_a_coin_that_is_off():
    plan = strategy.entries({"BTCEUR": signal("BTCEUR", on=False)}, held=set(), equity=100.0, cash=100.0)
    assert plan == []


def test_the_exchange_minimum_counts_when_it_is_higher():
    plan = strategy.entries({"BTCEUR": signal("BTCEUR")}, held=set(), equity=60.0, cash=60.0,
                            minimums={"BTCEUR": 20.0})
    assert plan[0].amount == 0.0 and "20.00" in plan[0].skipped


def test_amounts_are_floored_to_the_cent():
    """Rounding up could ask for more cash than the account holds."""
    plan = strategy.entries({"BTCEUR": signal("BTCEUR")}, held=set(), equity=100.0, cash=10.019)
    assert plan[0].amount == 10.01


def test_below_forty_the_rule_cannot_buy_at_all():
    """Doctrine section 3: a quarter of under 40 is under the 10 minimum."""
    signals = {symbol: signal(symbol) for symbol in doctrine.SYMBOLS}
    assert all(entry.skipped for entry in strategy.entries(signals, set(), equity=39.0, cash=39.0))
    assert not strategy.entries(signals, set(), equity=40.0, cash=40.0)[0].skipped
