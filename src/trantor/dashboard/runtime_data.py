"""Read-only access to the runtime database for the dashboard.

The dashboard opens the SQLite file with ``mode=ro``. It cannot write state,
engage or release the emergency stop, change settings, or reach any broker
adapter. Everything shown is labelled by provenance: live data, shadow
(hypothetical), paper simulation, unverified, or blocked.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime.store import RuntimeStore, StoreError
from .data import DashboardError

__all__ = ["RuntimeRepository"]

UTC = timezone.utc
_ID_RE = re.compile(r"^(dec|pos)-[0-9a-f]{20,40}$")
STATUS_STALE_SECONDS = 30


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


class RuntimeRepository:
    def __init__(self, db_path: Path, kill_switch_path: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.kill_switch_path = kill_switch_path

    def _store(self) -> RuntimeStore | None:
        if not self.db_path.exists():
            return None
        try:
            return RuntimeStore(self.db_path, read_only=True)
        except StoreError:
            return None

    def _with_store(self, fn, empty: Any) -> Any:
        store = self._store()
        if store is None:
            return empty
        try:
            return fn(store)
        except StoreError as exc:
            raise DashboardError(f"runtime database read failed: {exc}", 503) from None
        finally:
            store.close()

    # -- status -----------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        def read(store: RuntimeStore) -> dict[str, Any]:
            status = store.get_state("runtime_status")
            verification = store.latest_verification()
            reconciliation = store.latest_reconciliation()
            counts = store.decision_counts()
            return {
                "status": status,
                "verification": verification,
                "reconciliation": reconciliation,
                "decision_counts": counts,
            }

        data = self._with_store(read, None)
        if data is None:
            return {
                "available": False,
                "running": False,
                "reason": "no runtime database found; start `python -m trantor.runtime run`",
                "kill_switch": self._kill_switch(),
            }
        status = data["status"] or {}
        generated = status.get("generated_at")
        age = None
        if generated:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(generated)).total_seconds()
            except ValueError:
                age = None
        running = age is not None and age <= STATUS_STALE_SECONDS and not status.get("stopped_at")
        market = status.get("market") or {}
        if not running:
            # Never present a stopped runtime's last quote as a live price.
            market = {**market, "quote": None, "status": "runtime_not_running"}
        return {
            "available": True,
            "running": running,
            "status_age_seconds": None if age is None else round(age, 1),
            "stopped_at": status.get("stopped_at"),
            "execution_mode": status.get("execution_mode"),
            "profile": status.get("profile"),
            "labels": status.get("labels", []),
            "strategy": status.get("strategy"),
            "config_fingerprint": status.get("config_fingerprint"),
            "kill_switch": status.get("kill_switch") or self._kill_switch(),
            "entries_halted": status.get("entries_halted"),
            "reconciled": status.get("reconciled"),
            "market": market,
            "session": status.get("session"),
            "risk": status.get("risk"),
            "open_positions": status.get("open_positions", []) if running else status.get("open_positions", []),
            "last_decision": status.get("last_decision"),
            "calendar": status.get("calendar"),
            "notifications": status.get("notifications"),
            "stats": status.get("stats"),
            "started_at": status.get("started_at"),
            "uptime_seconds": status.get("uptime_seconds"),
            "broker_verification": data["verification"],
            "reconciliation": data["reconciliation"],
            "decision_counts": data["decision_counts"],
        }

    def _kill_switch(self) -> dict[str, Any]:
        if self.kill_switch_path is None or not self.kill_switch_path.exists():
            return {"engaged": False}
        return {"engaged": True}

    # -- collections ------------------------------------------------------------

    def candles(self, timeframe: str) -> dict[str, Any]:
        if timeframe not in ("15m", "4h", "1d"):
            raise DashboardError("timeframe must be 15m, 4h or 1d", 400)
        data = self._with_store(lambda store: store.get_state("live_candles"), None)
        status = self.status()
        if not data or not status.get("running"):
            return {"available": False, "label": "NO LIVE DATA", "bars": [], "reason": "runtime is not running"}
        frame = (data.get("frames") or {}).get(timeframe)
        if not frame:
            return {"available": False, "label": "NO LIVE DATA", "bars": [], "reason": f"{timeframe} candles unavailable"}
        feed = (status.get("market") or {}).get("status")
        if feed != "live":
            label = f"LAST RECEIVED - FEED {str(feed).upper()}"
        elif frame.get("time_basis_verified"):
            label = "LIVE DATA"
        else:
            label = "UNVERIFIED CLOCK"
        return {
            "available": True,
            "label": label,
            "timeframe": timeframe,
            "generated_at": data.get("generated_at"),
            "completion_basis": frame.get("completion_basis"),
            "bars": frame.get("bars", []),
        }

    def decisions(self, filters: dict[str, str]) -> dict[str, Any]:
        status_filter = filters.get("status") or None
        if status_filter not in (None, "candidate", "rejected"):
            raise DashboardError("status must be candidate or rejected", 400)
        try:
            limit = max(1, min(int(filters.get("limit", "100")), 500))
        except ValueError:
            raise DashboardError("limit must be an integer", 400) from None

        def read(store: RuntimeStore) -> list[dict[str, Any]]:
            rows = store.recent_decisions(limit, status=status_filter)
            items = []
            for row in rows:
                envelope = _loads(row["envelope_json"]) or {}
                items.append(
                    {
                        "decision_id": row["decision_id"],
                        "decision_time": row["decision_time"],
                        "evaluated_at": row["evaluated_at"],
                        "profile": row["profile"],
                        "variant_id": row["variant_id"],
                        "status": row["status"],
                        "side": row["side"],
                        "score": row["score_total"],
                        "confidence": row["confidence"],
                        "rejection_codes": _loads(row["rejection_codes"]) or [],
                        "outcome": row["outcome"],
                        "record_hash": row["record_hash"],
                        "news_status": (envelope.get("news") or {}).get("status"),
                        "blocked_reasons": envelope.get("blocked_reasons", []),
                    }
                )
            return items

        items = self._with_store(read, [])
        return {"items": items, "count": len(items)}

    def decision_detail(self, decision_id: str) -> dict[str, Any]:
        if not _ID_RE.match(decision_id or "") or not decision_id.startswith("dec-"):
            raise DashboardError("invalid decision id", 400)
        row = self._with_store(lambda store: store.decision(decision_id), None)
        if row is None:
            raise DashboardError("decision not found", 404)
        return {
            "decision_id": row["decision_id"],
            "record_hash": row["record_hash"],
            "outcome": row["outcome"],
            "record": _loads(row["record_json"]),
            "envelope": _loads(row["envelope_json"]),
        }

    def positions(self) -> dict[str, Any]:
        def read(store: RuntimeStore) -> dict[str, Any]:
            positions = store.positions(limit=500)
            for item in positions:
                item["events"] = [
                    {**event, "detail": _loads(event.pop("detail_json"))}
                    for event in store.position_events(item["position_id"])
                ]
            orders = store.orders(limit=200)
            for order in orders:
                order["request"] = _loads(order.pop("request_json"))
                order["events"] = [
                    {**event, "detail": _loads(event.pop("detail_json"))}
                    for event in store.order_events(order["client_order_id"])
                ]
            return {"positions": positions, "orders": orders}

        data = self._with_store(read, {"positions": [], "orders": []})
        closed = [p for p in data["positions"] if p["closed"]]
        wins = [p for p in closed if p["state"]["realized_r"] > 1e-9]
        losses = [p for p in closed if p["state"]["realized_r"] < -1e-9]
        realized = sum(p["state"]["realized_r"] for p in closed)
        return {
            **data,
            "summary": {
                "closed": len(closed),
                "open": len(data["positions"]) - len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "breakeven": len(closed) - len(wins) - len(losses),
                "realized_r": realized,
                "label": "PAPER/SHADOW SIMULATION - GROSS - NOT LIVE PERFORMANCE",
                "inconclusive": len(closed) < 30,
            },
        }

    def risk(self) -> dict[str, Any]:
        status = self.status()

        def read(store: RuntimeStore) -> list[dict[str, Any]]:
            profile = status.get("profile") or "shadow"
            return store.daily(profile, 60)

        return {
            "risk": status.get("risk"),
            "daily": self._with_store(read, []),
            "kill_switch": status.get("kill_switch"),
            "entries_halted": status.get("entries_halted"),
            "profile": status.get("profile"),
            "running": status.get("running"),
        }

    def health(self, filters: dict[str, str]) -> dict[str, Any]:
        level = filters.get("level") or None
        if level not in (None, "info", "warning", "error", "critical"):
            raise DashboardError("level must be info, warning, error or critical", 400)

        def read(store: RuntimeStore) -> dict[str, Any]:
            events = store.health_events(200, min_level=level)
            for event in events:
                event["detail"] = _loads(event.pop("detail_json"))
            return {"events": events, "notifications": store.notifications(50), "integrity": store.integrity_check()}

        return self._with_store(read, {"events": [], "notifications": [], "integrity": "no database"})


def experiments_payload(results_root: Path) -> dict[str, Any]:
    """Compare every versioned experiment under a fixed results directory.

    Only directories directly inside ``results_root`` that contain a
    ``summary.json`` are read; no request parameter selects a path.
    """

    items = []
    root = Path(results_root)
    if not root.is_dir():
        return {"experiments": []}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        summary_path = directory / "summary.json"
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            items.append({"experiment": directory.name, "error": "summary unreadable"})
            continue
        overall = (summary.get("overall") or {}).get("metrics") or {}
        splits = {}
        for name, split in (summary.get("splits") or {}).items():
            metrics = split.get("metrics") or {}
            splits[name] = {
                "closed": split.get("closed_trade_count"),
                "realized_r": metrics.get("gross_realized_r"),
                "win_rate": metrics.get("win_rate"),
                "profit_factor": metrics.get("profit_factor"),
            }
        strategy = summary.get("strategy") or {}
        time_semantics = summary.get("time_semantics") or {}
        items.append(
            {
                "experiment": directory.name,
                "variant_id": strategy.get("variant_id"),
                "labels": summary.get("labels"),
                "time_coordinate": time_semantics.get("coordinate"),
                "decisions": (summary.get("overall") or {}).get("decision_count"),
                "closed": overall.get("closed_trade_count"),
                "wins": overall.get("win_count"),
                "losses": overall.get("loss_count"),
                "win_rate": overall.get("win_rate"),
                "realized_r": overall.get("gross_realized_r"),
                "expectancy_r": overall.get("average_r"),
                "profit_factor": overall.get("profit_factor"),
                "max_drawdown_r": overall.get("max_drawdown_r"),
                "splits": splits,
            }
        )
    return {"experiments": items, "note": "Gross research results; fewer than 30 closed trades per split is inconclusive."}
