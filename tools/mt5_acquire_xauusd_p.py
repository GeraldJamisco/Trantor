"""Read-only XAUUSD-P daily history acquisition and audit.

This tool is deliberately outside ``src/trantor``. It uses the existing
read-only MT5 provider, writes versioned research datasets, and never calls
order, position, account-trading, or execution functions.

The worker is executed in a child process so a hung MT5 history request can be
terminated by the parent without hanging the acquisition run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from trantor.market_data import (  # noqa: E402
    MT5MarketDataProvider,
    MarketDataRequest,
    XAUUSD_P_INSTRUMENT,
)
from trantor.models import MarketObservation  # noqa: E402

SYMBOL = XAUUSD_P_INSTRUMENT.symbol
TIMEFRAME = "1d"
SCHEMA_VERSION = 2
DEFAULT_START = date(2010, 1, 1)
DEFAULT_CHUNK_DAYS = 90
DEFAULT_RETRIES = 2
DEFAULT_CHUNK_TIMEOUT_SECONDS = 60.0
DEFAULT_WORKER_TIMEOUT_MS = 15_000
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_data" / "xauusd_p" / "runs"
CSV_COLUMNS = ("time_utc", "open", "high", "low", "close", "volume")
COST_COLUMNS = ("time_utc", "spread_points", "real_volume", "tick_volume")
TIMEFRAME_MINUTES = {"1d": 1_440, "4h": 240, "15m": 15}
TIMEFRAME_CONSTANTS = {
    "1d": "TIMEFRAME_D1",
    "4h": "TIMEFRAME_H4",
    "15m": "TIMEFRAME_M15",
}
DEFAULT_CHUNK_DAYS_BY_TIMEFRAME = {"1d": 365, "4h": 180, "15m": 30}
DEFAULT_START_BY_TIMEFRAME = {
    "1d": date(2010, 1, 1),
    "4h": date(2024, 1, 1),
    "15m": date(2024, 1, 1),
}

DEFAULT_TERMINAL_CANDIDATES = (
    Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
    Path(r"C:\Program Files\FPG MetaTrader 5\terminal64.exe"),
    Path(r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe"),
)

FetchChunk = Callable[["ChunkPlan"], Sequence[MarketObservation]]


@dataclass(frozen=True, slots=True)
class ChunkPlan:
    index: int
    start: datetime
    end: datetime


@dataclass(slots=True)
class ChunkResult:
    plan: ChunkPlan
    status: str
    attempts: int = 0
    observations: tuple[MarketObservation, ...] = ()
    error: str = ""

    def summary(self) -> dict[str, Any]:
        first = self.observations[0].observed_at if self.observations else None
        last = self.observations[-1].observed_at if self.observations else None
        return {
            "index": self.plan.index,
            "requested_start_utc": self.plan.start.isoformat(),
            "requested_end_utc": self.plan.end.isoformat(),
            "status": self.status,
            "attempts": self.attempts,
            "returned_count": len(self.observations),
            "returned_first_utc": first.isoformat() if first else None,
            "returned_last_utc": last.isoformat() if last else None,
            "error": self.error,
        }


@dataclass(slots=True)
class ValidationReport:
    timeframe: str = TIMEFRAME
    row_count: int = 0
    first_observation_utc: str | None = None
    last_observation_utc: str | None = None
    duplicate_timestamps: int = 0
    unordered_pairs: int = 0
    non_utc_timestamps: int = 0
    non_midnight_timestamps: int = 0
    unexpected_boundary_timestamps: int = 0
    invalid_ohlc_rows: int = 0
    incomplete_current_rows: int = 0
    weekday_gap_count: int = 0
    weekday_gap_sample: list[str] = field(default_factory=list)
    gap_count: int = 0
    gap_sample: list[str] = field(default_factory=list)
    chunk_boundary_issues: list[str] = field(default_factory=list)
    repeatability_checked: bool = False
    repeatability_ok: bool | None = None
    repeatability_error: str = ""
    structural_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverage_complete: bool = False
    accepted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("value must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("value must be timezone-aware")
    return value.astimezone(timezone.utc)


def _date_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _date_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=timezone.utc)


def latest_completed_day(
    now: datetime | None = None,
    *,
    timeframe: str = TIMEFRAME,
) -> date:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    current = _as_utc(now or datetime.now(timezone.utc))
    if timeframe == "1d":
        return current.date() - timedelta(days=1)
    # Intraday requests may include completed bars from the current UTC date.
    # Validation excludes any candle whose full interval has not elapsed.
    return current.date()


def _candle_interval(timeframe: str) -> timedelta:
    try:
        return timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    except KeyError:
        raise ValueError(f"unsupported timeframe {timeframe!r}") from None


def _boundary_is_valid(observed_at: datetime, timeframe: str) -> bool:
    utc_at = observed_at.astimezone(timezone.utc)
    if utc_at.second != 0 or utc_at.microsecond != 0:
        return False
    if timeframe == "1d":
        return utc_at.hour == 0 and utc_at.minute == 0
    if timeframe == "4h":
        return utc_at.minute == 0 and utc_at.hour % 4 == 0
    if timeframe == "15m":
        return utc_at.minute % 15 == 0
    return False


def _gap_rows(
    observations: Sequence[MarketObservation],
    timeframe: str,
) -> list[dict[str, Any]]:
    interval = _candle_interval(timeframe)
    rows: list[dict[str, Any]] = []
    for previous, current in zip(observations, observations[1:]):
        delta = current.observed_at - previous.observed_at
        if delta <= interval:
            continue
        rows.append(
            {
                "previous_time_utc": previous.observed_at.astimezone(timezone.utc).isoformat(),
                "next_time_utc": current.observed_at.astimezone(timezone.utc).isoformat(),
                "gap_seconds": int(delta.total_seconds()),
                "gap_bars": max(1, int(delta / interval) - 1),
            }
        )
    return rows


def chunk_plan(
    start: date,
    end: date,
    *,
    chunk_days: int,
) -> tuple[ChunkPlan, ...]:
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if start > end:
        raise ValueError("start must not be after end")
    if isinstance(chunk_days, bool) or not isinstance(chunk_days, int) or chunk_days <= 0:
        raise ValueError("chunk_days must be a positive integer")

    plans: list[ChunkPlan] = []
    current = start
    index = 1
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        plans.append(
            ChunkPlan(
                index=index,
                start=_date_start(current),
                end=_date_end(chunk_end),
            )
        )
        current = chunk_end + timedelta(days=1)
        index += 1
    return tuple(plans)


def _observation_payload(observation: MarketObservation) -> dict[str, Any]:
    return {
        "time": observation.observed_at.astimezone(timezone.utc).isoformat(),
        "open": observation.open,
        "high": observation.high,
        "low": observation.low,
        "close": observation.close,
        "volume": observation.volume,
    }


def _observations_payload(
    observations: Sequence[MarketObservation],
) -> list[dict[str, Any]]:
    return [_observation_payload(item) for item in observations]


def _observations_from_payload(
    payload: Any,
    timeframe: str,
) -> tuple[MarketObservation, ...]:
    if not isinstance(payload, list):
        raise ValueError("worker payload observations must be a list")
    observations: list[MarketObservation] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"worker observation {index} is not an object")
        try:
            observed_at = datetime.fromisoformat(str(item["time"]))
            observation = MarketObservation(
                symbol=SYMBOL,
                timeframe=timeframe,
                observed_at=_as_utc(observed_at),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=None if item.get("volume") is None else float(item["volume"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"worker observation {index} is invalid: {exc}") from exc
        observations.append(observation)
    return tuple(observations)


def _worker_main(
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    terminal_path: str,
    timeout_ms: int,
) -> int:
    try:
        provider = MT5MarketDataProvider(
            terminal_path=terminal_path,
            instrument=XAUUSD_P_INSTRUMENT,
            timeframes=(timeframe,),
            latest_bar_count=1,
            terminal_timeout_ms=timeout_ms,
        )
        with provider:
            request = MarketDataRequest(
                instrument=XAUUSD_P_INSTRUMENT,
                timeframe=timeframe,
                start=_as_utc(start),
                end=_as_utc(end),
            )
            observations = provider.fetch_observations(request)
        print(
            json.dumps(
                {
                    "ok": True,
                    "observations": _observations_payload(observations),
                }
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        )
        return 1


def _fetch_chunk_isolated(
    plan: ChunkPlan,
    *,
    timeframe: str,
    terminal_path: str,
    timeout_seconds: float,
    worker_timeout_ms: int,
) -> tuple[MarketObservation, ...]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--timeframe",
        timeframe,
        "--worker-start",
        plan.start.isoformat(),
        "--worker-end",
        plan.end.isoformat(),
        "--terminal-path",
        terminal_path,
        "--worker-timeout-ms",
        str(worker_timeout_ms),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"chunk {plan.index} exceeded {timeout_seconds:.1f}s"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"could not start worker: {exc}") from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if not stdout:
        detail = stderr[-1000:] if stderr else "no worker output"
        raise RuntimeError(
            f"worker exited {completed.returncode} without JSON: {detail}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = stderr[-1000:] if stderr else stdout[-1000:]
        raise RuntimeError(f"worker returned invalid JSON: {detail}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else None
        raise RuntimeError(str(error or "worker reported failure"))
    return _observations_from_payload(payload.get("observations"), timeframe)


def _costs_worker_main(
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    terminal_path: str,
    timeout_ms: int,
) -> int:
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]

        if not mt5.initialize(terminal_path, timeout=timeout_ms):
            raise RuntimeError(f"initialize failed: {mt5.last_error()}")
        try:
            constant = TIMEFRAME_CONSTANTS[timeframe]
            mt5_timeframe = getattr(mt5, constant, None)
            if mt5_timeframe is None:
                raise RuntimeError(f"MT5 timeframe constant {constant!r} unavailable")
            rates = mt5.copy_rates_range(
                SYMBOL,
                mt5_timeframe,
                _as_utc(start),
                _as_utc(end),
            )
            if rates is None:
                raise RuntimeError(f"copy_rates_range failed: {mt5.last_error()}")
            rows = [
                {
                    "time": int(row["time"]),
                    "spread_points": int(row["spread"]),
                    "real_volume": float(row["real_volume"]),
                    "tick_volume": float(row["tick_volume"]),
                }
                for row in rates
            ]
        finally:
            mt5.shutdown()
        print(json.dumps({"ok": True, "rows": rows}))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        )
        return 1


def _fetch_costs_isolated(
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    terminal_path: str,
    timeout_seconds: float,
    worker_timeout_ms: int,
) -> list[dict[str, Any]]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--costs-worker",
        "--timeframe",
        timeframe,
        "--worker-start",
        _as_utc(start).isoformat(),
        "--worker-end",
        _as_utc(end).isoformat(),
        "--terminal-path",
        terminal_path,
        "--worker-timeout-ms",
        str(worker_timeout_ms),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"cost extraction exceeded {timeout_seconds:.1f}s"
        ) from exc
    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise RuntimeError(
            f"cost worker exited {completed.returncode} without JSON"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("cost worker returned invalid JSON") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else None
        raise RuntimeError(str(error or "cost worker reported failure"))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("cost worker returned no rows list")
    return rows


INSTRUMENT_METADATA_FIELDS = (
    "name",
    "description",
    "path",
    "exchange",
    "currency_base",
    "currency_profit",
    "currency_margin",
    "trade_contract_size",
    "trade_tick_size",
    "trade_tick_value",
    "point",
    "digits",
    "spread",
    "volume_min",
    "volume_max",
    "volume_step",
    "margin_initial",
    "margin_maintenance",
    "start_time",
    "expiration_time",
    "trade_mode",
    "trade_exemode",
    "trade_calc_mode",
    "filling_mode",
    "order_mode",
    "expiration_mode",
    "visible",
    "select",
    "custom",
)
ACCOUNT_METADATA_FIELDS = (
    "trade_mode",
    "company",
    "server",
    "currency",
    "leverage",
    "margin_mode",
    "trade_allowed",
    "trade_expert",
    "fifo_close",
    "limit_orders",
)


def _metadata_worker_main(*, terminal_path: str, timeout_ms: int) -> int:
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]

        if not mt5.initialize(terminal_path, timeout=timeout_ms):
            raise RuntimeError(f"initialize failed: {mt5.last_error()}")
        try:
            info = mt5.symbol_info(SYMBOL)
            if info is None:
                raise RuntimeError(f"symbol_info failed: {mt5.last_error()}")
            account = mt5.account_info()
            metadata = {
                "symbol": {
                    field: getattr(info, field, None)
                    for field in INSTRUMENT_METADATA_FIELDS
                },
                "account": (
                    {
                        field: getattr(account, field, None)
                        for field in ACCOUNT_METADATA_FIELDS
                    }
                    if account is not None
                    else {}
                ),
            }
        finally:
            mt5.shutdown()
        print(json.dumps({"ok": True, "metadata": metadata}, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        )
        return 1


def _fetch_metadata_isolated(
    *,
    terminal_path: str,
    timeout_seconds: float,
    worker_timeout_ms: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--metadata-worker",
        "--terminal-path",
        terminal_path,
        "--worker-timeout-ms",
        str(worker_timeout_ms),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"metadata extraction exceeded {timeout_seconds:.1f}s"
        ) from exc
    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise RuntimeError("metadata worker produced no JSON")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("metadata worker returned invalid JSON") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else None
        raise RuntimeError(str(error or "metadata worker reported failure"))
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("metadata worker returned no metadata")
    return metadata


def acquire_chunks(
    plans: Sequence[ChunkPlan],
    fetch_chunk: FetchChunk,
    *,
    retries: int = DEFAULT_RETRIES,
) -> tuple[ChunkResult, ...]:
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("retries must be a non-negative integer")

    results: list[ChunkResult] = []
    for plan in plans:
        attempts = 0
        result = ChunkResult(plan=plan, status="failed")
        while attempts <= retries:
            attempts += 1
            try:
                observations = tuple(fetch_chunk(plan))
            except TimeoutError as exc:
                result.status = "timeout"
                result.error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                result.status = "failed"
                result.error = f"{type(exc).__name__}: {exc}"
            else:
                result.observations = observations
                result.status = "ok" if observations else "empty"
                result.error = ""
                break
        result.attempts = attempts
        results.append(result)
    return tuple(results)


def _canonical_csv_bytes(observations: Sequence[MarketObservation]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for observation in observations:
        writer.writerow(
            (
                observation.observed_at.astimezone(timezone.utc).isoformat(),
                repr(float(observation.open)),
                repr(float(observation.high)),
                repr(float(observation.low)),
                repr(float(observation.close)),
                "" if observation.volume is None else repr(float(observation.volume)),
            )
        )
    return buffer.getvalue().encode("utf-8")


def validate_observations(
    observations: Sequence[MarketObservation],
    *,
    now: datetime,
    timeframe: str = TIMEFRAME,
    chunk_results: Sequence[ChunkResult] = (),
    repeatability_ok: bool | None = None,
    repeatability_error: str = "",
) -> tuple[tuple[MarketObservation, ...], ValidationReport]:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    current = _as_utc(now)
    interval = _candle_interval(timeframe)
    report = ValidationReport(
        timeframe=timeframe,
        repeatability_checked=repeatability_ok is not None,
        repeatability_ok=repeatability_ok,
        repeatability_error=repeatability_error,
    )

    completed: list[MarketObservation] = []
    for observation in observations:
        if observation.observed_at.astimezone(timezone.utc) + interval > current:
            report.incomplete_current_rows += 1
        else:
            completed.append(observation)

    if not completed:
        report.structural_errors.append(
            f"no completed {timeframe} observations"
        )
        return (), report

    seen: set[datetime] = set()
    previous: datetime | None = None
    for index, observation in enumerate(completed):
        observed_at = observation.observed_at
        if observed_at.utcoffset() != timedelta(0):
            report.non_utc_timestamps += 1
        try:
            utc_at = _as_utc(observed_at)
        except ValueError:
            report.non_utc_timestamps += 1
            continue
        if not _boundary_is_valid(utc_at, timeframe):
            if timeframe == "1d":
                report.non_midnight_timestamps += 1
            else:
                report.unexpected_boundary_timestamps += 1
        if utc_at in seen:
            report.duplicate_timestamps += 1
        if previous is not None and utc_at < previous:
            report.unordered_pairs += 1
        seen.add(utc_at)
        previous = utc_at

        prices = (observation.open, observation.high, observation.low, observation.close)
        if any(isinstance(value, bool) or not math.isfinite(float(value)) for value in prices):
            report.invalid_ohlc_rows += 1
        elif (
            observation.high < max(observation.open, observation.close, observation.low)
            or observation.low > min(observation.open, observation.close, observation.high)
        ):
            report.invalid_ohlc_rows += 1
        if observation.volume is not None and (
            isinstance(observation.volume, bool)
            or not math.isfinite(observation.volume)
            or observation.volume < 0
        ):
            report.invalid_ohlc_rows += 1
        if observation.symbol != SYMBOL:
            report.structural_errors.append(
                f"observation {index} has unexpected symbol {observation.symbol!r}"
            )
        if observation.timeframe != timeframe:
            report.structural_errors.append(
                f"observation {index} has unexpected timeframe "
                f"{observation.timeframe!r}; expected {timeframe!r}"
            )

    if report.duplicate_timestamps:
        report.structural_errors.append(
            f"{report.duplicate_timestamps} duplicate timestamp(s)"
        )
    if report.unordered_pairs:
        report.structural_errors.append(
            f"{report.unordered_pairs} unordered timestamp pair(s)"
        )
    if report.non_utc_timestamps:
        report.structural_errors.append(
            f"{report.non_utc_timestamps} non-UTC timestamp(s)"
        )
    if report.non_midnight_timestamps:
        report.structural_errors.append(
            f"{report.non_midnight_timestamps} non-midnight daily timestamp(s)"
        )
    if report.unexpected_boundary_timestamps:
        report.structural_errors.append(
            f"{report.unexpected_boundary_timestamps} unexpected "
            f"{timeframe} candle boundary timestamp(s)"
        )
    if report.invalid_ohlc_rows:
        report.structural_errors.append(
            f"{report.invalid_ohlc_rows} invalid OHLC/volume row(s)"
        )

    first_dt = min(item.observed_at.astimezone(timezone.utc) for item in completed)
    last_dt = max(item.observed_at.astimezone(timezone.utc) for item in completed)
    first = first_dt.date()
    last = last_dt.date()
    report.row_count = len(completed)
    report.first_observation_utc = first_dt.isoformat()
    report.last_observation_utc = last_dt.isoformat()

    gaps = _gap_rows(completed, timeframe)
    report.gap_count = len(gaps)
    report.gap_sample = [
        (
            f"{row['previous_time_utc']} -> {row['next_time_utc']} "
            f"({row['gap_seconds']}s, ~{row['gap_bars']} bars)"
        )
        for row in gaps[:100]
    ]
    if gaps:
        report.warnings.append(
            f"{len(gaps)} gap(s) exceed the normal {timeframe} interval; "
            "they are reported but not classified as holidays or weekends"
        )

    if timeframe == "1d":
        expected_weekdays: set[date] = set()
        cursor = first
        while cursor <= last:
            if cursor.weekday() < 5:
                expected_weekdays.add(cursor)
            cursor += timedelta(days=1)
        observed_dates = {
            item.observed_at.astimezone(timezone.utc).date()
            for item in completed
        }
        missing_weekdays = sorted(expected_weekdays - observed_dates)
        report.weekday_gap_count = len(missing_weekdays)
        report.weekday_gap_sample = [
            item.isoformat() for item in missing_weekdays[:100]
        ]
        if missing_weekdays:
            report.warnings.append(
                "weekday gaps exist; they are not classified as holidays because "
                "the XAUUSD-P session calendar is unverified"
            )

    for previous_result, current_result in zip(chunk_results, chunk_results[1:]):
        if not previous_result.observations or not current_result.observations:
            continue
        previous_last = previous_result.observations[-1].observed_at
        current_first = current_result.observations[0].observed_at
        if current_first <= previous_last:
            report.chunk_boundary_issues.append(
                f"chunk {current_result.plan.index} starts at or before the previous "
                f"chunk's last observation ({previous_last.isoformat()})"
            )

    if repeatability_ok is False:
        report.structural_errors.append("repeatability check failed")
    report.coverage_complete = all(
        result.status in {"ok", "empty"} for result in chunk_results
    ) if chunk_results else False
    report.accepted = bool(completed) and not report.structural_errors
    return tuple(completed), report


def _new_run_dir(output_root: Path, now: datetime) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    base = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = output_root / base
    suffix = 1
    while candidate.exists():
        candidate = output_root / f"{base}-{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def write_dataset(
    *,
    output_root: Path,
    observations: Sequence[MarketObservation],
    chunk_results: Sequence[ChunkResult],
    validation: ValidationReport,
    requested_start: datetime,
    requested_end: datetime,
    acquired_at: datetime,
    chunk_days: int,
    timeframe: str = TIMEFRAME,
    cost_rows: Sequence[dict[str, Any]] | None = None,
    cost_error: str = "",
    instrument_metadata: dict[str, Any] | None = None,
    instrument_metadata_error: str = "",
    symbol_identity_note: str = "UNVERIFIED_GOLD; not GC/MGC or COMEX futures",
) -> Path:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    run_dir = _new_run_dir(output_root, acquired_at)
    csv_bytes = _canonical_csv_bytes(observations)
    checksum = hashlib.sha256(csv_bytes).hexdigest()
    gap_rows = _gap_rows(observations, timeframe)

    if validation.accepted:
        (run_dir / "candles.csv").write_bytes(csv_bytes)

    cost_checksum: str | None = None
    if validation.accepted and cost_rows is not None:
        cost_buffer = io.StringIO(newline="")
        cost_writer = csv.writer(cost_buffer, lineterminator="\n")
        cost_writer.writerow(COST_COLUMNS)
        for row in cost_rows:
            cost_writer.writerow(
                (
                    str(row["time_utc"]),
                    str(int(row["spread_points"])),
                    repr(float(row["real_volume"])),
                    repr(float(row["tick_volume"])),
                )
            )
        cost_bytes = cost_buffer.getvalue().encode("utf-8")
        (run_dir / "spread_volume.csv").write_bytes(cost_bytes)
        cost_checksum = hashlib.sha256(cost_bytes).hexdigest()

    if gap_rows:
        gap_buffer = io.StringIO(newline="")
        gap_writer = csv.DictWriter(
            gap_buffer,
            fieldnames=(
                "previous_time_utc",
                "next_time_utc",
                "gap_seconds",
                "gap_bars",
            ),
            lineterminator="\n",
        )
        gap_writer.writeheader()
        gap_writer.writerows(gap_rows)
        (run_dir / "gaps.csv").write_text(
            gap_buffer.getvalue(),
            encoding="utf-8",
        )

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "timeframe_minutes": TIMEFRAME_MINUTES[timeframe],
        "symbol_identity": symbol_identity_note,
        "volume_semantics": "MT5 tick_volume; real_volume is recorded only in spread_volume.csv",
        "requested_start_utc": requested_start.astimezone(timezone.utc).isoformat(),
        "requested_end_utc": requested_end.astimezone(timezone.utc).isoformat(),
        "acquired_at_utc": acquired_at.astimezone(timezone.utc).isoformat(),
        "incomplete_candle_exclusion_boundary_utc": acquired_at.astimezone(timezone.utc).isoformat(),
        "chunk_days": chunk_days,
        "requested_chunks": len(chunk_results),
        "row_count": validation.row_count,
        "first_observation_utc": validation.first_observation_utc,
        "last_observation_utc": validation.last_observation_utc,
        "gap_count": validation.gap_count,
        "checksum_sha256": checksum,
        "checksum_algorithm": "sha256",
        "checksum_scope": "canonical candles.csv bytes",
        "columns": list(CSV_COLUMNS),
        "cost_columns": list(COST_COLUMNS),
        "cost_checksum_sha256": cost_checksum,
        "cost_error": cost_error,
        "instrument_metadata": instrument_metadata or {},
        "instrument_metadata_error": instrument_metadata_error,
        "chunks": [result.summary() for result in chunk_results],
        "failures": [
            result.summary()
            for result in chunk_results
            if result.status in {"failed", "timeout"}
        ],
    }
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "validation.json").write_text(
        json.dumps(validation.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run_dir


def _read_csv(
    dataset_dir: Path,
    timeframe: str = TIMEFRAME,
) -> tuple[MarketObservation, ...]:
    path = dataset_dir / "candles.csv"
    if not path.exists():
        raise ValueError(f"dataset has no candles.csv: {path}")
    observations: list[MarketObservation] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
            raise ValueError("dataset CSV columns do not match the expected schema")
        for index, row in enumerate(reader):
            try:
                observations.append(
                    MarketObservation(
                        symbol=SYMBOL,
                        timeframe=timeframe,
                        observed_at=_as_utc(datetime.fromisoformat(row["time_utc"])),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=None if not row["volume"] else float(row["volume"]),
                    )
                )
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"invalid CSV row {index + 1}: {exc}") from exc
    return tuple(observations)


def audit_saved_dataset(
    dataset_dir: Path,
    *,
    now: datetime,
    refetch: FetchChunk | None = None,
) -> dict[str, Any]:
    dataset_dir = dataset_dir.resolve()
    metadata_path = dataset_dir / "metadata.json"
    if not metadata_path.exists():
        raise ValueError(f"dataset has no metadata.json: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("symbol") != SYMBOL:
        raise ValueError(
            f"dataset symbol is {metadata.get('symbol')!r}, expected {SYMBOL!r}"
        )
    timeframe = str(metadata.get("timeframe", ""))
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"dataset timeframe is unsupported: {timeframe!r}")
    csv_bytes = (dataset_dir / "candles.csv").read_bytes()
    checksum = hashlib.sha256(csv_bytes).hexdigest()
    if checksum != metadata.get("checksum_sha256"):
        raise ValueError("dataset checksum does not match metadata.json")
    observations = _read_csv(dataset_dir, timeframe)

    chunk_results: list[ChunkResult] = []
    for chunk in metadata.get("chunks", []):
        try:
            chunk_results.append(
                ChunkResult(
                    plan=ChunkPlan(
                        index=int(chunk["index"]),
                        start=datetime.fromisoformat(chunk["requested_start_utc"]),
                        end=datetime.fromisoformat(chunk["requested_end_utc"]),
                    ),
                    status=str(chunk["status"]),
                    attempts=int(chunk.get("attempts", 0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    completed, report = validate_observations(
        observations,
        now=now,
        timeframe=timeframe,
        chunk_results=tuple(chunk_results),
    )
    result: dict[str, Any] = {
        "dataset_dir": str(dataset_dir),
        "checksum_sha256": checksum,
        "checksum_ok": True,
        "row_count": len(observations),
        "validation": report.as_dict(),
    }
    if refetch is not None:
        chunks = metadata.get("chunks", [])
        changes: list[str] = []
        new_rows = 0
        for chunk in chunks:
            if chunk.get("status") not in {"ok", "empty"}:
                continue
            plan = ChunkPlan(
                index=int(chunk["index"]),
                start=datetime.fromisoformat(chunk["requested_start_utc"]),
                end=datetime.fromisoformat(chunk["requested_end_utc"]),
            )
            fresh = tuple(refetch(plan))
            saved_by_time = {
                item.observed_at: item
                for item in observations
                if plan.start <= item.observed_at <= plan.end
            }
            fresh_by_time = {item.observed_at: item for item in fresh}
            for observed_at, saved_observation in saved_by_time.items():
                fresh_observation = fresh_by_time.get(observed_at)
                if fresh_observation is None:
                    changes.append(
                        f"chunk {plan.index}: saved bar {observed_at.isoformat()} "
                        "is missing from refetch"
                    )
                elif fresh_observation != saved_observation:
                    changes.append(
                        f"chunk {plan.index}: saved bar {observed_at.isoformat()} "
                        "changed on refetch"
                    )
            new_rows += len(set(fresh_by_time) - set(saved_by_time))
        result["refetch_new_rows"] = new_rows
        result["refetch_changes"] = changes
        result["refetch_ok"] = not changes
    return result


def _resolve_terminal_path(value: str | None) -> str:
    if value:
        return value
    environment = os.environ.get("MT5_TERMINAL_PATH")
    if environment:
        return environment
    for candidate in DEFAULT_TERMINAL_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return str(DEFAULT_TERMINAL_CANDIDATES[0])


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, received {value!r}"
        ) from None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire and audit read-only XAUUSD-P history."
    )
    parser.add_argument(
        "--timeframe",
        choices=tuple(TIMEFRAME_MINUTES),
        default=TIMEFRAME,
        help="Candle timeframe (default: 1d).",
    )
    parser.add_argument(
        "--start",
        type=_parse_date,
        default=None,
        help="First calendar date to request (timeframe-specific default).",
    )
    parser.add_argument(
        "--end",
        type=_parse_date,
        default=None,
        help="Last calendar date to request (default: latest completed day).",
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=None,
        help="Calendar days per request chunk (timeframe-specific default).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Retries for failed chunks only (default: {DEFAULT_RETRIES}).",
    )
    parser.add_argument(
        "--chunk-timeout",
        type=float,
        default=DEFAULT_CHUNK_TIMEOUT_SECONDS,
        help="Seconds before a chunk worker is terminated.",
    )
    parser.add_argument(
        "--terminal-path",
        default=None,
        help="Path to terminal64.exe. Defaults to MT5_TERMINAL_PATH or a known path.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for immutable run directories.",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=None,
        help="Audit an existing dataset directory instead of acquiring data.",
    )
    parser.add_argument(
        "--refetch",
        action="store_true",
        help="With --audit, re-fetch saved chunks and compare them.",
    )
    parser.add_argument(
        "--no-repeat-check",
        action="store_true",
        help="Skip the post-acquisition repeatability request.",
    )
    parser.add_argument(
        "--include-costs",
        action="store_true",
        help="Also save raw spread/real_volume fields in spread_volume.csv.",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--costs-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--metadata-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-start", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-end", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--worker-timeout-ms",
        type=int,
        default=DEFAULT_WORKER_TIMEOUT_MS,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.chunk_days is not None and args.chunk_days <= 0:
        parser.error("--chunk-days must be positive")
    if args.retries < 0:
        parser.error("--retries must be non-negative")
    if args.chunk_timeout <= 0:
        parser.error("--chunk-timeout must be positive")
    return args


def _print_summary(run_dir: Path, metadata: dict[str, Any], validation: ValidationReport) -> None:
    print(f"dataset_dir={run_dir}")
    print(f"symbol={metadata['symbol']} timeframe={metadata['timeframe']}")
    print(f"rows={metadata['row_count']}")
    print(f"first={metadata['first_observation_utc']}")
    print(f"last={metadata['last_observation_utc']}")
    print(f"checksum_sha256={metadata['checksum_sha256']}")
    print(f"chunks={metadata['requested_chunks']} failures={len(metadata['failures'])}")
    print(f"accepted={validation.accepted} coverage_complete={validation.coverage_complete}")
    print(f"structural_errors={len(validation.structural_errors)}")
    if validation.structural_errors:
        for error in validation.structural_errors:
            print(f"  error: {error}")
    print(f"warnings={len(validation.warnings)}")
    for warning in validation.warnings:
        print(f"  warning: {warning}")
    print(f"weekday_gaps={validation.weekday_gap_count} gaps={validation.gap_count}")
    if metadata.get("cost_checksum_sha256"):
        print("cost_dataset=spread_volume.csv")
    elif metadata.get("cost_error"):
        print(f"cost_dataset=unavailable ({metadata['cost_error']})")


def _cost_rows_for_dataset(
    *,
    raw_rows: Sequence[dict[str, Any]],
    observations: Sequence[MarketObservation],
) -> list[dict[str, Any]]:
    allowed = {
        observation.observed_at.astimezone(timezone.utc)
        for observation in observations
    }
    result: list[dict[str, Any]] = []
    for row in raw_rows:
        try:
            observed_at = datetime.fromtimestamp(
                int(row["time"]), tz=timezone.utc
            )
            if observed_at not in allowed:
                continue
            result.append(
                {
                    "time_utc": observed_at.isoformat(),
                    "spread_points": int(row["spread_points"]),
                    "real_volume": float(row["real_volume"]),
                    "tick_volume": float(row["tick_volume"]),
                }
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    timeframe = args.timeframe

    if args.worker:
        if not args.worker_start or not args.worker_end:
            raise SystemExit("worker requires --worker-start and --worker-end")
        return _worker_main(
            timeframe=timeframe,
            start=datetime.fromisoformat(args.worker_start),
            end=datetime.fromisoformat(args.worker_end),
            terminal_path=_resolve_terminal_path(args.terminal_path),
            timeout_ms=args.worker_timeout_ms,
        )

    if args.costs_worker:
        if not args.worker_start or not args.worker_end:
            raise SystemExit("cost worker requires --worker-start and --worker-end")
        return _costs_worker_main(
            timeframe=timeframe,
            start=datetime.fromisoformat(args.worker_start),
            end=datetime.fromisoformat(args.worker_end),
            terminal_path=_resolve_terminal_path(args.terminal_path),
            timeout_ms=args.worker_timeout_ms,
        )

    if args.metadata_worker:
        return _metadata_worker_main(
            terminal_path=_resolve_terminal_path(args.terminal_path),
            timeout_ms=args.worker_timeout_ms,
        )

    if args.audit is not None:
        metadata_path = args.audit.resolve() / "metadata.json"
        if not metadata_path.exists():
            raise ValueError(f"dataset has no metadata.json: {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        audit_timeframe = str(metadata.get("timeframe", timeframe))
        refetch: FetchChunk | None = None
        if args.refetch:
            terminal_path = _resolve_terminal_path(args.terminal_path)

            def refetch(plan: ChunkPlan) -> tuple[MarketObservation, ...]:
                return _fetch_chunk_isolated(
                    plan,
                    timeframe=audit_timeframe,
                    terminal_path=terminal_path,
                    timeout_seconds=args.chunk_timeout,
                    worker_timeout_ms=args.worker_timeout_ms,
                )

        result = audit_saved_dataset(
            args.audit,
            now=datetime.now(timezone.utc),
            refetch=refetch,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["validation"]["accepted"] else 2

    now = datetime.now(timezone.utc)
    start_date = args.start or DEFAULT_START_BY_TIMEFRAME[timeframe]
    end_date = args.end or latest_completed_day(now, timeframe=timeframe)
    chunk_days = args.chunk_days or DEFAULT_CHUNK_DAYS_BY_TIMEFRAME[timeframe]
    plans = chunk_plan(start_date, end_date, chunk_days=chunk_days)
    terminal_path = _resolve_terminal_path(args.terminal_path)

    def fetch(plan: ChunkPlan) -> tuple[MarketObservation, ...]:
        return _fetch_chunk_isolated(
            plan,
            timeframe=timeframe,
            terminal_path=terminal_path,
            timeout_seconds=args.chunk_timeout,
            worker_timeout_ms=args.worker_timeout_ms,
        )

    results = acquire_chunks(plans, fetch, retries=args.retries)
    observations = tuple(
        observation
        for result in results
        for observation in result.observations
    )

    repeatability_ok: bool | None = None
    repeatability_error = ""
    interval = _candle_interval(timeframe)

    def completed_only(items: Sequence[MarketObservation]) -> tuple[MarketObservation, ...]:
        return tuple(
            item
            for item in items
            if item.observed_at.astimezone(timezone.utc) + interval <= now
        )

    if not args.no_repeat_check:
        successful = [result for result in results if result.status == "ok"]
        if successful:
            try:
                repeated = completed_only(tuple(fetch(successful[-1].plan)))
            except Exception as exc:
                repeatability_ok = False
                repeatability_error = f"{type(exc).__name__}: {exc}"
            else:
                expected = completed_only(successful[-1].observations)
                repeatability_ok = repeated == expected
                if not repeatability_ok:
                    repeatability_error = "identical chunk request returned different rows"

    completed, validation = validate_observations(
        observations,
        now=now,
        timeframe=timeframe,
        chunk_results=results,
        repeatability_ok=repeatability_ok,
        repeatability_error=repeatability_error,
    )

    instrument_metadata: dict[str, Any] | None = None
    instrument_metadata_error = ""
    try:
        instrument_metadata = _fetch_metadata_isolated(
            terminal_path=terminal_path,
            timeout_seconds=args.chunk_timeout,
            worker_timeout_ms=args.worker_timeout_ms,
        )
    except Exception as exc:
        instrument_metadata_error = f"{type(exc).__name__}: {exc}"

    cost_rows: list[dict[str, Any]] | None = None
    cost_error = ""
    if args.include_costs and validation.accepted:
        try:
            raw_costs = _fetch_costs_isolated(
                timeframe=timeframe,
                start=_date_start(start_date),
                end=_date_end(end_date),
                terminal_path=terminal_path,
                timeout_seconds=args.chunk_timeout,
                worker_timeout_ms=args.worker_timeout_ms,
            )
            cost_rows = _cost_rows_for_dataset(
                raw_rows=raw_costs,
                observations=completed,
            )
            if not cost_rows:
                cost_error = "no cost rows matched completed candles"
        except Exception as exc:
            cost_error = f"{type(exc).__name__}: {exc}"

    run_dir = write_dataset(
        output_root=args.output_root,
        observations=completed,
        chunk_results=results,
        validation=validation,
        requested_start=_date_start(start_date),
        requested_end=_date_end(end_date),
        acquired_at=now,
        chunk_days=chunk_days,
        timeframe=timeframe,
        cost_rows=cost_rows,
        cost_error=cost_error,
        instrument_metadata=instrument_metadata,
        instrument_metadata_error=instrument_metadata_error,
    )
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    _print_summary(run_dir, metadata, validation)
    return 0 if validation.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
