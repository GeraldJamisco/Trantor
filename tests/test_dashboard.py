"""Focused tests for the read-only research dashboard."""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trantor.dashboard.data import DashboardConfig, ResearchRepository
from trantor.dashboard.server import build_server
from trantor.strategy.research_record import canonical_json

UTC = timezone.utc
GROSS = "GROSS BASELINE - EXECUTION COSTS EXCLUDED"
RESEARCH = "RESEARCH-ONLY FILTER VARIANT - NOT COMPLETE APPROVED STRATEGY"
DT1 = datetime(2024, 1, 10, 8, 15, tzinfo=UTC)
DT2 = datetime(2024, 1, 10, 8, 30, tzinfo=UTC)


def _decision(
    decision_time: datetime,
    *,
    status: str,
    side: str | None = None,
    score: int = 0,
    rejection: str | None = None,
) -> tuple[dict, dict]:
    candidate_side = side if status == "candidate" else None
    confidence = "medium" if score >= 5 else "reject"
    plan = None
    candidate = None
    if candidate_side:
        plan = {
            "entry": 2024.34,
            "stop": 2021.76,
            "tp1": 2029.5,
            "tp2": 2032.08,
            "risk_distance": 2.58,
            "tp1_fraction": 0.5,
            "tp2_fraction": 0.5,
            "breakeven": 2024.34,
            "breakeven_costs_verified": False,
            "monetary_sizing_status": "blocked_unverified_contract_economics",
            "partial_exit_status": "blocked_pending_volume_sizing",
        }
        candidate = {
            "side": candidate_side,
            "decision_time": decision_time.isoformat(),
            "entry_time": (decision_time + timedelta(minutes=15)).isoformat(),
            "entry_reference": 2024.34,
            "confidence": confidence,
            "execution_ready": False,
            "research_mode": True,
            "news_unaware": True,
            "rationale": "fixture candidate",
        }
    canonical = {
        "schema": "trantor.research_decision",
        "schema_version": "1.0",
        "record_type": "strategy_decision",
        "authoritative_source": "StrategyDecision",
        "strategy_name": "xauusd_p_multi_timeframe_research_filter_isolated",
        "strategy_version": "1.0.0",
        "variant_id": "xauusd_p_filter_isolated_v1",
        "variant_name": "research_filter_isolated",
        "research_only": True,
        "execution_ready": False,
        "decision_time": decision_time.isoformat(),
        "decision_time_original": decision_time.isoformat(),
        "time_semantics": "dataset_time_unverified_absolute_utc",
        "absolute_utc_provenance": "unverified",
        "symbol": "XAUUSD-P",
        "timeframe": "15m",
        "instrument": {
            "symbol": "XAUUSD-P",
            "asset_class": "unverified_gold",
            "exchange": "BROKER",
            "currency": "USD",
            "timezone": "UTC",
        },
        "status": status,
        "rejections": (
            [{"code": rejection, "message": "test rejection"}]
            if rejection
            else []
        ),
        "score": {
            "total": score,
            "maximum": 8,
            "confidence": confidence,
            "factors": (
                [
                    {
                        "name": "fixture",
                        "points": score,
                        "awarded": True,
                        "available": True,
                        "detail": "fixture",
                    }
                ]
                if score
                else []
            ),
        },
        "session": {
            "in_overlap": False,
            "calendar_verified": False,
            "research_allowed": True,
            "reason": "research-only",
            "filter_mode": "research_isolated",
        },
        "news": {
            "available": False,
            "safe": False,
            "point_awarded": False,
            "unaware": True,
            "reason": "news-unaware",
        },
        "news_unaware": True,
        "plan": plan,
        "candidate": candidate,
        "daily_bias": None,
        "h4_trend": None,
        "zone": None,
        "risk": None,
        "limitations": [
            "dataset_time_does_not_claim_verified_absolute_utc",
            "session_calendar_unverified",
            "news_calendar_unverified",
            "cost_verification_unavailable",
            "monetary_sizing_blocked",
            "research_only",
            "execution_not_ready",
        ],
    }
    record = {
        "decision_time": decision_time.isoformat(),
        "period": "development",
        "status": status,
        "rejection_codes": [rejection] if rejection else [],
        "has_candidate": side is not None,
        "record_hash": hashlib.sha256(
            canonical_json(canonical).encode()
        ).hexdigest(),
        "canonical": canonical,
    }
    return record, canonical


