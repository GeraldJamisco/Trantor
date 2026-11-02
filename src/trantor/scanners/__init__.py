"""Broker-independent research analysis for COMEX gold futures.

The gold analyzer consumes validated Phase 2 results and session calendars. It
never calls a provider, creates a trade signal, authorizes execution, or submits
an order.
"""

from .gold import GoldAnalysisInput, GoldOpportunityAnalyzer
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
    MarketDataResultDigest,
    RejectionCode,
    ScanOutcome,
)
from .replay import (
    GoldReplayFrame,
    ReplayError,
    ReplayProvider,
    load_replay_frame,
)

__all__ = [
    "EvaluationOutcome",
    "FilterEvaluation",
    "FilterName",
    "FilterStatus",
    "GoldAnalysisEvaluation",
    "GoldAnalysisInput",
    "GoldAnalysisReport",
    "GoldCandidate",
    "GoldMeasurement",
    "GoldOpportunityAnalyzer",
    "GoldRejection",
    "GoldReplayFrame",
    "MarketDataResultDigest",
    "RejectionCode",
    "ReplayError",
    "ReplayProvider",
    "ScanOutcome",
    "load_replay_frame",
]
