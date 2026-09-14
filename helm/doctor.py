"""Preflight check.

Run this on the machine that will actually trade, before trusting it with money. It answers
one question per line: can this box reach the venue, are the credentials real, does the
balance exist, and would an order be accepted. It never places one.
"""

from __future__ import annotations

from . import doctrine

TICK = "  \033[32m✓\033[0m"
CROSS = "  \033[31m✗\033[0m"
WARN = "  \033[33m!\033[0m"


def run(config) -> int:
    print("\nHelm preflight\n" + "-" * 52)
    problems = 0
    warnings = 0

    mode = "LIVE (real money)" if config.live else "dry run (simulated fills)"
    print(f"{TICK} mode: {mode}")
    if config.mode == "live" and not config.live_ack:
        print(f"{WARN} HELM_MODE=live but I_UNDERSTAND_REAL_MONEY is unset — running dry")
        warnings += 1

    # -- venue ---------------------------------------------------------------------------
    market = None
    if config.venue == "synthetic":
        # An explicit request for generated prices. Say so bluntly — it is a demo posture,
        # not a degraded one, and it must never be mistaken for a live configuration.
        print(f"{WARN} venue is 'synthetic' — prices are generated, nothing here is a real market")
        print("      set HELM_VENUE=binance (or alpaca) to trade an actual venue")
        warnings += 1
        return _finish(problems, warnings)

    try:
        if config.venue == "alpaca":
            from .venues.alpaca import AlpacaVenue

            market = AlpacaVenue(config)
        else:
            from .venues.binance import BinanceVenue

            market = BinanceVenue(config)
            market.sync_time()
        instruments = market.instruments()
        print(f"{TICK} {config.venue} reachable — {len(instruments)} {config.quote} pairs tradeable")
        if not instruments:
            print(f"{CROSS} no {config.quote} pairs found — is HELM_QUOTE right for this venue?")
            problems += 1
    except Exception as exc:
        print(f"{CROSS} {config.venue} unreachable: {exc}")
        print("      the engine will fall back to generated prices and trade nothing real")
        problems += 1

    # -- market data ---------------------------------------------------------------------
    if market is not None:
        try:
            quotes = market.quotes()
            liquid = sum(1 for q in quotes.values() if q.quote_volume_24h >= doctrine.MIN_QUOTE_VOLUME_24H)
            print(f"{TICK} market data: {len(quotes)} quotes, {liquid} pass the liquidity floor")
            if liquid == 0:
                print(f"{WARN} nothing meets the {doctrine.MIN_QUOTE_VOLUME_24H:,.0f} 24h volume minimum")
                warnings += 1
        except Exception as exc:
            print(f"{CROSS} quotes failed: {exc}")
            problems += 1

        for timeframe in (doctrine.REGIME_TF, doctrine.STRUCTURE_TF, doctrine.TRIGGER_TF):
            try:
                probe = next(iter(market.instruments()))
                candles = market.candles(probe, timeframe, 60)
                print(f"{TICK} candles {timeframe}: {len(candles)} bars for {probe}")
            except Exception as exc:
                print(f"{CROSS} candles {timeframe} failed: {exc}")
                problems += 1
                break

    # -- credentials ---------------------------------------------------------------------
    if config.live:
        try:
            balances = market.balances()
            cash = balances.get(config.quote, 0.0)
            print(f"{TICK} account reachable — {cash:.2f} {config.quote} free")
            if cash < doctrine.MIN_NOTIONAL * 2:
                print(f"{WARN} {cash:.2f} {config.quote} is below what two minimum positions need")
                warnings += 1
        except Exception as exc:
            print(f"{CROSS} account check failed: {exc}")
            problems += 1
    else:
        keys = "present" if (config.binance_key or config.alpaca_key) else "absent"
        print(f"{WARN} not live — exchange credentials {keys}, no order path tested")
        warnings += 1

    # -- decision layer ------------------------------------------------------------------
    if config.brain_enabled:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=config.anthropic_key, timeout=30.0)
            client.messages.create(
                model=config.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            )
            print(f"{TICK} decision layer: {config.model} responding")
        except Exception as exc:
            print(f"{CROSS} decision layer unreachable: {exc}")
            print("      without it every candidate is declined and nothing will be bought")
            problems += 1
    else:
        print(f"{CROSS} no ANTHROPIC_API_KEY — every candidate will be declined")
        problems += 1

    # -- storage -------------------------------------------------------------------------
    try:
        from .store import Store

        store = Store(config.db_path)
        store.put("doctor", "ok")
        print(f"{TICK} storage writable at {config.db_path}")
    except Exception as exc:
        print(f"{CROSS} storage failed: {exc}")
        problems += 1

    if config.kill_file().exists():
        print(f"{WARN} kill file present at {config.kill_file()} — the engine will stop immediately")
        warnings += 1

    return _finish(problems, warnings)


def _finish(problems: int, warnings: int) -> int:
    print("-" * 52)
    if problems:
        print(f"{problems} problem(s), {warnings} warning(s). Fix the problems before trading.\n")
        return 1
    print(f"Ready. {warnings} warning(s).\n")
    return 0
