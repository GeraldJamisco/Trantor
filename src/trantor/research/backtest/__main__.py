"""Command-line entry point for the research-only Phase E backtester."""

from __future__ import annotations

import argparse
from pathlib import Path

from .data import load_research_data
from .engine import BacktestConfig, run_backtest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic gross price/R XAUUSD-P research backtest. "
            "This does not place orders or size positions."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="directory for summary, trade, decision, and manifest outputs",
    )
    parser.add_argument(
        "--no-decisions",
        action="store_true",
        help=(
            "diagnostic-only: disable canonical decision recording and omit "
            "the decision JSONL; not valid for authoritative acceptance"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data = load_research_data()
    result = run_backtest(
        BacktestConfig(
            data=data,
            record_decisions=not args.no_decisions,
            authoritative=not args.no_decisions,
        )
    )
    paths = result.write_reports(args.output, include_decisions=not args.no_decisions)
    report = result.report
    if args.no_decisions:
        print("DIAGNOSTIC ONLY - NOT AN AUTHORITATIVE EXPERIMENT")
    print(report["labels"]["gross"])
    print(report["labels"]["research"])
    print(f"config_fingerprint={result.config_fingerprint}")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
