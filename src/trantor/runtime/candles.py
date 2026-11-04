"""Completed-candle identification and candle-series quality checks.

A candle is *completed* only when the broker clock has passed its close
(``open + duration``). The newest bar returned by MT5 is normally still forming
and is never passed to the strategy as a completed bar. Missing, duplicated and
out-of-order candles are reported explicitly; nothing is filled in.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..models import MarketObservation

__all__ = [
    "CandleGap",
    "CandleQuality",
    "RawCandle",
    "TIMEFRAME_DURATION",
    "analyse_candles",
    "split_completed",
]

UTC = timezone.utc
TIMEFRAME_DURATION = {
    "1m": timedelta(minutes=1),
    "15m": timedelta(minutes=15),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


@dataclass(frozen=True, slots=True)
class RawCandle:
    """A candle exactly as the source returned it, before validation."""

    time: object
    open: object
    high: object
    low: object
    close: object
    tick_volume: object = None
    spread: object = None


@dataclass(frozen=True, slots=True)
class CandleGap:
    after: datetime
    before: datetime
    missing_bars: int
    weekend_like: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "after": self.after.isoformat(),
            "before": self.before.isoformat(),
            "missing_bars": self.missing_bars,
            "weekend_like": self.weekend_like,
        }


@dataclass(frozen=True, slots=True)
class CandleQuality:
    timeframe: str
    received: int
    valid: int
    invalid_rows: tuple[str, ...]
    duplicates: int
    out_of_order: int
    off_grid: int
    gaps: tuple[CandleGap, ...]
    completed: int
    forming: bool

    @property
    def clean(self) -> bool:
        return not (self.invalid_rows or self.duplicates or self.out_of_order or self.off_grid)

    def as_dict(self) -> dict[str, object]:
        return {
            "timeframe": self.timeframe,
            "received": self.received,
            "valid": self.valid,
            "invalid_rows": list(self.invalid_rows[:10]),
            "invalid_row_count": len(self.invalid_rows),
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "off_grid": self.off_grid,
            "gaps": [gap.as_dict() for gap in self.gaps[-10:]],
            "gap_count": len(self.gaps),
            "non_weekend_gap_count": sum(1 for gap in self.gaps if not gap.weekend_like),
            "completed": self.completed,
            "forming_bar_present": self.forming,
            "clean": self.clean,
        }


def _on_grid(at: datetime, timeframe: str) -> bool:
    if timeframe == "1d":
        return at.hour == 0 and at.minute == 0 and at.second == 0 and at.microsecond == 0
    if timeframe == "4h":
        return at.hour % 4 == 0 and at.minute == 0 and at.second == 0 and at.microsecond == 0
    if timeframe == "15m":
        return at.minute % 15 == 0 and at.second == 0 and at.microsecond == 0
    if timeframe == "1m":
        return at.second == 0 and at.microsecond == 0
    return False


def _weekend_like(after: datetime, before: datetime) -> bool:
    # A gap that contains a Saturday is consistent with the weekend closure.
    # It is not proof of a scheduled closure: the broker calendar is unverified.
    day = after
    while day <= before:
        if day.weekday() == 5:
            return True
        day += timedelta(hours=12)
    return False


def _to_observation(
    raw: RawCandle,
    *,
    symbol: str,
    timeframe: str,
    shift: timedelta,
) -> MarketObservation:
    stamp = raw.time
    if isinstance(stamp, bool):
        raise ValueError("time must be an epoch or datetime")
    if isinstance(stamp, (int, float)):
        if not math.isfinite(float(stamp)):
            raise ValueError("time must be finite")
        at = datetime.fromtimestamp(int(stamp), tz=UTC)
    elif isinstance(stamp, datetime):
        if stamp.tzinfo is None:
            raise ValueError("time must be timezone-aware")
        at = stamp.astimezone(UTC)
    else:
        raise ValueError("time must be an epoch or datetime")
    volume = raw.tick_volume
    return MarketObservation(
        symbol=symbol,
        timeframe=timeframe,
        observed_at=at - shift,
        open=float(raw.open),  # type: ignore[arg-type]
        high=float(raw.high),  # type: ignore[arg-type]
        low=float(raw.low),  # type: ignore[arg-type]
        close=float(raw.close),  # type: ignore[arg-type]
        volume=None if volume is None else float(volume),  # type: ignore[arg-type]
    )


def analyse_candles(
    raw: Sequence[RawCandle],
    *,
    symbol: str,
    timeframe: str,
    server_now: datetime,
    shift: timedelta = timedelta(0),
    clock_tolerance: timedelta = timedelta(seconds=5),
) -> tuple[tuple[MarketObservation, ...], MarketObservation | None, CandleQuality]:
    """Validate raw candles and split them into completed bars and a forming bar.

    ``server_now`` is the broker clock in the same coordinate as the raw
    times. ``shift`` converts raw times to the output coordinate
    (``raw - shift``). A forming bar may open up to ``clock_tolerance`` ahead
    of ``server_now`` (sub-second broker/host clock skew); anything further
    ahead is rejected. Invalid rows, duplicates and out-of-order rows are
    dropped from the output and counted; they are never repaired.
    """

    if timeframe not in TIMEFRAME_DURATION:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    if server_now.tzinfo is None:
        raise ValueError("server_now must be timezone-aware")
    duration = TIMEFRAME_DURATION[timeframe]
    invalid: list[str] = []
    accepted: list[MarketObservation] = []
    duplicates = 0
    out_of_order = 0
    off_grid = 0
    seen: set[datetime] = set()
    for index, item in enumerate(raw):
        try:
            observation = _to_observation(item, symbol=symbol, timeframe=timeframe, shift=timedelta(0))
        except (TypeError, ValueError, OverflowError) as exc:
            invalid.append(f"row {index}: {exc}")
            continue
        if observation.observed_at in seen:
            duplicates += 1
            continue
        if accepted and observation.observed_at < accepted[-1].observed_at:
            out_of_order += 1
            continue
        if not _on_grid(observation.observed_at, timeframe):
            off_grid += 1
            continue
        seen.add(observation.observed_at)
        accepted.append(observation)

    gaps: list[CandleGap] = []
    for previous, current in zip(accepted, accepted[1:]):
        step = current.observed_at - previous.observed_at
        if step > duration:
            missing = int(step / duration) - 1
            gaps.append(
                CandleGap(
                    after=previous.observed_at - shift,
                    before=current.observed_at - shift,
                    missing_bars=missing,
                    weekend_like=_weekend_like(previous.observed_at, current.observed_at),
                )
            )

    now = server_now.astimezone(UTC)
    # A bar that opens further ahead than the clock tolerance cannot be trusted.
    ahead = [bar for bar in accepted if bar.observed_at > now + clock_tolerance]
    if ahead:
        invalid.append(f"{len(ahead)} bar(s) open ahead of the broker clock")
        accepted = [bar for bar in accepted if bar.observed_at <= now + clock_tolerance]
    # Every bar followed by a newer bar is complete: the broker has already
    # started the next one. The newest bar is complete only once its close
    # has passed on the broker clock.
    completed = list(accepted[:-1])
    forming = None
    if accepted:
        last = accepted[-1]
        if last.observed_at + duration <= now:
            completed.append(last)
        else:
            forming = last
    shifted_completed = tuple(_shift(bar, shift) for bar in completed)
    shifted_forming = None if forming is None else _shift(forming, shift)
    quality = CandleQuality(
        timeframe=timeframe,
        received=len(raw),
        valid=len(accepted),
        invalid_rows=tuple(invalid),
        duplicates=duplicates,
        out_of_order=out_of_order,
        off_grid=off_grid,
        gaps=tuple(gaps),
        completed=len(shifted_completed),
        forming=shifted_forming is not None,
    )
    return shifted_completed, shifted_forming, quality


def _shift(bar: MarketObservation, shift: timedelta) -> MarketObservation:
    if not shift:
        return bar
    return MarketObservation(
        symbol=bar.symbol,
        timeframe=bar.timeframe,
        observed_at=bar.observed_at - shift,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
    )


def split_completed(
    bars: Sequence[MarketObservation],
    timeframe: str,
    now: datetime,
) -> tuple[tuple[MarketObservation, ...], MarketObservation | None]:
    """Split already-validated bars into completed bars and the forming bar."""

    duration = TIMEFRAME_DURATION[timeframe]
    completed = tuple(bar for bar in bars if bar.observed_at + duration <= now)
    forming = [bar for bar in bars if bar.observed_at + duration > now]
    return completed, (forming[-1] if forming else None)
