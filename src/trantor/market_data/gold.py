"""COMEX gold futures session schedules.

Active scope is limited to COMEX GC and MGC futures.  They have a nearly
24-hour electronic week with a daily maintenance break.  The shipped schedule
encodes CME Group's published regular hours plus the verified 2026-2027 holiday,
early-close, halt, and special-session overrides.

Weekends, timezone conversions, and modified sessions are modelled here.  No
permanent UTC offset is used; local boundaries are resolved through the
configured IANA timezone.  Outside the verified range the calendar fails with
:class:`SessionCalendarRangeError` rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..models import AssetClass, MarketInstrument
from .sessions import (
    InstrumentScheduleMismatchError,
    SessionCalendarRangeError,
    SessionError,
    SessionInfo,
    SessionState,
    UnverifiedScheduleError,
)

__all__ = [
    "GoldConvention",
    "GoldSessionCalendar",
    "GoldSessionError",
    "GoldSessionSchedule",
    "RecurringLocalWindow",
    "SessionOverride",
    "UnsupportedGoldInstrumentError",
    "comex_gold_futures_schedule",
    "gold_session_calendar_for",
]


class GoldConvention(StrEnum):
    COMEX_FUTURES = "comex_futures"


class GoldSessionError(SessionError):
    """Base class for gold-specific schedule errors."""


class UnsupportedGoldInstrumentError(GoldSessionError):
    """The instrument is not a supported gold futures contract."""


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _is_plain_local_time(value: object) -> bool:
    return isinstance(value, time) and value.tzinfo is None


@dataclass(frozen=True, slots=True)
class RecurringLocalWindow:
    """A weekly recurring local-time interval.

    ``start_weekday`` uses Monday=0 through Sunday=6.  ``end_weekday_offset``
    is the number of calendar days after the start date; zero means a same-day
    window and requires ``end`` to be later than ``start``.  This explicit form
    avoids guessing whether a window crosses midnight.
    """

    start_weekday: int
    start: time
    end_weekday_offset: int
    end: time

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_weekday, bool)
            or not isinstance(self.start_weekday, int)
            or not 0 <= self.start_weekday <= 6
        ):
            raise ValueError("start_weekday must be an integer from 0 (Mon) to 6 (Sun)")
        if not _is_plain_local_time(self.start):
            raise ValueError("start must be a local time without tzinfo")
        if (
            isinstance(self.end_weekday_offset, bool)
            or not isinstance(self.end_weekday_offset, int)
            or not 0 <= self.end_weekday_offset <= 6
        ):
            raise ValueError("end_weekday_offset must be an integer from 0 to 6")
        if not _is_plain_local_time(self.end):
            raise ValueError("end must be a local time without tzinfo")
        if self.end_weekday_offset == 0 and self.end <= self.start:
            raise ValueError("a same-day window must end after it starts")

    def contains(self, local_at: datetime, timezone: ZoneInfo) -> bool:
        """Return whether an aware local datetime lies in any weekly occurrence."""

        if local_at.tzinfo is None or local_at.utcoffset() is None:
            raise ValueError("local_at must be timezone-aware")
        for days_ago in range(0, 8):
            anchor_date = local_at.date() - timedelta(days=days_ago)
            if anchor_date.weekday() != self.start_weekday:
                continue
            start_at = datetime.combine(anchor_date, self.start, tzinfo=timezone)
            end_date = anchor_date + timedelta(days=self.end_weekday_offset)
            end_at = datetime.combine(end_date, self.end, tzinfo=timezone)
            if start_at <= local_at < end_at:
                return True
        return False


_OVERRIDE_STATES = frozenset(
    (SessionState.OPEN, SessionState.CLOSED, SessionState.BREAK)
)
_OVERRIDE_PRIORITY = {
    SessionState.BREAK: 1,
    SessionState.CLOSED: 2,
    SessionState.OPEN: 3,
}


@dataclass(frozen=True, slots=True)
class SessionOverride:
    """An explicit local-time interval that replaces the recurring state.

    Overrides are end-exclusive.  They are used for exchange holidays, early
    closes, holiday halts, and exceptional sessions that cannot be represented
    by the recurring weekly windows.  Local date/time fields (rather than a
    fixed UTC offset) keep the override DST-aware through the schedule timezone.
    """

    start_date: date
    start: time
    end_date: date
    end: time
    state: SessionState
    reason: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.start_date, "start_date"),
            (self.end_date, "end_date"),
        ):
            if not isinstance(value, date) or isinstance(value, datetime):
                raise ValueError(f"{field} must be a date")
        if not _is_plain_local_time(self.start):
            raise ValueError("start must be a local time without tzinfo")
        if not _is_plain_local_time(self.end):
            raise ValueError("end must be a local time without tzinfo")
        if not isinstance(self.state, SessionState):
            raise ValueError("state must be a SessionState")
        if self.state not in _OVERRIDE_STATES:
            raise ValueError("override state must be open, closed, or break")
        _require_text(self.reason, "reason")
        if datetime.combine(self.start_date, self.start) >= datetime.combine(
            self.end_date, self.end
        ):
            raise ValueError("override must end after it starts")

    def contains(self, local_at: datetime, timezone: ZoneInfo) -> bool:
        """Return whether an aware local datetime lies in this override."""

        if local_at.tzinfo is None or local_at.utcoffset() is None:
            raise ValueError("local_at must be timezone-aware")
        start_at = datetime.combine(self.start_date, self.start, tzinfo=timezone)
        end_at = datetime.combine(self.end_date, self.end, tzinfo=timezone)
        return start_at <= local_at < end_at


@dataclass(frozen=True, slots=True)
class GoldSessionSchedule:
    """A configurable gold schedule with a verification label and range.

    An unverified schedule is usable for development and fixture tests but can
    be rejected by callers that require an accurate production calendar via
    ``require_verified=True``.  A schedule may declare a verified local-date
    range; requests outside that range fail explicitly instead of falling back
    to a guessed recurring state.  If overrides overlap, an ``OPEN`` override
    takes precedence over ``CLOSED``, which takes precedence over ``BREAK``.
    """

    name: str
    convention: GoldConvention
    timezone: str
    sessions: tuple[RecurringLocalWindow, ...]
    breaks: tuple[RecurringLocalWindow, ...] = ()
    verified: bool = False
    source_note: str = ""
    warnings: tuple[str, ...] = ()
    overrides: tuple[SessionOverride, ...] = ()
    verified_start: date | None = None
    verified_end: date | None = None

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        if not isinstance(self.convention, GoldConvention):
            raise ValueError("convention must be a GoldConvention")
        _require_text(self.timezone, "timezone")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {self.timezone!r}") from None
        if not isinstance(self.sessions, tuple) or not self.sessions:
            raise ValueError("sessions must be a non-empty tuple")
        if any(not isinstance(item, RecurringLocalWindow) for item in self.sessions):
            raise ValueError("sessions must contain only RecurringLocalWindow values")
        if not isinstance(self.breaks, tuple):
            raise ValueError("breaks must be a tuple")
        if any(not isinstance(item, RecurringLocalWindow) for item in self.breaks):
            raise ValueError("breaks must contain only RecurringLocalWindow values")
        if not isinstance(self.verified, bool):
            raise ValueError("verified must be a boolean")
        if not isinstance(self.source_note, str):
            raise ValueError("source_note must be a string")
        if self.source_note and not self.source_note.strip():
            raise ValueError("source_note must be empty or non-empty text")
        if not isinstance(self.warnings, tuple):
            raise ValueError("warnings must be a tuple")
        for warning in self.warnings:
            _require_text(warning, "warning")
        if not isinstance(self.overrides, tuple):
            raise ValueError("overrides must be a tuple")
        if any(not isinstance(item, SessionOverride) for item in self.overrides):
            raise ValueError("overrides must contain only SessionOverride values")
        if (self.verified_start is None) != (self.verified_end is None):
            raise ValueError(
                "verified_start and verified_end must be provided together"
            )
        for value, field in (
            (self.verified_start, "verified_start"),
            (self.verified_end, "verified_end"),
        ):
            if value is not None and (
                not isinstance(value, date) or isinstance(value, datetime)
            ):
                raise ValueError(f"{field} must be a date or None")
        if (
            self.verified_start is not None
            and self.verified_end is not None
            and self.verified_start > self.verified_end
        ):
            raise ValueError("verified_start must not be after verified_end")


def _override(
    start_date: date,
    start: time,
    end_date: date,
    end: time,
    state: SessionState,
    reason: str,
) -> SessionOverride:
    return SessionOverride(
        start_date=start_date,
        start=start,
        end_date=end_date,
        end=end,
        state=state,
        reason=reason,
    )


def comex_gold_futures_schedule() -> GoldSessionSchedule:
    """Return the verified COMEX GC/MGC electronic trading schedule.

    The regular electronic window is Sunday 18:00 through Friday 17:00 in
    America/New_York, with a 17:00-18:00 maintenance break Monday-Thursday.
    CME holiday, early-close, halt, and exceptional-session events for
    2026-01-01 through 2027-12-31 are represented explicitly.  Times from the
    CME service are in U.S. Central Time; they are converted one hour forward
    to Eastern Time here.
    """

    close = SessionState.CLOSED
    open_ = SessionState.OPEN
    break_ = SessionState.BREAK

    overrides = (
        # New Year's Day 2026: the 2025-12-31 close runs through the holiday.
        _override(
            date(2025, 12, 31),
            time(17, 0),
            date(2026, 1, 1),
            time(18, 0),
            close,
            "CME New Year's Day 2026 closure",
        ),
        # Monday holidays: metals halt at 14:30 ET and reopen at 18:00 ET.
        _override(
            date(2026, 1, 19),
            time(14, 30),
            date(2026, 1, 19),
            time(18, 0),
            close,
            "CME Martin Luther King Jr. Day 2026 halt",
        ),
        _override(
            date(2026, 2, 16),
            time(14, 30),
            date(2026, 2, 16),
            time(18, 0),
            close,
            "CME Presidents' Day 2026 halt",
        ),
        # One-off additional CME pre-open/open event carried in the GC/MGC
        # service for trade date 2026-02-25.
        _override(
            date(2026, 2, 25),
            time(14, 31),
            date(2026, 2, 25),
            time(14, 45),
            break_,
            "CME GC/MGC additional pre-open event on 2026-02-25",
        ),
        _override(
            date(2026, 4, 2),
            time(17, 0),
            date(2026, 4, 5),
            time(18, 0),
            close,
            "CME Good Friday 2026 closure",
        ),
        _override(
            date(2026, 5, 25),
            time(14, 30),
            date(2026, 5, 25),
            time(18, 0),
            close,
            "CME Memorial Day 2026 halt",
        ),
        # Juneteenth 2026: early close Friday, special Saturday session.
        _override(
            date(2026, 6, 19),
            time(13, 0),
            date(2026, 6, 20),
            time(6, 0),
            close,
            "CME Juneteenth 2026 early close",
        ),
        _override(
            date(2026, 6, 20),
            time(6, 0),
            date(2026, 6, 20),
            time(18, 0),
            open_,
            "CME Juneteenth 2026 special Saturday session",
        ),
        # Independence Day observed 2026: early close Friday, Saturday session.
        _override(
            date(2026, 7, 3),
            time(13, 0),
            date(2026, 7, 4),
            time(6, 0),
            close,
            "CME Independence Day 2026 observed early close",
        ),
        _override(
            date(2026, 7, 4),
            time(6, 0),
            date(2026, 7, 4),
            time(18, 0),
            open_,
            "CME Independence Day 2026 special Saturday session",
        ),
        _override(
            date(2026, 9, 7),
            time(14, 30),
            date(2026, 9, 7),
            time(18, 0),
            close,
            "CME Labor Day 2026 halt",
        ),
        # Thanksgiving 2026 and the following early close.
        _override(
            date(2026, 11, 26),
            time(14, 30),
            date(2026, 11, 26),
            time(18, 0),
            close,
            "CME Thanksgiving Day 2026 halt",
        ),
        _override(
            date(2026, 11, 27),
            time(14, 45),
            date(2026, 11, 29),
            time(18, 0),
            close,
            "CME day-after-Thanksgiving 2026 early close",
        ),
        # Christmas 2026 and the New Year 2027 closure.
        _override(
            date(2026, 12, 24),
            time(13, 45),
            date(2026, 12, 27),
            time(18, 0),
            close,
            "CME Christmas 2026 closure",
        ),
        _override(
            date(2026, 12, 31),
            time(17, 0),
            date(2027, 1, 3),
            time(18, 0),
            close,
            "CME New Year's Day 2027 closure",
        ),
        # 2027 Monday holidays.
        _override(
            date(2027, 1, 18),
            time(14, 30),
            date(2027, 1, 18),
            time(18, 0),
            close,
            "CME Martin Luther King Jr. Day 2027 halt",
        ),
        _override(
            date(2027, 2, 15),
            time(14, 30),
            date(2027, 2, 15),
            time(18, 0),
            close,
            "CME Presidents' Day 2027 halt",
        ),
        _override(
            date(2027, 3, 25),
            time(17, 0),
            date(2027, 3, 28),
            time(18, 0),
            close,
            "CME Good Friday 2027 closure",
        ),
        _override(
            date(2027, 5, 31),
            time(14, 30),
            date(2027, 5, 31),
            time(18, 0),
            close,
            "CME Memorial Day 2027 halt",
        ),
        # Juneteenth 2027: early close Friday, special Saturday session.
        _override(
            date(2027, 6, 18),
            time(13, 0),
            date(2027, 6, 19),
            time(6, 0),
            close,
            "CME Juneteenth 2027 early close",
        ),
        _override(
            date(2027, 6, 19),
            time(6, 0),
            date(2027, 6, 19),
            time(18, 0),
            open_,
            "CME Juneteenth 2027 special Saturday session",
        ),
        _override(
            date(2027, 7, 5),
            time(14, 30),
            date(2027, 7, 5),
            time(18, 0),
            close,
            "CME Independence Day 2027 observed halt",
        ),
        _override(
            date(2027, 9, 6),
            time(14, 30),
            date(2027, 9, 6),
            time(18, 0),
            close,
            "CME Labor Day 2027 halt",
        ),
        # Thanksgiving 2027 and the following early close.
        _override(
            date(2027, 11, 25),
            time(14, 30),
            date(2027, 11, 25),
            time(18, 0),
            close,
            "CME Thanksgiving Day 2027 halt",
        ),
        _override(
            date(2027, 11, 26),
            time(14, 45),
            date(2027, 11, 28),
            time(18, 0),
            close,
            "CME day-after-Thanksgiving 2027 early close",
        ),
        # Christmas 2027.
        _override(
            date(2027, 12, 23),
            time(17, 0),
            date(2027, 12, 26),
            time(18, 0),
            close,
            "CME Christmas 2027 closure",
        ),
    )

    return GoldSessionSchedule(
        name="comex_gold_futures_verified_2026_2027",
        convention=GoldConvention.COMEX_FUTURES,
        timezone="America/New_York",
        sessions=(
            RecurringLocalWindow(
                start_weekday=6,
                start=time(18, 0),
                end_weekday_offset=5,
                end=time(17, 0),
            ),
        ),
        breaks=(
            RecurringLocalWindow(0, time(17, 0), 0, time(18, 0)),
            RecurringLocalWindow(1, time(17, 0), 0, time(18, 0)),
            RecurringLocalWindow(2, time(17, 0), 0, time(18, 0)),
            RecurringLocalWindow(3, time(17, 0), 0, time(18, 0)),
        ),
        verified=True,
        source_note=(
            "CME Group GC (product 437) and MGC (product 5224) trading-hours "
            "data for 2026-01-01 through 2027-12-31 (retrieved 2026-10-22), "
            "converted from U.S. Central Time to America/New_York. Regular "
            "electronic hours are Sunday 18:00-Friday 17:00 ET with a daily "
            "17:00-18:00 ET maintenance break; holiday, early-close, halt, and "
            "special-session events are explicit SessionOverride entries."
        ),
        warnings=(
            "CME holiday schedules can change and are normally finalized about "
            "two weeks before each holiday; this calendar is verified only for "
            "2026-01-01 through 2027-12-31 and raises "
            "SessionCalendarRangeError outside that range.",
            "The recurring 17:00-18:00 ET daily maintenance window is BREAK; "
            "exchange holiday halts and early closes are CLOSED.",
        ),
        overrides=overrides,
        verified_start=date(2026, 1, 1),
        verified_end=date(2027, 12, 31),
    )


_EXPECTED_ASSET_CLASS = {
    GoldConvention.COMEX_FUTURES: AssetClass.GOLD_FUTURES,
}
_SUPPORTED_GOLD_SYMBOLS = frozenset(("GC", "MGC"))


class GoldSessionCalendar:
    """Evaluate one clearly labelled gold schedule for a matching instrument."""

    def __init__(self, schedule: GoldSessionSchedule) -> None:
        if not isinstance(schedule, GoldSessionSchedule):
            raise ValueError("schedule must be a GoldSessionSchedule")
        self._schedule = schedule
        self._tz = ZoneInfo(schedule.timezone)

    @property
    def schedule(self) -> GoldSessionSchedule:
        return self._schedule

    def _check_verified_range(self, local_at: datetime) -> None:
        start = self._schedule.verified_start
        end = self._schedule.verified_end
        if start is None or end is None:
            return
        local_date = local_at.date()
        if local_date < start or local_date > end:
            raise SessionCalendarRangeError(
                f"schedule {self._schedule.name!r} is verified only for "
                f"{start.isoformat()} through {end.isoformat()} in "
                f"{self._schedule.timezone}; received {local_date.isoformat()}"
            )

    def status_at(
        self,
        instrument: MarketInstrument,
        at: datetime,
        *,
        require_verified: bool = False,
    ) -> SessionInfo:
        if not isinstance(instrument, MarketInstrument):
            raise ValueError("instrument must be a MarketInstrument")
        expected = _EXPECTED_ASSET_CLASS[self._schedule.convention]
        if instrument.asset_class is not expected:
            raise InstrumentScheduleMismatchError(
                f"schedule convention {self._schedule.convention.value!r} requires "
                f"asset class {expected.value!r}, received "
                f"{instrument.asset_class.value!r}"
            )
        if instrument.symbol not in _SUPPORTED_GOLD_SYMBOLS:
            raise UnsupportedGoldInstrumentError(
                "active scope supports only COMEX GC and MGC futures; received "
                f"gold-futures symbol {instrument.symbol!r}"
            )
        if not isinstance(at, datetime):
            raise ValueError("at must be a datetime")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        if require_verified and not self._schedule.verified:
            raise UnverifiedScheduleError(
                f"schedule {self._schedule.name!r} is not verified; accurate "
                "holiday/modified-session data is required"
            )

        local_at = at.astimezone(self._tz)
        self._check_verified_range(local_at)

        in_session = any(
            window.contains(local_at, self._tz) for window in self._schedule.sessions
        )
        in_break = in_session and any(
            window.contains(local_at, self._tz) for window in self._schedule.breaks
        )
        if not in_session:
            state = SessionState.CLOSED
        elif in_break:
            state = SessionState.BREAK
        else:
            state = SessionState.OPEN

        matched_overrides = tuple(
            override
            for override in self._schedule.overrides
            if override.contains(local_at, self._tz)
        )
        if matched_overrides:
            state = max(
                matched_overrides,
                key=lambda override: _OVERRIDE_PRIORITY[override.state],
            ).state

        notes: list[str] = []
        if self._schedule.source_note:
            notes.append(self._schedule.source_note)
        notes.extend(self._schedule.warnings)
        notes.extend(
            f"Explicit session override: {override.reason}"
            for override in matched_overrides
        )
        return SessionInfo(
            instrument=instrument,
            at=at,
            state=state,
            calendar_name=self._schedule.name,
            timezone=self._schedule.timezone,
            session_date=local_at.date(),
            verified=self._schedule.verified,
            notes=tuple(notes),
        )


def gold_session_calendar_for(
    instrument: MarketInstrument,
    *,
    futures_schedule: GoldSessionSchedule | None = None,
) -> GoldSessionCalendar:
    """Return a COMEX calendar for a GC/MGC gold futures instrument.

    Non-futures instruments are rejected explicitly.  There is no equity or
    spot/CFD fallback in the active gold-futures scope.
    """

    if not isinstance(instrument, MarketInstrument):
        raise ValueError("instrument must be a MarketInstrument")
    if instrument.asset_class is not AssetClass.GOLD_FUTURES:
        raise UnsupportedGoldInstrumentError(
            "active scope supports only COMEX gold futures; received asset class "
            f"{instrument.asset_class.value!r}"
        )
    if instrument.symbol not in _SUPPORTED_GOLD_SYMBOLS:
        raise UnsupportedGoldInstrumentError(
            "active scope supports only COMEX GC and MGC futures; received "
            f"gold-futures symbol {instrument.symbol!r}"
        )

    schedule = futures_schedule or comex_gold_futures_schedule()
    if schedule.convention is not GoldConvention.COMEX_FUTURES:
        raise InstrumentScheduleMismatchError(
            "a gold futures calendar requires a comex_futures schedule"
        )
    return GoldSessionCalendar(schedule)
