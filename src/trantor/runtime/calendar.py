"""Economic-calendar integration for the strategy's news rules.

The approved v1 event set is US Nonfarm Payrolls, FOMC decisions/press
conferences and US CPI (spec §12.1). The provider distinguishes five states and
never turns missing data into "no news":

``unavailable``          no calendar source is configured or readable;
``verification_failed``  the source exists but is malformed, unverified or does
                         not cover the decision window;
``stale``                the source has not been re-verified recently enough;
``event_detected``       verified, and a qualifying event is inside the 60-minute
                         score window or the 30-minute safety window;
``verified_clear``       verified, current, covering the window, no event.

Only a verified snapshot becomes a ``NewsContext`` with ``verified=True``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..strategy.xauusd_p import NewsContext, NewsEvent

__all__ = [
    "CalendarError",
    "CalendarSnapshot",
    "FileCalendarProvider",
    "NewsStatus",
    "UnavailableCalendarProvider",
    "evaluate_news",
]

UTC = timezone.utc
_EVENT_FIELDS = {"event_type", "title", "currency", "impact", "scheduled_at", "local_time", "timezone", "revised_at"}
_TOP_FIELDS = {
    "schema",
    "version",
    "source",
    "source_urls",
    "retrieved_at",
    "owner_confirmed",
    "confirmed_by",
    "confirmed_at",
    "coverage",
    "events",
    "notes",
}
_QUALIFYING = ("nfp", "fomc", "cpi")


class CalendarError(ValueError):
    """The calendar source is malformed."""


class NewsStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    VERIFICATION_FAILED = "verification_failed"
    STALE = "stale"
    EVENT_DETECTED = "event_detected"
    VERIFIED_CLEAR = "verified_clear"


@dataclass(frozen=True, slots=True)
class CalendarSnapshot:
    source: str
    retrieved_at: datetime
    verified: bool
    coverage_start: datetime | None
    coverage_end: datetime | None
    events: tuple[NewsEvent, ...] = ()
    failure: str | None = None
    confirmed_at: datetime | None = None
    duplicates_removed: int = 0
    titles: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "retrieved_at": self.retrieved_at.isoformat(),
            "verified": self.verified,
            "coverage_start": None if self.coverage_start is None else self.coverage_start.isoformat(),
            "coverage_end": None if self.coverage_end is None else self.coverage_end.isoformat(),
            "event_count": len(self.events),
            "failure": self.failure,
            "confirmed_at": None if self.confirmed_at is None else self.confirmed_at.isoformat(),
            "duplicates_removed": self.duplicates_removed,
        }


class UnavailableCalendarProvider:
    """Explicit 'no calendar configured' provider."""

    name = "unavailable"

    def refresh(self, now: datetime) -> CalendarSnapshot:
        return CalendarSnapshot(
            source="unavailable",
            retrieved_at=now,
            verified=False,
            coverage_start=None,
            coverage_end=None,
            failure="no economic calendar source is configured",
        )


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CalendarError(f"{label} must be an ISO-8601 string")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CalendarError(f"{label} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise CalendarError(f"{label} must include a UTC offset")
    return parsed.astimezone(UTC)


def _event_time(raw: dict[str, Any], index: int) -> datetime:
    if "scheduled_at" in raw:
        if "local_time" in raw:
            raise CalendarError(f"event {index}: give scheduled_at or local_time, not both")
        return _parse_time(raw["scheduled_at"], f"event {index}.scheduled_at")
    local = raw.get("local_time")
    zone_name = raw.get("timezone")
    if not isinstance(local, str) or not isinstance(zone_name, str):
        raise CalendarError(f"event {index}: scheduled_at or local_time+timezone is required")
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarError(f"event {index}: unknown timezone {zone_name!r}") from exc
    try:
        naive = datetime.fromisoformat(local)
    except ValueError as exc:
        raise CalendarError(f"event {index}: local_time is not ISO-8601") from exc
    if naive.tzinfo is not None:
        raise CalendarError(f"event {index}: local_time must not include an offset")
    # IANA conversion handles DST; never a fixed offset.
    return naive.replace(tzinfo=zone).astimezone(UTC)


def parse_calendar(data: Any, *, source_label: str, now: datetime) -> CalendarSnapshot:
    if not isinstance(data, dict):
        raise CalendarError("calendar must be a JSON object")
    unknown = set(data) - _TOP_FIELDS
    if unknown:
        raise CalendarError(f"unknown calendar fields: {sorted(unknown)}")
    if data.get("schema") != "trantor.news_calendar" or data.get("version") != 1:
        raise CalendarError("calendar schema must be trantor.news_calendar version 1")
    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise CalendarError("calendar source must be named")
    coverage = data.get("coverage")
    if not isinstance(coverage, dict):
        raise CalendarError("coverage {start, end} is required")
    start = _parse_time(coverage.get("start"), "coverage.start")
    end = _parse_time(coverage.get("end"), "coverage.end")
    if end <= start:
        raise CalendarError("coverage.end must be after coverage.start")
    events_raw = data.get("events")
    if not isinstance(events_raw, list):
        raise CalendarError("events must be a list")
    seen: dict[tuple[str, datetime], NewsEvent] = {}
    titles: dict[str, str] = {}
    duplicates = 0
    for index, raw in enumerate(events_raw):
        if not isinstance(raw, dict):
            raise CalendarError(f"event {index} must be an object")
        extra = set(raw) - _EVENT_FIELDS
        if extra:
            raise CalendarError(f"event {index}: unknown fields {sorted(extra)}")
        event_type = str(raw.get("event_type", "")).strip().lower()
        if event_type not in _QUALIFYING:
            raise CalendarError(f"event {index}: event_type must be one of {_QUALIFYING}")
        scheduled = _event_time(raw, index)
        if not start <= scheduled <= end:
            raise CalendarError(f"event {index}: scheduled time is outside the declared coverage")
        event = NewsEvent(
            event_type=event_type,
            currency=str(raw.get("currency", "")).upper(),
            impact=str(raw.get("impact", "")).lower(),
            scheduled_at=scheduled,
        )
        key = (event_type, scheduled)
        if key in seen:
            duplicates += 1
            continue
        seen[key] = event
        titles[f"{event_type}@{scheduled.isoformat()}"] = str(raw.get("title") or event_type.upper())
    confirmed = data.get("owner_confirmed") is True
    confirmed_at = _parse_time(data["confirmed_at"], "confirmed_at") if confirmed and data.get("confirmed_at") else None
    if confirmed and confirmed_at is None:
        raise CalendarError("owner_confirmed calendars must record confirmed_at")
    return CalendarSnapshot(
        source=f"{source_label}: {source.strip()}",
        retrieved_at=now,
        verified=confirmed,
        coverage_start=start,
        coverage_end=end,
        events=tuple(sorted(seen.values(), key=lambda item: item.scheduled_at)),
        failure=None if confirmed else "calendar content is not owner-confirmed",
        confirmed_at=confirmed_at,
        duplicates_removed=duplicates,
        titles=titles,
    )


@dataclass
class FileCalendarProvider:
    """Calendar from an owner-maintained JSON file of official release times."""

    path: Path
    name: str = "file"

    def refresh(self, now: datetime) -> CalendarSnapshot:
        try:
            text = Path(self.path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return CalendarSnapshot(
                source=f"file:{Path(self.path).name}",
                retrieved_at=now,
                verified=False,
                coverage_start=None,
                coverage_end=None,
                failure="calendar file not found",
            )
        except OSError as exc:
            return CalendarSnapshot(
                source=f"file:{Path(self.path).name}",
                retrieved_at=now,
                verified=False,
                coverage_start=None,
                coverage_end=None,
                failure=f"calendar file unreadable: {exc}",
            )
        try:
            data = json.loads(text)
            return parse_calendar(data, source_label=f"file:{Path(self.path).name}", now=now)
        except (CalendarError, json.JSONDecodeError) as exc:
            return CalendarSnapshot(
                source=f"file:{Path(self.path).name}",
                retrieved_at=now,
                verified=False,
                coverage_start=None,
                coverage_end=None,
                failure=f"calendar invalid: {exc}",
            )


@dataclass(frozen=True, slots=True)
class NewsAssessment:
    status: NewsStatus
    context: NewsContext
    reason: str
    upcoming: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "verified_for_strategy": self.context.verified,
            "reason": self.reason,
            "upcoming": list(self.upcoming),
        }


def evaluate_news(
    snapshot: CalendarSnapshot | None,
    *,
    decision_time: datetime,
    max_confirmation_age: timedelta = timedelta(days=7),
    lookahead: timedelta = timedelta(minutes=60),
    safety_window: timedelta = timedelta(minutes=30),
) -> NewsAssessment:
    """Classify the calendar for one decision and build the strategy context."""

    if snapshot is None or snapshot.source == "unavailable":
        return NewsAssessment(NewsStatus.UNAVAILABLE, NewsContext.unavailable(), "no calendar source configured")
    if snapshot.coverage_start is None or snapshot.coverage_end is None:
        return NewsAssessment(
            NewsStatus.UNAVAILABLE if snapshot.failure and "not found" in snapshot.failure else NewsStatus.VERIFICATION_FAILED,
            NewsContext.unavailable(),
            snapshot.failure or "calendar has no coverage",
        )
    upcoming = tuple(
        {"event_type": event.event_type, "scheduled_at": event.scheduled_at.isoformat()}
        for event in snapshot.events
        if decision_time - safety_window <= event.scheduled_at <= decision_time + timedelta(hours=24)
    )
    if not snapshot.verified:
        return NewsAssessment(
            NewsStatus.VERIFICATION_FAILED,
            NewsContext.unavailable(),
            snapshot.failure or "calendar not verified",
            upcoming,
        )
    if snapshot.confirmed_at is None or decision_time - snapshot.confirmed_at > max_confirmation_age:
        return NewsAssessment(
            NewsStatus.STALE,
            NewsContext.unavailable(),
            "calendar has not been re-confirmed within the allowed age",
            upcoming,
        )
    if not (snapshot.coverage_start <= decision_time - safety_window and decision_time + lookahead <= snapshot.coverage_end):
        return NewsAssessment(
            NewsStatus.VERIFICATION_FAILED,
            NewsContext.unavailable(),
            "calendar coverage does not include the decision window",
            upcoming,
        )
    context = NewsContext(
        verified=True,
        complete=True,
        as_of=min(snapshot.retrieved_at, decision_time),
        events=snapshot.events,
        source=snapshot.source,
    )
    window = [
        event
        for event in snapshot.events
        if decision_time - safety_window <= event.scheduled_at <= decision_time + lookahead
    ]
    if window:
        return NewsAssessment(
            NewsStatus.EVENT_DETECTED,
            context,
            f"{len(window)} qualifying event(s) within the safety/score window",
            upcoming,
        )
    return NewsAssessment(NewsStatus.VERIFIED_CLEAR, context, "verified calendar: no qualifying event in window", upcoming)
