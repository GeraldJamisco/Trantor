"""Read-only MetaTrader 5 gateway with connection health and reconnection.

The official ``MetaTrader5`` package holds one global connection per process.
This gateway serialises every call behind a lock, records connection events,
and reconnects with exponential backoff. It exposes **no** order, position
modification, or account-changing operation. Account fields that identify the
owner are masked before they leave this module.
"""

from __future__ import annotations

import math
import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

from ..market_data.providers import ProviderUnavailableError
from .candles import RawCandle

__all__ = [
    "ACCOUNT_TRADE_MODES",
    "MT5Gateway",
    "Tick",
    "mask_login",
]

UTC = timezone.utc
ACCOUNT_TRADE_MODES = {0: "demo", 1: "contest", 2: "real"}
_TIMEFRAMES = {
    "1m": "TIMEFRAME_M1",
    "15m": "TIMEFRAME_M15",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}


def mask_login(login: object) -> str | None:
    if login is None:
        return None
    text = str(login)
    if len(text) <= 3:
        return "***"
    return "*" * (len(text) - 3) + text[-3:]


@dataclass(frozen=True, slots=True)
class Tick:
    """One broker tick. ``raw_time`` is the MT5 epoch interpreted as UTC."""

    raw_time: datetime
    bid: float | None
    ask: float | None
    last: float | None

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    def as_dict(self) -> dict[str, object]:
        return {
            "raw_time": self.raw_time.isoformat(),
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "spread": self.spread,
        }


