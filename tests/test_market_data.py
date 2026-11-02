from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from trantor.market_data import (
    FreshnessInfo,
    InvalidProviderResponseError,
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataRequest,
    MarketDataResult,
    MarketDataService,
    MarketDataStatus,
    ProviderCapabilities,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ValidationIssue,
    ValidationIssueCode,
)
from trantor.models import AssetClass, MarketInstrument, MarketObservation

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 22, 14, 30, tzinfo=UTC)
INSTRUMENT = MarketInstrument(
    symbol="GC",
    asset_class=AssetClass.GOLD_FUTURES,
    exchange="COMEX",
    currency="USD",
    timezone="America/New_York",
)


def _observation(**overrides: object) -> MarketObservation:
    values: dict[str, object] = {
        "symbol": "GC",
        "timeframe": "5m",
        "observed_at": NOW - timedelta(minutes=1),
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000.0,
    }
    values.update(overrides)
    return MarketObservation(**values)  # type: ignore[arg-type]


def _request(
    *,
    instrument: MarketInstrument = INSTRUMENT,
    timeframe: str = "5m",
    start: datetime | None = None,
    end: datetime | None = None,
) -> MarketDataRequest:
    return MarketDataRequest(
        instrument=instrument,
        timeframe=timeframe,
        start=start,
        end=end,
    )


class FakeProvider:
    def __init__(
        self,
        response: object = (),
        *,
        capabilities: object | None = None,
        name: str = "fixture-provider",
    ) -> None:
        self.name = name
        self.capabilities = capabilities or ProviderCapabilities(
            asset_classes=frozenset({AssetClass.GOLD_FUTURES}),
            timeframes=frozenset({"5m"}),
            symbols=frozenset({"GC"}),
            supports_historical=True,
        )
        self.response = response
        self.calls: list[MarketDataRequest] = []

    def fetch_observations(self, request: MarketDataRequest):
        self.calls.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        if callable(self.response):
            return self.response(request)
        return self.response


def _service(provider: FakeProvider) -> MarketDataService:
    return MarketDataService(provider)


def test_fake_provider_satisfies_protocol() -> None:
    assert isinstance(FakeProvider(), MarketDataProvider)


def test_observation_accepts_optional_volume() -> None:
    without_volume = MarketObservation(
        symbol="GC",
        timeframe="5m",
        observed_at=NOW,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
    )

    assert without_volume.volume is None
    assert _observation(volume=None).volume is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"observed_at": datetime(2026, 10, 22, 14, 0)},
        {"open": math.nan},
        {"high": math.inf},
        {"low": 0.0},
        {"close": -1.0},
        {"volume": -1.0},
        {"open": True},
        {"high": 99.0},
        {"low": 100.5},
    ],
)
def test_invalid_observations_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _observation(**overrides)


def test_missing_observation_field_is_rejected() -> None:
    values = {
        "symbol": "GC",
        "timeframe": "5m",
        "observed_at": NOW,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
    }

    with pytest.raises(TypeError):
        MarketObservation(**values)  # type: ignore[arg-type]


