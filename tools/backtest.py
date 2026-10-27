"""Backtest — the doctrine's rule replayed against real Binance history.

Runs the real `Engine.cycle()` once a day, a minute after midnight UTC on a simulated
clock: the real rule, sizing, sales-before-purchases, the 70% halt, the books, fees and
slippage. Only the market is substituted — recorded daily candles instead of a live feed.

WHAT THIS MEASURES
    Whether the code trades the rule as tested: the result, the worst drop, how often it
    trades and how much of the time it is invested, against simply holding the same coins.
    The research behind doctrine v2 used a separate script; this is the engine itself, so
    the two should agree, and a gap between them is a bug in one or the other.

WHAT IT DOES NOT
    Fills are at the day's opening price plus the simulated broker's slippage and the taker
    fee — the moment the live engine acts, just after the close. That is a model of a fill,
    not a fill, and on a crash day the real one can be worse.

    One period is one sample. Crypto has a short history and only a few full cycles; a
    result is evidence about that stretch of market, not a forecast, and must never be
    quoted as one.

    python tools/backtest.py                              the doctrine's coins, all history
    python tools/backtest.py --start 2026-04-30           from a date
    python tools/backtest.py --symbols BTCUSDT,ETHUSDT    an experiment, labelled as one
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import soak  # noqa: E402  — the simulated clock and database handling are shared
from helm import doctrine  # noqa: E402
from helm.config import Config  # noqa: E402
from helm.engine import GIVE_UP_MS  # noqa: E402
from helm.models import Candles, Instrument, Quote  # noqa: E402
from helm.venues.base import VenueError  # noqa: E402

BINANCE = "https://api.binance.com"
MINUTE = 60_000
DAY = 86_400_000
TF_MS = {"1d": DAY}

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
    """Recorded daily candles, served as of the simulated clock.

    Only days that had closed by then are visible — the same view a live feed gives, which
    drops the still-forming candle. The price is the day in progress's opening price: what
    the market was at the moment the engine acts. Anything looser lets the future leak into
    a decision, and a backtest with a leak in it is worse than none, because it is convincing.
    """

    name = "historical"

    def __init__(self, series: dict[str, Candles], instruments: dict[str, Instrument]) -> None:
        self._series = series
        self._instruments = instruments

    def _closed(self, symbol: str) -> int:
        bars = self._series[symbol]
        return int(np.searchsorted(bars.open_time + DAY, soak.CLOCK[0], side="right"))

    def instruments(self) -> dict[str, Instrument]:
        return self._instruments

    def quotes(self) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for symbol, bars in self._series.items():
            k = self._closed(symbol)
            if k == 0:
                continue
            forming = k < len(bars) and bars.open_time[k] <= soak.CLOCK[0]
            price = float(bars.open[k]) if forming else float(bars.close[k - 1])
            out[symbol] = Quote(symbol=symbol, price=price)
        return out

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        bars = self._series.get(symbol)
        if bars is None or timeframe != doctrine.TREND_TF:
            raise VenueError(f"no {timeframe} history for {symbol}")
        k = self._closed(symbol)
        if k == 0:
            raise VenueError(f"no closed days for {symbol} yet")
        lo = max(0, k - limit)
        return Candles(symbol=symbol, timeframe=timeframe, open_time=bars.open_time[lo:k],
                       open=bars.open[lo:k], high=bars.high[lo:k], low=bars.low[lo:k],
                       close=bars.close[lo:k], volume=bars.volume[lo:k])


def _date(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None,
                        help="first day to trade, YYYY-MM-DD. Defaults to the first day every "
                             f"coin has {doctrine.SMA_DAYS} closed days behind it")
    parser.add_argument("--end", default=None,
                        help="last day of the window, YYYY-MM-DD (exclusive). Defaults to today "
                             "UTC, so every day replayed has closed")
    parser.add_argument("--symbols", default=None,
                        help="experiment: trade these instead of the doctrine's coins, a "
                             "1/n share each, e.g. BTCUSDT,ETHUSDT for history before EUR pairs")
    parser.add_argument("--equity", type=float, default=100.0)
    parser.add_argument("--db", default="/tmp/helm-backtest/backtest.db")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="experiment: replace a numeric doctrine constant for this run "
                             "only, e.g. --set SMA_DAYS=150. Repeatable. Exists only here — "
                             "the engine itself has no way to change a rule")
    parser.add_argument("--fee", type=float, default=None,
                        help="taker fee as a fraction, e.g. 0.00075 for Binance with fees paid "
                             "in BNB. Defaults to HELM_TAKER_FEE (0.001)")
    args = parser.parse_args()

    experiments = []
    if args.symbols:
        symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
        quotes = {next((q for q in ("EUR", "USDT", "USDC") if s.endswith(q)), "") for s in symbols}
        if len(quotes) != 1 or "" in quotes:
            raise SystemExit("--symbols must all share one quote currency: EUR, USDT or USDC")
        doctrine.SYMBOLS, doctrine.QUOTE = symbols, quotes.pop()
        doctrine.ENTRY_FRACTION = 1 / len(symbols)
        experiments.append(f"coins {', '.join(symbols)}, 1/{len(symbols)} of the account each")
    for item in args.set:
        name, _, value = item.partition("=")
        current = getattr(doctrine, name, None)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise SystemExit(f"--set {item}: doctrine has no numeric constant called {name}")
        setattr(doctrine, name, type(current)(float(value)))
        experiments.append(f"{name} {current} -> {getattr(doctrine, name)}")
    symbols, quote = doctrine.SYMBOLS, doctrine.QUOTE

    end = _ms(args.end) if args.end else int(
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    print(f"loading daily history for {', '.join(symbols)}")
    series = {symbol: klines(symbol, "1d", 0, end) for symbol in symbols}
    for symbol, bars in series.items():
        print(f"  {symbol:10s} {len(bars):5d} days from {_date(bars.open_time[0])}")

    earliest = max(int(bars.open_time[doctrine.SMA_DAYS]) for bars in series.values())
    start = max(_ms(args.start), earliest) if args.start else earliest
    if start >= end:
        raise SystemExit("nothing to replay: the window ends before the rule has enough history")

    soak.clear_database(Path(args.db))
    import helm.engine as engine_module
    from helm.venues.binance import BinanceVenue

    soak.CLOCK[0] = start
    soak.install_clock()

    config = Config()
    config.db_path = args.db
    config.venue = "synthetic"        # builds the simulated broker; the market is swapped below
    config.quote = quote
    config.starting_equity = args.equity
    if args.fee is not None:
        config.taker_fee = args.fee
    config.telegram_token = ""        # a backtest must never page anyone

    venue = BinanceVenue(config).instruments()
    market = HistoricalMarket(series, {s: venue[s] for s in symbols if s in venue})

    engine = engine_module.Engine(config)
    engine.market = engine.broker.market = market
    engine.synthetic = False

    began = time.time()
    days = list(range(start, end, DAY))
    curve: list[tuple[int, float, float]] = []      # (day, equity, invested)
    late = 0
    halted_at = None
    print(f"\nreplaying {len(days):,} days, {_date(start)} to {_date(end - DAY)}\n")

    for i, day in enumerate(days):
        soak.CLOCK[0] = day + MINUTE
        engine.cycle()
        if int(engine.store.get("decided_day", 0) or 0) < day - DAY:
            # A coin's candle is missing from the record. Live, the engine waits a few hours
            # and then decides without it; so does the replay.
            soak.CLOCK[0] = day + GIVE_UP_MS + MINUTE
            engine.cycle()
            late += 1
        state = engine.account_state()
        curve.append((day, state.equity, state.invested))
        if engine.halted and halted_at is None:
            halted_at = (day, engine.halt_reason)
        if i % max(1, len(days) // 10) == 0:
            print(f"  {_date(day)}  equity {state.equity:9.2f}  held {len(engine.positions)}  "
                  f"closed {len(engine.store.all_closed()):3d}")

    # The last day's close: where the account stands at the end of the window.
    soak.CLOCK[0] = end
    engine.refresh_prices()
    final = engine.account_state()
    curve.append((end, final.equity, final.invested))

    # ---- report --------------------------------------------------------------------------
    equity = np.array([row[1] for row in curve])
    peak = np.maximum.accumulate(equity)
    drawdowns = 1 - equity / peak
    worst = int(np.argmax(drawdowns))
    exposure = float(np.mean([row[2] / row[1] for row in curve if row[1] > 0]))
    years = (end - start) / DAY / 365.25

    closed = engine.store.all_closed()
    fills = engine.store.fills(1_000_000)
    fees = sum(row["fee"] for row in fills)

    holds = {}
    for symbol, bars in series.items():
        first = int(np.searchsorted(bars.open_time, start))
        bought = (1 - config.taker_fee) / (bars.open[first] * (1 + engine.broker.slippage))
        holds[symbol] = bought * bars.close[-1]
    hold_end = args.equity * sum(holds.values()) / len(holds)
    hold_curve = sum(
        series[s].close[np.searchsorted(series[s].open_time, start):] /
        series[s].open[np.searchsorted(series[s].open_time, start)] for s in symbols
    )
    hold_dd = float(np.max(1 - hold_curve / np.maximum.accumulate(hold_curve)))

    print("\n" + "=" * 66)
    print(f"BACKTEST   {', '.join(symbols)}")
    print(f"           {_date(start)} to {_date(end - DAY)} ({len(days)} days, {years:.1f} years)")
    print("=" * 66)
    print(f"  doctrine v{doctrine.VERSION}: hold while the daily close is above the "
          f"{doctrine.SMA_DAYS}-day average.")
    print(f"  Fills at the day's open, {engine.broker.slippage * 1e4:.0f}bps slippage, "
          f"{config.taker_fee * 100:.3f}% fee.")
    for change in experiments:
        print(f"  EXPERIMENT — not the doctrine: {change}")

    print(f"\n  starting equity    {args.equity:10.2f} {quote}")
    print(f"  ending equity      {final.equity:10.2f} {quote}   ({(final.equity / args.equity - 1) * 100:+.0f}%, "
          f"{((final.equity / args.equity) ** (1 / years) - 1) * 100:+.1f}% a year)")
    print(f"  worst drop         {drawdowns[worst] * 100:9.0f}%    (to {_date(curve[worst][0])})")
    print(f"  hard halt          {'never' if not halted_at else _date(halted_at[0]) + ' — ' + halted_at[1]}")
    print(f"  invested           {exposure * 100:9.0f}%    of the time, on average")
    print(f"  buy and hold       {hold_end:10.2f} {quote}   ({(hold_end / args.equity - 1) * 100:+.0f}%), "
          f"worst drop {hold_dd * 100:.0f}%")
    if late:
        print(f"  days decided late  {late:10d}    (a coin's candle missing from the record)")

    print("\n" + "-" * 66)
    trades = len(closed) + len(engine.positions)
    print(f"  trades             {trades:5d}   ({trades / max(1, len(days) / 30.44):.1f} a month)")
    print(f"  fees paid          {fees:8.2f} {quote}")
    if closed:
        returns = [row["return_pct"] or 0.0 for row in closed]
        held = sorted(((row["closed_at"] or 0) - (row["opened_at"] or 0)) / DAY for row in closed)
        wins = [value for value in returns if value > 0]
        print(f"  closed trades      {len(closed):5d}   {len(wins) / len(closed) * 100:.0f}% won, "
              f"average {sum(returns) / len(returns):+.1f}%")
        print(f"  best / worst       {max(returns):+.0f}% / {min(returns):+.0f}%")
        print(f"  median hold        {held[len(held) // 2]:5.0f} days")

    print("\n  BY YEAR            rule      hold")
    by_year: dict[str, list[int]] = {}
    for index, (day, _, _) in enumerate(curve):
        by_year.setdefault(_date(day)[:4], []).append(index)
    for year, indexes in by_year.items():
        before = indexes[0] - 1 if indexes[0] > 0 else 0
        rule = equity[indexes[-1]] / equity[before] - 1
        hold = hold_curve[min(indexes[-1], len(hold_curve) - 1)] / hold_curve[before] - 1
        print(f"    {year}           {rule * 100:+6.0f}%   {hold * 100:+6.0f}%")

    print(f"\n  ran in {time.time() - began:.0f}s\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
