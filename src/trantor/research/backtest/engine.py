"""Deterministic, research-only Phase E historical backtester.

This module deliberately has no broker, execution, monetary-sizing, or cost-model
imports. It consumes the existing strategy and canonical decision record
contract, and produces gross price/R research records only.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from trantor.instruments import InstrumentProfile, XAUUSD_P_PROFILE
from trantor.models import MarketObservation, Side
from trantor.strategy.xauusd_p import (
    ResearchRiskState,
    StrategyDecision,
    XAUUSDPMultiTimeframeStrategy,
)
from trantor.strategy.xauusd_p_research import XAUUSDPMTFResearchVariant
from trantor.strategy.research_record import (
    RESEARCH_RECORD_SCHEMA,
    RESEARCH_RECORD_SCHEMA_VERSION,
    canonical_json,
    serialize_strategy_decision,
)

from .data import DatasetIdentity, ResearchData, ResearchDataError
from .fills import (
    EntryAttempt,
    OpenPosition,
    TradeRecord,
    create_position,
    entry_rejection_reason,
    finish_trade,
    process_position_bar,
)
from .replay import HistoricalView

__all__ = [
    "BacktestConfig",
    "BacktestError",
    "BacktestResult",
    "DecisionRecord",
    "GROSS_LABEL",
    "HOLDOUT_CUTOFF",
    "RESEARCH_LABEL",
    "Split",
    "SPLITS",
    "split_for",
    "run_backtest",
]

UTC = timezone.utc
GROSS_LABEL = "GROSS BASELINE - EXECUTION COSTS EXCLUDED"
RESEARCH_LABEL = "RESEARCH-ONLY FILTER VARIANT - NOT COMPLETE APPROVED STRATEGY"
HOLDOUT_CUTOFF = datetime(2026, 10, 21, 23, 59, 59, 999999, tzinfo=UTC)

BACKTEST_ASSUMPTIONS = (
    "Timestamps are dataset_time; absolute UTC provenance is unverified.",
    "Only completed bars whose close is at or before the decision time are used.",
    "Entry reference is the next M15 open; trigger-close fills are not used.",
    "Ambiguous same-bar stop/target outcomes use conservative stop-first.",
    "Gap-through-stop fills at the open; gap-through-target fills at the target.",
    "At most one target event is credited per bar; TP2 is inactive before TP1.",
    "TP1 uses the configured fraction; TP2 closes the remaining fraction.",
    "Breakeven moves the remaining stop to entry after TP1.",
    "Open or truncated positions are reported without inventing a close.",
    "Results are gross price/R only; execution costs are excluded.",
    "No monetary sizing, account-equity return, or profitability claim is made.",
)


def _record_hash(canonical: dict[str, Any] | None) -> str | None:
    if canonical is None:
        return None
    return hashlib.sha256(canonical_json(canonical).encode()).hexdigest()


def _profile_dict(profile: InstrumentProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "symbol": profile.instrument.symbol,
        "asset_class": profile.instrument.asset_class.value,
        "exchange": profile.instrument.exchange,
        "currency": profile.instrument.currency,
        "timezone": profile.instrument.timezone,
        "session_scope": profile.session_scope,
        "product_identity_verified": profile.product_identity_verified,
        "contract_economics_verified": profile.contract_economics_verified,
        "source": profile.source,
    }


class BacktestError(ValueError):
    """The backtest configuration or state is invalid."""


@dataclass(frozen=True, slots=True)
class Split:
    name: str
    start: datetime
    end: datetime

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


SPLITS = (
    Split(
        "development",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ),
    Split(
        "validation",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ),
    Split(
        "holdout",
        datetime(2026, 1, 1, tzinfo=UTC),
        HOLDOUT_CUTOFF,
    ),
)
_SPLIT_BY_NAME = {split.name: split for split in SPLITS}


def split_for(at: datetime) -> str | None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("split timestamp must be timezone-aware")
    for split in SPLITS:
        if split.start <= at <= split.end:
            return split.name
    return None


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_time: datetime
    period: str
    status: str
    rejection_codes: tuple[str, ...]
    has_candidate: bool
    canonical: dict[str, Any] | None
    record_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_time": self.decision_time.isoformat(),
            "period": self.period,
            "status": self.status,
            "rejection_codes": list(self.rejection_codes),
            "has_candidate": self.has_candidate,
            "record_hash": self.record_hash,
            "canonical": self.canonical,
        }


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    data: ResearchData
    strategy: XAUUSDPMultiTimeframeStrategy = field(
        default_factory=XAUUSDPMTFResearchVariant
    )
    instrument_profile: InstrumentProfile = XAUUSD_P_PROFILE
    m15_lookback: int = 64
    end_time: datetime = HOLDOUT_CUTOFF
    record_decisions: bool = True
    authoritative: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.data, ResearchData):
            raise TypeError("data must be ResearchData")
        if not isinstance(self.instrument_profile, InstrumentProfile):
            raise TypeError("instrument_profile must be an InstrumentProfile")
        if self.end_time.tzinfo is None or self.end_time.utcoffset() is None:
            raise ValueError("end_time must be timezone-aware")
        if not hasattr(self.strategy, "evaluate") or not hasattr(self.strategy, "identity"):
            raise TypeError("strategy must expose evaluate() and identity")
        if isinstance(self.m15_lookback, bool) or not isinstance(self.m15_lookback, int):
            raise ValueError("m15_lookback must be an integer")
        if self.m15_lookback < 21:
            raise ValueError("m15_lookback must be at least 21")
        if not isinstance(self.authoritative, bool):
            raise ValueError("authoritative must be a boolean")
        if self.authoritative and not self.record_decisions:
            raise ValueError(
                "authoritative runs require canonical decision records"
            )


@dataclass(frozen=True, slots=True)
class BacktestResult:
    decisions: tuple[DecisionRecord, ...]
    trades: tuple[TradeRecord, ...]
    entry_attempts: tuple[EntryAttempt, ...]
    report: dict[str, Any]
    sources: tuple[DatasetIdentity, ...]
    config_fingerprint: str

    def to_dict(
        self,
        *,
        include_decisions: bool = False,
        include_trades: bool = True,
        include_attempts: bool = True,
    ) -> dict[str, Any]:
        payload = json.loads(json.dumps(self.report, sort_keys=True))
        if include_trades:
            payload["trades"] = [trade.as_dict() for trade in self.trades]
        if include_attempts:
            payload["entry_attempts"] = [
                attempt.as_dict() for attempt in self.entry_attempts
            ]
        if include_decisions:
            payload["decisions"] = [record.as_dict() for record in self.decisions]
        return payload

    def manifest(self) -> dict[str, Any]:
        """Return the deterministic run manifest for this result."""

        overall = self.report["overall"]
        return {
            "manifest_version": "phase_e_manifest_1.0",
            "harness_version": self.report["harness_version"],
            "config_fingerprint": self.config_fingerprint,
            "strategy": self.report["strategy"],
            "instrument_profile": self.report["instrument_profile"],
            "record_contract": self.report["record_contract"],
            "sources": [source.as_dict() for source in self.sources],
            "labels": self.report["labels"],
            "assumptions": self.report["assumptions"],
            "limitations": self.report["limitations"],
            "time_semantics": self.report["time_semantics"],
            "costs": self.report["costs"],
            "splits": [split.as_dict() for split in SPLITS],
            "counts": {
                "decision_count": overall["decision_count"],
                "decision_record_count": overall["decision_record_count"],
                "filled_trade_count": overall["filled_trade_count"],
                "trades_with_decision_hash": overall["trades_with_decision_hash"],
            },
        }

    def validate_authoritative(self) -> None:
        """Fail closed if canonical decisions are missing or unlinked."""

        record_contract = self.report.get("record_contract", {})
        if record_contract.get("canonical_records_enabled") is not True:
            raise BacktestError(
                "authoritative runs require canonical decision records"
            )
        if not self.decisions:
            raise BacktestError("authoritative run contains no decision records")

        by_time: dict[datetime, DecisionRecord] = {}
        for decision in self.decisions:
            if decision.canonical is None:
                raise BacktestError(
                    f"missing canonical decision record at "
                    f"{decision.decision_time.isoformat()}"
                )
            if not isinstance(decision.record_hash, str) or not decision.record_hash:
                raise BacktestError(
                    f"missing decision record hash at "
                    f"{decision.decision_time.isoformat()}"
                )
            expected_hash = _record_hash(decision.canonical)
            if decision.record_hash != expected_hash:
                raise BacktestError(
                    f"inconsistent decision record hash at "
                    f"{decision.decision_time.isoformat()}"
                )
            canonical_status = decision.canonical.get("status")
            if canonical_status != decision.status:
                raise BacktestError(
                    f"decision status/record mismatch at "
                    f"{decision.decision_time.isoformat()}"
                )
            if (decision.canonical.get("candidate") is not None) != decision.has_candidate:
                raise BacktestError(
                    f"candidate/record mismatch at "
                    f"{decision.decision_time.isoformat()}"
                )
            canonical_time = decision.canonical.get("decision_time")
            if canonical_time != decision.decision_time.isoformat():
                raise BacktestError(
                    f"decision time/record mismatch at "
                    f"{decision.decision_time.isoformat()}"
                )
            if decision.decision_time in by_time:
                raise BacktestError(
                    f"duplicate decision record at "
                    f"{decision.decision_time.isoformat()}"
                )
            by_time[decision.decision_time] = decision

        for trade in self.trades:
            decision = by_time.get(trade.decision_time)
            if decision is None:
                raise BacktestError(
                    f"trade {trade.trade_id} has no decision record at "
                    f"{trade.decision_time.isoformat()}"
                )
            if not trade.decision_hash:
                raise BacktestError(
                    f"trade {trade.trade_id} has no decision_hash"
                )
            if trade.decision_hash != decision.record_hash:
                raise BacktestError(
                    f"trade {trade.trade_id} decision_hash does not match "
                    "its authoritative decision record"
                )
            if not decision.has_candidate:
                raise BacktestError(
                    f"trade {trade.trade_id} links to a non-candidate decision"
                )

        for attempt in self.entry_attempts:
            decision = by_time.get(attempt.decision_time)
            if decision is None:
                raise BacktestError(
                    f"entry attempt has no decision record at "
                    f"{attempt.decision_time.isoformat()}"
                )
            if not attempt.decision_hash:
                raise BacktestError(
                    f"entry attempt at {attempt.decision_time.isoformat()} "
                    "has no decision_hash"
                )
            if attempt.decision_hash != decision.record_hash:
                raise BacktestError(
                    f"entry attempt at {attempt.decision_time.isoformat()} "
                    "decision_hash does not match its authoritative decision record"
                )
            if attempt.accepted and not decision.has_candidate:
                raise BacktestError(
                    f"accepted entry attempt at "
                    f"{attempt.decision_time.isoformat()} links to a "
                    "non-candidate decision"
                )

        overall = self.report["overall"]
        if overall["decision_record_count"] != len(self.decisions):
            raise BacktestError("decision record count does not match decisions")
        if overall["trades_with_decision_hash"] != len(self.trades):
            raise BacktestError("trade decision linkage count does not match trades")

    def write_reports(
        self,
        output_dir: Path,
        *,
        include_decisions: bool = True,
    ) -> tuple[Path, ...]:
        if (
            not include_decisions
            and self.report.get("record_contract", {}).get(
                "canonical_records_enabled"
            )
            is True
        ):
            raise BacktestError(
                "canonical decisions cannot be omitted from an authoritative "
                "run; --no-decisions is diagnostic-only"
            )
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(
                self.to_dict(
                    include_decisions=False,
                    include_trades=True,
                    include_attempts=True,
                ),
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        outputs.append(summary_path)
        trades_path = output_dir / "trades.json"
        trades_path.write_text(
            json.dumps(
                [trade.as_dict() for trade in self.trades],
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        outputs.append(trades_path)
        attempts_path = output_dir / "entry_attempts.json"
        attempts_path.write_text(
            json.dumps(
                [attempt.as_dict() for attempt in self.entry_attempts],
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        outputs.append(attempts_path)
        if include_decisions:
            decisions_path = output_dir / "decisions.jsonl"
            with decisions_path.open("w", encoding="utf-8", newline="\n") as handle:
                for record in self.decisions:
                    handle.write(
                        json.dumps(
                            record.as_dict(),
                            sort_keys=True,
                            allow_nan=False,
                        )
                        + "\n"
                    )
            outputs.append(decisions_path)
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                self.manifest(),
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        outputs.append(manifest_path)
        return tuple(outputs)


@dataclass(slots=True)
class _RiskBook:
    equity: float = 1.0
    start_of_day_equity: float = 1.0
    open_positions: int = 0
    entries_today: int = 0
    consecutive_losses: int = 0
    last_exit_at: datetime | None = None
    cooldown_until: datetime | None = None
    daily_locked_out: bool = False
    day: date | None = None

    def reset_day(self, at: datetime) -> None:
        current = at.astimezone(UTC).date()
        if self.day == current:
            return
        self.day = current
        self.entries_today = 0
        self.start_of_day_equity = self.equity
        self.daily_locked_out = False

    def state(self) -> ResearchRiskState:
        # 1.0 is a neutral unit value, not account equity. No monetary P&L or
        # sizing is computed here.
        return ResearchRiskState(
            equity=self.equity,
            start_of_day_equity=self.start_of_day_equity,
            open_positions=self.open_positions,
            entries_today=self.entries_today,
            consecutive_losses=self.consecutive_losses,
            last_exit_at=self.last_exit_at,
            cooldown_until=self.cooldown_until,
            daily_locked_out=self.daily_locked_out,
            processed_trigger_times=frozenset(),
        )

    def on_entry(self, at: datetime) -> None:
        self.reset_day(at)
        self.entries_today += 1
        self.open_positions += 1

    def on_full_exit(self, at: datetime, realized_r: float) -> None:
        self.open_positions = max(0, self.open_positions - 1)
        self.last_exit_at = at
        if realized_r < -1e-12:
            self.consecutive_losses += 1
            cooldown_bars = 32 if self.consecutive_losses >= 2 else 16
            calculated = at + timedelta(minutes=15) * cooldown_bars
            self.cooldown_until = (
                calculated
                if self.cooldown_until is None
                else max(self.cooldown_until, calculated)
            )
        else:
            self.consecutive_losses = 0
            if self.cooldown_until is not None and at >= self.cooldown_until:
                self.cooldown_until = None


@dataclass(slots=True)
class _PendingEntry:
    candidate: Any
    decision_period: str
    entry_time: datetime
    decision_hash: str | None


def _jsonable(value: object) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):
        return _jsonable(value.value)
    return str(value)


def _parameters_dict(strategy: object) -> dict[str, Any]:
    parameters = getattr(strategy, "parameters", None)
    if parameters is None:
        return {}
    try:
        value = asdict(parameters)
    except (TypeError, ValueError):
        value = {"repr": repr(parameters)}
    return _jsonable(value)


def _fingerprint(config: BacktestConfig, sources: tuple[DatasetIdentity, ...]) -> str:
    identity = config.strategy.identity
    payload = {
        "strategy": {
            "name": identity.strategy_name,
            "version": identity.strategy_version,
            "variant_id": identity.variant_id,
            "variant_name": identity.variant_name,
            "research_only": identity.research_only,
            "parameters": _parameters_dict(config.strategy),
        },
        "instrument_profile": _profile_dict(config.instrument_profile),
        "m15_lookback": config.m15_lookback,
        "record_decisions": config.record_decisions,
        "authoritative": config.authoritative,
        "end_time": config.end_time.isoformat(),
        "splits": [split.as_dict() for split in SPLITS],
        "sources": [source.as_dict() for source in sources],
        "assumptions": list(BACKTEST_ASSUMPTIONS),
        "record_contract": {
            "schema": RESEARCH_RECORD_SCHEMA,
            "schema_version": RESEARCH_RECORD_SCHEMA_VERSION,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _exit_events(records: tuple[TradeRecord, ...]):
    for trade in records:
        for event in trade.exits:
            yield trade, event


def _max_drawdown(values: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        worst = max(worst, peak - cumulative)
    return worst


def _metrics(trades: list[TradeRecord]) -> dict[str, Any]:
    closed = [trade for trade in trades if trade.closed]
    realized = [trade.realized_r for trade in closed]
    wins = [value for value in realized if value > 1e-12]
    losses = [value for value in realized if value < -1e-12]
    breakeven = [value for value in realized if abs(value) <= 1e-12]
    gross_profit = math.fsum(wins)
    gross_loss = math.fsum(losses)
    ordered = sorted(closed, key=lambda trade: (trade.exit_time or trade.entry_time, trade.trade_id))
    cumulative_values = [trade.realized_r for trade in ordered]
    long_trades = [trade for trade in closed if trade.side == Side.BUY.value]
    short_trades = [trade for trade in closed if trade.side == Side.SELL.value]
    confidence_groups: dict[str, list[float]] = {}
    year_groups: dict[str, list[float]] = {}
    for trade in closed:
        confidence_groups.setdefault(trade.confidence, []).append(trade.realized_r)
        year_groups.setdefault(str(trade.decision_time.year), []).append(
            trade.realized_r
        )
    exit_reasons: dict[str, int] = {}
    for trade in trades:
        for event in trade.exits:
            exit_reasons[event.reason] = exit_reasons.get(event.reason, 0) + 1
    statuses: dict[str, int] = {}
    for trade in trades:
        statuses[trade.status] = statuses.get(trade.status, 0) + 1
    count = len(closed)
    open_trades = [trade for trade in trades if not trade.closed]
    open_unrealized = math.fsum(
        trade.unrealized_r for trade in open_trades if trade.unrealized_r is not None
    )
    return {
        "closed_trade_count": count,
        "open_trade_count": len(trades) - count,
        "open_realized_r": math.fsum(trade.realized_r for trade in open_trades),
        "open_unrealized_r": open_unrealized,
        "open_remaining_fraction": math.fsum(
            trade.remaining_fraction for trade in open_trades
        ),
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": len(breakeven),
        "win_rate": (len(wins) / count) if count else None,
        "gross_realized_r": math.fsum(realized),
        "gross_profit_r": gross_profit,
        "gross_loss_r": gross_loss,
        "average_r": (math.fsum(realized) / count) if count else None,
        "average_win_r": (math.fsum(wins) / len(wins)) if wins else None,
        "average_loss_r": (math.fsum(losses) / len(losses)) if losses else None,
        "profit_factor": (
            gross_profit / abs(gross_loss) if gross_loss < 0 else None
        ),
        "profit_factor_status": (
            "undefined_no_losses" if gross_loss >= 0 else "defined"
        ),
        "max_drawdown_r": _max_drawdown(cumulative_values),
        "long": {
            "closed_trade_count": len(long_trades),
            "gross_realized_r": math.fsum(trade.realized_r for trade in long_trades),
        },
        "short": {
            "closed_trade_count": len(short_trades),
            "gross_realized_r": math.fsum(trade.realized_r for trade in short_trades),
        },
        "by_confidence": {
            confidence: {
                "closed_trade_count": len(values),
                "gross_realized_r": math.fsum(values),
                "average_r": math.fsum(values) / len(values),
            }
            for confidence, values in sorted(confidence_groups.items())
        },
        "by_entry_year": {
            year: {
                "closed_trade_count": len(values),
                "gross_realized_r": math.fsum(values),
            }
            for year, values in sorted(year_groups.items())
        },
        "average_bars_held": (
            sum(trade.bars_held for trade in closed) / count if count else None
        ),
        "exit_reason_counts": dict(sorted(exit_reasons.items())),
        "status_counts": dict(sorted(statuses.items())),
        "ambiguous_bar_count": sum(trade.ambiguous_bar_count for trade in trades),
        "gap_exit_count": sum(trade.gap_exit_count for trade in trades),
        "exposure_bars": sum(trade.bars_held for trade in trades),
        "first_exit_at": (
            min((trade.exit_time for trade in closed if trade.exit_time)).isoformat()
            if any(trade.exit_time for trade in closed)
            else None
        ),
        "last_exit_at": (
            max((trade.exit_time for trade in closed if trade.exit_time)).isoformat()
            if any(trade.exit_time for trade in closed)
            else None
        ),
    }


def _daily_r(trades: tuple[TradeRecord, ...]) -> dict[str, Any]:
    by_day: dict[date, list[tuple[datetime, float, int]]] = {}
    for trade, event in _exit_events(trades):
        day = event.exit_time.astimezone(UTC).date()
        by_day.setdefault(day, []).append(
            (event.exit_time, event.contribution, trade.trade_id)
        )
    days: list[dict[str, Any]] = []
    for day in sorted(by_day):
        events = sorted(by_day[day], key=lambda item: (item[0], item[2]))
        values = [item[1] for item in events]
        days.append(
            {
                "date": day.isoformat(),
                "realized_r": math.fsum(values),
                "max_drawdown_r": _max_drawdown(values),
                "exit_event_count": len(values),
            }
        )
    return {
        "days": days,
        "daily_loss_limit_status": "not_evaluable_monetary",
        "monetary_daily_loss_applied": False,
    }


def _daily_activity(
    decisions: tuple[DecisionRecord, ...],
    trades: tuple[TradeRecord, ...],
) -> dict[str, Any]:
    decision_days = {record.decision_time.date() for record in decisions}
    entry_counts: dict[date, int] = {}
    for trade in trades:
        day = trade.entry_time.date()
        entry_counts[day] = entry_counts.get(day, 0) + 1
    no_trade_days = len(decision_days.difference(entry_counts))
    return {
        "decision_days": len(decision_days),
        "entry_days": len(entry_counts),
        "no_trade_days": no_trade_days,
        "daily_trade_counts": {
            day.isoformat(): count for day, count in sorted(entry_counts.items())
        },
    }


def _trade_in_split(trade: TradeRecord, split: Split) -> bool:
    return trade.entry_period == split.name


def _closed_by_split(trade: TradeRecord, split: Split) -> bool:
    return (
        _trade_in_split(trade, split)
        and trade.closed
        and trade.exit_time is not None
        and trade.exit_time <= split.end
    )


def _split_report(
    split: Split,
    decisions: tuple[DecisionRecord, ...],
    trades: tuple[TradeRecord, ...],
    attempts: tuple[EntryAttempt, ...],
) -> dict[str, Any]:
    split_decisions = [record for record in decisions if record.period == split.name]
    split_trades = [trade for trade in trades if _trade_in_split(trade, split)]
    closed = [trade for trade in split_trades if _closed_by_split(trade, split)]
    open_or_truncated = [trade for trade in split_trades if not _closed_by_split(trade, split)]
    rejected = [record for record in split_decisions if record.status == "rejected"]
    rejection_counts: dict[str, int] = {}
    for record in rejected:
        for code in record.rejection_codes:
            rejection_counts[code] = rejection_counts.get(code, 0) + 1
    boundary_excluded = [
        attempt
        for attempt in attempts
        if attempt.excluded_at_split_boundary
        and split.start <= attempt.decision_time <= split.end
    ]
    carried = [
        trade
        for trade in trades
        if trade.entry_period != split.name
        and trade.entry_time < split.start
        and (trade.exit_time is None or trade.exit_time > split.start)
    ]
    metrics = _metrics(closed)
    metrics["closed_trade_count"] = len(closed)
    metrics["open_or_truncated_trade_count"] = len(open_or_truncated)
    metrics["open_unrealized_r"] = math.fsum(
        trade.unrealized_r
        for trade in open_or_truncated
        if trade.unrealized_r is not None
    )
    metrics["open_remaining_fraction"] = math.fsum(
        trade.remaining_fraction for trade in open_or_truncated
    )
    metrics["inconclusive_under_30_trades"] = len(closed) < 30
    metrics["sample_size_gate"] = (
        "inconclusive_fewer_than_30_closed_trades"
        if len(closed) < 30
        else "descriptive_at_least_30_closed_trades_not_statistical_proof"
    )
    return {
        "split": split.as_dict(),
        "decision_count": len(split_decisions),
        "candidate_count": sum(record.has_candidate for record in split_decisions),
        "rejected_decision_count": len(rejected),
        "rejection_reason_counts": dict(sorted(rejection_counts.items())),
        "entry_boundary_excluded_count": len(boundary_excluded),
        "filled_trade_count": len(split_trades),
        "closed_trade_count": len(closed),
        "open_or_truncated_trade_count": len(open_or_truncated),
        "carried_position_count_at_split_start": len(carried),
        "metrics": metrics,
        "open_or_truncated_trades": [trade.as_dict() for trade in open_or_truncated],
    }


def _build_report(
    config: BacktestConfig,
    decisions: tuple[DecisionRecord, ...],
    trades: tuple[TradeRecord, ...],
    attempts: tuple[EntryAttempt, ...],
    fingerprint: str,
) -> dict[str, Any]:
    identity = config.strategy.identity
    overall_metrics = _metrics(list(trades))
    overall_metrics["open_trade_count"] = len([trade for trade in trades if not trade.closed])
    split_reports = {
        split.name: _split_report(split, decisions, trades, attempts)
        for split in SPLITS
    }
    rejected = [record for record in decisions if record.status == "rejected"]
    rejection_counts: dict[str, int] = {}
    for record in rejected:
        for code in record.rejection_codes:
            rejection_counts[code] = rejection_counts.get(code, 0) + 1
    return {
        "labels": {
            "research": RESEARCH_LABEL,
            "gross": GROSS_LABEL,
        },
        "strategy": {
            "name": identity.strategy_name,
            "version": identity.strategy_version,
            "variant_id": identity.variant_id,
            "variant_name": identity.variant_name,
            "research_only": identity.research_only,
            "parameters": _parameters_dict(config.strategy),
        },
        "instrument_profile": _profile_dict(config.instrument_profile),
        "config_fingerprint": fingerprint,
        "record_contract": {
            "schema": RESEARCH_RECORD_SCHEMA,
            "schema_version": RESEARCH_RECORD_SCHEMA_VERSION,
            "decision_source": "StrategyDecision",
            "canonical_records_enabled": config.record_decisions,
            "authoritative": config.authoritative,
        },
        "assumptions": list(BACKTEST_ASSUMPTIONS),
        "splits": split_reports,
        "overall": {
            "decision_count": len(decisions),
            "decision_record_count": sum(
                record.canonical is not None for record in decisions
            ),
            "trades_with_decision_hash": sum(
                trade.decision_hash is not None for trade in trades
            ),
            "candidate_count": sum(record.has_candidate for record in decisions),
            "rejected_decision_count": len(rejected),
            "rejection_reason_counts": dict(sorted(rejection_counts.items())),
            "entry_attempt_count": len(attempts),
            "entry_boundary_excluded_count": sum(
                attempt.excluded_at_split_boundary for attempt in attempts
            ),
            "filled_trade_count": len(trades),
            "closed_trade_count": len([trade for trade in trades if trade.closed]),
            "open_or_truncated_trade_count": len(
                [trade for trade in trades if not trade.closed]
            ),
            "metrics": overall_metrics,
        },
        "daily_r": _daily_r(trades),
        "daily_activity": _daily_activity(decisions, trades),
        "data_sources": [source.as_dict() for source in config.data.sources],
        "time_semantics": {
            "coordinate": "dataset_time",
            "absolute_utc_provenance": "unverified",
            "session_compliance": "not_claimed",
            "holiday_compliance": "not_claimed",
            "news_compliance": "not_claimed",
        },
        "costs": {
            "gross_only": True,
            "costs_applied": False,
            "observed_spread_applied": False,
            "commission_applied": False,
            "financing_applied": False,
            "slippage_applied": False,
            "monetary_metrics": "unavailable",
            "net_profitability": "not_claimed",
        },
        "daily_loss_limit": {
            "status": "not_evaluable_monetary",
            "applied": False,
        },
        "limitations": [
            "dataset_time uses unverified absolute-UTC provenance",
            "research-only filter variant, not the complete approved strategy",
            "session and news compliance are not claimed",
            "gross price/R only; execution costs are excluded",
            "spread adjustment is blocked",
            "monetary sizing and account-equity returns are unavailable",
            "M15 OHLC cannot establish intrabar tick paths",
            "partial-exit volume feasibility is blocked",
            "fewer than 30 closed trades per split is inconclusive",
        ],
        "harness_version": "phase_e_backtest_1.0",
    }


def run_backtest(config: BacktestConfig) -> BacktestResult:
    """Replay the configured data and return deterministic research records."""

    if not isinstance(config, BacktestConfig):
        raise TypeError("config must be BacktestConfig")
    data = config.data
    view = HistoricalView(data, m15_lookback=config.m15_lookback)
    view.ensure_common_overlap()
    end_time = min(config.end_time, HOLDOUT_CUTOFF)
    first = bisect_left(data.m15_close_times, SPLITS[0].start)
    last = bisect_right(data.m15_close_times, end_time) - 1
    if first > last:
        raise BacktestError("no M15 decisions fall within the evaluation windows")

    strategy = config.strategy
    identity = strategy.identity
    decision_indices = set(
        range(
            first,
            last + 1,
        )
    )
    decisions: list[DecisionRecord] = []
    trades: list[TradeRecord] = []
    attempts: list[EntryAttempt] = []
    open_positions: list[OpenPosition] = []
    position_periods: dict[int, str] = {}
    position_decision_hashes: dict[int, str | None] = {}
    pending: _PendingEntry | None = None
    risk = _RiskBook()
    next_trade_id = 1
    seen_decisions: set[datetime] = set()
    last_close: float | None = None

    for index in range(first, last + 1):
        bar = data.m15[index]
        last_close = bar.close
        if pending is not None and pending.entry_time == bar.observed_at:
            candidate = pending.candidate
            attempt = EntryAttempt(
                decision_time=candidate.decision_time,
                entry_time=candidate.entry_time,
                side=candidate.side.value,
                entry_price=bar.open,
                stop=candidate.plan.stop,
                tp1=candidate.plan.tp1,
                tp2=candidate.plan.tp2,
                accepted=False,
                reason=None,
                excluded_at_split_boundary=False,
                decision_hash=pending.decision_hash,
            )
            if pending.entry_time > _SPLIT_BY_NAME[pending.decision_period].end:
                attempts.append(
                    replace(
                        attempt,
                        reason="entry_boundary_excluded",
                        excluded_at_split_boundary=True,
                    )
                )
            else:
                reason = entry_rejection_reason(candidate, bar)
                if reason is not None:
                    attempts.append(replace(attempt, reason=reason))
                else:
                    position = create_position(candidate, bar, trade_id=next_trade_id)
                    next_trade_id += 1
                    attempts.append(replace(attempt, accepted=True))
                    risk.on_entry(bar.observed_at)
                    open_positions.append(position)
                    position_periods[position.trade_id] = pending.decision_period
                    position_decision_hashes[position.trade_id] = pending.decision_hash
            pending = None

        for position in list(open_positions):
            process_position_bar(position, bar)
            if not position.closed:
                continue
            entry_period = position_periods[position.trade_id]
            entry_split = _SPLIT_BY_NAME[entry_period]
            exit_time = position.exits[-1].exit_time
            exit_period = split_for(exit_time)
            trade = finish_trade(
                position,
                mark_price=None,
                entry_period=entry_period,
                exit_period=exit_period,
                open_or_truncated_at_split_cutoff=(
                    exit_time is None or exit_time > entry_split.end
                ),
                later_period_data_required=exit_period != entry_period,
                decision_hash=position_decision_hashes.get(position.trade_id),
            )
            trades.append(trade)
            risk.on_full_exit(exit_time, position.realized_r)
            open_positions.remove(position)

        if index not in decision_indices:
            continue
        decision_time = data.m15_close_times[index]
        period = split_for(decision_time)
        if period is None:
            continue
        if decision_time in seen_decisions:
            raise BacktestError(f"duplicate decision timestamp {decision_time.isoformat()}")
        seen_decisions.add(decision_time)
        risk.reset_day(decision_time)
        as_of = view.as_of(decision_time, m15_index=index)
        decision: StrategyDecision = strategy.evaluate(
            as_of.d1,
            as_of.h4,
            as_of.m15,
            decision_time=decision_time,
            next_m15_bar=as_of.next_m15_bar,
            risk_state=risk.state(),
            news_context=None,
            research_mode=True,
            news_unaware=True,
            session_calendar_verified=False,
        )
        canonical = (
            serialize_strategy_decision(decision, identity)
            if config.record_decisions
            else None
        )
        record_hash = _record_hash(canonical)
        decisions.append(
            DecisionRecord(
                decision_time=decision_time,
                period=period,
                status=decision.status.value,
                rejection_codes=tuple(code.code.value for code in decision.rejections),
                has_candidate=decision.candidate is not None,
                canonical=canonical,
                record_hash=record_hash,
            )
        )
        if decision.candidate is not None:
            candidate = decision.candidate
            split = _SPLIT_BY_NAME[period]
            if candidate.entry_time > split.end:
                attempts.append(
                    EntryAttempt(
                        decision_time=decision_time,
                        entry_time=candidate.entry_time,
                        side=candidate.side.value,
                        entry_price=candidate.entry_reference,
                        stop=candidate.plan.stop,
                        tp1=candidate.plan.tp1,
                        tp2=candidate.plan.tp2,
                        accepted=False,
                        reason="entry_boundary_excluded",
                        excluded_at_split_boundary=True,
                        decision_hash=record_hash,
                    )
                )
            else:
                pending = _PendingEntry(
                    candidate=candidate,
                    decision_period=period,
                    entry_time=candidate.entry_time,
                    decision_hash=record_hash,
                )

    if pending is not None:
        candidate = pending.candidate
        attempts.append(
            EntryAttempt(
                decision_time=candidate.decision_time,
                entry_time=candidate.entry_time,
                side=candidate.side.value,
                entry_price=candidate.entry_reference,
                stop=candidate.plan.stop,
                tp1=candidate.plan.tp1,
                tp2=candidate.plan.tp2,
                accepted=False,
                reason="entry_after_backtest_end",
                excluded_at_split_boundary=False,
                decision_hash=pending.decision_hash,
            )
        )

    # Close/truncate positions at the data/evaluation end without inventing bars.
    final_mark = last_close
    for position in open_positions:
        entry_period = position_periods[position.trade_id]
        entry_split = _SPLIT_BY_NAME[entry_period]
        last_exit = position.exits[-1].exit_time if position.exits else None
        exit_period = split_for(last_exit) if last_exit is not None else None
        trade = finish_trade(
            position,
            mark_price=final_mark,
            entry_period=entry_period,
            exit_period=exit_period,
            open_or_truncated_at_split_cutoff=True,
            later_period_data_required=True,
            decision_hash=position_decision_hashes.get(position.trade_id),
        )
        trades.append(trade)

    decisions_tuple = tuple(decisions)
    trades_tuple = tuple(trades)
    attempts_tuple = tuple(attempts)
    fingerprint = _fingerprint(config, data.sources)
    report = _build_report(
        config,
        decisions_tuple,
        trades_tuple,
        attempts_tuple,
        fingerprint,
    )
    result = BacktestResult(
        decisions=decisions_tuple,
        trades=trades_tuple,
        entry_attempts=attempts_tuple,
        report=report,
        sources=data.sources,
        config_fingerprint=fingerprint,
    )
    if config.authoritative:
        result.validate_authoritative()
    return result
