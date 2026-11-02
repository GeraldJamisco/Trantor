"""Typed domain models for the Trantor foundation.

The models intentionally keep strategy intent (a :class:`TradeSignal`), risk
approval (a :class:`RiskAssessment`), order intent (an :class:`OrderRequest`),
and broker-confirmed order state (an :class:`OrderState`) separate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "AssetClass",
    "MarketInstrument",
    "MarketObservation",
    "OrderRequest",
    "OrderState",
    "OrderStatus",
    "OrderType",
    "Position",
    "RiskAssessment",
    "Side",
    "TimeInForce",
    "TradeRecord",
    "TradeSignal",
    "TradingMode",
]


class AssetClass(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    GOLD_SPOT = "gold_spot"
    GOLD_ETF = "gold_etf"
    GOLD_FUTURES = "gold_futures"
    # Broker-provided gold instrument whose product identity has not been
    # verified. This deliberately does not imply spot, futures, or CFD.
    UNVERIFIED_GOLD = "unverified_gold"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TradingMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(StrEnum):
    """Lifecycle states reported by an execution adapter.

    ``SUBMITTED`` and ``ACKNOWLEDGED`` mean the broker received the request.
    They do not mean it was filled. Only ``FILLED`` represents a complete fill.
    """

    PENDING_SUBMISSION = "pending_submission"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _require_bool(value: object, field: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")


def _require_finite(
    value: object,
    field: str,
    *,
    min_value: float | None = None,
    strictly_greater: bool = False,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{field} must be finite") from None
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    if min_value is not None:
        if strictly_greater and not number > min_value:
            raise ValueError(f"{field} must be greater than {min_value}")
        if not strictly_greater and not number >= min_value:
            raise ValueError(f"{field} must be at least {min_value}")


def _require_aware(value: object, field: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_enum(value: object, expected: type[StrEnum], field: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"{field} must be a {expected.__name__}")


@dataclass(frozen=True, slots=True)
class MarketInstrument:
    symbol: str
    asset_class: AssetClass
    exchange: str
    currency: str
    timezone: str

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_enum(self.asset_class, AssetClass, "asset_class")
        _require_text(self.exchange, "exchange")
        _require_text(self.currency, "currency")
        _require_text(self.timezone, "timezone")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {self.timezone!r}") from None


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """One validated market-data bar or observation.

    ``volume`` is optional because not every provider or asset class supplies
    it. It is never synthesized as zero when absent.
    """

    symbol: str
    timeframe: str
    observed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_text(self.timeframe, "timeframe")
        _require_aware(self.observed_at, "observed_at")
        for field in ("open", "high", "low", "close"):
            _require_finite(getattr(self, field), field, min_value=0.0, strictly_greater=True)
        if self.volume is not None:
            _require_finite(self.volume, "volume", min_value=0.0)
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be greater than or equal to open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be less than or equal to open, close, and high")


@dataclass(frozen=True, slots=True)
class TradeSignal:
    """A strategy's non-executable trading intent.

    A signal has no quantity, order type, brokerage identifier, or submission
    state. Turning it into an order requires a risk assessment and the central
    order authorizer.
    """

    signal_id: str
    strategy_name: str
    strategy_version: str
    symbol: str
    side: Side
    timeframe: str
    generated_at: datetime
    reference_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        for field in (
            "signal_id",
            "strategy_name",
            "strategy_version",
            "symbol",
            "timeframe",
        ):
            _require_text(getattr(self, field), field)
        _require_enum(self.side, Side, "side")
        _require_aware(self.generated_at, "generated_at")
        _require_finite(self.reference_price, "reference_price", min_value=0.0, strictly_greater=True)
        if not isinstance(self.rationale, str):
            raise ValueError("rationale must be a string")

        if self.stop_loss is not None:
            _require_finite(self.stop_loss, "stop_loss", min_value=0.0, strictly_greater=True)
        if self.take_profit is not None:
            _require_finite(self.take_profit, "take_profit", min_value=0.0, strictly_greater=True)

        if self.side is Side.BUY:
            if self.stop_loss is not None and self.stop_loss >= self.reference_price:
                raise ValueError("a buy stop_loss must be below reference_price")
            if self.take_profit is not None and self.take_profit <= self.reference_price:
                raise ValueError("a buy take_profit must be above reference_price")
        else:
            if self.stop_loss is not None and self.stop_loss <= self.reference_price:
                raise ValueError("a sell stop_loss must be above reference_price")
            if self.take_profit is not None and self.take_profit >= self.reference_price:
                raise ValueError("a sell take_profit must be below reference_price")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """The risk engine's decision about one signal."""

    signal_id: str
    approved: bool
    reason: str
    quantity: float = 0.0
    risk_amount: float = 0.0
    max_risk_amount: float = 0.0
    stop_price: float | None = None
    estimated_notional: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.signal_id, "signal_id")
        _require_bool(self.approved, "approved")
        _require_text(self.reason, "reason")
        _require_finite(self.quantity, "quantity", min_value=0.0)
        _require_finite(self.risk_amount, "risk_amount", min_value=0.0)
        _require_finite(self.max_risk_amount, "max_risk_amount", min_value=0.0)
        if self.stop_price is not None:
            _require_finite(self.stop_price, "stop_price", min_value=0.0, strictly_greater=True)
        if self.estimated_notional is not None:
            _require_finite(
                self.estimated_notional,
                "estimated_notional",
                min_value=0.0,
                strictly_greater=True,
            )

        if self.approved:
            if self.quantity <= 0:
                raise ValueError("an approved risk assessment must have positive quantity")
            if self.max_risk_amount <= 0:
                raise ValueError("an approved risk assessment must have a positive max_risk_amount")
            if self.risk_amount <= 0:
                raise ValueError("an approved risk assessment must have a positive risk_amount")
            if self.risk_amount > self.max_risk_amount + 1e-9:
                raise ValueError("risk_amount cannot exceed max_risk_amount")
            if self.stop_price is None:
                raise ValueError("an approved risk assessment must have a stop_price")
        else:
            if self.quantity != 0 or self.risk_amount != 0:
                raise ValueError("a rejected risk assessment must have zero quantity and risk_amount")


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """An explicit request to a broker adapter, never produced by a strategy.

    ``client_order_id`` is the idempotency key. Execution code must reserve it
    through the central order authorizer before submitting anything.
    """

    client_order_id: str
    signal_id: str
    symbol: str
    side: Side
    quantity: float
    order_type: OrderType
    time_in_force: TimeInForce
    created_at: datetime
    limit_price: float | None = None
    stop_price: float | None = None

    def __post_init__(self) -> None:
        for field in ("client_order_id", "signal_id", "symbol"):
            _require_text(getattr(self, field), field)
        _require_enum(self.side, Side, "side")
        _require_finite(self.quantity, "quantity", min_value=0.0, strictly_greater=True)
        _require_enum(self.order_type, OrderType, "order_type")
        _require_enum(self.time_in_force, TimeInForce, "time_in_force")
        _require_aware(self.created_at, "created_at")
        if self.limit_price is not None:
            _require_finite(self.limit_price, "limit_price", min_value=0.0, strictly_greater=True)
        if self.stop_price is not None:
            _require_finite(self.stop_price, "stop_price", min_value=0.0, strictly_greater=True)

        if self.order_type is OrderType.MARKET and (
            self.limit_price is not None or self.stop_price is not None
        ):
            raise ValueError("a market order must not include limit_price or stop_price")
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError(f"{self.order_type.value} order requires limit_price")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError(f"{self.order_type.value} order requires stop_price")
        if self.order_type is OrderType.LIMIT and self.stop_price is not None:
            raise ValueError("a limit order must not include stop_price")
        if self.order_type is OrderType.STOP and self.limit_price is not None:
            raise ValueError("a stop order must not include limit_price")


