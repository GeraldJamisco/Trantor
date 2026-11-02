"""Focused tests for Phase F trade-level diagnostics."""

from __future__ import annotations

from pathlib import Path

import pytest

from trantor.research.diagnostics import (
    DEFAULT_ARTIFACT_DIR,
    DiagnosticConfig,
    DiagnosticError,
    TradeDiagnosticsService,
)


_SERVICE: TradeDiagnosticsService | None = None


def _authoritative_service() -> TradeDiagnosticsService:
    global _SERVICE
    if _SERVICE is None:
        config = DiagnosticConfig.default()
        if not (config.artifact_dir / "summary.json").is_file():
            pytest.skip("authoritative Phase E artifacts are not present")
        _SERVICE = TradeDiagnosticsService(config)
    return _SERVICE


def test_authoritative_diagnostics_reconcile() -> None:
    service = _authoritative_service()
    payload = service.build()

    assert payload["verification"]["status"] == "PASS"
    assert payload["verification"]["trades_checked"] == 43
    assert payload["verification"]["linked_decisions_checked"] == 43
    assert all(
        source["verified"] for source in payload["verification"]["source_checksums"]
    )

    assert list(payload["breakdowns"]["by_split"]) == [
        "development",
        "validation",
        "holdout",
    ]
    overall = payload["breakdowns"]["overall"]
    assert overall["trade_count"] == 43
    assert overall["win_count"] == 13
    assert overall["loss_count"] == 30
    assert overall["gross_realized_r"] == pytest.approx(-5.0, abs=1e-9)
    assert overall["expectancy_r"] == pytest.approx(-0.1162790697, rel=1e-9)
    assert overall["profit_factor"] == pytest.approx(0.8333333333, rel=1e-9)

    validation = payload["breakdowns"]["by_split"]["validation"]
    assert validation["trade_count"] == 21
    assert validation["win_count"] == 2
    assert validation["loss_count"] == 19
    assert validation["gross_realized_r"] == pytest.approx(-15.5, abs=1e-9)
    assert validation["expectancy_r"] == pytest.approx(-0.7380952381, rel=1e-9)
    assert validation["profit_factor"] == pytest.approx(0.1842105263, rel=1e-9)
    assert validation["inconclusive"] is True

    assert payload["drawdown"]["maximum_drawdown_r"] == pytest.approx(17.0)
    assert payload["drawdown"]["matches_summary"] is True
    assert payload["drawdown"]["peak_trade_id"] == 19
    assert payload["drawdown"]["trough_trade_id"] == 38

    exits = payload["exit_reconciliation"]
    assert exits["matches_summary"] is True
    assert exits["actual"]["tp1_events"] == 13
    assert exits["actual"]["tp2_events"] == 8
    assert exits["actual"]["breakeven_stops"] == 5
    assert exits["actual"]["initial_stops"] == 30

    assert payload["ambiguous"]["ambiguous_bar_count"] == 6
    assert len(payload["ambiguous"]["events"]) == 6
    assert {event["trade_id"] for event in payload["ambiguous"]["events"]} == {
        7,
        8,
        18,
        24,
        37,
        38,
    }
    assert all(
        event["bar_touched_tp1"] and event["bar_touched_tp2"]
        for event in payload["ambiguous"]["events"]
    )


def test_trade_rows_and_2025_focus_are_derived_from_records() -> None:
    payload = _authoritative_service().build()
    rows = payload["trades"]
    assert len(rows) == 43
    assert all(row["decision_hash"] for row in rows)
    assert all(row["max_favorable_excursion_r"] is not None for row in rows)
    assert all(row["max_adverse_excursion_r"] is not None for row in rows)
    assert all("max_favorable_excursion_r" in row["derived_fields"] for row in rows)

    validation = [row for row in rows if row["split"] == "validation"]
    assert len(validation) == 21
    assert sum(row["tp1_reached"] for row in validation) == 2
    assert sum(row["tp2_completed"] for row in validation) == 1
    assert sum(row["ambiguous_bar_count"] for row in validation) == 4
    assert sum(row["exit_classification"] == "stopped" for row in validation) == 19

    trade_1 = next(row for row in rows if row["trade_id"] == 1)
    assert trade_1["side"] == "buy"
    assert trade_1["realized_r"] == pytest.approx(2.5)
    assert trade_1["tp1_reached"] is True
    assert trade_1["tp2_completed"] is True
    assert trade_1["score"] == 5
    assert trade_1["d1_bias"] == "bullish"
    assert trade_1["h4_direction"] == "bullish"


def test_diagnostics_write_artifacts(tmp_path: Path) -> None:
    service = _authoritative_service()
    paths = service.write_artifacts(tmp_path)
    assert {path.name for path in paths} == {
        "trade_diagnostics.json",
        "breakdowns.json",
        "summary.json",
        "trade_diagnostics.csv",
    }
    for path in paths:
        assert path.is_file()
        assert path.stat().st_size > 0
    csv_header = paths[-1].read_text(encoding="utf-8").splitlines()[0]
    assert "decision_hash" in csv_header
    assert "max_adverse_excursion_r" in csv_header


def test_missing_artifacts_fail_closed(tmp_path: Path) -> None:
    config = DiagnosticConfig(
        artifact_dir=tmp_path / "missing",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
    )
    with pytest.raises(DiagnosticError, match="missing"):
        TradeDiagnosticsService(config).build()


def test_default_artifact_path_is_repository_relative() -> None:
    config = DiagnosticConfig.default()
    assert config.artifact_dir.name == DEFAULT_ARTIFACT_DIR.name
    assert config.artifact_dir.is_absolute()
    assert config.relative(config.artifact_dir).startswith("research_results/")
