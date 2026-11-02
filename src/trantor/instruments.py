"""Instrument profiles and identity boundaries.

The active Trantor profile is the broker symbol ``XAUUSD-P``. It is deliberately
represented as ``AssetClass.UNVERIFIED_GOLD``: the repository does not establish
that this broker symbol is GC, MGC, spot gold, a CFD, or a COMEX future.

Legacy GC/MGC COMEX components remain separate and must not be mixed with this
profile.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import AssetClass, MarketInstrument
from .market_data.mt5_provider import XAUUSD_P_INSTRUMENT

__all__ = [
    "InstrumentProfile",
    "UnsupportedInstrumentProfileError",
    "XAUUSD_P_PROFILE",
    "profile_for",
]

XAUUSD_P_SESSION_SCOPE = "xauusd_research_unverified"


class UnsupportedInstrumentProfileError(ValueError):
    """The requested instrument is outside the approved active profile."""


@dataclass(frozen=True, slots=True)
class InstrumentProfile:
    """Typed profile for an approved research/execution instrument."""

    instrument: MarketInstrument
    profile_id: str
    session_scope: str
    product_identity_verified: bool
    contract_economics_verified: bool
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, MarketInstrument):
            raise ValueError("instrument must be a MarketInstrument")
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("profile_id must be a non-empty string")
        if not isinstance(self.session_scope, str) or not self.session_scope.strip():
            raise ValueError("session_scope must be a non-empty string")
        if not isinstance(self.product_identity_verified, bool):
            raise ValueError("product_identity_verified must be a boolean")
        if not isinstance(self.contract_economics_verified, bool):
            raise ValueError("contract_economics_verified must be a boolean")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")


XAUUSD_P_PROFILE = InstrumentProfile(
    instrument=XAUUSD_P_INSTRUMENT,
    profile_id="xauusd_p_broker_gold_v1",
    session_scope=XAUUSD_P_SESSION_SCOPE,
    product_identity_verified=False,
    contract_economics_verified=False,
    source=(
        "Fortune Prime Limited MT5 symbol XAUUSD-P; product identity and "
        "contract economics are not verified"
    ),
)


def profile_for(instrument: MarketInstrument) -> InstrumentProfile:
    """Return the approved profile for an instrument or fail closed."""

    if not isinstance(instrument, MarketInstrument):
        raise TypeError("instrument must be a MarketInstrument")
    if (
        instrument.symbol == XAUUSD_P_INSTRUMENT.symbol
        and instrument.asset_class is AssetClass.UNVERIFIED_GOLD
        and instrument.exchange == XAUUSD_P_INSTRUMENT.exchange
        and instrument.currency == XAUUSD_P_INSTRUMENT.currency
        and instrument.timezone == XAUUSD_P_INSTRUMENT.timezone
    ):
        return XAUUSD_P_PROFILE
    raise UnsupportedInstrumentProfileError(
        "the active profile supports only the broker XAUUSD-P instrument; "
        f"received {instrument.symbol!r}/{instrument.asset_class.value!r}"
    )
