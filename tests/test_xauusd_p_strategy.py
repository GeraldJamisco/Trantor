"""Deterministic Phase D tests for the approved XAUUSD-P research strategy."""

from __future__ import annotations

import inspect
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from trantor.models import MarketObservation, Side, TradeSignal
import trantor.strategy.xauusd_p as xauusd_module
from trantor.strategy.xauusd_p import (
    BiasState,
    Confidence,
    ConfluenceScore,
    DailyBias,
    DecisionStatus,
    H4Trend,
    NewsContext,
    NewsEvaluation,
    NewsEvent,
    Pivot,
    PivotKind,
    RejectionCode,
    ResearchPositionState,
    ResearchRiskState,
    RiskDecision,
    ScoreFactor,
    SessionEvaluation,
    SessionFilterMode,
    StrategyIdentity,
    TradePlan,
    TradeStatus,
    TrendState,
    XAUUSDPMTFParameters,
    XAUUSDPMultiTimeframeStrategy,
    XAUUSDPSignalCandidate,
    Zone,
    ZoneCluster,
    ZoneKind,
    _atr_series,
    _build_fib_zone,
    _build_sr_zones,
    _contiguous_m15,
    _ema_series,
    _h4_trend,
    _news_evaluation,
    _pattern_flags,
    _pivots,
    _risk_decision,
    _rsi_series,
    _score,
    _select_zone,
    _session_overlap,
    _volume_confirmed,
    _zone_clusters,
)
from trantor.strategy.xauusd_p_research import XAUUSDPMTFResearchVariant
from trantor.strategy.research_record import (
    DATASET_TIME_SEMANTICS,
    RESEARCH_RECORD_SCHEMA,
    RESEARCH_RECORD_SCHEMA_VERSION,
    ResearchRecordIdentityError,
    ResearchRecordSchemaError,
    canonical_json,
    parse_research_record,
    serialize_strategy_decision,
    validate_research_record,
)

UTC = timezone.utc
SYMBOL = "XAUUSD-P"
DECISION_TIME = datetime(2026, 10, 22, 12, 15, tzinfo=UTC)


def _bar(
    timeframe: str,
    at: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None = 100.0,
) -> MarketObservation:
    return MarketObservation(
        symbol=SYMBOL,
        timeframe=timeframe,
        observed_at=at,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _d1_series(*, bearish: bool = False, constant: bool = False) -> list[MarketObservation]:
    start = datetime(2026, 10, 21, tzinfo=UTC) - timedelta(days=209)
    price = 5000.0 if bearish else 3000.0
    result: list[MarketObservation] = []
    for index in range(210):
        at = start + timedelta(days=index)
        open_ = price
        if constant:
            close = 3000.0
        else:
            close = open_ - 4.0 if bearish else open_ + 4.0
        result.append(
            _bar("1d", at, open_, max(open_, close) + 1.0, min(open_, close) - 1.0, close)
        )
        price = close
    return result


def _h4_series(*, bearish: bool = False) -> list[MarketObservation]:
    """A rising or falling zig-zag with strict 2/2 turning points.

    The final segment deliberately leaves a confirmed support pivot and a
    distant opposing high so the fixture can exercise the stop/target rules.
    """

    values: list[float] = []
    for cycle in range(30):
        low = 4000.0 + cycle * 1000.0
        high = low + 500.0
        for step in range(4):
            values.append(low + (high - low) * step / 3.0)
        for step in range(1, 5):
            values.append(high - (high - (low + 5.0)) * step / 4.0)
    low = 4000.0 + 30 * 1000.0
    high = low + 500.0
    values.extend([low + 100.0, low + 200.0, high, low, low + 100.0, low + 200.0])
    values = values[-200:]
    if bearish:
        values = list(reversed(values))
    start = datetime(2026, 10, 22, 8, tzinfo=UTC) - timedelta(hours=4 * (len(values) - 1))
    result: list[MarketObservation] = []
    previous = values[0]
    for index, close in enumerate(values):
        at = start + timedelta(hours=4 * index)
        open_ = previous
        high_value = max(open_, close)
        low_value = min(open_, close)
        if 0 < index < len(values) - 1 and close > values[index - 1] and close > values[index + 1]:
            high_value += 5.0
        if 0 < index < len(values) - 1 and close < values[index - 1] and close < values[index + 1]:
            low_value -= 5.0
        result.append(_bar("4h", at, open_, high_value, low_value, close))
        previous = close
    return result


def _latest_support(h4: list[MarketObservation]) -> float:
    lows = [pivot.price for pivot in _pivots(h4, 2, 2) if pivot.kind is PivotKind.LOW]
    assert lows
    return lows[-1]


def _m15_series(
    zone_center: float,
    *,
    trigger_at: datetime = datetime(2026, 10, 22, 12, tzinfo=UTC),
) -> list[MarketObservation]:
    start = trigger_at - timedelta(minutes=15 * 24)
    result: list[MarketObservation] = []
    price = zone_center + 55.0
    for index in range(25):
        at = start + timedelta(minutes=15 * index)
        if index == 24:
            open_ = zone_center - 20.0
            close = zone_center - 15.0
            low = zone_center - 22.0
            high = zone_center - 10.0
        else:
            open_ = price
            close = open_ - 2.5
            low = min(open_, close) - 1.0
            high = max(open_, close) + 1.0
        result.append(
            _bar(
                "15m",
                at,
                open_,
                high,
                low,
                close,
                100.0 if index < 24 else 400.0,
            )
        )
        price = close
    return result


def _next_bar(
    zone_center: float,
    *,
    at: datetime = DECISION_TIME,
) -> MarketObservation:
    return _bar(
        "15m",
        at,
        zone_center - 12.0,
        zone_center - 9.0,
        zone_center - 14.0,
        zone_center - 10.0,
    )


def _fixture() -> tuple[
    list[MarketObservation],
    list[MarketObservation],
    list[MarketObservation],
    MarketObservation,
]:
    d1 = _d1_series()
    h4 = _h4_series()
    center = _latest_support(h4)
    return d1, h4, _m15_series(center), _next_bar(center)


def _risk(**overrides: object) -> ResearchRiskState:
    values: dict[str, object] = {
        "equity": 100_000.0,
        "start_of_day_equity": 100_000.0,
    }
    values.update(overrides)
    return ResearchRiskState(**values)  # type: ignore[arg-type]


def _evaluate(
    *,
    d1: list[MarketObservation] | None = None,
    h4: list[MarketObservation] | None = None,
    m15: list[MarketObservation] | None = None,
    next_bar: MarketObservation | None = None,
    risk: ResearchRiskState | None = None,
    news: NewsContext | None = None,
    research_mode: bool = True,
    news_unaware: bool = True,
    session_verified: bool = False,
    parameters: XAUUSDPMTFParameters | None = None,
) -> object:
    default_d1, default_h4, default_m15, default_next = _fixture()
    strategy = XAUUSDPMultiTimeframeStrategy(parameters)
    return strategy.evaluate(
        default_d1 if d1 is None else d1,
        default_h4 if h4 is None else h4,
        default_m15 if m15 is None else m15,
        decision_time=DECISION_TIME,
        next_m15_bar=default_next if next_bar is None else next_bar,
        risk_state=_risk() if risk is None else risk,
        news_context=news,
        research_mode=research_mode,
        news_unaware=news_unaware,
        session_calendar_verified=session_verified,
    )


def _daily_bias(state: BiasState = BiasState.BULLISH) -> DailyBias:
    return DailyBias(
        state=state,
        ema_fast=101.0,
        ema_slow=100.0,
        tolerance=1.0,
        latest_bar_at=DECISION_TIME - timedelta(days=1),
        previous_high=105.0,
        previous_low=95.0,
        bar_count=201,
    )


def _cluster(
    lower: float = 100.0,
    upper: float = 110.0,
    center: float | None = None,
    *,
    kind: ZoneKind = ZoneKind.SUPPORT,
    direction: Side | None = None,
) -> ZoneCluster:
    zone = Zone(
        kind=kind,
        lower=lower,
        upper=upper,
        center=(lower + upper) / 2.0,
        confirmed_at=DECISION_TIME - timedelta(hours=4),
        confirmed_index=10,
        evidence="test zone",
        direction=direction,
    )
    return ZoneCluster(
        zones=(zone,),
        lower=lower,
        upper=upper,
        center=(lower + upper) / 2.0 if center is None else center,
        evidence=("test zone",),
    )


def _news(
    *,
    event_type: str = "CPI",
    currency: str = "USD",
    impact: str = "high",
    at: datetime = DECISION_TIME + timedelta(minutes=45),
    as_of: datetime | None = None,
) -> NewsContext:
    return NewsContext(
        verified=True,
        complete=True,
        as_of=DECISION_TIME - timedelta(minutes=5) if as_of is None else as_of,
        events=(NewsEvent(event_type, currency, impact, at),),
        source="test",
    )


def _h4_trend_for_plan(
    pivots: tuple[Pivot, ...],
    *,
    atr: float = 2.0,
    state: TrendState = TrendState.BULLISH,
) -> H4Trend:
    return H4Trend(
        state=state,
        atr=atr,
        pivots=pivots,
        ph1=110.0,
        ph2=105.0,
        pl1=95.0,
        pl2=90.0,
        latest_bar_at=DECISION_TIME,
        bar_count=30,
    )


def _pivot(index: int, kind: PivotKind, price: float) -> Pivot:
    return Pivot(
        index=index,
        kind=kind,
        price=price,
        confirmed_at=DECISION_TIME - timedelta(hours=4),
        confirmed_index=index + 2,
    )


# ---------------------------------------------------------------------------
# Data and timing
# ---------------------------------------------------------------------------


def test_candidate_fixture_is_deterministic_and_uses_existing_signal() -> None:
    first = _evaluate()
    second = _evaluate()
    assert first == second
    assert first.status is DecisionStatus.CANDIDATE
    assert first.candidate is not None
    candidate = first.candidate
    assert candidate.side is Side.BUY
    assert candidate.signal_timestamp == DECISION_TIME
    assert candidate.entry_time == DECISION_TIME
    assert candidate.entry_reference == pytest.approx(candidate.plan.entry)
    assert candidate.plan.stop < candidate.entry_reference
    assert candidate.entry_reference < candidate.plan.tp1 < candidate.plan.tp2
    assert candidate.plan.tp1_fraction == 0.5
    assert candidate.plan.tp2_fraction == 0.5
    assert candidate.plan.breakeven == candidate.entry_reference
    assert candidate.risk.risk_fraction == pytest.approx(0.005)
    assert candidate.execution_ready is False
    signal = first.to_trade_signal()
    assert isinstance(signal, TradeSignal)
    assert signal.symbol == SYMBOL
    assert signal.side is Side.BUY
    assert signal.generated_at == DECISION_TIME
    assert signal.reference_price == candidate.entry_reference
    assert signal.stop_loss == candidate.plan.stop
    assert signal.take_profit == candidate.plan.tp1
    assert not hasattr(signal, "quantity")


def test_missing_timeframes_are_rejected_explicitly() -> None:
    assert _evaluate(d1=[]).rejections[0].code is RejectionCode.D1_UNAVAILABLE
    assert _evaluate(h4=[]).rejections[0].code is RejectionCode.H4_UNAVAILABLE
    assert _evaluate(m15=[]).rejections[0].code is RejectionCode.M15_TRIGGER_MISSING


def test_stale_d1_and_h4_are_rejected() -> None:
    d1, h4, _, _ = _fixture()
    stale_d1 = [
        replace(item, observed_at=item.observed_at - timedelta(days=7)) for item in d1
    ]
    assert _evaluate(d1=stale_d1).rejections[0].code is RejectionCode.D1_STALE
    shifted = [
        replace(item, observed_at=item.observed_at - timedelta(days=4)) for item in h4
    ]
    decision = _evaluate(h4=shifted)
    assert decision.rejections[0].code is RejectionCode.H4_STALE


def test_invalid_unordered_and_duplicate_series_are_rejected() -> None:
    d1, h4, m15, _ = _fixture()
    unordered = list(m15)
    unordered[0], unordered[1] = unordered[1], unordered[0]
    assert _evaluate(m15=unordered).rejections[0].code is RejectionCode.INVALID_DATA
    duplicate = list(m15)
    duplicate[1] = replace(duplicate[1], observed_at=duplicate[0].observed_at)
    assert _evaluate(m15=duplicate).rejections[0].code is RejectionCode.INVALID_DATA
    with pytest.raises(ValueError):
        MarketObservation(SYMBOL, "15m", DECISION_TIME, 100.0, 90.0, 95.0, 99.0)


def test_incomplete_candle_is_not_used_and_missing_trigger_is_rejected() -> None:
    d1, h4, m15, next_bar = _fixture()
    future = _bar("15m", DECISION_TIME + timedelta(minutes=15), 10.0, 11.0, 9.0, 10.5)
    assert _evaluate(d1=d1, h4=h4, m15=[*m15, future], next_bar=next_bar).status is DecisionStatus.CANDIDATE
    assert _evaluate(m15=m15[:-1]).rejections[0].code is RejectionCode.M15_TRIGGER_MISSING


def test_no_lookahead_from_future_bars_or_next_bar_ohlc() -> None:
    d1, h4, m15, next_bar = _fixture()
    baseline = _evaluate(d1=d1, h4=h4, m15=m15, next_bar=next_bar)
    future_h4 = _bar(
        "4h",
        DECISION_TIME,
        60_000.0,
        70_000.0,
        50_000.0,
        65_000.0,
    )
    future_m15 = _bar(
        "15m",
        DECISION_TIME + timedelta(minutes=15),
        60_000.0,
        70_000.0,
        50_000.0,
        65_000.0,
    )
    altered_next = replace(next_bar, high=next_bar.open + 1000.0, close=next_bar.open + 500.0)
    changed = _evaluate(
        d1=d1,
        h4=[*h4, future_h4],
        m15=[*m15, future_m15],
        next_bar=altered_next,
    )
    assert baseline.status is DecisionStatus.CANDIDATE
    assert changed.status is DecisionStatus.CANDIDATE
    assert changed.candidate == baseline.candidate


def test_indicator_warmup_and_contiguity() -> None:
    assert _evaluate(d1=_d1_series()[:200]).rejections[0].code is RejectionCode.D1_WARMUP
    d1, h4, m15, _ = _fixture()
    assert _evaluate(h4=h4[-10:]).rejections[0].code is RejectionCode.H4_WARMUP
    gap = [*m15[:10], *m15[11:]]
    assert _evaluate(m15=gap).rejections[0].code is RejectionCode.M15_GAP
    assert _contiguous_m15(m15, XAUUSDPMTFParameters()) is True
    assert _contiguous_m15(gap, XAUUSDPMTFParameters()) is False


def test_decision_time_and_next_bar_alignment() -> None:
    d1, h4, m15, next_bar = _fixture()
    strategy = XAUUSDPMultiTimeframeStrategy()
    wrong_next = replace(next_bar, observed_at=DECISION_TIME + timedelta(minutes=15))
    decision = strategy.evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=wrong_next,
        risk_state=_risk(),
        news_context=NewsContext.unavailable(),
        research_mode=True,
        news_unaware=True,
    )
    assert decision.rejections[0].code is RejectionCode.NEXT_BAR_MISSING
    no_next = strategy.evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=None,
        risk_state=_risk(),
        news_context=NewsContext.unavailable(),
        research_mode=True,
        news_unaware=True,
    )
    assert no_next.rejections[0].code is RejectionCode.NEXT_BAR_MISSING


