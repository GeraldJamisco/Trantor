from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trantor.models import MarketObservation

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "mt5_acquire_xauusd_p.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("mt5_acquire_xauusd_p", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")


def _obs(
    day: date,
    *,
    close: float = 4000.0,
    timeframe: str | None = None,
) -> MarketObservation:
    return MarketObservation(
        symbol=tool.SYMBOL,
        timeframe=timeframe or tool.TIMEFRAME,
        observed_at=datetime.combine(day, time.min, tzinfo=UTC),
        open=close - 5.0,
        high=close + 10.0,
        low=close - 10.0,
        close=close,
        volume=1000.0,
    )


def _obs_at(observed_at: datetime, timeframe: str, *, close: float = 4000.0) -> MarketObservation:
    return MarketObservation(
        symbol=tool.SYMBOL,
        timeframe=timeframe,
        observed_at=observed_at,
        open=close - 5.0,
        high=close + 10.0,
        low=close - 10.0,
        close=close,
        volume=1000.0,
    )


def _chunk_result(index: int, observations, *, status: str = "ok"):
    start = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=index - 1)
    end = start + timedelta(days=1)
    return tool.ChunkResult(
        plan=tool.ChunkPlan(index=index, start=start, end=end),
        status=status,
        attempts=1,
        observations=tuple(observations),
    )


def test_chunk_plan_is_inclusive_and_non_overlapping() -> None:
    plans = tool.chunk_plan(date(2020, 1, 1), date(2020, 1, 5), chunk_days=2)

    assert len(plans) == 3
    assert plans[0].start.date() == date(2020, 1, 1)
    assert plans[0].end.date() == date(2020, 1, 2)
    assert plans[1].start.date() == date(2020, 1, 3)
    assert plans[1].end.date() == date(2020, 1, 4)
    assert plans[2].start.date() == date(2020, 1, 5)
    assert plans[2].end.date() == date(2020, 1, 5)
    assert all(plans[i].end < plans[i + 1].start for i in range(len(plans) - 1))


def test_chunk_plan_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="after"):
        tool.chunk_plan(date(2020, 1, 2), date(2020, 1, 1), chunk_days=2)
    with pytest.raises(ValueError, match="positive"):
        tool.chunk_plan(date(2020, 1, 1), date(2020, 1, 2), chunk_days=0)


def test_timeframe_mapping_and_completed_day_defaults() -> None:
    assert set(tool.TIMEFRAME_MINUTES) == {"1d", "4h", "15m"}
    assert tool.TIMEFRAME_MINUTES["4h"] == 240
    assert tool.TIMEFRAME_MINUTES["15m"] == 15
    assert tool.DEFAULT_CHUNK_DAYS_BY_TIMEFRAME == {
        "1d": 365,
        "4h": 180,
        "15m": 30,
    }
    now = datetime(2026, 10, 22, 19, 40, tzinfo=UTC)
    assert tool.latest_completed_day(now, timeframe="1d") == date(2026, 10, 21)
    assert tool.latest_completed_day(now, timeframe="4h") == date(2026, 10, 22)
    assert tool.latest_completed_day(now, timeframe="15m") == date(2026, 10, 22)


def test_h4_and_m15_boundaries_are_validated_without_shifting() -> None:
    h4_valid = _obs_at(datetime(2026, 10, 22, 16, tzinfo=UTC), "4h")
    h4_invalid = _obs_at(datetime(2026, 10, 22, 17, tzinfo=UTC), "4h")
    _, report = tool.validate_observations(
        (h4_valid, h4_invalid),
        now=datetime(2026, 10, 23, tzinfo=UTC),
        timeframe="4h",
    )
    assert report.unexpected_boundary_timestamps == 1
    assert report.accepted is False

    m15_valid = _obs_at(datetime(2026, 10, 22, 16, 45, tzinfo=UTC), "15m")
    m15_invalid = _obs_at(datetime(2026, 10, 22, 16, 50, tzinfo=UTC), "15m")
    _, report = tool.validate_observations(
        (m15_valid, m15_invalid),
        now=datetime(2026, 10, 23, tzinfo=UTC),
        timeframe="15m",
    )
    assert report.unexpected_boundary_timestamps == 1
    assert report.accepted is False


