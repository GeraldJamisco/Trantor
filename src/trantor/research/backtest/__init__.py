"""Research-only backtesting components for Phase E."""

from .engine import (
    BacktestConfig,
    BacktestError,
    BacktestResult,
    DecisionRecord,
    GROSS_LABEL,
    HOLDOUT_CUTOFF,
    RESEARCH_LABEL,
    SPLITS,
    Split,
    run_backtest,
    split_for,
)

__all__ = [
    "BacktestConfig",
    "BacktestError",
    "BacktestResult",
    "DecisionRecord",
    "GROSS_LABEL",
    "HOLDOUT_CUTOFF",
    "RESEARCH_LABEL",
    "SPLITS",
    "Split",
    "run_backtest",
    "split_for",
]