def test_future_aligned_h4_pivot_is_confirmed_only_after_two_right_bars() -> None:
    start = datetime(2026, 10, 22, 0, tzinfo=UTC)
    highs = (10.0, 11.0, 20.0, 11.0, 10.0, 9.0)
    bars = [
        _bar(
            "4h",
            start + timedelta(hours=4 * index),
            9.0,
            high,
            8.0,
            9.0,
        )
        for index, high in enumerate(highs)
    ]
    assert _pivots(bars[:4], 2, 2) == ()
    pivots = _pivots(bars[:5], 2, 2)
    pivot = next(
        item for item in pivots if item.kind is PivotKind.HIGH and item.index == 2
    )
    assert pivot.confirmed_at == bars[4].observed_at + timedelta(hours=4)


def test_aligned_future_h4_pivot_does_not_change_earlier_decision() -> None:
    d1, h4, m15, next_bar = _fixture()
    baseline = _evaluate(d1=d1, h4=h4, m15=m15, next_bar=next_bar)
    future = (
        _bar("4h", datetime(2026, 10, 22, 12, tzinfo=UTC), 30_000.0, 30_100.0, 29_900.0, 30_000.0),
        _bar("4h", datetime(2026, 10, 22, 16, tzinfo=UTC), 30_000.0, 50_000.0, 29_900.0, 30_000.0),
        _bar("4h", datetime(2026, 10, 22, 20, tzinfo=UTC), 30_000.0, 30_100.0, 29_900.0, 30_000.0),
        _bar("4h", datetime(2026, 10, 23, 0, tzinfo=UTC), 30_000.0, 30_000.0, 29_900.0, 30_000.0),
    )
    changed = _evaluate(d1=d1, h4=[*h4, *future], m15=m15, next_bar=next_bar)
    assert baseline.status is DecisionStatus.CANDIDATE
    assert changed.status is DecisionStatus.CANDIDATE
    assert changed.candidate == baseline.candidate
    assert changed.score == baseline.score


def test_pivot_is_available_only_after_two_right_bars() -> None:
    bars = [
        _bar("4h", datetime(2026, 1, 1, 0, tzinfo=UTC) + timedelta(hours=4 * i), 9.0, h, 8.0, 9.0)
        for i, h in enumerate([10.0, 11.0, 20.0, 11.0, 10.0, 9.0])
    ]
    assert _pivots(bars[:4], 2, 2) == ()
    pivots = _pivots(bars[:5], 2, 2)
    assert any(pivot.kind is PivotKind.HIGH and pivot.index == 2 for pivot in pivots)
    assert [pivot for pivot in pivots if pivot.kind is PivotKind.HIGH][0].confirmed_at == bars[4].observed_at + timedelta(hours=4)


