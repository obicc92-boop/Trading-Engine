"""End-to-end engine behaviour.

The real `Engine`, the real risk rails, the real exit ladder, the real store — driven
against the synthetic market with a stubbed decision layer, because the point is to test
the wiring rather than the model. Anything mocked here is called out.
"""

import pytest

from helm import doctrine
from helm.config import Config
from helm.engine import Engine
from helm.models import Analysis, Candidate, Verdict


@pytest.fixture
def engine(tmp_path):
    config = Config()
    config.db_path = str(tmp_path / "test.db")
    config.venue = "synthetic"
    engine = Engine(config)
    engine.refresh_prices()
    return engine


def approve(engine, **kwargs):
    """Stub the decision layer with a GO, so the wiring around it is what gets tested."""
    verdict = Verdict(go=True, conviction=70, size_multiplier=1.0, stop_override=None,
                      thesis="Stubbed approval.", invalidation="stub", source="stub", **kwargs)
    engine.brain.judge_entry = lambda *args, **kw: verdict
    return verdict


def decline(engine):
    verdict = Verdict(go=False, conviction=10, size_multiplier=0.25, stop_override=None,
                      thesis="Stubbed refusal.", invalidation="stub", source="stub")
    engine.brain.judge_entry = lambda *args, **kw: verdict
    return verdict


def make_candidate(engine, symbol="BTCEUR", score=75.0):
    price = engine._prices[symbol]
    analysis = Analysis(symbol=symbol, price=price, regime="TRENDING_UP",
                        indicators={"atr": price * 0.01, "swing_low": price * 0.97,
                                    "ema21": price * 0.99, "rsi": 55.0})
    engine._analysis[symbol] = analysis
    return Candidate(symbol=symbol, setup="TREND_PULLBACK", score=score, factors={},
                     price=price, stop=price * 0.96, target=price * 1.12,
                     regime="TRENDING_UP", analysis=analysis, rationale=["test"])


def mark(engine, symbol, price):
    engine._prices[symbol] = price
    engine.broker.set_marks(engine._prices)


# -- boot --------------------------------------------------------------------------------

def test_cycle_runs_clean_with_no_positions(engine):
    engine.cycle()
    assert engine.snapshot()["cycle"] == 1
    assert engine.snapshot()["positions"] == []


def test_dry_mode_never_uses_a_live_broker(engine):
    assert engine.broker.live is False
    assert engine.config.live is False


def test_snapshot_is_json_safe(engine):
    import json

    engine.cycle()
    json.dumps(engine.snapshot())      # raises on NaN or numpy scalars


# -- entry -------------------------------------------------------------------------------

