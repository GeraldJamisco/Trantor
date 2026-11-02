from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from trantor.config import (
    GoldAnalysisDiscoveryConfig,
    StrategyConfig,
    load_strategy_config,
)
from trantor.market_data import (
    FreshnessInfo,
    GoldConvention,
    GoldSessionSchedule,
    MarketDataRequest,
    MarketDataResult,
    MarketDataStatus,
    RecurringLocalWindow,
    ValidationIssue,
    ValidationIssueCode,
)
from trantor.models import AssetClass, MarketInstrument, MarketObservation
from trantor.scanners import GoldAnalysisInput

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = PROJECT_ROOT / "rules" / "gold.json"
PHASE3_FIXTURES = PROJECT_ROOT / "tests" / "fixtures"

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
REG_NOW = datetime(2026, 10, 22, 14, 30, tzinfo=UTC)  # 10:30 New York
PREV_CLOSE_AT = datetime(2026, 10, 21, 16, 0, tzinfo=NEW_YORK)
REG_REFERENCE_AT = datetime(2026, 10, 22, 10, 25, tzinfo=NEW_YORK)

GC = MarketInstrument(
    "GC", AssetClass.GOLD_FUTURES, "COMEX", "USD", "America/New_York"
)
MGC = MarketInstrument(
    "MGC", AssetClass.GOLD_FUTURES, "COMEX", "USD", "America/New_York"
)


def gold_strategy(**overrides: object) -> StrategyConfig:
    base = load_strategy_config(GOLD_PATH)
    discovery = base.opportunity_discovery
    assert isinstance(discovery, GoldAnalysisDiscoveryConfig)
    return replace(
        base,
        opportunity_discovery=replace(discovery, **overrides),
    )


def bar(
    symbol: str,
    timeframe: str,
    observed_at: datetime,
    close: float,
    *,
    volume: float | None = 1000.0,
) -> MarketObservation:
    return MarketObservation(
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed_at,
        open=close,
        high=close + 1.0,
        low=max(close - 1.0, 0.01),
        close=close,
        volume=volume,
    )


_STATUS_ISSUE = {
    MarketDataStatus.INVALID: ValidationIssueCode.INVALID_PROVIDER_RESPONSE,
    MarketDataStatus.PROVIDER_ERROR: ValidationIssueCode.PROVIDER_ERROR,
    MarketDataStatus.UNSUPPORTED: ValidationIssueCode.UNSUPPORTED_SYMBOL,
}


def result(
    instrument: MarketInstrument,
    timeframe: str,
    observations: tuple[MarketObservation, ...],
    *,
    status: MarketDataStatus = MarketDataStatus.OK,
    now: datetime = REG_NOW,
    historical: bool = False,
) -> MarketDataResult:
    request = MarketDataRequest(
        instrument=instrument,
        timeframe=timeframe,
        start=datetime(2026, 10, 1, tzinfo=UTC) if historical else None,
        end=now if historical else None,
    )
    if status in (MarketDataStatus.OK, MarketDataStatus.STALE):
        if not observations:
            raise ValueError("OK/STALE fixture results require observations")
        freshness = None
        if status is MarketDataStatus.STALE:
            newest = max(item.observed_at for item in observations)
            freshness = FreshnessInfo(
                evaluated_at=now,
                newest_observation_at=newest,
                age=now - newest,
                max_age=timedelta(seconds=1),
                is_stale=True,
            )
        return MarketDataResult(
            request=request,
            provider="phase3-fixture",
            status=status,
            retrieved_at=now,
            observations=observations,
            freshness=freshness,
            message=f"fixture {status.value} result",
        )

    issues = ()
    code = _STATUS_ISSUE.get(status)
    if code is not None:
        issues = (ValidationIssue(code, f"fixture {status.value} issue"),)
    return MarketDataResult(
        request=request,
        provider="phase3-fixture",
        status=status,
        retrieved_at=now,
        issues=issues,
        message=f"fixture {status.value} result",
    )


def gold_input(
    instrument: MarketInstrument = GC,
    *,
    now: datetime = REG_NOW,
    previous_close: float = 2500.0,
    reference_close: float = 2520.0,
    reference_volume: float | None = 1000.0,
    previous_at: datetime = PREV_CLOSE_AT,
    reference_at: datetime = REG_REFERENCE_AT,
    previous_status: MarketDataStatus = MarketDataStatus.OK,
    reference_status: MarketDataStatus = MarketDataStatus.OK,
    previous_timeframe: str = "1d",
    reference_timeframe: str = "15m",
    previous_instrument: MarketInstrument | None = None,
    reference_instrument: MarketInstrument | None = None,
    previous_observations: tuple[MarketObservation, ...] | None = None,
    reference_observations: tuple[MarketObservation, ...] | None = None,
    include_previous: bool = False,
) -> GoldAnalysisInput:
    previous_instrument = previous_instrument or instrument
    reference_instrument = reference_instrument or instrument
    if previous_observations is None:
        previous_observations = (
            bar(
                previous_instrument.symbol,
                previous_timeframe,
                previous_at,
                previous_close,
            ),
        )
    if reference_observations is None:
        reference_observations = (
            bar(
                reference_instrument.symbol,
                reference_timeframe,
                reference_at,
                reference_close,
                volume=reference_volume,
            ),
        )
    previous_result = (
        result(
            previous_instrument,
            previous_timeframe,
            previous_observations,
            status=previous_status,
            now=now,
            historical=True,
        )
        if include_previous
        else None
    )
    return GoldAnalysisInput(
        instrument=instrument,
        reference_result=result(
            reference_instrument,
            reference_timeframe,
            reference_observations,
            status=reference_status,
            now=now,
        ),
        previous_close_result=previous_result,
    )


def verified_gold_schedule(
    *,
    timezone_name: str = "America/New_York",
    with_break: bool = False,
) -> GoldSessionSchedule:
    breaks = ()
    if with_break:
        breaks = tuple(
            RecurringLocalWindow(weekday, time(17, 0), 0, time(18, 0))
            for weekday in range(0, 4)
        )
    return GoldSessionSchedule(
        name="fixture_comex_futures_verified",
        convention=GoldConvention.COMEX_FUTURES,
        timezone=timezone_name,
        sessions=(
            RecurringLocalWindow(
                start_weekday=0,
                start=time(0, 0),
                end_weekday_offset=6,
                end=time(23, 59),
            ),
        ),
        breaks=breaks,
        verified=True,
        source_note="Synthetic schedule for deterministic tests only.",
    )


def unverified_gold_schedule(
    *,
    timezone_name: str = "UTC",
) -> GoldSessionSchedule:
    return GoldSessionSchedule(
        name="fixture_comex_futures_unverified",
        convention=GoldConvention.COMEX_FUTURES,
        timezone=timezone_name,
        sessions=(
            RecurringLocalWindow(
                start_weekday=0,
                start=time(0, 0),
                end_weekday_offset=6,
                end=time(23, 59),
            ),
        ),
        verified=False,
        source_note="Synthetic unverified schedule for deterministic tests only.",
        warnings=("Synthetic schedule is unverified.",),
    )
