from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from trantor.market_data import (
    InvalidProviderResponseError,
    MT5MarketDataProvider,
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataRequest,
    MarketDataService,
    MarketDataStatus,
    ProviderUnavailableError,
    ValidationIssueCode,
    XAUUSD_P_INSTRUMENT,
    gold_session_calendar_for,
    mt5_provider_from_settings,
)
from trantor.market_data.mt5_provider import MT5SymbolMetadata
from trantor.models import AssetClass, MarketInstrument
from trantor.scanners import EvaluationOutcome, GoldOpportunityAnalyzer
from phase3_helpers import gold_input, gold_strategy

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 22, 14, 30, tzinfo=UTC)
TERMINAL_PATH = r"C:\Fake\MetaTrader 5\terminal64.exe"


class FakeMT5:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240
    TIMEFRAME_D1 = 1_440
    TIMEFRAME_W1 = 10_080

    def __init__(self) -> None:
        self.initialize_result = True
        self.initialize_exception: Exception | None = None
        self.terminal_info_result: object | None = SimpleNamespace(connected=True)
        self.symbol_info_result: object | None = self._metadata()
        self.range_rates: object = ()
        self.pos_rates: object = ()
        self.range_exception: Exception | None = None
        self.pos_exception: Exception | None = None
        self.shutdown_exception: Exception | None = None
        self.last_error_result: tuple[object, object] = (-4, "Terminal: Not found")
        self.initialize_calls: list[tuple[str, int]] = []
        self.symbol_info_calls: list[str] = []
        self.range_calls: list[tuple[object, ...]] = []
        self.pos_calls: list[tuple[object, ...]] = []
        self.shutdown_calls = 0

    @staticmethod
    def _metadata() -> SimpleNamespace:
        return SimpleNamespace(
            name="XAUUSD-P",
            description="Gold vs USD",
            path=r"Metal-P\XAUUSD-P",
            exchange="",
            currency_base="USD",
            currency_profit="USD",
            currency_margin="USD",
            trade_contract_size=100.0,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            point=0.01,
            digits=2,
            spread=18,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
            margin_initial=0.0,
            margin_maintenance=0.0,
            start_time=0,
            expiration_time=0,
            trade_mode=4,
            trade_exemode=2,
            trade_calc_mode=4,
            filling_mode=3,
            order_mode=63,
            expiration_mode=15,
            visible=True,
            select=True,
            custom=False,
        )

    def initialize(self, path: str, timeout: int = 60_000) -> bool:
        self.initialize_calls.append((path, timeout))
        if self.initialize_exception is not None:
            raise self.initialize_exception
        return self.initialize_result

    def terminal_info(self) -> object | None:
        return self.terminal_info_result

    def symbol_info(self, symbol: str) -> object | None:
        self.symbol_info_calls.append(symbol)
        return self.symbol_info_result

    def copy_rates_range(
        self,
        symbol: str,
        timeframe: object,
        start: datetime,
        end: datetime,
    ) -> object:
        self.range_calls.append((symbol, timeframe, start, end))
        if self.range_exception is not None:
            raise self.range_exception
        return self.range_rates

    def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: object,
        start_pos: int,
        count: int,
    ) -> object:
        self.pos_calls.append((symbol, timeframe, start_pos, count))
        if self.pos_exception is not None:
            raise self.pos_exception
        return self.pos_rates

    def last_error(self) -> tuple[object, object]:
        return self.last_error_result

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self.shutdown_exception is not None:
            raise self.shutdown_exception


def _candle(
    observed_at: datetime,
    *,
    open_price: float = 100.0,
    high: float = 102.0,
    low: float = 99.0,
    close: float = 101.0,
    tick_volume: float = 25.0,
) -> dict[str, object]:
    return {
        "time": int(observed_at.timestamp()),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": tick_volume,
        "spread": 18,
        "real_volume": 0.0,
    }


def _request(
    *,
    timeframe: str = "1h",
    start: datetime | None = None,
    end: datetime | None = None,
    instrument: MarketInstrument = XAUUSD_P_INSTRUMENT,
) -> MarketDataRequest:
    return MarketDataRequest(
        instrument=instrument,
        timeframe=timeframe,
        start=start,
        end=end,
    )


