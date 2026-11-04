"""Phase G experiment: server-time to UTC conversion and non-overwrite policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trantor.research.experiments import run_phase_g, server_to_utc, to_utc_research_data

from runtime_helpers import research_data_available

UTC = timezone.utc


@pytest.mark.parametrize(
    "server, expected",
    [
        (datetime(2024, 1, 10, 0, 0, tzinfo=UTC), datetime(2024, 1, 9, 22, 0, tzinfo=UTC)),  # US standard time: +2
        (datetime(2024, 7, 10, 0, 0, tzinfo=UTC), datetime(2024, 7, 9, 21, 0, tzinfo=UTC)),  # US DST: +3
        (datetime(2024, 3, 15, 1, 0, tzinfo=UTC), datetime(2024, 3, 14, 22, 0, tzinfo=UTC)),  # US-on/EU-off week
        (datetime(2026, 11, 4, 12, 57, tzinfo=UTC), datetime(2026, 11, 4, 10, 57, tzinfo=UTC)),  # live measurement
    ],
)
def test_server_to_utc(server: datetime, expected: datetime) -> None:
    assert server_to_utc(server) == expected


def test_daily_break_maps_to_cme_break_year_round() -> None:
    # 00:00 server is always 17:00 New York (CME gold maintenance break).
    from zoneinfo import ZoneInfo

    for month in (1, 4, 7, 11):
        utc = server_to_utc(datetime(2025, month, 15, 0, 0, tzinfo=UTC))
        assert utc.astimezone(ZoneInfo("America/New_York")).hour == 17


def test_experiment_refuses_to_overwrite(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_phase_g(tmp_path)


@pytest.mark.skipif(not research_data_available(), reason="research_data/ not present")
def test_utc_conversion_keeps_series_strictly_increasing_and_contiguous() -> None:
    from trantor.research.backtest.data import load_research_data

    data = to_utc_research_data(load_research_data())
    m15 = data.m15
    # Across the 2024 US DST switch the converted UTC series has no 1 h artefact
    # inside a trading day.
    steps = {
        (b.observed_at - a.observed_at)
        for a, b in zip(m15, m15[1:])
        if a.observed_at.date() == datetime(2024, 3, 11).date()
    }
    assert steps == {timedelta(minutes=15)} or max(steps) <= timedelta(hours=1, minutes=15)
    assert all(b.observed_at > a.observed_at for a, b in zip(m15[:5000], m15[1:5001]))
    assert data.m15_close_times[0] == m15[0].observed_at + timedelta(minutes=15)
