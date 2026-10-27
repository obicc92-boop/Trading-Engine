"""End-to-end engine behaviour.

The real `Engine`, the real store and the real simulated broker, driven against a market
whose daily closes each test sets on purpose (`tests/support.py`) and a clock it moves.
Nothing about the rule is mocked: what the engine buys and sells follows from the closes.
"""

import json

import pytest

from helm import doctrine
from helm.engine import GIVE_UP_MS, RETRY_MS
from helm.models import Position
from helm.store import Store
from helm.venues.base import VenueError
from tests.support import DOWN, UP, TrendMarket, build_engine


@pytest.fixture
def engine(tmp_path, market):
    return build_engine(str(tmp_path / "test.db"), market)


def held(engine):
    return sorted(position.symbol for position in engine.positions)


def actions(engine):
    return {row["symbol"]: row["action"] for row in engine.store.decisions(4)}


# -- boot --------------------------------------------------------------------------------

def test_dry_mode_never_uses_a_live_broker(engine):
    assert engine.broker.live is False
    assert engine.config.live is False


def test_snapshot_is_json_safe(engine):
    engine.cycle()
    json.dumps(engine.snapshot())      # raises on NaN or numpy scalars


def test_snapshot_shows_every_coin_against_its_average(engine, market):
    market.set_close("BTCEUR", UP)
    engine.cycle()
    coins = {row["symbol"]: row for row in engine.snapshot()["coins"]}
    assert list(coins) == list(doctrine.SYMBOLS)
    assert coins["BTCEUR"]["on"] is True and coins["BTCEUR"]["held"] is True
    assert coins["ETHEUR"]["on"] is False and coins["ETHEUR"]["held"] is False
    assert coins["BTCEUR"]["distance"] == pytest.approx(UP / coins["BTCEUR"]["average"] - 1)


def test_refuses_a_version_one_database(tmp_path):
    import sqlite3

    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE positions (id TEXT, setup TEXT, stop REAL)")
    with pytest.raises(RuntimeError, match="version 1"):
        Store(str(path))


# -- the daily decision ------------------------------------------------------------------

def test_buys_a_quarter_of_the_account_in_each_coin_above_its_average(engine, market):
    market.set_close("BTCEUR", UP)
    market.set_close("SOLEUR", UP)
    engine.cycle()

    assert held(engine) == ["BTCEUR", "SOLEUR"]
    for position in engine.positions:
        assert position.cost == pytest.approx(25.0)
    assert engine.account_state().cash == pytest.approx(50.0)
    assert actions(engine) == {"BTCEUR": "BUY", "ETHEUR": "STAY_OUT", "SOLEUR": "BUY", "XRPEUR": "STAY_OUT"}


def test_all_four_on_means_fully_invested(engine, market):
    for symbol in doctrine.SYMBOLS:
        market.set_close(symbol, UP)
    engine.cycle()
    assert held(engine) == sorted(doctrine.SYMBOLS)
    assert engine.account_state().cash == pytest.approx(0.0, abs=0.01)


def test_decides_once_a_day_however_often_it_cycles(engine, market, clock):
    engine.cycle()
    market.set_close("BTCEUR", UP)          # a change after the decision is not acted on
    for _ in range(10):
        clock.advance(60_000)
        engine.cycle()
    assert engine.positions == []
    assert len(engine.store.decisions(100)) == len(doctrine.SYMBOLS)


def test_acts_on_the_next_close(engine, market):
    engine.cycle()
    market.new_day({"BTCEUR": UP})
    engine.cycle()
    assert held(engine) == ["BTCEUR"]


def test_sells_a_coin_when_it_closes_below_its_average(engine, market):
    market.set_close("ETHEUR", UP)
    engine.cycle()
    market.new_day({"ETHEUR": DOWN})
    engine.cycle()

    assert engine.positions == []
    trade = engine.store.closed_positions()[0]
    assert trade["exit_reason"] == "TREND_OFF"
    assert trade["realized_pnl"] < 0
    assert actions(engine)["ETHEUR"] == "SELL"


def test_holds_through_a_loss_while_the_trend_holds(engine, market):
    """No stop inside the trend: a close that is lower, but still above the average, is held."""
    market.set_close("ETHEUR", UP)
    engine.cycle()
    market.new_day({"ETHEUR": UP * 0.95})
    engine.cycle()
    assert held(engine) == ["ETHEUR"]
    assert actions(engine)["ETHEUR"] == "HOLD"