# ---------------------------------------------------------------------------
# Indicators and structure
# ---------------------------------------------------------------------------


def test_ema_initialization_and_daily_bias_states() -> None:
    assert _ema_series([1.0, 2.0, 3.0], 2) == (None, 1.5, 2.5)
    strategy = XAUUSDPMultiTimeframeStrategy()
    assert strategy._daily_bias(_d1_series()).state is BiasState.BULLISH
    assert strategy._daily_bias(_d1_series(bearish=True)).state is BiasState.BEARISH
    assert strategy._daily_bias(_d1_series(constant=True)).state is BiasState.NEUTRAL


def test_h4_trend_bullish_bearish_and_unavailable() -> None:
    parameters = XAUUSDPMTFParameters()
    bullish = _h4_trend(_h4_series(), parameters)
    assert bullish.state is TrendState.BULLISH
    bearish = _h4_trend(_h4_series(bearish=True), parameters)
    assert bearish.state is TrendState.BEARISH
    assert _h4_trend(_h4_series()[:10], parameters).state is TrendState.UNAVAILABLE


def test_atr_and_rsi_warmup_and_extremes() -> None:
    d1, h4, m15, _ = _fixture()
    atr_values = _atr_series(h4, 14)
    assert atr_values[12] is None
    assert atr_values[13] is not None
    rising = _rsi_series([float(value) for value in range(1, 40)], 14)
    falling = _rsi_series([float(value) for value in range(40, 1, -1)], 14)
    assert rising[-1] == 100.0
    assert falling[-1] == 0.0
    assert _rsi_series([1.0] * 10, 14)[-1] is None


def test_sr_zones_and_zone_cluster_merging() -> None:
    _, h4, _, _ = _fixture()
    parameters = XAUUSDPMTFParameters()
    trend = _h4_trend(h4, parameters)
    zones = _build_sr_zones(h4, trend, parameters)
    assert any(zone.kind is ZoneKind.SUPPORT for zone in zones)
    assert any(abs(zone.center - 33995.0) < 10.0 for zone in zones)
    first = Zone(
        ZoneKind.SUPPORT,
        95.0,
        105.0,
        100.0,
        DECISION_TIME,
        10,
        "first",
    )
    second = Zone(
        ZoneKind.SUPPORT,
        100.0,
        110.0,
        105.0,
        DECISION_TIME,
        11,
        "second",
    )
    clusters = _zone_clusters((first, second))
    assert len(clusters) == 1
    assert clusters[0].lower == 95.0
    assert clusters[0].upper == 110.0


def test_sr_merged_boundaries_use_center_plus_minus_half_width() -> None:
    parameters = XAUUSDPMTFParameters()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    observations = [
        _bar("4h", start + timedelta(hours=4 * i), 100.0, 101.0, 99.0, 100.0)
        for i in range(10)
    ]
    trend = _h4_trend_for_plan(
        (
            _pivot(2, PivotKind.LOW, 100.0),
            _pivot(4, PivotKind.LOW, 101.0),
        ),
        atr=10.0,
    )
    zones = _build_sr_zones(observations, trend, parameters)
    assert len(zones) == 1
    assert zones[0].lower == pytest.approx(98.0)
    assert zones[0].upper == pytest.approx(103.0)
    assert zones[0].center == pytest.approx(100.5)


def test_sr_zone_invalidation_and_expiry() -> None:
    parameters = XAUUSDPMTFParameters()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    observations = [
        _bar("4h", start + timedelta(hours=4 * i), 100.0, 101.0, 99.0, 100.0)
        for i in range(10)
    ]
    observations[-1] = replace(observations[-1], open=80.0, high=81.0, low=79.0, close=80.0)
    pivot = _pivot(2, PivotKind.LOW, 100.0)
    trend = _h4_trend_for_plan((pivot,), atr=10.0)
    assert _build_sr_zones(observations, trend, parameters) == ()
    many = [
        _bar("4h", start + timedelta(hours=4 * i), 100.0, 101.0, 99.0, 100.0)
        for i in range(130)
    ]
    old_pivot = replace(pivot, index=0, confirmed_index=0)
    stale_trend = _h4_trend_for_plan((old_pivot,), atr=10.0)
    assert _build_sr_zones(many, stale_trend, parameters) == ()


def test_fibonacci_zone_calculation_and_expiry() -> None:
    parameters = XAUUSDPMTFParameters()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    observations = [
        _bar("4h", start + timedelta(hours=4 * i), 110.0, 111.0, 109.0, 110.0)
        for i in range(8)
    ]
    trend = _h4_trend_for_plan(
        (
            _pivot(2, PivotKind.LOW, 100.0),
            _pivot(5, PivotKind.HIGH, 120.0),
        ),
        atr=10.0,
    )
    zone = _build_fib_zone(observations, trend, parameters)
    assert zone is not None
    assert zone.direction is Side.BUY
    assert zone.lower == pytest.approx(105.14, abs=0.01)
    assert zone.upper == pytest.approx(112.5, abs=0.01)
    old = [
        _bar("4h", start + timedelta(hours=4 * i), 110.0, 111.0, 109.0, 110.0)
        for i in range(130)
    ]
    stale = _h4_trend_for_plan(
        (
            replace(_pivot(2, PivotKind.LOW, 100.0), index=0, confirmed_index=0),
            replace(_pivot(5, PivotKind.HIGH, 120.0), index=5, confirmed_index=5),
        ),
        atr=10.0,
    )
    assert _build_fib_zone(old, stale, parameters) is None


def test_zone_selection_uses_nearest_intersecting_cluster() -> None:
    trigger = _bar("15m", DECISION_TIME, 103.0, 104.0, 99.0, 101.0)
    near = _cluster(99.0, 103.0, center=101.0)
    far = _cluster(100.0, 110.0, center=105.0)
    selected = _select_zone((far, near), trigger, Side.BUY)
    assert selected is near
    outside = _bar("15m", DECISION_TIME, 130.0, 131.0, 129.0, 130.0)
    assert _select_zone((near, far), outside, Side.BUY) is None


def test_directional_zone_eligibility() -> None:
    trigger = _bar("15m", DECISION_TIME, 103.0, 104.0, 99.0, 101.0)
    support = _cluster(99.0, 103.0, center=101.0, kind=ZoneKind.SUPPORT)
    resistance = _cluster(99.0, 103.0, center=101.0, kind=ZoneKind.RESISTANCE)
    bullish_fib = _cluster(
        99.0,
        103.0,
        center=101.0,
        kind=ZoneKind.FIBONACCI,
        direction=Side.BUY,
    )
    bearish_fib = _cluster(
        99.0,
        103.0,
        center=101.0,
        kind=ZoneKind.FIBONACCI,
        direction=Side.SELL,
    )
    assert _select_zone((support,), trigger, Side.BUY) is support
    assert _select_zone((support,), trigger, Side.SELL) is None
    assert _select_zone((resistance,), trigger, Side.BUY) is None
    assert _select_zone((resistance,), trigger, Side.SELL) is resistance
    assert _select_zone((bullish_fib,), trigger, Side.BUY) is bullish_fib
    assert _select_zone((bullish_fib,), trigger, Side.SELL) is None
    assert _select_zone((bearish_fib,), trigger, Side.SELL) is bearish_fib
    assert _select_zone((bearish_fib,), trigger, Side.BUY) is None
    support_zone = Zone(
        ZoneKind.SUPPORT,
        90.0,
        100.0,
        95.0,
        DECISION_TIME,
        10,
        "support",
    )
    resistance_zone = Zone(
        ZoneKind.RESISTANCE,
        101.0,
        105.0,
        103.0,
        DECISION_TIME,
        10,
        "resistance",
    )
    mixed = ZoneCluster(
        zones=(support_zone, resistance_zone),
        lower=90.0,
        upper=105.0,
        center=97.5,
        evidence=("support", "resistance"),
    )
    resistance_trigger = _bar("15m", DECISION_TIME, 102.0, 102.0, 101.0, 101.5)
    assert _select_zone((mixed,), resistance_trigger, Side.BUY) is None
    assert _select_zone((mixed,), resistance_trigger, Side.SELL) is mixed


def test_rsi_threshold_is_strict_at_30() -> None:
    daily = _daily_bias()
    zone = _cluster()
    news = NewsEvaluation(False, False, False, True, "news unaware")
    at_threshold = _score(
        Side.BUY,
        daily,
        zone,
        30.0,
        False,
        False,
        news,
        XAUUSDPMTFParameters(),
    )
    below = _score(
        Side.BUY,
        daily,
        zone,
        29.999,
        False,
        False,
        news,
        XAUUSDPMTFParameters(),
    )
    assert next(factor for factor in at_threshold.factors if factor.name == "rsi").awarded is False
    assert next(factor for factor in below.factors if factor.name == "rsi").awarded is True


