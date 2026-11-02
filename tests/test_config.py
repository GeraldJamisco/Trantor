from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from trantor.config import (
    ConfigurationError,
    StrategyStatus,
    load_settings,
    load_strategy_config,
)
from trantor.models import AssetClass, TradingMode

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = PROJECT_ROOT / "rules"
GOLD_PATH = RULES_DIR / "gold.json"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.example.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _set_path(payload: dict, dotted_path: str, value: object) -> None:
    node = payload
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def test_shipped_gold_strategy_loads_as_a_placeholder() -> None:
    config = load_strategy_config(GOLD_PATH)

    assert config.status is StrategyStatus.PLACEHOLDER
    assert config.execution_enabled is False
    assert config.strategy_name
    assert config.strategy_version
    assert "PLACEHOLDER" in config.description.upper()
    assert config.entry_conditions.conditions == ()
    assert config.stop_loss.required is True
    assert config.stop_loss.placeholder_values is True
    assert config.take_profit.placeholder_values is True
    assert config.source_path == GOLD_PATH.resolve()


def test_gold_config_has_expected_futures_structure() -> None:
    config = load_strategy_config(GOLD_PATH)

    assert config.supported_instruments.universe == "gold_futures"
    assert config.supported_instruments.asset_classes == (
        AssetClass.GOLD_FUTURES,
    )
    assert {"GC", "MGC"} == set(config.supported_instruments.symbols)
    assert config.timeframes.signal == "15m"
    assert config.sessions.timezone == "America/New_York"
    assert config.risk_limits.max_concurrent_positions == 2
    assert 0 < config.risk_limits.max_risk_per_trade_fraction <= 0.01


def test_example_settings_load_and_default_to_paper() -> None:
    settings = load_settings(SETTINGS_PATH)

    assert settings.trading_mode is TradingMode.PAPER
    assert settings.live_trading_enabled is False
    assert settings.broker_adapter == "paper"
    assert settings.max_risk_per_trade_fraction > 0
    assert settings.max_concurrent_positions > 0


def test_example_settings_default_to_disabled_market_data() -> None:
    settings = load_settings(SETTINGS_PATH)

    assert settings.market_data_provider == "disabled"
    assert settings.mt5_terminal_path is None


def test_mt5_provider_can_be_selected_with_terminal_path(tmp_path: Path) -> None:
    payload = _read_json(SETTINGS_PATH)
    payload["market_data_provider"] = "mt5"
    payload["mt5_terminal_path"] = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    path = _write_json(tmp_path / "settings.json", payload)

    settings = load_settings(path)

    assert settings.market_data_provider == "mt5"
    assert settings.mt5_terminal_path == r"C:\Program Files\MetaTrader 5\terminal64.exe"
    assert settings.trading_mode is TradingMode.PAPER
    assert settings.live_trading_enabled is False
    assert settings.broker_adapter == "paper"


def test_mt5_provider_requires_terminal_path(tmp_path: Path) -> None:
    payload = _read_json(SETTINGS_PATH)
    payload["market_data_provider"] = "mt5"
    payload["mt5_terminal_path"] = None
    path = _write_json(tmp_path / "settings.json", payload)

    with pytest.raises(ConfigurationError, match="mt5_terminal_path"):
        load_settings(path)


def test_unknown_market_data_provider_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(SETTINGS_PATH)
    payload["market_data_provider"] = "unsupported"
    path = _write_json(tmp_path / "settings.json", payload)

    with pytest.raises(ConfigurationError, match="market_data_provider"):
        load_settings(path)


def test_mt5_terminal_path_must_be_a_non_empty_string(tmp_path: Path) -> None:
    payload = _read_json(SETTINGS_PATH)
    payload["market_data_provider"] = "mt5"
    payload["mt5_terminal_path"] = 123
    path = _write_json(tmp_path / "settings.json", payload)

    with pytest.raises(ConfigurationError, match="mt5_terminal_path"):
        load_settings(path)


def test_settings_path_can_come_from_environment() -> None:
    settings = load_settings(environ={"TRANTOR_SETTINGS_PATH": str(SETTINGS_PATH)})
    assert settings.trading_mode is TradingMode.PAPER


def test_empty_settings_path_environment_variable_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="TRANTOR_SETTINGS_PATH"):
        load_settings(environ={"TRANTOR_SETTINGS_PATH": "   "})