def test_incomplete_intraday_candles_are_excluded() -> None:
    h4 = _obs_at(datetime(2026, 10, 22, 20, tzinfo=UTC), "4h")
    m15 = _obs_at(datetime(2026, 10, 22, 23, 45, tzinfo=UTC), "15m")

    h4_completed, h4_report = tool.validate_observations(
        (h4,),
        now=datetime(2026, 10, 22, 21, 0, tzinfo=UTC),
        timeframe="4h",
    )
    m15_completed, m15_report = tool.validate_observations(
        (m15,),
        now=datetime(2026, 10, 22, 23, 50, tzinfo=UTC),
        timeframe="15m",
    )

    assert h4_completed == ()
    assert h4_report.incomplete_current_rows == 1
    assert m15_completed == ()
    assert m15_report.incomplete_current_rows == 1


def test_timeframe_mismatch_is_a_structural_error() -> None:
    wrong = _obs_at(datetime(2026, 10, 22, 16, tzinfo=UTC), "1d")
    _, report = tool.validate_observations(
        (wrong,),
        now=datetime(2026, 10, 23, tzinfo=UTC),
        timeframe="4h",
    )

    assert report.accepted is False
    assert any("timeframe" in error for error in report.structural_errors)


def test_gap_report_uses_timeframe_interval() -> None:
    observations = (
        _obs_at(datetime(2026, 10, 22, 0, tzinfo=UTC), "4h"),
        _obs_at(datetime(2026, 10, 22, 12, tzinfo=UTC), "4h"),
    )
    gaps = tool._gap_rows(observations, "4h")
    _, report = tool.validate_observations(
        observations,
        now=datetime(2026, 10, 23, tzinfo=UTC),
        timeframe="4h",
    )

    assert gaps
    assert gaps[0]["gap_seconds"] == 12 * 60 * 60
    assert report.gap_count == 1


def test_cost_rows_are_filtered_to_completed_candles() -> None:
    observations = (
        _obs(date(2020, 1, 1)),
        _obs(date(2020, 1, 2)),
    )
    raw = [
        {"time": int(datetime(2020, 1, 1, tzinfo=UTC).timestamp()), "spread_points": 18, "real_volume": 0, "tick_volume": 100},
        {"time": int(datetime(2020, 1, 2, tzinfo=UTC).timestamp()), "spread_points": 20, "real_volume": 0, "tick_volume": 200},
        {"time": int(datetime(2020, 1, 3, tzinfo=UTC).timestamp()), "spread_points": 22, "real_volume": 0, "tick_volume": 300},
    ]

    rows = tool._cost_rows_for_dataset(raw_rows=raw, observations=observations)

    assert len(rows) == 2
    assert rows[0]["spread_points"] == 18
    assert rows[1]["time_utc"].startswith("2020-01-02")


def test_acquire_retries_only_failed_chunks() -> None:
    calls: list[int] = []
    plans = tool.chunk_plan(date(2020, 1, 1), date(2020, 1, 4), chunk_days=2)

    def fetch(plan):
        calls.append(plan.index)
        if plan.index == 1 and calls.count(1) < 3:
            raise RuntimeError("temporary failure")
        return (_obs(plan.start.date()),)

    results = tool.acquire_chunks(plans, fetch, retries=2)

    assert [result.status for result in results] == ["ok", "ok"]
    assert [result.attempts for result in results] == [3, 1]
    assert calls == [1, 1, 1, 2]


def test_acquire_stops_after_bounded_retries() -> None:
    plans = tool.chunk_plan(date(2020, 1, 1), date(2020, 1, 2), chunk_days=1)

    def fetch(plan):
        raise RuntimeError("unavailable")

    results = tool.acquire_chunks(plans, fetch, retries=1)

    assert results[0].status == "failed"
    assert results[0].attempts == 2
    assert results[0].observations == ()


def test_acquire_records_timeout_and_retries() -> None:
    calls = []

    def fetch(plan):
        calls.append(plan.index)
        raise TimeoutError("chunk hung")

    plans = tool.chunk_plan(date(2020, 1, 1), date(2020, 1, 4), chunk_days=2)
    results = tool.acquire_chunks(plans, fetch, retries=1)

    assert [result.status for result in results] == ["timeout", "timeout"]
    assert [result.attempts for result in results] == [2, 2]
    assert calls == [1, 1, 2, 2]


