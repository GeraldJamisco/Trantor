"""Strategy boundaries.

``Strategy`` is the legacy single-series/TradeSignal protocol. The active
XAUUSD-P strategy uses :class:`ResearchStrategy`, which consumes completed D1,
H4 and M15 contexts and returns the complete ``StrategyDecision``.

Neither protocol permits order creation, position sizing, execution imports, or
broker submission. Risk approval and order authorization belong to later layers.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..models import MarketObservation, TradeSignal

if TYPE_CHECKING:
    from .xauusd_p import (
        NewsContext,
        ResearchRiskState,
        StrategyDecision,
        StrategyIdentity,
        XAUUSDPMTFParameters,
    )

__all__ = ["ResearchStrategy", "Strategy"]


class Strategy(Protocol):
    """Legacy single-series strategy protocol; not the XAUUSD-P path."""

    name: str
    version: str

    def evaluate(self, observations: Sequence[MarketObservation]) -> TradeSignal | None:
        """Return one signal or no signal. Never return or place an order."""


@runtime_checkable
class ResearchStrategy(Protocol):
    """Authoritative multi-timeframe research strategy protocol."""

    @property
    def identity(self) -> "StrategyIdentity":
        """Stable strategy/variant identity."""

    @property
    def parameters(self) -> "XAUUSDPMTFParameters":
        """Approved strategy parameters."""

    def evaluate(
        self,
        d1: Sequence[MarketObservation],
        h4: Sequence[MarketObservation],
        m15: Sequence[MarketObservation],
        *,
        decision_time: datetime,
        next_m15_bar: MarketObservation | None,
        risk_state: "ResearchRiskState | None",
        news_context: "NewsContext | None" = None,
        research_mode: bool = True,
        news_unaware: bool = False,
        session_calendar_verified: bool = False,
    ) -> "StrategyDecision":
        """Return a complete research decision; never create an order."""
