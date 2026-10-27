"""Position bookkeeping: what each sale books against the purchase it came from.

Pure arithmetic, pinned exactly — the booked result has to match the cash, fee for fee and
lot step for lot step, or the drawdown the hard halt reads describes money never lost.
"""

import pytest

from helm.models import Position
from helm.positions import book_swept, realise, write_off


def make(**kwargs) -> Position:
    base = dict(id="p", symbol="BTCEUR", venue="sim", qty=1.0, entry_price=100.0,
                initial_qty=1.0, opened_at=0, cost=100.0)
    base.update(kwargs)
    return Position(**base)


def test_realise_nets_fees_off_the_result():
    position = make(entry_fee=0.10)
    net = realise(position, 1.0, 110.0, 0.11)
    assert net == pytest.approx(10.0 - 0.11 - 0.10)
    assert position.qty == 0.0


def test_entry_fee_is_split_across_partial_exits_and_never_double_counted():
    """A sale that fills short and is finished later must charge the entry fee once in total."""
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
