"""The Trantor decision pipeline.

MARKET DATA -> DATA VALIDATION -> D1/H4/M15 ANALYSIS -> CONFLUENCE SCORING
-> NEWS/SESSION -> RISK -> ENTRY AUTHORIZATION -> PAPER/SHADOW ENTRY
-> POSITION MONITORING -> EXIT MANAGEMENT -> TRADE RECORD

The engine contains no strategy logic. Steps D1 through risk are performed by
:meth:`XAUUSDPMultiTimeframeStrategy.evaluate`; the engine supplies verified
inputs, persists an auditable envelope around the unchanged canonical research
record, and applies the runtime execution gate.

Profiles:

``shadow``  (execution_mode ``research``) — hypothetical positions only; no
            order objects are created.
``paper``   (execution_mode ``paper``) — orders go through
            :class:`~trantor.execution.gate.ExecutionGate` to the in-process
            :class:`~trantor.execution.paper.PaperBroker`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import ExecutionMode
from ..execution.gate import ExecutionGate, client_order_id_for
from ..execution.paper import PaperBroker
from ..instruments import XAUUSD_P_PROFILE
from ..models import MarketObservation, OrderRequest, OrderStatus, OrderType, TimeInForce
from ..safety import ExecutionBlockedError
from ..strategy.research_record import canonical_json, serialize_strategy_decision
from ..strategy.xauusd_p import StrategyDecision, XAUUSDPMultiTimeframeStrategy
from .calendar import NewsAssessment
from .positions import ManagedPosition, PositionManager
from .risk import RiskTracker
from .store import RuntimeStore, utc_now_iso

__all__ = [
    "CycleInputs",
    "DecisionEngine",
    "EngineConfig",
    "EngineResult",
    "decision_id_for",
    "reconcile",
]

UTC = timezone.utc


def decision_id_for(variant_id: str, profile: str, decision_time: datetime) -> str:
    key = f"{variant_id}|{profile}|{decision_time.astimezone(UTC).isoformat()}"
    return "dec-" + hashlib.sha256(key.encode()).hexdigest()[:28]


def parameters_fingerprint(strategy: XAUUSDPMultiTimeframeStrategy) -> str:
    params = {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in asdict(strategy.parameters).items()
    }
    payload = json.dumps(
        {"identity": asdict(strategy.identity), "parameters": params}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EngineConfig:
    execution_mode: ExecutionMode
    config_fingerprint: str
    entry_window: timedelta = timedelta(seconds=120)

    @property
    def profile(self) -> str:
        if self.execution_mode is ExecutionMode.PAPER:
            return "paper"
        if self.execution_mode is ExecutionMode.RESEARCH:
            return "shadow"
        raise ValueError(f"the decision engine does not run in {self.execution_mode.value!r} mode")

    @property
    def labels(self) -> list[str]:
        base = ["GROSS - EXECUTION COSTS EXCLUDED", "RESEARCH-GRADE TIMESTAMPS UNLESS CLOCK VERIFIED"]
        if self.profile == "paper":
            return ["PAPER SIMULATION - NO BROKER ORDERS", *base]
        return ["SHADOW MODE - HYPOTHETICAL ONLY - NO ORDERS", *base]


@dataclass(frozen=True, slots=True)
class CycleInputs:
    decision_time: datetime
    d1: tuple[MarketObservation, ...]
    h4: tuple[MarketObservation, ...]
    m15: tuple[MarketObservation, ...]
    next_bar: MarketObservation | None
    evaluated_at: datetime
    data_identity: str
    news: NewsAssessment
    blockers: tuple[str, ...] = ()
    decision_time_raw: datetime | None = None
    time_basis: dict[str, Any] = field(default_factory=dict)
    broker_verification: str = "not_run"
    mark_bid: float | None = None
    mark_ask: float | None = None


@dataclass
class EngineResult:
    decision_id: str
    duplicate: bool
    decision: StrategyDecision | None = None
    outcome: str = ""
    record_hash: str | None = None
    position: ManagedPosition | None = None
    blocked_reasons: list[str] = field(default_factory=list)
    order_status: str | None = None


def reconcile(
    store: RuntimeStore,
    mode: str,
    tracker: RiskTracker,
    manager: PositionManager,
) -> tuple[bool, dict[str, Any]]:
    """Compare orders, positions and risk state. Never resubmits anything."""

    issues: list[str] = []
    abandoned: list[str] = []
    open_positions = {p.client_order_id: p for p in manager.open.values()}
    if mode == "paper":
        orders = [order for order in store.orders(limit=1000) if order["mode"] == mode]
        entry_orders = {o["client_order_id"]: o for o in orders if o["client_order_id"].startswith("trn-ent-")}
        for cid, order in entry_orders.items():
            if order["status"] in (OrderStatus.PENDING_SUBMISSION.value, OrderStatus.SUBMITTED.value, OrderStatus.ACKNOWLEDGED.value):
                # Outcome unknown after a restart. Paper never retries: abandon.
                with store.transaction() as conn:
                    store.update_order(
                        cid,
                        status=OrderStatus.CANCELED.value,
                        filled_quantity=0.0,
                        average_fill_price=None,
                        broker_order_id=None,
                        conn=conn,
                    )
                    store.append_order_event(cid, "abandoned_on_recovery", OrderStatus.CANCELED.value, {"reason": "outcome unknown after restart; not retried"}, conn)
                abandoned.append(cid)
        all_positions = {p["client_order_id"] for p in store.positions(mode=mode, limit=10000)}
        for cid, order in entry_orders.items():
            if order["status"] == OrderStatus.FILLED.value and cid not in all_positions:
                issues.append(f"filled entry order {cid} has no position")
        for cid in open_positions:
            order = entry_orders.get(cid)
            if order is None or order["status"] != OrderStatus.FILLED.value:
                issues.append(f"open position for {cid} has no filled entry order")
    book = tracker.book
    if book is None:
        issues.append("risk state not loaded")
    elif book.open_positions != len(open_positions):
        issues.append(
            f"risk state counts {book.open_positions} open positions; position store has {len(open_positions)}"
        )
    detail = {"issues": issues, "abandoned_orders": abandoned, "open_positions": len(open_positions)}
    store.record_reconciliation(mode, not issues, detail)
    return not issues, detail


class DecisionEngine:
    def __init__(
        self,
        *,
        store: RuntimeStore,
        strategy: XAUUSDPMultiTimeframeStrategy,
        config: EngineConfig,
        tracker: RiskTracker,
        positions: PositionManager,
        gate: ExecutionGate,
        broker: PaperBroker | None = None,
        notifier: Any = None,
    ) -> None:
        if config.profile == "paper" and broker is None:
            raise ValueError("paper profile requires the paper broker")
        self.store = store
        self.strategy = strategy
        self.config = config
        self.tracker = tracker
        self.positions = positions
        self.gate = gate
        self.broker = broker
        self.notifier = notifier
        self.reconciled = False
        self.parameters_fingerprint = parameters_fingerprint(strategy)

    # -- notifications ---------------------------------------------------------

    def _notify(self, kind: str, key: str, text: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.notify(kind, key, text)
        except Exception:
            pass

    # -- main step ---------------------------------------------------------------

    def process(self, inputs: CycleInputs) -> EngineResult:
        identity = self.strategy.identity
        profile = self.config.profile
        decision_id = decision_id_for(identity.variant_id, profile, inputs.decision_time)
        if self.store.decision_exists(decision_id):
            return EngineResult(decision_id=decision_id, duplicate=True, outcome="duplicate_skipped")

        mark_bid = inputs.mark_bid if inputs.mark_bid is not None else (inputs.m15[-1].close if inputs.m15 else None)
        mark_ask = inputs.mark_ask if inputs.mark_ask is not None else mark_bid
        _, unrealized_pnl, _ = self.positions.unrealized_total(mark_bid, mark_ask)
        risk_state = self.tracker.snapshot(inputs.decision_time, unrealized_pnl=unrealized_pnl)
        news_unaware = not inputs.news.context.verified
        decision = self.strategy.evaluate(
            inputs.d1,
            inputs.h4,
            inputs.m15,
            decision_time=inputs.decision_time,
            next_m15_bar=inputs.next_bar,
            risk_state=risk_state,
            news_context=inputs.news.context if inputs.news.context.verified else None,
            research_mode=True,
            news_unaware=news_unaware,
            session_calendar_verified=False,
        )
        canonical = serialize_strategy_decision(decision, identity)
        record_text = canonical_json(canonical)
        record_hash = hashlib.sha256(record_text.encode()).hexdigest()

        candidate = decision.candidate
        blocked: list[str] = []
        outcome = "no_trade"
        client_order_id: str | None = None
        lateness = None
        if candidate is not None:
            lateness = (inputs.evaluated_at - candidate.entry_time).total_seconds()
            blockers = list(inputs.blockers)
            if lateness > self.config.entry_window.total_seconds():
                blockers.append(
                    f"entry window missed: evaluated {lateness:.0f}s after the entry bar opened"
                )
            if not self.reconciled:
                blockers.append("startup reconciliation has not completed")
            try:
                if profile == "paper":
                    client_order_id = self.gate.authorize_entry(
                        decision_id=decision_id,
                        risk_eligible=candidate.risk.eligible,
                        risk_fraction=candidate.risk.risk_fraction,
                        open_positions=self.positions.open_count(),
                        entries_today=risk_state.entries_today,
                        daily_loss_fraction=self.tracker.daily_loss_fraction(unrealized_pnl),
                        blockers=blockers,
                        reconciliation_consistent=self.reconciled,
                    )
                else:
                    if self.gate.kill_switch.engaged():
                        raise ExecutionBlockedError("emergency stop is engaged; new entries are blocked")
                    if blockers:
                        raise ExecutionBlockedError("runtime blockers: " + "; ".join(blockers))
                    client_order_id = "shadow-" + decision_id[4:24]
                outcome = "entry_authorized"
            except ExecutionBlockedError as exc:
                blocked.append(str(exc))
                outcome = "entry_blocked"

        envelope = self._envelope(inputs, decision, decision_id, record_hash, outcome, blocked, lateness)
        row = {
            "decision_id": decision_id,
            "variant_id": identity.variant_id,
            "profile": profile,
            "decision_time": inputs.decision_time.astimezone(UTC).isoformat(),
            "decision_time_raw": None if inputs.decision_time_raw is None else inputs.decision_time_raw.isoformat(),
            "evaluated_at": inputs.evaluated_at.astimezone(UTC).isoformat(),
            "execution_mode": self.config.execution_mode.value,
            "status": decision.status.value,
            "side": None if candidate is None else candidate.side.value,
            "score_total": decision.score.total,
            "confidence": decision.score.confidence.value,
            "rejection_codes": json.dumps([item.code.value for item in decision.rejections]),
            "record_hash": record_hash,
            "record_json": record_text,
            "envelope_json": json.dumps(envelope, sort_keys=True, default=str, allow_nan=False),
            "outcome": outcome,
        }
        request: OrderRequest | None = None
        with self.store.transaction() as conn:
            if not self.store.insert_decision(row, conn):
                return EngineResult(decision_id=decision_id, duplicate=True, outcome="duplicate_skipped")
            if outcome == "entry_authorized" and profile == "paper":
                assert candidate is not None and client_order_id is not None
                request = OrderRequest(
                    client_order_id=client_order_id,
                    signal_id=candidate.to_trade_signal().signal_id,
                    symbol=candidate.instrument.symbol,
                    side=candidate.side,
                    quantity=1.0,
                    order_type=OrderType.MARKET,
                    time_in_force=TimeInForce.IOC,
                    created_at=inputs.evaluated_at,
                )
                self.store.insert_order(
                    {
                        "client_order_id": client_order_id,
                        "decision_id": decision_id,
                        "mode": profile,
                        "side": candidate.side.value,
                        "quantity": 1.0,
                        "order_type": OrderType.MARKET.value,
                        "status": OrderStatus.PENDING_SUBMISSION.value,
                        "broker_order_id": None,
                        "filled_quantity": 0.0,
                        "average_fill_price": None,
                        "created_at": utc_now_iso(),
                        "updated_at": utc_now_iso(),
                        "request_json": json.dumps(
                            {
                                "signal_id": request.signal_id,
                                "entry_reference": candidate.entry_reference,
                                "stop": candidate.plan.stop,
                                "tp1": candidate.plan.tp1,
                                "tp2": candidate.plan.tp2,
                                "quantity_unit": "1 paper position unit (broker volume not modelled)",
                            }
                        ),
                    },
                    conn,
                )
                self.store.append_order_event(client_order_id, "created", OrderStatus.PENDING_SUBMISSION.value, {"decision_id": decision_id}, conn)

        result = EngineResult(
            decision_id=decision_id,
            duplicate=False,
            decision=decision,
            outcome=outcome,
            record_hash=record_hash,
            blocked_reasons=blocked,
        )
        if candidate is not None:
            self._notify(
                "setup",
                decision_id,
                (
                    f"{candidate.confidence.value.upper()} {candidate.side.value.upper()} setup "
                    f"score {decision.score.total}/8 at {inputs.decision_time:%Y-%m-%d %H:%M} UTC; "
                    f"entry {candidate.entry_reference} stop {candidate.plan.stop} "
                    f"TP1 {candidate.plan.tp1} TP2 {candidate.plan.tp2}; outcome {outcome}"
                    + (f" ({'; '.join(blocked)})" if blocked else "")
                ),
            )
        if outcome != "entry_authorized":
            return result

        assert candidate is not None and client_order_id is not None and inputs.next_bar is not None
        if profile == "paper":
            assert self.broker is not None and request is not None
            self.broker.set_reference_price(client_order_id, candidate.entry_reference)
            state = self.broker.submit_order(request)
            result.order_status = state.status.value
            with self.store.transaction() as conn:
                self.store.update_order(
                    client_order_id,
                    status=state.status.value,
                    filled_quantity=state.filled_quantity,
                    average_fill_price=state.average_fill_price,
                    broker_order_id=state.broker_order_id,
                    conn=conn,
                )
                self.store.append_order_event(
                    client_order_id,
                    "broker_response",
                    state.status.value,
                    {"filled_quantity": state.filled_quantity, "average_fill_price": state.average_fill_price},
                    conn,
                )
                if state.status is OrderStatus.FILLED:
                    result.position = self.positions.create(
                        candidate=candidate,
                        entry_bar=inputs.next_bar,
                        decision_id=decision_id,
                        client_order_id=client_order_id,
                        quantity_label="1 paper unit; risk amount from paper equity model",
                        conn=conn,
                    )
            if result.position is not None:
                self._notify(
                    "paper_open",
                    result.position.position_id,
                    f"PAPER {candidate.side.value.upper()} opened at {result.position.entry_price} "
                    f"stop {result.position.stop_price} TP1 {result.position.tp1} TP2 {result.position.tp2} "
                    f"risk {result.position.risk_fraction:.2%} of paper equity",
                )
        else:
            with self.store.transaction() as conn:
                result.position = self.positions.create(
                    candidate=candidate,
                    entry_bar=inputs.next_bar,
                    decision_id=decision_id,
                    client_order_id=client_order_id,
                    quantity_label="hypothetical; no order",
                    conn=conn,
                )
        return result

    def _envelope(
        self,
        inputs: CycleInputs,
        decision: StrategyDecision,
        decision_id: str,
        record_hash: str,
        outcome: str,
        blocked: list[str],
        lateness: float | None,
    ) -> dict[str, Any]:
        identity = self.strategy.identity
        candidate = decision.candidate
        return {
            "decision_id": decision_id,
            "record_hash": record_hash,
            "record_contract": "trantor.research_decision v1.0 (unchanged)",
            "profile": self.config.profile,
            "execution_mode": self.config.execution_mode.value,
            "labels": self.config.labels,
            "strategy": {
                "variant_id": identity.variant_id,
                "strategy_name": identity.strategy_name,
                "strategy_version": identity.strategy_version,
                "parameters_fingerprint": self.parameters_fingerprint,
            },
            "config_fingerprint": self.config.config_fingerprint,
            "instrument": {
                "symbol": XAUUSD_P_PROFILE.instrument.symbol,
                "profile_id": XAUUSD_P_PROFILE.profile_id,
                "broker_verification": inputs.broker_verification,
            },
            "data_identity": inputs.data_identity,
            "data": {
                "d1_bars": len(inputs.d1),
                "h4_bars": len(inputs.h4),
                "m15_bars": len(inputs.m15),
                "last_d1_open": inputs.d1[-1].observed_at.isoformat() if inputs.d1 else None,
                "last_h4_open": inputs.h4[-1].observed_at.isoformat() if inputs.h4 else None,
                "trigger_m15_open": inputs.m15[-1].observed_at.isoformat() if inputs.m15 else None,
                "next_bar_open_time": None if inputs.next_bar is None else inputs.next_bar.observed_at.isoformat(),
                "next_bar_open": None if inputs.next_bar is None else inputs.next_bar.open,
            },
            "time": {
                "decision_time_normalized_utc": inputs.decision_time.isoformat(),
                "decision_time_raw_broker": None if inputs.decision_time_raw is None else inputs.decision_time_raw.isoformat(),
                "evaluated_at_host_utc": inputs.evaluated_at.isoformat(),
                "time_basis": {
                    key: inputs.time_basis.get(key)
                    for key in ("offset_seconds", "verified", "provenance")
                },
                "entry_lateness_seconds": lateness,
            },
            "news": inputs.news.as_dict(),
            "session": {
                "iana_overlap": decision.session.in_overlap,
                "broker_calendar_verified": False,
                "reason": decision.session.reason,
            },
            "score": {
                "total": decision.score.total,
                "maximum": 8,
                "confidence": decision.score.confidence.value,
                "factors": [asdict(item) for item in decision.score.factors],
            },
            "rejections": [{"code": item.code.value, "message": item.message} for item in decision.rejections],
            "proposal": None if candidate is None else {
                "side": candidate.side.value,
                "entry_reference": candidate.entry_reference,
                "entry_time": candidate.entry_time.isoformat(),
                "stop": candidate.plan.stop,
                "tp1": candidate.plan.tp1,
                "tp2": candidate.plan.tp2,
                "risk_distance": candidate.plan.risk_distance,
                "risk_fraction_ceiling": candidate.risk.risk_fraction,
                "monetary_sizing": "blocked_unverified_contract_economics",
            },
            "risk": None if decision.risk is None else {
                "eligible": decision.risk.eligible,
                "risk_fraction": decision.risk.risk_fraction,
                "daily_loss_fraction": decision.risk.daily_loss_fraction,
                "reasons": list(decision.risk.reasons),
            },
            "runtime_blockers": list(inputs.blockers),
            "blocked_reasons": blocked,
            "outcome": outcome,
        }