def _historical_request(*, timeframe: str = "1h") -> MarketDataRequest:
    return _request(
        timeframe=timeframe,
        start=NOW - timedelta(hours=3),
        end=NOW,
    )


def _provider(fake: FakeMT5, **overrides: object) -> MT5MarketDataProvider:
    values: dict[str, object] = {
        "terminal_path": TERMINAL_PATH,
        "instrument": XAUUSD_P_INSTRUMENT,
        "timeframes": ("1h", "1d"),
        "latest_bar_count": 2,
        "terminal_timeout_ms": 5_000,
        "mt5_module": fake,
    }
    values.update(overrides)
    return MT5MarketDataProvider(**values)  # type: ignore[arg-type]


def _service(fake: FakeMT5, **overrides: object) -> MarketDataService:
    return MarketDataService(_provider(fake, **overrides))


def test_provider_satisfies_protocol_and_declares_unverified_scope() -> None:
    provider = _provider(FakeMT5())

    assert isinstance(provider, MarketDataProvider)
    assert provider.name == "mt5-readonly"
    assert provider.capabilities.asset_classes == frozenset(
        {AssetClass.UNVERIFIED_GOLD}
    )
    assert provider.capabilities.symbols == frozenset({"XAUUSD-P"})
    assert provider.capabilities.supports_historical is True
    assert any("UNVERIFIED" in note for note in provider.capabilities.notes)


def test_settings_factory_selects_mt5_without_connecting() -> None:
    settings = SimpleNamespace(
        market_data_provider="mt5",
        mt5_terminal_path=TERMINAL_PATH,
    )

    provider = mt5_provider_from_settings(settings)

    assert isinstance(provider, MT5MarketDataProvider)
    assert provider.instrument == XAUUSD_P_INSTRUMENT
    assert provider.is_connected is False


def test_settings_factory_rejects_disabled_provider() -> None:
    settings = SimpleNamespace(
        market_data_provider="disabled",
        mt5_terminal_path=None,
    )

    with pytest.raises(MarketDataProviderError, match="not set to 'mt5'"):
        mt5_provider_from_settings(settings)


def test_symbol_metadata_is_typed_and_marks_identity_unverified() -> None:
    fake = FakeMT5()
    provider = _provider(fake)

    with provider:
        metadata = provider.symbol_metadata()

    assert isinstance(metadata, MT5SymbolMetadata)
    assert metadata.symbol == "XAUUSD-P"
    assert metadata.description == "Gold vs USD"
    assert metadata.contract_size == 100.0
    assert metadata.tick_size == 0.01
    assert metadata.tick_value == 1.0
    assert metadata.start_time is None
    assert metadata.expiration_time is None
    assert metadata.product_identity_verified is False
    assert fake.shutdown_calls == 1


def test_valid_candles_are_converted_and_service_validates_them() -> None:
    fake = FakeMT5()
    fake.range_rates = (
        _candle(NOW - timedelta(hours=1)),
        _candle(NOW - timedelta(minutes=30)),
    )
    request = _historical_request()

    result = _service(fake).get_observations(
        request,
        now=NOW,
        max_age=timedelta(hours=2),
    )

    assert result.status is MarketDataStatus.OK
    assert result.provider == "mt5-readonly"
    assert len(result.observations) == 2
    assert result.observations[0].symbol == "XAUUSD-P"
    assert result.observations[0].timeframe == "1h"
    assert result.observations[0].observed_at.tzinfo is UTC
    assert result.observations[0].volume == 25.0
    assert result.has_current_data is True


def test_request_and_observations_are_normalized_to_utc() -> None:
    fake = FakeMT5()
    fake.range_rates = (_candle(NOW - timedelta(hours=1)),)
    local_start = (NOW - timedelta(hours=3)).astimezone(NEW_YORK)
    local_end = NOW.astimezone(NEW_YORK)

    result = _service(fake).get_observations(
        _request(start=local_start, end=local_end),
        now=NOW,
    )

    assert result.status is MarketDataStatus.OK
    assert fake.range_calls
    _, _, passed_start, passed_end = fake.range_calls[0]
    assert passed_start == NOW - timedelta(hours=3)
    assert passed_end == NOW
    assert passed_start.tzinfo is UTC
    assert passed_end.tzinfo is UTC
    assert result.observations[0].observed_at.tzinfo is UTC


