"""Execution gate, paper broker, kill switch and notification safety."""

from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trantor.config import ExecutionMode
from trantor.execution.gate import EffectiveLimits, ExecutionGate, KillSwitch, client_order_id_for
from trantor.execution.paper import PaperBroker
from trantor.models import OrderRequest, OrderStatus, OrderType, Side, TimeInForce
from trantor.monitoring.notify import Notifier, TelegramTransport
from trantor.runtime.replay import replay_settings
from trantor.runtime.store import RuntimeStore
from trantor.safety import ExecutionBlockedError
from trantor.strategy.xauusd_p import XAUUSDPMTFParameters

UTC = timezone.utc


def _gate(tmp_path: Path, mode: ExecutionMode = ExecutionMode.PAPER, **settings_overrides) -> ExecutionGate:
    settings = replace(replay_settings(mode), **settings_overrides)
    return ExecutionGate(settings, KillSwitch(tmp_path / "STOP"), EffectiveLimits.combine(settings, XAUUSDPMTFParameters()))


def _authorize(gate: ExecutionGate, **overrides) -> str:
    args = dict(
        decision_id="dec-abc",
        risk_eligible=True,
        risk_fraction=0.005,
        open_positions=0,
        entries_today=0,
        daily_loss_fraction=0.0,
        blockers=[],
        reconciliation_consistent=True,
    )
    args.update(overrides)
    return gate.authorize_entry(**args)


def test_paper_entry_authorized_with_deterministic_client_order_id(tmp_path: Path) -> None:
    cid = _authorize(_gate(tmp_path))
    assert cid == client_order_id_for("dec-abc") and cid.startswith("trn-ent-")
    assert client_order_id_for("dec-abc") != client_order_id_for("dec-abd")


@pytest.mark.parametrize("mode", [ExecutionMode.DISABLED, ExecutionMode.RESEARCH, ExecutionMode.DEMO, ExecutionMode.LIVE])
def test_non_paper_modes_never_authorize(tmp_path: Path, mode: ExecutionMode) -> None:
    with pytest.raises(ExecutionBlockedError):
        _authorize(_gate(tmp_path, mode))


def test_paper_mode_requires_paper_adapter_and_live_flag_false(tmp_path: Path) -> None:
    with pytest.raises(ExecutionBlockedError):
        _authorize(_gate(tmp_path, broker_adapter="mt5"))
    with pytest.raises(ExecutionBlockedError):
        _authorize(_gate(tmp_path, live_trading_enabled=True))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"risk_eligible": False}, "risk evaluation"),
        ({"risk_fraction": 0.02}, "ceiling"),
        ({"risk_fraction": 0.0}, "ceiling"),
        ({"open_positions": 2}, "concurrent"),
        ({"entries_today": 3}, "daily entries"),
        ({"daily_loss_fraction": 0.03}, "daily loss"),
        ({"blockers": ["feed stale"]}, "feed stale"),
        ({"reconciliation_consistent": False}, "reconciliation"),
    ],
)
def test_gate_rechecks_every_limit(tmp_path: Path, overrides: dict, message: str) -> None:
    with pytest.raises(ExecutionBlockedError, match=message):
        _authorize(_gate(tmp_path), **overrides)


def test_stricter_settings_win_over_strategy_ceilings(tmp_path: Path) -> None:
    gate = _gate(tmp_path, max_risk_per_trade_fraction=0.005, max_daily_loss_fraction=0.02, max_concurrent_positions=5)
    assert gate.limits.max_risk_fraction == 0.005
    assert gate.limits.max_daily_loss_fraction == 0.02
    assert gate.limits.max_concurrent_positions == 2  # strategy is stricter here
    with pytest.raises(ExecutionBlockedError):
        _authorize(gate, risk_fraction=0.01)


