"""Phase E research backtester tests.

The tests use synthetic M15 paths for fill/risk/report behavior and a small
synthetic dataset for point-in-time views. They do not run the historical
experiment.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trantor.instruments import XAUUSD_P_PROFILE
from trantor.models import MarketObservation, Side
from trantor.research.backtest.data import (
    DatasetIdentity,
    DatasetSpec,
    ResearchData,
    ResearchDataError,
    load_dataset,
    load_research_data,
)
from trantor.research.backtest.engine import (
    BacktestConfig,
    GROSS_LABEL,
    RESEARCH_LABEL,
    SPLITS,
    BacktestError,
    _RiskBook,
    run_backtest,
    split_for,
)
from trantor.research.backtest.fills import (
    create_position,
    entry_rejection_reason,
    finish_trade,
    process_position_bar,
    unrealized_r,
)
from trantor.research.backtest.replay import HistoricalView
from trantor.strategy.xauusd_p import (
    BiasState,
    Confidence,
    ConfluenceScore,
    DailyBias,
    DecisionStatus,
    H4Trend,
    NewsEvaluation,
    Rejection,
    RejectionCode,
    ResearchRiskState,
    RiskDecision,
    ScoreFactor,
    SessionEvaluation,
    SessionFilterMode,
    StrategyDecision,
    TradePlan,
    TrendState,
    XAUUSDPMTFParameters,
    XAUUSDPSignalCandidate,
    XAUUSD_P_INSTRUMENT,
    ZoneCluster,
)
from trantor.strategy.research_record import serialize_strategy_decision
from trantor.strategy.xauusd_p_research import XAUUSDPMTFResearchVariant

UTC = timezone.utc
SYMBOL = "XAUUSD-P"


def _bar(
    at: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    timeframe: str = "15m",
    volume: float = 100.0,
) -> MarketObservation:
    return MarketObservation(
        symbol=SYMBOL,
        timeframe=timeframe,
        observed_at=at,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _identity(label: str, timeframe: str = "15m") -> DatasetIdentity:
    first = datetime(2024, 1, 1, tzinfo=UTC)
    return DatasetIdentity(
        label=label,
        path=f"synthetic/{label}",
        timeframe=timeframe,
        checksum_sha256="synthetic",
        row_count=1,
        first_observation=first,
        last_observation=first,
    )


def _data(
    m15: tuple[MarketObservation, ...],
    *,
    d1: tuple[MarketObservation, ...] | None = None,
    h4: tuple[MarketObservation, ...] | None = None,
) -> ResearchData:
    if d1 is None:
        d1 = (
            _bar(
                datetime(2024, 1, 1, tzinfo=UTC),
                100.0,
                101.0,
                99.0,
                100.0,
                "1d",
            ),
        )
    if h4 is None:
        h4 = (
            _bar(
                datetime(2024, 1, 1, 20, tzinfo=UTC),
                100.0,
                101.0,
                99.0,
                100.0,
                "4h",
            ),
        )
    source = _identity("synthetic")
    return ResearchData(
        d1=d1,
        h4=h4,
        m15=m15,
        d1_close_times=tuple(
            item.observed_at + timedelta(days=1) for item in d1
        ),
        h4_close_times=tuple(
            item.observed_at + timedelta(hours=4) for item in h4
        ),
        m15_close_times=tuple(
            item.observed_at + timedelta(minutes=15) for item in m15
        ),
        sources=(source,),
        d1_final_source=source,
        d1_warmup_source=source,
    )


def _candidate(
    decision_time: datetime,
    next_bar: MarketObservation,
    *,
    side: Side = Side.BUY,
    entry: float = 100.0,
    stop: float = 99.0,
    tp1: float = 102.0,
    tp2: float = 103.0,
) -> XAUUSDPSignalCandidate:
    plan = TradePlan(
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        risk_distance=abs(entry - stop),
        tp1_fraction=0.5,
        tp2_fraction=0.5,
        breakeven=entry,
        breakeven_costs_verified=False,
    )
    score = ConfluenceScore(
        (ScoreFactor("synthetic", 5, True, True, "test"),),
        5,
        Confidence.MEDIUM,
    )
    daily = DailyBias(
        BiasState.BULLISH,
        100.0,
        99.0,
        0.1,
        decision_time,
        101.0,
        99.0,
        201,
    )
    h4 = H4Trend(
        TrendState.BULLISH,
        1.0,
        (),
        102.0,
        101.0,
        99.0,
        98.0,
        decision_time,
        30,
    )
    news = NewsEvaluation(False, False, False, True, "test news-unaware")
    session = SessionEvaluation(
        True,
        False,
        True,
        "test research session",
        SessionFilterMode.RESEARCH_ISOLATED,
    )
    risk = RiskDecision(
        True,
        0.005,
        0.0,
        "blocked_unverified_contract_economics",
        (),
        False,
    )
    zone = ZoneCluster((), entry - 0.5, entry + 0.5, entry, ("test",))
    return XAUUSDPSignalCandidate(
        instrument=XAUUSD_P_INSTRUMENT,
        side=side,
        decision_time=decision_time,
        entry_time=next_bar.observed_at,
        entry_reference=entry,
        zone=zone,
        plan=plan,
        confidence=Confidence.MEDIUM,
        score=score,
        daily_bias=daily,
        h4_trend=h4,
        news=news,
        session=session,
        risk=risk,
        research_mode=True,
        news_unaware=True,
        rationale="synthetic candidate",
    )


def _rejected(decision_time: datetime) -> StrategyDecision:
    return StrategyDecision(
        instrument=XAUUSD_P_INSTRUMENT,
        decision_time=decision_time,
        status=DecisionStatus.REJECTED,
        candidate=None,
        rejections=(Rejection(RejectionCode.SCORE_BELOW_THRESHOLD, "synthetic reject"),),
        score=ConfluenceScore((), 0, Confidence.REJECT),
        daily_bias=None,
        h4_trend=None,
        zone=None,
        news=NewsEvaluation(False, False, False, True, "test news-unaware"),
        session=SessionEvaluation(
            True,
            False,
            True,
            "test research session",
            SessionFilterMode.RESEARCH_ISOLATED,
        ),
        risk=None,
        research_mode=True,
        news_unaware=True,
        variant_id=XAUUSDPMTFResearchVariant().variant_id,
    )


def _candidate_decision(
    decision_time: datetime,
    next_bar: MarketObservation,
) -> StrategyDecision:
    candidate = _candidate(decision_time, next_bar)
    return StrategyDecision(
        instrument=candidate.instrument,
        decision_time=decision_time,
        status=DecisionStatus.CANDIDATE,
        candidate=candidate,
        rejections=(),
        score=candidate.score,
        daily_bias=candidate.daily_bias,
        h4_trend=candidate.h4_trend,
        zone=candidate.zone,
        news=candidate.news,
        session=candidate.session,
        risk=candidate.risk,
        research_mode=True,
        news_unaware=True,
        variant_id=XAUUSDPMTFResearchVariant().variant_id,
    )


class _FakeStrategy:
    identity = XAUUSDPMTFResearchVariant().identity
    parameters = XAUUSDPMTFParameters()

    def __init__(self, candidate_times: set[datetime]) -> None:
        self.candidate_times = candidate_times

    def evaluate(
        self,
        d1,
        h4,
        m15,
        *,
        decision_time,
        next_m15_bar,
        risk_state=None,
        news_context=None,
        research_mode=True,
        news_unaware=True,
        session_calendar_verified=False,
    ):
        del d1, h4, m15, risk_state, news_context, research_mode, news_unaware
        del session_calendar_verified
        if decision_time in self.candidate_times and next_m15_bar is not None:
            return _candidate_decision(decision_time, next_m15_bar)
        return _rejected(decision_time)


def _engine_data() -> tuple[ResearchData, datetime]:
    start = datetime(2024, 1, 2, 1, 0, tzinfo=UTC)
    bars = (
        _bar(start, 100.0, 100.2, 99.8, 100.0),
        _bar(start + timedelta(minutes=15), 100.0, 100.5, 99.5, 100.2),
        _bar(start + timedelta(minutes=30), 100.2, 102.2, 100.0, 101.5),
        _bar(start + timedelta(minutes=45), 101.5, 103.2, 100.3, 102.8),
        _bar(start + timedelta(minutes=60), 102.8, 103.0, 101.0, 102.0),
    )
    d1 = (
        _bar(datetime(2024, 1, 1, tzinfo=UTC), 100.0, 101.0, 99.0, 100.0, "1d"),
        _bar(datetime(2024, 1, 2, tzinfo=UTC), 100.0, 101.0, 99.0, 100.0, "1d"),
        _bar(datetime(2024, 1, 3, tzinfo=UTC), 100.0, 101.0, 99.0, 100.0, "1d"),
    )
    h4 = (
        _bar(datetime(2024, 1, 1, 20, tzinfo=UTC), 100.0, 101.0, 99.0, 100.0, "4h"),
        _bar(datetime(2024, 1, 2, 0, tzinfo=UTC), 100.0, 101.0, 99.0, 100.0, "4h"),
        _bar(datetime(2024, 1, 2, 4, tzinfo=UTC), 100.0, 101.0, 99.0, 100.0, "4h"),
    )
    return _data(bars, d1=d1, h4=h4), bars[0].observed_at + timedelta(minutes=15)


def test_research_data_loader_and_identity(tmp_path: Path) -> None:
    def write_run(name: str, timeframe: str, rows: list[list[str]]) -> DatasetSpec:
        directory = tmp_path / name
        directory.mkdir()
        candles = directory / "candles.csv"
        with candles.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_utc", "open", "high", "low", "close", "volume"])
            writer.writerows(rows)
        checksum = hashlib.sha256(candles.read_bytes()).hexdigest()
        metadata = {
            "symbol": SYMBOL,
            "timeframe": timeframe,
            "checksum_sha256": checksum,
            "row_count": len(rows),
            "first_observation_utc": rows[0][0],
            "last_observation_utc": rows[-1][0],
        }
        (directory / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        return DatasetSpec(directory, timeframe, expected_checksum=checksum)

    d1_final = write_run(
        "d1",
        "1d",
        [
            ["2024-01-02T00:00:00+00:00", "100", "101", "99", "100", "10"],
            ["2024-01-03T00:00:00+00:00", "100", "101", "99", "100", "10"],
        ],
    )
    d1_warm = write_run(
        "d1w",
        "1d",
        [
            ["2023-12-29T00:00:00+00:00", "100", "101", "99", "100", "10"],
            ["2024-01-02T00:00:00+00:00", "100", "101", "99", "100", "10"],
            ["2024-01-03T00:00:00+00:00", "100", "101", "99", "100", "10"],
        ],
    )
    h4 = write_run(
        "h4",
        "4h",
        [
            ["2024-01-02T00:00:00+00:00", "100", "101", "99", "100", "10"],
            ["2024-01-02T04:00:00+00:00", "100", "101", "99", "100", "10"],
        ],
    )
    m15 = write_run(
        "m15",
        "15m",
        [
            ["2024-01-02T01:00:00+00:00", "100", "101", "99", "100", "10"],
            ["2024-01-02T01:15:00+00:00", "100", "101", "99", "100", "10"],
        ],
    )
    loaded = load_dataset(d1_final)
    assert loaded.identity.row_count == 2
    combined = load_research_data((d1_final, h4, m15, d1_warm))
    assert len(combined.d1) == 3
    assert combined.d1[0].observed_at == datetime(2023, 12, 29, tzinfo=UTC)

    tampered = d1_final.path / "candles.csv"
    original = tampered.read_bytes()
    tampered.write_bytes(original + b"\n")
    try:
        with pytest.raises(ResearchDataError):
            load_dataset(d1_final)
    finally:
        tampered.write_bytes(original)


def test_historical_view_is_point_in_time_and_future_invariant() -> None:
    data, decision_time = _engine_data()
    view = HistoricalView(data)
    before = view.as_of(decision_time)
    assert all(item.observed_at <= decision_time for item in before.m15)
    assert all(
        item.observed_at + timedelta(days=1) <= decision_time
        for item in before.d1
    )
    assert all(
        item.observed_at + timedelta(hours=4) <= decision_time
        for item in before.h4
    )
    assert before.next_m15_bar is not None
    assert before.next_m15_bar.observed_at == decision_time
    not_yet_closed = view.as_of(data.m15[0].observed_at)
    assert not_yet_closed.m15 == ()
    assert not_yet_closed.next_m15_bar == data.m15[0]

    future = tuple(data.m15) + (
        _bar(
            decision_time + timedelta(minutes=15),
            102.0,
            104.0,
            101.0,
            103.0,
        ),
    )
    appended = ResearchData(
        d1=data.d1,
        h4=data.h4,
        m15=future,
        d1_close_times=data.d1_close_times,
        h4_close_times=data.h4_close_times,
        m15_close_times=tuple(
            item.observed_at + timedelta(minutes=15) for item in future
        ),
        sources=data.sources,
        d1_final_source=data.d1_final_source,
        d1_warmup_source=data.d1_warmup_source,
    )
    after = HistoricalView(appended).as_of(decision_time)
    assert before.d1 == after.d1
    assert before.h4 == after.h4
    assert before.m15 == after.m15


def test_missing_common_input_overlap_is_rejected() -> None:
    m15 = (
        _bar(
            datetime(2024, 1, 2, 1, 0, tzinfo=UTC),
            100.0,
            100.2,
            99.8,
            100.0,
        ),
    )
    data = _data(m15, d1=(), h4=())
    with pytest.raises(ResearchDataError, match="non-empty"):
        HistoricalView(data).ensure_common_overlap()


def test_decision_record_is_future_data_invariant() -> None:
    data, target = _engine_data()
    strategy = _FakeStrategy({target})
    before = run_backtest(BacktestConfig(data=data, strategy=strategy))  # type: ignore[arg-type]

    future = _bar(
        target + timedelta(minutes=75),
        102.0,
        104.0,
        101.0,
        103.0,
    )
    extended_data = _data(
        tuple(data.m15) + (future,),
        d1=data.d1,
        h4=data.h4,
    )
    after = run_backtest(
        BacktestConfig(data=extended_data, strategy=_FakeStrategy({target}))  # type: ignore[arg-type]
    )

    before_record = next(
        record for record in before.decisions if record.decision_time == target
    )
    after_record = next(
        record for record in after.decisions if record.decision_time == target
    )
    assert before_record.canonical == after_record.canonical
    assert before_record.record_hash == after_record.record_hash
    assert before.trades[0].as_dict() == after.trades[0].as_dict()


def test_fill_tp1_tp2_partial_and_breakeven() -> None:
    decision_time = datetime(2024, 1, 2, 1, 15, tzinfo=UTC)
    entry_bar = _bar(decision_time, 100.0, 100.5, 99.5, 100.2)
    candidate = _candidate(decision_time, entry_bar)
    position = create_position(candidate, entry_bar, trade_id=1)
    assert position.risk_distance == pytest.approx(1.0)

    process_position_bar(
        position,
        _bar(decision_time + timedelta(minutes=15), 100.2, 102.2, 100.0, 101.5),
    )
    assert position.tp1_filled is True
    assert position.remaining_fraction == pytest.approx(0.5)
    assert position.realized_r == pytest.approx(1.0)
    assert position.stop_price == pytest.approx(100.0)

    process_position_bar(
        position,
        _bar(decision_time + timedelta(minutes=30), 101.5, 103.2, 100.3, 102.8),
    )
    assert position.closed is True
    assert position.realized_r == pytest.approx(2.5)
    assert [event.reason for event in position.exits] == ["tp1", "tp2"]

    # A fresh position that reaches TP1 and then returns to breakeven.
    position = create_position(candidate, entry_bar, trade_id=2)
    process_position_bar(
        position,
        _bar(decision_time + timedelta(minutes=15), 100.2, 102.2, 100.0, 101.5),
    )
    process_position_bar(
        position,
        _bar(decision_time + timedelta(minutes=30), 101.0, 101.5, 99.8, 100.0),
    )
    assert position.closed is True
    assert position.status == "breakeven_stopped"
    assert position.realized_r == pytest.approx(1.0)


def test_fill_short_mirror_and_invalid_target_entry() -> None:
    decision_time = datetime(2024, 1, 2, 1, 15, tzinfo=UTC)
    entry_bar = _bar(decision_time, 100.0, 100.5, 99.5, 100.0)
    candidate = _candidate(
        decision_time,
        entry_bar,
        side=Side.SELL,
        entry=100.0,
        stop=101.0,
        tp1=98.0,
        tp2=97.0,
    )
    position = create_position(candidate, entry_bar, trade_id=1)
    process_position_bar(
        position,
        _bar(decision_time + timedelta(minutes=15), 99.8, 100.2, 97.8, 98.2),
    )
    assert position.tp1_filled is True
    assert position.realized_r == pytest.approx(1.0)
    process_position_bar(
        position,
        _bar(decision_time + timedelta(minutes=30), 98.2, 98.4, 96.8, 97.2),
    )
    assert position.closed is True
    assert position.realized_r == pytest.approx(2.5)

    above_target = _bar(decision_time, 102.5, 103.0, 102.0, 102.5)
    long_candidate = _candidate(decision_time, entry_bar)
    assert entry_rejection_reason(long_candidate, above_target) == (
        "entry_open_at_or_beyond_tp1"
    )


def test_fill_stop_first_gap_and_entry_invalid() -> None:
    decision_time = datetime(2024, 1, 2, 1, 15, tzinfo=UTC)
    entry_bar = _bar(decision_time, 100.0, 100.5, 99.5, 100.2)
    candidate = _candidate(decision_time, entry_bar)

    ambiguous = create_position(candidate, entry_bar, trade_id=1)
    process_position_bar(
        ambiguous,
        _bar(decision_time + timedelta(minutes=15), 100.0, 103.0, 98.9, 99.5),
    )
    assert ambiguous.closed is True
    assert ambiguous.status == "stopped"
    assert ambiguous.exits[0].price == pytest.approx(99.0)
    assert ambiguous.exits[0].ambiguous is True

    gap = create_position(candidate, entry_bar, trade_id=2)
    process_position_bar(
        gap,
        _bar(decision_time + timedelta(minutes=15), 98.5, 99.0, 98.0, 98.5),
    )
    assert gap.closed is True
    assert gap.exits[0].price == pytest.approx(98.5)
    assert gap.exits[0].gap is True

    invalid_entry = _bar(decision_time, 98.5, 99.0, 98.0, 98.5)
    assert entry_rejection_reason(candidate, invalid_entry) is not None


def test_engine_integration_canonical_records_and_report() -> None:
    data, target = _engine_data()
    strategy = _FakeStrategy({target})
    config = BacktestConfig(
        data=data,
        strategy=strategy,  # type: ignore[arg-type]
        record_decisions=True,
    )
    result = run_backtest(config)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.closed is True
    assert trade.entry_period == "development"
    assert trade.entry_time == target
    assert trade.entry_price == pytest.approx(data.m15[1].open)
    assert trade.realized_r == pytest.approx(2.5)
    assert trade.status == "tp2_closed"
    assert result.entry_attempts[0].accepted is True
    assert result.decisions[0].canonical is not None
    assert result.decisions[0].canonical["variant_id"] == strategy.identity.variant_id
    assert result.decisions[0].record_hash is not None
    assert trade.decision_hash == result.decisions[0].record_hash
    assert result.entry_attempts[0].decision_hash == result.decisions[0].record_hash

    view = HistoricalView(data).as_of(target)
    direct = strategy.evaluate(
        view.d1,
        view.h4,
        view.m15,
        decision_time=target,
        next_m15_bar=view.next_m15_bar,
        risk_state=ResearchRiskState(1.0, 1.0),
        news_context=None,
        research_mode=True,
        news_unaware=True,
        session_calendar_verified=False,
    )
    assert serialize_strategy_decision(direct, strategy.identity) == (
        result.decisions[0].canonical
    )

    report = result.report
    assert report["labels"]["gross"] == GROSS_LABEL
    assert report["labels"]["research"] == RESEARCH_LABEL
    assert report["record_contract"]["decision_source"] == "StrategyDecision"
    assert report["instrument_profile"]["profile_id"] == XAUUSD_P_PROFILE.profile_id
    assert report["assumptions"]
    assert report["costs"]["gross_only"] is True
    assert report["costs"]["costs_applied"] is False
    assert report["daily_loss_limit"]["status"] == "not_evaluable_monetary"
    assert report["splits"]["development"]["closed_trade_count"] == 1
    assert report["splits"]["development"]["metrics"]["inconclusive_under_30_trades"] is True
    assert report["overall"]["decision_record_count"] == 5
    assert report["overall"]["trades_with_decision_hash"] == 1
    assert report["overall"]["metrics"]["profit_factor"] is None
    assert report["overall"]["metrics"]["profit_factor_status"] == "undefined_no_losses"
    assert report["overall"]["metrics"]["by_confidence"]["medium"]["closed_trade_count"] == 1
    assert report["daily_activity"]["entry_days"] == 1
    assert report["daily_activity"]["no_trade_days"] == 0
    assert result.to_dict(include_decisions=True)["trades"][0]["trade_id"] == 1

    manifest = result.manifest()
    assert manifest["config_fingerprint"] == result.config_fingerprint
    assert manifest["strategy"]["variant_id"] == strategy.identity.variant_id
    assert manifest["instrument_profile"]["symbol"] == "XAUUSD-P"
    assert manifest["sources"][0]["checksum_sha256"] == "synthetic"
    assert manifest["assumptions"] == report["assumptions"]
    assert manifest["limitations"] == report["limitations"]
    assert manifest["counts"]["decision_record_count"] == 5
    assert manifest["counts"]["trades_with_decision_hash"] == 1
    assert manifest["labels"]["gross"] == GROSS_LABEL
    assert all(attempt.decision_hash for attempt in result.entry_attempts)


def test_authoritative_run_requires_canonical_decisions() -> None:
    data, _ = _engine_data()
    with pytest.raises(ValueError, match="authoritative runs require"):
        BacktestConfig(data=data, record_decisions=False)


def test_authoritative_validation_rejects_missing_or_inconsistent_links(
    tmp_path: Path,
) -> None:
    data, target = _engine_data()
    result = run_backtest(
        BacktestConfig(data=data, strategy=_FakeStrategy({target}))  # type: ignore[arg-type]
    )

    unlinked = replace(
        result,
        trades=(replace(result.trades[0], decision_hash=None),),
    )
    with pytest.raises(BacktestError, match="no decision_hash"):
        unlinked.validate_authoritative()

    inconsistent = replace(
        result,
        trades=(replace(result.trades[0], decision_hash="0" * 64),),
    )
    with pytest.raises(BacktestError, match="does not match"):
        inconsistent.validate_authoritative()

    missing = replace(
        result,
        decisions=(replace(result.decisions[0], canonical=None, record_hash=None),),
    )
    with pytest.raises(BacktestError, match="missing canonical decision record"):
        missing.validate_authoritative()

    unlinked_attempt = replace(
        result,
        entry_attempts=(replace(result.entry_attempts[0], decision_hash=None),),
    )
    with pytest.raises(BacktestError, match="entry attempt.*no decision_hash"):
        unlinked_attempt.validate_authoritative()

    with pytest.raises(BacktestError, match="diagnostic-only"):
        result.write_reports(tmp_path, include_decisions=False)

    diagnostic = run_backtest(
        BacktestConfig(
            data=data,
            strategy=_FakeStrategy({target}),  # type: ignore[arg-type]
            record_decisions=False,
            authoritative=False,
        )
    )
    diagnostic_paths = diagnostic.write_reports(tmp_path, include_decisions=False)
    assert {path.name for path in diagnostic_paths} == {
        "summary.json",
        "trades.json",
        "entry_attempts.json",
        "manifest.json",
    }
    diagnostic_manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert diagnostic_manifest["record_contract"]["authoritative"] is False
    assert diagnostic_manifest["counts"]["trades_with_decision_hash"] == 0


def test_cross_period_position_is_truncated_and_carried() -> None:
    start = datetime(2024, 12, 31, 23, 0, tzinfo=UTC)
    bars = (
        _bar(start, 100.0, 100.2, 99.8, 100.0),
        _bar(start + timedelta(minutes=15), 100.0, 100.2, 99.8, 100.0),
        _bar(start + timedelta(minutes=30), 100.0, 100.2, 99.8, 100.0),
        _bar(start + timedelta(minutes=45), 100.0, 100.2, 99.8, 100.0),
        _bar(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), 100.0, 100.2, 99.8, 100.0),
        _bar(datetime(2025, 1, 1, 0, 15, tzinfo=UTC), 100.0, 100.2, 99.8, 100.0),
    )
    d1 = (
        _bar(datetime(2024, 12, 31, tzinfo=UTC), 100, 101, 99, 100, "1d"),
        _bar(datetime(2025, 1, 1, tzinfo=UTC), 100, 101, 99, 100, "1d"),
    )
    h4 = (
        _bar(datetime(2024, 12, 31, 20, tzinfo=UTC), 100, 101, 99, 100, "4h"),
        _bar(datetime(2025, 1, 1, 0, tzinfo=UTC), 100, 101, 99, 100, "4h"),
        _bar(datetime(2025, 1, 1, 4, tzinfo=UTC), 100, 101, 99, 100, "4h"),
    )
    data = _data(bars, d1=d1, h4=h4)
    target = datetime(2024, 12, 31, 23, 45, tzinfo=UTC)
    result = run_backtest(
        BacktestConfig(
            data=data,
            strategy=_FakeStrategy({target}),  # type: ignore[arg-type]
            record_decisions=False,
            authoritative=False,
        )
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.closed is False
    assert trade.entry_period == "development"
    assert trade.open_or_truncated_at_split_cutoff is True
    assert trade.unrealized_r is not None
    assert result.report["splits"]["development"]["open_or_truncated_trade_count"] == 1
    assert result.report["splits"]["validation"]["carried_position_count_at_split_start"] == 1


def test_daily_r_and_drawdown_report() -> None:
    data, target = _engine_data()
    result = run_backtest(
        BacktestConfig(
            data=data,
            strategy=_FakeStrategy({target}),  # type: ignore[arg-type]
            record_decisions=False,
            authoritative=False,
        )
    )
    days = result.report["daily_r"]["days"]
    assert len(days) == 1
    assert days[0]["realized_r"] == pytest.approx(2.5)
    assert days[0]["max_drawdown_r"] >= 0.0
    assert result.report["daily_loss_limit"]["status"] == "not_evaluable_monetary"


def test_engine_replay_is_deterministic_and_writes_outputs(tmp_path: Path) -> None:
    data, target = _engine_data()
    strategy = _FakeStrategy({target})
    config = BacktestConfig(data=data, strategy=strategy)  # type: ignore[arg-type]
    first = run_backtest(config)
    second = run_backtest(config)
    assert first.to_dict(include_decisions=True) == second.to_dict(include_decisions=True)
    assert first.manifest() == second.manifest()
    outputs = first.write_reports(tmp_path)
    assert {path.name for path in outputs} == {
        "summary.json",
        "trades.json",
        "entry_attempts.json",
        "decisions.jsonl",
        "manifest.json",
    }
    assert all(path.is_file() for path in outputs)
    written_manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert written_manifest == first.manifest()
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["labels"]["gross"] == GROSS_LABEL
    assert summary["instrument_profile"]["profile_id"] == XAUUSD_P_PROFILE.profile_id


def test_split_boundaries() -> None:
    assert split_for(datetime(2024, 6, 1, tzinfo=UTC)) == "development"
    assert split_for(datetime(2025, 6, 1, tzinfo=UTC)) == "validation"
    assert split_for(datetime(2026, 6, 1, tzinfo=UTC)) == "holdout"
    assert split_for(datetime(2027, 1, 1, tzinfo=UTC)) is None
    assert [(split.name, split.start.date(), split.end.date()) for split in SPLITS] == [
        ("development", datetime(2024, 1, 1).date(), datetime(2024, 12, 31).date()),
        ("validation", datetime(2025, 1, 1).date(), datetime(2025, 12, 31).date()),
        ("holdout", datetime(2026, 1, 1).date(), datetime(2026, 10, 21).date()),
    ]


def test_research_backtest_has_no_broker_or_execution_imports() -> None:
    backtest_root = Path(__file__).resolve().parents[1] / "src" / "trantor" / "research" / "backtest"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(backtest_root.glob("*.py"))
    )
    forbidden = (
        "trantor.execution",
        "trantor.safety",
        "MetaTrader5",
        "place_order",
        "order_send",
        "TradeSignal",
    )
    for token in forbidden:
        assert token not in source, token


def test_invalid_backtest_configuration() -> None:
    data, _ = _engine_data()
    with pytest.raises(ValueError):
        BacktestConfig(data=data, m15_lookback=10)
    with pytest.raises(TypeError):
        BacktestConfig(data=data, strategy=object())  # type: ignore[arg-type]


def test_unrealized_r_is_separate_from_realized() -> None:
    assert unrealized_r(Side.BUY, 100.0, 101.0, 1.0, 0.5) == pytest.approx(0.5)
    assert unrealized_r(Side.SELL, 100.0, 99.0, 1.0, 0.5) == pytest.approx(0.5)


def test_risk_book_daily_entries_and_cooldown() -> None:
    book = _RiskBook()
    at = datetime(2024, 1, 2, 1, 0, tzinfo=UTC)
    book.on_entry(at)
    book.on_entry(at + timedelta(minutes=15))
    assert book.entries_today == 2
    assert book.open_positions == 2

    exit_at = at + timedelta(hours=1)
    book.on_full_exit(exit_at, -1.0)
    assert book.consecutive_losses == 1
    assert book.cooldown_until == exit_at + timedelta(minutes=16 * 15)

    book.reset_day(at + timedelta(days=1))
    assert book.entries_today == 0
    assert book.cooldown_until is not None

    second_exit = exit_at + timedelta(minutes=30)
    book.on_full_exit(second_exit, -1.0)
    assert book.consecutive_losses == 2
    assert book.cooldown_until == second_exit + timedelta(minutes=32 * 15)

    book.on_full_exit(second_exit + timedelta(minutes=5), 1.0)
    assert book.consecutive_losses == 0
    assert book.cooldown_until is not None
    book.on_full_exit(book.cooldown_until, 1.0)
    assert book.cooldown_until is None