def test_never_adds_to_a_coin_already_held(engine, market):
    market.set_close("BTCEUR", UP)
    engine.cycle()
    market.new_day({"BTCEUR": UP * 1.2})
    engine.cycle()
    assert len(engine.positions) == 1
    assert engine.positions[0].cost == pytest.approx(25.0)


def test_sells_first_so_the_sale_pays_for_the_purchase(engine, market):
    for symbol in doctrine.SYMBOLS[:3]:
        market.set_close(symbol, UP)
    market.set_close("XRPEUR", DOWN)
    engine.cycle()
    engine.broker._balances["EUR"] = 0.0     # spend the last quarter elsewhere

    market.new_day({"BTCEUR": DOWN, "XRPEUR": UP})
    engine.cycle()
    assert held(engine) == ["ETHEUR", "SOLEUR", "XRPEUR"]


def test_records_every_coin_every_day_including_the_quiet_ones(engine, market):
    engine.cycle()
    market.new_day()
    engine.cycle()
    rows = engine.store.decisions(100)
    assert len(rows) == 2 * len(doctrine.SYMBOLS)
    assert all(row["close"] and row["average"] for row in rows)


# -- when it will not buy ----------------------------------------------------------------

def test_paused_it_still_sells_but_does_not_buy(engine, market):
    market.set_close("BTCEUR", UP)
    engine.cycle()
    engine.pause()
    market.new_day({"BTCEUR": DOWN, "ETHEUR": UP})
    engine.cycle()

    assert engine.positions == []
    assert actions(engine)["BTCEUR"] == "SELL"
    assert actions(engine)["ETHEUR"] == "STAY_OUT"
    assert any("paused" in row["note"] for row in engine.store.decisions(4))


def test_halts_at_seventy_percent_below_peak_and_keeps_selling(engine, market):
    for symbol in doctrine.SYMBOLS:
        market.set_close(symbol, UP)
    engine.cycle()                                    # fully invested, peak 100

    crash = {symbol: UP * 0.25 for symbol in doctrine.SYMBOLS}
    crash["BTCEUR"] = UP * 0.3
    market.closes = {s: [UP * 0.2] * 199 + [market.closes[s][-1]] for s in doctrine.SYMBOLS}
    market.new_day(crash)                             # still above their (crashed) averages
    engine.cycle()
    assert engine.halted
    assert "70%" in engine.halt_reason

    market.new_day({"BTCEUR": UP * 0.01})             # BTC breaks its trend
    engine.cycle()
    assert "BTCEUR" not in held(engine), "a halt must never stop a sale"


def test_a_halt_blocks_purchases_until_cleared(engine, market):
    engine.halt("testing")
    market.set_close("BTCEUR", UP)
    engine.cycle()
    assert engine.positions == []

    engine.clear_halt()
    market.new_day()
    engine.cycle()
    assert held(engine) == ["BTCEUR"]


def test_clearing_a_halt_measures_the_peak_afresh(engine, market):
    """Otherwise the next decision sees the same drop against the same peak and halts again."""
    engine.store.put("equity_peak", 1_000.0)
    engine.cycle()
    assert engine.halted

    engine.clear_halt()
    market.new_day()
    engine.cycle()
    assert not engine.halted


def test_will_not_buy_a_coin_the_account_already_holds_unrecorded(engine, market):
    engine.broker._balances["BTC"] = 0.5            # ~50 EUR the engine has no record of
    market.set_close("BTCEUR", UP)
    engine.cycle()
    assert engine.positions == []
    assert any("no record" in row["note"] for row in engine.store.decisions(4))


# -- when the exchange gets in the way ---------------------------------------------------

def test_waits_for_the_close_rather_than_deciding_on_an_old_one(engine, market, clock):
    market.unpublished.add("ETHEUR")
    market.set_close("BTCEUR", UP)
    engine.cycle()
    assert engine.positions == [] and engine.store.decisions(10) == []

    market.unpublished.clear()
    clock.advance(60_000)
    engine.cycle()
    assert held(engine) == ["BTCEUR"]


def test_after_hours_without_a_close_it_decides_without_that_coin(engine, market, clock):
    market.failing.add("ETHEUR")
    market.set_close("BTCEUR", UP)
    clock.advance(GIVE_UP_MS)
    engine.cycle()
    assert held(engine) == ["BTCEUR"]
    assert "ETHEUR" not in actions(engine)