def test_request_requires_an_aware_non_empty_interval() -> None:
    with pytest.raises(ValueError, match="start and end"):
        _request(start=NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        _request(start=datetime(2026, 10, 22, 14, 0), end=NOW)
    with pytest.raises(ValueError, match="before"):
        _request(start=NOW, end=NOW)


def test_valid_response_is_explicitly_ok() -> None:
    observation = _observation()
    provider = FakeProvider((observation,))

    result = _service(provider).get_observations(_request(), now=NOW, max_age=timedelta(minutes=5))

    assert result.status is MarketDataStatus.OK
    assert result.provider == "fixture-provider"
    assert result.request == _request()
    assert result.observations == (observation,)
    assert result.issues == ()
    assert result.freshness is not None
    assert result.freshness.is_stale is False
    assert result.freshness.age == timedelta(minutes=1)
    assert result.freshness.max_age == timedelta(minutes=5)
    assert result.freshness.newest_observation_at == NOW - timedelta(minutes=1)
    assert result.has_current_data is True
    assert len(provider.calls) == 1


def test_timezone_aware_observations_compare_across_zones() -> None:
    observation = _observation(
        observed_at=datetime(2026, 10, 22, 10, 29, tzinfo=NEW_YORK)
    )

    result = _service(FakeProvider((observation,))).get_observations(
        _request(), now=NOW, max_age=timedelta(minutes=5)
    )

    assert result.status is MarketDataStatus.OK
    assert result.freshness is not None
    assert result.freshness.age == timedelta(minutes=1)


def test_empty_response_is_a_successful_empty_result_not_a_market_finding() -> None:
    result = _service(FakeProvider(())).get_observations(
        _request(), now=NOW, max_age=timedelta(minutes=5)
    )

    assert result.status is MarketDataStatus.EMPTY
    assert result.observations == ()
    assert result.freshness is None
    assert result.has_current_data is False
    assert "not evidence" in result.message.lower()
    assert "opportunities" in result.message.lower()


def test_stale_data_is_distinct_from_empty_and_ok() -> None:
    observation = _observation(observed_at=NOW - timedelta(minutes=10))
    result = _service(FakeProvider((observation,))).get_observations(
        _request(), now=NOW, max_age=timedelta(minutes=5)
    )

    assert result.status is MarketDataStatus.STALE
    assert result.observations == (observation,)
    assert result.freshness is not None
    assert result.freshness.is_stale is True
    assert result.freshness.age == timedelta(minutes=10)
    assert result.issues[0].code is ValidationIssueCode.STALE_DATA
    assert result.has_current_data is False


def test_age_exactly_at_max_age_is_not_stale() -> None:
    observation = _observation(observed_at=NOW - timedelta(minutes=5))

    result = _service(FakeProvider((observation,))).get_observations(
        _request(), now=NOW, max_age=timedelta(minutes=5)
    )

    assert result.status is MarketDataStatus.OK
    assert result.freshness is not None
    assert result.freshness.is_stale is False


def test_freshness_is_unknown_without_a_max_age_policy() -> None:
    observation = _observation(observed_at=NOW - timedelta(hours=2))

    result = _service(FakeProvider((observation,))).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.OK
    assert result.freshness is not None
    assert result.freshness.max_age is None
    assert result.freshness.is_stale is None
    assert result.freshness.age == timedelta(hours=2)
    assert result.has_current_data is False


def test_duplicate_timestamps_invalidate_the_whole_response() -> None:
    duplicate = _observation()
    provider = FakeProvider((duplicate, _observation(close=100.0)))

    result = _service(provider).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.INVALID
    assert result.observations == ()
    assert any(
        issue.code is ValidationIssueCode.DUPLICATE_TIMESTAMP
        for issue in result.issues
    )


def test_same_instant_in_different_timezones_counts_as_duplicate() -> None:
    first = _observation(observed_at=NOW)
    second = _observation(
        observed_at=NOW.astimezone(NEW_YORK),
        close=100.5,
    )

    result = _service(FakeProvider((first, second))).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.INVALID
    assert any(
        issue.code is ValidationIssueCode.DUPLICATE_TIMESTAMP
        for issue in result.issues
    )


def test_out_of_order_observations_are_invalid_not_silently_sorted() -> None:
    later = _observation(observed_at=NOW - timedelta(minutes=1))
    earlier = _observation(observed_at=NOW - timedelta(minutes=2))

    result = _service(FakeProvider((later, earlier))).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.INVALID
    assert result.observations == ()
    assert any(issue.code is ValidationIssueCode.OUT_OF_ORDER for issue in result.issues)


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"symbol": "MGC"}, ValidationIssueCode.SYMBOL_MISMATCH),
        ({"timeframe": "1m"}, ValidationIssueCode.TIMEFRAME_MISMATCH),
    ],
)
def test_provider_identity_fields_must_match_the_request(
    overrides: dict[str, object], expected_code: ValidationIssueCode
) -> None:
    result = _service(FakeProvider((_observation(**overrides),))).get_observations(
        _request(), now=NOW
    )

    assert result.status is MarketDataStatus.INVALID
    assert any(issue.code is expected_code for issue in result.issues)


def test_malformed_provider_items_are_invalid() -> None:
    result = _service(FakeProvider(({"open": 100.0},))).get_observations(
        _request(), now=NOW
    )

    assert result.status is MarketDataStatus.INVALID
    assert result.observations == ()
    assert result.issues[0].code is ValidationIssueCode.INVALID_OBSERVATION_TYPE


