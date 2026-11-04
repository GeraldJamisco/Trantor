"""Broker clock and timestamp provenance.

MetaTrader 5 reports bar and tick times as integer epochs. Whether those epochs
are absolute UTC or broker server wall-clock time is a broker property that the
repository has never verified (limitation L-01). This module measures it
instead of assuming it:

``raw broker time``
    The MT5 epoch interpreted with ``tz=UTC``. This is the coordinate used by
    the historical datasets (``dataset_time``).
``normalized UTC``
    ``raw - server_offset``. Only *verified* when the offset was measured from
    fresh ticks against the host clock and repeated measurements agree.

The offset is rounded to the nearest 30 minutes, so host-clock error below
15 minutes cannot change it. The host clock itself is an explicit assumption.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum

__all__ = [
    "BrokerTimeBasis",
    "ClockMeasurement",
    "TimeProvenance",
    "measure_offset",
]

UTC = timezone.utc
_OFFSET_QUANTUM_SECONDS = 1800
_MAX_RESIDUAL_SECONDS = 180.0
_REQUIRED_AGREEING_MEASUREMENTS = 3


class TimeProvenance(StrEnum):
    RAW_BROKER = "raw_broker_epoch_as_utc"
    NORMALIZED_UTC_VERIFIED = "normalized_utc_verified_offset"
    NORMALIZED_UTC_UNVERIFIED = "normalized_utc_unverified_offset"
    DATASET_TIME = "dataset_time_unverified_absolute_utc"
    HOST_UTC = "host_clock_utc"


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ClockMeasurement:
    """One comparison of a broker tick time with the host UTC clock."""

    host_utc: datetime
    raw_server: datetime
    raw_difference_seconds: float
    offset_seconds: int
    residual_seconds: float

    @property
    def usable(self) -> bool:
        return abs(self.residual_seconds) <= _MAX_RESIDUAL_SECONDS

    def as_dict(self) -> dict[str, object]:
        return {
            "host_utc": self.host_utc.isoformat(),
            "raw_server": self.raw_server.isoformat(),
            "raw_difference_seconds": round(self.raw_difference_seconds, 3),
            "offset_seconds": self.offset_seconds,
            "residual_seconds": round(self.residual_seconds, 3),
            "usable": self.usable,
        }


def measure_offset(raw_server: datetime, host_utc: datetime) -> ClockMeasurement:
    """Compare one fresh broker tick time with the host clock."""

    raw = _aware(raw_server, "raw_server")
    host = _aware(host_utc, "host_utc")
    difference = (raw - host).total_seconds()
    if not math.isfinite(difference):
        raise ValueError("clock difference must be finite")
    offset = int(round(difference / _OFFSET_QUANTUM_SECONDS)) * _OFFSET_QUANTUM_SECONDS
    return ClockMeasurement(
        host_utc=host,
        raw_server=raw,
        raw_difference_seconds=difference,
        offset_seconds=offset,
        residual_seconds=difference - offset,
    )


@dataclass(slots=True)
class BrokerTimeBasis:
    """Rolling estimate of the broker server offset from UTC.

    A tick is only a valid clock sample when it is fresh: the residual after
    rounding must be within three minutes. Stale ticks (closed market, frozen
    feed) are rejected rather than producing a wrong offset.
    """

    history: deque[ClockMeasurement] = field(default_factory=lambda: deque(maxlen=20))
    offset_changes: list[dict[str, object]] = field(default_factory=list)
    _offset_seconds: int | None = None

    def observe(self, raw_server: datetime, host_utc: datetime) -> ClockMeasurement:
        measurement = measure_offset(raw_server, host_utc)
        if not measurement.usable:
            return measurement
        previous = self._offset_seconds
        self.history.append(measurement)
        recent = [item for item in self.history if item.usable][-_REQUIRED_AGREEING_MEASUREMENTS:]
        if (
            len(recent) == _REQUIRED_AGREEING_MEASUREMENTS
            and len({item.offset_seconds for item in recent}) == 1
        ):
            agreed = recent[-1].offset_seconds
            if previous is not None and agreed != previous:
                self.offset_changes.append(
                    {
                        "at_host_utc": measurement.host_utc.isoformat(),
                        "previous_offset_seconds": previous,
                        "new_offset_seconds": agreed,
                    }
                )
            self._offset_seconds = agreed
        return measurement

    @property
    def offset(self) -> timedelta | None:
        if self._offset_seconds is None:
            return None
        return timedelta(seconds=self._offset_seconds)

    @property
    def verified(self) -> bool:
        if self._offset_seconds is None:
            return False
        recent = [item for item in self.history if item.usable][-_REQUIRED_AGREEING_MEASUREMENTS:]
        return (
            len(recent) == _REQUIRED_AGREEING_MEASUREMENTS
            and all(item.offset_seconds == self._offset_seconds for item in recent)
        )

    @property
    def provenance(self) -> TimeProvenance:
        return (
            TimeProvenance.NORMALIZED_UTC_VERIFIED
            if self.verified
            else TimeProvenance.NORMALIZED_UTC_UNVERIFIED
        )

    def assume(self, offset: timedelta) -> None:
        """Seed an offset for replay/tests. It stays unverified."""

        seconds = offset.total_seconds()
        if seconds % _OFFSET_QUANTUM_SECONDS:
            raise ValueError("offset must be a multiple of 30 minutes")
        self._offset_seconds = int(seconds)

    def to_utc(self, raw_server: datetime) -> datetime:
        if self._offset_seconds is None:
            raise ValueError("broker server offset is unknown")
        return _aware(raw_server, "raw_server") - timedelta(seconds=self._offset_seconds)

    def to_server(self, utc_time: datetime) -> datetime:
        if self._offset_seconds is None:
            raise ValueError("broker server offset is unknown")
        return _aware(utc_time, "utc_time") + timedelta(seconds=self._offset_seconds)

    def snapshot(self) -> dict[str, object]:
        return {
            "offset_seconds": self._offset_seconds,
            "offset_hours": (
                None if self._offset_seconds is None else self._offset_seconds / 3600
            ),
            "verified": self.verified,
            "provenance": self.provenance.value,
            "method": (
                "fresh MT5 tick time compared with host UTC clock; rounded to "
                "30 minutes; 3 agreeing measurements required"
            ),
            "host_clock_assumption": "host clock error below 15 minutes",
            "recent_measurements": [item.as_dict() for item in list(self.history)[-5:]],
            "offset_changes": list(self.offset_changes),
        }
