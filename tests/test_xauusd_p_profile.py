"""Tests for the active XAUUSD-P profile and strategy interface boundary."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from trantor.config import (
    ConfigurationError,
    SUPPORTED_XAUUSD_P_UNIVERSE,
    load_strategy_config,
)
from trantor.instruments import (
    XAUUSD_P_PROFILE,
    UnsupportedInstrumentProfileError,
    profile_for,
)
from trantor.market_data.mt5_provider import XAUUSD_P_INSTRUMENT
from trantor.models import AssetClass, MarketInstrument
from trantor.strategy import ResearchStrategy
from trantor.strategy.xauusd_p import XAUUSDPMultiTimeframeStrategy
from trantor.strategy.xauusd_p_research import XAUUSDPMTFResearchVariant

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = PROJECT_ROOT / "rules" / "gold.json"


def _payload() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "xauusd_p.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_xauusd_p_profile_identity_and_limits() -> None:
    profile = profile_for(XAUUSD_P_INSTRUMENT)
    assert profile is XAUUSD_P_PROFILE
    assert profile.instrument.symbol == "XAUUSD-P"
    assert profile.instrument.asset_class is AssetClass.UNVERIFIED_GOLD
    assert profile.session_scope == "xauusd_research_unverified"
    assert profile.product_identity_verified is False
    assert profile.contract_economics_verified is False


def test_profile_rejects_gc_mgc_and_other_gold_symbols() -> None:
    for instrument in (
        MarketInstrument(
            "GC",
            AssetClass.GOLD_FUTURES,
            "COMEX",
            "USD",
            "America/New_York",
        ),
        MarketInstrument(
            "XAUUSD",
            AssetClass.GOLD_SPOT,
            "BROKER",
            "USD",
            "UTC",
        ),
    ):
        with pytest.raises(UnsupportedInstrumentProfileError):
            profile_for(instrument)


def test_xauusd_p_strategy_config_is_separate_from_gold_futures(
    tmp_path: Path,
) -> None:
    payload = copy.deepcopy(_payload())
    payload["supported_instruments"] = {
        "universe": SUPPORTED_XAUUSD_P_UNIVERSE,
        "asset_classes": ["unverified_gold"],
        "symbols": ["XAUUSD-P"],
    }
    config = load_strategy_config(_write(tmp_path, payload))
    assert config.supported_instruments.universe == SUPPORTED_XAUUSD_P_UNIVERSE
    assert config.supported_instruments.asset_classes == (
        AssetClass.UNVERIFIED_GOLD,
    )
    assert config.supported_instruments.symbols == ("XAUUSD-P",)


def test_xauusd_p_config_cannot_mix_gc_mgc_or_unknown_symbols(
    tmp_path: Path,
) -> None:
    mixed = copy.deepcopy(_payload())
    mixed["supported_instruments"] = {
        "universe": "gold_futures",
        "asset_classes": ["gold_futures", "unverified_gold"],
        "symbols": ["GC", "XAUUSD-P"],
    }
    with pytest.raises(ConfigurationError, match="mixed|asset_classes"):
        load_strategy_config(_write(tmp_path, mixed))

    wrong_symbol = copy.deepcopy(_payload())
    wrong_symbol["supported_instruments"] = {
        "universe": SUPPORTED_XAUUSD_P_UNIVERSE,
        "asset_classes": ["unverified_gold"],
        "symbols": ["XAUUSD-P", "GC"],
    }
    with pytest.raises(ConfigurationError, match="XAUUSD-P"):
        load_strategy_config(_write(tmp_path, wrong_symbol))


def test_active_strategies_satisfy_research_strategy_protocol() -> None:
    assert isinstance(XAUUSDPMultiTimeframeStrategy(), ResearchStrategy)
    assert isinstance(XAUUSDPMTFResearchVariant(), ResearchStrategy)
