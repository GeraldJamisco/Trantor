from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trantor.config import StrategyStatus, load_settings, load_strategy_config
from trantor.execution import BrokerUnavailableError, DisabledBroker
from trantor.models import (
    OrderRequest,
    OrderState,
    OrderStatus,
    OrderType,
    RiskAssessment,
    Side,
    TimeInForce,
    TradeSignal,
    TradingMode,
)
from trantor.safety import (
    DuplicateOrderError,
    ExecutionBlockedError,
    OrderAuthorizer,
    SecretRedactingFilter,
    assert_execution_allowed,
    redact_secrets,
    secret_values_from_environment,
    validate_execution_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = PROJECT_ROOT / "rules" / "gold.json"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.example.json"
NOW = datetime(2026, 10, 22, 14, 30, tzinfo=ZoneInfo("America/New_York"))


@pytest.fixture()
def settings():
    return load_settings(SETTINGS_PATH)


@pytest.fixture()
def validated_config():
    config = load_strategy_config(GOLD_PATH)
    entry_conditions = replace(
        config.entry_conditions,
        conditions=({"type": "test_only"},),
    )
    return replace(
        config,
        status=StrategyStatus.VALIDATED,
        execution_enabled=True,
        entry_conditions=entry_conditions,
        stop_loss=replace(config.stop_loss, placeholder_values=False),
        take_profit=replace(config.take_profit, placeholder_values=False),
    )


def _signal(config, **overrides):
    values = {
        "signal_id": "sig-1",
        "strategy_name": config.strategy_name,
        "strategy_version": config.strategy_version,
        "symbol": "GC",
        "side": Side.BUY,
        "timeframe": "15m",
        "generated_at": NOW,
        "reference_price": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "rationale": "unit test signal",
    }
    values.update(overrides)
    return TradeSignal(**values)


def _risk(**overrides):
    values = {
        "signal_id": "sig-1",
        "approved": True,
        "reason": "approved in unit test",
        "quantity": 10.0,
        "risk_amount": 20.0,
        "max_risk_amount": 50.0,
        "stop_price": 98.0,
        "estimated_notional": 1000.0,
    }
    values.update(overrides)
    return RiskAssessment(**values)


def _order(**overrides):
    values = {
        "client_order_id": "client-1",
        "signal_id": "sig-1",
        "symbol": "GC",
        "side": Side.BUY,
        "quantity": 10.0,
        "order_type": OrderType.MARKET,
        "time_in_force": TimeInForce.DAY,
        "created_at": NOW + timedelta(seconds=1),
    }
    values.update(overrides)
    return OrderRequest(**values)


def test_paper_trading_is_the_default(settings) -> None:
    assert settings.trading_mode is TradingMode.PAPER
    assert settings.live_trading_enabled is False
    assert settings.broker_adapter == "paper"
    validate_execution_settings(settings)


def test_a_validated_paper_strategy_can_pass_the_gate(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk()
    order = _order()
    gate = OrderAuthorizer(settings, validated_config)

    gate.authorize(
        signal=signal,
        risk=risk,
        order=order,
        open_positions=1,
        daily_loss_fraction=0.019,
    )


def test_shipped_placeholder_strategy_cannot_execute(settings) -> None:
    config = load_strategy_config(GOLD_PATH)

    with pytest.raises(ExecutionBlockedError, match="not validated"):
        assert_execution_allowed(settings, config)

    with pytest.raises(ExecutionBlockedError, match="not validated"):
        OrderAuthorizer(settings, config)


def test_live_trading_mode_is_blocked(settings) -> None:
    live_settings = replace(settings, trading_mode=TradingMode.LIVE)

    with pytest.raises(ExecutionBlockedError, match="live trading is disabled"):
        validate_execution_settings(live_settings)


def test_live_trading_flag_is_blocked(settings) -> None:
    armed_settings = replace(settings, live_trading_enabled=True)

    with pytest.raises(ExecutionBlockedError, match="live_trading_enabled"):
        validate_execution_settings(armed_settings)


def test_non_paper_broker_adapter_is_blocked(settings) -> None:
    live_broker_settings = replace(settings, broker_adapter="non_paper_broker")

    with pytest.raises(ExecutionBlockedError, match="paper broker adapter"):
        validate_execution_settings(live_broker_settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_risk_per_trade_fraction", 0),
        ("max_risk_per_trade_fraction", -0.001),
        ("max_risk_per_trade_fraction", 0.02),
        ("max_risk_per_trade_fraction", float("nan")),
        ("max_concurrent_positions", 0),
        ("max_concurrent_positions", 11),
        ("max_concurrent_positions", True),
        ("max_daily_loss_fraction", 0),
        ("max_daily_loss_fraction", 0.06),
    ],
)
def test_unsafe_execution_settings_are_rejected(settings, field: str, value: object) -> None:
    unsafe_settings = replace(settings, **{field: value})

    with pytest.raises(ExecutionBlockedError, match=field):
        validate_execution_settings(unsafe_settings)


def test_strategy_risk_cannot_exceed_machine_policy(settings, validated_config) -> None:
    strategy = replace(
        validated_config,
        risk_limits=replace(
            validated_config.risk_limits,
            max_risk_per_trade_fraction=0.01,
        ),
    )

    with pytest.raises(ExecutionBlockedError, match="maximum risk"):
        assert_execution_allowed(settings, strategy)


def test_strategy_position_count_cannot_exceed_machine_policy(
    settings, validated_config
) -> None:
    strategy = replace(
        validated_config,
        risk_limits=replace(
            validated_config.risk_limits,
            max_concurrent_positions=6,
        ),
    )

    with pytest.raises(ExecutionBlockedError, match="maximum positions"):
        assert_execution_allowed(settings, strategy)


def test_strategy_daily_loss_cannot_exceed_machine_policy(
    settings, validated_config
) -> None:
    strategy = replace(
        validated_config,
        risk_limits=replace(
            validated_config.risk_limits,
            max_daily_loss_fraction=0.03,
        ),
    )

    with pytest.raises(ExecutionBlockedError, match="daily loss"):
        assert_execution_allowed(settings, strategy)


def test_strategy_position_sizing_cannot_exceed_its_own_maximum(
    settings, validated_config
) -> None:
    strategy = replace(
        validated_config,
        position_sizing=replace(
            validated_config.position_sizing,
            risk_per_trade_fraction=0.01,
        ),
    )

    with pytest.raises(ExecutionBlockedError, match="position risk"):
        assert_execution_allowed(settings, strategy)


def test_strategy_must_require_a_stop_loss(settings, validated_config) -> None:
    strategy = replace(
        validated_config,
        stop_loss=replace(validated_config.stop_loss, required=False),
    )

    with pytest.raises(ExecutionBlockedError, match="stop-loss"):
        assert_execution_allowed(settings, strategy)


def test_strategy_cannot_execute_with_placeholder_exit_parameters(
    settings, validated_config
) -> None:
    strategy = replace(
        validated_config,
        stop_loss=replace(validated_config.stop_loss, placeholder_values=True),
    )

    with pytest.raises(ExecutionBlockedError, match="placeholder"):
        assert_execution_allowed(settings, strategy)


def test_strategy_must_have_entry_conditions(settings, validated_config) -> None:
    strategy = replace(
        validated_config,
        entry_conditions=replace(validated_config.entry_conditions, conditions=()),
    )

    with pytest.raises(ExecutionBlockedError, match="entry conditions"):
        assert_execution_allowed(settings, strategy)


def test_disabled_strategy_is_blocked(settings, validated_config) -> None:
    strategy = replace(validated_config, execution_enabled=False)

    with pytest.raises(ExecutionBlockedError, match="execution-enabled"):
        assert_execution_allowed(settings, strategy)


def test_maximum_open_positions_blocks_new_entries(settings, validated_config) -> None:
    with pytest.raises(ExecutionBlockedError, match="maximum concurrent positions"):
        assert_execution_allowed(
            settings,
            validated_config,
            open_positions=validated_config.risk_limits.max_concurrent_positions,
        )


def test_daily_loss_limit_blocks_new_entries(settings, validated_config) -> None:
    with pytest.raises(ExecutionBlockedError, match="daily loss limit"):
        assert_execution_allowed(
            settings,
            validated_config,
            daily_loss_fraction=validated_config.risk_limits.max_daily_loss_fraction,
        )


def test_a_signal_is_not_an_order(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk()
    gate = OrderAuthorizer(settings, validated_config)

    assert not isinstance(signal, OrderRequest)
    assert not hasattr(signal, "client_order_id")
    assert not hasattr(signal, "quantity")
    assert not hasattr(signal, "order_type")

    with pytest.raises(ExecutionBlockedError, match="OrderRequest"):
        gate.authorize(signal=signal, risk=risk, order=signal)  # type: ignore[arg-type]


def test_rejected_risk_cannot_authorize_an_order(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk(approved=False, quantity=0.0, risk_amount=0.0)
    gate = OrderAuthorizer(settings, validated_config)

    with pytest.raises(ExecutionBlockedError, match="rejected"):
        gate.authorize(signal=signal, risk=risk, order=_order())


def test_failed_authorization_does_not_consume_client_order_id(
    settings, validated_config
) -> None:
    signal = _signal(validated_config)
    gate = OrderAuthorizer(settings, validated_config)
    rejected_risk = _risk(approved=False, quantity=0.0, risk_amount=0.0)

    with pytest.raises(ExecutionBlockedError):
        gate.authorize(signal=signal, risk=rejected_risk, order=_order())

    gate.authorize(signal=signal, risk=_risk(), order=_order())


def test_risk_must_belong_to_the_signal(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk(signal_id="sig-other")
    gate = OrderAuthorizer(settings, validated_config)

    with pytest.raises(ExecutionBlockedError, match="risk assessment"):
        gate.authorize(signal=signal, risk=risk, order=_order())


def test_order_must_belong_to_the_signal(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk()
    gate = OrderAuthorizer(settings, validated_config)

    with pytest.raises(ExecutionBlockedError, match="order does not belong"):
        gate.authorize(signal=signal, risk=risk, order=_order(signal_id="sig-other"))


def test_order_quantity_must_match_approved_risk(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk()
    gate = OrderAuthorizer(settings, validated_config)

    with pytest.raises(ExecutionBlockedError, match="quantity"):
        gate.authorize(signal=signal, risk=risk, order=_order(quantity=5.0))


def test_order_symbol_must_match_the_signal(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk()
    gate = OrderAuthorizer(settings, validated_config)

    with pytest.raises(ExecutionBlockedError, match="symbol"):
        gate.authorize(signal=signal, risk=risk, order=_order(symbol="MGC"))


def test_signal_must_come_from_the_configured_strategy(settings, validated_config) -> None:
    signal = _signal(validated_config, strategy_name="different_strategy")
    risk = _risk()
    gate = OrderAuthorizer(settings, validated_config)

    with pytest.raises(ExecutionBlockedError, match="configured strategy"):
        gate.authorize(signal=signal, risk=risk, order=_order())


def test_risk_stop_must_match_the_signal_stop(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk(stop_price=97.0)
    gate = OrderAuthorizer(settings, validated_config)

    with pytest.raises(ExecutionBlockedError, match="stop price"):
        gate.authorize(signal=signal, risk=risk, order=_order())


def test_duplicate_client_order_id_is_blocked(settings, validated_config) -> None:
    signal = _signal(validated_config)
    risk = _risk()
    order = _order()
    gate = OrderAuthorizer(settings, validated_config)

    gate.authorize(signal=signal, risk=risk, order=order)

    with pytest.raises(DuplicateOrderError, match="already been authorized"):
        gate.authorize(signal=signal, risk=risk, order=order)


def test_order_status_distinguishes_submission_from_fill() -> None:
    submitted = OrderState(
        client_order_id="client-1",
        status=OrderStatus.SUBMITTED,
        requested_quantity=10.0,
        updated_at=NOW,
    )
    acknowledged = OrderState(
        client_order_id="client-1",
        status=OrderStatus.ACKNOWLEDGED,
        requested_quantity=10.0,
        updated_at=NOW,
    )
    partially_filled = OrderState(
        client_order_id="client-1",
        status=OrderStatus.PARTIALLY_FILLED,
        requested_quantity=10.0,
        updated_at=NOW,
        filled_quantity=4.0,
        average_fill_price=99.0,
    )
    filled = OrderState(
        client_order_id="client-1",
        status=OrderStatus.FILLED,
        requested_quantity=10.0,
        updated_at=NOW,
        filled_quantity=10.0,
        average_fill_price=100.0,
    )

    assert submitted.is_filled is False
    assert acknowledged.is_filled is False
    assert partially_filled.is_filled is False
    assert filled.is_filled is True
    assert filled.remaining_quantity == 0.0


def test_incomplete_fill_cannot_be_reported_as_filled() -> None:
    with pytest.raises(ValueError, match="full requested quantity"):
        OrderState(
            client_order_id="client-1",
            status=OrderStatus.FILLED,
            requested_quantity=10.0,
            updated_at=NOW,
            filled_quantity=4.0,
            average_fill_price=100.0,
        )


def test_fully_filled_order_cannot_be_canceled() -> None:
    with pytest.raises(ValueError, match="cannot be marked canceled"):
        OrderState(
            client_order_id="client-1",
            status=OrderStatus.CANCELED,
            requested_quantity=10.0,
            updated_at=NOW,
            filled_quantity=10.0,
            average_fill_price=100.0,
        )


def test_all_required_order_statuses_exist() -> None:
    assert {
        OrderStatus.SUBMITTED,
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    }.issubset(set(OrderStatus))


def test_disabled_broker_fails_closed(validated_config) -> None:
    broker = DisabledBroker()
    order = _order()

    with pytest.raises(BrokerUnavailableError, match="disabled"):
        broker.submit_order(order)


def test_secret_values_are_redacted() -> None:
    environment = {
        "TELEGRAM_BOT_TOKEN": "tok-123",
        "API_KEY": "key-456",
        "PATH": "C:/normal/path",
    }

    values = secret_values_from_environment(environment)

    assert set(values) == {"tok-123", "key-456"}
    assert "C:/normal/path" not in values
    assert redact_secrets("token=tok-123", values) == "token=[REDACTED]"


def test_logging_filter_redacts_known_secrets() -> None:
    record = logging.LogRecord(
        name="trantor",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="broker token=%s",
        args=("tok-123",),
        exc_info=None,
    )
    log_filter = SecretRedactingFilter(secrets=("tok-123",))

    assert log_filter.filter(record) is True
    assert record.getMessage() == "broker token=[REDACTED]"
    assert "tok-123" not in record.getMessage()
