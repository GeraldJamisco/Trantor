"""Canonical, versioned serialization for research strategy decisions.

This module is deliberately storage-oriented. It does not implement a backtest,
broker adapter, order handling, monetary sizing, or market-data acquisition.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any

from ..models import AssetClass, Side
from .xauusd_p import (
    BiasState,
    Confidence,
    DecisionStatus,
    RejectionCode,
    SessionFilterMode,
    StrategyDecision,
    StrategyIdentity,
    TrendState,
    ZoneKind,
)

__all__ = [
    "DATASET_TIME_SEMANTICS",
    "RESEARCH_RECORD_SCHEMA",
    "RESEARCH_RECORD_SCHEMA_VERSION",
    "ResearchRecordError",
    "ResearchRecordIdentityError",
    "ResearchRecordSchemaError",
    "canonical_json",
    "parse_research_record",
    "serialize_strategy_decision",
    "validate_research_record",
]

RESEARCH_RECORD_SCHEMA = "trantor.research_decision"
RESEARCH_RECORD_SCHEMA_VERSION = "1.0"
DATASET_TIME_SEMANTICS = "dataset_time_unverified_absolute_utc"

_ALLOWED_IDENTITIES = {
    "xauusd_p_approved_v1": (
        "xauusd_p_multi_timeframe",
        "1.0.0",
        "approved",
    ),
    "xauusd_p_filter_isolated_v1": (
        "xauusd_p_multi_timeframe_research_filter_isolated",
        "1.0.0",
        "research_filter_isolated",
    ),
}

_ALLOWED_IDENTITY_FILTER_MODES = {
    "xauusd_p_approved_v1": {SessionFilterMode.APPROVED.value},
    "xauusd_p_filter_isolated_v1": {
        SessionFilterMode.RESEARCH_ISOLATED.value
    },
}

_REQUIRED_LIMITATIONS = frozenset(
    {
        "dataset_time_does_not_claim_verified_absolute_utc",
        "session_calendar_unverified",
        "news_calendar_unverified",
        "cost_verification_unavailable",
        "monetary_sizing_blocked",
        "research_only",
        "execution_not_ready",
    }
)

# Price, risk, ratio, and level fields use a canonical binary64 float
# representation. Integer-valued counts and score points remain integers.
_CANONICAL_FLOAT_KEYS = frozenset(
    {
        "entry",
        "stop",
        "tp1",
        "tp2",
        "risk_distance",
        "tp1_fraction",
        "tp2_fraction",
        "breakeven",
        "entry_reference",
        "ema_fast",
        "ema_slow",
        "tolerance",
        "previous_high",
        "previous_low",
        "atr",
        "ph1",
        "ph2",
        "pl1",
        "pl2",
        "lower",
        "upper",
        "center",
        "risk_fraction",
        "daily_loss_fraction",
    }
)

_INSTRUMENT_FIELDS = frozenset(
    {"symbol", "asset_class", "exchange", "currency", "timezone"}
)
_REJECTION_FIELDS = frozenset({"code", "message"})
_SCORE_FIELDS = frozenset({"total", "maximum", "confidence", "factors"})
_FACTOR_FIELDS = frozenset(
    {"name", "points", "awarded", "available", "detail"}
)
_SESSION_FIELDS = frozenset(
    {"in_overlap", "calendar_verified", "research_allowed", "reason", "filter_mode"}
)
_NEWS_FIELDS = frozenset(
    {"available", "safe", "point_awarded", "unaware", "reason"}
)
_PLAN_FIELDS = frozenset(
    {
        "entry",
        "stop",
        "tp1",
        "tp2",
        "risk_distance",
        "tp1_fraction",
        "tp2_fraction",
        "breakeven",
        "breakeven_costs_verified",
        "monetary_sizing_status",
        "partial_exit_status",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {
        "side",
        "decision_time",
        "entry_time",
        "entry_reference",
        "confidence",
        "execution_ready",
        "research_mode",
        "news_unaware",
        "rationale",
    }
)
_DAILY_FIELDS = frozenset(
    {
        "state",
        "ema_fast",
        "ema_slow",
        "tolerance",
        "latest_bar_at",
        "previous_high",
        "previous_low",
        "bar_count",
    }
)
_H4_FIELDS = frozenset(
    {
        "state",
        "atr",
        "ph1",
        "ph2",
        "pl1",
        "pl2",
        "latest_bar_at",
        "bar_count",
        "pivot_count",
    }
)
_ZONE_FIELDS = frozenset({"lower", "upper", "center", "evidence", "zones"})
_ZONE_ITEM_FIELDS = frozenset(
    {
        "kind",
        "lower",
        "upper",
        "center",
        "confirmed_at",
        "confirmed_index",
        "evidence",
        "direction",
    }
)
_RISK_FIELDS = frozenset(
    {
        "eligible",
        "risk_fraction",
        "daily_loss_fraction",
        "sizing_status",
        "reasons",
        "cost_adjusted",
    }
)


class ResearchRecordError(ValueError):
    """Base error for an invalid or unsupported research record."""


class ResearchRecordSchemaError(ResearchRecordError):
    """The record schema or schema version is unsupported."""


class ResearchRecordIdentityError(ResearchRecordError):
    """The decision and record identity do not agree."""


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchRecordError("decision timestamps must be timezone-aware")
    # Preserve the represented offset; do not claim the offset is verified UTC.
    return value.isoformat()


def _value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    return value


def _score(decision: StrategyDecision) -> dict[str, Any]:
    return {
        "total": decision.score.total,
        "maximum": decision.score.maximum,
        "confidence": _value(decision.score.confidence),
        "factors": [
            {
                "name": factor.name,
                "points": factor.points,
                "awarded": factor.awarded,
                "available": factor.available,
                "detail": factor.detail,
            }
            for factor in decision.score.factors
        ],
    }


def _session(decision: StrategyDecision) -> dict[str, Any]:
    session = decision.session
    return {
        "in_overlap": session.in_overlap,
        "calendar_verified": session.calendar_verified,
        "research_allowed": session.research_allowed,
        "reason": session.reason,
        "filter_mode": _value(session.filter_mode),
    }


def _news(decision: StrategyDecision) -> dict[str, Any]:
    news = decision.news
    return {
        "available": news.available,
        "safe": news.safe,
        "point_awarded": news.point_awarded,
        "unaware": news.unaware,
        "reason": news.reason,
    }


def _plan(decision: StrategyDecision) -> dict[str, Any] | None:
    candidate = decision.candidate
    if candidate is None:
        return None
    plan = candidate.plan
    return {
        "entry": plan.entry,
        "stop": plan.stop,
        "tp1": plan.tp1,
        "tp2": plan.tp2,
        "risk_distance": plan.risk_distance,
        "tp1_fraction": plan.tp1_fraction,
        "tp2_fraction": plan.tp2_fraction,
        "breakeven": plan.breakeven,
        "breakeven_costs_verified": plan.breakeven_costs_verified,
        "monetary_sizing_status": plan.monetary_sizing_status,
        "partial_exit_status": plan.partial_exit_status,
    }


def _candidate(decision: StrategyDecision) -> dict[str, Any] | None:
    candidate = decision.candidate
    if candidate is None:
        return None
    return {
        "side": _value(candidate.side),
        "decision_time": _iso(candidate.decision_time),
        "entry_time": _iso(candidate.entry_time),
        "entry_reference": candidate.entry_reference,
        "confidence": _value(candidate.confidence),
        "execution_ready": candidate.execution_ready,
        "research_mode": candidate.research_mode,
        "news_unaware": candidate.news_unaware,
        "rationale": candidate.rationale,
    }


def _daily_bias(decision: StrategyDecision) -> dict[str, Any] | None:
    daily = decision.daily_bias
    if daily is None:
        return None
    return {
        "state": _value(daily.state),
        "ema_fast": daily.ema_fast,
        "ema_slow": daily.ema_slow,
        "tolerance": daily.tolerance,
        "latest_bar_at": _iso(daily.latest_bar_at) if daily.latest_bar_at else None,
        "previous_high": daily.previous_high,
        "previous_low": daily.previous_low,
        "bar_count": daily.bar_count,
    }


def _h4_trend(decision: StrategyDecision) -> dict[str, Any] | None:
    h4 = decision.h4_trend
    if h4 is None:
        return None
    return {
        "state": _value(h4.state),
        "atr": h4.atr,
        "ph1": h4.ph1,
        "ph2": h4.ph2,
        "pl1": h4.pl1,
        "pl2": h4.pl2,
        "latest_bar_at": _iso(h4.latest_bar_at) if h4.latest_bar_at else None,
        "bar_count": h4.bar_count,
        "pivot_count": len(h4.pivots),
    }


def _zone(decision: StrategyDecision) -> dict[str, Any] | None:
    zone = decision.zone
    if zone is None:
        return None
    return {
        "lower": zone.lower,
        "upper": zone.upper,
        "center": zone.center,
        "evidence": list(zone.evidence),
        "zones": [
            {
                "kind": _value(item.kind),
                "lower": item.lower,
                "upper": item.upper,
                "center": item.center,
                "confirmed_at": _iso(item.confirmed_at),
                "confirmed_index": item.confirmed_index,
                "evidence": item.evidence,
                "direction": _value(item.direction) if item.direction is not None else None,
            }
            for item in zone.zones
        ],
    }


def _risk(decision: StrategyDecision) -> dict[str, Any] | None:
    risk = decision.risk
    if risk is None:
        return None
    return {
        "eligible": risk.eligible,
        "risk_fraction": risk.risk_fraction,
        "daily_loss_fraction": risk.daily_loss_fraction,
        "sizing_status": risk.sizing_status,
        "reasons": list(risk.reasons),
        "cost_adjusted": risk.cost_adjusted,
    }


def _limitations(decision: StrategyDecision) -> list[str]:
    limitations = [
        "dataset_time_does_not_claim_verified_absolute_utc",
        "session_calendar_unverified",
        "news_calendar_unverified",
        "cost_verification_unavailable",
        "monetary_sizing_blocked",
        "research_only",
        "execution_not_ready",
    ]
    if decision.session.filter_mode is SessionFilterMode.RESEARCH_ISOLATED:
        limitations.append("session_filter_isolated_research_variant")
    if decision.news.unaware:
        limitations.append("news_unaware_no_verified_calendar")
    if decision.candidate is not None:
        limitations.append("monetary_sizing_blocked_unverified_contract_economics")
        limitations.append("partial_exit_volume_feasibility_blocked")
    return limitations


def serialize_strategy_decision(
    decision: StrategyDecision,
    identity: StrategyIdentity,
    *,
    original_decision_time: str | None = None,
) -> dict[str, Any]:
    """Serialize a detailed decision into the canonical version-1 record."""

    if not isinstance(decision, StrategyDecision):
        raise TypeError(
            "serialize_strategy_decision requires a StrategyDecision; "
            "TradeSignal and candidate-only records are not authoritative"
        )
    if not isinstance(identity, StrategyIdentity):
        raise TypeError("identity must be a StrategyIdentity")
    if decision.variant_id != identity.variant_id:
        raise ResearchRecordIdentityError(
            "decision variant_id does not match the supplied strategy identity"
        )
    expected_identity = _ALLOWED_IDENTITIES.get(identity.variant_id)
    if expected_identity != (
        identity.strategy_name,
        identity.strategy_version,
        identity.variant_name,
    ):
        raise ResearchRecordIdentityError(
            "strategy identity does not match the declared variant_id"
        )
    if not identity.research_only:
        raise ResearchRecordIdentityError("research identity must be research-only")
    decision_time = decision.decision_time
    decision_iso = _iso(decision_time)
    original = (
        original_decision_time
        if original_decision_time is not None
        else decision_iso
    )
    if not isinstance(original, str) or not original.strip():
        raise ResearchRecordError("original_decision_time must be a non-empty string")

    plan = _plan(decision)
    record = {
        "schema": RESEARCH_RECORD_SCHEMA,
        "schema_version": RESEARCH_RECORD_SCHEMA_VERSION,
        "record_type": "strategy_decision",
        "authoritative_source": "StrategyDecision",
        "strategy_name": identity.strategy_name,
        "strategy_version": identity.strategy_version,
        "variant_id": identity.variant_id,
        "variant_name": identity.variant_name,
        "research_only": identity.research_only,
        "execution_ready": decision.execution_ready,
        "decision_time": decision_iso,
        "decision_time_original": original,
        "time_semantics": DATASET_TIME_SEMANTICS,
        "absolute_utc_provenance": "unverified",
        "symbol": decision.instrument.symbol,
        "timeframe": "15m",
        "instrument": {
            "symbol": decision.instrument.symbol,
            "asset_class": _value(decision.instrument.asset_class),
            "exchange": decision.instrument.exchange,
            "currency": decision.instrument.currency,
            "timezone": decision.instrument.timezone,
        },
        "status": _value(decision.status),
        "rejections": [
            {"code": _value(rejection.code), "message": rejection.message}
            for rejection in decision.rejections
        ],
        "score": _score(decision),
        "session": _session(decision),
        "news": _news(decision),
        "news_unaware": decision.news_unaware,
        "plan": plan,
        "candidate": _candidate(decision),
        "daily_bias": _daily_bias(decision),
        "h4_trend": _h4_trend(decision),
        "zone": _zone(decision),
        "risk": _risk(decision),
        "limitations": _limitations(decision),
    }
    return validate_research_record(record)


_REQUIRED_TOP_LEVEL = frozenset(
    {
        "schema",
        "schema_version",
        "record_type",
        "authoritative_source",
        "strategy_name",
        "strategy_version",
        "variant_id",
        "variant_name",
        "research_only",
        "execution_ready",
        "decision_time",
        "decision_time_original",
        "time_semantics",
        "absolute_utc_provenance",
        "symbol",
        "timeframe",
        "instrument",
        "status",
        "rejections",
        "score",
        "session",
        "news",
        "news_unaware",
        "plan",
        "candidate",
        "daily_bias",
        "h4_trend",
        "zone",
        "risk",
        "limitations",
    }
)


def _reject_unknown_fields(
    record: Mapping[str, Any],
    allowed: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(record).difference(allowed), key=str)
    if unknown:
        raise ResearchRecordSchemaError(
            f"unsupported {label} field(s): {', '.join(repr(key) for key in unknown)}"
        )


def _finite_float(
    value: object,
    key: str,
    *,
    positive: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchRecordSchemaError(f"field {key!r} must be numeric")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ResearchRecordSchemaError(
            f"field {key!r} cannot be represented as a finite number"
        ) from exc
    if not math.isfinite(number):
        raise ResearchRecordSchemaError(f"field {key!r} must be finite")
    if positive and number <= 0:
        raise ResearchRecordSchemaError(f"field {key!r} must be positive")
    return number


def _normalize_numbers(value: object, key: str | None = None) -> object:
    if isinstance(value, Mapping):
        return {
            item_key: _normalize_numbers(item_value, item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_numbers(item, key) for item in value]
    if (
        key in _CANONICAL_FLOAT_KEYS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        try:
            number = float(value)
        except (OverflowError, ValueError) as exc:
            raise ResearchRecordSchemaError(
                f"field {key!r} cannot be represented as a finite number"
            ) from exc
        if not math.isfinite(number):
            raise ResearchRecordSchemaError(f"field {key!r} must be finite")
        return 0.0 if number == 0.0 else number
    return value


def _require(record: Mapping[str, Any], key: str, expected_type: type) -> Any:
    if key not in record:
        raise ResearchRecordSchemaError(f"missing required field {key!r}")
    value = record[key]
    if not isinstance(value, expected_type):
        raise ResearchRecordSchemaError(
            f"field {key!r} must be {expected_type.__name__}"
        )
    return value


def _require_nonempty(record: Mapping[str, Any], key: str) -> str:
    value = _require(record, key, str)
    if not value.strip():
        raise ResearchRecordSchemaError(f"field {key!r} must not be empty")
    return value


def _require_bool(record: Mapping[str, Any], key: str) -> bool:
    value = _require(record, key, bool)
    return value


def _require_int(record: Mapping[str, Any], key: str) -> int:
    value = _require(record, key, int)
    if isinstance(value, bool):
        raise ResearchRecordSchemaError(f"field {key!r} must be an integer")
    return value


def _require_number(
    record: Mapping[str, Any],
    key: str,
    *,
    positive: bool = False,
) -> float:
    if key not in record:
        raise ResearchRecordSchemaError(f"missing required field {key!r}")
    return _finite_float(record[key], key, positive=positive)


def _optional_number(record: Mapping[str, Any], key: str) -> float | None:
    if key not in record:
        raise ResearchRecordSchemaError(f"missing required field {key!r}")
    value = record[key]
    if value is None:
        return None
    return _finite_float(value, key)


def _optional_mapping(
    record: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any] | None:
    if key not in record:
        raise ResearchRecordSchemaError(f"missing required field {key!r}")
    value = record[key]
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ResearchRecordSchemaError(f"field {key!r} must be a mapping or null")
    return value


def _parse_iso(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ResearchRecordSchemaError(f"{field_name} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchRecordSchemaError(
            f"{field_name} must include a timezone offset"
        )
    return parsed


def _optional_iso(record: Mapping[str, Any], key: str) -> None:
    if key not in record:
        raise ResearchRecordSchemaError(f"missing required field {key!r}")
    value = record[key]
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ResearchRecordSchemaError(f"field {key!r} must be ISO-8601 or null")
    _parse_iso(value, key)


def _string_list(record: Mapping[str, Any], key: str) -> list[str]:
    values = _require(record, key, list)
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ResearchRecordSchemaError(f"field {key!r} must contain strings")
        result.append(value)
    return result


def validate_research_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete canonical record and return a plain copy."""

    if not isinstance(record, Mapping):
        raise ResearchRecordSchemaError("research record must be a mapping")
    missing = sorted(_REQUIRED_TOP_LEVEL.difference(record))
    if missing:
        raise ResearchRecordSchemaError(
            f"missing required field(s): {', '.join(repr(key) for key in missing)}"
        )
    _reject_unknown_fields(record, _REQUIRED_TOP_LEVEL, "record")

    schema = _require(record, "schema", str)
    version = _require(record, "schema_version", str)
    if schema != RESEARCH_RECORD_SCHEMA:
        raise ResearchRecordSchemaError(f"unsupported record schema {schema!r}")
    if version != RESEARCH_RECORD_SCHEMA_VERSION:
        raise ResearchRecordSchemaError(
            f"unsupported research record schema version {version!r}"
        )

    try:
        strategy_name = _require_nonempty(record, "strategy_name")
        strategy_version = _require_nonempty(record, "strategy_version")
        variant_id = _require_nonempty(record, "variant_id")
        variant_name = _require_nonempty(record, "variant_name")
    except ResearchRecordSchemaError as exc:
        raise ResearchRecordIdentityError(str(exc)) from exc
    expected = _ALLOWED_IDENTITIES.get(variant_id)
    if expected is None:
        raise ResearchRecordIdentityError(
            f"unknown or inconsistent variant_id {variant_id!r}"
        )
    if (strategy_name, strategy_version, variant_name) != expected:
        raise ResearchRecordIdentityError(
            "strategy name/version/variant name do not match variant_id"
        )

    if _require_nonempty(record, "record_type") != "strategy_decision":
        raise ResearchRecordSchemaError("record_type must be 'strategy_decision'")
    if record.get("research_only") is not True:
        raise ResearchRecordSchemaError("research_only must be true")
    if record.get("execution_ready") is not False:
        raise ResearchRecordSchemaError("execution_ready must be false")
    if record.get("authoritative_source") != "StrategyDecision":
        raise ResearchRecordSchemaError(
            "authoritative_source must be 'StrategyDecision'"
        )
    if record.get("time_semantics") != DATASET_TIME_SEMANTICS:
        raise ResearchRecordSchemaError("unsupported time_semantics")
    if record.get("absolute_utc_provenance") != "unverified":
        raise ResearchRecordSchemaError(
            "absolute_utc_provenance must be marked unverified"
        )
    decision_dt = _parse_iso(
        _require_nonempty(record, "decision_time"),
        "decision_time",
    )
    _require_nonempty(record, "decision_time_original")
    symbol = _require_nonempty(record, "symbol")
    if symbol != "XAUUSD-P":
        raise ResearchRecordSchemaError("record symbol must be 'XAUUSD-P'")
    if _require_nonempty(record, "timeframe") != "15m":
        raise ResearchRecordSchemaError("timeframe must be '15m'")

    instrument = _require(record, "instrument", Mapping)
    _reject_unknown_fields(instrument, _INSTRUMENT_FIELDS, "instrument")
    if _require_nonempty(instrument, "symbol") != symbol:
        raise ResearchRecordSchemaError("instrument symbol does not match symbol")
    if _require_nonempty(instrument, "asset_class") not in {
        item.value for item in AssetClass
    }:
        raise ResearchRecordSchemaError("invalid instrument asset_class")
    for key in ("exchange", "currency", "timezone"):
        _require(instrument, key, str)

    status = _require_nonempty(record, "status")
    if status not in {item.value for item in DecisionStatus}:
        raise ResearchRecordSchemaError("invalid decision status")
    rejections = _require(record, "rejections", list)
    allowed_rejection_codes = {item.value for item in RejectionCode}
    for rejection in rejections:
        if not isinstance(rejection, Mapping):
            raise ResearchRecordSchemaError("rejection entries must be mappings")
        _reject_unknown_fields(rejection, _REJECTION_FIELDS, "rejection")
        if _require_nonempty(rejection, "code") not in allowed_rejection_codes:
            raise ResearchRecordSchemaError("invalid rejection code")
        _require_nonempty(rejection, "message")

    score = _require(record, "score", Mapping)
    _reject_unknown_fields(score, _SCORE_FIELDS, "score")
    total = _require_int(score, "total")
    maximum = _require_int(score, "maximum")
    if total < 0 or maximum < 0 or total > maximum:
        raise ResearchRecordSchemaError("invalid score total/maximum")
    if _require_nonempty(score, "confidence") not in {
        item.value for item in Confidence
    }:
        raise ResearchRecordSchemaError("invalid score confidence")
    factors = _require(score, "factors", list)
    awarded_total = 0
    for factor in factors:
        if not isinstance(factor, Mapping):
            raise ResearchRecordSchemaError("score factors must be mappings")
        _reject_unknown_fields(factor, _FACTOR_FIELDS, "score factor")
        _require_nonempty(factor, "name")
        points = _require_int(factor, "points")
        if points < 0:
            raise ResearchRecordSchemaError("score factor points cannot be negative")
        awarded = _require_bool(factor, "awarded")
        _require_bool(factor, "available")
        _require(factor, "detail", str)
        if awarded:
            awarded_total += points
    if awarded_total != total:
        raise ResearchRecordSchemaError("score total does not match awarded factors")

    session = _require(record, "session", Mapping)
    _reject_unknown_fields(session, _SESSION_FIELDS, "session")
    for key in ("in_overlap", "calendar_verified", "research_allowed"):
        _require_bool(session, key)
    _require_nonempty(session, "reason")
    if session["calendar_verified"] is not False:
        raise ResearchRecordSchemaError(
            "verified session calendar evidence is unavailable for this contract"
        )
    filter_mode = _require_nonempty(session, "filter_mode")
    if filter_mode not in {item.value for item in SessionFilterMode}:
        raise ResearchRecordSchemaError("invalid session filter_mode")
    if filter_mode not in _ALLOWED_IDENTITY_FILTER_MODES[variant_id]:
        raise ResearchRecordIdentityError(
            "session filter_mode does not match the research variant identity"
        )

    news = _require(record, "news", Mapping)
    _reject_unknown_fields(news, _NEWS_FIELDS, "news")
    for key in ("available", "safe", "point_awarded", "unaware"):
        _require_bool(news, key)
    _require_nonempty(news, "reason")
    news_unaware = _require_bool(record, "news_unaware")
    if news_unaware is not news["unaware"]:
        raise ResearchRecordSchemaError("news_unaware does not match news.unaware")
    if news_unaware is not True:
        raise ResearchRecordSchemaError(
            "verified news-calendar evidence is unavailable for this contract"
        )
    if (
        variant_id == "xauusd_p_filter_isolated_v1"
        and news["point_awarded"]
    ):
        raise ResearchRecordIdentityError(
            "research-isolated records cannot award the news point"
        )
    if news["available"] or news["safe"] or news["point_awarded"]:
        raise ResearchRecordSchemaError(
            "news availability, safety, or news point claims require verified calendar evidence"
        )

    plan = _optional_mapping(record, "plan")
    candidate = _optional_mapping(record, "candidate")
    if status == DecisionStatus.CANDIDATE.value:
        if candidate is None or plan is None:
            raise ResearchRecordSchemaError(
                "candidate decisions require candidate and plan records"
            )
    elif status == DecisionStatus.REJECTED.value:
        if not rejections:
            raise ResearchRecordSchemaError("rejected decisions require a reason")
        if candidate is not None or plan is not None:
            raise ResearchRecordSchemaError(
                "rejected decisions cannot contain a candidate or plan"
            )

    plan_values: dict[str, float | None] | None = None
    if plan is not None:
        _reject_unknown_fields(plan, _PLAN_FIELDS, "plan")
        entry = _require_number(plan, "entry", positive=True)
        stop = _require_number(plan, "stop", positive=True)
        tp1 = _require_number(plan, "tp1", positive=True)
        tp2 = _require_number(plan, "tp2", positive=True)
        risk_distance = _require_number(plan, "risk_distance", positive=True)
        tp1_fraction = _require_number(plan, "tp1_fraction", positive=True)
        tp2_fraction = _require_number(plan, "tp2_fraction", positive=True)
        breakeven = _optional_number(plan, "breakeven")
        if abs(tp1_fraction + tp2_fraction - 1.0) > 1e-9:
            raise ResearchRecordSchemaError("partial fractions must sum to 1.0")
        if breakeven is not None and not math.isclose(
            breakeven,
            entry,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ResearchRecordSchemaError(
                "breakeven must equal the entry reference in the current plan"
            )
        if not math.isclose(
            risk_distance,
            abs(entry - stop),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ResearchRecordSchemaError(
                "risk_distance must equal the absolute entry-to-stop distance"
            )
        if _require_bool(plan, "breakeven_costs_verified") is not False:
            raise ResearchRecordSchemaError(
                "breakeven cost verification is unavailable for this contract"
            )
        if plan.get("monetary_sizing_status") != "blocked_unverified_contract_economics":
            raise ResearchRecordSchemaError("monetary sizing must remain blocked")
        if _require_nonempty(plan, "partial_exit_status") != (
            "blocked_pending_volume_sizing"
        ):
            raise ResearchRecordSchemaError(
                "partial-exit volume feasibility must remain blocked"
            )
        plan_values = {
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "risk_distance": risk_distance,
        }

    if candidate is not None:
        _reject_unknown_fields(candidate, _CANDIDATE_FIELDS, "candidate")
        side = _require_nonempty(candidate, "side")
        if side not in {item.value for item in Side}:
            raise ResearchRecordSchemaError("invalid candidate side")
        candidate_time = _parse_iso(
            _require_nonempty(candidate, "decision_time"),
            "candidate decision_time",
        )
        if candidate_time != decision_dt:
            raise ResearchRecordSchemaError(
                "candidate decision_time must match the decision timestamp"
            )
        entry_time = _parse_iso(
            _require_nonempty(candidate, "entry_time"),
            "candidate entry_time",
        )
        if entry_time < decision_dt:
            raise ResearchRecordSchemaError(
                "candidate entry_time cannot precede the decision timestamp"
            )
        entry_reference = _require_number(
            candidate,
            "entry_reference",
            positive=True,
        )
        if plan_values is None or entry_reference != plan_values["entry"]:
            raise ResearchRecordSchemaError(
                "candidate entry_reference does not match plan entry"
            )
        candidate_confidence = _require_nonempty(candidate, "confidence")
        if candidate_confidence not in {item.value for item in Confidence}:
            raise ResearchRecordSchemaError("invalid candidate confidence")
        if candidate_confidence != score["confidence"]:
            raise ResearchRecordSchemaError(
                "candidate confidence must match the score confidence"
            )
        if candidate.get("execution_ready") is not False:
            raise ResearchRecordSchemaError("candidate execution_ready must be false")
        if candidate.get("research_mode") is not True:
            raise ResearchRecordSchemaError("candidate must remain in research mode")
        if _require_bool(candidate, "news_unaware") is not news_unaware:
            raise ResearchRecordSchemaError(
                "candidate news_unaware does not match the decision"
            )
        _require(candidate, "rationale", str)

        if plan_values is None:
            raise ResearchRecordSchemaError(
                "a candidate requires a trade plan"
            )
        entry = plan_values["entry"]
        stop = plan_values["stop"]
        tp1 = plan_values["tp1"]
        tp2 = plan_values["tp2"]
        if side == Side.BUY.value:
            if not (stop < entry < tp1 < tp2):
                raise ResearchRecordSchemaError(
                    "long plan requires stop < entry < TP1 < TP2"
                )
        elif not (stop > entry > tp1 > tp2):
            raise ResearchRecordSchemaError(
                "short plan requires stop > entry > TP1 > TP2"
            )

    daily = _optional_mapping(record, "daily_bias")
    if daily is not None:
        _reject_unknown_fields(daily, _DAILY_FIELDS, "daily_bias")
        if _require_nonempty(daily, "state") not in {
            item.value for item in BiasState
        }:
            raise ResearchRecordSchemaError("invalid daily bias state")
        for key in ("ema_fast", "ema_slow", "tolerance", "previous_high", "previous_low"):
            _optional_number(daily, key)
        _optional_iso(daily, "latest_bar_at")
        _require_int(daily, "bar_count")

    h4 = _optional_mapping(record, "h4_trend")
    if h4 is not None:
        _reject_unknown_fields(h4, _H4_FIELDS, "h4_trend")
        if _require_nonempty(h4, "state") not in {
            item.value for item in TrendState
        }:
            raise ResearchRecordSchemaError("invalid H4 trend state")
        for key in ("atr", "ph1", "ph2", "pl1", "pl2"):
            _optional_number(h4, key)
        _optional_iso(h4, "latest_bar_at")
        _require_int(h4, "bar_count")
        if _require_int(h4, "pivot_count") < 0:
            raise ResearchRecordSchemaError("pivot_count cannot be negative")

    zone = _optional_mapping(record, "zone")
    if zone is not None:
        _reject_unknown_fields(zone, _ZONE_FIELDS, "zone")
        for key in ("lower", "upper", "center"):
            _require_number(zone, key, positive=True)
        _string_list(zone, "evidence")
        zones = _require(zone, "zones", list)
        for item in zones:
            if not isinstance(item, Mapping):
                raise ResearchRecordSchemaError("zone entries must be mappings")
            _reject_unknown_fields(item, _ZONE_ITEM_FIELDS, "zone entry")
            if _require_nonempty(item, "kind") not in {
                zone_kind.value for zone_kind in ZoneKind
            }:
                raise ResearchRecordSchemaError("invalid zone kind")
            for key in ("lower", "upper", "center"):
                _require_number(item, key, positive=True)
            _parse_iso(_require_nonempty(item, "confirmed_at"), "zone confirmed_at")
            if _require_int(item, "confirmed_index") < 0:
                raise ResearchRecordSchemaError("zone index cannot be negative")
            _require(item, "evidence", str)
            direction = item.get("direction")
            if direction is not None and direction not in {
                side.value for side in Side
            }:
                raise ResearchRecordSchemaError("invalid zone direction")

    risk = _optional_mapping(record, "risk")
    if risk is not None:
        _reject_unknown_fields(risk, _RISK_FIELDS, "risk")
        _require_bool(risk, "eligible")
        _require_number(risk, "risk_fraction")
        _require_number(risk, "daily_loss_fraction")
        if _require_nonempty(risk, "sizing_status") != (
            "blocked_unverified_contract_economics"
        ):
            raise ResearchRecordSchemaError("monetary risk sizing must remain blocked")
        _string_list(risk, "reasons")
        if _require_bool(risk, "cost_adjusted") is not False:
            raise ResearchRecordSchemaError(
                "cost-adjusted risk is unavailable for this contract"
            )

    limitations = _string_list(record, "limitations")
    missing_limitations = sorted(_REQUIRED_LIMITATIONS.difference(limitations))
    if missing_limitations:
        raise ResearchRecordSchemaError(
            "missing required limitation(s): "
            + ", ".join(repr(item) for item in missing_limitations)
        )

    return _normalize_numbers(dict(record))


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ResearchRecordSchemaError(
        f"non-finite JSON number {value!r} is not allowed"
    )


def parse_research_record(payload: str | bytes | Mapping[str, Any]) -> dict[str, Any]:
    """Parse and validate a serialized record; unsupported versions fail closed."""

    if isinstance(payload, (str, bytes, bytearray)):
        try:
            loaded = json.loads(
                payload,
                parse_constant=_reject_nonfinite_json_constant,
            )
        except ResearchRecordSchemaError:
            raise
        except (ValueError, TypeError) as exc:
            raise ResearchRecordSchemaError("record is not valid JSON") from exc
    else:
        loaded = payload
    if not isinstance(loaded, Mapping):
        raise ResearchRecordSchemaError("record must be a JSON object")
    return validate_research_record(loaded)


def canonical_json(record: Mapping[str, Any]) -> str:
    """Return deterministic canonical JSON after schema validation."""

    validated = validate_research_record(record)
    try:
        return json.dumps(
            validated,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ResearchRecordSchemaError(
            f"record is not canonically serializable: {exc}"
        ) from exc
