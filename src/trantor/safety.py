"""Central fail-closed safety checks and order provenance rules.

Nothing in this module talks to a broker. It exists so that any future
execution adapter has one mandatory gate to pass before submitting an order.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections.abc import Iterable, Mapping

from .config import ExecutionMode, Settings, StrategyConfig, StrategyStatus
from .models import OrderRequest, RiskAssessment, TradeSignal, TradingMode

__all__ = [
    "DuplicateOrderError",
    "ExecutionBlockedError",
    "HARD_MAX_CONCURRENT_POSITIONS",
    "HARD_MAX_DAILY_LOSS_FRACTION",
    "HARD_MAX_RISK_PER_TRADE_FRACTION",
    "OrderAuthorizer",
    "SecretRedactingFilter",
    "assert_execution_allowed",
    "redact_secrets",
    "secret_values_from_environment",
    "validate_execution_settings",
    "validate_strategy_for_execution",
]

# Independent defence-in-depth ceilings. Policy files may be stricter, but
# cannot raise these limits.
HARD_MAX_RISK_PER_TRADE_FRACTION = 0.01
HARD_MAX_CONCURRENT_POSITIONS = 10
HARD_MAX_DAILY_LOSS_FRACTION = 0.05

_SECRET_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY")


class ExecutionBlockedError(RuntimeError):
    """Execution is not permitted by the current configuration or state."""


class DuplicateOrderError(ExecutionBlockedError):
    """The client order id has already been authorized in this process."""


def _enforce_fraction(value: object, field_name: str, hard_max: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionBlockedError(f"{field_name} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ExecutionBlockedError(f"{field_name} is invalid") from None
    if not math.isfinite(number) or not 0 < number <= hard_max:
        raise ExecutionBlockedError(
            f"{field_name} must be greater than 0 and at most {hard_max}"
        )


def _enforce_positive_int(value: object, field_name: str, hard_max: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExecutionBlockedError(f"{field_name} must be a positive integer")
    if hard_max is not None and value > hard_max:
        raise ExecutionBlockedError(f"{field_name} must be at most {hard_max}")


def validate_execution_settings(settings: Settings) -> None:
    """Reject any settings that permit anything other than Phase 1 paper mode."""

    if not isinstance(settings, Settings):
        raise ExecutionBlockedError("execution settings are missing or invalid")
    if settings.schema_version != 1:
        raise ExecutionBlockedError("unsupported execution settings schema")
    if not isinstance(settings.execution_mode, ExecutionMode):
        raise ExecutionBlockedError("execution_mode is missing or invalid")
    if settings.execution_mode in (
        ExecutionMode.DISABLED,
        ExecutionMode.RESEARCH,
    ):
        raise ExecutionBlockedError(
            f"execution is disabled by execution_mode={settings.execution_mode.value!r}"
        )
    if settings.execution_mode in (ExecutionMode.DEMO, ExecutionMode.LIVE):
        raise ExecutionBlockedError(
            f"execution_mode={settings.execution_mode.value!r} is not enabled in this phase"
        )
    if settings.execution_mode is not ExecutionMode.PAPER:
        raise ExecutionBlockedError("unsupported execution mode")
    if settings.trading_mode is not TradingMode.PAPER:
        raise ExecutionBlockedError(
            "live trading is disabled; paper mode is the only supported trading mode"
        )
    if settings.live_trading_enabled is not False:
        raise ExecutionBlockedError("live_trading_enabled must remain false")
    if not isinstance(settings.broker_adapter, str) or settings.broker_adapter.lower() != "paper":
        raise ExecutionBlockedError(
            "only the paper broker adapter may be used in this phase"
        )

    _enforce_fraction(
        settings.max_risk_per_trade_fraction,
        "settings.max_risk_per_trade_fraction",
        HARD_MAX_RISK_PER_TRADE_FRACTION,
    )
    _enforce_positive_int(
        settings.max_concurrent_positions,
        "settings.max_concurrent_positions",
        HARD_MAX_CONCURRENT_POSITIONS,
    )
    _enforce_fraction(
        settings.max_daily_loss_fraction,
        "settings.max_daily_loss_fraction",
        HARD_MAX_DAILY_LOSS_FRACTION,
    )


def validate_strategy_for_execution(
    strategy: StrategyConfig,
    settings: Settings,
    *,
    open_positions: int = 0,
    daily_loss_fraction: float = 0.0,
) -> None:
    """Reject placeholder, disabled, inconsistent, or over-limit strategies."""

    if not isinstance(strategy, StrategyConfig):
        raise ExecutionBlockedError("strategy configuration is missing or invalid")
    if strategy.status is not StrategyStatus.VALIDATED:
        raise ExecutionBlockedError(
            f"strategy {strategy.strategy_name!r} is not validated (status={strategy.status.value})"
        )
    if strategy.execution_enabled is not True:
        raise ExecutionBlockedError(f"strategy {strategy.strategy_name!r} is not execution-enabled")
    if not strategy.entry_conditions.conditions:
        raise ExecutionBlockedError("strategy has no entry conditions")
    if strategy.stop_loss.required is not True:
        raise ExecutionBlockedError("strategy does not require a stop-loss")
    if strategy.stop_loss.placeholder_values or (
        strategy.take_profit.enabled and strategy.take_profit.placeholder_values
    ):
        raise ExecutionBlockedError("strategy still contains placeholder exit parameters")

    _enforce_fraction(
        strategy.risk_limits.max_risk_per_trade_fraction,
        "strategy.risk_limits.max_risk_per_trade_fraction",
        HARD_MAX_RISK_PER_TRADE_FRACTION,
    )
    _enforce_fraction(
        strategy.position_sizing.risk_per_trade_fraction,
        "strategy.position_sizing.risk_per_trade_fraction",
        HARD_MAX_RISK_PER_TRADE_FRACTION,
    )
    _enforce_fraction(
        strategy.risk_limits.max_daily_loss_fraction,
        "strategy.risk_limits.max_daily_loss_fraction",
        HARD_MAX_DAILY_LOSS_FRACTION,
    )
    _enforce_positive_int(
        strategy.risk_limits.max_concurrent_positions,
        "strategy.risk_limits.max_concurrent_positions",
        HARD_MAX_CONCURRENT_POSITIONS,
    )

    if (
        strategy.position_sizing.risk_per_trade_fraction
        > strategy.risk_limits.max_risk_per_trade_fraction + 1e-12
    ):
        raise ExecutionBlockedError(
            "strategy position risk exceeds its maximum risk per trade"
        )
    if (
        strategy.position_sizing.risk_per_trade_fraction
        > settings.max_risk_per_trade_fraction + 1e-12
    ):
        raise ExecutionBlockedError(
            "strategy position risk exceeds the machine-level risk limit"
        )
    if (
        strategy.risk_limits.max_risk_per_trade_fraction
        > settings.max_risk_per_trade_fraction + 1e-12
    ):
        raise ExecutionBlockedError(
            "strategy maximum risk exceeds the machine-level risk limit"
        )
    if (
        strategy.risk_limits.max_concurrent_positions
        > settings.max_concurrent_positions
    ):
        raise ExecutionBlockedError(
            "strategy maximum positions exceed the machine-level limit"
        )
    if (
        strategy.risk_limits.max_daily_loss_fraction
        > settings.max_daily_loss_fraction + 1e-12
    ):
        raise ExecutionBlockedError(
            "strategy daily loss limit exceeds the machine-level limit"
        )

    if isinstance(open_positions, bool) or not isinstance(open_positions, int) or open_positions < 0:
        raise ExecutionBlockedError("open_positions must be a non-negative integer")
    if isinstance(daily_loss_fraction, bool) or not isinstance(daily_loss_fraction, (int, float)):
        raise ExecutionBlockedError("daily_loss_fraction must be a number")
    try:
        loss_fraction = float(daily_loss_fraction)
    except (OverflowError, ValueError):
        raise ExecutionBlockedError("daily_loss_fraction is invalid") from None
    if not math.isfinite(loss_fraction) or not 0 <= loss_fraction < 1:
        raise ExecutionBlockedError("daily_loss_fraction must be between 0 and 1")

    if open_positions >= strategy.risk_limits.max_concurrent_positions:
        raise ExecutionBlockedError(
            "strategy maximum concurrent positions has been reached"
        )
    if open_positions >= settings.max_concurrent_positions:
        raise ExecutionBlockedError(
            "machine-level maximum concurrent positions has been reached"
        )
    if loss_fraction >= strategy.risk_limits.max_daily_loss_fraction:
        raise ExecutionBlockedError("strategy daily loss limit has been reached")
    if loss_fraction >= settings.max_daily_loss_fraction:
        raise ExecutionBlockedError("machine-level daily loss limit has been reached")


def assert_execution_allowed(
    settings: Settings,
    strategy: StrategyConfig,
    *,
    open_positions: int = 0,
    daily_loss_fraction: float = 0.0,
) -> None:
    """Run every non-order-specific execution guard. Raises on any failure."""

    validate_execution_settings(settings)
    validate_strategy_for_execution(
        strategy,
        settings,
        open_positions=open_positions,
        daily_loss_fraction=daily_loss_fraction,
    )


class OrderAuthorizer:
    """Mandatory provenance and idempotency gate for future broker submission.

    The authorizer only records that an order was authorized. It does not send
    anything. A future execution adapter must call :meth:`authorize` immediately
    before a single submission attempt and must not retry with a new client order
    id while the prior attempt's broker state is unknown.
    """

    def __init__(self, settings: Settings, strategy: StrategyConfig) -> None:
        assert_execution_allowed(settings, strategy)
        self._settings = settings
        self._strategy = strategy
        self._reserved_client_order_ids: set[str] = set()
        self._lock = threading.Lock()

    def authorize(
        self,
        *,
        signal: TradeSignal,
        risk: RiskAssessment,
        order: OrderRequest,
        open_positions: int = 0,
        daily_loss_fraction: float = 0.0,
    ) -> None:
        if not isinstance(signal, TradeSignal):
            raise ExecutionBlockedError("a TradeSignal is required; a signal is not an order")
        if not isinstance(risk, RiskAssessment):
            raise ExecutionBlockedError("an approved RiskAssessment is required")
        if not isinstance(order, OrderRequest):
            raise ExecutionBlockedError(
                "an OrderRequest is required; a signal or strategy output cannot be submitted"
            )

        assert_execution_allowed(
            self._settings,
            self._strategy,
            open_positions=open_positions,
            daily_loss_fraction=daily_loss_fraction,
        )

        if (
            signal.strategy_name != self._strategy.strategy_name
            or signal.strategy_version != self._strategy.strategy_version
        ):
            raise ExecutionBlockedError("signal was not produced by the configured strategy")
        if risk.approved is not True:
            raise ExecutionBlockedError(f"risk assessment rejected the signal: {risk.reason}")
        if risk.signal_id != signal.signal_id:
            raise ExecutionBlockedError("risk assessment does not belong to this signal")
        if order.signal_id != signal.signal_id:
            raise ExecutionBlockedError("order does not belong to this signal")
        if order.symbol != signal.symbol:
            raise ExecutionBlockedError("order symbol does not match the signal")
        if order.side is not signal.side:
            raise ExecutionBlockedError("order side does not match the signal")
        if not math.isclose(order.quantity, risk.quantity, rel_tol=1e-9, abs_tol=1e-9):
            raise ExecutionBlockedError("order quantity does not match the approved risk quantity")
        if (
            signal.stop_loss is not None
            and risk.stop_price is not None
            and not math.isclose(risk.stop_price, signal.stop_loss, rel_tol=1e-9, abs_tol=1e-9)
        ):
            raise ExecutionBlockedError("risk stop price does not match the signal stop-loss")

        with self._lock:
            if order.client_order_id in self._reserved_client_order_ids:
                raise DuplicateOrderError(
                    f"client_order_id {order.client_order_id!r} has already been authorized"
                )
            self._reserved_client_order_ids.add(order.client_order_id)


def secret_values_from_environment(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return non-empty environment values whose names look secret-bearing."""

    environment = os.environ if environ is None else environ
    values = {
        value
        for key, value in environment.items()
        if value and any(marker in key.upper() for marker in _SECRET_ENV_MARKERS)
    }
    return tuple(sorted(values, key=len, reverse=True))


def redact_secrets(text: str, secrets: Iterable[str] | None = None) -> str:
    """Replace known secret values in a message with ``[REDACTED]``."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    secret_values = (
        secret_values_from_environment() if secrets is None else tuple(secrets)
    )
    result = text
    for secret in sorted({item for item in secret_values if item}, key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    return result


class SecretRedactingFilter(logging.Filter):
    """Logging filter that redacts secret values from formatted messages.

    Future logging setup must attach this filter to every handler that can
    receive application records. It is defence in depth; secrets should never
    be passed to log calls in the first place.
    """

    def __init__(self, secrets: Iterable[str] | None = None) -> None:
        super().__init__()
        self._secrets = (
            secret_values_from_environment() if secrets is None else tuple(secrets)
        )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secrets(message, self._secrets)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True