def test_rsi_parameter_is_fixed_to_the_trigger_candle() -> None:
    assert XAUUSDPMTFParameters().m15_rsi_window == 1
    with pytest.raises(ValueError, match="m15_rsi_window"):
        XAUUSDPMTFParameters(m15_rsi_window=2)


def test_engulfing_and_pin_bar_boundaries() -> None:
    previous = _bar("15m", DECISION_TIME, 100.0, 101.0, 97.0, 98.0)
    bullish = _bar("15m", DECISION_TIME, 97.5, 101.0, 97.0, 100.5)
    flags = _pattern_flags(bullish, previous)
    assert flags["bullish_engulfing"] is True
    pin = _bar("15m", DECISION_TIME, 100.0, 101.2, 97.0, 101.0)
    assert _pattern_flags(pin, previous)["bullish_pin"] is True
    exact_wick = _bar("15m", DECISION_TIME, 100.0, 100.5, 99.6, 100.2)
    assert _pattern_flags(exact_wick, previous)["bullish_pin"] is True
    weak_wick = _bar("15m", DECISION_TIME, 100.0, 100.5, 99.61, 100.2)
    assert _pattern_flags(weak_wick, previous)["bullish_pin"] is False
    long_upper_wick = _bar("15m", DECISION_TIME, 100.0, 101.0, 99.6, 100.2)
    assert _pattern_flags(long_upper_wick, previous)["bullish_pin"] is False
    at_body_fraction = _bar("15m", DECISION_TIME, 97.9, 100.2, 88.7, 100.2)
    assert _pattern_flags(at_body_fraction, previous)["bullish_engulfing"] is True
    below_body_fraction = _bar("15m", DECISION_TIME, 97.9, 100.2, 88.6, 100.2)
    assert _pattern_flags(below_body_fraction, previous)["bullish_engulfing"] is False
    zero_body = _bar("15m", DECISION_TIME, 100.0, 101.0, 99.0, 100.0)
    assert _pattern_flags(zero_body, previous)["bullish_pin"] is False
    doji_prev = _bar("15m", DECISION_TIME, 100.0, 101.0, 99.0, 100.0)
    assert _pattern_flags(bullish, doji_prev)["bullish_engulfing"] is False


def test_tick_volume_baseline_and_missing_values() -> None:
    start = DECISION_TIME - timedelta(minutes=15 * 21)
    bars = [
        _bar("15m", start + timedelta(minutes=15 * i), 100.0, 101.0, 99.0, 100.0, 100.0)
        for i in range(20)
    ]
    trigger = _bar("15m", DECISION_TIME, 100.0, 101.0, 99.0, 100.0, 150.0)
    assert _volume_confirmed([*bars, trigger], XAUUSDPMTFParameters()) is True
    low_volume = replace(trigger, volume=149.0)
    assert _volume_confirmed([*bars, low_volume], XAUUSDPMTFParameters()) is False
    missing = replace(trigger, volume=None)
    assert _volume_confirmed([*bars, missing], XAUUSDPMTFParameters()) is False
    zero_baseline = [replace(item, volume=0.0) for item in bars]
    assert _volume_confirmed([*zero_baseline, trigger], XAUUSDPMTFParameters()) is False


# ---------------------------------------------------------------------------
# Scoring and mandatory filters
# ---------------------------------------------------------------------------


def test_score_all_factors_is_eight_and_high_confidence() -> None:
    news = NewsEvaluation(True, True, True, False, "none in 60 minutes")
    score = _score(
        Side.BUY,
        _daily_bias(),
        _cluster(),
        20.0,
        True,
        True,
        news,
        XAUUSDPMTFParameters(),
    )
    assert score.total == 8
    assert score.confidence is Confidence.HIGH
    assert [factor.name for factor in score.factors] == [
        "daily_alignment",
        "h4_zone",
        "rsi",
        "reversal_candle",
        "tick_volume",
        "news",
    ]
    assert all(factor.awarded for factor in score.factors)


def test_score_confidence_tiers_and_missing_evidence() -> None:
    parameters = XAUUSDPMTFParameters()
    unavailable = NewsEvaluation(False, False, False, True, "unavailable")
    medium = _score(Side.BUY, _daily_bias(), _cluster(), 50.0, False, True, unavailable, parameters)
    reject = _score(Side.BUY, _daily_bias(), _cluster(), 50.0, False, False, unavailable, parameters)
    assert medium.total == 5
    assert medium.confidence is Confidence.MEDIUM
    assert reject.total == 4
    assert reject.confidence is Confidence.REJECT
    assert next(factor for factor in reject.factors if factor.name == "rsi").available is True
    assert next(factor for factor in reject.factors if factor.name == "news").available is False


@pytest.mark.parametrize(
    ("total", "daily_state", "has_zone", "rsi", "pattern", "volume", "news_point"),
    [
        (0, BiasState.NEUTRAL, False, 50.0, False, False, False),
        (1, BiasState.NEUTRAL, False, 20.0, False, False, False),
        (2, BiasState.BULLISH, False, 50.0, False, False, False),
        (3, BiasState.BULLISH, False, 20.0, False, False, False),
        (4, BiasState.BULLISH, True, 50.0, False, False, False),
        (5, BiasState.BULLISH, True, 20.0, False, False, False),
        (6, BiasState.BULLISH, True, 20.0, True, False, False),
        (7, BiasState.BULLISH, True, 20.0, True, True, False),
        (8, BiasState.BULLISH, True, 20.0, True, True, True),
    ],
)
def test_every_supported_total_score(
    total: int,
    daily_state: BiasState,
    has_zone: bool,
    rsi: float,
    pattern: bool,
    volume: bool,
    news_point: bool,
) -> None:
    news = NewsEvaluation(True, True, news_point, False, "test")
    score = _score(
        Side.BUY,
        _daily_bias(daily_state),
        _cluster() if has_zone else None,
        rsi,
        pattern,
        volume,
        news,
        XAUUSDPMTFParameters(),
    )
    assert score.total == total
    assert score.confidence is (
        Confidence.HIGH
        if total >= 7
        else Confidence.MEDIUM
        if total >= 5
        else Confidence.REJECT
    )


def test_volume_unavailable_is_not_awarded_and_is_marked_unavailable() -> None:
    news = NewsEvaluation(False, False, False, True, "unavailable")
    score = _score(
        Side.BUY,
        _daily_bias(),
        _cluster(),
        20.0,
        False,
        False,
        news,
        XAUUSDPMTFParameters(),
        volume_available=False,
    )
    factor = next(item for item in score.factors if item.name == "tick_volume")
    assert factor.awarded is False
    assert factor.available is False
    assert score.total == 5


def test_h4_mandatory_filter_and_daily_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    neutral = H4Trend(
        TrendState.NEUTRAL,
        100.0,
        (),
        None,
        None,
        None,
        None,
        DECISION_TIME,
        200,
    )
    strategy = XAUUSDPMultiTimeframeStrategy()
    monkeypatch.setattr(xauusd_module, "_h4_trend", lambda *args: neutral)
    d1, h4, m15, next_bar = _fixture()
    neutral_decision = strategy.evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        risk_state=_risk(),
        news_context=NewsContext.unavailable(),
        research_mode=True,
        news_unaware=True,
    )
    assert neutral_decision.rejections[0].code is RejectionCode.H4_NEUTRAL
    monkeypatch.undo()

    conflict = _evaluate(d1=_d1_series(bearish=True))
    assert conflict.rejections[0].code is RejectionCode.D1_CONFLICT


def test_no_zone_and_late_entry_rejections() -> None:
    d1, h4, _, next_bar = _fixture()
    center = _latest_support(h4)
    far_m15 = _m15_series(center + 2000.0)
    no_zone = _evaluate(d1=d1, h4=h4, m15=far_m15, next_bar=next_bar)
    assert no_zone.rejections[0].code is RejectionCode.NO_H4_ZONE
    assert len(no_zone.score.factors) == 6
    assert next(factor for factor in no_zone.score.factors if factor.name == "h4_zone").awarded is False
    chased = _next_bar(center + 1000.0)
    assert _evaluate(d1=d1, h4=h4, m15=_m15_series(center), next_bar=chased).rejections[0].code is RejectionCode.ENTRY_CHASED


def test_news_unavailable_and_research_unaware_separation() -> None:
    research = _evaluate(
        news=NewsContext.unavailable(),
        research_mode=True,
        news_unaware=True,
    )
    assert research.status is DecisionStatus.CANDIDATE
    assert research.candidate.news_unaware is True
    assert research.candidate.execution_ready is False
    production = _evaluate(
        news=NewsContext.unavailable(),
        research_mode=False,
        news_unaware=False,
    )
    assert production.rejections[0].code is RejectionCode.NEWS_UNAVAILABLE


