"""XAUUSD-P execution gate and emergency stop.

Every order for the active instrument passes through :class:`ExecutionGate`.
The gate re-checks everything itself; it never trusts a caller's (or a
dashboard's) claim that an order is allowed:

- execution mode — ``paper`` only. ``demo`` additionally needs a verified demo
  account, verified contract economics and an owner authorization file, and is
  still refused because no demo adapter has been validated. ``live`` is always
  refused.
- emergency stop — engaged means no new entry, in any mode;
- reconciliation — any inconsistency between orders, positions and risk state
  blocks new entries;
- feed/runtime blockers supplied by the runtime;
- strategy risk eligibility, and the stricter of strategy and settings ceilings;
- idempotency — the ``client_order_id`` is reserved in the database once.

The legacy GC/MGC :class:`trantor.safety.OrderAuthorizer` is unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import ExecutionMode, Settings
from ..safety import (
    HARD_MAX_CONCURRENT_POSITIONS,
    HARD_MAX_DAILY_LOSS_FRACTION,
    HARD_MAX_RISK_PER_TRADE_FRACTION,
    ExecutionBlockedError,
)

__all__ = [
    "EffectiveLimits",
    "ExecutionGate",
    "KillSwitch",
    "client_order_id_for",
]

UTC = timezone.utc


def client_order_id_for(decision_id: str, purpose: str = "entry") -> str:
    digest = hashlib.sha256(f"{decision_id}|{purpose}".encode()).hexdigest()[:20]
    return f"trn-{purpose[:3]}-{digest}"


@dataclass(frozen=True, slots=True)
class EffectiveLimits:
    max_risk_fraction: float
    max_concurrent_positions: int
    max_daily_loss_fraction: float
    max_daily_entries: int

    @classmethod
    def combine(cls, settings: Settings, strategy_params: Any) -> "EffectiveLimits":
        return cls(
            max_risk_fraction=min(
                settings.max_risk_per_trade_fraction,
                strategy_params.risk_high_confidence,
                HARD_MAX_RISK_PER_TRADE_FRACTION,
            ),
            max_concurrent_positions=min(
                settings.max_concurrent_positions,
                strategy_params.max_concurrent_positions,
                HARD_MAX_CONCURRENT_POSITIONS,
            ),
            max_daily_loss_fraction=min(
                settings.max_daily_loss_fraction,
                strategy_params.daily_loss_limit,
                HARD_MAX_DAILY_LOSS_FRACTION,
            ),
            max_daily_entries=strategy_params.max_daily_entries,
        )


class KillSwitch:
    """File-backed emergency stop that survives restarts.

    Engaging it blocks new entries and strategy-triggered orders. It never
    closes positions and never stops position monitoring or risk enforcement.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def engaged(self) -> bool:
        return self.path.exists()

    def status(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"engaged": False}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"reason": "unreadable stop file (treated as engaged)"}
        return {"engaged": True, **(data if isinstance(data, dict) else {})}

    def engage(self, reason: str, actor: str = "operator") -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "reason": reason.strip() or "unspecified",
            "actor": actor,
            "engaged_at": datetime.now(UTC).isoformat(),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def release(self) -> bool:
        if not self.path.exists():
            return False
        self.path.unlink()
        return True


class ExecutionGate:
    def __init__(
        self,
        settings: Settings,
        kill_switch: KillSwitch,
        limits: EffectiveLimits,
        *,
        demo_authorization: Path | None = None,
    ) -> None:
        if not isinstance(settings, Settings):
            raise ExecutionBlockedError("settings are missing")
        self.settings = settings
        self.kill_switch = kill_switch
        self.limits = limits
        self.demo_authorization = demo_authorization

    def mode_allows_orders(self) -> tuple[bool, str]:
        mode = self.settings.execution_mode
        if mode is ExecutionMode.PAPER:
            if self.settings.live_trading_enabled:
                return False, "live_trading_enabled must be false"
            if self.settings.broker_adapter.lower() != "paper":
                return False, "paper mode requires the paper broker adapter"
            return True, "paper simulation"
        if mode is ExecutionMode.DEMO:
            return False, (
                "demo execution is not authorized: requires an owner demo authorization, a "
                "verified demo account, verified contract economics and a validated demo adapter"
            )
        if mode is ExecutionMode.LIVE:
            return False, "live execution is not authorized"
        return False, f"execution_mode={mode.value!r} never submits orders"

    def authorize_entry(
        self,
        *,
        decision_id: str,
        risk_eligible: bool,
        risk_fraction: float,
        open_positions: int,
        entries_today: int,
        daily_loss_fraction: float,
        blockers: list[str],
        reconciliation_consistent: bool,
    ) -> str:
        """Return the reserved-to-be client order id, or raise."""

        allowed, reason = self.mode_allows_orders()
        if not allowed:
            raise ExecutionBlockedError(reason)
        if self.kill_switch.engaged():
            raise ExecutionBlockedError("emergency stop is engaged; new entries are blocked")
        if not reconciliation_consistent:
            raise ExecutionBlockedError("position/order/risk reconciliation is inconsistent")
        if blockers:
            raise ExecutionBlockedError("runtime blockers: " + "; ".join(blockers))
        if not risk_eligible:
            raise ExecutionBlockedError("strategy risk evaluation did not approve the entry")
        if not 0 < risk_fraction <= self.limits.max_risk_fraction + 1e-12:
            raise ExecutionBlockedError("risk fraction exceeds the effective ceiling")
        if open_positions >= self.limits.max_concurrent_positions:
            raise ExecutionBlockedError("maximum concurrent positions reached")
        if entries_today >= self.limits.max_daily_entries:
            raise ExecutionBlockedError("maximum daily entries reached")
        if daily_loss_fraction >= self.limits.max_daily_loss_fraction - 1e-12:
            raise ExecutionBlockedError("daily loss limit reached")
        return client_order_id_for(decision_id)
