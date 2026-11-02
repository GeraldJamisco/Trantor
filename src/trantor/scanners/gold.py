"""Broker-independent analysis of COMEX gold futures.

GC and MGC use the explicitly labelled COMEX convention. No gold strategy rule
or probability of profit is implemented here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time

from ..config import (
    ConfigurationError,
    GoldAnalysisDiscoveryConfig,
    StrategyConfig,
    StrategyStatus,
)
from ..market_data import (
    GoldSessionCalendar,
    GoldSessionSchedule,
    InstrumentScheduleMismatchError,
    MarketDataResult,
    MarketDataStatus,
    SessionInfo,
    SessionState,
    UnsupportedGoldInstrumentError,
    gold_session_calendar_for,
)
from ..models import AssetClass, MarketInstrument, MarketObservation
from ._common import (
    ObservationSelectionError,
    build_data_digests,
    data_failure,
    eligible_field_names,
    select_latest_eligible,
    select_previous_close,
    validate_result_identity,
)
from .models import (
    EvaluationOutcome,
    FilterEvaluation,
    FilterName,
    FilterStatus,
    GoldAnalysisEvaluation,
    GoldAnalysisReport,
    GoldCandidate,
    GoldMeasurement,
    GoldRejection,
    RejectionCode,
)

__all__ = ["GoldAnalysisInput", "GoldOpportunityAnalyzer"]

_PREVIOUS_MEASUREMENTS = frozenset(
    {
        "previous_close",
        "change",
        "change_percent",
        "absolute_change_percent",
    }
)


@dataclass(frozen=True, slots=True)
class GoldAnalysisInput:
    """Validated current data plus optional prior-close data for one gold future."""

    instrument: MarketInstrument
    reference_result: MarketDataResult
    previous_close_result: MarketDataResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, MarketInstrument):
            raise ValueError("instrument must be a MarketInstrument")
        if not isinstance(self.reference_result, MarketDataResult):
            raise ValueError("reference_result must be a MarketDataResult")
        if self.previous_close_result is not None and not isinstance(
            self.previous_close_result, MarketDataResult
        ):
            raise ValueError(
                "previous_close_result must be a MarketDataResult or None"
            )


class GoldOpportunityAnalyzer:
    """Evaluate gold research candidates from Phase 2 result objects."""

    def __init__(
        self,
        strategy: StrategyConfig,
        *,
        futures_schedule: GoldSessionSchedule | None = None,
    ) -> None:
        if not isinstance(strategy, StrategyConfig):
            raise TypeError("strategy must be a StrategyConfig")
        discovery = strategy.opportunity_discovery
        if not isinstance(discovery, GoldAnalysisDiscoveryConfig):
            raise ConfigurationError(
                "gold analyzer requires a gold_analysis opportunity_discovery "
                "configuration"
            )
        if futures_schedule is not None and not isinstance(
            futures_schedule, GoldSessionSchedule
        ):
            raise TypeError(
                "futures_schedule must be a GoldSessionSchedule or None"
            )
        self._strategy = strategy
        self._config = discovery
        self._futures_schedule = futures_schedule

    @property
    def config(self) -> GoldAnalysisDiscoveryConfig:
        return self._config

    @property
    def strategy(self) -> StrategyConfig:
        return self._strategy

    def analyze(
        self,
        inputs: Sequence[GoldAnalysisInput],
        *,
        evaluated_at: datetime,
    ) -> GoldAnalysisReport:
        """Analyze a non-empty batch at one explicit instant."""

        if not isinstance(evaluated_at, datetime):
            raise ValueError("evaluated_at must be a datetime")
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if isinstance(inputs, (str, bytes, bytearray)):
            raise TypeError("inputs must be a sequence of GoldAnalysisInput values")
        materialized = tuple(inputs)
        if not materialized:
            raise ValueError("inputs must contain at least one GoldAnalysisInput")
        if any(not isinstance(item, GoldAnalysisInput) for item in materialized):
            raise TypeError("inputs must contain only GoldAnalysisInput values")
        symbols = [item.instrument.symbol for item in materialized]
        if len(symbols) != len(set(symbols)):
            raise ValueError("inputs must not contain duplicate instruments")

        evaluations = tuple(
            self.evaluate(item, evaluated_at=evaluated_at)
            for item in materialized
        )
        ranked = self._rank_candidates(evaluations)
        ranked_by_symbol = {
            candidate.instrument.symbol: candidate for candidate in ranked
        }
        if ranked_by_symbol:
            evaluations = tuple(
                replace(
                    evaluation,
                    candidate=ranked_by_symbol[evaluation.instrument.symbol],
                )
                if evaluation.candidate is not None
                else evaluation
                for evaluation in evaluations
            )

        notes = [
            "Research measurements only; they are not gold trade signals or orders.",
            "GC and MGC use the separately labelled COMEX futures schedule.",
            "An unverified schedule is never represented as verified.",
            "Ranking is a deterministic sort over configured measurements and "
            "is not a probability of profit.",
        ]
        if self._strategy.status is StrategyStatus.PLACEHOLDER:
            notes.append(
                "The gold strategy is a placeholder; no candidate authorizes trading."
            )

        return GoldAnalysisReport(
            strategy_name=self._strategy.strategy_name,
            strategy_version=self._strategy.strategy_version,
            strategy_status=self._strategy.status,
            evaluated_at=evaluated_at,
            evaluations=evaluations,
            notes=tuple(notes),
        )

    def evaluate(
        self,
        data: GoldAnalysisInput,
        *,
        evaluated_at: datetime,
    ) -> GoldAnalysisEvaluation:
        """Analyze one gold instrument without provider or broker access."""

        if not isinstance(data, GoldAnalysisInput):
            raise TypeError("data must be a GoldAnalysisInput")
        if not isinstance(evaluated_at, datetime):
            raise ValueError("evaluated_at must be a datetime")
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")

        instrument = data.instrument
        data_entries: list[tuple[str, MarketDataResult]] = [
            ("reference", data.reference_result)
        ]
        if data.previous_close_result is not None:
            data_entries.append(
                ("previous_close", data.previous_close_result)
            )
        data_results = build_data_digests(*data_entries)

        if instrument.asset_class is not AssetClass.GOLD_FUTURES:
            return GoldAnalysisEvaluation(
                instrument=instrument,
                outcome=EvaluationOutcome.UNSUPPORTED,
                message=(
                    "gold analysis supports only GOLD_FUTURES; received "
                    f"{instrument.asset_class.value}"
                ),
                data_results=data_results,
            )
        supported_symbols = self._strategy.supported_instruments.symbols
        if instrument.symbol not in supported_symbols:
            return GoldAnalysisEvaluation(
                instrument=instrument,
                outcome=EvaluationOutcome.UNSUPPORTED,
                message=(
                    f"instrument {instrument.symbol!r} is not in the strategy's "
                    f"supported instruments ({', '.join(supported_symbols)})"
                ),
                data_results=data_results,
            )

        try:
            calendar = self._calendar_for(instrument)
        except (
            InstrumentScheduleMismatchError,
            UnsupportedGoldInstrumentError,
        ) as exc:
            return GoldAnalysisEvaluation(
                instrument=instrument,
                outcome=EvaluationOutcome.UNSUPPORTED,
                message=str(exc),
                data_results=data_results,
            )

        preflight: list[tuple[str, MarketDataResult]] = [
            ("reference", data.reference_result)
        ]
        if (
            data.previous_close_result is not None
            and data.previous_close_result.status
            in (
                MarketDataStatus.PROVIDER_ERROR,
                MarketDataStatus.INVALID,
                MarketDataStatus.UNSUPPORTED,
            )
        ):
            preflight.append(("previous_close", data.previous_close_result))
        current_failure = data_failure(*preflight)
        if current_failure is not None:
            return GoldAnalysisEvaluation(
                instrument=instrument,
                outcome=current_failure.outcome,
                message=current_failure.message,
                data_results=data_results,
            )

        try:
            scan_session = calendar.status_at(instrument, evaluated_at)
        except Exception as exc:
            return self._reject(
                instrument,
                RejectionCode.SESSION_ERROR,
                f"scan session could not be determined ({type(exc).__name__}): {exc}",
                data_results=data_results,
            )
        if scan_session.instrument != instrument:
            return self._reject(
                instrument,
                RejectionCode.WRONG_INSTRUMENT,
                "session calendar returned a different instrument",
                scan_session=scan_session,
                data_results=data_results,
            )

        eligible_states = frozenset(
            SessionState(item) for item in self._config.eligible_sessions
        )
        if scan_session.state not in eligible_states:
            return self._reject(
                instrument,
                RejectionCode.SESSION_NOT_ELIGIBLE,
                f"market session {scan_session.state.value!r} is not in the "
                "configured gold eligible_sessions",
                scan_session=scan_session,
                data_results=data_results,
            )
        if self._config.require_verified_session and not scan_session.verified:
            return self._reject(
                instrument,
                RejectionCode.SESSION_UNVERIFIED,
                "the configured strategy requires a verified session schedule; "
                f"{scan_session.calendar_name!r} is explicitly unverified",
                scan_session=scan_session,
                data_results=data_results,
            )

        if data.reference_result.status is MarketDataStatus.STALE:
            return self._reject(
                instrument,
                RejectionCode.STALE_REFERENCE_DATA,
                "the gold reference market-data result is stale and was not used",
                scan_session=scan_session,
                data_results=data_results,
            )

        try:
            validate_result_identity(
                data.reference_result,
                instrument,
                self._config.reference_timeframe,
                evaluated_at=evaluated_at,
            )
        except ObservationSelectionError as exc:
            return self._reject(
                instrument,
                exc.code,
                exc.message,
                scan_session=scan_session,
                data_results=data_results,
            )

        if instrument.symbol in self._config.excluded_symbols:
            excluded_filter = FilterEvaluation(
                name=FilterName.EXCLUDED_SYMBOL,
                status=FilterStatus.FAILED,
                required=True,
                observed=instrument.symbol,
                threshold=", ".join(self._config.excluded_symbols),
                detail="instrument is explicitly excluded by configuration",
            )
            return self._reject(
                instrument,
                RejectionCode.EXCLUDED_SYMBOL,
                f"{instrument.symbol} is excluded by configuration",
                filters=(excluded_filter,),
                scan_session=scan_session,
                data_results=data_results,
            )

        try:
            reference_observation, _, reference_age = select_latest_eligible(
                data.reference_result,
                calendar,
                instrument,
                evaluated_at=evaluated_at,
                states=eligible_states,
                session_date=None,
                max_age_seconds=self._config.max_data_age_seconds,
            )
        except ObservationSelectionError as exc:
            age_filters: tuple[FilterEvaluation, ...] = ()
            if exc.code is RejectionCode.DATA_TOO_OLD:
                age_filters = (
                    FilterEvaluation(
                        name=FilterName.DATA_AGE,
                        status=FilterStatus.FAILED,
                        required=True,
                        observed=None,
                        threshold=float(self._config.max_data_age_seconds),
                        detail=(
                            "the selected eligible reference exceeds the "
                            "configured maximum age"
                        ),
                    ),
                )
            return self._reject(
                instrument,
                exc.code,
                exc.message,
                filters=age_filters,
                scan_session=scan_session,
                data_results=data_results,
            )

        previous_observation: MarketObservation | None = None
        previous_unavailable_reason = "no previous-close result was supplied"
        required_previous = bool(
            set(self._config.required_measurements) & _PREVIOUS_MEASUREMENTS
        )
        if data.previous_close_result is not None:
            previous_status = data.previous_close_result.status
            if previous_status in (
                MarketDataStatus.PROVIDER_ERROR,
                MarketDataStatus.INVALID,
                MarketDataStatus.UNSUPPORTED,
            ):
                return GoldAnalysisEvaluation(
                    instrument=instrument,
                    outcome={
                        MarketDataStatus.PROVIDER_ERROR: EvaluationOutcome.PROVIDER_ERROR,
                        MarketDataStatus.INVALID: EvaluationOutcome.INVALID_DATA,
                        MarketDataStatus.UNSUPPORTED: EvaluationOutcome.UNSUPPORTED,
                    }[previous_status],
                    message=(
                        f"previous_close data outcome is {previous_status.value}: "
                        f"{data.previous_close_result.message}"
                    ),
                    data_results=data_results,
                )
            if previous_status is MarketDataStatus.STALE:
                previous_unavailable_reason = (
                    "the previous-close result is stale and was not used"
                )
                if required_previous:
                    return self._reject(
                        instrument,
                        RejectionCode.STALE_PREVIOUS_CLOSE,
                        previous_unavailable_reason,
                        missing_information=tuple(
                            sorted(
                                set(self._config.required_measurements)
                                & _PREVIOUS_MEASUREMENTS
                            )
                        ),
                        scan_session=scan_session,
                        data_results=data_results,
                    )
            elif previous_status is MarketDataStatus.EMPTY:
                previous_unavailable_reason = (
                    "the previous-close result contains no observations"
                )
            else:
                try:
                    validate_result_identity(
                        data.previous_close_result,
                        instrument,
                        self._config.previous_close_timeframe,
                        evaluated_at=evaluated_at,
                    )
                except ObservationSelectionError as exc:
                    return self._reject(
                        instrument,
                        exc.code,
                        exc.message,
                        scan_session=scan_session,
                        data_results=data_results,
                    )
                try:
                    previous_observation, _ = select_previous_close(
                        data.previous_close_result,
                        calendar,
                        instrument,
                        scan_session,
                        timeframe=self._config.previous_close_timeframe,
                        is_daily_bar=self._config.previous_close_is_daily,
                        required_state=SessionState.OPEN,
                        probe_times=(time(10, 0), time(14, 0), time(20, 0)),
                    )
                except ObservationSelectionError as exc:
                    previous_unavailable_reason = exc.message
                    if required_previous:
                        return self._reject(
                            instrument,
                            RejectionCode.REQUIRED_MEASUREMENT_MISSING,
                            exc.message,
                            missing_information=tuple(
                                sorted(
                                    set(self._config.required_measurements)
                                    & _PREVIOUS_MEASUREMENTS
                                )
                            ),
                            scan_session=scan_session,
                            data_results=data_results,
                        )

        measurements = self._build_measurements(
            instrument,
            reference_observation,
            previous_observation,
            reference_age,
            previous_unavailable_reason,
        )
        values = {
            item.name: item.value
            for item in measurements
            if item.available
        }

        filters: list[FilterEvaluation] = [
            FilterEvaluation(
                name=FilterName.SESSION,
                status=FilterStatus.PASSED,
                required=True,
                observed=scan_session.state.value,
                threshold=", ".join(self._config.eligible_sessions),
                detail="scan session is in the configured gold eligible sessions",
            ),
            FilterEvaluation(
                name=FilterName.SESSION_VERIFIED,
                status=(
                    FilterStatus.PASSED
                    if scan_session.verified
                    else FilterStatus.UNAVAILABLE
                ),
                required=self._config.require_verified_session,
                observed=scan_session.verified,
                threshold="verified",
                detail=(
                    "session schedule is verified"
                    if scan_session.verified
                    else "session schedule is explicitly unverified"
                ),
            ),
            FilterEvaluation(
                name=FilterName.EXCLUDED_SYMBOL,
                status=FilterStatus.PASSED,
                required=True,
                observed=instrument.symbol,
                threshold=(
                    ", ".join(self._config.excluded_symbols)
                    if self._config.excluded_symbols
                    else "(none)"
                ),
                detail="instrument is not excluded",
            ),
        ]

        missing_fields = eligible_field_names(
            reference_observation,
            self._config.required_fields,
        )
        filters.append(
            FilterEvaluation(
                name=FilterName.REQUIRED_FIELDS,
                status=(
                    FilterStatus.FAILED
                    if missing_fields
                    else FilterStatus.PASSED
                ),
                required=True,
                observed=(
                    "missing: " + ", ".join(missing_fields)
                    if missing_fields
                    else "all present"
                ),
                threshold=", ".join(self._config.required_fields),
                detail=(
                    "configured reference fields are missing"
                    if missing_fields
                    else "all configured reference fields are present"
                ),
            )
        )

        reference_price = float(reference_observation.close)
        below_minimum = (
            self._config.min_price is not None
            and reference_price < self._config.min_price
        )
        above_maximum = (
            self._config.max_price is not None
            and reference_price > self._config.max_price
        )
        if self._config.min_price is not None or self._config.max_price is not None:
            filters.append(
                FilterEvaluation(
                    name=FilterName.PRICE_RANGE,
                    status=(
                        FilterStatus.FAILED
                        if below_minimum or above_maximum
                        else FilterStatus.PASSED
                    ),
                    required=True,
                    observed=reference_price,
                    threshold=(
                        f"{self._config.min_price}..{self._config.max_price}"
                    ),
                    detail=(
                        "reference price is below the configured minimum"
                        if below_minimum
                        else "reference price is above the configured maximum"
                        if above_maximum
                        else "reference price is within the configured range"
                    ),
                )
            )

        filters.append(
            FilterEvaluation(
                name=FilterName.DATA_AGE,
                status=FilterStatus.PASSED,
                required=True,
                observed=reference_age,
                threshold=float(self._config.max_data_age_seconds),
                detail="reference observation satisfies the configured maximum age",
            )
        )

        missing_required_measurements = tuple(
            name
            for name in self._config.required_measurements
            if name not in values
        )
        filters.append(
            FilterEvaluation(
                name=FilterName.REQUIRED_MEASUREMENTS,
                status=(
                    FilterStatus.FAILED
                    if missing_required_measurements
                    else FilterStatus.PASSED
                ),
                required=True,
                observed=(
                    "missing: " + ", ".join(missing_required_measurements)
                    if missing_required_measurements
                    else "all present"
                ),
                threshold=", ".join(self._config.required_measurements),
                detail=(
                    "configured gold measurements are unavailable"
                    if missing_required_measurements
                    else "all configured gold measurements are available"
                ),
            )
        )

        missing_information: list[str] = []
        if missing_fields:
            missing_information.extend(
                f"required_field:{field}" for field in missing_fields
            )
        for measurement in measurements:
            if not measurement.available:
                missing_information.append(measurement.name)
        for name in missing_required_measurements:
            tagged = f"required_measurement:{name}"
            if tagged not in missing_information:
                missing_information.append(tagged)

        blocking = [item for item in filters if item.blocked]
        if blocking:
            code = self._rejection_code_for(
                blocking,
                reference_price,
                missing_required_measurements,
            )
            return self._reject(
                instrument,
                code,
                "; ".join(item.detail for item in blocking),
                filters=tuple(filters),
                missing_information=tuple(dict.fromkeys(missing_information)),
                measurements=measurements,
                scan_session=scan_session,
                data_results=data_results,
            )

        change = values.get("change")
        change_percent = values.get("change_percent")
        price_rationale = (
            "no price range is configured"
            if self._config.min_price is None
            and self._config.max_price is None
            else f"latest price {reference_price:.4f} is within the configured range"
        )
        rationale_parts = [
            price_rationale,
            f"reference age {reference_age:.1f}s is within the configured "
            f"maximum {self._config.max_data_age_seconds}s",
            f"session is {scan_session.state.value} on calendar "
            f"{scan_session.calendar_name}",
        ]
        if change is not None and change_percent is not None:
            rationale_parts.append(
                f"change from prior close is {change:.4f} "
                f"({change_percent:.4f}%)"
            )
        else:
            rationale_parts.append(
                "prior-close-based measurements are unavailable and were not "
                "required by this configuration"
            )
        if not scan_session.verified:
            rationale_parts.append(
                "session schedule is unverified; this remains research-only"
            )
        if self._strategy.status is StrategyStatus.PLACEHOLDER:
            rationale_parts.append(
                "strategy status is placeholder; this does not authorize trading"
            )

        candidate = GoldCandidate(
            instrument=instrument,
            scan_session=scan_session,
            reference_observation=reference_observation,
            previous_close_observation=previous_observation,
            measurements=measurements,
            filters=tuple(filters),
            missing_information=tuple(dict.fromkeys(missing_information)),
            pass_rationale="; ".join(rationale_parts),
            eligible_for_strategy_evaluation=True,
            session_verified=scan_session.verified,
        )
        return GoldAnalysisEvaluation(
            instrument=instrument,
            outcome=EvaluationOutcome.CANDIDATE,
            message=(
                f"{instrument.symbol} produced a research candidate at "
                f"{reference_price:.4f}; no trading rule was evaluated"
            ),
            data_results=data_results,
            candidate=candidate,
        )

    def _calendar_for(self, instrument: MarketInstrument) -> GoldSessionCalendar:
        if instrument.asset_class is not AssetClass.GOLD_FUTURES:
            raise UnsupportedGoldInstrumentError(
                f"asset class {instrument.asset_class.value!r} is not a gold future"
            )
        return gold_session_calendar_for(
            instrument,
            futures_schedule=self._futures_schedule,
        )

    @staticmethod
    def _build_measurements(
        instrument: MarketInstrument,
        reference: MarketObservation,
        previous: MarketObservation | None,
        reference_age: float,
        previous_unavailable_reason: str,
    ) -> tuple[GoldMeasurement, ...]:
        latest_price = float(reference.close)
        available: list[GoldMeasurement] = [
            GoldMeasurement(
                name="latest_price",
                value=latest_price,
                unit=instrument.currency,
                available=True,
                source="current reference observation close",
                notes="Raw close from the selected eligible observation.",
            ),
            GoldMeasurement(
                name="reference_age_seconds",
                value=reference_age,
                unit="seconds",
                available=True,
                source="scan evaluation time minus reference observation time",
                notes="Data-age measurement, not a trading score.",
            ),
        ]
        if reference.volume is None:
            available.append(
                GoldMeasurement(
                    name="reference_volume",
                    value=None,
                    unit="units",
                    available=False,
                    source="current reference observation",
                    notes="Provider did not supply volume; it was not set to zero.",
                )
            )
        else:
            available.append(
                GoldMeasurement(
                    name="reference_volume",
                    value=float(reference.volume),
                    unit="units",
                    available=True,
                    source="current reference observation volume",
                )
            )

        previous_close = (
            float(previous.close) if previous is not None else None
        )
        if previous_close is None:
            unavailable = GoldMeasurement(
                name="previous_close",
                value=None,
                unit=instrument.currency,
                available=False,
                source="previous-close analysis",
                notes=previous_unavailable_reason,
            )
            available.append(unavailable)
            for name, description in (
                ("change", "absolute change from the prior close"),
                ("change_percent", "percentage change from the prior close"),
                (
                    "absolute_change_percent",
                    "absolute percentage change from the prior close",
                ),
            ):
                available.append(
                    GoldMeasurement(
                        name=name,
                        value=None,
                        unit=(
                            instrument.currency
                            if name == "change"
                            else "percent"
                        ),
                        available=False,
                        source="previous-close analysis",
                        notes=description + " is unavailable: "
                        + previous_unavailable_reason,
                    )
                )
            return tuple(available)

        change = latest_price - previous_close
        change_percent = (change / previous_close) * 100.0
        available.extend(
            (
                GoldMeasurement(
                    name="previous_close",
                    value=previous_close,
                    unit=instrument.currency,
                    available=True,
                    source="selected prior-session close",
                ),
                GoldMeasurement(
                    name="change",
                    value=change,
                    unit=instrument.currency,
                    available=True,
                    source="latest price minus previous close",
                ),
                GoldMeasurement(
                    name="change_percent",
                    value=change_percent,
                    unit="percent",
                    available=True,
                    source=(
                        "(latest price - previous close) / previous close * 100"
                    ),
                ),
                GoldMeasurement(
                    name="absolute_change_percent",
                    value=abs(change_percent),
                    unit="percent",
                    available=True,
                    source="absolute value of change_percent",
                    notes="A measurement, not a probability of profit.",
                ),
            )
        )
        return tuple(available)

    def _rank_candidates(
        self,
        evaluations: tuple[GoldAnalysisEvaluation, ...],
    ) -> tuple[GoldCandidate, ...]:
        candidates = [
            evaluation.candidate
            for evaluation in evaluations
            if evaluation.candidate is not None
        ]
        candidates.sort(key=lambda item: item.instrument.symbol)
        for criterion in reversed(self._config.ranking.measurements):
            if criterion.direction.value == "descending":
                candidates.sort(
                    key=lambda item: self._ranking_key(
                        item, criterion.measurement
                    )
                )
            else:
                candidates.sort(
                    key=lambda item: self._ascending_ranking_key(
                        item, criterion.measurement
                    )
                )
        return tuple(
            replace(candidate, rank=index)
            for index, candidate in enumerate(candidates, start=1)
        )

    @staticmethod
    def _measurement_value(
        candidate: GoldCandidate,
        measurement: str,
    ) -> float | None:
        item = candidate.measurement(measurement)
        if item is None or not item.available:
            return None
        return item.value

    @classmethod
    def _ranking_key(
        cls,
        candidate: GoldCandidate,
        measurement: str,
    ) -> tuple[bool, float]:
        value = cls._measurement_value(candidate, measurement)
        return (value is None, -(value or 0.0))

    @classmethod
    def _ascending_ranking_key(
        cls,
        candidate: GoldCandidate,
        measurement: str,
    ) -> tuple[bool, float]:
        value = cls._measurement_value(candidate, measurement)
        return (value is None, value if value is not None else 0.0)

    def _rejection_code_for(
        self,
        blocking: list[FilterEvaluation],
        reference_price: float,
        missing_required_measurements: tuple[str, ...],
    ) -> RejectionCode:
        names = {item.name for item in blocking}
        if FilterName.REQUIRED_FIELDS in names:
            return RejectionCode.REQUIRED_FIELD_MISSING
        if FilterName.PRICE_RANGE in names:
            if (
                self._config.min_price is not None
                and reference_price < self._config.min_price
            ):
                return RejectionCode.PRICE_BELOW_MINIMUM
            return RejectionCode.PRICE_ABOVE_MAXIMUM
        if FilterName.DATA_AGE in names:
            return RejectionCode.DATA_TOO_OLD
        if FilterName.REQUIRED_MEASUREMENTS in names:
            return RejectionCode.REQUIRED_MEASUREMENT_MISSING
        if FilterName.SESSION_VERIFIED in names:
            return RejectionCode.SESSION_UNVERIFIED
        return RejectionCode.NO_ELIGIBLE_REFERENCE

    @staticmethod
    def _reject(
        instrument: MarketInstrument,
        code: RejectionCode,
        message: str,
        *,
        filters: tuple[FilterEvaluation, ...] = (),
        missing_information: tuple[str, ...] = (),
        measurements: tuple[GoldMeasurement, ...] = (),
        scan_session: SessionInfo | None = None,
        data_results: tuple = (),
    ) -> GoldAnalysisEvaluation:
        rejection = GoldRejection(
            instrument=instrument,
            code=code,
            message=message,
            filters=filters,
            missing_information=missing_information,
            measurements=measurements,
            scan_session=scan_session,
            data_results=data_results,
        )
        return GoldAnalysisEvaluation(
            instrument=instrument,
            outcome=EvaluationOutcome.REJECTED,
            message=message,
            data_results=data_results,
            rejection=rejection,
        )
