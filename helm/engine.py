"""The engine loop.

Every cycle, in this order:

    reconcile -> record -> (once a day) read the close -> sell -> buy -> report -> broadcast

The loop runs every minute, because the account has to be watched and the dashboard kept
live, but the rule acts once a day: the first cycle after a daily candle closes reads it,
sells whatever has closed below its average, then buys whatever has closed above. Sales come
first, because the purchases are paid for with what the sales free up.

The loop is defensive about its own failures: an exception inside a cycle is logged and the
cycle is abandoned, never the loop. A failed order leaves the day undecided, and the whole
decision runs again a few minutes later — it is safe to repeat, because it only ever moves
the account towards what the closes say it should hold.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import doctrine, positions as books, strategy
from .bus import bus
from .models import Fill, Position, clean, new_id, now_ms
from .notify.telegram import TelegramNotifier
from .risk import AccountState, buy_blocks, utc_day_start
from .store import Store
from .strategy import Signal
from .venues.base import VenueError
from .venues.simulated import SimulatedBroker, SyntheticMarket

log = logging.getLogger("helm.engine")

DAY_MS = 86_400_000
RETRY_MS = 5 * 60_000            # after a failed order, before the decision runs again
GIVE_UP_MS = 3 * 3_600_000       # after the close, before deciding without a missing coin

# The bot's command menu, registered with Telegram at start so it can never drift from
# what the engine actually answers.
COMMANDS = {
    "status": "Equity, the four coins against their averages",
    "positions": "Coins held, with their results so far",
    "trades": "The last closed trades",
    "pause": "Stop purchases; sales still happen",
    "resume": "Let the rule buy again",
    "flatten": "Sell everything and pause (needs /confirm)",
    "kill": "Flatten and stop the engine (needs /confirm)",
    "confirm": "Approve the last /flatten or /kill",
    "help": "List these commands",
}


def _coin(symbol: str) -> str:
    return symbol[: -len(doctrine.QUOTE)] if symbol.endswith(doctrine.QUOTE) else symbol


def _day(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


class Engine:
    def __init__(self, config) -> None:
        self.config = config
        self.store = Store(config.db_path)

        self.market, self.broker, self.synthetic = self._build_venue()

        self.positions: list[Position] = []
        self.instruments: dict = {}
        # The verdicts the last decision acted on, kept across restarts for the dashboard.
        self.signals: dict[str, Signal] = {
            row["symbol"]: Signal(row["symbol"], row["day"], row["close"], row["average"])
            for row in (self.store.get("signals", []) or [])
        }

        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._cycle = 0
        self._prices: dict[str, float] = {}
        self._retry_at = 0
        # Failures already reported today, so a retry every few minutes does not page anyone
        # every few minutes.
        self._reported: set[str] = set()

        self.notifier = TelegramNotifier(
            config.telegram_token,
            config.telegram_chat_id,
            allowed_user_id=config.telegram_allowed_user_id,
            commands_enabled=config.telegram_commands,
            timeout=config.request_timeout,
        )
        # An armed destructive command, waiting on /confirm: (action name, expiry ms).
        self._pending_command: tuple[str, int] | None = None

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

        try:
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
            self.positions.append(
                Position(
                    id=row["id"], symbol=row["symbol"], venue=row["venue"], qty=row["qty"],
                    entry_price=row["entry_price"], initial_qty=row["initial_qty"],
                    opened_at=row["opened_at"], cost=row["cost"],
                    entry_close=row["entry_close"], entry_average=row["entry_average"],
                    fees_paid=row["fees_paid"], entry_fee=row["entry_fee"],
                    realized_pnl=row["realized_pnl"], last_note=row["last_note"],
                )
            )
        if self.positions:
            self.narrate(
                f"Resumed with {len(self.positions)} coin"
                f"{'s' if len(self.positions) > 1 else ''} held from the last run: "
                f"{', '.join(_coin(p.symbol) for p in self.positions)}.",
                level="info",
            )

    # -- lifecycle -----------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._restore_positions()
        self.notifier.start(self._telegram_handlers(), menu=COMMANDS)
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run, name="helm-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        self.notifier.stop()
        if self._thread:
            self._thread.join(timeout=10)

    def pause(self, why: str = "Paused by hand.") -> None:
        self.paused = True
        self.store.put("paused", True)
        self.narrate(f"{why} No purchases until resumed; a coin whose trend breaks is still sold.",
                     level="warn")

    def resume(self) -> None:
        self.paused = False
        self.store.put("paused", False)
        self.narrate("Resumed. The rule buys again from the next daily close.", level="info")

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
            state.equity, state.cash, state.invested, state.realized_today, len(self.positions)
        )

        self.daily()
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
        try:
            balances = self.broker.balances()
        except Exception as exc:
            log.warning("balance check failed: %s", exc)
            return

        self._check_orphans(balances)
        if not self.positions:
            return

        for position in list(self.positions):
            held = balances.get(self._base_of(position.symbol), 0.0)
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

    def _orphans(self, balances: dict[str, float]) -> list[tuple[str, float, float]]:
        """Coins in the account worth selling that no open position accounts for."""
        known = {position.symbol for position in self.positions}
        found = []
        for symbol, instrument in self._instrument_map().items():
            if symbol in known:
                continue
            held = balances.get(instrument.base, 0.0)
            if held <= 0:
                continue
            value = held * self._price_of(symbol, 0.0)
            # Ignore dust: leftover fractions below the minimum order size cannot be sold
            # anyway, so reporting them is noise.
            if value >= self._dust_floor(symbol):
                found.append((symbol, held, value))
        return found

    def _check_orphans(self, balances: dict[str, float]) -> None:
        """Flag coins sitting in the account that the engine has no record of buying.

        This is the symptom of losing the database — most likely a redeploy onto ephemeral
        storage. The holdings survive on the exchange, but the record of why they are there
        is gone.

        It deliberately warns rather than adopting. The account may legitimately hold coins
        the engine did not buy, and an engine that assumed every balance was its own would
        sell them. Whoever reads the alert decides. Until then the rule will not buy more of
        a coin the account already holds this way: that would be adding to it.
        """
        if not self._instrument_map():
            return
        warned = set(self.store.get("orphans_warned", []) or [])
        found = self._orphans(balances)

        for symbol, held, value in found:
            if symbol in warned:
                continue
            warned.add(symbol)
            self.narrate(
                f"{symbol}: the account holds {held:.8f} (~{value:.2f} {self.config.quote}) "
                f"that I have no record of buying, and I am not managing it. If the database "
                f"was lost on a redeploy, put it on a persistent volume. Sell it by hand, or "
                f"use /flatten, if it should not be there.",
                level="error",
            )

        # Forget symbols that are no longer orphaned, so a recurrence warns again.
        self.store.put("orphans_warned", sorted(warned & {row[0] for row in found}))

    def _instrument_map(self) -> dict:
        if not self.instruments:
            try:
                self.instruments = self.market.instruments()
            except Exception as exc:
                log.warning("instrument load failed: %s", exc)
        return self.instruments

    def _base_of(self, symbol: str) -> str:
        instrument = self._instrument_map().get(symbol)
        return instrument.base if instrument else symbol.replace(self.config.quote, "")

    def _dust_floor(self, symbol: str) -> float:
        """The value below which a holding is dust: too small to sell as an order of its own."""
        instrument = self._instrument_map().get(symbol)
        return max(doctrine.MIN_NOTIONAL, instrument.min_notional if instrument else 0.0)

    # -- account -------------------------------------------------------------------------
    def account_state(self) -> AccountState:
        synced = True
        try:
            balances = self.broker.balances()
        except Exception as exc:
            log.warning("balances unavailable: %s", exc)
            balances, synced = {}, False

        cash = balances.get(self.config.quote, 0.0)
        holdings = 0.0
        for position in self.positions:
            holdings += position.qty * self._price_of(position.symbol, position.entry_price)
        equity = cash + holdings
        if equity <= 0:
            equity = self.config.starting_equity

        return AccountState(
            equity=equity,
            cash=cash,
            positions=self.positions,
            realized_today=self.store.realized_since(utc_day_start().timestamp() * 1000),
            peak_equity=float(self.store.get("equity_peak", 0.0) or 0.0),
            halted=self.halted,
            halt_reason=self.halt_reason,
            synced=synced,
        )

    def _check_drawdown_halt(self, equity: float) -> None:
        """Doctrine section 6, measured once a day on the equity the decision sees.

        The peak is the highest equity any decision has seen — open positions marked to the
        close, as the rule was tested. A drop that big means the market is doing something the
        rule has never met or the engine is broken, and a human should look before it buys.
        """
        peak = max(float(self.store.get("equity_peak", 0.0) or 0.0), equity)
        self.store.put("equity_peak", peak)
        drawdown = 1 - equity / peak if peak > 0 else 0.0
        if not self.halted and drawdown >= doctrine.MAX_DRAWDOWN_HALT:
            self.halt(
                f"equity is {drawdown*100:.1f}% below its peak of {peak:.2f}, past the "
                f"{doctrine.MAX_DRAWDOWN_HALT*100:.0f}% hard limit"
            )

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        self.store.put("halted", True)
        self.store.put("halt_reason", reason)
        self.narrate(
            f"HALTED — {reason}. No purchases until a human clears it; sales still happen.",
            level="error",
        )

    def clear_halt(self) -> None:
        """Clearing the halt is a human accepting the account as it now stands.

        So the peak starts again from today's equity. Left where it was, the next decision
        would measure the same drop against the same peak and halt again at once — a halt no
        human could ever clear.
        """
        self.halted = False
        self.halt_reason = ""
        self.store.put("halted", False)
        self.store.put("halt_reason", "")
        self.store.put("equity_peak", self.account_state().equity)
        self.narrate("Halt cleared by hand. The rule buys again from the next daily close.",
                     level="warn")

    # -- the daily decision --------------------------------------------------------------
    def _last_close(self) -> int:
        """Open time of the most recent daily candle that has closed."""
        return int((utc_day_start() - timedelta(days=1)).timestamp() * 1000)

    def daily(self) -> None:
        """Run the day's decision, once, as soon as the day it rests on has closed."""
        day = self._last_close()
        if int(self.store.get("decided_day", 0) or 0) >= day or now_ms() < self._retry_at:
            return

        signals, missing, unknown = self._read_signals(day)
        if missing:
            # Usually the exchange has not published the new candle yet, a few seconds after
            # midnight. Try again next cycle, quietly — unless it has been hours.
            if now_ms() - (day + DAY_MS) < GIVE_UP_MS:
                log.info("waiting for the %s close: %s", _day(day), "; ".join(missing))
                return
            self._report_failure(
                "close",
                f"No {_day(day)} close for {', '.join(missing)} after "
                f"{GIVE_UP_MS // 3_600_000} hours. Deciding without "
                f"{'it' if len(missing) == 1 else 'them'}: a coin held stays held, and none "
                f"is bought.",
            )

        self.phase = "deciding"
        self.broadcast()
        self.signals = {**self.signals, **signals}
        self.store.put("signals", [signal.to_dict() for signal in self.signals.values()])

        state = self.account_state()
        if not state.synced:
            # Deciding blind would size from no cash at all and skip the day's purchases.
            self._report_failure("account", "Could not read the account for the daily decision. "
                                            "Trying again shortly.")
            self._retry_at = now_ms() + RETRY_MS
            return
        equity = state.equity                   # before any sale: the figure the rule was tested on
        self._check_drawdown_halt(equity)

        outcomes: dict[str, tuple[str, str, str | None]] = {}
        failed = False

        # Sales first: they pay for the purchases.
        held = {position.symbol for position in self.positions}
        for symbol in strategy.exits(signals, held):
            position = next(p for p in self.positions if p.symbol == symbol)
            done, note = self._sell(position, signals[symbol])
            outcomes[symbol] = ("SELL" if done else "SELL_FAILED", note, position.id)
            failed |= not done

        try:
            balances = self.broker.balances()
        except Exception as exc:
            # Cash is unknown, so nothing can be sized. Record what was sold and come back.
            log.warning("balances unavailable after the sales: %s", exc)
            self._record(day, signals, outcomes)
            self._retry_at = now_ms() + RETRY_MS
            return
        state = self.account_state()
        blocks = buy_blocks(state, self.paused)
        held = {position.symbol for position in self.positions}
        holding_already = {row[0] for row in self._orphans(balances)}
        minimums = {symbol: self._dust_floor(symbol) for symbol in doctrine.SYMBOLS}

        for entry in strategy.entries(signals, held, equity, balances.get(self.config.quote, 0.0), minimums):
            signal = signals[entry.symbol]
            if blocks:
                outcomes[entry.symbol] = ("STAY_OUT", f"Above its average, but not buying: {blocks[0]}.", None)
            elif entry.symbol in holding_already:
                outcomes[entry.symbol] = (
                    "STAY_OUT",
                    f"Above its average, but the account already holds {_coin(entry.symbol)} "
                    f"I have no record of. Buying more would be adding to it.",
                    None,
                )
            elif entry.skipped:
                outcomes[entry.symbol] = ("STAY_OUT", f"Above its average, but {entry.skipped}.", None)
            else:
                position_id, note = self._buy(entry, signal)
                outcomes[entry.symbol] = ("BUY" if position_id else "BUY_FAILED", note, position_id)
                failed |= position_id is None

        self._record(day, signals, outcomes)

        if failed:
            self._retry_at = now_ms() + RETRY_MS
            return
        self.store.put("decided_day", day)
        self._reported.clear()
        self._daily_report(day, signals, unknown, outcomes)

    def _read_signals(self, day: int) -> tuple[dict[str, Signal], list[str], list[str]]:
        """Verdicts for the closed day. Returns (signals, missing, unknown).

        Missing coins have no candle for that day yet, or the fetch failed: worth waiting for.
        Unknown coins have fewer closed days than the average needs: nothing to wait for, and
        no verdict to act on.
        """
        signals: dict[str, Signal] = {}
        missing: list[str] = []
        unknown: list[str] = []
        for symbol in doctrine.SYMBOLS:
            try:
                # A few spare: a live feed drops the still-forming candle from what it returns.
                candles = self.market.candles(symbol, doctrine.TREND_TF, doctrine.SMA_DAYS + 5)
            except Exception as exc:
                missing.append(f"{symbol} ({exc})")
                continue
            signal = strategy.read(candles)
            if signal is None:
                unknown.append(symbol)
            elif signal.day < day:
                missing.append(symbol)
            else:
                signals[symbol] = signal
        return signals, missing, unknown

    def _sell(self, position: Position, signal: Signal) -> tuple[bool, str]:
        """Sell a coin whose trend has broken. Returns (sold in full, what happened)."""
        self.phase = "executing"
        self.broadcast()
        why = (f"closed at {signal.close:,.6g}, {-signal.distance*100:.1f}% below its "
               f"{doctrine.SMA_DAYS}-day average of {signal.average:,.6g}")
        try:
            fill, net, closed = self._sell_out(position, 1.0)
        except VenueError as exc:
            self._report_failure(position.symbol, f"Could not sell {position.symbol}: {exc}. Trying again shortly.")
            return False, f"Sale rejected: {exc}"

        self.store.save_fill(new_id("fill"), position.id, fill, "TREND_OFF")
        if not closed:
            position.last_note = f"Trend broke; sold {fill.qty:.8f}, {position.qty:.8f} still to sell."
            self.store.sync_position(position)
            self._report_failure(position.symbol, f"{position.symbol}: the sale filled short — "
                                                  f"{position.qty:.8f} still held. Selling the rest shortly.")
            return False, position.last_note
        self._finalise(position, fill.price, "TREND_OFF", reason_text=f"it {why}.", net=net)
        return True, f"Sold: it {why}."

    def _buy(self, entry: strategy.Entry, signal: Signal) -> tuple[str | None, str]:
        """Buy a coin whose trend has turned on. Returns (position id or None, what happened)."""
        self.phase = "executing"
        self.broadcast()
        why = (f"closed at {signal.close:,.6g}, {signal.distance*100:.1f}% above its "
               f"{doctrine.SMA_DAYS}-day average of {signal.average:,.6g}")
        try:
            fill = self.broker.buy(entry.symbol, entry.amount)
        except VenueError as exc:
            self._report_failure(entry.symbol, f"Could not buy {entry.symbol}: {exc}. Trying again shortly.")
            return None, f"Purchase rejected: {exc}"
        if fill.qty <= 0:
            self._report_failure(entry.symbol, f"{entry.symbol}: the order returned no fill.")
            return None, "The order returned no fill."

        position = Position(
            id=new_id("pos"), symbol=entry.symbol, venue=self.broker.name, qty=fill.qty,
            entry_price=fill.price, initial_qty=fill.qty, opened_at=now_ms(),
            cost=fill.qty * fill.price + fill.fee, entry_close=signal.close,
            entry_average=signal.average, fees_paid=fill.fee, entry_fee=fill.fee,
            last_note=f"Bought: it {why}.",
        )
        self.positions.append(position)
        self.store.open_position(position)
        self.store.save_fill(new_id("fill"), position.id, fill, "TREND_ON")
        self._persist_sim_balances()

        self.narrate(
            f"Bought {entry.symbol} — {position.cost:.2f} {self.config.quote} at "
            f"{fill.price:,.6g}. It {why}. Held until a daily close below the average.",
            level="entry",
        )
        bus.publish("position_opened", position.to_dict(fill.price))
        return position.id, position.last_note

    def _report_failure(self, key: str, message: str) -> None:
        """Narrate a failure once a day; retries after that only reach the log."""
        if key in self._reported:
            log.warning("%s", message)
            return
        self._reported.add(key)
        self.narrate(message, level="error")

    def _record(self, day: int, signals: dict[str, Signal], outcomes: dict) -> None:
        for symbol, signal in signals.items():
            held = any(position.symbol == symbol for position in self.positions)
            if held:
                default = ("HOLD", "Above its average, and held." if signal.on else
                           "Below its average; the sale waits for the decision to run again.", None)
            else:
                default = ("STAY_OUT", "Below its average, and in cash." if not signal.on else
                           "Above its average; the purchase waits for the decision to run again.", None)
            action, note, position_id = outcomes.get(symbol, default)
            self.store.save_decision({
                "id": new_id("dec"), "ts": now_ms(), "day": day, "symbol": symbol,
                "close": signal.close, "average": signal.average, "distance": signal.distance,
                "on": signal.on, "action": action, "note": note, "position_id": position_id,
            })
        bus.publish("decision", {"day": day})

    def _daily_report(self, day: int, signals: dict[str, Signal], unknown: list[str], outcomes: dict) -> None:
        """One message a day, whether or not anything happened.

        A system that only speaks up when it trades is indistinguishable from one that has
        quietly died, and with this rule most days are correctly uneventful.
        """
        state = self.account_state()
        quote = self.config.quote
        lines = [f"Daily close — {_day(day)}", ""]
        for symbol in doctrine.SYMBOLS:
            signal = signals.get(symbol)
            if signal is None:
                lines.append(f"{_coin(symbol):4} no verdict"
                             + (f" (fewer than {doctrine.SMA_DAYS} days)" if symbol in unknown else ""))
                continue
            action = outcomes.get(symbol, ("", "", None))[0]
            held = any(position.symbol == symbol for position in self.positions)
            doing = {"BUY": "bought", "SELL": "sold"}.get(
                action, "held" if held else ("not bought" if signal.on else "in cash"))
            lines.append(f"{_coin(symbol):4} {'above' if signal.on else 'below'} average "
                         f"{signal.distance*100:+.1f}% · {doing}")
        lines += [
            "",
            f"Equity {state.equity:.2f} {quote} "
            f"({(state.equity / self.config.starting_equity - 1) * 100:+.1f}% overall)",
            f"Invested {state.invested:.2f} · cash {state.cash:.2f} · "
            f"drawdown {state.drawdown*100:.1f}% (halt at {doctrine.MAX_DRAWDOWN_HALT*100:.0f}%)",
        ]
        blocks = buy_blocks(state, self.paused)
        if blocks:
            lines += ["", f"Not buying: {blocks[0]}"]
        # Straight to the notifier rather than through narrate(): the report is a message to
        # you, not an event in the engine's own story.
        self.notifier.notify("\n".join(lines), "warn" if blocks else "entry")
        self.narrate(f"Read the {_day(day)} close. " + "; ".join(
            f"{_coin(s)} {'above' if sig.on else 'below'} ({sig.distance*100:+.1f}%)"
            for s, sig in signals.items()) + ".", level="info")

    # -- selling -------------------------------------------------------------------------
    def _sell_out(self, position: Position, fraction: float) -> tuple[Fill, float, bool]:
        """Sell `fraction` of a position and book it. Returns (fill, net, books closed).

        A final exit does two more things. It also sells any dust earlier trades left in
        the same coin — only an amount too small to sell on its own, so a holding the engine
        did not buy is never touched. And it writes off whatever still cannot be sold, so
        the booked result matches the cash. A remainder too big to be dust is a short fill,
        not a sliver: the position stays open and the decision sells the rest on its retry.
        """
        final = fraction >= 1.0
        qty = position.qty * fraction
        price = self._price_of(position.symbol, position.entry_price)
        if final:
            try:
                held = self.broker.balances().get(self._base_of(position.symbol), 0.0)
            except Exception as exc:
                log.warning("balance check before exit failed: %s", exc)
                held = position.qty
            extra = held - position.qty
            if 0 < extra * price < self._dust_floor(position.symbol):
                qty += extra
            if held > 0:
                # Never ask for more than the account holds: the exchange rejects the whole
                # order, and a rejected exit is a sale that never happens.
                qty = min(qty, held)
        instrument = self._instrument_map().get(position.symbol)
        if instrument is not None:
            qty = instrument.round_qty(qty) or qty
        if qty <= 0:
            raise VenueError(f"{position.symbol}: nothing to sell")

        fill = self.broker.sell(position.symbol, qty)
        own = min(fill.qty, position.qty)
        own_fee = fill.fee * (own / fill.qty) if fill.qty > 0 else fill.fee
        net = books.realise(position, own, fill.price, own_fee)
        if fill.qty > own:
            net += books.book_swept(position, fill.qty - own, fill.price, fill.fee - own_fee)

        if not final or position.qty * price >= self._dust_floor(position.symbol):
            return fill, net, False
        net += books.write_off(position)
        return fill, net, True

    def _finalise(self, position: Position, price: float, kind: str, reason_text: str = "", net: float | None = None) -> None:
        """Close out the engine's record of a position."""
        if net is None and position.qty > 0:
            books.realise(position, position.qty, price, 0.0)
        position.qty = 0.0
        if reason_text:
            position.last_note = reason_text

        self.store.close_position(position, price, kind)
        if position in self.positions:
            self.positions.remove(position)
        self._persist_sim_balances()

        pct = position.realized_pnl / position.cost * 100 if position.cost else 0.0
        summary = reason_text or kind.replace("_", " ").lower() + "."
        self.narrate(
            f"Sold {position.symbol} — {summary} "
            f"Result {position.realized_pnl:+.2f} {self.config.quote} ({pct:+.1f}%).",
            level="win" if position.realized_pnl > 0 else "loss",
        )
        bus.publish("position_closed", {"symbol": position.symbol, "pnl": position.realized_pnl})

    # -- manual controls -----------------------------------------------------------------
    def flatten(self, reason: str = "manual") -> int:
        """Sell everything at market, now, and pause. The one operation that must always work.

        Pausing is part of it: without that, the rule would buy straight back in at the
        next daily close whatever was still above its average.
        """
        closed = 0
        for position in list(self.positions):
            try:
                fill, net, done = self._sell_out(position, 1.0)
                self.store.save_fill(new_id("fill"), position.id, fill, "FLATTEN")
                if not done:
                    self.store.sync_position(position)
                    raise VenueError(f"filled short, {position.qty:.8f} still held")
                self._finalise(position, fill.price, "FLATTENED", reason_text=f"flattened ({reason}).", net=net)
            except Exception as exc:
                log.error("flatten failed for %s: %s", position.symbol, exc)
                self.narrate(f"Could not flatten {position.symbol}: {exc}", level="error")
                continue
            closed += 1
        if closed:
            self.narrate(f"Flattened {closed} position(s) — {reason}.", level="warn")
        if not self.paused:
            self.pause("Paused after flattening, so the rule does not buy straight back in.")
        return closed

    def close_one(self, position_id: str) -> bool:
        for position in list(self.positions):
            if position.id != position_id:
                continue
            try:
                fill, net, closed = self._sell_out(position, 1.0)
            except Exception as exc:
                self.narrate(f"Could not close {position.symbol}: {exc}", level="error")
                return False
            self.store.save_fill(new_id("fill"), position.id, fill, "MANUAL")
            if not closed:
                self.store.sync_position(position)
                self.narrate(f"{position.symbol} filled short — {position.qty:.8f} still held.", level="error")
                return False
            self._finalise(
                position, fill.price, "MANUAL", net=net,
                reason_text="closed by hand. The rule buys it back at the next daily close if "
                            "it is still above its average — pause to stop that.",
            )
            return True
        return False

    def _persist_sim_balances(self) -> None:
        if isinstance(self.broker, SimulatedBroker):
            self.store.put("sim_balances", self.broker.snapshot())

    def next_decision_at(self) -> int:
        return int((utc_day_start() + timedelta(days=1)).timestamp() * 1000)

    def _until_next(self) -> str:
        if int(self.store.get("decided_day", 0) or 0) < self._last_close():
            return "due now"
        minutes = max(0, (self.next_decision_at() - now_ms()) // 60_000)
        return f"in {minutes // 60}h {minutes % 60:02d}m"

    def coins(self) -> list[dict]:
        """Each coin against its average: the last decision's verdict, and where it is now."""
        rows = []
        for symbol in doctrine.SYMBOLS:
            signal = self.signals.get(symbol)
            price = self._price_of(symbol)
            rows.append({
                "symbol": symbol,
                "price": price,
                "held": any(position.symbol == symbol for position in self.positions),
                **(signal.to_dict() if signal else {"day": None, "close": None, "average": None,
                                                    "distance": None, "on": None}),
                "now_distance": price / signal.average - 1 if signal and signal.average and price else None,
            })
        return rows

    # -- telegram ------------------------------------------------------------------------
    def _telegram_handlers(self) -> dict:
        """Commands the bot will answer.

        Read-only queries run immediately. The two that sell — flatten and kill — arm a
        confirmation instead, because a fat-thumbed tap on a phone should not be able to
        empty the account. Note what is deliberately absent: there is no command to buy or
        to relax a limit. The bot can stop this system; it cannot aim it.
        """

        def status() -> str:
            state = self.account_state()
            blocks = buy_blocks(state, self.paused)
            quote = self.config.quote
            lines = [
                f"<b>{'LIVE' if self.config.live else 'DRY RUN'}</b>"
                + ("  ⚠️ HALTED" if self.halted else ""),
                f"Equity   {state.equity:.2f} {quote}"
                f"  ({(state.equity / self.config.starting_equity - 1) * 100:+.1f}%)",
                f"Invested {state.invested:.2f} · cash {state.cash:.2f}",
                f"Drawdown {state.drawdown * 100:.1f}%  (halt at {doctrine.MAX_DRAWDOWN_HALT*100:.0f}%)",
                f"Next decision {self._until_next()} (00:00 UTC)",
                "",
            ]
            for row in self.coins():
                if row["on"] is None:
                    lines.append(f"{_coin(row['symbol']):4} no verdict yet")
                    continue
                lines.append(
                    f"{_coin(row['symbol']):4} {'above' if row['on'] else 'below'} "
                    f"{row['distance'] * 100:+.1f}% at the close"
                    + (f", {row['now_distance'] * 100:+.1f}% now" if row["now_distance"] is not None else "")
                    + (" · held" if row["held"] else "")
                )
            if self.synthetic:
                lines.append("\n⚠️ generated prices — not a real market")
            lines.append("")
            lines.append("Following the rule." if not blocks else "Not buying: " + blocks[0])
            return "\n".join(lines)

        def positions() -> str:
            if not self.positions:
                return "Nothing held — all cash."
            quote = self.config.quote
            out = []
            for position in self.positions:
                price = self._price_of(position.symbol, position.entry_price)
                row = position.to_dict(price)
                signal = self.signals.get(position.symbol)
                out.append(
                    f"<b>{position.symbol}</b>  {row['unrealized']:+.2f} {quote}"
                    f"  ({row['unrealized_pct']:+.1f}%)\n"
                    f"  bought {position.entry_price:,.6g}  now {price:,.6g}\n"
                    + (f"  average {signal.average:,.6g} — sold on a close below it\n" if signal else "")
                    + f"  held {(now_ms() - position.opened_at) / DAY_MS:.0f} days"
                )
            return "\n\n".join(out)

        def trades() -> str:
            rows = self.store.closed_positions(5)
            if not rows:
                return "No closed trades yet."
            quote = self.config.quote
            return "\n".join(
                f"{_day(row['closed_at'] or 0)}  {row['symbol']}  "
                f"{row.get('realized_pnl') or 0:+.2f} {quote} ({row.get('return_pct') or 0:+.1f}%)"
                f"  {(row.get('exit_reason') or '').replace('_', ' ').lower()}"
                for row in rows
            )

        def pause() -> str:
            self.pause("Paused from Telegram.")
            return "Paused. No purchases; a coin whose trend breaks is still sold. /resume to undo."

        def resume() -> str:
            if self.halted:
                return f"Still halted: {self.halt_reason}\nClear it from the dashboard first."
            self.resume()
            return "Resumed — the rule buys again from the next daily close."

        def arm(action: str, prompt: str) -> str:
            self._pending_command = (action, now_ms() + 120_000)
            return f"{prompt}\n\nSend /confirm within 2 minutes."

        def flatten() -> str:
            if not self.positions:
                return "Nothing to flatten — nothing held."
            return arm(
                "flatten",
                f"This sells all {len(self.positions)} coin(s) at market, now, and pauses "
                f"purchases until you /resume.",
            )

        def kill() -> str:
            return arm(
                "kill",
                "This flattens everything AND stops the engine. "
                "Restarting it needs access to the host.",
            )

        def confirm() -> str:
            pending = self._pending_command
            self._pending_command = None
            if pending is None:
                return "Nothing waiting to be confirmed."
            action, expires = pending
            if now_ms() > expires:
                return "That confirmation expired. Send the command again."
            if action == "flatten":
                closed = self.flatten("confirmed from telegram")
                return f"Flattened {closed} position(s). Paused — /resume to let the rule buy again."
            if action == "kill":
                closed = self.flatten("kill switch from telegram")
                self.halt("kill switch used from Telegram")
                self._stop_flag.set()
                return f"Flattened {closed} position(s). Engine stopped."
            return "Unknown pending action."

        def help_text() -> str:
            return "<b>Helm</b>\n" + "\n".join(
                f"/{name} — {description}" for name, description in COMMANDS.items()
            )

        return {
            "status": status, "positions": positions, "trades": trades,
            "pause": pause, "resume": resume, "flatten": flatten,
            "kill": kill, "confirm": confirm, "help": help_text, "start": help_text,
        }

    # -- output --------------------------------------------------------------------------
    def narrate(self, message: str, level: str = "info") -> None:
        """Say what is happening, in plain language.

        This is the primary interface. A trading system you cannot follow is one you will
        eventually turn off out of unease, so the narration is a feature and not logging
        decoration.
        """
        log.info("%s", message)
        # The stored row and the live event share one timestamp. They are the same line
        # arriving by two routes, and the browser needs them to look identical or it shows
        # every message twice — once from the socket, once from the backfill.
        ts = now_ms()
        self.store.log_event("narrate", message, level, ts=ts)
        bus.publish("narrate", {"message": message, "level": level}, ts=ts)
        # Queued and level-filtered inside the notifier — this call cannot block the cycle
        # or raise, however badly Telegram is behaving.
        self.notifier.notify(message, level)

    def snapshot(self) -> dict:
        state = self.account_state()
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
                    "invested": state.invested,
                    "starting_equity": self.config.starting_equity,
                    "quote": self.config.quote,
                    "realized_today": state.realized_today,
                    "peak_equity": state.peak_equity,
                    "drawdown": state.drawdown,
                    "total_return_pct": (state.equity / self.config.starting_equity - 1) * 100
                    if self.config.starting_equity
                    else 0.0,
                },
                "blocks": buy_blocks(state, self.paused),
                "positions": [
                    p.to_dict(self._price_of(p.symbol, p.entry_price)) for p in self.positions
                ],
                "coins": self.coins(),
                "decided_day": self.store.get("decided_day"),
                "next_decision_at": self.next_decision_at(),
            }
        )

    def broadcast(self) -> None:
        bus.publish("state", self.snapshot())
