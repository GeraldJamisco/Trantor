"""Trade-level diagnostics for the authoritative Phase E experiment.

This module is read-only. It joins completed trades to their canonical
StrategyDecision records, derives bar-level MFE/MAE from the validated M15
series, and reports descriptive gross-R groupings. It does not alter strategy,
backtest, broker, account, dataset, or execution behavior.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trantor.research.backtest.data import (
    DEFAULT_CHECKSUMS,
    load_research_data,
)
from trantor.strategy.research_record import canonical_json

SPLIT_ORDER = ("development", "validation", "holdout")
DEFAULT_ARTIFACT_DIR = Path(
    "research_results/xauusd_p/phase_e_authoritative_20261102"
)
DEFAULT_OUTPUT_DIR = Path("research_results/xauusd_p/phase_f_diagnostics")
GROSS_LABEL = "GROSS BASELINE - EXECUTION COSTS EXCLUDED"
INCONCLUSIVE_N = 30
SOURCE_KEYS = {
    "d1_final": "d1",
    "h4_final": "h4",
    "m15_final": "m15",
    "d1_warmup": "d1_warmup",
}
LIMITATIONS = (
    "The experiment used the research-isolated filter variant, not the complete intended strategy.",
    "All results are gross price/R only; execution costs are excluded.",
    "Absolute UTC provenance, session compliance, and news compliance are unverified.",
    "M15 OHLC cannot establish the exact intrabar path; MFE/MAE are bar-level approximations.",
    "No monetary sizing, account-equity return, or cost-adjusted profitability is claimed.",
    "Small groups are descriptive only and are not statistical evidence.",
)


class DiagnosticError(RuntimeError):
    """The authoritative artifacts cannot support a reliable diagnostic run."""


@dataclass(frozen=True, slots=True)
class DiagnosticConfig:
    artifact_dir: Path
    repo_root: Path
    output_dir: Path = DEFAULT_OUTPUT_DIR

    @classmethod
    def default(cls) -> "DiagnosticConfig":
        root = Path(__file__).resolve().parents[3]
        return cls(
            artifact_dir=root / DEFAULT_ARTIFACT_DIR,
            repo_root=root,
            output_dir=root / DEFAULT_OUTPUT_DIR,
        )

    def __post_init__(self) -> None:
        for name in ("artifact_dir", "repo_root", "output_dir"):
            value = getattr(self, name)
            if not isinstance(value, Path):
                object.__setattr__(self, name, Path(value))
        if not self.artifact_dir.is_absolute():
            object.__setattr__(self, "artifact_dir", self.repo_root / self.artifact_dir)
        if not self.output_dir.is_absolute():
            object.__setattr__(self, "output_dir", self.repo_root / self.output_dir)

    def relative(self, path: Path | str) -> str:
        try:
            return Path(path).resolve().relative_to(self.repo_root.resolve()).as_posix()
        except (OSError, ValueError):
            return "<external>"


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError(f"cannot parse {path.name}: {exc}") from exc


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DiagnosticError(f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DiagnosticError(f"timestamp lacks timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    wins = [row for row in rows if row["realized_r"] > 1e-12]
    losses = [row for row in rows if row["realized_r"] < -1e-12]
    breakeven = [row for row in rows if abs(row["realized_r"]) <= 1e-12]
    gross_profit = math.fsum(row["realized_r"] for row in wins)
    gross_loss = math.fsum(row["realized_r"] for row in losses)
    realized = math.fsum(row["realized_r"] for row in rows)
    return {
        "trade_count": n,
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": len(breakeven),
        "win_rate": (len(wins) / n) if n else None,
        "gross_realized_r": realized,
        "expectancy_r": (realized / n) if n else None,
        "gross_profit_r": gross_profit,
        "gross_loss_r": gross_loss,
        "profit_factor": (
            gross_profit / abs(gross_loss) if gross_loss < -1e-12 else None
        ),
        "profit_factor_status": (
            "defined" if gross_loss < -1e-12 else "undefined_no_losses"
        ),
        "inconclusive": n < INCONCLUSIVE_N,
        "sample_size_gate": (
            "inconclusive_fewer_than_30_trades"
            if n < INCONCLUSIVE_N
            else "descriptive_at_least_30_trades_not_statistical_proof"
        ),
    }


def _drawdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        rows,
        key=lambda row: (row["exit_time"] or row["entry_time"], row["trade_id"]),
    )
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    peak_trade = None
    trough_trade = None
    curve = []
    for row in ordered:
        cumulative += row["realized_r"]
        if cumulative > peak:
            peak = cumulative
            peak_trade = row["trade_id"]
        drawdown = peak - cumulative
        if drawdown > worst:
            worst = drawdown
            trough_trade = row["trade_id"]
        curve.append(
            {
                "trade_id": row["trade_id"],
                "exit_time": row["exit_time"],
                "split": row["split"],
                "realized_r": row["realized_r"],
                "cumulative_r": cumulative,
                "drawdown_r": drawdown,
            }
        )
    return {
        "maximum_drawdown_r": worst,
        "peak_cumulative_r": peak,
        "peak_trade_id": peak_trade,
        "trough_trade_id": trough_trade,
        "definition": (
            "Peak-to-trough cumulative realized R of closed trades ordered by "
            "exit_time then trade_id; no monetary drawdown is calculated."
        ),
        "curve": curve,
    }


def _alignment(side: str | None, d1: str | None, h4: str | None) -> str:
    if side == "buy":
        if d1 == "bullish" and h4 == "bullish":
            return "aligned_long"
        if d1 == "bearish" or h4 == "bearish":
            return "conflicted_long"
        return "partial_long"
    if side == "sell":
        if d1 == "bearish" and h4 == "bearish":
            return "aligned_short"
        if d1 == "bullish" or h4 == "bullish":
            return "conflicted_short"
        return "partial_short"
    return "unknown"


class TradeDiagnosticsService:
    """Build deterministic diagnostics from one Phase E artifact directory."""

    def __init__(
        self,
        config: DiagnosticConfig | None = None,
        *,
        data_loader: Any = load_research_data,
    ) -> None:
        self.config = config or DiagnosticConfig.default()
        self._data_loader = data_loader
        self._payload: dict[str, Any] | None = None
        self._data: Any = None

    def _load_artifacts(self) -> tuple[dict, dict, list[dict], list[dict]]:
        required = (
            "summary.json",
            "manifest.json",
            "trades.json",
            "entry_attempts.json",
            "decisions.jsonl",
        )
        for name in required:
            path = self.config.artifact_dir / name
            if not path.is_file():
                raise DiagnosticError(f"required artifact is missing: {name}")
        summary = _json(self.config.artifact_dir / "summary.json")
        manifest = _json(self.config.artifact_dir / "manifest.json")
        trades = _json(self.config.artifact_dir / "trades.json")
        attempts = _json(self.config.artifact_dir / "entry_attempts.json")
        if not isinstance(summary, dict) or not isinstance(manifest, dict):
            raise DiagnosticError("summary.json and manifest.json must be objects")
        if not isinstance(trades, list) or not isinstance(attempts, list):
            raise DiagnosticError("trades.json and entry_attempts.json must be lists")
        if not all(isinstance(item, dict) for item in trades + attempts):
            raise DiagnosticError("trade or entry-attempt records are malformed")
        return summary, manifest, trades, attempts

    def _load_decisions(
        self, wanted: set[str]
    ) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        path = self.config.artifact_dir / "decisions.jsonl"
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        raise DiagnosticError(
                            f"decisions.jsonl has a blank line at {line_number}"
                        )
                    record = json.loads(line)
                    record_hash = record.get("record_hash")
                    if record_hash not in wanted:
                        continue
                    canonical = record.get("canonical")
                    if not isinstance(canonical, dict):
                        raise DiagnosticError(
                            f"decision {record_hash!r} lacks canonical data"
                        )
                    calculated = hashlib.sha256(
                        canonical_json(canonical).encode()
                    ).hexdigest()
                    if calculated != record_hash:
                        raise DiagnosticError(
                            f"canonical hash mismatch for decision {record_hash!r}"
                        )
                    if canonical.get("decision_time") != record.get("decision_time"):
                        raise DiagnosticError(
                            f"decision timestamp mismatch for {record_hash!r}"
                        )
                    found[record_hash] = record
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DiagnosticError(f"cannot read canonical decisions: {exc}") from exc
        missing = wanted - set(found)
        if missing:
            raise DiagnosticError(
                "missing canonical decision record(s): "
                + ", ".join(sorted(missing)[:5])
            )
        return found

    def _load_data(self) -> Any:
        if self._data is None:
            try:
                self._data = self._data_loader()
            except Exception as exc:
                raise DiagnosticError(
                    f"historical data validation failed: {type(exc).__name__}: {exc}"
                ) from exc
        return self._data

    def _source_verification(
        self, manifest: dict[str, Any], data: Any
    ) -> list[dict[str, Any]]:
        source_map = {source["label"]: source for source in manifest.get("sources", [])}
        actual_map = {source.label: source for source in data.sources}
        result = []
        for label in ("d1_warmup", "d1_final", "h4_final", "m15_final"):
            declared = source_map.get(label)
            actual = actual_map.get(label)
            if declared is None or actual is None:
                result.append(
                    {
                        "label": label,
                        "verified": False,
                        "error": "source missing from manifest or loaded data",
                    }
                )
                continue
            expected = DEFAULT_CHECKSUMS[SOURCE_KEYS[label]]
            checksum = actual.checksum_sha256
            result.append(
                {
                    "label": label,
                    "timeframe": actual.timeframe,
                    "rows": actual.row_count,
                    "checksum_sha256": checksum,
                    "expected_checksum_sha256": expected,
                    "manifest_checksum_sha256": declared.get("checksum_sha256"),
                    "verified": (
                        checksum == expected
                        and declared.get("checksum_sha256") == expected
                        and declared.get("row_count") == actual.row_count
                    ),
                    "path": self.config.relative(actual.path),
                }
            )
        return result

    def _build_row(
        self,
        trade: dict[str, Any],
        decision: dict[str, Any],
        bars_by_time: dict[datetime, Any],
        bar_times: list[datetime],
    ) -> dict[str, Any]:
        canonical = decision["canonical"]
        candidate = canonical.get("candidate") or {}
        daily = canonical.get("daily_bias") or {}
        h4 = canonical.get("h4_trend") or {}
        score = canonical.get("score") or {}
        factors = [
            {
                "name": factor.get("name"),
                "points": factor.get("points"),
                "awarded": factor.get("awarded"),
                "available": factor.get("available"),
                "detail": factor.get("detail"),
            }
            for factor in score.get("factors", [])
            if isinstance(factor, dict)
        ]
        side = trade["side"]
        entry_time = _iso(trade["entry_time"])
        exits = trade.get("exits") or []
        last_exit = exits[-1] if exits else {}
        exit_time = _iso(trade["exit_time"]) if trade.get("exit_time") else None
        last_bar_time = _iso(last_exit["bar_time"]) if last_exit.get("bar_time") else None
        risk = float(trade["risk_distance"])
        entry = float(trade["entry_price"])

        # MFE/MAE are derived bar-level approximations. The entry bar is
        # included; the final exit-event bar is included through its completed
        # OHLC, without inventing an intrabar path.
        mfe_r = mae_r = None
        mfe_price = mae_price = None
        excursion_bars = 0
        if last_bar_time is not None and risk > 0:
            start_index = bisect_left(bar_times, entry_time)
            end_index = bisect_right(bar_times, last_bar_time) - 1
            if end_index >= start_index:
                selected = [bars_by_time[time] for time in bar_times[start_index : end_index + 1]]
                excursion_bars = len(selected)
                if side == "buy":
                    mfe_price = max(bar.high for bar in selected)
                    mae_price = min(bar.low for bar in selected)
                    mfe_r = max(0.0, (mfe_price - entry) / risk)
                    mae_r = max(0.0, (entry - mae_price) / risk)
                else:
                    mfe_price = min(bar.low for bar in selected)
                    mae_price = max(bar.high for bar in selected)
                    mfe_r = max(0.0, (entry - mfe_price) / risk)
                    mae_r = max(0.0, (mae_price - entry) / risk)

        factor_awards = {
            factor["name"]: factor["awarded"]
            for factor in factors
            if factor.get("name") is not None
        }
        tp1_reached = any(event.get("reason") == "tp1" for event in exits)
        tp2_completed = any(event.get("reason") == "tp2" for event in exits)
        breakeven_exit = any(event.get("reason") == "breakeven_stop" for event in exits)
        return {
            "trade_id": trade["trade_id"],
            "decision_hash": trade["decision_hash"],
            "decision_time": trade["decision_time"],
            "entry_time": trade["entry_time"],
            "exit_time": trade.get("exit_time"),
            "split": trade["entry_period"],
            "side": side,
            "entry_period": trade["entry_period"],
            "exit_period": trade.get("exit_period"),
            "entry_reference": candidate.get("entry_reference"),
            "entry_price": entry,
            "stop": trade["stop"],
            "tp1": trade["tp1"],
            "tp2": trade["tp2"],
            "risk_distance": risk,
            "risk_distance_pct_of_entry": (risk / entry * 100.0) if entry else None,
            "tp1_fraction": trade.get("tp1_fraction"),
            "tp2_fraction": trade.get("tp2_fraction"),
            "score": score.get("total"),
            "score_maximum": score.get("maximum"),
            "confidence": score.get("confidence"),
            "score_factors": factors,
            "factor_awards": factor_awards,
            "d1_bias": daily.get("state"),
            "h4_direction": h4.get("state"),
            "d1_h4_alignment": _alignment(side, daily.get("state"), h4.get("state")),
            "m15_trigger": {
                "rationale": candidate.get("rationale"),
                "score_factors": factors,
            },
            "zone_evidence": canonical.get("zone", {}).get("evidence") if isinstance(canonical.get("zone"), dict) else [],
            "exit_classification": trade.get("status"),
            "last_exit_reason": last_exit.get("reason"),
            "last_exit_price": last_exit.get("price"),
            "realized_r": float(trade["realized_r"]),
            "is_win": float(trade["realized_r"]) > 1e-12,
            "is_loss": float(trade["realized_r"]) < -1e-12,
            "tp1_reached": tp1_reached,
            "tp2_completed": tp2_completed,
            "breakeven_exit": breakeven_exit,
            "bars_held": trade.get("bars_held"),
            "holding_hours": (
                (exit_time - entry_time).total_seconds() / 3600.0
                if exit_time is not None
                else None
            ),
            "ambiguous_bar_count": trade.get("ambiguous_bar_count", 0),
            "gap_exit_count": trade.get("gap_exit_count", 0),
            "exits": exits,
            "max_favorable_excursion_r": mfe_r,
            "max_adverse_excursion_r": mae_r,
            "mfe_price": mfe_price,
            "mae_price": mae_price,
            "excursion_bars": excursion_bars,
            "excursion_method": (
                "Derived from M15 high/low from entry bar through the final "
                "exit-event bar; bar-level approximation, not an intrabar path."
            ),
            "derived_fields": [
                "d1_h4_alignment",
                "risk_distance_pct_of_entry",
                "holding_hours",
                "tp1_reached",
                "tp2_completed",
                "breakeven_exit",
                "is_win",
                "is_loss",
                "max_favorable_excursion_r",
                "max_adverse_excursion_r",
                "mfe_price",
                "mae_price",
                "excursion_bars",
            ],
        }

    def _group(self, rows: list[dict[str, Any]], key_name: str, key_fn: Any) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(key_fn(row))].append(row)
        return {name: _stats(group) for name, group in sorted(groups.items())}

    def _build_breakdowns(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        factor_names = sorted(
            {
                factor["name"]
                for row in rows
                for factor in row["score_factors"]
                if factor.get("name")
            }
        )
        factor_breakdown = {}
        for name in factor_names:
            awarded = [row for row in rows if row["factor_awards"].get(name) is True]
            not_awarded = [row for row in rows if row["factor_awards"].get(name) is not True]
            factor_breakdown[name] = {
                "awarded": _stats(awarded),
                "not_awarded": _stats(not_awarded),
            }
        return {
            "overall": _stats(rows),
            "by_split": self._group(rows, "split", lambda row: row["split"]),
            "by_side": self._group(rows, "side", lambda row: row["side"]),
            "by_score": self._group(rows, "score", lambda row: row["score"]),
            "by_exit_classification": self._group(
                rows, "exit_classification", lambda row: row["exit_classification"]
            ),
            "by_tp1_reached": self._group(
                rows, "tp1_reached", lambda row: str(row["tp1_reached"]).lower()
            ),
            "by_tp2_completed": self._group(
                rows, "tp2_completed", lambda row: str(row["tp2_completed"]).lower()
            ),
            "by_d1_bias": self._group(rows, "d1", lambda row: row["d1_bias"]),
            "by_h4_direction": self._group(
                rows, "h4", lambda row: row["h4_direction"]
            ),
            "by_d1_h4_alignment": self._group(
                rows, "alignment", lambda row: row["d1_h4_alignment"]
            ),
            "by_ambiguous": self._group(
                rows, "ambiguous", lambda row: bool(row["ambiguous_bar_count"])
            ),
            "by_confluence_factor": factor_breakdown,
        }

    def _ambiguous_events(
        self, rows: list[dict[str, Any]], bars_by_time: dict[datetime, Any]
    ) -> list[dict[str, Any]]:
        events = []
        for row in rows:
            for event in row["exits"]:
                if not event.get("ambiguous"):
                    continue
                bar_time = _iso(event["bar_time"])
                bar = bars_by_time.get(bar_time)
                touched_tp1 = touched_tp2 = None
                if bar is not None:
                    if row["side"] == "buy":
                        touched_tp1 = bar.high >= row["tp1"]
                        touched_tp2 = bar.high >= row["tp2"]
                    else:
                        touched_tp1 = bar.low <= row["tp1"]
                        touched_tp2 = bar.low <= row["tp2"]
                events.append(
                    {
                        "trade_id": row["trade_id"],
                        "split": row["split"],
                        "side": row["side"],
                        "event_reason": event.get("reason"),
                        "event_price": event.get("price"),
                        "event_bar_time": event["bar_time"],
                        "realized_r": row["realized_r"],
                        "stop": row["stop"],
                        "tp1": row["tp1"],
                        "tp2": row["tp2"],
                        "bar_high": bar.high if bar is not None else None,
                        "bar_low": bar.low if bar is not None else None,
                        "bar_touched_tp1": touched_tp1,
                        "bar_touched_tp2": touched_tp2,
                        "handling": "conservative stop-first; target path not assumed",
                    }
                )
        return events

    def _exit_reconciliation(self, summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        reasons = Counter(
            event.get("reason") for row in rows for event in row["exits"]
        )
        metrics = (summary.get("overall") or {}).get("metrics") or {}
        expected_reasons = metrics.get("exit_reason_counts") or {}
        actual = {
            "tp1_events": reasons.get("tp1", 0),
            "tp2_events": reasons.get("tp2", 0),
            "breakeven_stops": reasons.get("breakeven_stop", 0),
            "initial_stops": reasons.get("stop", 0),
            "tp1_trade_count": sum(row["tp1_reached"] for row in rows),
            "tp2_trade_count": sum(row["tp2_completed"] for row in rows),
            "status_counts": dict(sorted(Counter(row["exit_classification"] for row in rows).items())),
        }
        expected = {
            "tp1_events": expected_reasons.get("tp1", 0),
            "tp2_events": expected_reasons.get("tp2", 0),
            "breakeven_stops": expected_reasons.get("breakeven_stop", 0),
            "initial_stops": expected_reasons.get("stop", 0),
            "tp1_trade_count": metrics.get("win_count", 0),
            "tp2_trade_count": metrics.get("status_counts", {}).get("tp2_closed", 0)
            if isinstance(metrics.get("status_counts"), dict)
            else 0,
        }
        return {
            "actual": actual,
            "summary_reference": expected,
            "matches_summary": (
                actual["tp1_events"] == expected["tp1_events"]
                and actual["tp2_events"] == expected["tp2_events"]
                and actual["breakeven_stops"] == expected["breakeven_stops"]
                and actual["initial_stops"] == expected["initial_stops"]
            ),
        }

    def build(self) -> dict[str, Any]:
        if self._payload is not None:
            return self._payload
        summary, manifest, trades, attempts = self._load_artifacts()
        contract = summary.get("record_contract") or {}
        if contract.get("authoritative") is not True or contract.get("canonical_records_enabled") is not True:
            raise DiagnosticError("artifacts are not an authoritative canonical-record run")
        if manifest.get("record_contract") != contract:
            raise DiagnosticError("manifest and summary record contracts differ")
        if manifest.get("config_fingerprint") != summary.get("config_fingerprint"):
            raise DiagnosticError("manifest and summary config fingerprints differ")
        if len(trades) != 43:
            raise DiagnosticError(f"expected 43 closed trades, found {len(trades)}")
        if any(trade.get("closed") is not True for trade in trades):
            raise DiagnosticError("not all trades are closed")
        wanted = {
            trade.get("decision_hash") for trade in trades
        } | {
            attempt.get("decision_hash") for attempt in attempts
        }
        if None in wanted:
            raise DiagnosticError("trade or entry attempt lacks decision_hash")
        decisions = self._load_decisions(wanted)
        data = self._load_data()
        bars = data.m15
        bars_by_time = {bar.observed_at: bar for bar in bars}
        bar_times = [bar.observed_at for bar in bars]
        rows = [
            self._build_row(trade, decisions[trade["decision_hash"]], bars_by_time, bar_times)
            for trade in sorted(trades, key=lambda item: item["trade_id"])
        ]
        breakdowns = self._build_breakdowns(rows)
        drawdown = _drawdown(rows)
        summary_drawdown = (summary.get("overall") or {}).get("metrics", {}).get("max_drawdown_r")
        drawdown["matches_summary"] = (
            summary_drawdown is not None
            and abs(summary_drawdown - drawdown["maximum_drawdown_r"]) < 1e-9
        )
        sources = self._source_verification(manifest, data)
        if not all(source["verified"] for source in sources):
            raise DiagnosticError("dataset checksum verification failed")
        missing_attempt_links = [
            attempt.get("decision_time")
            for attempt in attempts
            if attempt.get("decision_hash") not in decisions
        ]
        trades_linked = all(trade.get("decision_hash") in decisions for trade in trades)
        payload = {
            "schema": "trantor.phase_f_trade_diagnostics",
            "schema_version": "1.0",
            "gross_label": GROSS_LABEL,
            "run": {
                "artifact_dir": self.config.relative(self.config.artifact_dir),
                "config_fingerprint": summary.get("config_fingerprint"),
                "variant_id": (summary.get("strategy") or {}).get("variant_id"),
                "variant_name": (summary.get("strategy") or {}).get("variant_name"),
                "record_contract": contract,
            },
            "verification": {
                "status": "PASS" if trades_linked and not missing_attempt_links else "FAIL",
                "trades_checked": len(trades),
                "attempts_checked": len(attempts),
                "linked_decisions_checked": len(decisions),
                "trades_linked": trades_linked,
                "attempts_linked": not missing_attempt_links,
                "missing_attempt_links": missing_attempt_links,
                "source_checksums": sources,
            },
            "trades": rows,
            "breakdowns": breakdowns,
            "drawdown": drawdown,
            "exit_reconciliation": self._exit_reconciliation(summary, rows),
            "ambiguous": {
                "ambiguous_bar_count": sum(row["ambiguous_bar_count"] for row in rows),
                "events": self._ambiguous_events(rows, bars_by_time),
            },
            "constraints": list(LIMITATIONS),
        }
        self._payload = payload
        return payload

    def write_artifacts(self, output_dir: Path | None = None) -> tuple[Path, ...]:
        payload = self.build()
        directory = output_dir or self.config.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / "trade_diagnostics.json"
        breakdown_path = directory / "breakdowns.json"
        summary_path = directory / "summary.json"
        csv_path = directory / "trade_diagnostics.csv"
        json_path.write_text(
            json.dumps(payload["trades"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        breakdown_path.write_text(
            json.dumps(payload["breakdowns"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_payload = {
            key: payload[key]
            for key in (
                "schema",
                "schema_version",
                "gross_label",
                "run",
                "verification",
                "drawdown",
                "exit_reconciliation",
                "ambiguous",
                "constraints",
            )
        }
        summary_payload["breakdowns"] = payload["breakdowns"]
        summary_path.write_text(
            json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        columns = [
            "trade_id",
            "decision_hash",
            "decision_time",
            "entry_time",
            "exit_time",
            "split",
            "side",
            "score",
            "confidence",
            "d1_bias",
            "h4_direction",
            "d1_h4_alignment",
            "entry_reference",
            "entry_price",
            "stop",
            "tp1",
            "tp2",
            "risk_distance",
            "risk_distance_pct_of_entry",
            "exit_classification",
            "last_exit_reason",
            "last_exit_price",
            "realized_r",
            "tp1_reached",
            "tp2_completed",
            "bars_held",
            "holding_hours",
            "ambiguous_bar_count",
            "gap_exit_count",
            "max_favorable_excursion_r",
            "max_adverse_excursion_r",
            "score_factors",
            "m15_trigger",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in payload["trades"]:
                writer.writerow(
                    {
                        column: (
                            json.dumps(row.get(column), sort_keys=True)
                            if column in {"score_factors", "m15_trigger"}
                            else row.get(column)
                        )
                        for column in columns
                    }
                )
        return (json_path, breakdown_path, summary_path, csv_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate read-only Phase F trade diagnostics."
    )
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    config = DiagnosticConfig.default()
    artifacts = args.artifacts if args.artifacts.is_absolute() else config.repo_root / args.artifacts
    output = args.output if args.output.is_absolute() else config.repo_root / args.output
    service = TradeDiagnosticsService(
        DiagnosticConfig(
            artifact_dir=artifacts,
            repo_root=config.repo_root,
            output_dir=output,
        )
    )
    payload = service.build()
    paths = service.write_artifacts()
    print(GROSS_LABEL)
    print(json.dumps(payload["verification"], indent=2, sort_keys=True))
    print(json.dumps(payload["drawdown"]["maximum_drawdown_r"], sort_keys=True))
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
