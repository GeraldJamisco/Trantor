"""Research-only session-isolated variant of the approved XAUUSD-P strategy.

This module deliberately contains no backtest engine, broker adapter, order
handling, or monetary sizing. It exposes a separate strategy identity that can
be selected explicitly for the Phase E research experiment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from ..models import MarketObservation
from .xauusd_p import (
    NewsContext,
    ResearchRiskState,
    SessionEvaluation,
    SessionFilterMode,
    StrategyDecision,
    StrategyIdentity,
    XAUUSDPMultiTimeframeStrategy,
)

__all__ = ["XAUUSDPMTFResearchVariant"]

_RESEARCH_VARIANT_NAME = "xauusd_p_multi_timeframe_research_filter_isolated"
_RESEARCH_VARIANT_VERSION = "1.0.0"
_RESEARCH_VARIANT_ID = "xauusd_p_filter_isolated_v1"


class XAUUSDPMTFResearchVariant(XAUUSDPMultiTimeframeStrategy):
    """Explicit research-only variant with session/news filters isolated."""

    _identity = StrategyIdentity(
        strategy_name=_RESEARCH_VARIANT_NAME,
        strategy_version=_RESEARCH_VARIANT_VERSION,
        variant_id=_RESEARCH_VARIANT_ID,
        variant_name="research_filter_isolated",
    )

    def _session_evaluation(
        self,
        decision_time: datetime,
        entry_time: datetime,
        *,
        session_calendar_verified: bool,
        research_mode: bool,
    ) -> SessionEvaluation:
        del decision_time, entry_time, session_calendar_verified, research_mode
        return SessionEvaluation(
            in_overlap=True,
            calendar_verified=False,
            research_allowed=True,
            reason=(
                "Research variant: session filter isolated; "
                "not verified session compliance."
            ),
            filter_mode=SessionFilterMode.RESEARCH_ISOLATED,
        )

    def evaluate(
        self,
        d1: Sequence[MarketObservation],
        h4: Sequence[MarketObservation],
        m15: Sequence[MarketObservation],
        *,
        decision_time: datetime,
        next_m15_bar: MarketObservation | None,
        risk_state: ResearchRiskState | None = None,
        news_context: NewsContext | None = None,
        research_mode: bool = True,
        news_unaware: bool = True,
        session_calendar_verified: bool = False,
    ) -> StrategyDecision:
        """Evaluate the labelled research variant.

        News and session compliance controls are intentionally unavailable in
        this variant. Supplied news data is ignored and never earns the news
        point. A caller asking for verified session status is rejected rather
        than being allowed to create a misleading compliance claim.
        """

        if session_calendar_verified:
            raise ValueError(
                "the research variant cannot claim a verified session calendar"
            )
        del news_context, research_mode, news_unaware
        decision = super().evaluate(
            d1,
            h4,
            m15,
            decision_time=decision_time,
            next_m15_bar=next_m15_bar,
            risk_state=risk_state,
            news_context=None,
            research_mode=True,
            news_unaware=True,
            session_calendar_verified=False,
        )
        if decision.session.filter_mode is not SessionFilterMode.RESEARCH_ISOLATED:
            decision = replace(
                decision,
                session=replace(
                    decision.session,
                    filter_mode=SessionFilterMode.RESEARCH_ISOLATED,
                ),
            )
        return replace(decision, variant_id=self.variant_id)
