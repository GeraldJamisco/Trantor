"""Read-only MetaTrader 5 market-data provider.

The provider attaches to an already-running MT5 terminal through the official
``MetaTrader5`` package. It does not log in, read account credentials, or expose
account balances. It only converts broker-provided symbol metadata and OHLC bars
into Trantor's existing market-data models.

The initial instrument is the broker symbol ``XAUUSD-P``. Its product identity
is explicitly unverified: it is represented as
:attr:`trantor.models.AssetClass.UNVERIFIED_GOLD`, not as GC, MGC, or a COMEX
future. No COMEX session calendar is applied to it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import import_module
from math import isfinite
from operator import index as integer_index
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import AssetClass, MarketInstrument, MarketObservation
from .contracts import MarketDataRequest, ProviderCapabilities
from .providers import (
    InvalidProviderResponseError,
    MarketDataProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

if TYPE_CHECKING:
    from ..config import Settings

__all__ = [
    "DEFAULT_MT5_TIMEFRAMES",
    "MT5MarketDataProvider",
    "MT5SymbolMetadata",
    "XAUUSD_P_INSTRUMENT",
    "mt5_provider_from_settings",
    "xauusd_p_instrument",
]

DEFAULT_MT5_TIMEFRAMES = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
)
_TIMEFRAME_CONSTANTS = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
    "1w": "TIMEFRAME_W1",
}


def xauusd_p_instrument() -> MarketInstrument:
    """Return the explicitly configured initial MT5 research instrument.

    ``exchange`` and ``timezone`` are deliberately non-committal labels:
    ``BROKER`` because no exchange was established, and ``UTC`` because MT5
    timestamps are returned in UTC. The asset class is unverified.
    """

    return MarketInstrument(
        symbol="XAUUSD-P",
        asset_class=AssetClass.UNVERIFIED_GOLD,
        exchange="BROKER",
        currency="USD",
        timezone="UTC",
    )


XAUUSD_P_INSTRUMENT = xauusd_p_instrument()


@dataclass(frozen=True, slots=True)
class MT5SymbolMetadata:
    """Typed symbol metadata; never a raw MT5 named tuple."""

    symbol: str
    description: str
    path: str
    exchange: str
    currency_base: str
    currency_profit: str
    currency_margin: str
    contract_size: float | None
    tick_size: float | None
    tick_value: float | None
    point: float | None
    digits: int | None
    spread_points: int | None
    volume_min: float | None
    volume_max: float | None
    volume_step: float | None
    margin_initial: float | None
    margin_maintenance: float | None
    start_time: datetime | None
    expiration_time: datetime | None
    trade_mode: int | None
    execution_mode: int | None
    calc_mode: int | None
    filling_mode: int | None
    order_mode: int | None
    expiration_mode: int | None
    visible: bool | None
    selected: bool | None
    custom: bool | None
    product_identity_verified: bool = field(default=False, init=False)


def _field(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidProviderResponseError(
            f"MT5 symbol metadata field {field_name!r} is not numeric"
        )
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise InvalidProviderResponseError(
            f"MT5 symbol metadata field {field_name!r} is not numeric"
        ) from None
    if not isfinite(number):
        raise InvalidProviderResponseError(
            f"MT5 symbol metadata field {field_name!r} is not finite"
        )
    return number


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidProviderResponseError(
            f"MT5 symbol metadata field {field_name!r} is not an integer"
        )
    try:
        return integer_index(value)
    except TypeError:
        raise InvalidProviderResponseError(
            f"MT5 symbol metadata field {field_name!r} is not an integer"
        ) from None


def _optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise InvalidProviderResponseError(
            f"MT5 symbol metadata field {field_name!r} is not a boolean"
        )
    return value


def _epoch_or_none(value: Any, field_name: str) -> datetime | None:
    if value is None or value == 0:
        return None
    if isinstance(value, bool):
        raise InvalidProviderResponseError(
            f"MT5 symbol metadata field {field_name!r} is not a valid timestamp"
        )
    try:
        return datetime.fromtimestamp(integer_index(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        raise InvalidProviderResponseError(
            f"MT5 symbol metadata field {field_name!r} is not a valid timestamp"
        ) from None


def _metadata_from_info(info: Any, expected_symbol: str) -> MT5SymbolMetadata:
    symbol = _text(_field(info, "name"))
    if not symbol:
        raise InvalidProviderResponseError("MT5 symbol_info() returned no symbol name")
    if symbol != expected_symbol:
        raise InvalidProviderResponseError(
            f"MT5 symbol_info() returned {symbol!r} while {expected_symbol!r} "
            "was requested"
        )

    return MT5SymbolMetadata(
        symbol=symbol,
        description=_text(_field(info, "description")),
        path=_text(_field(info, "path")),
        exchange=_text(_field(info, "exchange")),
        currency_base=_text(_field(info, "currency_base")),
        currency_profit=_text(_field(info, "currency_profit")),
        currency_margin=_text(_field(info, "currency_margin")),
        contract_size=_optional_float(
            _field(info, "trade_contract_size"), "trade_contract_size"
        ),
        tick_size=_optional_float(_field(info, "trade_tick_size"), "trade_tick_size"),
        tick_value=_optional_float(
            _field(info, "trade_tick_value"), "trade_tick_value"
        ),
        point=_optional_float(_field(info, "point"), "point"),
        digits=_optional_int(_field(info, "digits"), "digits"),
        spread_points=_optional_int(_field(info, "spread"), "spread"),
        volume_min=_optional_float(_field(info, "volume_min"), "volume_min"),
        volume_max=_optional_float(_field(info, "volume_max"), "volume_max"),
        volume_step=_optional_float(_field(info, "volume_step"), "volume_step"),
        margin_initial=_optional_float(
            _field(info, "margin_initial"), "margin_initial"
        ),
        margin_maintenance=_optional_float(
            _field(info, "margin_maintenance"), "margin_maintenance"
        ),
        start_time=_epoch_or_none(_field(info, "start_time"), "start_time"),
        expiration_time=_epoch_or_none(
            _field(info, "expiration_time"), "expiration_time"
        ),
        trade_mode=_optional_int(_field(info, "trade_mode"), "trade_mode"),
        execution_mode=_optional_int(
            _field(info, "trade_exemode"), "trade_exemode"
        ),
        calc_mode=_optional_int(
            _field(info, "trade_calc_mode"), "trade_calc_mode"
        ),
        filling_mode=_optional_int(_field(info, "filling_mode"), "filling_mode"),
        order_mode=_optional_int(_field(info, "order_mode"), "order_mode"),
        expiration_mode=_optional_int(
            _field(info, "expiration_mode"), "expiration_mode"
        ),
        visible=_optional_bool(_field(info, "visible"), "visible"),
        selected=_optional_bool(_field(info, "select"), "select"),
        custom=_optional_bool(_field(info, "custom"), "custom"),
    )


def _record_value(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    try:
        return record[name]
    except (KeyError, IndexError, TypeError, ValueError):
        return getattr(record, name, None)


def _record_float(record: Any, name: str, index: int) -> float:
    value = _record_value(record, name)
    if value is None:
        raise ValueError(f"candle {index} is missing {name}")
    if isinstance(value, bool):
        raise ValueError(f"candle {index} has non-numeric {name}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"candle {index} has non-numeric {name}") from None
    if not isfinite(number):
        raise ValueError(f"candle {index} has non-finite {name}")
    return number


def _record_time(record: Any, index: int) -> datetime:
    value = _record_value(record, "time")
    if value is None:
        raise ValueError(f"candle {index} is missing time")
    if isinstance(value, bool):
        raise ValueError(f"candle {index} has invalid time")
    try:
        return datetime.fromtimestamp(integer_index(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        raise ValueError(f"candle {index} has invalid time") from None


class MT5MarketDataProvider:
    """Read-only MT5 OHLC provider for one explicitly configured instrument.

    Use the provider as a context manager, or call :meth:`close` explicitly, to
    release its terminal connection. Construction does not connect.
    """

    def __init__(
        self,
        *,
        terminal_path: str | Path,
        instrument: MarketInstrument = XAUUSD_P_INSTRUMENT,
        timeframes: Iterable[str] = DEFAULT_MT5_TIMEFRAMES,
        latest_bar_count: int = 100,
        terminal_timeout_ms: int = 15_000,
        mt5_module: Any | None = None,
    ) -> None:
        path_text = str(terminal_path).strip()
        if not path_text:
            raise ValueError("terminal_path must be a non-empty path")
        if not isinstance(instrument, MarketInstrument):
            raise TypeError("instrument must be a MarketInstrument")
        if (
            isinstance(latest_bar_count, bool)
            or not isinstance(latest_bar_count, int)
            or latest_bar_count <= 0
        ):
            raise ValueError("latest_bar_count must be a positive integer")
        if (
            isinstance(terminal_timeout_ms, bool)
            or not isinstance(terminal_timeout_ms, int)
            or terminal_timeout_ms <= 0
        ):
            raise ValueError("terminal_timeout_ms must be a positive integer")
        if isinstance(timeframes, str):
            raise ValueError("timeframes must be an iterable of timeframe names")

        selected_timeframes = tuple(dict.fromkeys(timeframes))
        if not selected_timeframes:
            raise ValueError("timeframes must contain at least one timeframe")
        unknown = [
            timeframe
            for timeframe in selected_timeframes
            if timeframe not in _TIMEFRAME_CONSTANTS
        ]
        if unknown:
            raise ValueError(f"unsupported MT5 timeframe(s): {', '.join(unknown)}")

        self._terminal_path = Path(path_text)
        self._instrument = instrument
        self._timeframes = selected_timeframes
        self._latest_bar_count = latest_bar_count
        self._terminal_timeout_ms = terminal_timeout_ms
        self._mt5 = mt5_module
        self._connected = False
        self._metadata_cache: dict[str, MT5SymbolMetadata] = {}

    @property
    def name(self) -> str:
        return "mt5-readonly"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            asset_classes=frozenset({self._instrument.asset_class}),
            timeframes=frozenset(self._timeframes),
            symbols=frozenset({self._instrument.symbol}),
            supports_historical=True,
            notes=(
                "Read-only MetaTrader 5 OHLC provider; no order or account "
                "operations.",
                f"Broker symbol {self._instrument.symbol!r} is treated as a "
                "distinct instrument; its product identity is UNVERIFIED and it "
                "is not treated as GC, MGC, or a COMEX future.",
                "The observation volume field is MT5 tick_volume; MT5 "
                "real_volume is not represented by the existing domain model.",
            ),
        )

    @property
    def instrument(self) -> MarketInstrument:
        return self._instrument

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _resolve_mt5(self) -> Any:
        if self._mt5 is None:
            try:
                self._mt5 = import_module("MetaTrader5")
            except Exception as exc:
                raise ProviderUnavailableError(
                    "MetaTrader5 package is not importable; install the "
                    "official package in this Windows environment"
                ) from exc
        return self._mt5

    def _error_text(self) -> str:
        if self._mt5 is None:
            return "MT5 error information unavailable"
        try:
            error = self._mt5.last_error()
        except Exception as exc:
            return f"MT5 error information unavailable ({type(exc).__name__})"
        if isinstance(error, tuple) and len(error) >= 2:
            return f"code={error[0]} description={error[1]}"
        return f"error={error!r}"

    def connect(self) -> None:
        """Attach to the already-running terminal; never logs in."""

        if self._connected:
            return
        mt5 = self._resolve_mt5()
        try:
            initialized = mt5.initialize(
                str(self._terminal_path),
                timeout=self._terminal_timeout_ms,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"MT5 initialize() timed out: {self._error_text()}"
            ) from exc
        except Exception as exc:
            raise ProviderUnavailableError(
                f"MT5 initialize() failed ({type(exc).__name__}): {exc}; "
                f"{self._error_text()}"
            ) from exc

        if not initialized:
            raise ProviderUnavailableError(
                f"MT5 initialize() failed; {self._error_text()}"
            )

        self._connected = True
        try:
            info = mt5.terminal_info()
        except TimeoutError as exc:
            self._close_after_failed_connect()
            raise ProviderTimeoutError(
                f"MT5 terminal_info() timed out: {self._error_text()}"
            ) from exc
        except Exception as exc:
            self._close_after_failed_connect()
            raise ProviderUnavailableError(
                f"MT5 terminal_info() failed ({type(exc).__name__}): {exc}; "
                f"{self._error_text()}"
            ) from exc
        if info is None:
            self._close_after_failed_connect()
            raise ProviderUnavailableError(
                f"MT5 terminal_info() returned None; {self._error_text()}"
            )
        if not bool(_field(info, "connected")):
            self._close_after_failed_connect()
            raise ProviderUnavailableError(
                "MT5 terminal is not connected to its broker; "
                f"{self._error_text()}"
            )

    def _close_after_failed_connect(self) -> None:
        try:
            self.close()
        except Exception:
            self._connected = False

    def close(self) -> None:
        """Close the MT5 Python connection; safe to call repeatedly."""

        if not self._connected:
            return
        self._connected = False
        self._metadata_cache.clear()
        if self._mt5 is None:
            return
        try:
            self._mt5.shutdown()
        except Exception as exc:
            raise ProviderUnavailableError(
                f"MT5 shutdown() failed ({type(exc).__name__}): {exc}"
            ) from exc

    def __enter__(self) -> "MT5MarketDataProvider":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _timeframe_value(self, timeframe: str) -> Any:
        if timeframe not in self._timeframes:
            raise MarketDataProviderError(
                f"timeframe {timeframe!r} is not enabled for this provider"
            )
        mt5 = self._resolve_mt5()
        constant_name = _TIMEFRAME_CONSTANTS[timeframe]
        value = getattr(mt5, constant_name, None)
        if value is None:
            raise MarketDataProviderError(
                f"MT5 timeframe constant {constant_name!r} is unavailable"
            )
        return value

    def _get_metadata(self, symbol: str) -> MT5SymbolMetadata:
        cached = self._metadata_cache.get(symbol)
        if cached is not None:
            return cached
        mt5 = self._resolve_mt5()
        try:
            info = mt5.symbol_info(symbol)
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"MT5 symbol_info({symbol!r}) timed out: {self._error_text()}"
            ) from exc
        except Exception as exc:
            raise ProviderUnavailableError(
                f"MT5 symbol_info({symbol!r}) failed "
                f"({type(exc).__name__}): {exc}; {self._error_text()}"
            ) from exc
        if info is None:
            raise ProviderUnavailableError(
                f"MT5 symbol {symbol!r} is unavailable; {self._error_text()}"
            )
        metadata = _metadata_from_info(info, symbol)
        self._metadata_cache[symbol] = metadata
        return metadata

    def symbol_metadata(self, symbol: str | None = None) -> MT5SymbolMetadata:
        """Return typed metadata for the configured or exact requested symbol."""

        self.connect()
        selected = self._instrument.symbol if symbol is None else str(symbol)
        if selected != self._instrument.symbol:
            raise MarketDataProviderError(
                f"symbol {selected!r} is outside this provider's configured "
                f"instrument {self._instrument.symbol!r}"
            )
        return self._get_metadata(selected)

    def _rates_for_request(self, request: MarketDataRequest) -> Any:
        mt5 = self._resolve_mt5()
        timeframe = self._timeframe_value(request.timeframe)
        symbol = self._instrument.symbol
        try:
            if request.is_historical:
                if request.start is None or request.end is None:
                    raise ValueError("historical request must include start and end")
                start = request.start.astimezone(timezone.utc)
                end = request.end.astimezone(timezone.utc)
                return mt5.copy_rates_range(symbol, timeframe, start, end)
            return mt5.copy_rates_from_pos(
                symbol,
                timeframe,
                0,
                self._latest_bar_count,
            )
        except TimeoutError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(
                f"MT5 candle request for {symbol!r} failed "
                f"({type(exc).__name__}): {exc}; {self._error_text()}"
            ) from exc

    def _convert_rates(
        self,
        rates: Any,
        request: MarketDataRequest,
    ) -> tuple[MarketObservation, ...]:
        try:
            records = list(rates)
        except TypeError:
            raise InvalidProviderResponseError(
                "MT5 candle response is not iterable"
            ) from None

        observations: list[MarketObservation] = []
        seen_timestamps: set[datetime] = set()
        for index, record in enumerate(records):
            try:
                observed_at = _record_time(record, index)
                if observed_at in seen_timestamps:
                    raise InvalidProviderResponseError(
                        f"MT5 candle response contains duplicate timestamp "
                        f"{observed_at.isoformat()} at index {index}"
                    )
                seen_timestamps.add(observed_at)
                volume_value = _record_value(record, "tick_volume")
                volume = (
                    None
                    if volume_value is None
                    else _record_float(record, "tick_volume", index)
                )
                observation = MarketObservation(
                    symbol=self._instrument.symbol,
                    timeframe=request.timeframe,
                    observed_at=observed_at,
                    open=_record_float(record, "open", index),
                    high=_record_float(record, "high", index),
                    low=_record_float(record, "low", index),
                    close=_record_float(record, "close", index),
                    volume=volume,
                )
            except InvalidProviderResponseError:
                raise
            except (TypeError, ValueError, OverflowError) as exc:
                raise InvalidProviderResponseError(
                    f"malformed MT5 candle at index {index}: {exc}"
                ) from exc
            observations.append(observation)

        observations.sort(key=lambda observation: observation.observed_at)
        return tuple(observations)

    def fetch_observations(
        self, request: MarketDataRequest
    ) -> tuple[MarketObservation, ...]:
        """Return exact-symbol MT5 OHLC observations or raise explicitly."""

        if not isinstance(request, MarketDataRequest):
            raise TypeError("request must be a MarketDataRequest")
        if request.instrument != self._instrument:
            raise MarketDataProviderError(
                f"request instrument {request.instrument.symbol!r} does not "
                f"match this provider's configured instrument "
                f"{self._instrument.symbol!r}"
            )

        self.connect()
        self._get_metadata(self._instrument.symbol)
        rates = self._rates_for_request(request)
        if rates is None:
            raise ProviderUnavailableError(
                f"MT5 returned no candle response for {self._instrument.symbol!r}; "
                f"{self._error_text()}"
            )
        return self._convert_rates(rates, request)


def mt5_provider_from_settings(settings: "Settings") -> MT5MarketDataProvider:
    """Build the explicitly configured MT5 provider without connecting.

    ``Settings`` is intentionally imported only for static typing. At runtime
    this function reads the validated ``market_data_provider`` and
    ``mt5_terminal_path`` fields, so the market-data package does not acquire a
    runtime dependency on the configuration layer.
    """

    provider_name = getattr(settings, "market_data_provider", None)
    if provider_name != "mt5":
        raise MarketDataProviderError(
            "market_data_provider is not set to 'mt5'; "
            f"received {provider_name!r}"
        )
    terminal_path = getattr(settings, "mt5_terminal_path", None)
    if not isinstance(terminal_path, str) or not terminal_path.strip():
        raise MarketDataProviderError(
            "mt5_terminal_path must be configured for the MT5 provider"
        )
    return MT5MarketDataProvider(
        terminal_path=terminal_path,
        instrument=XAUUSD_P_INSTRUMENT,
    )
