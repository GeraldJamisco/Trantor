"""Small deterministic replay feed for Phase 3 scanner tests.

Replay observations are passed through the Phase 2 :class:`MarketDataService`
before reaching a scanner. The replay files are synthetic fixtures, not evidence
of real-world profitability.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import SUPPORTED_GOLD_FUTURES_SYMBOLS
from ..market_data import (
    MarketDataProviderError,
    MarketDataRequest,
    MarketDataResult,
    MarketDataService,
    ProviderCapabilities,
)
from ..models import AssetClass, MarketInstrument, MarketObservation
from .gold import GoldAnalysisInput, GoldOpportunityAnalyzer
from .models import GoldAnalysisReport

__all__ = [
    "GoldReplayFrame",
    "ReplayError",
    "ReplayProvider",
    "load_replay_frame",
]


class ReplayError(ValueError):
    """A replay fixture is malformed or internally inconsistent."""


_REQUIRED_FRAME_FIELDS = frozenset(
    {"schema_version", "kind", "label", "disclaimer", "evaluated_at", "cases"}
)
_INSTRUMENT_FIELDS = (
    "symbol",
    "asset_class",
    "exchange",
    "currency",
    "timezone",
)
_BAR_FIELDS = frozenset({"observed_at", "open", "high", "low", "close"})
_OPTIONAL_BAR_FIELDS = frozenset({"volume"})


class ReplayProvider:
    """A read-only provider backed by in-memory recorded observations."""

    def __init__(
        self,
        name: str,
        capabilities: ProviderCapabilities,
        responses: Mapping[
            tuple[str, str, bool], Sequence[MarketObservation] | BaseException
        ],
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(capabilities, ProviderCapabilities):
            raise ValueError("capabilities must be ProviderCapabilities")
        if not isinstance(responses, Mapping):
            raise ValueError("responses must be a mapping")
        self._name = name
        self._capabilities = capabilities
        self._responses = dict(responses)

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def fetch_observations(self, request: MarketDataRequest):
        key = (
            request.instrument.symbol,
            request.timeframe,
            request.is_historical,
        )
        try:
            response = self._responses[key]
        except KeyError:
            raise MarketDataProviderError(
                f"replay feed has no response for {key!r}"
            ) from None
        if isinstance(response, BaseException):
            raise response
        return response


@dataclass(frozen=True, slots=True)
class GoldReplayFrame:
    """A labelled synthetic COMEX gold futures replay batch."""

    label: str
    disclaimer: str
    evaluated_at: datetime
    inputs: tuple[GoldAnalysisInput, ...]

    def __post_init__(self) -> None:
        _validate_frame_metadata(self.label, self.disclaimer, self.evaluated_at)
        if not isinstance(self.inputs, tuple) or not self.inputs:
            raise ValueError("inputs must be a non-empty tuple")
        if any(not isinstance(item, GoldAnalysisInput) for item in self.inputs):
            raise ValueError("inputs must contain only GoldAnalysisInput values")

    def run(self, analyzer: GoldOpportunityAnalyzer) -> GoldAnalysisReport:
        if not isinstance(analyzer, GoldOpportunityAnalyzer):
            raise TypeError("analyzer must be a GoldOpportunityAnalyzer")
        report = analyzer.analyze(self.inputs, evaluated_at=self.evaluated_at)
        return replace(
            report,
            notes=report.notes
            + (
                f"Replay fixture {self.label!r}. {self.disclaimer}",
            ),
        )


def _validate_frame_metadata(
    label: str,
    disclaimer: str,
    evaluated_at: datetime,
) -> None:
    if not isinstance(label, str) or not label.strip():
        raise ValueError("label must be a non-empty string")
    if not isinstance(disclaimer, str) or not disclaimer.strip():
        raise ValueError("disclaimer must be a non-empty string")
    if "synthetic" not in disclaimer.lower() and "fixture" not in disclaimer.lower():
        raise ValueError(
            "disclaimer must identify the data as synthetic or fixture data"
        )
    if not isinstance(evaluated_at, datetime):
        raise ValueError("evaluated_at must be a datetime")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")


def _error(source: Path, message: str) -> ReplayError:
    return ReplayError(f"{source}: {message}")


def _mapping(value: Any, source: Path, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(source, f"{field} must be a JSON object")
    return value


def _keys(
    value: Any,
    source: Path,
    field: str,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    mapping = _mapping(value, source, field)
    missing = sorted(required - set(mapping))
    if missing:
        raise _error(source, f"{field} missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(mapping) - required - optional)
    if unknown:
        raise _error(source, f"{field} contains unknown field(s): {', '.join(unknown)}")
    return mapping


def _text(value: Any, source: Path, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(source, f"{field} must be a non-empty string")
    return value


def _datetime(value: Any, source: Path, field: str) -> datetime:
    text = _text(value, source, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise _error(source, f"{field} must be an ISO-8601 datetime") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _error(source, f"{field} must include a timezone offset")
    return parsed


def _instrument(value: Any, source: Path, field: str) -> MarketInstrument:
    mapping = _keys(
        value,
        source,
        field,
        required=frozenset(
            {"symbol", "asset_class", "exchange", "currency", "timezone"}
        ),
    )
    raw_class = _text(mapping["asset_class"], source, f"{field}.asset_class")
    try:
        asset_class = AssetClass(raw_class)
    except ValueError:
        raise _error(
            source,
            f"{field}.asset_class must be a supported asset class",
        ) from None
    return MarketInstrument(
        symbol=_text(mapping["symbol"], source, f"{field}.symbol"),
        asset_class=asset_class,
        exchange=_text(mapping["exchange"], source, f"{field}.exchange"),
        currency=_text(mapping["currency"], source, f"{field}.currency"),
        timezone=_text(mapping["timezone"], source, f"{field}.timezone"),
    )


def _bars(
    value: Any,
    source: Path,
    field: str,
    *,
    symbol: str,
    timeframe: str,
) -> tuple[MarketObservation, ...]:
    if not isinstance(value, list):
        raise _error(source, f"{field} must be a list")
    observations: list[MarketObservation] = []
    for index, raw_bar in enumerate(value):
        bar_field = f"{field}[{index}]"
        bar = _keys(
            raw_bar,
            source,
            bar_field,
            required=_BAR_FIELDS,
            optional=_OPTIONAL_BAR_FIELDS,
        )
        try:
            observations.append(
                MarketObservation(
                    symbol=symbol,
                    timeframe=timeframe,
                    observed_at=_datetime(
                        bar["observed_at"], source, f"{bar_field}.observed_at"
                    ),
                    open=bar["open"],
                    high=bar["high"],
                    low=bar["low"],
                    close=bar["close"],
                    volume=bar.get("volume"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise _error(source, f"{bar_field} is invalid: {exc}") from None
    return tuple(observations)


def _validated_results(
    *,
    source: Path,
    provider_name: str,
    instrument: MarketInstrument,
    previous_close_timeframe: str,
    reference_timeframe: str,
    previous_bars: tuple[MarketObservation, ...],
    reference_bars: tuple[MarketObservation, ...],
    evaluated_at: datetime,
) -> tuple[MarketDataResult, MarketDataResult]:
    historical_start = (
        min(item.observed_at for item in previous_bars) - timedelta(minutes=1)
        if previous_bars
        else evaluated_at - timedelta(days=30)
    )
    previous_request = MarketDataRequest(
        instrument=instrument,
        timeframe=previous_close_timeframe,
        start=historical_start,
        end=evaluated_at,
    )
    reference_request = MarketDataRequest(
        instrument=instrument,
        timeframe=reference_timeframe,
    )
    capabilities = ProviderCapabilities(
        asset_classes=frozenset({instrument.asset_class}),
        timeframes=frozenset(
            {previous_close_timeframe, reference_timeframe}
        ),
        symbols=frozenset({instrument.symbol}),
        supports_historical=True,
    )
    provider = ReplayProvider(
        provider_name,
        capabilities,
        {
            (instrument.symbol, previous_close_timeframe, True): previous_bars,
            (instrument.symbol, reference_timeframe, False): reference_bars,
        },
    )
    service = MarketDataService(provider)
    previous_result = service.get_observations(
        previous_request,
        now=evaluated_at,
    )
    reference_result = service.get_observations(
        reference_request,
        now=evaluated_at,
    )
    return previous_result, reference_result


def _load_gold_cases(
    raw_cases: Any,
    source: Path,
    provider_name: str,
    evaluated_at: datetime,
) -> tuple[GoldAnalysisInput, ...]:
    if not isinstance(raw_cases, list) or not raw_cases:
        raise _error(source, "cases must be a non-empty list")
    inputs: list[GoldAnalysisInput] = []
    for index, raw_case in enumerate(raw_cases):
        field = f"cases[{index}]"
        case = _keys(
            raw_case,
            source,
            field,
            required=frozenset(
                {
                    "symbol",
                    "asset_class",
                    "exchange",
                    "currency",
                    "timezone",
                    "reference_timeframe",
                    "reference",
                }
            ),
            optional=frozenset({"previous_close_timeframe", "previous_close"}),
        )
        instrument = _instrument(
            {name: case[name] for name in _INSTRUMENT_FIELDS},
            source,
            f"{field}.instrument",
        )
        if instrument.asset_class is not AssetClass.GOLD_FUTURES:
            raise _error(
                source,
                f"{field} must use the 'gold_futures' asset class",
            )
        if instrument.symbol not in SUPPORTED_GOLD_FUTURES_SYMBOLS:
            raise _error(
                source,
                f"{field} symbol must be GC or MGC, received {instrument.symbol!r}",
            )
        reference_timeframe = _text(
            case["reference_timeframe"],
            source,
            f"{field}.reference_timeframe",
        )
        reference_bars = _bars(
            case["reference"],
            source,
            f"{field}.reference",
            symbol=instrument.symbol,
            timeframe=reference_timeframe,
        )

        if ("previous_close_timeframe" in case) != (
            "previous_close" in case
        ):
            raise _error(
                source,
                f"{field} must supply previous_close_timeframe and "
                "previous_close together",
            )
        if "previous_close" in case:
            previous_timeframe = _text(
                case["previous_close_timeframe"],
                source,
                f"{field}.previous_close_timeframe",
            )
            previous_result, reference_result = _validated_results(
                source=source,
                provider_name=provider_name,
                instrument=instrument,
                previous_close_timeframe=previous_timeframe,
                reference_timeframe=reference_timeframe,
                previous_bars=_bars(
                    case["previous_close"],
                    source,
                    f"{field}.previous_close",
                    symbol=instrument.symbol,
                    timeframe=previous_timeframe,
                ),
                reference_bars=reference_bars,
                evaluated_at=evaluated_at,
            )
        else:
            reference_request = MarketDataRequest(
                instrument=instrument,
                timeframe=reference_timeframe,
            )
            capabilities = ProviderCapabilities(
                asset_classes=frozenset({instrument.asset_class}),
                timeframes=frozenset({reference_timeframe}),
                symbols=frozenset({instrument.symbol}),
                supports_historical=False,
            )
            provider = ReplayProvider(
                provider_name,
                capabilities,
                {
                    (instrument.symbol, reference_timeframe, False): reference_bars
                },
            )
            reference_result = MarketDataService(provider).get_observations(
                reference_request,
                now=evaluated_at,
            )
            previous_result = None

        inputs.append(
            GoldAnalysisInput(
                instrument=instrument,
                reference_result=reference_result,
                previous_close_result=previous_result,
            )
        )
    return tuple(inputs)


def load_replay_frame(path: str | Path) -> GoldReplayFrame:
    """Load one strict synthetic gold-futures replay frame from JSON."""

    resolved = Path(path).expanduser().resolve()
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _error(resolved, "replay file not found") from None
    except UnicodeDecodeError:
        raise _error(resolved, "replay file is not valid UTF-8") from None
    except OSError as exc:
        raise _error(resolved, f"replay file could not be read: {exc}") from None
    if not text.strip():
        raise _error(resolved, "replay file is empty")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _error(
            resolved,
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
        ) from None

    frame = _keys(
        data,
        resolved,
        "root",
        required=_REQUIRED_FRAME_FIELDS,
        optional=frozenset({"provider_name"}),
    )
    schema_version = frame["schema_version"]
    if type(schema_version) is not int or schema_version != 1:
        raise _error(resolved, "schema_version must be the integer 1")
    kind = _text(frame["kind"], resolved, "kind")
    label = _text(frame["label"], resolved, "label")
    disclaimer = _text(frame["disclaimer"], resolved, "disclaimer")
    if "synthetic" not in disclaimer.lower() and "fixture" not in disclaimer.lower():
        raise _error(
            resolved,
            "disclaimer must identify the data as synthetic or fixture data",
        )
    provider_name = _text(
        frame.get("provider_name", "synthetic-replay-fixture"),
        resolved,
        "provider_name",
    )
    evaluated_at = _datetime(frame["evaluated_at"], resolved, "evaluated_at")

    if kind != "gold_analysis_replay":
        raise _error(
            resolved,
            "kind must be 'gold_analysis_replay'",
        )
    return GoldReplayFrame(
        label=label,
        disclaimer=disclaimer,
        evaluated_at=evaluated_at,
        inputs=_load_gold_cases(
            frame["cases"],
            resolved,
            provider_name,
            evaluated_at,
        ),
    )