def test_news_safety_window_and_score_window() -> None:
    safety = _evaluate(news=_news(at=DECISION_TIME + timedelta(minutes=10)))
    assert safety.rejections[0].code is RejectionCode.NEWS_EVENT_WINDOW
    assert len(safety.score.factors) == 6
    assert next(factor for factor in safety.score.factors if factor.name == "news").awarded is False
    # Under the approved next-open convention, entry_time equals decision_time.
    # The safety helper is nevertheless exercised with an explicitly later
    # entry reference to prove it honors the entry timestamp.
    news = _news(at=DECISION_TIME + timedelta(minutes=31))
    entry_eval = _news_evaluation(
        news,
        DECISION_TIME,
        XAUUSDPMTFParameters(),
        entry_time=DECISION_TIME + timedelta(minutes=15),
        research_mode=True,
        news_unaware=False,
    )
    assert entry_eval.safe is False
    entry_boundary = _evaluate(news=_news(at=DECISION_TIME + timedelta(minutes=30)))
    assert entry_boundary.rejections[0].code is RejectionCode.NEWS_EVENT_WINDOW
    no_point = _evaluate(news=_news(at=DECISION_TIME + timedelta(minutes=31)))
    assert no_point.status is DecisionStatus.CANDIDATE
    assert no_point.score.total == 6
    later_no_point = _evaluate(news=_news(at=DECISION_TIME + timedelta(minutes=45)))
    assert later_no_point.status is DecisionStatus.CANDIDATE
    assert later_no_point.score.total == 6
    point = _evaluate(news=_news(at=DECISION_TIME + timedelta(minutes=90)))
    assert point.status is DecisionStatus.CANDIDATE
    assert point.score.total == 7
    assert point.score.confidence is Confidence.HIGH
    assert point.candidate.risk.risk_fraction == pytest.approx(0.010)
    non_qualifying = _evaluate(
        news=_news(event_type="Retail Sales", at=DECISION_TIME + timedelta(minutes=10))
    )
    assert non_qualifying.status is DecisionStatus.CANDIDATE
    assert non_qualifying.score.total == 7


def test_stale_news_calendar_is_unavailable_and_not_awarded() -> None:
    stale = NewsContext(
        verified=True,
        complete=True,
        as_of=DECISION_TIME - timedelta(minutes=16),
        events=(),
        source="stale",
    )
    production = _evaluate(
        news=stale,
        research_mode=False,
        news_unaware=False,
    )
    assert production.rejections[0].code is RejectionCode.NEWS_UNAVAILABLE
    research = _evaluate(
        news=stale,
        research_mode=True,
        news_unaware=True,
    )
    assert research.status is DecisionStatus.CANDIDATE
    news_factor = next(factor for factor in research.score.factors if factor.name == "news")
    assert news_factor.available is False
    assert news_factor.awarded is False


def test_news_snapshot_from_the_future_is_not_used() -> None:
    future_snapshot = _news(as_of=DECISION_TIME + timedelta(minutes=1))
    production = _evaluate(
        news=future_snapshot,
        research_mode=False,
        news_unaware=False,
    )
    assert production.rejections[0].code is RejectionCode.NEWS_UNAVAILABLE
    research = _evaluate(
        news=future_snapshot,
        research_mode=True,
        news_unaware=True,
    )
    assert research.status is DecisionStatus.CANDIDATE
    assert research.candidate.news_unaware is True


def _outside_session_fixture() -> tuple[
    list[MarketObservation],
    list[MarketObservation],
    list[MarketObservation],
    MarketObservation,
    datetime,
]:
    d1, h4, _, _ = _fixture()
    decision = datetime(2026, 10, 22, 3, 15, tzinfo=UTC)
    trigger_at = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)
    completed_h4 = [
        item
        for item in h4
        if item.observed_at.astimezone(UTC) + timedelta(hours=4) <= decision
    ]
    center = _latest_support(completed_h4)
    return (
        d1,
        h4,
        _m15_series(center, trigger_at=trigger_at),
        _next_bar(center, at=decision),
        decision,
    )


def test_session_outside_rejects_end_to_end() -> None:
    d1, h4, m15, next_bar, decision = _outside_session_fixture()
    result = XAUUSDPMultiTimeframeStrategy().evaluate(
        d1,
        h4,
        m15,
        decision_time=decision,
        next_m15_bar=next_bar,
        risk_state=_risk(),
        news_context=NewsContext.unavailable(),
        research_mode=True,
        news_unaware=True,
    )
    assert result.rejections[0].code is RejectionCode.SESSION_OUTSIDE


def test_approved_strategy_has_no_public_session_disable_switch() -> None:
    parameters = inspect.signature(XAUUSDPMultiTimeframeStrategy.evaluate).parameters
    assert "disable_session" not in parameters
    assert "session_filter_mode" not in parameters
    decision = _evaluate()
    assert decision.variant_id == XAUUSDPMultiTimeframeStrategy().variant_id
    assert decision.session.filter_mode is SessionFilterMode.APPROVED


def test_research_variant_identity_and_metadata() -> None:
    variant = XAUUSDPMTFResearchVariant()
    assert variant.name == "xauusd_p_multi_timeframe_research_filter_isolated"
    assert variant.version == "1.0.0"
    assert variant.variant_id == "xauusd_p_filter_isolated_v1"
    assert isinstance(variant, XAUUSDPMultiTimeframeStrategy)
    d1, h4, m15, next_bar = _fixture()
    decision = variant.evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        risk_state=_risk(),
        news_context=None,
    )
    assert decision.variant_id == variant.variant_id
    assert decision.session.filter_mode is SessionFilterMode.RESEARCH_ISOLATED
    assert decision.session.calendar_verified is False
    assert decision.candidate is not None


def test_research_variant_isolates_outside_session_filter() -> None:
    d1, h4, m15, next_bar, decision_time = _outside_session_fixture()
    result = XAUUSDPMTFResearchVariant().evaluate(
        d1,
        h4,
        m15,
        decision_time=decision_time,
        next_m15_bar=next_bar,
        risk_state=_risk(),
        news_context=NewsContext.unavailable(),
    )
    assert result.variant_id == XAUUSDPMTFResearchVariant().variant_id
    assert result.session.filter_mode is SessionFilterMode.RESEARCH_ISOLATED
    assert result.session.in_overlap is True
    assert result.session.calendar_verified is False
    assert not any(
        rejection.code is RejectionCode.SESSION_OUTSIDE
        for rejection in result.rejections
    )


def test_research_variant_never_awards_news_point() -> None:
    d1, h4, m15, next_bar = _fixture()
    result = XAUUSDPMTFResearchVariant().evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        risk_state=_risk(),
        news_context=_news(at=DECISION_TIME + timedelta(minutes=90)),
    )
    assert result.candidate is not None
    news_factor = next(
        factor for factor in result.score.factors if factor.name == "news"
    )
    assert news_factor.awarded is False
    assert news_factor.available is False
    assert result.news.point_awarded is False
    assert result.candidate.news_unaware is True


def test_research_variant_rejects_verified_session_claim() -> None:
    d1, h4, m15, next_bar = _fixture()
    with pytest.raises(ValueError, match="verified session"):
        XAUUSDPMTFResearchVariant().evaluate(
            d1,
            h4,
            m15,
            decision_time=DECISION_TIME,
            next_m15_bar=next_bar,
            risk_state=_risk(),
            session_calendar_verified=True,
        )


def test_research_variant_labels_even_early_rejections() -> None:
    _, h4, m15, next_bar = _fixture()
    result = XAUUSDPMTFResearchVariant().evaluate(
        [],
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        risk_state=_risk(),
        news_context=NewsContext.unavailable(),
    )
    assert result.status is DecisionStatus.REJECTED
    assert result.variant_id == XAUUSDPMTFResearchVariant().variant_id
    assert result.session.filter_mode is SessionFilterMode.RESEARCH_ISOLATED


def test_variants_are_distinguishable_in_decision_records() -> None:
    approved = _evaluate()
    d1, h4, m15, next_bar = _fixture()
    research = XAUUSDPMTFResearchVariant().evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        risk_state=_risk(),
    )
    assert approved.variant_id != research.variant_id
    assert approved.session.filter_mode is SessionFilterMode.APPROVED
    assert research.session.filter_mode is SessionFilterMode.RESEARCH_ISOLATED
    assert approved.variant_id == "xauusd_p_approved_v1"
    assert research.variant_id == "xauusd_p_filter_isolated_v1"


