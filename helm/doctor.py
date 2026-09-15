"""Preflight check.

Run this on the machine that will actually trade, before trusting it with money. It answers
one question per line: can this box reach the venue, are the credentials real, does the
balance exist, and would an order be accepted. It never places one.
"""

from __future__ import annotations

from pathlib import Path

from . import doctrine

class _Skip(Exception):
    """Control flow for the synthetic venue, which has no network checks to run."""


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

    # The control API waves everything through when no token is set. Harmless bound to a
    # laptop, and an open liquidate button on anything with a domain.
    if config.control_api_is_open:
        if config.mode == "live":
            print(f"{CROSS} HELM_UI_TOKEN is unset — the control API is unauthenticated")
            print("      live mode refuses to start without it: anyone who reached the port")
            print("      could POST /api/control/flatten and sell the account")
            problems += 1
        else:
            print(f"{WARN} HELM_UI_TOKEN is unset — pause/flatten/close are unauthenticated")
            print("      set one before this is reachable from anywhere but localhost")
            warnings += 1

    # -- venue ---------------------------------------------------------------------------
    market = None
    synthetic = config.venue == "synthetic"
    if synthetic:
        # An explicit request for generated prices. Say so bluntly — it is a demo posture,
        # not a degraded one, and it must never be mistaken for a live configuration.
        # Storage and notification checks still run below: they matter either way.
        print(f"{WARN} venue is 'synthetic' — prices are generated, nothing here is a real market")
        print("      set HELM_VENUE=binance (or alpaca) to trade an actual venue")
        warnings += 1

    try:
        if synthetic:
            raise _Skip()
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
    except _Skip:
        pass
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
    if synthetic:
        pass
    elif config.live:
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
        open_count = len(store.load_open_positions())
        print(f"{TICK} storage writable at {config.db_path} ({open_count} open position(s) on record)")

        # The database carries the stop and thesis for every open position. On a platform
        # with an ephemeral filesystem, a redeploy silently drops all of it and leaves the
        # coins on the exchange with nothing managing them.
        resolved = Path(config.db_path).resolve()
        ephemeral_roots = ("/app", "/tmp", "/workspace", "/usr/src")
        if any(str(resolved).startswith(root) for root in ephemeral_roots):
            print(f"{WARN} {resolved} looks like container-local storage")
            print("      mount a volume and point HELM_DB at it, or a redeploy loses every")
            print("      open position's stop while the coins stay on the exchange")
            warnings += 1
    except Exception as exc:
        print(f"{CROSS} storage failed: {exc}")
        problems += 1

    # -- notifications ---------------------------------------------------------------------
    if config.telegram_enabled:
        try:
            import httpx

            response = httpx.post(
                f"https://api.telegram.org/bot{config.telegram_token}/sendMessage",
                json={"chat_id": config.telegram_chat_id,
                      "text": "Helm preflight: notifications are working."},
                timeout=15.0,
            )
            if response.status_code < 400:
                print(f"{TICK} telegram reachable — a test message was sent")
            else:
                print(f"{CROSS} telegram rejected the message: {response.text[:160]}")
                problems += 1
        except Exception as exc:
            print(f"{CROSS} telegram unreachable: {exc}")
            problems += 1
    else:
        print(f"{WARN} telegram not configured — the engine will trade silently")
        warnings += 1

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
