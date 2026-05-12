from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client


@dataclass(slots=True)
class SupabaseService:
    url: str
    key: str
    logger: logging.Logger
    client: Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.client = create_client(self.url, self.key)

    def save_signal(self, signal_data: dict[str, Any]) -> dict[str, Any] | None:
        return self._insert_record("signals", signal_data)

    def save_trade(self, trade_data: dict[str, Any]) -> dict[str, Any] | None:
        return self._insert_record("trades", trade_data, add_timestamp=False)

    def save_journal_entry(self, journal_data: dict[str, Any]) -> bool:
        return self._insert("journal_entries", journal_data)

    def save_performance_log(self, performance_data: dict[str, Any]) -> bool:
        return self._insert("performance_logs", performance_data)

    def update_trade(self, trade_id: str | int, trade_data: dict[str, Any]) -> bool:
        payload = dict(trade_data)
        try:
            self.client.table("trades").update(payload).eq("id", trade_id).execute()
            self.logger.info("Updated trade %s in Supabase.", trade_id)
            return True
        except Exception as exc:  # pragma: no cover - depends on runtime service responses.
            self.logger.error("Failed to update trade %s in Supabase: %s", trade_id, exc)
            return False

    def get_recent_signals(self, limit: int = 5) -> list[dict[str, Any]]:
        return self._select("signals", order_by="timestamp", descending=True, limit=limit)

    def get_recent_trades(
        self,
        *,
        limit: int = 50,
        execution_mode: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {}
        if execution_mode is not None:
            filters["execution_mode"] = execution_mode
        if status is not None:
            filters["status"] = status
        return self._select("trades", filters=filters, order_by="opened_at", descending=True, limit=limit)

    def get_open_trades(self, execution_mode: str) -> list[dict[str, Any]]:
        return self._select(
            "trades",
            filters={"execution_mode": execution_mode, "status": "OPEN"},
            order_by="opened_at",
            descending=False,
        )

    def get_pending_trades(self, execution_mode: str) -> list[dict[str, Any]]:
        return self._select(
            "trades",
            filters={"execution_mode": execution_mode, "status": "PENDING_APPROVAL"},
            order_by="opened_at",
            descending=False,
        )

    def get_trade_by_id(self, trade_id: str | int) -> dict[str, Any] | None:
        records = self._select("trades", filters={"id": trade_id}, limit=1)
        return records[0] if records else None

    def get_recent_journal_entries(self, limit: int = 5) -> list[dict[str, Any]]:
        return self._select("journal_entries", order_by="timestamp", descending=True, limit=limit)

    def get_latest_performance_log(self) -> dict[str, Any] | None:
        records = self._select("performance_logs", order_by="timestamp", descending=True, limit=1)
        return records[0] if records else None

    def get_latest_performance_log_for_mode(self, execution_mode: str) -> dict[str, Any] | None:
        records = self._select(
            "performance_logs",
            filters={"execution_mode": execution_mode},
            order_by="timestamp",
            descending=True,
            limit=1,
        )
        return records[0] if records else None

    def _insert(self, table_name: str, payload: dict[str, Any]) -> bool:
        record = self._insert_record(table_name, payload)
        return record is not None

    def _insert_record(self, table_name: str, payload: dict[str, Any], *, add_timestamp: bool = True) -> dict[str, Any] | None:
        record = dict(payload)
        if add_timestamp:
            record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

        try:
            response = self.client.table(table_name).insert(record).execute()
            self.logger.info("Saved record to Supabase table '%s'.", table_name)
            rows = list(response.data or [])
            return rows[0] if rows else record
        except Exception as exc:  # pragma: no cover - depends on runtime service responses.
            self.logger.error("Failed to save record to Supabase table '%s': %s", table_name, exc)
            return None

    def _select(
        self,
        table_name: str,
        *,
        filters: dict[str, Any] | None = None,
        order_by: str = "timestamp",
        descending: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            query = self.client.table(table_name).select("*")
            for field_name, value in (filters or {}).items():
                query = query.eq(field_name, value)
            query = query.order(order_by, desc=descending)
            if limit is not None:
                query = query.limit(limit)
            response = query.execute()
            return list(response.data or [])
        except Exception as exc:  # pragma: no cover - depends on runtime service responses.
            self.logger.error("Failed to read from Supabase table '%s': %s", table_name, exc)
            return []