def test_empty_chunk_is_not_retried() -> None:
    calls = []

    def fetch(plan):
        calls.append(plan.index)
        return ()

    plans = tool.chunk_plan(date(2020, 1, 1), date(2020, 1, 4), chunk_days=2)
    results = tool.acquire_chunks(plans, fetch, retries=2)

    assert [result.status for result in results] == ["empty", "empty"]
    assert calls == [1, 2]


def test_validation_accepts_clean_completed_daily_rows() -> None:
    observations = (_obs(date(2020, 1, 1)), _obs(date(2020, 1, 2)))
    completed, report = tool.validate_observations(observations, now=datetime(2020, 1, 3, tzinfo=UTC))

    assert len(completed) == 2
    assert report.accepted is True
    assert report.structural_errors == []
    assert report.duplicate_timestamps == 0
    assert report.unordered_pairs == 0
    assert report.invalid_ohlc_rows == 0


def test_validation_rejects_duplicate_and_unordered_timestamps() -> None:
    duplicate = _obs(date(2020, 1, 1))
    observations = (duplicate, duplicate, _obs(date(2020, 1, 2)))
    _, report = tool.validate_observations(observations, now=datetime(2020, 1, 3, tzinfo=UTC))

    assert report.duplicate_timestamps == 1
    assert report.structural_errors

    unordered = (_obs(date(2020, 1, 2)), _obs(date(2020, 1, 1)))
    _, report = tool.validate_observations(
        unordered,
        now=datetime(2020, 1, 3, tzinfo=UTC),
    )
    assert report.unordered_pairs == 1
    assert report.accepted is False


def test_validation_rejects_non_utc_and_non_midnight_timestamps() -> None:
    local_midnight = MarketObservation(
        symbol=tool.SYMBOL,
        timeframe=tool.TIMEFRAME,
        observed_at=datetime(2020, 1, 2, 0, 0, tzinfo=NEW_YORK),
        open=4000.0,
        high=4010.0,
        low=3990.0,
        close=4005.0,
        volume=1.0,
    )
    _, report = tool.validate_observations(
        (local_midnight,),
        now=datetime(2020, 1, 4, tzinfo=UTC),
    )

    assert report.non_utc_timestamps == 1
    assert report.non_midnight_timestamps == 1
    assert report.accepted is False


def test_validation_rejects_invalid_ohlc_and_nonfinite_values() -> None:
    observation = _obs(date(2020, 1, 2))
    object.__setattr__(observation, "high", 1.0)
    _, report = tool.validate_observations(
        (observation,),
        now=datetime(2020, 1, 3, tzinfo=UTC),
    )

    assert report.invalid_ohlc_rows == 1
    assert report.accepted is False

    nonfinite = _obs(date(2020, 1, 2))
    object.__setattr__(nonfinite, "close", float("nan"))
    _, report = tool.validate_observations(
        (nonfinite,),
        now=datetime(2020, 1, 3, tzinfo=UTC),
    )
    assert report.invalid_ohlc_rows == 1


def test_validation_drops_incomplete_current_candle() -> None:
    observations = (
        _obs(date(2020, 1, 1)),
        _obs(date(2020, 1, 2)),
    )
    completed, report = tool.validate_observations(
        observations,
        now=datetime(2020, 1, 2, 12, tzinfo=UTC),
    )

    assert len(completed) == 1
    assert report.incomplete_current_rows == 1
    assert report.accepted is True


def test_validation_reports_weekday_gaps_without_classifying_them() -> None:
    observations = (
        _obs(date(2020, 1, 6)),  # Monday
        _obs(date(2020, 1, 8)),  # Wednesday; Tuesday missing
    )
    _, report = tool.validate_observations(
        observations,
        now=datetime(2020, 1, 10, tzinfo=UTC),
    )

    assert report.weekday_gap_count == 1
    assert "2020-01-07" in report.weekday_gap_sample
    assert report.warnings
    assert report.accepted is True