@pytest.mark.parametrize("response", [None, 42, "not-a-sequence"])
def test_malformed_provider_response_shapes_are_invalid(response: object) -> None:
    result = _service(FakeProvider(response)).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.INVALID
    assert result.issues[0].code is ValidationIssueCode.INVALID_PROVIDER_RESPONSE


def test_future_observation_is_invalid() -> None:
    observation = _observation(observed_at=NOW + timedelta(minutes=2))

    result = _service(FakeProvider((observation,))).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.INVALID
    assert any(issue.code is ValidationIssueCode.FUTURE_TIMESTAMP for issue in result.issues)


def test_small_clock_skew_is_tolerated() -> None:
    observation = _observation(observed_at=NOW + timedelta(seconds=30))

    result = _service(FakeProvider((observation,))).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.OK


def test_observation_outside_requested_interval_is_invalid() -> None:
    request = _request(
        start=NOW - timedelta(minutes=1),
        end=NOW,
    )
    observation = _observation(observed_at=NOW - timedelta(minutes=5))

    result = _service(FakeProvider((observation,))).get_observations(
        request, now=NOW
    )

    assert result.status is MarketDataStatus.INVALID
    assert any(
        issue.code is ValidationIssueCode.OUTSIDE_REQUESTED_INTERVAL
        for issue in result.issues
    )


def test_provider_timeout_is_an_explicit_provider_error() -> None:
    result = _service(FakeProvider(TimeoutError("timed out"))).get_observations(
        _request(), now=NOW
    )

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.observations == ()
    assert result.issues[0].code is ValidationIssueCode.PROVIDER_TIMEOUT


def test_provider_reported_invalid_response_is_not_a_provider_failure() -> None:
    result = _service(
        FakeProvider(InvalidProviderResponseError("malformed vendor payload"))
    ).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.INVALID
    assert result.observations == ()
    assert result.issues[0].code is ValidationIssueCode.INVALID_PROVIDER_RESPONSE


def test_typed_provider_timeout_is_an_explicit_provider_error() -> None:
    result = _service(FakeProvider(ProviderTimeoutError("slow"))).get_observations(
        _request(), now=NOW
    )

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.issues[0].code is ValidationIssueCode.PROVIDER_TIMEOUT


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailableError("offline"),
        MarketDataProviderError("vendor rejected request"),
        RuntimeError("unexpected adapter bug"),
    ],
)
def test_provider_failures_are_not_empty_successes(error: Exception) -> None:
    result = _service(FakeProvider(error)).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.observations == ()
    assert result.freshness is None
    assert result.issues[0].code is ValidationIssueCode.PROVIDER_ERROR
    assert "conclusion" in result.message.lower()


def test_unsupported_timeframe_is_not_sent_to_the_provider() -> None:
    provider = FakeProvider(())

    result = _service(provider).get_observations(_request(timeframe="1m"), now=NOW)

    assert result.status is MarketDataStatus.UNSUPPORTED
    assert provider.calls == []
    assert any(
        issue.code is ValidationIssueCode.UNSUPPORTED_TIMEFRAME
        for issue in result.issues
    )


def test_unsupported_asset_class_is_explicit() -> None:
    spy = MarketInstrument("SPY", AssetClass.ETF, "NYSE Arca", "USD", "America/New_York")
    provider = FakeProvider(())

    result = _service(provider).get_observations(_request(instrument=spy), now=NOW)

    assert result.status is MarketDataStatus.UNSUPPORTED
    assert provider.calls == []
    assert any(
        issue.code is ValidationIssueCode.UNSUPPORTED_ASSET_CLASS
        for issue in result.issues
    )


def test_unsupported_symbol_is_explicit() -> None:
    msft = MarketInstrument("MGC", AssetClass.GOLD_FUTURES, "COMEX", "USD", "America/New_York")
    provider = FakeProvider(())

    result = _service(provider).get_observations(_request(instrument=msft), now=NOW)

    assert result.status is MarketDataStatus.UNSUPPORTED
    assert provider.calls == []
    assert any(
        issue.code is ValidationIssueCode.UNSUPPORTED_SYMBOL for issue in result.issues
    )


