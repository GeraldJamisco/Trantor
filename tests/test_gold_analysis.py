from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trantor.config import StrategyStatus
from trantor.market_data import MarketDataStatus
from trantor.models import AssetClass, MarketInstrument
from trantor.scanners import (
    EvaluationOutcome,
    FilterName,
    FilterStatus,
    GoldAnalysisInput,
    GoldOpportunityAnalyzer,
    RejectionCode,
    ScanOutcome,
)
from phase3_helpers import (
    GC,
    MGC,
    REG_NOW,
    REG_REFERENCE_AT,
    bar,
    gold_input,
    gold_strategy,
    unverified_gold_schedule,
    verified_gold_schedule,
)

UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")
GLD = MarketInstrument(
    "GLD", AssetClass.GOLD_ETF, "NYSE Arca", "USD", "America/New_York"
)
XAUUSD = MarketInstrument("XAUUSD", AssetClass.GOLD_SPOT, "BROKER", "USD", "UTC")
SIL = MarketInstrument(
    "SIL", AssetClass.GOLD_FUTURES, "COMEX", "USD", "America/New_York"
)
VERIFIED = verified_gold_schedule()


def _report(
    data: GoldAnalysisInput | tuple[GoldAnalysisInput, ...],
    *,
    strategy=None,
    schedule=VERIFIED,
    now: datetime = REG_NOW,
):
    if isinstance(data, GoldAnalysisInput):
        data = (data,)
    kwargs = {} if schedule is None else {"futures_schedule": schedule}
    analyzer = GoldOpportunityAnalyzer(strategy or gold_strategy(), **kwargs)
    return analyzer.analyze(data, evaluated_at=now)


def _analyze(
    data: GoldAnalysisInput | tuple[GoldAnalysisInput, ...],
    *,
    strategy=None,
    schedule=VERIFIED,
    now: datetime = REG_NOW,
):
    return _report(
        data,
        strategy=strategy,
        schedule=schedule,
        now=now,
    ).evaluations[0]


def test_gc_and_mgc_produce_research_candidates() -> None:
    for instrument in (GC, MGC):
        evaluation = _analyze(gold_input(instrument))

        assert evaluation.outcome is EvaluationOutcome.CANDIDATE
        candidate = evaluation.candidate
        assert candidate is not None
        assert candidate.instrument is instrument
        assert candidate.scan_session.state.value == "open"
        assert candidate.scan_session.verified is True
        assert candidate.measurement("latest_price").value == pytest.approx(2520.0)
        assert candidate.research_only is True
        assert candidate.execution_authorized is False
        assert candidate.trading_eligible is False
        assert candidate.is_trade_signal is False
        assert candidate.is_order is False


@pytest.mark.parametrize("instrument", [GLD, XAUUSD])
def test_non_futures_instruments_are_unsupported(
    instrument: MarketInstrument,
) -> None:
    evaluation = _analyze(gold_input(instrument))

    assert evaluation.outcome is EvaluationOutcome.UNSUPPORTED
    assert "GOLD_FUTURES" in evaluation.message
    assert evaluation.candidate is None
    assert evaluation.rejection is None


def test_futures_symbol_outside_active_scope_is_unsupported() -> None:
    evaluation = _analyze(gold_input(SIL))

    assert evaluation.outcome is EvaluationOutcome.UNSUPPORTED
    assert "not in the strategy's supported instruments" in evaluation.message


def test_strategy_supported_symbols_gate_candidate_analysis() -> None:
    strategy = gold_strategy()
    gc_only = replace(
        strategy,
        supported_instruments=replace(
            strategy.supported_instruments,
            symbols=("GC",),
        ),
    )

    evaluation = _analyze(gold_input(MGC), strategy=gc_only)

    assert evaluation.outcome is EvaluationOutcome.UNSUPPORTED
    assert "MGC" in evaluation.message


def test_default_comex_schedule_is_verified_and_produces_candidates() -> None:
    for instrument in (GC, MGC):
        evaluation = _analyze(gold_input(instrument), schedule=None)

        assert evaluation.outcome is EvaluationOutcome.CANDIDATE
        assert evaluation.candidate is not None
        assert evaluation.candidate.scan_session.verified is True
        assert evaluation.candidate.scan_session.state.value == "open"
        assert (
            evaluation.candidate.scan_session.calendar_name
            == "comex_gold_futures_verified_2026_2027"
        )


