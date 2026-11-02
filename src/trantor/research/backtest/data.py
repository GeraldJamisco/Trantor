"""Read-only data loading and dataset identity for the Phase E research backtester.

The loader never rewrites source CSV files or manifests. It validates the
stored identity, checksum, row count, ordering, timeframe, and OHLC schema before
exposing immutable ``MarketObservation`` tuples.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trantor.models import MarketObservation

__all__ = [
    "DEFAULT_DATA_ROOT",
    "DatasetIdentity",
    "DatasetSpec",
    "LoadedDataset",
    "ResearchData",
    "ResearchDataError",
    "default_dataset_specs",
    "load_dataset",
    "load_research_data",
]

SYMBOL = "XAUUSD-P"
UTC = timezone.utc
_TIMEFRAME_DURATION = {
    "1d": timedelta(days=1),
    "4h": timedelta(hours=4),
    "15m": timedelta(minutes=15),
}

# src/trantor/research/backtest/data.py -> repository root
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATA_ROOT = _REPO_ROOT / "research_data" / "xauusd_p" / "runs"

DEFAULT_D1_RUN = DEFAULT_DATA_ROOT / "20261022T205633Z"
DEFAULT_H4_RUN = DEFAULT_DATA_ROOT / "20261022T205633Z-01"
DEFAULT_M15_RUN = DEFAULT_DATA_ROOT / "20261022T205651Z"
DEFAULT_D1_WARMUP_RUN = DEFAULT_DATA_ROOT / "20261022T194021Z"

DEFAULT_CHECKSUMS = {
    "d1": "353a2452d01248e12973d6c17f93de6c3f7979aa089b7783d36d048182864947",
    "h4": "70558ae65a4ce9869129f44e32c0fac2378f5b07bedd8dac812a337fd3a84fa4",
    "m15": "4b10429e2ad681bed78c65f66a1858604bea2388af8354276200515768d31535",
    "d1_warmup": "3847390c3cdde0c98ea22bcab4d099cbab4ef17a8d73eb27c2c502819b0299d6",
}
DEFAULT_ROWS = {"d1": 725, "h4": 4352, "m15": 66475, "d1_warmup": 4331}


class ResearchDataError(ValueError):
    """The research dataset does not satisfy the expected identity/schema."""


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    path: Path
    timeframe: str
    expected_checksum: str | None = None
    expected_rows: int | None = None
    label: str = ""

    def __post_init__(self) -> None:
        if self.timeframe not in _TIMEFRAME_DURATION:
            raise ValueError(f"unsupported timeframe {self.timeframe!r}")
        if not isinstance(self.path, Path):
            raise TypeError("dataset path must be a pathlib.Path")


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    label: str
    path: str
    timeframe: str
    checksum_sha256: str
    row_count: int
    first_observation: datetime
    last_observation: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "path": self.path,
            "timeframe": self.timeframe,
            "checksum_sha256": self.checksum_sha256,
            "row_count": self.row_count,
            "first_observation": self.first_observation.isoformat(),
            "last_observation": self.last_observation.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class LoadedDataset:
    identity: DatasetIdentity
    bars: tuple[MarketObservation, ...]
    close_times: tuple[datetime, ...]


@dataclass(frozen=True, slots=True)
class ResearchData:
    d1: tuple[MarketObservation, ...]
    h4: tuple[MarketObservation, ...]
    m15: tuple[MarketObservation, ...]
    d1_close_times: tuple[datetime, ...]
    h4_close_times: tuple[datetime, ...]
    m15_close_times: tuple[datetime, ...]
    sources: tuple[DatasetIdentity, ...]
    d1_final_source: DatasetIdentity
    d1_warmup_source: DatasetIdentity

    def manifest_sources(self) -> list[dict[str, object]]:
        return [source.as_dict() for source in self.sources]


def default_dataset_specs() -> tuple[DatasetSpec, DatasetSpec, DatasetSpec, DatasetSpec]:
    return (
        DatasetSpec(
            DEFAULT_D1_RUN,
            "1d",
            DEFAULT_CHECKSUMS["d1"],
            DEFAULT_ROWS["d1"],
            "d1_final",
        ),
        DatasetSpec(
            DEFAULT_H4_RUN,
            "4h",
            DEFAULT_CHECKSUMS["h4"],
            DEFAULT_ROWS["h4"],
            "h4_final",
        ),
        DatasetSpec(
            DEFAULT_M15_RUN,
            "15m",
            DEFAULT_CHECKSUMS["m15"],
            DEFAULT_ROWS["m15"],
            "m15_final",
        ),
        DatasetSpec(
            DEFAULT_D1_WARMUP_RUN,
            "1d",
            DEFAULT_CHECKSUMS["d1_warmup"],
            DEFAULT_ROWS["d1_warmup"],
            "d1_warmup",
        ),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResearchDataError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchDataError(f"{path} must contain a JSON object")
    return value


def _parse_datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ResearchDataError(f"{label} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchDataError(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _is_on_grid(at: datetime, timeframe: str) -> bool:
    if timeframe == "1d":
        return at.hour == 0 and at.minute == 0 and at.second == 0 and at.microsecond == 0
    if timeframe == "4h":
        return at.hour % 4 == 0 and at.minute == 0 and at.second == 0 and at.microsecond == 0
    return at.minute in {0, 15, 30, 45} and at.second == 0 and at.microsecond == 0


def _read_candles(path: Path, timeframe: str) -> tuple[MarketObservation, ...]:
    if not path.is_file():
        raise ResearchDataError(f"candle file does not exist: {path}")
    bars: list[MarketObservation] = []
    previous: datetime | None = None
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"time_utc", "open", "high", "low", "close", "volume"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ResearchDataError(
                    f"{path} must contain columns {sorted(required)}"
                )
            for row_number, row in enumerate(reader, start=2):
                try:
                    at = _parse_datetime(row["time_utc"], f"{path}:{row_number}")
                    if not _is_on_grid(at, timeframe):
                        raise ResearchDataError(
                            f"{path}:{row_number} is not on the {timeframe} grid"
                        )
                    if previous is not None and at <= previous:
                        raise ResearchDataError(
                            f"{path}:{row_number} timestamps are not strictly increasing"
                        )
                    volume_text = row["volume"]
                    volume = float(volume_text) if volume_text not in (None, "") else None
                    bar = MarketObservation(
                        symbol=SYMBOL,
                        timeframe=timeframe,
                        observed_at=at,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=volume,
                    )
                except (TypeError, ValueError) as exc:
                    raise ResearchDataError(
                        f"invalid candle at {path}:{row_number}: {exc}"
                    ) from exc
                bars.append(bar)
                previous = at
    except OSError as exc:
        raise ResearchDataError(f"cannot read {path}: {exc}") from exc
    return tuple(bars)


def load_dataset(spec: DatasetSpec) -> LoadedDataset:
    """Load and validate one immutable CSV dataset directory."""

    directory = spec.path
    metadata_path = directory / "metadata.json"
    candles_path = directory / "candles.csv"
    metadata = _load_json(metadata_path)
    if metadata.get("symbol") != SYMBOL:
        raise ResearchDataError(f"{metadata_path}: unexpected symbol")
    if metadata.get("timeframe") != spec.timeframe:
        raise ResearchDataError(f"{metadata_path}: unexpected timeframe")

    checksum = _sha256(candles_path)
    if metadata.get("checksum_sha256") != checksum:
        raise ResearchDataError(f"{metadata_path}: checksum does not match candles.csv")
    if spec.expected_checksum is not None and checksum != spec.expected_checksum:
        raise ResearchDataError(f"{candles_path}: checksum does not match expected identity")
    row_count = metadata.get("row_count")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        raise ResearchDataError(f"{metadata_path}: invalid row_count")
    if spec.expected_rows is not None and row_count != spec.expected_rows:
        raise ResearchDataError(f"{candles_path}: row count does not match expected identity")

    validation_path = directory / "validation.json"
    if validation_path.is_file():
        validation = _load_json(validation_path)
        if validation.get("accepted") is not True:
            raise ResearchDataError(f"{validation_path}: dataset was not accepted")
        if validation.get("row_count") != row_count:
            raise ResearchDataError(f"{validation_path}: row_count mismatch")

    bars = _read_candles(candles_path, spec.timeframe)
    if len(bars) != row_count:
        raise ResearchDataError(f"{candles_path}: row count differs from metadata")
    if not bars:
        raise ResearchDataError(f"{candles_path}: no candles")
    first_metadata = metadata.get("first_observation_utc")
    last_metadata = metadata.get("last_observation_utc")
    if first_metadata is not None and _parse_datetime(str(first_metadata), "first_observation_utc") != bars[0].observed_at:
        raise ResearchDataError(f"{metadata_path}: first observation mismatch")
    if last_metadata is not None and _parse_datetime(str(last_metadata), "last_observation_utc") != bars[-1].observed_at:
        raise ResearchDataError(f"{metadata_path}: last observation mismatch")

    identity = DatasetIdentity(
        label=spec.label or spec.timeframe,
        path=str(candles_path),
        timeframe=spec.timeframe,
        checksum_sha256=checksum,
        row_count=len(bars),
        first_observation=bars[0].observed_at,
        last_observation=bars[-1].observed_at,
    )
    close_times = tuple(
        bar.observed_at + _TIMEFRAME_DURATION[spec.timeframe] for bar in bars
    )
    return LoadedDataset(identity=identity, bars=bars, close_times=close_times)


def _reconcile_d1(
    warmup: LoadedDataset,
    final: LoadedDataset,
) -> tuple[MarketObservation, ...]:
    warmup_by_time = {bar.observed_at: bar for bar in warmup.bars}
    for final_bar in final.bars:
        warmup_bar = warmup_by_time.get(final_bar.observed_at)
        if warmup_bar is None:
            raise ResearchDataError(
                "authoritative D1 row is missing from the long warm-up dataset"
            )
        if (
            warmup_bar.open != final_bar.open
            or warmup_bar.high != final_bar.high
            or warmup_bar.low != final_bar.low
            or warmup_bar.close != final_bar.close
            or warmup_bar.volume != final_bar.volume
        ):
            raise ResearchDataError(
                f"D1 overlap mismatch at {final_bar.observed_at.isoformat()}"
            )

    first_final = final.bars[0].observed_at
    pre_final = tuple(
        bar for bar in warmup.bars if bar.observed_at < first_final
    )
    combined = pre_final + final.bars
    previous: datetime | None = None
    for bar in combined:
        if previous is not None and bar.observed_at <= previous:
            raise ResearchDataError("combined D1 data is not strictly ordered")
        previous = bar.observed_at
    return combined


def load_research_data(
    specs: tuple[DatasetSpec, DatasetSpec, DatasetSpec, DatasetSpec] | None = None,
) -> ResearchData:
    """Load final D1/H4/M15 plus long D1 warm-up and reconcile overlap."""

    d1_spec, h4_spec, m15_spec, warmup_spec = specs or default_dataset_specs()
    d1 = load_dataset(d1_spec)
    h4 = load_dataset(h4_spec)
    m15 = load_dataset(m15_spec)
    warmup = load_dataset(warmup_spec)
    if d1_spec.timeframe != "1d" or warmup_spec.timeframe != "1d":
        raise ResearchDataError("D1 and warm-up specs must use 1d")
    combined_d1 = _reconcile_d1(warmup, d1)
    d1_close_times = tuple(
        bar.observed_at + timedelta(days=1) for bar in combined_d1
    )
    return ResearchData(
        d1=combined_d1,
        h4=h4.bars,
        m15=m15.bars,
        d1_close_times=d1_close_times,
        h4_close_times=h4.close_times,
        m15_close_times=m15.close_times,
        sources=(warmup.identity, d1.identity, h4.identity, m15.identity),
        d1_final_source=d1.identity,
        d1_warmup_source=warmup.identity,
    )
