"""Internal validation and deterministic observation-selection helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..market_data import (
    MarketDataResult,
    MarketDataStatus,
    SessionCalendar,
    SessionInfo,
    SessionState,
)
from ..models import MarketInstrument, MarketObservation
from .models import (
    EvaluationOutcome,
    MarketDataResultDigest,
    RejectionCode,
)

__all__ = [
    "ObservationSelectionError",
    "build_data_digests",
    "data_failure",
    "eligible_field_names",
    "previous_session_date",
    "result_age_seconds",
    "select_latest_eligible",
    "select_previous_close",
    "validate_result_identity",
]

_STATUS_PRIORITY = {
    MarketDataStatus.PROVIDER_ERROR: 4,
    MarketDataStatus.INVALID: 3,
    MarketDataStatus.UNSUPPORTED: 2,
    MarketDataStatus.EMPTY: 1,
}

_OUTCOME_FOR_STATUS = {
    MarketDataStatus.EMPTY: EvaluationOutcome.EMPTY_DATA,
    MarketDataStatus.INVALID: EvaluationOutcome.INVALID_DATA,
    MarketDataStatus.PROVIDER_ERROR: EvaluationOutcome.PROVIDER_ERROR,
    MarketDataStatus.UNSUPPORTED: EvaluationOutcome.UNSUPPORTED,
}


class ObservationSelectionError(ValueError):
    """A validated result cannot supply an eligible scanner observation."""

    def __init__(self, code: RejectionCode, message: str) -> None:
        if not isinstance(code, RejectionCode):
            raise ValueError("code must be a RejectionCode")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DataFailure:
    outcome: EvaluationOutcome
    message: str
    digests: tuple[MarketDataResultDigest, ...]


def build_data_digests(
    *entries: tuple[str, MarketDataResult],
) -> tuple[MarketDataResultDigest, ...]:
    return tuple(
        MarketDataResultDigest.from_result(purpose, result)
        for purpose, result in entries
    )


def data_failure(
    *entries: tuple[str, MarketDataResult],
) -> DataFailure | None:
    """Return the highest-priority non-OK/STALE data failure, if any.

    Priority is provider failure, invalid data, unsupported request, then a
    successful empty response. Keeping the priority explicit prevents a
    provider failure from being mistaken for an empty market result.
    """

    failing = [
        (purpose, result)
        for purpose, result in entries
        if result.status in _STATUS_PRIORITY
    ]
    if not failing:
        return None
    purpose, result = max(
        failing,
        key=lambda item: _STATUS_PRIORITY[item[1].status],
    )
    details = "; ".join(
        f"{name} result={item.status.value}: {item.message}"
        for name, item in failing
    )
    return DataFailure(
        outcome=_OUTCOME_FOR_STATUS[result.status],
        message=f"{purpose} data outcome is {result.status.value}. {details}",
        digests=build_data_digests(*entries),
    )


def _is_positive_finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def validate_result_identity(
    result: MarketDataResult,
    instrument: MarketInstrument,
    timeframe: str,
    *,
    evaluated_at: datetime,
) -> None:
    """Fail closed on any identity, timestamp, ordering, or price violation."""

    if result.request.instrument != instrument:
        raise ObservationSelectionError(
            RejectionCode.WRONG_INSTRUMENT,
            "market-data request instrument does not match the instrument "
            f"being evaluated ({result.request.instrument.symbol!r} vs "
            f"{instrument.symbol!r})",
        )
    if result.request.timeframe != timeframe:
        raise ObservationSelectionError(
            RejectionCode.WRONG_TIMEFRAME,
            f"market-data request timeframe is {result.request.timeframe!r}; "
            f"expected {timeframe!r}",
        )

    previous_at: datetime | None = None
    for observation in result.observations:
        if observation.symbol != instrument.symbol:
            raise ObservationSelectionError(
                RejectionCode.WRONG_INSTRUMENT,
                f"observation symbol {observation.symbol!r} does not match "
                f"{instrument.symbol!r}",
            )
        if observation.timeframe != timeframe:
            raise ObservationSelectionError(
                RejectionCode.WRONG_TIMEFRAME,
                f"observation timeframe {observation.timeframe!r} does not "
                f"match {timeframe!r}",
            )
        for field in ("open", "high", "low", "close"):
            if not _is_positive_finite(getattr(observation, field)):
                raise ObservationSelectionError(
                    RejectionCode.INVALID_PRICE,
                    f"observation {field} is not a finite positive price",
                )
        if observation.volume is not None:
            volume = observation.volume
            if isinstance(volume, bool) or not isinstance(volume, (int, float)):
                raise ObservationSelectionError(
                    RejectionCode.INVALID_PRICE,
                    "observation volume is not numeric",
                )
            numeric_volume = float(volume)
            if not math.isfinite(numeric_volume) or numeric_volume < 0:
                raise ObservationSelectionError(
                    RejectionCode.INVALID_PRICE,
                    "observation volume is not finite and non-negative",
                )

        observed_utc = observation.observed_at.astimezone(timezone.utc)
        if observed_utc > evaluated_at.astimezone(timezone.utc):
            raise ObservationSelectionError(
                RejectionCode.FUTURE_OBSERVATION,
                "observation timestamp is after the scan evaluation time",
            )
        if previous_at is not None and observed_utc <= previous_at:
            raise ObservationSelectionError(
                RejectionCode.OUT_OF_ORDER_OBSERVATIONS,
                "observations are duplicated or out of timestamp order",
            )
        previous_at = observed_utc


def _session_at(
    calendar: SessionCalendar,
    instrument: MarketInstrument,
    at: datetime,
) -> SessionInfo:
    try:
        session = calendar.status_at(instrument, at)
    except Exception as exc:
        raise ObservationSelectionError(
            RejectionCode.SESSION_ERROR,
            f"session calendar could not classify {at.isoformat()} "
            f"({type(exc).__name__}): {exc}",
        ) from exc
    if not isinstance(session, SessionInfo):
        raise ObservationSelectionError(
            RejectionCode.SESSION_ERROR,
            "session calendar did not return a SessionInfo",
        )
    if session.instrument != instrument:
        raise ObservationSelectionError(
            RejectionCode.WRONG_INSTRUMENT,
            "session calendar returned a different instrument",
        )
    return session


def select_latest_eligible(
    result: MarketDataResult,
    calendar: SessionCalendar,
    instrument: MarketInstrument,
    *,
    evaluated_at: datetime,
    states: frozenset[SessionState],
    session_date: date | None,
    max_age_seconds: int | None,
) -> tuple[MarketObservation, SessionInfo, float]:
    """Select the newest observation that passes session and age rules.

    "Newest" is applied only after filtering ineligible sessions, dates, and
    timestamps. It is never assumed that the result's final row is usable.
    """

    if not states:
        raise ValueError("states must not be empty")
    if max_age_seconds is not None and max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be greater than zero or None")

    eligible: list[tuple[MarketObservation, SessionInfo]] = []
    for observation in result.observations:
        try:
            session = _session_at(calendar, instrument, observation.observed_at)
        except ObservationSelectionError:
            raise
        if session.state not in states:
            continue
        if session_date is not None and session.session_date != session_date:
            continue
        eligible.append((observation, session))

    if not eligible:
        expected = ", ".join(sorted(item.value for item in states))
        raise ObservationSelectionError(
            RejectionCode.NO_ELIGIBLE_REFERENCE,
            "no observation belongs to an eligible reference session "
            f"({expected}"
            + (f" on {session_date.isoformat()}" if session_date else "")
            + ")",
        )

    observation, session = max(eligible, key=lambda item: item[0].observed_at)
    age = result_age_seconds(observation, evaluated_at)
    if max_age_seconds is not None and age > max_age_seconds:
        raise ObservationSelectionError(
            RejectionCode.DATA_TOO_OLD,
            f"reference observation age {age:.1f}s exceeds configured maximum "
            f"{max_age_seconds}s",
        )
    return observation, session, age


def result_age_seconds(
    observation: MarketObservation,
    evaluated_at: datetime,
) -> float:
    age = (
        evaluated_at.astimezone(timezone.utc)
        - observation.observed_at.astimezone(timezone.utc)
    ).total_seconds()
    if age < 0:
        raise ObservationSelectionError(
            RejectionCode.FUTURE_OBSERVATION,
            "reference observation is in the future",
        )
    return float(age)


def previous_session_date(
    calendar: SessionCalendar,
    instrument: MarketInstrument,
    scan_session: SessionInfo,
    *,
    required_state: SessionState,
    probe_times: tuple[time, ...] = (time(12, 0),),
    max_lookback_days: int = 14,
) -> date:
    """Find the most recent prior date with a session in ``required_state``."""

    if not required_state:
        raise ValueError("required_state is required")
    if not probe_times:
        raise ValueError("probe_times must not be empty")
    if max_lookback_days <= 0:
        raise ValueError("max_lookback_days must be greater than zero")

    try:
        local_tz = ZoneInfo(scan_session.timezone)
    except Exception as exc:  # pragma: no cover - SessionInfo already validates tz
        raise ObservationSelectionError(
            RejectionCode.SESSION_ERROR,
            f"invalid session timezone {scan_session.timezone!r}: {exc}",
        ) from exc

    for offset in range(1, max_lookback_days + 1):
        candidate_date = scan_session.session_date - timedelta(days=offset)
        for probe in probe_times:
            at = datetime.combine(candidate_date, probe, tzinfo=local_tz)
            session = _session_at(calendar, instrument, at)
            if (
                session.state is required_state
                and session.session_date == candidate_date
            ):
                return candidate_date

    raise ObservationSelectionError(
        RejectionCode.NO_PREVIOUS_CLOSE,
        "no prior session with state "
        f"{required_state.value!r} was found within {max_lookback_days} days",
    )


def select_previous_close(
    result: MarketDataResult,
    calendar: SessionCalendar,
    instrument: MarketInstrument,
    scan_session: SessionInfo,
    *,
    timeframe: str,
    is_daily_bar: bool,
    required_state: SessionState,
    probe_times: tuple[time, ...],
) -> tuple[MarketObservation, date]:
    """Select the latest bar for the immediately preceding eligible session."""

    expected_date = previous_session_date(
        calendar,
        instrument,
        scan_session,
        required_state=required_state,
        probe_times=probe_times,
    )
    try:
        local_tz = ZoneInfo(scan_session.timezone)
    except Exception as exc:  # pragma: no cover - SessionInfo already validates tz
        raise ObservationSelectionError(
            RejectionCode.SESSION_ERROR,
            f"invalid session timezone {scan_session.timezone!r}: {exc}",
        ) from exc

    candidates: list[MarketObservation] = []
    for observation in result.observations:
        if observation.timeframe != timeframe:
            continue
        local_date = observation.observed_at.astimezone(local_tz).date()
        if local_date != expected_date:
            continue
        if not is_daily_bar:
            session = _session_at(calendar, instrument, observation.observed_at)
            if (
                session.state is not required_state
                or session.session_date != expected_date
            ):
                continue
        candidates.append(observation)

    if not candidates:
        raise ObservationSelectionError(
            RejectionCode.NO_PREVIOUS_CLOSE,
            "no previous-close observation exists for the expected prior "
            f"session {expected_date.isoformat()}",
        )
    return max(candidates, key=lambda item: item.observed_at), expected_date


def eligible_field_names(
    observation: MarketObservation,
    required_fields: tuple[str, ...],
) -> tuple[str, ...]:
    missing: list[str] = []
    for field in required_fields:
        value = getattr(observation, field, None)
        if value is None:
            missing.append(field)
    return tuple(missing)
