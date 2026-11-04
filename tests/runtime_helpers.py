"""Deterministic fakes for runtime tests. No terminal, network or broker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from trantor.runtime.candles import RawCandle
from trantor.runtime.mt5_gateway import Tick

UTC = timezone.utc
RESEARCH_DATA_PRESENT = None


def research_data_available() -> bool:
    global RESEARCH_DATA_PRESENT
    if RESEARCH_DATA_PRESENT is None:
        try:
            from trantor.research.backtest.data import load_research_data

            load_research_data()
            RESEARCH_DATA_PRESENT = True
        except Exception:
            RESEARCH_DATA_PRESENT = False
    return RESEARCH_DATA_PRESENT


def symbol_info(**overrides: Any) -> dict[str, Any]:
    info = {
        "name": "XAUUSD-P",
        "description": "Gold vs US Dollar",
        "path": "Metals\\XAUUSD-P",
        "custom": False,
        "currency_base": "XAU",
        "currency_profit": "USD",
        "currency_margin": "XAU",
        "trade_contract_size": 100.0,
        "trade_tick_size": 0.01,
        "trade_tick_value": 1.0,
        "trade_tick_value_profit": 1.0,
        "trade_tick_value_loss": 1.0,
        "point": 0.01,
        "digits": 2,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "volume_limit": 0.0,
        "trade_mode": 4,
        "trade_calc_mode": 2,
        "trade_exemode": 2,
        "filling_mode": 3,
        "trade_stops_level": 0,
        "trade_freeze_level": 0,
        "swap_mode": 1,
        "swap_long": -40.0,
        "swap_short": 20.0,
        "swap_rollover3days": 3,
        "margin_initial": 0.0,
        "margin_maintenance": 0.0,
        "spread": 20,
        "spread_float": True,
    }
    info.update(overrides)
    return info


def account(**overrides: Any) -> dict[str, Any]:
    data = {
        "login_masked": "*****123",
        "server": "FortunePrime-Demo",
        "trade_mode": "demo",
        "is_demo": True,
        "currency": "USD",
        "leverage": 500,
        "margin_mode": 2,
        "trade_allowed": True,
        "trade_expert": True,
        "balance": 10000.0,
        "equity": 10000.0,
    }
    data.update(overrides)
    return data


class FakeMT5:
    """Minimal stand-in for the official MetaTrader5 module (read-only API)."""

    TIMEFRAME_M1 = 1
    TIMEFRAME_M15 = 15
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self) -> None:
        self.initialize_ok = True
        self.connected = True
        self.raise_on: set[str] = set()
        self.tick_value: dict[str, Any] | None = {"time": 0, "time_msc": 0, "bid": 2000.0, "ask": 2000.2, "last": 0.0}
        self.rates_rows: dict[int, list[dict[str, Any]]] = {}
        self.calls: list[str] = []
        self.login = 12345678

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name in self.raise_on:
            raise RuntimeError(f"{name} failed")

    def initialize(self, path: str, timeout: int = 0) -> bool:
        self._record("initialize")
        return self.initialize_ok

    def shutdown(self) -> None:
        self.calls.append("shutdown")

    def last_error(self) -> tuple[int, str]:
        return (-10004, "No IPC connection")

    def terminal_info(self) -> Any:
        self._record("terminal_info")
        return SimpleNamespace(connected=self.connected, build=5000, trade_allowed=False, tradeapi_disabled=False, ping_last=25000, company="Fortune Prime")

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self._record("symbol_select")
        return symbol == "XAUUSD-P"

    def account_info(self) -> Any:
        self._record("account_info")
        return SimpleNamespace(login=self.login, server="FortunePrime-Demo", company="Fortune Prime", trade_mode=0, currency="USD", leverage=500, margin_mode=2, trade_allowed=True, trade_expert=True, balance=10000.0, equity=10000.0, margin=0.0, margin_free=10000.0)

    def symbol_info(self, symbol: str) -> Any:
        self._record("symbol_info")
        return SimpleNamespace(**symbol_info())

    def symbol_info_tick(self, symbol: str) -> Any:
        self._record("symbol_info_tick")
        return None if self.tick_value is None else SimpleNamespace(**self.tick_value)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any:
        self._record("copy_rates_from_pos")
        return self.rates_rows.get(timeframe)

    def order_calc_profit(self, action: int, symbol: str, volume: float, open_: float, close: float) -> float:
        self._record("order_calc_profit")
        direction = 1 if action == self.ORDER_TYPE_BUY else -1
        return direction * (close - open_) * 100.0 * volume

    def order_calc_margin(self, action: int, symbol: str, volume: float, price: float) -> float:
        self._record("order_calc_margin")
        return price * 100.0 * volume / 500

    def history_deals_get(self, since: datetime, until: datetime, group: str = "") -> Any:
        self._record("history_deals_get")
        return ()


@dataclass
class FakeSource:
    """Programmable LiveSource for runtime fault-injection tests."""

    now: datetime
    offset: timedelta = timedelta(hours=2)
    connected: bool = True
    bid: float | None = 2000.0
    ask: float | None = 2000.3
    tick_lag: timedelta = timedelta(seconds=0)
    frozen_tick: datetime | None = None
    bars: dict[str, list[RawCandle]] = field(default_factory=dict)
    raise_rates: bool = False
    closed: bool = False

    def health(self) -> dict[str, Any]:
        return {"connected": self.connected, "error": None if self.connected else "terminal offline"}

    def tick(self) -> Tick | None:
        from trantor.market_data.providers import ProviderUnavailableError

        if not self.connected:
            raise ProviderUnavailableError("not connected")
        raw = self.frozen_tick or (self.now + self.offset - self.tick_lag)
        return Tick(raw_time=raw, bid=self.bid, ask=self.ask, last=None)

    def rates(self, timeframe: str, count: int) -> list[RawCandle] | None:
        from trantor.market_data.providers import ProviderUnavailableError

        if self.raise_rates or not self.connected:
            raise ProviderUnavailableError("rates unavailable")
        return list(self.bars.get(timeframe, []))[-count:]

    def symbol_info(self) -> dict[str, Any]:
        return symbol_info()

    def account(self) -> dict[str, Any]:
        return account()

    def calc_profit(self, buy: bool, volume: float, open_: float, close: float) -> float:
        return (close - open_) * 100.0 * volume * (1 if buy else -1)

    def calc_margin(self, buy: bool, volume: float, price: float) -> float:
        return price * 100.0 * volume / 500

    def deal_commissions(self, since: datetime, until: datetime) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        self.closed = True


def m15_series(server_start: datetime, count: int, *, price: float = 2000.0, step: float = 0.1) -> list[RawCandle]:
    rows = []
    for index in range(count):
        at = server_start + timedelta(minutes=15 * index)
        open_ = price + index * step
        rows.append(
            RawCandle(
                time=int(at.timestamp()),
                open=open_,
                high=open_ + 1.0,
                low=open_ - 1.0,
                close=open_ + step,
                tick_volume=100 + index,
            )
        )
    return rows
