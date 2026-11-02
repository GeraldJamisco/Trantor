from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from trantor.config import StrategyStatus, load_strategy_config
from trantor.scanners import (
    EvaluationOutcome,
    GoldOpportunityAnalyzer,
    ScanOutcome,
)
from trantor.scanners.replay import (
    GoldReplayFrame,
    ReplayError,
    load_replay_frame,
)
from phase3_helpers import (
    GOLD_PATH,
    PHASE3_FIXTURES,
    unverified_gold_schedule,
    verified_gold_schedule,
)

GOLD_FIXTURE = PHASE3_FIXTURES / "phase3_gold_replay.json"


def _payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: dict, name: str = "replay.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_gold_replay_fixture_runs_through_phase2_validation() -> None:
    frame = load_replay_frame(GOLD_FIXTURE)

    assert isinstance(frame, GoldReplayFrame)
    assert "synthetic" in frame.disclaimer.lower()
    assert [item.instrument.symbol for item in frame.inputs] == ["GC", "MGC"]
    report = frame.run(
        GoldOpportunityAnalyzer(
            load_strategy_config(GOLD_PATH),
            futures_schedule=verified_gold_schedule(),
        )
    )

    assert report.status is ScanOutcome.CANDIDATES_FOUND
    assert [item.instrument.symbol for item in report.candidates] == ["GC", "MGC"]
    assert {
        item.instrument.symbol: item.rank for item in report.candidates
    } == {"MGC": 1, "GC": 2}
    assert all(
        item.scan_session.calendar_name == "fixture_comex_futures_verified"
        for item in report.candidates
    )
    gc_candidate = next(
        item for item in report.candidates if item.instrument.symbol == "GC"
    )
    assert gc_candidate.measurement("latest_price").value == pytest.approx(2520.0)
    assert gc_candidate.measurement("change_percent").value == pytest.approx(0.8)
    assert gc_candidate.is_trade_signal is False
    assert gc_candidate.is_order is False
    assert gc_candidate.execution_authorized is False
    assert report.strategy_status is StrategyStatus.PLACEHOLDER
    assert report.execution_authorized is False
    assert report.outcome_count(EvaluationOutcome.CANDIDATE) == 2
    assert report.evaluations[0].data_results[0].provider == "synthetic-phase3-fixture"
    assert any("synthetic" in note.lower() for note in report.notes)


def test_gold_replay_default_schedule_is_verified() -> None:
    frame = load_replay_frame(GOLD_FIXTURE)

    report = frame.run(
        GoldOpportunityAnalyzer(load_strategy_config(GOLD_PATH))
    )

    assert report.status is ScanOutcome.CANDIDATES_FOUND
    assert [item.instrument.symbol for item in report.candidates] == ["GC", "MGC"]
    assert all(
        item.candidate is not None
        and item.candidate.scan_session.verified is True
        and item.candidate.scan_session.calendar_name
        == "comex_gold_futures_verified_2026_2027"
        for item in report.evaluations
    )


def test_gold_replay_explicit_unverified_schedule_is_rejected() -> None:
    frame = load_replay_frame(GOLD_FIXTURE)

    report = frame.run(
        GoldOpportunityAnalyzer(
            load_strategy_config(GOLD_PATH),
            futures_schedule=unverified_gold_schedule(),
        )
    )

    assert report.status is ScanOutcome.NO_CANDIDATES_PASSED
    assert all(
        item.rejection is not None
        and item.rejection.code.value == "session_unverified"
        for item in report.evaluations
    )


def test_gold_replay_frame_rejects_stock_mode(tmp_path: Path) -> None:
    payload = _payload(GOLD_FIXTURE)
    payload["mode"] = "premarket_gap_discovery"

    with pytest.raises(ReplayError, match="unknown field"):
        load_replay_frame(_write(tmp_path, payload))


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda payload: payload.pop("disclaimer"), "missing required field"),
        (
            lambda payload: payload.__setitem__(
                "disclaimer", "Historical observations supplied by a vendor."
            ),
            "synthetic or fixture",
        ),
        (lambda payload: payload.__setitem__("schema_version", 2), "schema_version"),
        (
            lambda payload: payload.__setitem__("schema_version", True),
            "schema_version",
        ),
        (lambda payload: payload.__setitem__("kind", "other"), "kind must be"),
        (lambda payload: payload.__setitem__("extra", 1), "unknown field"),
        (lambda payload: payload.pop("cases"), "missing required field"),
        (
            lambda payload: payload.__setitem__("cases", []),
            "cases must be a non-empty list",
        ),
    ],
)
def test_frame_metadata_is_strict(
    tmp_path: Path,
    mutation,
    expected: str,
) -> None:
    payload = copy.deepcopy(_payload(GOLD_FIXTURE))
    mutation(payload)

    with pytest.raises(ReplayError, match=expected):
        load_replay_frame(_write(tmp_path, payload))


def test_unknown_case_field_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_FIXTURE))
    payload["cases"][0]["future_peek"] = 2530.0

    with pytest.raises(ReplayError, match="unknown field"):
        load_replay_frame(_write(tmp_path, payload))


def test_case_must_be_gold_futures(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_FIXTURE))
    payload["cases"][0]["asset_class"] = "gold_etf"

    with pytest.raises(ReplayError, match="gold_futures"):
        load_replay_frame(_write(tmp_path, payload))


def test_case_symbol_must_be_gc_or_mgc(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_FIXTURE))
    payload["cases"][0]["symbol"] = "GLD"

    with pytest.raises(ReplayError, match="GC or MGC"):
        load_replay_frame(_write(tmp_path, payload))


def test_malformed_bar_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_FIXTURE))
    del payload["cases"][0]["reference"][0]["close"]

    with pytest.raises(ReplayError, match="missing required field"):
        load_replay_frame(_write(tmp_path, payload))


def test_unknown_bar_field_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_FIXTURE))
    payload["cases"][0]["reference"][0]["adjusted_close"] = 2520.0

    with pytest.raises(ReplayError, match="unknown field"):
        load_replay_frame(_write(tmp_path, payload))


def test_invalid_bar_price_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_FIXTURE))
    payload["cases"][0]["reference"][0]["close"] = -1.0

    with pytest.raises(ReplayError, match="is invalid"):
        load_replay_frame(_write(tmp_path, payload))


def test_previous_close_fields_must_appear_together(tmp_path: Path) -> None:
    payload = copy.deepcopy(_payload(GOLD_FIXTURE))
    del payload["cases"][0]["previous_close"]

    with pytest.raises(ReplayError, match="must supply previous_close_timeframe"):
        load_replay_frame(_write(tmp_path, payload))


def test_missing_replay_file_is_a_replay_error(tmp_path: Path) -> None:
    with pytest.raises(ReplayError, match="not found"):
        load_replay_frame(tmp_path / "missing.json")


def test_empty_replay_file_is_a_replay_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ReplayError, match="empty"):
        load_replay_frame(path)


def test_malformed_json_is_a_replay_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ReplayError, match="invalid JSON"):
        load_replay_frame(path)
