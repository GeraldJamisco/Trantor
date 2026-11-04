"""End-to-end runtime integration and fault injection.

Real-data tests replay the validated Phase B datasets through the complete
runtime (market monitor -> strategy -> canonical record -> gate -> paper broker
-> position manager -> persistence) and compare with the immutable Phase E
baseline. They are skipped when ``research_data/`` is not present.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trantor.config import ExecutionMode
from trantor.execution.gate import KillSwitch
from trantor.runtime.replay import load_runtime_decisions, replay_settings, run_runtime_replay
from trantor.runtime.service import LockError, RuntimeConfig, TrantorRuntime
from trantor.runtime.store import RuntimeStore, StoreError
from trantor.strategy.xauusd_p_research import XAUUSDPMTFResearchVariant

from runtime_helpers import FakeSource, m15_series, research_data_available

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "research_results" / "xauusd_p" / "phase_e_authoritative_20261102"
WINDOW_START = datetime(2024, 1, 10, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2024, 1, 11, 12, 0, tzinfo=UTC)

needs_data = pytest.mark.skipif(not research_data_available(), reason="research_data/ not present")


@pytest.fixture(scope="module")
def data():
    from trantor.research.backtest.data import load_research_data

    return load_research_data()


def _baseline_trades(until: datetime) -> list[dict]:
    trades = json.loads((BASELINE / "trades.json").read_text(encoding="utf-8"))
    return [t for t in trades if WINDOW_START.isoformat() <= t["entry_time"] <= until.isoformat()]


def _baseline_hashes(start: datetime, end: datetime) -> dict[str, str]:
    result = {}
    path = BASELINE / "decisions.jsonl"
    if path.stat().st_size < 1024:
        pytest.skip("decisions.jsonl is an LFS pointer")
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            at = row["decision_time"]
            if at > end.isoformat():
                break
            if at >= start.isoformat():
                result[at] = row["record_hash"]
    return result


def _replay(data, tmp_path: Path, **kwargs):
    kwargs.setdefault("strategy", XAUUSDPMTFResearchVariant())
    return run_runtime_replay(data, start=WINDOW_START, end=WINDOW_END, state_dir=tmp_path, **kwargs)


def _positions(tmp_path: Path) -> list[dict]:
    store = RuntimeStore(tmp_path / "trantor_runtime.sqlite3", read_only=True)
    try:
        return sorted(store.positions(limit=100), key=lambda p: p["entry_time"])
    finally:
        store.close()


@needs_data
def test_shadow_replay_reproduces_phase_e_records_and_trades(data, tmp_path: Path) -> None:
    out = _replay(data, tmp_path)
    assert out["stats"]["step_errors"] == 0
    rows = load_runtime_decisions(tmp_path / "trantor_runtime.sqlite3")
    baseline = _baseline_hashes(WINDOW_START, WINDOW_END)
    runtime = {row["decision_time"]: row["record_hash"] for row in rows}
    common = set(runtime) & set(baseline)
    assert len(common) >= len(baseline) - 1
    assert all(runtime[key] == baseline[key] for key in common)
    positions = _positions(tmp_path)
    expected = _baseline_trades(WINDOW_END)
    closed = [p for p in positions if p["closed"]]
    assert [(p["entry_time"], p["state"]["realized_r"]) for p in closed] == [
        (t["entry_time"], t["realized_r"]) for t in expected if t["exit_time"] and t["exit_time"] <= WINDOW_END.isoformat()
    ]
    assert all(p["mode"] == "shadow" for p in positions)


@needs_data
def test_paper_replay_creates_orders_and_positions_through_the_gate(data, tmp_path: Path) -> None:
    _replay(data, tmp_path, mode=ExecutionMode.PAPER, equity_model="paper")
    store = RuntimeStore(tmp_path / "trantor_runtime.sqlite3", read_only=True)
    try:
        orders = store.orders()
        positions = store.positions()
        assert orders and all(o["status"] == "filled" and o["mode"] == "paper" for o in orders)
        assert {o["client_order_id"] for o in orders} == {p["client_order_id"] for p in positions}
        events = store.order_events(orders[0]["client_order_id"])
        assert [e["event"] for e in events] == ["created", "broker_response"]
        first = positions[-1]
        assert first["state"]["risk_amount"] > 0
        daily = store.daily("paper")
        assert any(day["entries"] >= 1 for day in daily)
        reconciliation = store.latest_reconciliation()
        assert reconciliation["consistent"] == 1
    finally:
        store.close()


@needs_data
def test_restart_during_open_position_resumes_without_duplicates(data, tmp_path: Path) -> None:
    out = _replay(data, tmp_path, restart_every=7)
    assert out["restarts"] > 10
    reference = tmp_path / "reference"
    _replay(data, reference)
    assert [(p["entry_time"], p["state"]["realized_r"], p["status"]) for p in _positions(tmp_path)] == [
        (p["entry_time"], p["state"]["realized_r"], p["status"]) for p in _positions(reference)
    ]
    restarted = {r["decision_time"]: r["record_hash"] for r in load_runtime_decisions(tmp_path / "trantor_runtime.sqlite3")}
    straight = {r["decision_time"]: r["record_hash"] for r in load_runtime_decisions(reference / "trantor_runtime.sqlite3")}
    assert restarted == straight


@needs_data
def test_rerun_on_same_state_creates_no_duplicate_decisions_or_positions(data, tmp_path: Path) -> None:
    _replay(data, tmp_path)
    before = (len(load_runtime_decisions(tmp_path / "trantor_runtime.sqlite3")), len(_positions(tmp_path)))
    out = _replay(data, tmp_path)
    after = (len(load_runtime_decisions(tmp_path / "trantor_runtime.sqlite3")), len(_positions(tmp_path)))
    assert before == after
    assert out["stats"]["evaluations"] == 0


@needs_data
def test_emergency_stop_blocks_entries_but_keeps_records(data, tmp_path: Path) -> None:
    KillSwitch(tmp_path / "EMERGENCY_STOP").engage("test halt")
    _replay(data, tmp_path, mode=ExecutionMode.PAPER, equity_model="paper")
    rows = load_runtime_decisions(tmp_path / "trantor_runtime.sqlite3")
    candidates = [r for r in rows if r["status"] == "candidate"]
    assert candidates and all(r["outcome"] == "entry_blocked" for r in candidates)
    envelope = json.loads(candidates[0]["envelope_json"])
    assert "emergency stop" in envelope["blocked_reasons"][0]
    assert _positions(tmp_path) == []


@needs_data
def test_emergency_stop_keeps_managing_open_positions(data, tmp_path: Path) -> None:
    # Open the first position, engage the stop, then continue: exits still happen.
    first_entry = datetime(2024, 1, 10, 8, 15, tzinfo=UTC)
    run_runtime_replay(data, start=WINDOW_START, end=first_entry, state_dir=tmp_path, strategy=XAUUSDPMTFResearchVariant())
    open_before = [p for p in _positions(tmp_path) if not p["closed"]]
    assert open_before
    KillSwitch(tmp_path / "EMERGENCY_STOP").engage("halt with open position")
    run_runtime_replay(data, start=first_entry, end=WINDOW_END, state_dir=tmp_path, strategy=XAUUSDPMTFResearchVariant())
    managed = {p["position_id"]: p for p in _positions(tmp_path)}
    assert managed[open_before[0]["position_id"]]["closed"] == 1
    assert len(managed) == len(open_before)  # no new entry after the stop


@needs_data
def test_late_evaluation_never_enters(data, tmp_path: Path) -> None:
    _replay(data, tmp_path, step_offset=timedelta(minutes=5))
    rows = load_runtime_decisions(tmp_path / "trantor_runtime.sqlite3")
    candidates = [r for r in rows if r["status"] == "candidate"]
    assert candidates and all(r["outcome"] == "entry_blocked" for r in candidates)
    assert "entry window missed" in json.loads(candidates[0]["envelope_json"])["blocked_reasons"][0]
    assert _positions(tmp_path) == []


@needs_data
def test_approved_strategy_records_its_own_identity(data, tmp_path: Path) -> None:
    from trantor.strategy.xauusd_p import XAUUSDPMultiTimeframeStrategy

    _replay(data, tmp_path, strategy=XAUUSDPMultiTimeframeStrategy())
    rows = load_runtime_decisions(tmp_path / "trantor_runtime.sqlite3")
    assert rows and all(r["variant_id"] == "xauusd_p_approved_v1" for r in rows)
    record = json.loads(rows[-1]["record_json"])
    assert record["variant_id"] == "xauusd_p_approved_v1"
    assert record["session"]["filter_mode"] == "approved"
    envelope = json.loads(rows[-1]["envelope_json"])
    assert envelope["news"]["status"] == "unavailable"
    assert "SHADOW MODE - HYPOTHETICAL ONLY - NO ORDERS" in envelope["labels"]


# -- fault injection without real data -------------------------------------------

def _runtime(tmp_path: Path, source: FakeSource, mode: ExecutionMode = ExecutionMode.RESEARCH) -> TrantorRuntime:
    config = RuntimeConfig(
        settings=replay_settings(mode),
        state_dir=tmp_path,
        poll_interval=0,
        bar_refresh_interval=0,
        status_interval=0,
        verification_interval=None,
        calendar_refresh_interval=3600,
    )
    clock = {"mono": 0.0}

    def mono() -> float:
        clock["mono"] += 1.0
        return clock["mono"]

    return TrantorRuntime(config=config, source=source, host_clock=lambda: source.now, monotonic=mono)


def _fake_source() -> FakeSource:
    now = datetime(2026, 11, 4, 10, 0, 1, tzinfo=UTC)
    source = FakeSource(now=now, offset=timedelta(hours=2))
    server = now + source.offset
    source.bars["15m"] = m15_series(server.replace(minute=0, second=0) - timedelta(hours=20), 81)
    source.bars["4h"] = m15_series(server.replace(hour=0, minute=0, second=0) - timedelta(days=60), 1)
    source.bars["1d"] = m15_series(server.replace(hour=0, minute=0, second=0) - timedelta(days=30), 1)
    return source


@pytest.mark.parametrize("mode", [ExecutionMode.DISABLED, ExecutionMode.DEMO, ExecutionMode.LIVE])
def test_runtime_refuses_non_shadow_non_paper_modes(tmp_path: Path, mode: ExecutionMode) -> None:
    runtime = _runtime(tmp_path, _fake_source(), mode)
    with pytest.raises(RuntimeError, match="not authorized|only runs"):
        runtime.start()


def test_second_runtime_on_same_state_is_refused(tmp_path: Path) -> None:
    first = _runtime(tmp_path, _fake_source())
    first.start()
    try:
        with pytest.raises(LockError):
            _runtime(tmp_path, _fake_source()).start()
    finally:
        first.shutdown("test")
    second = _runtime(tmp_path, _fake_source())
    second.start()
    second.shutdown("test")


def test_disconnect_and_reconnect_are_recorded_and_decisions_pause(tmp_path: Path) -> None:
    source = _fake_source()
    runtime = _runtime(tmp_path, source)
    runtime.start()
    try:
        for _ in range(4):
            runtime.step()
            source.now += timedelta(seconds=1)
        assert runtime.monitor.status.value == "live"
        evaluated = runtime.stats["evaluations"]
        source.connected = False
        for _ in range(3):
            runtime.step()
            source.now += timedelta(seconds=1)
        assert runtime.monitor.status.value == "disconnected"
        assert runtime.stats["evaluations"] == evaluated
        assert runtime.status()["market"]["quote"] is None
        source.connected = True
        for _ in range(3):
            runtime.step()
            source.now += timedelta(seconds=1)
        assert runtime.monitor.status.value == "live"
        events = [e["event"] for e in runtime.store.health_events(100)]
        assert "feed_status_changed" in events
        assert runtime.stats["step_errors"] == 0
    finally:
        runtime.shutdown("test")


def test_storage_failure_halts_new_entries(tmp_path: Path, monkeypatch) -> None:
    source = _fake_source()
    runtime = _runtime(tmp_path, source)
    runtime.start()
    try:
        def boom(*args, **kwargs):
            raise StoreError("disk full")

        monkeypatch.setattr(runtime.engine, "process", boom)
        for _ in range(4):
            runtime.step()
            source.now += timedelta(seconds=1)
        assert runtime.status()["entries_halted"].startswith("storage failure")
        assert runtime.engine.reconciled is False
    finally:
        runtime.shutdown("test")


def test_risk_state_mismatch_on_restart_halts_entries(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _fake_source())
    runtime.start()
    runtime.shutdown("test")
    store = RuntimeStore(tmp_path / "trantor_runtime.sqlite3")
    state = store.get_state("risk:shadow")
    state["open_positions"] = 3
    store.set_state("risk:shadow", state)
    store.close()
    restarted = _runtime(tmp_path, _fake_source())
    restarted.start()
    try:
        assert "risk state" in restarted.status()["entries_halted"]
        assert not restarted.engine.reconciled
    finally:
        restarted.shutdown("test")


def test_notification_failure_never_breaks_the_loop(tmp_path: Path) -> None:
    from trantor.monitoring.notify import Notifier

    class Broken:
        name = "broken"

        def send(self, text: str) -> None:
            raise RuntimeError("telegram down")

    source = _fake_source()
    runtime = _runtime(tmp_path, source)
    runtime.notifier_factory = lambda store: Notifier(store, Broken())
    runtime.start()
    try:
        for _ in range(4):
            runtime.step()
            source.now += timedelta(seconds=1)
        runtime.notifier.deliver_pending_now()
        assert runtime.stats["step_errors"] == 0
    finally:
        runtime.shutdown("test")


def test_status_snapshot_contains_no_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN-VALUE")
    source = _fake_source()
    runtime = _runtime(tmp_path, source)
    runtime.start()
    try:
        runtime.step()
        text = json.dumps(runtime.status(), default=str)
        assert "SECRET-TOKEN-VALUE" not in text
        assert "12345678" not in text
    finally:
        runtime.shutdown("test")


@needs_data
def test_early_rejection_reports_session_not_evaluated(data, tmp_path: Path) -> None:
    from trantor.strategy.xauusd_p import XAUUSDPMultiTimeframeStrategy

    _replay(data, tmp_path, strategy=XAUUSDPMultiTimeframeStrategy())
    rows = load_runtime_decisions(tmp_path / "trantor_runtime.sqlite3")
    early = [r for r in rows if json.loads(r["rejection_codes"]) in (["d1_conflict"], ["h4_neutral"], ["m15_gap"])]
    assert early
    for row in early:
        session = json.loads(row["envelope_json"])["session"]
        assert session["evaluated"] is False and session["iana_overlap"] is None
    late = [r for r in rows if json.loads(r["rejection_codes"]) == ["session_outside"]]
    for row in late:
        session = json.loads(row["envelope_json"])["session"]
        assert session["evaluated"] is True and session["iana_overlap"] is False
