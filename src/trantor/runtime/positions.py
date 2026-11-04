"""Shadow/paper position management.

Positions are managed on completed M15 bars with the *same* conservative fill
function the Phase E backtester uses
(:func:`trantor.research.backtest.fills.process_position_bar`): stop-first on
ambiguous bars, gap-through-stop at the open, targets at the target price,
TP2 only after TP1, breakeven after TP1. Paper results are therefore a forward
test of the research fill model on live bars, not a tick-level simulation.

Every state change is persisted with optimistic versioning before the next
bar is processed, so a restart resumes from ``processed_through`` without
double-counting an exit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import MarketObservation, Side
from ..research.backtest.fills import ExitEvent, entry_rejection_reason, process_position_bar
from ..strategy.xauusd_p import XAUUSDPSignalCandidate
from .risk import RiskTracker
from .store import RuntimeStore, StoreError, utc_now_iso

__all__ = ["ManagedPosition", "PositionManager", "position_id_for"]

UTC = timezone.utc
M15 = timedelta(minutes=15)


def position_id_for(decision_id: str) -> str:
    return "pos-" + hashlib.sha256(decision_id.encode()).hexdigest()[:20]


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


@dataclass
class ManagedPosition:
    """Attribute names mirror ``OpenPosition`` so the backtest fill rules apply."""

    position_id: str
    decision_id: str
    client_order_id: str
    mode: str
    side: Side
    entry_price: float
    stop_price: float
    initial_stop: float
    tp1: float
    tp2: float
    risk_distance: float
    tp1_fraction: float
    tp2_fraction: float
    decision_time: datetime
    entry_time: datetime
    confidence: str
    score: int
    risk_fraction: float
    risk_amount: float
    equity_at_entry: float
    quantity_label: str
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
    processed_through: datetime | None = None
    gap_warnings: list[str] = field(default_factory=list)
    version: int = 1

    @property
    def trade_id(self) -> str:
        return self.position_id

    def to_state(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "decision_id": self.decision_id,
            "client_order_id": self.client_order_id,
            "mode": self.mode,
            "side": self.side.value,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "initial_stop": self.initial_stop,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "risk_distance": self.risk_distance,
            "tp1_fraction": self.tp1_fraction,
            "tp2_fraction": self.tp2_fraction,
            "decision_time": self.decision_time.isoformat(),
            "entry_time": self.entry_time.isoformat(),
            "confidence": self.confidence,
            "score": self.score,
            "risk_fraction": self.risk_fraction,
            "risk_amount": self.risk_amount,
            "equity_at_entry": self.equity_at_entry,
            "quantity_label": self.quantity_label,
            "remaining_fraction": self.remaining_fraction,
            "tp1_filled": self.tp1_filled,
            "tp2_filled": self.tp2_filled,
            "breakeven_active": self.breakeven_active,
            "realized_r": self.realized_r,
            "realized_pnl": self.realized_r * self.risk_amount,
            "exits": [exit_.as_dict() for exit_ in self.exits],
            "ambiguous_bar_count": self.ambiguous_bar_count,
            "gap_exit_count": self.gap_exit_count,
            "bars_held": self.bars_held,
            "last_bar_time": None if self.last_bar_time is None else self.last_bar_time.isoformat(),
            "last_close": self.last_close,
            "closed": self.closed,
            "status": self.status,
            "processed_through": None if self.processed_through is None else self.processed_through.isoformat(),
            "gap_warnings": list(self.gap_warnings),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any], version: int) -> "ManagedPosition":
        exits = [
            ExitEvent(
                exit_time=_dt(item["exit_time"]),  # type: ignore[arg-type]
                bar_time=_dt(item["bar_time"]),  # type: ignore[arg-type]
                price=float(item["price"]),
                fraction=float(item["fraction"]),
                r_multiple=float(item["r_multiple"]),
                contribution=float(item["contribution"]),
                reason=str(item["reason"]),
                gap=bool(item["gap"]),
                ambiguous=bool(item["ambiguous"]),
            )
            for item in state.get("exits", [])
        ]
        return cls(
            position_id=state["position_id"],
            decision_id=state["decision_id"],
            client_order_id=state["client_order_id"],
            mode=state["mode"],
            side=Side(state["side"]),
            entry_price=float(state["entry_price"]),
            stop_price=float(state["stop_price"]),
            initial_stop=float(state["initial_stop"]),
            tp1=float(state["tp1"]),
            tp2=float(state["tp2"]),
            risk_distance=float(state["risk_distance"]),
            tp1_fraction=float(state["tp1_fraction"]),
            tp2_fraction=float(state["tp2_fraction"]),
            decision_time=_dt(state["decision_time"]),  # type: ignore[arg-type]
            entry_time=_dt(state["entry_time"]),  # type: ignore[arg-type]
            confidence=state["confidence"],
            score=int(state["score"]),
            risk_fraction=float(state["risk_fraction"]),
            risk_amount=float(state["risk_amount"]),
            equity_at_entry=float(state["equity_at_entry"]),
            quantity_label=state["quantity_label"],
            remaining_fraction=float(state["remaining_fraction"]),
            tp1_filled=bool(state["tp1_filled"]),
            tp2_filled=bool(state["tp2_filled"]),
            breakeven_active=bool(state["breakeven_active"]),
            realized_r=float(state["realized_r"]),
            exits=exits,
            ambiguous_bar_count=int(state["ambiguous_bar_count"]),
            gap_exit_count=int(state["gap_exit_count"]),
            bars_held=int(state["bars_held"]),
            last_bar_time=_dt(state.get("last_bar_time")),
            last_close=state.get("last_close"),
            closed=bool(state["closed"]),
            status=state["status"],
            processed_through=_dt(state.get("processed_through")),
            gap_warnings=list(state.get("gap_warnings", [])),
            version=version,
        )

    def unrealized(self, bid: float | None, ask: float | None) -> tuple[float | None, float | None]:
        """Unrealized (R, P/L) at the executable side of the live quote."""

        if self.closed or self.remaining_fraction <= 0:
            return 0.0, 0.0
        mark = bid if self.side is Side.BUY else ask
        if mark is None:
            return None, None
        unit = (mark - self.entry_price) if self.side is Side.BUY else (self.entry_price - mark)
        r_value = unit / self.risk_distance * self.remaining_fraction
        return r_value, r_value * self.risk_amount


@dataclass
class PositionEvent:
    position: ManagedPosition
    exit: ExitEvent
    closed: bool


class PositionManager:
    def __init__(self, store: RuntimeStore, tracker: RiskTracker, mode: str) -> None:
        self.store = store
        self.tracker = tracker
        self.mode = mode
        self.open: dict[str, ManagedPosition] = {}

    def load(self) -> list[ManagedPosition]:
        self.open = {}
        for row in self.store.positions(open_only=True, mode=self.mode):
            position = ManagedPosition.from_state(row["state"], row["version"])
            self.open[position.position_id] = position
        return list(self.open.values())

    def open_count(self) -> int:
        return len(self.open)

    def create(
        self,
        *,
        candidate: XAUUSDPSignalCandidate,
        entry_bar: MarketObservation,
        decision_id: str,
        client_order_id: str,
        quantity_label: str,
        conn: Any,
    ) -> ManagedPosition:
        reason = entry_rejection_reason(candidate, entry_bar)
        if reason is not None:
            raise ValueError(reason)
        risk_fraction = candidate.risk.risk_fraction
        risk_amount = self.tracker.risk_amount(risk_fraction)
        position = ManagedPosition(
            position_id=position_id_for(decision_id),
            decision_id=decision_id,
            client_order_id=client_order_id,
            mode=self.mode,
            side=candidate.side,
            entry_price=entry_bar.open,
            stop_price=candidate.plan.stop,
            initial_stop=candidate.plan.stop,
            tp1=candidate.plan.tp1,
            tp2=candidate.plan.tp2,
            risk_distance=candidate.plan.risk_distance,
            tp1_fraction=candidate.plan.tp1_fraction,
            tp2_fraction=candidate.plan.tp2_fraction,
            decision_time=candidate.decision_time,
            entry_time=candidate.entry_time,
            confidence=candidate.confidence.value,
            score=candidate.score.total,
            risk_fraction=risk_fraction,
            risk_amount=risk_amount,
            equity_at_entry=self.tracker.book.equity if self.tracker.book else 0.0,
            quantity_label=quantity_label,
            last_bar_time=entry_bar.observed_at,
            last_close=entry_bar.open,
        )
        self.store.insert_position(
            {
                "position_id": position.position_id,
                "decision_id": decision_id,
                "client_order_id": client_order_id,
                "mode": self.mode,
                "side": position.side.value,
                "status": position.status,
                "closed": 0,
                "entry_time": position.entry_time.isoformat(),
                "closed_at": None,
                "state_json": _json(position.to_state()),
                "updated_at": utc_now_iso(),
            },
            conn,
        )
        self.store.append_position_event(
            position.position_id,
            "opened",
            {"entry_price": position.entry_price, "stop": position.stop_price, "tp1": position.tp1, "tp2": position.tp2},
            conn,
        )
        self.tracker.on_entry(position.entry_time, risk_amount, conn)
        self.open[position.position_id] = position
        return position

    def on_completed_bars(self, bars: tuple[MarketObservation, ...]) -> list[PositionEvent]:
        """Process every unprocessed completed bar for each open position."""

        events: list[PositionEvent] = []
        for position in sorted(self.open.values(), key=lambda item: item.entry_time):
            pending = [
                bar
                for bar in bars
                if bar.observed_at >= position.entry_time
                and (position.processed_through is None or bar.observed_at > position.processed_through)
            ]
            previous_time = position.processed_through
            for bar in pending:
                if previous_time is not None and bar.observed_at - previous_time > M15:
                    position.gap_warnings.append(
                        f"missing M15 bars between {previous_time.isoformat()} and {bar.observed_at.isoformat()}"
                    )
                before = len(position.exits)
                process_position_bar(position, bar)  # type: ignore[arg-type]
                position.processed_through = bar.observed_at
                previous_time = bar.observed_at
                new_exits = position.exits[before:]
                self._persist(position, new_exits)
                events.extend(PositionEvent(position, exit_, position.closed) for exit_ in new_exits)
                if position.closed:
                    break
            if position.closed:
                self.open.pop(position.position_id, None)
        return events

    def _persist(self, position: ManagedPosition, new_exits: list[ExitEvent]) -> None:
        try:
            with self.store.transaction() as conn:
                position.version = self.store.update_position(
                    position.position_id,
                    status=position.status,
                    closed=position.closed,
                    closed_at=(
                        position.exits[-1].exit_time.isoformat()
                        if position.closed and position.exits
                        else None
                    ),
                    state=position.to_state(),
                    expected_version=position.version,
                    conn=conn,
                )
                for index, exit_ in enumerate(new_exits):
                    pnl = exit_.contribution * position.risk_amount
                    self.store.append_position_event(
                        position.position_id,
                        f"exit_{exit_.reason}",
                        {**exit_.as_dict(), "pnl": pnl},
                        conn,
                    )
                    last = position.closed and index == len(new_exits) - 1
                    if last:
                        self.tracker.on_full_exit(
                            exit_.exit_time,
                            trade_r=position.realized_r,
                            final_pnl=pnl,
                            final_r_contribution=exit_.contribution,
                            released_risk=position.risk_amount,
                            conn=conn,
                        )
                    else:
                        self.tracker.on_partial(pnl, exit_.contribution, conn)
        except StoreError:
            raise

    def unrealized_total(self, bid: float | None, ask: float | None) -> tuple[float, float, bool]:
        total_r = 0.0
        total_pnl = 0.0
        complete = True
        for position in self.open.values():
            r_value, pnl = position.unrealized(bid, ask)
            if r_value is None or pnl is None:
                complete = False
                continue
            total_r += r_value
            total_pnl += pnl
        return total_r, total_pnl, complete


def _json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
