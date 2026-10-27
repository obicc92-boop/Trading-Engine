"""Backtest — the doctrine's rules replayed against real Binance history.

Runs the real `Engine.cycle()` over months of actual prices on a simulated clock: the real
scanner, risk rails, sizing, exit ladder, fees and slippage. Two things are substituted —
the market (recorded candles instead of a live feed) and the decision layer.

WHAT THIS MEASURES
    Whether the mechanical rules have an edge on the markets you would trade: how often they
    trade, the win rate, expectancy in R, the drawdown, and how all of that moves over time.

WHAT IT DOES NOT
    The decision layer. Every candidate the rails allow is approved at full size, so this is
    the rules with no judgement on top. The model can only veto or shrink, so the live system
    takes a subset of these trades. Replaying it would cost one paid API call per candidate,
    and a model asked about the past may already know how it ended.

    Fills are at the one-minute close, plus the simulated broker's slippage and the taker fee.
    That matches how the live engine works — it watches its stops on a 60s loop rather than
    resting them on the exchange — but it is still a model of a fill, not a fill.

    One period is one sample. A result is evidence about that stretch of market, not a
    forecast, and must never be quoted as one.

    python tools/backtest.py --days 180
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import soak  # noqa: E402  — the simulated clock and database handling are shared
from helm import doctrine  # noqa: E402
from helm.analysis import setups  # noqa: E402
from helm.config import Config  # noqa: E402
from helm.models import Candles, Instrument, Quote, Verdict  # noqa: E402
from helm.venues.base import VenueError  # noqa: E402

BINANCE = "https://api.binance.com"
MINUTE = 60_000
TF_MS = {"1m": MINUTE, "15m": 15 * MINUTE, "1h": 60 * MINUTE, "4h": 240 * MINUTE}

# The doctrine's setups, by the name trades are recorded under, and their detectors.
SETUPS = {
    "TREND_PULLBACK": "_trend_pullback",
    "SQUEEZE_BREAKOUT": "_squeeze_breakout",
    "CAPITULATION_REVERSAL": "_capitulation_reversal",
}

# Closed bars of history needed before the first simulated minute. The engine asks for at
# most 250 on any timeframe; the margin keeps the first scan from running short.
WARMUP_BARS = {doctrine.REGIME_TF: 260, doctrine.STRUCTURE_TF: 260, doctrine.TRIGGER_TF: 130}

# Downloads are cached here (inside data/, so never committed). Files are named by the
# window they hold, and history does not change, so a cached file never goes stale.
CACHE = ROOT / "data" / "backtest"


def _from_cache(symbol: str, interval: str, start_ms: int, end_ms: int) -> np.ndarray | None:
    """Any cached download covering the window, cut down to it — so a shorter or shifted
    window inside one already fetched never goes back to the network."""
    for path in CACHE.glob(f"{symbol}_{interval}_*_*.npy"):
        held_from, held_to = (int(part) for part in path.stem.split("_")[-2:])
        if held_from <= start_ms and held_to >= end_ms:
            bars = np.load(path)
            return bars[(bars[:, 0] >= start_ms) & (bars[:, 0] < end_ms)]
    return None


def klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> Candles:
    """Every bar of `interval` opening in [start_ms, end_ms), from cache or Binance."""
    path = CACHE / f"{symbol}_{interval}_{start_ms}_{end_ms}.npy"
    bars = _from_cache(symbol, interval, start_ms, end_ms) if CACHE.exists() else None
    if bars is None:
        rows: list[list] = []
        cursor = start_ms
        with httpx.Client(base_url=BINANCE, timeout=30.0) as http:
            while cursor < end_ms:
                response = http.get("/api/v3/klines", params={
                    "symbol": symbol, "interval": interval,
                    "startTime": cursor, "endTime": end_ms - 1, "limit": 1000,
                })
                response.raise_for_status()
                batch = response.json()
                if not batch:
                    break
                rows.extend(batch)
                cursor = int(batch[-1][0]) + TF_MS[interval]
        if not rows:
            raise SystemExit(f"Binance returned no {interval} history for {symbol}")
        bars = np.array([[float(value) for value in row[:6]] for row in rows], dtype=float)
        CACHE.mkdir(parents=True, exist_ok=True)
        np.save(path, bars)
    return Candles(symbol=symbol, timeframe=interval, open_time=bars[:, 0], open=bars[:, 1],
                   high=bars[:, 2], low=bars[:, 3], close=bars[:, 4], volume=bars[:, 5])


class HistoricalMarket:
    """Recorded candles, served as of the simulated clock.

    Only bars that had closed by then are visible — the same view a live feed gives, which
    drops the still-forming bar. Anything looser lets the future leak into a decision, and a
    backtest with a leak in it is worse than none, because it is convincing.
    """

    name = "historical"

    def __init__(self, series: dict[str, dict[str, Candles]], instruments: dict[str, Instrument]) -> None:
        self._series = series
        self._instruments = instruments
        self._closes_at = {
            symbol: {tf: bars.open_time + TF_MS[tf] for tf, bars in frames.items()}
            for symbol, frames in series.items()
        }
        # 24h quote volume from one-minute bars, as a running sum so each quote is O(1).
        self._volume_sum = {
            symbol: np.concatenate(([0.0], np.cumsum(frames["1m"].close * frames["1m"].volume)))
            for symbol, frames in series.items()
        }

    def _closed(self, symbol: str, timeframe: str) -> int:
        return int(np.searchsorted(self._closes_at[symbol][timeframe], soak.CLOCK[0], side="right"))

    def instruments(self) -> dict[str, Instrument]:
        return self._instruments

    def quotes(self) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for symbol, frames in self._series.items():
            k = self._closed(symbol, "1m")
            if k == 0:
                continue
            minutes = frames["1m"]
            price = float(minutes.close[k - 1])
            day_ago = max(0, k - 1440)
            out[symbol] = Quote(
                symbol=symbol,
                price=price,
                quote_volume_24h=float(self._volume_sum[symbol][k] - self._volume_sum[symbol][day_ago]),
                change_24h_pct=(price / float(minutes.close[day_ago]) - 1) * 100,
            )
        return out

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        frames = self._series.get(symbol)
        if frames is None or timeframe not in frames:
            raise VenueError(f"no {timeframe} history for {symbol}")
        k = self._closed(symbol, timeframe)
        if k == 0:
            raise VenueError(f"no closed {timeframe} bars for {symbol} yet")
        bars, lo = frames[timeframe], max(0, k - limit)
        return Candles(symbol=symbol, timeframe=timeframe, open_time=bars.open_time[lo:k],
                       open=bars.open[lo:k], high=bars.high[lo:k], low=bars.low[lo:k],
                       close=bars.close[lo:k], volume=bars.volume[lo:k])

    def listing_age_days(self, symbol: str) -> float:
        return 9999.0


class RulesOnlyDesk:
    """Stands in for the decision layer: whatever the rails allow is taken at full size.

    The most generous desk possible, which is the point — it measures the rules alone. The
    real model can only remove trades from this set or make them smaller.
    """

    def __init__(self) -> None:
        self.asked = 0

    def __call__(self, candidate, state, recent, scan) -> Verdict:
        self.asked += 1
        return Verdict(go=True, conviction=int(candidate.score), size_multiplier=1.0,
                       stop_override=None,
                       thesis=f"Backtest: rules-only approval at score {candidate.score:.0f}.",
                       invalidation="", source="backtest")


def load_history(symbols: list[str], start: int, end: int) -> dict[str, dict[str, Candles]]:
    series: dict[str, dict[str, Candles]] = {}
    for symbol in symbols:
        frames = {}
        for tf, bars in WARMUP_BARS.items():
            frames[tf] = klines(symbol, tf, start - bars * TF_MS[tf], end)
        # One day of minutes before the start, so the first quote has a 24h volume.
        frames["1m"] = klines(symbol, "1m", start - 1440 * MINUTE, end)
        series[symbol] = frames
        print(f"  {symbol:10s} {len(frames['1m']):,} one-minute bars")
    return series


def _date(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _summary(rows: list[dict]) -> str:
    if not rows:
        return "   0"
    r = [row["r_multiple"] or 0.0 for row in rows]
    wins = sum(1 for value in r if value > 0)
    return f"{len(rows):4d}   {wins / len(rows) * 100:3.0f}%   {sum(r) / len(r):+.2f}R"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--end", default=None,
                        help="last day of the window, YYYY-MM-DD (exclusive). Defaults to today "
                             "UTC, so the window is whole days of closed bars")
    parser.add_argument("--symbols", default="BTCEUR,ETHEUR,SOLEUR,XRPEUR")
    parser.add_argument("--quote", default="EUR")
    parser.add_argument("--equity", type=float, default=100.0)
    parser.add_argument("--scan-minutes", type=int, default=5,
                        help="minutes between scans; the live engine scans every 5 cycles of 60s")
    parser.add_argument("--db", default="/tmp/helm-backtest/backtest.db")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="experiment: replace a numeric doctrine constant for this run "
                             "only, e.g. --set TIME_STOP_HOURS=48. Repeatable. Exists only "
                             "here — the engine itself has no way to change a rule")
    parser.add_argument("--without", action="append", default=[], choices=sorted(SETUPS),
                        help="experiment: switch one of the doctrine's setups off for this run")
    parser.add_argument("--fee", type=float, default=None,
                        help="taker fee as a fraction, e.g. 0.00075 for Binance with fees paid "
                             "in BNB. Defaults to HELM_TAKER_FEE (0.001)")
    args = parser.parse_args()

    overrides = []
    for item in args.set:
        name, _, value = item.partition("=")
        current = getattr(doctrine, name, None)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise SystemExit(f"--set {item}: doctrine has no numeric constant called {name}")
        setattr(doctrine, name, type(current)(float(value)))
        overrides.append(f"{name} {current} -> {getattr(doctrine, name)}")
    for name in args.without:
        # detect() looks each detector up by name on every call, so this takes effect.
        setattr(setups, SETUPS[name], lambda *args, **kwargs: None)
        overrides.append(f"{name} switched off")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.end:
        end_day = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        end_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = int(end_day.timestamp() * 1000)
    start = int((end_day - timedelta(days=args.days)).timestamp() * 1000)

    print(f"loading {args.days} days of history, {_date(start)} to {_date(end)}")
    series = load_history(symbols, start, end)

    soak.clear_database(Path(args.db))
    import helm.engine as engine_module
    from helm.venues.binance import BinanceVenue

    soak.CLOCK[0] = start
    soak.install_clock()

    config = Config()
    config.db_path = args.db
    config.venue = "synthetic"        # builds the simulated broker; the market is swapped below
    config.quote = args.quote
    config.symbols = symbols
    config.starting_equity = args.equity
    if args.fee is not None:
        config.taker_fee = args.fee
    config.scan_every_cycles = 1      # scan cadence is set by the loop below instead
    config.anthropic_key = ""         # the desk below decides; nothing may reach the real API
    config.telegram_token = ""        # and a backtest must never page anyone

    venue = BinanceVenue(config).instruments()
    instruments = {symbol: venue[symbol] for symbol in symbols if symbol in venue}
    market = HistoricalMarket(series, instruments)

    engine = engine_module.Engine(config)
    engine.market = engine.scanner.market = engine.broker.market = market
    engine.synthetic = False
    desk = RulesOnlyDesk()
    engine.brain.judge_entry = desk

    began = time.time()
    halted_at = None
    max_dd = 0.0
    total_minutes = (end - start) // MINUTE
    print(f"\nreplaying {total_minutes:,} minutes, scanning every {args.scan_minutes}\n")

    for i in range(1, total_minutes + 1):
        soak.CLOCK[0] = start + i * MINUTE
        if i % args.scan_minutes == 0:
            engine.cycle()
            # The engine's own measure — from the realized peak, as the hard halt reads it —
            # rather than one rebuilt from the equity curve, which would disagree with it.
            max_dd = max(max_dd, engine.account_state().drawdown)
        elif engine.positions:
            # Between scans the live engine still runs a cycle every minute, and for an open
            # position that is what matters: the stop is checked against the latest price.
            engine.refresh_prices()
            engine.manage_positions()

        if engine.halted and halted_at is None:
            halted_at = (soak.CLOCK[0], engine.halt_reason)

        if i % (total_minutes // 10) == 0:
            state = engine.account_state()
            print(f"  {_date(soak.CLOCK[0])}  equity {state.equity:7.2f}  open {len(engine.positions)}  "
                  f"closed {len(engine.store.all_closed()):3d}  dd {state.drawdown * 100:4.1f}%")

    # ---- report --------------------------------------------------------------------------
    closed = engine.store.all_closed()
    state = engine.account_state()

    # Coins left in the account that no open position accounts for: the slivers exits
    # cannot sell. Equity leaves them out, so they are shown on their own — a few cents is
    # normal, and anything more means exits are leaving money behind again.
    balances = engine.broker.balances()
    in_positions: dict[str, float] = {}
    for position in engine.positions:
        base = engine._base_of(position.symbol)
        in_positions[base] = in_positions.get(base, 0.0) + position.qty
    dust = sum(
        (balances.get(engine._base_of(symbol), 0.0) - in_positions.get(engine._base_of(symbol), 0.0))
        * engine._price_of(symbol)
        for symbol in symbols
    )

    holds = {}
    for symbol, frames in series.items():
        minutes = frames["1m"]
        first = minutes.close[int(np.searchsorted(minutes.open_time, start))]
        holds[symbol] = (minutes.close[-1] / first - 1) * 100

    print("\n" + "=" * 64)
    print(f"BACKTEST   {', '.join(symbols)}")
    print(f"           {_date(start)} to {_date(end)} ({args.days} days)")
    print("=" * 64)
    print("  rules only — no decision layer. Fills at the 1m close, "
          f"{engine.broker.slippage * 1e4:.0f}bps slippage, {config.taker_fee * 100:.3f}% fee.")
    for change in overrides:
        print(f"  EXPERIMENT — not the doctrine: {change}")

    print(f"\n  starting equity    {args.equity:8.2f} {args.quote}")
    print(f"  ending equity      {state.equity:8.2f} {args.quote}   "
          f"({(state.equity / args.equity - 1) * 100:+.1f}%)")
    print(f"  left as dust       {dust:8.2f} {args.quote}   (unsellable slivers, outside equity)")
    print(f"  max drawdown       {max_dd * 100:7.1f}%")
    print(f"  hard halt          {'never' if not halted_at else _date(halted_at[0]) + ' — ' + halted_at[1]}")
    print(f"  buy and hold       " + "   ".join(f"{s[:-len(args.quote)]} {v:+.0f}%" for s, v in holds.items())
          + f"   (equal weight {sum(holds.values()) / len(holds):+.1f}%)")

    print("\n" + "-" * 64)
    if not closed:
        print("  no trades closed in this window")
        print(f"\n  ran in {time.time() - began:.0f}s\n")
        return 0

    r = [row["r_multiple"] or 0.0 for row in closed]
    wins = [value for value in r if value > 0]
    losses = [value for value in r if value <= 0]
    gross_win = sum(row["realized_pnl"] for row in closed if row["realized_pnl"] > 0)
    gross_loss = -sum(row["realized_pnl"] for row in closed if row["realized_pnl"] <= 0)
    fees = sum(row["fees_paid"] or 0.0 for row in closed)
    holding = sorted(((row["closed_at"] or 0) - (row["opened_at"] or 0)) / 3_600_000 for row in closed)

    print(f"  trades             {len(closed):5d}   ({len(closed) / (args.days / 7):.1f} a week)")
    print(f"  win rate           {len(wins) / len(r) * 100:5.0f}%")
    print(f"  expectancy         {sum(r) / len(r):+.2f}R a trade")
    print(f"  profit factor      {gross_win / gross_loss if gross_loss else float('inf'):5.2f}")
    print(f"  average win/loss   {(sum(wins) / len(wins) if wins else 0):+.2f}R / "
          f"{(sum(losses) / len(losses) if losses else 0):+.2f}R")
    print(f"  best / worst       {max(r):+.2f}R / {min(r):+.2f}R")
    print(f"  fees paid          {fees:6.2f} {args.quote}   ({fees / args.equity * 100:.1f}% of starting equity)")
    print(f"  median hold        {holding[len(holding) // 2]:5.1f}h")
    print(f"  sent to the desk   {desk.asked:5d}   (each one a paid model call when live)")

    for title, key in (("BY SETUP", "setup"), ("BY SYMBOL", "symbol")):
        print(f"\n  {title:18s} trades   win   expectancy")
        for value in sorted({row[key] for row in closed}):
            print(f"    {value:22s} {_summary([row for row in closed if row[key] == value])}")

    print("\n  BY MONTH           trades   win   expectancy      P&L")
    for month in sorted({_date(row["closed_at"])[:7] for row in closed}):
        rows = [row for row in closed if _date(row["closed_at"]).startswith(month)]
        pnl = sum(row["realized_pnl"] for row in rows)
        print(f"    {month:22s} {_summary(rows)}   {pnl:+7.2f}")

    print("\n  EXIT REASONS")
    reasons: dict[str, list[float]] = {}
    for row in closed:
        reasons.setdefault(row["exit_reason"], []).append(row["r_multiple"] or 0.0)
    for reason, values in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
        print(f"    {reason.lower().replace('_', ' '):22s} {len(values):4d}   avg {sum(values) / len(values):+.2f}R")

    print(f"\n  ran in {time.time() - began:.0f}s\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
