"""MT5 demo adapter: demo-only, idempotent, no blind retry, broker constraints.

Runs against a fake MT5 trading module only. The adapter is not wired into the
runtime and the execution gate refuses demo mode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trantor.execution.mt5_demo import TRANTOR_MAGIC, DemoExecutionRefused, MT5DemoBroker
from trantor.models import OrderRequest, OrderStatus, OrderType, Side, TimeInForce

from runtime_helpers import symbol_info

UTC = timezone.utc


class FakeTradingMT5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_TIME_GTC = 0

    def __init__(self, *, demo: bool = True) -> None:
        self.trade_mode = 0 if demo else 2
        self.positions: list[SimpleNamespace] = []
        self.deals: list[SimpleNamespace] = []
        self.sent: list[dict] = []
        self.send_result: object = "fill"
        self.check_retcode = 0
        self.info = symbol_info(trade_stops_level=10, trade_freeze_level=5)

    def account_info(self):
        return SimpleNamespace(trade_mode=self.trade_mode)

    def symbol_info(self, symbol):
        return SimpleNamespace(**self.info)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=2000.0, ask=2000.2)

    def positions_get(self, symbol=None, ticket=None):
        if ticket is not None:
            return tuple(p for p in self.positions if p.ticket == ticket)
        return tuple(self.positions)

    def history_deals_get(self, since, until):
        return tuple(self.deals)

    def order_check(self, payload):
        return SimpleNamespace(retcode=self.check_retcode)

    def order_send(self, payload):
        self.sent.append(payload)
        if self.send_result == "raise":
            raise TimeoutError("IPC timeout")
        if self.send_result is None:
            return None
        if self.send_result == "requote":
            return SimpleNamespace(retcode=10004, volume=0.0, price=0.0, order=0)
        if payload["action"] == self.TRADE_ACTION_SLTP:
            return SimpleNamespace(retcode=10009)
        volume = payload["volume"] / 2 if self.send_result == "partial" else payload["volume"]
        if "position" not in payload:
            self.positions.append(
                SimpleNamespace(ticket=900 + len(self.positions), magic=TRANTOR_MAGIC, comment=payload["comment"], volume=volume, price_open=payload["price"], type=payload["type"], time=1_790_000_000, sl=payload["sl"], tp=payload["tp"])
            )
        return SimpleNamespace(retcode=10010 if self.send_result == "partial" else 10009, volume=volume, price=payload["price"], order=5000 + len(self.sent))


def _request(cid: str = "trn-ent-0123456789abcdef0123", qty: float = 0.04) -> OrderRequest:
    return OrderRequest(cid, "sig", "XAUUSD-P", Side.BUY, qty, OrderType.MARKET, TimeInForce.IOC, datetime(2026, 11, 4, tzinfo=UTC))


def test_refuses_non_demo_account() -> None:
    broker = MT5DemoBroker(FakeTradingMT5(demo=False))
    with pytest.raises(DemoExecutionRefused, match="demo"):
        broker.submit_order(_request(), stop=1990.0, take_profit=2020.0)


def test_fill_then_resubmission_is_idempotent() -> None:
    mt5 = FakeTradingMT5()
    broker = MT5DemoBroker(mt5)
    first = broker.submit_order(_request(), stop=1990.0, take_profit=2020.0)
    assert first.status is OrderStatus.FILLED and first.average_fill_price == 2000.2
    again = broker.submit_order(_request(), stop=1990.0, take_profit=2020.0)
    assert again.status is OrderStatus.FILLED
    assert len(mt5.sent) == 1  # never sent twice
    assert mt5.sent[0]["magic"] == TRANTOR_MAGIC and len(mt5.sent[0]["comment"]) <= 31


def test_unknown_outcome_is_not_retried() -> None:
    mt5 = FakeTradingMT5()
    mt5.send_result = "raise"
    broker = MT5DemoBroker(mt5)
    state = broker.submit_order(_request(), stop=1990.0, take_profit=2020.0)
    assert state.status is OrderStatus.SUBMITTED and state.filled_quantity == 0
    assert len(mt5.sent) == 1
    assert broker.get_order(_request().client_order_id) is None  # caller must reconcile, not resend


def test_deal_history_found_after_restart_prevents_duplicate() -> None:
    mt5 = FakeTradingMT5()
    mt5.deals.append(SimpleNamespace(magic=TRANTOR_MAGIC, comment=_request().client_order_id[:31], entry=0, volume=0.04, price=2000.1, order=77))
    state = MT5DemoBroker(mt5).submit_order(_request(), stop=1990.0, take_profit=2020.0)
    assert state.status is OrderStatus.FILLED and state.broker_order_id == "77"
    assert mt5.sent == []


def test_partial_fill_and_rejections() -> None:
    mt5 = FakeTradingMT5()
    mt5.send_result = "partial"
    state = MT5DemoBroker(mt5).submit_order(_request(), stop=1990.0, take_profit=2020.0)
    assert state.status is OrderStatus.PARTIALLY_FILLED and state.filled_quantity == pytest.approx(0.02)
    requote = FakeTradingMT5()
    requote.send_result = "requote"
    assert MT5DemoBroker(requote).submit_order(_request(), stop=1990.0, take_profit=2020.0).status is OrderStatus.REJECTED
    failed_check = FakeTradingMT5()
    failed_check.check_retcode = 10019
    assert MT5DemoBroker(failed_check).submit_order(_request(), stop=1990.0, take_profit=2020.0).status is OrderStatus.REJECTED
    assert failed_check.sent == []


def test_stop_validation_against_broker_levels() -> None:
    broker = MT5DemoBroker(FakeTradingMT5())
    with pytest.raises(DemoExecutionRefused, match="stops level"):
        broker.submit_order(_request(), stop=2000.15, take_profit=2020.0)
    with pytest.raises(DemoExecutionRefused, match="wrong side"):
        broker.submit_order(_request(), stop=2010.0, take_profit=2020.0)


def test_modify_never_widens_and_partial_close_and_reconcile() -> None:
    mt5 = FakeTradingMT5()
    broker = MT5DemoBroker(mt5)
    broker.submit_order(_request(), stop=1990.0, take_profit=2020.0)
    ticket = mt5.positions[0].ticket
    with pytest.raises(DemoExecutionRefused, match="never moved further"):
        broker.modify_stops(ticket, side=Side.BUY, stop=1980.0, take_profit=2020.0)
    assert broker.modify_stops(ticket, side=Side.BUY, stop=1995.0, take_profit=2020.0)
    closed = broker.close_partial(ticket, 0.02, client_order_id="trn-tp1-abc")
    assert closed.status is OrderStatus.FILLED
    with pytest.raises(DemoExecutionRefused):
        broker.close_partial(ticket, 1.0, client_order_id="trn-tp1-x")
    report = broker.reconcile({_request().client_order_id: 0.04})
    assert report["consistent"]
    mismatch = broker.reconcile({})
    assert not mismatch["consistent"] and "unknown internally" in mismatch["issues"][0]
    assert broker.get_positions()[0].side is Side.BUY


def test_runtime_never_constructs_the_demo_adapter() -> None:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "trantor"
    users = [p for p in root.rglob("*.py") if "MT5DemoBroker" in p.read_text(encoding="utf-8") and p.name != "mt5_demo.py"]
    assert users == []
