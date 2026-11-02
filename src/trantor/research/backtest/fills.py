"""Conservative gross price/R fill and position-state simulation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from trantor.models import MarketObservation, Side
from trantor.strategy.xauusd_p import XAUUSDPSignalCandidate

__all__ = [
    "EntryAttempt",
    "ExitEvent",
    "OpenPosition",
    "TradeRecord",
    "create_position",
    "entry_rejection_reason",
    "finish_trade",
    "process_position_bar",
    "unrealized_r",
]


@dataclass(frozen=True, slots=True)
class ExitEvent:
    exit_time: datetime
    bar_time: datetime
    price: float
    fraction: float
    r_multiple: float
    contribution: float
    reason: str
    gap: bool = False
    ambiguous: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "exit_time": self.exit_time.isoformat(),
            "bar_time": self.bar_time.isoformat(),
            "price": self.price,
            "fraction": self.fraction,
            "r_multiple": self.r_multiple,
            "contribution": self.contribution,
            "reason": self.reason,
            "gap": self.gap,
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True, slots=True)
class EntryAttempt:
    decision_time: datetime
    entry_time: datetime | None
    side: str | None
    entry_price: float | None
    stop: float | None
    tp1: float | None
    tp2: float | None
    accepted: bool
    reason: str | None
    excluded_at_split_boundary: bool = False
    decision_hash: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "decision_time": self.decision_time.isoformat(),
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "side": self.side,
            "entry_price": self.entry_price,
            "stop": self.stop,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "accepted": self.accepted,
            "reason": self.reason,
            "excluded_at_split_boundary": self.excluded_at_split_boundary,
            "decision_hash": self.decision_hash,
        }


@dataclass(slots=True)
class OpenPosition:
    trade_id: int
    candidate: XAUUSDPSignalCandidate
    entry_bar: MarketObservation
    entry_price: float
    side: Side
    stop_price: float
    tp1: float
    tp2: float
    risk_distance: float
    tp1_fraction: float
    tp2_fraction: float
    decision_time: datetime
    entry_time: datetime
    remaining_fraction: float = 1.0
    tp1_filled: bool = False
    tp2_filled: bool = False
    breakeven_active: bool = False
    realized_r: float = 0.0
    exits: list[ExitEvent] = field(default_factory=list)
    ambiguous_bar_count: int = 0
    gap_exit_count: int = 0
    bars_held: int = 0
    last_bar_time: datetime | None = None
    last_close: float | None = None
    closed: bool = False
    status: str = "open"


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: int
    decision_time: datetime
    decision_hash: str | None
    entry_time: datetime
    entry_period: str
    exit_time: datetime | None
    exit_period: str | None
    side: str
    confidence: str
    entry_price: float
    stop: float
    tp1: float
    tp2: float
    risk_distance: float
    tp1_fraction: float
    tp2_fraction: float
    status: str
    closed: bool
    realized_r: float
    remaining_fraction: float
    unrealized_r: float | None
    exits: tuple[ExitEvent, ...]
    ambiguous_bar_count: int
    gap_exit_count: int
    bars_held: int
    open_or_truncated_at_split_cutoff: bool
    later_period_data_required: bool
    entry_boundary_excluded: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "trade_id": self.trade_id,
            "decision_time": self.decision_time.isoformat(),
            "decision_hash": self.decision_hash,
            "entry_time": self.entry_time.isoformat(),
            "entry_period": self.entry_period,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_period": self.exit_period,
            "side": self.side,
            "confidence": self.confidence,
            "entry_price": self.entry_price,
            "stop": self.stop,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "risk_distance": self.risk_distance,
            "tp1_fraction": self.tp1_fraction,
            "tp2_fraction": self.tp2_fraction,
            "status": self.status,
            "closed": self.closed,
            "realized_r": self.realized_r,
            "remaining_fraction": self.remaining_fraction,
            "unrealized_r": self.unrealized_r,
            "exits": [event.as_dict() for event in self.exits],
            "ambiguous_bar_count": self.ambiguous_bar_count,
            "gap_exit_count": self.gap_exit_count,
            "bars_held": self.bars_held,
            "open_or_truncated_at_split_cutoff": self.open_or_truncated_at_split_cutoff,
            "later_period_data_required": self.later_period_data_required,
            "entry_boundary_excluded": self.entry_boundary_excluded,
        }


def _unit_r(
    side: Side,
    entry: float,
    price: float,
    risk: float,
) -> float:
    if risk <= 0:
        raise ValueError("risk distance must be positive")
    return (price - entry) / risk if side is Side.BUY else (entry - price) / risk


def unrealized_r(
    side: Side,
    entry: float,
    mark: float,
    risk: float,
    remaining_fraction: float,
) -> float:
    if remaining_fraction <= 0:
        return 0.0
    return _unit_r(side, entry, mark, risk) * remaining_fraction


def entry_rejection_reason(
    candidate: XAUUSDPSignalCandidate,
    entry_bar: MarketObservation,
) -> str | None:
    """Return a fail-closed entry rejection reason, or ``None``."""

    if candidate.side is Side.BUY:
        if entry_bar.open <= candidate.plan.stop:
            return "entry_open_at_or_beyond_stop"
        if entry_bar.open >= candidate.plan.tp1:
            return "entry_open_at_or_beyond_tp1"
    else:
        if entry_bar.open >= candidate.plan.stop:
            return "entry_open_at_or_beyond_stop"
        if entry_bar.open <= candidate.plan.tp1:
            return "entry_open_at_or_beyond_tp1"
    if not math.isclose(
        candidate.entry_reference,
        entry_bar.open,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        return "entry_open_does_not_match_decision_reference"
    expected_risk = abs(entry_bar.open - candidate.plan.stop)
    if expected_risk <= 0 or not math.isclose(
        candidate.plan.risk_distance,
        expected_risk,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        return "invalid_entry_risk_distance"
    if candidate.plan.tp1_fraction <= 0 or candidate.plan.tp2_fraction <= 0:
        return "invalid_partial_fractions"
    if not math.isclose(
        candidate.plan.tp1_fraction + candidate.plan.tp2_fraction,
        1.0,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        return "invalid_partial_fractions"
    return None


def create_position(
    candidate: XAUUSDPSignalCandidate,
    entry_bar: MarketObservation,
    *,
    trade_id: int,
) -> OpenPosition:
    reason = entry_rejection_reason(candidate, entry_bar)
    if reason is not None:
        raise ValueError(reason)
    return OpenPosition(
        trade_id=trade_id,
        candidate=candidate,
        entry_bar=entry_bar,
        entry_price=entry_bar.open,
        side=candidate.side,
        stop_price=candidate.plan.stop,
        tp1=candidate.plan.tp1,
        tp2=candidate.plan.tp2,
        risk_distance=candidate.plan.risk_distance,
        tp1_fraction=candidate.plan.tp1_fraction,
        tp2_fraction=candidate.plan.tp2_fraction,
        decision_time=candidate.decision_time,
        entry_time=candidate.entry_time,
        last_bar_time=entry_bar.observed_at,
        last_close=entry_bar.close,
    )


def _add_exit(
    position: OpenPosition,
    *,
    bar: MarketObservation,
    price: float,
    fraction: float,
    reason: str,
    gap: bool,
    ambiguous: bool,
) -> None:
    if fraction <= 0:
        raise ValueError("exit fraction must be positive")
    fraction = min(fraction, position.remaining_fraction)
    if fraction <= 0:
        return
    unit_r = _unit_r(position.side, position.entry_price, price, position.risk_distance)
    contribution = unit_r * fraction
    position.remaining_fraction = max(0.0, position.remaining_fraction - fraction)
    position.realized_r += contribution
    position.exits.append(
        ExitEvent(
            # OHLC cannot identify an exact intrabar time. Attribute the
            # event to the completed bar close for deterministic daily R.
            exit_time=bar.observed_at + timedelta(minutes=15),
            bar_time=bar.observed_at,
            price=price,
            fraction=fraction,
            r_multiple=unit_r,
            contribution=contribution,
            reason=reason,
            gap=gap,
            ambiguous=ambiguous,
        )
    )
    if gap:
        position.gap_exit_count += 1
    if ambiguous:
        position.ambiguous_bar_count += 1
    if position.remaining_fraction <= 1e-12:
        position.remaining_fraction = 0.0
        position.closed = True


def process_position_bar(position: OpenPosition, bar: MarketObservation) -> None:
    """Process one completed M15 bar using the approved conservative order.

    The bar open is checked for a gap through the active stop. If the stop is
    touched, it always wins over a target touch on the same bar. Otherwise at
    most one target event is credited per bar. TP2 is inactive until TP1 has
    filled. After TP1, the active stop becomes the entry price.
    """

    if position.closed:
        return
    if position.last_bar_time is not None and bar.observed_at < position.last_bar_time:
        raise ValueError("position bars must be processed chronologically")
    position.bars_held += 1
    position.last_bar_time = bar.observed_at
    position.last_close = bar.close

    side = position.side
    stop = position.stop_price
    active_stop_reason = "breakeven_stop" if position.breakeven_active else "stop"

    if side is Side.BUY:
        if bar.open <= stop:
            _add_exit(
                position,
                bar=bar,
                price=bar.open,
                fraction=position.remaining_fraction,
                reason="gap_" + active_stop_reason,
                gap=True,
                ambiguous=False,
            )
            position.status = "breakeven_stopped" if position.breakeven_active else "stopped"
            return
        touched_stop = bar.low <= stop
        touched_tp1 = not position.tp1_filled and bar.high >= position.tp1
        touched_tp2 = (
            position.tp1_filled
            and not position.tp2_filled
            and bar.high >= position.tp2
        )
        ambiguous = touched_stop and (touched_tp1 or touched_tp2)
        if touched_stop:
            _add_exit(
                position,
                bar=bar,
                price=stop,
                fraction=position.remaining_fraction,
                reason=active_stop_reason,
                gap=False,
                ambiguous=ambiguous,
            )
            position.status = "breakeven_stopped" if position.breakeven_active else "stopped"
            return
        if touched_tp1:
            _add_exit(
                position,
                bar=bar,
                price=position.tp1,
                fraction=position.tp1_fraction,
                reason="tp1",
                gap=bar.open >= position.tp1,
                ambiguous=False,
            )
            position.tp1_filled = True
            if not position.closed:
                position.stop_price = position.entry_price
                position.breakeven_active = True
                position.status = "tp1_filled"
            else:
                position.status = "tp1_closed"
            return
        if touched_tp2:
            _add_exit(
                position,
                bar=bar,
                price=position.tp2,
                fraction=position.remaining_fraction,
                reason="tp2",
                gap=bar.open >= position.tp2,
                ambiguous=False,
            )
            position.tp2_filled = True
            position.status = "tp2_closed"
            return
        return

    if bar.open >= stop:
        _add_exit(
            position,
            bar=bar,
            price=bar.open,
            fraction=position.remaining_fraction,
            reason="gap_" + active_stop_reason,
            gap=True,
            ambiguous=False,
        )
        position.status = "breakeven_stopped" if position.breakeven_active else "stopped"
        return
    touched_stop = bar.high >= stop
    touched_tp1 = not position.tp1_filled and bar.low <= position.tp1
    touched_tp2 = (
        position.tp1_filled
        and not position.tp2_filled
        and bar.low <= position.tp2
    )
    ambiguous = touched_stop and (touched_tp1 or touched_tp2)
    if touched_stop:
        _add_exit(
            position,
            bar=bar,
            price=stop,
            fraction=position.remaining_fraction,
            reason=active_stop_reason,
            gap=False,
            ambiguous=ambiguous,
        )
        position.status = "breakeven_stopped" if position.breakeven_active else "stopped"
        return
    if touched_tp1:
        _add_exit(
            position,
            bar=bar,
            price=position.tp1,
            fraction=position.tp1_fraction,
            reason="tp1",
            gap=bar.open <= position.tp1,
            ambiguous=False,
        )
        position.tp1_filled = True
        if not position.closed:
            position.stop_price = position.entry_price
            position.breakeven_active = True
            position.status = "tp1_filled"
        else:
            position.status = "tp1_closed"
        return
    if touched_tp2:
        _add_exit(
            position,
            bar=bar,
            price=position.tp2,
            fraction=position.remaining_fraction,
            reason="tp2",
            gap=bar.open <= position.tp2,
            ambiguous=False,
        )
        position.tp2_filled = True
        position.status = "tp2_closed"


def finish_trade(
    position: OpenPosition,
    *,
    mark_price: float | None,
    entry_period: str,
    exit_period: str | None,
    open_or_truncated_at_split_cutoff: bool,
    later_period_data_required: bool,
    decision_hash: str | None = None,
) -> TradeRecord:
    if not position.closed:
        position.status = "open_or_truncated"
    unrealized = (
        unrealized_r(
            position.side,
            position.entry_price,
            mark_price,
            position.risk_distance,
            position.remaining_fraction,
        )
        if not position.closed and mark_price is not None
        else None
    )
    return TradeRecord(
        trade_id=position.trade_id,
        decision_time=position.decision_time,
        decision_hash=decision_hash,
        entry_time=position.entry_time,
        entry_period=entry_period,
        exit_time=position.exits[-1].exit_time if position.exits else None,
        exit_period=exit_period,
        side=position.side.value,
        confidence=position.candidate.confidence.value,
        entry_price=position.entry_price,
        stop=position.candidate.plan.stop,
        tp1=position.tp1,
        tp2=position.tp2,
        risk_distance=position.risk_distance,
        tp1_fraction=position.tp1_fraction,
        tp2_fraction=position.tp2_fraction,
        status=position.status,
        closed=position.closed,
        realized_r=position.realized_r,
        remaining_fraction=position.remaining_fraction,
        unrealized_r=unrealized,
        exits=tuple(position.exits),
        ambiguous_bar_count=position.ambiguous_bar_count,
        gap_exit_count=position.gap_exit_count,
        bars_held=position.bars_held,
        open_or_truncated_at_split_cutoff=open_or_truncated_at_split_cutoff,
        later_period_data_required=later_period_data_required,
    )
