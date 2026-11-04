"""SQLite persistence for the Trantor runtime.

Design (``docs/architecture/state_interfaces_persistence.md`` §5):

- one SQLite database, WAL journal, standard library only;
- append-only facts: decisions, order events, position events, health events,
  reconciliations;
- mutable projections (orders, positions, risk state) updated inside
  ``BEGIN IMMEDIATE`` transactions with a version column;
- unique keys enforce idempotency: one decision per
  ``(variant_id, profile, decision_time)`` and one order per
  ``client_order_id``.

A failure to write is raised to the caller; the runtime treats any storage
failure as a reason to stop opening positions.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["RuntimeStore", "StoreError", "SCHEMA_VERSION", "utc_now_iso"]

UTC = timezone.utc
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    decision_time_raw TEXT,
    evaluated_at TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    side TEXT,
    score_total INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    rejection_codes TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    UNIQUE (variant_id, profile, decision_time)
);
CREATE INDEX IF NOT EXISTS idx_decisions_time ON decisions (decision_time);
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    broker_order_id TEXT,
    filled_quantity REAL NOT NULL DEFAULT 0,
    average_fill_price REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    request_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS order_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    status TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    client_order_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0,
    entry_time TEXT NOT NULL,
    closed_at TEXT,
    state_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS position_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_daily (
    day TEXT NOT NULL,
    mode TEXT NOT NULL,
    start_equity REAL NOT NULL,
    realized_r REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    entries INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    locked_out INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (day, mode)
);
CREATE TABLE IF NOT EXISTS health_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    event TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_at ON health_events (at);
CREATE TABLE IF NOT EXISTS config_snapshots (
    fingerprint TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    dedup_key TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS broker_verifications (
    report_id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    status TEXT NOT NULL,
    report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliations (
    reconciliation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    mode TEXT NOT NULL,
    consistent INTEGER NOT NULL,
    detail_json TEXT NOT NULL
);
"""


class StoreError(RuntimeError):
    """Persistent state could not be read or written safely."""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


