"""The engine loop.

One cycle, in this order, every time:

    reconcile -> manage open positions -> scan -> decide -> execute -> record -> broadcast

Open positions are managed *before* anything new is considered, because protecting money
already at risk beats finding somewhere else to put it. The scan runs on a slower cadence
than the loop, since candles do not change every sixty seconds and API quota is finite.

The loop is defensive about its own failures: an exception inside a cycle is logged and the
cycle is abandoned, never the loop. An engine that dies on a transient API error leaves real
positions unmanaged, which is the worst outcome available to it.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback

from . import doctrine, positions as position_rules
from .analysis import regime as regime_module, setups
from .analysis.scanner import Scanner
from .brain.claude import Brain
from .bus import bus
from .models import Analysis, Candidate, Position, clean, new_id, now_ms
from .risk import AccountState, RiskEngine, utc_day_start, utc_week_start
from .store import Store
from .venues.base import VenueError
from .venues.simulated import SimulatedBroker, SyntheticMarket

log = logging.getLogger("helm.engine")


class Engine:
    def __init__(self, config) -> None:
        self.config = config
        self.store = Store(config.db_path)
        self.risk = RiskEngine()
        self.brain = Brain(config)

        self.market, self.broker, self.synthetic = self._build_venue()
        self.scanner = Scanner(self.market, config)

        self.positions: list[Position] = []
        self.instruments: dict = {}

        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._cycle = 0
        self._scan_rows: list[dict] = []
        self._prices: dict[str, float] = {}
        self._analysis: dict[str, Analysis] = {}

        self.phase = "idle"
        self.paused = bool(self.store.get("paused", False))
        self.halted = bool(self.store.get("halted", False))
        self.halt_reason = str(self.store.get("halt_reason", ""))
        self.last_error = ""
        self.last_cycle_at = 0
        self.started_at = now_ms()

    # -- setup ---------------------------------------------------------------------------
    def _build_venue(self):
        """Pick a market source and a broker, and be honest about which we ended up with.

        Real market data is used even in dry mode whenever it is reachable — a rehearsal
        against invented prices teaches nothing. Synthetic data is the last resort, and the
        UI says so in as many words when it is in play.
        """
        config = self.config
        venue = config.venue

        if venue == "synthetic":
            market = SyntheticMarket(config.quote)
            return market, self._simulated_broker(market), True

        real = None
        try:
            if venue == "alpaca":
                from .venues.alpaca import AlpacaVenue

                real = AlpacaVenue(config)
            else:
                from .venues.binance import BinanceVenue

                real = BinanceVenue(config)
                real.sync_time()
            real.instruments()  # proves the venue is actually reachable
        except Exception as exc:
            log.warning("venue %s unreachable (%s) — falling back to synthetic data", venue, exc)
            self._boot_warning = f"{venue} unreachable: {exc}"
            market = SyntheticMarket(config.quote)
            return market, self._simulated_broker(market), True

        if config.live and real.live:
            return real, real, False

        return real, self._simulated_broker(real), False

    def _simulated_broker(self, market) -> SimulatedBroker:
        """Build the dry-run broker and give it back the balances it had before.

        Restoring matters more than it looks: without it a restart shows an empty account,
        reconciliation concludes every open position has vanished, and the engine closes
        trades that were never actually closed.
        """
        broker = SimulatedBroker(
            market, self.config.quote, self.config.starting_equity, self.config.taker_fee
        )
        broker.restore(self.store.get("sim_balances", {}))
        return broker

    def _restore_positions(self) -> None:
        for row in self.store.load_open_positions():
            state = row.get("state") or {}
            self.positions.append(
                Position(
                    id=row["id"], symbol=row["symbol"], venue=row["venue"], qty=row["qty"],
                    entry_price=row["entry_price"], stop=row["stop"], target=row["target"],
                    initial_stop=row["initial_stop"], initial_qty=row["initial_qty"],
                    risk_amount=row["risk_amount"], opened_at=row["opened_at"],
                    setup=row["setup"], score=row["score"], conviction=row["conviction"],
                    thesis=row["thesis"], invalidation=row["invalidation"],
                    scaled_out=bool(state.get("scaled_out")),
                    breakeven_set=bool(state.get("breakeven_set")),
                    highest_close=float(state.get("highest_close") or row["entry_price"]),
                    fees_paid=row["fees_paid"], realized_pnl=row["realized_pnl"],
                    regime_break_closes=int(state.get("regime_break_closes") or 0),
                    regime_bar_time=float(state.get("regime_bar_time") or 0.0),
                    last_note=str(state.get("last_note") or ""),
                )
            )
        if self.positions:
            self.narrate(
                f"Resumed management of {len(self.positions)} open "
                f"position{'s' if len(self.positions) > 1 else ''} from the last run.",
                level="info",
            )

    # -- lifecycle -----------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._restore_positions()
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run, name="helm-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=10)

    def pause(self) -> None:
        self.paused = True
        self.store.put("paused", True)
        self.narrate("Paused by hand. Open positions stay managed; no new entries.", level="warn")

    def resume(self) -> None:
        self.paused = False
        self.store.put("paused", False)
        self.narrate("Resumed. Scanning again.", level="info")

    def _run(self) -> None:
        mode = "LIVE — real money" if self.config.live else "dry run — simulated fills"
        source = "synthetic data" if self.synthetic else f"{self.market.name} market data"
        self.narrate(f"Engine up. {mode}, {source}.", level="info")
        if getattr(self, "_boot_warning", ""):
            self.narrate(
                f"Falling back to generated prices — {self._boot_warning}. "
                "Nothing here reflects a real market.",
                level="warn",
            )

        while not self._stop_flag.is_set():
            started = time.time()
            try:
                self.cycle()
                self.last_error = ""
            except Exception as exc:  # never let one bad cycle kill the loop
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.error("cycle failed: %s\n%s", exc, traceback.format_exc())
                self.narrate(f"Cycle failed: {self.last_error}. Retrying next cycle.", level="error")
            self.phase = "waiting"
            self.broadcast()
            elapsed = time.time() - started
            self._stop_flag.wait(max(1.0, self.config.cycle_seconds - elapsed))

    # -- the cycle -----------------------------------------------------------------------
    def cycle(self) -> None:
        self._cycle += 1
        self.last_cycle_at = now_ms()

        if self.config.kill_file().exists():
            self.narrate("Kill file found. Flattening everything and stopping.", level="error")
            self.flatten("kill switch")
            self.halt("kill switch engaged")
            self._stop_flag.set()
            return

        self.phase = "syncing"
        self.broadcast()
        self.refresh_prices()
        self.reconcile()

        state = self.account_state()
        self.store.record_equity(
            state.equity, state.cash,
            sum(p.qty * self._price_of(p.symbol, p.entry_price) for p in self.positions),
            state.realized_today, len(self.positions),
        )
        self._check_drawdown_halt(state)

        self.phase = "managing"
        self.broadcast()
        self.manage_positions()

        # Counted from the first cycle rather than by remainder-equals-one: `n % 1 == 1`
        # is false for every integer, so the obvious form silently disables scanning
        # altogether when someone sets the interval to 1.
        if (self._cycle - 1) % max(1, self.config.scan_every_cycles) == 0:
            self.phase = "scanning"
            self.broadcast()
            self.hunt()
        self.phase = "waiting"

    # -- prices and reconciliation -------------------------------------------------------
    def refresh_prices(self) -> None:
        try:
            self._prices = {symbol: quote.price for symbol, quote in self.market.quotes().items()}
            if isinstance(self.broker, SimulatedBroker):
                self.broker.set_marks(self._prices)
        except Exception as exc:
            log.warning("price refresh failed: %s", exc)

    def _price_of(self, symbol: str, default: float = 0.0) -> float:
        return self._prices.get(symbol) or default

    def reconcile(self) -> None:
        """Trust the broker, not our own bookkeeping.

        If the exchange says we hold less than the engine thinks — a manual sale, a partial
        fill, a dust rounding — the exchange is right. Silently trading against a position
        that isn't there is how an engine loses money it never sees.
        """
        if not self.positions:
            return
        try:
            balances = self.broker.balances()
        except Exception as exc:
            log.warning("balance check failed: %s", exc)
            return

        instruments = self._instrument_map()
        for position in list(self.positions):
            instrument = instruments.get(position.symbol)
            base = instrument.base if instrument else position.symbol.replace(self.config.quote, "")
            held = balances.get(base, 0.0)
            if held >= position.qty * 0.98:
                continue
            if held <= position.qty * 0.02:
                price = self._price_of(position.symbol, position.entry_price)
                self.narrate(
                    f"{position.symbol} is no longer in the account — closing the engine's "
                    f"record of it. The exchange is the source of truth.",
                    level="warn",
                )
                self._finalise(position, price, "EXTERNALLY_CLOSED")
            else:
                self.narrate(
                    f"{position.symbol} size corrected from {position.qty:.8f} to {held:.8f} "
                    f"to match the exchange.",
                    level="warn",
                )
                position.qty = held
                self.store.sync_position(position)

    def _instrument_map(self) -> dict:
        if not self.instruments:
            try:
                self.instruments = self.market.instruments()
            except Exception as exc:
                log.warning("instrument load failed: %s", exc)
        return self.instruments

    # -- account -------------------------------------------------------------------------
    def account_state(self) -> AccountState:
        try:
            balances = self.broker.balances()
        except Exception as exc:
            log.warning("balances unavailable: %s", exc)
            balances = {}

        cash = balances.get(self.config.quote, 0.0)
        holdings = 0.0
        for position in self.positions:
            holdings += position.qty * self._price_of(position.symbol, position.entry_price)
        equity = cash + holdings
        if equity <= 0:
            equity = self.config.starting_equity

        day_start = utc_day_start().timestamp() * 1000
        week_start = utc_week_start().timestamp() * 1000

        realized_today = self.store.realized_since(day_start)
        realized_week = self.store.realized_since(week_start)

        # Counts closed *and* still-open trades opened today: the daily cap is about how
        # many times I pulled the trigger, not how many of them happen to be finished.
        entries_today = self.store.entries_since(day_start)

        streak = 0
        for result in self.store.recent_results(20):
            if result < 0:
                streak += 1
            else:
                break

        # The high-water mark advances on *realised* results only. Marking it to an open
        # position's unrealised spike would let a winner that later gives back some profit
        # register as a drawdown — and a big enough round trip would trip the hard halt
        # without a single losing trade.
        realized_equity = self.config.starting_equity + self.store.total_realized()
        peak = max(
            float(self.store.get("peak_equity", 0.0) or 0.0),
            realized_equity,
            self.config.starting_equity,
        )
        self.store.put("peak_equity", peak)

        return AccountState(
            equity=equity,
            cash=cash,
            positions=self.positions,
            realized_today=realized_today,
            realized_week=realized_week,
            peak_equity=peak,
            consecutive_losses=streak,
            entries_today=entries_today,
            cooldowns={k: int(v) for k, v in (self.store.get("cooldowns", {}) or {}).items()},
            loss_cooldown_until=int(self.store.get("loss_cooldown_until", 0) or 0),
            halted=self.halted,
            halt_reason=self.halt_reason,
        )

    def _check_drawdown_halt(self, state: AccountState) -> None:
        if self.halted or state.drawdown < doctrine.MAX_DRAWDOWN_HALT:
            return
        self.halt(
            f"drawdown reached {state.drawdown*100:.1f}%, past the "
            f"{doctrine.MAX_DRAWDOWN_HALT*100:.0f}% hard limit"
        )

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        self.store.put("halted", True)
        self.store.put("halt_reason", reason)
        self.narrate(f"HALTED — {reason}. This needs a human to clear.", level="error")

    def clear_halt(self) -> None:
        self.halted = False
        self.halt_reason = ""
        self.store.put("halted", False)
        self.store.put("halt_reason", "")
        self.narrate("Halt cleared by hand. Trading may resume.", level="warn")

    # -- managing open positions ---------------------------------------------------------
    def manage_positions(self) -> None:
        for position in list(self.positions):
            try:
                self._manage_one(position)
            except Exception as exc:
                log.error("managing %s failed: %s", position.symbol, exc)
                self.narrate(f"Could not manage {position.symbol}: {exc}", level="error")

    def _manage_one(self, position: Position) -> None:
        price = self._price_of(position.symbol)
        if price <= 0:
            return

        analysis = self._analysis.get(position.symbol)
        structure = None
        try:
            structure = self.market.candles(position.symbol, doctrine.STRUCTURE_TF, 120)
            if analysis is None:
                trigger = self.market.candles(position.symbol, doctrine.TRIGGER_TF, 60)
                regime_candles = self.market.candles(position.symbol, doctrine.REGIME_TF, 250)
                regime, metrics = regime_module.classify(regime_candles)
                analysis = setups.analyse(position.symbol, trigger, structure, regime, metrics)
                self._analysis[position.symbol] = analysis
        except Exception as exc:
            log.warning("analysis unavailable for %s: %s", position.symbol, exc)

        position_rules.update_highest_close(position, structure, price)
        if analysis is not None:
            position_rules.count_regime_break(position, structure, analysis.indicators.get("ema21", 0.0))

        result = position_rules.plan(position, price, analysis)

        if result.stop_update is not None:
            position.stop = result.stop_update.stop
            if position.stop >= position.entry_price:
                position.breakeven_set = True
            position.last_note = result.stop_update.reason
            self.store.sync_position(position)
            self.narrate(f"{position.symbol}: {result.stop_update.reason}", level="info")

        if result.exit is None:
            if result.notes:
                position.last_note = result.notes[0]
                self.store.sync_position(position)
            return

        self._execute_exit(position, result.exit, price)

    def _execute_exit(self, position: Position, exit_plan, price: float) -> None:
        qty = position.qty * exit_plan.fraction
        instrument = self._instrument_map().get(position.symbol)
        if instrument is not None:
            qty = instrument.round_qty(qty) or qty
        if qty <= 0:
            return

        self.phase = "executing"
        self.broadcast()
        try:
            fill = self.broker.sell(position.symbol, qty)
        except VenueError as exc:
            self.narrate(f"Exit order for {position.symbol} was rejected: {exc}", level="error")
            return

        net = position_rules.realise(position, fill.qty, fill.price, fill.fee)
        self.store.save_fill(new_id("fill"), position.id, fill, exit_plan.kind)

        if exit_plan.kind == "SCALE_OUT":
            position.scaled_out = True
            position.last_note = exit_plan.reason
            self.store.sync_position(position)
            self.narrate(
                f"{position.symbol}: {exit_plan.reason} Booked {net:+.2f} {self.config.quote}.",
                level="win" if net > 0 else "info",
            )
            bus.publish("position_update", position.to_dict(price))
            return

        self._finalise(position, fill.price, exit_plan.kind, reason_text=exit_plan.reason, net=net)

    def _finalise(self, position: Position, price: float, kind: str, reason_text: str = "", net: float | None = None) -> None:
        """Close out the engine's record of a position and update the streak counters."""
        if net is None and position.qty > 0:
            net = position_rules.realise(position, position.qty, price, 0.0)
        position.qty = 0.0

        r_multiple = position.realized_pnl / position.risk_amount if position.risk_amount else 0.0
        self.store.close_position(position, price, kind, r_multiple)
        if position in self.positions:
            self.positions.remove(position)
        self._analysis.pop(position.symbol, None)

        won = position.realized_pnl > 0
        if kind in {"STOP", "CATASTROPHE"} or not won:
            cooldowns = self.store.get("cooldowns", {}) or {}
            cooldowns[position.symbol] = now_ms() + doctrine.SYMBOL_COOLDOWN_HOURS * 3_600_000
            self.store.put("cooldowns", cooldowns)

        state = self.account_state()
        if state.consecutive_losses >= doctrine.CONSECUTIVE_LOSS_LIMIT:
            until = now_ms() + doctrine.CONSECUTIVE_LOSS_COOLDOWN_HOURS * 3_600_000
            self.store.put("loss_cooldown_until", until)
            self.narrate(
                f"{state.consecutive_losses} losses in a row. Standing down for "
                f"{doctrine.CONSECUTIVE_LOSS_COOLDOWN_HOURS}h — the regime has probably changed.",
                level="warn",
            )

        self._persist_sim_balances()
        summary = reason_text or kind.replace("_", " ").lower()
        self.narrate(
            f"Closed {position.symbol} — {summary} "
            f"Result {position.realized_pnl:+.2f} {self.config.quote} ({r_multiple:+.2f}R).",
            level="win" if won else "loss",
        )
        bus.publish("position_closed", {"symbol": position.symbol, "pnl": position.realized_pnl})

    # -- hunting -------------------------------------------------------------------------
    def hunt(self) -> None:
        try:
            candidates, rows = self.scanner.scan()
        except Exception as exc:
            self.narrate(f"Scan failed: {exc}", level="error")
            return

        self._scan_rows = rows
        for candidate in candidates:
            self._analysis[candidate.symbol] = candidate.analysis
        bus.publish("scan", rows)

        state = self.account_state()
        blocks = self.risk.gate_entries(state, now_ms())

        if self.paused:
            blocks = ["paused by hand"] + blocks

        if blocks:
            self.narrate(
                f"Looked at {len(rows)} markets. Not taking anything: {blocks[0]}.",
                level="info",
            )
            return

        if not candidates:
            tradeable = sum(1 for row in rows if row["tradeable"])
            self.narrate(
                f"Looked at {len(rows)} markets, {tradeable} in a regime worth trading. "
                f"Nothing met the bar. Holding cash.",
                level="info",
            )
            return

        for candidate in candidates[:2]:   # at most two judgements per scan, for cost
            if self._consider(candidate, state):
                break

    def _consider(self, candidate: Candidate, state: AccountState) -> bool:
        """Run one candidate through the rails, the decision layer, then the rails again."""
        instrument = self._instrument_map().get(candidate.symbol)
        pre = self.risk.evaluate(candidate, state, now_ms(), instrument=instrument)

        if not pre.allowed:
            self.narrate(
                f"{candidate.symbol} scored {candidate.score:.0f} on a {_label(candidate.setup)} "
                f"but the rules say no: {pre.reasons[0]}",
                level="info",
            )
            self._record_decision(candidate, None, pre, blocked_by=pre.reasons[0])
            return False

        if not self.brain.should_ask(candidate):
            return False

        self.phase = "deciding"
        self.broadcast()
        self.narrate(
            f"{candidate.symbol} — {_label(candidate.setup)} scoring {candidate.score:.0f}. "
            f"Thinking it over.",
            level="think",
        )

        verdict = self.brain.judge_entry(
            candidate, state, self.store.closed_positions(8), self._scan_rows
        )

        if not verdict.go:
            self.narrate(
                f"Passing on {candidate.symbol}. {verdict.thesis or verdict.invalidation}",
                level="pass",
            )
            self._record_decision(candidate, verdict, pre, blocked_by="declined")
            return False

        final = self.risk.evaluate(
            candidate, state, now_ms(),
            size_multiplier=verdict.size_multiplier,
            stop_override=verdict.stop_override,
            instrument=instrument,
        )
        if not final.allowed or final.sizing is None:
            reason = final.reasons[0] if final.reasons else "sizing failed"
            self.narrate(f"Wanted {candidate.symbol} but the rules blocked it: {reason}", level="warn")
            self._record_decision(candidate, verdict, final, blocked_by=reason)
            return False

        return self._enter(candidate, verdict, final)

    def _enter(self, candidate: Candidate, verdict, risk_verdict) -> bool:
        sizing = risk_verdict.sizing
        self.phase = "executing"
        self.broadcast()
        self.narrate(
            f"Buying {candidate.symbol} — {sizing.quote_amount:.2f} {self.config.quote}, "
            f"risking {sizing.risk_amount:.2f}. {verdict.thesis}",
            level="entry",
        )

        try:
            fill = self.broker.buy(candidate.symbol, sizing.quote_amount)
        except VenueError as exc:
            self.narrate(f"Entry for {candidate.symbol} was rejected: {exc}", level="error")
            self._record_decision(candidate, verdict, risk_verdict, blocked_by=f"order rejected: {exc}")
            return False

        if fill.qty <= 0:
            self.narrate(f"{candidate.symbol} order returned no fill. Standing down.", level="error")
            return False

        # Risk is recomputed from the *actual* fill, not the intended one. Slippage between
        # the two is small but it is real, and the recorded risk should be the true one.
        stop = sizing.stop
        actual_risk = (fill.price - stop) * fill.qty

        position = Position(
            id=new_id("pos"), symbol=candidate.symbol, venue=self.broker.name, qty=fill.qty,
            entry_price=fill.price, stop=stop, target=candidate.target, initial_stop=stop,
            initial_qty=fill.qty, risk_amount=max(actual_risk, 0.01), opened_at=now_ms(),
            setup=candidate.setup, score=candidate.score, conviction=verdict.conviction,
            thesis=verdict.thesis, invalidation=verdict.invalidation,
            highest_close=fill.price, fees_paid=fill.fee, entry_fee=fill.fee,
        )
        self.positions.append(position)
        self.store.open_position(position)
        self.store.save_fill(new_id("fill"), position.id, fill, "ENTRY")
        self._record_decision(candidate, verdict, risk_verdict, position_id=position.id)
        self._persist_sim_balances()

        self.narrate(
            f"In {candidate.symbol} at {fill.price:.6f}. Stop {stop:.6f} "
            f"({sizing.stop_distance*100:.1f}% away), target {candidate.target:.6f}. "
            f"Worst case from here is {sizing.risk_amount:.2f} {self.config.quote}.",
            level="entry",
        )
        bus.publish("position_opened", position.to_dict(fill.price))
        return True

    def _record_decision(self, candidate, verdict, risk_verdict, blocked_by: str = "", position_id: str | None = None) -> None:
        self.store.save_decision(
            {
                "id": new_id("dec"),
                "ts": now_ms(),
                "symbol": candidate.symbol,
                "setup": candidate.setup,
                "score": candidate.score,
                "go": bool(verdict and verdict.go and not blocked_by),
                "conviction": verdict.conviction if verdict else 0,
                "size_multiplier": verdict.size_multiplier if verdict else 0.0,
                "source": verdict.source if verdict else "rules",
                "thesis": verdict.thesis if verdict else "",
                "invalidation": verdict.invalidation if verdict else "",
                "blocked_by": blocked_by,
                "candidate": candidate.to_dict(),
                "verdict": {
                    **(verdict.to_dict() if verdict else {}),
                    "risk": risk_verdict.to_dict() if risk_verdict else {},
                },
                "position_id": position_id,
            }
        )
        bus.publish("decision", {"symbol": candidate.symbol, "go": bool(position_id)})

    # -- manual controls -----------------------------------------------------------------
    def flatten(self, reason: str = "manual") -> int:
        """Sell everything at market, now. The one operation that must always work."""
        closed = 0
        for position in list(self.positions):
            try:
                fill = self.broker.sell(position.symbol, position.qty)
                net = position_rules.realise(position, fill.qty, fill.price, fill.fee)
                self.store.save_fill(new_id("fill"), position.id, fill, "FLATTEN")
                self._finalise(position, fill.price, "FLATTENED", reason_text=f"flattened ({reason}).", net=net)
            except Exception as exc:
                log.error("flatten failed for %s: %s", position.symbol, exc)
                self.narrate(f"Could not flatten {position.symbol}: {exc}", level="error")
                continue
            closed += 1
        if closed:
            self.narrate(f"Flattened {closed} position(s) — {reason}.", level="warn")
        return closed

    def close_one(self, position_id: str) -> bool:
        for position in list(self.positions):
            if position.id != position_id:
                continue
            try:
                fill = self.broker.sell(position.symbol, position.qty)
            except Exception as exc:
                self.narrate(f"Could not close {position.symbol}: {exc}", level="error")
                return False
            net = position_rules.realise(position, fill.qty, fill.price, fill.fee)
            self.store.save_fill(new_id("fill"), position.id, fill, "MANUAL")
            self._finalise(position, fill.price, "MANUAL", reason_text="closed by hand.", net=net)
            return True
        return False

    def _persist_sim_balances(self) -> None:
        if isinstance(self.broker, SimulatedBroker):
            self.store.put("sim_balances", self.broker.snapshot())

    # -- output --------------------------------------------------------------------------
    def narrate(self, message: str, level: str = "info") -> None:
        """Say what is happening, in plain language.

        This is the primary interface. A trading system you cannot follow in real time is
        one you will eventually turn off out of unease, so the narration is a feature and
        not logging decoration.
        """
        log.info("%s", message)
        # The stored row and the live event share one timestamp. They are the same line
        # arriving by two routes, and the browser needs them to look identical or it shows
        # every message twice — once from the socket, once from the backfill.
        ts = now_ms()
        self.store.log_event("narrate", message, level, ts=ts)
        bus.publish("narrate", {"message": message, "level": level}, ts=ts)

    def snapshot(self) -> dict:
        state = self.account_state()
        blocks = self.risk.gate_entries(state, now_ms())
        if self.paused:
            blocks = ["paused by hand"] + blocks

        return clean(
            {
                "phase": self.phase,
                "paused": self.paused,
                "halted": self.halted,
                "halt_reason": self.halt_reason,
                "cycle": self._cycle,
                "last_cycle_at": self.last_cycle_at,
                "started_at": self.started_at,
                "last_error": self.last_error,
                "synthetic": self.synthetic,
                "config": self.config.describe(),
                "doctrine": doctrine.summary(),
                "account": {
                    "equity": state.equity,
                    "cash": state.cash,
                    "starting_equity": self.config.starting_equity,
                    "quote": self.config.quote,
                    "realized_today": state.realized_today,
                    "realized_week": state.realized_week,
                    "peak_equity": state.peak_equity,
                    "drawdown": state.drawdown,
                    "open_risk": state.open_risk,
                    "heat_limit": doctrine.MAX_PORTFOLIO_HEAT * state.equity,
                    "entries_today": state.entries_today,
                    "consecutive_losses": state.consecutive_losses,
                    "total_return_pct": (state.equity / self.config.starting_equity - 1) * 100
                    if self.config.starting_equity
                    else 0.0,
                },
                "blocks": blocks,
                "positions": [
                    p.to_dict(self._price_of(p.symbol, p.entry_price)) for p in self.positions
                ],
                "scan": self._scan_rows,
                "brain": self.brain.stats(),
            }
        )

    def broadcast(self) -> None:
        bus.publish("state", self.snapshot())


def _label(setup: str) -> str:
    return {
        "TREND_PULLBACK": "trend pullback",
        "SQUEEZE_BREAKOUT": "squeeze breakout",
        "CAPITULATION_REVERSAL": "capitulation reversal",
    }.get(setup, setup.lower())