def _write_fixture(tmp_path: Path) -> DashboardConfig:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    candidate, _ = _decision(DT1, status="candidate", side="buy", score=7)
    rejected, _ = _decision(
        DT2,
        status="rejected",
        rejection="h4_neutral",
    )
    trade = {
        "trade_id": 1,
        "decision_time": DT1.isoformat(),
        "decision_hash": candidate["record_hash"],
        "entry_time": DT1.isoformat(),
        "entry_period": "development",
        "exit_time": "2024-01-10T10:15:00+00:00",
        "exit_period": "development",
        "side": "buy",
        "confidence": "medium",
        "entry_price": 2024.34,
        "stop": 2021.76,
        "tp1": 2029.5,
        "tp2": 2032.08,
        "risk_distance": 2.58,
        "tp1_fraction": 0.5,
        "tp2_fraction": 0.5,
        "status": "tp2_closed",
        "closed": True,
        "realized_r": 2.5,
        "remaining_fraction": 0.0,
        "unrealized_r": None,
        "exits": [],
        "ambiguous_bar_count": 0,
        "gap_exit_count": 0,
        "bars_held": 8,
        "open_or_truncated_at_split_cutoff": False,
        "later_period_data_required": False,
        "entry_boundary_excluded": False,
    }
    attempt = {
        "decision_time": DT1.isoformat(),
        "entry_time": DT1.isoformat(),
        "side": "buy",
        "entry_price": 2024.34,
        "stop": 2021.76,
        "tp1": 2029.5,
        "tp2": 2032.08,
        "accepted": True,
        "reason": None,
        "excluded_at_split_boundary": False,
        "decision_hash": candidate["record_hash"],
    }
    overall_metrics = {
        "closed_trade_count": 1,
        "win_count": 1,
        "loss_count": 0,
        "win_rate": 1.0,
        "gross_realized_r": 2.5,
        "average_r": 2.5,
        "profit_factor": None,
        "max_drawdown_r": 0.0,
        "ambiguous_bar_count": 0,
        "exit_reason_counts": {"tp2": 1},
        "inconclusive_under_30_trades": True,
    }
    splits = {
        "development": {
            "split": {
                "name": "development",
                "start": "2024-01-01T00:00:00+00:00",
                "end": "2024-12-31T23:59:59.999999+00:00",
            },
            "decision_count": 2,
            "candidate_count": 1,
            "filled_trade_count": 1,
            "closed_trade_count": 1,
            "open_or_truncated_trade_count": 0,
            "carried_position_count_at_split_start": 0,
            "metrics": {
                **overall_metrics,
                "sample_size_gate": "inconclusive_fewer_than_30_closed_trades",
                "inconclusive_under_30_trades": True,
            },
            "open_or_truncated_trades": [],
        },
        "validation": {},
        "holdout": {},
    }
    summary = {
        "config_fingerprint": "fixture-fingerprint",
        "labels": {"gross": GROSS, "research": RESEARCH},
        "strategy": {
            "name": "fixture_strategy",
            "variant_id": "xauusd_p_filter_isolated_v1",
            "variant_name": "research_filter_isolated",
            "version": "1.0.0",
            "research_only": True,
            "parameters": {"tp1_r": 2.0},
        },
        "instrument_profile": {
            "symbol": "XAUUSD-P",
            "asset_class": "unverified_gold",
        },
        "record_contract": {
            "authoritative": True,
            "canonical_records_enabled": True,
            "decision_source": "StrategyDecision",
            "schema": "trantor.research_decision",
            "schema_version": "1.0",
        },
        "overall": {
            "decision_count": 2,
            "decision_record_count": 2,
            "candidate_count": 1,
            "entry_attempt_count": 1,
            "filled_trade_count": 1,
            "closed_trade_count": 1,
            "open_or_truncated_trade_count": 0,
            "trades_with_decision_hash": 1,
            "metrics": overall_metrics,
        },
        "splits": splits,
        "data_sources": [],
        "limitations": ["gross only", "research variant"],
    }
    manifest = {
        "config_fingerprint": "fixture-fingerprint",
        "strategy": summary["strategy"],
        "instrument_profile": summary["instrument_profile"],
        "record_contract": summary["record_contract"],
        "sources": [],
        "assumptions": ["test assumption"],
        "limitations": summary["limitations"],
        "time_semantics": {
            "absolute_utc_provenance": "unverified",
            "session_compliance": "not_claimed",
            "news_compliance": "not_claimed",
        },
        "costs": {
            "gross_only": True,
            "costs_applied": False,
            "monetary_metrics": "unavailable",
            "net_profitability": "not_claimed",
        },
        "splits": [
            {"name": "development", "start": "2024-01-01", "end": "2024-12-31"},
            {"name": "validation", "start": "2025-01-01", "end": "2025-12-31"},
            {"name": "holdout", "start": "2026-01-01", "end": "2026-10-21"},
        ],
    }
    (artifacts / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (artifacts / "trades.json").write_text(json.dumps([trade]), encoding="utf-8")
    (artifacts / "entry_attempts.json").write_text(
        json.dumps([attempt]), encoding="utf-8"
    )
    (artifacts / "decisions.jsonl").write_text(
        "\n".join(json.dumps(item) for item in (candidate, rejected)) + "\n",
        encoding="utf-8",
    )
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trading_mode": "paper",
                "execution_mode": "paper",
                "live_trading_enabled": False,
                "broker_adapter": "paper",
                "max_risk_per_trade_fraction": 0.005,
                "max_concurrent_positions": 5,
                "max_daily_loss_fraction": 0.02,
                "log_level": "INFO",
                "market_data_provider": "mt5",
                "mt5_terminal_path": r"C:\private\terminal64.exe",
            }
        ),
        encoding="utf-8",
    )
    return DashboardConfig(
        repo_root=tmp_path,
        artifact_dir=artifacts,
        settings_path=settings,
        settings_example_path=None,
    )