@dataclass(frozen=True, slots=True)
class OrderState:
    """A broker or paper-adapter order snapshot.

    The safety module and callers must consult ``status`` instead of treating a
    submitted request as filled.
    """

    client_order_id: str
    status: OrderStatus
    requested_quantity: float
    updated_at: datetime
    broker_order_id: str | None = None
    filled_quantity: float = 0.0
    average_fill_price: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.client_order_id, "client_order_id")
        _require_enum(self.status, OrderStatus, "status")
        _require_finite(
            self.requested_quantity,
            "requested_quantity",
            min_value=0.0,
            strictly_greater=True,
        )
        _require_aware(self.updated_at, "updated_at")
        _require_finite(self.filled_quantity, "filled_quantity", min_value=0.0)
        if self.broker_order_id is not None:
            _require_text(self.broker_order_id, "broker_order_id")
        if self.average_fill_price is not None:
            _require_finite(
                self.average_fill_price,
                "average_fill_price",
                min_value=0.0,
                strictly_greater=True,
            )

        if self.filled_quantity > self.requested_quantity + 1e-9:
            raise ValueError("filled_quantity cannot exceed requested_quantity")
        if self.filled_quantity > 0 and self.average_fill_price is None:
            raise ValueError("a filled quantity requires average_fill_price")
        if self.filled_quantity == 0 and self.average_fill_price is not None:
            raise ValueError("average_fill_price requires a positive filled_quantity")

        if self.status in (
            OrderStatus.PENDING_SUBMISSION,
            OrderStatus.SUBMITTED,
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.REJECTED,
        ) and self.filled_quantity != 0:
            raise ValueError(f"{self.status.value} cannot have a filled quantity")
        if self.status is OrderStatus.PARTIALLY_FILLED:
            if not 0 < self.filled_quantity < self.requested_quantity:
                raise ValueError("partially_filled requires 0 < filled_quantity < requested_quantity")
        if self.status is OrderStatus.FILLED:
            if not math.isclose(self.filled_quantity, self.requested_quantity, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("filled status requires the full requested quantity")
        if self.status is OrderStatus.CANCELED and self.filled_quantity >= self.requested_quantity:
            raise ValueError("a fully filled order cannot be marked canceled")

    @property
    def is_filled(self) -> bool:
        """True only for a complete broker-confirmed fill."""
        return self.status is OrderStatus.FILLED

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, self.requested_quantity - self.filled_quantity)


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    side: Side
    quantity: float
    average_entry_price: float
    opened_at: datetime
    stop_price: float | None = None
    take_profit_price: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_enum(self.side, Side, "side")
        _require_finite(self.quantity, "quantity", min_value=0.0, strictly_greater=True)
        _require_finite(
            self.average_entry_price,
            "average_entry_price",
            min_value=0.0,
            strictly_greater=True,
        )
        _require_aware(self.opened_at, "opened_at")
        if self.stop_price is not None:
            _require_finite(self.stop_price, "stop_price", min_value=0.0, strictly_greater=True)
        if self.take_profit_price is not None:
            _require_finite(
                self.take_profit_price,
                "take_profit_price",
                min_value=0.0,
                strictly_greater=True,
            )


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: str
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    entry_time: datetime
    exit_price: float | None = None
    exit_time: datetime | None = None
    fees: float = 0.0
    realized_pnl: float | None = None
    r_multiple: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.trade_id, "trade_id")
        _require_text(self.symbol, "symbol")
        _require_enum(self.side, Side, "side")
        _require_finite(self.quantity, "quantity", min_value=0.0, strictly_greater=True)
        _require_finite(self.entry_price, "entry_price", min_value=0.0, strictly_greater=True)
        _require_aware(self.entry_time, "entry_time")
        _require_finite(self.fees, "fees", min_value=0.0)
        if self.exit_price is not None:
            _require_finite(self.exit_price, "exit_price", min_value=0.0, strictly_greater=True)
        if self.exit_time is not None:
            _require_aware(self.exit_time, "exit_time")
        if self.realized_pnl is not None:
            _require_finite(self.realized_pnl, "realized_pnl")
        if self.r_multiple is not None:
            _require_finite(self.r_multiple, "r_multiple")

        has_exit_data = any(
            value is not None
            for value in (self.exit_price, self.exit_time, self.realized_pnl, self.r_multiple)
        )
        if has_exit_data:
            if self.exit_price is None or self.exit_time is None or self.realized_pnl is None:
                raise ValueError(
                    "a closed trade requires exit_price, exit_time, and realized_pnl"
                )
            if self.exit_time < self.entry_time:
                raise ValueError("exit_time cannot be before entry_time")
        else:
            if self.realized_pnl is not None or self.r_multiple is not None:
                raise ValueError("an open trade cannot have realized P&L or an R multiple")

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None