def test_a_declined_candidate_is_not_traded(engine):
    decline(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    assert engine.positions == []
    assert engine.store.decisions()[0]["blocked_by"] == "declined"


def test_no_decision_layer_means_no_trade(engine):
    """The default posture with no API key: candidates arrive, nothing is bought."""
    assert engine.brain.enabled is False
    engine._consider(make_candidate(engine), engine.account_state())
    assert engine.positions == []


def test_an_approved_candidate_opens_a_sized_position(engine):
    approve(engine)
    assert engine._consider(make_candidate(engine), engine.account_state())

    position = engine.positions[0]
    assert position.symbol == "BTCEUR"
    assert position.qty > 0
    # Risk is bounded by the doctrine, measured from the actual fill.
    assert position.risk_amount <= doctrine.MAX_RISK_PER_TRADE * 100.0 + 0.05
    assert position.notional == pytest.approx(37.5, rel=0.05)


def test_the_position_is_persisted_and_survives_a_restart(engine, tmp_path):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    original = engine.positions[0]

    revived = Engine(engine.config)
    revived._restore_positions()
    assert len(revived.positions) == 1
    assert revived.positions[0].id == original.id
    assert revived.positions[0].stop == pytest.approx(original.stop)


def test_decision_is_logged_with_its_thesis(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    record = engine.store.decisions()[0]
    assert record["go"] is True
    assert record["thesis"] == "Stubbed approval."
    assert record["position_id"] is not None


# -- lifecycle ---------------------------------------------------------------------------

def test_winning_trade_scales_out_then_trails_to_a_profit(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    position = engine.positions[0]
    r_unit = position.r_unit
    starting_qty = position.qty

    mark(engine, "BTCEUR", position.entry_price + r_unit * 1.05)
    engine._manage_one(position)
    assert position.scaled_out
    assert position.qty == pytest.approx(starting_qty / 2, rel=0.02)
    assert position.stop > position.entry_price        # the trade is now free
    assert position.realized_pnl > 0

    position.highest_close = position.entry_price + r_unit * 3
    mark(engine, "BTCEUR", position.entry_price + r_unit * 2.8)
    engine._manage_one(position)
    assert position.stop > position.entry_price

    mark(engine, "BTCEUR", position.stop * 0.999)
    engine._manage_one(position)

    assert engine.positions == []
    closed = engine.store.closed_positions()[0]
    assert closed["realized_pnl"] > 0
    assert closed["r_multiple"] > 0


def test_losing_trade_costs_no_more_than_the_planned_risk(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    position = engine.positions[0]
    planned = position.risk_amount

    mark(engine, "BTCEUR", position.stop * 0.999)
    engine._manage_one(position)

    closed = engine.store.closed_positions()[0]
    assert closed["realized_pnl"] < 0
    # Slippage and fees make the realised loss slightly worse than planned, but only
    # slightly — anything beyond a small margin means sizing is broken.
    assert abs(closed["realized_pnl"]) < planned * 1.25


def test_a_stop_out_puts_the_symbol_on_cooldown(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    position = engine.positions[0]
    mark(engine, "BTCEUR", position.stop * 0.999)
    engine._manage_one(position)

    verdict = engine.risk.evaluate(
        make_candidate(engine), engine.account_state(), engine.last_cycle_at or 1
    )
    assert not verdict.allowed


def test_catastrophe_guard_closes_a_gapped_position(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    position = engine.positions[0]

    mark(engine, "BTCEUR", position.entry_price * 0.88)     # gapped straight through
    engine._manage_one(position)

    assert engine.positions == []
    assert engine.store.closed_positions()[0]["exit_reason"] == "CATASTROPHE"


# -- rails in the live path --------------------------------------------------------------

def test_will_not_open_a_second_position_in_the_same_symbol(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    assert not engine._consider(make_candidate(engine), engine.account_state())
    assert len(engine.positions) == 1


def test_stops_entering_at_the_position_cap(engine):
    approve(engine)
    for symbol in ("BTCEUR", "ETHEUR", "SOLEUR", "ADAEUR"):
        engine.brain._asked.clear()
        engine._consider(make_candidate(engine, symbol), engine.account_state())
    assert len(engine.positions) <= doctrine.MAX_OPEN_POSITIONS


def test_pausing_stops_new_entries_but_keeps_managing(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    engine.pause()
    engine.hunt()                       # must not raise, must not enter
    assert len(engine.positions) == 1
    assert "paused by hand" in engine.snapshot()["blocks"]


def test_flatten_closes_everything(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    assert engine.flatten("test") == 1
    assert engine.positions == []
    assert engine.store.closed_positions()[0]["exit_reason"] == "FLATTENED"


def test_halt_blocks_entries_until_cleared(engine):
    engine.halt("testing")
    assert engine.risk.gate_entries(engine.account_state(), 1)
    engine.clear_halt()
    assert not engine.halted


# -- reconciliation ----------------------------------------------------------------------

def test_engine_defers_to_the_broker_when_a_position_vanishes(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())

    # Simulate the coin being sold outside the engine.
    engine.broker._balances["BTC"] = 0.0
    engine.reconcile()

    assert engine.positions == []
    assert engine.store.closed_positions()[0]["exit_reason"] == "EXTERNALLY_CLOSED"


def test_engine_corrects_a_partially_reduced_position(engine):
    approve(engine)
    engine._consider(make_candidate(engine), engine.account_state())
    position = engine.positions[0]
    # Captured by value: reconcile mutates the position in place, so holding a reference
    # and comparing it afterwards would compare the object to itself and assert nothing.
    original_qty = position.qty
    reduced = original_qty * 0.5

    engine.broker._balances["BTC"] = reduced
    engine.reconcile()

    assert len(engine.positions) == 1
    assert engine.positions[0].qty == pytest.approx(reduced)
    assert engine.positions[0].qty < original_qty


# -- accounting --------------------------------------------------------------------------

def test_equity_reflects_realised_results(engine):
    approve(engine)
    start = engine.account_state().equity
    engine._consider(make_candidate(engine), engine.account_state())
    position = engine.positions[0]

    mark(engine, "BTCEUR", position.entry_price * 1.10)
    engine._manage_one(position)                        # scale out
    mark(engine, "BTCEUR", position.entry_price * 1.20)
    engine._manage_one(position)                        # target or trail

    assert engine.account_state().equity > start


def test_scan_produces_rows_for_everything_considered(engine):
    engine.hunt()
    rows = engine.snapshot()["scan"]
    assert rows
    assert all("regime" in row and "score" in row for row in rows)


# -- scan cadence ------------------------------------------------------------------------

@pytest.mark.parametrize("interval,expected_scans", [(1, 5), (2, 3), (5, 1)])
def test_scan_interval_is_honoured(engine, interval, expected_scans):
    """An interval of 1 must mean *every* cycle.

    The natural-looking `cycle % interval == 1` is false for every integer when interval
    is 1, which silently stops the engine from ever scanning again after the first cycle.
    """
    engine.config.scan_every_cycles = interval
    scans = []
    engine.hunt = lambda: scans.append(engine._cycle)

    for _ in range(5):
        engine.cycle()

    assert len(scans) == expected_scans
    assert scans[0] == 1        # always scan on the first cycle after a restart


# -- account aggregates ------------------------------------------------------------------

def test_entries_today_counts_open_and_closed_trades_alike(engine):
    """The daily cap is about how many times I pulled the trigger.

    Whether a trade has finished yet is irrelevant — counting only closed ones would let
    three open positions be followed by three more entries on the same day.
    """
    approve(engine)
    engine._consider(make_candidate(engine, "BTCEUR"), engine.account_state())
    assert engine.account_state().entries_today == 1

    position = engine.positions[0]
    mark(engine, "BTCEUR", position.stop * 0.999)
    engine._manage_one(position)                     # now closed

    assert engine.positions == []
    assert engine.account_state().entries_today == 1  # still counts

    engine.brain._asked.clear()
    engine._consider(make_candidate(engine, "ETHEUR"), engine.account_state())
    assert engine.account_state().entries_today == 2


def test_realised_aggregates_match_the_full_scan_they_replaced(engine):
    """The SQL aggregates must agree with summing every row by hand."""
    approve(engine)
    for symbol in ("BTCEUR", "ETHEUR"):
        engine.brain._asked.clear()
        engine._consider(make_candidate(engine, symbol), engine.account_state())
    for position in list(engine.positions):
        mark(engine, position.symbol, position.stop * 0.999)
        engine._manage_one(position)

    by_hand = sum((row.get("realized_pnl") or 0.0) for row in engine.store.all_closed())
    assert engine.store.total_realized() == pytest.approx(by_hand)
    assert engine.store.realized_since(0) == pytest.approx(by_hand)


def test_losing_streak_counts_back_from_the_most_recent_trade(engine, tmp_path):
    from helm.models import Position, now_ms

    for index, pnl in enumerate([-1.0, -1.0, 2.0, -1.0, -1.0]):
        position = Position(
            id=f"p{index}", symbol=f"S{index}EUR", venue="sim", qty=1.0, entry_price=100.0,
            stop=96.0, target=112.0, initial_stop=96.0, initial_qty=1.0, risk_amount=1.5,
            opened_at=now_ms() + index, setup="TREND_PULLBACK", score=70, conviction=60,
        )
        engine.store.open_position(position)
        position.realized_pnl = pnl
        engine.store.close_position(position, 100.0, "STOP", pnl / 1.5)

    # Newest two are losses; the win before them ends the streak.
    assert engine.account_state().consecutive_losses == 2
