from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from trantor.market_data import (
    GoldConvention,
    GoldSessionCalendar,
    GoldSessionSchedule,
    InstrumentScheduleMismatchError,
    RecurringLocalWindow,
    SessionCalendarRangeError,
    SessionOverride,
    SessionState,
    UnsupportedGoldInstrumentError,
    UnverifiedScheduleError,
    comex_gold_futures_schedule,
    gold_session_calendar_for,
)
from trantor.models import AssetClass, MarketInstrument

UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")
GC = MarketInstrument(
    "GC", AssetClass.GOLD_FUTURES, "COMEX", "USD", "America/New_York"
)
MGC = MarketInstrument(
    "MGC", AssetClass.GOLD_FUTURES, "COMEX", "USD", "America/New_York"
)
GLD = MarketInstrument(
    "GLD", AssetClass.GOLD_ETF, "NYSE Arca", "USD", "America/New_York"
)
XAUUSD = MarketInstrument("XAUUSD", AssetClass.GOLD_SPOT, "BROKER", "USD", "UTC")
AAPL = MarketInstrument(
    "AAPL", AssetClass.STOCK, "NASDAQ", "USD", "America/New_York"
)
SIL = MarketInstrument(
    "SIL", AssetClass.GOLD_FUTURES, "COMEX", "USD", "America/New_York"
)


@pytest.fixture(scope="module")
def comex_calendar() -> GoldSessionCalendar:
    return GoldSessionCalendar(comex_gold_futures_schedule())


def _ny(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=NEW_YORK)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _state(calendar: GoldSessionCalendar, at: datetime) -> SessionState:
    return calendar.status_at(GC, at).state


def test_default_schedule_is_verified_for_documented_range(
    comex_calendar: GoldSessionCalendar,
) -> None:
    schedule = comex_gold_futures_schedule()

    assert schedule.verified is True
    assert schedule.verified_start == date(2026, 1, 1)
    assert schedule.verified_end == date(2027, 12, 31)
    assert schedule.timezone == "America/New_York"
    assert "CME Group" in schedule.source_note
    assert "2026-01-01 through 2027-12-31" in schedule.source_note
    assert any(
        "2026-01-01 through 2027-12-31" in warning
        for warning in schedule.warnings
    )
    assert len(schedule.overrides) >= 20
    assert schedule.overrides[0].start_date == date(2025, 12, 31)

    status = comex_calendar.status_at(
        GC,
        _ny("2026-10-26T10:00:00"),
        require_verified=True,
    )
    assert status.verified is True
    assert status.calendar_name == "comex_gold_futures_verified_2026_2027"


