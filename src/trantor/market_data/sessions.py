"""Shared session-state vocabulary and calendar protocol.

Equity and gold calendars implement different rules but return the same
:class:`SessionInfo` shape.  No strategy should inspect a provider's or
exchange's proprietary session object directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..models import MarketInstrument

__all__ = [
    "InstrumentScheduleMismatchError",
    "SessionCalendar",
    "SessionCalendarRangeError",
    "SessionError",
    "SessionInfo",
    "SessionState",
    "UnsupportedInstrumentScheduleError",
    "UnverifiedScheduleError",
]


class SessionState(StrEnum):
    """A market phase. Equities use PREMARKET/REGULAR/POSTMARKET; gold uses OPEN/BREAK."""

    CLOSED = "closed"
    PREMARKET = "premarket"
    REGULAR = "regular"
    POSTMARKET = "postmarket"
    OPEN = "open"
    BREAK = "break"


class SessionError(ValueError):
    """Base class for explicit session/calendar errors."""


class SessionCalendarRangeError(SessionError):
    """The requested instant is outside the verified calendar range."""


class UnsupportedInstrumentScheduleError(SessionError):
    """The instrument's asset class does not belong to this calendar."""


class InstrumentScheduleMismatchError(SessionError):
    """A schedule with incompatible instrument conventions was supplied."""


class UnverifiedScheduleError(SessionError):
    """An accurate calendar is required but only an unverified schedule exists."""


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """The session phase for one instrument at one timezone-aware instant."""

    instrument: MarketInstrument
    at: datetime
    state: SessionState
    calendar_name: str
    timezone: str
    session_date: date
    verified: bool
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, MarketInstrument):
            raise ValueError("instrument must be a MarketInstrument")
        if not isinstance(self.at, datetime):
            raise ValueError("at must be a datetime")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        if not isinstance(self.state, SessionState):
            raise ValueError("state must be a SessionState")
        _require_text(self.calendar_name, "calendar_name")
        _require_text(self.timezone, "timezone")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {self.timezone!r}") from None
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise ValueError("session_date must be a date")
        if not isinstance(self.verified, bool):
            raise ValueError("verified must be a boolean")
        if not isinstance(self.notes, tuple):
            raise ValueError("notes must be a tuple of strings")
        for note in self.notes:
            _require_text(note, "note")

    @property
    def is_core_session(self) -> bool:
        """True in an equity regular session or a gold continuous open session."""

        return self.state in (SessionState.REGULAR, SessionState.OPEN)

    @property
    def is_extended_hours(self) -> bool:
        return self.state in (SessionState.PREMARKET, SessionState.POSTMARKET)

    @property
    def is_break(self) -> bool:
        return self.state is SessionState.BREAK


@runtime_checkable
class SessionCalendar(Protocol):
    """A timezone-aware, instrument-specific source of session states."""

    def status_at(self, instrument: MarketInstrument, at: datetime) -> SessionInfo:
        """Return the session state at an aware instant."""