def test_historical_request_requires_declared_capability() -> None:
    capabilities = ProviderCapabilities(
        asset_classes=frozenset({AssetClass.GOLD_FUTURES}),
        timeframes=frozenset({"5m"}),
        symbols=frozenset({"GC"}),
        supports_historical=False,
    )
    provider = FakeProvider((), capabilities=capabilities)
    request = _request(start=NOW - timedelta(hours=1), end=NOW)

    result = _service(provider).get_observations(request, now=NOW)

    assert result.status is MarketDataStatus.UNSUPPORTED
    assert provider.calls == []
    assert any(
        issue.code is ValidationIssueCode.UNSUPPORTED_HISTORICAL
        for issue in result.issues
    )


def test_supported_historical_request_is_validated_normally() -> None:
    observation = _observation(observed_at=NOW - timedelta(minutes=30))
    request = _request(start=NOW - timedelta(hours=1), end=NOW)

    result = _service(FakeProvider((observation,))).get_observations(request, now=NOW)

    assert result.status is MarketDataStatus.OK
    assert result.request.is_historical is True


def test_invalid_provider_capabilities_are_a_provider_error() -> None:
    result = _service(FakeProvider((), capabilities="not-capabilities")).get_observations(
        _request(), now=NOW
    )

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.issues[0].code is ValidationIssueCode.INVALID_PROVIDER_CAPABILITIES


def test_invalid_provider_name_is_a_provider_error_and_provider_is_not_queried() -> None:
    provider = FakeProvider((), name="  ")

    result = _service(provider).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.provider == "unknown"
    assert provider.calls == []


def test_invalid_now_and_max_age_are_programming_errors() -> None:
    service = _service(FakeProvider(()))

    with pytest.raises(ValueError, match="timezone-aware"):
        service.get_observations(_request(), now=datetime(2026, 10, 22, 14, 30))
    with pytest.raises(ValueError, match="greater than zero"):
        service.get_observations(_request(), now=NOW, max_age=timedelta(0))
    with pytest.raises(TypeError, match="MarketDataRequest"):
        service.get_observations(object(), now=NOW)  # type: ignore[arg-type]


def test_result_invariants_prevent_ambiguous_data_exposure() -> None:
    request = _request()
    with pytest.raises(ValueError, match="must carry observations"):
        MarketDataResult(
            request=request,
            provider="fixture",
            status=MarketDataStatus.OK,
            retrieved_at=NOW,
            message="bad result",
        )
    with pytest.raises(ValueError, match="must not expose observations"):
        MarketDataResult(
            request=request,
            provider="fixture",
            status=MarketDataStatus.EMPTY,
            retrieved_at=NOW,
            observations=(_observation(),),
            message="bad result",
        )
    with pytest.raises(ValueError, match="at least one issue"):
        MarketDataResult(
            request=request,
            provider="fixture",
            status=MarketDataStatus.INVALID,
            retrieved_at=NOW,
            message="bad result",
        )


def test_stale_result_requires_stale_freshness() -> None:
    with pytest.raises(ValueError, match="stale results require"):
        MarketDataResult(
            request=_request(),
            provider="fixture",
            status=MarketDataStatus.STALE,
            retrieved_at=NOW,
            observations=(_observation(),),
            message="bad result",
        )


def test_freshness_rejects_inconsistent_staleness() -> None:
    with pytest.raises(ValueError, match="must match"):
        FreshnessInfo(
            evaluated_at=NOW,
            newest_observation_at=NOW - timedelta(minutes=10),
            age=timedelta(minutes=10),
            max_age=timedelta(minutes=5),
            is_stale=False,
        )


def test_validation_issue_requires_a_code_and_non_negative_index() -> None:
    with pytest.raises(ValueError, match="code"):
        ValidationIssue("not-a-code", "message")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative"):
        ValidationIssue(ValidationIssueCode.OUT_OF_ORDER, "message", -1)


def test_capabilities_reject_empty_declarations() -> None:
    with pytest.raises(ValueError, match="asset_classes"):
        ProviderCapabilities(frozenset(), frozenset({"5m"}))
    with pytest.raises(ValueError, match="timeframes"):
        ProviderCapabilities(frozenset({AssetClass.GOLD_FUTURES}), frozenset())
