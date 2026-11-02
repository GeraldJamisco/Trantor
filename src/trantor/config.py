"""Loading and validation for strategy and execution configuration.

No invalid configuration falls back to defaults. Every loader either returns a
fully validated immutable object or raises :class:`ConfigurationError`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field, replace
from datetime import time
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import AssetClass, TradingMode

__all__ = [
    "ConditionGroup",
    "ConfigurationError",
    "DEFAULT_SETTINGS_PATH",
    "DiscoveryKind",
    "ExecutionMode",
    "GoldAnalysisDiscoveryConfig",
    "OpportunityDiscoveryConfig",
    "PositionSizing",
    "RankingConfig",
    "RankingCriterion",
    "RankingDirection",
    "RiskLimits",
    "SessionConfig",
    "Settings",
    "StopLossConfig",
    "StrategyConfig",
    "StrategyStatus",
    "SUPPORTED_GOLD_FUTURES_SYMBOLS",
    "SUPPORTED_XAUUSD_P_SYMBOLS",
    "SUPPORTED_XAUUSD_P_UNIVERSE",
    "SupportedInstruments",
    "TakeProfitConfig",
    "Timeframes",
    "TradingWindow",
    "load_settings",
    "load_strategy_config",
    "settings_fingerprint",
    "settings_snapshot",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"
SUPPORTED_SCHEMA_VERSION = 1
_STRATEGY_STATUSES = ("placeholder", "validated")
_ASSET_CLASSES = frozenset(item.value for item in AssetClass)
_TRADING_DAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_OBSERVATION_FIELDS = frozenset({"open", "high", "low", "close", "volume"})
SUPPORTED_GOLD_FUTURES_SYMBOLS = frozenset({"GC", "MGC"})
SUPPORTED_XAUUSD_P_SYMBOLS = frozenset({"XAUUSD-P"})
SUPPORTED_XAUUSD_P_UNIVERSE = "xauusd_broker_gold"
_MARKET_DATA_PROVIDERS = frozenset({"disabled", "mt5"})
_GOLD_DISCOVERY_SESSIONS = frozenset({"open"})
_GOLD_RANKING_MEASUREMENTS = frozenset(
    {
        "latest_price",
        "previous_close",
        "change",
        "change_percent",
        "absolute_change_percent",
        "reference_volume",
    }
)
_GOLD_REQUIRED_MEASUREMENTS = frozenset(
    {
        "latest_price",
        "previous_close",
        "change",
        "change_percent",
        "absolute_change_percent",
        "reference_volume",
    }
)


class ConfigurationError(ValueError):
    """A configuration file is missing, malformed, or invalid."""


class StrategyStatus(StrEnum):
    PLACEHOLDER = "placeholder"
    VALIDATED = "validated"


class ExecutionMode(StrEnum):
    """Explicit runtime execution boundary.

    ``DISABLED`` and ``RESEARCH`` cannot reach an order adapter. ``PAPER`` is
    the only currently supported order-capable mode. Demo/live are reserved for
    separately authorized future adapters and are rejected by the current gate.
    """

    DISABLED = "disabled"
    RESEARCH = "research"
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


class DiscoveryKind(StrEnum):
    """The kind of research discovery configured for a strategy."""

    GOLD_ANALYSIS = "gold_analysis"


class RankingDirection(StrEnum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


@dataclass(frozen=True, slots=True)
class RankingCriterion:
    """One documented measurement used to order research candidates."""

    measurement: str
    direction: RankingDirection


@dataclass(frozen=True, slots=True)
class RankingConfig:
    """Ordered measurements for deterministic candidate ranking.

    These are sorting keys, not probabilities, expected returns, or a claim
    that a ranked candidate is more likely to be profitable.
    """

    measurements: tuple[RankingCriterion, ...]


@dataclass(frozen=True, slots=True)
class GoldAnalysisDiscoveryConfig:
    """Strict research configuration for gold opportunity analysis.

    Only measurements are configured here. No gold entry rule, probability of
    profit, or validated trading edge is implied.
    """

    kind: DiscoveryKind
    reference_timeframe: str
    previous_close_timeframe: str
    previous_close_is_daily: bool
    max_data_age_seconds: int
    min_price: float | None
    max_price: float | None
    required_fields: tuple[str, ...]
    required_measurements: tuple[str, ...]
    eligible_sessions: tuple[str, ...]
    excluded_symbols: tuple[str, ...]
    require_verified_session: bool
    ranking: RankingConfig


OpportunityDiscoveryConfig = GoldAnalysisDiscoveryConfig


@dataclass(frozen=True, slots=True)
class Settings:
    schema_version: int
    trading_mode: TradingMode
    live_trading_enabled: bool
    broker_adapter: str
    max_risk_per_trade_fraction: float
    max_concurrent_positions: int
    max_daily_loss_fraction: float
    log_level: str = "INFO"
    market_data_provider: str = "disabled"
    mt5_terminal_path: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.DISABLED

    @property
    def execution_enabled(self) -> bool:
        """True only for the explicitly supported paper execution mode."""

        return self.execution_mode is ExecutionMode.PAPER


@dataclass(frozen=True, slots=True)
class SupportedInstruments:
    universe: str
    asset_classes: tuple[AssetClass, ...]
    symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Timeframes:
    signal: str
    context: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TradingWindow:
    start: time
    end: time


@dataclass(frozen=True, slots=True)
class SessionConfig:
    timezone: str
    trading_days: tuple[str, ...]
    windows: tuple[TradingWindow, ...]
    entry_cutoff: time
    close_positions_by: time


@dataclass(frozen=True, slots=True)
class ConditionGroup:
    logic: str
    conditions: tuple[dict[str, Any], ...]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class PositionSizing:
    method: str
    risk_per_trade_fraction: float
    max_position_notional_fraction: float


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_risk_per_trade_fraction: float
    max_concurrent_positions: int
    max_daily_loss_fraction: float


@dataclass(frozen=True, slots=True)
class StopLossConfig:
    method: str
    required: bool
    parameters: dict[str, Any]
    placeholder_values: bool


@dataclass(frozen=True, slots=True)
class TakeProfitConfig:
    method: str
    enabled: bool
    parameters: dict[str, Any]
    placeholder_values: bool


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    schema_version: int
    strategy_name: str
    strategy_version: str
    status: StrategyStatus
    execution_enabled: bool
    description: str
    notes: tuple[str, ...]
    supported_instruments: SupportedInstruments
    timeframes: Timeframes
    sessions: SessionConfig
    entry_conditions: ConditionGroup
    exit_conditions: ConditionGroup
    position_sizing: PositionSizing
    risk_limits: RiskLimits
    stop_loss: StopLossConfig
    take_profit: TakeProfitConfig
    strategy_parameters: dict[str, Any]
    opportunity_discovery: OpportunityDiscoveryConfig | None = None
    source_path: Path | None = field(default=None, compare=False)


def _error(source: str, field_name: str, message: str) -> ConfigurationError:
    location = f"{source}: {field_name}" if field_name else source
    return ConfigurationError(f"{location}: {message}")


def _as_object(value: Any, source: str, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(source, field_name, "expected a JSON object")
    return value


def _check_keys(
    value: Any,
    *,
    source: str,
    field_name: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    mapping = _as_object(value, source, field_name)
    missing = sorted(required - set(mapping))
    if missing:
        raise _error(source, field_name, f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(mapping) - required - optional)
    if unknown:
        raise _error(source, field_name, f"unknown field(s): {', '.join(unknown)}")
    return mapping


def _text(value: Any, source: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(source, field_name, "must be a non-empty string")
    return value


def _boolean(value: Any, source: str, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise _error(source, field_name, "must be a boolean")
    return value


def _number(value: Any, source: str, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(source, field_name, "must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise _error(source, field_name, "must be finite") from None
    if not math.isfinite(number):
        raise _error(source, field_name, "must be finite")
    return number


def _integer(value: Any, source: str, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(source, field_name, "must be an integer")
    return value


def _fraction(value: Any, source: str, field_name: str) -> float:
    number = _number(value, source, field_name)
    if not 0 < number <= 1:
        raise _error(source, field_name, "must be greater than 0 and at most 1")
    return number


def _string_tuple(
    value: Any,
    source: str,
    field_name: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _error(source, field_name, "must be a list of strings")
    if not value and not allow_empty:
        raise _error(source, field_name, "must not be empty")
    items = tuple(_text(item, source, f"{field_name}[{index}]") for index, item in enumerate(value))
    if len(set(items)) != len(items):
        raise _error(source, field_name, "must not contain duplicates")
    return items


def _parse_time(value: Any, source: str, field_name: str) -> time:
    text = _text(value, source, field_name)
    if not _TIME_PATTERN.fullmatch(text):
        raise _error(source, field_name, "must use 24-hour HH:MM format")
    return time.fromisoformat(text)


def _parse_timezone(value: Any, source: str, field_name: str) -> str:
    name = _text(value, source, field_name)
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise _error(source, field_name, f"unknown IANA timezone {name!r}") from None
    return name


def _parse_log_level(value: Any, source: str, field_name: str) -> str:
    level = _text(value, source, field_name).upper()
    if level not in _LOG_LEVELS:
        raise _error(
            source,
            field_name,
            f"must be one of {', '.join(sorted(_LOG_LEVELS))}",
        )
    return level


def _parse_condition_group(value: Any, source: str, field_name: str) -> ConditionGroup:
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset({"logic", "conditions"}),
        optional=frozenset({"notes"}),
    )
    logic = _text(mapping["logic"], source, f"{field_name}.logic")
    if logic not in {"all", "any"}:
        raise _error(source, f"{field_name}.logic", "must be 'all' or 'any'")

    raw_conditions = mapping["conditions"]
    if not isinstance(raw_conditions, list):
        raise _error(source, f"{field_name}.conditions", "must be a list")
    conditions: list[dict[str, Any]] = []
    for index, condition in enumerate(raw_conditions):
        conditions.append(_as_object(condition, source, f"{field_name}.conditions[{index}]"))

    notes: str | None = None
    if "notes" in mapping:
        notes = _text(mapping["notes"], source, f"{field_name}.notes")
    return ConditionGroup(logic=logic, conditions=tuple(conditions), notes=notes)


def _parse_supported_instruments(value: Any, source: str) -> SupportedInstruments:
    field_name = "supported_instruments"
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset({"universe", "asset_classes", "symbols"}),
    )
    universe = _text(mapping["universe"], source, f"{field_name}.universe")
    raw_classes = _string_tuple(
        mapping["asset_classes"],
        source,
        f"{field_name}.asset_classes",
        allow_empty=False,
    )
    unknown_classes = sorted(set(raw_classes) - _ASSET_CLASSES)
    if unknown_classes:
        allowed = ", ".join(sorted(_ASSET_CLASSES))
        raise _error(
            source,
            f"{field_name}.asset_classes",
            f"unsupported value(s) {unknown_classes}; allowed: {allowed}",
        )
    asset_classes = tuple(AssetClass(item) for item in raw_classes)
    symbols = _string_tuple(
        mapping["symbols"],
        source,
        f"{field_name}.symbols",
        allow_empty=False,
    )

    if asset_classes == (AssetClass.GOLD_FUTURES,):
        unsupported_symbols = sorted(
            set(symbols) - SUPPORTED_GOLD_FUTURES_SYMBOLS
        )
        if unsupported_symbols:
            raise _error(
                source,
                f"{field_name}.symbols",
                "gold_futures scope supports only GC and MGC; unsupported "
                f"symbol(s): {', '.join(unsupported_symbols)}",
            )
    elif asset_classes == (AssetClass.UNVERIFIED_GOLD,):
        if universe != SUPPORTED_XAUUSD_P_UNIVERSE:
            raise _error(
                source,
                f"{field_name}.universe",
                f"unverified_gold requires universe {SUPPORTED_XAUUSD_P_UNIVERSE!r}",
            )
        if set(symbols) != SUPPORTED_XAUUSD_P_SYMBOLS:
            raise _error(
                source,
                f"{field_name}.symbols",
                "unverified_gold scope supports only XAUUSD-P; "
                f"received {', '.join(symbols)}",
            )
    else:
        raise _error(
            source,
            f"{field_name}.asset_classes",
            "active scope requires exactly one of 'gold_futures' (GC/MGC) or "
            "'unverified_gold' (XAUUSD-P); mixed or unsupported asset classes "
            "are not allowed",
        )
    return SupportedInstruments(
        universe=universe,
        asset_classes=asset_classes,
        symbols=symbols,
    )


def _parse_timeframes(value: Any, source: str) -> Timeframes:
    field_name = "timeframes"
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset({"signal", "context"}),
    )
    signal = _text(mapping["signal"], source, f"{field_name}.signal")
    context = _string_tuple(
        mapping["context"],
        source,
        f"{field_name}.context",
        allow_empty=True,
    )
    return Timeframes(signal=signal, context=context)


def _parse_sessions(value: Any, source: str) -> SessionConfig:
    field_name = "sessions"
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset(
            {"timezone", "trading_days", "windows", "entry_cutoff", "close_positions_by"}
        ),
    )
    timezone = _parse_timezone(mapping["timezone"], source, f"{field_name}.timezone")

    raw_days = _string_tuple(
        mapping["trading_days"],
        source,
        f"{field_name}.trading_days",
        allow_empty=False,
    )
    normalized_days = tuple(day.lower() for day in raw_days)
    unknown_days = sorted(set(normalized_days) - _TRADING_DAYS)
    if unknown_days:
        raise _error(
            source,
            f"{field_name}.trading_days",
            f"unsupported day(s) {unknown_days}; use mon, tue, wed, thu, fri, sat, sun",
        )
    if len(set(normalized_days)) != len(normalized_days):
        raise _error(source, f"{field_name}.trading_days", "must not contain duplicates")

    raw_windows = mapping["windows"]
    if not isinstance(raw_windows, list) or not raw_windows:
        raise _error(source, f"{field_name}.windows", "must be a non-empty list")
    windows: list[TradingWindow] = []
    for index, raw_window in enumerate(raw_windows):
        window_field = f"{field_name}.windows[{index}]"
        window_mapping = _check_keys(
            raw_window,
            source=source,
            field_name=window_field,
            required=frozenset({"start", "end"}),
        )
        start = _parse_time(window_mapping["start"], source, f"{window_field}.start")
        end = _parse_time(window_mapping["end"], source, f"{window_field}.end")
        if start == end:
            raise _error(source, window_field, "start and end must differ")
        windows.append(TradingWindow(start=start, end=end))

    entry_cutoff = _parse_time(mapping["entry_cutoff"], source, f"{field_name}.entry_cutoff")
    close_positions_by = _parse_time(
        mapping["close_positions_by"], source, f"{field_name}.close_positions_by"
    )
    return SessionConfig(
        timezone=timezone,
        trading_days=normalized_days,
        windows=tuple(windows),
        entry_cutoff=entry_cutoff,
        close_positions_by=close_positions_by,
    )


def _parse_position_sizing(value: Any, source: str) -> PositionSizing:
    field_name = "position_sizing"
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset(
            {"method", "risk_per_trade_fraction", "max_position_notional_fraction"}
        ),
    )
    return PositionSizing(
        method=_text(mapping["method"], source, f"{field_name}.method"),
        risk_per_trade_fraction=_fraction(
            mapping["risk_per_trade_fraction"],
            source,
            f"{field_name}.risk_per_trade_fraction",
        ),
        max_position_notional_fraction=_fraction(
            mapping["max_position_notional_fraction"],
            source,
            f"{field_name}.max_position_notional_fraction",
        ),
    )


def _parse_risk_limits(value: Any, source: str) -> RiskLimits:
    field_name = "risk_limits"
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset(
            {
                "max_risk_per_trade_fraction",
                "max_concurrent_positions",
                "max_daily_loss_fraction",
            }
        ),
    )
    max_risk = _fraction(
        mapping["max_risk_per_trade_fraction"],
        source,
        f"{field_name}.max_risk_per_trade_fraction",
    )
    max_positions = _integer(
        mapping["max_concurrent_positions"],
        source,
        f"{field_name}.max_concurrent_positions",
    )
    if max_positions <= 0:
        raise _error(
            source,
            f"{field_name}.max_concurrent_positions",
            "must be greater than 0",
        )
    return RiskLimits(
        max_risk_per_trade_fraction=max_risk,
        max_concurrent_positions=max_positions,
        max_daily_loss_fraction=_fraction(
            mapping["max_daily_loss_fraction"],
            source,
            f"{field_name}.max_daily_loss_fraction",
        ),
    )


def _parse_stop_loss(value: Any, source: str) -> StopLossConfig:
    field_name = "stop_loss"
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset({"method", "required", "parameters", "placeholder_values"}),
    )
    return StopLossConfig(
        method=_text(mapping["method"], source, f"{field_name}.method"),
        required=_boolean(mapping["required"], source, f"{field_name}.required"),
        parameters=_as_object(mapping["parameters"], source, f"{field_name}.parameters"),
        placeholder_values=_boolean(
            mapping["placeholder_values"],
            source,
            f"{field_name}.placeholder_values",
        ),
    )


def _parse_take_profit(value: Any, source: str) -> TakeProfitConfig:
    field_name = "take_profit"
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset({"method", "enabled", "parameters", "placeholder_values"}),
    )
    return TakeProfitConfig(
        method=_text(mapping["method"], source, f"{field_name}.method"),
        enabled=_boolean(mapping["enabled"], source, f"{field_name}.enabled"),
        parameters=_as_object(mapping["parameters"], source, f"{field_name}.parameters"),
        placeholder_values=_boolean(
            mapping["placeholder_values"],
            source,
            f"{field_name}.placeholder_values",
        ),
    )


def _positive_number(
    value: Any,
    source: str,
    field_name: str,
    *,
    allow_zero: bool = False,
) -> float:
    number = _number(value, source, field_name)
    if allow_zero:
        if number < 0:
            raise _error(source, field_name, "must be at least 0")
    elif number <= 0:
        raise _error(source, field_name, "must be greater than 0")
    return number


def _optional_positive_number(
    value: Any,
    source: str,
    field_name: str,
) -> float | None:
    if value is None:
        return None
    return _positive_number(value, source, field_name)


def _parse_required_fields(value: Any, source: str) -> tuple[str, ...]:
    field_name = "opportunity_discovery.required_fields"
    fields = _string_tuple(value, source, field_name, allow_empty=False)
    unknown = sorted(set(fields) - _OBSERVATION_FIELDS)
    if unknown:
        raise _error(
            source,
            field_name,
            f"unsupported field(s) {unknown}; allowed: "
            f"{', '.join(sorted(_OBSERVATION_FIELDS))}",
        )
    if "close" not in fields:
        raise _error(
            source,
            field_name,
            "must include 'close'; reference and previous-close prices are required",
        )
    return fields


def _parse_discovery_sessions(
    value: Any,
    source: str,
    *,
    allowed: frozenset[str],
) -> tuple[str, ...]:
    field_name = "opportunity_discovery.eligible_sessions"
    raw_sessions = _string_tuple(value, source, field_name, allow_empty=False)
    sessions = tuple(item.lower() for item in raw_sessions)
    if len(set(sessions)) != len(sessions):
        raise _error(source, field_name, "must not contain duplicates")
    unknown = sorted(set(sessions) - allowed)
    if unknown:
        raise _error(
            source,
            field_name,
            f"unsupported session(s) {unknown}; allowed: {', '.join(sorted(allowed))}",
        )
    return sessions


def _parse_excluded_symbols(value: Any, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    return _string_tuple(
        value,
        source,
        "opportunity_discovery.excluded_symbols",
        allow_empty=True,
    )


def _parse_ranking(
    value: Any,
    source: str,
    *,
    allowed_measurements: frozenset[str],
) -> RankingConfig:
    field_name = "opportunity_discovery.ranking"
    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset({"measurements"}),
    )
    raw_measurements = mapping["measurements"]
    if not isinstance(raw_measurements, list) or not raw_measurements:
        raise _error(source, f"{field_name}.measurements", "must be a non-empty list")

    criteria: list[RankingCriterion] = []
    seen: set[str] = set()
    for index, raw_criterion in enumerate(raw_measurements):
        criterion_field = f"{field_name}.measurements[{index}]"
        criterion = _check_keys(
            raw_criterion,
            source=source,
            field_name=criterion_field,
            required=frozenset({"measurement", "direction"}),
        )
        measurement = _text(
            criterion["measurement"],
            source,
            f"{criterion_field}.measurement",
        )
        if measurement not in allowed_measurements:
            raise _error(
                source,
                f"{criterion_field}.measurement",
                f"unsupported measurement {measurement!r}; allowed: "
                f"{', '.join(sorted(allowed_measurements))}",
            )
        if measurement in seen:
            raise _error(
                source,
                f"{field_name}.measurements",
                f"duplicate measurement {measurement!r}",
            )
        seen.add(measurement)

        raw_direction = _text(
            criterion["direction"],
            source,
            f"{criterion_field}.direction",
        )
        try:
            direction = RankingDirection(raw_direction)
        except ValueError:
            allowed = ", ".join(item.value for item in RankingDirection)
            raise _error(
                source,
                f"{criterion_field}.direction",
                f"must be one of {allowed}",
            ) from None
        criteria.append(RankingCriterion(measurement=measurement, direction=direction))

    return RankingConfig(measurements=tuple(criteria))


def _parse_opportunity_discovery(
    value: Any,
    source: str,
    *,
    timeframes: Timeframes,
) -> OpportunityDiscoveryConfig | None:
    if value is None:
        return None

    field_name = "opportunity_discovery"
    base_mapping = _as_object(value, source, field_name)
    if "kind" not in base_mapping:
        raise _error(source, field_name, "missing required field(s): kind")
    raw_kind = _text(base_mapping["kind"], source, f"{field_name}.kind")
    try:
        kind = DiscoveryKind(raw_kind)
    except ValueError:
        allowed = ", ".join(item.value for item in DiscoveryKind)
        raise _error(
            source,
            f"{field_name}.kind",
            f"must be one of {allowed}",
        ) from None

    mapping = _check_keys(
        value,
        source=source,
        field_name=field_name,
        required=frozenset(
            {
                "kind",
                "reference_timeframe",
                "previous_close_timeframe",
                "previous_close_is_daily",
                "max_data_age_seconds",
                "required_fields",
                "required_measurements",
                "eligible_sessions",
                "require_verified_session",
                "ranking",
            }
        ),
        optional=frozenset(
            {"min_price", "max_price", "excluded_symbols"}
        ),
    )
    reference_timeframe = _text(
        mapping["reference_timeframe"],
        source,
        f"{field_name}.reference_timeframe",
    )
    if reference_timeframe != timeframes.signal:
        raise _error(
            source,
            f"{field_name}.reference_timeframe",
            "must match timeframes.signal",
        )
    previous_close_timeframe = _text(
        mapping["previous_close_timeframe"],
        source,
        f"{field_name}.previous_close_timeframe",
    )
    if previous_close_timeframe == reference_timeframe:
        raise _error(
            source,
            field_name,
            "previous_close_timeframe and reference_timeframe must differ",
        )

    max_age = _integer(
        mapping["max_data_age_seconds"],
        source,
        f"{field_name}.max_data_age_seconds",
    )
    if max_age <= 0:
        raise _error(
            source,
            f"{field_name}.max_data_age_seconds",
            "must be greater than 0",
        )

    min_price = _optional_positive_number(
        mapping.get("min_price"),
        source,
        f"{field_name}.min_price",
    )
    max_price = _optional_positive_number(
        mapping.get("max_price"),
        source,
        f"{field_name}.max_price",
    )
    if (
        min_price is not None
        and max_price is not None
        and max_price <= min_price
    ):
        raise _error(
            source,
            f"{field_name}.max_price",
            "must be greater than min_price",
        )

    required_measurements = _string_tuple(
        mapping["required_measurements"],
        source,
        f"{field_name}.required_measurements",
        allow_empty=False,
    )
    unknown_measurements = sorted(
        set(required_measurements) - _GOLD_REQUIRED_MEASUREMENTS
    )
    if unknown_measurements:
        raise _error(
            source,
            f"{field_name}.required_measurements",
            f"unsupported measurement(s) {unknown_measurements}; allowed: "
            f"{', '.join(sorted(_GOLD_REQUIRED_MEASUREMENTS))}",
        )

    return GoldAnalysisDiscoveryConfig(
        kind=kind,
        reference_timeframe=reference_timeframe,
        previous_close_timeframe=previous_close_timeframe,
        previous_close_is_daily=_boolean(
            mapping["previous_close_is_daily"],
            source,
            f"{field_name}.previous_close_is_daily",
        ),
        max_data_age_seconds=max_age,
        min_price=min_price,
        max_price=max_price,
        required_fields=_parse_required_fields(mapping["required_fields"], source),
        required_measurements=required_measurements,
        eligible_sessions=_parse_discovery_sessions(
            mapping["eligible_sessions"],
            source,
            allowed=_GOLD_DISCOVERY_SESSIONS,
        ),
        excluded_symbols=_parse_excluded_symbols(
            mapping.get("excluded_symbols"), source
        ),
        require_verified_session=_boolean(
            mapping["require_verified_session"],
            source,
            f"{field_name}.require_verified_session",
        ),
        ranking=_parse_ranking(
            mapping["ranking"],
            source,
            allowed_measurements=_GOLD_RANKING_MEASUREMENTS,
        ),
    )


def _parse_strategy(data: Any, source: Path) -> StrategyConfig:
    source_text = str(source)
    root = _check_keys(
        data,
        source=source_text,
        field_name="",
        required=frozenset(
            {
                "schema_version",
                "strategy_name",
                "strategy_version",
                "status",
                "execution_enabled",
                "description",
                "supported_instruments",
                "timeframes",
                "sessions",
                "entry_conditions",
                "exit_conditions",
                "position_sizing",
                "risk_limits",
                "stop_loss",
                "take_profit",
                "strategy_parameters",
            }
        ),
        optional=frozenset({"notes", "opportunity_discovery"}),
    )

    schema_version = _integer(root["schema_version"], source_text, "schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise _error(
            source_text,
            "schema_version",
            f"unsupported version {schema_version}; expected {SUPPORTED_SCHEMA_VERSION}",
        )

    raw_status = _text(root["status"], source_text, "status")
    if raw_status not in _STRATEGY_STATUSES:
        raise _error(
            source_text,
            "status",
            f"must be one of {', '.join(_STRATEGY_STATUSES)}",
        )
    status = StrategyStatus(raw_status)

    notes = (
        _string_tuple(root["notes"], source_text, "notes", allow_empty=True)
        if "notes" in root
        else ()
    )

    entry_conditions = _parse_condition_group(
        root["entry_conditions"], source_text, "entry_conditions"
    )
    exit_conditions = _parse_condition_group(
        root["exit_conditions"], source_text, "exit_conditions"
    )
    position_sizing = _parse_position_sizing(root["position_sizing"], source_text)
    risk_limits = _parse_risk_limits(root["risk_limits"], source_text)
    stop_loss = _parse_stop_loss(root["stop_loss"], source_text)
    supported_instruments = _parse_supported_instruments(
        root["supported_instruments"], source_text
    )
    timeframes = _parse_timeframes(root["timeframes"], source_text)
    opportunity_discovery = _parse_opportunity_discovery(
        root.get("opportunity_discovery"),
        source_text,
        timeframes=timeframes,
    )
    config = StrategyConfig(
        schema_version=schema_version,
        strategy_name=_text(root["strategy_name"], source_text, "strategy_name"),
        strategy_version=_text(root["strategy_version"], source_text, "strategy_version"),
        status=status,
        execution_enabled=_boolean(
            root["execution_enabled"], source_text, "execution_enabled"
        ),
        description=_text(root["description"], source_text, "description"),
        notes=notes,
        supported_instruments=supported_instruments,
        timeframes=timeframes,
        sessions=_parse_sessions(root["sessions"], source_text),
        entry_conditions=entry_conditions,
        exit_conditions=exit_conditions,
        position_sizing=position_sizing,
        risk_limits=risk_limits,
        stop_loss=stop_loss,
        take_profit=_parse_take_profit(root["take_profit"], source_text),
        strategy_parameters=_as_object(
            root["strategy_parameters"], source_text, "strategy_parameters"
        ),
        opportunity_discovery=opportunity_discovery,
        source_path=source,
    )

    if config.status is StrategyStatus.PLACEHOLDER and config.execution_enabled:
        raise _error(
            source_text,
            "execution_enabled",
            "a placeholder strategy cannot be enabled for execution",
        )
    if config.status is StrategyStatus.VALIDATED:
        if not config.entry_conditions.conditions:
            raise _error(
                source_text,
                "entry_conditions.conditions",
                "a validated strategy must define at least one entry condition",
            )
        if not config.stop_loss.required:
            raise _error(
                source_text,
                "stop_loss.required",
                "a validated strategy must require a stop-loss",
            )
        if config.stop_loss.placeholder_values or (
            config.take_profit.enabled and config.take_profit.placeholder_values
        ):
            raise _error(
                source_text,
                "stop_loss.placeholder_values",
                "a validated strategy cannot retain placeholder stop-loss or take-profit values",
            )
    if (
        config.position_sizing.risk_per_trade_fraction
        > config.risk_limits.max_risk_per_trade_fraction + 1e-12
    ):
        raise _error(
            source_text,
            "position_sizing.risk_per_trade_fraction",
            "cannot exceed risk_limits.max_risk_per_trade_fraction",
        )
    return config


def _parse_settings(data: Any, source: Path) -> Settings:
    source_text = str(source)
    mapping = _check_keys(
        data,
        source=source_text,
        field_name="",
        required=frozenset(
            {
                "schema_version",
                "trading_mode",
                "live_trading_enabled",
                "broker_adapter",
                "max_risk_per_trade_fraction",
                "max_concurrent_positions",
                "max_daily_loss_fraction",
            }
        ),
        optional=frozenset(
            {
                "log_level",
                "market_data_provider",
                "mt5_terminal_path",
                "execution_mode",
            }
        ),
    )

    schema_version = _integer(mapping["schema_version"], source_text, "schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise _error(
            source_text,
            "schema_version",
            f"unsupported version {schema_version}; expected {SUPPORTED_SCHEMA_VERSION}",
        )

    raw_mode = _text(mapping["trading_mode"], source_text, "trading_mode")
    try:
        trading_mode = TradingMode(raw_mode)
    except ValueError:
        allowed = ", ".join(item.value for item in TradingMode)
        raise _error(
            source_text,
            "trading_mode",
            f"must be one of {allowed}",
        ) from None

    execution_mode = ExecutionMode.DISABLED
    if "execution_mode" in mapping:
        raw_execution_mode = _text(
            mapping["execution_mode"],
            source_text,
            "execution_mode",
        )
        try:
            execution_mode = ExecutionMode(raw_execution_mode)
        except ValueError:
            allowed = ", ".join(item.value for item in ExecutionMode)
            raise _error(
                source_text,
                "execution_mode",
                f"must be one of {allowed}",
            ) from None
    max_positions = _integer(
        mapping["max_concurrent_positions"], source_text, "max_concurrent_positions"
    )
    if max_positions <= 0:
        raise _error(source_text, "max_concurrent_positions", "must be greater than 0")

    log_level = "INFO"
    if "log_level" in mapping:
        log_level = _parse_log_level(mapping["log_level"], source_text, "log_level")

    market_data_provider = "disabled"
    if "market_data_provider" in mapping:
        market_data_provider = _text(
            mapping["market_data_provider"],
            source_text,
            "market_data_provider",
        )
        if market_data_provider not in _MARKET_DATA_PROVIDERS:
            allowed = ", ".join(sorted(_MARKET_DATA_PROVIDERS))
            raise _error(
                source_text,
                "market_data_provider",
                f"must be one of {allowed}",
            )

    mt5_terminal_path: str | None = None
    if mapping.get("mt5_terminal_path") is not None:
        mt5_terminal_path = _text(
            mapping["mt5_terminal_path"],
            source_text,
            "mt5_terminal_path",
        )
    if market_data_provider == "mt5" and mt5_terminal_path is None:
        raise _error(
            source_text,
            "mt5_terminal_path",
            "must be provided when market_data_provider is 'mt5'",
        )

    return Settings(
        schema_version=schema_version,
        trading_mode=trading_mode,
        live_trading_enabled=_boolean(
            mapping["live_trading_enabled"], source_text, "live_trading_enabled"
        ),
        broker_adapter=_text(mapping["broker_adapter"], source_text, "broker_adapter"),
        max_risk_per_trade_fraction=_fraction(
            mapping["max_risk_per_trade_fraction"],
            source_text,
            "max_risk_per_trade_fraction",
        ),
        max_concurrent_positions=max_positions,
        max_daily_loss_fraction=_fraction(
            mapping["max_daily_loss_fraction"],
            source_text,
            "max_daily_loss_fraction",
        ),
        log_level=log_level,
        market_data_provider=market_data_provider,
        mt5_terminal_path=mt5_terminal_path,
        execution_mode=execution_mode,
    )


def _reject_json_constant(value: str) -> None:
    raise ConfigurationError(f"non-finite JSON number {value!r} is not allowed")


def _parse_finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ConfigurationError(f"non-finite JSON number {value!r} is not allowed")
    return number


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_json(path: str | Path) -> tuple[Any, Path]:
    resolved = Path(path).expanduser().resolve()
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigurationError(f"configuration file not found: {resolved}") from None
    except UnicodeDecodeError:
        raise ConfigurationError(f"{resolved}: file is not valid UTF-8") from None
    except OSError as exc:
        raise ConfigurationError(f"{resolved}: could not read file: {exc}") from None

    if not text.strip():
        raise ConfigurationError(f"{resolved}: configuration file is empty")

    try:
        data = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except ConfigurationError as exc:
        raise ConfigurationError(f"{resolved}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"{resolved}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from None
    return data, resolved


def load_settings(
    path: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> Settings:
    """Load and validate machine-level execution and read-only data settings.

    When ``path`` is omitted, ``TRANTOR_SETTINGS_PATH`` is used, falling back to
    ``config/settings.json``. A missing file is an error; there is no silent
    safe-default configuration file.
    """

    environment = os.environ if environ is None else environ
    if path is None:
        configured_path = environment.get("TRANTOR_SETTINGS_PATH")
        if configured_path is not None:
            if not configured_path.strip():
                raise ConfigurationError("TRANTOR_SETTINGS_PATH is set but empty")
            path = configured_path
        else:
            path = DEFAULT_SETTINGS_PATH

    data, source = _load_json(path)
    settings = _parse_settings(data, source)

    log_level_override = environment.get("TRANTOR_LOG_LEVEL")
    if log_level_override is not None:
        if not log_level_override.strip():
            raise ConfigurationError("TRANTOR_LOG_LEVEL is set but empty")
        settings = replace(
            settings,
            log_level=_parse_log_level(
                log_level_override,
                f"environment variable TRANTOR_LOG_LEVEL ({source})",
                "log_level",
            ),
        )
    return settings


def load_strategy_config(path: str | Path) -> StrategyConfig:
    """Load and validate one JSON strategy configuration file."""

    data, source = _load_json(path)
    return _parse_strategy(data, source)


def settings_snapshot(settings: Settings) -> dict[str, object]:
    """Return a redacted, operator-visible effective settings snapshot."""

    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    return {
        "schema_version": settings.schema_version,
        "execution_mode": settings.execution_mode.value,
        "execution_enabled": settings.execution_enabled,
        "trading_mode": settings.trading_mode.value,
        "live_trading_enabled": settings.live_trading_enabled,
        "broker_adapter": settings.broker_adapter,
        "max_risk_per_trade_fraction": settings.max_risk_per_trade_fraction,
        "max_concurrent_positions": settings.max_concurrent_positions,
        "max_daily_loss_fraction": settings.max_daily_loss_fraction,
        "log_level": settings.log_level,
        "market_data_provider": settings.market_data_provider,
        "mt5_terminal_path_configured": settings.mt5_terminal_path is not None,
        "config_fingerprint": settings_fingerprint(settings),
    }


def settings_fingerprint(settings: Settings) -> str:
    """Return a stable fingerprint of non-secret effective settings."""

    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    payload = {
        "schema_version": settings.schema_version,
        "execution_mode": settings.execution_mode.value,
        "trading_mode": settings.trading_mode.value,
        "live_trading_enabled": settings.live_trading_enabled,
        "broker_adapter": settings.broker_adapter,
        "max_risk_per_trade_fraction": settings.max_risk_per_trade_fraction,
        "max_concurrent_positions": settings.max_concurrent_positions,
        "max_daily_loss_fraction": settings.max_daily_loss_fraction,
        "log_level": settings.log_level,
        "market_data_provider": settings.market_data_provider,
        "mt5_terminal_path": settings.mt5_terminal_path,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