def test_research_record_preserves_identity_schema_and_plan() -> None:
    decision = _evaluate()
    identity = XAUUSDPMultiTimeframeStrategy().identity
    record = serialize_strategy_decision(decision, identity)

    assert record["schema"] == RESEARCH_RECORD_SCHEMA
    assert record["schema_version"] == RESEARCH_RECORD_SCHEMA_VERSION
    assert record["record_type"] == "strategy_decision"
    assert record["authoritative_source"] == "StrategyDecision"
    assert record["strategy_name"] == identity.strategy_name
    assert record["strategy_version"] == identity.strategy_version
    assert record["variant_id"] == "xauusd_p_approved_v1"
    assert record["variant_name"] == "approved"
    assert record["research_only"] is True
    assert record["execution_ready"] is False
    assert record["time_semantics"] == DATASET_TIME_SEMANTICS
    assert record["absolute_utc_provenance"] == "unverified"
    assert record["decision_time"] == decision.decision_time.isoformat()
    assert record["decision_time_original"] == decision.decision_time.isoformat()
    assert record["symbol"] == "XAUUSD-P"
    assert record["timeframe"] == "15m"
    assert record["status"] == "candidate"
    assert record["session"]["filter_mode"] == "approved"
    assert record["session"]["calendar_verified"] is False
    assert record["news_unaware"] is True
    assert record["score"]["total"] == decision.score.total
    assert record["score"]["confidence"] == decision.score.confidence.value
    assert [factor["name"] for factor in record["score"]["factors"]] == [
        factor.name for factor in decision.score.factors
    ]
    assert record["plan"] is not None
    plan = decision.candidate.plan
    assert record["plan"] == {
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
    assert record["candidate"]["entry_reference"] == decision.candidate.entry_reference
    assert record["limitations"]
    for limitation in (
        "research_only",
        "dataset_time_does_not_claim_verified_absolute_utc",
        "session_calendar_unverified",
        "news_calendar_unverified",
        "cost_verification_unavailable",
        "monetary_sizing_blocked",
        "execution_not_ready",
    ):
        assert limitation in record["limitations"]
    validate_research_record(record)


def test_approved_and_research_records_are_distinct() -> None:
    approved_decision = _evaluate()
    approved_identity = XAUUSDPMultiTimeframeStrategy().identity
    approved_record = serialize_strategy_decision(approved_decision, approved_identity)

    d1, h4, m15, next_bar = _fixture()
    research = XAUUSDPMTFResearchVariant()
    research_decision = research.evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        risk_state=_risk(),
    )
    research_record = serialize_strategy_decision(research_decision, research.identity)

    assert approved_record["variant_id"] != research_record["variant_id"]
    assert approved_record["session"]["filter_mode"] == "approved"
    assert research_record["session"]["filter_mode"] == "research_isolated"
    assert approved_record["strategy_name"] != research_record["strategy_name"]
    assert research_record["strategy_name"] == research.name
    assert research_record["strategy_version"] == research.version
    assert research_record["variant_name"] == "research_filter_isolated"
    assert approved_record["news"]["point_awarded"] is False
    assert research_record["news"]["point_awarded"] is False
    research_record["news"]["point_awarded"] = True
    with pytest.raises(ResearchRecordIdentityError, match="news point"):
        validate_research_record(research_record)


def test_research_record_preserves_original_timestamp_representation() -> None:
    decision = _evaluate()
    original = "2026-10-22 14:30:00+00:00"
    record = serialize_strategy_decision(
        decision,
        XAUUSDPMultiTimeframeStrategy().identity,
        original_decision_time=original,
    )
    assert record["decision_time"] == decision.decision_time.isoformat()
    assert record["decision_time_original"] == original
    assert record["time_semantics"] == DATASET_TIME_SEMANTICS
    assert record["absolute_utc_provenance"] == "unverified"


def test_research_record_round_trip_and_determinism() -> None:
    decision = _evaluate()
    record = serialize_strategy_decision(decision, XAUUSDPMultiTimeframeStrategy().identity)
    canonical = canonical_json(record)
    parsed = parse_research_record(canonical)
    assert canonical_json(parsed) == canonical
    assert serialize_strategy_decision(decision, XAUUSDPMultiTimeframeStrategy().identity) == record
    assert canonical_json(record) == canonical


def test_research_record_rejects_unsupported_schema() -> None:
    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["schema_version"] = "999.0"
    with pytest.raises(ResearchRecordSchemaError, match="schema version"):
        validate_research_record(record)
    with pytest.raises(ResearchRecordSchemaError):
        parse_research_record(record)


def test_research_record_rejects_malformed_json() -> None:
    with pytest.raises(ResearchRecordSchemaError, match="valid JSON"):
        parse_research_record("{")


def test_research_record_rejects_missing_required_fields() -> None:
    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    del record["session"]
    with pytest.raises(ResearchRecordSchemaError, match="session"):
        validate_research_record(record)