def test_log_level_can_come_from_environment() -> None:
    settings = load_settings(
        SETTINGS_PATH,
        environ={"TRANTOR_LOG_LEVEL": "debug"},
    )

    assert settings.log_level == "DEBUG"


def test_invalid_log_level_environment_value_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="log_level"):
        load_settings(SETTINGS_PATH, environ={"TRANTOR_LOG_LEVEL": "verbose"})


@pytest.mark.parametrize("loader", [load_settings, load_strategy_config])
def test_missing_configuration_file_is_an_error(tmp_path: Path, loader) -> None:
    missing = tmp_path / "missing.json"

    with pytest.raises(ConfigurationError, match="not found"):
        loader(missing)


def test_invalid_json_reports_line_and_column(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text('{"schema_version": 1,', encoding="utf-8")

    with pytest.raises(ConfigurationError) as exc_info:
        load_strategy_config(path)

    message = str(exc_info.value)
    assert "invalid JSON" in message
    assert "line 1" in message
    assert "column" in message


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text("   \n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="empty"):
        load_strategy_config(path)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text('{"schema_version": 1, "schema_version": 2}', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="duplicate JSON key"):
        load_strategy_config(path)


@pytest.mark.parametrize("literal", ["NaN", "1e400"])
def test_non_finite_json_number_is_rejected(tmp_path: Path, literal: str) -> None:
    path = tmp_path / "rules.json"
    path.write_text(f'{{"schema_version": {literal}}}', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="non-finite"):
        load_strategy_config(path)


def test_non_object_json_root_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="expected a JSON object"):
        load_strategy_config(path)


def test_unknown_top_level_field_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["silent_fallback"] = True
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="unknown field"):
        load_strategy_config(path)


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    del payload["risk_limits"]
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="risk_limits"):
        load_strategy_config(path)


def test_placeholder_strategy_cannot_be_armed_for_execution(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["execution_enabled"] = True
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="placeholder strategy"):
        load_strategy_config(path)


def test_validated_strategy_requires_entry_conditions(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["status"] = "validated"
    payload["execution_enabled"] = True
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="entry condition"):
        load_strategy_config(path)


def test_validated_strategy_requires_stop_loss(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["status"] = "validated"
    payload["execution_enabled"] = True
    payload["entry_conditions"]["conditions"] = [{"type": "test_only"}]
    payload["stop_loss"]["required"] = False
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="stop-loss"):
        load_strategy_config(path)


def test_validated_strategy_cannot_retain_placeholder_exit_values(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["status"] = "validated"
    payload["execution_enabled"] = True
    payload["entry_conditions"]["conditions"] = [{"type": "test_only"}]
    payload["stop_loss"]["placeholder_values"] = True
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="placeholder"):
        load_strategy_config(path)


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("risk_limits.max_risk_per_trade_fraction", 0),
        ("risk_limits.max_risk_per_trade_fraction", -0.001),
        ("risk_limits.max_risk_per_trade_fraction", "0.005"),
        ("risk_limits.max_risk_per_trade_fraction", True),
        ("risk_limits.max_risk_per_trade_fraction", 1.5),
        ("risk_limits.max_risk_per_trade_fraction", 10**400),
        ("risk_limits.max_concurrent_positions", 0),
        ("risk_limits.max_concurrent_positions", 2.5),
        ("risk_limits.max_concurrent_positions", True),
        ("risk_limits.max_daily_loss_fraction", 0),
        ("risk_limits.max_daily_loss_fraction", 1.01),
        ("position_sizing.risk_per_trade_fraction", 0),
        ("position_sizing.max_position_notional_fraction", 1.5),
    ],
)
def test_invalid_risk_settings_are_rejected(
    tmp_path: Path, field_path: str, value: object
) -> None:
    payload = _read_json(GOLD_PATH)
    _set_path(payload, field_path, value)
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match=field_path.split(".")[-1]):
        load_strategy_config(path)


def test_position_risk_cannot_exceed_strategy_maximum(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["position_sizing"]["risk_per_trade_fraction"] = 0.01
    payload["risk_limits"]["max_risk_per_trade_fraction"] = 0.005
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="cannot exceed"):
        load_strategy_config(path)


def test_invalid_timezone_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["sessions"]["timezone"] = "Mars/Olympus"
    path = _write_json(tmp_path / "gold.json", payload)

    with pytest.raises(ConfigurationError, match="timezone"):
        load_strategy_config(path)


