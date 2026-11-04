"""Versioned research experiments built on the unchanged Phase E backtester.

Experiment ``phase_g_approved_utc_v1``
--------------------------------------

The first historical evaluation of the **complete approved strategy**
(``xauusd_p_approved_v1``) with its IANA London/New York session filter
active. That filter needs true UTC, but the datasets are in broker server time
(``dataset_time``). Evidence for the conversion:

1. Live measurement on 2026-11-04: broker tick time = UTC + 2 h.
2. In the datasets the daily trading break sits at 00:00-01:00 server time in
   every DST regime, including the 43 trading days in 2024-2026 when US DST was
   on and EU DST was off; the weekly open is always Monday 01:00. CME's gold
   break is 17:00-18:00 New York time. Hence server time = New York + 7 h
   (UTC+2 without US DST, UTC+3 with it).

The rule is inferred and consistent with all evidence, but it is not a broker
statement. Every output carries that label. Nothing here changes a strategy
parameter, a dataset file, or the Phase E/F baselines; outputs go to a new
directory. The news filter stays unavailable (no historical calendar), so the
run is ``news_unaware``; results are gross.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..models import MarketObservation
from ..strategy.xauusd_p import XAUUSDPMultiTimeframeStrategy
from .backtest.data import ResearchData, load_research_data
from .backtest.engine import GROSS_LABEL, BacktestConfig, run_backtest

__all__ = [
    "EXPERIMENT_ID",
    "server_to_utc",
    "to_utc_research_data",
    "run_phase_g",
]

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
EXPERIMENT_ID = "phase_g_approved_utc_v1"
SERVER_RULE = "broker server time = America/New_York local time + 7 h (inferred)"
_DURATIONS = {"1d": timedelta(days=1), "4h": timedelta(hours=4), "15m": timedelta(minutes=15)}


def server_to_utc(server_time: datetime) -> datetime:
    """Convert a broker server wall-clock time (stored as UTC) to true UTC."""

    wall = server_time.replace(tzinfo=None) - timedelta(hours=7)
    # fold=0 resolves the repeated autumn hour to the first (DST) occurrence,
    # and a skipped spring hour is normalised forward by zoneinfo.
    return wall.replace(tzinfo=NEW_YORK).astimezone(UTC)


def _convert(bars: tuple[MarketObservation, ...]) -> tuple[MarketObservation, ...]:
    converted = [replace(bar, observed_at=server_to_utc(bar.observed_at)) for bar in bars]
    for previous, current in zip(converted, converted[1:]):
        if current.observed_at <= previous.observed_at:
            raise ValueError("server-to-UTC conversion produced non-increasing timestamps")
    return tuple(converted)


def to_utc_research_data(data: ResearchData) -> ResearchData:
    d1 = _convert(data.d1)
    h4 = _convert(data.h4)
    m15 = _convert(data.m15)
    return replace(
        data,
        d1=d1,
        h4=h4,
        m15=m15,
        d1_close_times=tuple(bar.observed_at + _DURATIONS["1d"] for bar in d1),
        h4_close_times=tuple(bar.observed_at + _DURATIONS["4h"] for bar in h4),
        m15_close_times=tuple(bar.observed_at + _DURATIONS["15m"] for bar in m15),
    )


def run_phase_g(output_dir: Path, *, end_time: datetime | None = None) -> dict[str, Any]:
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"{output_dir} is not empty; experiments never overwrite results")
    data = to_utc_research_data(load_research_data())
    config = BacktestConfig(data=data, strategy=XAUUSDPMultiTimeframeStrategy())
    if end_time is not None:
        config = replace(config, end_time=end_time)
    result = run_backtest(config)
    result.report["labels"] = {
        "research": (
            "COMPLETE APPROVED STRATEGY xauusd_p_approved_v1 - IANA SESSION FILTER ACTIVE - "
            "NEWS-UNAWARE (NO HISTORICAL CALENDAR) - RESEARCH ONLY"
        ),
        "gross": GROSS_LABEL,
        "time": "UTC VIA INFERRED BROKER DST RULE - NOT BROKER-CONFIRMED",
    }
    result.report["time_semantics"] = {
        "coordinate": "utc_inferred",
        "rule": SERVER_RULE,
        "evidence": [
            "live tick-vs-host measurement 2026-11-04: UTC+2",
            "daily break 00:00-01:00 server in all DST regimes incl. US-on/EU-off weeks",
            "weekly open Monday 01:00 server = Sunday 18:00 New York",
        ],
        "split_boundaries": "split windows are evaluated in UTC (Phase E used dataset_time)",
    }
    result.report["experiment"] = {
        "experiment_id": EXPERIMENT_ID,
        "baseline_compared": "phase_e_authoritative_20261102 (research_filter_isolated variant, dataset_time)",
        "changes_vs_baseline": [
            "strategy identity: approved (session filter active) instead of filter-isolated variant",
            "timestamps converted from broker server time to UTC with the inferred rule",
        ],
        "unchanged": [
            "all strategy parameters",
            "backtester, fill model, splits, datasets (source files untouched)",
        ],
        "holdout_policy": "evaluated once; no parameter was tuned on any split",
    }
    outputs = result.write_reports(output_dir)
    (output_dir / "experiment.json").write_text(
        json.dumps(result.report["experiment"] | {"time_semantics": result.report["time_semantics"], "labels": result.report["labels"]}, indent=2),
        encoding="utf-8",
    )
    return {"outputs": [str(path) for path in outputs], "overall": result.report.get("overall"), "splits": result.report.get("splits")}


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("research_results/xauusd_p") / EXPERIMENT_ID
    summary = run_phase_g(target)
    print(json.dumps({"outputs": summary["outputs"]}, indent=2))
