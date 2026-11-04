"""Persistent risk state and monetary position sizing.

``RiskTracker`` owns the state the strategy needs in ``ResearchRiskState``
(entries today, open positions, consecutive losses, cooldown, daily lockout).
It is persisted after every change so a restart cannot reset limits. If the
persisted state is missing or corrupt while positions exist, the tracker fails
closed until reconciled.

The trading-day boundary is 00:00 UTC (spec §11.3 research boundary). The
broker's own trading day is unverified; this is recorded on every snapshot.

``size_position`` implements spec §11.2 and is only callable with
:class:`~trantor.runtime.broker_verification.VerifiedEconomics`.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..strategy.xauusd_p import ResearchRiskState, XAUUSDPMTFParameters
from .broker_verification import VerifiedEconomics
from .store import RuntimeStore, StoreError, utc_now_iso

__all__ = [
    "DAY_BOUNDARY",
    "RiskStateError",
    "RiskTracker",
    "SizingResult",
    "size_position",
]

UTC = timezone.utc
DAY_BOUNDARY = "00:00 UTC (research boundary; broker trading day unverified)"


class RiskStateError(RuntimeError):
    """Risk state is unavailable or inconsistent; new entries must stop."""


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise RiskStateError("persisted risk timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


@dataclass
class RiskBook:
    mode: str
    day: str
    equity: float
    start_of_day_equity: float
    open_positions: int = 0
    entries_today: int = 0
    consecutive_losses: int = 0
    last_exit_at: str | None = None
    cooldown_until: str | None = None
    daily_locked_out: bool = False
    realized_r_today: float = 0.0
    realized_pnl_today: float = 0.0
    losses_today: int = 0
    open_risk_amount: float = 0.0
    lockout_reason: str | None = None

    def validate(self) -> None:
        for name in ("equity", "start_of_day_equity"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise RiskStateError(f"risk state {name} is invalid")
        for name in ("open_positions", "entries_today", "consecutive_losses", "losses_today"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RiskStateError(f"risk state {name} is invalid")
        if self.consecutive_losses > 0 and self.last_exit_at is None and self.cooldown_until is None:
            raise RiskStateError("loss recorded without cooldown timing")
        _dt(self.last_exit_at)
        _dt(self.cooldown_until)


@dataclass
class RiskTracker:
    store: RuntimeStore
    mode: str
    parameters: XAUUSDPMTFParameters = field(default_factory=XAUUSDPMTFParameters)
    starting_equity: float = 10_000.0
    equity_model: str = "paper"
    book: RiskBook | None = None

    def __post_init__(self) -> None:
        # "paper": P/L = R x risk amount moves paper equity (3% rule evaluable).
        # "neutral": equity is a constant unit, exactly like the Phase E
        # backtester; the daily-loss rule is then not evaluable.
        if self.equity_model not in ("paper", "neutral"):
            raise ValueError("equity_model must be 'paper' or 'neutral'")

    @property
    def key(self) -> str:
        return f"risk:{self.mode}"

    def load(self, now: datetime, *, open_positions: int) -> RiskBook:
        """Load persisted state; create it only when no position exists."""

        try:
            raw = self.store.get_state(self.key)
        except StoreError as exc:
            raise RiskStateError(f"risk state unreadable: {exc}") from exc
        if raw is None:
            if open_positions:
                raise RiskStateError(
                    "risk state is missing while positions are open; reconcile before trading"
                )
            day = now.astimezone(UTC).date().isoformat()
            self.book = RiskBook(
                mode=self.mode,
                day=day,
                equity=self.starting_equity,
                start_of_day_equity=self.starting_equity,
            )
            self._persist()
            return self.book
        try:
            book = RiskBook(**raw)
            book.validate()
        except (TypeError, ValueError) as exc:
            raise RiskStateError(f"risk state is corrupt: {exc}") from exc
        if book.open_positions != open_positions:
            raise RiskStateError(
                f"risk state has {book.open_positions} open positions but the position "
                f"store has {open_positions}"
            )
        self.book = book
        return book

    def _require(self) -> RiskBook:
        if self.book is None:
            raise RiskStateError("risk state not loaded")
        return self.book

    def _persist(self, conn: Any = None) -> None:
        book = self._require()
        self.store.set_state(self.key, asdict(book), conn=conn)
        row = {
            "day": book.day,
            "mode": self.mode,
            "start_equity": book.start_of_day_equity,
            "realized_r": book.realized_r_today,
            "realized_pnl": book.realized_pnl_today,
            "entries": book.entries_today,
            "losses": book.losses_today,
            "locked_out": 1 if book.daily_locked_out else 0,
            "updated_at": utc_now_iso(),
        }
        if conn is not None:
            self.store.upsert_daily(row, conn)
        else:
            with self.store.transaction() as tx:
                self.store.upsert_daily(row, tx)

    def roll_day(self, at: datetime, conn: Any = None) -> bool:
        book = self._require()
        day = at.astimezone(UTC).date().isoformat()
        if day <= book.day:
            return False
        book.day = day
        book.start_of_day_equity = book.equity
        book.entries_today = 0
        book.realized_r_today = 0.0
        book.realized_pnl_today = 0.0
        book.losses_today = 0
        book.daily_locked_out = False
        book.lockout_reason = None
        self._persist(conn)
        return True

    def daily_loss_fraction(self, unrealized_pnl: float = 0.0) -> float:
        book = self._require()
        if self.equity_model == "neutral":
            return 0.0
        current = book.equity + unrealized_pnl
        return max(0.0, (book.start_of_day_equity - current) / book.start_of_day_equity)

    def check_lockout(self, unrealized_pnl: float = 0.0, conn: Any = None) -> bool:
        book = self._require()
        if book.daily_locked_out:
            return True
        loss = self.daily_loss_fraction(unrealized_pnl)
        if loss >= self.parameters.daily_loss_limit - 1e-12:
            book.daily_locked_out = True
            book.lockout_reason = (
                f"daily loss {loss:.2%} reached the {self.parameters.daily_loss_limit:.0%} limit"
            )
            self._persist(conn)
            return True
        return False

    def snapshot(self, at: datetime, *, unrealized_pnl: float = 0.0) -> ResearchRiskState:
        book = self._require()
        self.roll_day(at)
        self.check_lockout(unrealized_pnl)
        if self.equity_model == "neutral":
            unrealized_pnl = 0.0
        return ResearchRiskState(
            equity=max(1e-9, book.equity + unrealized_pnl),
            start_of_day_equity=book.start_of_day_equity,
            open_positions=book.open_positions,
            entries_today=book.entries_today,
            consecutive_losses=book.consecutive_losses,
            last_exit_at=_dt(book.last_exit_at),
            cooldown_until=_dt(book.cooldown_until),
            daily_locked_out=book.daily_locked_out,
            processed_trigger_times=frozenset(),
        )

    def risk_amount(self, risk_fraction: float) -> float:
        return self._require().equity * risk_fraction

    def on_entry(self, at: datetime, risk_amount: float, conn: Any = None) -> None:
        book = self._require()
        self.roll_day(at, conn)
        book.entries_today += 1
        book.open_positions += 1
        book.open_risk_amount += risk_amount
        self._persist(conn)

    def on_partial(self, pnl: float, r_contribution: float, conn: Any = None) -> None:
        book = self._require()
        if self.equity_model == "paper":
            book.equity += pnl
        book.realized_pnl_today += pnl
        book.realized_r_today += r_contribution
        self._persist(conn)

    def on_full_exit(
        self,
        at: datetime,
        *,
        trade_r: float,
        final_pnl: float,
        final_r_contribution: float,
        released_risk: float,
        conn: Any = None,
    ) -> None:
        """Apply the last exit of a trade; cooldown follows the backtest rules."""

        book = self._require()
        if self.equity_model == "paper":
            book.equity += final_pnl
        book.realized_pnl_today += final_pnl
        book.realized_r_today += final_r_contribution
        book.open_positions = max(0, book.open_positions - 1)
        book.open_risk_amount = max(0.0, book.open_risk_amount - released_risk)
        book.last_exit_at = _iso(at)
        if trade_r < -1e-12:
            book.consecutive_losses += 1
            book.losses_today += 1
            bars = (
                self.parameters.cooldown_after_two_losses_m15
                if book.consecutive_losses >= 2
                else self.parameters.cooldown_after_loss_m15
            )
            calculated = at + timedelta(minutes=15) * bars
            current = _dt(book.cooldown_until)
            book.cooldown_until = _iso(calculated if current is None else max(current, calculated))
        else:
            book.consecutive_losses = 0
            current = _dt(book.cooldown_until)
            if current is not None and at >= current:
                book.cooldown_until = None
        self._persist(conn)
        self.check_lockout(0.0, conn)

    def status(self, now: datetime, unrealized_pnl: float = 0.0) -> dict[str, Any]:
        book = self._require()
        cooldown = _dt(book.cooldown_until)
        return {
            **asdict(book),
            "day_boundary": DAY_BOUNDARY,
            "daily_loss_fraction": self.daily_loss_fraction(unrealized_pnl),
            "daily_loss_limit": self.parameters.daily_loss_limit,
            "max_daily_entries": self.parameters.max_daily_entries,
            "max_concurrent_positions": self.parameters.max_concurrent_positions,
            "cooldown_active": cooldown is not None and cooldown > now,
            "equity_model": (
                "paper R-equity model: P/L = realized R x risk amount at entry"
                if self.equity_model == "paper"
                else "neutral unit equity (Phase E equivalent); daily-loss rule not evaluable"
            ),
        }


@dataclass(frozen=True, slots=True)
class SizingResult:
    approved: bool
    volume: float
    risk_amount_allowed: float
    risk_amount_actual: float
    reasons: tuple[str, ...]
    margin_required: float | None = None
    broker_loss_at_stop: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round_down_to_step(value: float, step: float) -> float:
    steps = math.floor(value / step + 1e-9)
    return round(steps * step, 10)


def size_position(
    *,
    equity: float,
    risk_fraction: float,
    risk_ceiling: float,
    entry: float,
    stop: float,
    economics: VerifiedEconomics | None,
    commission_per_lot_round_trip: float | None = None,
    margin_per_lot: float | None = None,
    free_margin: float | None = None,
    broker_loss_at_stop: Any = None,
) -> SizingResult:
    """Spec §11.2 sizing. Never rounds up; rejects rather than exceeding risk."""

    def reject(*reasons: str, allowed: float = 0.0) -> SizingResult:
        return SizingResult(False, 0.0, allowed, 0.0, tuple(reasons))

    if economics is None:
        return reject("contract economics are not verified; monetary sizing is blocked")
    for name, value in (("equity", equity), ("entry", entry), ("stop", stop)):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return reject(f"{name} is invalid")
    if not 0 < risk_fraction <= risk_ceiling + 1e-12:
        return reject("risk fraction exceeds the confidence ceiling")
    distance = abs(entry - stop)
    if distance <= 0:
        return reject("stop distance must be positive")
    allowed = equity * risk_fraction
    per_lot_loss = distance * economics.value_per_price_unit_per_lot
    if commission_per_lot_round_trip is not None:
        if commission_per_lot_round_trip < 0 or not math.isfinite(commission_per_lot_round_trip):
            return reject("commission is invalid", allowed=allowed)
        per_lot_loss += commission_per_lot_round_trip
    else:
        return reject("commission is not verified; costs cannot be represented", allowed=allowed)
    raw = allowed / per_lot_loss
    volume = min(_round_down_to_step(raw, economics.volume_step), economics.volume_max)
    volume = _round_down_to_step(volume, economics.volume_step)
    # Keep an even number of steps so TP1 and TP2 each close exactly half.
    # Dropping one step only ever reduces risk.
    steps = round(volume / economics.volume_step)
    if steps % 2:
        volume = round((steps - 1) * economics.volume_step, 10)
    reasons: list[str] = []
    if volume < economics.volume_min - 1e-12:
        reasons.append("minimum volume would exceed the permitted risk")
    if volume < 2 * economics.volume_step - 1e-12:
        reasons.append("volume is below two steps; 50/50 partial exit impossible")
    elif volume / 2 < economics.volume_min - 1e-12:
        reasons.append("half of the volume is below the broker minimum; 50/50 partial exit impossible")
    actual = volume * per_lot_loss
    if actual > allowed + 1e-9:
        reasons.append("rounded volume exceeds the permitted risk")
    margin = None if margin_per_lot is None else margin_per_lot * volume
    if margin_per_lot is None:
        reasons.append("margin requirement unavailable")
    elif free_margin is not None and margin > free_margin:
        reasons.append("insufficient free margin")
    broker_loss = None
    if broker_loss_at_stop is not None and not reasons:
        try:
            broker_loss = abs(float(broker_loss_at_stop(volume)))
        except Exception as exc:
            reasons.append(f"broker loss calculation failed: {exc}")
        else:
            independent = volume * distance * economics.value_per_price_unit_per_lot
            if not math.isclose(broker_loss, independent, rel_tol=0.01, abs_tol=0.01):
                reasons.append(
                    f"broker loss at stop {broker_loss:.2f} differs from independent {independent:.2f}"
                )
    if reasons:
        return SizingResult(False, 0.0, allowed, 0.0, tuple(reasons), margin, broker_loss)
    return SizingResult(True, volume, allowed, actual, (), margin, broker_loss)
