"""Typed contracts at the broker-independent market-data boundary.

Providers supply observations.  Trantor validates them and wraps them in a
:class:`MarketDataResult` whose status makes empty, stale, invalid, unsupported,
and failed retrieval explicit.  A provider failure is never represented as an
empty successful response, and no status implies that a trading opportunity
exists or does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from ..models import AssetClass, MarketInstrument, MarketObservation

__all__ = [
    "FreshnessInfo",
    "MarketDataRequest",
    "MarketDataResult",
    "MarketDataStatus",
    "ProviderCapabilities",
    "ValidationIssue",
    "ValidationIssueCode",
]


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _require_aware(value: object, field: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class MarketDataStatus(StrEnum):
    """The explicit outcome of one market-data retrieval attempt."""

    OK = "ok"
    EMPTY = "empty"
    STALE = "stale"
    INVALID = "invalid"
    PROVIDER_ERROR = "provider_error"
    UNSUPPORTED = "unsupported"


class ValidationIssueCode(StrEnum):
    UNSUPPORTED_ASSET_CLASS = "unsupported_asset_class"
    UNSUPPORTED_SYMBOL = "unsupported_symbol"
    UNSUPPORTED_TIMEFRAME = "unsupported_timeframe"
    UNSUPPORTED_HISTORICAL = "unsupported_historical"
    SYMBOL_MISMATCH = "symbol_mismatch"
    TIMEFRAME_MISMATCH = "timeframe_mismatch"
    OUTSIDE_REQUESTED_INTERVAL = "outside_requested_interval"
    FUTURE_TIMESTAMP = "future_timestamp"
    DUPLICATE_TIMESTAMP = "duplicate_timestamp"
    OUT_OF_ORDER = "out_of_order"
    INVALID_OBSERVATION_TYPE = "invalid_observation_type"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    INVALID_PROVIDER_CAPABILITIES = "invalid_provider_capabilities"
    STALE_DATA = "stale_data"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A machine-readable reason that a response cannot be used as-is."""

    code: ValidationIssueCode
    message: str
    observation_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ValidationIssueCode):
            raise ValueError("code must be a ValidationIssueCode")
        _require_text(self.message, "message")
        if self.observation_index is not None:
            if (
                isinstance(self.observation_index, bool)
                or not isinstance(self.observation_index, int)
                or self.observation_index < 0
            ):
                raise ValueError("observation_index must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class FreshnessInfo:
    """Freshness of the newest observation, evaluated against a caller's age limit.

    ``is_stale`` is ``None`` when the caller supplied no ``max_age`` policy.
    That is deliberately not the same as asserting that the data is fresh.
    """

    evaluated_at: datetime
    newest_observation_at: datetime | None
    age: timedelta | None
    max_age: timedelta | None
    is_stale: bool | None

    def __post_init__(self) -> None:
        _require_aware(self.evaluated_at, "evaluated_at")
        if self.newest_observation_at is not None:
            _require_aware(self.newest_observation_at, "newest_observation_at")
        if self.age is not None:
            if not isinstance(self.age, timedelta):
                raise ValueError("age must be a timedelta or None")
            if self.age < timedelta(0):
                raise ValueError("age cannot be negative")
        if self.max_age is not None:
            if not isinstance(self.max_age, timedelta):
                raise ValueError("max_age must be a timedelta or None")
            if self.max_age <= timedelta(0):
                raise ValueError("max_age must be greater than zero")
        if self.is_stale is not None and not isinstance(self.is_stale, bool):
            raise ValueError("is_stale must be a boolean or None")

        if self.newest_observation_at is None:
            if self.age is not None or self.is_stale is not None:
                raise ValueError("empty freshness must have no age and no staleness decision")
            return

        if self.age is None:
            raise ValueError("freshness with an observation must include its age")
        if self.max_age is None:
            if self.is_stale is not None:
                raise ValueError("is_stale is unknown when max_age is not supplied")
            return
        if self.is_stale is None:
            raise ValueError("is_stale must be decided when max_age is supplied")
        if self.is_stale != (self.age > self.max_age):
            raise ValueError("is_stale must match age > max_age")


@dataclass(frozen=True, slots=True)
class MarketDataRequest:
    """A provider-independent request for one instrument and timeframe.

    ``start`` and ``end`` must either both be supplied for a historical
    interval or both be omitted for the provider's latest observations.
    """

    instrument: MarketInstrument
    timeframe: str
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, MarketInstrument):
            raise ValueError("instrument must be a MarketInstrument")
        _require_text(self.timeframe, "timeframe")
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end must be supplied together")
        if self.start is not None:
            _require_aware(self.start, "start")
            _require_aware(self.end, "end")
            if self.start >= self.end:
                raise ValueError("start must be before end")

    @property
    def is_historical(self) -> bool:
        return self.start is not None


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Declared capabilities. Empty ``symbols``/``timeframes`` are rejected."""

    asset_classes: frozenset[AssetClass]
    timeframes: frozenset[str]
    symbols: frozenset[str] | None = None
    supports_historical: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.asset_classes, frozenset) or not self.asset_classes:
            raise ValueError("asset_classes must be a non-empty frozenset")
        if any(not isinstance(item, AssetClass) for item in self.asset_classes):
            raise ValueError("asset_classes must contain only AssetClass values")
        if not isinstance(self.timeframes, frozenset) or not self.timeframes:
            raise ValueError("timeframes must be a non-empty frozenset")
        for timeframe in self.timeframes:
            _require_text(timeframe, "timeframe")
        if self.symbols is not None:
            if not isinstance(self.symbols, frozenset) or not self.symbols:
                raise ValueError("symbols must be a non-empty frozenset or None")
            for symbol in self.symbols:
                _require_text(symbol, "symbol")
        if not isinstance(self.supports_historical, bool):
            raise ValueError("supports_historical must be a boolean")
        if not isinstance(self.notes, tuple):
            raise ValueError("notes must be a tuple")
        for note in self.notes:
            _require_text(note, "note")

    def check(self, request: MarketDataRequest) -> tuple[ValidationIssue, ...]:
        """Return every reason the request is outside these capabilities."""

        if not isinstance(request, MarketDataRequest):
            raise ValueError("request must be a MarketDataRequest")

        issues: list[ValidationIssue] = []
        asset_class = request.instrument.asset_class
        if asset_class not in self.asset_classes:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.UNSUPPORTED_ASSET_CLASS,
                    f"asset class {asset_class.value!r} is not supported",
                )
            )
        if self.symbols is not None and request.instrument.symbol not in self.symbols:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.UNSUPPORTED_SYMBOL,
                    f"symbol {request.instrument.symbol!r} is not supported",
                )
            )
        if request.timeframe not in self.timeframes:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.UNSUPPORTED_TIMEFRAME,
                    f"timeframe {request.timeframe!r} is not supported",
                )
            )
        if request.is_historical and not self.supports_historical:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.UNSUPPORTED_HISTORICAL,
                    "historical intervals are not supported",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class MarketDataResult:
    """A retrieval outcome safe for callers to branch on.

    Only ``OK`` and ``STALE`` carry observations.  ``INVALID``,
    ``PROVIDER_ERROR``, and ``UNSUPPORTED`` deliberately carry none, so a
    downstream component cannot accidentally consume unvalidated data.
    """

    request: MarketDataRequest
    provider: str
    status: MarketDataStatus
    retrieved_at: datetime
    observations: tuple[MarketObservation, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()
    freshness: FreshnessInfo | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.request, MarketDataRequest):
            raise ValueError("request must be a MarketDataRequest")
        _require_text(self.provider, "provider")
        if not isinstance(self.status, MarketDataStatus):
            raise ValueError("status must be a MarketDataStatus")
        _require_aware(self.retrieved_at, "retrieved_at")
        _require_text(self.message, "message")
        if not isinstance(self.observations, tuple):
            raise ValueError("observations must be a tuple")
        if any(not isinstance(item, MarketObservation) for item in self.observations):
            raise ValueError("observations must contain only MarketObservation values")
        if not isinstance(self.issues, tuple):
            raise ValueError("issues must be a tuple")
        if any(not isinstance(item, ValidationIssue) for item in self.issues):
            raise ValueError("issues must contain only ValidationIssue values")

        carries_data = self.status in (MarketDataStatus.OK, MarketDataStatus.STALE)
        if carries_data and not self.observations:
            raise ValueError(f"{self.status.value} must carry observations")
        if not carries_data and self.observations:
            raise ValueError(f"{self.status.value} must not expose observations")

        if self.status in (
            MarketDataStatus.INVALID,
            MarketDataStatus.PROVIDER_ERROR,
            MarketDataStatus.UNSUPPORTED,
        ) and not self.issues:
            raise ValueError(f"{self.status.value} must include at least one issue")

        if self.status is MarketDataStatus.STALE:
            if self.freshness is None or self.freshness.is_stale is not True:
                raise ValueError("stale results require freshness with is_stale=True")
        if self.status is MarketDataStatus.OK and self.freshness is not None:
            if self.freshness.is_stale is True:
                raise ValueError("an ok result cannot carry stale freshness")

    @property
    def has_current_data(self) -> bool:
        """True only when a freshness policy was applied and passed.

        An ``ok`` status without ``max_age`` has validated observations but no
        proof that they are current, so this property remains false.
        """

        return (
            self.status is MarketDataStatus.OK
            and bool(self.observations)
            and self.freshness is not None
            and self.freshness.is_stale is False
        )
