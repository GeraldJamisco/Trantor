"""Broker-independent market data and COMEX gold futures sessions.

Phase 2 provides read-only provider contracts, validated result statuses, a
COMEX GC/MGC session calendar, and local fixture support. Gate 1 adds a
read-only MT5 OHLC provider for an explicitly unverified broker instrument.
This package does not scan markets, evaluate strategies, or place orders.
"""

from .contracts import (
    FreshnessInfo,
    MarketDataRequest,
    MarketDataResult,
    MarketDataStatus,
    ProviderCapabilities,
    ValidationIssue,
    ValidationIssueCode,
)
from .gold import (
    GoldConvention,
    GoldSessionCalendar,
    GoldSessionError,
    GoldSessionSchedule,
    RecurringLocalWindow,
    SessionOverride,
    UnsupportedGoldInstrumentError,
    comex_gold_futures_schedule,
    gold_session_calendar_for,
)
from .mt5_provider import (
    DEFAULT_MT5_TIMEFRAMES,
    MT5MarketDataProvider,
    MT5SymbolMetadata,
    XAUUSD_P_INSTRUMENT,
    mt5_provider_from_settings,
    xauusd_p_instrument,
)
from .providers import (
    InvalidProviderResponseError,
    MarketDataProvider,
    MarketDataProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from .service import MarketDataService
from .sessions import (
    InstrumentScheduleMismatchError,
    SessionCalendar,
    SessionCalendarRangeError,
    SessionError,
    SessionInfo,
    SessionState,
    UnsupportedInstrumentScheduleError,
    UnverifiedScheduleError,
)

__all__ = [
    "DEFAULT_MT5_TIMEFRAMES",
    "FreshnessInfo",
    "GoldConvention",
    "GoldSessionCalendar",
    "GoldSessionError",
    "GoldSessionSchedule",
    "InstrumentScheduleMismatchError",
    "InvalidProviderResponseError",
    "MT5MarketDataProvider",
    "MT5SymbolMetadata",
    "MarketDataProvider",
    "MarketDataProviderError",
    "MarketDataRequest",
    "MarketDataResult",
    "MarketDataService",
    "MarketDataStatus",
    "ProviderCapabilities",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RecurringLocalWindow",
    "SessionCalendar",
    "SessionOverride",
    "SessionCalendarRangeError",
    "SessionError",
    "SessionInfo",
    "SessionState",
    "UnsupportedGoldInstrumentError",
    "UnsupportedInstrumentScheduleError",
    "UnverifiedScheduleError",
    "ValidationIssue",
    "ValidationIssueCode",
    "XAUUSD_P_INSTRUMENT",
    "comex_gold_futures_schedule",
    "gold_session_calendar_for",
    "mt5_provider_from_settings",
    "xauusd_p_instrument",
]
