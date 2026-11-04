"""Live market-data monitoring for XAUUSD-P.

The monitor polls a :class:`LiveSource` (the MT5 gateway in production, a fake
in tests), measures the broker clock, separates completed from forming candles,
and reports freshness. It never invents a price: when the source is
disconnected or stale, the snapshot says so and carries no current quote.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Protocol

from ..market_data.providers import ProviderUnavailableError
from ..models import MarketObservation
from .candles import TIMEFRAME_DURATION, CandleQuality, RawCandle, analyse_candles
from .mt5_gateway import Tick
from .timebase import BrokerTimeBasis

__all__ = [
    "DEFAULT_BAR_COUNTS",
    "FeedStatus",
    "LiveSource",
    "MarketDataMonitor",
    "TimeframeState",
]

UTC = timezone.utc
DEFAULT_BAR_COUNTS = {"1d": 320, "4h": 520, "15m": 200}


class LiveSource(Protocol):
    def health(self) -> dict[str, Any]: ...

    def tick(self) -> Tick | None: ...

    def rates(self, timeframe: str, count: int) -> list[RawCandle] | None: ...


class FeedStatus(StrEnum):
    LIVE = "live"
    STALE = "stale"
    DISCONNECTED = "disconnected"
    CLOCK_UNVERIFIED = "clock_unverified"
    NO_DATA = "no_data"


@dataclass(frozen=True, slots=True)
class TimeframeState:
    timeframe: str
    completed: tuple[MarketObservation, ...]
    forming: MarketObservation | None
    quality: CandleQuality
    fetched_at: datetime
    completion_basis: str

    @property
    def last_completed(self) -> MarketObservation | None:
        return self.completed[-1] if self.completed else None

    def as_dict(self) -> dict[str, Any]:
        last = self.last_completed
        duration = TIMEFRAME_DURATION[self.timeframe]
        return {
            "timeframe": self.timeframe,
            "fetched_at": self.fetched_at.isoformat(),
            "completion_basis": self.completion_basis,
            "last_completed": None if last is None else _bar_dict(last, duration),
            "forming": None if self.forming is None else {
                **_bar_dict(self.forming, duration),
                "complete": False,
            },
            "quality": self.quality.as_dict(),
        }


def _bar_dict(bar: MarketObservation, duration: timedelta) -> dict[str, Any]:
    return {
        "open_time": bar.observed_at.isoformat(),
        "close_time": (bar.observed_at + duration).isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "tick_volume": bar.volume,
        "complete": True,
    }


@dataclass
class MarketDataMonitor:
    source: LiveSource
    timebase: BrokerTimeBasis
    symbol: str = "XAUUSD-P"
    max_tick_age: timedelta = timedelta(seconds=120)
    max_m15_lag: timedelta = timedelta(minutes=20)
    slow_refresh: timedelta = timedelta(minutes=5)
    bar_counts: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_BAR_COUNTS))
    host_clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    on_event: Callable[[str, str, dict[str, Any]], None] | None = None

    last_tick: Tick | None = None
    last_tick_seen_at: datetime | None = None
    last_health: dict[str, Any] = field(default_factory=dict)
    frames: dict[str, TimeframeState] = field(default_factory=dict)
    spread_samples: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    _status: FeedStatus = FeedStatus.NO_DATA
    _gap_reported: dict[str, datetime] = field(default_factory=dict)

    def _emit(self, level: str, event: str, detail: dict[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(level, event, detail)
            except Exception:
                pass

    # -- polling --------------------------------------------------------------

    def poll_tick(self) -> Tick | None:
        self.last_health = self.source.health()
        if not self.last_health.get("connected"):
            self._set_status(FeedStatus.DISCONNECTED, {"error": self.last_health.get("error")})
            return None
        try:
            tick = self.source.tick()
        except ProviderUnavailableError as exc:
            self._set_status(FeedStatus.DISCONNECTED, {"error": str(exc)})
            return None
        host_now = self.host_clock()
        if tick is None or tick.bid is None or tick.ask is None:
            self._set_status(FeedStatus.NO_DATA, {"reason": "no bid/ask available"})
            return None
        if tick.ask < tick.bid:
            self._emit("error", "crossed_quote", {"bid": tick.bid, "ask": tick.ask})
            self._set_status(FeedStatus.NO_DATA, {"reason": "crossed quote rejected"})
            return None
        is_new = self.last_tick is None or tick.raw_time != self.last_tick.raw_time
        if self.last_tick is not None and tick.raw_time < self.last_tick.raw_time:
            self._emit(
                "warning",
                "tick_time_regressed",
                {"previous": self.last_tick.raw_time.isoformat(), "current": tick.raw_time.isoformat()},
            )
            is_new = False
        if is_new:
            self.timebase.observe(tick.raw_time, host_now)
            self.last_tick = tick
            self.last_tick_seen_at = host_now
            if tick.spread is not None:
                self.spread_samples.append(tick.spread)
                del self.spread_samples[:-500]
        self._set_status(self._freshness(host_now), {})
        return tick

    def _freshness(self, host_now: datetime) -> FeedStatus:
        if self.last_tick is None:
            return FeedStatus.NO_DATA
        if not self.timebase.verified:
            return FeedStatus.CLOCK_UNVERIFIED
        age = host_now - self.timebase.to_utc(self.last_tick.raw_time)
        return FeedStatus.LIVE if age <= self.max_tick_age else FeedStatus.STALE

    def _set_status(self, status: FeedStatus, detail: dict[str, Any]) -> None:
        if status is not self._status:
            level = "info" if status is FeedStatus.LIVE else "warning"
            if status is FeedStatus.DISCONNECTED:
                level = "error"
            self._emit(level, "feed_status_changed", {"from": self._status.value, "to": status.value, **detail})
            self._status = status

    @property
    def status(self) -> FeedStatus:
        return self._status

    def server_now(self) -> datetime | None:
        """Best estimate of the broker clock.

        The host clock plus the measured offset can trail the broker by a
        fraction of a second; the newest tick time is the broker's own clock.
        """

        if self.timebase.offset is None:
            return None
        estimate = self.host_clock() + self.timebase.offset
        if self.last_tick is not None and self.last_tick.raw_time > estimate:
            return self.last_tick.raw_time
        return estimate

    def _refresh_due(self, timeframe: str, server_now: datetime | None) -> bool:
        """M15 always; D1/H4 when their forming bar may have closed or every 5 min."""

        frame = self.frames.get(timeframe)
        if timeframe == "15m" or frame is None or server_now is None:
            return True
        if frame.completion_basis != "broker_clock":
            return True
        if self.host_clock() - frame.fetched_at >= self.slow_refresh:
            return True
        if frame.forming is None:
            return True
        shift = self.timebase.offset or timedelta(0)
        forming_close = frame.forming.observed_at + TIMEFRAME_DURATION[timeframe] + shift
        return server_now >= forming_close

    def refresh(self, timeframes: tuple[str, ...] = ("1d", "4h", "15m")) -> dict[str, TimeframeState]:
        server_now = self.server_now()
        shift = self.timebase.offset or timedelta(0)
        for timeframe in timeframes:
            if not self._refresh_due(timeframe, server_now):
                continue
            try:
                raw = self.source.rates(timeframe, self.bar_counts.get(timeframe, 200))
            except ProviderUnavailableError as exc:
                self.errors.append(f"{timeframe}: {exc}")
                del self.errors[:-50]
                self.frames.pop(timeframe, None)
                continue
            if raw is None:
                self.frames.pop(timeframe, None)
                continue
            if server_now is not None:
                completed, forming, quality = analyse_candles(
                    raw, symbol=self.symbol, timeframe=timeframe, server_now=server_now, shift=shift
                )
                basis = "broker_clock"
            else:
                # Without a measured clock, completion is structural only: every
                # bar followed by a later bar is complete and the newest bar is
                # treated as forming, even if it may already have closed.
                far_future = datetime.max.replace(tzinfo=UTC) - timedelta(days=2)
                completed_all, _, quality = analyse_candles(
                    raw, symbol=self.symbol, timeframe=timeframe, server_now=far_future
                )
                completed = completed_all[:-1]
                forming = completed_all[-1] if completed_all else None
                basis = "structural_unverified_clock"
            reported = self._gap_reported.get(timeframe)
            new_gaps = [
                gap
                for gap in quality.gaps
                if not gap.weekend_like and (reported is None or gap.before > reported)
            ]
            if quality.gaps:
                self._gap_reported[timeframe] = max(gap.before for gap in quality.gaps)
            if new_gaps and reported is not None:
                # The first fetch only establishes history; later fetches report
                # newly observed gaps once.
                self._emit(
                    "warning",
                    "missing_bars_detected",
                    {"timeframe": timeframe, "gaps": [gap.as_dict() for gap in new_gaps[-3:]]},
                )
            elif new_gaps:
                self._emit(
                    "info",
                    "historical_gaps_present",
                    {"timeframe": timeframe, "non_weekend_gaps": len(new_gaps), "latest": new_gaps[-1].as_dict()},
                )
            if quality.duplicates or quality.out_of_order or quality.invalid_rows:
                self._emit("warning", "candle_quality_issue", quality.as_dict())
            self.frames[timeframe] = TimeframeState(
                timeframe=timeframe,
                completed=completed,
                forming=forming,
                quality=quality,
                fetched_at=self.host_clock(),
                completion_basis=basis,
            )
        return self.frames

    # -- views ---------------------------------------------------------------

    def m15_lag(self) -> timedelta | None:
        frame = self.frames.get("15m")
        if frame is None or frame.last_completed is None or self.timebase.offset is None:
            return None
        close = frame.last_completed.observed_at + TIMEFRAME_DURATION["15m"]
        return self.host_clock() - close

    def entry_blockers(self) -> list[str]:
        """Reasons the current feed cannot support a new entry."""

        reasons = []
        if self._status is not FeedStatus.LIVE:
            reasons.append(f"market data feed is {self._status.value}")
        if not self.timebase.verified:
            reasons.append("broker clock offset is not verified")
        for timeframe in ("1d", "4h", "15m"):
            frame = self.frames.get(timeframe)
            if frame is None:
                reasons.append(f"{timeframe} candles unavailable")
            elif not frame.quality.clean:
                reasons.append(f"{timeframe} candle quality issues")
        lag = self.m15_lag()
        if lag is not None and lag > self.max_m15_lag:
            reasons.append("latest completed M15 bar is stale")
        return reasons

    def snapshot(self) -> dict[str, Any]:
        tick = self.last_tick if self._status in (FeedStatus.LIVE, FeedStatus.CLOCK_UNVERIFIED) else None
        tick_utc = None
        tick_age = None
        if self.last_tick is not None and self.timebase.offset is not None:
            tick_utc = self.timebase.to_utc(self.last_tick.raw_time)
            tick_age = (self.host_clock() - tick_utc).total_seconds()
        spreads = sorted(self.spread_samples[-200:])
        return {
            "symbol": self.symbol,
            "status": self._status.value,
            "connection": self.last_health,
            "quote": None if tick is None else {
                "bid": tick.bid,
                "ask": tick.ask,
                "spread": None if tick.spread is None else round(tick.spread, 5),
                "label": "LIVE DATA" if self._status is FeedStatus.LIVE else "UNVERIFIED CLOCK",
            },
            "last_tick": None if self.last_tick is None else {
                "raw_broker_time": self.last_tick.raw_time.isoformat(),
                "normalized_utc": None if tick_utc is None else tick_utc.isoformat(),
                "age_seconds": None if tick_age is None else round(tick_age, 1),
                "host_received_at": None if self.last_tick_seen_at is None else self.last_tick_seen_at.isoformat(),
                "bid": self.last_tick.bid,
                "ask": self.last_tick.ask,
            },
            "spread_stats": None if not spreads else {
                "samples": len(spreads),
                "min": round(spreads[0], 5),
                "median": round(spreads[len(spreads) // 2], 5),
                "max": round(spreads[-1], 5),
            },
            "time_basis": self.timebase.snapshot(),
            "frames": {name: frame.as_dict() for name, frame in self.frames.items()},
            "m15_lag_seconds": None if self.m15_lag() is None else round(self.m15_lag().total_seconds(), 1),
            "entry_blockers": self.entry_blockers(),
            "recent_errors": self.errors[-5:],
        }