def test_latest_request_uses_bounded_position_request() -> None:
    fake = FakeMT5()
    fake.pos_rates = (_candle(NOW - timedelta(minutes=30)),)

    result = _service(fake).get_observations(_request(), now=NOW)

    assert result.status is MarketDataStatus.OK
    assert fake.pos_calls == [("XAUUSD-P", FakeMT5.TIMEFRAME_H1, 0, 2)]
    assert fake.range_calls == []


def test_empty_history_is_empty_not_a_provider_failure() -> None:
    fake = FakeMT5()
    fake.range_rates = ()

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
        max_age=timedelta(hours=2),
    )

    assert result.status is MarketDataStatus.EMPTY
    assert result.observations == ()
    assert result.issues == ()


def test_missing_symbol_is_explicit_and_not_substituted() -> None:
    fake = FakeMT5()
    fake.symbol_info_result = None
    fake.last_error_result = (-4, "Terminal: Not found")

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.observations == ()
    assert result.issues[0].code is ValidationIssueCode.PROVIDER_ERROR
    assert "XAUUSD-P" in result.message
    assert fake.symbol_info_calls == ["XAUUSD-P"]
    assert fake.range_calls == []


def test_missing_metatrader5_package_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    import trantor.market_data.mt5_provider as mt5_provider_module

    def fail_import(name: str) -> object:
        raise ImportError(f"cannot import {name}")

    monkeypatch.setattr(mt5_provider_module, "import_module", fail_import)
    provider = MT5MarketDataProvider(
        terminal_path=TERMINAL_PATH,
        instrument=XAUUSD_P_INSTRUMENT,
    )

    with pytest.raises(ProviderUnavailableError, match="not importable"):
        provider.connect()


def test_initialization_failure_is_explicit() -> None:
    fake = FakeMT5()
    fake.initialize_result = False
    fake.last_error_result = (-10003, "IPC connect failed")

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.issues[0].code is ValidationIssueCode.PROVIDER_ERROR
    assert "initialize" in result.message.lower()
    assert "-10003" in result.message
    assert fake.range_calls == []


def test_terminal_disconnection_fails_and_shuts_down() -> None:
    fake = FakeMT5()
    fake.terminal_info_result = SimpleNamespace(connected=False)
    fake.last_error_result = (-10004, "IPC connection lost")

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.issues[0].code is ValidationIssueCode.PROVIDER_ERROR
    assert "not connected" in result.message.lower()
    assert fake.shutdown_calls == 1


def test_provider_timeout_is_a_timeout_failure() -> None:
    fake = FakeMT5()
    fake.range_exception = TimeoutError("timed out")

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.issues[0].code is ValidationIssueCode.PROVIDER_TIMEOUT


def test_unavailable_history_is_explicit() -> None:
    fake = FakeMT5()
    fake.range_rates = None
    fake.last_error_result = (-4, "no history")

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.PROVIDER_ERROR
    assert result.issues[0].code is ValidationIssueCode.PROVIDER_ERROR
    assert "no candle response" in result.message


def test_duplicate_candles_invalidate_the_response() -> None:
    fake = FakeMT5()
    duplicate = _candle(NOW - timedelta(hours=1))
    fake.range_rates = (duplicate, dict(duplicate))

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.INVALID
    assert result.observations == ()
    assert result.issues[0].code is ValidationIssueCode.INVALID_PROVIDER_RESPONSE
    assert "duplicate" in result.issues[0].message.lower()


def test_unsorted_candles_are_normalized_to_ascending_order() -> None:
    fake = FakeMT5()
    later = _candle(NOW - timedelta(minutes=30))
    earlier = _candle(NOW - timedelta(hours=1))
    fake.range_rates = (later, earlier)

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.OK
    timestamps = [observation.observed_at for observation in result.observations]
    assert timestamps == sorted(timestamps)


