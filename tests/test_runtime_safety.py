"""Tests for runtime mode separation and configuration visibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trantor.config import (
    ConfigurationError,
    ExecutionMode,
    load_settings,
    settings_fingerprint,
    settings_snapshot,
)
from trantor.safety import ExecutionBlockedError, validate_execution_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.example.json"


def _payload() -> dict:
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_example_settings_use_explicit_paper_execution() -> None:
    settings = load_settings(SETTINGS_PATH)
    assert settings.execution_mode is ExecutionMode.PAPER
    assert settings.execution_enabled is True
    validate_execution_settings(settings)


def test_missing_execution_mode_defaults_to_disabled(tmp_path: Path) -> None:
    payload = _payload()
    del payload["execution_mode"]
    settings = load_settings(_write(tmp_path, payload))
    assert settings.execution_mode is ExecutionMode.DISABLED
    assert settings.execution_enabled is False
    with pytest.raises(ExecutionBlockedError, match="execution_mode"):
        validate_execution_settings(settings)


@pytest.mark.parametrize(
    "mode",
    [
        ExecutionMode.DISABLED,
        ExecutionMode.RESEARCH,
        ExecutionMode.DEMO,
        ExecutionMode.LIVE,
    ],
)
def test_non_paper_modes_are_blocked_by_the_safety_gate(
    tmp_path: Path,
    mode: ExecutionMode,
) -> None:
    payload = _payload()
    payload["execution_mode"] = mode.value
    settings = load_settings(_write(tmp_path, payload))
    with pytest.raises(ExecutionBlockedError, match="execution"):
        validate_execution_settings(settings)


def test_invalid_execution_mode_is_rejected(tmp_path: Path) -> None:
    payload = _payload()
    payload["execution_mode"] = "not-a-mode"
    with pytest.raises(ConfigurationError, match="execution_mode"):
        load_settings(_write(tmp_path, payload))


def test_paper_execution_mode_is_blocked_when_trading_mode_is_live(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["trading_mode"] = "live"
    settings = load_settings(_write(tmp_path, payload))
    with pytest.raises(ExecutionBlockedError, match="live trading"):
        validate_execution_settings(settings)


def test_settings_snapshot_is_redacted_and_fingerprint_is_stable(tmp_path: Path) -> None:
    payload = _payload()
    payload["mt5_terminal_path"] = r"C:\secret\terminal64.exe"
    settings = load_settings(_write(tmp_path, payload))
    snapshot = settings_snapshot(settings)
    assert settings_fingerprint(settings) == settings_fingerprint(settings)
    assert snapshot["config_fingerprint"] == settings_fingerprint(settings)
    assert "secret" not in json.dumps(snapshot)
    assert snapshot["mt5_terminal_path_configured"] is True
    assert "mt5_terminal_path" not in snapshot