def test_normal_week_boundaries_and_daily_break(
    comex_calendar: GoldSessionCalendar,
) -> None:
    assert _state(comex_calendar, _ny("2026-10-25T17:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-10-25T18:00:00")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-10-26T16:59:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-10-26T17:00:00")) is SessionState.BREAK
    assert _state(comex_calendar, _ny("2026-10-26T17:59:59")) is SessionState.BREAK
    assert _state(comex_calendar, _ny("2026-10-26T18:00:00")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-10-23T16:59:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-10-23T17:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-10-24T12:00:00")) is SessionState.CLOSED


def test_break_and_open_properties(comex_calendar: GoldSessionCalendar) -> None:
    open_status = comex_calendar.status_at(GC, _ny("2026-10-26T10:00:00"))
    break_status = comex_calendar.status_at(GC, _ny("2026-10-26T17:30:00"))

    assert open_status.is_core_session is True
    assert open_status.is_break is False
    assert break_status.is_core_session is False
    assert break_status.is_break is True


def test_dst_transition_day_boundaries(
    comex_calendar: GoldSessionCalendar,
) -> None:
    # Spring forward: Sunday 18:00 is already EDT (22:00 UTC).
    assert _state(comex_calendar, _utc("2026-03-08T21:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _utc("2026-03-08T22:00:00")) is SessionState.OPEN
    # Fall back: Sunday 18:00 is back on EST (23:00 UTC).
    assert _state(comex_calendar, _utc("2026-11-01T22:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _utc("2026-11-01T23:00:00")) is SessionState.OPEN


def test_gold_utc_conversion_in_winter_and_summer(
    comex_calendar: GoldSessionCalendar,
) -> None:
    # EST: Sunday 18:00 New York == 23:00 UTC.
    assert _state(comex_calendar, _utc("2026-01-04T22:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _utc("2026-01-04T23:00:00")) is SessionState.OPEN
    # EDT: Sunday 18:00 New York == 22:00 UTC.
    assert _state(comex_calendar, _utc("2026-07-05T21:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _utc("2026-07-05T22:00:00")) is SessionState.OPEN


def test_gold_daily_break_uses_dst_aware_boundaries(
    comex_calendar: GoldSessionCalendar,
) -> None:
    # EST: Monday 17:00 New York == 22:00 UTC.
    assert _state(comex_calendar, _utc("2026-01-05T22:00:00")) is SessionState.BREAK
    assert _state(comex_calendar, _utc("2026-01-05T23:00:00")) is SessionState.OPEN
    # EDT: Monday 17:00 New York == 21:00 UTC.
    assert _state(comex_calendar, _utc("2026-07-06T21:00:00")) is SessionState.BREAK
    assert _state(comex_calendar, _utc("2026-07-06T22:00:00")) is SessionState.OPEN


@pytest.mark.parametrize(
    "at",
    [
        _ny("2025-12-31T12:00:00"),
        _ny("2028-01-01T00:00:00"),
        _utc("2028-01-01T05:00:00"),  # 00:00 New York
    ],
)
def test_dates_outside_verified_range_raise_explicitly(
    comex_calendar: GoldSessionCalendar,
    at: datetime,
) -> None:
    with pytest.raises(
        SessionCalendarRangeError,
        match=r"verified only for 2026-01-01 through 2027-12-31",
    ):
        comex_calendar.status_at(GC, at)


def test_verified_range_endpoints_are_evaluated(
    comex_calendar: GoldSessionCalendar,
) -> None:
    assert _state(comex_calendar, _ny("2026-01-01T00:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-12-31T23:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _utc("2026-01-01T05:00:00")) is SessionState.CLOSED
    assert (
        _state(comex_calendar, _utc("2028-01-01T04:59:59"))
        is SessionState.CLOSED
    )


def test_every_documented_date_is_evaluable(
    comex_calendar: GoldSessionCalendar,
) -> None:
    day = date(2026, 1, 1)
    last_day = date(2027, 12, 31)
    while day <= last_day:
        status = comex_calendar.status_at(
            GC,
            datetime.combine(day, time(12, 0), tzinfo=NEW_YORK),
        )
        assert status.session_date == day
        day += timedelta(days=1)


def test_all_in_range_override_boundaries_are_end_exclusive(
    comex_calendar: GoldSessionCalendar,
) -> None:
    start = date(2026, 1, 1)
    end = date(2027, 12, 31)

    for override in comex_gold_futures_schedule().overrides:
        if override.start_date < start or override.end_date > end:
            continue
        override_start = datetime.combine(
            override.start_date,
            override.start,
            tzinfo=NEW_YORK,
        )
        override_end = datetime.combine(
            override.end_date,
            override.end,
            tzinfo=NEW_YORK,
        )
        assert comex_calendar.status_at(GC, override_start).state is override.state
        assert (
            comex_calendar.status_at(
                GC,
                override_end - timedelta(seconds=1),
            ).state
            is override.state
        )
        assert comex_calendar.status_at(GC, override_end).state is not override.state


def test_new_year_closures(
    comex_calendar: GoldSessionCalendar,
) -> None:
    assert _state(comex_calendar, _ny("2026-01-01T00:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-01-01T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-01-01T17:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-01-01T18:00:00")) is SessionState.OPEN

    assert _state(comex_calendar, _ny("2026-12-31T16:59:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-12-31T17:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-12-31T17:30:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-01-01T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-01-02T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-01-03T17:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-01-03T18:00:00")) is SessionState.OPEN


@pytest.mark.parametrize(
    "holiday",
    [
        "2026-01-19",
        "2026-02-16",
        "2026-05-25",
        "2026-09-07",
        "2027-01-18",
        "2027-02-15",
        "2027-05-31",
        "2027-07-05",
        "2027-09-06",
    ],
)
def test_monday_holiday_halts_and_reopens(
    comex_calendar: GoldSessionCalendar,
    holiday: str,
) -> None:
    assert _state(comex_calendar, _ny(f"{holiday}T14:29:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny(f"{holiday}T14:30:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny(f"{holiday}T17:30:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny(f"{holiday}T18:00:00")) is SessionState.OPEN


def test_extra_february_2026_break_is_explicit(
    comex_calendar: GoldSessionCalendar,
) -> None:
    assert _state(comex_calendar, _ny("2026-02-25T14:30:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-02-25T14:31:00")) is SessionState.BREAK
    assert _state(comex_calendar, _ny("2026-02-25T14:44:59")) is SessionState.BREAK
    assert _state(comex_calendar, _ny("2026-02-25T14:45:00")) is SessionState.OPEN


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        ("2026-04-02T16:59:59", SessionState.OPEN),
        ("2026-04-02T17:00:00", SessionState.CLOSED),
        ("2026-04-03T12:00:00", SessionState.CLOSED),
        ("2026-04-04T12:00:00", SessionState.CLOSED),
        ("2026-04-05T17:59:59", SessionState.CLOSED),
        ("2026-04-05T18:00:00", SessionState.OPEN),
        ("2027-03-25T16:59:59", SessionState.OPEN),
        ("2027-03-25T17:00:00", SessionState.CLOSED),
        ("2027-03-26T12:00:00", SessionState.CLOSED),
        ("2027-03-27T12:00:00", SessionState.CLOSED),
        ("2027-03-28T17:59:59", SessionState.CLOSED),
        ("2027-03-28T18:00:00", SessionState.OPEN),
    ],
)
def test_good_friday_closures(
    comex_calendar: GoldSessionCalendar,
    at: str,
    expected: SessionState,
) -> None:
    assert _state(comex_calendar, _ny(at)) is expected


def test_juneteenth_and_independence_day_sessions(
    comex_calendar: GoldSessionCalendar,
) -> None:
    assert _state(comex_calendar, _ny("2026-06-19T12:59:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-06-19T13:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-06-19T17:30:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-06-20T05:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-06-20T06:00:00")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-06-20T12:00:00")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-06-20T17:59:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-06-20T18:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-06-21T17:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-06-21T18:00:00")) is SessionState.OPEN

    assert _state(comex_calendar, _ny("2026-07-03T12:59:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-07-03T13:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-07-04T05:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-07-04T06:00:00")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-07-04T18:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-07-05T18:00:00")) is SessionState.OPEN

    assert _state(comex_calendar, _ny("2027-06-18T12:59:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2027-06-18T13:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-06-19T06:00:00")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2027-06-19T18:00:00")) is SessionState.CLOSED


def test_thanksgiving_and_day_after_sessions(
    comex_calendar: GoldSessionCalendar,
) -> None:
    assert _state(comex_calendar, _ny("2026-11-26T14:29:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-11-26T14:30:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-11-26T17:30:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-11-26T18:00:00")) is SessionState.OPEN

    assert _state(comex_calendar, _ny("2026-11-27T14:44:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-11-27T14:45:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-11-27T17:30:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-11-28T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-11-29T17:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-11-29T18:00:00")) is SessionState.OPEN

    assert _state(comex_calendar, _ny("2027-11-25T14:30:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-11-25T18:00:00")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2027-11-26T14:44:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2027-11-26T14:45:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-11-27T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-11-28T18:00:00")) is SessionState.OPEN


def test_christmas_closures(
    comex_calendar: GoldSessionCalendar,
) -> None:
    assert _state(comex_calendar, _ny("2026-12-24T13:44:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2026-12-24T13:45:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-12-25T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-12-26T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-12-27T17:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2026-12-27T18:00:00")) is SessionState.OPEN

    assert _state(comex_calendar, _ny("2027-12-23T16:59:59")) is SessionState.OPEN
    assert _state(comex_calendar, _ny("2027-12-23T17:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-12-24T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-12-25T12:00:00")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-12-26T17:59:59")) is SessionState.CLOSED
    assert _state(comex_calendar, _ny("2027-12-26T18:00:00")) is SessionState.OPEN


def test_override_notes_are_attached_to_status(
    comex_calendar: GoldSessionCalendar,
) -> None:
    status = comex_calendar.status_at(GC, _ny("2026-01-19T14:30:00"))

    assert status.state is SessionState.CLOSED
    assert status.session_date == date(2026, 1, 19)
    assert any("CME Group" in note for note in status.notes)
    assert any("Martin Luther King Jr. Day 2026" in note for note in status.notes)


def test_unverified_custom_schedule_remains_rejected() -> None:
    schedule = GoldSessionSchedule(
        name="custom_unverified_comex",
        convention=GoldConvention.COMEX_FUTURES,
        timezone="America/New_York",
        sessions=(RecurringLocalWindow(0, time(0, 0), 6, time(23, 59)),),
        verified=False,
        source_note="Synthetic unverified schedule for safety tests.",
    )
    calendar = gold_session_calendar_for(GC, futures_schedule=schedule)

    status = calendar.status_at(GC, _ny("2026-10-26T10:00:00"))
    assert status.state is SessionState.OPEN
    assert status.verified is False
    with pytest.raises(UnverifiedScheduleError, match="not verified"):
        calendar.status_at(
            GC,
            _ny("2026-10-26T10:00:00"),
            require_verified=True,
        )


def test_gc_and_mgc_share_the_comex_default_but_are_distinct_instruments(
    comex_calendar: GoldSessionCalendar,
) -> None:
    at = _ny("2026-10-26T10:00:00")

    gc_status = comex_calendar.status_at(GC, at)
    mgc_status = comex_calendar.status_at(MGC, at)

    assert gc_status.state is mgc_status.state is SessionState.OPEN
    assert gc_status.instrument is GC
    assert mgc_status.instrument is MGC


@pytest.mark.parametrize("instrument", [GLD, XAUUSD, AAPL])
def test_non_futures_instruments_are_rejected(
    instrument: MarketInstrument,
) -> None:
    with pytest.raises(UnsupportedGoldInstrumentError, match="gold futures"):
        gold_session_calendar_for(instrument)


def test_other_gold_futures_symbols_are_rejected(
    comex_calendar: GoldSessionCalendar,
) -> None:
    with pytest.raises(UnsupportedGoldInstrumentError, match="only COMEX GC and MGC"):
        gold_session_calendar_for(SIL)

    with pytest.raises(UnsupportedGoldInstrumentError, match="only COMEX GC and MGC"):
        comex_calendar.status_at(SIL, _ny("2026-10-26T10:00:00"))


def test_gold_calendar_rejects_mismatched_asset_class(
    comex_calendar: GoldSessionCalendar,
) -> None:
    with pytest.raises(InstrumentScheduleMismatchError, match="requires asset class"):
        comex_calendar.status_at(GLD, _ny("2026-10-26T10:00:00"))


def test_custom_futures_schedule_is_instrument_specific() -> None:
    custom = GoldSessionSchedule(
        name="custom_comex_hours",
        convention=GoldConvention.COMEX_FUTURES,
        timezone="America/New_York",
        sessions=tuple(
            RecurringLocalWindow(day, time(8, 0), 0, time(12, 0))
            for day in range(0, 5)
        ),
        verified=True,
        source_note="Configured test schedule.",
    )
    calendar = gold_session_calendar_for(GC, futures_schedule=custom)

    assert calendar.status_at(GC, _ny("2026-10-26T09:00:00")).state is SessionState.OPEN
    assert calendar.status_at(GC, _ny("2026-10-26T12:00:00")).state is SessionState.CLOSED
    assert calendar.status_at(GC, _ny("2026-10-26T09:00:00")).verified is True
    with pytest.raises(InstrumentScheduleMismatchError, match="requires asset class"):
        calendar.status_at(GLD, _ny("2026-10-26T09:00:00"))


def test_schedule_rejects_invalid_windows() -> None:
    with pytest.raises(ValueError, match="start_weekday"):
        RecurringLocalWindow(7, time(8, 0), 0, time(9, 0))
    with pytest.raises(ValueError, match="end_weekday_offset"):
        RecurringLocalWindow(0, time(8, 0), 7, time(9, 0))
    with pytest.raises(ValueError, match="same-day"):
        RecurringLocalWindow(0, time(9, 0), 0, time(8, 0))
    with pytest.raises(ValueError, match="tzinfo"):
        RecurringLocalWindow(
            0,
            time(8, 0, tzinfo=UTC),
            0,
            time(9, 0),
        )


def test_schedule_rejects_invalid_definition() -> None:
    with pytest.raises(ValueError, match="sessions"):
        GoldSessionSchedule(
            name="bad",
            convention=GoldConvention.COMEX_FUTURES,
            timezone="UTC",
            sessions=(),
        )
    with pytest.raises(ValueError, match="timezone"):
        GoldSessionSchedule(
            name="bad",
            convention=GoldConvention.COMEX_FUTURES,
            timezone="Mars/Olympus",
            sessions=(RecurringLocalWindow(0, time(8, 0), 0, time(9, 0)),),
        )


def test_schedule_rejects_invalid_verified_range() -> None:
    session = (RecurringLocalWindow(0, time(8, 0), 0, time(9, 0)),)
    with pytest.raises(ValueError, match="provided together"):
        GoldSessionSchedule(
            name="bad_range",
            convention=GoldConvention.COMEX_FUTURES,
            timezone="UTC",
            sessions=session,
            verified_start=date(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="not be after"):
        GoldSessionSchedule(
            name="bad_range",
            convention=GoldConvention.COMEX_FUTURES,
            timezone="UTC",
            sessions=session,
            verified_start=date(2027, 1, 1),
            verified_end=date(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="must be a date"):
        GoldSessionSchedule(
            name="bad_range",
            convention=GoldConvention.COMEX_FUTURES,
            timezone="UTC",
            sessions=session,
            verified_start=datetime(2026, 1, 1, tzinfo=UTC),  # type: ignore[arg-type]
            verified_end=date(2026, 12, 31),
        )


def test_session_override_rejects_invalid_intervals() -> None:
    with pytest.raises(ValueError, match="end after"):
        SessionOverride(
            date(2026, 1, 19),
            time(14, 30),
            date(2026, 1, 19),
            time(14, 30),
            SessionState.CLOSED,
            "bad interval",
        )
    with pytest.raises(ValueError, match="state"):
        SessionOverride(
            date(2026, 1, 19),
            time(14, 30),
            date(2026, 1, 19),
            time(18, 0),
            SessionState.PREMARKET,
            "bad state",
        )
    with pytest.raises(ValueError, match="tzinfo"):
        SessionOverride(
            date(2026, 1, 19),
            time(14, 30, tzinfo=UTC),
            date(2026, 1, 19),
            time(18, 0),
            SessionState.CLOSED,
            "tz-aware time",
        )
    with pytest.raises(ValueError, match="reason"):
        SessionOverride(
            date(2026, 1, 19),
            time(14, 30),
            date(2026, 1, 19),
            time(18, 0),
            SessionState.CLOSED,
            " ",
        )


def test_recurring_window_requires_aware_input() -> None:
    window = RecurringLocalWindow(0, time(8, 0), 0, time(9, 0))

    with pytest.raises(ValueError, match="timezone-aware"):
        window.contains(datetime(2026, 10, 26, 8, 30), NEW_YORK)


def test_session_override_requires_aware_input() -> None:
    override = SessionOverride(
        date(2026, 1, 19),
        time(14, 30),
        date(2026, 1, 19),
        time(18, 0),
        SessionState.CLOSED,
        "test override",
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        override.contains(datetime(2026, 1, 19, 15, 0), NEW_YORK)


def test_gold_calendar_rejects_naive_datetime(
    comex_calendar: GoldSessionCalendar,
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        comex_calendar.status_at(GC, datetime(2026, 10, 26, 10, 0))
