from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from trantor.config import (
    ConfigurationError,
    DiscoveryKind,
    GoldAnalysisDiscoveryConfig,
    RankingDirection,
    load_strategy_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = PROJECT_ROOT / "rules"
GOLD_PATH = RULES_DIR / "gold.json"


def _payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: dict, name: str = "strategy.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _set_path(payload: dict, dotted_path: str, value: object) -> None:
    node = payload
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def test_gold_is_the_only_active_discovery_kind() -> None:
    assert tuple(DiscoveryKind) == (DiscoveryKind.GOLD_ANALYSIS,)


def test_shipped_gold_discovery_config_is_typed_and_strict() -> None:
    config = load_strategy_config(GOLD_PATH)

    discovery = config.opportunity_discovery
    assert isinstance(discovery, GoldAnalysisDiscoveryConfig)
    assert discovery.kind is DiscoveryKind.GOLD_ANALYSIS
    assert discovery.reference_timeframe == "15m"
    assert discovery.previous_close_timeframe == "1d"
    assert discovery.required_measurements == ("latest_price",)
    assert discovery.eligible_sessions == ("open",)
    assert discovery.require_verified_session is True
    assert tuple(item.measurement for item in discovery.ranking.measurements) == (
        "absolute_change_percent",
        "latest_price",
    )
    assert all(
        item.direction is RankingDirection.DESCENDING
        for item in discovery.ranking.measurements
    )


def test_discovery_block_remains_optional_for_strategy_payloads(
    tmp_path: Path,
) -> None:
    payload = _payload(GOLD_PATH)
    del payload["opportunity_discovery"]

    config = load_strategy_config(_write(tmp_path, payload))

    assert config.opportunity_discovery is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("opportunity_discovery.max_data_age_seconds", "max_data_age_seconds"),
        ("opportunity_discovery.max_price", "max_price"),
        ("opportunity_discovery.required_fields", "required_fields"),
        ("opportunity_discovery.eligible_sessions", "eligible_sessions"),
        ("opportunity_discovery.excluded_symbols", "excluded_symbols"),
        ("opportunity_discovery.require_verified_session", "require_verified_session"),
        ("opportunity_discovery.previous_close_is_daily", "previous_close_is_daily"),
        ("opportunity_discovery.reference_timeframe", "reference_timeframe"),
    ],
)
def test_invalid_gold_discovery_values_fail_clearly(
    tmp_path: Path,
    path: str,
    expected: str,
) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    invalid_values = {
        "opportunity_discovery.max_data_age_seconds": 0,
        "opportunity_discovery.max_price": 0.0,
        "opportunity_discovery.required_fields": ["open"],
        "opportunity_discovery.eligible_sessions": ["premarket"],
        "opportunity_discovery.excluded_symbols": ["GC", "GC"],
        "opportunity_discovery.require_verified_session": "true",
        "opportunity_discovery.previous_close_is_daily": "true",
        "opportunity_discovery.reference_timeframe": "1m",
    }
    _set_path(payload, path, invalid_values[path])

    with pytest.raises(ConfigurationError) as excinfo:
        load_strategy_config(_write(tmp_path, payload))

    assert expected in str(excinfo.value)


def test_unknown_discovery_field_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    payload["opportunity_discovery"]["profit_probability"] = 0.9

    with pytest.raises(ConfigurationError, match="unknown field"):
        load_strategy_config(_write(tmp_path, payload))


def test_unknown_ranking_measurement_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(
        payload,
        "opportunity_discovery.ranking.measurements",
        [{"measurement": "expected_profit", "direction": "descending"}],
    )

    with pytest.raises(ConfigurationError, match="unsupported measurement"):
        load_strategy_config(_write(tmp_path, payload))


def test_duplicate_ranking_measurement_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(
        payload,
        "opportunity_discovery.ranking.measurements",
        [
            {"measurement": "absolute_change_percent", "direction": "descending"},
            {"measurement": "absolute_change_percent", "direction": "ascending"},
        ],
    )

    with pytest.raises(ConfigurationError, match="duplicate measurement"):
        load_strategy_config(_write(tmp_path, payload))


def test_invalid_ranking_direction_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(
        payload,
        "opportunity_discovery.ranking.measurements",
        [{"measurement": "absolute_change_percent", "direction": "best"}],
    )

    with pytest.raises(ConfigurationError, match="must be one of"):
        load_strategy_config(_write(tmp_path, payload))


def test_stock_discovery_kind_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(payload, "opportunity_discovery.kind", "stock_gap")

    with pytest.raises(ConfigurationError, match="must be one of gold_analysis"):
        load_strategy_config(_write(tmp_path, payload))


@pytest.mark.parametrize(
    "asset_classes",
    [["gold_etf"], ["gold_spot"], ["stock"], ["gold_futures", "gold_etf"]],
)
def test_non_futures_asset_classes_are_rejected(
    tmp_path: Path, asset_classes: list[str]
) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(payload, "supported_instruments.asset_classes", asset_classes)

    with pytest.raises(ConfigurationError, match="gold_futures"):
        load_strategy_config(_write(tmp_path, payload))


@pytest.mark.parametrize("symbols", [["GLD"], ["XAUUSD"], ["AAPL"], ["ES"]])
def test_unsupported_target_symbols_are_rejected(
    tmp_path: Path, symbols: list[str]
) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(payload, "supported_instruments.symbols", symbols)

    with pytest.raises(ConfigurationError, match="GC and MGC"):
        load_strategy_config(_write(tmp_path, payload))


def test_invalid_gold_required_measurement_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(
        payload,
        "opportunity_discovery.required_measurements",
        ["latest_price", "certain_profit"],
    )

    with pytest.raises(ConfigurationError, match="unsupported measurement"):
        load_strategy_config(_write(tmp_path, payload))


def test_gold_required_measurements_must_not_be_empty(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(
        payload,
        "opportunity_discovery.required_measurements",
        [],
    )

    with pytest.raises(ConfigurationError, match="must not be empty"):
        load_strategy_config(_write(tmp_path, payload))


@pytest.mark.parametrize(
    "session",
    ["closed", "break", "premarket", "regular", "postmarket"],
)
def test_only_open_is_an_eligible_gold_session(
    tmp_path: Path, session: str
) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(
        payload,
        "opportunity_discovery.eligible_sessions",
        [session],
    )

    with pytest.raises(ConfigurationError, match="unsupported session"):
        load_strategy_config(_write(tmp_path, payload))


def test_gold_price_range_must_be_ordered(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(payload, "opportunity_discovery.min_price", 100.0)
    _set_path(payload, "opportunity_discovery.max_price", 99.0)

    with pytest.raises(ConfigurationError, match="greater than min_price"):
        load_strategy_config(_write(tmp_path, payload))


def test_discovery_previous_close_timeframe_must_differ(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(
        payload,
        "opportunity_discovery.previous_close_timeframe",
        "15m",
    )

    with pytest.raises(ConfigurationError, match="must differ"):
        load_strategy_config(_write(tmp_path, payload))


def test_discovery_reference_timeframe_must_match_signal_timeframe(
    tmp_path: Path,
) -> None:
    payload = copy.deepcopy(_payload(GOLD_PATH))
    _set_path(
        payload,
        "opportunity_discovery.reference_timeframe",
        "5m",
    )

    with pytest.raises(ConfigurationError, match="timeframes.signal"):
        load_strategy_config(_write(tmp_path, payload))