def test_artifact_discovery_and_summary_metrics(tmp_path: Path) -> None:
    repo = ResearchRepository(_write_fixture(tmp_path))
    overview = repo.overview()
    assert overview["metrics"]["closed_trade_count"] == 1
    assert overview["metrics"]["win_rate"] == 1.0
    assert overview["splits"]["development"]["tp2_count"] == 1
    assert overview["record_validation"]["status"] == "PASS"
    assert [item["name"] for item in overview["artifact_status"]] == [
        "summary.json",
        "manifest.json",
        "trades.json",
        "entry_attempts.json",
        "decisions.jsonl",
    ]
    assert overview["labels"]["gross"] == GROSS


def test_authoritative_snapshot_reconciles() -> None:
    config = DashboardConfig.default()
    if not (config.artifact_dir / "summary.json").is_file():
        pytest.skip("authoritative Phase E artifacts are not present")
    overview = ResearchRepository(config).overview()
    metrics = overview["metrics"]
    assert metrics["closed_trade_count"] == 43
    assert metrics["win_count"] == 13
    assert metrics["loss_count"] == 30
    assert metrics["win_rate"] == pytest.approx(0.3023255814, rel=1e-9)
    assert metrics["gross_realized_r"] == pytest.approx(-5.0, abs=1e-9)
    assert metrics["average_r"] == pytest.approx(-0.1162790697, rel=1e-9)
    assert metrics["profit_factor"] == pytest.approx(0.8333333333, rel=1e-9)
    assert metrics["max_drawdown_r"] == pytest.approx(17.0)
    assert overview["splits"]["development"]["closed_trade_count"] == 17
    assert overview["splits"]["validation"]["closed_trade_count"] == 21
    assert overview["splits"]["holdout"]["closed_trade_count"] == 5
    assert all(
        split["inconclusive_under_30_trades"]
        for split in overview["splits"].values()
    )
    assert overview["data"]["status"] == "PASS"
    assert overview["record_validation"]["status"] == "PASS"


