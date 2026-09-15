"""SQLite persistence.

One file, WAL mode, a single connection guarded by a lock. The engine is one thread doing
one cycle at a time, so contention is not a concern and a connection pool would be
ceremony. Every write is small and committed immediately, because the process can be killed
at any moment and an unflushed trade is a lost trade.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .models import Position, clean, now_ms

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    setup TEXT NOT NULL,
    score REAL NOT NULL,
    go INTEGER NOT NULL,
    conviction INTEGER NOT NULL DEFAULT 0,
    size_multiplier REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'claude',
    thesis TEXT NOT NULL DEFAULT '',
    invalidation TEXT NOT NULL DEFAULT '',
    blocked_by TEXT NOT NULL DEFAULT '',
    candidate TEXT NOT NULL DEFAULT '{}',
    verdict TEXT NOT NULL DEFAULT '{}',
    position_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts DESC);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    venue TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    opened_at INTEGER NOT NULL,
    closed_at INTEGER,
    entry_price REAL NOT NULL,
    exit_price REAL,
    qty REAL NOT NULL,
    initial_qty REAL NOT NULL,
    stop REAL NOT NULL,
    initial_stop REAL NOT NULL,
    target REAL NOT NULL,
    risk_amount REAL NOT NULL,
    setup TEXT NOT NULL,
    score REAL NOT NULL,
    conviction INTEGER NOT NULL DEFAULT 0,
    thesis TEXT NOT NULL DEFAULT '',
    invalidation TEXT NOT NULL DEFAULT '',
    realized_pnl REAL NOT NULL DEFAULT 0,
    fees_paid REAL NOT NULL DEFAULT 0,
    r_multiple REAL,
    exit_reason TEXT,
    state TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_positions_closed ON positions(status, closed_at DESC);

CREATE TABLE IF NOT EXISTS fills (
    id TEXT PRIMARY KEY,
    position_id TEXT,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts DESC);

CREATE TABLE IF NOT EXISTS equity (
    ts INTEGER PRIMARY KEY,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    positions_value REAL NOT NULL DEFAULT 0,
    realized_today REAL NOT NULL DEFAULT 0,
    open_positions INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # -- primitives ----------------------------------------------------------------------
    def _write(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self._db.execute(sql, tuple(params))
            self._db.commit()

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._db.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def _row(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    # -- key/value -----------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        row = self._row("SELECT value FROM kv WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def put(self, key: str, value: Any) -> None:
        self._write(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(clean(value))),
        )

    # -- events --------------------------------------------------------------------------
    def log_event(
        self, kind: str, message: str, level: str = "info", data: Any = None,
        ts: int | None = None,
    ) -> None:
        self._write(
            "INSERT INTO events(ts, kind, level, message, data) VALUES(?,?,?,?,?)",
            (ts or now_ms(), kind, level, message, json.dumps(clean(data or {}))),
        )

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
        for row in rows:
            row["data"] = json.loads(row["data"])
        return rows

    # -- decisions -----------------------------------------------------------------------
    def save_decision(self, record: dict[str, Any]) -> None:
        self._write(
            """INSERT INTO decisions
               (id, ts, symbol, setup, score, go, conviction, size_multiplier, source,
                thesis, invalidation, blocked_by, candidate, verdict, position_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record["id"],
                record.get("ts", now_ms()),
                record["symbol"],
                record.get("setup", ""),
                float(record.get("score", 0)),
                1 if record.get("go") else 0,
                int(record.get("conviction", 0)),
                float(record.get("size_multiplier", 0)),
                record.get("source", "claude"),
                record.get("thesis", ""),
                record.get("invalidation", ""),
                record.get("blocked_by", ""),
                json.dumps(clean(record.get("candidate", {}))),
                json.dumps(clean(record.get("verdict", {}))),
                record.get("position_id"),
            ),
        )

    def decisions(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,))
        for row in rows:
            row["go"] = bool(row["go"])
            row["candidate"] = json.loads(row["candidate"])
            row["verdict"] = json.loads(row["verdict"])
        return rows

    # -- positions -----------------------------------------------------------------------
    def open_position(self, position: Position) -> None:
        self._write(
            """INSERT INTO positions
               (id, symbol, venue, status, opened_at, entry_price, qty, initial_qty, stop,
                initial_stop, target, risk_amount, setup, score, conviction, thesis,
                invalidation, state)
               VALUES(?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                position.id,
                position.symbol,
                position.venue,
                position.opened_at,
                position.entry_price,
                position.qty,
                position.initial_qty,
                position.stop,
                position.initial_stop,
                position.target,
                position.risk_amount,
                position.setup,
                position.score,
                position.conviction,
                position.thesis,
                position.invalidation,
                "{}",
            ),
        )

    def sync_position(self, position: Position) -> None:
        """Persist the mutable half of a live position after every cycle.

        Cheap, and it means a restart resumes management of open trades exactly where it
        left off rather than rebuilding a plan from scratch.
        """
        self._write(
            """UPDATE positions SET qty=?, stop=?, target=?, realized_pnl=?, fees_paid=?,
               state=? WHERE id=?""",
            (
                position.qty,
                position.stop,
                position.target,
                position.realized_pnl,
                position.fees_paid,
                json.dumps(
                    {
                        "scaled_out": position.scaled_out,
                        "breakeven_set": position.breakeven_set,
                        "highest_close": position.highest_close,
                        "regime_break_closes": position.regime_break_closes,
                        "regime_bar_time": position.regime_bar_time,
                        "last_note": position.last_note,
                    }
                ),
                position.id,
            ),
        )

    def close_position(
        self, position: Position, exit_price: float, reason: str, r_multiple: float
    ) -> None:
        self._write(
            """UPDATE positions SET status='CLOSED', closed_at=?, exit_price=?, qty=0,
               realized_pnl=?, fees_paid=?, exit_reason=?, r_multiple=? WHERE id=?""",
            (
                now_ms(),
                exit_price,
                position.realized_pnl,
                position.fees_paid,
                reason,
                r_multiple,
                position.id,
            ),
        )

    def load_open_positions(self) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at")
        for row in rows:
            row["state"] = json.loads(row["state"] or "{}")
        return rows

    def closed_positions(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM positions WHERE status='CLOSED' "
            "ORDER BY closed_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )

    def all_closed(self) -> list[dict[str, Any]]:
        """Every closed trade. For reporting only — never call this on the hot path."""
        return self._rows(
            "SELECT * FROM positions WHERE status='CLOSED' ORDER BY closed_at, rowid"
        )

    # The engine rebuilds its account picture every cycle, several times. Doing that by
    # loading the whole trade history worked fine on day one and gets steadily slower for
    # the life of the account. These aggregates keep it flat instead.
    def realized_since(self, since_ms: float) -> float:
        row = self._row(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS total FROM positions "
            "WHERE status='CLOSED' AND closed_at >= ?",
            (since_ms,),
        )
        return float(row["total"]) if row else 0.0

    def total_realized(self) -> float:
        row = self._row(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS total FROM positions WHERE status='CLOSED'"
        )
        return float(row["total"]) if row else 0.0

    def entries_since(self, since_ms: float) -> int:
        row = self._row(
            "SELECT COUNT(*) AS n FROM positions WHERE opened_at >= ?", (since_ms,)
        )
        return int(row["n"]) if row else 0

    def recent_results(self, limit: int = 20) -> list[float]:
        """The last N realised results, newest first — enough to measure a losing streak.

        Bounded deliberately: a streak longer than this would have tripped a circuit
        breaker long before, so there is nothing further back worth reading.
        """
        # `rowid DESC` breaks ties on closed_at. Several positions routinely close within
        # the same millisecond — all three can exit in one cycle — and without a stable
        # tiebreaker SQLite is free to return them in any order, which scrambles the streak
        # the consecutive-loss breaker is counting.
        rows = self._rows(
            "SELECT realized_pnl FROM positions WHERE status='CLOSED' "
            "ORDER BY closed_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [float(row["realized_pnl"] or 0.0) for row in rows]

    # -- fills ---------------------------------------------------------------------------
    def save_fill(self, fill_id: str, position_id: str | None, fill: Any, reason: str) -> None:
        self._write(
            """INSERT INTO fills(id, position_id, ts, symbol, side, qty, price, fee, reason, raw)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                fill_id,
                position_id,
                fill.ts,
                fill.symbol,
                fill.side,
                fill.qty,
                fill.price,
                fill.fee,
                reason,
                json.dumps(clean(fill.raw)),
            ),
        )

    def fills(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM fills ORDER BY ts DESC LIMIT ?", (limit,))

    # -- equity --------------------------------------------------------------------------
    def record_equity(
        self,
        equity: float,
        cash: float,
        positions_value: float,
        realized_today: float,
        open_positions: int,
    ) -> None:
        self._write(
            """INSERT INTO equity(ts, equity, cash, positions_value, realized_today, open_positions)
               VALUES(?,?,?,?,?,?) ON CONFLICT(ts) DO NOTHING""",
            (now_ms(), equity, cash, positions_value, realized_today, open_positions),
        )

    def equity_curve(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM equity ORDER BY ts DESC LIMIT ?", (limit,))
        return list(reversed(rows))