def test_validation_reports_overlapping_chunk_boundaries() -> None:
    first = _chunk_result(1, (_obs(date(2020, 1, 1)),))
    second = _chunk_result(2, (_obs(date(2020, 1, 1)),))
    _, report = tool.validate_observations(
        (_obs(date(2020, 1, 1)),),
        now=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_results=(first, second),
    )

    assert report.chunk_boundary_issues
    assert "chunk 2" in report.chunk_boundary_issues[0]


def test_repeatability_failure_is_a_structural_error() -> None:
    _, report = tool.validate_observations(
        (_obs(date(2020, 1, 1)),),
        now=datetime(2020, 1, 3, tzinfo=UTC),
        repeatability_ok=False,
        repeatability_error="rows changed",
    )

    assert report.accepted is False
    assert "repeatability" in report.structural_errors[0]


def test_canonical_csv_is_deterministic() -> None:
    observations = (_obs(date(2020, 1, 1)), _obs(date(2020, 1, 2), close=4010.0))
    first = tool._canonical_csv_bytes(observations)
    second = tool._canonical_csv_bytes(observations)

    assert first == second
    assert first.startswith(b"time_utc,open,high,low,close,volume\n")


def test_write_dataset_records_metadata_and_does_not_overwrite(tmp_path: Path) -> None:
    observations = (_obs(date(2020, 1, 1)), _obs(date(2020, 1, 2)))
    chunk = _chunk_result(1, observations)
    _, validation = tool.validate_observations(
        observations,
        now=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_results=(chunk,),
    )
    assert validation.accepted is True

    first = tool.write_dataset(
        output_root=tmp_path / "runs",
        observations=observations,
        chunk_results=(chunk,),
        validation=validation,
        requested_start=datetime(2020, 1, 1, tzinfo=UTC),
        requested_end=datetime(2020, 1, 2, 23, 59, 59, tzinfo=UTC),
        acquired_at=datetime(2020, 1, 3, 12, tzinfo=UTC),
        chunk_days=2,
    )
    second = tool.write_dataset(
        output_root=tmp_path / "runs",
        observations=observations,
        chunk_results=(chunk,),
        validation=validation,
        requested_start=datetime(2020, 1, 1, tzinfo=UTC),
        requested_end=datetime(2020, 1, 2, 23, 59, 59, tzinfo=UTC),
        acquired_at=datetime(2020, 1, 3, 12, tzinfo=UTC),
        chunk_days=2,
    )

    assert first != second
    metadata = json.loads((first / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["symbol"] == "XAUUSD-P"
    assert metadata["timeframe"] == "1d"
    assert metadata["row_count"] == 2
    assert metadata["checksum_sha256"] == __import__("hashlib").sha256(
        (first / "candles.csv").read_bytes()
    ).hexdigest()
    assert (first / "validation.json").exists()


def test_write_dataset_preserves_spread_and_real_volume(tmp_path: Path) -> None:
    observations = (_obs(date(2020, 1, 1)),)
    chunk = _chunk_result(1, observations)
    _, validation = tool.validate_observations(
        observations,
        now=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_results=(chunk,),
    )
    cost_rows = [
        {
            "time_utc": observations[0].observed_at.isoformat(),
            "spread_points": 18,
            "real_volume": 0.0,
            "tick_volume": 1000.0,
        }
    ]

    dataset = tool.write_dataset(
        output_root=tmp_path / "runs",
        observations=observations,
        chunk_results=(chunk,),
        validation=validation,
        requested_start=datetime(2020, 1, 1, tzinfo=UTC),
        requested_end=datetime(2020, 1, 1, 23, 59, 59, tzinfo=UTC),
        acquired_at=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_days=1,
        cost_rows=cost_rows,
    )

    cost_path = dataset / "spread_volume.csv"
    metadata = json.loads((dataset / "metadata.json").read_text(encoding="utf-8"))
    assert cost_path.exists()
    assert cost_path.read_text(encoding="utf-8").startswith(
        "time_utc,spread_points,real_volume,tick_volume\n"
    )
    assert metadata["cost_checksum_sha256"] == __import__("hashlib").sha256(
        cost_path.read_bytes()
    ).hexdigest()


def test_audit_refetch_reports_new_rows_separately(tmp_path: Path) -> None:
    observations = (_obs(date(2020, 1, 1)),)
    plan = tool.ChunkPlan(
        index=1,
        start=datetime(2020, 1, 1, tzinfo=UTC),
        end=datetime(2020, 1, 1, 23, 59, 59, tzinfo=UTC),
    )
    chunk = tool.ChunkResult(
        plan=plan,
        status="ok",
        attempts=1,
        observations=observations,
    )
    _, validation = tool.validate_observations(
        observations,
        now=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_results=(chunk,),
    )
    dataset = tool.write_dataset(
        output_root=tmp_path / "runs",
        observations=observations,
        chunk_results=(chunk,),
        validation=validation,
        requested_start=plan.start,
        requested_end=plan.end,
        acquired_at=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_days=1,
    )

    fresh = observations + (_obs(date(2020, 1, 2)),)
    result = tool.audit_saved_dataset(
        dataset,
        now=datetime(2020, 1, 3, tzinfo=UTC),
        refetch=lambda item: fresh,
    )

    assert result["refetch_ok"] is True
    assert result["refetch_changes"] == []
    assert result["refetch_new_rows"] == 1


def test_audit_detects_checksum_changes(tmp_path: Path) -> None:
    observations = (_obs(date(2020, 1, 1)),)
    chunk = _chunk_result(1, observations)
    _, validation = tool.validate_observations(
        observations,
        now=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_results=(chunk,),
    )
    dataset = tool.write_dataset(
        output_root=tmp_path / "runs",
        observations=observations,
        chunk_results=(chunk,),
        validation=validation,
        requested_start=datetime(2020, 1, 1, tzinfo=UTC),
        requested_end=datetime(2020, 1, 1, 23, 59, 59, tzinfo=UTC),
        acquired_at=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_days=1,
    )
    (dataset / "candles.csv").write_bytes(b"tampered\n")

    with pytest.raises(ValueError, match="checksum"):
        tool.audit_saved_dataset(
            dataset,
            now=datetime(2020, 1, 3, tzinfo=UTC),
        )


def test_audit_refetch_comparison_detects_changes(tmp_path: Path) -> None:
    observations = (_obs(date(2020, 1, 1)), _obs(date(2020, 1, 2)))
    plan = tool.ChunkPlan(
        index=1,
        start=datetime(2020, 1, 1, tzinfo=UTC),
        end=datetime(2020, 1, 2, 23, 59, 59, tzinfo=UTC),
    )
    chunk = tool.ChunkResult(
        plan=plan,
        status="ok",
        attempts=1,
        observations=observations,
    )
    _, validation = tool.validate_observations(
        observations,
        now=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_results=(chunk,),
    )
    dataset = tool.write_dataset(
        output_root=tmp_path / "runs",
        observations=observations,
        chunk_results=(chunk,),
        validation=validation,
        requested_start=plan.start,
        requested_end=plan.end,
        acquired_at=datetime(2020, 1, 3, tzinfo=UTC),
        chunk_days=2,
    )

    same = tool.audit_saved_dataset(
        dataset,
        now=datetime(2020, 1, 3, tzinfo=UTC),
        refetch=lambda item: observations,
    )
    changed = tool.audit_saved_dataset(
        dataset,
        now=datetime(2020, 1, 3, tzinfo=UTC),
        refetch=lambda item: (_obs(date(2020, 1, 1), close=3999.0),),
    )

    assert same["refetch_ok"] is True
    assert changed["refetch_ok"] is False
    assert changed["refetch_changes"]


def test_worker_payload_round_trip() -> None:
    observations = (_obs(date(2020, 1, 1)), _obs(date(2020, 1, 2)))
    payload = tool._observations_payload(observations)
    restored = tool._observations_from_payload(payload, tool.TIMEFRAME)

    assert restored == observations


def test_symbol_is_fixed_to_xauusd_p() -> None:
    assert tool.SYMBOL == "XAUUSD-P"
    assert tool.TIMEFRAME == "1d"
    assert tool.XAUUSD_P_INSTRUMENT.symbol == "XAUUSD-P"