def test_research_record_rejects_inconsistent_values() -> None:
    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["score"]["total"] += 1
    with pytest.raises(ResearchRecordSchemaError, match="score total"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["plan"]["tp1_fraction"] = 0.4
    with pytest.raises(ResearchRecordSchemaError, match="fractions"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["session"]["filter_mode"] = "research_isolated"
    with pytest.raises(ResearchRecordIdentityError, match="filter_mode"):
        validate_research_record(record)


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
)
def test_research_record_rejects_non_finite_numbers(non_finite: float) -> None:
    decision = _evaluate()
    assert decision.risk is not None
    invalid_decision = replace(
        decision,
        risk=replace(decision.risk, risk_fraction=non_finite),
    )
    with pytest.raises(ResearchRecordSchemaError, match="finite"):
        serialize_strategy_decision(
            invalid_decision,
            XAUUSDPMultiTimeframeStrategy().identity,
        )

    record = serialize_strategy_decision(
        decision,
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["risk"]["risk_fraction"] = non_finite
    with pytest.raises(ResearchRecordSchemaError, match="finite"):
        validate_research_record(record)
    with pytest.raises(ResearchRecordSchemaError, match="finite"):
        canonical_json(record)

    encoded = json.dumps(record)
    with pytest.raises(ResearchRecordSchemaError):
        parse_research_record(encoded)


def test_research_record_rejects_unverified_compliance_claims() -> None:
    def reject(mutator, match: str) -> None:
        record = serialize_strategy_decision(
            _evaluate(),
            XAUUSDPMultiTimeframeStrategy().identity,
        )
        mutator(record)
        with pytest.raises(ResearchRecordSchemaError, match=match):
            validate_research_record(record)

    reject(
        lambda record: record["session"].__setitem__("calendar_verified", True),
        "session calendar",
    )
    reject(
        lambda record: record["news"].__setitem__("safe", True),
        "news",
    )
    reject(
        lambda record: record["news"].__setitem__("point_awarded", True),
        "news point",
    )
    reject(
        lambda record: record["risk"].__setitem__("sizing_status", "sized"),
        "sizing",
    )
    reject(
        lambda record: record["risk"].__setitem__("cost_adjusted", True),
        "cost-adjusted",
    )
    reject(
        lambda record: record["plan"].__setitem__("breakeven_costs_verified", True),
        "breakeven",
    )
    reject(
        lambda record: record.__setitem__("execution_ready", True),
        "execution_ready",
    )
    reject(
        lambda record: record.__setitem__("absolute_utc_provenance", "verified"),
        "absolute_utc_provenance",
    )


def test_research_record_rejects_unverified_news_awareness() -> None:
    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["news"].update(
        available=True,
        safe=True,
        unaware=False,
        point_awarded=False,
    )
    record["news_unaware"] = False
    record["candidate"]["news_unaware"] = False
    with pytest.raises(ResearchRecordSchemaError, match="news"):
        validate_research_record(record)


def test_research_record_rejects_compliance_claims_at_serialization() -> None:
    decision = _evaluate()
    identity = XAUUSDPMultiTimeframeStrategy().identity

    verified_session = replace(
        decision,
        session=replace(decision.session, calendar_verified=True),
    )
    with pytest.raises(ResearchRecordSchemaError, match="session calendar"):
        serialize_strategy_decision(verified_session, identity)

    aware_news = NewsEvaluation(
        available=True,
        safe=True,
        point_awarded=False,
        unaware=False,
        reason="test verified news",
    )
    news_decision = replace(
        decision,
        news=aware_news,
        news_unaware=False,
        candidate=replace(
            decision.candidate,
            news=aware_news,
            news_unaware=False,
        ),
    )
    with pytest.raises(ResearchRecordSchemaError, match="news"):
        serialize_strategy_decision(news_decision, identity)

    assert decision.risk is not None
    sized_risk = replace(
        decision,
        risk=replace(decision.risk, sizing_status="sized"),
    )
    with pytest.raises(ResearchRecordSchemaError, match="sizing"):
        serialize_strategy_decision(sized_risk, identity)

    assert decision.candidate is not None
    verified_cost = replace(
        decision,
        candidate=replace(
            decision.candidate,
            plan=replace(
                decision.candidate.plan,
                breakeven_costs_verified=True,
            ),
        ),
    )
    with pytest.raises(ResearchRecordSchemaError, match="breakeven"):
        serialize_strategy_decision(verified_cost, identity)


def test_research_record_rejects_candidate_timestamp_inconsistency() -> None:
    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["candidate"]["decision_time"] = "2026-10-23T12:15:00+00:00"
    with pytest.raises(ResearchRecordSchemaError, match="candidate decision_time"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["candidate"]["entry_time"] = "2026-10-21T12:15:00+00:00"
    with pytest.raises(ResearchRecordSchemaError, match="entry_time"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["candidate"]["confidence"] = Confidence.REJECT.value
    with pytest.raises(ResearchRecordSchemaError, match="confidence"):
        validate_research_record(record)


def test_research_record_rejects_invalid_plan_geometry() -> None:
    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    entry = record["plan"]["entry"]
    record["plan"]["stop"] = entry + 1.0
    record["plan"]["risk_distance"] = 1.0
    with pytest.raises(ResearchRecordSchemaError, match="long plan"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["plan"]["tp1"] = record["plan"]["entry"] - 1.0
    with pytest.raises(ResearchRecordSchemaError, match="long plan"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["plan"]["risk_distance"] = record["plan"]["risk_distance"] + 1.0
    with pytest.raises(ResearchRecordSchemaError, match="risk_distance"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["plan"]["breakeven"] = record["plan"]["entry"] + 1.0
    with pytest.raises(ResearchRecordSchemaError, match="breakeven"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["candidate"]["side"] = Side.SELL.value
    with pytest.raises(ResearchRecordSchemaError, match="short plan"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    entry = record["plan"]["entry"]
    record["candidate"]["side"] = Side.SELL.value
    record["plan"].update(
        stop=entry - 1.0,
        tp1=entry + 1.0,
        tp2=entry + 2.0,
        risk_distance=1.0,
    )
    with pytest.raises(ResearchRecordSchemaError, match="short plan"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["plan"]["tp1_fraction"] = 0.0
    with pytest.raises(ResearchRecordSchemaError, match="positive"):
        validate_research_record(record)


def test_research_record_rejects_unsupported_fields_and_objects() -> None:
    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["unexpected"] = 1
    with pytest.raises(ResearchRecordSchemaError, match="unsupported"):
        validate_research_record(record)
    with pytest.raises(ResearchRecordSchemaError, match="unsupported"):
        canonical_json(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["session"]["unexpected"] = True
    with pytest.raises(ResearchRecordSchemaError, match="unsupported"):
        validate_research_record(record)

    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["session"]["reason"] = object()
    with pytest.raises(ResearchRecordSchemaError):
        validate_research_record(record)


def test_research_record_canonicalizes_numeric_representation() -> None:
    record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    integer_record = serialize_strategy_decision(
        _evaluate(),
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    integer_record["plan"]["entry"] = int(integer_record["plan"]["entry"])
    integer_record["candidate"]["entry_reference"] = int(
        integer_record["candidate"]["entry_reference"]
    )

    assert canonical_json(record) == canonical_json(integer_record)
    parsed = parse_research_record(canonical_json(integer_record))
    assert isinstance(parsed["plan"]["entry"], float)
    assert isinstance(parsed["plan"]["tp1"], float)
    assert isinstance(parsed["score"]["total"], int)


def test_research_record_identity_mismatch_fails_closed() -> None:
    decision = _evaluate()
    research_identity = XAUUSDPMTFResearchVariant().identity
    with pytest.raises(ResearchRecordIdentityError):
        serialize_strategy_decision(decision, research_identity)

    inconsistent_identity = StrategyIdentity(
        strategy_name="xauusd_p_multi_timeframe",
        strategy_version="1.0.0",
        variant_id="xauusd_p_approved_v1",
        variant_name="wrong",
    )
    with pytest.raises(ResearchRecordIdentityError):
        serialize_strategy_decision(decision, inconsistent_identity)

    record = serialize_strategy_decision(
        decision,
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["variant_id"] = ""
    with pytest.raises(ResearchRecordIdentityError):
        validate_research_record(record)

    record = serialize_strategy_decision(
        decision,
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["variant_name"] = "wrong"
    with pytest.raises(ResearchRecordIdentityError):
        validate_research_record(record)

    record = serialize_strategy_decision(
        decision,
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["strategy_version"] = "999.0"
    with pytest.raises(ResearchRecordIdentityError):
        validate_research_record(record)

    record = serialize_strategy_decision(
        decision,
        XAUUSDPMultiTimeframeStrategy().identity,
    )
    record["strategy_name"] = "wrong"
    with pytest.raises(ResearchRecordIdentityError):
        validate_research_record(record)


def test_candidate_and_trade_signal_are_not_authoritative_records() -> None:
    decision = _evaluate()
    identity = XAUUSDPMultiTimeframeStrategy().identity
    assert decision.candidate is not None
    with pytest.raises(TypeError):
        serialize_strategy_decision(decision.candidate, identity)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        serialize_strategy_decision(decision.to_trade_signal(), identity)  # type: ignore[arg-type]


def test_variant_identity_cannot_be_mutated_on_instances() -> None:
    variant = XAUUSDPMTFResearchVariant()
    with pytest.raises(AttributeError):
        variant.variant_id = "xauusd_p_approved_v1"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        variant.name = "approved"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        variant.version = "999"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        variant._identity = None  # type: ignore[misc]
    assert variant.identity.variant_id == "xauusd_p_filter_isolated_v1"


def test_rejected_research_record_retains_identity_and_reason() -> None:
    _, h4, m15, next_bar = _fixture()
    variant = XAUUSDPMTFResearchVariant()
    decision = variant.evaluate(
        [],
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        risk_state=_risk(),
    )
    record = serialize_strategy_decision(decision, variant.identity)
    assert record["variant_id"] == variant.variant_id
    assert record["session"]["filter_mode"] == "research_isolated"
    assert record["status"] == "rejected"
    assert record["rejections"]
    assert record["rejections"][0]["code"] == "d1_unavailable"
    assert record["plan"] is None
    assert record["candidate"] is None
    assert validate_research_record(record)["plan"] is None
    assert canonical_json(record)


def test_session_overlap_and_production_session_block() -> None:
    parameters = XAUUSDPMTFParameters()
    inside, entry_inside = _session_overlap(
        DECISION_TIME,
        DECISION_TIME,
        parameters,
    )
    assert inside is True and entry_inside is True
    outside, _ = _session_overlap(
        datetime(2026, 10, 22, 3, 15, tzinfo=UTC),
        datetime(2026, 10, 22, 3, 30, tzinfo=UTC),
        parameters,
    )
    assert outside is False
    news = _news(at=DECISION_TIME + timedelta(minutes=90))
    production = _evaluate(
        news=news,
        research_mode=False,
        news_unaware=False,
        session_verified=False,
    )
    assert production.rejections[0].code is RejectionCode.SESSION_UNVERIFIED


# ---------------------------------------------------------------------------
# Stops, targets, risk, and position state
# ---------------------------------------------------------------------------


def test_structural_stop_atr_buffer_and_targets() -> None:
    decision = _evaluate()
    candidate = decision.candidate
    assert candidate is not None
    h4 = decision.h4_trend
    zone = decision.zone
    assert h4 is not None and zone is not None and h4.atr is not None
    expected_reference = min(zone.lower, h4.pl1)  # type: ignore[arg-type]
    expected_stop = math.floor(
        (expected_reference - 0.25 * h4.atr) / XAUUSDPMTFParameters().tick_size + 1e-9
    ) * XAUUSDPMTFParameters().tick_size
    assert candidate.plan.stop == pytest.approx(expected_stop)
    risk = candidate.entry_reference - candidate.plan.stop
    assert candidate.plan.risk_distance == pytest.approx(risk)
    assert candidate.plan.tp1 == pytest.approx(
        math.floor((candidate.entry_reference + 2.0 * risk) / 0.01 + 1e-9) * 0.01
    )
    assert candidate.plan.tp2 == pytest.approx(
        math.floor((candidate.entry_reference + 3.0 * risk) / 0.01 + 1e-9) * 0.01
    )


def test_stop_distance_rejection() -> None:
    parameters = replace(XAUUSDPMTFParameters(), max_stop_atr=0.01)
    decision = _evaluate(parameters=parameters)
    assert decision.rejections[0].code is RejectionCode.STOP_TOO_WIDE


def test_structural_obstacle_rules() -> None:
    parameters = XAUUSDPMTFParameters()
    zone = _cluster(95.0, 99.0, center=97.0)
    base_pivots = (
        _pivot(2, PivotKind.LOW, 94.0),
        _pivot(5, PivotKind.HIGH, 110.0),
    )
    before_tp1 = XAUUSDPMultiTimeframeStrategy()._trade_plan(
        Side.BUY,
        100.0,
        zone,
        _h4_trend_for_plan(base_pivots, atr=2.0),
    )
    assert before_tp1 is None

    between = (
        _pivot(2, PivotKind.LOW, 94.0),
        _pivot(5, PivotKind.HIGH, 116.0),
    )
    too_small = XAUUSDPMultiTimeframeStrategy()._trade_plan(
        Side.BUY,
        100.0,
        zone,
        _h4_trend_for_plan(between, atr=2.0),
    )
    assert too_small is None

    capped = (
        _pivot(2, PivotKind.LOW, 94.0),
        _pivot(5, PivotKind.HIGH, 118.0),
    )
    plan = XAUUSDPMultiTimeframeStrategy()._trade_plan(
        Side.BUY,
        100.0,
        zone,
        _h4_trend_for_plan(capped, atr=2.0),
    )
    assert plan is not None
    assert plan.tp2 == pytest.approx(117.5)


def test_short_structural_plan_and_obstacle_rule() -> None:
    zone = _cluster(101.0, 105.0, center=103.0)
    trend = _h4_trend_for_plan(
        (
            _pivot(2, PivotKind.HIGH, 106.0),
            _pivot(5, PivotKind.LOW, 70.0),
        ),
        atr=2.0,
        state=TrendState.BEARISH,
    )
    plan = XAUUSDPMultiTimeframeStrategy()._trade_plan(Side.SELL, 100.0, zone, trend)
    assert plan is not None
    assert plan.stop == pytest.approx(106.5)
    assert plan.tp1 < 100.0 < plan.stop
    assert plan.tp2 < plan.tp1
    blocked = _h4_trend_for_plan(
        (
            _pivot(2, PivotKind.HIGH, 106.0),
            _pivot(5, PivotKind.LOW, 90.0),
        ),
        atr=2.0,
        state=TrendState.BEARISH,
    )
    assert XAUUSDPMultiTimeframeStrategy()._trade_plan(Side.SELL, 100.0, zone, blocked) is None


def test_partial_exit_and_breakeven_state() -> None:
    state = ResearchPositionState()
    after_tp1 = state.on_tp1_filled(0.5)
    assert after_tp1.remaining_fraction == pytest.approx(0.5)
    assert after_tp1.breakeven_active is True
    assert after_tp1.status is TradeStatus.TP1_FILLED
    after_tp2 = after_tp1.on_tp2_filled(0.5)
    assert after_tp2.remaining_fraction == 0.0
    assert after_tp2.status is TradeStatus.TP2_CLOSED
    stopped = after_tp1.on_stop_filled()
    assert stopped.status is TradeStatus.BREAKEVEN_STOPPED
    assert stopped.remaining_fraction == 0.0
    with pytest.raises(ValueError):
        state.on_tp2_filled(0.5)
    with pytest.raises(ValueError):
        state.on_tp1_filled(0.75)


def test_stop_and_targets_are_tick_aligned() -> None:
    decision = _evaluate()
    candidate = decision.candidate
    assert candidate is not None
    for price in (
        candidate.plan.stop,
        candidate.plan.tp1,
        candidate.plan.tp2,
    ):
        assert price == pytest.approx(round(price, 2), abs=1e-9)
    assert candidate.plan.risk_distance > 0.0


def test_trade_signal_is_research_only_and_uses_tp1() -> None:
    decision = _evaluate()
    candidate = decision.candidate
    assert candidate is not None
    signal = decision.to_trade_signal()
    assert isinstance(signal, TradeSignal)
    assert signal.take_profit == pytest.approx(candidate.plan.tp1)
    assert signal.take_profit != pytest.approx(candidate.plan.tp2)
    assert "TP2" in signal.rationale
    assert "blocked" in signal.rationale.lower()
    assert not hasattr(signal, "execution_ready")
    assert candidate.execution_ready is False
    assert candidate.plan.monetary_sizing_status == "blocked_unverified_contract_economics"
    assert candidate.plan.partial_exit_status == "blocked_pending_volume_sizing"


def test_monetary_sizing_is_explicitly_blocked() -> None:
    decision = _evaluate()
    candidate = decision.candidate
    assert candidate is not None
    assert candidate.plan.monetary_sizing_status == "blocked_unverified_contract_economics"
    assert candidate.plan.partial_exit_status == "blocked_pending_volume_sizing"
    assert candidate.risk.sizing_status == "blocked_unverified_contract_economics"
    assert candidate.execution_ready is False
    assert decision.execution_ready is False


def test_daily_loss_entry_count_concurrent_and_cooldown_limits() -> None:
    assert _evaluate(risk=_risk(equity=97_000.0)).rejections[0].code is RejectionCode.RISK_DAILY_LOSS
    assert _evaluate(risk=_risk(entries_today=3)).rejections[0].code is RejectionCode.RISK_DAILY_ENTRIES
    assert _evaluate(risk=_risk(open_positions=2)).rejections[0].code is RejectionCode.RISK_CONCURRENT
    cooldown = _evaluate(
        risk=_risk(
            consecutive_losses=1,
            last_exit_at=DECISION_TIME - timedelta(hours=1),
        )
    )
    assert cooldown.rejections[0].code is RejectionCode.RISK_COOLDOWN
    recovered = _evaluate(
        risk=_risk(
            consecutive_losses=1,
            last_exit_at=DECISION_TIME - timedelta(hours=5),
        )
    )
    assert recovered.status is DecisionStatus.CANDIDATE
    double_loss = _evaluate(
        risk=_risk(
            consecutive_losses=2,
            last_exit_at=DECISION_TIME - timedelta(hours=6),
        )
    )
    assert double_loss.rejections[0].code is RejectionCode.RISK_COOLDOWN
    double_loss_expired = _evaluate(
        risk=_risk(
            consecutive_losses=2,
            last_exit_at=DECISION_TIME - timedelta(hours=9),
        )
    )
    assert double_loss_expired.status is DecisionStatus.CANDIDATE
    incomplete_single = _evaluate(risk=_risk(consecutive_losses=1))
    assert incomplete_single.rejections[0].code is RejectionCode.RISK_COOLDOWN
    assert "cooldown state unavailable" in incomplete_single.rejections[0].message.lower()
    incomplete_double = _evaluate(risk=_risk(consecutive_losses=2))
    assert incomplete_double.rejections[0].code is RejectionCode.RISK_COOLDOWN
    persisted_future = _evaluate(
        risk=_risk(cooldown_until=DECISION_TIME + timedelta(hours=1))
    )
    assert persisted_future.rejections[0].code is RejectionCode.RISK_COOLDOWN
    inconsistent = _evaluate(
        risk=_risk(
            consecutive_losses=1,
            last_exit_at=DECISION_TIME - timedelta(hours=1),
            cooldown_until=DECISION_TIME - timedelta(hours=2),
        )
    )
    assert inconsistent.rejections[0].code is RejectionCode.RISK_COOLDOWN
    first = _evaluate(
        risk=_risk(
            consecutive_losses=1,
            last_exit_at=DECISION_TIME - timedelta(hours=1),
        )
    )
    second = _evaluate(
        risk=_risk(
            consecutive_losses=1,
            last_exit_at=DECISION_TIME - timedelta(hours=1),
        )
    )
    assert first.rejections[0].code is RejectionCode.RISK_COOLDOWN
    assert second.rejections[0].code is RejectionCode.RISK_COOLDOWN


def test_missing_risk_state_is_rejected_without_a_candidate() -> None:
    d1, h4, m15, next_bar = _fixture()
    decision = XAUUSDPMultiTimeframeStrategy().evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        risk_state=None,
        news_context=NewsContext.unavailable(),
        research_mode=True,
        news_unaware=True,
    )
    assert decision.status is DecisionStatus.REJECTED
    assert decision.candidate is None
    assert decision.rejections[0].code is RejectionCode.RISK_UNAVAILABLE
    omitted = XAUUSDPMultiTimeframeStrategy().evaluate(
        d1,
        h4,
        m15,
        decision_time=DECISION_TIME,
        next_m15_bar=next_bar,
        news_context=NewsContext.unavailable(),
        research_mode=True,
        news_unaware=True,
    )
    assert omitted.rejections[0].code is RejectionCode.RISK_UNAVAILABLE


def test_rejected_confidence_cannot_receive_risk_allowance() -> None:
    result = _risk_decision(
        _risk(),
        DECISION_TIME,
        Confidence.REJECT,
        XAUUSDPMTFParameters(),
    )
    assert result.eligible is False
    assert result.risk_fraction == 0.0
    assert any("Rejected confidence" in reason for reason in result.reasons)


def test_tp2_configuration_cannot_be_below_the_approved_minimum() -> None:
    with pytest.raises(ValueError, match="tp2_r"):
        XAUUSDPMTFParameters(tp2_r=2.4)


def test_multiple_independent_risk_reasons_are_reported() -> None:
    decision = _evaluate(risk=_risk(equity=97_000.0, entries_today=3))
    codes = {rejection.code for rejection in decision.rejections}
    assert RejectionCode.RISK_DAILY_LOSS in codes
    assert RejectionCode.RISK_DAILY_ENTRIES in codes
    assert len(decision.rejections) >= 2
    assert len(decision.score.factors) == 6


def test_duplicate_signal_and_restart_state() -> None:
    risk = _risk(processed_trigger_times=frozenset({DECISION_TIME}))
    decision = _evaluate(risk=risk)
    assert decision.rejections[0].code is RejectionCode.DUPLICATE_SIGNAL
    assert decision.candidate is None


def test_symbol_substitution_is_rejected() -> None:
    d1, h4, m15, _ = _fixture()
    wrong = [replace(item, symbol="GC") for item in d1]
    assert _evaluate(d1=wrong).rejections[0].code is RejectionCode.INVALID_DATA