def test_a_rejected_order_is_retried_and_reported_once(engine, market, clock):
    alerts = []
    engine.notifier.notify = lambda message, level="info": alerts.append(level)
    real_buy = engine.broker.buy
    engine.broker.buy = lambda symbol, amount: (_ for _ in ()).throw(VenueError("exchange busy"))
    market.set_close("BTCEUR", UP)

    engine.cycle()
    clock.advance(RETRY_MS)
    engine.cycle()                                   # fails again, quietly
    assert engine.positions == []
    assert alerts.count("error") == 1

    engine.broker.buy = real_buy
    clock.advance(RETRY_MS)
    engine.cycle()
    assert held(engine) == ["BTCEUR"]


def test_an_unreadable_account_is_retried_not_decided_blind(engine, market, clock):
    """With no balances the cash looks like zero, and sizing from that would skip the day."""
    real_balances = engine.broker.balances
    engine.broker.balances = lambda: (_ for _ in ()).throw(VenueError("timeout"))
    market.set_close("BTCEUR", UP)
    engine.cycle()
    assert engine.positions == [] and engine.store.get("decided_day") is None

    engine.broker.balances = real_balances
    clock.advance(RETRY_MS)
    engine.cycle()
    assert held(engine) == ["BTCEUR"]


def test_the_daily_message_goes_out_once_per_decision(engine, market, clock):
    sent = []
    engine.notifier.notify = lambda message, level="info": sent.append(message)
    engine.cycle()
    clock.advance(60_000)
    engine.cycle()
    reports = [message for message in sent if message.startswith("Daily close")]
    assert len(reports) == 1
    assert "Equity" in reports[0] and "BTC" in reports[0]


# -- restarts ----------------------------------------------------------------------------

def test_a_restart_resumes_holdings_and_does_not_decide_the_same_day_twice(engine, market, tmp_path):
    market.set_close("BTCEUR", UP)
    engine.cycle()

    again = build_engine(engine.config.db_path, market)
    again._restore_positions()
    again.cycle()

    assert held(again) == ["BTCEUR"]
    assert again.positions[0].cost == pytest.approx(25.0)
    assert again.signals["BTCEUR"].on
    assert len(again.store.decisions(100)) == len(doctrine.SYMBOLS)


# -- manual controls ---------------------------------------------------------------------

def test_flatten_sells_everything_and_pauses(engine, market):
    for symbol in doctrine.SYMBOLS:
        market.set_close(symbol, UP)
    engine.cycle()
    assert engine.flatten("test") == 4
    assert engine.positions == []
    assert engine.paused, "otherwise the rule buys straight back in at the next close"


def test_closing_one_coin_by_hand(engine, market):
    market.set_close("BTCEUR", UP)
    engine.cycle()
    assert engine.close_one(engine.positions[0].id)
    assert engine.store.closed_positions()[0]["exit_reason"] == "MANUAL"


# -- reconciliation ----------------------------------------------------------------------

def test_engine_defers_to_the_broker_when_a_position_vanishes(engine, market):
    market.set_close("BTCEUR", UP)
    engine.cycle()
    engine.broker._balances["BTC"] = 0.0
    engine.reconcile()
    assert engine.positions == []
    assert engine.store.closed_positions()[0]["exit_reason"] == "EXTERNALLY_CLOSED"


def test_engine_corrects_a_partially_reduced_position(engine, market):
    market.set_close("BTCEUR", UP)
    engine.cycle()
    position = engine.positions[0]
    engine.broker._balances["BTC"] = position.qty * 0.5
    engine.reconcile()
    assert position.qty == pytest.approx(engine.broker._balances["BTC"])


# -- books and dust ----------------------------------------------------------------------
# Exchanges sell in whole lot steps, so a sale floors its quantity and can leave a sliver.
# These pin that the books still match the cash, and that nothing worth selling is left.

STEP = 0.00001


@pytest.fixture
def stepped(tmp_path, clock):
    market = TrendMarket(clock, step=STEP)
    for symbol in doctrine.SYMBOLS:
        market.closes[symbol] = [50_000.0] * 250
        market.price[symbol] = 50_000.0
    engine = build_engine(str(tmp_path / "stepped.db"), market)
    engine.market_ = market
    return engine