def test_authoritative_diagnostics_are_exposed_read_only() -> None:
    config = DashboardConfig.default()
    if not (config.artifact_dir / "summary.json").is_file():
        pytest.skip("authoritative Phase E artifacts are not present")
    payload = ResearchRepository(config).diagnostics_payload()
    assert payload["verification"]["status"] == "PASS"
    assert len(payload["trades"]) == 43
    assert payload["drawdown"]["maximum_drawdown_r"] == 17.0
    assert payload["ambiguous"]["ambiguous_bar_count"] == 6


def test_missing_and_invalid_artifacts_are_warnings(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    repo = ResearchRepository(
        DashboardConfig(
            repo_root=tmp_path,
            artifact_dir=tmp_path / "artifacts",
            settings_path=None,
            settings_example_path=None,
        )
    )
    overview = repo.overview()
    assert overview["metrics"]["closed_trade_count"] == 0
    assert overview["record_validation"]["status"] == "WARNING"
    assert any("missing" in warning for warning in overview["warnings"])
    assert any("summary.json" in warning for warning in overview["warnings"])

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "summary.json").write_text("{not-json", encoding="utf-8")
    repo = ResearchRepository(
        DashboardConfig(
            repo_root=tmp_path,
            artifact_dir=invalid,
            settings_path=None,
            settings_example_path=None,
        )
    )
    assert any("not valid JSON" in warning for warning in repo.overview()["warnings"])


def test_trade_and_decision_filtering_and_pagination(tmp_path: Path) -> None:
    repo = ResearchRepository(_write_fixture(tmp_path))
    trades = repo.trades_payload({"side": "buy", "period": "development"})
    assert trades["total"] == 1
    assert trades["items"][0]["trade_id"] == 1

    decisions = repo.decisions_payload({"status": "rejected", "page": "1", "page_size": "1"})
    assert decisions["total"] == 1
    assert len(decisions["items"]) == 1
    assert decisions["items"][0]["status"] == "rejected"
    detail = repo.decision_detail(decisions["items"][0]["record_hash"])
    assert detail["canonical"]["status"] == "rejected"
    assert detail["canonical"]["rejections"][0]["code"] == "h4_neutral"

    candidate = repo.decisions_payload({"side": "buy"})
    assert candidate["total"] == 1
    assert candidate["items"][0]["side"] == "buy"

    with pytest.raises(Exception):
        repo.decisions_payload({"page": "not-a-number"})


def test_configuration_redaction_and_execution_disabled(tmp_path: Path) -> None:
    repo = ResearchRepository(_write_fixture(tmp_path))
    payload = repo.config_payload()
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["available"] is True
    assert "private" not in serialized
    assert "terminal64.exe" not in serialized
    assert payload["snapshot"]["mt5_terminal_path_configured"] is True
    system = repo.system_payload()
    assert system["execution"]["status"] == "DISABLED"
    assert system["execution"]["order_submission"] == "BLOCKED"
    assert system["read_only"] is True
    assert "C:\\\\" not in json.dumps(repo.data_page_payload(), sort_keys=True)
    assert "C:\\\\" not in json.dumps(repo.reports_payload(), sort_keys=True)


def test_server_is_read_only_and_rejects_traversal(tmp_path: Path) -> None:
    repo = ResearchRepository(_write_fixture(tmp_path))
    server = build_server(repo.config, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        connection = http.client.HTTPConnection(host, port, timeout=10)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "TRANTOR" in body

        connection.request("GET", "/api/overview")
        response = connection.getresponse()
        overview = json.loads(response.read())
        assert response.status == 200
        assert overview["execution"]["status"] == "DISABLED"

        connection.request("GET", "/assets/../index.html")
        response = connection.getresponse()
        response.read()
        assert response.status == 404

        connection.request("GET", "/api/decisions/../../settings.json")
        response = connection.getresponse()
        response.read()
        assert response.status in {400, 404}

        connection.request("POST", "/api/overview", body=b"{}")
        response = connection.getresponse()
        response.read()
        assert response.status == 405
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
