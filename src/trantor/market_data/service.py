"""Validation service for provider responses.

The service is the single place that turns provider output and exceptions into
an explicit :class:`MarketDataResult`.  It validates identity, ordering,
duplicates, requested intervals, future timestamps, and optional staleness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import MarketObservation
from .contracts import (
    FreshnessInfo,
    MarketDataRequest,
    MarketDataResult,
    MarketDataStatus,
    ProviderCapabilities,
    ValidationIssue,
    ValidationIssueCode,
)
from .providers import (
    InvalidProviderResponseError,
    MarketDataProvider,
    MarketDataProviderError,
    ProviderTimeoutError,
)

__all__ = ["MarketDataService"]

_CLOCK_SKEW_TOLERANCE = timedelta(minutes=1)


class MarketDataService:
    """Retrieve and validate observations from one configured provider."""

    def __init__(self, provider: MarketDataProvider) -> None:
        if provider is None:
            raise ValueError("provider must not be None")
        self._provider = provider

    def get_observations(
        self,
        request: MarketDataRequest,
        *,
        now: datetime | None = None,
        max_age: timedelta | None = None,
    ) -> MarketDataResult:
        """Retrieve one request and return an explicit outcome.

        Staleness is evaluated only when ``max_age`` is supplied.  The newest
        observation drives the decision; historical bars older than the newest
        bar are not individually marked stale.
        """

        if not isinstance(request, MarketDataRequest):
            raise TypeError("request must be a MarketDataRequest")
        if max_age is not None:
            if not isinstance(max_age, timedelta):
                raise ValueError("max_age must be a timedelta or None")
            if max_age <= timedelta(0):
                raise ValueError("max_age must be greater than zero")

        current = self._current_time(now)
        provider_name = self._provider_name()
        if provider_name is None:
            return self._failure_result(
                request,
                "unknown",
                current,
                ValidationIssueCode.PROVIDER_ERROR,
                "Provider did not report a valid non-empty source name; "
                "the provider was not queried.",
            )

        try:
            capabilities = self._provider.capabilities
        except Exception as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_ERROR,
                f"Provider {provider_name} could not report capabilities "
                f"({type(exc).__name__}): {exc}",
            )
        if not isinstance(capabilities, ProviderCapabilities):
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.INVALID_PROVIDER_CAPABILITIES,
                f"Provider {provider_name} reported invalid capabilities "
                f"(expected ProviderCapabilities, received "
                f"{type(capabilities).__name__})",
            )

        unsupported = capabilities.check(request)
        if unsupported:
            return MarketDataResult(
                request=request,
                provider=provider_name,
                status=MarketDataStatus.UNSUPPORTED,
                retrieved_at=current,
                issues=unsupported,
                message=(
                    f"Provider {provider_name} does not support this request; "
                    "the provider was not queried."
                ),
            )

        try:
            response = self._provider.fetch_observations(request)
        except TimeoutError as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_TIMEOUT,
                f"Provider {provider_name} timed out ({type(exc).__name__}): {exc}",
            )
        except ProviderTimeoutError as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_TIMEOUT,
                f"Provider {provider_name} timed out ({type(exc).__name__}): {exc}",
            )
        except InvalidProviderResponseError as exc:
            return self._invalid_response(
                request,
                provider_name,
                current,
                f"provider rejected its own response ({type(exc).__name__}): {exc}",
            )
        except MarketDataProviderError as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_ERROR,
                f"Provider {provider_name} failed ({type(exc).__name__}): {exc}",
            )
        except Exception as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_ERROR,
                f"Provider {provider_name} failed unexpectedly "
                f"({type(exc).__name__}): {exc}",
            )

        if response is None or isinstance(response, (str, bytes, bytearray)):
            return self._invalid_response(
                request,
                provider_name,
                current,
                "provider response must be a sequence of MarketObservation values",
            )

        try:
            raw_observations = tuple(response)
        except TimeoutError as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_TIMEOUT,
                f"Provider {provider_name} timed out while streaming "
                f"({type(exc).__name__}): {exc}",
            )
        except ProviderTimeoutError as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_TIMEOUT,
                f"Provider {provider_name} timed out while streaming "
                f"({type(exc).__name__}): {exc}",
            )
        except InvalidProviderResponseError as exc:
            return self._invalid_response(
                request,
                provider_name,
                current,
                f"provider rejected its own response while streaming "
                f"({type(exc).__name__}): {exc}",
            )
        except MarketDataProviderError as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_ERROR,
                f"Provider {provider_name} failed while streaming "
                f"({type(exc).__name__}): {exc}",
            )
        except TypeError as exc:
            return self._invalid_response(
                request,
                provider_name,
                current,
                f"provider response is not iterable: {exc}",
            )
        except Exception as exc:
            return self._failure_result(
                request,
                provider_name,
                current,
                ValidationIssueCode.PROVIDER_ERROR,
                f"Provider {provider_name} failed unexpectedly while streaming "
                f"({type(exc).__name__}): {exc}",
            )

        if not raw_observations:
            return MarketDataResult(
                request=request,
                provider=provider_name,
                status=MarketDataStatus.EMPTY,
                retrieved_at=current,
                message=(
                    "Provider returned no observations. This is not evidence that "
                    "no trading opportunities exist."
                ),
            )

        return self._validate_observations(
            request,
            provider_name,
            current,
            raw_observations,
            max_age=max_age,
        )

    def _provider_name(self) -> str | None:
        try:
            name = self._provider.name
        except Exception:
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        return name

    @staticmethod
    def _current_time(now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(timezone.utc)
        if not isinstance(now, datetime):
            raise ValueError("now must be a datetime or None")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return now

    def _failure_result(
        self,
        request: MarketDataRequest,
        provider: str,
        current: datetime,
        code: ValidationIssueCode,
        message: str,
    ) -> MarketDataResult:
        return MarketDataResult(
            request=request,
            provider=provider,
            status=MarketDataStatus.PROVIDER_ERROR,
            retrieved_at=current,
            issues=(ValidationIssue(code, message),),
            message=(
                f"{message}. No market-data conclusion can be drawn from this failure."
            ),
        )

    def _invalid_response(
        self,
        request: MarketDataRequest,
        provider: str,
        current: datetime,
        detail: str,
    ) -> MarketDataResult:
        return MarketDataResult(
            request=request,
            provider=provider,
            status=MarketDataStatus.INVALID,
            retrieved_at=current,
            issues=(
                ValidationIssue(
                    ValidationIssueCode.INVALID_PROVIDER_RESPONSE,
                    f"Provider {provider} returned an invalid response: {detail}",
                ),
            ),
            message=(
                "Provider response was invalid. No observations are exposed and "
                "no market conclusion can be drawn."
            ),
        )

    @staticmethod
    def _validate_observations(
        request: MarketDataRequest,
        provider: str,
        current: datetime,
        raw_observations: tuple[object, ...],
        *,
        max_age: timedelta | None,
    ) -> MarketDataResult:
        current_utc = current.astimezone(timezone.utc)
        start_utc = (
            request.start.astimezone(timezone.utc) if request.start is not None else None
        )
        end_utc = (
            request.end.astimezone(timezone.utc) if request.end is not None else None
        )

        issues: list[ValidationIssue] = []
        validated: list[MarketObservation] = []
        seen_timestamps: set[datetime] = set()
        previous_timestamp: datetime | None = None

        for index, observation in enumerate(raw_observations):
            if not isinstance(observation, MarketObservation):
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.INVALID_OBSERVATION_TYPE,
                        "observation must be a MarketObservation",
                        observation_index=index,
                    )
                )
                continue

            if observation.symbol != request.instrument.symbol:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.SYMBOL_MISMATCH,
                        f"expected symbol {request.instrument.symbol!r}, "
                        f"received {observation.symbol!r}",
                        observation_index=index,
                    )
                )
            if observation.timeframe != request.timeframe:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.TIMEFRAME_MISMATCH,
                        f"expected timeframe {request.timeframe!r}, "
                        f"received {observation.timeframe!r}",
                        observation_index=index,
                    )
                )

            timestamp = observation.observed_at.astimezone(timezone.utc)
            if timestamp > current_utc + _CLOCK_SKEW_TOLERANCE:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.FUTURE_TIMESTAMP,
                        f"observation timestamp {observation.observed_at.isoformat()} "
                        f"is in the future relative to {current.isoformat()}",
                        observation_index=index,
                    )
                )
            if start_utc is not None and (
                timestamp < start_utc or (end_utc is not None and timestamp > end_utc)
            ):
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.OUTSIDE_REQUESTED_INTERVAL,
                        "observation timestamp is outside the requested interval",
                        observation_index=index,
                    )
                )
            if timestamp in seen_timestamps:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.DUPLICATE_TIMESTAMP,
                        f"duplicate observation timestamp {observation.observed_at.isoformat()}",
                        observation_index=index,
                    )
                )
            elif previous_timestamp is not None and timestamp < previous_timestamp:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.OUT_OF_ORDER,
                        "observations must be in non-decreasing timestamp order",
                        observation_index=index,
                    )
                )

            seen_timestamps.add(timestamp)
            previous_timestamp = timestamp
            validated.append(observation)

        if issues:
            return MarketDataResult(
                request=request,
                provider=provider,
                status=MarketDataStatus.INVALID,
                retrieved_at=current,
                issues=tuple(issues),
                message=(
                    "Provider observations failed validation. No observations are "
                    "exposed and no market conclusion can be drawn."
                ),
            )

        newest = max(observation.observed_at for observation in validated).astimezone(
            timezone.utc
        )
        age = current_utc - newest
        if age < timedelta(0):
            age = timedelta(0)
        is_stale = None if max_age is None else age > max_age
        freshness = FreshnessInfo(
            evaluated_at=current,
            newest_observation_at=newest,
            age=age,
            max_age=max_age,
            is_stale=is_stale,
        )

        observations = tuple(validated)
        if is_stale:
            return MarketDataResult(
                request=request,
                provider=provider,
                status=MarketDataStatus.STALE,
                retrieved_at=current,
                observations=observations,
                issues=(
                    ValidationIssue(
                        ValidationIssueCode.STALE_DATA,
                        "newest observation is older than the allowed maximum age",
                    ),
                ),
                freshness=freshness,
                message=(
                    "Provider data is stale relative to the requested maximum age. "
                    "It must not be treated as current market evidence."
                ),
            )

        return MarketDataResult(
            request=request,
            provider=provider,
            status=MarketDataStatus.OK,
            retrieved_at=current,
            observations=observations,
            freshness=freshness,
            message=f"Retrieved {len(observations)} validated observation(s) from {provider}.",
        )