def _round_trip(engine) -> None:
    market = engine.market_
    market.new_day({"BTCEUR": 58_000.0})
    engine.cycle()
    assert held(engine) == ["BTCEUR"]
    market.new_day({"BTCEUR": 40_000.0})
    engine.cycle()
    assert engine.positions == []


def test_books_match_the_cash_across_round_trips_with_lot_steps(stepped):
    start = stepped.config.starting_equity
    for _ in range(3):
        _round_trip(stepped)
        cash = stepped.broker.balances()["EUR"]
        assert start + stepped.store.total_realized() == pytest.approx(cash, abs=1e-9)
        assert stepped.account_state().equity == pytest.approx(cash, abs=1e-9)


def test_dust_never_accumulates_past_one_lot_step(stepped):
    for _ in range(3):
        _round_trip(stepped)
        assert 0 <= stepped.broker.balances().get("BTC", 0.0) < STEP


def test_a_sale_never_sells_coins_the_engine_did_not_buy(stepped):
    """Only dust is swept. A holding worth selling on its own is not the engine's to sell."""
    market = stepped.market_
    market.new_day({"BTCEUR": 58_000.0})
    stepped.cycle()
    owned_by_hand = 0.001                           # ~58 EUR of BTC already in the account
    stepped.broker._balances["BTC"] += owned_by_hand

    market.new_day({"BTCEUR": 40_000.0})
    stepped.cycle()
    assert stepped.positions == []
    assert stepped.broker.balances()["BTC"] == pytest.approx(owned_by_hand, abs=STEP)


def test_a_sale_asks_for_no_more_than_the_account_holds(stepped):
    """Binance takes a buy's fee out of the coins received, so the account can hold a
    little less than the position records. The exchange rejects an over-sized sell whole,
    and a rejected sale is a broken trend held."""
    market = stepped.market_
    market.new_day({"BTCEUR": 58_000.0})
    stepped.cycle()
    position = stepped.positions[0]
    floored = int(position.qty / STEP) * STEP
    stepped.broker._balances["BTC"] = floored - STEP / 10

    real_sell = stepped.broker.sell

    def strict_sell(symbol, qty):
        if qty > stepped.broker.balances()["BTC"] + 1e-15:
            raise VenueError("Account has insufficient balance for requested action.")
        return real_sell(symbol, qty)

    stepped.broker.sell = strict_sell
    market.new_day({"BTCEUR": 40_000.0})
    stepped.cycle()
    assert stepped.positions == []
    assert stepped.store.closed_positions()[0]["exit_reason"] == "TREND_OFF"


def test_a_short_fill_keeps_the_rest_and_sells_it_on_the_retry(stepped, clock):
    market = stepped.market_
    market.new_day({"BTCEUR": 58_000.0})
    stepped.cycle()
    position = stepped.positions[0]
    real_sell = stepped.broker.sell
    # Most of it left unsold: worth more than the dust floor, so not written off.
    stepped.broker.sell = lambda symbol, qty: real_sell(symbol, qty * 0.1)

    market.new_day({"BTCEUR": 40_000.0})
    stepped.cycle()
    assert stepped.positions == [position] and position.qty > 0

    stepped.broker.sell = real_sell
    clock.advance(RETRY_MS)
    stepped.cycle()
    assert stepped.positions == []


def test_realised_aggregates_match_the_rows_they_sum(engine, market):
    for symbol in ("BTCEUR", "ETHEUR"):
        market.set_close(symbol, UP)
    engine.cycle()
    market.new_day({"BTCEUR": DOWN, "ETHEUR": DOWN})
    engine.cycle()
    by_hand = sum(row["realized_pnl"] for row in engine.store.all_closed())
    assert engine.store.total_realized() == pytest.approx(by_hand)
    assert engine.store.realized_since(0) == pytest.approx(by_hand)


def test_a_position_round_trips_through_the_store(engine):
    position = Position(id="p1", symbol="BTCEUR", venue="sim", qty=0.5, entry_price=100.0,
                        initial_qty=0.5, opened_at=1, cost=50.05, entry_close=99.0,
                        entry_average=95.0, fees_paid=0.05, entry_fee=0.05, last_note="note")
    engine.store.open_position(position)
    engine._restore_positions()
    assert engine.positions[0] == position
