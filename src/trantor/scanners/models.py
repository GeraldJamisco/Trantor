"""Structured research results for Phase 3 gold analysis.

Nothing in this module is a trade signal, risk assessment, order request, or
execution authorization. A candidate is only an explainable research result
that may be passed to a later strategy layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..config import StrategyStatus
from ..market_data import (
    MarketDataResult,
    MarketDataStatus,
    SessionInfo,
    ValidationIssueCode,
)
from ..models import MarketInstrument, MarketObservation

__all__ = [
    "EvaluationOutcome",
    "FilterEvaluation",
    "FilterName",
    "FilterStatus",
    "GoldAnalysisEvaluation",
    "GoldAnalysisReport",
    "GoldCandidate",
    "GoldMeasurement",
    "GoldRejection",
    "MarketDataResultDigest",
    "RejectionCode",
    "ScanOutcome",
]


class EvaluationOutcome(StrEnum):
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    EMPTY_DATA = "empty_data"
    INVALID_DATA = "invalid_data"
    PROVIDER_ERROR = "provider_error"
    UNSUPPORTED = "unsupported"


class ScanOutcome(StrEnum):
    """Batch-level summary; per-instrument outcomes remain available."""

    CANDIDATES_FOUND = "candidates_found"
    NO_CANDIDATES_PASSED = "no_candidates_passed"
    EMPTY_DATA = "empty_data"
    INVALID_DATA = "invalid_data"
    PROVIDER_ERROR = "provider_error"
    UNSUPPORTED = "unsupported"
    MIXED = "mixed"


class FilterStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"


class FilterName(StrEnum):
    EXCLUDED_SYMBOL = "excluded_symbol"
    SESSION = "session"
    SESSION_VERIFIED = "session_verified"
    DATA_AGE = "data_age"
    REQUIRED_FIELDS = "required_fields"
    PRICE_RANGE = "price_range"
    REQUIRED_MEASUREMENTS = "required_measurements"


class RejectionCode(StrEnum):
    UNSUPPORTED_INSTRUMENT = "unsupported_instrument"
    WRONG_INSTRUMENT = "wrong_instrument"
    WRONG_TIMEFRAME = "wrong_timeframe"
    SESSION_NOT_ELIGIBLE = "session_not_eligible"
    SESSION_UNVERIFIED = "session_unverified"
    SESSION_ERROR = "session_error"
    STALE_PREVIOUS_CLOSE = "stale_previous_close"
    STALE_REFERENCE_DATA = "stale_reference_data"
    DATA_TOO_OLD = "data_too_old"
    NO_PREVIOUS_CLOSE = "no_previous_close"
    NO_ELIGIBLE_REFERENCE = "no_eligible_reference"
    FUTURE_OBSERVATION = "future_observation"
    OUT_OF_ORDER_OBSERVATIONS = "out_of_order_observations"
    INVALID_PRICE = "invalid_price"
    REQUIRED_FIELD_MISSING = "required_field_missing"
    EXCLUDED_SYMBOL = "excluded_symbol"
    PRICE_BELOW_MINIMUM = "price_below_minimum"
    PRICE_ABOVE_MAXIMUM = "price_above_maximum"
    REQUIRED_MEASUREMENT_MISSING = "required_measurement_missing"


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _require_aware(value: object, field: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_bool(value: object, field: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")


def _require_finite(
    value: object,
    field: str,
    *,
    minimum: float | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{field} must be finite") from None
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field} must be at least {minimum}")


@dataclass(frozen=True, slots=True)
class MarketDataResultDigest:
    """A compact, auditable view of the Phase 2 result used by a scanner."""

    purpose: str
    provider: str
    status: MarketDataStatus
    retrieved_at: datetime
    message: str
    observation_count: int
    issue_codes: tuple[ValidationIssueCode, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.purpose, "purpose")
        _require_text(self.provider, "provider")
        if not isinstance(self.status, MarketDataStatus):
            raise ValueError("status must be a MarketDataStatus")
        _require_aware(self.retrieved_at, "retrieved_at")
        if not isinstance(self.message, str):
            raise ValueError("message must be a string")
        if (
            isinstance(self.observation_count, bool)
            or not isinstance(self.observation_count, int)
            or self.observation_count < 0
        ):
            raise ValueError("observation_count must be a non-negative integer")
        if not isinstance(self.issue_codes, tuple) or any(
            not isinstance(item, ValidationIssueCode) for item in self.issue_codes
        ):
            raise ValueError("issue_codes must contain only ValidationIssueCode values")

    @classmethod
    def from_result(
        cls,
        purpose: str,
        result: MarketDataResult,
    ) -> MarketDataResultDigest:
        if not isinstance(result, MarketDataResult):
            raise ValueError("result must be a MarketDataResult")
        return cls(
            purpose=purpose,
            provider=result.provider,
            status=result.status,
            retrieved_at=result.retrieved_at,
            message=result.message,
            observation_count=len(result.observations),
            issue_codes=tuple(issue.code for issue in result.issues),
        )


@dataclass(frozen=True, slots=True)
class FilterEvaluation:
    """One configured filter and its observed value/decision."""

    name: FilterName
    status: FilterStatus
    required: bool
    observed: float | str | None
    threshold: float | str | None
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, FilterName):
            raise ValueError("name must be a FilterName")
        if not isinstance(self.status, FilterStatus):
            raise ValueError("status must be a FilterStatus")
        _require_bool(self.required, "required")
        for field_name in ("observed", "threshold"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, (int, float, str)):
                raise ValueError(f"{field_name} must be a number, string, or None")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite")
        _require_text(self.detail, "detail")

    @property
    def blocked(self) -> bool:
        """Whether this filter prevents candidate eligibility."""

        return self.status is FilterStatus.FAILED or (
            self.required and self.status is FilterStatus.UNAVAILABLE
        )


@dataclass(frozen=True, slots=True)
class GoldMeasurement:
    """One named gold measurement or an explicit unavailable placeholder."""

    name: str
    value: float | None
    unit: str
    available: bool
    source: str
    notes: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        if self.value is not None:
            _require_finite(self.value, "value")
        _require_bool(self.available, "available")
        if self.available and self.value is None:
            raise ValueError("an available measurement must have a value")
        if not self.available and self.value is not None:
            raise ValueError("an unavailable measurement must have a None value")
        if not isinstance(self.unit, str):
            raise ValueError("unit must be a string")
        _require_text(self.source, "source")
        if not isinstance(self.notes, str):
            raise ValueError("notes must be a string")


@dataclass(frozen=True, slots=True)
class GoldCandidate:
    """A gold research candidate; not a signal, order, or trading permission."""

    instrument: MarketInstrument
    scan_session: SessionInfo
    reference_observation: MarketObservation
    previous_close_observation: MarketObservation | None
    measurements: tuple[GoldMeasurement, ...]
    filters: tuple[FilterEvaluation, ...]
    missing_information: tuple[str, ...]
    pass_rationale: str
    eligible_for_strategy_evaluation: bool
    session_verified: bool
    rank: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, MarketInstrument):
            raise ValueError("instrument must be a MarketInstrument")
        if not isinstance(self.scan_session, SessionInfo):
            raise ValueError("scan_session must be a SessionInfo")
        if self.scan_session.instrument != self.instrument:
            raise ValueError("scan_session instrument must match candidate")
        if not isinstance(self.reference_observation, MarketObservation):
            raise ValueError("reference_observation must be a MarketObservation")
        if self.reference_observation.symbol != self.instrument.symbol:
            raise ValueError("reference observation symbol does not match")
        if self.previous_close_observation is not None:
            if not isinstance(
                self.previous_close_observation, MarketObservation
            ):
                raise ValueError(
                    "previous_close_observation must be a MarketObservation or None"
                )
            if self.previous_close_observation.symbol != self.instrument.symbol:
                raise ValueError("previous-close observation symbol does not match")
        if not isinstance(self.measurements, tuple) or any(
            not isinstance(item, GoldMeasurement) for item in self.measurements
        ):
            raise ValueError("measurements must contain only GoldMeasurement values")
        names = [item.name for item in self.measurements]
        if len(names) != len(set(names)):
            raise ValueError("measurements must not repeat a measurement name")
        if not isinstance(self.filters, tuple) or any(
            not isinstance(item, FilterEvaluation) for item in self.filters
        ):
            raise ValueError("filters must contain only FilterEvaluation values")
        if any(item.blocked for item in self.filters):
            raise ValueError("a candidate cannot contain a blocking filter")
        if not isinstance(self.missing_information, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.missing_information
        ):
            raise ValueError("missing_information must contain non-empty strings")
        _require_text(self.pass_rationale, "pass_rationale")
        _require_bool(
            self.eligible_for_strategy_evaluation,
            "eligible_for_strategy_evaluation",
        )
        _require_bool(self.session_verified, "session_verified")
        if self.session_verified != self.scan_session.verified:
            raise ValueError("session_verified must match the scan session")
        if self.rank is not None:
            if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank <= 0:
                raise ValueError("rank must be a positive integer or None")

    def measurement(self, name: str) -> GoldMeasurement | None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        for item in self.measurements:
            if item.name == name:
                return item
        return None

    @property
    def research_only(self) -> bool:
        return True

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def trading_eligible(self) -> bool:
        """Phase 3 never produces a trading-eligible result."""

        return False

    @property
    def is_trade_signal(self) -> bool:
        return False

    @property
    def is_order(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class GoldRejection:
    """An explicit reason a gold instrument was not analyzed as a candidate."""

    instrument: MarketInstrument
    code: RejectionCode
    message: str
    filters: tuple[FilterEvaluation, ...] = ()
    missing_information: tuple[str, ...] = ()
    measurements: tuple[GoldMeasurement, ...] = ()
    scan_session: SessionInfo | None = None
    data_results: tuple[MarketDataResultDigest, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, MarketInstrument):
            raise ValueError("instrument must be a MarketInstrument")
        if not isinstance(self.code, RejectionCode):
            raise ValueError("code must be a RejectionCode")
        _require_text(self.message, "message")
        if not isinstance(self.filters, tuple) or any(
            not isinstance(item, FilterEvaluation) for item in self.filters
        ):
            raise ValueError("filters must contain only FilterEvaluation values")
        if not isinstance(self.missing_information, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.missing_information
        ):
            raise ValueError("missing_information must contain non-empty strings")
        if not isinstance(self.measurements, tuple) or any(
            not isinstance(item, GoldMeasurement) for item in self.measurements
        ):
            raise ValueError("measurements must contain only GoldMeasurement values")
        if self.scan_session is not None:
            if not isinstance(self.scan_session, SessionInfo):
                raise ValueError("scan_session must be a SessionInfo or None")
            if self.scan_session.instrument != self.instrument:
                raise ValueError("scan_session instrument must match rejection")
        if not isinstance(self.data_results, tuple) or any(
            not isinstance(item, MarketDataResultDigest)
            for item in self.data_results
        ):
            raise ValueError(
                "data_results must contain only MarketDataResultDigest values"
            )


@dataclass(frozen=True, slots=True)
class GoldAnalysisEvaluation:
    """The complete outcome for one gold instrument in one analysis run."""

    instrument: MarketInstrument
    outcome: EvaluationOutcome
    message: str
    data_results: tuple[MarketDataResultDigest, ...] = ()
    candidate: GoldCandidate | None = None
    rejection: GoldRejection | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, MarketInstrument):
            raise ValueError("instrument must be a MarketInstrument")
        if not isinstance(self.outcome, EvaluationOutcome):
            raise ValueError("outcome must be an EvaluationOutcome")
        _require_text(self.message, "message")
        if not isinstance(self.data_results, tuple) or any(
            not isinstance(item, MarketDataResultDigest)
            for item in self.data_results
        ):
            raise ValueError(
                "data_results must contain only MarketDataResultDigest values"
            )

        if self.outcome is EvaluationOutcome.CANDIDATE:
            if self.candidate is None or self.rejection is not None:
                raise ValueError("a candidate outcome requires only a candidate")
            if self.candidate.instrument != self.instrument:
                raise ValueError("candidate instrument must match evaluation")
        elif self.outcome is EvaluationOutcome.REJECTED:
            if self.rejection is None or self.candidate is not None:
                raise ValueError("a rejected outcome requires only a rejection")
            if self.rejection.instrument != self.instrument:
                raise ValueError("rejection instrument must match evaluation")
        elif self.candidate is not None or self.rejection is not None:
            raise ValueError(
                "data outcomes must not carry a candidate or rejection object"
            )


def derive_scan_outcome(
    evaluations: tuple[GoldAnalysisEvaluation, ...],
) -> ScanOutcome:
    """Derive a scan-level summary without hiding per-instrument outcomes."""

    if not evaluations:
        raise ValueError("a scan report requires at least one evaluation")
    if any(item.outcome is EvaluationOutcome.CANDIDATE for item in evaluations):
        return ScanOutcome.CANDIDATES_FOUND

    outcomes = {item.outcome for item in evaluations}
    if len(outcomes) > 1:
        return ScanOutcome.MIXED

    outcome = next(iter(outcomes))
    return {
        EvaluationOutcome.REJECTED: ScanOutcome.NO_CANDIDATES_PASSED,
        EvaluationOutcome.EMPTY_DATA: ScanOutcome.EMPTY_DATA,
        EvaluationOutcome.INVALID_DATA: ScanOutcome.INVALID_DATA,
        EvaluationOutcome.PROVIDER_ERROR: ScanOutcome.PROVIDER_ERROR,
        EvaluationOutcome.UNSUPPORTED: ScanOutcome.UNSUPPORTED,
    }[outcome]


@dataclass(frozen=True, slots=True)
class GoldAnalysisReport:
    """A deterministic, research-only batch result for gold analysis."""

    strategy_name: str
    strategy_version: str
    strategy_status: StrategyStatus
    evaluated_at: datetime
    evaluations: tuple[GoldAnalysisEvaluation, ...]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.strategy_name, "strategy_name")
        _require_text(self.strategy_version, "strategy_version")
        if not isinstance(self.strategy_status, StrategyStatus):
            raise ValueError("strategy_status must be a StrategyStatus")
        _require_aware(self.evaluated_at, "evaluated_at")
        if not isinstance(self.evaluations, tuple) or not self.evaluations:
            raise ValueError("evaluations must be a non-empty tuple")
        if any(
            not isinstance(item, GoldAnalysisEvaluation) for item in self.evaluations
        ):
            raise ValueError(
                "evaluations must contain only GoldAnalysisEvaluation values"
            )
        if not isinstance(self.notes, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.notes
        ):
            raise ValueError("notes must contain non-empty strings")

    @property
    def status(self) -> ScanOutcome:
        return derive_scan_outcome(self.evaluations)

    @property
    def candidates(self) -> tuple[GoldCandidate, ...]:
        return tuple(
            item.candidate
            for item in self.evaluations
            if item.candidate is not None
        )

    @property
    def rejections(self) -> tuple[GoldRejection, ...]:
        return tuple(
            item.rejection
            for item in self.evaluations
            if item.rejection is not None
        )

    @property
    def research_only(self) -> bool:
        return True

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def is_trade_signal(self) -> bool:
        return False

    @property
    def is_order(self) -> bool:
        return False

    def outcome_count(self, outcome: EvaluationOutcome) -> int:
        if not isinstance(outcome, EvaluationOutcome):
            raise ValueError("outcome must be an EvaluationOutcome")
        return sum(1 for item in self.evaluations if item.outcome is outcome)
