"""HTTP + WebSocket surface, and the static UI.

Reads are plain JSON snapshots; the live feed is a WebSocket that replays recent history on
connect so a browser opened mid-session isn't staring at a blank page waiting for the next
event.

Control endpoints (pause, flatten, close) are POST-only and, when `HELM_UI_TOKEN` is set,
token-guarded. They move real money, so they are never reachable by a GET that something
could prefetch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from . import doctrine
from .bus import bus

log = logging.getLogger("helm.api")

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
DOCTRINE_PATH = Path(__file__).resolve().parent.parent / "DOCTRINE.md"


def create_app(engine, start_engine: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # The bus has to learn the server's loop before the engine thread publishes into it.
        bus.bind(asyncio.get_running_loop())
        if start_engine:
            engine.start()
        yield
        engine.stop()

    app = FastAPI(title="Helm", docs_url=None, redoc_url=None, lifespan=lifespan)

    def guard(token: str | None) -> None:
        expected = engine.config.ui_token
        if expected and token != expected:
            raise HTTPException(status_code=401, detail="bad or missing token")

    # -- pages ---------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = UI_DIR / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>Helm</h1><p>UI assets are missing.</p>", status_code=500)
        return HTMLResponse(page.read_text())

    @app.get("/{asset}.css")
    async def css(asset: str) -> FileResponse:
        return _asset(f"{asset}.css", "text/css")

    @app.get("/{asset}.js")
    async def js(asset: str) -> FileResponse:
        return _asset(f"{asset}.js", "application/javascript")

    def _asset(name: str, media_type: str) -> FileResponse:
        path = (UI_DIR / name).resolve()
        # Never serve outside the UI directory, whatever the path segment contains.
        if not path.is_file() or UI_DIR.resolve() not in path.parents:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(path, media_type=media_type)

    # -- read ----------------------------------------------------------------------------
    @app.get("/api/state")
    async def state(x_helm_token: str | None = Header(default=None)) -> JSONResponse:
        guard(x_helm_token)
        return JSONResponse(engine.snapshot())

    @app.get("/api/feed")
    async def feed(
        limit: int = Query(default=80, le=400),
        x_helm_token: str | None = Header(default=None),
    ) -> JSONResponse:
        guard(x_helm_token)
        return JSONResponse(engine.store.events(limit))

    @app.get("/api/decisions")
    async def decisions(
        limit: int = Query(default=40, le=200),
        x_helm_token: str | None = Header(default=None),
    ) -> JSONResponse:
        guard(x_helm_token)
        return JSONResponse(engine.store.decisions(limit))

    @app.get("/api/trades")
    async def trades(
        limit: int = Query(default=60, le=300),
        x_helm_token: str | None = Header(default=None),
    ) -> JSONResponse:
        guard(x_helm_token)
        return JSONResponse(engine.store.closed_positions(limit))

    @app.get("/api/equity")
    async def equity(
        limit: int = Query(default=400, le=2000),
        x_helm_token: str | None = Header(default=None),
    ) -> JSONResponse:
        guard(x_helm_token)
        return JSONResponse(engine.store.equity_curve(limit))

    @app.get("/api/stats")
    async def stats(x_helm_token: str | None = Header(default=None)) -> JSONResponse:
        guard(x_helm_token)
        return JSONResponse(_performance(engine))

    @app.get("/api/doctrine")
    async def doctrine_json(x_helm_token: str | None = Header(default=None)) -> JSONResponse:
        guard(x_helm_token)
        return JSONResponse(doctrine.summary())

    @app.get("/api/doctrine.md", response_class=PlainTextResponse)
    async def doctrine_text() -> PlainTextResponse:
        if not DOCTRINE_PATH.exists():
            return PlainTextResponse("Doctrine file is missing.", status_code=404)
        return PlainTextResponse(DOCTRINE_PATH.read_text())

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> PlainTextResponse:
        # Deliberately unauthenticated and boring — it's for the platform, not for people.
        return PlainTextResponse("ok" if not engine.halted else "halted")

    # -- control -------------------------------------------------------------------------
    @app.post("/api/control/pause")
    async def pause(x_helm_token: str | None = Header(default=None)) -> dict:
        guard(x_helm_token)
        engine.pause()
        return {"paused": True}

    @app.post("/api/control/resume")
    async def resume(x_helm_token: str | None = Header(default=None)) -> dict:
        guard(x_helm_token)
        engine.resume()
        return {"paused": False}

    @app.post("/api/control/flatten")
    async def flatten(x_helm_token: str | None = Header(default=None)) -> dict:
        guard(x_helm_token)
        closed = await asyncio.to_thread(engine.flatten, "requested from the dashboard")
        return {"closed": closed}

    @app.post("/api/control/close/{position_id}")
    async def close(position_id: str, x_helm_token: str | None = Header(default=None)) -> dict:
        guard(x_helm_token)
        ok = await asyncio.to_thread(engine.close_one, position_id)
        if not ok:
            raise HTTPException(status_code=404, detail="no such open position")
        return {"closed": True}

    @app.post("/api/control/clear-halt")
    async def clear_halt(x_helm_token: str | None = Header(default=None)) -> dict:
        guard(x_helm_token)
        engine.clear_halt()
        return {"halted": False}

    # -- live feed -----------------------------------------------------------------------
    @app.websocket("/ws")
    async def websocket(socket: WebSocket) -> None:
        expected = engine.config.ui_token
        if expected and socket.query_params.get("token") != expected:
            await socket.close(code=4401)
            return

        await socket.accept()
        queue = bus.subscribe()
        try:
            # Replay first, so a tab opened between cycles has something to show.
            await socket.send_text(json.dumps({"kind": "state", "data": engine.snapshot()}))
            for event in bus.recent(60):
                await socket.send_text(json.dumps(event))
            while True:
                event = await queue.get()
                await socket.send_text(json.dumps(event))
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        except Exception as exc:
            log.debug("websocket ended: %s", exc)
        finally:
            bus.unsubscribe(queue)
            with contextlib.suppress(Exception):
                await socket.close()

    return app


def _performance(engine) -> dict:
    """Trade statistics, computed from closed positions only.

    Open positions are excluded on purpose: counting unrealised gains as performance is the
    most common way a trading record flatters itself. With a trend rule that matters more
    than usual — most of the result sits in a few long holds, open until the trend breaks.
    """
    closed = engine.store.all_closed()
    if not closed:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "avg_return_pct": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0, "best": 0.0,
            "worst": 0.0, "total_pnl": 0.0, "fees": 0.0, "avg_days_held": 0.0, "by_symbol": {},
        }

    pnls = [row.get("realized_pnl") or 0.0 for row in closed]
    returns = [row.get("return_pct") or 0.0 for row in closed]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    days = [((row.get("closed_at") or 0) - (row.get("opened_at") or 0)) / 86_400_000 for row in closed]

    by_symbol: dict[str, dict] = {}
    for row in closed:
        bucket = by_symbol.setdefault(row["symbol"], {"trades": 0, "pnl": 0.0, "wins": 0})
        bucket["trades"] += 1
        bucket["pnl"] += row.get("realized_pnl") or 0.0
        bucket["wins"] += 1 if (row.get("realized_pnl") or 0) > 0 else 0

    gross_loss = abs(sum(losses))
    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closed) * 100,
        "avg_return_pct": sum(returns) / len(returns),
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else 0.0,
        "best": max(pnls),
        "worst": min(pnls),
        "total_pnl": sum(pnls),
        "fees": sum(row.get("fees_paid") or 0.0 for row in closed),
        "avg_days_held": sum(days) / len(days),
        "by_symbol": by_symbol,
    }
