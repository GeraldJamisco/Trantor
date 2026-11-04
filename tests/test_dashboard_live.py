"""Live dashboard endpoints and security boundaries."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trantor.dashboard.data import DashboardConfig
from trantor.dashboard.runtime_data import RuntimeRepository
from trantor.dashboard.server import build_server
from trantor.runtime.store import RuntimeStore

UTC = timezone.utc


def _seed(db: Path, *, running: bool = True, feed: str = "live") -> None:
    store = RuntimeStore(db)
    now = datetime.now(UTC)
    generated = now if running else now - timedelta(minutes=10)
    store.set_state(
        "runtime_status",
        {
            "generated_at": generated.isoformat(),
            "profile": "shadow",
            "execution_mode": "research",
            "labels": ["SHADOW MODE - HYPOTHETICAL ONLY - NO ORDERS"],
            "market": {"status": feed, "quote": {"bid": 4000.0, "ask": 4000.2, "spread": 0.2, "label": "LIVE DATA"}},
            "kill_switch": {"engaged": False},
            "risk": {"mode": "shadow", "entries_today": 0},
            "stats": {"steps": 10},
        },
    )
    store.set_state(
        "live_candles",
        {"generated_at": now.isoformat(), "frames": {"15m": {"completion_basis": "broker_clock", "time_basis_verified": True, "bars": [{"t": now.isoformat(), "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3, "complete": False}]}}},
    )
    with store.transaction() as conn:
        store.insert_decision(
            {
                "decision_id": "dec-" + "a" * 28,
                "variant_id": "xauusd_p_approved_v1",
                "profile": "shadow",
                "decision_time": now.isoformat(),
                "decision_time_raw": None,
                "evaluated_at": now.isoformat(),
                "execution_mode": "research",
                "status": "rejected",
                "side": None,
                "score_total": 0,
                "confidence": "reject",
                "rejection_codes": json.dumps(["d1_conflict"]),
                "record_hash": "b" * 64,
                "record_json": json.dumps({"variant_id": "xauusd_p_approved_v1"}),
                "envelope_json": json.dumps({"news": {"status": "unavailable"}, "blocked_reasons": []}),
                "outcome": "no_trade",
            },
            conn,
        )
    store.close()


@pytest.fixture()
def server(tmp_path: Path):
    db = tmp_path / "rt.sqlite3"
    _seed(db)
    srv = build_server(DashboardConfig.default(), host="127.0.0.1", port=0, runtime_db=db)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", db
    srv.shutdown()
    srv.server_close()


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_live_status_and_candles(server) -> None:
    base, _ = server
    status, body = _get(f"{base}/api/live/status")
    assert status == 200 and body["running"] and body["market"]["quote"]["bid"] == 4000.0
    status, candles = _get(f"{base}/api/live/candles?tf=15m")
    assert candles["label"] == "LIVE DATA" and candles["bars"][0]["complete"] is False
    assert _get(f"{base}/api/live/candles?tf=1w")[0] == 400


def test_decisions_list_and_detail(server) -> None:
    base, _ = server
    _, body = _get(f"{base}/api/live/decisions?status=rejected")
    assert body["count"] == 1 and body["items"][0]["rejection_codes"] == ["d1_conflict"]
    _, detail = _get(f"{base}/api/live/decisions/dec-{'a' * 28}")
    assert detail["record"]["variant_id"] == "xauusd_p_approved_v1"
    assert _get(f"{base}/api/live/decisions?status=bogus")[0] == 400
    assert _get(f"{base}/api/live/decisions?limit=abc")[0] == 400


@pytest.mark.parametrize(
    "path",
    [
        "/api/live/decisions/..%2F..%2Fconfig%2Fsettings.json",
        "/api/live/decisions/dec-x' OR '1'='1",
        "/api/live/decisions/" + "a" * 400,
        "/api/live/../../config/settings.json",
        "/assets/..%2F..%2F..%2Fconfig%2Fsettings.json",
        "/api/live/nope",
        "/state/trantor_runtime.sqlite3",
    ],
)
def test_path_traversal_and_injection_are_rejected(server, path: str) -> None:
    base, _ = server
    status, body = _get(base + path.replace(" ", "%20"))
    assert status in (400, 404)
    assert "schema_version" not in json.dumps(body)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_methods_rejected(server, method: str) -> None:
    base, _ = server
    request = urllib.request.Request(f"{base}/api/live/status", data=b"{}", method=method)
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 405


def test_dashboard_never_writes_the_runtime_database(server) -> None:
    base, db = server
    before = db.stat().st_mtime_ns
    for path in ("status", "decisions", "positions", "risk", "health", "candles?tf=15m"):
        assert _get(f"{base}/api/live/{path}")[0] == 200
    store = RuntimeStore(db, read_only=True)
    try:
        assert store.decision_counts() == {"rejected": 1}
    finally:
        store.close()
    assert db.stat().st_mtime_ns == before


def test_stopped_runtime_shows_no_price(tmp_path: Path) -> None:
    db = tmp_path / "rt.sqlite3"
    _seed(db, running=False)
    repo = RuntimeRepository(db)
    status = repo.status()
    assert not status["running"]
    assert status["market"]["quote"] is None
    assert not repo.candles("15m")["available"]


def test_disconnected_feed_candles_not_labelled_live(tmp_path: Path) -> None:
    db = tmp_path / "rt.sqlite3"
    _seed(db, feed="disconnected")
    assert repo_label(db) == "LAST RECEIVED - FEED DISCONNECTED"


def repo_label(db: Path) -> str:
    return RuntimeRepository(db).candles("15m")["label"]


def test_missing_runtime_database_is_explicit(tmp_path: Path) -> None:
    repo = RuntimeRepository(tmp_path / "missing.sqlite3")
    status = repo.status()
    assert status["available"] is False and status["running"] is False
    assert repo.decisions({})["count"] == 0


def test_cli_refuses_non_loopback_binding() -> None:
    from trantor.dashboard.__main__ import main

    assert main(["--host", "0.0.0.0", "--port", "0"]) == 2


def test_live_js_is_whitelisted_and_static_files_are_fixed() -> None:
    from trantor.dashboard.server import STATIC_FILES

    assert "/assets/live.js" in STATIC_FILES
    assert all(".." not in name for name, _ in STATIC_FILES.values())
