"""Persistence, risk state, position sizing, calendar and broker verification."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trantor.runtime.broker_verification import (
    CheckStatus,
    VerifiedEconomics,
    load_expectations,
    proposed_expectations,
    verify_broker,
)
from trantor.runtime.calendar import (
    FileCalendarProvider,
    NewsStatus,
    UnavailableCalendarProvider,
    evaluate_news,
)
from trantor.runtime.risk import RiskStateError, RiskTracker, size_position
from trantor.runtime.store import RuntimeStore, StoreError

from runtime_helpers import account, symbol_info

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)


@pytest.fixture()
def store(tmp_path: Path) -> RuntimeStore:
    db = RuntimeStore(tmp_path / "rt.sqlite3")
    yield db
    db.close()


# -- store ----------------------------------------------------------------------

def _decision_row(decision_id: str = "dec-1", at: str = "2026-11-04T14:00:00+00:00") -> dict:
    return {
        "decision_id": decision_id,
        "variant_id": "xauusd_p_approved_v1",
        "profile": "paper",
        "decision_time": at,
        "decision_time_raw": None,
        "evaluated_at": at,
        "execution_mode": "paper",
        "status": "rejected",
        "side": None,
        "score_total": 0,
        "confidence": "reject",
        "rejection_codes": "[]",
        "record_hash": "a" * 64,
        "record_json": "{}",
        "envelope_json": "{}",
        "outcome": "no_trade",
    }


def test_store_uses_wal_and_integrity_ok(store: RuntimeStore) -> None:
    mode = store._query("PRAGMA journal_mode")[0][0]  # noqa: SLF001
    assert mode.lower() == "wal"
    assert store.integrity_check() == "ok"


def test_decisions_are_inserted_once(store: RuntimeStore) -> None:
    with store.transaction() as conn:
        assert store.insert_decision(_decision_row(), conn)
    with store.transaction() as conn:
        assert not store.insert_decision(_decision_row(), conn)
        # Same variant/profile/time under a different id is also a duplicate.
        assert not store.insert_decision(_decision_row("dec-2"), conn)
    assert store.decision_counts() == {"rejected": 1}


def test_duplicate_client_order_id_rejected(store: RuntimeStore) -> None:
    order = {
        "client_order_id": "trn-ent-1",
        "decision_id": "dec-1",
        "mode": "paper",
        "side": "buy",
        "quantity": 1.0,
        "order_type": "market",
        "status": "pending_submission",
        "broker_order_id": None,
        "filled_quantity": 0.0,
        "average_fill_price": None,
        "created_at": "x",
        "updated_at": "x",
        "request_json": "{}",
    }
    with store.transaction() as conn:
        store.insert_order(order, conn)
    with pytest.raises(StoreError):
        with store.transaction() as conn:
            store.insert_order(order, conn)


def test_transaction_rolls_back_on_error(store: RuntimeStore) -> None:
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            store.insert_decision(_decision_row(), conn)
            raise RuntimeError("crash mid-transaction")
    assert store.recent_decisions() == []


def test_optimistic_version_conflict(store: RuntimeStore) -> None:
    with store.transaction() as conn:
        store.insert_position(
            {
                "position_id": "pos-1",
                "decision_id": "dec-1",
                "client_order_id": "c",
                "mode": "paper",
                "side": "buy",
                "status": "open",
                "closed": 0,
                "entry_time": "2026-11-04T14:00:00+00:00",
                "closed_at": None,
                "state_json": "{}",
                "updated_at": "x",
            },
            conn,
        )
    with store.transaction() as conn:
        store.update_position("pos-1", status="open", closed=False, closed_at=None, state={}, expected_version=1, conn=conn)
    with pytest.raises(StoreError):
        with store.transaction() as conn:
            store.update_position("pos-1", status="open", closed=False, closed_at=None, state={}, expected_version=1, conn=conn)


def test_read_only_store_cannot_write(tmp_path: Path) -> None:
    RuntimeStore(tmp_path / "rt.sqlite3").close()
    ro = RuntimeStore(tmp_path / "rt.sqlite3", read_only=True)
    with pytest.raises(StoreError):
        ro.set_state("x", 1)
    ro.close()
    with pytest.raises(StoreError):
        RuntimeStore(tmp_path / "missing.sqlite3", read_only=True)


def test_corrupt_state_raises(store: RuntimeStore) -> None:
    store._conn.execute("INSERT INTO kv_state (key, value, updated_at) VALUES ('risk:paper', '{not json', 'x')")  # noqa: SLF001
    with pytest.raises(StoreError):
        store.get_state("risk:paper")


def test_newer_schema_refused(tmp_path: Path) -> None:
    path = tmp_path / "rt.sqlite3"
    RuntimeStore(path).close()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO schema_version VALUES (99, 'x')")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError):
        RuntimeStore(path)


def test_notification_claim_is_deduplicated(store: RuntimeStore) -> None:
    assert store.claim_notification("setup:1", "setup")
    assert not store.claim_notification("setup:1", "setup")


# -- risk tracker -----------------------------------------------------------------

def test_risk_state_created_only_without_positions(store: RuntimeStore) -> None:
    tracker = RiskTracker(store, "paper")
    with pytest.raises(RiskStateError):
        tracker.load(NOW, open_positions=1)
    book = tracker.load(NOW, open_positions=0)
    assert book.equity == 10_000.0 and book.day == "2026-11-04"


def test_risk_state_survives_restart_and_mismatch_fails_closed(store: RuntimeStore) -> None:
    tracker = RiskTracker(store, "paper")
    tracker.load(NOW, open_positions=0)
    tracker.on_entry(NOW, 100.0)
    again = RiskTracker(store, "paper")
    book = again.load(NOW, open_positions=1)
    assert book.entries_today == 1 and book.open_positions == 1
    with pytest.raises(RiskStateError):
        RiskTracker(store, "paper").load(NOW, open_positions=0)


def test_corrupt_risk_state_fails_closed(store: RuntimeStore) -> None:
    store.set_state("risk:paper", {"mode": "paper", "day": "2026-11-04", "equity": -5, "start_of_day_equity": 1})
    with pytest.raises(RiskStateError):
        RiskTracker(store, "paper").load(NOW, open_positions=0)
    store.set_state("risk:paper", {"unexpected": True})
    with pytest.raises(RiskStateError):
        RiskTracker(store, "paper").load(NOW, open_positions=0)
    store.set_state(
        "risk:paper",
        {"mode": "paper", "day": "2026-11-04", "equity": 1, "start_of_day_equity": 1, "consecutive_losses": 1},
    )
    with pytest.raises(RiskStateError):
        RiskTracker(store, "paper").load(NOW, open_positions=0)


def test_cooldown_after_one_and_two_losses(store: RuntimeStore) -> None:
    tracker = RiskTracker(store, "paper")
    tracker.load(NOW, open_positions=0)
    tracker.on_entry(NOW, 50.0)
    tracker.on_full_exit(NOW, trade_r=-1.0, final_pnl=-50.0, final_r_contribution=-1.0, released_risk=50.0)
    state = tracker.snapshot(NOW + timedelta(minutes=1))
    assert state.cooldown_until == NOW + timedelta(hours=4)
    tracker.on_entry(NOW + timedelta(hours=5), 50.0)
    tracker.on_full_exit(NOW + timedelta(hours=5), trade_r=-1.0, final_pnl=-50.0, final_r_contribution=-1.0, released_risk=50.0)
    state = tracker.snapshot(NOW + timedelta(hours=5, minutes=1))
    assert state.consecutive_losses == 2
    assert state.cooldown_until == NOW + timedelta(hours=13)
    tracker.on_entry(NOW + timedelta(hours=14), 50.0)
    tracker.on_full_exit(NOW + timedelta(hours=14), trade_r=1.0, final_pnl=50.0, final_r_contribution=1.0, released_risk=50.0)
    assert tracker.book.consecutive_losses == 0
    assert tracker.book.cooldown_until is None


def test_daily_lockout_at_three_percent_and_reset_next_utc_day(store: RuntimeStore) -> None:
    tracker = RiskTracker(store, "paper")
    tracker.load(NOW, open_positions=0)
    tracker.on_entry(NOW, 300.0)
    tracker.on_full_exit(NOW, trade_r=-1.0, final_pnl=-300.0, final_r_contribution=-1.0, released_risk=300.0)
    assert tracker.book.daily_locked_out
    assert tracker.snapshot(NOW + timedelta(hours=1)).daily_locked_out
    next_day = datetime(2026, 11, 5, 0, 0, tzinfo=UTC)
    state = tracker.snapshot(next_day)
    assert not state.daily_locked_out and state.entries_today == 0
    assert state.start_of_day_equity == pytest.approx(9_700.0)


def test_unrealized_loss_counts_toward_daily_limit(store: RuntimeStore) -> None:
    tracker = RiskTracker(store, "paper")
    tracker.load(NOW, open_positions=0)
    assert not tracker.check_lockout(-200.0)
    assert tracker.check_lockout(-300.0)


def test_neutral_model_matches_backtest_unit_equity(store: RuntimeStore) -> None:
    tracker = RiskTracker(store, "shadow", starting_equity=1.0, equity_model="neutral")
    tracker.load(NOW, open_positions=0)
    tracker.on_entry(NOW, 0.01)
    tracker.on_full_exit(NOW, trade_r=-1.0, final_pnl=-0.5, final_r_contribution=-1.0, released_risk=0.01)
    assert tracker.book.equity == 1.0
    assert tracker.daily_loss_fraction(-0.9) == 0.0
    with pytest.raises(ValueError):
        RiskTracker(store, "x", equity_model="martingale")


# -- position sizing --------------------------------------------------------------

ECON = VerifiedEconomics(
    contract_size=100.0,
    tick_size=0.01,
    tick_value=1.0,
    volume_min=0.01,
    volume_max=50.0,
    volume_step=0.01,
    account_currency="USD",
    digits=2,
    stops_level_points=0,
    freeze_level_points=0,
    point=0.01,
)


def _size(**kw):
    args = dict(
        equity=10_000.0,
        risk_fraction=0.01,
        risk_ceiling=0.01,
        entry=2000.0,
        stop=1990.0,
        economics=ECON,
        commission_per_lot_round_trip=7.0,
        margin_per_lot=400.0,
        free_margin=10_000.0,
    )
    args.update(kw)
    return size_position(**args)


def test_sizing_blocked_without_verified_economics() -> None:
    result = _size(economics=None)
    assert not result.approved and "not verified" in result.reasons[0]


def test_sizing_rounds_down_to_even_steps_and_never_exceeds_risk() -> None:
    result = _size()
    # 100 USD risk / (10 * 100 + 7) per lot = 0.0993 -> 0.09 -> even steps 0.08
    assert result.approved
    assert result.volume == pytest.approx(0.08)
    assert result.risk_amount_actual <= result.risk_amount_allowed
    assert result.risk_amount_actual == pytest.approx(0.08 * 1007.0)


def test_minimum_volume_exceeding_risk_is_rejected() -> None:
    result = _size(equity=500.0)
    assert not result.approved
    assert any("minimum volume" in reason or "two steps" in reason for reason in result.reasons)


def test_unknown_commission_or_margin_blocks() -> None:
    assert "commission" in _size(commission_per_lot_round_trip=None).reasons[0]
    result = _size(margin_per_lot=None)
    assert not result.approved and "margin requirement unavailable" in result.reasons
    assert "insufficient free margin" in _size(free_margin=1.0).reasons


def test_risk_above_ceiling_and_invalid_inputs_rejected() -> None:
    assert not _size(risk_fraction=0.02).approved
    assert not _size(stop=2000.0).approved
    assert not _size(entry=float("nan")).approved
    assert not _size(equity=float("inf")).approved


def test_volume_capped_at_broker_maximum() -> None:
    result = _size(equity=100_000_000.0, free_margin=None)
    assert result.volume <= ECON.volume_max


def test_broker_cross_check_mismatch_rejects() -> None:
    result = _size(broker_loss_at_stop=lambda volume: volume * 500.0)
    assert not result.approved and "differs from independent" in result.reasons[0]
    ok = _size(broker_loss_at_stop=lambda volume: -volume * 1000.0)
    assert ok.approved and ok.broker_loss_at_stop == pytest.approx(80.0)


# -- calendar ---------------------------------------------------------------------

def _calendar(tmp_path: Path, **overrides) -> Path:
    data = {
        "schema": "trantor.news_calendar",
        "version": 1,
        "source": "BLS and Federal Reserve published schedules",
        "source_urls": ["https://www.bls.gov/schedule/"],
        "retrieved_at": "2026-11-01T00:00:00Z",
        "owner_confirmed": True,
        "confirmed_by": "owner",
        "confirmed_at": "2026-11-03T00:00:00Z",
        "coverage": {"start": "2026-11-01T00:00:00Z", "end": "2026-12-31T23:59:59Z"},
        "events": [
            {"event_type": "cpi", "title": "US CPI", "currency": "USD", "impact": "high", "local_time": "2026-11-13T08:30:00", "timezone": "America/New_York"},
            {"event_type": "nfp", "title": "Employment Situation", "currency": "USD", "impact": "high", "scheduled_at": "2026-12-04T13:30:00Z"},
        ],
    }
    data.update(overrides)
    path = tmp_path / "news.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_unavailable_calendar_never_means_no_news() -> None:
    assessment = evaluate_news(UnavailableCalendarProvider().refresh(NOW), decision_time=NOW)
    assert assessment.status is NewsStatus.UNAVAILABLE
    assert not assessment.context.verified


def test_missing_and_malformed_files(tmp_path: Path) -> None:
    missing = FileCalendarProvider(tmp_path / "nope.json").refresh(NOW)
    assert evaluate_news(missing, decision_time=NOW).status is NewsStatus.UNAVAILABLE
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert evaluate_news(FileCalendarProvider(bad).refresh(NOW), decision_time=NOW).status is NewsStatus.VERIFICATION_FAILED
    path = _calendar(tmp_path, extra_field=1)
    assert evaluate_news(FileCalendarProvider(path).refresh(NOW), decision_time=NOW).status is NewsStatus.VERIFICATION_FAILED


def test_unconfirmed_calendar_is_not_verified(tmp_path: Path) -> None:
    path = _calendar(tmp_path, owner_confirmed=False)
    assessment = evaluate_news(FileCalendarProvider(path).refresh(NOW), decision_time=NOW)
    assert assessment.status is NewsStatus.VERIFICATION_FAILED and not assessment.context.verified


def test_stale_confirmation_and_coverage_gap(tmp_path: Path) -> None:
    snap = FileCalendarProvider(_calendar(tmp_path)).refresh(NOW)
    later = datetime(2026, 11, 20, tzinfo=UTC)
    assert evaluate_news(snap, decision_time=later).status is NewsStatus.STALE
    outside = datetime(2026, 11, 1, 0, 10, tzinfo=UTC)
    assert evaluate_news(snap, decision_time=outside).status is NewsStatus.VERIFICATION_FAILED


def test_event_window_and_dst_aware_local_time(tmp_path: Path) -> None:
    path = _calendar(tmp_path, confirmed_at="2026-11-12T00:00:00Z")
    snap = FileCalendarProvider(path).refresh(datetime(2026, 11, 13, tzinfo=UTC))
    cpi = [event for event in snap.events if event.event_type == "cpi"][0]
    assert cpi.scheduled_at == datetime(2026, 11, 13, 13, 30, tzinfo=UTC)  # EST, UTC-5
    before = evaluate_news(snap, decision_time=datetime(2026, 11, 13, 13, 0, tzinfo=UTC))
    assert before.status is NewsStatus.EVENT_DETECTED and before.context.verified
    after = evaluate_news(snap, decision_time=datetime(2026, 11, 13, 13, 55, tzinfo=UTC))
    assert after.status is NewsStatus.EVENT_DETECTED
    clear = evaluate_news(snap, decision_time=datetime(2026, 11, 13, 15, 0, tzinfo=UTC))
    assert clear.status is NewsStatus.VERIFIED_CLEAR


def test_duplicates_removed_and_unknown_event_types_rejected(tmp_path: Path) -> None:
    event = {"event_type": "fomc", "currency": "USD", "impact": "high", "scheduled_at": "2026-12-09T19:00:00Z"}
    snap = FileCalendarProvider(_calendar(tmp_path, events=[event, dict(event)])).refresh(NOW)
    assert snap.duplicates_removed == 1 and len(snap.events) == 1
    bad = FileCalendarProvider(_calendar(tmp_path, events=[{**event, "event_type": "retail sales"}])).refresh(NOW)
    assert not bad.verified and "event_type" in bad.failure
    naive = FileCalendarProvider(_calendar(tmp_path, events=[{**event, "scheduled_at": "2026-12-09T19:00:00"}])).refresh(NOW)
    assert "offset" in naive.failure


# -- broker verification ---------------------------------------------------------

def _report(expectations=None, **info_overrides):
    return verify_broker(
        symbol="XAUUSD-P",
        symbol_info=symbol_info(**info_overrides),
        account=account(),
        tick={"bid": 2000.0, "ask": 2000.2},
        calc_profit=lambda buy, v, o, c: (c - o) * 100.0 * v,
        calc_margin=lambda buy, v, p: p * 100 * v / 500,
        expectations=expectations,
        forming_m15_close=2000.0,
        time_basis={"verified": True, "offset_hours": 2},
        now=NOW,
    )


def test_observed_values_are_not_verified_without_owner_confirmation() -> None:
    report = _report()
    assert report.overall == "observed_unconfirmed"
    assert not report.economics_verified and report.economics() is None
    assert report.check("economics.trade_contract_size").status is CheckStatus.OBSERVED
    assert report.check("sessions.broker_calendar").status is CheckStatus.UNAVAILABLE
    assert report.check("costs.commission").status is CheckStatus.UNAVAILABLE
    assert report.check("spread.convention").status is CheckStatus.OBSERVED


def test_owner_confirmed_matching_values_verify_economics(tmp_path: Path) -> None:
    proposal = proposed_expectations(_report())
    assert proposal["confirmed_by_owner"] is False
    proposal.update(confirmed_by_owner=True, source="Fortune Prime XAUUSD-P contract specification")
    path = tmp_path / "exp.json"
    path.write_text(json.dumps(proposal), encoding="utf-8")
    report = _report(expectations=load_expectations(path))
    assert report.overall == "economics_verified"
    econ = report.economics()
    assert econ is not None and econ.value_per_price_unit_per_lot == pytest.approx(100.0)


def test_unconfirmed_expectation_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "exp.json"
    path.write_text(json.dumps({"confirmed_by_owner": False, "values": {}}), encoding="utf-8")
    assert load_expectations(path) is None
    path.write_text(json.dumps({"confirmed_by_owner": True, "source": "", "values": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_expectations(path)


def test_mismatches_detected() -> None:
    exp = {"source": "doc", "values": {"trade_contract_size": 1.0}}
    report = _report(expectations=exp)
    assert report.overall == "mismatch"
    assert _report(custom=True).check("identity.symbol").status is CheckStatus.MISMATCH
    inconsistent = _report(trade_tick_value=5.0)
    assert inconsistent.check("consistency.tick_value").status is CheckStatus.MISMATCH
    assert _report(trade_tick_size=0.05).check("consistency.strategy_tick_grid").status is CheckStatus.MISMATCH
    assert _report(trade_mode=3).check("permissions.symbol_trade_mode").status is CheckStatus.MISMATCH


def test_missing_symbol_info() -> None:
    report = verify_broker(symbol="XAUUSD-P", symbol_info=None, account=None, tick=None)
    assert report.overall == "identity_unavailable"


def test_usd_base_cfd_convention_accepted_only_with_gold_identity() -> None:
    report = _report(currency_base="USD", description="Gold vs USD", path=r"Metal-P\XAUUSD-P")
    assert report.check("identity.currencies").status is CheckStatus.OBSERVED
    assert "description and path" in report.check("identity.currencies").detail
    silver = _report(currency_base="USD", description="Silver vs USD", path=r"Metal-P\XAGUSD-P")
    assert silver.check("identity.currencies").status is CheckStatus.MISMATCH
    assert _report(currency_profit="EUR").check("identity.currencies").status is CheckStatus.MISMATCH