def test_kill_switch_engage_status_release(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    switch = gate.kill_switch
    switch.engage("operator test", actor="tester")
    assert switch.status()["engaged"] and switch.status()["reason"] == "operator test"
    with pytest.raises(ExecutionBlockedError, match="emergency stop"):
        _authorize(gate)
    assert switch.release() and not switch.engaged()
    assert switch.release() is False
    (tmp_path / "STOP").write_text("garbage", encoding="utf-8")
    assert switch.engaged() and switch.status()["engaged"]


def _request(order_type: OrderType = OrderType.MARKET) -> OrderRequest:
    return OrderRequest(
        client_order_id="trn-ent-1",
        signal_id="sig",
        symbol="XAUUSD-P",
        side=Side.BUY,
        quantity=1.0,
        order_type=order_type,
        time_in_force=TimeInForce.IOC,
        created_at=datetime(2026, 11, 4, tzinfo=UTC),
        limit_price=2000.0 if order_type is OrderType.LIMIT else None,
    )


def test_paper_broker_fills_at_reference_and_is_idempotent() -> None:
    broker = PaperBroker()
    broker.set_reference_price("trn-ent-1", 2001.5)
    first = broker.submit_order(_request())
    assert first.status is OrderStatus.FILLED and first.average_fill_price == 2001.5
    broker.set_reference_price("trn-ent-1", 1.0)
    assert broker.submit_order(_request()) == first
    assert broker.get_order("trn-ent-1") == first


def test_paper_broker_rejects_without_reference_or_non_market() -> None:
    broker = PaperBroker()
    assert broker.submit_order(_request()).status is OrderStatus.REJECTED
    other = PaperBroker()
    other.set_reference_price("trn-ent-1", 2000.0)
    assert other.submit_order(_request(OrderType.LIMIT)).status is OrderStatus.REJECTED


def test_paper_broker_is_not_an_external_adapter() -> None:
    import inspect

    import trantor.execution.paper as paper

    source = inspect.getsource(paper)
    for forbidden in ("MetaTrader5", "order_send", "urllib", "socket", "requests"):
        assert forbidden not in source


# -- notifications ----------------------------------------------------------------

class Recorder:
    name = "recorder"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("delivery failed with token 999:TOPSECRET")
        self.sent.append(text)


@pytest.fixture()
def store(tmp_path: Path):
    db = RuntimeStore(tmp_path / "n.sqlite3")
    yield db
    db.close()


def test_notifications_are_deduplicated_and_redacted(store: RuntimeStore) -> None:
    transport = Recorder()
    notifier = Notifier(store, transport, secrets=["999:TOPSECRET"])
    assert notifier.notify("setup", "k1", "token 999:TOPSECRET leaked?") == "queued"
    assert notifier.notify("setup", "k1", "again") == "duplicate"
    notifier.deliver_pending_now()
    assert transport.sent == ["[TRANTOR] token [REDACTED] leaked?"]
    assert store.notifications()[0]["status"] == "sent"


def test_delivery_failure_is_isolated_and_redacted(store: RuntimeStore) -> None:
    notifier = Notifier(store, Recorder(fail=True), secrets=["999:TOPSECRET"])
    notifier.notify("risk", "k", "x")
    notifier.deliver_pending_now()
    row = store.notifications()[0]
    assert row["status"] == "failed" and "TOPSECRET" not in row["last_error"]
    assert notifier.failures == 1


def test_disabled_notifier_records_without_sending(store: RuntimeStore) -> None:
    notifier = Notifier(store, None)
    assert not notifier.enabled
    assert notifier.notify("lifecycle", "start", "hello") == "disabled"


def test_full_queue_drops_instead_of_blocking(store: RuntimeStore) -> None:
    notifier = Notifier(store, Recorder(), max_queue=1)
    assert notifier.notify("a", "1", "x") == "queued"
    assert notifier.notify("a", "2", "x") == "dropped"


def test_background_thread_delivers(store: RuntimeStore) -> None:
    transport = Recorder()
    notifier = Notifier(store, transport)
    notifier.start()
    notifier.notify("a", "1", "hello")
    notifier.stop()
    assert transport.sent


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_telegram_transport_never_leaks_token() -> None:
    token = "123456:ABCDEF-secret"
    calls = []

    def ok_opener(request, timeout):
        calls.append(request.full_url)
        return _Response(json.dumps({"ok": True}).encode())

    TelegramTransport(token, "42", opener=ok_opener).send("hi")
    assert calls

    def failing_opener(request, timeout):
        raise urllib.error.URLError(f"cannot reach {request.full_url}")

    with pytest.raises(RuntimeError) as excinfo:
        TelegramTransport(token, "42", opener=failing_opener).send("hi")
    assert token not in str(excinfo.value)

    def rejecting_opener(request, timeout):
        return _Response(json.dumps({"ok": False, "description": "bad chat"}).encode())

    with pytest.raises(RuntimeError, match="bad chat"):
        TelegramTransport(token, "42", opener=rejecting_opener).send("hi")
    with pytest.raises(ValueError):
        TelegramTransport("", "42")
