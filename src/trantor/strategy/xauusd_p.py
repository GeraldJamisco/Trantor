"""Deterministic research strategy for the approved XAUUSD-P rules.

This module implements Phase D of the approved Phase C specification. It is
research-only: it never creates an order, sizes a monetary position, imports the
execution package, or contacts a broker. A valid decision may expose a
``TradeSignal`` for later risk/execution layers, but execution readiness is
always false until contract economics, costs, sessions, and news are verified.

The existing single-sequence ``Strategy`` protocol cannot carry synchronized D1,
H4, and M15 context. This module therefore exposes ``evaluate(d1, h4, m15, ...)``
for research decisions and ``to_trade_signal()`` on the result so valid
candidates still conform to the existing ``TradeSignal`` boundary.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo

from ..market_data import XAUUSD_P_INSTRUMENT
from ..models import MarketInstrument, MarketObservation, Side, TradeSignal

__all__ = [
    "BiasState",
    "Confidence",
    "ConfluenceScore",
    "DailyBias",
    "DecisionStatus",
    "H4Trend",
    "NewsContext",
    "NewsEvaluation",
    "NewsEvent",
    "Pivot",
    "PivotKind",
    "Rejection",
    "RejectionCode",
    "ResearchPositionState",
    "ResearchRiskState",
    "RiskDecision",
    "ScoreFactor",
    "SessionEvaluation",
    "SessionFilterMode",
    "StrategyDecision",
    "StrategyIdentity",
    "TradePlan",
    "TradeStatus",
    "TrendState",
    "XAUUSDPMTFParameters",
    "XAUUSDPMultiTimeframeStrategy",
    "XAUUSDPSignalCandidate",
    "Zone",
    "ZoneCluster",
    "ZoneKind",
]

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
_TIMEFRAME_SECONDS = {"1d": 86_400, "4h": 14_400, "15m": 900}
_SYMBOL = XAUUSD_P_INSTRUMENT.symbol
_STRATEGY_NAME = "xauusd_p_multi_timeframe"
_STRATEGY_VERSION = "1.0.0"
_APPROVED_VARIANT_ID = "xauusd_p_approved_v1"
_APPROVED_VARIANT_NAME = "approved"
_BOUNDARY_EPSILON = 1e-12


def _require_aware(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _duration(timeframe: str) -> timedelta:
    try:
        return timedelta(seconds=_TIMEFRAME_SECONDS[timeframe])
    except KeyError:
        raise ValueError(f"unsupported timeframe {timeframe!r}") from None


def _close_time(observation: MarketObservation, timeframe: str) -> datetime:
    return observation.observed_at.astimezone(UTC) + _duration(timeframe)


def _ensure_positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


@dataclass(frozen=True, slots=True)
class StrategyIdentity:
    """Immutable strategy identity used in research records."""

    strategy_name: str
    strategy_version: str
    variant_id: str
    variant_name: str
    research_only: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_name",
            "strategy_version",
            "variant_id",
            "variant_name",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.research_only, bool):
            raise ValueError("research_only must be a boolean")


class BiasState(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNAVAILABLE = "unavailable"


class TrendState(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNAVAILABLE = "unavailable"


class PivotKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class ZoneKind(StrEnum):
    SUPPORT = "support"
    RESISTANCE = "resistance"
    FIBONACCI = "fibonacci"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    REJECT = "reject"


class DecisionStatus(StrEnum):
    CANDIDATE = "candidate"
    REJECTED = "rejected"


class TradeStatus(StrEnum):
    OPEN = "open"
    TP1_FILLED = "tp1_filled"
    PARTIAL = "partial"
    TP2_CLOSED = "tp2_closed"
    STOPPED = "stopped"
    BREAKEVEN_STOPPED = "breakeven_stopped"


class RejectionCode(StrEnum):
    INVALID_DATA = "invalid_data"
    D1_UNAVAILABLE = "d1_unavailable"
    D1_STALE = "d1_stale"
    D1_WARMUP = "d1_warmup"
    D1_CONFLICT = "d1_conflict"
    H4_UNAVAILABLE = "h4_unavailable"
    H4_STALE = "h4_stale"
    H4_NEUTRAL = "h4_neutral"
    H4_CONFLICT = "h4_conflict"
    H4_WARMUP = "h4_warmup"
    M15_TRIGGER_MISSING = "m15_trigger_missing"
    M15_GAP = "m15_gap"
    M15_WARMUP = "m15_warmup"
    NEXT_BAR_MISSING = "next_bar_missing"
    NO_H4_ZONE = "no_h4_zone"
    NO_EVIDENCE = "no_evidence"
    SCORE_BELOW_THRESHOLD = "score_below_threshold"
    NEWS_UNAVAILABLE = "news_unavailable"
    NEWS_EVENT_WINDOW = "news_event_window"
    SESSION_OUTSIDE = "session_outside"
    SESSION_UNVERIFIED = "session_unverified"
    RISK_UNAVAILABLE = "risk_unavailable"
    RISK_DAILY_LOSS = "risk_daily_loss"
    RISK_DAILY_ENTRIES = "risk_daily_entries"
    RISK_CONCURRENT = "risk_concurrent"
    RISK_COOLDOWN = "risk_cooldown"
    DUPLICATE_SIGNAL = "duplicate_signal"
    STOP_UNAVAILABLE = "stop_unavailable"
    STOP_INVALID = "stop_invalid"
    STOP_TOO_WIDE = "stop_too_wide"
    TARGET_OBSTACLE = "target_obstacle"
    TARGET_TOO_CLOSE = "target_too_close"
    ENTRY_CHASED = "entry_chased"


def _default_london_start() -> time:
    return time(8, 0)


def _default_london_end() -> time:
    return time(16, 30)


def _default_new_york_start() -> time:
    return time(8, 0)


def _default_new_york_end() -> time:
    return time(17, 0)


@dataclass(frozen=True, slots=True)
class XAUUSDPMTFParameters:
    """Approved Phase C parameters.

    All values are starting policy values from the approved specification, not
    claimed empirical optima.
    """

    daily_ema_fast: int = 50
    daily_ema_slow: int = 200
    daily_ema_tolerance_pct: float = 0.0005
    daily_min_bars: int = 201
    d1_max_staleness_days: float = 5.0
    h4_pivot_left: int = 2
    h4_pivot_right: int = 2
    h4_atr_period: int = 14
    h4_max_staleness_hours: float = 72.0
    sr_merge_atr: float = 0.50
    sr_zone_half_width_atr: float = 0.25
    zone_invalidation_atr: float = 0.25
    zone_max_age_h4: int = 120
    fib_levels: tuple[float, float] = (0.5, 0.618)
    fib_band_atr: float = 0.25
    fib_min_leg_atr: float = 1.0
    fib_max_age_h4: int = 120
    m15_rsi_period: int = 14
    m15_rsi_long_threshold: float = 30.0
    m15_rsi_short_threshold: float = 70.0
    m15_rsi_window: int = 1
    engulf_min_body_fraction: float = 0.20
    pin_wick_body_ratio: float = 2.0
    pin_body_range_max: float = 0.35
    pin_close_position: float = 0.50
    volume_lookback_m15: int = 20
    volume_multiplier: float = 1.50
    m15_contiguous_bars: int = 21
    score_reject_below: int = 5
    score_high_confidence: int = 7
    risk_high_confidence: float = 0.010
    risk_medium_confidence: float = 0.005
    daily_loss_limit: float = 0.030
    max_daily_entries: int = 3
    max_concurrent_positions: int = 2
    cooldown_after_loss_m15: int = 16
    cooldown_after_two_losses_m15: int = 32
    stop_buffer_atr: float = 0.25
    max_stop_atr: float = 3.0
    min_stop_ticks: int = 1
    tp1_r: float = 2.0
    tp2_r: float = 3.0
    min_tp2_r: float = 2.5
    target_obstacle_buffer_atr: float = 0.25
    max_entry_distance_atr: float = 0.50
    news_pre_minutes: int = 30
    news_post_minutes: int = 30
    news_score_window_minutes: int = 60
    news_calendar_max_age_minutes: int = 15
    london_start: time = field(default_factory=_default_london_start)
    london_end: time = field(default_factory=_default_london_end)
    new_york_start: time = field(default_factory=_default_new_york_start)
    new_york_end: time = field(default_factory=_default_new_york_end)
    tick_size: float = 0.01

    def __post_init__(self) -> None:
        for name in (
            "daily_ema_fast",
            "daily_ema_slow",
            "daily_min_bars",
            "h4_pivot_left",
            "h4_pivot_right",
            "h4_atr_period",
            "zone_max_age_h4",
            "fib_max_age_h4",
            "m15_rsi_period",
            "m15_rsi_window",
            "volume_lookback_m15",
            "m15_contiguous_bars",
            "score_reject_below",
            "score_high_confidence",
            "max_daily_entries",
            "max_concurrent_positions",
            "cooldown_after_loss_m15",
            "cooldown_after_two_losses_m15",
            "min_stop_ticks",
            "news_pre_minutes",
            "news_post_minutes",
            "news_score_window_minutes",
            "news_calendar_max_age_minutes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "daily_ema_tolerance_pct",
            "d1_max_staleness_days",
            "h4_max_staleness_hours",
            "sr_merge_atr",
            "sr_zone_half_width_atr",
            "zone_invalidation_atr",
            "fib_band_atr",
            "fib_min_leg_atr",
            "m15_rsi_long_threshold",
            "m15_rsi_short_threshold",
            "engulf_min_body_fraction",
            "pin_wick_body_ratio",
            "pin_body_range_max",
            "pin_close_position",
            "volume_multiplier",
            "risk_high_confidence",
            "risk_medium_confidence",
            "daily_loss_limit",
            "stop_buffer_atr",
            "max_stop_atr",
            "tp1_r",
            "tp2_r",
            "min_tp2_r",
            "target_obstacle_buffer_atr",
            "max_entry_distance_atr",
            "tick_size",
        ):
            _ensure_positive(getattr(self, name), name)
        if self.daily_ema_fast >= self.daily_ema_slow:
            raise ValueError("daily_ema_fast must be less than daily_ema_slow")
        if len(self.fib_levels) != 2 or any(
            not 0 < float(level) < 1 for level in self.fib_levels
        ):
            raise ValueError("fib_levels must contain two values between 0 and 1")
        if self.min_tp2_r <= self.tp1_r:
            raise ValueError("min_tp2_r must exceed tp1_r")
        if self.tp2_r < self.min_tp2_r:
            raise ValueError("tp2_r must be at least min_tp2_r")
        if self.m15_rsi_window != 1:
            raise ValueError("m15_rsi_window is fixed at 1 by the approved trigger-candle rule")
        if self.risk_high_confidence < self.risk_medium_confidence:
            raise ValueError("high-confidence risk must not be below medium risk")
        if self.london_start >= self.london_end:
            raise ValueError("London session start must be before end")
        if self.new_york_start >= self.new_york_end:
            raise ValueError("New York session start must be before end")


@dataclass(frozen=True, slots=True)
class Pivot:
    index: int
    kind: PivotKind
    price: float
    confirmed_at: datetime
    confirmed_index: int


@dataclass(frozen=True, slots=True)
class DailyBias:
    state: BiasState
    ema_fast: float | None
    ema_slow: float | None
    tolerance: float | None
    latest_bar_at: datetime | None
    previous_high: float | None
    previous_low: float | None
    bar_count: int


@dataclass(frozen=True, slots=True)
class H4Trend:
    state: TrendState
    atr: float | None
    pivots: tuple[Pivot, ...]
    ph1: float | None
    ph2: float | None
    pl1: float | None
    pl2: float | None
    latest_bar_at: datetime | None
    bar_count: int


@dataclass(frozen=True, slots=True)
class Zone:
    kind: ZoneKind
    lower: float
    upper: float
    center: float
    confirmed_at: datetime
    confirmed_index: int
    evidence: str
    direction: Side | None = None


@dataclass(frozen=True, slots=True)
class ZoneCluster:
    zones: tuple[Zone, ...]
    lower: float
    upper: float
    center: float
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NewsEvent:
    event_type: str
    currency: str
    impact: str
    scheduled_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("event_type", "currency", "impact"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        _require_aware(self.scheduled_at, "scheduled_at")


@dataclass(frozen=True, slots=True)
class NewsContext:
    verified: bool = False
    complete: bool = False
    as_of: datetime | None = None
    events: tuple[NewsEvent, ...] = ()
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.verified, bool) or not isinstance(self.complete, bool):
            raise ValueError("verified and complete must be booleans")
        if self.as_of is not None:
            _require_aware(self.as_of, "as_of")
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, NewsEvent) for event in self.events
        ):
            raise ValueError("events must be a tuple of NewsEvent values")
        if not isinstance(self.source, str):
            raise ValueError("source must be a string")

    @classmethod
    def unavailable(cls) -> "NewsContext":
        return cls(verified=False, complete=False, as_of=None, events=(), source="unavailable")


@dataclass(frozen=True, slots=True)
class NewsEvaluation:
    available: bool
    safe: bool
    point_awarded: bool
    unaware: bool
    reason: str


class SessionFilterMode(StrEnum):
    APPROVED = "approved"
    RESEARCH_ISOLATED = "research_isolated"


@dataclass(frozen=True, slots=True)
class SessionEvaluation:
    in_overlap: bool
    calendar_verified: bool
    research_allowed: bool
    reason: str
    filter_mode: SessionFilterMode = SessionFilterMode.APPROVED


@dataclass(frozen=True, slots=True)
class ResearchRiskState:
    equity: float
    start_of_day_equity: float
    open_positions: int = 0
    entries_today: int = 0
    consecutive_losses: int = 0
    last_exit_at: datetime | None = None
    cooldown_until: datetime | None = None
    daily_locked_out: bool = False
    processed_trigger_times: frozenset[datetime] = frozenset()

    def __post_init__(self) -> None:
        _ensure_positive(self.equity, "equity")
        _ensure_positive(self.start_of_day_equity, "start_of_day_equity")
        for field_name in ("open_positions", "entries_today", "consecutive_losses"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.last_exit_at is not None:
            _require_aware(self.last_exit_at, "last_exit_at")
        if self.cooldown_until is not None:
            _require_aware(self.cooldown_until, "cooldown_until")
        if not isinstance(self.daily_locked_out, bool):
            raise ValueError("daily_locked_out must be a boolean")
        if not isinstance(self.processed_trigger_times, frozenset):
            raise ValueError("processed_trigger_times must be a frozenset")
        for item in self.processed_trigger_times:
            _require_aware(item, "processed_trigger_times item")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    eligible: bool
    risk_fraction: float
    daily_loss_fraction: float
    sizing_status: str
    reasons: tuple[str, ...] = ()
    cost_adjusted: bool = False


@dataclass(frozen=True, slots=True)
class ScoreFactor:
    name: str
    points: int
    awarded: bool
    available: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ConfluenceScore:
    factors: tuple[ScoreFactor, ...]
    total: int
    confidence: Confidence

    @property
    def maximum(self) -> int:
        return 8


@dataclass(frozen=True, slots=True)
class TradePlan:
    entry: float
    stop: float
    tp1: float
    tp2: float
    risk_distance: float
    tp1_fraction: float = 0.5
    tp2_fraction: float = 0.5
    breakeven: float | None = None
    breakeven_costs_verified: bool = False
    monetary_sizing_status: str = "blocked_unverified_contract_economics"
    partial_exit_status: str = "blocked_pending_volume_sizing"

    def __post_init__(self) -> None:
        for name in ("entry", "stop", "tp1", "tp2", "risk_distance"):
            _ensure_positive(getattr(self, name), name)
        if self.risk_distance <= 0:
            raise ValueError("risk_distance must be positive")
        if self.tp1_fraction + self.tp2_fraction != 1.0:
            raise ValueError("partial fractions must sum to 1.0")
        if self.breakeven is not None:
            _ensure_positive(self.breakeven, "breakeven")


@dataclass(frozen=True, slots=True)
class Rejection:
    code: RejectionCode
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, RejectionCode):
            raise ValueError("code must be a RejectionCode")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string")


@dataclass(frozen=True, slots=True)
class XAUUSDPSignalCandidate:
    instrument: MarketInstrument
    side: Side
    decision_time: datetime
    entry_time: datetime
    entry_reference: float
    zone: ZoneCluster
    plan: TradePlan
    confidence: Confidence
    score: ConfluenceScore
    daily_bias: DailyBias
    h4_trend: H4Trend
    news: NewsEvaluation
    session: SessionEvaluation
    risk: RiskDecision
    research_mode: bool
    news_unaware: bool
    rationale: str
    execution_ready: bool = False

    @property
    def signal_timestamp(self) -> datetime:
        """The completed-candle decision time represented by the signal."""

        return self.decision_time

    def to_trade_signal(self) -> TradeSignal:
        return TradeSignal(
            signal_id=(
                f"{self.instrument.symbol.lower()}-{self.side.value}-"
                f"{self.decision_time.strftime('%Y%m%dT%H%M%SZ')}"
            ),
            strategy_name=_STRATEGY_NAME,
            strategy_version=_STRATEGY_VERSION,
            symbol=self.instrument.symbol,
            side=self.side,
            timeframe="15m",
            generated_at=self.decision_time,
            reference_price=self.entry_reference,
            stop_loss=self.plan.stop,
            take_profit=self.plan.tp1,
            rationale=self.rationale,
        )


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    instrument: MarketInstrument
    decision_time: datetime
    status: DecisionStatus
    candidate: XAUUSDPSignalCandidate | None
    rejections: tuple[Rejection, ...]
    score: ConfluenceScore
    daily_bias: DailyBias | None
    h4_trend: H4Trend | None
    zone: ZoneCluster | None
    news: NewsEvaluation
    session: SessionEvaluation
    risk: RiskDecision | None
    research_mode: bool
    news_unaware: bool
    execution_ready: bool = False
    variant_id: str = _APPROVED_VARIANT_ID

    def to_trade_signal(self) -> TradeSignal | None:
        if self.candidate is None:
            return None
        return self.candidate.to_trade_signal()


@dataclass(frozen=True, slots=True)
class ResearchPositionState:
    remaining_fraction: float = 1.0
    tp1_filled_fraction: float = 0.0
    status: TradeStatus = TradeStatus.OPEN
    breakeven_active: bool = False

    def __post_init__(self) -> None:
        for name in ("remaining_fraction", "tp1_filled_fraction"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not isinstance(self.status, TradeStatus):
            raise ValueError("status must be a TradeStatus")
        if not isinstance(self.breakeven_active, bool):
            raise ValueError("breakeven_active must be a boolean")

    def on_tp1_filled(self, fraction: float = 0.5) -> "ResearchPositionState":
        amount = _fraction(fraction)
        if amount > self.remaining_fraction + 1e-9:
            raise ValueError("TP1 fill exceeds remaining position")
        if self.tp1_filled_fraction + amount > 0.5 + 1e-9:
            raise ValueError("TP1 cumulative fill cannot exceed 50% of the original position")
        new_tp1 = self.tp1_filled_fraction + amount
        remaining = max(0.0, self.remaining_fraction - amount)
        breakeven = new_tp1 >= 0.5 - 1e-9
        status = TradeStatus.TP1_FILLED if breakeven else TradeStatus.PARTIAL
        return ResearchPositionState(
            remaining_fraction=remaining,
            tp1_filled_fraction=new_tp1,
            status=status,
            breakeven_active=breakeven,
        )

    def on_tp2_filled(self, fraction: float = 0.5) -> "ResearchPositionState":
        if self.tp1_filled_fraction < 0.5 - 1e-9:
            raise ValueError("TP2 cannot fill before the required TP1 fraction")
        amount = _fraction(fraction)
        if amount > self.remaining_fraction + 1e-9:
            raise ValueError("TP2 fill exceeds remaining position")
        remaining = max(0.0, self.remaining_fraction - amount)
        return ResearchPositionState(
            remaining_fraction=remaining,
            tp1_filled_fraction=self.tp1_filled_fraction,
            status=TradeStatus.TP2_CLOSED if remaining <= 1e-9 else TradeStatus.PARTIAL,
            breakeven_active=self.breakeven_active,
        )

    def on_stop_filled(self) -> "ResearchPositionState":
        return ResearchPositionState(
            remaining_fraction=0.0,
            tp1_filled_fraction=self.tp1_filled_fraction,
            status=(
                TradeStatus.BREAKEVEN_STOPPED
                if self.breakeven_active
                else TradeStatus.STOPPED
            ),
            breakeven_active=self.breakeven_active,
        )


def _fraction(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("fraction must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 1:
        raise ValueError("fraction must be greater than 0 and at most 1")
    return number


def _ema_series(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    k = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        result[index] = values[index] * k + result[index - 1] * (1.0 - k)  # type: ignore[operator]
    return tuple(result)


def _atr_series(
    observations: Sequence[MarketObservation],
    period: int,
) -> tuple[float | None, ...]:
    if len(observations) < period:
        return tuple(None for _ in observations)
    true_ranges: list[float] = []
    previous_close = observations[0].close
    for index, observation in enumerate(observations):
        if index == 0:
            tr = observation.high - observation.low
        else:
            tr = max(
                observation.high - observation.low,
                abs(observation.high - previous_close),
                abs(observation.low - previous_close),
            )
        true_ranges.append(tr)
        previous_close = observation.close
    result: list[float | None] = [None] * len(observations)
    seed = sum(true_ranges[:period]) / period
    result[period - 1] = seed
    for index in range(period, len(observations)):
        result[index] = (result[index - 1] * (period - 1) + true_ranges[index]) / period  # type: ignore[operator]
    return tuple(result)


def _rsi_series(closes: Sequence[float], period: int) -> tuple[float | None, ...]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return tuple(result)
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, period + 1):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = _rsi_value(avg_gain, avg_loss)
    for index in range(period + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[index] = _rsi_value(avg_gain, avg_loss)
    return tuple(result)


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _pivots(
    observations: Sequence[MarketObservation],
    left: int,
    right: int,
) -> tuple[Pivot, ...]:
    pivots: list[Pivot] = []
    for index in range(left, len(observations) - right):
        high = observations[index].high
        low = observations[index].low
        if all(
            high > observations[index - offset].high for offset in range(1, left + 1)
        ) and all(
            high > observations[index + offset].high for offset in range(1, right + 1)
        ):
            pivots.append(
                Pivot(
                    index=index,
                    kind=PivotKind.HIGH,
                    price=high,
                    confirmed_at=_close_time(observations[index + right], "4h"),
                    confirmed_index=index + right,
                )
            )
        if all(
            low < observations[index - offset].low for offset in range(1, left + 1)
        ) and all(
            low < observations[index + offset].low for offset in range(1, right + 1)
        ):
            pivots.append(
                Pivot(
                    index=index,
                    kind=PivotKind.LOW,
                    price=low,
                    confirmed_at=_close_time(observations[index + right], "4h"),
                    confirmed_index=index + right,
                )
            )
    return tuple(pivots)


def _h4_trend(
    observations: Sequence[MarketObservation],
    parameters: XAUUSDPMTFParameters,
) -> H4Trend:
    if len(observations) < max(parameters.h4_atr_period, 4):
        return H4Trend(TrendState.UNAVAILABLE, None, (), None, None, None, None, None, len(observations))
    atr_values = _atr_series(observations, parameters.h4_atr_period)
    atr = atr_values[-1]
    if atr is None or atr <= 0:
        return H4Trend(TrendState.UNAVAILABLE, None, (), None, None, None, None, observations[-1].observed_at, len(observations))
    pivots = _pivots(observations, parameters.h4_pivot_left, parameters.h4_pivot_right)
    highs = [pivot for pivot in pivots if pivot.kind is PivotKind.HIGH]
    lows = [pivot for pivot in pivots if pivot.kind is PivotKind.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return H4Trend(TrendState.UNAVAILABLE, atr, pivots, None, None, None, None, observations[-1].observed_at, len(observations))
    # Latest two are ordered by pivot detection index; use the two most recent by index.
    recent_highs = sorted(highs, key=lambda item: item.index)[-2:]
    recent_lows = sorted(lows, key=lambda item: item.index)[-2:]
    # PL1/PH1 are the most recent confirmed pivots; PL2/PH2 are previous.
    ph1, ph2 = recent_highs[1].price, recent_highs[0].price
    pl1, pl2 = recent_lows[1].price, recent_lows[0].price
    if ph1 > ph2 and pl1 > pl2:
        state = TrendState.BULLISH
    elif ph1 < ph2 and pl1 < pl2:
        state = TrendState.BEARISH
    else:
        state = TrendState.NEUTRAL
    return H4Trend(
        state=state,
        atr=atr,
        pivots=pivots,
        ph1=ph1,
        ph2=ph2,
        pl1=pl1,
        pl2=pl2,
        latest_bar_at=observations[-1].observed_at,
        bar_count=len(observations),
    )


def _build_sr_zones(
    observations: Sequence[MarketObservation],
    trend: H4Trend,
    parameters: XAUUSDPMTFParameters,
) -> tuple[Zone, ...]:
    if trend.atr is None or not trend.pivots:
        return ()
    atr = trend.atr
    latest_index = len(observations) - 1
    merge_distance = parameters.sr_merge_atr * atr
    half_width = parameters.sr_zone_half_width_atr * atr
    invalidation = parameters.zone_invalidation_atr * atr
    clusters: list[dict[str, object]] = []
    for pivot in sorted(trend.pivots, key=lambda item: (item.confirmed_index, item.index)):
        kind = ZoneKind.RESISTANCE if pivot.kind is PivotKind.HIGH else ZoneKind.SUPPORT
        matching = [
            cluster
            for cluster in clusters
            if cluster["kind"] is kind
            and abs(float(cluster["center"]) - pivot.price) <= merge_distance
        ]
        if matching:
            members = [
                member
                for cluster in matching
                for member in cluster["members"]  # type: ignore[union-attr]
            ]
            members.append(pivot)
            for cluster in matching:
                clusters.remove(cluster)
            center = sum(member.price for member in members) / len(members)
            clusters.append(
                {
                    "kind": kind,
                    "center": center,
                    "members": members,
                }
            )
        else:
            clusters.append(
                {
                    "kind": kind,
                    "center": pivot.price,
                    "members": [pivot],
                }
            )

    zones: list[Zone] = []
    for cluster in clusters:
        members = cluster["members"]  # type: ignore[assignment]
        center = sum(member.price for member in members) / len(members)
        # The approved merged-zone definition is center +/- the configured
        # ATR-based half-width. Do not widen the result to the constituent
        # boundaries.
        lower = center - half_width
        upper = center + half_width
        confirmed_index = max(member.confirmed_index for member in members)
        confirmed_at = max(member.confirmed_at for member in members)
        if latest_index - confirmed_index > parameters.zone_max_age_h4:
            continue
        invalid = False
        for observation in observations[confirmed_index + 1 :]:
            if cluster["kind"] is ZoneKind.SUPPORT:
                if observation.close < lower - invalidation:
                    invalid = True
                    break
            elif observation.close > upper + invalidation:
                invalid = True
                break
        if invalid:
            continue
        zones.append(
            Zone(
                kind=cluster["kind"],  # type: ignore[arg-type]
                lower=lower,
                upper=upper,
                center=center,
                confirmed_at=confirmed_at,
                confirmed_index=confirmed_index,
                evidence=f"{cluster['kind'].value} pivot cluster ({len(members)} pivot(s))",
            )
        )
    return tuple(zones)


def _build_fib_zone(
    observations: Sequence[MarketObservation],
    trend: H4Trend,
    parameters: XAUUSDPMTFParameters,
) -> Zone | None:
    if trend.atr is None or trend.state not in (TrendState.BULLISH, TrendState.BEARISH):
        return None
    highs = sorted(
        [pivot for pivot in trend.pivots if pivot.kind is PivotKind.HIGH],
        key=lambda item: item.index,
    )
    lows = sorted(
        [pivot for pivot in trend.pivots if pivot.kind is PivotKind.LOW],
        key=lambda item: item.index,
    )
    if not highs or not lows:
        return None
    if trend.state is TrendState.BULLISH:
        high = highs[-1]
        candidates = [low for low in lows if low.index < high.index]
        if not candidates:
            return None
        low = candidates[-1]
        direction = Side.BUY
        if high.price - low.price < parameters.fib_min_leg_atr * trend.atr:
            return None
        levels = (
            high.price - parameters.fib_levels[0] * (high.price - low.price),
            high.price - parameters.fib_levels[1] * (high.price - low.price),
        )
        invalid_level = low.price - parameters.zone_invalidation_atr * trend.atr
        if observations[-1].close < invalid_level:
            return None
    else:
        low = lows[-1]
        candidates = [high for high in highs if high.index < low.index]
        if not candidates:
            return None
        high = candidates[-1]
        direction = Side.SELL
        if high.price - low.price < parameters.fib_min_leg_atr * trend.atr:
            return None
        levels = (
            low.price + parameters.fib_levels[0] * (high.price - low.price),
            low.price + parameters.fib_levels[1] * (high.price - low.price),
        )
        invalid_level = high.price + parameters.zone_invalidation_atr * trend.atr
        if observations[-1].close > invalid_level:
            return None

    lower = min(levels) - parameters.fib_band_atr * trend.atr
    upper = max(levels) + parameters.fib_band_atr * trend.atr
    confirmed_index = max(high.confirmed_index, low.confirmed_index)
    if len(observations) - 1 - confirmed_index > parameters.fib_max_age_h4:
        return None
    return Zone(
        kind=ZoneKind.FIBONACCI,
        lower=lower,
        upper=upper,
        center=(lower + upper) / 2.0,
        confirmed_at=max(high.confirmed_at, low.confirmed_at),
        confirmed_index=confirmed_index,
        evidence=f"Fibonacci 0.5/0.618 from confirmed {direction.value} leg",
        direction=direction,
    )


def _zone_clusters(zones: Sequence[Zone]) -> tuple[ZoneCluster, ...]:
    clusters: list[list[Zone]] = []
    for zone in sorted(zones, key=lambda item: (item.lower, item.upper, item.kind.value)):
        matching = [
            cluster
            for cluster in clusters
            if any(
                zone.lower <= existing.upper and zone.upper >= existing.lower
                for existing in cluster
            )
        ]
        if matching:
            for cluster in matching:
                clusters.remove(cluster)
            merged = [item for cluster in matching for item in cluster]
            merged.append(zone)
            clusters.append(merged)
        else:
            clusters.append([zone])
    result: list[ZoneCluster] = []
    for cluster in clusters:
        lower = min(zone.lower for zone in cluster)
        upper = max(zone.upper for zone in cluster)
        result.append(
            ZoneCluster(
                zones=tuple(cluster),
                lower=lower,
                upper=upper,
                center=(lower + upper) / 2.0,
                evidence=tuple(zone.evidence for zone in cluster),
            )
        )
    return tuple(result)


def _zone_eligible_for_side(zone: Zone, side: Side) -> bool:
    if zone.kind is ZoneKind.SUPPORT:
        return side is Side.BUY
    if zone.kind is ZoneKind.RESISTANCE:
        return side is Side.SELL
    if zone.kind is ZoneKind.FIBONACCI:
        return zone.direction is side
    return False


def _zone_intersects_trigger(zone: Zone, trigger: MarketObservation) -> bool:
    return trigger.low <= zone.upper and trigger.high >= zone.lower


def _cluster_has_eligible_intersection(
    cluster: ZoneCluster,
    trigger: MarketObservation,
    side: Side,
) -> bool:
    return any(
        _zone_eligible_for_side(zone, side) and _zone_intersects_trigger(zone, trigger)
        for zone in cluster.zones
    )


def _select_zone(
    clusters: Sequence[ZoneCluster],
    trigger: MarketObservation,
    side: Side,
) -> ZoneCluster | None:
    candidates = [
        cluster
        for cluster in clusters
        if _cluster_has_eligible_intersection(cluster, trigger, side)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda cluster: (
            abs(cluster.center - trigger.close),
            cluster.center,
            cluster.lower,
            cluster.upper,
        ),
    )


def _pattern_flags(trigger: MarketObservation, previous: MarketObservation) -> dict[str, bool]:
    prev_body = abs(previous.close - previous.open)
    body = abs(trigger.close - trigger.open)
    range_ = trigger.high - trigger.low
    lower_wick = min(trigger.open, trigger.close) - trigger.low
    upper_wick = trigger.high - max(trigger.open, trigger.close)
    bull_engulf = (
        previous.close < previous.open
        and trigger.close > trigger.open
        and trigger.open <= previous.close
        and trigger.close >= previous.open
        and body + _BOUNDARY_EPSILON >= 0.20 * range_
        and range_ > 0
    )
    bear_engulf = (
        previous.close > previous.open
        and trigger.close < trigger.open
        and trigger.open >= previous.close
        and trigger.close <= previous.open
        and body + _BOUNDARY_EPSILON >= 0.20 * range_
        and range_ > 0
    )
    bull_pin = (
        range_ > 0
        and body > 0
        and lower_wick + _BOUNDARY_EPSILON >= 2.0 * body
        and body <= 0.35 * range_ + _BOUNDARY_EPSILON
        and trigger.close + _BOUNDARY_EPSILON >= trigger.low + 0.50 * range_
        and upper_wick <= 0.35 * range_ + _BOUNDARY_EPSILON
    )
    bear_pin = (
        range_ > 0
        and body > 0
        and upper_wick + _BOUNDARY_EPSILON >= 2.0 * body
        and body <= 0.35 * range_ + _BOUNDARY_EPSILON
        and trigger.close <= trigger.high - 0.50 * range_ + _BOUNDARY_EPSILON
        and lower_wick <= 0.35 * range_ + _BOUNDARY_EPSILON
    )
    return {
        "bullish_engulfing": bool(bull_engulf and prev_body > 0),
        "bearish_engulfing": bool(bear_engulf and prev_body > 0),
        "bullish_pin": bool(bull_pin),
        "bearish_pin": bool(bear_pin),
    }


def _volume_available(
    observations: Sequence[MarketObservation],
    parameters: XAUUSDPMTFParameters,
) -> bool:
    if len(observations) < parameters.volume_lookback_m15 + 1:
        return False
    trigger = observations[-1]
    prior = observations[-(parameters.volume_lookback_m15 + 1) : -1]
    if trigger.volume is None or not math.isfinite(trigger.volume) or trigger.volume <= 0:
        return False
    values = [item.volume for item in prior]
    if any(value is None or not math.isfinite(value) or value < 0 for value in values):
        return False
    return statistics.median(values) > 0


def _volume_confirmed(
    observations: Sequence[MarketObservation],
    parameters: XAUUSDPMTFParameters,
) -> bool:
    if not _volume_available(observations, parameters):
        return False
    trigger = observations[-1]
    prior = observations[-(parameters.volume_lookback_m15 + 1) : -1]
    baseline = statistics.median([item.volume for item in prior])  # type: ignore[arg-type]
    return bool(trigger.volume >= parameters.volume_multiplier * baseline)


def _contiguous_m15(
    observations: Sequence[MarketObservation],
    parameters: XAUUSDPMTFParameters,
) -> bool:
    if len(observations) < parameters.m15_contiguous_bars:
        return False
    window = observations[-parameters.m15_contiguous_bars :]
    return all(
        _duration("15m")
        == current.observed_at - previous.observed_at
        for previous, current in zip(window, window[1:])
    )


def _session_overlap(
    decision_time: datetime,
    entry_time: datetime,
    parameters: XAUUSDPMTFParameters,
) -> tuple[bool, bool]:
    def window(zone: ZoneInfo, start: time, end: time, at: datetime) -> tuple[datetime, datetime]:
        local_date = at.astimezone(zone).date()
        return (
            datetime.combine(local_date, start, tzinfo=zone).astimezone(UTC),
            datetime.combine(local_date, end, tzinfo=zone).astimezone(UTC),
        )

    london_start, london_end = window(LONDON, parameters.london_start, parameters.london_end, decision_time)
    ny_start, ny_end = window(NEW_YORK, parameters.new_york_start, parameters.new_york_end, decision_time)
    start = max(london_start, ny_start)
    end = min(london_end, ny_end)
    return (start <= decision_time < end, start <= entry_time < end)


def _news_evaluation(
    context: NewsContext,
    decision_time: datetime,
    parameters: XAUUSDPMTFParameters,
    *,
    entry_time: datetime | None = None,
    research_mode: bool,
    news_unaware: bool,
) -> NewsEvaluation:
    if entry_time is not None:
        entry_time = _require_aware(entry_time, "entry_time")
    age_limit = timedelta(minutes=parameters.news_calendar_max_age_minutes)
    if (
        not context.verified
        or not context.complete
        or context.as_of is None
        or context.as_of > decision_time
        or decision_time - context.as_of > age_limit
    ):
        if research_mode and news_unaware:
            return NewsEvaluation(
                available=False,
                safe=False,
                point_awarded=False,
                unaware=True,
                reason="Calendar unavailable; research news-unaware mode does not claim news compliance.",
            )
        return NewsEvaluation(
            available=False,
            safe=False,
            point_awarded=False,
            unaware=False,
            reason="Calendar unavailable, stale, incomplete, or not valid at decision time.",
        )

    events = [
        event for event in context.events if _is_qualifying_news_event(event)
    ]
    pre = timedelta(minutes=parameters.news_pre_minutes)
    post = timedelta(minutes=parameters.news_post_minutes)
    score_window = timedelta(minutes=parameters.news_score_window_minutes)
    safety_reference = decision_time if entry_time is None else entry_time
    safety_events = [
        event
        for event in events
        if safety_reference - pre
        <= event.scheduled_at
        <= safety_reference + post
    ]
    if safety_events:
        return NewsEvaluation(
            available=True,
            safe=False,
            point_awarded=False,
            unaware=False,
            reason=(
                "A high-impact USD event is within the 30-minute safety window "
                "around the proposed entry."
            ),
        )
    future_events = [
        event for event in events if decision_time <= event.scheduled_at <= decision_time + score_window
    ]
    return NewsEvaluation(
        available=True,
        safe=True,
        point_awarded=not future_events,
        unaware=False,
        reason=(
            "No high-impact USD event in the next 60 minutes."
            if not future_events
            else "A high-impact USD event is within the next 60 minutes; no news point."
        ),
    )


def _is_qualifying_news_event(event: NewsEvent) -> bool:
    if event.currency.upper() != "USD" or event.impact.lower() != "high":
        return False
    normalized = event.event_type.strip().lower()
    return (
        normalized in {"nfp", "fomc", "cpi"}
        or "nonfarm" in normalized
        or "non-farm" in normalized
        or "fomc" in normalized
        or "consumer price" in normalized
        or "cpi" in normalized
    )


def _risk_decision(
    state: ResearchRiskState,
    decision_time: datetime,
    confidence: Confidence,
    parameters: XAUUSDPMTFParameters,
) -> RiskDecision:
    if decision_time in state.processed_trigger_times:
        return RiskDecision(
            eligible=False,
            risk_fraction=0.0,
            daily_loss_fraction=0.0,
            sizing_status="blocked_unverified_contract_economics",
            reasons=("This decision timestamp was already processed.",),
        )
    if confidence is Confidence.REJECT:
        return RiskDecision(
            eligible=False,
            risk_fraction=0.0,
            daily_loss_fraction=0.0,
            sizing_status="blocked_unverified_contract_economics",
            reasons=("Rejected confidence cannot receive a positive risk allowance.",),
        )
    daily_loss = max(
        0.0,
        (state.start_of_day_equity - state.equity) / state.start_of_day_equity,
    )
    reasons: list[str] = []
    if state.daily_locked_out or daily_loss >= parameters.daily_loss_limit:
        reasons.append("Daily loss limit reached.")
    if state.entries_today >= parameters.max_daily_entries:
        reasons.append("Maximum daily entries reached.")
    if state.open_positions >= parameters.max_concurrent_positions:
        reasons.append("Maximum concurrent positions reached.")
    cooldown_until = state.cooldown_until
    if state.consecutive_losses > 0:
        if state.last_exit_at is None and state.cooldown_until is None:
            reasons.append(
                "cooldown state unavailable; the loss-triggered cooldown cannot be evaluated."
            )
        else:
            if state.last_exit_at is not None:
                cooldown_bars = (
                    parameters.cooldown_after_two_losses_m15
                    if state.consecutive_losses >= 2
                    else parameters.cooldown_after_loss_m15
                )
                calculated = state.last_exit_at + _duration("15m") * cooldown_bars
                cooldown_until = (
                    calculated
                    if cooldown_until is None
                    else max(cooldown_until, calculated)
                )
            if cooldown_until is not None and cooldown_until > decision_time:
                reasons.append("Loss cooldown is active.")
    elif cooldown_until is not None and cooldown_until > decision_time:
        # A persisted cooldown can remain authoritative even if the loss
        # counter has already been reset by a caller.
        reasons.append("Loss cooldown is active.")
    risk_fraction = (
        parameters.risk_high_confidence
        if confidence is Confidence.HIGH
        else parameters.risk_medium_confidence
    )
    return RiskDecision(
        eligible=not reasons,
        risk_fraction=risk_fraction,
        daily_loss_fraction=daily_loss,
        sizing_status="blocked_unverified_contract_economics",
        reasons=tuple(reasons),
    )


def _risk_rejection_code(reason: str) -> RejectionCode:
    lowered = reason.lower()
    if "already processed" in lowered:
        return RejectionCode.DUPLICATE_SIGNAL
    if "daily loss" in lowered:
        return RejectionCode.RISK_DAILY_LOSS
    if "daily entries" in lowered:
        return RejectionCode.RISK_DAILY_ENTRIES
    if "concurrent" in lowered:
        return RejectionCode.RISK_CONCURRENT
    if "cooldown" in lowered:
        return RejectionCode.RISK_COOLDOWN
    return RejectionCode.RISK_UNAVAILABLE


def _score(
    side: Side,
    daily: DailyBias,
    zone: ZoneCluster | None,
    rsi_value: float | None,
    pattern_ok: bool,
    volume_ok: bool,
    news: NewsEvaluation,
    parameters: XAUUSDPMTFParameters,
    *,
    volume_available: bool = True,
) -> ConfluenceScore:
    factors: list[ScoreFactor] = []
    daily_aligned = (
        (side is Side.BUY and daily.state is BiasState.BULLISH)
        or (side is Side.SELL and daily.state is BiasState.BEARISH)
    )
    factors.append(
        ScoreFactor(
            "daily_alignment",
            2,
            daily_aligned,
            daily.state is not BiasState.UNAVAILABLE,
            f"Daily state={daily.state.value}",
        )
    )
    factors.append(
        ScoreFactor(
            "h4_zone",
            2,
            zone is not None,
            True,
            "Eligible H4 S/R or Fibonacci zone intersects trigger." if zone else "No eligible H4 zone.",
        )
    )
    rsi_ok = rsi_value is not None and (
        (side is Side.BUY and rsi_value < parameters.m15_rsi_long_threshold)
        or (side is Side.SELL and rsi_value > parameters.m15_rsi_short_threshold)
    )
    factors.append(
        ScoreFactor(
            "rsi",
            1,
            rsi_ok,
            rsi_value is not None,
            f"RSI={rsi_value:.4f}" if rsi_value is not None else "RSI unavailable.",
        )
    )
    factors.append(
        ScoreFactor(
            "reversal_candle",
            1,
            pattern_ok,
            True,
            "Matching reversal pattern." if pattern_ok else "No matching reversal pattern.",
        )
    )
    factors.append(
        ScoreFactor(
            "tick_volume",
            1,
            volume_ok,
            volume_available,
            "Tick-volume confirmation."
            if volume_ok
            else (
                "No tick-volume confirmation."
                if volume_available
                else "Tick volume unavailable or invalid."
            ),
        )
    )
    factors.append(
        ScoreFactor(
            "news",
            1,
            news.point_awarded,
            news.available,
            news.reason,
        )
    )
    total = sum(factor.points for factor in factors if factor.awarded)
    confidence = (
        Confidence.HIGH
        if total >= parameters.score_high_confidence
        else Confidence.MEDIUM
        if total >= parameters.score_reject_below
        else Confidence.REJECT
    )
    return ConfluenceScore(tuple(factors), total, confidence)


def _round_down(value: float, tick: float) -> float:
    return math.floor(value / tick + 1e-9) * tick


def _round_up(value: float, tick: float) -> float:
    return math.ceil(value / tick - 1e-9) * tick


def _latest_pivot(pivots: Sequence[Pivot], kind: PivotKind) -> float | None:
    items = [pivot for pivot in pivots if pivot.kind is kind]
    return items[-1].price if items else None


def _opposing_pivot(
    pivots: Sequence[Pivot],
    side: Side,
    entry: float,
) -> float | None:
    if side is Side.BUY:
        values = [pivot.price for pivot in pivots if pivot.kind is PivotKind.HIGH and pivot.price > entry]
        return min(values) if values else None
    values = [pivot.price for pivot in pivots if pivot.kind is PivotKind.LOW and pivot.price < entry]
    return max(values) if values else None


def _reject(
    *,
    decision_time: datetime,
    score: ConfluenceScore,
    daily: DailyBias | None,
    h4: H4Trend | None,
    zone: ZoneCluster | None,
    news: NewsEvaluation,
    session: SessionEvaluation,
    risk: RiskDecision | None,
    code: RejectionCode,
    message: str,
    research_mode: bool,
    news_unaware: bool,
    additional_rejections: Sequence[Rejection] = (),
) -> StrategyDecision:
    return StrategyDecision(
        instrument=XAUUSD_P_INSTRUMENT,
        decision_time=decision_time,
        status=DecisionStatus.REJECTED,
        candidate=None,
        rejections=(Rejection(code, message), *additional_rejections),
        score=score,
        daily_bias=daily,
        h4_trend=h4,
        zone=zone,
        news=news,
        session=session,
        risk=risk,
        research_mode=research_mode,
        news_unaware=news_unaware,
    )


class XAUUSDPMultiTimeframeStrategy:
    """Research-only, no-look-ahead implementation of the Phase C strategy."""

    _identity = StrategyIdentity(
        strategy_name=_STRATEGY_NAME,
        strategy_version=_STRATEGY_VERSION,
        variant_id=_APPROVED_VARIANT_ID,
        variant_name=_APPROVED_VARIANT_NAME,
    )

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    @property
    def name(self) -> str:
        return self._identity.strategy_name

    @property
    def version(self) -> str:
        return self._identity.strategy_version

    @property
    def variant_id(self) -> str:
        return self._identity.variant_id

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"identity", "_identity", "name", "version", "variant_id"}:
            raise AttributeError("strategy identity is immutable")
        super().__setattr__(name, value)

    def __init__(
        self,
        parameters: XAUUSDPMTFParameters | None = None,
        instrument: MarketInstrument = XAUUSD_P_INSTRUMENT,
    ) -> None:
        if not isinstance(instrument, MarketInstrument):
            raise TypeError("instrument must be a MarketInstrument")
        if instrument.symbol != _SYMBOL:
            raise ValueError("this strategy is bound to XAUUSD-P only")
        self.parameters = parameters or XAUUSDPMTFParameters()
        if not isinstance(self.parameters, XAUUSDPMTFParameters):
            raise TypeError("parameters must be XAUUSDPMTFParameters")
        self.instrument = instrument

    def evaluate(
        self,
        d1: Sequence[MarketObservation],
        h4: Sequence[MarketObservation],
        m15: Sequence[MarketObservation],
        *,
        decision_time: datetime,
        next_m15_bar: MarketObservation | None,
        risk_state: ResearchRiskState | None = None,
        news_context: NewsContext | None = None,
        research_mode: bool = True,
        news_unaware: bool = False,
        session_calendar_verified: bool = False,
    ) -> StrategyDecision:
        decision_at = _require_aware(decision_time, "decision_time")
        news = news_context or NewsContext.unavailable()
        if not isinstance(news, NewsContext):
            raise TypeError("news_context must be a NewsContext or None")
        if risk_state is not None and not isinstance(risk_state, ResearchRiskState):
            raise TypeError("risk_state must be a ResearchRiskState or None")
        if not isinstance(research_mode, bool) or not isinstance(news_unaware, bool):
            raise ValueError("research_mode and news_unaware must be booleans")
        if not isinstance(session_calendar_verified, bool):
            raise ValueError("session_calendar_verified must be a boolean")
        if risk_state is None:
            empty = ConfluenceScore((), 0, Confidence.REJECT)
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=None,
                h4=None,
                zone=None,
                news=_news_evaluation(
                    news,
                    decision_at,
                    self.parameters,
                    research_mode=research_mode,
                    news_unaware=news_unaware,
                ),
                session=SessionEvaluation(
                    False,
                    session_calendar_verified,
                    research_mode,
                    "risk state unavailable",
                ),
                risk=None,
                code=RejectionCode.RISK_UNAVAILABLE,
                message="Risk state is required before a candidate can be evaluated.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        try:
            d1_series = self._validate_series(d1, "1d", "d1")
            h4_series = self._validate_series(h4, "4h", "h4")
            m15_series = self._validate_series(m15, "15m", "m15")
        except ValueError as exc:
            empty = ConfluenceScore((), 0, Confidence.REJECT)
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=None,
                h4=None,
                zone=None,
                news=NewsEvaluation(False, False, False, False, str(exc)),
                session=SessionEvaluation(False, session_calendar_verified, research_mode, str(exc)),
                risk=None,
                code=RejectionCode.INVALID_DATA,
                message=str(exc),
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        d1_completed = tuple(
            item for item in d1_series if _close_time(item, "1d") <= decision_at
        )
        h4_completed = tuple(
            item for item in h4_series if _close_time(item, "4h") <= decision_at
        )
        m15_completed = tuple(
            item for item in m15_series if _close_time(item, "15m") <= decision_at
        )
        empty = ConfluenceScore((), 0, Confidence.REJECT)
        base_session = SessionEvaluation(
            in_overlap=False,
            calendar_verified=session_calendar_verified,
            research_allowed=research_mode,
            reason="not evaluated",
        )

        if not d1_completed:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=None,
                h4=None,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.D1_UNAVAILABLE,
                message="No completed D1 bars are available at the decision time.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if len(d1_completed) < self.parameters.daily_min_bars:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=None,
                h4=None,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.D1_WARMUP,
                message=f"Only {len(d1_completed)} completed D1 bars; {self.parameters.daily_min_bars} required.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        daily = self._daily_bias(d1_completed)
        if daily.state is BiasState.UNAVAILABLE:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=None,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.D1_UNAVAILABLE,
                message="D1 EMA state could not be established.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        latest_d1_close = _close_time(d1_completed[-1], "1d")
        if decision_at - latest_d1_close > timedelta(days=self.parameters.d1_max_staleness_days):
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=None,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.D1_STALE,
                message="Latest completed D1 bar is older than the allowed staleness limit.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        if not h4_completed:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=None,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.H4_UNAVAILABLE,
                message="No completed H4 bars are available at the decision time.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if decision_at - _close_time(h4_completed[-1], "4h") > timedelta(hours=self.parameters.h4_max_staleness_hours):
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=None,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.H4_STALE,
                message="Latest completed H4 bar is older than the allowed staleness limit.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        h4_state = _h4_trend(h4_completed, self.parameters)
        if h4_state.state is TrendState.UNAVAILABLE:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.H4_WARMUP,
                message="H4 trend or pivot confirmation history is insufficient.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        if not m15_completed:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.M15_TRIGGER_MISSING,
                message="No completed M15 bars are available at the decision time.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        trigger = m15_completed[-1]
        if _close_time(trigger, "15m") != decision_at:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.M15_TRIGGER_MISSING,
                message="The latest completed M15 bar does not close exactly at the decision time.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if not _contiguous_m15(m15_completed, self.parameters):
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.M15_GAP,
                message="The last 21 M15 bars are not contiguous on the 15-minute grid.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if next_m15_bar is None:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.NEXT_BAR_MISSING,
                message="The next M15 bar is required for entry reference and is unavailable.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if not isinstance(next_m15_bar, MarketObservation):
            raise TypeError("next_m15_bar must be a MarketObservation or None")
        if (
            next_m15_bar.symbol != _SYMBOL
            or next_m15_bar.timeframe != "15m"
            or next_m15_bar.observed_at != trigger.observed_at + _duration("15m")
        ):
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.NEXT_BAR_MISSING,
                message="The supplied next bar is not the immediate XAUUSD-P M15 bar.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        entry_time = next_m15_bar.observed_at
        entry = next_m15_bar.open

        if h4_state.state is TrendState.BULLISH:
            side = Side.BUY
        elif h4_state.state is TrendState.BEARISH:
            side = Side.SELL
        else:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.H4_NEUTRAL,
                message="H4 trend is neutral; the mandatory directional filter is not satisfied.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        daily_conflict = (
            side is Side.BUY and daily.state is BiasState.BEARISH
        ) or (
            side is Side.SELL and daily.state is BiasState.BULLISH
        )
        if daily_conflict:
            return _reject(
                decision_time=decision_at,
                score=empty,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=_news_evaluation(news, decision_at, self.parameters, research_mode=research_mode, news_unaware=news_unaware),
                session=base_session,
                risk=None,
                code=RejectionCode.D1_CONFLICT,
                message="Daily bias opposes the candidate direction.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        rsi_values = _rsi_series([item.close for item in m15_completed], self.parameters.m15_rsi_period)
        rsi_value = rsi_values[-self.parameters.m15_rsi_window]
        previous = m15_completed[-2]
        flags = _pattern_flags(trigger, previous)
        pattern_ok = (
            flags["bullish_engulfing"] or flags["bullish_pin"]
            if side is Side.BUY
            else flags["bearish_engulfing"] or flags["bearish_pin"]
        )
        volume_ok = _volume_confirmed(m15_completed, self.parameters)
        volume_available = _volume_available(m15_completed, self.parameters)
        news_eval = _news_evaluation(
            news,
            decision_at,
            self.parameters,
            entry_time=entry_time,
            research_mode=research_mode,
            news_unaware=news_unaware,
        )

        zones = list(_build_sr_zones(h4_completed, h4_state, self.parameters))
        fib = _build_fib_zone(h4_completed, h4_state, self.parameters)
        if fib is not None:
            zones.append(fib)
        clusters = _zone_clusters(zones)
        selected_zone = _select_zone(clusters, trigger, side)
        score = _score(
            side,
            daily,
            selected_zone,
            rsi_value,
            pattern_ok,
            volume_ok,
            news_eval,
            self.parameters,
            volume_available=volume_available,
        )
        if selected_zone is None:
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=None,
                news=news_eval,
                session=base_session,
                risk=None,
                code=RejectionCode.NO_H4_ZONE,
                message="No valid H4 S/R or Fibonacci zone intersects the trigger.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if not news_eval.available and not news_eval.unaware:
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=base_session,
                risk=None,
                code=RejectionCode.NEWS_UNAVAILABLE,
                message=news_eval.reason,
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if not news_eval.safe and not news_eval.unaware:
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=base_session,
                risk=None,
                code=RejectionCode.NEWS_EVENT_WINDOW,
                message=news_eval.reason,
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        session_eval = self._session_evaluation(
            decision_at,
            entry_time,
            session_calendar_verified=session_calendar_verified,
            research_mode=research_mode,
        )
        if not session_eval.in_overlap:
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=session_eval,
                risk=None,
                code=RejectionCode.SESSION_OUTSIDE,
                message=session_eval.reason,
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if not session_calendar_verified and not research_mode:
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=session_eval,
                risk=None,
                code=RejectionCode.SESSION_UNVERIFIED,
                message="XAUUSD-P session calendar is not verified; production entry is blocked.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        if score.total < self.parameters.score_reject_below:
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=session_eval,
                risk=None,
                code=RejectionCode.SCORE_BELOW_THRESHOLD,
                message=f"Confluence score {score.total} is below {self.parameters.score_reject_below}.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        assert risk_state is not None
        risk = _risk_decision(risk_state, decision_at, score.confidence, self.parameters)
        if not risk.eligible:
            risk_rejections = tuple(
                Rejection(_risk_rejection_code(reason), reason)
                for reason in risk.reasons
            )
            first = risk_rejections[0]
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=session_eval,
                risk=risk,
                code=first.code,
                message="; ".join(risk.reasons),
                research_mode=research_mode,
                news_unaware=news_unaware,
                additional_rejections=risk_rejections[1:],
            )

        if not self._entry_not_chased(side, entry, trigger, selected_zone, h4_state):
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=session_eval,
                risk=risk,
                code=RejectionCode.ENTRY_CHASED,
                message="Next open is too far from the trigger close or outside the eligible zone buffer.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        plan = self._trade_plan(side, entry, selected_zone, h4_state)
        if plan is None:
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=session_eval,
                risk=risk,
                code=RejectionCode.STOP_UNAVAILABLE,
                message=(
                    "No valid structural stop or target plan could be constructed "
                    "(including the opposing-obstacle and minimum-TP2 rules)."
                ),
                research_mode=research_mode,
                news_unaware=news_unaware,
            )
        if plan.risk_distance > self.parameters.max_stop_atr * (h4_state.atr or 0):
            return _reject(
                decision_time=decision_at,
                score=score,
                daily=daily,
                h4=h4_state,
                zone=selected_zone,
                news=news_eval,
                session=session_eval,
                risk=risk,
                code=RejectionCode.STOP_TOO_WIDE,
                message="Structural stop exceeds the approved maximum stop distance.",
                research_mode=research_mode,
                news_unaware=news_unaware,
            )

        rationale = (
            f"{side.value} XAUUSD-P research candidate at {decision_at.isoformat()}; "
            f"score {score.total}/8 ({score.confidence.value}); "
            f"zone {selected_zone.evidence[0]}; "
            f"entry {entry:.2f}, stop {plan.stop:.2f}, TP1 {plan.tp1:.2f}, TP2 {plan.tp2:.2f}; "
            f"daily={daily.state.value}, prev_day_high={daily.previous_high}, "
            f"prev_day_low={daily.previous_low}, h4={h4_state.state.value}; "
            f"news={news_eval.reason}; session={session_eval.reason}; "
            "monetary sizing blocked pending verified contract economics."
        )
        candidate = XAUUSDPSignalCandidate(
            instrument=self.instrument,
            side=side,
            decision_time=decision_at,
            entry_time=entry_time,
            entry_reference=entry,
            zone=selected_zone,
            plan=plan,
            confidence=score.confidence,
            score=score,
            daily_bias=daily,
            h4_trend=h4_state,
            news=news_eval,
            session=session_eval,
            risk=risk,
            research_mode=research_mode,
            news_unaware=news_eval.unaware,
            rationale=rationale,
        )
        return StrategyDecision(
            instrument=self.instrument,
            decision_time=decision_at,
            status=DecisionStatus.CANDIDATE,
            candidate=candidate,
            rejections=(),
            score=score,
            daily_bias=daily,
            h4_trend=h4_state,
            zone=selected_zone,
            news=news_eval,
            session=session_eval,
            risk=risk,
            research_mode=research_mode,
            news_unaware=news_eval.unaware,
        )

    def _validate_series(
        self,
        observations: Sequence[MarketObservation],
        timeframe: str,
        label: str,
    ) -> tuple[MarketObservation, ...]:
        if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
            raise ValueError(f"{label} must be a sequence of MarketObservation values")
        result = tuple(observations)
        if not result:
            return ()
        previous: datetime | None = None
        for index, observation in enumerate(result):
            if not isinstance(observation, MarketObservation):
                raise ValueError(f"{label}[{index}] is not a MarketObservation")
            if observation.symbol != _SYMBOL:
                raise ValueError(f"{label}[{index}] is not XAUUSD-P")
            if observation.timeframe != timeframe:
                raise ValueError(f"{label}[{index}] has timeframe {observation.timeframe!r}, expected {timeframe!r}")
            at = _require_aware(observation.observed_at, f"{label}[{index}].observed_at")
            if previous is not None and at <= previous:
                raise ValueError(f"{label} timestamps must be strictly increasing with no duplicates")
            previous = at
        return result

    def _session_evaluation(
        self,
        decision_time: datetime,
        entry_time: datetime,
        *,
        session_calendar_verified: bool,
        research_mode: bool,
    ) -> SessionEvaluation:
        in_session, entry_in_session = _session_overlap(
            decision_time,
            entry_time,
            self.parameters,
        )
        return SessionEvaluation(
            in_overlap=in_session and entry_in_session,
            calendar_verified=session_calendar_verified,
            research_allowed=research_mode,
            reason=(
                "Within IANA London/New York overlap."
                if in_session and entry_in_session
                else "Outside IANA London/New York overlap."
            ),
            filter_mode=SessionFilterMode.APPROVED,
        )

    def _daily_bias(self, observations: Sequence[MarketObservation]) -> DailyBias:
        closes = [item.close for item in observations]
        fast = _ema_series(closes, self.parameters.daily_ema_fast)[-1]
        slow = _ema_series(closes, self.parameters.daily_ema_slow)[-1]
        if fast is None or slow is None:
            return DailyBias(BiasState.UNAVAILABLE, fast, slow, None, observations[-1].observed_at, observations[-1].high, observations[-1].low, len(observations))
        tolerance = self.parameters.daily_ema_tolerance_pct * closes[-1]
        if fast - slow > tolerance:
            state = BiasState.BULLISH
        elif slow - fast > tolerance:
            state = BiasState.BEARISH
        else:
            state = BiasState.NEUTRAL
        return DailyBias(
            state=state,
            ema_fast=fast,
            ema_slow=slow,
            tolerance=tolerance,
            latest_bar_at=observations[-1].observed_at,
            previous_high=observations[-1].high,
            previous_low=observations[-1].low,
            bar_count=len(observations),
        )

    def _entry_not_chased(
        self,
        side: Side,
        entry: float,
        trigger: MarketObservation,
        zone: ZoneCluster,
        h4: H4Trend,
    ) -> bool:
        if h4.atr is None:
            return False
        max_distance = self.parameters.max_entry_distance_atr * h4.atr
        if side is Side.BUY:
            if entry - trigger.close > max_distance:
                return False
            if entry > zone.upper + max_distance:
                return False
        else:
            if trigger.close - entry > max_distance:
                return False
            if entry < zone.lower - max_distance:
                return False
        return True

    def _trade_plan(
        self,
        side: Side,
        entry: float,
        zone: ZoneCluster,
        h4: H4Trend,
    ) -> TradePlan | None:
        if h4.atr is None:
            return None
        if side is Side.BUY:
            structural = _latest_pivot(h4.pivots, PivotKind.LOW)
            if structural is None:
                return None
            reference = min(zone.lower, structural)
            raw_stop = reference - self.parameters.stop_buffer_atr * h4.atr
            stop = _round_down(raw_stop, self.parameters.tick_size)
            if stop >= entry:
                return None
            risk = entry - stop
            raw_tp1 = entry + self.parameters.tp1_r * risk
            raw_tp2 = entry + self.parameters.tp2_r * risk
            tp1 = _round_down(raw_tp1, self.parameters.tick_size)
            tp2 = _round_down(raw_tp2, self.parameters.tick_size)
            obstacle = _opposing_pivot(h4.pivots, side, entry)
            if obstacle is not None:
                if obstacle < tp1:
                    return None
                if obstacle < tp2:
                    tp2 = _round_down(
                        obstacle - self.parameters.target_obstacle_buffer_atr * h4.atr,
                        self.parameters.tick_size,
                    )
                    if tp2 < entry + self.parameters.min_tp2_r * risk:
                        return None
        else:
            structural = _latest_pivot(h4.pivots, PivotKind.HIGH)
            if structural is None:
                return None
            reference = max(zone.upper, structural)
            raw_stop = reference + self.parameters.stop_buffer_atr * h4.atr
            stop = _round_up(raw_stop, self.parameters.tick_size)
            if stop <= entry:
                return None
            risk = stop - entry
            raw_tp1 = entry - self.parameters.tp1_r * risk
            raw_tp2 = entry - self.parameters.tp2_r * risk
            tp1 = _round_up(raw_tp1, self.parameters.tick_size)
            tp2 = _round_up(raw_tp2, self.parameters.tick_size)
            obstacle = _opposing_pivot(h4.pivots, side, entry)
            if obstacle is not None:
                if obstacle > tp1:
                    return None
                if obstacle > tp2:
                    tp2 = _round_up(
                        obstacle + self.parameters.target_obstacle_buffer_atr * h4.atr,
                        self.parameters.tick_size,
                    )
                    if tp2 > entry - self.parameters.min_tp2_r * risk:
                        return None
        if risk < self.parameters.min_stop_ticks * self.parameters.tick_size:
            return None
        if side is Side.BUY and not (stop < entry < tp1 < tp2):
            return None
        if side is Side.SELL and not (stop > entry > tp1 > tp2):
            return None
        return TradePlan(
            entry=entry,
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            risk_distance=risk,
            breakeven=entry,
            breakeven_costs_verified=False,
        )
