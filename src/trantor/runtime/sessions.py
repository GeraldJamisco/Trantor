"""Session status for operators.

The entry rule itself lives in the strategy (IANA London/New York overlap,
spec §12.2). This module only reports the same windows for display, with DST
handled by ``zoneinfo``. The broker's own XAUUSD-P trading calendar is not
exposed by the MT5 Python API and stays unverified; observed tick activity is
reported separately and is not proof of a scheduled session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..strategy.xauusd_p import LONDON, NEW_YORK, XAUUSDPMTFParameters

__all__ = ["session_snapshot"]

UTC = timezone.utc


def _window(zone, start, end, at: datetime) -> tuple[datetime, datetime]:
    local_date = at.astimezone(zone).date()
    return (
        datetime.combine(local_date, start, tzinfo=zone).astimezone(UTC),
        datetime.combine(local_date, end, tzinfo=zone).astimezone(UTC),
    )


def session_snapshot(
    now: datetime,
    parameters: XAUUSDPMTFParameters | None = None,
    *,
    feed_live: bool,
) -> dict[str, Any]:
    params = parameters or XAUUSDPMTFParameters()
    now = now.astimezone(UTC)
    london = _window(LONDON, params.london_start, params.london_end, now)
    new_york = _window(NEW_YORK, params.new_york_start, params.new_york_end, now)
    start, end = max(london[0], new_york[0]), min(london[1], new_york[1])
    return {
        "now_utc": now.isoformat(),
        "london": {"open_utc": london[0].isoformat(), "close_utc": london[1].isoformat(), "active": london[0] <= now < london[1]},
        "new_york": {"open_utc": new_york[0].isoformat(), "close_utc": new_york[1].isoformat(), "active": new_york[0] <= now < new_york[1]},
        "overlap": {
            "open_utc": start.isoformat() if start < end else None,
            "close_utc": end.isoformat() if start < end else None,
            "active": start <= now < end,
        },
        "broker_calendar_verified": False,
        "broker_ticks_observed": feed_live,
        "note": "IANA London/New York windows; XAUUSD-P broker calendar unverified; ticks are not proof of a scheduled session",
    }