class RuntimeStore:
    """Thread-safe SQLite store. One instance per process."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self._lock = threading.RLock()
        try:
            if read_only:
                if not self.path.exists():
                    raise StoreError(f"runtime database not found: {self.path}")
                uri = f"file:{self.path.as_posix()}?mode=ro"
                self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=5)
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(
                    str(self.path), check_same_thread=False, timeout=10, isolation_level=None
                )
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._migrate()
        except sqlite3.Error as exc:
            raise StoreError(f"cannot open runtime database {self.path}: {exc}") from exc
        self._conn.row_factory = sqlite3.Row

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        # executescript commits implicitly, so it runs outside an explicit
        # transaction. Every statement is idempotent (IF NOT EXISTS).
        self._conn.executescript(_SCHEMA)
        row = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] if row else None
        if current is None:
            self._conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now_iso()),
            )
        elif current > SCHEMA_VERSION:
            raise StoreError(
                f"database schema {current} is newer than supported {SCHEMA_VERSION}"
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise StoreError("store is read-only")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise StoreError(f"cannot begin transaction: {exc}") from exc
            try:
                yield self._conn
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            else:
                try:
                    self._conn.execute("COMMIT")
                except sqlite3.Error as exc:
                    raise StoreError(f"commit failed: {exc}") from exc

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return list(self._conn.execute(sql, params))
            except sqlite3.Error as exc:
                raise StoreError(f"query failed: {exc}") from exc

    # -- key/value state ------------------------------------------------------

    def get_state(self, key: str) -> Any | None:
        rows = self._query("SELECT value FROM kv_state WHERE key = ?", (key,))
        if not rows:
            return None
        try:
            return json.loads(rows[0]["value"])
        except json.JSONDecodeError as exc:
            raise StoreError(f"state {key!r} is corrupt: {exc}") from exc

    def set_state(self, key: str, value: object, conn: sqlite3.Connection | None = None) -> None:
        payload = _dumps(value)
        now = utc_now_iso()
        sql = (
            "INSERT INTO kv_state (key, value, version, updated_at) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "version = kv_state.version + 1, updated_at = excluded.updated_at"
        )
        if conn is not None:
            conn.execute(sql, (key, payload, now))
            return
        with self.transaction() as tx:
            tx.execute(sql, (key, payload, now))

    # -- decisions ------------------------------------------------------------

    def decision_exists(self, decision_id: str) -> bool:
        return bool(self._query("SELECT 1 FROM decisions WHERE decision_id = ?", (decision_id,)))

    def insert_decision(self, row: Mapping[str, Any], conn: sqlite3.Connection) -> bool:
        """Insert a decision once. Returns False if it already exists."""

        try:
            conn.execute(
                "INSERT INTO decisions (decision_id, variant_id, profile, decision_time, "
                "decision_time_raw, evaluated_at, execution_mode, status, side, score_total, "
                "confidence, rejection_codes, record_hash, record_json, envelope_json, outcome) "
                "VALUES (:decision_id, :variant_id, :profile, :decision_time, :decision_time_raw, "
                ":evaluated_at, :execution_mode, :status, :side, :score_total, :confidence, "
                ":rejection_codes, :record_hash, :record_json, :envelope_json, :outcome)",
                dict(row),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def recent_decisions(self, limit: int = 50, *, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if status:
            rows = self._query(
                "SELECT * FROM decisions WHERE status = ? ORDER BY decision_time DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self._query(
                "SELECT * FROM decisions ORDER BY decision_time DESC LIMIT ?", (limit,)
            )
        return [dict(row) for row in rows]

    def decision(self, decision_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,))
        return dict(rows[0]) if rows else None

    def decision_counts(self) -> dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS n FROM decisions GROUP BY status")
        return {row["status"]: row["n"] for row in rows}

    # -- orders ---------------------------------------------------------------

    def insert_order(self, row: Mapping[str, Any], conn: sqlite3.Connection) -> None:
        try:
            conn.execute(
                "INSERT INTO orders (client_order_id, decision_id, mode, side, quantity, "
                "order_type, status, broker_order_id, filled_quantity, average_fill_price, "
                "created_at, updated_at, request_json) VALUES (:client_order_id, :decision_id, "
                ":mode, :side, :quantity, :order_type, :status, :broker_order_id, "
                ":filled_quantity, :average_fill_price, :created_at, :updated_at, :request_json)",
                dict(row),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"duplicate client_order_id {row.get('client_order_id')!r}") from exc

    def update_order(
        self,
        client_order_id: str,
        *,
        status: str,
        filled_quantity: float,
        average_fill_price: float | None,
        broker_order_id: str | None,
        conn: sqlite3.Connection,
    ) -> None:
        cursor = conn.execute(
            "UPDATE orders SET status = ?, filled_quantity = ?, average_fill_price = ?, "
            "broker_order_id = COALESCE(?, broker_order_id), updated_at = ?, "
            "version = version + 1 WHERE client_order_id = ?",
            (status, filled_quantity, average_fill_price, broker_order_id, utc_now_iso(), client_order_id),
        )
        if cursor.rowcount != 1:
            raise StoreError(f"unknown client_order_id {client_order_id!r}")

    def order(self, client_order_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,))
        return dict(rows[0]) if rows else None

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    def append_order_event(
        self,
        client_order_id: str,
        event: str,
        status: str,
        detail: Mapping[str, Any],
        conn: sqlite3.Connection,
    ) -> None:
        conn.execute(
            "INSERT INTO order_events (client_order_id, at, event, status, detail_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (client_order_id, utc_now_iso(), event, status, _dumps(dict(detail))),
        )

    def order_events(self, client_order_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM order_events WHERE client_order_id = ? ORDER BY event_id",
            (client_order_id,),
        )
        return [dict(row) for row in rows]

    # -- positions ------------------------------------------------------------

    def insert_position(self, row: Mapping[str, Any], conn: sqlite3.Connection) -> None:
        try:
            conn.execute(
                "INSERT INTO positions (position_id, decision_id, client_order_id, mode, side, "
                "status, closed, entry_time, closed_at, state_json, updated_at) VALUES "
                "(:position_id, :decision_id, :client_order_id, :mode, :side, :status, :closed, "
                ":entry_time, :closed_at, :state_json, :updated_at)",
                dict(row),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError(
                f"duplicate position for decision {row.get('decision_id')!r}"
            ) from exc

    def update_position(
        self,
        position_id: str,
        *,
        status: str,
        closed: bool,
        closed_at: str | None,
        state: Mapping[str, Any],
        expected_version: int,
        conn: sqlite3.Connection,
    ) -> int:
        cursor = conn.execute(
            "UPDATE positions SET status = ?, closed = ?, closed_at = ?, state_json = ?, "
            "version = version + 1, updated_at = ? WHERE position_id = ? AND version = ?",
            (
                status,
                1 if closed else 0,
                closed_at,
                _dumps(dict(state)),
                utc_now_iso(),
                position_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise StoreError(
                f"position {position_id!r} changed concurrently or does not exist"
            )
        return expected_version + 1

    def append_position_event(
        self,
        position_id: str,
        event: str,
        detail: Mapping[str, Any],
        conn: sqlite3.Connection,
    ) -> None:
        conn.execute(
            "INSERT INTO position_events (position_id, at, event, detail_json) VALUES (?, ?, ?, ?)",
            (position_id, utc_now_iso(), event, _dumps(dict(detail))),
        )

    def positions(self, *, open_only: bool = False, mode: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if open_only:
            clauses.append("closed = 0")
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = self._query(
            f"SELECT * FROM positions {where} ORDER BY entry_time DESC LIMIT ?", tuple(params)
        )
        result = []
        for row in rows:
            item = dict(row)
            item["state"] = json.loads(item.pop("state_json"))
            result.append(item)
        return result

    def position_events(self, position_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM position_events WHERE position_id = ? ORDER BY event_id", (position_id,)
        )
        return [dict(row) for row in rows]

    # -- daily risk -----------------------------------------------------------

    def upsert_daily(self, row: Mapping[str, Any], conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO risk_daily (day, mode, start_equity, realized_r, realized_pnl, entries, "
            "losses, locked_out, updated_at) VALUES (:day, :mode, :start_equity, :realized_r, "
            ":realized_pnl, :entries, :losses, :locked_out, :updated_at) "
            "ON CONFLICT(day, mode) DO UPDATE SET start_equity = excluded.start_equity, "
            "realized_r = excluded.realized_r, realized_pnl = excluded.realized_pnl, "
            "entries = excluded.entries, losses = excluded.losses, "
            "locked_out = excluded.locked_out, updated_at = excluded.updated_at",
            dict(row),
        )

    def daily(self, mode: str, limit: int = 60) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM risk_daily WHERE mode = ? ORDER BY day DESC LIMIT ?", (mode, int(limit))
        )
        return [dict(row) for row in rows]

    # -- health, config, notifications, verification ---------------------------

    def record_health(
        self,
        level: str,
        component: str,
        event: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO health_events (at, level, component, event, detail_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (utc_now_iso(), level, component, event, _dumps(dict(detail or {}))),
            )

    def health_events(self, limit: int = 100, *, min_level: str | None = None) -> list[dict[str, Any]]:
        levels = {"info": 0, "warning": 1, "error": 2, "critical": 3}
        rows = self._query(
            "SELECT * FROM health_events ORDER BY event_id DESC LIMIT ?", (int(limit) * 4,)
        )
        threshold = levels.get(min_level or "info", 0)
        result = [dict(row) for row in rows if levels.get(row["level"], 0) >= threshold]
        return result[: int(limit)]

    def record_config(self, fingerprint: str, snapshot: Mapping[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO config_snapshots (fingerprint, first_seen_at, snapshot_json) "
                "VALUES (?, ?, ?)",
                (fingerprint, utc_now_iso(), _dumps(dict(snapshot))),
            )

    def claim_notification(self, dedup_key: str, kind: str) -> bool:
        """Reserve a notification key. False means it was already sent/claimed."""

        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO notifications (dedup_key, at, kind, status, attempts) "
                    "VALUES (?, ?, ?, 'pending', 0)",
                    (dedup_key, utc_now_iso(), kind),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def finish_notification(self, dedup_key: str, status: str, error: str | None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE notifications SET status = ?, attempts = attempts + 1, last_error = ? "
                "WHERE dedup_key = ?",
                (status, error, dedup_key),
            )

    def notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM notifications ORDER BY at DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    def record_verification(self, report_id: str, status: str, report: Mapping[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO broker_verifications (report_id, at, status, report_json) "
                "VALUES (?, ?, ?, ?)",
                (report_id, utc_now_iso(), status, _dumps(dict(report))),
            )

    def latest_verification(self) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM broker_verifications ORDER BY at DESC LIMIT 1")
        if not rows:
            return None
        item = dict(rows[0])
        item["report"] = json.loads(item.pop("report_json"))
        return item

    def record_reconciliation(
        self,
        mode: str,
        consistent: bool,
        detail: Mapping[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> None:
        sql = (
            "INSERT INTO reconciliations (at, mode, consistent, detail_json) VALUES (?, ?, ?, ?)"
        )
        params = (utc_now_iso(), mode, 1 if consistent else 0, _dumps(dict(detail)))
        if conn is not None:
            conn.execute(sql, params)
            return
        with self.transaction() as tx:
            tx.execute(sql, params)

    def latest_reconciliation(self) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM reconciliations ORDER BY reconciliation_id DESC LIMIT 1")
        if not rows:
            return None
        item = dict(rows[0])
        item["detail"] = json.loads(item.pop("detail_json"))
        return item

    def integrity_check(self) -> str:
        rows = self._query("PRAGMA integrity_check")
        return str(rows[0][0]) if rows else "unknown"
