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

-- One row per coin per daily decision, including the days nothing was done.
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    day INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    close REAL NOT NULL,
    average REAL NOT NULL,
    distance REAL NOT NULL,
    trend_on INTEGER NOT NULL,
    action TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
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
    cost REAL NOT NULL,
    entry_close REAL NOT NULL DEFAULT 0,
    entry_average REAL NOT NULL DEFAULT 0,
    entry_fee REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    fees_paid REAL NOT NULL DEFAULT 0,
    return_pct REAL,
    exit_reason TEXT,
    last_note TEXT NOT NULL DEFAULT ''
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
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(positions)")}
        if "setup" in columns:
            self._db.close()
            raise RuntimeError(
                f"{self.path} was written by Helm version 1, whose positions carried stops and "
                f"targets this version does not have. Point HELM_DB at a new file; the old one "
                f"is untouched."
            )
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
               (id, ts, day, symbol, close, average, distance, trend_on, action, note, position_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record["id"],
                record.get("ts", now_ms()),
                int(record["day"]),
                record["symbol"],
                float(record["close"]),
                float(record["average"]),
                float(record["distance"]),
                1 if record["on"] else 0,
                record["action"],
                record.get("note", ""),
                record.get("position_id"),
            ),
        )

    def decisions(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM decisions ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,))
        for row in rows:
            row["on"] = bool(row.pop("trend_on"))
        return rows

    # -- positions -----------------------------------------------------------------------
    def open_position(self, position: Position) -> None:
        self._write(
            """INSERT INTO positions
               (id, symbol, venue, status, opened_at, entry_price, qty, initial_qty, cost,
                entry_close, entry_average, entry_fee, fees_paid, last_note)
               VALUES(?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?)""",
            (
                position.id,
                position.symbol,
                position.venue,
                position.opened_at,
                position.entry_price,
                position.qty,
                position.initial_qty,
                position.cost,
                position.entry_close,
                position.entry_average,
                position.entry_fee,
                position.fees_paid,
                position.last_note,
            ),
        )

    def sync_position(self, position: Position) -> None:
        """Persist the mutable half of a live position, so a restart resumes it exactly."""
        self._write(
            "UPDATE positions SET qty=?, realized_pnl=?, fees_paid=?, last_note=? WHERE id=?",
            (position.qty, position.realized_pnl, position.fees_paid, position.last_note, position.id),
        )

    def close_position(self, position: Position, exit_price: float, reason: str) -> None:
        """`return_pct` is the result against what the purchase cost, fees on both legs in."""
        return_pct = position.realized_pnl / position.cost * 100 if position.cost else 0.0
        self._write(
            """UPDATE positions SET status='CLOSED', closed_at=?, exit_price=?, qty=0,
               realized_pnl=?, fees_paid=?, exit_reason=?, return_pct=?, last_note=? WHERE id=?""",
            (
                now_ms(),
                exit_price,
                position.realized_pnl,
                position.fees_paid,
                reason,
                return_pct,
                position.last_note,
                position.id,
            ),
        )

    def load_open_positions(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at")

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