def test_default_schedule_holiday_is_not_eligible() -> None:
    at = datetime(2026, 12, 25, 15, 0, tzinfo=UTC)  # 10:00 New York
    evaluation = _analyze(
        gold_input(GC, now=at, previous_at=at, reference_at=at),
        schedule=None,
        now=at,
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.SESSION_NOT_ELIGIBLE
    assert evaluation.rejection.scan_session is not None
    assert evaluation.rejection.scan_session.state.value == "closed"
    assert evaluation.rejection.scan_session.verified is True


def test_default_schedule_special_saturday_is_eligible() -> None:
    at = datetime(2026, 6, 20, 14, 0, tzinfo=UTC)  # 10:00 New York
    evaluation = _analyze(
        gold_input(GC, now=at, previous_at=at, reference_at=at),
        schedule=None,
        now=at,
    )

    assert evaluation.outcome is EvaluationOutcome.CANDIDATE
    assert evaluation.candidate is not None
    assert evaluation.candidate.scan_session.state.value == "open"
    assert evaluation.candidate.scan_session.verified is True


def test_explicit_unverified_comex_schedule_is_still_rejected() -> None:
    evaluation = _analyze(
        gold_input(GC),
        schedule=unverified_gold_schedule(),
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.SESSION_UNVERIFIED
    assert evaluation.rejection.scan_session is not None
    assert evaluation.rejection.scan_session.verified is False


def test_out_of_range_comex_schedule_is_rejected_as_session_error() -> None:
    at = datetime(2028, 1, 3, 14, 30, tzinfo=UTC)
    evaluation = _analyze(
        gold_input(
            GC,
            now=at,
            previous_at=at,
            reference_at=at,
        ),
        schedule=None,
        now=at,
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.SESSION_ERROR
    assert "SessionCalendarRangeError" in evaluation.rejection.message
    assert "verified only" in evaluation.rejection.message


def test_break_session_is_not_eligible() -> None:
    schedule = verified_gold_schedule(with_break=True)
    at = datetime(2026, 10, 26, 17, 30, tzinfo=NEW_YORK)
    evaluation = _analyze(
        gold_input(
            GC,
            now=at,
            reference_at=datetime(2026, 10, 26, 17, 25, tzinfo=NEW_YORK),
        ),
        schedule=schedule,
        now=at,
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.SESSION_NOT_ELIGIBLE
    assert evaluation.rejection.scan_session is not None
    assert evaluation.rejection.scan_session.state.value == "break"


def test_previous_close_measurements_are_explicit() -> None:
    strategy = gold_strategy(
        required_measurements=(
            "latest_price",
            "previous_close",
            "change",
            "change_percent",
            "absolute_change_percent",
        )
    )
    evaluation = _analyze(
        gold_input(
            GC,
            previous_close=2500.0,
            reference_close=2520.0,
            include_previous=True,
        ),
        strategy=strategy,
    )

    assert evaluation.outcome is EvaluationOutcome.CANDIDATE
    candidate = evaluation.candidate
    assert candidate is not None
    assert candidate.previous_close_observation is not None
    assert candidate.measurement("previous_close").value == pytest.approx(2500.0)
    assert candidate.measurement("change").value == pytest.approx(20.0)
    assert candidate.measurement("change_percent").value == pytest.approx(0.8)
    assert candidate.measurement("absolute_change_percent").value == pytest.approx(0.8)


def test_previous_close_is_optional_when_not_required() -> None:
    evaluation = _analyze(gold_input(GC, include_previous=False))

    assert evaluation.outcome is EvaluationOutcome.CANDIDATE
    assert evaluation.candidate is not None
    assert evaluation.candidate.previous_close_observation is None
    assert evaluation.candidate.measurement("previous_close").available is False


def test_required_previous_close_missing_is_rejected() -> None:
    strategy = gold_strategy(required_measurements=("previous_close",))
    evaluation = _analyze(gold_input(GC, include_previous=False), strategy=strategy)

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.REQUIRED_MEASUREMENT_MISSING


def test_stale_previous_close_is_rejected_when_required() -> None:
    strategy = gold_strategy(required_measurements=("previous_close",))
    evaluation = _analyze(
        gold_input(
            GC,
            include_previous=True,
            previous_status=MarketDataStatus.STALE,
        ),
        strategy=strategy,
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.STALE_PREVIOUS_CLOSE


def test_stale_reference_data_is_rejected() -> None:
    evaluation = _analyze(
        gold_input(
            GC,
            reference_observations=(
                bar("GC", "15m", REG_REFERENCE_AT, 2520.0),
            ),
            reference_status=MarketDataStatus.STALE,
        )
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.STALE_REFERENCE_DATA


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (MarketDataStatus.PROVIDER_ERROR, EvaluationOutcome.PROVIDER_ERROR),
        (MarketDataStatus.INVALID, EvaluationOutcome.INVALID_DATA),
        (MarketDataStatus.UNSUPPORTED, EvaluationOutcome.UNSUPPORTED),
        (MarketDataStatus.EMPTY, EvaluationOutcome.EMPTY_DATA),
    ],
)
def test_reference_data_outcomes_are_preserved(
    status: MarketDataStatus,
    outcome: EvaluationOutcome,
) -> None:
    evaluation = _analyze(gold_input(GC, reference_status=status))

    assert evaluation.outcome is outcome
    assert evaluation.candidate is None
    assert evaluation.rejection is None
    assert evaluation.data_results


def test_excluded_symbol_is_rejected() -> None:
    evaluation = _analyze(
        gold_input(GC),
        strategy=gold_strategy(excluded_symbols=("GC",)),
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.EXCLUDED_SYMBOL
    assert evaluation.rejection.filters[0].name is FilterName.EXCLUDED_SYMBOL


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"min_price": 3000.0}, RejectionCode.PRICE_BELOW_MINIMUM),
        ({"max_price": 2000.0}, RejectionCode.PRICE_ABOVE_MAXIMUM),
    ],
)
def test_price_range_filters_are_enforced(
    overrides: dict[str, object],
    code: RejectionCode,
) -> None:
    evaluation = _analyze(
        gold_input(GC, reference_close=2520.0),
        strategy=gold_strategy(**overrides),
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is code


def test_stale_reference_age_is_rejected() -> None:
    evaluation = _analyze(
        gold_input(GC),
        strategy=gold_strategy(max_data_age_seconds=60),
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.DATA_TOO_OLD
    assert evaluation.rejection.filters[0].name is FilterName.DATA_AGE
    assert evaluation.rejection.filters[0].status is FilterStatus.FAILED


def test_reference_result_must_match_instrument() -> None:
    evaluation = _analyze(
        gold_input(GC, reference_instrument=MGC),
    )

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.WRONG_INSTRUMENT


def test_reference_result_must_match_configured_timeframe() -> None:
    evaluation = _analyze(gold_input(GC, reference_timeframe="5m"))

    assert evaluation.outcome is EvaluationOutcome.REJECTED
    assert evaluation.rejection is not None
    assert evaluation.rejection.code is RejectionCode.WRONG_TIMEFRAME


def test_ranking_orders_candidates_without_implying_profitability() -> None:
    strategy = gold_strategy(
        required_measurements=(
            "latest_price",
            "previous_close",
            "change",
            "change_percent",
            "absolute_change_percent",
        )
    )
    report = _report(
        (
            gold_input(
                GC,
                previous_close=2500.0,
                reference_close=2520.0,
                include_previous=True,
            ),
            gold_input(
                MGC,
                previous_close=2500.0,
                reference_close=2550.0,
                include_previous=True,
            ),
        ),
        strategy=strategy,
    )

    assert report.status is ScanOutcome.CANDIDATES_FOUND
    ranks = {
        candidate.instrument.symbol: candidate.rank
        for candidate in report.candidates
    }
    assert ranks == {"MGC": 1, "GC": 2}
    assert "not a probability of profit" in " ".join(report.notes)


def test_report_is_research_only() -> None:
    report = _report((gold_input(GC), gold_input(MGC)))

    assert report.research_only is True
    assert report.execution_authorized is False
    assert report.is_trade_signal is False
    assert report.is_order is False
    assert report.strategy_status is StrategyStatus.PLACEHOLDER
    assert "not gold trade signals or orders" in " ".join(report.notes)


def test_analyzer_rejects_duplicate_instruments() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _report((gold_input(GC), gold_input(GC)))


def test_analyzer_rejects_empty_batch() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _report(())


def test_analyzer_rejects_naive_evaluated_at() -> None:
    analyzer = GoldOpportunityAnalyzer(
        gold_strategy(),
        futures_schedule=VERIFIED,
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        analyzer.analyze(
            (gold_input(GC),),
            evaluated_at=datetime(2026, 10, 22, 10, 30),
        )
