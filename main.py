"""Helm — entry point.

    python main.py           run the engine and dashboard
    python main.py doctor    check the environment without trading anything
"""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> int:
    from helm.config import load

    config = load()
    configure_logging(config.log_level)

    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from helm.doctor import run

        return run(config)

    import uvicorn

    from helm.api import create_app
    from helm.engine import Engine

    engine = Engine(config)
    banner(config, engine)
    uvicorn.run(
        create_app(engine),
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        access_log=False,
    )
    return 0


def banner(config, engine) -> None:
    mode = "LIVE — REAL MONEY" if config.live else "DRY RUN — simulated fills"
    print("\n" + "=" * 62)
    print("  HELM")
    print("=" * 62)
    print(f"  Mode        {mode}")
    print(f"  Venue       {engine.market.name}" + ("  (generated prices)" if engine.synthetic else ""))
    print(f"  Quote       {config.quote}")
    print(f"  Decisions   {config.model if config.brain_enabled else 'DISABLED — no API key, nothing will be bought'}")
    print(f"  Dashboard   http://{config.host}:{config.port}")
    if config.mode == "live" and not config.live:
        print("\n  !! HELM_MODE=live but this is running DRY, because:")
        if not config.live_ack:
            print("     - I_UNDERSTAND_REAL_MONEY is not set.")
        if not config.ui_token:
            print("     - HELM_UI_TOKEN is not set, so the control API is unguarded.")
            print("       Anyone who reaches this port could flatten the account.")
        print("     Fix the above before real orders are sent.")
    elif config.control_api_is_open:
        print("\n  !  HELM_UI_TOKEN is not set — pause/flatten/close are unauthenticated.")
        print("     Fine on a laptop. Set one before exposing this to a network.")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
