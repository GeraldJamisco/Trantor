"""Provider protocol and explicit provider-side failure types.

A provider adapter may talk to a read-only data source, such as the MT5 OHLC
provider added in Gate 1. The protocol is intentionally read-only and has no
broker, order, account, or position methods.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..models import MarketObservation
from .contracts import MarketDataRequest, ProviderCapabilities

__all__ = [
    "InvalidProviderResponseError",
    "MarketDataProvider",
    "MarketDataProviderError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
]


class MarketDataProviderError(RuntimeError):
    """Base class for an explicit market-data provider failure."""


class ProviderTimeoutError(MarketDataProviderError):
    """The provider did not return within its time budget."""


class ProviderUnavailableError(MarketDataProviderError):
    """The provider could not be reached or is currently unavailable."""


class InvalidProviderResponseError(MarketDataProviderError):
    """The provider returned a response that cannot be a sequence of observations."""


@runtime_checkable
class MarketDataProvider(Protocol):
    """A broker-independent, read-only source of validated observation objects.

    Providers must declare their capabilities and must raise one of the
    provider exceptions above (or built-in :class:`TimeoutError`) on failure.
    They must not return ``[]`` to represent an API error.
    """

    @property
    def name(self) -> str:
        """Stable provider/source identifier recorded in every result."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Instruments, timeframes, and historical support this adapter offers."""

    def fetch_observations(self, request: MarketDataRequest) -> Sequence[MarketObservation]:
        """Return observations for the request or raise a provider exception."""