def test_invalid_ohlc_invalidates_the_response() -> None:
    fake = FakeMT5()
    fake.range_rates = (
        _candle(
            NOW - timedelta(hours=1),
            open_price=100.0,
            high=98.0,
            low=99.0,
            close=100.0,
        ),
    )

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.INVALID
    assert result.issues[0].code is ValidationIssueCode.INVALID_PROVIDER_RESPONSE
    assert "malformed" in result.issues[0].message.lower()


def test_non_iterable_history_is_invalid_not_empty() -> None:
    fake = FakeMT5()
    fake.range_rates = 42

    result = _service(fake).get_observations(
        _historical_request(),
        now=NOW,
    )

    assert result.status is MarketDataStatus.INVALID
    assert result.issues[0].code is ValidationIssueCode.INVALID_PROVIDER_RESPONSE


def test_stale_data_remains_stale_after_provider_conversion() -> None:
    fake = FakeMT5()
    fake.range_rates = (_candle(NOW - timedelta(hours=2)),)

    result = _service(fake).get_observations(
        _request(start=NOW - timedelta(hours=4), end=NOW),
        now=NOW,
        max_age=timedelta(minutes=15),
    )

    assert result.status is MarketDataStatus.STALE
    assert result.observations
    assert result.freshness is not None
    assert result.freshness.is_stale is True
    assert result.issues[0].code is ValidationIssueCode.STALE_DATA


def test_clean_context_manager_shutdown_is_idempotent() -> None:
    fake = FakeMT5()
    provider = _provider(fake)

    with provider:
        assert provider.is_connected is True
        provider.symbol_metadata()

    assert provider.is_connected is False
    assert fake.shutdown_calls == 1
    provider.close()
    assert fake.shutdown_calls == 1


def test_shutdown_failure_is_explicit() -> None:
    fake = FakeMT5()
    fake.shutdown_exception = RuntimeError("shutdown failed")
    provider = _provider(fake)
    provider.connect()

    with pytest.raises(Exception, match="shutdown"):
        provider.close()

    assert provider.is_connected is False


def test_no_silent_symbol_substitution_at_service_boundary() -> None:
    fake = FakeMT5()
    other = MarketInstrument(
        symbol="OTHER-GOLD",
        asset_class=AssetClass.UNVERIFIED_GOLD,
        exchange="BROKER",
        currency="USD",
        timezone="UTC",
    )
    provider = _provider(fake)

    result = MarketDataService(provider).get_observations(
        _request(instrument=other),
        now=NOW,
    )

    assert result.status is MarketDataStatus.UNSUPPORTED
    assert provider.is_connected is False
    assert fake.symbol_info_calls == []
    assert result.issues[0].code is ValidationIssueCode.UNSUPPORTED_SYMBOL


def test_no_silent_symbol_substitution_at_provider_boundary() -> None:
    fake = FakeMT5()
    other = MarketInstrument(
        symbol="OTHER-GOLD",
        asset_class=AssetClass.UNVERIFIED_GOLD,
        exchange="BROKER",
        currency="USD",
        timezone="UTC",
    )
    provider = _provider(fake)

    with pytest.raises(MarketDataProviderError, match="does not match"):
        provider.fetch_observations(_request(instrument=other))

    assert fake.initialize_calls == []
    assert fake.symbol_info_calls == []


def test_invalid_provider_response_error_is_not_a_silent_error() -> None:
    fake = FakeMT5()
    fake.range_rates = ({"time": "bad", "open": 1, "high": 2, "low": 1, "close": 2},)

    with _provider(fake) as provider:
        with pytest.raises(InvalidProviderResponseError):
            provider.fetch_observations(_historical_request())

    assert fake.shutdown_calls == 1


def test_unverified_instrument_cannot_use_comex_session_calendar() -> None:
    with pytest.raises(Exception, match="gold futures"):
        gold_session_calendar_for(XAUUSD_P_INSTRUMENT)


def test_unverified_instrument_is_not_eligible_for_gold_strategy() -> None:
    evaluation = GoldOpportunityAnalyzer(gold_strategy()).evaluate(
        gold_input(XAUUSD_P_INSTRUMENT),
        evaluated_at=NOW,
    )

    assert evaluation.outcome is EvaluationOutcome.UNSUPPORTED
    assert "unverified_gold" in evaluation.message