def test_invalid_time_format_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["sessions"]["windows"][0]["start"] = "9:30"
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="HH:MM"):
        load_strategy_config(path)


def test_zero_length_session_window_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["sessions"]["windows"][0]["end"] = payload["sessions"]["windows"][0]["start"]
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="must differ"):
        load_strategy_config(path)


def test_invalid_condition_logic_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["entry_conditions"]["logic"] = "none"
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="'all' or 'any'"):
        load_strategy_config(path)


def test_unsupported_asset_class_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["supported_instruments"]["asset_classes"] = ["gold_etf"]
    path = _write_json(tmp_path / "gold.json", payload)

    with pytest.raises(ConfigurationError, match="gold_futures"):
        load_strategy_config(path)


def test_unrecognized_asset_class_value_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["supported_instruments"]["asset_classes"] = ["crypto"]
    path = _write_json(tmp_path / "gold.json", payload)

    with pytest.raises(ConfigurationError, match="unsupported value"):
        load_strategy_config(path)


@pytest.mark.parametrize("symbols", [["GLD"], ["XAUUSD"], ["ES"], []])
def test_unsupported_target_symbols_are_rejected(
    tmp_path: Path, symbols: list[str]
) -> None:
    payload = _read_json(GOLD_PATH)
    payload["supported_instruments"]["symbols"] = symbols
    path = _write_json(tmp_path / "gold.json", payload)

    with pytest.raises(ConfigurationError, match="symbol"):
        load_strategy_config(path)


def test_unknown_nested_field_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(GOLD_PATH)
    payload["stop_loss"]["ignored_safety_flag"] = True
    path = _write_json(tmp_path / "rules.json", payload)

    with pytest.raises(ConfigurationError, match="unknown field"):
        load_strategy_config(path)


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("max_risk_per_trade_fraction", 0),
        ("max_risk_per_trade_fraction", 2),
        ("max_risk_per_trade_fraction", "0.005"),
        ("max_concurrent_positions", 0),
        ("max_daily_loss_fraction", 0),
        ("live_trading_enabled", "false"),
    ],
)
def test_invalid_settings_values_are_rejected(
    tmp_path: Path, field_path: str, value: object
) -> None:
    payload = _read_json(SETTINGS_PATH)
    payload[field_path] = value
    path = _write_json(tmp_path / "settings.json", payload)

    with pytest.raises(ConfigurationError, match=field_path):
        load_settings(path)


def test_invalid_trading_mode_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(SETTINGS_PATH)
    payload["trading_mode"] = "leveraged"
    path = _write_json(tmp_path / "settings.json", payload)

    with pytest.raises(ConfigurationError, match="trading_mode"):
        load_settings(path)


def test_missing_settings_field_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(SETTINGS_PATH)
    del payload["max_concurrent_positions"]
    path = _write_json(tmp_path / "settings.json", payload)

    with pytest.raises(ConfigurationError, match="max_concurrent_positions"):
        load_settings(path)


def test_unknown_settings_field_is_rejected(tmp_path: Path) -> None:
    payload = _read_json(SETTINGS_PATH)
    payload["paper_trading"] = True
    path = _write_json(tmp_path / "settings.json", payload)

    with pytest.raises(ConfigurationError, match="unknown field"):
        load_settings(path)


def test_live_mode_can_be_loaded_but_is_not_silently_rewritten(tmp_path: Path) -> None:
    payload = _read_json(SETTINGS_PATH)
    payload["trading_mode"] = "live"
    payload["live_trading_enabled"] = True
    path = _write_json(tmp_path / "settings.json", payload)

    settings = load_settings(path)

    assert settings.trading_mode is TradingMode.LIVE
    assert settings.live_trading_enabled is True


def test_validated_config_copy_can_be_loaded(tmp_path: Path) -> None:
    payload = copy.deepcopy(_read_json(GOLD_PATH))
    payload["status"] = "validated"
    payload["execution_enabled"] = True
    payload["entry_conditions"]["conditions"] = [{"type": "test_only"}]
    payload["stop_loss"]["placeholder_values"] = False
    payload["take_profit"]["placeholder_values"] = False
    path = _write_json(tmp_path / "rules.json", payload)

    config = load_strategy_config(path)

    assert config.status is StrategyStatus.VALIDATED
    assert config.execution_enabled is True
    assert config.entry_conditions.conditions == ({"type": "test_only"},)