def _finite_positive(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    if isinstance(obj, dict):
        return dict(obj)
    return {
        name: getattr(obj, name)
        for name in dir(obj)
        if not name.startswith("_") and not callable(getattr(obj, name, None))
    }


class MT5Gateway:
    """Serialised, reconnecting, read-only access to one MT5 terminal."""

    def __init__(
        self,
        *,
        terminal_path: str | Path,
        symbol: str = "XAUUSD-P",
        mt5_module: Any | None = None,
        timeout_ms: int = 15_000,
        max_backoff_seconds: float = 60.0,
        clock: Callable[[], float] = _time.monotonic,
        on_event: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        if not str(terminal_path).strip():
            raise ValueError("terminal_path must be configured")
        self._terminal_path = str(terminal_path)
        self.symbol = symbol
        self._mt5 = mt5_module
        self._timeout_ms = timeout_ms
        self._lock = threading.RLock()
        self._connected = False
        self._failures = 0
        self._next_attempt = 0.0
        self._max_backoff = max_backoff_seconds
        self._clock = clock
        self._on_event = on_event
        self.reconnect_count = 0
        self.last_error: str | None = None
        self.connected_since: datetime | None = None

    # -- connection -----------------------------------------------------------

    def set_event_handler(self, handler: Callable[[str, str, dict[str, Any]], None]) -> None:
        self._on_event = handler

    def _emit(self, level: str, event: str, detail: dict[str, Any]) -> None:
        if self._on_event is not None:
            try:
                self._on_event(level, event, detail)
            except Exception:
                pass

    def _module(self) -> Any:
        if self._mt5 is None:
            try:
                self._mt5 = import_module("MetaTrader5")
            except Exception as exc:
                raise ProviderUnavailableError("MetaTrader5 package is not importable") from exc
        return self._mt5

    def _error_text(self) -> str:
        try:
            error = self._module().last_error()
        except Exception:
            return "unavailable"
        return f"{error[0]}: {error[1]}" if isinstance(error, tuple) and len(error) >= 2 else repr(error)

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """Attach to the terminal. Returns False (never raises) on failure."""

        with self._lock:
            if self._connected:
                return True
            now = self._clock()
            if now < self._next_attempt:
                return False
            mt5 = self._module()
            try:
                ok = bool(mt5.initialize(self._terminal_path, timeout=self._timeout_ms))
                info = mt5.terminal_info() if ok else None
                ok = ok and info is not None and bool(getattr(info, "connected", False))
                if ok and not mt5.symbol_select(self.symbol, True):
                    ok = False
                    self.last_error = f"symbol {self.symbol} cannot be selected: {self._error_text()}"
            except Exception as exc:  # the package raises arbitrary errors
                ok = False
                self.last_error = f"{type(exc).__name__}: {exc}"
            if not ok:
                if self.last_error is None or "symbol" not in self.last_error:
                    self.last_error = f"initialize failed: {self._error_text()}"
                self._failures += 1
                backoff = min(self._max_backoff, 2 ** min(self._failures - 1, 6))
                self._next_attempt = now + backoff
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                self._emit(
                    "warning",
                    "mt5_connect_failed",
                    {"error": self.last_error, "retry_in_seconds": backoff},
                )
                return False
            was_reconnect = self._failures > 0 or self.connected_since is not None
            self._connected = True
            self._failures = 0
            self.last_error = None
            self.connected_since = datetime.now(UTC)
            if was_reconnect:
                self.reconnect_count += 1
            self._emit(
                "info",
                "mt5_reconnected" if was_reconnect else "mt5_connected",
                {"reconnect_count": self.reconnect_count},
            )
            return True

    def mark_disconnected(self, reason: str) -> None:
        with self._lock:
            if not self._connected:
                return
            self._connected = False
            self.last_error = reason
            self._failures = 1
            self._next_attempt = self._clock() + 1.0
            try:
                self._module().shutdown()
            except Exception:
                pass
            self._emit("error", "mt5_disconnected", {"reason": reason})

    def close(self) -> None:
        with self._lock:
            if self._connected:
                try:
                    self._module().shutdown()
                except Exception:
                    pass
            self._connected = False

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            if not self._connected and not self.connect():
                raise ProviderUnavailableError(f"MT5 is not connected: {self.last_error}")
            try:
                result = getattr(self._module(), name)(*args, **kwargs)
            except Exception as exc:
                self.mark_disconnected(f"{name} raised {type(exc).__name__}: {exc}")
                raise ProviderUnavailableError(f"MT5 {name} failed: {exc}") from exc
            return result

    # -- read-only queries ----------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Check the terminal-to-broker link; marks a disconnect when broken."""

        if not self._connected and not self.connect():
            return {"connected": False, "error": self.last_error}
        try:
            info = self._call("terminal_info")
        except ProviderUnavailableError as exc:
            return {"connected": False, "error": str(exc)}
        if info is None or not bool(getattr(info, "connected", False)):
            self.mark_disconnected("terminal reports no broker connection")
            return {"connected": False, "error": self.last_error}
        data = _as_dict(info)
        return {
            "connected": True,
            "terminal_build": data.get("build"),
            "terminal_trade_allowed": data.get("trade_allowed"),
            "tradeapi_disabled": data.get("tradeapi_disabled"),
            "ping_ms": (
                None if data.get("ping_last") is None else round(float(data["ping_last"]) / 1000, 1)
            ),
            "company": data.get("company"),
            "connected_since": None if self.connected_since is None else self.connected_since.isoformat(),
            "reconnect_count": self.reconnect_count,
        }

    def account(self) -> dict[str, Any] | None:
        """Read-only account snapshot with the login masked."""

        info = self._call("account_info")
        if info is None:
            return None
        data = _as_dict(info)
        mode = data.get("trade_mode")
        return {
            "login_masked": mask_login(data.get("login")),
            "server": data.get("server"),
            "company": data.get("company"),
            "trade_mode": ACCOUNT_TRADE_MODES.get(mode, f"unknown({mode})"),
            "is_demo": mode == 0,
            "currency": data.get("currency"),
            "leverage": data.get("leverage"),
            "margin_mode": data.get("margin_mode"),
            "trade_allowed": data.get("trade_allowed"),
            "trade_expert": data.get("trade_expert"),
            "balance": data.get("balance"),
            "equity": data.get("equity"),
            "margin": data.get("margin"),
            "margin_free": data.get("margin_free"),
        }

    def symbol_info(self) -> dict[str, Any] | None:
        info = self._call("symbol_info", self.symbol)
        return None if info is None else _as_dict(info)

    def tick(self) -> Tick | None:
        tick = self._call("symbol_info_tick", self.symbol)
        if tick is None:
            return None
        data = _as_dict(tick)
        msc = data.get("time_msc")
        seconds = data.get("time")
        if msc:
            raw = datetime.fromtimestamp(int(msc) / 1000, tz=UTC)
        elif seconds:
            raw = datetime.fromtimestamp(int(seconds), tz=UTC)
        else:
            return None
        return Tick(
            raw_time=raw,
            bid=_finite_positive(data.get("bid")),
            ask=_finite_positive(data.get("ask")),
            last=_finite_positive(data.get("last")),
        )

    def rates(self, timeframe: str, count: int) -> list[RawCandle] | None:
        constant = _TIMEFRAMES.get(timeframe)
        if constant is None:
            raise ValueError(f"unsupported timeframe {timeframe!r}")
        mt5 = self._module()
        value = getattr(mt5, constant)
        rows = self._call("copy_rates_from_pos", self.symbol, value, 0, int(count))
        if rows is None:
            return None
        result = []
        for row in rows:
            get = (lambda key, row=row: row[key]) if hasattr(row, "dtype") or isinstance(row, dict) else (
                lambda key, row=row: getattr(row, key)
            )
            result.append(
                RawCandle(
                    time=int(get("time")),
                    open=get("open"),
                    high=get("high"),
                    low=get("low"),
                    close=get("close"),
                    tick_volume=get("tick_volume"),
                    spread=get("spread"),
                )
            )
        return result

    def calc_profit(self, buy: bool, volume: float, price_open: float, price_close: float) -> float | None:
        mt5 = self._module()
        action = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
        value = self._call("order_calc_profit", action, self.symbol, volume, price_open, price_close)
        return None if value is None else float(value)

    def calc_margin(self, buy: bool, volume: float, price: float) -> float | None:
        mt5 = self._module()
        action = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
        value = self._call("order_calc_margin", action, self.symbol, volume, price)
        return None if value is None else float(value)

    def deal_commissions(self, since: datetime, until: datetime) -> list[dict[str, Any]]:
        """Read historical deals for the symbol (read-only) to observe commission."""

        rows = self._call("history_deals_get", since, until, group=f"*{self.symbol}*")
        if rows is None:
            return []
        result = []
        for row in rows:
            data = _as_dict(row)
            if data.get("symbol") != self.symbol:
                continue
            result.append(
                {
                    "volume": data.get("volume"),
                    "commission": data.get("commission"),
                    "fee": data.get("fee"),
                    "swap": data.get("swap"),
                    "entry": data.get("entry"),
                }
            )
        return result
